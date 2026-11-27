"""Curated 5e 2024 (SRD 5.2) arena scenarios and custom scenario JSON loader.

Implements STAGE3-SPEC §3:
- Three empirically verified built-in scenarios (`goblin-ambush`, `crypt-of-ghouls`,
  `wyrmling-lair`) whose PCs compile cleanly via `compile_character()` and whose
  monsters have `coverage == "full"` in `dist/rules.json`.
- Custom scenario loading from JSON files or inline dicts with strict validation.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from srd2024.engine.character import compile_character
from srd2024.engine.store import RulesStore, strip_prefix

BUILTIN_SCENARIO_IDS: tuple[str, ...] = (
    "goblin-ambush",
    "crypt-of-ghouls",
    "wyrmling-lair",
)

# ----------------------------------------------------------------------
# Verified Built-In CharacterBuildSpecs (STAGE3-SPEC §3.1)
# ----------------------------------------------------------------------

_SPEC_BRAM_L1: dict[str, Any] = {
    "name": "Bram Ironhand",
    "species": "human",
    "subspecies": None,
    "size": "Medium",
    "background": "soldier",
    "background_asi": {"str": 2, "con": 1},
    "origin_feat": "savage-attacker",
    "extra_origin_feat": "alert",
    "class": "fighter",
    "subclass": None,
    "level": 1,
    "ability_method": "point_buy",
    "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
    "asi_choices": [],
    "fighting_style": "defense",
    "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
    "equipment": {
        "armor": "chain-mail",
        "shield": True,
        "weapons": ["longsword", "shortbow", "javelin"],
    },
    "weapon_mastery_weapons": ["longsword", "shortbow", "javelin"],
    "cantrips": [],
    "prepared_spells": [],
    "hp_method": "fixed",
}

_SPEC_TALIA_L1: dict[str, Any] = {
    "name": "Talia Spark",
    "species": "elf",
    "subspecies": "elven-lineage-high-elf",
    "size": "Medium",
    "background": "sage",
    "background_asi": {"int": 2, "con": 1},
    "origin_feat": "magic-initiate",
    "class": "wizard",
    "subclass": None,
    "level": 1,
    "ability_method": "standard_array",
    "base_abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
    "asi_choices": [],
    "skill_proficiencies": ["arcana", "history", "investigation", "insight"],
    "equipment": {
        "armor": None,
        "shield": False,
        "weapons": ["quarterstaff", "dagger"],
    },
    "cantrips": ["fire-bolt", "ray-of-frost", "acid-splash"],
    "prepared_spells": ["shield", "magic-missile", "mage-armor", "burning-hands"],
    "hp_method": "fixed",
}

_SPEC_ALDRIC_L5: dict[str, Any] = {
    "name": "Aldric Vance",
    "species": "human",
    "subspecies": None,
    "size": "Medium",
    "background": "soldier",
    "background_asi": {"str": 2, "con": 1},
    "origin_feat": "savage-attacker",
    "extra_origin_feat": "alert",
    "class": "fighter",
    "subclass": "champion",
    "level": 5,
    "ability_method": "point_buy",
    "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
    "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}],
    "fighting_style": "defense",
    "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
    "equipment": {
        "armor": "chain-mail",
        "shield": True,
        "weapons": ["longsword", "greatsword", "javelin"],
    },
    "weapon_mastery_weapons": ["longsword", "greatsword", "javelin", "battleaxe"],
    "cantrips": [],
    "prepared_spells": [],
    "hp_method": "fixed",
}

_SPEC_LYRA_L5: dict[str, Any] = {
    "name": "Lyra Starweave",
    "species": "elf",
    "subspecies": "elven-lineage-high-elf",
    "size": "Medium",
    "background": "sage",
    "background_asi": {"int": 2, "con": 1},
    "origin_feat": "magic-initiate",
    "class": "wizard",
    "subclass": "evoker",
    "level": 5,
    "ability_method": "standard_array",
    "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
    "asi_choices": [{"level": 4, "type": "asi", "increases": {"int": 2}}],
    "skill_proficiencies": ["arcana", "history", "investigation", "insight"],
    "equipment": {
        "armor": None,
        "shield": False,
        "weapons": ["quarterstaff", "dagger"],
    },
    "cantrips": ["fire-bolt", "ray-of-frost", "acid-splash", "chill-touch"],
    "prepared_spells": [
        "fireball",
        "lightning-bolt",
        "shatter",
        "misty-step",
        "scorching-ray",
        "magic-missile",
        "shield",
        "mage-armor",
        "burning-hands",
        "hold-person",
    ],
    "hp_method": "fixed",
}

_SPEC_DORIAN_L5: dict[str, Any] = {
    "name": "Dorian Dawnshield",
    "species": "dwarf",
    "subspecies": None,
    "size": "Medium",
    "background": "acolyte",
    "background_asi": {"wis": 2, "cha": 1},
    "origin_feat": "magic-initiate",
    "class": "cleric",
    "subclass": "life-domain",
    "level": 5,
    "ability_method": "standard_array",
    "base_abilities": {"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 15, "cha": 12},
    "asi_choices": [{"level": 4, "type": "asi", "increases": {"wis": 2}}],
    "skill_proficiencies": ["insight", "religion", "medicine", "persuasion"],
    "equipment": {
        "armor": "scale-mail",
        "shield": True,
        "weapons": ["mace"],
    },
    "cantrips": ["sacred-flame", "guidance", "light", "resistance"],
    "prepared_spells": [
        "bless",
        "cure-wounds",
        "healing-word",
        "guiding-bolt",
        "shield-of-faith",
        "aid",
        "hold-person",
        "mass-healing-word",
        "spirit-guardians",
    ],
    "hp_method": "fixed",
}

_SPEC_KAELEN_L5: dict[str, Any] = {
    "name": "Kaelen Whisper",
    "species": "halfling",
    "subspecies": None,
    "size": "Small",
    "background": "criminal",
    "background_asi": {"dex": 2, "con": 1},
    "origin_feat": "alert",
    "class": "rogue",
    "subclass": "thief",
    "level": 5,
    "ability_method": "standard_array",
    "base_abilities": {"str": 8, "dex": 15, "con": 14, "int": 12, "wis": 13, "cha": 10},
    "asi_choices": [{"level": 4, "type": "asi", "increases": {"dex": 2}}],
    "skill_proficiencies": [
        "stealth",
        "sleight-of-hand",
        "acrobatics",
        "perception",
        "investigation",
        "deception",
    ],
    "equipment": {
        "armor": "studded-leather-armor",
        "shield": False,
        "weapons": ["shortsword", "dagger", "shortbow"],
    },
    "weapon_mastery_weapons": ["shortsword", "dagger"],
    "cantrips": [],
    "prepared_spells": [],
    "hp_method": "fixed",
}

# ----------------------------------------------------------------------
# Built-In Scenario Definitions
# ----------------------------------------------------------------------

_BUILTIN_SCENARIOS: dict[str, dict[str, Any]] = {
    "goblin-ambush": {
        "id": "goblin-ambush",
        "name": "Goblin Ambush",
        "description": (
            "Tier 1 5e 2024 (SRD 5.2) skirmish: two Level 1 adventurers (Fighter and Wizard) "
            "repel two Goblin Warriors and a Goblin Minion testing reach vs. ranged positioning, 2024 Weapon "
            "Masteries (Sap, Vex, Slow), Shield/Mage Armor economy, and focus fire."
        ),
        "tier": 1,
        "party_level": 1,
        "max_rounds": 10,
        "tactical_themes": [
            "positioning",
            "weapon-mastery",
            "spell-economy",
            "focus-fire",
        ],
        "party": [
            {"id": "pc-bram", "spec": _SPEC_BRAM_L1, "position": {"x": 0, "y": 0}},
            {"id": "pc-talia", "spec": _SPEC_TALIA_L1, "position": {"x": 0, "y": 15}},
        ],
        "enemies": [
            {
                "id": "goblin-1",
                "monster": "goblin-warrior",
                "name": "Goblin Scout A",
                "position": {"x": 20, "y": 0},
            },
            {
                "id": "goblin-2",
                "monster": "goblin-warrior",
                "name": "Goblin Scout B",
                "position": {"x": 25, "y": 15},
            },
            {
                "id": "goblin-minion-1",
                "monster": "goblin-minion",
                "name": "Goblin Runt",
                "position": {"x": 25, "y": 5},
            },
        ],
    },
    "crypt-of-ghouls": {
        "id": "crypt-of-ghouls",
        "name": "Crypt of Ghouls",
        "description": (
            "Tier 2 5e 2024 (SRD 5.2) undead gauntlet: three Level 5 adventurers (Champion "
            "Fighter, Evoker Wizard, Life Cleric) face four Ghouls, two Ghasts, a Mummy, and "
            "a Skeleton archer, testing AoE Fireball placement, Paralysis threat management, 0-HP revival "
            "via Healing Word, Bless concentration, Extra Attack, and Weapon Masteries."
        ),
        "tier": 2,
        "party_level": 5,
        "max_rounds": 12,
        "tactical_themes": [
            "aoe-discipline",
            "paralysis-threat",
            "revival-triage",
            "concentration",
            "weapon-mastery",
        ],
        "party": [
            {"id": "pc-aldric", "spec": _SPEC_ALDRIC_L5, "position": {"x": 0, "y": 5}},
            {"id": "pc-lyra", "spec": _SPEC_LYRA_L5, "position": {"x": 0, "y": 20}},
            {"id": "pc-dorian", "spec": _SPEC_DORIAN_L5, "position": {"x": 5, "y": 15}},
        ],
        "enemies": [
            {
                "id": "ghoul-1",
                "monster": "ghoul",
                "name": "Crypt Ghoul A",
                "position": {"x": 25, "y": 0},
            },
            {
                "id": "ghoul-2",
                "monster": "ghoul",
                "name": "Crypt Ghoul B",
                "position": {"x": 25, "y": 10},
            },
            {
                "id": "ghast-1",
                "monster": "ghast",
                "name": "Crypt Ghast",
                "position": {"x": 30, "y": 5},
            },
            {
                "id": "skeleton-1",
                "monster": "skeleton",
                "name": "Crypt Archer",
                "position": {"x": 35, "y": 15},
            },
            {
                "id": "mummy-1",
                "monster": "mummy",
                "name": "Crypt Mummy",
                "position": {"x": 35, "y": 5},
            },
            {
                "id": "ghast-2",
                "monster": "ghast",
                "name": "Crypt Ghast B",
                "position": {"x": 30, "y": 15},
            },
            {
                "id": "ghoul-3",
                "monster": "ghoul",
                "name": "Crypt Ghoul C",
                "position": {"x": 35, "y": 25},
            },
            {
                "id": "ghoul-4",
                "monster": "ghoul",
                "name": "Crypt Ghoul D",
                "position": {"x": 20, "y": -5},
            },
        ],
    },
    "wyrmling-lair": {
        "id": "wyrmling-lair",
        "name": "Wyrmling Lair",
        "description": (
            "Tier 2 5e 2024 (SRD 5.2) boss encounter: four Level 5 adventurers (Fighter, "
            "Wizard, Cleric, Rogue) battle a Red Dragon Wyrmling, two Cinder Hounds (one "
            "held back), and an Ember Magmin—all fire-immune—testing damage-immunity awareness, breath-weapon spacing, "
            "Bless + Shield economy, Vex + Nick mastery combos, and multi-round focus fire."
        ),
        "tier": 2,
        "party_level": 5,
        "max_rounds": 15,
        "tactical_themes": [
            "immunity-awareness",
            "breath-weapon-spacing",
            "spell-economy",
            "weapon-mastery",
            "focus-fire",
        ],
        "party": [
            {"id": "pc-aldric", "spec": _SPEC_ALDRIC_L5, "position": {"x": 0, "y": 0}},
            {"id": "pc-lyra", "spec": _SPEC_LYRA_L5, "position": {"x": 0, "y": 25}},
            {"id": "pc-dorian", "spec": _SPEC_DORIAN_L5, "position": {"x": 5, "y": 15}},
            {"id": "pc-kaelen", "spec": _SPEC_KAELEN_L5, "position": {"x": 10, "y": 20}},
        ],
        "enemies": [
            {
                "id": "wyrmling-1",
                "monster": "red-dragon-wyrmling",
                "name": "Red Dragon Wyrmling",
                "position": {"x": 30, "y": 10},
            },
            {
                "id": "hound-1",
                "monster": "hell-hound",
                "name": "Cinder Hound",
                "position": {"x": 25, "y": 0},
            },
            {
                "id": "magmin-1",
                "monster": "magmin",
                "name": "Ember Magmin",
                "position": {"x": 25, "y": 20},
            },
            {
                "id": "hound-2",
                "monster": "hell-hound",
                "name": "Cinder Hound B",
                "position": {"x": 50, "y": 30},
            },
        ],
    },
}


def _summarize_party_entry(entry: dict[str, Any]) -> str:
    spec = entry.get("spec") or entry.get("statblock") or {}
    name = str(entry.get("name") or spec.get("name") or entry.get("id") or "PC")
    cls = strip_prefix(str(spec.get("class") or "adventurer")).title()
    lvl = int(spec.get("level", 1))
    return f"{name} (L{lvl} {cls})"


def _summarize_enemy_entry(entry: dict[str, Any]) -> str:
    name = str(entry.get("name") or entry.get("id") or "Enemy")
    monster = strip_prefix(str(entry.get("monster") or "custom"))
    return f"{name} ({monster})"


def list_scenarios() -> list[dict[str, Any]]:
    """Return compact sorted metadata for all built-in 5e 2024 (SRD 5.2) scenarios."""
    rows: list[dict[str, Any]] = []
    for sid in BUILTIN_SCENARIO_IDS:
        raw = _BUILTIN_SCENARIOS[sid]
        rows.append(
            {
                "id": raw["id"],
                "name": raw["name"],
                "tier": int(raw["tier"]),
                "party_level": int(raw["party_level"]),
                "max_rounds": int(raw["max_rounds"]),
                "description": raw["description"],
                "party_summary": [_summarize_party_entry(p) for p in raw["party"]],
                "enemy_summary": [_summarize_enemy_entry(e) for e in raw["enemies"]],
                "tactical_themes": list(raw["tactical_themes"]),
            }
        )
    rows.sort(key=lambda r: (r["tier"], r["party_level"], r["id"]))
    return rows


def _parse_position(raw_pos: Any, combatant_id: str) -> dict[str, int]:
    if raw_pos is None:
        return {"x": 0, "y": 0}
    if not isinstance(raw_pos, dict) or "x" not in raw_pos or "y" not in raw_pos:
        raise ValueError(f"Combatant '{combatant_id}' position must be a dict with 'x' and 'y'.")
    try:
        return {"x": int(raw_pos["x"]), "y": int(raw_pos["y"])}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Combatant '{combatant_id}' position coordinates must be integers.") from exc


def _copy_optional_combatant_fields(src: dict[str, Any], dst: dict[str, Any]) -> None:
    for key in ("hp", "hp_max", "temp_hp", "exhaustion_level", "conditions"):
        if key in src and src[key] is not None:
            dst[key] = copy.deepcopy(src[key])


def _compile_raw_scenario(
    raw: dict[str, Any],
    rules: RulesStore,
    source: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Scenario definition must be a JSON object.")

    # Allow passing an already-compiled scenario dict cleanly after validation.
    if (
        isinstance(raw.get("combatants"), list)
        and raw["combatants"]
        and isinstance(raw.get("party_ids"), list)
        and raw["party_ids"]
        and isinstance(raw.get("enemy_ids"), list)
        and raw["enemy_ids"]
        and "party" not in raw
        and "enemies" not in raw
    ):
        compiled_copy = copy.deepcopy(raw)
        compiled_copy.setdefault("source", source)
        return compiled_copy

    scenario_id = str(raw.get("id") or "").strip()
    if not scenario_id:
        raise ValueError("Scenario must define a non-empty 'id'.")

    party_raw = raw.get("party")
    enemies_raw = raw.get("enemies")
    if not isinstance(party_raw, list) or not party_raw:
        raise ValueError(f"Scenario '{scenario_id}' must define a non-empty 'party' list.")
    if not isinstance(enemies_raw, list) or not enemies_raw:
        raise ValueError(f"Scenario '{scenario_id}' must define a non-empty 'enemies' list.")

    seen_ids: set[str] = set()
    combatants: list[dict[str, Any]] = []
    party_ids: list[str] = []
    enemy_ids: list[str] = []
    inferred_levels: list[int] = []

    for idx, entry in enumerate(party_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Scenario '{scenario_id}' party[{idx}] must be an object.")
        cid = str(entry.get("id") or "").strip()
        if not cid:
            raise ValueError(f"Scenario '{scenario_id}' party[{idx}] is missing a non-empty 'id'.")
        if cid in seen_ids:
            raise ValueError(f"Scenario '{scenario_id}' has duplicate combatant id '{cid}'.")
        seen_ids.add(cid)

        pos = _parse_position(entry.get("position"), cid)
        if "spec" in entry and entry["spec"] is not None:
            if not isinstance(entry["spec"], dict):
                raise ValueError(f"Party combatant '{cid}' 'spec' must be an object.")
            compiled = compile_character(entry["spec"], rules)
            if not compiled.get("valid"):
                err_msgs = "; ".join(
                    f"{e.get('path', '?')}: {e.get('message', e.get('code', 'invalid'))}"
                    for e in (compiled.get("errors") or [])
                )
                raise ValueError(
                    f"Invalid PC spec for '{cid}' in scenario '{scenario_id}': {err_msgs or 'compilation failed'}"
                )
            statblock = copy.deepcopy(compiled["statblock"])
        elif "statblock" in entry and isinstance(entry["statblock"], dict):
            statblock = copy.deepcopy(entry["statblock"])
        else:
            raise ValueError(
                f"Party combatant '{cid}' in scenario '{scenario_id}' must provide 'spec' or 'statblock'."
            )

        inferred_levels.append(int(statblock.get("level", 1)))
        comb_entry: dict[str, Any] = {
            "id": cid,
            "name": str(entry.get("name") or statblock.get("name") or cid),
            "side": "party",
            "statblock": statblock,
            "position": pos,
        }
        _copy_optional_combatant_fields(entry, comb_entry)
        combatants.append(comb_entry)
        party_ids.append(cid)

    for idx, entry in enumerate(enemies_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Scenario '{scenario_id}' enemies[{idx}] must be an object.")
        cid = str(entry.get("id") or "").strip()
        if not cid:
            raise ValueError(f"Scenario '{scenario_id}' enemies[{idx}] is missing a non-empty 'id'.")
        if cid in seen_ids:
            raise ValueError(f"Scenario '{scenario_id}' has duplicate combatant id '{cid}'.")
        seen_ids.add(cid)

        pos = _parse_position(entry.get("position"), cid)
        if "monster" in entry and entry["monster"] is not None:
            m_slug = strip_prefix(str(entry["monster"]))
            m_rec = rules.get_monster(m_slug)
            if m_rec is None:
                raise ValueError(
                    f"Unknown monster slug '{entry['monster']}' for '{cid}' in scenario '{scenario_id}'."
                )
            comb_entry = {
                "id": cid,
                "name": str(entry.get("name") or m_rec.get("name") or cid),
                "side": "enemy",
                "monster": m_slug,
                "position": pos,
            }
        elif "statblock" in entry and isinstance(entry["statblock"], dict):
            sb = copy.deepcopy(entry["statblock"])
            comb_entry = {
                "id": cid,
                "name": str(entry.get("name") or sb.get("name") or cid),
                "side": "enemy",
                "statblock": sb,
                "position": pos,
            }
        else:
            raise ValueError(
                f"Enemy combatant '{cid}' in scenario '{scenario_id}' must provide 'monster' or 'statblock'."
            )

        _copy_optional_combatant_fields(entry, comb_entry)
        combatants.append(comb_entry)
        enemy_ids.append(cid)

    party_level = int(raw.get("party_level") or (max(inferred_levels) if inferred_levels else 1))
    tier = int(raw.get("tier") or (1 if party_level <= 4 else (2 if party_level <= 10 else 3)))
    max_rounds = int(raw.get("max_rounds") or 10)
    if max_rounds < 1:
        raise ValueError(f"Scenario '{scenario_id}' max_rounds must be >= 1, got {max_rounds}.")

    return {
        "id": scenario_id,
        "name": str(raw.get("name") or scenario_id),
        "description": str(raw.get("description") or ""),
        "tier": tier,
        "party_level": party_level,
        "max_rounds": max_rounds,
        "tactical_themes": [str(t) for t in (raw.get("tactical_themes") or [])],
        "combatants": combatants,
        "party_ids": party_ids,
        "enemy_ids": enemy_ids,
        "source": source,
    }


def load_scenario(
    name_or_path: str | Path | dict[str, Any],
    rules: RulesStore,
) -> dict[str, Any]:
    """Load a built-in scenario slug, custom scenario JSON file path, or inline dict.

    Compiles every PC spec via `compile_character(spec, rules)` and validates every
    monster slug exists in `rules`. Raises `ValueError` on any invalid scenario input.
    """
    if isinstance(name_or_path, dict):
        return _compile_raw_scenario(name_or_path, rules, source="inline")

    if isinstance(name_or_path, Path):
        path = name_or_path.expanduser()
        if not path.is_file():
            raise ValueError(f"Scenario file not found: '{path}'.")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to parse scenario JSON '{path}': {exc}") from exc
        return _compile_raw_scenario(raw, rules, source="file")

    if isinstance(name_or_path, str):
        key = name_or_path.strip()
        if not key:
            raise ValueError("Scenario name or path cannot be empty.")
        key_lower = key.lower()
        if key_lower in _BUILTIN_SCENARIOS:
            return _compile_raw_scenario(copy.deepcopy(_BUILTIN_SCENARIOS[key_lower]), rules, source="builtin")
        path = Path(key).expanduser()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Failed to parse scenario JSON '{path}': {exc}") from exc
            return _compile_raw_scenario(raw, rules, source="file")
        valid_list = ", ".join(BUILTIN_SCENARIO_IDS)
        raise ValueError(
            f"Unknown scenario '{name_or_path}'. Expected one of [{valid_list}] or a valid JSON file path."
        )

    raise ValueError(f"Unsupported scenario input type: {type(name_or_path).__name__}.")
