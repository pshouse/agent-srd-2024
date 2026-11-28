"""Multi-Encounter Adventuring Day, Party Carryover & Deterministic Short Rests.

Implements M6.3B (`docs/M6-DUNGEON-SPEC.md` §3):
- Built-in 3-room Tier 2 dungeon (`ashfall-keep`) using 5 verified Level 5 PCs
  and all `coverage == "full"` monsters on bounded tactical terrain maps.
- Party state carryover across sequential encounters (HP, death saves, spell slots,
  class resources, hit dice, heroic inspiration, exhaustion, conditions).
- Deterministic 5e 2024 (SRD 5.2) Short Rest resolution (`resolve_short_rest`)
  supporting Hit Dice rolling, +1 Second Wind / Channel Divinity, full Action Surge /
  Warlock Pact Magic recharge, and once-per-Long-Rest Wizard Arcane Recovery.
- Multi-room `dungeon_trace` (`schema_version: "5"`), SHA-256 digest & deterministic
  `replay_dungeon()`, 5-dimension `score_dungeon()` with `resource_conservation_score`,
  interactive `run_interactive_dungeon()`, and Markdown renderers.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from srd2024.engine.character import compile_character
from srd2024.engine.combat import in_bounds, is_wall, validate_bounds, validate_terrain
from srd2024.engine.dice import DeterministicRNG
from srd2024.engine.store import RulesStore, strip_prefix

from .agents import AgentDriver
from .runner import STATE_SHAPE_VERSION, _describe_driver_config, replay_match, run_match
from .scenarios import (
    _SPEC_ALDRIC_L5,
    _SPEC_DORIAN_L5,
    _SPEC_KAELEN_L5,
    _SPEC_LYRA_L5,
    _summarize_enemy_entry,
    _summarize_party_entry,
)
from .session import run_interactive_session

BUILTIN_DUNGEON_IDS: tuple[str, ...] = ("ashfall-keep",)

DUNGEON_SCHEMA_VERSION = "5"

DUNGEON_SCORE_WEIGHTS: dict[str, float] = {
    "outcome": 0.35,
    "rules_legality": 0.20,
    "tactical": 0.20,
    "coordination": 0.10,
    "resource_conservation": 0.15,
}

# Conditions that persist across rooms (unless cured); combat-duration conditions clear.
_PERSISTENT_BETWEEN_ROOMS_CONDITIONS: frozenset[str] = frozenset({"poisoned"})

# ----------------------------------------------------------------------
# Verified L5 Paladin Spec (`_SPEC_SEREN_L5`, M6-DUNGEON-SPEC §3.1)
# ----------------------------------------------------------------------

_SPEC_SEREN_L5: dict[str, Any] = {
    "name": "Seren Dawnblade",
    "species": "human",
    "subspecies": None,
    "size": "Medium",
    "background": "soldier",
    "background_asi": {"str": 2, "con": 1},
    "origin_feat": "savage-attacker",
    "extra_origin_feat": "alert",
    "class": "paladin",
    "subclass": "oath-of-devotion",
    "level": 5,
    "ability_method": "point_buy",
    "base_abilities": {"str": 15, "dex": 10, "con": 14, "int": 8, "wis": 10, "cha": 14},
    "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}],
    "fighting_style": "defense",
    "skill_proficiencies": ["athletics", "intimidation", "insight", "persuasion", "perception"],
    "equipment": {
        "armor": "chain-mail",
        "shield": True,
        "weapons": ["longsword", "javelin"],
    },
    "weapon_mastery_weapons": ["longsword", "javelin"],
    "cantrips": [],
    "prepared_spells": [
        "divine-smite",
        "bless",
        "cure-wounds",
        "shield-of-faith",
        "aid",
        "shining-smite",
    ],
    "hp_method": "fixed",
}

# ----------------------------------------------------------------------
# Built-In Dungeon Definitions (M6-DUNGEON-SPEC §3.1)
# ----------------------------------------------------------------------

_BUILTIN_DUNGEONS: dict[str, dict[str, Any]] = {
    "ashfall-keep": {
        "id": "ashfall-keep",
        "name": "Ashfall Keep",
        "description": (
            "Three-room Tier 2 5e 2024 (SRD 5.2) adventuring day testing resource conservation "
            "and Short Rest recovery across bounded terrain rooms."
        ),
        "tier": 2,
        "party_level": 5,
        "allow_short_rest_between_rooms": True,
        "party": [
            {"id": "pc-aldric", "spec": _SPEC_ALDRIC_L5},
            {"id": "pc-kaelen", "spec": _SPEC_KAELEN_L5},
            {"id": "pc-seren", "spec": _SPEC_SEREN_L5},
            {"id": "pc-dorian", "spec": _SPEC_DORIAN_L5},
            {"id": "pc-lyra", "spec": _SPEC_LYRA_L5},
        ],
        "rooms": [
            {
                "id": "entrance-hall",
                "name": "Room 1: Entrance Hall",
                "max_rounds": 10,
                "bounds": {"min_x": 0, "min_y": 0, "max_x": 50, "max_y": 35},
                "terrain": {
                    "walls": [[20, 0], [20, 5], [20, 30], [20, 35]],
                    "difficult_terrain": [[15, 15], [15, 20], [20, 15], [20, 20]],
                    "cover": [
                        {"x": 10, "y": 10, "kind": "half"},
                        {"x": 10, "y": 25, "kind": "half"},
                        {"x": 30, "y": 10, "kind": "half"},
                        {"x": 30, "y": 25, "kind": "half"},
                    ],
                },
                "party_spawns": {
                    "pc-aldric": {"x": 5, "y": 15},
                    "pc-seren": {"x": 5, "y": 20},
                    "pc-kaelen": {"x": 5, "y": 10},
                    "pc-dorian": {"x": 0, "y": 15},
                    "pc-lyra": {"x": 0, "y": 20},
                },
                "enemies": [
                    {
                        "id": "r1-ghoul-1",
                        "monster": "ghoul",
                        "name": "Gate Ghoul A",
                        "position": {"x": 35, "y": 10},
                    },
                    {
                        "id": "r1-ghoul-2",
                        "monster": "ghoul",
                        "name": "Gate Ghoul B",
                        "position": {"x": 35, "y": 25},
                    },
                    {
                        "id": "r1-skeleton-1",
                        "monster": "skeleton",
                        "name": "Gate Archer A",
                        "position": {"x": 45, "y": 10},
                    },
                    {
                        "id": "r1-skeleton-2",
                        "monster": "skeleton",
                        "name": "Gate Archer B",
                        "position": {"x": 45, "y": 25},
                    },
                    {
                        "id": "r1-armor-1",
                        "monster": "animated-armor",
                        "name": "Gate Sentinel",
                        "position": {"x": 30, "y": 15},
                    },
                ],
            },
            {
                "id": "guardroom",
                "name": "Room 2: Sunken Guardroom",
                "max_rounds": 12,
                "bounds": {"min_x": 0, "min_y": 0, "max_x": 55, "max_y": 40},
                "terrain": {
                    "walls": [[25, 0], [25, 5], [25, 10], [25, 30], [25, 35], [25, 40]],
                    "difficult_terrain": [
                        [20, 15],
                        [20, 20],
                        [20, 25],
                        [30, 15],
                        [30, 20],
                        [30, 25],
                    ],
                    "cover": [
                        {"x": 15, "y": 15, "kind": "three_quarters"},
                        {"x": 15, "y": 25, "kind": "three_quarters"},
                        {"x": 35, "y": 15, "kind": "three_quarters"},
                        {"x": 35, "y": 25, "kind": "three_quarters"},
                    ],
                },
                "party_spawns": {
                    "pc-aldric": {"x": 5, "y": 20},
                    "pc-seren": {"x": 5, "y": 15},
                    "pc-kaelen": {"x": 5, "y": 25},
                    "pc-dorian": {"x": 0, "y": 15},
                    "pc-lyra": {"x": 0, "y": 25},
                },
                "enemies": [
                    {
                        "id": "r2-ghast-1",
                        "monster": "ghast",
                        "name": "Guardroom Ghast",
                        "position": {"x": 40, "y": 20},
                    },
                    {
                        "id": "r2-knight-1",
                        "monster": "knight",
                        "name": "Oathbound Knight",
                        "position": {"x": 35, "y": 15},
                    },
                    {
                        "id": "r2-ghoul-1",
                        "monster": "ghoul",
                        "name": "Barracks Ghoul A",
                        "position": {"x": 40, "y": 10},
                    },
                    {
                        "id": "r2-ghoul-2",
                        "monster": "ghoul",
                        "name": "Barracks Ghoul B",
                        "position": {"x": 40, "y": 30},
                    },
                ],
            },
            {
                "id": "boss-chamber",
                "name": "Room 3: Inner Sanctum",
                "max_rounds": 15,
                "bounds": {"min_x": 0, "min_y": 0, "max_x": 60, "max_y": 45},
                "terrain": {
                    "walls": [
                        [20, 0],
                        [20, 5],
                        [20, 40],
                        [20, 45],
                        [40, 0],
                        [40, 5],
                        [40, 40],
                        [40, 45],
                    ],
                    "difficult_terrain": [[35, 15], [35, 20], [35, 25], [35, 30]],
                    "cover": [
                        {"x": 15, "y": 15, "kind": "half"},
                        {"x": 15, "y": 30, "kind": "half"},
                        {"x": 30, "y": 10, "kind": "half"},
                        {"x": 30, "y": 35, "kind": "half"},
                    ],
                },
                "party_spawns": {
                    "pc-aldric": {"x": 5, "y": 20},
                    "pc-seren": {"x": 5, "y": 25},
                    "pc-kaelen": {"x": 5, "y": 15},
                    "pc-dorian": {"x": 0, "y": 20},
                    "pc-lyra": {"x": 0, "y": 25},
                },
                "enemies": [
                    {
                        "id": "r3-mummy-1",
                        "monster": "mummy",
                        "name": "Sanctum Mummy",
                        "position": {"x": 50, "y": 20},
                    },
                    {
                        "id": "r3-minotaur-1",
                        "monster": "minotaur-of-baphomet",
                        "name": "Vault Minotaur",
                        "position": {"x": 45, "y": 25},
                    },
                    {
                        "id": "r3-ghast-1",
                        "monster": "ghast",
                        "name": "Sanctum Ghast",
                        "position": {"x": 45, "y": 15},
                    },
                    {
                        "id": "r3-ghoul-1",
                        "monster": "ghoul",
                        "name": "Crypt Stalker A",
                        "position": {"x": 40, "y": 10},
                    },
                    {
                        "id": "r3-ghoul-2",
                        "monster": "ghoul",
                        "name": "Crypt Stalker B",
                        "position": {"x": 40, "y": 35},
                    },
                ],
            },
        ],
    },
}


# ----------------------------------------------------------------------
# Registry & Loader
# ----------------------------------------------------------------------


def list_builtin_dungeons() -> list[dict[str, Any]]:
    """Return compact sorted metadata for all built-in 5e 2024 (SRD 5.2) dungeons."""
    rows: list[dict[str, Any]] = []
    for did in BUILTIN_DUNGEON_IDS:
        raw = _BUILTIN_DUNGEONS[did]
        rooms_summary = [
            {
                "id": r["id"],
                "name": r["name"],
                "max_rounds": int(r.get("max_rounds", 10)),
                "enemy_summary": [_summarize_enemy_entry(e) for e in r["enemies"]],
            }
            for r in raw["rooms"]
        ]
        rows.append(
            {
                "id": raw["id"],
                "name": raw["name"],
                "tier": int(raw["tier"]),
                "party_level": int(raw["party_level"]),
                "total_rooms": len(raw["rooms"]),
                "allow_short_rest_between_rooms": bool(
                    raw.get("allow_short_rest_between_rooms", True)
                ),
                "description": raw["description"],
                "party_summary": [_summarize_party_entry(p) for p in raw["party"]],
                "rooms": rooms_summary,
            }
        )
    rows.sort(key=lambda r: (r["tier"], r["party_level"], r["id"]))
    return rows


def _parse_pos(raw_pos: Any, label: str) -> dict[str, int]:
    if not isinstance(raw_pos, dict) or "x" not in raw_pos or "y" not in raw_pos:
        raise ValueError(f"{label} position must be an object with integer 'x' and 'y'.")
    try:
        return {"x": int(raw_pos["x"]), "y": int(raw_pos["y"])}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} position coordinates must be integers.") from exc


def _compile_raw_dungeon(
    raw: dict[str, Any],
    rules: RulesStore,
    source: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Dungeon definition must be a JSON object.")

    if raw.get("_compiled") is True and isinstance(raw.get("party"), list) and isinstance(raw.get("rooms"), list):
        out = copy.deepcopy(raw)
        out.setdefault("source", source)
        return out

    dungeon_id = str(raw.get("id") or "").strip()
    if not dungeon_id:
        raise ValueError("Dungeon must define a non-empty 'id'.")

    party_raw = raw.get("party")
    rooms_raw = raw.get("rooms")
    if not isinstance(party_raw, list) or not party_raw:
        raise ValueError(f"Dungeon '{dungeon_id}' must define a non-empty 'party' list.")
    if not isinstance(rooms_raw, list) or not rooms_raw:
        raise ValueError(f"Dungeon '{dungeon_id}' must define a non-empty 'rooms' list.")

    compiled_party: list[dict[str, Any]] = []
    party_ids: list[str] = []
    seen_party_ids: set[str] = set()
    inferred_levels: list[int] = []

    for idx, entry in enumerate(party_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Dungeon '{dungeon_id}' party[{idx}] must be an object.")
        cid = str(entry.get("id") or "").strip()
        if not cid:
            raise ValueError(f"Dungeon '{dungeon_id}' party[{idx}] is missing a non-empty 'id'.")
        if cid in seen_party_ids:
            raise ValueError(f"Dungeon '{dungeon_id}' has duplicate party id '{cid}'.")
        seen_party_ids.add(cid)

        if "spec" in entry and entry["spec"] is not None:
            if not isinstance(entry["spec"], dict):
                raise ValueError(f"Party combatant '{cid}' 'spec' must be an object.")
            compiled_pc = compile_character(entry["spec"], rules)
            if not compiled_pc.get("valid"):
                err_msgs = "; ".join(
                    f"{e.get('path', '?')}: {e.get('message', e.get('code', 'invalid'))}"
                    for e in (compiled_pc.get("errors") or [])
                )
                raise ValueError(
                    f"Invalid PC spec for '{cid}' in dungeon '{dungeon_id}': "
                    f"{err_msgs or 'compilation failed'}"
                )
            statblock = copy.deepcopy(compiled_pc["statblock"])
        elif "statblock" in entry and isinstance(entry["statblock"], dict):
            statblock = copy.deepcopy(entry["statblock"])
        else:
            raise ValueError(
                f"Party combatant '{cid}' in dungeon '{dungeon_id}' must provide 'spec' or 'statblock'."
            )

        inferred_levels.append(int(statblock.get("level", 1)))
        pc_rec: dict[str, Any] = {
            "id": cid,
            "name": str(entry.get("name") or statblock.get("name") or cid),
            "statblock": statblock,
        }
        if "position" in entry and entry["position"] is not None:
            pc_rec["default_position"] = _parse_pos(entry["position"], f"party '{cid}'")
        compiled_party.append(pc_rec)
        party_ids.append(cid)

    compiled_rooms: list[dict[str, Any]] = []
    seen_room_ids: set[str] = set()

    for ridx, rraw in enumerate(rooms_raw):
        if not isinstance(rraw, dict):
            raise ValueError(f"Dungeon '{dungeon_id}' rooms[{ridx}] must be an object.")
        rid = str(rraw.get("id") or f"room-{ridx + 1}").strip()
        if not rid:
            raise ValueError(f"Dungeon '{dungeon_id}' rooms[{ridx}] has an empty 'id'.")
        if rid in seen_room_ids:
            raise ValueError(f"Dungeon '{dungeon_id}' has duplicate room id '{rid}'.")
        seen_room_ids.add(rid)

        max_rounds = int(rraw.get("max_rounds") or 10)
        if max_rounds < 1:
            raise ValueError(f"Room '{rid}' max_rounds must be >= 1, got {max_rounds}.")

        bounds = validate_bounds(rraw.get("bounds"))
        terrain = validate_terrain(rraw.get("terrain"))

        spawns_raw = rraw.get("party_spawns")
        if not isinstance(spawns_raw, dict):
            spawns_raw = {}

        party_spawns: dict[str, dict[str, int]] = {}
        occupied: dict[tuple[int, int], str] = {}

        for pc in compiled_party:
            cid = pc["id"]
            if cid in spawns_raw:
                pos = _parse_pos(spawns_raw[cid], f"room '{rid}' party_spawns['{cid}']")
            elif "default_position" in pc:
                pos = copy.deepcopy(pc["default_position"])
            else:
                raise ValueError(
                    f"Room '{rid}' in dungeon '{dungeon_id}' is missing party_spawns entry for '{cid}'."
                )
            if not in_bounds(pos, bounds):
                raise ValueError(
                    f"Party member '{cid}' in room '{rid}' at ({pos['x']}, {pos['y']}) is outside bounds {bounds}."
                )
            if is_wall(pos, terrain):
                raise ValueError(
                    f"Party member '{cid}' in room '{rid}' at ({pos['x']}, {pos['y']}) cannot start on a wall."
                )
            sq = (pos["x"], pos["y"])
            if sq in occupied:
                raise ValueError(
                    f"Room '{rid}' spawn collision at ({pos['x']}, {pos['y']}) between '{occupied[sq]}' and '{cid}'."
                )
            occupied[sq] = cid
            party_spawns[cid] = pos

        enemies_raw = rraw.get("enemies")
        if not isinstance(enemies_raw, list) or not enemies_raw:
            raise ValueError(f"Room '{rid}' in dungeon '{dungeon_id}' must define a non-empty 'enemies' list.")

        compiled_enemies: list[dict[str, Any]] = []
        seen_enemy_ids: set[str] = set()
        for eidx, eentry in enumerate(enemies_raw):
            if not isinstance(eentry, dict):
                raise ValueError(f"Room '{rid}' enemies[{eidx}] must be an object.")
            eid = str(eentry.get("id") or "").strip()
            if not eid:
                raise ValueError(f"Room '{rid}' enemies[{eidx}] is missing a non-empty 'id'.")
            if eid in seen_enemy_ids or eid in seen_party_ids:
                raise ValueError(f"Room '{rid}' has duplicate combatant id '{eid}'.")
            seen_enemy_ids.add(eid)

            pos = _parse_pos(eentry.get("position"), f"room '{rid}' enemy '{eid}'")
            if not in_bounds(pos, bounds):
                raise ValueError(
                    f"Enemy '{eid}' in room '{rid}' at ({pos['x']}, {pos['y']}) is outside bounds {bounds}."
                )
            if is_wall(pos, terrain):
                raise ValueError(
                    f"Enemy '{eid}' in room '{rid}' at ({pos['x']}, {pos['y']}) cannot start on a wall."
                )
            sq = (pos["x"], pos["y"])
            if sq in occupied:
                raise ValueError(
                    f"Room '{rid}' spawn collision at ({pos['x']}, {pos['y']}) between '{occupied[sq]}' and '{eid}'."
                )
            occupied[sq] = eid

            if "monster" in eentry and eentry["monster"] is not None:
                m_slug = strip_prefix(str(eentry["monster"]))
                m_rec = rules.get_monster(m_slug)
                if m_rec is None:
                    raise ValueError(f"Unknown monster slug '{eentry['monster']}' for '{eid}' in room '{rid}'.")
                e_compiled: dict[str, Any] = {
                    "id": eid,
                    "name": str(eentry.get("name") or m_rec.get("name") or eid),
                    "side": "enemy",
                    "monster": m_slug,
                    "position": pos,
                }
            elif "statblock" in eentry and isinstance(eentry["statblock"], dict):
                sb = copy.deepcopy(eentry["statblock"])
                e_compiled = {
                    "id": eid,
                    "name": str(eentry.get("name") or sb.get("name") or eid),
                    "side": "enemy",
                    "statblock": sb,
                    "position": pos,
                }
            else:
                raise ValueError(f"Enemy '{eid}' in room '{rid}' must provide 'monster' or 'statblock'.")

            for opt_key in ("hp", "hp_max", "temp_hp", "exhaustion_level", "conditions"):
                if opt_key in eentry and eentry[opt_key] is not None:
                    e_compiled[opt_key] = copy.deepcopy(eentry[opt_key])
            compiled_enemies.append(e_compiled)

        room_obj: dict[str, Any] = {
            "id": rid,
            "name": str(rraw.get("name") or rid),
            "max_rounds": max_rounds,
            "party_spawns": party_spawns,
            "enemies": compiled_enemies,
        }
        if bounds is not None:
            room_obj["bounds"] = bounds
        if terrain is not None:
            room_obj["terrain"] = terrain
        compiled_rooms.append(room_obj)

    party_level = int(raw.get("party_level") or (max(inferred_levels) if inferred_levels else 1))
    tier = int(raw.get("tier") or (1 if party_level <= 4 else (2 if party_level <= 10 else 3)))

    return {
        "_compiled": True,
        "id": dungeon_id,
        "name": str(raw.get("name") or dungeon_id),
        "description": str(raw.get("description") or ""),
        "tier": tier,
        "party_level": party_level,
        "allow_short_rest_between_rooms": bool(raw.get("allow_short_rest_between_rooms", True)),
        "party": compiled_party,
        "party_ids": party_ids,
        "rooms": compiled_rooms,
        "source": source,
    }


def load_dungeon(
    spec_or_id: str | Path | dict[str, Any],
    rules: RulesStore,
) -> dict[str, Any]:
    """Load a built-in dungeon ID, custom dungeon JSON file path, or inline dict."""
    if isinstance(spec_or_id, dict):
        return _compile_raw_dungeon(spec_or_id, rules, source="inline")

    if isinstance(spec_or_id, Path):
        path = spec_or_id.expanduser()
        if not path.is_file():
            raise ValueError(f"Dungeon file not found: '{path}'.")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to parse dungeon JSON '{path}': {exc}") from exc
        return _compile_raw_dungeon(raw, rules, source="file")

    if isinstance(spec_or_id, str):
        key = spec_or_id.strip()
        if not key:
            raise ValueError("Dungeon ID or path cannot be empty.")
        key_lower = key.lower()
        if key_lower in _BUILTIN_DUNGEONS:
            return _compile_raw_dungeon(copy.deepcopy(_BUILTIN_DUNGEONS[key_lower]), rules, source="builtin")
        path = Path(key).expanduser()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Failed to parse dungeon JSON '{path}': {exc}") from exc
            return _compile_raw_dungeon(raw, rules, source="file")
        valid_list = ", ".join(BUILTIN_DUNGEON_IDS)
        raise ValueError(
            f"Unknown dungeon '{spec_or_id}'. Expected one of [{valid_list}] or a valid JSON file path."
        )

    raise ValueError(f"Unsupported dungeon input type: {type(spec_or_id).__name__}.")


# ----------------------------------------------------------------------
# Party Carryover State & Decoupled M6.3A Defaults (§2.2, §3.2, §5)
# ----------------------------------------------------------------------


def _default_class_resources_max(
    statblock: dict[str, Any],
    rules: RulesStore | None = None,
) -> dict[str, dict[str, Any]]:
    """Derive SRD 5.2 class_resources_max from statblock (compatible pre/post M6.3A)."""
    existing = statblock.get("class_resources_max")
    if isinstance(existing, dict) and existing:
        return copy.deepcopy(existing)

    class_slug = strip_prefix(str(statblock.get("class") or statblock.get("source_id") or "")).lower()
    level = max(1, int(statblock.get("level", 1)))
    level_rec: dict[str, Any] = {}
    if rules is not None:
        levels_map = (rules.data.get("character_creation") or {}).get("levels") or {}
        level_rec = levels_map.get(f"{class_slug}-{level}") or {}
    class_spec = level_rec.get("class_specific") or {}

    out: dict[str, dict[str, Any]] = {}
    if class_slug == "fighter":
        sw_uses = int(
            class_spec.get("second_wind_uses")
            or (2 if level <= 3 else (3 if level <= 9 else 4))
        )
        out["second_wind"] = {"max": sw_uses, "recharge": "short_rest_one"}
        if level >= 2:
            out["action_surge"] = {
                "max": 2 if level >= 17 else 1,
                "recharge": "short_rest_all",
            }
    elif class_slug == "paladin":
        out["lay_on_hands"] = {"max": 5 * level, "recharge": "long_rest"}
        if level >= 3:
            cd_uses = int(
                class_spec.get("channel_divinity_charges")
                or (2 if level <= 10 else 3)
            )
            out["channel_divinity"] = {"max": cd_uses, "recharge": "short_rest_one"}
    elif class_slug == "cleric":
        if level >= 2:
            cd_uses = int(
                class_spec.get("channel_divinity_charges")
                or (2 if level <= 5 else (3 if level <= 17 else 4))
            )
            out["channel_divinity"] = {"max": cd_uses, "recharge": "short_rest_one"}
    elif class_slug == "wizard":
        out["arcane_recovery"] = {
            "max": 1,
            "budget_levels": (level + 1) // 2,
            "max_slot_level": 5,
            "recharge": "long_rest",
        }
    return out


def _infer_hit_die(statblock_or_comb: dict[str, Any], rules: RulesStore | None = None) -> int:
    if statblock_or_comb.get("hit_die"):
        return int(statblock_or_comb["hit_die"])
    hd_obj = statblock_or_comb.get("hit_dice")
    if isinstance(hd_obj, dict) and hd_obj.get("die"):
        return int(hd_obj["die"])
    class_slug = strip_prefix(
        str(statblock_or_comb.get("class") or statblock_or_comb.get("source_id") or "")
    ).lower()
    if rules is not None and class_slug:
        cls_rec = ((rules.data.get("character_creation") or {}).get("classes") or {}).get(class_slug)
        if isinstance(cls_rec, dict) and cls_rec.get("hit_die"):
            return int(cls_rec["hit_die"])
    defaults = {
        "barbarian": 12,
        "fighter": 10,
        "paladin": 10,
        "ranger": 10,
        "bard": 8,
        "cleric": 8,
        "druid": 8,
        "monk": 8,
        "rogue": 8,
        "warlock": 8,
        "sorcerer": 6,
        "wizard": 6,
    }
    return defaults.get(class_slug, 8)


def init_party_carryover(
    compiled_party: list[dict[str, Any]],
    rules: RulesStore | None = None,
) -> dict[str, dict[str, Any]]:
    """Initialize full party carryover state before Room 1."""
    carryover: dict[str, dict[str, Any]] = {}
    for entry in compiled_party:
        cid = str(entry["id"])
        sb = entry["statblock"]
        level = max(1, int(sb.get("level", 1)))
        hp_max = max(1, int(sb["hp_max"]))
        hit_die = _infer_hit_die(sb, rules)
        sc = sb.get("spellcasting") or {}
        slots_max = {str(k): int(v) for k, v in (sc.get("slots") or {}).items()}
        cr_max = _default_class_resources_max(sb, rules)
        class_resources = {
            rname: {**copy.deepcopy(rdata), "remaining": int(rdata["max"])}
            for rname, rdata in cr_max.items()
        }
        traits = {strip_prefix(str(t)) for t in (sb.get("traits") or [])}
        carryover[cid] = {
            "id": cid,
            "name": str(entry.get("name") or sb.get("name") or cid),
            "class": str(sb.get("class") or "classes:fighter"),
            "level": level,
            "hp": hp_max,
            "hp_max": hp_max,
            "temp_hp": 0,
            "alive": True,
            "con_mod": int((sb.get("ability_mods") or {}).get("con", 0)),
            "hit_die": hit_die,
            "hit_dice": {"die": hit_die, "max": level, "remaining": level},
            "slots": copy.deepcopy(slots_max),
            "slots_max": copy.deepcopy(slots_max),
            "is_pact_magic": bool(sc.get("is_pact_magic", False)),
            "class_resources": class_resources,
            "heroic_inspiration": "resourceful" in traits,
            "exhaustion_level": 0,
            "death_saves": {"successes": 0, "failures": 0},
            "conditions": [],
        }
        free = _feat_free_casts_of(sb)
        if free is not None:
            carryover[cid]["feat_free_casts"] = free
    return carryover


def _feat_free_casts_of(holder: dict[str, Any]) -> list[dict[str, int]] | None:
    """Magic Initiate free casts per feat block (#8); they recharge on a Long Rest only, so they carry over."""
    blocks = [b for b in (holder.get("feat_spellcasting") or []) if isinstance(b, dict)]
    if not blocks:
        return None
    return [{str(k): max(0, int(v)) for k, v in (b.get("free_casts") or {}).items()} for b in blocks]


def _init_party_carryover_from_initial_state(
    init_state: dict[str, Any],
    party_ids: list[str],
    rules: RulesStore | None = None,
) -> dict[str, dict[str, Any]]:
    """Reconstruct Room 1 starting party carryover from a recorded MatchTranscript initial_state."""
    combs = init_state.get("combatants") or {}
    carryover: dict[str, dict[str, Any]] = {}
    for cid in party_ids:
        c = combs.get(cid)
        if not isinstance(c, dict):
            continue
        level = max(1, int(c.get("level", 1)))
        hp_max = max(1, int(c.get("hp_max", 1)))
        hit_die = _infer_hit_die(c, rules)
        hd_raw = c.get("hit_dice")
        if isinstance(hd_raw, dict):
            hit_dice = {
                "die": int(hd_raw.get("die", hit_die)),
                "max": int(hd_raw.get("max", level)),
                "remaining": int(hd_raw.get("remaining", level)),
            }
        else:
            hit_dice = {"die": hit_die, "max": level, "remaining": level}

        sc = c.get("spellcasting") or {}
        slots = {str(k): int(v) for k, v in (sc.get("slots") or {}).items()}
        slots_max_raw = c.get("slots_max")
        if isinstance(slots_max_raw, dict):
            slots_max = {str(k): int(v) for k, v in slots_max_raw.items()}
        else:
            slots_max = copy.deepcopy(slots)

        cr_default = _default_class_resources_max(c, rules)
        cr_state = c.get("class_resources")
        class_resources: dict[str, dict[str, Any]] = {}
        for rname, rdata in cr_default.items():
            class_resources[rname] = {**copy.deepcopy(rdata), "remaining": int(rdata["max"])}
        if isinstance(cr_state, dict):
            for rname, rdata in cr_state.items():
                if isinstance(rdata, dict):
                    merged = copy.deepcopy(class_resources.get(rname, {}))
                    merged.update(copy.deepcopy(rdata))
                    class_resources[rname] = merged

        carryover[cid] = {
            "id": cid,
            "name": str(c.get("name") or cid),
            "class": str(c.get("source_id") or "classes:fighter"),
            "level": level,
            "hp": max(0, int(c.get("hp", hp_max))),
            "hp_max": hp_max,
            "temp_hp": max(0, int(c.get("temp_hp", 0))),
            "alive": bool(c.get("alive", True)),
            "con_mod": int((c.get("ability_mods") or {}).get("con", 0)),
            "hit_die": hit_die,
            "hit_dice": hit_dice,
            "slots": slots,
            "slots_max": slots_max,
            "is_pact_magic": bool(sc.get("is_pact_magic", False)),
            "class_resources": class_resources,
            "heroic_inspiration": bool(c.get("heroic_inspiration", False)),
            "exhaustion_level": int(c.get("exhaustion_level", 0)),
            "death_saves": copy.deepcopy(c.get("death_saves") or {"successes": 0, "failures": 0}),
            "conditions": copy.deepcopy(c.get("conditions") or []),
        }
        free = _feat_free_casts_of(c)
        if free is not None:
            carryover[cid]["feat_free_casts"] = free
    return carryover


def _extract_persistent_conditions(
    raw_conditions: list[Any],
    *,
    alive: bool,
    hp: int,
) -> list[dict[str, Any]]:
    if not alive:
        return []
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_conditions or []:
        slug = strip_prefix(
            str(entry.get("condition") if isinstance(entry, dict) else entry)
        ).lower()
        if slug in _PERSISTENT_BETWEEN_ROOMS_CONDITIONS and slug not in seen:
            seen.add(slug)
            kept.append({"condition": slug, "source_id": None, "duration": "indefinite"})
    if hp == 0:
        for slug in ("unconscious", "prone"):
            if slug not in seen:
                seen.add(slug)
                kept.append({"condition": slug, "source_id": None, "duration": "indefinite"})
    return kept


def extract_party_carryover(
    room_final_state: dict[str, Any],
    prev_carryover: dict[str, dict[str, Any]],
    *,
    room_won: bool = True,
) -> dict[str, dict[str, Any]]:
    """Extract post-room party state carryover per M6-DUNGEON-SPEC §3.2."""
    updated = copy.deepcopy(prev_carryover)
    final_combs = room_final_state.get("combatants") or {}

    for cid, co in updated.items():
        comb = final_combs.get(cid)
        if not isinstance(comb, dict):
            # Already dead and omitted from this room.
            continue

        alive = bool(comb.get("alive", False))
        hp = max(0, int(comb.get("hp", 0))) if alive else 0
        co["alive"] = alive
        co["hp"] = hp
        co["temp_hp"] = max(0, int(comb.get("temp_hp", 0))) if alive else 0

        if alive and room_won:
            co["death_saves"] = {"successes": 0, "failures": 0}
        else:
            ds = comb.get("death_saves") or {"successes": 0, "failures": 0}
            co["death_saves"] = {
                "successes": int(ds.get("successes", 0)),
                "failures": int(ds.get("failures", 0)),
            }

        sc = comb.get("spellcasting")
        if isinstance(sc, dict) and isinstance(sc.get("slots"), dict):
            co["slots"] = {str(k): max(0, int(v)) for k, v in sc["slots"].items()}
        if isinstance(comb.get("slots_max"), dict):
            co["slots_max"] = {str(k): int(v) for k, v in comb["slots_max"].items()}

        if isinstance(comb.get("class_resources"), dict):
            for rname, rdata in comb["class_resources"].items():
                if not isinstance(rdata, dict):
                    continue
                target = co["class_resources"].setdefault(rname, copy.deepcopy(rdata))
                target["remaining"] = max(0, int(rdata.get("remaining", target.get("remaining", 0))))
                if "max" in rdata:
                    target["max"] = int(rdata["max"])
                if "recharge" in rdata:
                    target["recharge"] = str(rdata["recharge"])

        if isinstance(comb.get("hit_dice"), dict):
            hd = comb["hit_dice"]
            co["hit_dice"] = {
                "die": int(hd.get("die", co["hit_dice"]["die"])),
                "max": int(hd.get("max", co["hit_dice"]["max"])),
                "remaining": max(0, int(hd.get("remaining", co["hit_dice"]["remaining"]))),
            }

        free = _feat_free_casts_of(comb)
        if free is not None:
            co["feat_free_casts"] = free
        co["heroic_inspiration"] = bool(comb.get("heroic_inspiration", co.get("heroic_inspiration", False)))
        co["exhaustion_level"] = int(comb.get("exhaustion_level", co.get("exhaustion_level", 0)))
        co["conditions"] = _extract_persistent_conditions(
            comb.get("conditions") or [],
            alive=alive,
            hp=hp,
        )

    return updated


def build_room_scenario(
    compiled_dungeon: dict[str, Any],
    room_index: int,
    party_carryover: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a compiled single-encounter scenario dict for room `room_index` with party carryover."""
    room = compiled_dungeon["rooms"][room_index]
    combatants: list[dict[str, Any]] = []
    party_ids: list[str] = []

    for pc in compiled_dungeon["party"]:
        cid = pc["id"]
        co = party_carryover[cid]
        if not co.get("alive", False):
            continue

        sb = copy.deepcopy(pc["statblock"])
        # Pre-M6.3A combat.py reads starting slots from statblock["spellcasting"]["slots"];
        # post-M6.3A (STATE_SHAPE_VERSION >= 3) reads slots_max from statblock and overrides
        # starting slots from entry["slots"].
        if STATE_SHAPE_VERSION < 3 and isinstance(sb.get("spellcasting"), dict) and co.get("slots"):
            sb["spellcasting"]["slots"] = copy.deepcopy(co["slots"])

        comb_entry: dict[str, Any] = {
            "id": cid,
            "name": pc["name"],
            "side": "party",
            "statblock": sb,
            "position": copy.deepcopy(room["party_spawns"][cid]),
            "hp": int(co["hp"]),
            "temp_hp": int(co.get("temp_hp", 0)),
            "exhaustion_level": int(co.get("exhaustion_level", 0)),
            "heroic_inspiration": bool(co.get("heroic_inspiration", False)),
            "conditions": copy.deepcopy(co.get("conditions") or []),
            "death_saves": copy.deepcopy(co.get("death_saves") or {"successes": 0, "failures": 0}),
            "hit_dice": copy.deepcopy(co["hit_dice"]),
            "class_resources": copy.deepcopy(co["class_resources"]),
        }
        if co.get("slots"):
            comb_entry["slots"] = copy.deepcopy(co["slots"])
        if co.get("feat_free_casts") is not None:
            comb_entry["feat_free_casts"] = copy.deepcopy(co["feat_free_casts"])

        combatants.append(comb_entry)
        party_ids.append(cid)

    enemy_ids: list[str] = []
    for enemy in room["enemies"]:
        combatants.append(copy.deepcopy(enemy))
        enemy_ids.append(enemy["id"])

    scenario: dict[str, Any] = {
        "id": f"{compiled_dungeon['id']}:{room['id']}",
        "name": f"{compiled_dungeon['name']} — {room['name']}",
        "description": compiled_dungeon.get("description", ""),
        "tier": int(compiled_dungeon["tier"]),
        "party_level": int(compiled_dungeon["party_level"]),
        "max_rounds": int(room["max_rounds"]),
        "tactical_themes": ["adventuring-day", "resource-conservation", "short-rest"],
        "combatants": combatants,
        "party_ids": party_ids,
        "enemy_ids": enemy_ids,
        "source": compiled_dungeon.get("source", "builtin"),
    }
    if room.get("bounds") is not None:
        scenario["bounds"] = copy.deepcopy(room["bounds"])
    if room.get("terrain") is not None:
        scenario["terrain"] = copy.deepcopy(room["terrain"])
    return scenario


# ----------------------------------------------------------------------
# Deterministic Short Rest (M6-DUNGEON-SPEC §3.2)
# ----------------------------------------------------------------------


def _greedy_arcane_recovery_slots(
    slots: dict[str, int],
    slots_max: dict[str, int],
    *,
    budget: int,
    max_slot_level: int = 5,
) -> dict[str, int]:
    """Greedily pick highest expended spell slot levels <= max_slot_level fitting budget."""
    recovered: dict[str, int] = {}
    rem_budget = max(0, int(budget))
    if rem_budget <= 0:
        return recovered

    for lvl in range(min(max_slot_level, 9), 0, -1):
        key = str(lvl)
        cur = int(slots.get(key, 0))
        mx = int(slots_max.get(key, cur))
        missing = max(0, mx - cur)
        if missing <= 0 or lvl > rem_budget:
            continue
        take = min(missing, rem_budget // lvl)
        if take > 0:
            recovered[key] = take
            rem_budget -= take * lvl
        if rem_budget <= 0:
            break
    return recovered


def auto_short_rest_plan(
    party_carryover: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Compute a deterministic heuristic Short Rest recommendation per PC (for previews/UI)."""
    plan: dict[str, dict[str, Any]] = {}
    for cid, pc in party_carryover.items():
        if not pc.get("alive", False):
            plan[cid] = {"hit_dice_to_spend": 0}
            continue
        hp = int(pc["hp"])
        hp_max = int(pc["hp_max"])
        con_mod = int(pc.get("con_mod", 0))
        hd = pc.get("hit_dice") or {"die": 8, "max": 1, "remaining": 0}
        die = int(hd.get("die", 8))
        rem = int(hd.get("remaining", 0))
        avg_heal = max(1, die // 2 + 1 + con_mod)

        est_hp = hp
        dice_to_spend = 0
        while dice_to_spend < rem and est_hp < hp_max and (
            est_hp == 0 or (hp_max - est_hp) >= avg_heal or est_hp * 4 <= hp_max * 3
        ):
            est_hp = min(hp_max, est_hp + avg_heal)
            dice_to_spend += 1

        entry: dict[str, Any] = {"hit_dice_to_spend": dice_to_spend}
        ar = (pc.get("class_resources") or {}).get("arcane_recovery")
        if isinstance(ar, dict) and int(ar.get("remaining", 0)) >= 1:
            budget = int(ar.get("budget_levels", (int(pc.get("level", 1)) + 1) // 2))
            max_lvl = int(ar.get("max_slot_level", 5))
            ar_slots = _greedy_arcane_recovery_slots(
                pc.get("slots") or {},
                pc.get("slots_max") or {},
                budget=budget,
                max_slot_level=max_lvl,
            )
            if ar_slots:
                entry["arcane_recovery_slots"] = ar_slots
        plan[cid] = entry
    return plan


def resolve_short_rest(
    party_carryover: dict[str, dict[str, Any]],
    *,
    rng: DeterministicRNG,
    plan: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Apply a 5e 2024 (SRD 5.2) Short Rest purely and deterministically.

    `plan` (optional per-PC overrides for human/UI/replay play):
      {
        "pc-aldric": {"hit_dice_to_spend": 2},
        "pc-lyra":   {"hit_dice_to_spend": 1, "arcane_recovery_slots": {"2": 1, "1": 1}}
      }
    When `plan` is None, uses the dynamic deterministic auto policy from M6-DUNGEON-SPEC §3.2.
    Returns `(updated_carryover, short_rest_record)`.
    """
    updated = copy.deepcopy(party_carryover)
    per_pc: dict[str, dict[str, Any]] = {}

    for cid, pc in updated.items():
        if not pc.get("alive", False):
            hd_rem = int((pc.get("hit_dice") or {}).get("remaining", 0))
            per_pc[cid] = {
                "hp_before": 0,
                "hp_after": 0,
                "hit_dice_spent": 0,
                "hit_dice_rolls": [],
                "hit_dice_remaining": hd_rem,
                "resources_recovered": {},
                "slots_recovered": {},
            }
            continue

        # Short Rest expires 1-hour temp HP.
        pc["temp_hp"] = 0

        hp_before = int(pc["hp"])
        hp = hp_before
        hp_max = int(pc["hp_max"])
        con_mod = int(pc.get("con_mod", 0))
        hd_obj = pc["hit_dice"]
        hit_die = int(hd_obj["die"])
        hd_rem = int(hd_obj["remaining"])

        pc_plan = plan.get(cid) if isinstance(plan, dict) else None
        rolls: list[int] = []

        # 1. Hit Dice spending
        if isinstance(pc_plan, dict) and "hit_dice_to_spend" in pc_plan and pc_plan["hit_dice_to_spend"] is not None:
            requested = int(pc_plan["hit_dice_to_spend"])
            if requested < 0 or requested > hd_rem:
                raise ValueError(
                    f"PC '{cid}' cannot spend {requested} Hit Dice (only {hd_rem} remaining)."
                )
            for _ in range(requested):
                if hp >= hp_max:
                    break
                r = int(rng.roll_die(hit_die))
                heal = max(1, r + con_mod)
                hp = min(hp_max, hp + heal)
                hd_rem -= 1
                rolls.append(r)
        elif plan is None:
            threshold = max(1, hit_die // 2 + 1 + con_mod)
            while hd_rem > 0 and hp < hp_max and (
                hp == 0 or (hp_max - hp) >= threshold or hp * 4 <= hp_max * 3
            ):
                r = int(rng.roll_die(hit_die))
                heal = max(1, r + con_mod)
                hp = min(hp_max, hp + heal)
                hd_rem -= 1
                rolls.append(r)

        pc["hp"] = hp
        hd_obj["remaining"] = hd_rem

        # Reviving from 0 HP clears unconscious & prone.
        if hp_before == 0 and hp > 0:
            pc["conditions"] = [
                c
                for c in (pc.get("conditions") or [])
                if strip_prefix(str(c.get("condition") if isinstance(c, dict) else c)).lower()
                not in ("unconscious", "prone")
            ]

        # 2. Short-Rest Class Resource Recovery
        resources_recovered: dict[str, int] = {}
        class_resources = pc.get("class_resources") or {}
        for rname, rdata in class_resources.items():
            if not isinstance(rdata, dict):
                continue
            recharge = str(rdata.get("recharge") or "long_rest")
            cur = int(rdata.get("remaining", 0))
            mx = int(rdata.get("max", cur))
            if recharge == "short_rest_one":
                new_val = min(mx, cur + 1)
            elif recharge == "short_rest_all":
                new_val = mx
            else:
                new_val = cur
            if new_val > cur:
                rdata["remaining"] = new_val
                resources_recovered[rname] = new_val - cur

        # 3. Warlock Pact Magic & Wizard Arcane Recovery
        slots_recovered: dict[str, int] = {}
        slots = pc.get("slots") or {}
        slots_max = pc.get("slots_max") or {}

        if pc.get("is_pact_magic", False):
            for lvl_key, mx_val in slots_max.items():
                cur_val = int(slots.get(lvl_key, 0))
                diff = max(0, int(mx_val) - cur_val)
                if diff > 0:
                    slots[lvl_key] = int(mx_val)
                    slots_recovered[str(lvl_key)] = diff

        ar = class_resources.get("arcane_recovery")
        if isinstance(ar, dict) and int(ar.get("remaining", 0)) >= 1:
            budget = int(ar.get("budget_levels", (int(pc.get("level", 1)) + 1) // 2))
            max_lvl = int(ar.get("max_slot_level", 5))

            if isinstance(pc_plan, dict) and "arcane_recovery_slots" in pc_plan:
                req_slots = pc_plan.get("arcane_recovery_slots") or {}
                if not isinstance(req_slots, dict):
                    raise ValueError(f"PC '{cid}' arcane_recovery_slots must be a dict of level -> count.")
                total_cost = 0
                validated: dict[str, int] = {}
                for k, v in req_slots.items():
                    lvl = int(k)
                    cnt = int(v)
                    if cnt < 0:
                        raise ValueError(f"PC '{cid}' arcane_recovery_slots count must be >= 0.")
                    if cnt == 0:
                        continue
                    if lvl < 1 or lvl > max_lvl:
                        raise ValueError(
                            f"PC '{cid}' Arcane Recovery slot level {lvl} must be between 1 and {max_lvl}."
                        )
                    key = str(lvl)
                    cur_val = int(slots.get(key, 0))
                    mx_val = int(slots_max.get(key, 0))
                    if cur_val + cnt > mx_val:
                        raise ValueError(
                            f"PC '{cid}' cannot recover {cnt} level-{lvl} slot(s) "
                            f"(current={cur_val}, max={mx_val})."
                        )
                    total_cost += lvl * cnt
                    validated[key] = cnt
                if total_cost > budget:
                    raise ValueError(
                        f"PC '{cid}' Arcane Recovery total slot levels ({total_cost}) exceeds budget ({budget})."
                    )
                if validated:
                    for key, cnt in validated.items():
                        slots[key] = int(slots.get(key, 0)) + cnt
                        slots_recovered[key] = slots_recovered.get(key, 0) + cnt
                    ar["remaining"] = 0
            elif plan is None:
                auto_ar = _greedy_arcane_recovery_slots(
                    slots,
                    slots_max,
                    budget=budget,
                    max_slot_level=max_lvl,
                )
                if auto_ar:
                    for key, cnt in auto_ar.items():
                        slots[key] = int(slots.get(key, 0)) + cnt
                        slots_recovered[key] = slots_recovered.get(key, 0) + cnt
                    ar["remaining"] = 0
        elif isinstance(pc_plan, dict) and pc_plan.get("arcane_recovery_slots"):
            req_nonempty = {k: int(v) for k, v in pc_plan["arcane_recovery_slots"].items() if int(v) > 0}
            if req_nonempty:
                raise ValueError(f"PC '{cid}' has no Arcane Recovery uses remaining.")

        per_pc[cid] = {
            "hp_before": hp_before,
            "hp_after": hp,
            "hit_dice_spent": len(rolls),
            "hit_dice_rolls": rolls,
            "hit_dice_remaining": hd_rem,
            "resources_recovered": resources_recovered,
            "slots_recovered": slots_recovered,
        }

    return updated, {"taken": True, "per_pc": per_pc}


# ----------------------------------------------------------------------
# Final Party Snapshot, Digest & 5-Dimension Scorer (§3.3)
# ----------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _format_final_party_state(party_carryover: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cid, co in party_carryover.items():
        hd = co.get("hit_dice") or {"die": 8, "max": 1, "remaining": 0}
        out[cid] = {
            "hp": int(co.get("hp", 0)),
            "hp_max": int(co.get("hp_max", 1)),
            "alive": bool(co.get("alive", False)),
            "hit_dice": {
                "die": int(hd.get("die", 8)),
                "remaining": int(hd.get("remaining", 0)),
                "max": int(hd.get("max", 1)),
            },
            "slots": {str(k): int(v) for k, v in (co.get("slots") or {}).items()},
            "slots_max": {str(k): int(v) for k, v in (co.get("slots_max") or {}).items()},
            "class_resources": {
                rname: {
                    "remaining": int(rdata.get("remaining", 0)),
                    "max": int(rdata.get("max", 0)),
                    "recharge": str(rdata.get("recharge", "long_rest")),
                }
                for rname, rdata in (co.get("class_resources") or {}).items()
                if isinstance(rdata, dict)
            },
        }
    return out


def compute_dungeon_digest(trace: dict[str, Any]) -> str:
    """SHA-256 hex digest over canonical JSON of dungeon outcome, rooms, rests, final state & scorecard."""
    payload = {
        "winner": trace.get("winner"),
        "rooms_cleared": int(trace.get("rooms_cleared", 0)),
        "room_digests": [r.get("digest") for r in (trace.get("rooms") or [])],
        "rests": trace.get("rests") or [],
        "final_party_state": trace.get("final_party_state") or {},
        "scorecard": trace.get("scorecard") or {},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def score_dungeon(
    dungeon_trace: dict[str, Any],
    rules: RulesStore | None = None,
) -> dict[str, Any]:
    """Compute 5-dimension deterministic 0–100 DungeonScorecard (M6-DUNGEON-SPEC §3.3).

    Does NOT alter single-encounter `score_match()`.
    """
    _ = rules  # Reserved for future rule-aware scoring extensions.
    winner = str(dungeon_trace.get("winner") or "enemy")
    rooms: list[dict[str, Any]] = list(dungeon_trace.get("rooms") or [])
    rests: list[dict[str, Any]] = list(dungeon_trace.get("rests") or [])
    total_rooms = max(1, int(dungeon_trace.get("total_rooms") or len(rooms) or 1))
    rooms_cleared = max(0, min(total_rooms, int(dungeon_trace.get("rooms_cleared", 0))))
    clear_ratio = rooms_cleared / total_rooms

    final_party: dict[str, dict[str, Any]] = dungeon_trace.get("final_party_state") or {}
    total_pcs = max(1, len(final_party))

    survival_sum = 0.0
    total_hp = 0
    total_hp_max = 0

    slots_rem = 0
    slots_max_sum = 0
    hd_rem = 0
    hd_max_sum = 0
    cr_rem = 0
    cr_max_sum = 0

    for pc in final_party.values():
        alive = bool(pc.get("alive", False))
        hp = max(0, int(pc.get("hp", 0)))
        hp_max = max(1, int(pc.get("hp_max", 1)))
        total_hp += hp if alive else 0
        total_hp_max += hp_max
        if alive:
            if hp > 0:
                survival_sum += 1.0
            elif winner == "party":
                survival_sum += 0.5

        hd = pc.get("hit_dice") or {}
        hd_rem += max(0, int(hd.get("remaining", 0))) if alive else 0
        hd_max_sum += max(0, int(hd.get("max", 0)))

        pc_slots = pc.get("slots") or {}
        pc_slots_max = pc.get("slots_max") or {}
        for lvl_key, mx_v in pc_slots_max.items():
            mx_i = max(0, int(mx_v))
            slots_max_sum += mx_i
            if alive:
                slots_rem += min(mx_i, max(0, int(pc_slots.get(lvl_key, 0))))

        pc_cr = pc.get("class_resources") or {}
        for rdata in pc_cr.values():
            if not isinstance(rdata, dict):
                continue
            mx_i = max(0, int(rdata.get("max", 0)))
            cr_max_sum += mx_i
            if alive:
                cr_rem += min(mx_i, max(0, int(rdata.get("remaining", 0))))

    final_survival_ratio = _clamp(survival_sum / total_pcs, 0.0, 1.0)
    final_hp_ratio = _clamp(total_hp / max(1, total_hp_max), 0.0, 1.0)

    # 1. Outcome Score (weight 0.35)
    outcome_score = round(
        _clamp(
            40.0 * clear_ratio
            + (15.0 if winner == "party" else 0.0)
            + 25.0 * final_survival_ratio
            + 20.0 * final_hp_ratio
        ),
        2,
    )

    # 2–4. Mean of per-room legality, tactical, coordination scores
    if rooms:
        room_cards = [r.get("scorecard") or {} for r in rooms]
        rules_legality_score = round(
            _clamp(sum(float(c.get("rules_legality_score", 0.0)) for c in room_cards) / len(room_cards)),
            2,
        )
        tactical_score = round(
            _clamp(sum(float(c.get("tactical_score", 0.0)) for c in room_cards) / len(room_cards)),
            2,
        )
        coordination_score = round(
            _clamp(sum(float(c.get("coordination_score", 0.0)) for c in room_cards) / len(room_cards)),
            2,
        )
    else:
        rules_legality_score = 0.0
        tactical_score = 0.0
        coordination_score = 0.0

    # 5. Resource Conservation Score (weight 0.15, scaled by rooms_cleared / total_rooms)
    slots_remaining_ratio = _clamp(slots_rem / slots_max_sum, 0.0, 1.0) if slots_max_sum > 0 else 1.0
    hit_dice_remaining_ratio = _clamp(hd_rem / hd_max_sum, 0.0, 1.0) if hd_max_sum > 0 else 1.0
    class_resources_remaining_ratio = _clamp(cr_rem / cr_max_sum, 0.0, 1.0) if cr_max_sum > 0 else 1.0

    raw_conservation = (
        40.0 * slots_remaining_ratio
        + 30.0 * hit_dice_remaining_ratio
        + 30.0 * class_resources_remaining_ratio
    )
    resource_conservation_score = round(_clamp(clear_ratio * raw_conservation), 2)

    composite_score = round(
        _clamp(
            DUNGEON_SCORE_WEIGHTS["outcome"] * outcome_score
            + DUNGEON_SCORE_WEIGHTS["rules_legality"] * rules_legality_score
            + DUNGEON_SCORE_WEIGHTS["tactical"] * tactical_score
            + DUNGEON_SCORE_WEIGHTS["coordination"] * coordination_score
            + DUNGEON_SCORE_WEIGHTS["resource_conservation"] * resource_conservation_score
        ),
        2,
    )

    return {
        "outcome_score": outcome_score,
        "rules_legality_score": rules_legality_score,
        "tactical_score": tactical_score,
        "coordination_score": coordination_score,
        "resource_conservation_score": resource_conservation_score,
        "composite_score": composite_score,
        "weights": dict(DUNGEON_SCORE_WEIGHTS),
        "metrics": {
            "winner": winner,
            "rooms_cleared": rooms_cleared,
            "total_rooms": total_rooms,
            "total_rounds": sum(int(r.get("rounds", 0)) for r in rooms),
            "short_rests_taken": sum(1 for r in rests if bool(r.get("taken", False))),
            "final_survival_ratio": round(final_survival_ratio, 4),
            "final_hp_ratio": round(final_hp_ratio, 4),
            "slots_remaining_ratio": round(slots_remaining_ratio, 4),
            "hit_dice_remaining_ratio": round(hit_dice_remaining_ratio, 4),
            "class_resources_remaining_ratio": round(class_resources_remaining_ratio, 4),
        },
    }


# ----------------------------------------------------------------------
# Multi-Room Dungeon Runner & Deterministic Replay (§3.3)
# ----------------------------------------------------------------------


def _has_conscious_living_pc(party_carryover: dict[str, dict[str, Any]]) -> bool:
    return any(
        bool(co.get("alive", False)) and int(co.get("hp", 0)) > 0
        for co in party_carryover.values()
    )


def _has_any_living_pc(party_carryover: dict[str, dict[str, Any]]) -> bool:
    return any(bool(co.get("alive", False)) for co in party_carryover.values())


def run_dungeon(
    dungeon: str | Path | dict[str, Any],
    *,
    party_driver: AgentDriver | dict[str, AgentDriver],
    enemy_driver: AgentDriver | dict[str, AgentDriver],
    rules: RulesStore,
    seed: int = 42,
    allow_short_rest: bool = True,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
    rest_planner: Callable[[dict[str, dict[str, Any]], int, dict[str, Any]], dict[str, dict[str, Any]] | bool | None] | None = None,
) -> dict[str, Any]:
    """Run a sequential multi-room 5e 2024 (SRD 5.2) dungeon with carryover & Short Rests."""
    compiled = load_dungeon(dungeon, rules)
    party_carryover = init_party_carryover(compiled["party"], rules)
    total_rooms = len(compiled["rooms"])
    effective_allow_rest = bool(allow_short_rest) and bool(compiled.get("allow_short_rest_between_rooms", True))

    rooms_traces: list[dict[str, Any]] = []
    rests_records: list[dict[str, Any]] = []
    rooms_cleared = 0
    last_winner = "enemy"

    for room_idx, room in enumerate(compiled["rooms"]):
        if not _has_conscious_living_pc(party_carryover):
            break

        room_scenario = build_room_scenario(compiled, room_idx, party_carryover)
        room_seed = int(seed) + room_idx * 1000
        room_trace = run_match(
            room_scenario,
            party_driver=party_driver,
            enemy_driver=enemy_driver,
            rules=rules,
            seed=room_seed,
            max_actions_per_turn=max_actions_per_turn,
            max_retries_per_action=max_retries_per_action,
        )
        rooms_traces.append(room_trace)
        last_winner = str(room_trace.get("winner") or "draw")
        room_won = last_winner == "party"
        party_carryover = extract_party_carryover(
            room_trace["final_state"],
            party_carryover,
            room_won=room_won,
        )

        if not room_won:
            break

        rooms_cleared += 1

        if room_idx < total_rooms - 1:
            if not _has_any_living_pc(party_carryover):
                break
            if effective_allow_rest:
                plan = rest_planner(party_carryover, room_idx, room) if callable(rest_planner) else None
                if plan is False:
                    rests_records.append(
                        {
                            "after_room_index": room_idx,
                            "after_room_id": room["id"],
                            "taken": False,
                            "per_pc": {},
                        }
                    )
                else:
                    rest_rng = DeterministicRNG(seed=(int(seed) ^ 0x52455354) + room_idx)
                    party_carryover, rest_rec = resolve_short_rest(
                        party_carryover,
                        rng=rest_rng,
                        plan=plan if isinstance(plan, dict) else None,
                    )
                    rests_records.append(
                        {
                            "after_room_index": room_idx,
                            "after_room_id": room["id"],
                            **rest_rec,
                        }
                    )
            else:
                rests_records.append(
                    {
                        "after_room_index": room_idx,
                        "after_room_id": room["id"],
                        "taken": False,
                        "per_pc": {},
                    }
                )

            if not _has_conscious_living_pc(party_carryover):
                break

    winner = "party" if rooms_cleared == total_rooms else last_winner
    final_party_state = _format_final_party_state(party_carryover)

    trace: dict[str, Any] = {
        "schema_version": DUNGEON_SCHEMA_VERSION,
        "kind": "dungeon_trace",
        "dataset_id": rules.dataset_id,
        "dungeon": {
            "id": compiled["id"],
            "name": compiled["name"],
            "tier": int(compiled["tier"]),
            "party_level": int(compiled["party_level"]),
            "total_rooms": total_rooms,
        },
        "config": {
            "seed": int(seed),
            "party_driver": _describe_driver_config(party_driver),
            "enemy_driver": _describe_driver_config(enemy_driver),
            "allow_short_rest": effective_allow_rest,
            "max_actions_per_turn": int(max_actions_per_turn),
            "max_retries_per_action": int(max_retries_per_action),
        },
        "winner": winner,
        "rooms_cleared": rooms_cleared,
        "total_rooms": total_rooms,
        "rooms": rooms_traces,
        "rests": rests_records,
        "final_party_state": final_party_state,
    }
    trace["scorecard"] = score_dungeon(trace, rules)
    trace["digest"] = compute_dungeon_digest(trace)
    return trace


def _plan_from_recorded_rest(
    rest_record: dict[str, Any],
    party_carryover: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build an explicit `resolve_short_rest` plan from a recorded ShortRestRecord."""
    plan: dict[str, dict[str, Any]] = {}
    per_pc = rest_record.get("per_pc") or {}
    for cid, co in party_carryover.items():
        rec = per_pc.get(cid)
        if not isinstance(rec, dict):
            plan[cid] = {"hit_dice_to_spend": 0}
            continue
        entry: dict[str, Any] = {"hit_dice_to_spend": int(rec.get("hit_dice_spent", 0))}
        slots_rec = rec.get("slots_recovered") or {}
        if slots_rec and not co.get("is_pact_magic", False):
            entry["arcane_recovery_slots"] = {str(k): int(v) for k, v in slots_rec.items()}
        plan[cid] = entry
    return plan


def replay_dungeon(
    trace: dict[str, Any],
    rules: RulesStore,
) -> dict[str, Any]:
    """Deterministically replay a `dungeon_trace` across every room and Short Rest."""
    schema = str(trace.get("schema_version") or "")
    kind = str(trace.get("kind") or "")
    dungeon_meta = trace.get("dungeon") or {}
    cfg = trace.get("config") or {}
    dungeon_id = str(dungeon_meta.get("id") or "")
    seed = int(cfg.get("seed", 42))

    if schema != DUNGEON_SCHEMA_VERSION or kind != "dungeon_trace":
        msg = (
            f"Unsupported dungeon trace format (schema_version={schema!r}, kind={kind!r}); "
            f"expected schema_version={DUNGEON_SCHEMA_VERSION!r} and kind='dungeon_trace'."
        )
        return {
            "status": "error",
            "replay_status": "error",
            "error": msg,
            "dungeon_id": dungeon_id,
            "seed": seed,
            "deterministic_match": False,
            "original_digest": str(trace.get("digest") or ""),
            "replayed_digest": "",
            "room_results": [],
            "rest_matches": [],
            "diffs": [msg],
        }

    recorded_rooms: list[dict[str, Any]] = list(trace.get("rooms") or [])
    recorded_rests: list[dict[str, Any]] = list(trace.get("rests") or [])
    rests_by_idx = {int(r.get("after_room_index", -1)): r for r in recorded_rests if isinstance(r, dict)}

    diffs: list[str] = []
    room_results: list[dict[str, Any]] = []
    replayed_rooms: list[dict[str, Any]] = []
    replayed_rests: list[dict[str, Any]] = []
    rest_matches: list[bool] = []

    party_carryover: dict[str, dict[str, Any]] = {}
    if recorded_rooms:
        first_room = recorded_rooms[0]
        party_ids = list((first_room.get("scenario") or {}).get("party_ids") or [])
        if not party_ids:
            init_combs = (first_room.get("initial_state") or {}).get("combatants") or {}
            party_ids = [cid for cid, c in init_combs.items() if str(c.get("side")) == "party"]
        party_carryover = _init_party_carryover_from_initial_state(
            first_room.get("initial_state") or {},
            party_ids,
            rules,
        )

    rooms_cleared = 0
    for idx, room_trace in enumerate(recorded_rooms):
        r_res = replay_match(room_trace, rules)
        room_results.append(
            {
                "room_index": idx,
                "scenario_id": r_res.get("scenario_id"),
                "deterministic_match": bool(r_res.get("deterministic_match")),
                "original_digest": r_res.get("original_digest"),
                "replayed_digest": r_res.get("replayed_digest"),
                "diffs": list(r_res.get("diffs") or []),
            }
        )
        if not r_res.get("deterministic_match"):
            diffs.append(f"Room {idx} replay mismatch: {'; '.join(r_res.get('diffs') or ['digest mismatch'])}")

        rep_room = r_res.get("replayed_transcript") or room_trace
        replayed_rooms.append(rep_room)
        room_won = str(rep_room.get("winner") or "") == "party"
        if room_won:
            rooms_cleared += 1

        if party_carryover:
            party_carryover = extract_party_carryover(
                rep_room.get("final_state") or {},
                party_carryover,
                room_won=room_won,
            )

        rec_rest = rests_by_idx.get(idx)
        if rec_rest is not None:
            if bool(rec_rest.get("taken", False)):
                rest_rng = DeterministicRNG(seed=(seed ^ 0x52455354) + idx)
                rest_plan = _plan_from_recorded_rest(rec_rest, party_carryover)
                try:
                    party_carryover, rep_rest_body = resolve_short_rest(
                        party_carryover,
                        rng=rest_rng,
                        plan=rest_plan,
                    )
                    rep_rest = {
                        "after_room_index": idx,
                        "after_room_id": rec_rest.get("after_room_id"),
                        **rep_rest_body,
                    }
                except ValueError as exc:
                    rep_rest = {"error": str(exc)}
                    diffs.append(f"Short rest after room {idx} failed: {exc}")
            else:
                rep_rest = {
                    "after_room_index": idx,
                    "after_room_id": rec_rest.get("after_room_id"),
                    "taken": False,
                    "per_pc": {},
                }
            replayed_rests.append(rep_rest)
            matched = rep_rest == rec_rest
            rest_matches.append(matched)
            if not matched:
                diffs.append(f"Short rest after room {idx} differed from recorded rest.")

    replayed_final_party = _format_final_party_state(party_carryover) if party_carryover else {}
    if replayed_final_party != (trace.get("final_party_state") or {}):
        diffs.append("final_party_state differs between original and replayed dungeon trace.")

    total_rooms = int(trace.get("total_rooms") or dungeon_meta.get("total_rooms") or len(recorded_rooms))
    replayed_winner = (
        "party"
        if rooms_cleared == total_rooms and total_rooms > 0
        else (str(replayed_rooms[-1].get("winner") or "enemy") if replayed_rooms else "enemy")
    )
    if replayed_winner != trace.get("winner"):
        diffs.append(f"winner mismatch: original={trace.get('winner')!r}, replayed={replayed_winner!r}")
    if rooms_cleared != int(trace.get("rooms_cleared", 0)):
        diffs.append(
            f"rooms_cleared mismatch: original={trace.get('rooms_cleared')}, replayed={rooms_cleared}"
        )

    replayed_trace: dict[str, Any] = {
        "schema_version": DUNGEON_SCHEMA_VERSION,
        "kind": "dungeon_trace",
        "dataset_id": trace.get("dataset_id", rules.dataset_id),
        "dungeon": copy.deepcopy(dungeon_meta),
        "config": copy.deepcopy(cfg),
        "winner": replayed_winner,
        "rooms_cleared": rooms_cleared,
        "total_rooms": total_rooms,
        "rooms": replayed_rooms,
        "rests": replayed_rests,
        "final_party_state": replayed_final_party,
    }
    replayed_trace["scorecard"] = score_dungeon(replayed_trace, rules)
    replayed_digest = compute_dungeon_digest(replayed_trace)
    replayed_trace["digest"] = replayed_digest

    expected_orig_digest = compute_dungeon_digest(trace)
    original_digest = str(trace.get("digest") or expected_orig_digest)
    if original_digest != replayed_digest or original_digest != expected_orig_digest:
        diffs.append(f"digest mismatch: original={original_digest}, replayed={replayed_digest}")

    deterministic_match = (
        original_digest == replayed_digest
        and original_digest == expected_orig_digest
        and len(diffs) == 0
        and all(r["deterministic_match"] for r in room_results)
        and all(rest_matches)
    )

    return {
        "status": "ok",
        "replay_status": "match" if deterministic_match else "mismatch",
        "dungeon_id": dungeon_id,
        "seed": seed,
        "deterministic_match": deterministic_match,
        "original_digest": original_digest,
        "replayed_digest": replayed_digest,
        "room_results": room_results,
        "rest_matches": rest_matches,
        "diffs": diffs,
        "replayed_trace": replayed_trace,
    }


# ----------------------------------------------------------------------
# Interactive CLI Dungeon Play (`bin/srd-arena dungeon-play`)
# ----------------------------------------------------------------------


def _prompt_cli_short_rest(
    party_carryover: dict[str, dict[str, Any]],
    room: dict[str, Any],
    next_room: dict[str, Any],
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> dict[str, dict[str, Any]] | bool | None:
    """Interactive CLI prompt between dungeon rooms; returns None (auto), False (skip), or plan dict."""
    output_fn(f"\n=== Room Cleared: {room['name']} -> Next: {next_room['name']} ===")
    output_fn("Party Status before Short Rest:")
    for cid, co in party_carryover.items():
        hd = co["hit_dice"]
        status = f"HP {co['hp']}/{co['hp_max']}" if co["alive"] else "DEAD"
        output_fn(f"  - {co['name']} ({cid}): {status} | Hit Dice {hd['remaining']}/{hd['max']} d{hd['die']}")
    output_fn("Take a Short Rest? [Y]es (auto Hit Dice & Arcane Recovery) / [n]o (skip rest)")
    try:
        answer = input_fn("Short rest [Y/n]: ").strip().lower()
    except EOFError:
        return None
    if answer in ("n", "no", "skip"):
        return False
    return None


def run_interactive_dungeon(
    dungeon: str | Path | dict[str, Any],
    *,
    rules: RulesStore,
    play_as: str | None = None,
    seat_overrides: dict[str, Any] | list[str] | None = None,
    party_agent: str | dict[str, Any] = "heuristic-tactical",
    enemy_agent: str | dict[str, Any] = "heuristic-tactical",
    nl_driver: Any = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    seed: int = 42,
    allow_short_rest: bool = True,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
    out_trace: str | Path | None = None,
    force: bool = False,
    api_base: str | None = None,
    api_key_env: str | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Run an interactive multi-room CLI dungeon with Short Rest prompts between rooms."""
    compiled = load_dungeon(dungeon, rules)
    party_carryover = init_party_carryover(compiled["party"], rules)
    total_rooms = len(compiled["rooms"])
    effective_allow_rest = bool(allow_short_rest) and bool(compiled.get("allow_short_rest_between_rooms", True))

    rooms_traces: list[dict[str, Any]] = []
    rests_records: list[dict[str, Any]] = []
    rooms_cleared = 0
    last_winner = "enemy"

    output_fn(
        f"=== Starting Dungeon: {compiled['name']} ({total_rooms} rooms, Tier {compiled['tier']}) ==="
    )

    for room_idx, room in enumerate(compiled["rooms"]):
        if not _has_conscious_living_pc(party_carryover):
            break

        output_fn(f"\n--- Entering {room['name']} ({room_idx + 1}/{total_rooms}) ---")
        room_scenario = build_room_scenario(compiled, room_idx, party_carryover)
        room_seed = int(seed) + room_idx * 1000
        room_result = run_interactive_session(
            room_scenario,
            rules=rules,
            play_as=play_as,
            seat_overrides=seat_overrides,
            party_agent=party_agent,
            enemy_agent=enemy_agent,
            nl_driver=nl_driver,
            input_fn=input_fn,
            output_fn=output_fn,
            seed=room_seed,
            max_actions_per_turn=max_actions_per_turn,
            max_retries_per_action=max_retries_per_action,
            api_base=api_base,
            api_key_env=api_key_env,
            temperature=temperature,
        )
        if room_result.get("status") == "paused":
            return {
                "status": "paused",
                "dungeon_id": compiled["id"],
                "room_index": room_idx,
                "rooms_cleared": rooms_cleared,
                "paused_room": room_result,
            }

        rooms_traces.append(room_result)
        last_winner = str(room_result.get("winner") or "draw")
        room_won = last_winner == "party"
        party_carryover = extract_party_carryover(
            room_result["final_state"],
            party_carryover,
            room_won=room_won,
        )
        if not room_won:
            break

        rooms_cleared += 1
        if room_idx < total_rooms - 1:
            if not _has_any_living_pc(party_carryover):
                break
            if effective_allow_rest:
                plan = _prompt_cli_short_rest(
                    party_carryover,
                    room,
                    compiled["rooms"][room_idx + 1],
                    input_fn=input_fn,
                    output_fn=output_fn,
                )
                if plan is False:
                    rests_records.append(
                        {
                            "after_room_index": room_idx,
                            "after_room_id": room["id"],
                            "taken": False,
                            "per_pc": {},
                        }
                    )
                else:
                    rest_rng = DeterministicRNG(seed=(int(seed) ^ 0x52455354) + room_idx)
                    party_carryover, rest_rec = resolve_short_rest(
                        party_carryover,
                        rng=rest_rng,
                        plan=plan if isinstance(plan, dict) else None,
                    )
                    rests_records.append(
                        {
                            "after_room_index": room_idx,
                            "after_room_id": room["id"],
                            **rest_rec,
                        }
                    )
                    output_fn("Short Rest complete:")
                    for cid, r_pc in rest_rec["per_pc"].items():
                        output_fn(
                            f"  - {cid}: HP {r_pc['hp_before']} -> {r_pc['hp_after']} "
                            f"(HD spent: {r_pc['hit_dice_spent']}, left: {r_pc['hit_dice_remaining']})"
                        )
            else:
                rests_records.append(
                    {
                        "after_room_index": room_idx,
                        "after_room_id": room["id"],
                        "taken": False,
                        "per_pc": {},
                    }
                )
            if not _has_conscious_living_pc(party_carryover):
                break

    winner = "party" if rooms_cleared == total_rooms else last_winner
    final_party_state = _format_final_party_state(party_carryover)

    trace: dict[str, Any] = {
        "schema_version": DUNGEON_SCHEMA_VERSION,
        "kind": "dungeon_trace",
        "dataset_id": rules.dataset_id,
        "dungeon": {
            "id": compiled["id"],
            "name": compiled["name"],
            "tier": int(compiled["tier"]),
            "party_level": int(compiled["party_level"]),
            "total_rooms": total_rooms,
        },
        "config": {
            "seed": int(seed),
            "party_driver": str(party_agent),
            "enemy_driver": str(enemy_agent),
            "allow_short_rest": effective_allow_rest,
            "max_actions_per_turn": int(max_actions_per_turn),
            "max_retries_per_action": int(max_retries_per_action),
        },
        "winner": winner,
        "rooms_cleared": rooms_cleared,
        "total_rooms": total_rooms,
        "rooms": rooms_traces,
        "rests": rests_records,
        "final_party_state": final_party_state,
    }
    trace["scorecard"] = score_dungeon(trace, rules)
    trace["digest"] = compute_dungeon_digest(trace)

    if out_trace is not None:
        trace_path = Path(out_trace).expanduser()
        if trace_path.exists() and not force:
            raise FileExistsError(
                f"Refusing to overwrite '{trace_path}'. Pass --force to replace it."
            )
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(trace, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return trace


# ----------------------------------------------------------------------
# Markdown Renderers
# ----------------------------------------------------------------------


def render_dungeons_markdown(dungeons: list[dict[str, Any]]) -> str:
    """Render built-in 5e 2024 (SRD 5.2) dungeons catalog as Markdown."""
    lines: list[str] = [
        "# 5e 2024 (SRD 5.2) Multi-Encounter Dungeons",
        "",
        "| ID | Name | Tier | Level | Rooms | Short Rests |",
        "|---|---|---:|---:|---:|---|",
    ]
    for d in dungeons:
        lines.append(
            f"| `{d['id']}` | {d['name']} | {d['tier']} | {d['party_level']} "
            f"| {d['total_rooms']} | {'Yes' if d.get('allow_short_rest_between_rooms', True) else 'No'} |"
        )
    for d in dungeons:
        lines.extend(["", f"## {d['name']} (`{d['id']}`)", "", d.get("description", ""), ""])
        lines.append(f"- **Party:** {', '.join(d.get('party_summary') or [])}")
        for idx, rm in enumerate(d.get("rooms") or [], start=1):
            lines.append(
                f"- **Room {idx} — {rm['name']}** (`{rm['id']}`, max {rm['max_rounds']} rounds): "
                f"{', '.join(rm.get('enemy_summary') or [])}"
            )
    return "\n".join(lines)


def render_dungeon_markdown(dungeon_trace: dict[str, Any]) -> str:
    """Render a `dungeon_trace` (`schema_version: '5'`) as a structured Markdown report."""
    d_meta = dungeon_trace.get("dungeon") or {}
    cfg = dungeon_trace.get("config") or {}
    sc = dungeon_trace.get("scorecard") or {}
    rooms = dungeon_trace.get("rooms") or []
    rests = dungeon_trace.get("rests") or []
    final_party = dungeon_trace.get("final_party_state") or {}

    lines: list[str] = [
        f"# Dungeon Report: {d_meta.get('name', d_meta.get('id', 'Dungeon'))}",
        "",
        f"- **Dungeon ID:** `{d_meta.get('id', '')}` (Tier {d_meta.get('tier', 2)}, L{d_meta.get('party_level', 5)})",
        f"- **Outcome:** **{str(dungeon_trace.get('winner', '')).upper()}** "
        f"({dungeon_trace.get('rooms_cleared', 0)}/{dungeon_trace.get('total_rooms', len(rooms))} rooms cleared)",
        f"- **Drivers:** Party `{cfg.get('party_driver', '')}` vs. Enemy `{cfg.get('enemy_driver', '')}` "
        f"| **Seed:** `{cfg.get('seed', 0)}` | **Short Rests:** {'Enabled' if cfg.get('allow_short_rest', True) else 'Disabled'}",
        f"- **Digest:** `{str(dungeon_trace.get('digest', ''))[:16]}...`",
        "",
        "## 5-Dimension Dungeon Scorecard",
        "",
        "| Dimension | Weight | Score |",
        "|---|---:|---:|",
        f"| Outcome | 35% | {float(sc.get('outcome_score', 0.0)):.2f} |",
        f"| Rules Legality | 20% | {float(sc.get('rules_legality_score', 0.0)):.2f} |",
        f"| Tactical | 20% | {float(sc.get('tactical_score', 0.0)):.2f} |",
        f"| Coordination | 10% | {float(sc.get('coordination_score', 0.0)):.2f} |",
        f"| Resource Conservation | 15% | {float(sc.get('resource_conservation_score', 0.0)):.2f} |",
        f"| **Composite** | **100%** | **{float(sc.get('composite_score', 0.0)):.2f}** |",
        "",
        "## Room-by-Room Breakdown",
        "",
        "| Room | Winner | Rounds | Room Composite | Digest |",
        "|---|---|---:|---:|---|",
    ]
    for idx, rm in enumerate(rooms, start=1):
        r_meta = rm.get("scenario") or {}
        r_sc = rm.get("scorecard") or {}
        lines.append(
            f"| {idx}. {r_meta.get('name', r_meta.get('id', f'Room {idx}'))} "
            f"| {rm.get('winner', '')} | {rm.get('rounds', 0)} "
            f"| {float(r_sc.get('composite_score', 0.0)):.2f} "
            f"| `{str(rm.get('digest', ''))[:12]}` |"
        )

    if rests:
        lines.extend(["", "## Inter-Room Short Rests", ""])
        for rest in rests:
            after_id = rest.get("after_room_id", f"room-{rest.get('after_room_index', 0)}")
            if not rest.get("taken", False):
                lines.append(f"- After `{after_id}`: *Skipped*")
                continue
            lines.append(f"### After `{after_id}`")
            lines.append("")
            lines.append("| PC | HP | HD Spent (Rolls) | HD Left | Resources Recovered | Slots Recovered |")
            lines.append("|---|---|---|---:|---|---|")
            for cid, pc_r in (rest.get("per_pc") or {}).items():
                rolls_str = ",".join(str(x) for x in (pc_r.get("hit_dice_rolls") or [])) or "—"
                res_str = (
                    ", ".join(f"{k}+{v}" for k, v in (pc_r.get("resources_recovered") or {}).items())
                    or "—"
                )
                sl_str = (
                    ", ".join(f"L{k}+{v}" for k, v in (pc_r.get("slots_recovered") or {}).items())
                    or "—"
                )
                lines.append(
                    f"| `{cid}` | {pc_r.get('hp_before', 0)} -> {pc_r.get('hp_after', 0)} "
                    f"| {pc_r.get('hit_dice_spent', 0)} ({rolls_str}) | {pc_r.get('hit_dice_remaining', 0)} "
                    f"| {res_str} | {sl_str} |"
                )
            lines.append("")

    if final_party:
        lines.extend(
            [
                "## Final Party State",
                "",
                "| PC | Status | HP | Hit Dice | Spell Slots | Class Resources |",
                "|---|---|---:|---|---|---|",
            ]
        )
        for cid, pc in final_party.items():
            status = "Alive" if pc.get("alive") else "Dead"
            hd = pc.get("hit_dice") or {}
            hd_str = f"{hd.get('remaining', 0)}/{hd.get('max', 0)} d{hd.get('die', 8)}"
            slots = pc.get("slots") or {}
            slots_max = pc.get("slots_max") or {}
            sl_str = (
                ", ".join(f"L{k}:{slots.get(k, 0)}/{mx}" for k, mx in sorted(slots_max.items()))
                or "—"
            )
            cr = pc.get("class_resources") or {}
            cr_str = (
                ", ".join(f"{k}:{v.get('remaining', 0)}/{v.get('max', 0)}" for k, v in sorted(cr.items()))
                or "—"
            )
            lines.append(
                f"| `{cid}` | {status} | {pc.get('hp', 0)}/{pc.get('hp_max', 0)} "
                f"| {hd_str} | {sl_str} | {cr_str} |"
            )

    return "\n".join(lines)


def render_dungeon_replay_markdown(result: dict[str, Any]) -> str:
    """Render a `replay_dungeon()` verification report as Markdown."""
    if result.get("status") == "error":
        return f"**Error:** {result.get('error', 'Dungeon replay failed.')}"
    verdict = "MATCH" if result.get("deterministic_match") else "MISMATCH"
    lines: list[str] = [
        f"# Dungeon Replay Verification: {verdict}",
        "",
        f"- **Dungeon ID:** `{result.get('dungeon_id', '')}` | **Seed:** `{result.get('seed', 0)}`",
        f"- **Original Digest:** `{result.get('original_digest', '')}`",
        f"- **Replayed Digest:** `{result.get('replayed_digest', '')}`",
        f"- **Rooms Verified:** {len(result.get('room_results') or [])} "
        f"| **Short Rests Verified:** {len(result.get('rest_matches') or [])}",
    ]
    diffs = result.get("diffs") or []
    if diffs:
        lines.extend(["", "## Diffs", ""])
        for d in diffs:
            lines.append(f"- {d}")
    return "\n".join(lines)


__all__ = [
    "BUILTIN_DUNGEON_IDS",
    "DUNGEON_SCHEMA_VERSION",
    "DUNGEON_SCORE_WEIGHTS",
    "_SPEC_SEREN_L5",
    "auto_short_rest_plan",
    "build_room_scenario",
    "compute_dungeon_digest",
    "extract_party_carryover",
    "init_party_carryover",
    "list_builtin_dungeons",
    "load_dungeon",
    "render_dungeon_markdown",
    "render_dungeon_replay_markdown",
    "render_dungeons_markdown",
    "replay_dungeon",
    "resolve_short_rest",
    "run_dungeon",
    "run_interactive_dungeon",
    "score_dungeon",
]
