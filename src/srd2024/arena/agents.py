"""Pluggable Multi-Agent Drivers for the Stage 3 5e 2024 (SRD 5.2) Eval Arena.

Implements docs/STAGE3-SPEC.md §1 & §4 strictly using Python 3.11+ stdlib only
and Stage 2 `srd2024.engine` primitives (zero third-party imports; strictly
runtime-only engine boundary).

Exports:
- Core types: `AgentDecision`, `AgentDriver`
- Prompting & JSON parsing: `build_system_prompt`, `build_user_prompt`,
  `extract_decision_json`
- Offline deterministic drivers: `RandomDriver`, `GreedyDriver`,
  `HeuristicTacticalDriver`, `ReplayDriver`
- Pure-stdlib HTTP LLM drivers: `BaseLLMDriver`, `OpenAICompatDriver`,
  `AnthropicDriver`, `GeminiDriver`
- Factory: `create_driver`
"""

from __future__ import annotations

import copy
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from srd2024.engine.combat import distance_ft, grid_square
from srd2024.engine.dice import parse_dice_expr, scale_dice_expr
from srd2024.engine.store import RulesStore, strip_prefix

HttpTransport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]

# ---------------------------------------------------------------------------
# Core Data Types & Protocol (§4.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentDecision:
    """Structured decision emitted by an Arena agent driver for one action slot."""

    intent: dict[str, Any]
    rationale: str = ""
    table_talk: str | None = None
    raw_response: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class AgentDriver(Protocol):
    """Protocol implemented by all offline and live LLM Arena drivers."""

    name: str

    def decide(self, observation: dict[str, Any]) -> AgentDecision: ...

    def reset(self, seed: int | None = None) -> None: ...


# ---------------------------------------------------------------------------
# Shared Rules, Dice, Geometry & Observation Helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)

# Known AoE geometries for spells whose compiled rules.json `aoe` field is None.
_KNOWN_SPELL_AOE: dict[str, dict[str, Any]] = {
    "fireball": {"shape": "sphere", "radius_ft": 20, "range_ft": 150},
    "shatter": {"shape": "sphere", "radius_ft": 10, "range_ft": 60},
    "lightning-bolt": {"shape": "line", "length_ft": 100, "width_ft": 5, "range_ft": 100},
    "burning-hands": {"shape": "cone", "size_ft": 15, "range_ft": 15},
    "cone-of-cold": {"shape": "cone", "size_ft": 60, "range_ft": 60},
    "spirit-guardians": {"shape": "emanation", "radius_ft": 15, "range_ft": 15},
}

_HIGH_THREAT_MONSTERS: dict[str, float] = {
    "red-dragon-wyrmling": 20.0,
    "hell-hound": 16.0,
    "ghast": 18.0,
    "ghoul": 14.0,
    "magmin": 10.0,
    "goblin-warrior": 8.0,
    "skeleton": 7.0,
}

_BREATH_THREAT_MONSTERS: frozenset[str] = frozenset(
    {"red-dragon-wyrmling", "hell-hound", "adult-red-dragon", "young-red-dragon"}
)


def _ensure_rules(rules: RulesStore | None) -> RulesStore:
    if rules is not None:
        return rules
    return RulesStore()


def _expected_dice_value(expr: str | None) -> float:
    """Return exact mathematical expectation of a dice expression like '2d6+3'."""
    if not expr:
        return 0.0
    try:
        terms, flat = parse_dice_expr(str(expr))
    except ValueError:
        return 0.0
    total = float(flat)
    for sign, count, sides in terms:
        total += sign * count * (sides + 1) / 2.0
    return max(0.0, total)


def _cantrip_scaled_dice(base_dice: str, scaling: dict[str, Any] | None, caster_level: int) -> str:
    tiers = (scaling or {}).get("cantrip_tiers")
    if not isinstance(tiers, dict) or not tiers:
        return base_dice
    chosen = base_dice
    for threshold in sorted(int(k) for k in tiers.keys()):
        if caster_level >= threshold:
            chosen = str(tiers[str(threshold)] if str(threshold) in tiers else tiers[threshold])
    return chosen


def _condition_set(entity: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for entry in entity.get("conditions") or []:
        if isinstance(entry, dict):
            sl = strip_prefix(entry.get("condition", ""))
        else:
            sl = strip_prefix(str(entry))
        if sl:
            out.add(sl)
    return out


def _effect_kinds(entity: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("active_effects", "visible_effects"):
        for entry in entity.get(key) or []:
            if isinstance(entry, dict):
                k = str(entry.get("kind") or "").strip().lower()
            else:
                k = str(entry).strip().lower()
            if k:
                out.add(k)
    return out


def _available_slots_map(self_data: dict[str, Any]) -> dict[int, int]:
    sc = self_data.get("spellcasting")
    if not isinstance(sc, dict):
        return {}
    out: dict[int, int] = {}
    for k, v in (sc.get("slots") or {}).items():
        try:
            lvl = int(k)
            cnt = int(v)
        except (TypeError, ValueError):
            continue
        if cnt > 0:
            out[lvl] = cnt
    return out


def _lowest_slot(slots: dict[int, int], min_level: int) -> int | None:
    if min_level <= 0:
        return 0
    for lvl in sorted(slots.keys()):
        if lvl >= min_level and slots[lvl] > 0:
            return lvl
    return None


def _highest_slot(slots: dict[int, int], min_level: int) -> int | None:
    if min_level <= 0:
        return 0
    for lvl in sorted(slots.keys(), reverse=True):
        if lvl >= min_level and slots[lvl] > 0:
            return lvl
    return None


def _all_known_spells(observation: dict[str, Any]) -> list[str]:
    """Return deduplicated list of spells the active actor knows/prepares."""
    self_data = observation.get("self") or {}
    legal = observation.get("legal_actions") or {}
    sc = self_data.get("spellcasting") or {}
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in (
        list(legal.get("spells") or [])
        + list(legal.get("reaction_spells") or [])
        + list(sc.get("prepared_spells") or [])
        + list(sc.get("cantrips") or [])
    ):
        sl = strip_prefix(str(raw))
        if sl and sl not in seen:
            seen.add(sl)
            ordered.append(sl)
    return ordered


def _can_cast_spell_now(
    spell_slug: str,
    spell_rec: dict[str, Any],
    self_data: dict[str, Any],
    slots: dict[int, int],
    *,
    allow_reaction: bool = False,
) -> tuple[bool, str, int]:
    """Check action economy and slot availability for `spell_slug`.

    Returns `(can_cast, action_type, lowest_available_slot)`.
    """
    sc = self_data.get("spellcasting")
    if not isinstance(sc, dict):
        return False, "action", 0
    res = self_data.get("turn_resources") or {}
    action_type = str(spell_rec.get("action_type") or "action").lower()
    if action_type == "reaction":
        if not allow_reaction or not res.get("reaction"):
            return False, action_type, 0
    elif action_type == "bonus_action":
        if not res.get("bonus_action"):
            return False, action_type, 0
    else:
        if not res.get("action"):
            return False, action_type, 0

    sp_lvl = int(spell_rec.get("level", 0))
    if sp_lvl == 0:
        return True, action_type, 0
    if res.get("slot_spent_this_turn"):
        return False, action_type, 0  # one spell slot per turn (#52); cantrips are still fine
    slot = _lowest_slot(slots, sp_lvl)
    if slot is None:
        return False, action_type, 0
    return True, action_type, slot


def _alive_enemies(observation: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in observation.get("enemies") or []:
        if not e.get("alive", False):
            continue
        if e.get("hp_band") == "defeated":
            continue
        if "hp" in e and int(e["hp"]) <= 0:
            continue
        out.append(e)
    return out


def _conscious_allies(observation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        a
        for a in (observation.get("allies") or [])
        if a.get("alive", False) and int(a.get("hp", 0)) > 0 and not a.get("unconscious", False)
    ]


def _downed_allies(observation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        a
        for a in (observation.get("allies") or [])
        if a.get("alive", False) and (int(a.get("hp", 1)) == 0 or bool(a.get("unconscious", False)))
    ]


def _available_monster_actions(observation: dict[str, Any]) -> list[dict[str, Any]]:
    self_data = observation.get("self") or {}
    legal = observation.get("legal_actions") or {}
    all_actions = list(self_data.get("monster_actions") or [])
    if not all_actions:
        return []

    avail_names: list[str] | None = None
    if "available_monster_actions" in legal and legal["available_monster_actions"] is not None:
        avail_names = [str(x).strip().lower() for x in legal["available_monster_actions"]]
    elif "available_monster_actions" in self_data and self_data["available_monster_actions"] is not None:
        avail_names = [str(x).strip().lower() for x in self_data["available_monster_actions"]]

    if avail_names is None:
        return all_actions
    avail_set = set(avail_names)
    return [a for a in all_actions if str(a.get("name") or "").strip().lower() in avail_set]


def _monster_defense_profile(enemy: dict[str, Any], rules: RulesStore) -> tuple[set[str], set[str], set[str]]:
    """Look up `(immunities, resistances, vulnerabilities)` from `RulesStore` via `source_id`."""
    src = enemy.get("source_id")
    if not src:
        return set(), set(), set()
    m_rec = rules.get_monster(str(src))
    if not isinstance(m_rec, dict):
        return set(), set(), set()
    imm = {strip_prefix(x) for x in (m_rec.get("damage_immunities") or []) if x}
    res = {strip_prefix(x) for x in (m_rec.get("damage_resistances") or []) if x}
    vuln = {strip_prefix(x) for x in (m_rec.get("damage_vulnerabilities") or []) if x}
    return imm, res, vuln


def _damage_multiplier(dtype: str, imm: set[str], res: set[str], vuln: set[str]) -> float:
    dt = strip_prefix(dtype)
    if dt in imm:
        return 0.0
    is_res = dt in res
    is_vuln = dt in vuln
    if is_res and not is_vuln:
        return 0.5
    if is_vuln and not is_res:
        return 2.0
    return 1.0


def _weapon_max_range(weapon: dict[str, Any]) -> tuple[int, int, bool]:
    """Return `(normal_range_ft, max_range_ft, is_ranged_only)` for a PC weapon profile."""
    w_range = str(weapon.get("weapon_range") or "melee").lower()
    if w_range == "ranged":
        normal = int(weapon.get("range_normal_ft") or 80)
        long_r = int(weapon.get("range_long_ft") or normal)
        return normal, long_r, True
    reach = int(weapon.get("reach_ft") or 5)
    throw_range = weapon.get("throw_range")
    if isinstance(throw_range, dict):
        normal = int(throw_range.get("normal_ft") or reach)
        long_r = int(throw_range.get("long_ft") or normal)
        return max(reach, normal), max(reach, long_r), False
    return reach, reach, False


def _weapon_expected_damage(
    weapon: dict[str, Any],
    *,
    omit_positive_mod: bool = False,
) -> float:
    dice_val = _expected_dice_value(str(weapon.get("damage_dice") or "1"))
    bonus = int(weapon.get("damage_bonus", 0))
    if omit_positive_mod and bonus > 0:
        bonus = 0
    return max(0.0, dice_val + bonus)


def _spell_expected_damage_per_target(
    spell_slug: str,
    spell_rec: dict[str, Any],
    *,
    caster_level: int,
    slot_level: int,
    spell_mod: int = 0,
    enemy: dict[str, Any] | None = None,
    rules: RulesStore | None = None,
    ignore_defenses: bool = False,
) -> tuple[float, bool]:
    """Compute `(expected_damage, hits_immunity)` for one target.

    `hits_immunity` is True if any positive-damage part targets a damage type
    the enemy is immune to (when `ignore_defenses` is False).
    """
    dmg_list = list(spell_rec.get("damage") or [])
    if not dmg_list:
        return 0.0, False

    sp_lvl = int(spell_rec.get("level", 0))
    upcast = max(0, slot_level - sp_lvl) if sp_lvl >= 1 else 0
    scaling = spell_rec.get("scaling") or {}
    special = spell_rec.get("special_effect") or {}
    resolution = str(spell_rec.get("resolution") or "utility").lower()

    if enemy is not None and rules is not None and not ignore_defenses:
        imm, res, vuln = _monster_defense_profile(enemy, rules)
    else:
        imm, res, vuln = set(), set(), set()

    # Determine projectile count for multi-projectile spells.
    num_proj = 1
    if resolution == "auto_hit":
        base_p = int(
            (special.get("projectiles_base") if isinstance(special, dict) else None)
            or (3 if spell_slug == "magic-missile" else 1)
        )
        per_p = int(scaling.get("slot_projectiles_per_level") or (1 if spell_slug == "magic-missile" else 0))
        num_proj = base_p + upcast * per_p
    elif resolution == "attack" and (
        spell_slug == "scorching-ray"
        or (isinstance(special, dict) and special.get("projectiles_base"))
    ):
        base_p = int(special.get("projectiles_base", 3)) if isinstance(special, dict) else 3
        per_p = int(scaling.get("slot_projectiles_per_level") or 1)
        num_proj = base_p + upcast * per_p
    elif spell_slug == "eldritch-blast":
        num_proj = 4 if caster_level >= 17 else (3 if caster_level >= 11 else (2 if caster_level >= 5 else 1))

    per_proj = 0.0
    hits_immune = False
    for idx, d in enumerate(dmg_list):
        base_d = str(d.get("dice") or "0")
        if sp_lvl == 0 and spell_slug != "eldritch-blast":
            eff_d = _cantrip_scaled_dice(base_d, scaling, caster_level)
        elif idx == 0 and upcast > 0 and scaling.get("slot_damage_per_level"):
            eff_d = scale_dice_expr(base_d, str(scaling["slot_damage_per_level"]), upcast)
        else:
            eff_d = base_d
        raw_val = _expected_dice_value(eff_d) + (spell_mod if d.get("add_spell_mod") else 0)
        if raw_val <= 0:
            continue
        dtype = strip_prefix(str(d.get("type") or "bludgeoning"))
        if ignore_defenses:
            mult = 1.0
        else:
            mult = _damage_multiplier(dtype, imm, res, vuln)
            if mult == 0.0:
                hits_immune = True
        per_proj += raw_val * mult

    return per_proj * num_proj, hits_immune


def _spell_aoe_spec(spell_slug: str, spell_rec: dict[str, Any]) -> dict[str, Any] | None:
    aoe = spell_rec.get("aoe")
    if isinstance(aoe, dict) and aoe.get("shape"):
        return aoe
    return _KNOWN_SPELL_AOE.get(spell_slug)


def _spell_effective_range_ft(spell_slug: str, spell_rec: dict[str, Any]) -> int:
    aoe = _spell_aoe_spec(spell_slug, spell_rec)
    if aoe and aoe.get("range_ft"):
        return int(aoe["range_ft"])
    r = spell_rec.get("range_ft")
    if r is not None and int(r) > 0:
        return int(r)
    rk = str(spell_rec.get("range_kind") or "").lower()
    if rk == "touch":
        return 5
    if rk == "self":
        if aoe:
            return int(aoe.get("size_ft") or aoe.get("length_ft") or aoe.get("radius_ft") or 15)
        return 0
    return 60


def _sphere_aoe_targets(
    self_pos: dict[str, int],
    enemies: list[dict[str, Any]],
    friendlies: list[dict[str, Any]],
    *,
    range_ft: int,
    radius_ft: int,
    avoid_friendlies: bool,
) -> list[dict[str, Any]]:
    """Find best enemy subset covered by a sphere of `radius_ft` within `range_ft`."""
    in_range = [e for e in enemies if distance_ft(self_pos, e["position"]) <= range_ft + radius_ft]
    if not in_range:
        return []

    # Candidate centers: each enemy position and midpoints of pairs, snapped to 5-ft grid.
    centers: list[dict[str, int]] = []
    seen_c: set[tuple[int, int]] = set()

    def _add_center(cx: int, cy: int) -> None:
        gx = int(round(cx / 5.0) * 5)
        gy = int(round(cy / 5.0) * 5)
        if (gx, gy) in seen_c:
            return
        if distance_ft(self_pos, {"x": gx, "y": gy}) <= range_ft:
            seen_c.add((gx, gy))
            centers.append({"x": gx, "y": gy})

    for e in in_range:
        ex, ey = int(e["position"]["x"]), int(e["position"]["y"])
        _add_center(ex, ey)
        # Also test slightly offset centers away from friendlies so we can clip an enemy safely.
        for ox in (-radius_ft, 0, radius_ft):
            for oy in (-radius_ft, 0, radius_ft):
                _add_center(ex + ox, ey + oy)
    for i in range(len(in_range)):
        for j in range(i + 1, len(in_range)):
            e1, e2 = in_range[i]["position"], in_range[j]["position"]
            _add_center((int(e1["x"]) + int(e2["x"])) // 2, (int(e1["y"]) + int(e2["y"])) // 2)

    best: list[dict[str, Any]] = []
    for c in centers:
        if avoid_friendlies and any(distance_ft(c, f["position"]) <= radius_ft for f in friendlies):
            continue
        hit = [e for e in in_range if distance_ft(c, e["position"]) <= radius_ft]
        if len(hit) > len(best):
            best = hit
    return best


def _line_aoe_targets(
    self_pos: dict[str, int],
    enemies: list[dict[str, Any]],
    friendlies: list[dict[str, Any]],
    *,
    length_ft: int,
    width_ft: int,
    avoid_friendlies: bool,
) -> list[dict[str, Any]]:
    """Find best enemy subset along a ray of length `length_ft` and width `width_ft` from `self_pos`."""
    sx, sy = int(self_pos["x"]), int(self_pos["y"])
    half_w = max(5.0, width_ft / 2.0 + 2.5)
    candidates = [e for e in enemies if 0 < distance_ft(self_pos, e["position"]) <= length_ft]
    if not candidates:
        return []

    best: list[dict[str, Any]] = []
    for anchor in candidates:
        ax = int(anchor["position"]["x"]) - sx
        ay = int(anchor["position"]["y"]) - sy
        norm = math.hypot(ax, ay)
        if norm == 0:
            continue
        ux, uy = ax / norm, ay / norm

        def _on_ray(pos: dict[str, int]) -> bool:
            px = int(pos["x"]) - sx
            py = int(pos["y"]) - sy
            proj = px * ux + py * uy
            if proj <= 0 or proj > length_ft:
                return False
            perp = abs(px * uy - py * ux)
            return perp <= half_w

        if avoid_friendlies and any(_on_ray(f["position"]) for f in friendlies if f["position"] != self_pos):
            continue
        hit = [e for e in candidates if _on_ray(e["position"])]
        if len(hit) > len(best):
            best = hit
    return best


def _cone_aoe_targets(
    origin: dict[str, int],
    enemies: list[dict[str, Any]],
    friendlies: list[dict[str, Any]],
    *,
    size_ft: int,
    avoid_friendlies: bool,
) -> list[dict[str, Any]]:
    """Find best enemy subset inside a 5e cone of length `size_ft` from `origin`."""
    ox, oy = int(origin["x"]), int(origin["y"])
    in_cone_dist = [e for e in enemies if 0 < distance_ft(origin, e["position"]) <= size_ft]
    if not in_cone_dist:
        return []

    best: list[dict[str, Any]] = []
    # Test aiming directly at each candidate enemy.
    for anchor in in_cone_dist:
        ax = int(anchor["position"]["x"]) - ox
        ay = int(anchor["position"]["y"]) - oy
        norm = math.hypot(ax, ay)
        if norm == 0:
            continue
        ux, uy = ax / norm, ay / norm

        def _in_cone(pos: dict[str, int]) -> bool:
            d = distance_ft(origin, pos)
            if d == 0 or d > size_ft:
                return False
            px = int(pos["x"]) - ox
            py = int(pos["y"]) - oy
            proj = px * ux + py * uy
            if proj <= 0:
                return False
            # 5e cone width at distance proj equals proj -> half-width = proj / 2 (plus grid tolerance).
            perp = abs(px * uy - py * ux)
            return perp <= (proj * 0.55 + 2.5)

        if avoid_friendlies and any(_in_cone(f["position"]) for f in friendlies if f["position"] != origin):
            continue
        hit = [e for e in in_cone_dist if _in_cone(e["position"])]
        if len(hit) > len(best):
            best = hit
    return best


def _aoe_targets_for_spell(
    spell_slug: str,
    spell_rec: dict[str, Any],
    self_pos: dict[str, int],
    enemies: list[dict[str, Any]],
    friendlies: list[dict[str, Any]],
    *,
    avoid_friendlies: bool,
) -> list[dict[str, Any]]:
    aoe = _spell_aoe_spec(spell_slug, spell_rec)
    if not aoe:
        return []
    shape = str(aoe.get("shape") or "").lower()
    if shape == "sphere":
        return _sphere_aoe_targets(
            self_pos,
            enemies,
            friendlies,
            range_ft=int(aoe.get("range_ft") or spell_rec.get("range_ft") or 60),
            radius_ft=int(aoe.get("radius_ft") or aoe.get("size_ft") or 10),
            avoid_friendlies=avoid_friendlies,
        )
    if shape == "line":
        return _line_aoe_targets(
            self_pos,
            enemies,
            friendlies,
            length_ft=int(aoe.get("length_ft") or aoe.get("size_ft") or 100),
            width_ft=int(aoe.get("width_ft") or 5),
            avoid_friendlies=avoid_friendlies,
        )
    if shape == "cone":
        return _cone_aoe_targets(
            self_pos,
            enemies,
            friendlies,
            size_ft=int(aoe.get("size_ft") or 15),
            avoid_friendlies=avoid_friendlies,
        )
    if shape == "emanation":
        rad = int(aoe.get("radius_ft") or aoe.get("size_ft") or 15)
        return [e for e in enemies if distance_ft(self_pos, e["position"]) <= rad]
    return []


def _occupied_squares(observation: dict[str, Any]) -> set[tuple[int, int]]:
    """5-ft squares the acting creature can't end a move in: every other living creature's (#53).

    Mirrors `combat.square_occupant`, which stays the authority: unconscious allies at 0 HP still
    occupy their square, defeated monsters don't. Tiny creatures never block (engine rule); the
    observation only exposes size for enemies, so allies are treated as non-Tiny.
    """
    self_data = observation.get("self") or {}
    if str(self_data.get("size") or "Medium").strip().lower() == "tiny":
        return set()
    out: set[tuple[int, int]] = set()
    for a in observation.get("allies") or []:
        if a.get("alive", False) and isinstance(a.get("position"), dict):
            out.add(grid_square(a["position"]))
    for e in observation.get("enemies") or []:
        if not e.get("alive", False) or not isinstance(e.get("position"), dict):
            continue
        if str(e.get("size") or "Medium").strip().lower() == "tiny":
            continue
        out.add(grid_square(e["position"]))
    return out


def _is_free(pos: dict[str, int], occupied: set[tuple[int, int]] | None) -> bool:
    return not occupied or grid_square(pos) not in occupied


def _step_toward(
    origin: dict[str, int],
    target_pos: dict[str, int],
    max_move_ft: int,
    *,
    desired_dist_ft: int = 5,
    occupied: set[tuple[int, int]] | None = None,
) -> dict[str, int]:
    """Compute a 5-ft grid destination moving from `origin` toward `target_pos`.

    With `occupied`, the destination is never another creature's square (#53): if the straight-line
    step ends on one, the nearest free square within `max_move_ft` that still gets closer is used,
    else `origin` (no move).
    """
    cur_dist = distance_ft(origin, target_pos)
    if cur_dist <= desired_dist_ft or max_move_ft < 5:
        return {"x": int(origin["x"]), "y": int(origin["y"])}
    dest = _straight_step_toward(origin, target_pos, max_move_ft, desired_dist_ft=desired_dist_ft)
    if _is_free(dest, occupied) or dest == {"x": int(origin["x"]), "y": int(origin["y"])}:
        return dest
    return _nearest_free_toward(origin, target_pos, max_move_ft, desired_dist_ft, dest, occupied or set())


def _nearest_free_toward(
    origin: dict[str, int],
    target_pos: dict[str, int],
    max_move_ft: int,
    desired_dist_ft: int,
    ideal: dict[str, int],
    occupied: set[tuple[int, int]],
) -> dict[str, int]:
    """Best unoccupied square within reach of `origin` that gets closer to `target_pos`.

    Ranked by: reaching `desired_dist_ft`, then distance to the target, then distance to the
    blocked `ideal` square, then movement spent, then coordinates (deterministic).
    """
    ox, oy = int(origin["x"]), int(origin["y"])
    cur_dist = distance_ft(origin, target_pos)
    steps = max_move_ft // 5
    best: tuple[Any, ...] | None = None
    best_pos = {"x": ox, "y": oy}
    for dxs in range(-steps, steps + 1):
        for dys in range(-steps, steps + 1):
            if dxs == 0 and dys == 0:
                continue
            cand = {"x": ox + dxs * 5, "y": oy + dys * 5}
            if grid_square(cand) in occupied:
                continue
            d_t = distance_ft(cand, target_pos)
            if d_t >= cur_dist:
                continue
            key = (
                max(d_t, desired_dist_ft),
                d_t,
                distance_ft(cand, ideal),
                distance_ft(origin, cand),
                cand["x"],
                cand["y"],
            )
            if best is None or key < best:
                best, best_pos = key, cand
    return best_pos


def _straight_step_toward(
    origin: dict[str, int],
    target_pos: dict[str, int],
    max_move_ft: int,
    *,
    desired_dist_ft: int = 5,
) -> dict[str, int]:
    """Straight 5-ft-step path toward `target_pos`, ignoring other creatures."""
    steps_budget = max_move_ft // 5
    cx, cy = int(origin["x"]), int(origin["y"])
    tx, ty = int(target_pos["x"]), int(target_pos["y"])

    for _ in range(steps_budget):
        if distance_ft({"x": cx, "y": cy}, {"x": tx, "y": ty}) <= desired_dist_ft:
            break
        dx = tx - cx
        dy = ty - cy
        nx = cx + (5 if dx > 0 else (-5 if dx < 0 else 0))
        ny = cy + (5 if dy > 0 else (-5 if dy < 0 else 0))
        if nx == tx and ny == ty and desired_dist_ft > 0:
            # Avoid stepping onto target square if we can stop adjacent.
            if abs(dx) >= abs(dy) and cx != tx:
                nx = cx
            elif cy != ty:
                ny = cy
            if nx == cx and ny == cy:
                break
        cx, cy = nx, ny
    return {"x": cx, "y": cy}


def _best_kite_position(
    origin: dict[str, int],
    max_move_ft: int,
    enemies: list[dict[str, Any]],
    allies: list[dict[str, Any]],
    *,
    preferred_range_ft: int = 35,
    spread_from_allies: bool = False,
    occupied: set[tuple[int, int]] | None = None,
) -> dict[str, int] | None:
    """Pick a safe 5-ft grid destination maximizing distance from adjacent enemies (never an occupied square)."""
    if max_move_ft < 5 or not enemies:
        return None
    ox, oy = int(origin["x"]), int(origin["y"])
    max_steps = min(6, max_move_ft // 5)
    best_pos: dict[str, int] | None = None
    best_score = -1e9

    cur_min_e = min(distance_ft(origin, e["position"]) for e in enemies)

    for dx_s in range(-max_steps, max_steps + 1):
        for dy_s in range(-max_steps, max_steps + 1):
            if dx_s == 0 and dy_s == 0:
                continue
            cand = {"x": ox + dx_s * 5, "y": oy + dy_s * 5}
            if distance_ft(origin, cand) > max_move_ft:
                continue
            if not _is_free(cand, occupied):
                continue
            dists_e = [distance_ft(cand, e["position"]) for e in enemies]
            min_e = min(dists_e)
            if min_e <= 5:
                continue
            score = float(min(min_e, preferred_range_ft)) * 2.0
            # Keep at least one enemy comfortably in spell/ranged distance.
            if min_e > 60:
                score -= (min_e - 60) * 1.5
            # Spread >= 20 ft from allies against breath-weapon threats.
            if spread_from_allies and allies:
                min_a = min(distance_ft(cand, a["position"]) for a in allies)
                if min_a >= 20:
                    score += 12.0
                else:
                    score -= (20 - min_a) * 0.8
            if score > best_score:
                best_score = score
                best_pos = cand

    if best_pos is None:
        return None
    # Only move if we actually improve safety or spacing.
    if cur_min_e <= 5 or (
        spread_from_allies and allies and min(distance_ft(origin, a["position"]) for a in allies) < 20
    ):
        return best_pos
    return None


def _monster_action_expected_damage(
    action: dict[str, Any],
    all_actions: list[dict[str, Any]],
    enemy: dict[str, Any] | None = None,
    rules: RulesStore | None = None,
    *,
    ignore_defenses: bool = False,
) -> tuple[float, bool]:
    """Return `(expected_damage, hits_immunity)` for a monster action on one target."""
    act_name = str(action.get("name") or "")
    multi = action.get("multiattack")
    if isinstance(multi, dict) or act_name.strip().lower() == "multiattack":
        seq = list((multi or {}).get("sequence") or [])
        if not seq and (multi or {}).get("options"):
            opt = (multi or {})["options"][0]
            if isinstance(opt, dict) and isinstance(opt.get("actions"), list):
                seq = list(opt["actions"])
            elif isinstance(opt, list):
                seq = list(opt)
        total = 0.0
        any_imm = False
        by_name = {str(a.get("name") or "").strip().lower(): a for a in all_actions}
        for item in seq:
            sub_name = str(item.get("action_name") or item.get("name") or "").strip().lower()
            cnt = int(item.get("count", 1))
            sub = by_name.get(sub_name)
            if sub is not None:
                sub_dmg, sub_imm = _monster_action_expected_damage(
                    sub, all_actions, enemy, rules, ignore_defenses=ignore_defenses
                )
                total += sub_dmg * cnt
                any_imm = any_imm or sub_imm
        return total, any_imm

    if enemy is not None and rules is not None and not ignore_defenses:
        imm, res, vuln = _monster_defense_profile(enemy, rules)
    else:
        imm, res, vuln = set(), set(), set()

    total = 0.0
    any_imm = False
    for dp in action.get("damage_parts") or []:
        if dp.get("conditional") == "advantage":
            continue
        raw_val = _expected_dice_value(str(dp.get("dice") or "0"))
        if raw_val <= 0:
            continue
        dtype = strip_prefix(str(dp.get("type") or "bludgeoning"))
        if ignore_defenses:
            mult = 1.0
        else:
            mult = _damage_multiplier(dtype, imm, res, vuln)
            if mult == 0.0:
                any_imm = True
        total += raw_val * mult
    return total, any_imm


def _monster_action_range(action: dict[str, Any], all_actions: list[dict[str, Any]]) -> tuple[int, bool, bool]:
    """Return `(max_range_ft, is_ranged, is_save_aoe)` for a monster action."""
    act_name = str(action.get("name") or "")
    multi = action.get("multiattack")
    if isinstance(multi, dict) or act_name.strip().lower() == "multiattack":
        seq = list((multi or {}).get("sequence") or [])
        by_name = {str(a.get("name") or "").strip().lower(): a for a in all_actions}
        min_reach = 5
        is_r = False
        for item in seq:
            sub_name = str(item.get("action_name") or item.get("name") or "").strip().lower()
            sub = by_name.get(sub_name)
            if sub is not None:
                sr, s_is_r, _ = _monster_action_range(sub, all_actions)
                min_reach = sr
                is_r = s_is_r
                break
        return min_reach, is_r, False

    is_attack = action.get("attack_bonus") is not None or action.get("kind") == "attack"
    if not is_attack and isinstance(action.get("save"), dict):
        aoe = action.get("aoe") or {}
        size = int(aoe.get("size_ft") or 15)
        return size, False, True

    atk_kind = str(action.get("attack_kind") or "melee").lower()
    reach = int(action.get("reach_ft") or 5)
    if atk_kind == "ranged":
        rng = int(action.get("range_long_ft") or action.get("range_normal_ft") or 60)
        return rng, True, False
    if atk_kind == "melee_or_ranged":
        rng = int(action.get("range_long_ft") or action.get("range_normal_ft") or reach)
        return max(reach, rng), False, False
    return reach, False, False


# ---------------------------------------------------------------------------
# Strict JSON Decision Extractor & Prompt Builders (§4.4)
# ---------------------------------------------------------------------------


def _validate_decision_payload(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError("Decision JSON must be a JSON object.")
    intent = obj.get("intent")
    if not isinstance(intent, dict):
        raise ValueError("Decision JSON must include an 'intent' object.")
    itype = intent.get("type")
    if not isinstance(itype, str) or not itype.strip():
        raise ValueError("Decision JSON 'intent' must include a non-empty string 'type'.")
    return obj


_MAX_DECISION_TEXT = 64 * 1024
_MAX_JSON_DEPTH = 32
_MAX_JSON_CANDIDATES = 16


def _scan_balanced_json_objects(text: str) -> list[str]:
    """Scan `text` for top-level balanced `{...}` substrings respecting JSON strings.

    The scan is linear: each character is visited once, and an unbalanced `{`
    stops the scan instead of restarting inside it. Input is capped at 64 KB
    and nesting deeper than 32 is rejected so a hostile payload cannot hang
    or recurse inside `json.loads`.
    """
    if len(text) > _MAX_DECISION_TEXT:
        text = text[:_MAX_DECISION_TEXT]
    candidates: list[str] = []
    n = len(text)
    i = 0
    while i < n and len(candidates) < _MAX_JSON_CANDIDATES:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = False
        escape = False
        start = i
        j = i
        closed = False
        too_deep = False
        while j < n:
            ch = text[j]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                    if depth > _MAX_JSON_DEPTH:
                        too_deep = True
                        break
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start : j + 1])
                        closed = True
                        break
            j += 1
        if too_deep or not closed:
            break
        i = j + 1
    return candidates


def extract_decision_json(raw_text: str) -> dict[str, Any]:
    """Extract and validate a structured Arena decision dict from LLM output.

    Parsing cascade (§4.4):
    1. Direct `json.loads(raw_text.strip())`.
    2. Fenced code blocks (`` ```json ... ``` `` or generic `` ``` ... ``` ``).
    3. Balanced `{ ... }` scan respecting JSON strings and escapes.
    Validates that the parsed dict contains `"intent"` as a dict with a
    non-empty string `"type"`, raising `ValueError` otherwise.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Empty model response; expected JSON decision object.")
    if len(raw_text) > _MAX_DECISION_TEXT:
        raw_text = raw_text[:_MAX_DECISION_TEXT]

    stripped = raw_text.strip()
    if _json_nesting_exceeds(stripped):
        raise ValueError("JSON nesting exceeds the limit.")

    # 1. Direct JSON parse.
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        return _validate_decision_payload(parsed)

    # 2. Fenced code blocks.
    for match in _FENCE_RE.finditer(raw_text):
        block = match.group(1).strip()
        if not block:
            continue
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            try:
                return _validate_decision_payload(obj)
            except ValueError:
                continue

    # 3. Balanced `{ ... }` scan.
    for candidate in _scan_balanced_json_objects(raw_text):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "intent" in obj:
            try:
                return _validate_decision_payload(obj)
            except ValueError:
                continue

    raise ValueError("Could not extract a valid JSON decision object containing 'intent.type'.")


def build_system_prompt() -> str:
    """Return the system prompt teaching the 5e 2024 (SRD 5.2) Arena intent contract."""
    return (
        "You are a tactical combatant agent in a deterministic 5e 2024 (SRD 5.2) tabletop encounter.\n"
        "The rules engine resolves all dice rolls, saves, conditions, concentration, and HP.\n"
        "You NEVER roll dice or mutate state. On each call you emit exactly ONE legal intent.\n\n"
        "Allowed intent schemas (set actor_id to self.id):\n"
        '- move: {"type":"move","actor_id":"<id>","to":{"x":<int>,"y":<int>},"stand_up":<bool>}\n'
        '- attack (PC weapon): {"type":"attack","actor_id":"<id>","target_id":"<id>","weapon":"<slug>","offhand":<bool>}\n'
        '- attack (monster action): {"type":"attack","actor_id":"<id>","action":"<Action Name>","target_ids":["<id>",...]}\n'
        '- cast_spell: {"type":"cast_spell","actor_id":"<id>","spell":"<slug>","slot_level":<int>,"target_ids":["<id>",...],"destination":{"x":<int>,"y":<int>}}\n'
        '- dodge / dash / disengage: {"type":"dodge"|"dash"|"disengage","actor_id":"<id>"}\n'
        '- help: {"type":"help","actor_id":"<id>","target_id":"<enemy_id>","ally_id":"<ally_id>"}\n'
        '- end_turn: {"type":"end_turn","actor_id":"<id>"}\n\n'
        "Tactical rules:\n"
        "- Respect turn_resources (action, bonus_action, reaction, attacks_remaining), spell slots, and ranges.\n"
        "- Grid uses 5-ft squares with Chebyshev distance max(|dx|,|dy|).\n"
        "- Avoid damage types enemies are immune to, never hit allies with AoE, revive 0-HP allies, and use Weapon Masteries.\n"
        "- Reactions are automatic: the engine makes Opportunity Attacks and casts Shield when they are triggered. "
        "Never cast a reaction spell yourself.\n"
        "- Leaving an enemy's melee reach provokes an Opportunity Attack unless you Disengage first (teleporting doesn't provoke).\n"
        "- When no useful actions remain, emit end_turn.\n\n"
        "Respond with ONLY a single JSON object (no markdown outside JSON):\n"
        '{"intent": {...}, "rationale": "<short tactical reason>", "table_talk": "<optional brief ally callout or null>"}'
    )


def build_user_prompt(observation: dict[str, Any]) -> str:
    """Serialize the partial-observability `observation` compactly for an LLM driver."""
    parts: list[str] = []
    retry_fb = observation.get("retry_feedback")
    if retry_fb:
        parts.append(
            "IMPORTANT RETRY FEEDBACK — YOUR PREVIOUS INTENT WAS REJECTED:\n"
            f"{retry_fb}\n"
            "Fix the error above and emit a strictly legal intent.\n"
        )
    compact_obs = json.dumps(observation, separators=(",", ":"), sort_keys=False)
    parts.append(f"OBSERVATION:\n{compact_obs}\n\nRespond with ONLY the JSON decision object.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Offline Driver 1: RandomDriver (§4.3)
# ---------------------------------------------------------------------------


class RandomDriver:
    """Seeded, legal-action baseline driver that picks uniformly at random."""

    name: str = "random"

    def __init__(self, seed: int = 1, rules: RulesStore | None = None) -> None:
        self._seed: int = int(seed)
        self._rng: random.Random = random.Random(self._seed)
        self._rules: RulesStore = _ensure_rules(rules)

    def reset(self, seed: int | None = None) -> None:
        if seed is not None:
            self._seed = int(seed)
        self._rng = random.Random(self._seed)

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        self_data = observation.get("self") or {}
        actor_id = str(self_data.get("id") or (observation.get("legal_actions") or {}).get("active_actor_id") or "")
        self_pos = self_data.get("position") or {"x": 0, "y": 0}
        res = self_data.get("turn_resources") or {}
        legal = observation.get("legal_actions") or {}
        conds = _condition_set(self_data)
        enemies = _alive_enemies(observation)
        allies = [a for a in (observation.get("allies") or []) if a.get("alive", False)]
        rem_move = int(self_data.get("movement_remaining_ft", legal.get("movement_remaining_ft", 0)))
        speed = int(self_data.get("speed_ft", 30))

        # On retry feedback, bias safely toward end_turn if candidates shrink.
        candidates: list[dict[str, Any]] = []

        # 1. Stand up if prone.
        if "prone" in conds and legal.get("can_stand_up", speed > 0 and rem_move >= speed // 2):
            candidates.append(
                {
                    "type": "move",
                    "actor_id": actor_id,
                    "stand_up": True,
                    "to": {"x": int(self_pos["x"]), "y": int(self_pos["y"])},
                }
            )

        # 2. Random short movement step within remaining movement (avoid frightened violation).
        crawl_mult = 2 if "prone" in conds else 1
        max_step_dist = rem_move // crawl_mult
        if max_step_dist >= 5 and "frightened" not in conds and speed > 0:
            max_grid_steps = min(3, max_step_dist // 5)
            dx = self._rng.randint(-max_grid_steps, max_grid_steps) * 5
            dy = self._rng.randint(-max_grid_steps, max_grid_steps) * 5
            step_to = {"x": int(self_pos["x"]) + dx, "y": int(self_pos["y"]) + dy}
            if (dx != 0 or dy != 0) and _is_free(step_to, _occupied_squares(observation)):
                candidates.append({"type": "move", "actor_id": actor_id, "to": step_to})

        # 3. PC weapon attacks (strictly in reach/range).
        can_attack = bool(res.get("action")) or int(res.get("attacks_remaining", 0)) > 0
        if can_attack and enemies:
            for wp in self_data.get("weapons") or []:
                _, max_r, _ = _weapon_max_range(wp)
                in_r = [e for e in enemies if distance_ft(self_pos, e["position"]) <= max_r]
                if in_r:
                    tgt = self._rng.choice(in_r)
                    candidates.append(
                        {
                            "type": "attack",
                            "actor_id": actor_id,
                            "target_id": str(tgt["id"]),
                            "weapon": str(wp["slug"]),
                        }
                    )

        # 4. Monster actions (only available non-recharging actions).
        if res.get("action") and enemies:
            m_actions = _available_monster_actions(observation)
            for act in m_actions:
                max_r, _, is_save_aoe = _monster_action_range(act, self_data.get("monster_actions") or m_actions)
                in_r = [e for e in enemies if distance_ft(self_pos, e["position"]) <= max_r]
                if not in_r:
                    continue
                if is_save_aoe:
                    tids = [str(e["id"]) for e in in_r]
                else:
                    tids = [str(self._rng.choice(in_r)["id"])]
                candidates.append(
                    {
                        "type": "attack",
                        "actor_id": actor_id,
                        "action": str(act["name"]),
                        "target_ids": tids,
                    }
                )

        # 5. Spells (random legal spell with valid targets/destination).
        slots = _available_slots_map(self_data)
        for sp_slug in _all_known_spells(observation):
            sp_rec = self._rules.get_spell(sp_slug)
            if sp_rec is None:
                continue
            can_cast, action_type, slot_lvl = _can_cast_spell_now(
                sp_slug, sp_rec, self_data, slots, allow_reaction=True
            )
            if not can_cast:
                continue
            sp_intent: dict[str, Any] = {
                "type": "cast_spell",
                "actor_id": actor_id,
                "spell": sp_slug,
            }
            if int(sp_rec.get("level", 0)) >= 1:
                sp_intent["slot_level"] = slot_lvl
            if action_type == "reaction":
                continue  # reaction spells (Shield) are cast by the engine when triggered (#33)

            # Teleport spell requires valid destination within max_distance_ft.
            buffs = list(sp_rec.get("buffs") or [])
            tp_buff = next((b for b in buffs if b.get("kind") == "teleport"), None)
            if tp_buff is not None:
                max_d = int(tp_buff.get("max_distance_ft", 30))
                step_d = min(15, max_d)
                sp_intent["destination"] = {"x": int(self_pos["x"]) - step_d, "y": int(self_pos["y"])}
                sp_intent["target_ids"] = [actor_id]
                candidates.append(sp_intent)
                continue

            resolution = str(sp_rec.get("resolution") or "utility").lower()
            if resolution in ("attack", "save", "auto_hit"):
                if not enemies:
                    continue
                tgt = self._rng.choice(enemies)
                sp_intent["target_ids"] = [str(tgt["id"])]
                candidates.append(sp_intent)
            elif sp_rec.get("healing") or sp_rec.get("buffs"):
                pool_ids = [actor_id] + [str(a["id"]) for a in allies]
                sp_intent["target_ids"] = [self._rng.choice(pool_ids)]
                candidates.append(sp_intent)

        # 6. Basic actions.
        if res.get("action"):
            candidates.append({"type": "dodge", "actor_id": actor_id})
            candidates.append({"type": "dash", "actor_id": actor_id})

        # Always include end_turn.
        end_intent = {"type": "end_turn", "actor_id": actor_id}
        if observation.get("retry_feedback"):
            return AgentDecision(intent=end_intent, rationale="Fallback end_turn after retry.", table_talk=None)

        candidates.append(end_intent)
        chosen = self._rng.choice(candidates)
        return AgentDecision(
            intent=chosen,
            rationale=f"Random legal action selection ({chosen['type']}).",
            table_talk=None,
        )


# ---------------------------------------------------------------------------
# Offline Driver 2: GreedyDriver (§4.3)
# ---------------------------------------------------------------------------


class GreedyDriver:
    """Naive raw-dice-damage maximizer ignoring immunities, healing, and buffs."""

    name: str = "greedy"

    def __init__(self, rules: RulesStore | None = None) -> None:
        self._rules: RulesStore = _ensure_rules(rules)

    def reset(self, seed: int | None = None) -> None:
        return None

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        self_data = observation.get("self") or {}
        actor_id = str(self_data.get("id") or (observation.get("legal_actions") or {}).get("active_actor_id") or "")
        self_pos = self_data.get("position") or {"x": 0, "y": 0}
        res = self_data.get("turn_resources") or {}
        legal = observation.get("legal_actions") or {}
        conds = _condition_set(self_data)
        enemies = _alive_enemies(observation)
        rem_move = int(self_data.get("movement_remaining_ft", legal.get("movement_remaining_ft", 0)))
        speed = int(self_data.get("speed_ft", 30))
        caster_level = int(self_data.get("level", 1))

        if observation.get("retry_feedback") or not enemies:
            return AgentDecision(
                intent={"type": "end_turn", "actor_id": actor_id},
                rationale="No valid offensive target or retry fallback; ending turn.",
                table_talk=None,
            )

        # Evaluate ALL raw immediate expected damage options (deliberately ignoring immunities/resistances!).
        # Each tuple: (raw_expected_damage, in_range_now, tie_breaker, desired_range_ft, intent, label)
        options: list[tuple[float, bool, float, int, dict[str, Any], str]] = []

        # 1. Offensive spells (Action or Bonus Action, ignoring immunities/resistances/friendlies).
        slots = _available_slots_map(self_data)
        sc = self_data.get("spellcasting") or {}
        for idx, sp_slug in enumerate(_all_known_spells(observation)):
            sp_rec = self._rules.get_spell(sp_slug)
            if sp_rec is None or not sp_rec.get("damage"):
                continue
            can_cast, action_type, _ = _can_cast_spell_now(
                sp_slug, sp_rec, self_data, slots, allow_reaction=False
            )
            if not can_cast:
                continue
            sp_lvl = int(sp_rec.get("level", 0))
            slot_lvl = _highest_slot(slots, sp_lvl) if sp_lvl >= 1 else 0
            if slot_lvl is None:
                continue

            per_tgt_dmg, _ = _spell_expected_damage_per_target(
                sp_slug,
                sp_rec,
                caster_level=caster_level,
                slot_level=slot_lvl,
                ignore_defenses=True,
            )
            if per_tgt_dmg <= 0.0:
                continue

            aoe = _spell_aoe_spec(sp_slug, sp_rec)
            eff_range = _spell_effective_range_ft(sp_slug, sp_rec)

            if aoe:
                # Greedy ignores friendly fire and immunities, maximizing raw enemy targets hit.
                hit_targets = _aoe_targets_for_spell(
                    sp_slug, sp_rec, self_pos, enemies, [], avoid_friendlies=False
                )
                if hit_targets:
                    total_raw = per_tgt_dmg * len(hit_targets)
                    intent: dict[str, Any] = {
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": sp_slug,
                        "target_ids": [str(e["id"]) for e in hit_targets],
                    }
                    if sp_lvl >= 1:
                        intent["slot_level"] = slot_lvl
                    # Tie-break: higher range & fireball first (earlier in prepared_spells).
                    tie = (10.0 if sp_slug == "fireball" else 0.0) + eff_range * 0.01 - idx * 0.001
                    options.append((total_raw, True, tie, eff_range, intent, f"spell:{sp_slug}"))
                else:
                    # Out of AoE range right now; record as move-toward candidate.
                    nearest = min(enemies, key=lambda e: distance_ft(self_pos, e["position"]))
                    intent = {
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": sp_slug,
                        "target_ids": [str(nearest["id"])],
                    }
                    if sp_lvl >= 1:
                        intent["slot_level"] = slot_lvl
                    tie = (10.0 if sp_slug == "fireball" else 0.0) + eff_range * 0.01 - idx * 0.001
                    options.append((per_tgt_dmg, False, tie, max(5, eff_range), intent, f"spell:{sp_slug}"))
            else:
                in_r = [e for e in enemies if distance_ft(self_pos, e["position"]) <= max(5, eff_range)]
                if in_r:
                    tgt = min(in_r, key=lambda e: (distance_ft(self_pos, e["position"]), str(e["id"])))
                    intent = {
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": sp_slug,
                        "target_ids": [str(tgt["id"])],
                    }
                    if sp_lvl >= 1:
                        intent["slot_level"] = slot_lvl
                    tie = (5.0 if sp_slug == "fire-bolt" else 0.0) + eff_range * 0.01 - idx * 0.001
                    options.append((per_tgt_dmg, True, tie, eff_range, intent, f"spell:{sp_slug}"))
                else:
                    nearest = min(enemies, key=lambda e: distance_ft(self_pos, e["position"]))
                    intent = {
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": sp_slug,
                        "target_ids": [str(nearest["id"])],
                    }
                    if sp_lvl >= 1:
                        intent["slot_level"] = slot_lvl
                    tie = (5.0 if sp_slug == "fire-bolt" else 0.0) + eff_range * 0.01 - idx * 0.001
                    options.append((per_tgt_dmg, False, tie, max(5, eff_range), intent, f"spell:{sp_slug}"))

        # 2. PC Weapons (Action or Extra Attack).
        can_attack = bool(res.get("action")) or int(res.get("attacks_remaining", 0)) > 0
        if can_attack:
            for wp in self_data.get("weapons") or []:
                raw_dmg = _weapon_expected_damage(wp)
                _, max_r, _ = _weapon_max_range(wp)
                in_r = [e for e in enemies if distance_ft(self_pos, e["position"]) <= max_r]
                if in_r:
                    tgt = min(in_r, key=lambda e: (distance_ft(self_pos, e["position"]), str(e["id"])))
                    intent = {
                        "type": "attack",
                        "actor_id": actor_id,
                        "target_id": str(tgt["id"]),
                        "weapon": str(wp["slug"]),
                    }
                    options.append((raw_dmg, True, 0.0, max_r, intent, f"weapon:{wp['slug']}"))
                else:
                    nearest = min(enemies, key=lambda e: distance_ft(self_pos, e["position"]))
                    intent = {
                        "type": "attack",
                        "actor_id": actor_id,
                        "target_id": str(nearest["id"]),
                        "weapon": str(wp["slug"]),
                    }
                    options.append((raw_dmg, False, 0.0, max_r, intent, f"weapon:{wp['slug']}"))

        # 3. Monster Actions (only non-recharging actions).
        if res.get("action"):
            all_m = self_data.get("monster_actions") or []
            for act in _available_monster_actions(observation):
                per_dmg, _ = _monster_action_expected_damage(act, all_m, ignore_defenses=True)
                if per_dmg <= 0.0:
                    continue
                max_r, _, is_save_aoe = _monster_action_range(act, all_m)
                if is_save_aoe:
                    hit = _cone_aoe_targets(self_pos, enemies, [], size_ft=max_r, avoid_friendlies=False)
                    if hit:
                        intent = {
                            "type": "attack",
                            "actor_id": actor_id,
                            "action": str(act["name"]),
                            "target_ids": [str(e["id"]) for e in hit],
                        }
                        options.append((per_dmg * len(hit), True, 1.0, max_r, intent, f"monster:{act['name']}"))
                    else:
                        nearest = min(enemies, key=lambda e: distance_ft(self_pos, e["position"]))
                        intent = {
                            "type": "attack",
                            "actor_id": actor_id,
                            "action": str(act["name"]),
                            "target_ids": [str(nearest["id"])],
                        }
                        options.append((per_dmg, False, 1.0, max_r, intent, f"monster:{act['name']}"))
                else:
                    in_r = [e for e in enemies if distance_ft(self_pos, e["position"]) <= max_r]
                    if in_r:
                        tgt = min(in_r, key=lambda e: (distance_ft(self_pos, e["position"]), str(e["id"])))
                        intent = {
                            "type": "attack",
                            "actor_id": actor_id,
                            "action": str(act["name"]),
                            "target_ids": [str(tgt["id"])],
                        }
                        options.append((per_dmg, True, 0.0, max_r, intent, f"monster:{act['name']}"))
                    else:
                        nearest = min(enemies, key=lambda e: distance_ft(self_pos, e["position"]))
                        intent = {
                            "type": "attack",
                            "actor_id": actor_id,
                            "action": str(act["name"]),
                            "target_ids": [str(nearest["id"])],
                        }
                        options.append((per_dmg, False, 0.0, max_r, intent, f"monster:{act['name']}"))

        if not options:
            return AgentDecision(
                intent={"type": "end_turn", "actor_id": actor_id},
                rationale="No remaining offensive actions; ending turn.",
                table_talk=None,
            )

        # Pick highest raw expected damage overall!
        options.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        best_raw, best_in_range, _, best_range, best_intent, best_label = options[0]

        if best_in_range:
            return AgentDecision(
                intent=best_intent,
                rationale=f"Greedy max raw expected damage ({best_label}, {best_raw:.1f} avg).",
                table_talk=None,
            )

        # Best raw damage option is currently out of range: move toward nearest enemy if possible.
        crawl_mult = 2 if "prone" in conds else 1
        max_step = rem_move // crawl_mult
        if max_step >= 5 and speed > 0 and "frightened" not in conds:
            nearest = min(enemies, key=lambda e: (distance_ft(self_pos, e["position"]), str(e["id"])))
            dest = _step_toward(self_pos, nearest["position"], max_step, desired_dist_ft=max(5, min(best_range, 5)), occupied=_occupied_squares(observation))
            if dest != self_pos:
                return AgentDecision(
                    intent={"type": "move", "actor_id": actor_id, "to": dest},
                    rationale=f"Greedy closing toward {nearest['name']} for {best_label}.",
                    table_talk=None,
                )

        # Otherwise execute highest-damage option that IS currently in range.
        in_range_opts = [opt for opt in options if opt[1]]
        if in_range_opts:
            raw_v, _, _, _, intent_v, label_v = in_range_opts[0]
            return AgentDecision(
                intent=intent_v,
                rationale=f"Greedy fallback in-range damage ({label_v}, {raw_v:.1f} avg).",
                table_talk=None,
            )

        # Nothing in range and out of movement: Dash if Action available, else end_turn.
        if res.get("action") and speed > 0 and "frightened" not in conds:
            return AgentDecision(
                intent={"type": "dash", "actor_id": actor_id},
                rationale="No enemy in range; Dashing to close distance.",
                table_talk=None,
            )

        return AgentDecision(
            intent={"type": "end_turn", "actor_id": actor_id},
            rationale="Cannot reach any target; ending turn.",
            table_talk=None,
        )


# ---------------------------------------------------------------------------
# Offline Driver 3: HeuristicTacticalDriver (§4.3)
# ---------------------------------------------------------------------------


class HeuristicTacticalDriver:
    """Strong deterministic 5e 2024 (SRD 5.2) tactical baseline for PCs and monsters."""

    name: str = "heuristic-tactical"

    def __init__(self, rules: RulesStore | None = None) -> None:
        self._rules: RulesStore = _ensure_rules(rules)
        # Tracks per-turn state only when observation omits optional flags (e.g., nick_used).
        self._nick_used_turn: tuple[int, str] | None = None
        self._light_attack_turn: tuple[int, str] | None = None
        self._moved_turn: tuple[int, str] | None = None

    def reset(self, seed: int | None = None) -> None:
        self._nick_used_turn = None
        self._light_attack_turn = None
        self._moved_turn = None

    def _target_priority(
        self,
        enemy: dict[str, Any],
        self_pos: dict[str, int],
        *,
        prefer_melee_dist: bool = True,
    ) -> float:
        score = 10.0
        band = str(enemy.get("hp_band") or "unharmed").lower()
        if band == "critical":
            score += 42.0
        elif band == "bloodied" or enemy.get("bloodied"):
            score += 28.0
        elif band == "scratched":
            score += 18.0

        e_conds = _condition_set(enemy)
        dist = distance_ft(self_pos, enemy["position"])
        if "paralyzed" in e_conds or "unconscious" in e_conds:
            score += 36.0 if dist <= 5 else 15.0
        if "prone" in e_conds and dist <= 5:
            score += 10.0

        src_slug = strip_prefix(str(enemy.get("source_id") or ""))
        score += _HIGH_THREAT_MONSTERS.get(src_slug, 6.0)

        # Prefer already-vulnerable/low-AC caster targets when driving monsters.
        if src_slug in ("wizard", "sorcerer", "cleric", "rogue"):
            score += 10.0 if src_slug == "wizard" else 6.0

        if prefer_melee_dist:
            score -= dist * 0.35
        else:
            score -= dist * 0.10
        return score

    def _has_breath_enemy(self, enemies: list[dict[str, Any]]) -> bool:
        for e in enemies:
            src = strip_prefix(str(e.get("source_id") or ""))
            if src in _BREATH_THREAT_MONSTERS:
                return True
        return False

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        self_data = observation.get("self") or {}
        legal = observation.get("legal_actions") or {}
        actor_id = str(self_data.get("id") or legal.get("active_actor_id") or "")
        actor_name = str(self_data.get("name") or actor_id)
        side = str(self_data.get("side") or "party")
        kind = str(self_data.get("kind") or "pc")
        round_num = int(observation.get("round", 1))
        self_pos = self_data.get("position") or {"x": 0, "y": 0}
        res = self_data.get("turn_resources") or {}
        conds = _condition_set(self_data)
        self_effects = _effect_kinds(self_data)
        rem_move = int(self_data.get("movement_remaining_ft", legal.get("movement_remaining_ft", 0)))
        speed = int(self_data.get("speed_ft", 30))
        hp = int(self_data.get("hp", 1))
        hp_max = max(1, int(self_data.get("hp_max", max(1, hp))))
        enemies = _alive_enemies(observation)
        allies = _conscious_allies(observation)
        downed = _downed_allies(observation)
        is_party = side == "party"

        turn_key = (round_num, actor_id)
        if res.get("action") and int(res.get("attacks_remaining", 0)) == 0 and self._nick_used_turn == turn_key:
            # Fresh turn reset (helpful for repeated unit-test calls on round 1).
            self._nick_used_turn = None
            self._light_attack_turn = None

        # Safety net on retry feedback: avoid infinite violation loops.
        if observation.get("retry_feedback"):
            return AgentDecision(
                intent={"type": "end_turn", "actor_id": actor_id},
                rationale="Ending turn cleanly after retry feedback.",
                table_talk=None,
            )

        if not enemies:
            return AgentDecision(
                intent={"type": "end_turn", "actor_id": actor_id},
                rationale="No conscious enemies remain; ending turn.",
                table_talk=None,
            )

        # ------------------------------------------------------------------
        # 1. Prone Recovery (PCs & Monsters)
        # ------------------------------------------------------------------
        can_stand = bool(legal.get("can_stand_up", speed > 0 and rem_move >= speed // 2))
        if "prone" in conds and can_stand and speed > 0 and rem_move >= speed // 2:
            return AgentDecision(
                intent={
                    "type": "move",
                    "actor_id": actor_id,
                    "stand_up": True,
                    "to": {"x": int(self_pos["x"]), "y": int(self_pos["y"])},
                },
                rationale="Standing up from Prone to remove melee/attack penalties.",
                table_talk="Back on my feet—staying in the fight!" if is_party else None,
            )

        # ------------------------------------------------------------------
        # 2. Monster Tactical Branch
        # ------------------------------------------------------------------
        if kind == "monster" or (self_data.get("monster_actions") and not self_data.get("weapons")):
            return self._decide_monster(
                observation,
                actor_id=actor_id,
                self_pos=self_pos,
                res=res,
                rem_move=rem_move,
                speed=speed,
                conds=conds,
                enemies=enemies,
                allies=allies,
            )

        # ------------------------------------------------------------------
        # 3. PC Tactical Branch
        # ------------------------------------------------------------------
        slots = _available_slots_map(self_data)
        known_spells = _all_known_spells(observation)
        spell_recs: dict[str, dict[str, Any]] = {}
        for sp_slug in known_spells:
            rec = self._rules.get_spell(sp_slug)
            if rec is not None:
                spell_recs[sp_slug] = rec

        # 3a. 0-HP Ally Revival (HIGHEST PC PRIORITY!)
        if downed:
            revival = self._decide_revival(
                actor_id=actor_id,
                self_pos=self_pos,
                self_data=self_data,
                downed=downed,
                allies=allies,
                slots=slots,
                spell_recs=spell_recs,
                rem_move=rem_move,
                speed=speed,
                conds=conds,
                is_party=is_party,
                occupied=_occupied_squares(observation),
            )
            if revival is not None:
                return revival

        # 3b. Shield is a Reaction to being hit: the engine casts it when an incoming attack would hit and +5 AC
        #     turns it into a miss (#33). Casting it pre-emptively on our own turn is not legal, so don't plan it.

        # 3c. Bonus Action `misty-step` escape if adjacent enemy threatens caster
        adjacent_enemies = [e for e in enemies if distance_ft(self_pos, e["position"]) <= 5]
        if (
            adjacent_enemies
            and "misty-step" in spell_recs
            and res.get("bonus_action")
        ):
            can_ms, _, ms_slot = _can_cast_spell_now(
                "misty-step", spell_recs["misty-step"], self_data, slots, allow_reaction=False
            )
            if can_ms:
                dest = _best_kite_position(
                    self_pos,
                    30,
                    enemies,
                    allies,
                    preferred_range_ft=35,
                    spread_from_allies=self._has_breath_enemy(enemies),
                    occupied=_occupied_squares(observation),
                )
                if dest is not None and dest != self_pos:
                    return AgentDecision(
                        intent={
                            "type": "cast_spell",
                            "actor_id": actor_id,
                            "spell": "misty-step",
                            "slot_level": ms_slot,
                            "destination": dest,
                            "target_ids": [actor_id],
                        },
                        rationale=f"Teleporting via Misty Step to ({dest['x']},{dest['y']}) to escape melee.",
                        table_talk="Misty Stepping clear of melee range!" if is_party else None,
                    )

        # 3d. Bonus Action Triage Healing for critically wounded conscious ally (<= 40% HP)
        triage = self._decide_triage_heal(
            actor_id=actor_id,
            self_pos=self_pos,
            self_data=self_data,
            allies=allies,
            slots=slots,
            spell_recs=spell_recs,
            is_party=is_party,
        )
        if triage is not None:
            return triage

        # 3e. Cleric `bless` buff (Round 1 or when not concentrating & >=2 party members in 30 ft)
        if (
            "bless" in spell_recs
            and res.get("action")
            and not self_data.get("concentration")
        ):
            can_bless, _, bless_slot = _can_cast_spell_now(
                "bless", spell_recs["bless"], self_data, slots, allow_reaction=False
            )
            if can_bless:
                bless_pool = [a for a in allies if distance_ft(self_pos, a["position"]) <= 30]
                # Prioritize Fighters/Rogues/attackers, then casters, then self.
                def _bless_rank(member: dict[str, Any]) -> tuple[int, int]:
                    m_name = str(member.get("name") or "").lower()
                    m_id = str(member.get("id") or "").lower()
                    has_b = "bless" in _effect_kinds(member)
                    priority = 0 if has_b else 1
                    # Put martial/frontline first (higher HP max usually correlates with Fighter/Rogue).
                    return (priority, int(member.get("hp_max", 10)))

                bless_pool.sort(key=_bless_rank, reverse=True)
                chosen_targets = [str(a["id"]) for a in bless_pool if "bless" not in _effect_kinds(a)][:3]
                if len(chosen_targets) < 3 and "bless" not in self_effects:
                    chosen_targets.append(actor_id)
                if len(chosen_targets) >= 2 and (round_num == 1 or len(enemies) >= 2):
                    names_str = ", ".join(
                        next((str(a["name"]).split()[0] for a in allies if str(a["id"]) == tid), actor_name.split()[0])
                        for tid in chosen_targets
                    )
                    return AgentDecision(
                        intent={
                            "type": "cast_spell",
                            "actor_id": actor_id,
                            "spell": "bless",
                            "slot_level": bless_slot,
                            "target_ids": chosen_targets,
                        },
                        rationale=f"Casting Bless (+1d4 attacks & saves) on {len(chosen_targets)} allies.",
                        table_talk=f"Blessing {names_str}—strike true and hold your saves!" if is_party else None,
                    )

        # 3f. Evaluate Caster Offensive Spells (strictly immune-safe, zero friendly fire!)
        best_spell_plan = self._evaluate_best_offensive_spell(
            actor_id=actor_id,
            self_data=self_data,
            self_pos=self_pos,
            enemies=enemies,
            allies=allies,
            slots=slots,
            spell_recs=spell_recs,
        )

        # 3g. Wizard `mage-armor` if unarmored, not active, and no 2+-target non-immune AoE kill shot
        if (
            "mage-armor" in spell_recs
            and res.get("action")
            and "mage_armor" not in self_effects
            and not self_data.get("wearing_armor", False)
        ):
            base_ac = int(self_data.get("ac", 10))
            eff_ac = int(self_data.get("effective_ac", base_ac))
            ab_mods = self_data.get("ability_mods") or {}
            dex_mod = int(ab_mods.get("dex", max(0, base_ac - 10)))
            needs_mage_armor = base_ac < (13 + dex_mod) and eff_ac < (13 + dex_mod)
            has_multi_aoe = (
                best_spell_plan is not None
                and int(best_spell_plan.get("num_targets", 0)) >= 2
                and float(best_spell_plan.get("expected_damage", 0.0)) >= 24.0
            )
            can_ma, _, ma_slot = _can_cast_spell_now(
                "mage-armor", spell_recs["mage-armor"], self_data, slots, allow_reaction=False
            )
            if needs_mage_armor and can_ma and not has_multi_aoe:
                return AgentDecision(
                    intent={
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": "mage-armor",
                        "slot_level": ma_slot,
                        "target_ids": [actor_id],
                    },
                    rationale="Casting Mage Armor to raise unarmored AC to 13 + DEX.",
                    table_talk="Weaving Mage Armor before pressing the attack!" if is_party else None,
                )

        # 3h. Caster / Ranged Positioning (step away from adjacent enemies to avoid ranged disadvantage)
        is_caster = isinstance(self_data.get("spellcasting"), dict) and bool(spell_recs)
        has_breath = self._has_breath_enemy(enemies)
        if (
            is_caster
            and adjacent_enemies
            and rem_move >= 5
            and speed > 0
            and "frightened" not in conds
            and self._moved_turn != turn_key
            and (res.get("action") or best_spell_plan is not None)
        ):
            # Only kite if we are a ranged caster (Wizard) or Cleric without wanting melee mace.
            src_cls = strip_prefix(str(self_data.get("source_id") or ""))
            if src_cls in ("wizard", "sorcerer", "warlock") or (best_spell_plan is not None and best_spell_plan.get("is_ranged_attack")):
                kite_dest = _best_kite_position(
                    self_pos,
                    rem_move,
                    enemies,
                    allies,
                    preferred_range_ft=35,
                    spread_from_allies=has_breath,
                    occupied=_occupied_squares(observation),
                )
                if kite_dest is not None and kite_dest != self_pos:
                    self._moved_turn = turn_key
                    return AgentDecision(
                        intent={"type": "move", "actor_id": actor_id, "to": kite_dest},
                        rationale="Kiting out of 5-ft melee reach to avoid ranged attack disadvantage.",
                        table_talk="Repositioning out of reach for a clean spell line!" if is_party else None,
                    )

        # 3i. Execute Best Non-Immune Offensive Spell (if caster has one better than weapon)
        if best_spell_plan is not None:
            sp_slug = str(best_spell_plan["spell"])
            # Check if we need to move closer first for a short-range spell like burning-hands/shatter.
            if not best_spell_plan["in_range"] and rem_move >= 5 and speed > 0 and "frightened" not in conds:
                tgt_pos = best_spell_plan["anchor_pos"]
                dest = _step_toward(self_pos, tgt_pos, rem_move, desired_dist_ft=best_spell_plan["desired_dist"], occupied=_occupied_squares(observation))
                if dest != self_pos:
                    return AgentDecision(
                        intent={"type": "move", "actor_id": actor_id, "to": dest},
                        rationale=f"Moving into range to cast {sp_slug}.",
                        table_talk=None,
                    )
            if best_spell_plan["in_range"]:
                skipped_immune = best_spell_plan.get("skipped_immune_fire", False)
                tgt_names = best_spell_plan.get("target_names") or []
                tgt_label = ", ".join(tgt_names[:3]) if tgt_names else "the enemy"
                if skipped_immune:
                    talk = f"Fire won't scratch them—pivoting to {sp_slug} on {tgt_label}!"
                elif len(tgt_names) >= 2:
                    talk = f"Dropping {sp_slug} on {tgt_label}—allies are clear!"
                else:
                    talk = f"Focusing {sp_slug} on {tgt_label}!"
                return AgentDecision(
                    intent=best_spell_plan["intent"],
                    rationale=best_spell_plan["rationale"],
                    table_talk=talk if is_party else None,
                )

        # 3j. PC Weapon Attacks, Extra Attack & Rogue Vex+Nick Combo
        weapon_decision = self._decide_pc_weapon_combat(
            actor_id=actor_id,
            self_data=self_data,
            self_pos=self_pos,
            res=res,
            rem_move=rem_move,
            speed=speed,
            conds=conds,
            enemies=enemies,
            allies=allies,
            turn_key=turn_key,
            is_party=is_party,
            occupied=_occupied_squares(observation),
        )
        if weapon_decision is not None:
            return weapon_decision

        # 3k. Post-action spacing vs breath weapons (e.g., caster spreading >= 20 ft from allies)
        if (
            is_caster
            and has_breath
            and rem_move >= 5
            and speed > 0
            and "frightened" not in conds
            and self._moved_turn != turn_key
            and allies
            and min(distance_ft(self_pos, a["position"]) for a in allies) < 20
        ):
            spread_dest = _best_kite_position(
                self_pos,
                rem_move,
                enemies,
                allies,
                preferred_range_ft=35,
                spread_from_allies=True,
                occupied=_occupied_squares(observation),
            )
            if spread_dest is not None and spread_dest != self_pos:
                self._moved_turn = turn_key
                return AgentDecision(
                    intent={"type": "move", "actor_id": actor_id, "to": spread_dest},
                    rationale="Spreading >= 20 ft from allies to deny multi-target breath cones.",
                    table_talk="Spreading out—don't bunch up for Fire Breath!" if is_party else None,
                )

        # 3l. Fallback Action if Action still unused (Dash if out of reach, else Help/Dodge)
        if res.get("action"):
            nearest = min(enemies, key=lambda e: distance_ft(self_pos, e["position"]))
            if distance_ft(self_pos, nearest["position"]) > 5 and speed > 0 and "frightened" not in conds:
                return AgentDecision(
                    intent={"type": "dash", "actor_id": actor_id},
                    rationale=f"No target in range; Dashing to close on {nearest['name']}.",
                    table_talk=None,
                )
            return AgentDecision(
                intent={"type": "dodge", "actor_id": actor_id},
                rationale="Taking Dodge action to impose Disadvantage on incoming attacks.",
                table_talk=None,
            )

        return AgentDecision(
            intent={"type": "end_turn", "actor_id": actor_id},
            rationale="Turn resources exhausted; ending turn.",
            table_talk=None,
        )

    # ------------------------------------------------------------------
    # Revival & Triage Helpers
    # ------------------------------------------------------------------

    def _decide_revival(
        self,
        *,
        actor_id: str,
        self_pos: dict[str, int],
        self_data: dict[str, Any],
        downed: list[dict[str, Any]],
        allies: list[dict[str, Any]],
        slots: dict[int, int],
        spell_recs: dict[str, dict[str, Any]],
        rem_move: int,
        speed: int,
        conds: set[str],
        is_party: bool,
        occupied: set[tuple[int, int]] | None = None,
    ) -> AgentDecision | None:
        # Sort downed allies by closest first.
        downed_sorted = sorted(downed, key=lambda a: (distance_ft(self_pos, a["position"]), str(a["id"])))
        target = downed_sorted[0]
        dist = distance_ft(self_pos, target["position"])

        # 1. Bonus Action: mass-healing-word (if >=2 allies hurt/downed) or healing-word (60 ft!)
        for sp_slug in ("mass-healing-word", "healing-word"):
            if sp_slug not in spell_recs:
                continue
            if sp_slug == "mass-healing-word" and len(downed) < 2 and "healing-word" in spell_recs:
                can_hw, _, _ = _can_cast_spell_now("healing-word", spell_recs["healing-word"], self_data, slots)
                if can_hw:
                    continue
            can_cast, _, slot_lvl = _can_cast_spell_now(sp_slug, spell_recs[sp_slug], self_data, slots)
            if not can_cast:
                continue
            if dist <= 60:
                if sp_slug == "mass-healing-word":
                    heal_ids = [
                        str(a["id"])
                        for a in (downed_sorted + [x for x in allies if int(x.get("hp", 1)) < int(x.get("hp_max", 1))])
                        if distance_ft(self_pos, a["position"]) <= 60
                    ][:6]
                else:
                    heal_ids = [str(target["id"])]
                return AgentDecision(
                    intent={
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": sp_slug,
                        "slot_level": slot_lvl,
                        "target_ids": heal_ids,
                    },
                    rationale=f"Reviving 0-HP ally {target['name']} with Bonus Action {sp_slug}.",
                    table_talk=f"Get back up, {target['name']}! Casting {sp_slug}!" if is_party else None,
                )
            if rem_move >= 5 and speed > 0 and "frightened" not in conds:
                dest = _step_toward(self_pos, target["position"], rem_move, desired_dist_ft=60, occupied=occupied)
                if dest != self_pos:
                    return AgentDecision(
                        intent={"type": "move", "actor_id": actor_id, "to": dest},
                        rationale=f"Moving into 60-ft range to revive {target['name']} with {sp_slug}.",
                        table_talk=f"Moving to reach {target['name']}!" if is_party else None,
                    )

        # 2. Action: aid (30 ft, heals 5 HP) or cure-wounds (5 ft touch)
        if "aid" in spell_recs:
            can_aid, _, aid_slot = _can_cast_spell_now("aid", spell_recs["aid"], self_data, slots)
            if can_aid and dist <= 30:
                tids = [str(a["id"]) for a in downed_sorted if distance_ft(self_pos, a["position"]) <= 30][:3]
                return AgentDecision(
                    intent={
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": "aid",
                        "slot_level": aid_slot,
                        "target_ids": tids,
                    },
                    rationale=f"Reviving 0-HP ally {target['name']} with Aid at 30 ft.",
                    table_talk=f"Casting Aid to bring {target['name']} back into the fight!" if is_party else None,
                )

        if "cure-wounds" in spell_recs:
            can_cw, _, cw_slot = _can_cast_spell_now("cure-wounds", spell_recs["cure-wounds"], self_data, slots)
            if can_cw:
                if dist <= 5:
                    return AgentDecision(
                        intent={
                            "type": "cast_spell",
                            "actor_id": actor_id,
                            "spell": "cure-wounds",
                            "slot_level": cw_slot,
                            "target_ids": [str(target["id"])],
                        },
                        rationale=f"Reviving 0-HP ally {target['name']} with Cure Wounds.",
                        table_talk=f"Healing {target['name']} with Cure Wounds—stand up!" if is_party else None,
                    )
                if rem_move >= 5 and speed > 0 and "frightened" not in conds:
                    dest = _step_toward(self_pos, target["position"], rem_move, desired_dist_ft=5, occupied=occupied)
                    if dest != self_pos:
                        return AgentDecision(
                            intent={"type": "move", "actor_id": actor_id, "to": dest},
                            rationale=f"Moving to touch {target['name']} for Cure Wounds revival.",
                            table_talk=f"Rushing to {target['name']} with healing!" if is_party else None,
                        )
        return None

    def _decide_triage_heal(
        self,
        *,
        actor_id: str,
        self_pos: dict[str, int],
        self_data: dict[str, Any],
        allies: list[dict[str, Any]],
        slots: dict[int, int],
        spell_recs: dict[str, dict[str, Any]],
        is_party: bool,
    ) -> AgentDecision | None:
        if "healing-word" not in spell_recs and "mass-healing-word" not in spell_recs:
            return None
        total_slots = sum(slots.values())
        if total_slots <= 1:
            # Keep last slot for emergency 0-HP revival!
            return None

        hurt = [
            a
            for a in allies
            if distance_ft(self_pos, a["position"]) <= 60
            and int(a.get("hp", 1)) <= int(max(1, a.get("hp_max", 1)) * 0.40)
        ]
        # Also consider self if critically low.
        self_hp = int(self_data.get("hp", 1))
        self_max = max(1, int(self_data.get("hp_max", 1)))
        if self_hp <= int(self_max * 0.35):
            hurt.append({"id": actor_id, "name": self_data.get("name", actor_id), "hp": self_hp, "hp_max": self_max, "position": self_pos})
        if not hurt:
            return None

        hurt.sort(key=lambda a: int(a["hp"]) / max(1, int(a["hp_max"])))
        if len(hurt) >= 2 and "mass-healing-word" in spell_recs:
            can_mhw, _, mhw_slot = _can_cast_spell_now("mass-healing-word", spell_recs["mass-healing-word"], self_data, slots)
            if can_mhw:
                tids = [str(a["id"]) for a in hurt[:6]]
                return AgentDecision(
                    intent={
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": "mass-healing-word",
                        "slot_level": mhw_slot,
                        "target_ids": tids,
                    },
                    rationale=f"Triage Mass Healing Word on {len(tids)} critically wounded allies.",
                    table_talk="Mass Healing Word—hold the line, everyone!" if is_party else None,
                )

        if "healing-word" in spell_recs:
            can_hw, _, hw_slot = _can_cast_spell_now("healing-word", spell_recs["healing-word"], self_data, slots)
            if can_hw:
                tgt = hurt[0]
                return AgentDecision(
                    intent={
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": "healing-word",
                        "slot_level": hw_slot,
                        "target_ids": [str(tgt["id"])],
                    },
                    rationale=f"Bonus Action Healing Word on critically wounded {tgt['name']}.",
                    table_talk=f"Healing Word on {tgt['name']}—stay upright!" if is_party else None,
                )
        return None

    # ------------------------------------------------------------------
    # Offensive Spell Evaluation (Immunity-Safe & Zero Friendly Fire)
    # ------------------------------------------------------------------

    def _evaluate_best_offensive_spell(
        self,
        *,
        actor_id: str,
        self_data: dict[str, Any],
        self_pos: dict[str, int],
        enemies: list[dict[str, Any]],
        allies: list[dict[str, Any]],
        slots: dict[int, int],
        spell_recs: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        res = self_data.get("turn_resources") or {}
        if not res.get("action") and not res.get("bonus_action"):
            return None

        caster_level = int(self_data.get("level", 1))
        sc = self_data.get("spellcasting") or {}
        spell_ab = strip_prefix(str(sc.get("ability") or "int"))
        spell_mod = int((self_data.get("ability_mods") or {}).get(spell_ab, 0))
        concentrating = bool(self_data.get("concentration"))
        friendlies = [{"id": actor_id, "position": self_pos}] + list(allies)
        has_heal_role = "healing-word" in spell_recs or "cure-wounds" in spell_recs
        total_slots = sum(slots.values())

        skipped_immune_fire = False
        best_plan: dict[str, Any] | None = None
        best_score = 0.0

        for sp_slug, sp_rec in spell_recs.items():
            if not sp_rec.get("damage"):
                continue
            # Never break active Bless concentration for a damage spell!
            if concentrating and bool(sp_rec.get("concentration", False)):
                continue
            can_cast, action_type, slot_lvl = _can_cast_spell_now(
                sp_slug, sp_rec, self_data, slots, allow_reaction=False
            )
            if not can_cast:
                continue
            sp_lvl = int(sp_rec.get("level", 0))
            # Cleric reserves last L1 slot for emergency Healing Word revival.
            if has_heal_role and sp_lvl == 1 and total_slots <= 1:
                continue

            # Check whether this spell has fire damage that is immune on all enemies.
            sp_dtypes = {strip_prefix(str(d.get("type") or "")) for d in (sp_rec.get("damage") or [])}
            aoe = _spell_aoe_spec(sp_slug, sp_rec)
            eff_range = _spell_effective_range_ft(sp_slug, sp_rec)
            resolution = str(sp_rec.get("resolution") or "utility").lower()
            is_ranged_atk = resolution == "attack" and str(sp_rec.get("attack_kind") or "ranged").lower() != "melee"

            if aoe:
                # Strictly exclude friendlies AND any enemy immune to this spell's damage!
                non_immune_enemies = []
                for e in enemies:
                    dmg_e, hits_imm = _spell_expected_damage_per_target(
                        sp_slug,
                        sp_rec,
                        caster_level=caster_level,
                        slot_level=slot_lvl,
                        spell_mod=spell_mod,
                        enemy=e,
                        rules=self._rules,
                        ignore_defenses=False,
                    )
                    if hits_imm or dmg_e <= 0.0:
                        if "fire" in sp_dtypes:
                            skipped_immune_fire = True
                        continue
                    non_immune_enemies.append((e, dmg_e))

                if not non_immune_enemies:
                    continue

                valid_e_objs = [pair[0] for pair in non_immune_enemies]
                dmg_by_id = {str(pair[0]["id"]): pair[1] for pair in non_immune_enemies}
                hit_targets = _aoe_targets_for_spell(
                    sp_slug, sp_rec, self_pos, valid_e_objs, friendlies, avoid_friendlies=True
                )
                if not hit_targets:
                    continue

                # Effective total damage weighted by save half/none and target priority.
                save_on_succ = str((sp_rec.get("save") or {}).get("on_success") or "none").lower()
                save_mult = 0.78 if save_on_succ == "half" else 0.58
                total_eff = sum(dmg_by_id[str(e["id"])] * save_mult for e in hit_targets)
                focus_bonus = sum(
                    4.0 if str(e.get("hp_band") or "") in ("scratched", "bloodied", "critical") else 0.0
                    for e in hit_targets
                )
                # Prefer hitting 2+ targets with AoE slots, but single-target Lightning Bolt/Shatter
                # against a high-HP boss (like Red Dragon Wyrmling) is also awesome!
                slot_cost_penalty = sp_lvl * 2.5 if len(hit_targets) == 1 else sp_lvl * 1.0
                score = total_eff + focus_bonus - slot_cost_penalty
                if score > best_score:
                    intent: dict[str, Any] = {
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": sp_slug,
                        "target_ids": [str(e["id"]) for e in hit_targets],
                    }
                    if sp_lvl >= 1:
                        intent["slot_level"] = slot_lvl
                    best_score = score
                    best_plan = {
                        "spell": sp_slug,
                        "intent": intent,
                        "in_range": True,
                        "desired_dist": eff_range,
                        "anchor_pos": hit_targets[0]["position"],
                        "expected_damage": total_eff,
                        "num_targets": len(hit_targets),
                        "is_ranged_attack": False,
                        "target_names": [str(e["name"]) for e in hit_targets],
                        "rationale": f"Casting {sp_slug} (slot {slot_lvl}) on {len(hit_targets)} non-immune target(s) with zero friendly fire.",
                        "skipped_immune_fire": skipped_immune_fire,
                    }
            else:
                # Single-target or multi-projectile spell.
                adj_hostile = any(distance_ft(self_pos, e["position"]) <= 5 for e in enemies)
                for e in enemies:
                    dist = distance_ft(self_pos, e["position"])
                    dmg_e, hits_imm = _spell_expected_damage_per_target(
                        sp_slug,
                        sp_rec,
                        caster_level=caster_level,
                        slot_level=slot_lvl,
                        spell_mod=spell_mod,
                        enemy=e,
                        rules=self._rules,
                        ignore_defenses=False,
                    )
                    if hits_imm or dmg_e <= 0.0:
                        if "fire" in sp_dtypes:
                            skipped_immune_fire = True
                        continue

                    in_r = dist <= max(5, eff_range)
                    if not in_r:
                        continue

                    # Account for accuracy / auto-hit / disadvantage.
                    if resolution == "auto_hit":
                        hit_prob = 1.0
                    elif resolution == "attack":
                        hit_prob = 0.65
                        if is_ranged_atk and adj_hostile:
                            hit_prob = 0.42
                        if sp_slug == "chill-touch" and dist > 5:
                            hit_prob = 0.40
                    else:
                        save_on_succ = str((sp_rec.get("save") or {}).get("on_success") or "none").lower()
                        hit_prob = 0.78 if save_on_succ == "half" else 0.58

                    eff_dmg = dmg_e * hit_prob
                    bonus = 0.0
                    if sp_slug == "magic-missile":
                        # Guaranteed hit — extra strong vs critical/bloodied or high-AC bosses.
                        bonus += 4.5 if str(e.get("hp_band") or "") in ("bloodied", "critical") else 2.5
                    elif sp_slug == "guiding-bolt":
                        bonus += 4.5  # Grants Advantage to next ally attack!
                    elif sp_slug == "ray-of-frost":
                        bonus += 2.0  # Applies -10 ft Slow!

                    prio_bonus = self._target_priority(e, self_pos, prefer_melee_dist=False) * 0.25
                    slot_penalty = sp_lvl * 3.0
                    score = eff_dmg + bonus + prio_bonus - slot_penalty
                    if score > best_score:
                        intent = {
                            "type": "cast_spell",
                            "actor_id": actor_id,
                            "spell": sp_slug,
                            "target_ids": [str(e["id"])],
                        }
                        if sp_lvl >= 1:
                            intent["slot_level"] = slot_lvl
                        best_score = score
                        best_plan = {
                            "spell": sp_slug,
                            "intent": intent,
                            "in_range": True,
                            "desired_dist": eff_range,
                            "anchor_pos": e["position"],
                            "expected_damage": eff_dmg,
                            "num_targets": 1,
                            "is_ranged_attack": is_ranged_atk,
                            "target_names": [str(e["name"])],
                            "rationale": f"Casting {sp_slug} (slot {slot_lvl}) on {e['name']} ({eff_dmg:.1f} eff avg).",
                            "skipped_immune_fire": skipped_immune_fire,
                        }

        if best_plan is not None:
            best_plan["skipped_immune_fire"] = skipped_immune_fire
        return best_plan

    # ------------------------------------------------------------------
    # PC Weapon Combat, Extra Attack & Masteries
    # ------------------------------------------------------------------

    def _decide_pc_weapon_combat(
        self,
        *,
        actor_id: str,
        self_data: dict[str, Any],
        self_pos: dict[str, int],
        res: dict[str, Any],
        rem_move: int,
        speed: int,
        conds: set[str],
        enemies: list[dict[str, Any]],
        allies: list[dict[str, Any]],
        turn_key: tuple[int, str],
        is_party: bool,
        occupied: set[tuple[int, int]] | None = None,
    ) -> AgentDecision | None:
        weapons = list(self_data.get("weapons") or [])
        if not weapons:
            return None

        # Check Rogue / Dual-Wield off-hand Nick attack AFTER main Light weapon attack!
        nick_used = bool(res.get("nick_used")) or (self._nick_used_turn == turn_key)
        light_made = bool(res.get("light_attack_made")) or (self._light_attack_turn == turn_key)
        has_nick_weapon = any(
            w.get("mastery") == "nick" and w.get("mastery_active") and "light" in (w.get("properties") or [])
            for w in weapons
        )
        can_main_attack = bool(res.get("action")) or int(res.get("attacks_remaining", 0)) > 0

        # If main attack(s) are spent and we made a Light attack, execute Nick off-hand (or Bonus Action off-hand)!
        if not can_main_attack and light_made:
            if has_nick_weapon:
                if nick_used:
                    return None
                nick_wp = next(
                    w
                    for w in weapons
                    if w.get("mastery") == "nick" and w.get("mastery_active") and "light" in (w.get("properties") or [])
                )
                _, max_r, _ = _weapon_max_range(nick_wp)
                valid_t = self._non_immune_weapon_targets(nick_wp, enemies, self_pos, max_r)
                if valid_t:
                    tgt = max(valid_t, key=lambda e: self._target_priority(e, self_pos))
                    self._nick_used_turn = turn_key
                    return AgentDecision(
                        intent={
                            "type": "attack",
                            "actor_id": actor_id,
                            "target_id": str(tgt["id"]),
                            "weapon": str(nick_wp["slug"]),
                            "offhand": True,
                        },
                        rationale=f"Nick mastery off-hand {nick_wp['name']} strike on {tgt['name']} (preserves Bonus Action).",
                        table_talk=f"Following up with Nick {nick_wp['name']} on {tgt['name']}!" if is_party else None,
                    )
                return None
            if res.get("bonus_action"):
                light_wps = [w for w in weapons if "light" in (w.get("properties") or [])]
                for lw in light_wps:
                    _, max_r, _ = _weapon_max_range(lw)
                    valid_t = self._non_immune_weapon_targets(lw, enemies, self_pos, max_r)
                    if valid_t:
                        tgt = max(valid_t, key=lambda e: self._target_priority(e, self_pos))
                        return AgentDecision(
                            intent={
                                "type": "attack",
                                "actor_id": actor_id,
                                "target_id": str(tgt["id"]),
                                "weapon": str(lw["slug"]),
                                "offhand": True,
                            },
                            rationale=f"Bonus Action off-hand {lw['name']} attack on {tgt['name']}.",
                            table_talk=None,
                        )
            return None

        if not can_main_attack:
            return None

        # Separate melee vs ranged weapons.
        melee_wps = [w for w in weapons if str(w.get("weapon_range") or "melee").lower() != "ranged"]
        ranged_wps = [
            w
            for w in weapons
            if str(w.get("weapon_range") or "melee").lower() == "ranged" or isinstance(w.get("throw_range"), dict)
        ]

        # Prioritize targets.
        ranked_enemies = sorted(enemies, key=lambda e: self._target_priority(e, self_pos), reverse=True)
        top_target = ranked_enemies[0]

        # If we are a melee/skirmisher PC and no enemy is within 5 ft, move into 5 ft if reachable!
        in_melee = [e for e in ranked_enemies if distance_ft(self_pos, e["position"]) <= 5]
        if not in_melee and melee_wps and rem_move >= 5 and speed > 0 and "frightened" not in conds:
            # Prefer a priority target we can actually reach within remaining movement!
            reachable = [e for e in ranked_enemies if distance_ft(self_pos, e["position"]) - 5 <= rem_move]
            move_target = reachable[0] if reachable else top_target
            dest = _step_toward(self_pos, move_target["position"], rem_move, desired_dist_ft=5, occupied=occupied)
            if dest != self_pos:
                return AgentDecision(
                    intent={"type": "move", "actor_id": actor_id, "to": dest},
                    rationale=f"Closing to melee reach of {move_target['name']}.",
                    table_talk=None,
                )

        # Attack in melee if an enemy is within 5 ft and we have melee weapons.
        if in_melee and melee_wps:
            tgt = in_melee[0]
            chosen_wp = self._pick_best_melee_weapon(melee_wps, tgt, self_data, res)
            if chosen_wp is not None:
                if "light" in (chosen_wp.get("properties") or []):
                    self._light_attack_turn = turn_key
                mastery = chosen_wp.get("mastery") if chosen_wp.get("mastery_active") else None
                m_note = f" ({mastery.capitalize()} mastery)" if mastery else ""
                is_extra = int(res.get("attacks_remaining", 0)) > 0
                talk = (
                    None
                    if is_extra
                    else f"Focusing {tgt['name']} with {chosen_wp['name']}{m_note}!"
                )
                return AgentDecision(
                    intent={
                        "type": "attack",
                        "actor_id": actor_id,
                        "target_id": str(tgt["id"]),
                        "weapon": str(chosen_wp["slug"]),
                    },
                    rationale=f"Melee {chosen_wp['name']}{m_note} strike on {tgt['name']}.",
                    table_talk=talk if is_party else None,
                )

        # Ranged / Thrown weapon attack if out of melee!
        if ranged_wps:
            best_pair: tuple[float, dict[str, Any], dict[str, Any]] | None = None
            for wp in ranged_wps:
                norm_r, max_r, _ = _weapon_max_range(wp)
                valid_t = self._non_immune_weapon_targets(wp, ranked_enemies, self_pos, max_r)
                for e in valid_t:
                    dist = distance_ft(self_pos, e["position"])
                    imm, res_d, vuln_d = _monster_defense_profile(e, self._rules)
                    mult = _damage_multiplier(str(wp.get("damage_type") or "piercing"), imm, res_d, vuln_d)
                    dmg = _weapon_expected_damage(wp) * mult
                    if dist > norm_r:
                        dmg *= 0.65
                    if wp.get("mastery_active") and wp.get("mastery") in ("vex", "slow"):
                        dmg += 1.5
                    sc_val = dmg + self._target_priority(e, self_pos, prefer_melee_dist=False) * 0.25
                    if best_pair is None or sc_val > best_pair[0]:
                        best_pair = (sc_val, wp, e)
            if best_pair is not None:
                _, wp, tgt = best_pair
                mastery = wp.get("mastery") if wp.get("mastery_active") else None
                m_note = f" ({mastery.capitalize()})" if mastery else ""
                return AgentDecision(
                    intent={
                        "type": "attack",
                        "actor_id": actor_id,
                        "target_id": str(tgt["id"]),
                        "weapon": str(wp["slug"]),
                    },
                    rationale=f"Ranged {wp['name']}{m_note} attack against {tgt['name']}.",
                    table_talk=f"Firing {wp['name']}{m_note} at {tgt['name']}!" if is_party else None,
                )

        return None

    def _non_immune_weapon_targets(
        self,
        weapon: dict[str, Any],
        enemies: list[dict[str, Any]],
        self_pos: dict[str, int],
        max_range: int,
    ) -> list[dict[str, Any]]:
        dtype = strip_prefix(str(weapon.get("damage_type") or "bludgeoning"))
        out: list[dict[str, Any]] = []
        for e in enemies:
            if distance_ft(self_pos, e["position"]) > max_range:
                continue
            imm, _, _ = _monster_defense_profile(e, self._rules)
            if dtype in imm:
                continue
            out.append(e)
        return out

    def _pick_best_melee_weapon(
        self,
        melee_wps: list[dict[str, Any]],
        target: dict[str, Any],
        self_data: dict[str, Any],
        res: dict[str, Any],
    ) -> dict[str, Any] | None:
        t_effects = _effect_kinds(target)
        imm, res_d, vuln_d = _monster_defense_profile(target, self._rules)
        has_nick = any(w.get("mastery") == "nick" and w.get("mastery_active") for w in melee_wps)

        best_wp: dict[str, Any] | None = None
        best_score = -1.0
        for wp in melee_wps:
            dtype = strip_prefix(str(wp.get("damage_type") or "bludgeoning"))
            mult = _damage_multiplier(dtype, imm, res_d, vuln_d)
            if mult == 0.0:
                continue
            dmg = _weapon_expected_damage(wp) * mult
            mastery = wp.get("mastery") if wp.get("mastery_active") else None
            score = dmg

            # Rogue Vex + Nick synergy: open with Light Vex weapon (`shortsword`) to unlock Nick (`dagger`)!
            if has_nick and "light" in (wp.get("properties") or []) and mastery == "vex":
                score += 6.0
            # Fighter Sap vs un-Sapped melee threat on first attack, Graze (`greatsword`) on follow-up!
            if mastery == "sap" and "sap" not in t_effects and int(res.get("attacks_remaining", 0)) == 0:
                score += 3.5
            elif mastery == "graze":
                score += 2.2
            elif mastery == "vex":
                score += 2.0
            elif mastery == "topple":
                score += 2.0

            if score > best_score:
                best_score = score
                best_wp = wp
        return best_wp

    # ------------------------------------------------------------------
    # Monster Tactical Decision Logic (§4.3)
    # ------------------------------------------------------------------

    def _decide_monster(
        self,
        observation: dict[str, Any],
        *,
        actor_id: str,
        self_pos: dict[str, int],
        res: dict[str, Any],
        rem_move: int,
        speed: int,
        conds: set[str],
        enemies: list[dict[str, Any]],
        allies: list[dict[str, Any]],
    ) -> AgentDecision:
        if not res.get("action"):
            return AgentDecision(
                intent={"type": "end_turn", "actor_id": actor_id},
                rationale="Monster action spent; ending turn.",
                table_talk=None,
            )

        self_data = observation.get("self") or {}
        all_actions = list(self_data.get("monster_actions") or [])
        avail_actions = _available_monster_actions(observation)
        if not avail_actions:
            return AgentDecision(
                intent={"type": "end_turn", "actor_id": actor_id},
                rationale="No available monster actions; ending turn.",
                table_talk=None,
            )

        friendlies = [{"id": actor_id, "position": self_pos}] + list(allies)
        ranked_enemies = sorted(enemies, key=lambda e: self._target_priority(e, self_pos), reverse=True)

        # 1. Recharged Breath Weapon / Save AoE (e.g., Fire Breath 15-ft cone)
        aoe_actions = [
            a
            for a in avail_actions
            if not (a.get("attack_bonus") is not None or a.get("kind") == "attack")
            and isinstance(a.get("save"), dict)
        ]
        for breath in aoe_actions:
            cone_size = int((breath.get("aoe") or {}).get("size_ft") or 15)
            # Check non-immune enemies.
            valid_e = [
                e
                for e in enemies
                if _monster_action_expected_damage(breath, all_actions, e, self._rules)[0] > 0.0
            ]
            if not valid_e:
                continue
            cur_hit = _cone_aoe_targets(self_pos, valid_e, friendlies, size_ft=cone_size, avoid_friendlies=True)

            # Can we move within rem_move to hit MORE targets with the 15-ft cone?
            if rem_move >= 5 and speed > 0 and "frightened" not in conds and len(cur_hit) < min(2, len(valid_e)):
                best_pos: dict[str, int] | None = None
                best_cnt = len(cur_hit)
                for e in valid_e:
                    cand = _step_toward(self_pos, e["position"], rem_move, desired_dist_ft=5, occupied=_occupied_squares(observation))
                    if cand == self_pos:
                        continue
                    c_hit = _cone_aoe_targets(cand, valid_e, friendlies, size_ft=cone_size, avoid_friendlies=True)
                    if len(c_hit) > best_cnt:
                        best_cnt = len(c_hit)
                        best_pos = cand
                if best_pos is not None:
                    return AgentDecision(
                        intent={"type": "move", "actor_id": actor_id, "to": best_pos},
                        rationale=f"Repositioning to catch {best_cnt} target(s) in {breath['name']} cone.",
                        table_talk=None,
                    )

            if cur_hit:
                return AgentDecision(
                    intent={
                        "type": "attack",
                        "actor_id": actor_id,
                        "action": str(breath["name"]),
                        "target_ids": [str(e["id"]) for e in cur_hit],
                    },
                    rationale=f"Unleashing {breath['name']} against {len(cur_hit)} target(s).",
                    table_talk=None,
                )

        # 2. Separate Melee/Multiattack vs Ranged vs Paralyzer Claw actions
        by_name = {str(a.get("name") or "").strip().lower(): a for a in avail_actions}
        multi_act = next(
            (a for a in avail_actions if isinstance(a.get("multiattack"), dict) or str(a.get("name") or "").lower() == "multiattack"),
            None,
        )
        claw_act = next(
            (
                a
                for a in avail_actions
                if any(
                    (c.get("condition") if isinstance(c, dict) else str(c)) == "paralyzed"
                    for c in (a.get("conditions_applied") or [])
                )
            ),
            None,
        )
        ranged_acts = [a for a in avail_actions if _monster_action_range(a, all_actions)[1]]
        melee_acts = [
            a
            for a in avail_actions
            if a not in aoe_actions and not _monster_action_range(a, all_actions)[1]
        ]

        # 3. Ghoul / Ghast Claw vs Multiattack/Bite logic
        in_melee = [e for e in ranked_enemies if distance_ft(self_pos, e["position"]) <= 5]
        if in_melee:
            paralyzed_in_melee = [e for e in in_melee if "paralyzed" in _condition_set(e)]
            if paralyzed_in_melee:
                tgt = paralyzed_in_melee[0]
                best_dmg_act = multi_act or max(
                    melee_acts,
                    key=lambda a: _monster_action_expected_damage(a, all_actions, tgt, self._rules)[0],
                )
                return AgentDecision(
                    intent={
                        "type": "attack",
                        "actor_id": actor_id,
                        "action": str(best_dmg_act["name"]),
                        "target_ids": [str(tgt["id"])],
                    },
                    rationale=f"Exploiting Paralyzed {tgt['name']} (auto-crit) with {best_dmg_act['name']}.",
                    table_talk=None,
                )

            if claw_act is not None:
                tgt = in_melee[0]
                return AgentDecision(
                    intent={
                        "type": "attack",
                        "actor_id": actor_id,
                        "action": str(claw_act["name"]),
                        "target_ids": [str(tgt["id"])],
                    },
                    rationale=f"Using {claw_act['name']} on {tgt['name']} to inflict Paralyzed.",
                    table_talk=None,
                )

            if melee_acts:
                tgt = in_melee[0]
                best_m = max(
                    melee_acts,
                    key=lambda a: _monster_action_expected_damage(a, all_actions, tgt, self._rules)[0],
                )
                return AgentDecision(
                    intent={
                        "type": "attack",
                        "actor_id": actor_id,
                        "action": str(best_m["name"]),
                        "target_ids": [str(tgt["id"])],
                    },
                    rationale=f"Attacking {tgt['name']} in melee with {best_m['name']}.",
                    table_talk=None,
                )

        # 4. Ranged monster action (e.g., Goblin Warrior / Skeleton Shortbow) when out of melee!
        if ranged_acts:
            for r_act in ranged_acts:
                max_r, _, _ = _monster_action_range(r_act, all_actions)
                in_r = [e for e in ranked_enemies if distance_ft(self_pos, e["position"]) <= max_r]
                if in_r:
                    tgt = in_r[0]
                    return AgentDecision(
                        intent={
                            "type": "attack",
                            "actor_id": actor_id,
                            "action": str(r_act["name"]),
                            "target_ids": [str(tgt["id"])],
                        },
                        rationale=f"Firing ranged {r_act['name']} at {tgt['name']}.",
                        table_talk=None,
                    )

        # 5. Melee monster out of reach: close to 5 ft of best reachable target!
        if rem_move >= 5 and speed > 0 and "frightened" not in conds:
            reachable = [e for e in ranked_enemies if distance_ft(self_pos, e["position"]) - 5 <= rem_move]
            move_target = reachable[0] if reachable else ranked_enemies[0]
            dest = _step_toward(self_pos, move_target["position"], rem_move, desired_dist_ft=5, occupied=_occupied_squares(observation))
            if dest != self_pos:
                return AgentDecision(
                    intent={"type": "move", "actor_id": actor_id, "to": dest},
                    rationale=f"Closing to melee reach of {move_target['name']}.",
                    table_talk=None,
                )

        # 6. Still out of range and movement spent: Dash to close gap.
        if speed > 0 and "frightened" not in conds:
            return AgentDecision(
                intent={"type": "dash", "actor_id": actor_id},
                rationale="Dashing to close distance to opposing combatants.",
                table_talk=None,
            )

        return AgentDecision(
            intent={"type": "end_turn", "actor_id": actor_id},
            rationale="Cannot act further; ending turn.",
            table_talk=None,
        )


# ---------------------------------------------------------------------------
# Offline Driver 4: ReplayDriver (§4.3)
# ---------------------------------------------------------------------------


class ReplayDriver:
    """Replays recorded decisions, including rejected attempts, from a MatchTranscript."""

    name: str = "replay"

    def __init__(self, transcript: dict[str, Any], side: str | None = None) -> None:
        if not isinstance(transcript, dict) or not isinstance(transcript.get("actions"), list):
            raise ValueError("ReplayDriver requires a MatchTranscript dict with an 'actions' list.")
        self._side: str | None = side
        queued: list[tuple[tuple[int, int, int, int, int], dict[str, Any]]] = []
        for entry in transcript.get("violations") or []:
            if not isinstance(entry, dict):
                continue
            if side is not None and entry.get("side") != side:
                continue
            intent = entry.get("intent") if isinstance(entry.get("intent"), dict) else {"type": ""}
            queued.append(
                (
                    (
                        int(entry.get("round") or 0),
                        int(entry.get("turn_index") or 0),
                        int(entry.get("action_index") or 0),
                        int(entry.get("attempt") or 1),
                        0,
                    ),
                    {
                        "actor_id": entry.get("actor_id"),
                        "intent": copy.deepcopy(intent),
                        "rationale": "",
                        "table_talk": None,
                        "usage": {},
                    },
                )
            )
        for entry in transcript["actions"]:
            if not isinstance(entry, dict):
                continue
            if side is not None and entry.get("side") != side:
                continue
            if entry.get("auto_skipped") or entry.get("auto_end_turn") or entry.get("forced_fallback"):
                continue
            if not isinstance(entry.get("intent"), dict):
                continue
            queued.append(
                (
                    (
                        int(entry.get("round") or 0),
                        int(entry.get("turn_index") or 0),
                        int(entry.get("action_index") or 0),
                        int(entry.get("attempts") or 1),
                        1,
                    ),
                    copy.deepcopy(entry),
                )
            )
        queued.sort(key=lambda item: item[0])
        self._queue: list[dict[str, Any]] = [item[1] for item in queued]
        self._index: int = 0

    @property
    def remaining(self) -> int:
        return max(0, len(self._queue) - self._index)

    def reset(self, seed: int | None = None) -> None:
        self._index = 0

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        if self._index >= len(self._queue):
            raise RuntimeError(
                f"ReplayDriver exhausted (side={self._side!r}, consumed {self._index}/{len(self._queue)} actions)."
            )
        entry = self._queue[self._index]
        self._index += 1

        expected_actor = str(entry.get("actor_id") or "")
        actual_actor = str(
            (observation.get("self") or {}).get("id")
            or (observation.get("legal_actions") or {}).get("active_actor_id")
            or ""
        )
        if expected_actor and actual_actor and expected_actor != actual_actor:
            raise RuntimeError(
                f"ReplayDriver actor mismatch at action index {self._index - 1}: "
                f"expected '{expected_actor}', observed '{actual_actor}'."
            )

        return AgentDecision(
            intent=copy.deepcopy(entry["intent"]),
            rationale=str(entry.get("rationale") or ""),
            table_talk=entry.get("table_talk"),
            usage=copy.deepcopy(entry.get("usage") or {}),
        )


# ---------------------------------------------------------------------------
# Pure-Stdlib Live HTTP LLM Drivers (§4.4)
# ---------------------------------------------------------------------------


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_MAX_HTTP_BODY = 1024 * 1024
_SECRET_QUERY_KEYS = frozenset({"key", "api_key", "apikey", "token", "access_token", "secret"})
_SECRET_TEXT_RE = re.compile(
    r"(?i)("
    r"(?:api[_-]?key|x-goog-api-key|x-api-key|token|secret|password|authorization)"
    r"(?:\s+header)?\s*[=:]\s*\S+"
    r"|bearer\s+\S+"
    r"|\bsk-[A-Za-z0-9_\-]{8,}"
    r"|\bAIza[0-9A-Za-z_\-]{10,}"
    r"|[?&](?:key|api_key|apikey|token|access_token|secret)=[^&\s]+"
    r")"
)


def _secret_forms(secret: str) -> list[str]:
    """Raw value plus the escaped form header errors use (`repr` without quotes)."""
    forms = [secret]
    forms.extend(line for line in secret.splitlines() if line and line != secret)
    rendered = repr(secret)
    if len(rendered) >= 2 and rendered[0] == rendered[-1] and rendered[0] in "'\"":
        forms.append(rendered[1:-1])
    forms.append(rendered)
    return forms


def scrub_secrets(text: str, secrets: list[str] | None = None) -> str:
    """Redact secret-looking substrings and any explicit secret values from `text`.

    Header validation quotes CR/LF keys with backslash escapes, so those forms
    are removed as well as the raw value.
    """
    if not text:
        return text
    out = str(text)
    for secret in secrets or []:
        raw = str(secret or "")
        if len(raw) < 4:
            continue
        forms = [form for form in _secret_forms(raw) if len(form) >= 4]
        for form in sorted(forms, key=len, reverse=True):
            if form in out:
                out = out.replace(form, "[REDACTED]")
    return _SECRET_TEXT_RE.sub("[REDACTED]", out)


def _json_nesting_exceeds(text: str, limit: int = _MAX_JSON_DEPTH) -> bool:
    """Return whether `{` / `[` nesting outside strings is deeper than `limit`.

    This runs before `json.loads` so a hostile document cannot raise RecursionError.
    """
    depth = 0
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            if depth > limit:
                return True
        elif ch in "}]" and depth:
            depth -= 1
    return False


def _reject_control_chars_in_secret(value: str) -> None:
    """Refuse CR/LF and other controls before they are placed in an HTTP header."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise RuntimeError("LLM API key was rejected.")


def _is_local_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").strip().lower()
    return host in _LOOPBACK_HOSTS


def _assert_safe_llm_url(url: str) -> None:
    """Require https for LLM endpoints. Plain http is allowed only on loopback.

    Credentials in the userinfo or query string are refused. Redirects are
    refused separately by the opener, so this check only covers the URL the
    caller asked to contact.
    """
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()
    if parsed.username or parsed.password:
        raise RuntimeError("LLM endpoint URL must not include userinfo credentials.")
    if parsed.query:
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if any(name.lower() in _SECRET_QUERY_KEYS for name, _ in pairs):
            raise RuntimeError("LLM endpoint URL must not carry credentials in the query string.")
    if scheme == "https" and host:
        return
    if scheme == "http" and host in _LOOPBACK_HOSTS:
        return
    raise RuntimeError("LLM endpoint must use https (http is allowed only on loopback).")


class ProviderUnavailable(RuntimeError):
    """An LLM provider can't serve this contender any more: out of credits or quota, or failing persistently.

    The arena stops that contender cleanly (its finished matches are kept and it's marked aborted) instead of
    recording every failed call as a rules violation and playing on with forced end_turns.
    `reason` is "out_of_credits" or "unavailable".
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class RetryableLLMError(RuntimeError):
    """A transient provider failure (rate limit, 5xx, overloaded, timeout, connection) worth retrying.

    `billed` says whether the provider may have done (and charged for) the work: true for timeouts and 5xx, false
    for 429/409/425 rejections and connection failures. The budget meter charges billed attempts their worst case.
    """

    def __init__(self, message: str, retry_after_s: float | None = None, *, billed: bool = False) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.billed = billed


# Provider error codes/types that mean "no credits or quota left", checked in the error body. The body itself is
# never echoed: it can contain account details.
_QUOTA_MARKERS = (
    "insufficient_quota",
    "billing",
    "credit",
    "exceeded your current quota",
    "quota exceeded",
    "resource_exhausted",
    "payment required",
)
_RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _classify_http_error(code: int, body: bytes, retry_after: str | None) -> RuntimeError:
    text = body.decode("utf-8", errors="replace").lower()
    if code == 402 or (code in (400, 403, 429) and any(m in text for m in _QUOTA_MARKERS)):
        return ProviderUnavailable("out_of_credits", f"LLM provider reports no remaining credits or quota (HTTP {code}).")
    if code in _RETRYABLE_HTTP:
        wait = None
        try:
            wait = float(retry_after) if retry_after else None
        except ValueError:
            wait = None
        return RetryableLLMError(f"LLM HTTP {code}", retry_after_s=wait, billed=code >= 500 or code == 408)
    if code in (401, 403):
        return ProviderUnavailable("unavailable", f"LLM provider rejected the credentials (HTTP {code}).")
    return RuntimeError(f"LLM HTTP {code}")


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so Authorization and API-key headers cannot follow."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


# Env proxies are not installed: a proxy would receive the API key.
_LLM_OPENER = urllib.request.build_opener(_RefuseRedirects(), urllib.request.ProxyHandler({}))


def _read_bounded(resp: Any, limit: int = _MAX_HTTP_BODY) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = resp.read(65536)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise RuntimeError("LLM HTTP response exceeded 1MB.")
        chunks.append(block)
    return b"".join(chunks)


def _default_http_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    """Send an HTTP POST JSON request strictly via Python stdlib `urllib.request`.

    Failure text never includes the URL, headers, or response body. Redirects
    are refused. The body is capped at 1 MB.
    """
    _assert_safe_llm_url(url)
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **headers,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with _LLM_OPENER.open(req, timeout=float(timeout_s)) as resp:
            raw_bytes = _read_bounded(resp)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(8192) or b""
        except Exception:
            pass
        if exc.code in (301, 302, 303, 307, 308):
            raise RuntimeError("LLM redirect refused.") from None
        retry_after = exc.headers.get("retry-after") if getattr(exc, "headers", None) is not None else None
        raise _classify_http_error(int(exc.code), body, retry_after) from None
    except urllib.error.URLError:
        raise RetryableLLMError("LLM connection failed.") from None
    except TimeoutError:
        raise RetryableLLMError("LLM request timed out.", billed=True) from None
    except RuntimeError:
        raise

    raw_body = raw_bytes.decode("utf-8", errors="replace")
    if _json_nesting_exceeds(raw_body):
        raise RuntimeError("LLM response JSON nesting exceeds the limit.")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        raise RuntimeError("LLM response was not valid JSON.") from None
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM response was not a JSON object.")
    return parsed


class BaseLLMDriver:
    """Shared base class for stdlib HTTP LLM drivers (`openai-compat`, `anthropic`, `gemini`)."""

    DEFAULT_API_BASE: str = ""
    DEFAULT_API_KEY_ENV: str = ""
    PROVIDER_PREFIX: str = ""

    def __init__(
        self,
        model: str,
        *,
        api_base: str | None = None,
        api_key_env: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        timeout_s: float = 60.0,
        http_transport: HttpTransport | None = None,
    ) -> None:
        model_clean = str(model).strip()
        if not model_clean:
            raise ValueError(f"{self.__class__.__name__} requires a non-empty 'model' name.")
        self.model: str = model_clean
        self.name: str = f"{self.PROVIDER_PREFIX}:{self.model}"
        self.api_base: str = str(api_base or self.DEFAULT_API_BASE).rstrip("/")
        self.api_key_env: str = str(api_key_env or self.DEFAULT_API_KEY_ENV)
        self._explicit_api_key: str | None = api_key
        self.temperature: float = float(temperature)
        self.max_tokens: int = int(max_tokens)
        self.timeout_s: float = float(timeout_s)
        self._transport: HttpTransport = http_transport or _default_http_transport
        self._sleep: Callable[[float], None] = time.sleep
        self._consecutive_failures = 0
        # Set by the bench for paid contenders: a budget.MatchMeter checked before and charged after every attempt.
        self.meter: Any = None

    def _resolve_api_key(self, *, allow_empty_local: bool = False) -> str:
        key = self._explicit_api_key if self._explicit_api_key is not None else os.environ.get(self.api_key_env, "")
        key = str(key or "").strip()
        if not key:
            if allow_empty_local and _is_local_url(self.api_base):
                return ""
            if self._transport is not _default_http_transport:
                return ""
            # A missing key can't fix itself mid-bench: stop this contender instead of failing every decision.
            raise ProviderUnavailable(
                "unavailable",
                f"{self.__class__.__name__} requires an API key via 'api_key' or env var '{self.api_key_env}'.",
            )
        return key

    def _build_request(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        raise NotImplementedError

    def _parse_response(self, response: dict[str, Any]) -> tuple[str, dict[str, int]]:
        raise NotImplementedError

    def reset(self, seed: int | None = None) -> None:
        return None

    # Transient failures (rate limit, 5xx, overloaded, timeout) are retried with backoff. After
    # MAX_CONSECUTIVE_FAILURES decisions in a row that still failed, the provider counts as unavailable (e.g. a usage
    # limit), and ProviderUnavailable stops this contender instead of forcing end_turn for the rest of the bench.
    MAX_ATTEMPTS = 4
    BACKOFF_S = (2.0, 8.0, 30.0)
    MAX_CONSECUTIVE_FAILURES = 3

    @staticmethod
    def _max_output_tokens(payload: dict[str, Any]) -> int:
        """The output cap this request sends (reasoning and thinking count toward it). 0 if it has none."""
        for key in ("max_completion_tokens", "max_tokens"):
            if isinstance(payload.get(key), int):
                return int(payload[key])
        gen = payload.get("generationConfig")
        if isinstance(gen, dict) and isinstance(gen.get("maxOutputTokens"), int):
            return int(gen["maxOutputTokens"])
        return 0

    def _send(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """Call the transport with retries, metered; raise ProviderUnavailable when the provider can't serve us.

        Returns (response, worst-case cost of one attempt). With a meter, every attempt is checked against the
        match's limit first (MatchBudgetExceeded aborts before the call), and billed failures are charged.
        """
        secret_values = [self._explicit_api_key or "", os.environ.get(self.api_key_env, "")]
        bound = self.meter.call_bound_usd(payload, self._max_output_tokens(payload)) if self.meter is not None else 0.0
        last: Exception | None = None
        for attempt in range(self.MAX_ATTEMPTS):
            if self.meter is not None:
                self.meter.check(bound)
            try:
                resp = self._transport(url, headers, payload, self.timeout_s)
                self._consecutive_failures = 0
                return resp, bound
            except ProviderUnavailable:
                raise
            except RetryableLLMError as exc:
                last = exc
                if self.meter is not None and exc.billed:
                    self.meter.charge_bound(bound)
                if attempt + 1 < self.MAX_ATTEMPTS:
                    wait = self.BACKOFF_S[min(attempt, len(self.BACKOFF_S) - 1)]
                    if exc.retry_after_s is not None:
                        wait = max(wait, min(float(exc.retry_after_s), 120.0))
                    self._sleep(wait)
            except Exception as exc:
                cleaned = scrub_secrets(str(exc) or "LLM request failed.", secret_values)
                raise RuntimeError(cleaned or "LLM request failed.") from None
        self._consecutive_failures += 1
        msg = scrub_secrets(str(last) or "LLM request failed.", secret_values) or "LLM request failed."
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            raise ProviderUnavailable(
                "unavailable",
                f"LLM provider failed {self._consecutive_failures} decisions in a row after retries ({msg}).",
            )
        raise RuntimeError(f"{msg} (after {self.MAX_ATTEMPTS} attempts)")

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(observation)
        url, headers, payload = self._build_request(system_prompt, user_prompt)
        _assert_safe_llm_url(url)
        resp_json, bound = self._send(url, headers, payload)
        try:
            raw_text, usage = self._parse_response(resp_json)
        except Exception:
            if self.meter is not None:
                self.meter.charge_bound(bound)  # can't read the usage: charge the worst case (fail closed)
            raise
        if self.meter is not None:
            self.meter.charge_usage(usage, bound)
        parsed = extract_decision_json(raw_text)

        intent = parsed["intent"]
        rationale = str(parsed.get("rationale") or "")
        raw_talk = parsed.get("table_talk")
        table_talk = str(raw_talk).strip() if isinstance(raw_talk, str) and raw_talk.strip() else None

        return AgentDecision(
            intent=intent,
            rationale=rationale,
            table_talk=table_talk,
            raw_response=raw_text,
            usage=usage,
        )


class OpenAICompatDriver(BaseLLMDriver):
    """OpenAI-compatible Chat Completions driver (OpenAI, xAI, OpenRouter, Ollama, vLLM)."""

    DEFAULT_API_BASE = "https://api.openai.com/v1"
    DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
    PROVIDER_PREFIX = "openai-compat"

    def _build_request(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        key = self._resolve_api_key(allow_empty_local=True)
        url = f"{self.api_base}/chat/completions"
        headers: dict[str, str] = {}
        if key:
            _reject_control_chars_in_secret(key)
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        return url, headers, payload

    def _parse_response(self, response: dict[str, Any]) -> tuple[str, dict[str, int]]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenAI-compatible response missing 'choices'.")
        msg = choices[0].get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            raw_text = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            raw_text = str(content or "")

        u = response.get("usage") or {}
        prompt_t = int(u.get("prompt_tokens", 0) or 0)
        comp_t = int(u.get("completion_tokens", 0) or 0)
        total_t = int(u.get("total_tokens", prompt_t + comp_t) or (prompt_t + comp_t))
        reasoning_t = int(((u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)) or 0)
        return raw_text, {
            "prompt_tokens": prompt_t,
            "completion_tokens": comp_t,
            "total_tokens": total_t,
            "reasoning_tokens": reasoning_t,
            # OpenAI counts reasoning inside completion_tokens (#budget).
            "billable_output_tokens": self._billable_output(comp_t, reasoning_t),
        }

    def _billable_output(self, completion_tokens: int, reasoning_tokens: int) -> int:
        return completion_tokens


class OpenAIDriver(OpenAICompatDriver):
    """OpenAI Chat Completions with current-model request rules (`openai:<model>`).

    Current OpenAI models (GPT-5.x, GPT-6.x, o-series) reject `max_tokens` ("use 'max_completion_tokens'")
    and any `temperature` other than the default, so neither is sent. Reasoning tokens count toward
    `max_completion_tokens`, so the cap has a floor. Use `openai-compat:<model>` for other OpenAI-style
    endpoints (Ollama, vLLM, OpenRouter) that still expect `max_tokens` and `temperature`.
    """

    PROVIDER_PREFIX = "openai"
    MIN_MAX_TOKENS = 4096

    def _build_request(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url, headers, payload = super()._build_request(system_prompt, user_prompt)
        payload.pop("temperature", None)
        payload.pop("max_tokens", None)
        payload["max_completion_tokens"] = max(self.max_tokens, self.MIN_MAX_TOKENS)
        return url, headers, payload


class XAIDriver(OpenAICompatDriver):
    """xAI Grok via its OpenAI-compatible API (`xai:<model>`, e.g. `xai:grok-4.7`).

    Grok reasoning tokens count toward `max_tokens`, so the cap has a floor.
    """

    DEFAULT_API_BASE = "https://api.x.ai/v1"
    DEFAULT_API_KEY_ENV = "XAI_API_KEY"
    PROVIDER_PREFIX = "xai"
    MIN_MAX_TOKENS = 4096

    def _billable_output(self, completion_tokens: int, reasoning_tokens: int) -> int:
        # xAI reports reasoning tokens separately from completion_tokens (prompt + completion + reasoning = total)
        # and bills them as output.
        return completion_tokens + reasoning_tokens
    MIN_TIMEOUT_S = 180.0  # grok-4.7 reasoning turns ran past the 60 s default

    def __init__(self, model: str, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self.timeout_s = max(self.timeout_s, self.MIN_TIMEOUT_S)

    def _build_request(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url, headers, payload = super()._build_request(system_prompt, user_prompt)
        payload["max_tokens"] = max(self.max_tokens, self.MIN_MAX_TOKENS)
        return url, headers, payload


class AnthropicDriver(BaseLLMDriver):
    """Anthropic Messages API driver (`POST /v1/messages`)."""

    DEFAULT_API_BASE = "https://api.anthropic.com"
    DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
    PROVIDER_PREFIX = "anthropic"
    ANTHROPIC_VERSION = "2023-06-01"
    MIN_MAX_TOKENS = 4096  # headroom for default-on thinking plus the JSON decision

    OAUTH_BETA = "oauth-2025-04-20"
    AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"

    def _oauth_token(self) -> str:
        """An OAuth access token from `ANTHROPIC_AUTH_TOKEN`, else from the `ant` CLI's sign-in (`ant auth login`).

        Only ever used for the official API host, so a custom --api-base never receives the user's sign-in.
        `ant auth print-credentials --access-token` refreshes the token when needed, so asking per request keeps
        a long play session signed in; an exported token is short-lived and is not refreshed.
        """
        if self.api_base != self.DEFAULT_API_BASE:
            return ""
        token = os.environ.get(self.AUTH_TOKEN_ENV, "").strip()
        if token:
            return token
        ant = shutil.which("ant")
        if not ant:
            return ""
        try:
            proc = subprocess.run(
                [ant, "auth", "print-credentials", "--access-token"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        token = proc.stdout.strip() if proc.returncode == 0 else ""
        # The bare token is one line; anything else (e.g. the JSON form) must not reach a header.
        return token if token and "\n" not in token and not token.startswith("{") else ""

    def _build_request(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = f"{self.api_base}/v1/messages"
        headers: dict[str, str] = {
            "anthropic-version": self.ANTHROPIC_VERSION,
        }
        # Credential order, as in the Anthropic SDKs: API key, then ANTHROPIC_AUTH_TOKEN, then the `ant` sign-in.
        explicit = self._explicit_api_key if self._explicit_api_key is not None else os.environ.get(self.api_key_env, "")
        oauth = "" if str(explicit or "").strip() else self._oauth_token()
        if oauth:
            _reject_control_chars_in_secret(oauth)
            headers["Authorization"] = f"Bearer {oauth}"
            headers["anthropic-beta"] = self.OAUTH_BETA
            key = ""
        else:
            try:
                key = self._resolve_api_key(allow_empty_local=True)
            except RuntimeError as exc:
                raise ProviderUnavailable(
                    "unavailable",
                    f"{exc} Or sign in once with the Anthropic CLI (`ant auth login`), or set {self.AUTH_TOKEN_ENV}.",
                ) from None
        if key:
            _reject_control_chars_in_secret(key)
            headers["x-api-key"] = key
        # Current Claude models (Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.x) reject sampling parameters:
        # `temperature` returns a 400 (Haiku 5.5: any non-default value), so it is never sent and --temperature
        # has no effect on this driver. They also think by default, and thinking tokens count toward max_tokens,
        # so a small cap can end the turn before any text is written; keep enough headroom for both.
        payload = {
            "model": self.model,
            "max_tokens": max(self.max_tokens, self.MIN_MAX_TOKENS),
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        return url, headers, payload

    def _parse_response(self, response: dict[str, Any]) -> tuple[str, dict[str, int]]:
        blocks = response.get("content")
        if not isinstance(blocks, list):
            raise ValueError("Anthropic response missing 'content' list.")
        raw_text = "".join(
            str(b.get("text", ""))
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
        stop = response.get("stop_reason")
        if not raw_text.strip() and stop in ("refusal", "max_tokens"):
            # Name the cause instead of failing later with an opaque "no JSON found".
            raise ValueError(f"Anthropic response has no text (stop_reason={stop!r}).")
        u = response.get("usage") or {}
        inp_t = int(u.get("input_tokens", 0) or 0)
        out_t = int(u.get("output_tokens", 0) or 0)
        return raw_text, {
            "prompt_tokens": inp_t,
            "completion_tokens": out_t,
            "total_tokens": inp_t + out_t,
            "billable_output_tokens": out_t,  # includes thinking
        }


class GeminiDriver(BaseLLMDriver):
    """Google Gemini `generateContent` API driver."""

    DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
    DEFAULT_API_KEY_ENV = "GEMINI_API_KEY"
    PROVIDER_PREFIX = "gemini"
    MIN_MAX_TOKENS = 16384  # gemini-4-neon used up a 4096 cap on thinking in the Arena (finishReason MAX_TOKENS)
    MIN_TIMEOUT_S = 180.0  # gemini-4-neon timed out 23 times in 9 smoke games at the 60 s default

    def __init__(self, model: str, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self.timeout_s = max(self.timeout_s, self.MIN_TIMEOUT_S)

    def _build_request(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        key = self._resolve_api_key(allow_empty_local=True)
        encoded_model = urllib.parse.quote(self.model, safe="")
        url = f"{self.api_base}/v1beta/models/{encoded_model}:generateContent"
        headers: dict[str, str] = {}
        if key:
            _reject_control_chars_in_secret(key)
            headers["x-goog-api-key"] = key
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                # Thinking tokens count toward maxOutputTokens on Gemini 2.5+ (and Gemini 4), so keep headroom.
                "maxOutputTokens": max(self.max_tokens, self.MIN_MAX_TOKENS),
            },
        }
        return url, headers, payload

    def _parse_response(self, response: dict[str, Any]) -> tuple[str, dict[str, int]]:
        candidates = response.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Gemini response missing 'candidates'.")
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        raw_text = "".join(
            str(p.get("text", ""))
            for p in parts
            if isinstance(p, dict) and "text" in p and not p.get("thought")
        )
        finish = candidates[0].get("finishReason")
        if not raw_text.strip() and finish not in (None, "STOP"):
            raise ValueError(f"Gemini response has no text (finishReason={finish!r}).")
        u = response.get("usageMetadata") or {}
        prompt_t = int(u.get("promptTokenCount", 0) or 0)
        cand_t = int(u.get("candidatesTokenCount", 0) or 0)
        thoughts_t = int(u.get("thoughtsTokenCount", 0) or 0)
        total_t = int(u.get("totalTokenCount", prompt_t + cand_t) or (prompt_t + cand_t))
        return raw_text, {
            "prompt_tokens": prompt_t,
            "completion_tokens": cand_t,
            "total_tokens": total_t,
            "reasoning_tokens": thoughts_t,
            # Thinking tokens are billed as output on top of the candidates.
            "billable_output_tokens": cand_t + thoughts_t,
        }


# ---------------------------------------------------------------------------
# Driver Factory (§4.5)
# ---------------------------------------------------------------------------


def create_driver(
    spec: str | dict[str, Any],
    *,
    rules: RulesStore | None = None,
    seed: int = 1,
    api_base: str | None = None,
    api_key_env: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout_s: float = 60.0,
    http_transport: HttpTransport | None = None,
    replay_transcript: dict[str, Any] | None = None,
    replay_side: str | None = None,
    allow_human: bool = False,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], Any] | None = None,
    nl_driver: BaseLLMDriver | None = None,
) -> AgentDriver:
    """Instantiate any supported offline, human, or live LLM Arena driver from a spec.

    Accepted string specs:
    - `"random"`
    - `"greedy"`
    - `"heuristic-tactical"`
    - `"human"`
    - `"replay"` (requires `replay_transcript`)
    - `"openai-compat:<model>"` (any OpenAI-style endpoint; sends `max_tokens` and `temperature`)
    - `"openai:<model>"` (api.openai.com with current-model rules; `OPENAI_API_KEY`)
    - `"xai:<model>"` (Grok at api.x.ai; `XAI_API_KEY`)
    - `"anthropic:<model>"`
    - `"gemini:<model>"`
    - `"typesafe:<model>"` (TypeSafe AI Jev, e.g. `typesafe:jev-1.14.0`; party seat only, see `arena/jev.py`)

    Also accepts a dict spec (e.g. `{"driver": "openai-compat:gpt-4o-mini", "api_base": ...}`
    or `{"type": "anthropic", "model": "..."}`). Raises `ValueError` on unknown specs.
    """
    if isinstance(spec, dict):
        cfg = dict(spec)
        raw_spec = cfg.get("driver") or cfg.get("spec")
        if not raw_spec:
            d_type = str(cfg.get("type") or "").strip()
            d_model = str(cfg.get("model") or "").strip()
            raw_spec = f"{d_type}:{d_model}" if (d_type and d_model) else d_type
        return create_driver(
            str(raw_spec or ""),
            rules=rules,
            seed=int(cfg.get("seed", seed)),
            api_base=cfg.get("api_base", api_base),
            api_key_env=cfg.get("api_key_env", api_key_env),
            api_key=cfg.get("api_key", api_key),
            temperature=float(cfg.get("temperature", temperature)),
            max_tokens=int(cfg.get("max_tokens", max_tokens)),
            timeout_s=float(cfg.get("timeout_s", timeout_s)),
            http_transport=cfg.get("http_transport", http_transport),
            replay_transcript=cfg.get("replay_transcript", replay_transcript),
            replay_side=cfg.get("replay_side", replay_side),
            allow_human=bool(cfg.get("allow_human", allow_human)),
            input_fn=cfg.get("input_fn", input_fn),
            output_fn=cfg.get("output_fn", output_fn),
            nl_driver=cfg.get("nl_driver", nl_driver),
        )

    if not isinstance(spec, str) or not spec.strip():
        raise ValueError(f"Invalid empty driver spec: {spec!r}")

    norm = spec.strip()
    lower = norm.lower()

    if lower == "random":
        return RandomDriver(seed=seed, rules=rules)
    if lower == "greedy":
        return GreedyDriver(rules=rules)
    if lower == "heuristic-tactical":
        return HeuristicTacticalDriver(rules=rules)
    if lower == "human":
        if not allow_human:
            raise ValueError(
                "Driver spec 'human' is only available in interactive play "
                "(`srd arena play` / `srd-arena play`)."
            )
        from .human import HumanDriver

        return HumanDriver(
            rules=rules,
            input_fn=input_fn or input,
            output_fn=output_fn or print,
            nl_driver=nl_driver,
        )
    if lower == "replay":
        if replay_transcript is None:
            raise ValueError("Driver spec 'replay' requires 'replay_transcript'.")
        return ReplayDriver(replay_transcript, side=replay_side)

    if ":" in norm:
        prefix, model = norm.split(":", 1)
        p_low = prefix.strip().lower()
        model = model.strip()
        if not model:
            raise ValueError(f"Driver spec '{spec}' is missing a model name after ':'.")
        if p_low == "openai-compat":
            return OpenAICompatDriver(
                model,
                api_base=api_base,
                api_key_env=api_key_env,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                http_transport=http_transport,
            )
        if p_low in ("openai", "xai"):
            cls = OpenAIDriver if p_low == "openai" else XAIDriver
            return cls(
                model,
                api_base=api_base,
                api_key_env=api_key_env,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                http_transport=http_transport,
            )
        if p_low == "anthropic":
            return AnthropicDriver(
                model,
                api_base=api_base,
                api_key_env=api_key_env,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                http_transport=http_transport,
            )
        if p_low == "typesafe":
            from .jev import JevDriver

            return JevDriver(
                model,
                rules=rules,
                api_base=api_base,
                api_key_env=api_key_env,
                api_key=api_key,
                timeout_s=timeout_s,
                http_transport=http_transport,
            )
        if p_low == "gemini":
            return GeminiDriver(
                model,
                api_base=api_base,
                api_key_env=api_key_env,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                http_transport=http_transport,
            )

    raise ValueError(
        f"Unknown driver spec {spec!r}. Expected one of: 'random', 'greedy', "
        "'heuristic-tactical', 'human', 'replay', 'openai-compat:<model>', 'openai:<model>', "
        "'xai:<model>', 'anthropic:<model>', 'gemini:<model>', 'typesafe:<model>'."
    )


__all__ = [
    "AgentDecision",
    "AgentDriver",
    "AnthropicDriver",
    "BaseLLMDriver",
    "GeminiDriver",
    "GreedyDriver",
    "HeuristicTacticalDriver",
    "OpenAICompatDriver",
    "OpenAIDriver",
    "RandomDriver",
    "ReplayDriver",
    "build_system_prompt",
    "build_user_prompt",
    "create_driver",
    "extract_decision_json",
    "XAIDriver",
]
