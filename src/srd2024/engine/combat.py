"""Deterministic 5e-2024 Encounter & Combat State Machine (STAGE2-SPEC §5.2–§5.4).

Pure, non-mutating JSON state machine driven by structured intents:
`start_encounter`, `move`, `attack`, `cast_spell`, `dodge`, `dash`,
`disengage`, `help`, `end_turn`, `reroll`.

Implements:
- All 8 2024 Weapon Masteries: cleave, graze, nick, push, sap, slow, topple, vex
- All 15 2024 Conditions (incl. 2024 Exhaustion -2*lvl d20 & -5*lvl ft & death at 6)
- Strict 5e Advantage/Disadvantage cancellation
- Concentration CON save max(10, floor(dmg/2)) & replacement cleanup
- Shield (+5 AC, blocks magic-missile) & spell upcasting / cantrip scaling
- Honest `spell_cast_narrative` fallback for narrative spells
- Monster conditional damage (goblin-warrior Advantage +1d4), riders (ghoul Claw),
  AoE breath weapons (adult-red-dragon Fire Breath), and Multiattack
- 0 HP PC unconscious+prone revival & 2024 Death Saving Throws
"""

from __future__ import annotations

import copy
from fractions import Fraction
import heapq
import re
from typing import Any

from .dice import DeterministicRNG, roll_dice_expr, scale_dice_expr
from .store import RulesStore, strip_prefix

ABILITY_KEYS: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")

# Single canonical state-shape schema version (#66, #95, M6.3A).
# Re-exported by srd2024.arena.runner and srd2024.arena.
STATE_SHAPE_VERSION: int = 3

_ACTION_SURGE_MAGIC_BAN_ERROR = "Action Surge cannot be used to take the Magic action (5e 2024 SRD 5.2)."


def _fresh_turn_resources(state_shape_version: int) -> dict[str, Any]:
    res: dict[str, Any] = {
        "action": True,
        "bonus_action": True,
        "reaction": True,
        "attacks_remaining": 0,
        "cleave_used": False,
        "nick_used": False,
        "disengaged": False,
        "light_attack_made": False,
    }
    if state_shape_version >= 3:
        res["surge_action"] = False
        res["action_surge_used_this_turn"] = False
        res["sneak_attack_used"] = False
    return res


def _derive_class_resources_max(class_slug: str, level: int) -> dict[str, dict[str, Any]]:
    """Fallback deterministic class resource caps when statblock omits `class_resources_max`."""
    cls = strip_prefix(class_slug)
    lvl = max(1, int(level))
    out: dict[str, dict[str, Any]] = {}
    if cls == "fighter":
        sw_max = 4 if lvl >= 10 else (3 if lvl >= 4 else 2)
        out["second_wind"] = {"max": sw_max, "recharge": "short_rest_one"}
        if lvl >= 2:
            out["action_surge"] = {"max": 2 if lvl >= 17 else 1, "recharge": "short_rest_all"}
    elif cls == "paladin":
        out["lay_on_hands"] = {"max": 5 * lvl, "recharge": "long_rest"}
        if lvl >= 3:
            out["channel_divinity"] = {"max": 3 if lvl >= 11 else 2, "recharge": "short_rest_one"}
    elif cls == "cleric":
        if lvl >= 2:
            out["channel_divinity"] = {"max": 4 if lvl >= 18 else (3 if lvl >= 6 else 2), "recharge": "short_rest_one"}
    elif cls == "wizard":
        out["arcane_recovery"] = {
            "max": 1,
            "budget_levels": (lvl + 1) // 2,
            "max_slot_level": 5,
            "recharge": "long_rest",
        }
    return out


def _has_sneak_attack(combatant: dict[str, Any]) -> bool:
    feats = {strip_prefix(str(f)) for f in (combatant.get("features") or [])}
    if "rogue-sneak-attack" in feats:
        return True
    return combatant.get("kind") == "pc" and strip_prefix(str(combatant.get("source_id") or "")) == "rogue"


def _has_non_incapacitated_ally_within_5ft(
    state: dict[str, Any],
    attacker: dict[str, Any],
    target: dict[str, Any],
    rules: RulesStore,
) -> bool:
    attacker_id = attacker.get("id")
    attacker_side = attacker.get("side")
    for ally in state["combatants"].values():
        if (
            ally.get("id") != attacker_id
            and ally.get("side") == attacker_side
            and ally.get("alive") is True
            and int(ally.get("hp", 0)) > 0
            and not _has_modifier_flag(ally, "cannot_act", rules)
            and distance_ft(ally["position"], target["position"]) <= 5
        ):
            return True
    return False


def _consume_non_magic_action(res: dict[str, Any]) -> bool:
    """Consume regular Action first, otherwise Action Surge's `surge_action`."""
    if res.get("action"):
        res["action"] = False
        return True
    if res.get("surge_action"):
        res["surge_action"] = False
        return True
    return False

# SRD 5.2 spells that name a creature type. Hold Person: "Choose a Humanoid".
# A target is legal only when its type is known and listed. Unknown types fail closed
# (the cast is refused): a missing stat block, a blank creature_type, or a monster id
# this engine cannot resolve is not treated as a legal target.
# Player characters are Humanoid. Character creation only builds Humanoids, and a PC
# combatant does not store a monster creature_type, so kind "pc" is Humanoid rather
# than unknown. This table is the runtime check; it is not copied onto combatant state,
# so traces that never cast these spells replay unchanged.
SPELL_TARGET_CREATURE_TYPES: dict[str, frozenset[str]] = {
    "hold-person": frozenset({"humanoid"}),
}


def _required_creature_types(spell_slug: str, spell: dict[str, Any] | None) -> frozenset[str] | None:
    """Creature types `spell_slug` may target, or None when it may target any creature.

    A `target_creature_types` list on the spell record wins. Otherwise `SPELL_TARGET_CREATURE_TYPES`.
    """
    if isinstance(spell, dict):
        raw = spell.get("target_creature_types")
        if isinstance(raw, (list, tuple, set)) and raw:
            types = frozenset(str(x).strip().lower() for x in raw if str(x).strip())
            if types:
                return types
    return SPELL_TARGET_CREATURE_TYPES.get(strip_prefix(spell_slug))


def creature_type_of(combatant: dict[str, Any], rules: RulesStore) -> str | None:
    """Return this combatant's creature type in lowercase, or None when it is unknown.

    Unknown fails closed for type-restricted spells (see `SPELL_TARGET_CREATURE_TYPES`):
    the cast is refused rather than allowed. Resolution order:

    1. A non-empty `creature_type` already on the combatant.
    2. Player characters (`kind == "pc"`) are Humanoid. This engine's character builder
       only produces Humanoids, and PC combatants do not carry a monster creature type.
    3. Monsters are looked up by `source_id` in the rules store.
    4. A missing record or a blank `creature_type` is unknown (`None`).
    """
    explicit = combatant.get("creature_type")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    if str(combatant.get("kind") or "") == "pc":
        return "humanoid"
    source = str(combatant.get("source_id") or "").strip()
    if not source:
        return None
    rec = rules.get_monster(source)
    if not isinstance(rec, dict):
        return None
    raw = rec.get("creature_type")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip().lower()


def spell_target_allowed(
    spell_slug: str,
    spell: dict[str, Any] | None,
    combatant: dict[str, Any],
    rules: RulesStore,
) -> bool:
    """Whether `combatant` is a legal target of `spell_slug`.

    Spells with no creature-type restriction always return True. Restricted spells
    return True only when `creature_type_of` is known and listed. Unknown types
    return False (fail closed).
    """
    allowed = _required_creature_types(spell_slug, spell)
    if not allowed:
        return True
    kind = creature_type_of(combatant, rules)
    return kind is not None and kind in allowed


# Spells whose damage rides on a melee hit made earlier this turn (#6). "bonus_vs" adds dice against those
# creature types (Divine Smite: +1d8 against Fiends and Undead).
ON_HIT_SMITES: dict[str, dict[str, Any]] = {
    "divine-smite": {"bonus_vs": {"fiend", "undead"}, "bonus_dice": "1d8"},
    "shining-smite": {},
}


# On-hit masteries that put an effect on (or move) the creature that was hit (#27).
_TARGET_EFFECT_MASTERIES: frozenset[str] = frozenset({"push", "sap", "slow", "topple", "vex"})


def _knows_on_hit_smite(combatant: dict[str, Any]) -> bool:
    sc = combatant.get("spellcasting")
    if not isinstance(sc, dict):
        return False
    known = {strip_prefix(str(x)) for x in list(sc.get("prepared_spells") or []) + list(sc.get("cantrips") or [])}
    return bool(known & set(ON_HIT_SMITES))


# Self-range spells the compiler doesn't grade as areas but that are lines in SRD 5.2 (#81 MF1).
SELF_LINE_SPELLS: frozenset[str] = frozenset({"gust-of-wind", "sunbeam"})
# Self-range spells that SRD 5.2 lets you cast with no other creature (a held flame, a blade, a lasting detection
# or ward, Magic Jar's container): legal, and nothing to resolve yet (#81 MF1).
SELF_NO_TARGET_OK: frozenset[str] = frozenset(
    {"produce-flame", "flame-blade", "detect-thoughts", "dispel-evil-and-good", "magic-jar"}
)


SIZE_RANK: dict[str, int] = {
    "tiny": 0,
    "small": 1,
    "medium": 2,
    "large": 3,
    "huge": 4,
    "gargantuan": 5,
}

# Fallback condition modifiers (used if a condition is missing from rules.json,
# though rules.json always provides all 15/15 full conditions).
_FALLBACK_CONDITION_IMPLIES: dict[str, list[str]] = {
    "paralyzed": ["incapacitated"],
    "petrified": ["incapacitated"],
    "stunned": ["incapacitated"],
    "unconscious": ["incapacitated", "prone"],
}


def distance_ft(pos_a: dict[str, int], pos_b: dict[str, int]) -> int:
    """5e grid Chebyshev distance in feet."""
    return max(abs(int(pos_a["x"]) - int(pos_b["x"])), abs(int(pos_a["y"]) - int(pos_b["y"])))


def grid_square(pos: dict[str, Any]) -> tuple[int, int]:
    """The 5-ft square a position falls in (floor division, so negative coordinates work)."""
    return int(pos["x"]) // 5, int(pos["y"]) // 5


def _is_tiny(combatant: dict[str, Any]) -> bool:
    return str(combatant.get("size") or "Medium").strip().lower() == "tiny"


def square_occupant(state: dict[str, Any], mover_id: str, pos: dict[str, Any]) -> str | None:
    """ID of a creature whose space blocks `mover_id` from ending a move at `pos`, or None (#53).

    SRD 5.2 (Moving Around Other Creatures): you can't willingly end a move in a space
    occupied by another creature. Every living creature occupies its square, including an
    unconscious one at 0 HP; a defeated or dead creature does not. The engine models each
    creature as one 5-ft square, so a Tiny creature (2.5 ft) never blocks and is never blocked.
    """
    combatants = state.get("combatants") or {}
    mover = combatants.get(mover_id) or {}
    if mover and _is_tiny(mover):
        return None
    square = grid_square(pos)
    for cid in sorted(combatants):
        other = combatants[cid]
        if cid == mover_id or not other.get("alive"):
            continue
        if _is_tiny(other):
            continue
        if grid_square(other["position"]) == square:
            return cid
    return None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")


def _ability_mod(score: int) -> int:
    return (int(score) - 10) // 2


def _parse_int(value: Any, *, allow_str: bool = False) -> int | None:
    """Safely parse an integer, strictly rejecting booleans (and floats)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if allow_str and isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _parse_point(raw: Any) -> dict[str, int] | None:
    """Safely parse a grid coordinate dict `{x, y}` with int-convertible coords (rejecting bools)."""
    if not isinstance(raw, dict) or "x" not in raw or "y" not in raw:
        return None
    x = _parse_int(raw["x"], allow_str=True)
    y = _parse_int(raw["y"], allow_str=True)
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


# ----------------------------------------------------------------------
# Battlefield Bounds, Tactical Terrain, Pathfinding, LoS & Cover (M6.1/M6.2)
# ----------------------------------------------------------------------

COVER_BONUSES: dict[str, int] = {
    "half": 2,
    "three_quarters": 5,
}

_COVER_LABELS: dict[str, str] = {
    "half": "half cover",
    "three_quarters": "three-quarters cover",
}

# Spells that explicitly ignore Half Cover and Three-Quarters Cover (SRD 5.2 RAW).
COVER_IGNORING_SPELLS: frozenset[str] = frozenset({"sacred-flame"})

_AOE_SHAPES: frozenset[str] = frozenset(
    {"sphere", "cone", "cube", "cylinder", "line", "emanation", "radius"}
)


def _spell_aoe_info(spell_slug: str, spell: dict[str, Any]) -> dict[str, Any] | None:
    """Return AoE metadata dict if `spell` is an Area of Effect spell, else None."""
    targeting = spell.get("targeting")
    if isinstance(targeting, dict):
        aoe = targeting.get("aoe")
        if isinstance(aoe, dict):
            shape = str(aoe.get("shape") or "").strip().lower()
            if shape in _AOE_SHAPES or aoe.get("radius_ft") or aoe.get("radius"):
                return aoe
        if str(targeting.get("mode") or "").strip().lower() == "aoe":
            return aoe if isinstance(aoe, dict) else {"shape": "sphere"}
    top_aoe = spell.get("aoe")
    if isinstance(top_aoe, dict):
        shape = str(top_aoe.get("shape") or "").strip().lower()
        if shape in _AOE_SHAPES or top_aoe.get("radius_ft") or top_aoe.get("radius"):
            return top_aoe
    if spell_slug in SELF_LINE_SPELLS:
        return {"shape": "line"}
    return None


def _parse_grid_pair(raw: Any, label: str) -> tuple[int, int]:
    """Parse a 5-ft aligned `[x, y]` list/tuple or `{"x": x, "y": y}` dict."""
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        x = _parse_int(raw[0], allow_str=False)
        y = _parse_int(raw[1], allow_str=False)
    elif isinstance(raw, dict) and "x" in raw and "y" in raw:
        x = _parse_int(raw["x"], allow_str=False)
        y = _parse_int(raw["y"], allow_str=False)
    else:
        raise ValueError(f"{label} entry must be [x, y] or {{x, y}} with integer coordinates.")
    if x is None or y is None:
        raise ValueError(f"{label} coordinates must be integers (not booleans).")
    if x % 5 != 0 or y % 5 != 0:
        raise ValueError(f"{label} coordinates ({x}, {y}) must be 5-ft aligned.")
    return (x, y)


def validate_bounds(raw: Any) -> dict[str, int] | None:
    """Validate and normalize optional battlefield bounds `{min_x, min_y, max_x, max_y}`."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Battlefield 'bounds' must be an object with min_x, min_y, max_x, max_y.")
    req = ("min_x", "min_y", "max_x", "max_y")
    out: dict[str, int] = {}
    for k in req:
        if k not in raw:
            raise ValueError(f"Battlefield 'bounds' is missing required key '{k}'.")
        v = _parse_int(raw[k], allow_str=False)
        if v is None:
            raise ValueError(f"Battlefield 'bounds.{k}' must be an integer.")
        if v % 5 != 0:
            raise ValueError(f"Battlefield 'bounds.{k}' ({v}) must be 5-ft aligned.")
        out[k] = v
    if out["min_x"] > out["max_x"]:
        raise ValueError(f"Battlefield 'bounds.min_x' ({out['min_x']}) exceeds max_x ({out['max_x']}).")
    if out["min_y"] > out["max_y"]:
        raise ValueError(f"Battlefield 'bounds.min_y' ({out['min_y']}) exceeds max_y ({out['max_y']}).")
    return out


def validate_terrain(raw: Any) -> dict[str, Any] | None:
    """Validate and normalize optional tactical terrain `{walls, difficult_terrain, cover}`."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Battlefield 'terrain' must be an object.")

    walls_set: set[tuple[int, int]] = set()
    raw_walls = raw.get("walls")
    if raw_walls is not None:
        if not isinstance(raw_walls, list):
            raise ValueError("terrain.walls must be a list of [x, y] squares.")
        for item in raw_walls:
            walls_set.add(_parse_grid_pair(item, "terrain.walls"))

    diff_set: set[tuple[int, int]] = set()
    raw_diff = raw.get("difficult_terrain")
    if raw_diff is not None:
        if not isinstance(raw_diff, list):
            raise ValueError("terrain.difficult_terrain must be a list of [x, y] squares.")
        for item in raw_diff:
            diff_set.add(_parse_grid_pair(item, "terrain.difficult_terrain"))

    cover_map: dict[tuple[int, int], str] = {}
    raw_cover = raw.get("cover")
    if raw_cover is not None:
        if not isinstance(raw_cover, list):
            raise ValueError("terrain.cover must be a list of {x, y, kind} entries.")
        for item in raw_cover:
            if not isinstance(item, dict):
                raise ValueError("terrain.cover entry must be an object with x, y, kind.")
            xy = _parse_grid_pair(item, "terrain.cover")
            kind = str(item.get("kind") or "").strip().lower()
            if kind not in COVER_BONUSES:
                raise ValueError(
                    f"terrain.cover kind '{item.get('kind')}' must be 'half' or 'three_quarters'."
                )
            prev = cover_map.get(xy)
            if prev is None or COVER_BONUSES[kind] > COVER_BONUSES[prev]:
                cover_map[xy] = kind

    return {
        "walls": [[x, y] for x, y in sorted(walls_set)],
        "difficult_terrain": [[x, y] for x, y in sorted(diff_set)],
        "cover": [{"x": x, "y": y, "kind": k} for (x, y), k in sorted(cover_map.items())],
    }


def in_bounds(pos: dict[str, int], bounds: dict[str, int] | None) -> bool:
    """Return True if `pos` lies inside inclusive `bounds` (or if `bounds` is None)."""
    if not isinstance(bounds, dict):
        return True
    x = int(pos["x"])
    y = int(pos["y"])
    return (
        int(bounds["min_x"]) <= x <= int(bounds["max_x"])
        and int(bounds["min_y"]) <= y <= int(bounds["max_y"])
    )


def terrain_sets(
    terrain: dict[str, Any] | None,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]], dict[tuple[int, int], str]]:
    """Unpack normalized terrain dict into fast `(walls, difficult, cover_map)` lookups."""
    if not isinstance(terrain, dict):
        return set(), set(), {}
    walls: set[tuple[int, int]] = set()
    for w in terrain.get("walls") or []:
        if isinstance(w, (list, tuple)) and len(w) == 2:
            walls.add((int(w[0]), int(w[1])))
        elif isinstance(w, dict) and "x" in w and "y" in w:
            walls.add((int(w["x"]), int(w["y"])))
    diff: set[tuple[int, int]] = set()
    for d in terrain.get("difficult_terrain") or []:
        if isinstance(d, (list, tuple)) and len(d) == 2:
            diff.add((int(d[0]), int(d[1])))
        elif isinstance(d, dict) and "x" in d and "y" in d:
            diff.add((int(d["x"]), int(d["y"])))
    cover: dict[tuple[int, int], str] = {}
    for c in terrain.get("cover") or []:
        if isinstance(c, dict) and "x" in c and "y" in c:
            kind = str(c.get("kind") or "").strip().lower()
            if kind in COVER_BONUSES:
                xy = (int(c["x"]), int(c["y"]))
                prev = cover.get(xy)
                if prev is None or COVER_BONUSES[kind] > COVER_BONUSES[prev]:
                    cover[xy] = kind
    return walls, diff, cover


def is_wall(pos: dict[str, int], terrain: dict[str, Any] | None) -> bool:
    """Return True if `pos` is an impassable wall tile."""
    if not isinstance(terrain, dict) or not terrain.get("walls"):
        return False
    walls, _, _ = terrain_sets(terrain)
    return (int(pos["x"]), int(pos["y"])) in walls


def _ray_intermediate_cells(
    origin: dict[str, int],
    target: dict[str, int],
) -> tuple[list[tuple[int, int]], list[tuple[tuple[int, int], tuple[int, int]]]]:
    """Exact rational grid-raycast from cell center `origin` to cell center `target`.

    Returns `(interior_cells, corner_pairs)` in feet coordinates `(x, y)`:
    - `interior_cells`: 5-ft cells (excluding origin and target) whose interior is crossed
      by the center-to-center line segment.
    - `corner_pairs`: pairs of diagonally flanking 5-ft cells meeting at a grid vertex that
      the ray passes directly through (so two adjacent diagonal walls block the gap).
    """
    ax = int(origin["x"]) // 5
    ay = int(origin["y"]) // 5
    bx = int(target["x"]) // 5
    by = int(target["y"]) // 5
    if (ax, ay) == (bx, by):
        return [], []

    # Scale grid by 2 so cell (cx, cy) spans [2*cx, 2*cx+2] x [2*cy, 2*cy+2]
    # and cell centers are exact integers (2*cx+1, 2*cy+1).
    p0x = 2 * ax + 1
    p0y = 2 * ay + 1
    dx = 2 * (bx - ax)
    dy = 2 * (by - ay)

    x_lo, x_hi = (ax, bx) if ax <= bx else (bx, ax)
    y_lo, y_hi = (ay, by) if ay <= by else (by, ay)

    interior: list[tuple[int, int]] = []
    corner_hits: dict[Fraction, list[tuple[int, int]]] = {}

    for cx in range(x_lo, x_hi + 1):
        for cy in range(y_lo, y_hi + 1):
            if (cx, cy) == (ax, ay) or (cx, cy) == (bx, by):
                continue

            if dx == 0:
                if cx != ax:
                    continue
                t_xmin, t_xmax = Fraction(0), Fraction(1)
            else:
                tx1 = Fraction(2 * cx - p0x, dx)
                tx2 = Fraction(2 * cx + 2 - p0x, dx)
                t_xmin, t_xmax = (tx1, tx2) if tx1 <= tx2 else (tx2, tx1)

            if dy == 0:
                if cy != ay:
                    continue
                t_ymin, t_ymax = Fraction(0), Fraction(1)
            else:
                ty1 = Fraction(2 * cy - p0y, dy)
                ty2 = Fraction(2 * cy + 2 - p0y, dy)
                t_ymin, t_ymax = (ty1, ty2) if ty1 <= ty2 else (ty2, ty1)

            t_enter = max(Fraction(0), t_xmin, t_ymin)
            t_exit = min(Fraction(1), t_xmax, t_ymax)
            if t_enter < t_exit:
                interior.append((cx * 5, cy * 5))
            elif t_enter == t_exit and Fraction(0) < t_enter < Fraction(1):
                corner_hits.setdefault(t_enter, []).append((cx * 5, cy * 5))

    corner_pairs: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for t_key in sorted(corner_hits.keys()):
        cells = corner_hits[t_key]
        if len(cells) >= 2:
            for i in range(len(cells)):
                for j in range(i + 1, len(cells)):
                    corner_pairs.append((cells[i], cells[j]))

    return interior, corner_pairs


def has_line_of_sight(
    origin: dict[str, int],
    target: dict[str, int],
    terrain: dict[str, Any] | None,
) -> bool:
    """Return True if no wall blocks Line of Sight between `origin` and `target`."""
    if not isinstance(terrain, dict) or not terrain.get("walls"):
        return True
    walls, _, _ = terrain_sets(terrain)
    if not walls:
        return True
    interior, corner_pairs = _ray_intermediate_cells(origin, target)
    for cell in interior:
        if cell in walls:
            return False
    for c1, c2 in corner_pairs:
        if c1 in walls and c2 in walls:
            return False
    return True


def compute_cover(
    origin: dict[str, int],
    target: dict[str, int],
    terrain: dict[str, Any] | None,
) -> tuple[str | None, int]:
    """Return `(cover_kind, bonus)` between `origin` and `target`.

    Returns `("three_quarters", 5)`, `("half", 2)`, or `(None, 0)`, taking the
    highest cover bonus among all cover squares crossed by the center-to-center ray.
    """
    if not isinstance(terrain, dict) or not terrain.get("cover"):
        return None, 0
    _, _, cover_map = terrain_sets(terrain)
    if not cover_map:
        return None, 0

    interior, corner_pairs = _ray_intermediate_cells(origin, target)
    best_kind: str | None = None
    best_bonus = 0

    for cell in interior:
        kind = cover_map.get(cell)
        if kind and COVER_BONUSES[kind] > best_bonus:
            best_kind = kind
            best_bonus = COVER_BONUSES[kind]

    for c1, c2 in corner_pairs:
        k1 = cover_map.get(c1)
        k2 = cover_map.get(c2)
        if k1 and k2:
            # Passing directly between two cover tiles grants the lower of the two (or both if equal).
            b = min(COVER_BONUSES[k1], COVER_BONUSES[k2])
            if b > best_bonus:
                best_bonus = b
                best_kind = k1 if COVER_BONUSES[k1] == b else k2

    return best_kind, best_bonus


def movement_path_cost(
    start: dict[str, int],
    dest: dict[str, int],
    bounds: dict[str, int] | None,
    terrain: dict[str, Any] | None,
    *,
    is_prone: bool = False,
    max_cost: int | None = None,
) -> tuple[int | None, str | None]:
    """Compute exact shortest 5-ft grid movement cost respecting bounds, walls, and difficult terrain.

    Returns `(cost_ft, error_reason)`:
    - `(cost, None)` if a valid path exists (caller still compares `cost` against remaining movement).
    - `(None, error_msg)` if destination is out of bounds, on a wall, or completely blocked by walls.
    """
    sx, sy = int(start["x"]), int(start["y"])
    dx, dy = int(dest["x"]), int(dest["y"])

    if not in_bounds(dest, bounds):
        return (
            None,
            f"Destination ({dx}, {dy}) is outside battlefield bounds "
            f"[x: {bounds['min_x']}..{bounds['max_x']}, y: {bounds['min_y']}..{bounds['max_y']}].",
        )

    walls, difficult, _ = terrain_sets(terrain)
    if (dx, dy) in walls:
        return None, f"Destination ({dx}, {dy}) is blocked by a wall."

    if (sx, sy) == (dx, dy):
        return 0, None

    # Fast path when no walls and no difficult terrain: rectangular bounds are convex.
    if not walls and not difficult:
        dist = distance_ft(start, dest)
        cost = dist * 2 if is_prone else dist
        return cost, None

    if sx % 5 != 0 or sy % 5 != 0 or dx % 5 != 0 or dy % 5 != 0:
        return None, f"Destination ({dx}, {dy}) must be aligned to the 5-ft grid."

    # Determine deterministic search box (bounded by battlefield bounds when set).
    if isinstance(bounds, dict):
        box_min_x, box_max_x = int(bounds["min_x"]), int(bounds["max_x"])
        box_min_y, box_max_y = int(bounds["min_y"]), int(bounds["max_y"])
    else:
        pad = 60
        box_min_x = min(sx, dx) - pad
        box_max_x = max(sx, dx) + pad
        box_min_y = min(sy, dy) - pad
        box_max_y = max(sy, dy) + pad

    search_cap = max(int(max_cost) if max_cost is not None else 0, 240)
    dist_map: dict[tuple[int, int], int] = {(sx, sy): 0}
    heap: list[tuple[int, int, int]] = [(0, sx, sy)]

    while heap:
        cur_cost, cx, cy = heapq.heappop(heap)
        if (cx, cy) == (dx, dy):
            return cur_cost, None
        if cur_cost > dist_map.get((cx, cy), search_cap + 1):
            continue
        if cur_cost >= search_cap:
            continue

        for step_x in (-5, 0, 5):
            for step_y in (-5, 0, 5):
                if step_x == 0 and step_y == 0:
                    continue
                nx, ny = cx + step_x, cy + step_y
                if nx < box_min_x or nx > box_max_x or ny < box_min_y or ny > box_max_y:
                    continue
                if (nx, ny) in walls:
                    continue
                # Disallow cutting diagonally across a wall corner or squeezing through diagonal walls.
                if step_x != 0 and step_y != 0:
                    if (cx + step_x, cy) in walls or (cx, cy + step_y) in walls:
                        continue
                step_mult = 1 + (1 if is_prone else 0) + (1 if (nx, ny) in difficult else 0)
                nxt_cost = cur_cost + 5 * step_mult
                if nxt_cost > search_cap:
                    continue
                if nxt_cost < dist_map.get((nx, ny), search_cap + 1):
                    dist_map[(nx, ny)] = nxt_cost
                    heapq.heappush(heap, (nxt_cost, nx, ny))

    return None, f"Path to ({dx}, {dy}) is blocked by a wall."


def reachable_grid_costs(
    start: dict[str, int],
    max_move_ft: int,
    bounds: dict[str, int] | None,
    terrain: dict[str, Any] | None,
    *,
    is_prone: bool = False,
) -> dict[tuple[int, int], int]:
    """Return `{ (x, y): min_cost_ft }` for all 5-ft grid squares reachable within `max_move_ft`."""
    sx, sy = int(start["x"]), int(start["y"])
    budget = max(0, int(max_move_ft))
    if budget < 5:
        return {(sx, sy): 0}

    walls, difficult, _ = terrain_sets(terrain)
    if isinstance(bounds, dict):
        box_min_x, box_max_x = int(bounds["min_x"]), int(bounds["max_x"])
        box_min_y, box_max_y = int(bounds["min_y"]), int(bounds["max_y"])
    else:
        span = (budget // 5) * 5
        box_min_x, box_max_x = sx - span, sx + span
        box_min_y, box_max_y = sy - span, sy + span

    dist_map: dict[tuple[int, int], int] = {(sx, sy): 0}
    heap: list[tuple[int, int, int]] = [(0, sx, sy)]

    while heap:
        cur_cost, cx, cy = heapq.heappop(heap)
        if cur_cost > dist_map.get((cx, cy), budget + 1):
            continue
        for step_x in (-5, 0, 5):
            for step_y in (-5, 0, 5):
                if step_x == 0 and step_y == 0:
                    continue
                nx, ny = cx + step_x, cy + step_y
                if nx < box_min_x or nx > box_max_x or ny < box_min_y or ny > box_max_y:
                    continue
                if (nx, ny) in walls:
                    continue
                if step_x != 0 and step_y != 0:
                    if (cx + step_x, cy) in walls or (cx, cy + step_y) in walls:
                        continue
                step_mult = 1 + (1 if is_prone else 0) + (1 if (nx, ny) in difficult else 0)
                nxt_cost = cur_cost + 5 * step_mult
                if nxt_cost > budget:
                    continue
                if nxt_cost < dist_map.get((nx, ny), budget + 1):
                    dist_map[(nx, ny)] = nxt_cost
                    heapq.heappush(heap, (nxt_cost, nx, ny))

    return dist_map


# ----------------------------------------------------------------------
# Condition & Active-Effect Helpers
# ----------------------------------------------------------------------


def _get_condition_ir(slug: str, rules: RulesStore) -> dict[str, Any]:
    rec = rules.get_condition(slug)
    if rec is not None:
        return rec
    return {
        "slug": slug,
        "implies": _FALLBACK_CONDITION_IMPLIES.get(slug, []),
        "modifiers": {},
    }


def _direct_condition_slugs(combatant: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in combatant.get("conditions", []):
        if isinstance(entry, dict):
            sl = strip_prefix(entry.get("condition", ""))
        else:
            sl = strip_prefix(str(entry))
        if sl and sl not in out:
            out.append(sl)
    if int(combatant.get("exhaustion_level", 0)) > 0 and "exhaustion" not in out:
        out.append("exhaustion")
    return out


def _effective_conditions(combatant: dict[str, Any], rules: RulesStore) -> set[str]:
    """Return all direct AND implied condition slugs on a combatant."""
    result: set[str] = set()
    queue = list(_direct_condition_slugs(combatant))
    while queue:
        sl = queue.pop()
        if sl in result:
            continue
        result.add(sl)
        ir = _get_condition_ir(sl, rules)
        for imp in ir.get("implies") or _FALLBACK_CONDITION_IMPLIES.get(sl, []):
            imp_sl = strip_prefix(imp)
            if imp_sl not in result:
                queue.append(imp_sl)
    return result


def _condition_entries_for(combatant: dict[str, Any], cond_slug: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in combatant.get("conditions", []):
        if isinstance(entry, dict) and strip_prefix(entry.get("condition", "")) == cond_slug:
            entries.append(entry)
        elif isinstance(entry, str) and strip_prefix(entry) == cond_slug:
            entries.append({"condition": cond_slug, "source_id": None, "duration": "indefinite"})
    return entries


def _is_condition_immune(combatant: dict[str, Any], cond_slug: str, rules: RulesStore) -> bool:
    base_imm = {strip_prefix(c) for c in (combatant.get("condition_immunities") or [])}
    if cond_slug in base_imm:
        return True
    for active in _effective_conditions(combatant, rules):
        mods = _get_condition_ir(active, rules).get("modifiers") or {}
        if cond_slug in {strip_prefix(x) for x in (mods.get("immune_conditions") or [])}:
            return True
    return False


def _has_modifier_flag(combatant: dict[str, Any], flag: str, rules: RulesStore) -> bool:
    for sl in _effective_conditions(combatant, rules):
        mods = _get_condition_ir(sl, rules).get("modifiers") or {}
        if bool(mods.get(flag)):
            return True
    return False


def _auto_fail_saves(combatant: dict[str, Any], rules: RulesStore) -> set[str]:
    out: set[str] = set()
    for sl in _effective_conditions(combatant, rules):
        mods = _get_condition_ir(sl, rules).get("modifiers") or {}
        for ab in mods.get("auto_fail_saves") or []:
            out.add(strip_prefix(ab))
    return out


def _save_disadvantage_abilities(combatant: dict[str, Any], rules: RulesStore) -> set[str]:
    out: set[str] = set()
    for sl in _effective_conditions(combatant, rules):
        mods = _get_condition_ir(sl, rules).get("modifiers") or {}
        for ab in mods.get("save_disadvantage") or []:
            out.add(strip_prefix(ab))
    return out


def _exhaustion_level(combatant: dict[str, Any]) -> int:
    return max(0, int(combatant.get("exhaustion_level", 0)))


def _effective_speed(combatant: dict[str, Any], rules: RulesStore) -> int:
    if _has_modifier_flag(combatant, "speed_zero", rules):
        return 0
    base = int(combatant.get("speed_ft", 30))
    for eff in combatant.get("active_effects", []):
        if eff.get("kind") == "longstrider":
            base += int(eff.get("value", 10))
    # 2024 Exhaustion: -5 ft per level.
    base -= 5 * _exhaustion_level(combatant)
    # Slow mastery / Ray of Frost: non-stacking max penalty.
    slow_penalties = [
        int(eff.get("value", 10))
        for eff in combatant.get("active_effects", [])
        if eff.get("kind") == "slow"
    ]
    if slow_penalties:
        base -= max(slow_penalties)
    base = max(0, base)
    # Haste doubles speed.
    for eff in combatant.get("active_effects", []):
        if eff.get("kind") == "haste":
            val = eff.get("value")
            mult = int(val.get("speed_multiplier", 2)) if isinstance(val, dict) else 2
            base *= mult
    return max(0, base)


def _effective_ac(combatant: dict[str, Any]) -> int:
    ac = int(combatant.get("ac", 10))
    dex_mod = int(combatant.get("ability_mods", {}).get("dex", 0))
    for eff in combatant.get("active_effects", []):
        if eff.get("kind") == "mage_armor":
            formula_ac = int(eff.get("value", 13)) + dex_mod
            if not combatant.get("wearing_armor", False):
                ac = max(ac, formula_ac)
    for eff in combatant.get("active_effects", []):
        kind = eff.get("kind")
        if kind == "shield":
            ac += int(eff.get("value", 5))
        elif kind == "shield_of_faith":
            ac += int(eff.get("value", 2))
        elif kind == "haste":
            val = eff.get("value")
            ac += int(val.get("ac_bonus", 2)) if isinstance(val, dict) else 2
    for eff in combatant.get("active_effects", []):
        if eff.get("kind") == "barkskin":
            ac = max(ac, int(eff.get("value", 17)))
    return ac


def _roll_bless_bane(
    combatant: dict[str, Any],
    roll_kind: str,
    rng: DeterministicRNG,
) -> tuple[int, list[dict[str, Any]]]:
    # Heroic Inspiration (#85) rerolls one die, the d20. The reroll re-resolves the attack with the original
    # roll's Bless/Bane dice pinned, one entry per attack roll in the action.
    pinned = combatant.get("_pinned_bonus_dice")
    if roll_kind == "attack" and isinstance(pinned, list) and pinned:
        kept = [dict(d) for d in (pinned.pop(0) or [])]
        return sum(int(d.get("delta", 0)) for d in kept), kept
    total_bonus = 0
    details: list[dict[str, Any]] = []
    for eff in combatant.get("active_effects", []):
        kind = eff.get("kind")
        if kind not in ("bless", "bane"):
            continue
        applies_to = eff.get("applies_to") or ["attack", "save"]
        if roll_kind not in applies_to:
            continue
        dice_str = str(eff.get("value") or "1d4")
        sign = 1 if kind == "bless" else -1
        res = rng.roll_dice_expr(dice_str, crit=False)
        delta = sign * int(res["total"])
        total_bonus += delta
        details.append({"kind": kind, "dice": dice_str, "rolls": res["rolls"], "delta": delta})
    return total_bonus, details


def _action_bonus_dice(events: list[dict[str, Any]], actor_id: str, roll_type: str) -> list[list[dict[str, Any]]]:
    """The Bless/Bane dice of each of `actor_id`'s own attack rolls in this action, in order (#85)."""
    return [
        copy.deepcopy(e.get("bonus_dice") or [])
        for e in events
        if e.get("type") == roll_type and e.get("actor_id") == actor_id and not e.get("reaction")
    ]


def _next_effect_id(state: dict[str, Any], prefix: str) -> str:
    counter = int(state.get("_effect_seq", 0)) + 1
    state["_effect_seq"] = counter
    return f"{prefix}-{counter}"


# ----------------------------------------------------------------------
# Event & Diff Recording Helper
# ----------------------------------------------------------------------


class _StepTracker:
    def __init__(self, initial_state: dict[str, Any] | None) -> None:
        self.events: list[dict[str, Any]] = []
        self.hooks: list[str] = []
        self.slot_changes: list[dict[str, Any]] = []
        self.before_snapshot: dict[str, Any] = copy.deepcopy(initial_state) if initial_state else {}
        # Set while the engine resolves an automatic reaction: events emitted meanwhile carry "reaction": True so
        # scoring can tell engine-made reactions from a driver's own decisions.
        self.in_reaction: bool = False

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        ev = {"seq": len(self.events) + 1, "type": event_type, **fields}
        if self.in_reaction:
            ev["reaction"] = True
        self.events.append(ev)
        return ev

    def hook(self, text: str) -> None:
        self.hooks.append(text)

    def build_diff(self, after_state: dict[str, Any]) -> dict[str, Any]:
        before_c = self.before_snapshot.get("combatants", {}) if self.before_snapshot else {}
        after_c = after_state.get("combatants", {})

        hp_changes: list[dict[str, Any]] = []
        condition_changes: list[dict[str, Any]] = []
        position_changes: list[dict[str, Any]] = []
        effect_changes: list[dict[str, Any]] = []
        resource_changes: list[dict[str, Any]] = []
        ssv3 = int(after_state.get("state_shape_version", 1)) >= 3

        for cid in sorted(after_c.keys()):
            a = after_c[cid]
            b = before_c.get(cid)
            if b is None:
                continue
            if int(b["hp"]) != int(a["hp"]):
                hp_changes.append(
                    {
                        "id": cid,
                        "before": int(b["hp"]),
                        "after": int(a["hp"]),
                        "delta": int(a["hp"]) - int(b["hp"]),
                    }
                )
            b_conds = sorted(_direct_condition_slugs(b))
            a_conds = sorted(_direct_condition_slugs(a))
            added_c = sorted(set(a_conds) - set(b_conds))
            removed_c = sorted(set(b_conds) - set(a_conds))
            if added_c or removed_c:
                condition_changes.append({"id": cid, "added": added_c, "removed": removed_c})
            if b.get("position") != a.get("position"):
                position_changes.append(
                    {
                        "id": cid,
                        "before": copy.deepcopy(b.get("position")),
                        "after": copy.deepcopy(a.get("position")),
                    }
                )
            b_effs = [e.get("id") or e.get("kind") for e in b.get("active_effects", [])]
            a_effs = [e.get("id") or e.get("kind") for e in a.get("active_effects", [])]
            added_e = [e.get("kind") for e in a.get("active_effects", []) if (e.get("id") or e.get("kind")) not in b_effs]
            removed_e = [e.get("kind") for e in b.get("active_effects", []) if (e.get("id") or e.get("kind")) not in a_effs]
            if added_e or removed_e:
                effect_changes.append({"id": cid, "added": added_e, "removed": removed_e})
            if ssv3:
                b_cr = b.get("class_resources") or {}
                a_cr = a.get("class_resources") or {}
                for rname in sorted(set(b_cr.keys()) | set(a_cr.keys())):
                    b_rem = int((b_cr.get(rname) or {}).get("remaining", 0))
                    a_rem = int((a_cr.get(rname) or {}).get("remaining", 0))
                    if b_rem != a_rem:
                        resource_changes.append({"id": cid, "resource": rname, "before": b_rem, "after": a_rem})

        turn_changed = bool(
            self.before_snapshot
            and (
                self.before_snapshot.get("active_index") != after_state.get("active_index")
                or self.before_snapshot.get("round") != after_state.get("round")
            )
        )

        diff: dict[str, Any] = {
            "hp_changes": hp_changes,
            "condition_changes": condition_changes,
            "position_changes": position_changes,
            "slot_changes": self.slot_changes,
            "effect_changes": effect_changes,
            "turn_changed": turn_changed,
        }
        if ssv3:
            diff["resource_changes"] = resource_changes
        return diff


# ----------------------------------------------------------------------
# Condition / Concentration / Damage / Save Primitives
# ----------------------------------------------------------------------


def _drop_concentration(
    state: dict[str, Any],
    caster_id: str,
    tracker: _StepTracker,
    reason: str = "broken",
) -> None:
    caster = state["combatants"].get(caster_id)
    if not caster or not caster.get("concentration"):
        return
    conc = caster["concentration"]
    spell_slug = conc.get("spell")
    caster["concentration"] = None

    # Clean up all active_effects and conditions tied to this concentration spell.
    for cid, comb in state["combatants"].items():
        comb["active_effects"] = [
            e
            for e in comb.get("active_effects", [])
            if not (e.get("source_id") == caster_id and e.get("concentration_spell") == spell_slug)
        ]
        comb["conditions"] = [
            c
            for c in comb.get("conditions", [])
            if not (
                isinstance(c, dict)
                and c.get("source_id") == caster_id
                and c.get("concentration_spell") == spell_slug
            )
        ]

    tracker.emit("concentration_ended", actor_id=caster_id, spell=spell_slug, reason=reason)
    tracker.hook(f"{caster['name']} loses concentration on {spell_slug} ({reason}).")


def _death_save_tally(actor: dict[str, Any], what: str) -> str:
    ds = actor["death_saves"]
    return f"{actor['name']} {what}: {ds['successes']}/3 successes, {ds['failures']}/3 failures."


def _save_hook(name: str, ability: str, dc: int, source: str, success: bool, applied: list[str]) -> str:
    """Narration for a rider save, e.g. "Lyra fails a CON save (DC 10) against Claw and is Paralyzed." (#61)."""
    verb = "succeeds on" if success else "fails"
    text = f"{name} {verb} a {ability.upper()} save (DC {dc}) against {source}"
    if applied:
        text += f" and is {' and '.join(applied)}"
    return text + "."


def _apply_condition(
    state: dict[str, Any],
    target_id: str,
    cond_slug: str,
    rules: RulesStore,
    tracker: _StepTracker,
    *,
    source_id: str | None = None,
    duration: str = "indefinite",
    save_ability: str | None = None,
    save_dc: int | None = None,
    concentration_spell: str | None = None,
) -> bool:
    target = state["combatants"][target_id]
    cond_slug = strip_prefix(cond_slug)
    if _is_condition_immune(target, cond_slug, rules):
        tracker.emit("condition_immune", target_id=target_id, condition=cond_slug)
        return False

    existing = _condition_entries_for(target, cond_slug)
    if not existing:
        entry: dict[str, Any] = {
            "condition": cond_slug,
            "source_id": source_id,
            "duration": duration,
            "save_ability": save_ability,
            "save_dc": save_dc,
        }
        if concentration_spell:
            entry["concentration_spell"] = concentration_spell
        target["conditions"].append(entry)
        tracker.emit("condition_applied", target_id=target_id, condition=cond_slug, source_id=source_id)

    # Check if this condition breaks concentration (Incapacitated and all conditions implying it).
    cond_ir = _get_condition_ir(cond_slug, rules)
    mods = cond_ir.get("modifiers") or {}
    implied = set(cond_ir.get("implies") or _FALLBACK_CONDITION_IMPLIES.get(cond_slug, []))
    if mods.get("breaks_concentration") or "incapacitated" in implied or cond_slug == "incapacitated":
        if target.get("concentration"):
            _drop_concentration(state, target_id, tracker, reason=cond_slug)

    # If speed becomes zero, clamp remaining movement.
    if mods.get("speed_zero"):
        target["movement_remaining_ft"] = 0

    return True


def _remove_condition(combatant: dict[str, Any], cond_slug: str) -> bool:
    cond_slug = strip_prefix(cond_slug)
    before = len(combatant.get("conditions", []))
    combatant["conditions"] = [
        c
        for c in combatant.get("conditions", [])
        if (strip_prefix(c.get("condition", "")) if isinstance(c, dict) else strip_prefix(str(c))) != cond_slug
    ]
    return len(combatant["conditions"]) < before


def _check_exhaustion_death(
    state: dict[str, Any],
    combatant_id: str,
    tracker: _StepTracker,
) -> bool:
    comb = state["combatants"][combatant_id]
    if comb.get("alive") and _exhaustion_level(comb) >= 6:
        comb["hp"] = 0
        comb["alive"] = False
        if comb.get("concentration"):
            _drop_concentration(state, combatant_id, tracker, reason="exhaustion_6")
        tracker.emit("combatant_died", combatant_id=combatant_id, reason="exhaustion_level_6")
        tracker.hook(f"{comb['name']} dies from Exhaustion level 6.")
        return True
    return False


def _roll_save(
    state: dict[str, Any],
    target_id: str,
    ability: str,
    dc: int,
    rules: RulesStore,
    rng: DeterministicRNG,
    tracker: _StepTracker,
    *,
    reason: str = "save",
    source_pos: dict[str, int] | None = None,
    ignore_cover: bool = False,
) -> dict[str, Any]:
    target = state["combatants"][target_id]
    ab = strip_prefix(ability)
    auto_fails = _auto_fail_saves(target, rules)

    if ab in auto_fails:
        ev = tracker.emit(
            "saving_throw",
            target_id=target_id,
            ability=ab,
            dc=int(dc),
            auto_fail=True,
            mode="auto_fail",
            rolls=[],
            chosen=None,
            modifier=int(target["saves"].get(ab, 0)),
            total=None,
            success=False,
            reason=reason,
        )
        _maybe_legendary_resistance(target_id, target, ev, tracker)
        return ev

    adv_sources: list[str] = []
    disadv_sources: list[str] = []

    if ab in _save_disadvantage_abilities(target, rules):
        disadv_sources.append("condition_save_disadvantage")

    # Magic Resistance (#39): Advantage on saves against spells (the save's reason is the spell's slug).
    if _trait(target, "magic_resistance") and rules.get_spell(str(reason)) is not None:
        adv_sources.append("magic_resistance")

    if ab == "dex":
        has_dodge = any(e.get("kind") == "dodge" for e in target.get("active_effects", []))
        if has_dodge and not _has_modifier_flag(target, "cannot_act", rules) and _effective_speed(target, rules) > 0:
            adv_sources.append("dodge")

    if adv_sources and disadv_sources:
        mode = "normal"
    elif adv_sources:
        mode = "advantage"
    elif disadv_sources:
        mode = "disadvantage"
    else:
        mode = "normal"

    cover_kind: str | None = None
    cover_bonus = 0
    cover_fields: dict[str, Any] = {}
    if ab == "dex" and source_pos is not None:
        raw_cover_kind, raw_cover_bonus = compute_cover(source_pos, target["position"], state.get("terrain"))
        if raw_cover_bonus > 0 and raw_cover_kind:
            if ignore_cover:
                cover_kind = "none"
                cover_bonus = 0
                cover_fields["cover"] = "none"
                cover_fields["cover_save_bonus"] = 0
                cover_fields["cover_ignored"] = True
            else:
                cover_kind = raw_cover_kind
                cover_bonus = raw_cover_bonus
                cover_fields["cover"] = cover_kind
                cover_fields["cover_save_bonus"] = cover_bonus

    d20 = rng.roll_d20(mode)
    base_mod = int(target["saves"].get(ab, 0))
    exh_pen = -2 * _exhaustion_level(target)
    bb_bonus, bb_details = _roll_bless_bane(target, "save", rng)
    total = int(d20["chosen"]) + base_mod + exh_pen + bb_bonus + cover_bonus
    success = total >= int(dc)

    ev = tracker.emit(
        "saving_throw",
        target_id=target_id,
        ability=ab,
        dc=int(dc),
        auto_fail=False,
        mode=mode,
        rolls=d20["rolls"],
        chosen=d20["chosen"],
        modifier=base_mod,
        exhaustion_penalty=exh_pen,
        bonus_dice=bb_details,
        total=total,
        success=success,
        reason=reason,
        **cover_fields,
    )
    _maybe_legendary_resistance(target_id, target, ev, tracker)
    return ev


def _maybe_legendary_resistance(
    target_id: str, target: dict[str, Any], save_ev: dict[str, Any], tracker: _StepTracker
) -> None:
    """Legendary Resistance (#39): a failed save becomes a success while uses remain (auto policy: always use)."""
    uses = int(target.get("legendary_resistance_uses", 0))
    if save_ev.get("success") or uses <= 0 or str(target.get("reaction_policy") or "auto") == "none":
        return
    target["legendary_resistance_uses"] = uses - 1
    save_ev["success"] = True
    save_ev["legendary_resistance"] = True
    tracker.emit("legendary_resistance_used", target_id=target_id, reason=save_ev.get("reason"), uses_left=uses - 1)
    tracker.hook(f"{target['name']} uses Legendary Resistance to succeed instead ({uses - 1} left).")


def _apply_damage_parts(
    state: dict[str, Any],
    target_id: str,
    parts: list[dict[str, Any]],
    rules: RulesStore,
    rng: DeterministicRNG,
    tracker: _StepTracker,
    *,
    source_id: str | None = None,
    is_crit: bool = False,
) -> int:
    target = state["combatants"][target_id]
    immunities = {strip_prefix(x) for x in (target.get("damage_immunities") or [])}
    resistances = {strip_prefix(x) for x in (target.get("damage_resistances") or [])}
    vulnerabilities = {strip_prefix(x) for x in (target.get("damage_vulnerabilities") or [])}
    resist_all = _has_modifier_flag(target, "resist_all_damage", rules)

    breakdown: list[dict[str, Any]] = []
    total_effective = 0

    for part in parts:
        raw_amt = max(0, int(part["amount"]))
        dtype = strip_prefix(part.get("type") or "bludgeoning")
        modifier_applied = "normal"
        if dtype in immunities:
            eff = 0
            modifier_applied = "immune"
        else:
            is_res = resist_all or (dtype in resistances)
            is_vuln = dtype in vulnerabilities
            if is_res and not is_vuln:
                eff = raw_amt // 2
                modifier_applied = "resistant"
            elif is_vuln and not is_res:
                eff = raw_amt * 2
                modifier_applied = "vulnerable"
            else:
                eff = raw_amt
        total_effective += eff
        breakdown.append(
            {"type": dtype, "raw": raw_amt, "effective": eff, "modifier": modifier_applied}
        )

    hp_before = int(target["hp"])
    temp_before = int(target.get("temp_hp", 0))
    absorbed = min(temp_before, total_effective)
    target["temp_hp"] = temp_before - absorbed
    hp_damage = total_effective - absorbed
    target["hp"] = max(0, hp_before - hp_damage)

    # Undead Fortitude (#39): if this damage would drop it to 0 (not Radiant, not a crit), CON save DC 5 + damage.
    fortitude = False
    if (
        hp_before > 0
        and target["hp"] == 0
        and target.get("kind") == "monster"
        and _trait(target, "undead_fortitude")
        and not is_crit
        and not any(b["type"] == "radiant" and b["effective"] > 0 for b in breakdown)
    ):
        fort_ev = _roll_save(state, target_id, "con", 5 + total_effective, rules, rng, tracker, reason="Undead Fortitude")
        if fort_ev["success"]:
            target["hp"] = 1
            hp_damage = hp_before - 1
            fortitude = True

    dmg_fields: dict[str, Any] = {}
    if fortitude:
        dmg_fields["undead_fortitude"] = True
    tracker.emit(
        "damage_applied",
        source_id=source_id,
        target_id=target_id,
        breakdown=breakdown,
        total_damage=total_effective,
        temp_hp_absorbed=absorbed,
        hp_damage=hp_damage,
        hp_before=hp_before,
        hp_after=target["hp"],
        **dmg_fields,
    )
    if fortitude:
        tracker.hook(f"{target['name']} refuses to fall (Undead Fortitude) and drops to 1 HP instead.")

    # Instant Death (SRD 5.2): a PC dies outright if the damage left over after dropping it to 0 HP, or any damage
    # taken while at 0 HP, equals or exceeds its Hit Point maximum (#46). Temporary HP absorbed first doesn't count.
    hp_max = int(target.get("hp_max") or 0)
    if target.get("kind") == "pc" and target.get("alive") and hp_max > 0 and target["hp"] == 0 and hp_damage > 0 \
            and hp_damage - hp_before >= hp_max:
        drop_idx = len(tracker.hooks)
        if target.get("concentration"):
            _drop_concentration(state, target_id, tracker, reason="dropped_to_0_hp")
        target["alive"] = False
        tracker.emit("combatant_died", combatant_id=target_id, reason="massive_damage",
                     overflow=hp_damage - hp_before, hp_max=hp_max)
        tracker.hooks.insert(
            drop_idx,
            f"{target['name']} takes {hp_damage - hp_before} damage past 0 HP (max {hp_max}) and dies instantly.",
        )
        return total_effective

    # Damage at 0 HP on a PC causes death save failure(s).
    if hp_before == 0 and total_effective > 0 and target.get("kind") == "pc" and target.get("alive"):
        fails = 2 if is_crit else 1
        target["death_saves"]["failures"] = min(3, int(target["death_saves"]["failures"]) + fails)
        tracker.emit(
            "death_save_damage_failure",
            target_id=target_id,
            failures_added=fails,
            failures_total=target["death_saves"]["failures"],
        )
        if target["death_saves"]["failures"] >= 3:
            target["alive"] = False
            tracker.emit("combatant_died", combatant_id=target_id, reason="death_save_failures")

    # Dropping to 0 HP.
    if hp_before > 0 and target["hp"] == 0:
        drop_idx = len(tracker.hooks)  # the 0-HP line goes before the concentration loss it causes (#56)
        if target.get("concentration"):
            _drop_concentration(state, target_id, tracker, reason="dropped_to_0_hp")
        if target.get("kind") == "monster":
            target["alive"] = False
            tracker.emit("combatant_defeated", combatant_id=target_id)
            tracker.hooks.insert(drop_idx, f"{target['name']} drops to 0 HP and is defeated.")
            burst = _trait(target, "death_burst")
            if burst:
                _resolve_death_burst(state, target_id, burst, rules, rng, tracker)
        else:
            _apply_condition(state, target_id, "unconscious", rules, tracker, source_id=source_id)
            _apply_condition(state, target_id, "prone", rules, tracker, source_id=source_id)
            tracker.emit("pc_unconscious", combatant_id=target_id)
            tracker.hooks.insert(drop_idx, f"{target['name']} drops to 0 HP and falls Unconscious and Prone.")
    elif total_effective > 0 and target.get("concentration"):
        # Concentration CON saving throw: DC = max(10, floor(dmg / 2)).
        conc_dc = max(10, total_effective // 2)
        save_ev = _roll_save(
            state,
            target_id,
            "con",
            conc_dc,
            rules,
            rng,
            tracker,
            reason="concentration",
        )
        if not save_ev["success"]:
            _drop_concentration(state, target_id, tracker, reason="failed_concentration_save")

    return total_effective


# ----------------------------------------------------------------------
# Combatant Initialization (`start_encounter`)
# ----------------------------------------------------------------------


def _build_pc_combatant(entry: dict[str, Any], *, state_shape_version: int = STATE_SHAPE_VERSION) -> dict[str, Any]:
    sb = entry["statblock"]
    cid = str(entry["id"])
    side = str(entry.get("side") or "party")
    pos = entry.get("position") or {"x": 0, "y": 0}
    hp_max = int(sb["hp_max"])
    hp = int(entry.get("hp", hp_max))
    speed = int(sb.get("speed_ft", 30))
    level = int(sb.get("level", 1))
    spellcasting = copy.deepcopy(sb.get("spellcasting"))
    slots_max: dict[str, int] = {}
    if isinstance(spellcasting, dict) and isinstance(spellcasting.get("slots"), dict):
        spellcasting["slots"] = {str(k): int(v) for k, v in spellcasting["slots"].items()}
        raw_slots_max = entry.get("slots_max")
        if isinstance(raw_slots_max, dict):
            slots_max = {str(k): max(0, int(v)) for k, v in raw_slots_max.items()}
        else:
            slots_max = dict(spellcasting["slots"])
        # Optional carryover spell slot override (used by M6.3B between dungeon rooms).
        raw_slots_override = entry.get("slots")
        if isinstance(raw_slots_override, dict):
            merged_slots: dict[str, int] = {}
            for k in sorted(set(slots_max.keys()) | {str(x) for x in raw_slots_override.keys()}, key=lambda s: int(s) if s.isdigit() else 99):
                if k in raw_slots_override:
                    val = max(0, int(raw_slots_override[k]))
                    if k in slots_max:
                        val = min(slots_max[k], val)
                    merged_slots[k] = val
                elif k in spellcasting["slots"]:
                    merged_slots[k] = int(spellcasting["slots"][k])
            spellcasting["slots"] = merged_slots

    ac_breakdown = str(sb.get("ac_breakdown") or "")
    wearing_armor = bool(ac_breakdown) and not ac_breakdown.startswith("Unarmored") and "Unarmored Defense" not in ac_breakdown
    traits = {strip_prefix(t) for t in (sb.get("traits") or [])}
    heroic_inspiration = bool(entry.get("heroic_inspiration", "resourceful" in traits))

    raw_ds = entry.get("death_saves")
    if isinstance(raw_ds, dict):
        death_saves = {
            "successes": max(0, min(3, int(raw_ds.get("successes", 0)))),
            "failures": max(0, min(3, int(raw_ds.get("failures", 0)))),
        }
    else:
        death_saves = {"successes": 0, "failures": 0}

    alive = bool(entry["alive"]) if "alive" in entry else (hp > 0)

    comb: dict[str, Any] = {
        "id": cid,
        "name": str(entry.get("name") or sb.get("name") or cid),
        "side": side,
        "kind": "pc",
        "source_id": str(sb.get("class") or "classes:fighter"),
        "level": level,
        "size": str(sb.get("size") or "Medium"),
        "position": {"x": int(pos["x"]), "y": int(pos["y"])},
        "hp": hp,
        "hp_max": hp_max,
        "temp_hp": int(entry.get("temp_hp", 0)),
        "ac": int(sb["ac"]),
        "wearing_armor": wearing_armor,
        "speed_ft": speed,
        "movement_remaining_ft": speed,
        "initiative_bonus": int(sb.get("initiative", sb["ability_mods"]["dex"])),
        "abilities": copy.deepcopy(sb["abilities"]),
        "ability_mods": copy.deepcopy(sb["ability_mods"]),
        "proficiency_bonus": int(sb.get("proficiency_bonus", 2)),
        "saves": copy.deepcopy(sb["saving_throws"]),
        "damage_resistances": list(sb.get("damage_resistances", [])),
        "damage_immunities": list(sb.get("damage_immunities", [])),
        "damage_vulnerabilities": list(sb.get("damage_vulnerabilities", [])),
        "condition_immunities": list(sb.get("condition_immunities", [])),
        "conditions": [],
        "exhaustion_level": int(entry.get("exhaustion_level", 0)),
        "concentration": None,
        "active_effects": [],
        "heroic_inspiration": heroic_inspiration,
        "extra_attacks": int(sb.get("extra_attacks", 1)),
        "feats": list(sb.get("feats", [])),
        "turn_resources": _fresh_turn_resources(state_shape_version),
        "death_saves": death_saves,
        "alive": alive,
        "weapons": copy.deepcopy(sb.get("weapon_profiles", [])),
        "monster_actions": [],
        "spellcasting": spellcasting,
    }

    if state_shape_version >= 3:
        hit_die = int(sb.get("hit_die", 8))
        raw_hd = entry.get("hit_dice")
        if isinstance(raw_hd, dict):
            hd_max = max(1, int(raw_hd.get("max", level)))
            hd_rem = max(0, min(hd_max, int(raw_hd.get("remaining", hd_max))))
            hd_die = int(raw_hd.get("die", hit_die))
        elif isinstance(raw_hd, int) and not isinstance(raw_hd, bool):
            hd_max = level
            hd_rem = max(0, min(hd_max, int(raw_hd)))
            hd_die = hit_die
        else:
            hd_max = level
            hd_rem = level
            hd_die = hit_die

        raw_crm = sb.get("class_resources_max")
        if not isinstance(raw_crm, dict):
            raw_crm = _derive_class_resources_max(str(sb.get("class") or ""), level)
        class_resources: dict[str, dict[str, Any]] = {}
        for rname, rcfg in sorted(raw_crm.items()):
            if isinstance(rcfg, dict):
                rmax = max(0, int(rcfg.get("max", 0)))
                item = copy.deepcopy(rcfg)
                item["max"] = rmax
                item["remaining"] = rmax
                class_resources[str(rname)] = item

        raw_cr_override = entry.get("class_resources")
        if isinstance(raw_cr_override, dict):
            for rname, rval in raw_cr_override.items():
                key = str(rname)
                if key in class_resources:
                    rmax = int(class_resources[key]["max"])
                    rem_raw = rval.get("remaining", rmax) if isinstance(rval, dict) else rval
                    class_resources[key]["remaining"] = max(0, min(rmax, int(rem_raw)))
                elif isinstance(rval, dict) and "max" in rval:
                    rmax = max(0, int(rval["max"]))
                    rem = max(0, min(rmax, int(rval.get("remaining", rmax))))
                    item = copy.deepcopy(rval)
                    item["max"] = rmax
                    item["remaining"] = rem
                    class_resources[key] = item

        features: list[str] = []
        for f in sb.get("features") or []:
            f_slug = strip_prefix(str(f))
            if f_slug and f_slug not in features:
                features.append(f_slug)

        comb["subclass"] = sb.get("subclass")
        comb["features"] = features
        comb["hit_die"] = hit_die
        comb["hit_dice"] = {"die": hd_die, "max": hd_max, "remaining": hd_rem}
        comb["slots_max"] = slots_max
        comb["class_resources"] = class_resources

    return comb


def _build_monster_combatant(
    entry: dict[str, Any],
    rules: RulesStore,
    *,
    state_shape_version: int = STATE_SHAPE_VERSION,
) -> dict[str, Any]:
    m_slug = strip_prefix(entry["monster"])
    m_rec = rules.get_monster(m_slug)
    if m_rec is None:
        raise ValueError(f"Unknown monster '{entry['monster']}'")

    cid = str(entry["id"])
    side = str(entry.get("side") or "enemy")
    pos = entry.get("position") or {"x": 0, "y": 0}

    abilities_raw = m_rec.get("abilities") or m_rec.get("ability_scores") or {}
    abilities = {ab: int(abilities_raw.get(ab, 10)) for ab in ABILITY_KEYS}
    mods = {ab: _ability_mod(abilities[ab]) for ab in ABILITY_KEYS}

    saves_raw = m_rec.get("saves") or m_rec.get("saving_throws") or {}
    saves = {ab: int(saves_raw.get(ab, mods[ab])) for ab in ABILITY_KEYS}

    hp_max = int(
        entry.get("hp_max")
        or m_rec.get("hp_max")
        or m_rec.get("hp")
        or m_rec.get("hit_points")
        or 10
    )
    hp = int(entry.get("hp", hp_max))

    speed_obj = m_rec.get("speed")
    if isinstance(speed_obj, dict):
        speed_ft = int(speed_obj.get("walk_ft") or speed_obj.get("walk") or 30)
    elif isinstance(speed_obj, int):
        speed_ft = speed_obj
    else:
        speed_ft = int(m_rec.get("speed_ft") or 30)

    ac_val = m_rec.get("ac")
    if ac_val is None:
        ac_raw = m_rec.get("armor_class")
        if isinstance(ac_raw, list) and ac_raw:
            ac_val = int(ac_raw[0].get("value", 10))
        elif isinstance(ac_raw, int):
            ac_val = ac_raw
        else:
            ac_val = 10

    init_bonus = m_rec.get("initiative_bonus")
    if init_bonus is None:
        init_bonus = mods["dex"]

    m_traits = {
        strip_prefix(t.get("name") if isinstance(t, dict) else str(t))
        for t in (m_rec.get("traits") or [])
    }
    heroic_inspiration = bool(entry.get("heroic_inspiration", "resourceful" in m_traits))

    return {
        "id": cid,
        "name": str(entry.get("name") or m_rec.get("name") or cid),
        "side": side,
        "kind": "monster",
        "source_id": str(m_rec.get("id") or f"monsters:{m_slug}"),
        "level": max(1, int(float(m_rec.get("cr", m_rec.get("challenge_rating", 1)) or 1))),
        "size": str(m_rec.get("size") or "Medium"),
        "position": {"x": int(pos["x"]), "y": int(pos["y"])},
        "hp": hp,
        "hp_max": hp_max,
        "temp_hp": int(entry.get("temp_hp", 0)),
        "ac": int(ac_val),
        "wearing_armor": False,
        "speed_ft": speed_ft,
        "movement_remaining_ft": speed_ft,
        "initiative_bonus": int(init_bonus),
        "abilities": abilities,
        "ability_mods": mods,
        "proficiency_bonus": int(m_rec.get("proficiency_bonus", 2)),
        "saves": saves,
        "damage_resistances": [strip_prefix(x) for x in (m_rec.get("damage_resistances") or [])],
        "damage_immunities": [strip_prefix(x) for x in (m_rec.get("damage_immunities") or [])],
        "damage_vulnerabilities": [strip_prefix(x) for x in (m_rec.get("damage_vulnerabilities") or [])],
        "condition_immunities": [strip_prefix(x) for x in (m_rec.get("condition_immunities") or [])],
        "conditions": [],
        "exhaustion_level": int(entry.get("exhaustion_level", 0)),
        "concentration": None,
        "active_effects": [],
        "heroic_inspiration": heroic_inspiration,
        "extra_attacks": 1,
        "feats": [],
        "turn_resources": _fresh_turn_resources(state_shape_version),
        "death_saves": {"successes": 0, "failures": 0},
        "alive": hp > 0,
        "weapons": [],
        "monster_actions": copy.deepcopy(m_rec.get("actions", [])),
        "spellcasting": copy.deepcopy(m_rec.get("spellcasting")),
        # Executable stat-block traits and reactions (#39); see rules_compiler/monsters.py::_parse_trait_ir.
        # Added only when present, so trait-less combatants (and traces recorded before #39) keep the same state.
        **_trait_state_fields(m_rec),
    }


def _trait_state_fields(m_rec: dict[str, Any]) -> dict[str, Any]:
    traits = m_rec.get("traits_ir") or []
    if not traits:
        return {}
    fields: dict[str, Any] = {"traits_ir": copy.deepcopy(traits)}
    lr = sum(int(t.get("uses", 0)) for t in traits if t.get("kind") == "legendary_resistance")
    if lr:
        fields["legendary_resistance_uses"] = lr
    return fields


def _trait(combatant: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """The combatant's executable trait/reaction of this kind (#39), if any."""
    return next((t for t in combatant.get("traits_ir") or [] if t.get("kind") == kind), None)


def _resolve_death_burst(
    state: dict[str, Any],
    source_id: str,
    burst: dict[str, Any],
    rules: RulesStore,
    rng: DeterministicRNG,
    tracker: _StepTracker,
) -> None:
    """Death Burst (#39): one damage roll; each creature in the emanation saves for half (SRD 5.2 area rules)."""
    src = state["combatants"][source_id]
    radius = int(burst.get("radius_ft", 5))
    victims = [
        cid for cid in state.get("turn_order") or []
        if cid != source_id
        and state["combatants"][cid].get("alive")
        and distance_ft(state["combatants"][cid]["position"], src["position"]) <= radius
    ]
    roll = rng.roll_dice_expr(str(burst["dice"]))
    full = int(roll["total"])
    tracker.emit("death_burst", source_id=source_id, radius_ft=radius, dice=burst["dice"], damage=full,
                 damage_type=burst["damage_type"], targets=victims)
    tracker.hook(f"{src['name']} explodes in a {radius}-ft burst!")
    for cid in victims:
        sv = _roll_save(state, cid, str(burst["ability"]), int(burst["dc"]), rules, rng, tracker, reason="Death Burst")
        amount = full // 2 if sv["success"] else full
        if amount > 0:
            _apply_damage_parts(state, cid, [{"type": burst["damage_type"], "amount": amount}], rules, rng, tracker,
                                source_id=source_id)


def _start_of_turn_traits(
    state: dict[str, Any],
    actor_id: str,
    rules: RulesStore,
    rng: DeterministicRNG,
    tracker: _StepTracker,
) -> None:
    """Start-of-turn traits for `actor_id` (#39): its own Regeneration, and every aura it starts its turn in (Stench)."""
    actor = state["combatants"][actor_id]
    # Conditions that last "until the start of its next turn" end now, before new auras apply.
    kept = []
    for c in actor.get("conditions", []):
        if isinstance(c, dict) and c.get("duration") == "start_of_target_next_turn":
            tracker.emit("condition_ended", target_id=actor_id, condition=strip_prefix(c.get("condition", "")),
                         reason="duration_expired")
            continue
        kept.append(c)
    actor["conditions"] = kept

    regen = _trait(actor, "regeneration")
    if regen and actor.get("alive") and int(actor.get("hp", 0)) >= 1:
        before = int(actor["hp"])
        actor["hp"] = min(int(actor["hp_max"]), before + int(regen["hp"]))
        if actor["hp"] > before:
            tracker.emit("healing_applied", source_id=actor_id, target_id=actor_id, spell=None, trait="regeneration",
                         amount=actor["hp"] - before, hp_before=before, hp_after=actor["hp"], revived=False)
            tracker.hook(f"{actor['name']} regenerates {actor['hp'] - before} HP.")

    if not actor.get("alive") or int(actor.get("hp", 0)) <= 0:
        return
    for sid in state.get("turn_order") or []:
        if sid == actor_id:
            continue
        src = state["combatants"][sid]
        aura = _trait(src, "start_of_turn_aura_save")
        if not aura or not src.get("alive") or int(src.get("hp", 0)) <= 0:
            continue
        if distance_ft(src["position"], actor["position"]) > int(aura["radius_ft"]) or sid in (actor.get("aura_immune_to") or []):
            continue
        name = next((t.get("name") for t in src.get("traits_ir") or [] if t is aura), None) or "Aura"
        sv = _roll_save(state, actor_id, str(aura["ability"]), int(aura["dc"]), rules, rng, tracker, reason=str(name))
        if sv["success"]:
            if aura.get("immune_on_success"):
                actor.setdefault("aura_immune_to", []).append(sid)
        else:
            _apply_condition(state, actor_id, str(aura["condition"]), rules, tracker, source_id=sid,
                             duration="start_of_target_next_turn")


# ----------------------------------------------------------------------
# Attack Resolution Pipeline
# ----------------------------------------------------------------------


def _gather_attack_roll_mode(
    state: dict[str, Any],
    attacker: dict[str, Any],
    target: dict[str, Any],
    rules: RulesStore,
    *,
    is_ranged: bool,
    dist: int,
    range_normal_ft: int | None,
    advantage_override: bool,
    disadvantage_override: bool,
) -> tuple[str, list[str], list[str], list[tuple[str, str]]]:
    """Compute attack roll mode with strict 5e Adv/Disadv cancellation.

    Returns `(mode, adv_sources, disadv_sources, effects_to_consume)` where
    `effects_to_consume` is a list of `(owner_combatant_id, effect_id)`.
    """
    adv: list[str] = []
    disadv: list[str] = []
    consume: list[tuple[str, str]] = []

    atk_conds = _effective_conditions(attacker, rules)
    tgt_conds = _effective_conditions(target, rules)

    # Pack Tactics (#39): an able ally of the attacker within 5 ft of the target.
    if _trait(attacker, "pack_tactics"):
        for ally in state["combatants"].values():
            if (
                ally.get("id") != attacker.get("id")
                and ally.get("side") == attacker.get("side")
                and ally.get("alive")
                and int(ally.get("hp", 0)) > 0
                and not _has_modifier_flag(ally, "cannot_act", rules)
                and distance_ft(ally["position"], target["position"]) <= 5
            ):
                adv.append("pack_tactics")
                break

    # Attacker conditions.
    if "invisible" in atk_conds:
        adv.append("attacker_invisible")
    for c in ("blinded", "poisoned", "prone", "restrained"):
        if c in atk_conds:
            disadv.append(f"attacker_{c}")
    if "frightened" in atk_conds:
        fright_entries = _condition_entries_for(attacker, "frightened")
        active_fear = False
        for fe in fright_entries:
            src_id = fe.get("source_id")
            if not src_id or (src_id in state["combatants"] and state["combatants"][src_id].get("alive")):
                active_fear = True
        if active_fear or not fright_entries:
            disadv.append("attacker_frightened")
    if "grappled" in atk_conds:
        grap_entries = _condition_entries_for(attacker, "grappled")
        for ge in grap_entries:
            src_id = ge.get("source_id")
            if src_id and src_id != target["id"]:
                disadv.append("attacker_grappled_non_grappler")
                break

    # Target conditions.
    for c in ("blinded", "paralyzed", "petrified", "restrained", "stunned", "unconscious"):
        if c in tgt_conds:
            adv.append(f"target_{c}")
    if "invisible" in tgt_conds:
        disadv.append("target_invisible")
    if "prone" in tgt_conds:
        if dist <= 5:
            adv.append("target_prone_within_5ft")
        else:
            disadv.append("target_prone_beyond_5ft")

    # Active effects on target.
    for eff in target.get("active_effects", []):
        kind = eff.get("kind")
        if kind == "dodge":
            if not _has_modifier_flag(target, "cannot_act", rules) and _effective_speed(target, rules) > 0:
                disadv.append("target_dodge")
        elif kind == "help":
            val = eff.get("value") or {}
            ally_id = val.get("ally_id") if isinstance(val, dict) else None
            if ally_id is None or ally_id == attacker["id"]:
                adv.append("help")
                consume.append((target["id"], str(eff.get("id"))))
        elif kind in ("guiding_bolt_advantage", "next_attack_advantage"):
            adv.append(kind)
            consume.append((target["id"], str(eff.get("id"))))

    # Active effects on attacker.
    for eff in attacker.get("active_effects", []):
        kind = eff.get("kind")
        if kind == "sap":
            disadv.append("sap")
            consume.append((attacker["id"], str(eff.get("id"))))
        elif kind == "vex" and eff.get("target_id") == target["id"]:
            adv.append("vex")
            consume.append((attacker["id"], str(eff.get("id"))))

    # Ranged weapon/spell range & hostile-in-melee checks.
    if is_ranged:
        if range_normal_ft is not None and dist > range_normal_ft:
            disadv.append("long_range")
        for cid, other in state["combatants"].items():
            if cid == attacker["id"] or other.get("side") == attacker.get("side"):
                continue
            if not other.get("alive") or _has_modifier_flag(other, "cannot_act", rules):
                continue
            if distance_ft(attacker["position"], other["position"]) <= 5:
                disadv.append("ranged_hostile_within_5ft")
                break

    if advantage_override:
        adv.append("override_advantage")
    if disadvantage_override:
        disadv.append("override_disadvantage")

    # Strict 5e cancellation.
    if adv and disadv:
        mode = "normal"
    elif adv:
        mode = "advantage"
    elif disadv:
        mode = "disadvantage"
    else:
        mode = "normal"

    return mode, adv, disadv, consume


def _consume_effects(state: dict[str, Any], consume_list: list[tuple[str, str]]) -> None:
    if not consume_list:
        return
    to_remove = set(consume_list)
    for owner_id, eff_id in to_remove:
        comb = state["combatants"].get(owner_id)
        if not comb:
            continue
        comb["active_effects"] = [
            e for e in comb.get("active_effects", []) if str(e.get("id")) != eff_id
        ]


def _resolve_single_weapon_attack(
    state: dict[str, Any],
    attacker_id: str,
    target_id: str,
    weapon: dict[str, Any],
    rules: RulesStore,
    rng: DeterministicRNG,
    tracker: _StepTracker,
    *,
    two_handed: bool = False,
    omit_positive_mod: bool = False,
    use_mastery: bool = True,
    allow_cleave: bool = True,
    cleave_target_id: str | None = None,
    advantage_override: bool = False,
    disadvantage_override: bool = False,
    use_sneak_attack: bool = True,
) -> dict[str, Any]:
    attacker = state["combatants"][attacker_id]
    target = state["combatants"][target_id]
    dist = distance_ft(attacker["position"], target["position"])
    w_range = str(weapon.get("weapon_range") or "melee").lower()
    props = set(weapon.get("properties") or [])
    throw_range = weapon.get("throw_range")

    # Determine whether this attack is melee or ranged (thrown melee weapon beyond reach).
    reach_ft = int(weapon.get("reach_ft") or 5)
    is_ranged = w_range == "ranged"
    range_normal = weapon.get("range_normal_ft")
    if not is_ranged and dist > reach_ft and isinstance(throw_range, dict):
        is_ranged = True
        range_normal = throw_range.get("normal_ft")

    mode, adv_src, disadv_src, consume = _gather_attack_roll_mode(
        state,
        attacker,
        target,
        rules,
        is_ranged=is_ranged,
        dist=dist,
        range_normal_ft=int(range_normal) if range_normal is not None else None,
        advantage_override=advantage_override,
        disadvantage_override=disadvantage_override,
    )
    _consume_effects(state, consume)

    d20 = rng.roll_d20(mode)
    atk_bonus = int(weapon.get("attack_bonus", 0))
    exh_pen = -2 * _exhaustion_level(attacker)
    bb_bonus, bb_details = _roll_bless_bane(attacker, "attack", rng)
    total_to_hit = int(d20["chosen"]) + atk_bonus + exh_pen + bb_bonus
    cover_kind, cover_bonus = compute_cover(attacker["position"], target["position"], state.get("terrain"))
    target_ac = _effective_ac(target) + cover_bonus

    if d20["nat1"]:
        hit = False
        crit = False
    elif d20["nat20"]:
        hit = True
        crit = True
    else:
        hit = total_to_hit >= target_ac
        crit = False
    if hit and not is_ranged:  # Parry (Reaction, #39) against a melee hit
        parried_ac = _maybe_reactive_parry(state, target_id, attacker_id, d20, total_to_hit, target_ac, rules, tracker)
        if parried_ac is not None:
            target_ac = parried_ac
            hit = total_to_hit >= target_ac
    if hit:  # Shield (Reaction) can turn the hit into a miss (#33)
        shielded_ac = _maybe_reactive_shield(state, target_id, attacker_id, d20, total_to_hit, target_ac, rules, tracker)
        if shielded_ac is not None:
            target_ac = shielded_ac
            hit = total_to_hit >= target_ac

    # Paralyzed / Unconscious auto-crit within 5ft.
    if hit and not crit and dist <= 5 and _has_modifier_flag(target, "auto_crit_within_5ft", rules):
        crit = True

    cover_fields: dict[str, Any] = {}
    if cover_bonus > 0 and cover_kind:
        cover_fields["cover"] = cover_kind
        cover_fields["cover_ac_bonus"] = cover_bonus

    tracker.emit(
        "attack_roll",
        actor_id=attacker_id,
        target_id=target_id,
        weapon=weapon["slug"],
        mode=mode,
        advantage_sources=adv_src,
        disadvantage_sources=disadv_src,
        rolls=d20["rolls"],
        chosen=d20["chosen"],
        nat20=d20["nat20"],
        nat1=d20["nat1"],
        attack_bonus=atk_bonus,
        exhaustion_penalty=exh_pen,
        bonus_dice=bb_details,
        total=total_to_hit,
        target_ac=target_ac,
        hit=hit,
        crit=crit,
        **cover_fields,
    )

    mastery = weapon.get("mastery") if (use_mastery and weapon.get("mastery_active")) else None
    atk_ab = str(weapon.get("attack_ability") or "str")
    atk_ab_mod = int(attacker["ability_mods"].get(atk_ab, 0))
    dmg_type = str(weapon.get("damage_type") or "bludgeoning")
    cover_txt = f" [{_COVER_LABELS[cover_kind]} +{cover_bonus} AC]" if (cover_bonus > 0 and cover_kind) else ""

    if not hit:
        tracker.hook(
            f"{attacker['name']} misses {target['name']} with {weapon['name']} ({total_to_hit} vs AC {target_ac}{cover_txt})."
        )
        # Graze mastery on miss!
        if mastery == "graze":
            graze_dmg = max(0, atk_ab_mod)
            tracker.emit(
                "mastery_triggered",
                mastery="graze",
                actor_id=attacker_id,
                target_id=target_id,
                damage=graze_dmg,
                damage_type=dmg_type,
            )
            graze_idx = len(tracker.hooks)
            eff_dmg = _apply_damage_parts(
                state,
                target_id,
                [{"amount": graze_dmg, "type": dmg_type}],
                rules,
                rng,
                tracker,
                source_id=attacker_id,
            )
            tracker.hooks.insert(graze_idx, f"Graze mastery still deals {eff_dmg} {dmg_type} damage to {target['name']}.")
        return {
            "hit": False,
            "crit": False,
            "damage": 0,
            "chosen": int(d20["chosen"]),
            "total": int(total_to_hit),
            "target_ac": int(target_ac),
        }

    # Hit -> Roll damage.
    dice_str = (
        str(weapon["versatile_dice"])
        if (two_handed and weapon.get("versatile_dice"))
        else str(weapon.get("damage_dice") or "1")
    )
    dmg_roll = rng.roll_dice_expr(dice_str, crit=crit)
    raw_dmg_bonus = int(weapon.get("damage_bonus", atk_ab_mod))
    if omit_positive_mod and raw_dmg_bonus > 0:
        raw_dmg_bonus = 0
    raw_dmg = max(0, int(dmg_roll["total"]) + raw_dmg_bonus)

    # Rogue Sneak Attack (M6.3A, 5e 2024 SRD 5.2): once per turn (incl. off-turn Opportunity Attacks),
    # Finesse or Ranged weapon, Advantage or non-incapacitated ally within 5 ft (without Disadvantage).
    # Summed into raw_dmg before resistance math so resistance halves the combined total once.
    sa_hook_text: str | None = None
    if (
        int(state.get("state_shape_version", 1)) >= 3
        and use_sneak_attack
        and _has_sneak_attack(attacker)
        and not attacker["turn_resources"].get("sneak_attack_used", False)
        and ("finesse" in props or w_range == "ranged")
    ):
        sa_trigger: str | None = None
        sa_trigger_label: str = ""
        if mode == "advantage":
            sa_trigger = "advantage"
            sa_trigger_label = "Advantage"
        elif mode == "normal" and _has_non_incapacitated_ally_within_5ft(state, attacker, target, rules):
            sa_trigger = "ally_within_5ft"
            sa_trigger_label = "ally within 5 ft"
        if sa_trigger is not None:
            dice_count = (max(1, int(attacker.get("level", 1))) + 1) // 2
            sa_dice_expr = f"{dice_count}d6"
            sa_roll = rng.roll_dice_expr(sa_dice_expr, crit=crit)
            sa_dmg = max(0, int(sa_roll["total"]))
            attacker["turn_resources"]["sneak_attack_used"] = True
            tracker.emit(
                "sneak_attack_triggered",
                actor_id=attacker_id,
                target_id=target_id,
                weapon=weapon["slug"],
                dice=sa_dice_expr,
                rolls=sa_roll["rolls"],
                damage=sa_dmg,
                damage_type=dmg_type,
                crit=crit,
                trigger=sa_trigger,
            )
            raw_dmg += sa_dmg
            sa_hook_text = f"Sneak Attack ({sa_dice_expr}, {sa_trigger_label}) adds {sa_dmg} {dmg_type} damage!"

    hit_hook_idx = len(tracker.hooks)  # the hit line goes before any 0-HP line the damage causes (#62)
    eff_dmg = _apply_damage_parts(
        state,
        target_id,
        [{"amount": raw_dmg, "type": dmg_type}],
        rules,
        rng,
        tracker,
        source_id=attacker_id,
        is_crit=crit,
    )
    crit_txt = " (CRITICAL HIT!)" if crit else ""
    tracker.hooks.insert(
        hit_hook_idx,
        f"{attacker['name']} hits {target['name']} with {weapon['name']}{crit_txt} "
        f"({total_to_hit} vs AC {target_ac}{cover_txt}) for {eff_dmg} {dmg_type} damage.",
    )
    if sa_hook_text is not None:
        tracker.hooks.insert(hit_hook_idx + 1, sa_hook_text)
    # A smite (#6) can follow a melee hit on the attacker's own turn. Recorded only for casters who know one,
    # so nobody else's state changes. Cleared when turn_resources reset at the start of the next turn.
    if not is_ranged and _knows_on_hit_smite(attacker):
        order = state.get("turn_order") or []
        active = order[int(state.get("active_index", 0)) % len(order)] if order else None
        if active == attacker_id:
            attacker["turn_resources"]["last_melee_hit"] = {"target_id": target_id, "crit": bool(crit)}

    # Trigger on-hit 2024 Weapon Masteries. Push, Sap, Slow, Topple and Vex do nothing to a creature this hit
    # just dropped (or that was already at 0 HP): there is no effect to put on it (#27). Cleave is about a
    # second creature, so it still applies; Graze only works on a miss.
    target_down = not target.get("alive") or int(target.get("hp", 0)) <= 0
    if target_down and mastery in _TARGET_EFFECT_MASTERIES:
        tracker.emit(
            "mastery_skipped",
            mastery=mastery,
            actor_id=attacker_id,
            target_id=target_id,
            reason="target_down",
        )
    elif mastery == "push":
        t_size = str(target.get("size") or "Medium").strip().lower()
        if SIZE_RANK.get(t_size, 2) <= SIZE_RANK["large"]:
            dx = int(target["position"]["x"]) - int(attacker["position"]["x"])
            dy = int(target["position"]["y"]) - int(attacker["position"]["y"])
            sx = 1 if dx > 0 else (-1 if dx < 0 else 0)
            sy = 1 if dy > 0 else (-1 if dy < 0 else 0)
            if sx == 0 and sy == 0:
                sx = 1
            old_pos = copy.deepcopy(target["position"])
            # Push moves the target "up to 10 feet": stop cleanly at bounds/walls AND
            # never end in another living non-Tiny creature's space (#53, M6.1/M6.2).
            cur_x, cur_y = int(old_pos["x"]), int(old_pos["y"])
            bounds = state.get("bounds")
            terrain = state.get("terrain")
            walls, _, _ = terrain_sets(terrain)
            stopped_by: str | None = None
            reachable_steps: list[dict[str, int]] = []
            for _ in range(2):
                nx, ny = cur_x + sx * 5, cur_y + sy * 5
                cand = {"x": nx, "y": ny}
                if not in_bounds(cand, bounds):
                    stopped_by = "bounds"
                    break
                if (nx, ny) in walls:
                    stopped_by = "wall"
                    break
                if sx != 0 and sy != 0 and ((cur_x + sx * 5, cur_y) in walls or (cur_x, cur_y + sy * 5) in walls):
                    stopped_by = "wall"
                    break
                reachable_steps.append(cand)
                cur_x, cur_y = nx, ny

            new_pos = copy.deepcopy(old_pos)
            blocked_by: str | None = None
            for cand in reversed(reachable_steps):
                occupant = square_occupant(state, target_id, cand)
                if occupant is None:
                    new_pos = cand
                    break
                if blocked_by is None:
                    blocked_by = occupant

            actual_dist = distance_ft(old_pos, new_pos)
            target["position"] = new_pos
            push_event: dict[str, Any] = {
                "mastery": "push",
                "actor_id": attacker_id,
                "target_id": target_id,
                "from_pos": old_pos,
                "to_pos": copy.deepcopy(target["position"]),
                "distance_ft": actual_dist,
            }
            if stopped_by is not None:
                push_event["stopped_by"] = stopped_by
            if blocked_by is not None and actual_dist < 10:
                push_event["blocked_by"] = blocked_by
            tracker.emit("mastery_triggered", **push_event)
            if actual_dist == 10:
                tracker.hook(
                    f"Push mastery shoves {target['name']} 10 ft away to ({target['position']['x']}, {target['position']['y']})."
                )
            elif blocked_by is not None and stopped_by is None:
                if actual_dist > 0:
                    tracker.hook(
                        f"Push mastery shoves {target['name']} only {actual_dist} ft, to "
                        f"({target['position']['x']}, {target['position']['y']}); the next square is occupied."
                    )
                else:
                    tracker.hook(
                        f"Push mastery can't move {target['name']}: the squares behind it are occupied."
                    )
            elif actual_dist > 0:
                reason = f"{stopped_by} and an occupied square" if blocked_by is not None else str(stopped_by)
                tracker.hook(
                    f"Push mastery shoves {target['name']} {actual_dist} ft to ({target['position']['x']}, {target['position']['y']}) "
                    f"(stopped by {reason})."
                )
            else:
                reason = f"{stopped_by} and an occupied square" if blocked_by is not None else str(stopped_by)
                tracker.hook(
                    f"Push mastery tries to shove {target['name']}, but {target['name']} is stopped by {reason} at "
                    f"({target['position']['x']}, {target['position']['y']})."
                )
        else:
            tracker.emit(
                "mastery_skipped",
                mastery="push",
                actor_id=attacker_id,
                target_id=target_id,
                reason="target_too_large",
                target_size=target.get("size"),
            )

    elif mastery == "sap":
        eff_id = _next_effect_id(state, "sap")
        target["active_effects"].append(
            {
                "id": eff_id,
                "kind": "sap",
                "source_id": attacker_id,
                "target_id": target_id,
                "value": True,
                "expires": "start_of_source_next_turn",
                "created_round": state["round"],
                "concentration_spell": None,
            }
        )
        tracker.emit("mastery_triggered", mastery="sap", actor_id=attacker_id, target_id=target_id)
        tracker.hook(f"Sap mastery gives {target['name']} Disadvantage on its next attack roll.")

    elif mastery == "slow":
        if eff_dmg > 0:
            eff_id = _next_effect_id(state, "slow")
            target["active_effects"].append(
                {
                    "id": eff_id,
                    "kind": "slow",
                    "source_id": attacker_id,
                    "target_id": target_id,
                    "value": 10,
                    "expires": "start_of_source_next_turn",
                    "created_round": state["round"],
                    "concentration_spell": None,
                }
            )
            target["movement_remaining_ft"] = min(
                int(target["movement_remaining_ft"]),
                _effective_speed(target, rules),
            )
            tracker.emit(
                "mastery_triggered",
                mastery="slow",
                actor_id=attacker_id,
                target_id=target_id,
                speed_penalty_ft=10,
            )
            tracker.hook(f"Slow mastery reduces {target['name']}'s speed by 10 ft until start of {attacker['name']}'s next turn.")

    elif mastery == "topple":
        dc = 8 + atk_ab_mod + int(attacker.get("proficiency_bonus", 2))
        save_ev = _roll_save(state, target_id, "con", dc, rules, rng, tracker, reason="topple_mastery")
        applied = False
        if not save_ev["success"]:
            applied = _apply_condition(state, target_id, "prone", rules, tracker, source_id=attacker_id)
        tracker.emit(
            "mastery_triggered",
            mastery="topple",
            actor_id=attacker_id,
            target_id=target_id,
            dc=dc,
            save_success=save_ev["success"],
            applied_condition="prone" if applied else None,
        )
        if applied:
            tracker.hook(f"Topple mastery knocks {target['name']} Prone (failed CON DC {dc}).")

    elif mastery == "vex":
        if eff_dmg > 0:
            eff_id = _next_effect_id(state, "vex")
            attacker["active_effects"].append(
                {
                    "id": eff_id,
                    "kind": "vex",
                    "source_id": attacker_id,
                    "target_id": target_id,
                    "value": True,
                    "expires": "end_of_source_next_turn",
                    "created_round": state["round"],
                    "concentration_spell": None,
                }
            )
            tracker.emit("mastery_triggered", mastery="vex", actor_id=attacker_id, target_id=target_id)
            tracker.hook(f"Vex mastery grants {attacker['name']} Advantage on the next attack against {target['name']}.")

    elif mastery == "cleave" and allow_cleave and cleave_target_id:
        if is_ranged:
            tracker.emit("mastery_skipped", mastery="cleave", actor_id=attacker_id, reason="requires_melee")
        elif attacker["turn_resources"].get("cleave_used"):
            tracker.emit("mastery_skipped", mastery="cleave", actor_id=attacker_id, reason="once_per_turn")
        elif cleave_target_id == target_id or cleave_target_id not in state["combatants"]:
            tracker.emit("mastery_skipped", mastery="cleave", actor_id=attacker_id, reason="invalid_cleave_target")
        else:
            c_target = state["combatants"][cleave_target_id]
            adj_dist = distance_ft(target["position"], c_target["position"])
            atk_dist = distance_ft(attacker["position"], c_target["position"])
            if c_target.get("alive") and adj_dist <= 5 and atk_dist <= reach_ft:
                attacker["turn_resources"]["cleave_used"] = True
                tracker.emit(
                    "mastery_triggered",
                    mastery="cleave",
                    actor_id=attacker_id,
                    primary_target_id=target_id,
                    cleave_target_id=cleave_target_id,
                )
                tracker.hook(f"Cleave mastery triggers an extra melee attack against {c_target['name']}.")
                _resolve_single_weapon_attack(
                    state,
                    attacker_id,
                    cleave_target_id,
                    weapon,
                    rules,
                    rng,
                    tracker,
                    two_handed=two_handed,
                    omit_positive_mod=True,
                    use_mastery=False,
                    allow_cleave=False,
                    advantage_override=False,
                    disadvantage_override=False,
                )
            else:
                tracker.emit(
                    "mastery_skipped",
                    mastery="cleave",
                    actor_id=attacker_id,
                    cleave_target_id=cleave_target_id,
                    reason="target_out_of_cleave_range",
                )

    return {
        "hit": True,
        "crit": crit,
        "damage": eff_dmg,
        "chosen": int(d20["chosen"]),
        "total": int(total_to_hit),
        "target_ac": int(target_ac),
    }


def _find_monster_action(actor: dict[str, Any], action_name: str) -> dict[str, Any] | None:
    needle = action_name.strip().lower()
    needle_slug = _slugify(action_name)
    for act in actor.get("monster_actions", []):
        aname = str(act.get("name") or "").strip().lower()
        if aname == needle or _slugify(aname) == needle_slug:
            return act
    return None


def _resolve_monster_action(
    state: dict[str, Any],
    actor_id: str,
    action: dict[str, Any],
    target_ids: list[str],
    rules: RulesStore,
    rng: DeterministicRNG,
    tracker: _StepTracker,
    *,
    advantage_override: bool = False,
    disadvantage_override: bool = False,
) -> tuple[bool, str | None]:
    actor = state["combatants"][actor_id]
    act_name = str(action.get("name") or "Action")

    # Multiattack recursion.
    multi = action.get("multiattack")
    if isinstance(multi, dict) or _slugify(act_name) == "multiattack":
        seq: list[dict[str, Any]] = list((multi or {}).get("sequence") or [])
        opts = (multi or {}).get("options") or []
        if opts:
            # Pick the first option whose actions resolve to executable monster actions.
            for opt in opts:
                cand_actions: list[dict[str, Any]] = []
                if isinstance(opt, dict) and isinstance(opt.get("actions"), list):
                    cand_actions = [a for a in opt["actions"] if isinstance(a, dict)]
                elif isinstance(opt, list):
                    cand_actions = [a for a in opt if isinstance(a, dict)]
                elif isinstance(opt, dict) and (opt.get("action_name") or opt.get("name")):
                    cand_actions = [opt]
                if cand_actions and all(
                    _find_monster_action(actor, str(a.get("action_name") or a.get("name") or "")) is not None
                    for a in cand_actions
                ):
                    seq.extend(cand_actions)
                    break
        if not seq:
            return False, f"Multiattack on '{actor['name']}' has no executable sub-attack sequence."
        tracker.emit("multiattack", actor_id=actor_id, sequence=seq)
        t_idx = 0
        for step_item in seq:
            sub_name = str(step_item.get("action_name") or step_item.get("name") or "")
            count = int(step_item.get("count", 1))
            sub_action = _find_monster_action(actor, sub_name)
            if sub_action is None:
                continue
            for _ in range(count):
                tid = target_ids[min(t_idx, len(target_ids) - 1)]
                t_idx += 1
                _resolve_monster_action(
                    state,
                    actor_id,
                    sub_action,
                    [tid],
                    rules,
                    rng,
                    tracker,
                    advantage_override=advantage_override,
                    disadvantage_override=disadvantage_override,
                )
        return True, None

    # Save-only / AoE / Breath Weapon action (no attack_bonus).
    is_attack = action.get("attack_bonus") is not None or action.get("kind") == "attack"
    save_block = action.get("save")
    dmg_parts_ir = action.get("damage_parts") or []

    if not is_attack and isinstance(save_block, dict):
        terrain = state.get("terrain")
        for tid in target_ids:
            if not has_line_of_sight(actor["position"], state["combatants"][tid]["position"], terrain):
                return False, f"No line of sight to '{tid}' for '{act_name}': blocked by a wall."
        save_ab = strip_prefix(save_block.get("ability") or "dex")
        save_dc = int(save_block.get("dc") or 10)
        on_success = str(save_block.get("on_success") or "none").lower()

        rolled_parts: list[dict[str, Any]] = []
        for dp in dmg_parts_ir:
            dice_str = str(dp.get("dice") or "0")
            dtype = strip_prefix(dp.get("type") or "bludgeoning")
            res = rng.roll_dice_expr(dice_str, crit=False)
            rolled_parts.append({"amount": max(0, int(res["total"])), "type": dtype, "rolls": res["rolls"], "dice": dice_str})

        tracker.emit(
            "monster_save_action",
            actor_id=actor_id,
            action=act_name,
            target_ids=target_ids,
            save_ability=save_ab,
            save_dc=save_dc,
            on_success=on_success,
            rolled_damage=rolled_parts,
        )

        for tid in target_ids:
            save_ev = _roll_save(
                state,
                tid,
                save_ab,
                save_dc,
                rules,
                rng,
                tracker,
                reason=act_name,
                source_pos=actor["position"],
            )
            save_idx = len(tracker.hooks)  # this target's save line goes before any 0-HP / concentration lines
            took: int | None = None
            if rolled_parts:
                if not save_ev["success"]:
                    adj_parts = [{"amount": p["amount"], "type": p["type"]} for p in rolled_parts]
                elif on_success == "half":
                    adj_parts = [{"amount": p["amount"] // 2, "type": p["type"]} for p in rolled_parts]
                else:
                    adj_parts = []
                if adj_parts:
                    took = _apply_damage_parts(state, tid, adj_parts, rules, rng, tracker, source_id=actor_id)
            applied_conds: list[str] = []
            if not save_ev["success"]:
                for cond in action.get("conditions_applied") or []:
                    c_slug = strip_prefix(cond.get("condition") if isinstance(cond, dict) else str(cond))
                    if _apply_condition(
                        state,
                        tid,
                        c_slug,
                        rules,
                        tracker,
                        source_id=actor_id,
                        duration=str(cond.get("duration", "indefinite")) if isinstance(cond, dict) else "indefinite",
                        save_ability=cond.get("repeat_save_ability") if isinstance(cond, dict) else None,
                        save_dc=save_dc,
                    ):
                        applied_conds.append(c_slug.replace("-", " ").title())
            cov_save = save_ev.get("cover_save_bonus")
            cov_kind = save_ev.get("cover")
            cov_note = f" [{_COVER_LABELS[cov_kind]} +{cov_save} DEX save]" if (cov_save and cov_kind in _COVER_LABELS) else ""
            line = (
                f"{state['combatants'][tid]['name']} {'saves' if save_ev['success'] else 'fails'} "
                f"against {actor['name']}'s {act_name} (DC {save_dc}{cov_note})"
            )
            if took is not None:
                line += f" and takes {took} damage"
            if applied_conds:
                line += f"{',' if took is not None else ''} and is {' and '.join(applied_conds)}"
            tracker.hooks.insert(save_idx, line + ".")
        return True, None

    # Attack action (melee / ranged / melee_or_ranged).
    tid = target_ids[0]
    target = state["combatants"][tid]
    dist = distance_ft(actor["position"], target["position"])
    attack_kind = str(action.get("attack_kind") or "melee").lower()
    reach_ft = int(action.get("reach_ft") or 5)
    range_normal = action.get("range_normal_ft")
    range_long = action.get("range_long_ft") or range_normal

    is_ranged = attack_kind == "ranged" or (attack_kind == "melee_or_ranged" and dist > reach_ft)
    if is_ranged:
        max_r = int(range_long or 60)
        if dist > max_r:
            return False, f"Target '{tid}' is out of range ({dist} ft > {max_r} ft)."
    else:
        if dist > reach_ft:
            return False, f"Target '{tid}' is out of reach ({dist} ft > {reach_ft} ft)."

    if not has_line_of_sight(actor["position"], target["position"], state.get("terrain")):
        return False, f"No line of sight to '{tid}': blocked by a wall."

    mode, adv_src, disadv_src, consume = _gather_attack_roll_mode(
        state,
        actor,
        target,
        rules,
        is_ranged=is_ranged,
        dist=dist,
        range_normal_ft=int(range_normal) if range_normal is not None else None,
        advantage_override=advantage_override,
        disadvantage_override=disadvantage_override,
    )
    _consume_effects(state, consume)

    d20 = rng.roll_d20(mode)
    atk_bonus = int(action.get("attack_bonus", 0))
    exh_pen = -2 * _exhaustion_level(actor)
    bb_bonus, bb_details = _roll_bless_bane(actor, "attack", rng)
    total_to_hit = int(d20["chosen"]) + atk_bonus + exh_pen + bb_bonus
    cover_kind, cover_bonus = compute_cover(actor["position"], target["position"], state.get("terrain"))
    target_ac = _effective_ac(target) + cover_bonus

    if d20["nat1"]:
        hit = False
        crit = False
    elif d20["nat20"]:
        hit = True
        crit = True
    else:
        hit = total_to_hit >= target_ac
        crit = False
    if hit and not is_ranged:  # Parry (Reaction, #39) against a melee hit
        parried_ac = _maybe_reactive_parry(state, tid, actor_id, d20, total_to_hit, target_ac, rules, tracker)
        if parried_ac is not None:
            target_ac = parried_ac
            hit = total_to_hit >= target_ac
    if hit:  # Shield (Reaction) can turn the hit into a miss (#33)
        shielded_ac = _maybe_reactive_shield(state, tid, actor_id, d20, total_to_hit, target_ac, rules, tracker)
        if shielded_ac is not None:
            target_ac = shielded_ac
            hit = total_to_hit >= target_ac

    if hit and not crit and dist <= 5 and _has_modifier_flag(target, "auto_crit_within_5ft", rules):
        crit = True

    cover_fields: dict[str, Any] = {}
    if cover_bonus > 0 and cover_kind:
        cover_fields["cover"] = cover_kind
        cover_fields["cover_ac_bonus"] = cover_bonus

    tracker.emit(
        "attack_roll",
        actor_id=actor_id,
        target_id=tid,
        action=act_name,
        mode=mode,
        advantage_sources=adv_src,
        disadvantage_sources=disadv_src,
        rolls=d20["rolls"],
        chosen=d20["chosen"],
        nat20=d20["nat20"],
        nat1=d20["nat1"],
        attack_bonus=atk_bonus,
        exhaustion_penalty=exh_pen,
        bonus_dice=bb_details,
        total=total_to_hit,
        target_ac=target_ac,
        hit=hit,
        crit=crit,
        **cover_fields,
    )

    cover_txt = f" [{_COVER_LABELS[cover_kind]} +{cover_bonus} AC]" if (cover_bonus > 0 and cover_kind) else ""

    if not hit:
        tracker.hook(f"{actor['name']} misses {target['name']} with {act_name} ({total_to_hit} vs AC {target_ac}{cover_txt}).")
        return True, None

    dmg_to_apply: list[dict[str, Any]] = []
    conditional_triggered: list[str] = []
    for dp in dmg_parts_ir:
        cond_req = dp.get("conditional")
        if cond_req == "advantage" and mode != "advantage":
            continue
        if cond_req == "advantage":
            conditional_triggered.append("advantage")
        dice_str = str(dp.get("dice") or "1")
        dtype = strip_prefix(dp.get("type") or "bludgeoning")
        res = rng.roll_dice_expr(dice_str, crit=crit)
        dmg_to_apply.append({"amount": max(0, int(res["total"])), "type": dtype})

    hit_hook_idx = len(tracker.hooks)
    eff_dmg = _apply_damage_parts(
        state,
        tid,
        dmg_to_apply,
        rules,
        rng,
        tracker,
        source_id=actor_id,
        is_crit=crit,
    )
    tracker.hooks.insert(
        hit_hook_idx,
        f"{actor['name']} hits {target['name']} with {act_name}{' (CRITICAL HIT!)' if crit else ''} "
        f"({total_to_hit} vs AC {target_ac}{cover_txt}) for {eff_dmg} damage.",
    )

    # Rider saves & conditions (e.g., Ghoul Claw CON DC 10 -> paralyzed, Wolf Bite -> prone).
    conds_ir = action.get("conditions_applied") or []
    if isinstance(save_block, dict) and conds_ir:
        r_ab = strip_prefix(save_block.get("ability") or "con")
        r_dc = int(save_block.get("dc") or 10)
        save_ev = _roll_save(state, tid, r_ab, r_dc, rules, rng, tracker, reason=f"{act_name}_rider")
        rider_idx = len(tracker.hooks)
        applied: list[str] = []
        if not save_ev["success"]:
            for cond in conds_ir:
                c_slug = strip_prefix(cond.get("condition") if isinstance(cond, dict) else str(cond))
                dur = str(cond.get("duration", "save_ends")) if isinstance(cond, dict) else "save_ends"
                rep_ab = cond.get("repeat_save_ability", r_ab) if isinstance(cond, dict) else r_ab
                if _apply_condition(
                    state,
                    tid,
                    c_slug,
                    rules,
                    tracker,
                    source_id=actor_id,
                    duration=dur,
                    save_ability=strip_prefix(rep_ab) if rep_ab else None,
                    save_dc=r_dc,
                ):
                    applied.append(c_slug.replace("-", " ").title())
        tracker.hooks.insert(rider_idx, _save_hook(target["name"], r_ab, r_dc, act_name, save_ev["success"], applied))
    elif conds_ir:
        for cond in conds_ir:
            c_slug = strip_prefix(cond.get("condition") if isinstance(cond, dict) else str(cond))
            _apply_condition(state, tid, c_slug, rules, tracker, source_id=actor_id)

    return True, None


# ----------------------------------------------------------------------
# Legal Actions Builder
# ----------------------------------------------------------------------


def compute_legal_actions(state: dict[str, Any], rules: RulesStore) -> dict[str, Any]:
    ssv3 = int(state.get("state_shape_version", 1)) >= 3
    turn_order = state.get("turn_order") or []
    if not turn_order or state.get("status") == "ended":
        ended_legal: dict[str, Any] = {
            "active_actor_id": None,
            "movement_remaining_ft": 0,
            "actions": [],
            "bonus_actions": [],
            "weapons": [],
            "spells": [],
            "can_end_turn": False,
            "can_reroll": False,
            "reroll_hint": None,
        }
        if ssv3:
            ended_legal["class_features"] = []
            ended_legal["surge_action_available"] = False
            ended_legal["sneak_attack_available"] = False
        return ended_legal

    active_idx = int(state.get("active_index", 0)) % len(turn_order)
    active_id = turn_order[active_idx]
    actor = state["combatants"][active_id]
    res = actor.get("turn_resources", {})
    incap = _has_modifier_flag(actor, "cannot_act", rules) or not actor.get("alive", True)
    surge_avail = bool(res.get("surge_action")) if ssv3 else False

    actions: list[str] = []
    bonus_actions: list[str] = []
    weapons: list[str] = []
    spells: list[str] = []
    class_features: list[str] = []

    if not incap:
        if res.get("action") or surge_avail or int(res.get("attacks_remaining", 0)) > 0:
            actions.append("attack")
        if res.get("action") or surge_avail:
            actions.extend(["dodge", "dash", "disengage", "help"])
        weapons = [w["slug"] for w in actor.get("weapons", [])]
        if not weapons and actor.get("monster_actions"):
            weapons = [str(a.get("name")) for a in actor.get("monster_actions", [])]

        sc = actor.get("spellcasting")
        if isinstance(sc, dict):
            avail_slots = {int(k) for k, v in (sc.get("slots") or {}).items() if int(v) > 0}
            all_sp = list(sc.get("cantrips") or []) + list(sc.get("prepared_spells") or [])
            for sp_slug in all_sp:
                sp_rec = rules.get_spell(sp_slug)
                if sp_rec is None:
                    continue
                sp_lvl = int(sp_rec.get("level", 0))
                if sp_lvl > 0 and not any(s >= sp_lvl for s in avail_slots):
                    continue
                if sp_lvl > 0 and res.get("slot_spent_this_turn"):
                    continue  # one spell slot per turn (#52)
                atype = str(sp_rec.get("action_type") or "action")
                if atype == "action" and res.get("action"):
                    spells.append(sp_slug)
                    if "cast_spell" not in actions:
                        actions.append("cast_spell")
                elif atype == "bonus_action" and res.get("bonus_action"):
                    spells.append(sp_slug)
                    if "cast_spell" not in bonus_actions:
                        bonus_actions.append("cast_spell")

        if res.get("bonus_action") and any("light" in (w.get("properties") or []) for w in actor.get("weapons", [])):
            if "offhand_attack" not in bonus_actions:
                bonus_actions.append("offhand_attack")

        if ssv3 and int(actor.get("hp", 0)) > 0:
            actor_feats = {strip_prefix(str(f)) for f in (actor.get("features") or [])}
            cr = actor.get("class_resources") or {}
            if (
                "fighter-second-wind" in actor_feats
                and res.get("bonus_action")
                and int((cr.get("second_wind") or {}).get("remaining", 0)) >= 1
            ):
                class_features.append("second_wind")
                if "class_feature" not in bonus_actions:
                    bonus_actions.append("class_feature")
            if (
                "fighter-action-surge" in actor_feats
                and not res.get("action_surge_used_this_turn")
                and int((cr.get("action_surge") or {}).get("remaining", 0)) >= 1
            ):
                class_features.append("action_surge")
            if (
                "paladin-lay-on-hands" in actor_feats
                and res.get("bonus_action")
                and int((cr.get("lay_on_hands") or {}).get("remaining", 0)) >= 1
            ):
                class_features.append("lay_on_hands")
                if "class_feature" not in bonus_actions:
                    bonus_actions.append("class_feature")
            if (
                "life-preserve-life" in actor_feats
                and res.get("action")
                and int((cr.get("channel_divinity") or {}).get("remaining", 0)) >= 1
            ):
                class_features.append("preserve_life")
                if "class_feature" not in actions:
                    actions.append("class_feature")

    pending = state.get("pending_reroll")
    can_reroll = bool(
        not incap
        and actor.get("heroic_inspiration") is True
        and isinstance(pending, dict)
        and pending.get("actor_id") == active_id
        and isinstance(pending.get("intent"), dict)
        and isinstance(pending.get("rollback_combatants"), dict)
    )
    reroll_hint: dict[str, Any] | None = None
    if can_reroll and isinstance(pending, dict):
        reroll_hint = {
            "label": str(pending.get("label") or "Attack"),
            "target_id": str(pending.get("target_id") or ""),
            "target_name": str(pending.get("target_name") or ""),
            "previous_d20": int(pending.get("previous_d20", 0)),
            "previous_total": int(pending.get("previous_total", 0)),
            "target_ac": int(pending.get("target_ac", 0)),
        }

    out: dict[str, Any] = {
        "active_actor_id": active_id,
        "movement_remaining_ft": int(actor.get("movement_remaining_ft", 0)),
        "actions": actions,
        "bonus_actions": bonus_actions,
        "weapons": weapons,
        "spells": spells,
        "can_end_turn": True,
        "can_reroll": can_reroll,
        "reroll_hint": reroll_hint,
    }
    if ssv3:
        out["class_features"] = class_features
        out["surge_action_available"] = surge_avail
        out["sneak_attack_available"] = bool(
            not incap and _has_sneak_attack(actor) and not res.get("sneak_attack_used", False)
        )
    return out


def _invalid(state: dict[str, Any], message: str, rules: RulesStore) -> dict[str, Any]:
    diff: dict[str, Any] = {
        "hp_changes": [],
        "condition_changes": [],
        "position_changes": [],
        "slot_changes": [],
        "effect_changes": [],
        "turn_changed": False,
    }
    if isinstance(state, dict) and int(state.get("state_shape_version", 1)) >= 3:
        diff["resource_changes"] = []
    return {
        "status": "invalid_intent",
        "error": message,
        "events": [],
        "state_diff": diff,
        "state": state,
        "legal_actions": compute_legal_actions(state, rules) if state else {},
        "narrative_hooks": [],
    }


def _update_encounter_status(state: dict[str, Any], tracker: _StepTracker | None = None) -> None:
    """Flip `status` to "ended" once at most one side is still standing.

    On the active -> ended flip, emit `encounter_ended` (with `winner_side`, None if no side is left) and a
    narrative hook (#9). Before this the status changed silently.
    """
    living_sides = {
        c.get("side")
        for c in state["combatants"].values()
        if c.get("alive") and (c.get("kind") == "pc" or int(c.get("hp", 0)) > 0)
    }
    if len(living_sides) <= 1 and len(state["combatants"]) > 1:
        was_ended = state.get("status") == "ended"
        state["status"] = "ended"
        if tracker is not None and not was_ended:
            winner = str(next(iter(living_sides))) if living_sides else None
            enc_id = str(state.get("encounter_id") or "enc-1")
            tracker.emit("encounter_ended", encounter_id=enc_id, winner_side=winner, round=int(state.get("round", 0)))
            outcome = f"{winner} victorious" if winner else "no side left standing"
            tracker.hook(f"Encounter '{enc_id}' ends: {outcome}.")


# ----------------------------------------------------------------------
# Spell Helpers
# ----------------------------------------------------------------------


def _cantrip_scaled_dice(base_dice: str, scaling: dict[str, Any] | None, caster_level: int) -> str:
    tiers = (scaling or {}).get("cantrip_tiers")
    if not isinstance(tiers, dict) or not tiers:
        return base_dice
    chosen = base_dice
    for threshold in sorted(int(k) for k in tiers.keys()):
        if caster_level >= threshold:
            chosen = str(tiers[str(threshold)] if str(threshold) in tiers else tiers[threshold])
    return chosen


def _eldritch_blast_beams(caster_level: int) -> int:
    if caster_level >= 17:
        return 4
    if caster_level >= 11:
        return 3
    if caster_level >= 5:
        return 2
    return 1


def _has_active_shield(target: dict[str, Any], spell_slug: str) -> bool:
    for eff in target.get("active_effects", []):
        if eff.get("kind") == "shield":
            blocks = eff.get("blocks_spell", "magic-missile")
            if blocks == spell_slug or spell_slug == "magic-missile":
                return True
    return False


# ----------------------------------------------------------------------
# Public State Machine Entry Point: `step`
# ----------------------------------------------------------------------


def _reaction_trigger_applies(
    state: dict[str, Any],
    actor_id: str,
    spell: dict[str, Any],
) -> bool:
    """True only for a reaction spell with a real trigger: an engine `reaction_window` naming this caster.

    Casting a reaction spell on your own turn with no trigger is not legal (#33); triggered reactions such as Shield
    are cast by the engine itself (see `_maybe_reactive_shield`).
    """
    if str(spell.get("action_type") or "").lower() != "reaction":
        return False
    if not spell.get("reaction_trigger"):
        return False
    window = state.get("reaction_window")
    return isinstance(window, dict) and str(window.get("actor_id") or "") == str(actor_id)


# ----------------------------------------------------------------------
# Reactions (#33, #38): resolved by the engine with a deterministic policy
# ----------------------------------------------------------------------
# A combatant reacts automatically unless its entry sets "reaction_policy": "none". Each combatant has one
# Reaction per round (restored at the start of its own turn).


def _can_react(combatant: dict[str, Any], rules: RulesStore) -> bool:
    return (
        bool(combatant.get("alive"))
        and int(combatant.get("hp", 0)) > 0
        and str(combatant.get("reaction_policy") or "auto") != "none"
        and bool((combatant.get("turn_resources") or {}).get("reaction"))
        and not _has_modifier_flag(combatant, "cannot_act", rules)
    )


def _maybe_reactive_shield(
    state: dict[str, Any],
    target_id: str,
    attacker_id: str,
    d20: dict[str, Any],
    total_to_hit: int,
    target_ac: int,
    rules: RulesStore,
    tracker: _StepTracker,
    *,
    trigger: str = "hit_by_attack",
) -> int | None:
    """Cast Shield as a Reaction when an attack roll hits and +5 AC would make it miss (SRD 5.2 Shield).

    With `trigger="targeted_by_magic_missile"` (#33), the other trigger in the SRD 5.2 text, the hit/miss
    test is skipped: Shield negates every Magic Missile dart, so it is always worth casting.

    Requires Shield prepared, a free Reaction, a level-1+ slot, and no Shield already up. A natural 20 still hits,
    so Shield isn't wasted on it. Returns the new AC when Shield is cast (the caller re-checks the hit), else None.
    """
    target = state["combatants"][target_id]
    sc = target.get("spellcasting")
    if not isinstance(sc, dict) or "shield" not in (sc.get("prepared_spells") or []):
        return None
    mm_trigger = trigger == "targeted_by_magic_missile"
    if (not mm_trigger and d20.get("nat20")) or any(e.get("kind") == "shield" for e in target.get("active_effects", [])):
        return None
    if not _can_react(target, rules):
        return None
    spell = rules.get_spell("shield") or {}
    bonus = next((int(b.get("value", 5)) for b in (spell.get("buffs") or []) if b.get("kind") == "ac_bonus"), 5)
    if not mm_trigger and total_to_hit >= target_ac + bonus:
        return None  # Shield wouldn't turn this hit into a miss
    slots = sc.setdefault("slots", {})
    slot_level = next((lvl for lvl in sorted(int(k) for k, v in slots.items() if int(v) > 0) if lvl >= 1), None)
    if slot_level is None:
        return None
    # One spell slot per turn (#52) also binds a Reaction on your own turn (e.g. hit by an Opportunity Attack
    # while you move).
    order = state.get("turn_order") or []
    own_turn = bool(order) and order[int(state.get("active_index", 0)) % len(order)] == target_id
    if own_turn and target["turn_resources"].get("slot_spent_this_turn"):
        return None

    before = int(slots[str(slot_level)])
    slots[str(slot_level)] = before - 1
    if own_turn:
        target["turn_resources"]["slot_spent_this_turn"] = True
    tracker.slot_changes.append({"id": target_id, "slot_level": slot_level, "before": before, "after": before - 1})
    target["turn_resources"]["reaction"] = False
    target["active_effects"].append(
        {
            "id": _next_effect_id(state, "shield"),
            "kind": "shield",
            "source_id": target_id,
            "target_id": target_id,
            "value": bonus,
            "blocks_spell": "magic-missile",
            "expires": "start_of_source_next_turn",
            "created_round": state["round"],
            "concentration_spell": None,
        }
    )
    tracker.emit(
        "spell_cast",
        reaction=True,
        actor_id=target_id,
        spell="shield",
        slot_level=slot_level,
        target_ids=[target_id],
        as_reaction=True,
        trigger=trigger,
        trigger_source_id=attacker_id,
        coverage=str(spell.get("coverage") or "full"),
        unmodeled_clauses=list((spell.get("provenance") or {}).get("unmodeled_clauses") or []),
    )
    tracker.hook(f"{target['name']} casts Shield as a Reaction (+{bonus} AC until the start of their next turn).")
    return _effective_ac(target)


def _maybe_reactive_parry(
    state: dict[str, Any],
    target_id: str,
    attacker_id: str,
    d20: dict[str, Any],
    total_to_hit: int,
    target_ac: int,
    rules: RulesStore,
    tracker: _StepTracker,
) -> int | None:
    """Parry (#39): a monster hit by a melee attack adds N to its AC against that attack, if that makes it miss.

    The caller only asks for melee attacks. Holding a weapon is assumed for monsters with weapon attack actions,
    which every Parry monster in SRD 5.2 has. Returns the AC for this attack only.
    """
    target = state["combatants"][target_id]
    parry = _trait(target, "parry")
    if not parry or d20.get("nat20") or not _can_react(target, rules):
        return None
    bonus = int(parry.get("ac_bonus", 2))
    if total_to_hit >= target_ac + bonus:
        return None
    target["turn_resources"]["reaction"] = False
    tracker.emit("reaction_parry", reaction=True, actor_id=target_id, trigger_source_id=attacker_id, ac_bonus=bonus)
    tracker.hook(f"{target['name']} parries (+{bonus} AC against this attack).")
    return target_ac + bonus


def _melee_reach_and_attack(combatant: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str]:
    """(reach in ft, attack to use, 'weapon'|'action') for an Opportunity Attack; (0, None, '') if none.

    PCs use their melee weapon with the best average damage; monsters their best single melee attack action.
    """
    def avg(dice: str, bonus: int = 0) -> float:
        total = float(bonus)
        for n, d, k in re.findall(r"(?:(\d*)d(\d+)|([+-]?\d+))", str(dice).replace(" ", "")):
            total += int(n or 1) * (int(d) + 1) / 2 if d else int(k)
        return total

    best: tuple[float, int, dict[str, Any] | None, str] = (-1.0, 0, None, "")
    if combatant.get("kind") == "pc":
        for w in combatant.get("weapons") or []:
            if str(w.get("weapon_range") or "melee").lower() != "melee":
                continue
            reach = int(w.get("reach_ft") or (10 if "reach" in (w.get("properties") or []) else 5))
            score = avg(str(w.get("damage_dice") or "1"), int(w.get("damage_bonus") or 0))
            if score > best[0]:
                best = (score, reach, w, "weapon")
    else:
        for a in combatant.get("monster_actions") or []:
            if a.get("kind") != "attack" or str(a.get("attack_kind") or "").lower() != "melee":
                continue
            reach = int(a.get("reach_ft") or 5)
            score = sum(avg(str(p.get("dice") or "0")) for p in a.get("damage_parts") or [] if not p.get("conditional"))
            if score > best[0]:
                best = (score, reach, a, "action")
    return best[1], best[2], best[3]


def _opportunity_attacks(
    state: dict[str, Any],
    mover_id: str,
    from_pos: dict[str, int],
    to_pos: dict[str, int],
    rules: RulesStore,
    rng: DeterministicRNG,
    tracker: _StepTracker,
) -> bool:
    """Resolve Opportunity Attacks against a creature leaving reach (#38). Returns True if the mover was dropped.

    SRD 5.2: a hostile creature that can react makes one melee attack when the mover leaves its reach by moving,
    unless the mover took the Disengage action. Attacks happen in initiative order while the mover is still at
    `from_pos`; if one drops it to 0 HP, the move ends there. Teleportation never calls this.
    """
    mover = state["combatants"][mover_id]
    if (mover.get("turn_resources") or {}).get("disengaged"):
        return False
    for rid in state.get("turn_order") or []:
        if rid == mover_id or rid not in state["combatants"]:
            continue
        reactor = state["combatants"][rid]
        if reactor.get("side") == mover.get("side") or not _can_react(reactor, rules):
            continue
        reach, attack, kind = _melee_reach_and_attack(reactor)
        if attack is None:
            continue
        if not (distance_ft(reactor["position"], from_pos) <= reach < distance_ft(reactor["position"], to_pos)):
            continue
        reactor["turn_resources"]["reaction"] = False
        tracker.emit(
            "opportunity_attack",
            reaction=True,
            actor_id=rid,
            target_id=mover_id,
            weapon=attack.get("slug") if kind == "weapon" else None,
            action=attack.get("name") if kind == "action" else None,
        )
        tracker.hook(f"{reactor['name']} makes an Opportunity Attack as {mover['name']} leaves its reach!")
        tracker.in_reaction = True
        try:
            if kind == "weapon":
                _resolve_single_weapon_attack(state, rid, mover_id, attack, rules, rng, tracker, allow_cleave=False)
            else:
                _resolve_monster_action(state, rid, attack, [mover_id], rules, rng, tracker)
        finally:
            tracker.in_reaction = False
        if not mover.get("alive") or int(mover.get("hp", 0)) <= 0:
            return True
    return False


def step(
    state: dict[str, Any] | None,
    intent: dict[str, Any],
    rules: RulesStore,
    roll_overrides: list[int] | None = None,
) -> dict[str, Any]:
    """Advance the encounter state deterministically without mutating `state`."""
    if not isinstance(intent, dict) or not intent.get("type"):
        safe_copy = copy.deepcopy(state) if isinstance(state, dict) else {}
        return _invalid(safe_copy, "Intent must be a dict containing a 'type' field.", rules)

    intent_type = str(intent["type"]).strip().lower()

    # ------------------------------------------------------------------
    # 1. `start_encounter`
    # ------------------------------------------------------------------
    if intent_type == "start_encounter":
        safe_start = copy.deepcopy(state) if isinstance(state, dict) else {}
        seed = _parse_int(intent.get("seed", 1), allow_str=False)
        if seed is None:
            return _invalid(safe_start, "start_encounter 'seed' must be an integer.", rules)
        encounter_id = str(intent.get("encounter_id") or "enc-1")
        raw_combatants = intent.get("combatants")
        if not isinstance(raw_combatants, list) or not raw_combatants:
            return _invalid({}, "start_encounter requires a non-empty 'combatants' list.", rules)

        raw_ssv = intent.get("state_shape_version")
        if raw_ssv is None:
            ssv = STATE_SHAPE_VERSION
        else:
            parsed_ssv = _parse_int(raw_ssv, allow_str=False)
            if parsed_ssv is None or parsed_ssv < 1:
                return _invalid(safe_start, "start_encounter 'state_shape_version' must be a positive integer.", rules)
            ssv = parsed_ssv

        try:
            bounds = validate_bounds(intent.get("bounds"))
            terrain = validate_terrain(intent.get("terrain"))
        except ValueError as exc:
            return _invalid(safe_start, str(exc), rules)

        rng = DeterministicRNG(seed=seed, draw_index=0, roll_overrides=roll_overrides)
        tracker = _StepTracker(None)
        combatants: dict[str, dict[str, Any]] = {}
        init_records: list[tuple[int, int, str]] = []

        for entry in raw_combatants:
            if not isinstance(entry, dict) or not entry.get("id"):
                return _invalid({}, "Each combatant entry must be a dict with an 'id'.", rules)
            cid = str(entry["id"])
            if cid in combatants:
                return _invalid({}, f"Duplicate combatant id '{cid}'.", rules)
            try:
                if "statblock" in entry:
                    comb = _build_pc_combatant(entry, state_shape_version=ssv)
                elif "monster" in entry:
                    comb = _build_monster_combatant(entry, rules, state_shape_version=ssv)
                else:
                    return _invalid({}, f"Combatant '{cid}' must provide either 'statblock' or 'monster'.", rules)
            except ValueError as exc:
                return _invalid({}, str(exc), rules)

            if not in_bounds(comb["position"], bounds):
                pos = comb["position"]
                return _invalid(
                    safe_start,
                    f"Combatant '{cid}' initial position ({pos['x']}, {pos['y']}) is outside battlefield bounds "
                    f"[x: {bounds['min_x']}..{bounds['max_x']}, y: {bounds['min_y']}..{bounds['max_y']}].",
                    rules,
                )
            if is_wall(comb["position"], terrain):
                pos = comb["position"]
                return _invalid(
                    safe_start,
                    f"Combatant '{cid}' initial position ({pos['x']}, {pos['y']}) is on an impassable wall.",
                    rules,
                )

            # Optional initial conditions on start_encounter.
            for init_cond in entry.get("conditions") or []:
                c_slug = strip_prefix(init_cond.get("condition") if isinstance(init_cond, dict) else str(init_cond))
                comb["conditions"].append(
                    init_cond
                    if isinstance(init_cond, dict)
                    else {"condition": c_slug, "source_id": None, "duration": "indefinite"}
                )

            combatants[cid] = comb

        # Roll initiative in input order for deterministic draw consumption.
        for entry in raw_combatants:
            cid = str(entry["id"])
            comb = combatants[cid]
            conds = _effective_conditions(comb, rules)
            adv = "invisible" in conds
            disadv = "incapacitated" in conds
            mode = "normal" if (adv and disadv) else ("advantage" if adv else ("disadvantage" if disadv else "normal"))
            d20 = rng.roll_d20(mode)
            init_mod = int(comb.get("initiative_bonus", comb["ability_mods"]["dex"]))
            exh_pen = -2 * _exhaustion_level(comb)
            init_total = int(d20["chosen"]) + init_mod + exh_pen
            comb["initiative"] = init_total
            comb["movement_remaining_ft"] = _effective_speed(comb, rules)
            init_records.append((init_total, int(comb["abilities"]["dex"]), cid))
            tracker.emit(
                "initiative_rolled",
                combatant_id=cid,
                mode=mode,
                rolls=d20["rolls"],
                chosen=d20["chosen"],
                modifier=init_mod,
                exhaustion_penalty=exh_pen,
                total=init_total,
            )

        # Sort by (initiative DESC, dex_score DESC, id ASC).
        init_records.sort(key=lambda r: (-r[0], -r[1], r[2]))
        turn_order = [r[2] for r in init_records]

        new_state: dict[str, Any] = {
            "schema_version": "2",
            "encounter_id": encounter_id,
            "status": "active",
            "round": 1,
            "turn_order": turn_order,
            "active_index": 0,
            "rng": rng.to_dict(),
            "pending_reroll": None,
            "combatants": combatants,
        }
        if ssv >= 3:
            new_state["state_shape_version"] = STATE_SHAPE_VERSION
        if bounds is not None:
            new_state["bounds"] = bounds
        if terrain is not None:
            new_state["terrain"] = terrain

        # Check exhaustion 6 on start.
        for cid in turn_order:
            _check_exhaustion_death(new_state, cid, tracker)

        tracker.emit("encounter_started", encounter_id=encounter_id, turn_order=turn_order, round=1)
        first_actor = combatants[turn_order[0]]["name"]
        tracker.hook(f"Encounter '{encounter_id}' begins! Round 1: {first_actor} acts first.")
        _start_of_turn_traits(new_state, turn_order[0], rules, rng, tracker)
        new_state["rng"] = rng.to_dict()

        return {
            "status": "ok",
            "error": None,
            "events": tracker.events,
            "state_diff": tracker.build_diff(new_state),
            "state": new_state,
            "legal_actions": compute_legal_actions(new_state, rules),
            "narrative_hooks": tracker.hooks,
        }

    # All other intents require an existing EncounterState.
    if not isinstance(state, dict) or "combatants" not in state or "turn_order" not in state:
        return _invalid({}, f"Intent '{intent_type}' requires a valid EncounterState.", rules)

    # Always deep-copy to guarantee zero mutation of caller's input state.
    s = copy.deepcopy(state)
    rng = DeterministicRNG.from_dict(s.get("rng"), roll_overrides=roll_overrides)
    tracker = _StepTracker(state)

    turn_order = s["turn_order"]
    active_id = turn_order[int(s["active_index"]) % len(turn_order)]
    actor_id = str(intent.get("actor_id") or active_id)

    if actor_id not in s["combatants"]:
        return _invalid(copy.deepcopy(state), f"Unknown combatant '{actor_id}'.", rules)

    # #9: once the encounter has ended, nothing but a fresh start_encounter is accepted. A pending Heroic
    # Inspiration reroll is the one exception: rerolling the miss rewinds to before it, so it can reopen the fight.
    if s.get("status") == "ended" and not (intent_type == "reroll" and s.get("pending_reroll")):
        return _invalid(copy.deepcopy(state), "Encounter has ended.", rules)

    actor = s["combatants"][actor_id]

    # Enforce exhaustion 6 death check if externally mutated/initialized.
    if _exhaustion_level(actor) >= 6 and actor.get("alive"):
        s["pending_reroll"] = None
        _check_exhaustion_death(s, actor_id, tracker)
        s["rng"] = rng.to_dict()
        _update_encounter_status(s, tracker)
        return {
            "status": "ok",
            "error": None,
            "events": tracker.events,
            "state_diff": tracker.build_diff(s),
            "state": s,
            "legal_actions": compute_legal_actions(s, rules),
            "narrative_hooks": tracker.hooks,
        }

    # ------------------------------------------------------------------
    # 2. `move`
    # ------------------------------------------------------------------
    if intent_type == "move":
        s["pending_reroll"] = None
        if actor_id != active_id:
            return _invalid(copy.deepcopy(state), f"It is {active_id}'s turn, not {actor_id}'s.", rules)
        if not actor.get("alive"):
            return _invalid(copy.deepcopy(state), f"{actor_id} is not alive.", rules)
        if _has_modifier_flag(actor, "cannot_act", rules):
            return _invalid(copy.deepcopy(state), f"{actor_id} is Incapacitated and cannot move.", rules)

        eff_speed = _effective_speed(actor, rules)
        if eff_speed <= 0:
            return _invalid(copy.deepcopy(state), f"{actor_id} has 0 speed and cannot move.", rules)

        rem = min(int(actor.get("movement_remaining_ft", eff_speed)), eff_speed)
        stand_up = bool(intent.get("stand_up", False))
        to_pos = intent.get("to")
        if to_pos is None:
            to_pos = copy.deepcopy(actor["position"])
        else:
            parsed_to = _parse_point(to_pos)
            if parsed_to is None:
                return _invalid(
                    copy.deepcopy(state),
                    "move 'to' must be a dict with integer 'x' and 'y' coordinates.",
                    rules,
                )
            to_pos = parsed_to

        if distance_ft(actor["position"], to_pos) > 0:
            blocker_id = square_occupant(s, actor_id, to_pos)
            if blocker_id is not None:
                blocker_name = str(s["combatants"][blocker_id].get("name") or blocker_id)
                return _invalid(
                    copy.deepcopy(state),
                    f"({to_pos['x']}, {to_pos['y']}) is occupied by {blocker_name}; "
                    "a creature can't end its move in another creature's space.",
                    rules,
                )

        if stand_up:
            if "prone" in _effective_conditions(actor, rules):
                stand_cost = eff_speed // 2
                if rem < stand_cost:
                    return _invalid(
                        copy.deepcopy(state),
                        f"Not enough movement to stand up (need {stand_cost} ft, have {rem} ft).",
                        rules,
                    )
                rem -= stand_cost
                _remove_condition(actor, "prone")
                tracker.emit("stood_up", actor_id=actor_id, movement_cost_ft=stand_cost)
                tracker.hook(f"{actor['name']} spends {stand_cost} ft of movement to stand up from Prone.")

        dist = distance_ft(actor["position"], to_pos)
        if dist > 0:
            # Battlefield bounds & wall / terrain pathfinding enforcement (M6.1 / M6.2).
            bounds = s.get("bounds")
            terrain = s.get("terrain")
            is_prone = "prone" in _effective_conditions(actor, rules)
            move_cost, path_err = movement_path_cost(
                actor["position"],
                to_pos,
                bounds,
                terrain,
                is_prone=is_prone,
                max_cost=rem,
            )
            if path_err is not None or move_cost is None:
                return _invalid(
                    copy.deepcopy(state),
                    path_err or f"Destination ({to_pos['x']}, {to_pos['y']}) is unreachable.",
                    rules,
                )

            # Frightened: cannot willingly move closer to fear source.
            if "frightened" in _effective_conditions(actor, rules):
                for fe in _condition_entries_for(actor, "frightened"):
                    fsrc_id = fe.get("source_id")
                    if fsrc_id and fsrc_id in s["combatants"] and s["combatants"][fsrc_id].get("alive"):
                        fpos = s["combatants"][fsrc_id]["position"]
                        if distance_ft(to_pos, fpos) < distance_ft(actor["position"], fpos):
                            return _invalid(
                                copy.deepcopy(state),
                                f"{actor_id} is Frightened of {fsrc_id} and cannot move closer to it.",
                                rules,
                            )

            if move_cost > rem:
                extra_txt = "difficult terrain" if move_cost > (dist * 2 if is_prone else dist) else ("crawling" if is_prone else "walking")
                return _invalid(
                    copy.deepcopy(state),
                    f"Insufficient movement: need {move_cost} ft ({extra_txt}), have {rem} ft.",
                    rules,
                )
            old_pos = copy.deepcopy(actor["position"])
            # Opportunity Attacks happen as the mover leaves reach, before it gets away (#38).
            if _opportunity_attacks(s, actor_id, old_pos, to_pos, rules, rng, tracker):
                actor["movement_remaining_ft"] = 0
                tracker.hook(f"{actor['name']} is dropped before getting away; the move ends at ({old_pos['x']}, {old_pos['y']}).")
                s["rng"] = rng.to_dict()
                _update_encounter_status(s, tracker)
                return {
                    "status": "ok",
                    "error": None,
                    "events": tracker.events,
                    "state_diff": tracker.build_diff(s),
                    "state": s,
                    "legal_actions": compute_legal_actions(s, rules),
                    "narrative_hooks": tracker.hooks,
                }
            actor["position"] = to_pos
            rem -= move_cost
            tracker.emit(
                "moved",
                actor_id=actor_id,
                from_pos=old_pos,
                to_pos=to_pos,
                distance_ft=dist,
                movement_cost_ft=move_cost,
                crawled=is_prone,
            )
            tracker.hook(
                f"{actor['name']} {'crawls' if is_prone else 'moves'} {dist} ft to ({to_pos['x']}, {to_pos['y']})."
            )

        actor["movement_remaining_ft"] = rem
        s["rng"] = rng.to_dict()
        return {
            "status": "ok",
            "error": None,
            "events": tracker.events,
            "state_diff": tracker.build_diff(s),
            "state": s,
            "legal_actions": compute_legal_actions(s, rules),
            "narrative_hooks": tracker.hooks,
        }

    # ------------------------------------------------------------------
    # 3. `attack`
    # ------------------------------------------------------------------
    if intent_type == "attack":
        pre_action_combatants = copy.deepcopy(s["combatants"])
        s["pending_reroll"] = None
        if actor_id != active_id:
            return _invalid(copy.deepcopy(state), f"It is {active_id}'s turn, not {actor_id}'s.", rules)
        if not actor.get("alive"):
            return _invalid(copy.deepcopy(state), f"{actor_id} is not alive.", rules)
        if _has_modifier_flag(actor, "cannot_act", rules):
            return _invalid(copy.deepcopy(state), f"{actor_id} is Incapacitated and cannot attack.", rules)

        target_id = intent.get("target_id")
        target_ids_raw = intent.get("target_ids")
        if target_ids_raw and isinstance(target_ids_raw, list):
            tids = [str(x) for x in target_ids_raw]
        elif target_id:
            tids = [str(target_id)]
        else:
            return _invalid(copy.deepcopy(state), "attack intent requires 'target_id' (or 'target_ids').", rules)

        for tid in tids:
            if tid not in s["combatants"]:
                return _invalid(copy.deepcopy(state), f"Unknown target '{tid}'.", rules)
            # Charmed check: cannot target charmer.
            if "charmed" in _effective_conditions(actor, rules):
                for ce in _condition_entries_for(actor, "charmed"):
                    if ce.get("source_id") == tid:
                        return _invalid(
                            copy.deepcopy(state),
                            f"{actor_id} is Charmed by {tid} and cannot target them with an attack.",
                            rules,
                        )

        adv_ovr = bool(intent.get("advantage_override", False))
        disadv_ovr = bool(intent.get("disadvantage_override", False))

        # Monster action attack path.
        if intent.get("action") or (actor.get("kind") == "monster" and not intent.get("weapon")):
            act_name = str(intent.get("action") or "")
            if not act_name and actor.get("monster_actions"):
                act_name = str(actor["monster_actions"][0].get("name"))
            m_action = _find_monster_action(actor, act_name)
            if m_action is None:
                return _invalid(copy.deepcopy(state), f"Monster '{actor_id}' has no action '{act_name}'.", rules)
            if not _consume_non_magic_action(actor["turn_resources"]):
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Action remaining this turn.", rules)
            ok, err_msg = _resolve_monster_action(
                s,
                actor_id,
                m_action,
                tids,
                rules,
                rng,
                tracker,
                advantage_override=adv_ovr,
                disadvantage_override=disadv_ovr,
            )
            if not ok:
                return _invalid(copy.deepcopy(state), err_msg or "Monster action failed.", rules)
            s["rng"] = rng.to_dict()
            _update_encounter_status(s, tracker)
            atk_events = [e for e in tracker.events if e.get("type") == "attack_roll"]
            if (
                atk_events
                and not any(e.get("hit") for e in atk_events)
                and actor_id == active_id
                and actor.get("heroic_inspiration") is True
                and s.get("status") != "ended"
            ):
                last_ev = atk_events[-1]
                ev_tid = str(last_ev["target_id"])
                s["pending_reroll"] = {
                    "actor_id": actor_id,
                    "kind": "attack",
                    "bonus_dice": _action_bonus_dice(tracker.events, actor_id, "attack_roll"),
                    "intent": copy.deepcopy(intent),
                    "previous_d20": int(last_ev["chosen"]),
                    "previous_total": int(last_ev["total"]),
                    "target_ac": int(last_ev["target_ac"]),
                    "target_id": ev_tid,
                    "target_name": str(s["combatants"][ev_tid]["name"]),
                    "label": str(m_action.get("name") or act_name or "Attack"),
                    "rollback_combatants": pre_action_combatants,
                }
            return {
                "status": "ok",
                "error": None,
                "events": tracker.events,
                "state_diff": tracker.build_diff(s),
                "state": s,
                "legal_actions": compute_legal_actions(s, rules),
                "narrative_hooks": tracker.hooks,
            }

        # PC weapon attack path.
        w_slug = strip_prefix(intent.get("weapon") or "")
        weapon: dict[str, Any] | None = None
        for wp in actor.get("weapons", []):
            if strip_prefix(wp.get("slug", "")) == w_slug:
                weapon = wp
                break
        if weapon is None:
            return _invalid(copy.deepcopy(state), f"Combatant '{actor_id}' does not have weapon '{w_slug}'.", rules)

        tid = tids[0]
        target = s["combatants"][tid]
        dist = distance_ft(actor["position"], target["position"])
        w_range = str(weapon.get("weapon_range") or "melee").lower()
        reach_ft = int(weapon.get("reach_ft") or 5)
        throw_range = weapon.get("throw_range")

        if w_range == "ranged":
            max_r = int(weapon.get("range_long_ft") or weapon.get("range_normal_ft") or 80)
            if dist > max_r:
                return _invalid(copy.deepcopy(state), f"Target '{tid}' out of weapon range ({dist} ft > {max_r} ft).", rules)
        else:
            max_throw = int(throw_range.get("long_ft") or throw_range.get("normal_ft") or 0) if isinstance(throw_range, dict) else 0
            if dist > reach_ft and dist > max_throw:
                return _invalid(copy.deepcopy(state), f"Target '{tid}' out of reach ({dist} ft > {reach_ft} ft).", rules)

        if not has_line_of_sight(actor["position"], target["position"], s.get("terrain")):
            return _invalid(
                copy.deepcopy(state),
                f"No line of sight to '{tid}': blocked by a wall.",
                rules,
            )

        offhand = bool(intent.get("offhand", False))
        use_mastery = bool(intent.get("use_mastery", True))
        two_handed = bool(intent.get("two_handed", False))
        props = set(weapon.get("properties") or [])
        mastery_slug = weapon.get("mastery") if (use_mastery and weapon.get("mastery_active")) else None
        res = actor["turn_resources"]
        omit_pos_mod = False

        # Economy validation (Action / Extra Attack / Light + Nick mastery).
        if offhand:
            if "light" not in props:
                return _invalid(copy.deepcopy(state), f"Off-hand attack requires a Light weapon ('{w_slug}' is not Light).", rules)
            has_nick = mastery_slug == "nick" or any(
                w.get("mastery") == "nick" and w.get("mastery_active") for w in actor.get("weapons", [])
            )
            if use_mastery and has_nick:
                if res.get("nick_used"):
                    return _invalid(copy.deepcopy(state), "Nick mastery extra attack can only be used once per turn.", rules)
                # Nick moves the Light extra attack into the Attack action, preserving Bonus Action!
                if res.get("action") and not res.get("light_attack_made") and int(res.get("attacks_remaining", 0)) == 0:
                    res["action"] = False
                    res["attacks_remaining"] = max(0, int(actor.get("extra_attacks", 1)) - 1)
                res["nick_used"] = True
                tracker.emit("mastery_triggered", mastery="nick", actor_id=actor_id, weapon=w_slug, bonus_action_preserved=res.get("bonus_action", True))
            else:
                if not res.get("bonus_action"):
                    return _invalid(copy.deepcopy(state), f"{actor_id} has no Bonus Action remaining for off-hand attack.", rules)
                res["bonus_action"] = False
            omit_pos_mod = "two-weapon-fighting" not in (actor.get("feats") or [])
        else:
            if int(res.get("attacks_remaining", 0)) > 0:
                res["attacks_remaining"] = int(res["attacks_remaining"]) - 1
            elif res.get("action"):
                res["action"] = False
                res["attacks_remaining"] = max(0, int(actor.get("extra_attacks", 1)) - 1)
            elif res.get("surge_action"):
                res["surge_action"] = False
                res["attacks_remaining"] = max(0, int(actor.get("extra_attacks", 1)) - 1)
            else:
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Action or Extra Attacks remaining.", rules)
            if "light" in props:
                res["light_attack_made"] = True

        atk_res = _resolve_single_weapon_attack(
            s,
            actor_id,
            tid,
            weapon,
            rules,
            rng,
            tracker,
            two_handed=two_handed,
            omit_positive_mod=omit_pos_mod,
            use_mastery=use_mastery,
            allow_cleave=True,
            cleave_target_id=intent.get("cleave_target_id"),
            advantage_override=adv_ovr,
            disadvantage_override=disadv_ovr,
            use_sneak_attack=bool(intent.get("use_sneak_attack", True)),
        )

        s["rng"] = rng.to_dict()
        _update_encounter_status(s, tracker)
        if (
            not atk_res["hit"]
            and actor_id == active_id
            and actor.get("heroic_inspiration") is True
            and s.get("status") != "ended"
        ):
            s["pending_reroll"] = {
                "actor_id": actor_id,
                "kind": "attack",
                "bonus_dice": _action_bonus_dice(tracker.events, actor_id, "attack_roll"),
                "intent": copy.deepcopy(intent),
                "previous_d20": int(atk_res["chosen"]),
                "previous_total": int(atk_res["total"]),
                "target_ac": int(atk_res["target_ac"]),
                "target_id": tid,
                "target_name": str(s["combatants"][tid]["name"]),
                "label": str(weapon.get("name") or weapon.get("slug") or "Attack"),
                "rollback_combatants": pre_action_combatants,
            }
        return {
            "status": "ok",
            "error": None,
            "events": tracker.events,
            "state_diff": tracker.build_diff(s),
            "state": s,
            "legal_actions": compute_legal_actions(s, rules),
            "narrative_hooks": tracker.hooks,
        }

    # ------------------------------------------------------------------
    # 4. `cast_spell`
    # ------------------------------------------------------------------
    if intent_type == "cast_spell":
        pre_action_combatants = copy.deepcopy(s["combatants"])
        s["pending_reroll"] = None
        if not actor.get("alive"):
            return _invalid(copy.deepcopy(state), f"{actor_id} is not alive.", rules)
        if _has_modifier_flag(actor, "cannot_act", rules):
            return _invalid(copy.deepcopy(state), f"{actor_id} is Incapacitated and cannot cast spells.", rules)

        spell_raw = intent.get("spell")
        if not spell_raw:
            return _invalid(copy.deepcopy(state), "cast_spell intent requires 'spell'.", rules)
        spell_slug = strip_prefix(spell_raw)
        spell = rules.get_spell(spell_slug)
        if spell is None:
            return _invalid(copy.deepcopy(state), f"Unknown spell '{spell_raw}'.", rules)

        sc = actor.get("spellcasting")
        if not isinstance(sc, dict):
            return _invalid(copy.deepcopy(state), f"{actor_id} does not have spellcasting.", rules)

        action_type = str(spell.get("action_type") or "action").lower()
        wants_reaction = bool(intent.get("as_reaction", False)) or action_type == "reaction"
        if action_type == "reaction" and wants_reaction and not _reaction_trigger_applies(s, actor_id, spell):
            return _invalid(
                copy.deepcopy(state),
                f"No valid reaction trigger for '{spell_slug}'.",
                rules,
            )
        as_reaction = action_type == "reaction" and wants_reaction
        res = actor["turn_resources"]

        if as_reaction:
            if not res.get("reaction"):
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Reaction remaining.", rules)
            res["reaction"] = False
            window = s.get("reaction_window")
            if isinstance(window, dict) and str(window.get("actor_id") or "") == actor_id:
                s.pop("reaction_window", None)
        else:
            if actor_id != active_id:
                return _invalid(copy.deepcopy(state), f"It is {active_id}'s turn, not {actor_id}'s.", rules)
            if action_type == "bonus_action":
                if not res.get("bonus_action"):
                    return _invalid(copy.deepcopy(state), f"{actor_id} has no Bonus Action remaining.", rules)
                res["bonus_action"] = False
            else:
                if not res.get("action"):
                    if res.get("surge_action"):
                        return _invalid(copy.deepcopy(state), _ACTION_SURGE_MAGIC_BAN_ERROR, rules)
                    return _invalid(copy.deepcopy(state), f"{actor_id} has no Action remaining.", rules)
                res["action"] = False

        spell_level = int(spell.get("level", 0))
        raw_slot_level = intent.get("slot_level")
        if raw_slot_level is None:
            slot_level = spell_level
        else:
            parsed_slot = _parse_int(raw_slot_level, allow_str=False)
            if parsed_slot is None:
                return _invalid(copy.deepcopy(state), "cast_spell 'slot_level' must be an integer.", rules)
            slot_level = parsed_slot
        if spell_level >= 1:
            if slot_level < spell_level:
                return _invalid(
                    copy.deepcopy(state),
                    f"Slot level {slot_level} is below spell '{spell_slug}' base level {spell_level}.",
                    rules,
                )
            # SRD 5.2: on a turn you can expend only one spell slot to cast a spell (#52). Reactions on another
            # creature's turn are a different turn, and cantrips spend no slot.
            own_turn = actor_id == active_id
            if own_turn and actor["turn_resources"].get("slot_spent_this_turn"):
                return _invalid(
                    copy.deepcopy(state),
                    f"{actor_id} has no more spell slots to spend this turn (one spell slot per turn); "
                    "cast a cantrip or end the turn.",
                    rules,
                )
            slots_map = sc.setdefault("slots", {})
            before_cnt = int(slots_map.get(str(slot_level), 0))
            if before_cnt <= 0:
                return _invalid(
                    copy.deepcopy(state),
                    f"{actor_id} has no level-{slot_level} spell slots remaining.",
                    rules,
                )
            slots_map[str(slot_level)] = before_cnt - 1
            if own_turn:
                actor["turn_resources"]["slot_spent_this_turn"] = True
            tracker.slot_changes.append(
                {"id": actor_id, "slot_level": slot_level, "before": before_cnt, "after": before_cnt - 1}
            )
        else:
            slot_level = 0

        raw_targets = intent.get("target_ids")
        if raw_targets is None and intent.get("target_id"):
            raw_targets = [intent["target_id"]]
        if raw_targets is None:
            target_ids: list[str] = []
        elif isinstance(raw_targets, (list, tuple)):
            target_ids = [str(t) for t in raw_targets]
        else:
            return _invalid(
                copy.deepcopy(state),
                "cast_spell 'target_ids' must be a list of combatant IDs.",
                rules,
            )
        for tid in target_ids:
            if tid not in s["combatants"]:
                return _invalid(copy.deepcopy(state), f"Unknown spell target '{tid}'.", rules)

        # #69: Divine Smite / Shining Smite ride the melee hit from earlier this turn and may
        # replace target_ids with that creature. A refusal returns the caller's original state.
        smite = ON_HIT_SMITES.get(spell_slug)
        if smite is not None:
            last_hit = (actor.get("turn_resources") or {}).get("last_melee_hit")
            if not isinstance(last_hit, dict):
                return _invalid(
                    copy.deepcopy(state),
                    f"{spell.get('name', spell_slug)} must be cast right after you hit with a melee weapon this turn.",
                    rules,
                )
            smite_tid = str(last_hit["target_id"])
            if target_ids not in ([], [actor_id], [smite_tid]):
                return _invalid(
                    copy.deepcopy(state),
                    f"{spell.get('name', spell_slug)} must target the creature you just hit ({smite_tid}).",
                    rules,
                )
            smite_target = s["combatants"].get(smite_tid)
            if not smite_target or not smite_target.get("alive") or int(smite_target.get("hp", 0)) <= 0:
                return _invalid(copy.deepcopy(state), f"{smite_tid} is already down.", rules)
            target_ids = [smite_tid]

        # Hold Person: Humanoid only. Unknown types fail closed. Runs after the smite retarget
        # so the creature that will actually be affected is the one that is checked.
        allowed_types = _required_creature_types(spell_slug, spell)
        if allowed_types and target_ids:
            spell_name = str(spell.get("name") or spell_slug)
            need = " or ".join(part.title() for part in sorted(allowed_types))
            for tid in target_ids:
                target = s["combatants"][tid]
                kind = creature_type_of(target, rules)
                if kind is None or kind not in allowed_types:
                    shown = "unknown; unknown creature types are refused" if kind is None else kind
                    return _invalid(
                        copy.deepcopy(state),
                        f"{spell_name} requires a {need} target ({target.get('name') or tid} is {shown}).",
                        rules,
                    )

        # Default self-targeting for self buff/reaction spells like shield/mage-armor/false-life/misty-step.
        # A harmful Self-range spell (attack or save resolution: Burning Hands, Thunderwave, Spirit Guardians,
        # Produce Flame, ...) starts at the caster and affects others, so the caster is never its target (#67).
        # An area spell with nobody else in it is still a legal cast and affects no one (e.g. Spirit Guardians up
        # before enemies arrive). A single-target one with no target is refused, like Fire Bolt with no target.
        self_range = str(spell.get("range_kind") or "").lower() == "self"
        harmful_self_area = False
        castable_without_target = False
        if self_range and str(spell.get("resolution") or "").lower() in ("attack", "save"):
            target_ids = [t for t in target_ids if t != actor_id]
            harmful_self_area = (
                str((spell.get("targeting") or {}).get("mode") or "") == "aoe" or spell_slug in SELF_LINE_SPELLS
            )
            castable_without_target = harmful_self_area or spell_slug in SELF_NO_TARGET_OK
            if not target_ids and not castable_without_target:
                return _invalid(
                    copy.deepcopy(state),
                    f"'{spell_slug}' needs a target other than its caster.",
                    rules,
                )
        elif not target_ids and self_range:
            target_ids = [actor_id]

        # Charmed check for harmful spells.
        is_harmful = bool(spell.get("damage") or (spell.get("resolution") in ("attack", "save")))
        if is_harmful and "charmed" in _effective_conditions(actor, rules):
            for ce in _condition_entries_for(actor, "charmed"):
                if ce.get("source_id") in target_ids:
                    return _invalid(
                        copy.deepcopy(state),
                        f"{actor_id} is Charmed by {ce['source_id']} and cannot target them with '{spell_slug}'.",
                        rules,
                    )

        # AoE origin resolution & Line of Sight checks (M6.2 / MF2):
        # - Non-AoE targeted spells require LoS from caster to every target.
        # - AoE spells measure LoS and Cover from the AoE point of origin (caster for Self-originated AoEs,
        #   or explicit origin/target_point/point or primary in-LoS target for ranged-point AoEs).
        #   Targets behind total cover (wall) from the AoE origin are dropped individually without
        #   refusing the spell or refunding the slot when a valid point/target exists.
        terrain = s.get("terrain")
        aoe_info = _spell_aoe_info(spell_slug, spell)
        aoe_shape = str(aoe_info.get("shape") or "").strip().lower() if isinstance(aoe_info, dict) else ""
        is_self_origin_aoe = bool(
            aoe_info is not None
            and (
                self_range
                or aoe_shape in ("cone", "emanation")
                or spell_slug in ("thunderwave", "burning-hands", "spirit-guardians")
            )
        )
        aoe_origin: dict[str, int] | None = None
        explicit_aoe_origin = False
        blocked_aoe_targets: list[str] = []

        if aoe_info is not None:
            if is_self_origin_aoe:
                aoe_origin = copy.deepcopy(actor["position"])
            else:
                raw_origin = intent.get("origin")
                if raw_origin is None:
                    raw_origin = intent.get("target_point")
                if raw_origin is None:
                    raw_origin = intent.get("point")
                if raw_origin is not None:
                    parsed_origin = _parse_point(raw_origin)
                    if parsed_origin is None:
                        return _invalid(
                            copy.deepcopy(state),
                            f"Spell '{spell_slug}' AoE origin must be {{x, y}} with integer coordinates.",
                            rules,
                        )
                    if not in_bounds(parsed_origin, s.get("bounds")):
                        b = s["bounds"]
                        return _invalid(
                            copy.deepcopy(state),
                            f"AoE origin ({parsed_origin['x']}, {parsed_origin['y']}) for '{spell_slug}' is outside battlefield bounds "
                            f"[x: {b['min_x']}..{b['max_x']}, y: {b['min_y']}..{b['max_y']}].",
                            rules,
                        )
                    if is_wall(parsed_origin, terrain):
                        return _invalid(
                            copy.deepcopy(state),
                            f"AoE origin ({parsed_origin['x']}, {parsed_origin['y']}) for '{spell_slug}' is blocked by a wall.",
                            rules,
                        )
                    if parsed_origin != actor["position"] and not has_line_of_sight(actor["position"], parsed_origin, terrain):
                        return _invalid(
                            copy.deepcopy(state),
                            f"No line of sight to AoE origin ({parsed_origin['x']}, {parsed_origin['y']}) for '{spell_slug}': blocked by a wall.",
                            rules,
                        )
                    aoe_origin = parsed_origin
                    explicit_aoe_origin = True
                elif target_ids:
                    for tid in target_ids:
                        t_pos = s["combatants"][tid]["position"]
                        if tid == actor_id or has_line_of_sight(actor["position"], t_pos, terrain):
                            aoe_origin = copy.deepcopy(t_pos)
                            break
                    if aoe_origin is None:
                        return _invalid(
                            copy.deepcopy(state),
                            f"No line of sight to any target for '{spell_slug}': blocked by a wall.",
                            rules,
                        )

            if terrain and aoe_origin is not None and target_ids:
                kept_targets: list[str] = []
                for tid in target_ids:
                    t_pos = s["combatants"][tid]["position"]
                    if has_line_of_sight(aoe_origin, t_pos, terrain):
                        kept_targets.append(tid)
                    else:
                        blocked_aoe_targets.append(tid)
                target_ids = kept_targets
        else:
            if terrain and target_ids:
                for tid in target_ids:
                    if tid == actor_id:
                        continue
                    t_pos = s["combatants"][tid]["position"]
                    if not has_line_of_sight(actor["position"], t_pos, terrain):
                        return _invalid(
                            copy.deepcopy(state),
                            f"No line of sight to '{tid}' for '{spell_slug}': blocked by a wall.",
                            rules,
                        )

        # Concentration handling (drop previous concentration first!).
        is_conc = bool(spell.get("concentration", False))
        if is_conc:
            if actor.get("concentration"):
                _drop_concentration(s, actor_id, tracker, reason="new_concentration_spell")
            actor["concentration"] = {"spell": spell_slug, "targets": list(target_ids)}
            tracker.emit("concentration_started", actor_id=actor_id, spell=spell_slug, targets=list(target_ids))

        coverage = str(spell.get("coverage") or "narrative")
        unmodeled = list((spell.get("provenance") or {}).get("unmodeled_clauses") or [])
        resolution = str(spell.get("resolution") or "utility").lower()
        scaling = spell.get("scaling") or {}
        upcast = max(0, slot_level - spell_level) if spell_level >= 1 else 0
        caster_level = int(actor.get("level", 1))
        spell_ab = strip_prefix(sc.get("ability") or "int")
        spell_mod = int(actor["ability_mods"].get(spell_ab, 0))
        spell_dc = int(sc.get("spell_save_dc", 8 + int(actor.get("proficiency_bonus", 2)) + spell_mod))
        spell_atk = int(sc.get("spell_attack_bonus", int(actor.get("proficiency_bonus", 2)) + spell_mod))

        has_mechanics = bool(
            spell.get("damage")
            or spell.get("healing")
            or spell.get("temp_hp")
            or spell.get("conditions_applied")
            or spell.get("buffs")
            or spell.get("special_effect")
        )

        # Honest narrative fallback!
        if coverage == "narrative" or (resolution == "utility" and not has_mechanics):
            tracker.emit(
                "spell_cast_narrative",
                actor_id=actor_id,
                spell=spell_slug,
                slot_level=slot_level,
                target_ids=target_ids,
                coverage=coverage,
                unmodeled_clauses=unmodeled,
            )
            tracker.hook(
                f"{actor['name']} casts {spell.get('name', spell_slug)} (slot {slot_level}) — narrative spell resolved by table/GM."
            )
            s["rng"] = rng.to_dict()
            return {
                "status": "ok",
                "error": None,
                "events": tracker.events,
                "state_diff": tracker.build_diff(s),
                "state": s,
                "legal_actions": compute_legal_actions(s, rules),
                "narrative_hooks": tracker.hooks,
            }

        spell_cast_extra: dict[str, Any] = {}
        if aoe_origin is not None and (explicit_aoe_origin or terrain is not None):
            spell_cast_extra["aoe_origin"] = copy.deepcopy(aoe_origin)

        tracker.emit(
            "spell_cast",
            actor_id=actor_id,
            spell=spell_slug,
            slot_level=slot_level,
            target_ids=target_ids,
            coverage=coverage,
            unmodeled_clauses=unmodeled,
            **spell_cast_extra,
        )

        spell_name = str(spell.get("name") or spell_slug)

        for b_tid in blocked_aoe_targets:
            b_name = s["combatants"][b_tid]["name"]
            tracker.emit(
                "spell_target_blocked",
                actor_id=actor_id,
                target_id=b_tid,
                spell=spell_slug,
                reason="total_cover",
                origin=copy.deepcopy(aoe_origin),
            )
            tracker.hook(f"{b_name} has total cover from {spell_name} and is unaffected.")

        # 1. Teleport (Misty Step).
        buffs = list(spell.get("buffs") or [])
        for buff in buffs:
            if buff.get("kind") == "teleport":
                dest = intent.get("destination")
                to_pos = _parse_point(dest)
                if to_pos is None:
                    return _invalid(
                        copy.deepcopy(state),
                        f"Spell '{spell_slug}' requires 'destination': {{x, y}} with integer coordinates.",
                        rules,
                    )
                max_d = int(buff.get("max_distance_ft", 30))
                if distance_ft(actor["position"], to_pos) > max_d:
                    return _invalid(
                        copy.deepcopy(state),
                        f"Teleport destination exceeds {max_d} ft for '{spell_slug}'.",
                        rules,
                    )
                bounds = s.get("bounds")
                if not in_bounds(to_pos, bounds):
                    return _invalid(
                        copy.deepcopy(state),
                        f"Teleport destination ({to_pos['x']}, {to_pos['y']}) is outside battlefield bounds "
                        f"[x: {bounds['min_x']}..{bounds['max_x']}, y: {bounds['min_y']}..{bounds['max_y']}].",
                        rules,
                    )
                if is_wall(to_pos, s.get("terrain")):
                    return _invalid(
                        copy.deepcopy(state),
                        f"Teleport destination ({to_pos['x']}, {to_pos['y']}) is blocked by a wall.",
                        rules,
                    )
                if not has_line_of_sight(actor["position"], to_pos, s.get("terrain")):
                    return _invalid(
                        copy.deepcopy(state),
                        f"Teleport destination ({to_pos['x']}, {to_pos['y']}) for '{spell_slug}' has no line of sight: blocked by a wall.",
                        rules,
                    )
                blocker_id = square_occupant(s, actor_id, to_pos)
                if blocker_id is not None and distance_ft(actor["position"], to_pos) > 0:
                    blocker_name = str(s["combatants"][blocker_id].get("name") or blocker_id)
                    return _invalid(
                        copy.deepcopy(state),
                        f"Teleport destination ({to_pos['x']}, {to_pos['y']}) is occupied by {blocker_name}.",
                        rules,
                    )
                old_pos = copy.deepcopy(actor["position"])
                actor["position"] = to_pos
                tracker.emit("teleported", actor_id=actor_id, spell=spell_slug, from_pos=old_pos, to_pos=to_pos)
                tracker.hook(f"{actor['name']} teleports via {spell_name} to ({to_pos['x']}, {to_pos['y']}).")

        # 2. Healing spells (cure-wounds, healing-word, mass-healing-word, mass-cure-wounds, heal, aid).
        healing_ir = spell.get("healing")
        if isinstance(healing_ir, dict):
            h_dice = healing_ir.get("dice")
            h_flat = int(healing_ir.get("flat") or 0)
            add_mod = bool(healing_ir.get("add_spell_mod", False))
            if h_dice:
                scaled_h = scale_dice_expr(str(h_dice), scaling.get("slot_healing_per_level"), upcast)
                h_res = rng.roll_dice_expr(scaled_h, crit=False)
                heal_amt = int(h_res["total"]) + h_flat + (spell_mod if add_mod else 0)
            else:
                heal_amt = h_flat + (spell_mod if add_mod else 0)
            heal_amt = max(0, heal_amt)

            ssv3 = int(s.get("state_shape_version", 1)) >= 3
            actor_feats = {strip_prefix(str(f)) for f in (actor.get("features") or [])}
            dol_bonus = (
                2 + slot_level
                if (ssv3 and "life-disciple-of-life" in actor_feats and slot_level >= 1 and heal_amt > 0)
                else 0
            )
            healed_other_living = False

            for tid in (target_ids or [actor_id]):
                t = s["combatants"][tid]
                hp_before = int(t["hp"])
                if not t.get("alive"):
                    tracker.emit(
                        "healing_applied",
                        source_id=actor_id,
                        target_id=tid,
                        spell=spell_slug,
                        amount=0,
                        hp_before=hp_before,
                        hp_after=hp_before,
                        revived=False,
                        reason="target_dead",
                    )
                    tracker.hook(
                        f"{actor['name']} casts {spell_name}, but it has no effect on {t['name']} (dead)."
                    )
                    continue
                total_heal = heal_amt + dol_bonus
                revived = False
                if hp_before == 0 and t.get("kind") == "pc" and total_heal > 0:
                    # 2024 RAW: healing revives 0-HP living (unconscious) PC, removes unconscious, LEAVES prone!
                    _remove_condition(t, "unconscious")
                    t["death_saves"] = {"successes": 0, "failures": 0}
                    revived = True
                t["hp"] = min(int(t["hp_max"]), hp_before + total_heal)
                if tid != actor_id and total_heal > 0:
                    healed_other_living = True
                dol_fields = {"disciple_of_life_bonus": dol_bonus} if dol_bonus > 0 else {}
                tracker.emit(
                    "healing_applied",
                    source_id=actor_id,
                    target_id=tid,
                    spell=spell_slug,
                    amount=total_heal,
                    hp_before=hp_before,
                    hp_after=t["hp"],
                    revived=revived,
                    **dol_fields,
                )
                revive_note = " (regains consciousness, remains Prone)" if revived else ""
                dol_note = f" (+{dol_bonus} Disciple of Life)" if dol_bonus > 0 else ""
                tracker.hook(
                    f"{actor['name']} casts {spell_name}, restoring {total_heal} HP to {t['name']}{revive_note}{dol_note}."
                )

            # Life Domain L6+ Blessed Healer: immediately self-heal 2 + slot_level when healing another living creature with a slot.
            if (
                ssv3
                and "life-blessed-healer" in actor_feats
                and slot_level >= 1
                and actor.get("alive")
                and healed_other_living
            ):
                bh_amt = 2 + slot_level
                self_hp_before = int(actor["hp"])
                actor["hp"] = min(int(actor["hp_max"]), self_hp_before + bh_amt)
                tracker.emit(
                    "healing_applied",
                    source_id=actor_id,
                    target_id=actor_id,
                    feature="blessed_healer",
                    spell=spell_slug,
                    slot_level=slot_level,
                    amount=bh_amt,
                    hp_before=self_hp_before,
                    hp_after=actor["hp"],
                    revived=False,
                )
                tracker.hook(f"Blessed Healer restores {bh_amt} HP to {actor['name']}.")

        # 3. Temp HP spells (false-life).
        temp_hp_ir = spell.get("temp_hp")
        if isinstance(temp_hp_ir, dict):
            t_dice = temp_hp_ir.get("dice")
            t_flat = int(temp_hp_ir.get("flat") or 0) + (5 * upcast if spell_slug == "false-life" else 0)
            t_amt = (int(rng.roll_dice_expr(str(t_dice), crit=False)["total"]) if t_dice else 0) + t_flat
            t_amt = max(0, t_amt)
            for tid in (target_ids or [actor_id]):
                t = s["combatants"][tid]
                t["temp_hp"] = max(int(t.get("temp_hp", 0)), t_amt)
                tracker.emit("temp_hp_applied", target_id=tid, spell=spell_slug, amount=t_amt, temp_hp_after=t["temp_hp"])
                tracker.hook(
                    f"{actor['name']} casts {spell_name}, granting Temporary HP to {t['name']}."
                )

        # 4. Auto-hit multi-projectile spells (`magic-missile`).
        special = spell.get("special_effect") or {}
        dmg_list = list(spell.get("damage") or [])
        if resolution == "auto_hit" and dmg_list:
            proj_base = int(
                (special.get("projectiles_base") if isinstance(special, dict) else None)
                or (3 if spell_slug == "magic-missile" else 1)
            )
            proj_per_lvl = int(scaling.get("slot_projectiles_per_level") or (1 if spell_slug == "magic-missile" else 0))
            num_proj = proj_base + upcast * proj_per_lvl
            if not target_ids:
                return _invalid(copy.deepcopy(state), f"Spell '{spell_slug}' requires at least one target.", rules)

            # Being targeted by Magic Missile is a Shield trigger (#33): the target may react before the darts land.
            if spell_slug == "magic-missile":
                for tid in dict.fromkeys(target_ids):
                    t = s["combatants"][tid]
                    if t.get("side") != actor.get("side") and not _has_active_shield(t, spell_slug):
                        _maybe_reactive_shield(
                                s, tid, actor_id, {}, 0, 0, rules, tracker, trigger="targeted_by_magic_missile"
                        )

            pre_auto_hooks = len(tracker.hooks)
            dmg_by_target: dict[str, int] = {}
            dtype_by_target: dict[str, str] = {}
            blocked_targets: list[str] = []

            for p_idx in range(num_proj):
                tid = target_ids[p_idx % len(target_ids)]
                t = s["combatants"][tid]
                if _has_active_shield(t, spell_slug):
                    tracker.emit("spell_blocked", actor_id=actor_id, target_id=tid, spell=spell_slug, reason="shield")
                    if tid not in blocked_targets:
                        blocked_targets.append(tid)
                    continue
                parts_to_apply: list[dict[str, Any]] = []
                for d in dmg_list:
                    res_d = rng.roll_dice_expr(str(d["dice"]), crit=False)
                    amt = max(0, int(res_d["total"]) + (spell_mod if d.get("add_spell_mod") else 0))
                    parts_to_apply.append({"amount": amt, "type": strip_prefix(d["type"])})
                eff_proj = _apply_damage_parts(s, tid, parts_to_apply, rules, rng, tracker, source_id=actor_id)
                dmg_by_target[tid] = dmg_by_target.get(tid, 0) + eff_proj
                if tid not in dtype_by_target and parts_to_apply:
                    dtype_by_target[tid] = "/".join(dict.fromkeys(p["type"] for p in parts_to_apply))

            auto_hooks: list[str] = []
            for tid in blocked_targets:
                t_name = s["combatants"][tid]["name"]
                auto_hooks.append(f"{t_name}'s Shield blocks {spell_name}!")
            for tid, total_eff in dmg_by_target.items():
                t_name = s["combatants"][tid]["name"]
                dtype_txt = dtype_by_target.get(tid, "force")
                auto_hooks.append(
                    f"{actor['name']} casts {spell_name} at {t_name} for {total_eff} {dtype_txt} damage."
                )
            tracker.hooks[pre_auto_hooks:pre_auto_hooks] = auto_hooks

        # A Self-origin spell with no one else to affect (#67, #81 MF1): legal, and nothing to roll.
        elif resolution in ("attack", "save") and castable_without_target and not target_ids:
            if not blocked_aoe_targets:
                if harmful_self_area:
                    tracker.hook(f"{actor['name']} casts {spell_name}; no other creature is in its area.")
                else:
                    tracker.hook(f"{actor['name']} casts {spell_name}; it has no other creature to affect yet.")

        # 5. Spell attack resolution (`fire-bolt`, `scorching-ray`, `eldritch-blast`, `guiding-bolt`, `ray-of-frost`, etc.).
        elif resolution == "attack":
            if not target_ids:
                return _invalid(copy.deepcopy(state), f"Spell '{spell_slug}' requires at least one target.", rules)
            atk_kind = str(spell.get("attack_kind") or "ranged").lower()
            is_ranged = atk_kind != "melee"

            if spell_slug == "eldritch-blast":
                num_rays = _eldritch_blast_beams(caster_level)
            elif spell_slug == "scorching-ray" or (isinstance(special, dict) and special.get("projectiles_base")):
                base_p = int(special.get("projectiles_base", 3)) if isinstance(special, dict) else 3
                per_p = int(scaling.get("slot_projectiles_per_level") or 1)
                num_rays = base_p + upcast * per_p
            else:
                num_rays = len(target_ids)

            for r_idx in range(num_rays):
                tid = target_ids[r_idx % len(target_ids)]
                t = s["combatants"][tid]
                dist = distance_ft(actor["position"], t["position"])
                mode, adv_src, disadv_src, consume = _gather_attack_roll_mode(
                    s,
                    actor,
                    t,
                    rules,
                    is_ranged=is_ranged,
                    dist=dist,
                    range_normal_ft=spell.get("range_ft"),
                    advantage_override=bool(intent.get("advantage_override", False)),
                    disadvantage_override=bool(intent.get("disadvantage_override", False)),
                )
                _consume_effects(s, consume)
                d20 = rng.roll_d20(mode)
                exh_pen = -2 * _exhaustion_level(actor)
                bb_bonus, bb_details = _roll_bless_bane(actor, "attack", rng)
                total_to_hit = int(d20["chosen"]) + spell_atk + exh_pen + bb_bonus
                cover_kind, cover_bonus = compute_cover(actor["position"], t["position"], s.get("terrain"))
                t_ac = _effective_ac(t) + cover_bonus

                if d20["nat1"]:
                    hit, crit = False, False
                elif d20["nat20"]:
                    hit, crit = True, True
                else:
                    hit = total_to_hit >= t_ac
                    crit = False
                if hit:  # Shield (Reaction) can turn the hit into a miss (#33)
                    shielded_ac = _maybe_reactive_shield(s, tid, actor_id, d20, total_to_hit, t_ac, rules, tracker)
                    if shielded_ac is not None:
                        t_ac = shielded_ac
                        hit = total_to_hit >= t_ac

                if hit and not crit and dist <= 5 and _has_modifier_flag(t, "auto_crit_within_5ft", rules):
                    crit = True

                cover_fields: dict[str, Any] = {}
                if cover_bonus > 0 and cover_kind:
                    cover_fields["cover"] = cover_kind
                    cover_fields["cover_ac_bonus"] = cover_bonus

                tracker.emit(
                    "spell_attack_roll",
                    actor_id=actor_id,
                    target_id=tid,
                    spell=spell_slug,
                    mode=mode,
                    rolls=d20["rolls"],
                    chosen=d20["chosen"],
                    attack_bonus=spell_atk,
                    exhaustion_penalty=exh_pen,
                    bonus_dice=bb_details,
                    total=total_to_hit,
                    target_ac=t_ac,
                    hit=hit,
                    crit=crit,
                    **cover_fields,
                )

                cover_txt = f" [{_COVER_LABELS[cover_kind]} +{cover_bonus} AC]" if (cover_bonus > 0 and cover_kind) else ""

                if not hit:
                    tracker.hook(
                        f"{actor['name']} misses {t['name']} with {spell_name} ({total_to_hit} vs AC {t_ac}{cover_txt})."
                    )
                    continue

                if hit:
                    parts_to_apply = []
                    for d_idx, d in enumerate(dmg_list):
                        base_d = str(d["dice"])
                        if spell_level == 0 and spell_slug != "eldritch-blast":
                            eff_d = _cantrip_scaled_dice(base_d, scaling, caster_level)
                        elif d_idx == 0 and upcast > 0 and scaling.get("slot_damage_per_level"):
                            eff_d = scale_dice_expr(base_d, scaling["slot_damage_per_level"], upcast)
                        else:
                            eff_d = base_d
                        res_d = rng.roll_dice_expr(eff_d, crit=crit)
                        amt = max(0, int(res_d["total"]) + (spell_mod if d.get("add_spell_mod") else 0))
                        parts_to_apply.append({"amount": amt, "type": strip_prefix(d["type"])})
                    crit_txt = " (Critical Hit!)" if crit else ""
                    if parts_to_apply:
                        pre_hit_hooks = len(tracker.hooks)
                        eff_dmg = _apply_damage_parts(
                            s, tid, parts_to_apply, rules, rng, tracker, source_id=actor_id, is_crit=crit
                        )
                        dtype_txt = "/".join(dict.fromkeys(p["type"] for p in parts_to_apply))
                        tracker.hooks.insert(
                            pre_hit_hooks,
                            f"{actor['name']} hits {t['name']} with {spell_name} "
                            f"({total_to_hit} vs AC {t_ac}{cover_txt}){crit_txt} for {eff_dmg} {dtype_txt} damage.",
                        )
                    else:
                        tracker.hook(
                            f"{actor['name']} hits {t['name']} with {spell_name} "
                            f"({total_to_hit} vs AC {t_ac}{cover_txt}){crit_txt}."
                        )

                    # On-hit buffs/effects (e.g., guiding-bolt next_attack_advantage, ray-of-frost slow).
                    slow_applied_ft: int | None = None
                    for buff in buffs:
                        bkind = buff.get("kind")
                        if bkind == "next_attack_advantage":
                            t["active_effects"].append(
                                {
                                    "id": _next_effect_id(s, "guiding_bolt"),
                                    "kind": "guiding_bolt_advantage",
                                    "source_id": actor_id,
                                    "target_id": tid,
                                    "value": True,
                                    "expires": "end_of_source_next_turn",
                                    "created_round": s["round"],
                                    "concentration_spell": spell_slug if is_conc else None,
                                }
                            )
                            tracker.hook(f"Next attack roll against {t['name']} has Advantage.")
                        elif bkind == "speed_penalty_ft":
                            pen_ft = int(buff.get("value", 10))
                            t["active_effects"].append(
                                {
                                    "id": _next_effect_id(s, "slow"),
                                    "kind": "slow",
                                    "source_id": actor_id,
                                    "target_id": tid,
                                    "value": pen_ft,
                                    "expires": "start_of_source_next_turn",
                                    "created_round": s["round"],
                                    "concentration_spell": None,
                                }
                            )
                            slow_applied_ft = pen_ft
                    if spell_slug == "ray-of-frost" and slow_applied_ft is None:
                        t["active_effects"].append(
                            {
                                "id": _next_effect_id(s, "slow"),
                                "kind": "slow",
                                "source_id": actor_id,
                                "target_id": tid,
                                "value": 10,
                                "expires": "start_of_source_next_turn",
                                "created_round": s["round"],
                                "concentration_spell": None,
                            }
                        )
                        slow_applied_ft = 10
                    if slow_applied_ft is not None:
                        tracker.hook(
                            f"{t['name']}'s speed is reduced by {slow_applied_ft} ft until start of {actor['name']}'s next turn."
                        )
                    for cond in spell.get("conditions_applied") or []:
                        if isinstance(cond, dict) and cond.get("trigger") in ("on_hit", "auto", None):
                            _apply_condition(
                                s,
                                tid,
                                strip_prefix(cond["condition"]),
                                rules,
                                tracker,
                                source_id=actor_id,
                                duration=str(cond.get("duration", "end_of_source_next_turn")),
                                concentration_spell=spell_slug if is_conc else None,
                            )

        # 6. Saving throw spells (`fireball`, `lightning-bolt`, `sacred-flame`, `hold-person`, `bane`, `vicious-mockery`).
        elif resolution == "save":
            if not target_ids:
                if not (blocked_aoe_targets or explicit_aoe_origin):
                    return _invalid(copy.deepcopy(state), f"Spell '{spell_slug}' requires at least one target.", rules)
            save_ir = spell.get("save") or {}
            save_ab = strip_prefix(save_ir.get("ability") or "dex")
            on_success = str(save_ir.get("on_success") or "none").lower()
            save_source_pos = aoe_origin if aoe_origin is not None else actor["position"]
            ignore_cov = spell_slug in COVER_IGNORING_SPELLS

            # Roll damage once across AoE targets.
            rolled_parts: list[dict[str, Any]] = []
            if target_ids:
                for d_idx, d in enumerate(dmg_list):
                    base_d = str(d["dice"])
                    if spell_level == 0:
                        eff_d = _cantrip_scaled_dice(base_d, scaling, caster_level)
                    elif d_idx == 0 and upcast > 0 and scaling.get("slot_damage_per_level"):
                        eff_d = scale_dice_expr(base_d, scaling["slot_damage_per_level"], upcast)
                    else:
                        eff_d = base_d
                    res_d = rng.roll_dice_expr(eff_d, crit=False)
                    amt = max(0, int(res_d["total"]) + (spell_mod if d.get("add_spell_mod") else 0))
                    rolled_parts.append({"amount": amt, "type": strip_prefix(d["type"]), "dice": eff_d, "rolls": res_d["rolls"]})

            for tid in target_ids:
                t_name = s["combatants"][tid]["name"]
                save_ev = _roll_save(
                    s,
                    tid,
                    save_ab,
                    spell_dc,
                    rules,
                    rng,
                    tracker,
                    reason=spell_slug,
                    source_pos=save_source_pos,
                    ignore_cover=ignore_cov,
                )
                cov_save = save_ev.get("cover_save_bonus")
                cov_kind = save_ev.get("cover")
                if cov_save and cov_kind in _COVER_LABELS:
                    cov_note = f" [{_COVER_LABELS[cov_kind]} +{cov_save} DEX save]"
                elif save_ev.get("cover_ignored"):
                    cov_note = " [cover ignored]"
                else:
                    cov_note = ""
                save_dmg_hook_emitted = False
                if rolled_parts:
                    if not save_ev["success"]:
                        adj = [{"amount": p["amount"], "type": p["type"]} for p in rolled_parts]
                    elif on_success == "half":
                        adj = [{"amount": p["amount"] // 2, "type": p["type"]} for p in rolled_parts]
                    else:
                        adj = []
                    if adj:
                        pre_save_hooks = len(tracker.hooks)
                        eff = _apply_damage_parts(s, tid, adj, rules, rng, tracker, source_id=actor_id)
                        dtype_txt = "/".join(dict.fromkeys(p["type"] for p in adj))
                        tracker.hooks.insert(
                            pre_save_hooks,
                            f"{actor['name']} casts {spell_name} (DC {spell_dc} {save_ab.upper()}{cov_note}): "
                            f"{t_name} {'succeeds' if save_ev['success'] else 'fails'} and takes {eff} {dtype_txt} damage.",
                        )
                        save_dmg_hook_emitted = True
                    else:
                        tracker.hook(
                            f"{actor['name']} casts {spell_name} (DC {spell_dc} {save_ab.upper()}{cov_note}): "
                            f"{t_name} succeeds and takes no damage."
                        )
                        save_dmg_hook_emitted = True

                applied_notes: list[str] = []
                if not save_ev["success"]:
                    for cond in spell.get("conditions_applied") or []:
                        c_slug = strip_prefix(cond.get("condition") if isinstance(cond, dict) else str(cond))
                        dur = str(cond.get("duration", "save_ends")) if isinstance(cond, dict) else "save_ends"
                        rep_ab = cond.get("repeat_save_ability", save_ab) if isinstance(cond, dict) else save_ab
                        if _apply_condition(
                            s,
                            tid,
                            c_slug,
                            rules,
                            tracker,
                            source_id=actor_id,
                            duration=dur,
                            save_ability=strip_prefix(rep_ab) if rep_ab else None,
                            save_dc=spell_dc,
                            concentration_spell=spell_slug if is_conc else None,
                        ):
                            applied_notes.append(c_slug)
                    for buff in buffs:
                        if buff.get("kind") == "roll_bonus_dice" and int(buff.get("sign", 1)) < 0:
                            dice_v = str(buff.get("dice", "1d4"))
                            s["combatants"][tid]["active_effects"].append(
                                {
                                    "id": _next_effect_id(s, "bane"),
                                    "kind": "bane",
                                    "source_id": actor_id,
                                    "target_id": tid,
                                    "value": dice_v,
                                    "applies_to": list(buff.get("applies_to") or ["attack", "save"]),
                                    "expires": "concentration" if is_conc else "spell_duration",
                                    "created_round": s["round"],
                                    "concentration_spell": spell_slug if is_conc else None,
                                }
                            )
                            applied_notes.append(f"-{dice_v} to attacks/saves")
                    if spell_slug == "vicious-mockery":
                        s["combatants"][tid]["active_effects"].append(
                            {
                                "id": _next_effect_id(s, "sap"),
                                "kind": "sap",
                                "source_id": actor_id,
                                "target_id": tid,
                                "value": True,
                                "expires": "end_of_target_next_turn",
                                "created_round": s["round"],
                                "concentration_spell": None,
                            }
                        )
                        applied_notes.append("Disadvantage on next attack")

                if not save_dmg_hook_emitted:
                    if not save_ev["success"]:
                        eff_txt = f" ({', '.join(applied_notes)})" if applied_notes else ""
                        tracker.hook(
                            f"{actor['name']} casts {spell_name} (DC {spell_dc} {save_ab.upper()}): "
                            f"{t_name} fails{eff_txt}."
                        )
                    else:
                        tracker.hook(
                            f"{actor['name']} casts {spell_name} (DC {spell_dc} {save_ab.upper()}): "
                            f"{t_name} succeeds and resists."
                        )
                elif applied_notes:
                    tracker.hook(f"{t_name} is affected by {spell_name}: {', '.join(applied_notes)}.")

        # 7. Auto-apply / Buff spells (`shield`, `mage-armor`, `shield-of-faith`, `barkskin`, `bless`, `longstrider`, `haste`).
        elif smite is not None:
            last_hit = actor["turn_resources"].pop("last_melee_hit")
            tid = target_ids[0]
            t = s["combatants"][tid]
            crit = bool(last_hit.get("crit"))
            per_lvl = scaling.get("slot_damage_per_level")
            exprs = [(str(d.get("dice") or "1"), strip_prefix(d.get("type") or "radiant")) for d in dmg_list]
            if per_lvl and exprs:
                exprs += [(str(per_lvl), exprs[0][1])] * upcast
            ctype = ""
            if t.get("kind") == "monster":
                m_rec = rules.get_monster(str(t.get("source_id") or "")) or {}
                ctype = str(m_rec.get("creature_type") or "").lower()
            bonus_txt = ""
            if smite.get("bonus_vs") and ctype in smite["bonus_vs"] and exprs:
                exprs.append((str(smite["bonus_dice"]), exprs[0][1]))
                bonus_txt = f" (+{smite['bonus_dice']} vs {ctype.title()})"
            parts = [
                {"amount": max(0, int(rng.roll_dice_expr(dx, crit=crit)["total"])), "type": dt} for dx, dt in exprs
            ]
            hook_idx = len(tracker.hooks)
            eff = _apply_damage_parts(s, tid, parts, rules, rng, tracker, source_id=actor_id, is_crit=crit)
            crit_txt = " (critical: dice doubled)" if crit else ""
            tracker.hooks.insert(
                hook_idx,
                f"{actor['name']} casts {spell_name} on {t['name']}{crit_txt}{bonus_txt} for {eff} "
                f"{exprs[0][1] if exprs else ''} damage.",
            )
        else:
            apply_targets = target_ids or [actor_id]
            bless_targets: list[str] = []
            bless_dice = "1d4"
            bless_sign = 1
            for tid in apply_targets:
                t = s["combatants"][tid]
                for buff in buffs:
                    bkind = buff.get("kind")
                    if bkind == "ac_bonus":
                        if spell_slug == "shield" or buff.get("blocks_spell"):
                            val = int(buff.get("value", 5))
                            t["active_effects"].append(
                                {
                                    "id": _next_effect_id(s, "shield"),
                                    "kind": "shield",
                                    "source_id": actor_id,
                                    "target_id": tid,
                                    "value": val,
                                    "blocks_spell": buff.get("blocks_spell", "magic-missile"),
                                    "expires": "start_of_source_next_turn",
                                    "created_round": s["round"],
                                    "concentration_spell": None,
                                }
                            )
                            tracker.hook(
                                f"{actor['name']} casts {spell_name} (+{val} AC until start of next turn)."
                            )
                        elif spell_slug == "haste":
                            ac_b = int(buff.get("value", 2))
                            t["active_effects"].append(
                                {
                                    "id": _next_effect_id(s, "haste"),
                                    "kind": "haste",
                                    "source_id": actor_id,
                                    "target_id": tid,
                                    "value": {"ac_bonus": ac_b, "speed_multiplier": 2},
                                    "expires": "concentration" if is_conc else "spell_duration",
                                    "created_round": s["round"],
                                    "concentration_spell": spell_slug if is_conc else None,
                                }
                            )
                            tracker.hook(
                                f"{actor['name']} casts {spell_name} on {t['name']} (+{ac_b} AC, doubled speed)."
                            )
                        else:
                            val = int(buff.get("value", 2))
                            t["active_effects"].append(
                                {
                                    "id": _next_effect_id(s, "shield_of_faith"),
                                    "kind": "shield_of_faith",
                                    "source_id": actor_id,
                                    "target_id": tid,
                                    "value": val,
                                    "expires": "concentration" if is_conc else str(buff.get("duration", "spell_duration")),
                                    "created_round": s["round"],
                                    "concentration_spell": spell_slug if is_conc else None,
                                }
                            )
                            tracker.hook(f"{actor['name']} casts {spell_name} on {t['name']} (+{val} AC).")
                    elif bkind == "ac_formula":
                        base_ac = int(buff.get("base", 13))
                        t["active_effects"].append(
                            {
                                "id": _next_effect_id(s, "mage_armor"),
                                "kind": "mage_armor",
                                "source_id": actor_id,
                                "target_id": tid,
                                "value": base_ac,
                                "expires": "spell_duration",
                                "created_round": s["round"],
                                "concentration_spell": None,
                            }
                        )
                        tracker.hook(
                            f"{actor['name']} casts {spell_name} on {t['name']} (AC {base_ac} + DEX)."
                        )
                    elif bkind == "ac_min":
                        val = int(buff.get("value", 17))
                        t["active_effects"].append(
                            {
                                "id": _next_effect_id(s, "barkskin"),
                                "kind": "barkskin",
                                "source_id": actor_id,
                                "target_id": tid,
                                "value": val,
                                "expires": "spell_duration",
                                "created_round": s["round"],
                                "concentration_spell": spell_slug if is_conc else None,
                            }
                        )
                        tracker.hook(
                            f"{actor['name']} casts {spell_name} on {t['name']} (minimum AC {val})."
                        )
                    elif bkind == "roll_bonus_dice":
                        sign_val = int(buff.get("sign", 1))
                        ekind = "bless" if sign_val >= 0 else "bane"
                        dice_v = str(buff.get("dice", "1d4"))
                        t["active_effects"].append(
                            {
                                "id": _next_effect_id(s, ekind),
                                "kind": ekind,
                                "source_id": actor_id,
                                "target_id": tid,
                                "value": dice_v,
                                "applies_to": list(buff.get("applies_to") or ["attack", "save"]),
                                "expires": "concentration" if is_conc else "spell_duration",
                                "created_round": s["round"],
                                "concentration_spell": spell_slug if is_conc else None,
                            }
                        )
                        bless_targets.append(t["name"])
                        bless_dice = dice_v
                        bless_sign = 1 if sign_val >= 0 else -1
                    elif bkind == "speed_bonus_ft":
                        val = int(buff.get("value", 10))
                        t["active_effects"].append(
                            {
                                "id": _next_effect_id(s, "longstrider"),
                                "kind": "longstrider",
                                "source_id": actor_id,
                                "target_id": tid,
                                "value": val,
                                "expires": "spell_duration",
                                "created_round": s["round"],
                                "concentration_spell": None,
                            }
                        )
                        tracker.hook(
                            f"{actor['name']} casts {spell_name} on {t['name']} (+{val} ft speed)."
                        )
                    elif bkind == "speed_multiplier" and not any(
                        e.get("kind") == "haste" and e.get("source_id") == actor_id
                        for e in t.get("active_effects", [])
                    ):
                        mult_v = int(buff.get("value", 2))
                        t["active_effects"].append(
                            {
                                "id": _next_effect_id(s, "haste"),
                                "kind": "haste",
                                "source_id": actor_id,
                                "target_id": tid,
                                "value": {"ac_bonus": 0, "speed_multiplier": mult_v},
                                "expires": "concentration" if is_conc else "spell_duration",
                                "created_round": s["round"],
                                "concentration_spell": spell_slug if is_conc else None,
                            }
                        )
                        tracker.hook(
                            f"{actor['name']} casts {spell_name} on {t['name']} (speed x{mult_v})."
                        )
                for cond in spell.get("conditions_applied") or []:
                    c_slug = strip_prefix(cond.get("condition") if isinstance(cond, dict) else str(cond))
                    if _apply_condition(
                        s,
                        tid,
                        c_slug,
                        rules,
                        tracker,
                        source_id=actor_id,
                        duration=str(cond.get("duration", "concentration")) if isinstance(cond, dict) else "indefinite",
                        concentration_spell=spell_slug if is_conc else None,
                    ):
                        tracker.hook(f"{actor['name']} casts {spell_name} on {t['name']} ({c_slug}).")
            if bless_targets:
                sign_sym = "+" if bless_sign >= 0 else "-"
                tracker.hook(
                    f"{actor['name']} casts {spell_name} on {', '.join(bless_targets)} "
                    f"({sign_sym}{bless_dice} to attacks and saves)."
                )
            if dmg_list and resolution == "auto_apply":
                # Safety net (#6): this spell has damage the engine doesn't apply (e.g. a per-hit rider).
                # Say so, rather than spending the slot silently.
                tracker.emit("spell_damage_unresolved", actor_id=actor_id, spell=spell_slug, damage=copy.deepcopy(dmg_list))
                tracker.hook(f"{actor['name']} casts {spell_name}; its damage isn't modeled by the engine (resolve at the table).")
            if not tracker.hooks:
                tracker.hook(f"{actor['name']} casts {spell_name}.")

        s["rng"] = rng.to_dict()
        _update_encounter_status(s, tracker)
        if resolution == "attack":
            spell_atk_events = [e for e in tracker.events if e.get("type") == "spell_attack_roll"]
            if (
                spell_atk_events
                and not any(e.get("hit") for e in spell_atk_events)
                and actor_id == active_id
                and actor.get("heroic_inspiration") is True
                and s.get("status") != "ended"
            ):
                last_ev = spell_atk_events[-1]
                ev_tid = str(last_ev["target_id"])
                s["pending_reroll"] = {
                    "actor_id": actor_id,
                    "kind": "spell_attack",
                    "bonus_dice": _action_bonus_dice(tracker.events, actor_id, "spell_attack_roll"),
                    "intent": copy.deepcopy(intent),
                    "previous_d20": int(last_ev["chosen"]),
                    "previous_total": int(last_ev["total"]),
                    "target_ac": int(last_ev["target_ac"]),
                    "target_id": ev_tid,
                    "target_name": str(s["combatants"][ev_tid]["name"]),
                    "label": spell_name,
                    "rollback_combatants": pre_action_combatants,
                }
        return {
            "status": "ok",
            "error": None,
            "events": tracker.events,
            "state_diff": tracker.build_diff(s),
            "state": s,
            "legal_actions": compute_legal_actions(s, rules),
            "narrative_hooks": tracker.hooks,
        }

    # ------------------------------------------------------------------
    # 5. `dodge`, `dash`, `disengage`, `help`
    # ------------------------------------------------------------------
    if intent_type in ("dodge", "dash", "disengage", "help"):
        s["pending_reroll"] = None
        if actor_id != active_id:
            return _invalid(copy.deepcopy(state), f"It is {active_id}'s turn, not {actor_id}'s.", rules)
        if not actor.get("alive"):
            return _invalid(copy.deepcopy(state), f"{actor_id} is not alive.", rules)
        if _has_modifier_flag(actor, "cannot_act", rules):
            return _invalid(copy.deepcopy(state), f"{actor_id} is Incapacitated and cannot take actions.", rules)
        if not _consume_non_magic_action(actor["turn_resources"]):
            return _invalid(copy.deepcopy(state), f"{actor_id} has no Action remaining this turn.", rules)

        if intent_type == "dodge":
            actor["active_effects"].append(
                {
                    "id": _next_effect_id(s, "dodge"),
                    "kind": "dodge",
                    "source_id": actor_id,
                    "target_id": actor_id,
                    "value": True,
                    "expires": "start_of_source_next_turn",
                    "created_round": s["round"],
                    "concentration_spell": None,
                }
            )
            tracker.emit("action_dodge", actor_id=actor_id)
            tracker.hook(f"{actor['name']} takes the Dodge action.")

        elif intent_type == "dash":
            added = _effective_speed(actor, rules)
            actor["movement_remaining_ft"] = int(actor.get("movement_remaining_ft", 0)) + added
            tracker.emit("action_dash", actor_id=actor_id, added_movement_ft=added, movement_remaining_ft=actor["movement_remaining_ft"])
            tracker.hook(f"{actor['name']} Dashes, gaining +{added} ft of movement.")

        elif intent_type == "disengage":
            actor["turn_resources"]["disengaged"] = True
            tracker.emit("action_disengage", actor_id=actor_id)
            tracker.hook(f"{actor['name']} takes the Disengage action.")

        elif intent_type == "help":
            target_id = intent.get("target_id")
            ally_id = intent.get("ally_id")
            if target_id:
                tid = str(target_id)
                if tid not in s["combatants"]:
                    return _invalid(copy.deepcopy(state), f"Unknown Help target '{tid}'.", rules)
                s["combatants"][tid]["active_effects"].append(
                    {
                        "id": _next_effect_id(s, "help"),
                        "kind": "help",
                        "source_id": actor_id,
                        "target_id": tid,
                        "value": {"ally_id": str(ally_id) if ally_id else None},
                        "expires": "start_of_source_next_turn",
                        "created_round": s["round"],
                        "concentration_spell": None,
                    }
                )
            tracker.emit("action_help", actor_id=actor_id, ally_id=ally_id, target_id=target_id)
            tracker.hook(f"{actor['name']} takes the Help action.")

        s["rng"] = rng.to_dict()
        return {
            "status": "ok",
            "error": None,
            "events": tracker.events,
            "state_diff": tracker.build_diff(s),
            "state": s,
            "legal_actions": compute_legal_actions(s, rules),
            "narrative_hooks": tracker.hooks,
        }

    # ------------------------------------------------------------------
    # 6. `end_turn`
    # ------------------------------------------------------------------
    if intent_type == "end_turn":
        s["pending_reroll"] = None
        if intent.get("actor_id") and str(intent["actor_id"]) != active_id:
            return _invalid(copy.deepcopy(state), f"It is {active_id}'s turn, not {intent['actor_id']}'s.", rules)

        ending_id = active_id
        ending_actor = s["combatants"][ending_id]
        cur_round = int(s["round"])
        ending_actor["turn_resources"].pop("slot_spent_this_turn", None)  # #52: per turn only

        # 1. End-of-turn `save_ends` and `end_of_next_turn` conditions on ending actor.
        remaining_conds: list[Any] = []
        for cond in ending_actor.get("conditions", []):
            if isinstance(cond, dict):
                dur = str(cond.get("duration") or "")
                c_slug = strip_prefix(cond.get("condition", ""))
                if dur in ("end_of_next_turn", "end_of_target_next_turn"):
                    # Expires at the end of the target's turn (skip only if self-inflicted this round).
                    if cond.get("source_id") != ending_id or int(cond.get("created_round", 0)) < cur_round:
                        tracker.emit("condition_ended", target_id=ending_id, condition=c_slug, reason="duration_expired")
                        continue
                if dur == "save_ends" and cond.get("save_ability") and cond.get("save_dc"):
                    save_ev = _roll_save(
                        s,
                        ending_id,
                        str(cond["save_ability"]),
                        int(cond["save_dc"]),
                        rules,
                        rng,
                        tracker,
                        reason=f"end_turn_save_{c_slug}",
                    )
                    if save_ev["success"]:
                        tracker.emit("condition_ended", target_id=ending_id, condition=c_slug, reason="save_succeeded")
                        continue
            remaining_conds.append(cond)
        ending_actor["conditions"] = remaining_conds

        # 2. Expire `end_of_source_next_turn` effects created in an earlier round,
        #    plus `end_of_target_next_turn` effects on ending actor.
        for cid, comb in s["combatants"].items():
            kept_effs = []
            for eff in comb.get("active_effects", []):
                exp = eff.get("expires")
                created_r = int(eff.get("created_round", 0))
                if exp == "end_of_source_next_turn" and eff.get("source_id") == ending_id and created_r < cur_round:
                    tracker.emit("effect_expired", target_id=cid, kind=eff.get("kind"), source_id=ending_id)
                    continue
                if exp == "end_of_target_next_turn" and cid == ending_id:
                    tracker.emit("effect_expired", target_id=cid, kind=eff.get("kind"), source_id=eff.get("source_id"))
                    continue
                kept_effs.append(eff)
            comb["active_effects"] = kept_effs

        tracker.emit("turn_ended", actor_id=ending_id, round=cur_round)

        # #9: if the fight ended during this turn (e.g. a state whose status wasn't flipped yet), stop here:
        # don't advance active_index or round, and don't start another turn.
        _update_encounter_status(s, tracker)
        if s.get("status") == "ended":
            s["rng"] = rng.to_dict()
            return {
                "status": "ok",
                "error": None,
                "events": tracker.events,
                "state_diff": tracker.build_diff(s),
                "state": s,
                "legal_actions": compute_legal_actions(s, rules),
                "narrative_hooks": tracker.hooks,
            }

        # 3. Advance to next alive combatant.
        n = len(turn_order)
        cur_idx = int(s["active_index"]) % n
        next_idx = cur_idx
        new_round = cur_round
        for step_offset in range(1, n + 1):
            cand_idx = (cur_idx + step_offset) % n
            if cand_idx <= cur_idx and step_offset > 0:
                if (cur_idx + step_offset) >= n:
                    new_round = cur_round + 1
            cand_id = turn_order[cand_idx]
            if s["combatants"][cand_id].get("alive"):
                next_idx = cand_idx
                break

        s["active_index"] = next_idx
        s["round"] = new_round
        new_active_id = turn_order[next_idx]
        new_actor = s["combatants"][new_active_id]

        # 4. Expire `start_of_source_next_turn` effects owned by new active actor,
        #    and `start_of_target_next_turn` effects on new active actor.
        for cid, comb in s["combatants"].items():
            kept_effs = []
            for eff in comb.get("active_effects", []):
                exp = eff.get("expires")
                if exp == "start_of_source_next_turn" and eff.get("source_id") == new_active_id:
                    tracker.emit("effect_expired", target_id=cid, kind=eff.get("kind"), source_id=new_active_id)
                    continue
                if exp == "start_of_target_next_turn" and cid == new_active_id:
                    tracker.emit("effect_expired", target_id=cid, kind=eff.get("kind"), source_id=eff.get("source_id"))
                    continue
                kept_effs.append(eff)
            comb["active_effects"] = kept_effs

            comb["conditions"] = [
                c
                for c in comb.get("conditions", [])
                if not (
                    isinstance(c, dict)
                    and c.get("duration") == "start_of_source_next_turn"
                    and c.get("source_id") == new_active_id
                )
            ]

        # 5. Refresh turn resources and movement.
        ssv = int(s.get("state_shape_version", 1))
        if ssv >= 3:
            # Sneak Attack is once per turn (including off-turn Opportunity Attacks), so reset for every combatant.
            for comb in s["combatants"].values():
                if isinstance(comb.get("turn_resources"), dict):
                    comb["turn_resources"]["sneak_attack_used"] = False
        new_actor["turn_resources"] = _fresh_turn_resources(ssv)
        new_actor["movement_remaining_ft"] = _effective_speed(new_actor, rules)
        tracker.emit("turn_started", actor_id=new_active_id, round=new_round)
        tracker.hook(f"Round {new_round}: {new_actor['name']}'s turn begins.")
        _start_of_turn_traits(s, new_active_id, rules, rng, tracker)

        # 6. Check exhaustion 6 death or 0-HP PC Death Saving Throw.
        if not _check_exhaustion_death(s, new_active_id, tracker):
            if (
                new_actor.get("kind") == "pc"
                and int(new_actor.get("hp", 0)) == 0
                and new_actor.get("alive")
                and int(new_actor["death_saves"]["successes"]) < 3
            ):
                ds_roll = rng.roll_d20("normal")
                val = int(ds_roll["chosen"])
                if ds_roll["nat20"]:
                    new_actor["hp"] = 1
                    _remove_condition(new_actor, "unconscious")
                    new_actor["death_saves"] = {"successes": 0, "failures": 0}
                    tracker.emit(
                        "death_save",
                        actor_id=new_active_id,
                        roll=val,
                        outcome="nat20_revive",
                        successes=0,
                        failures=0,
                    )
                    tracker.hook(f"{new_actor['name']} rolls a Natural 20 on Death Save, regains 1 HP, and wakes Prone!")
                elif ds_roll["nat1"]:
                    new_actor["death_saves"]["failures"] = min(3, int(new_actor["death_saves"]["failures"]) + 2)
                    tracker.emit(
                        "death_save",
                        actor_id=new_active_id,
                        roll=val,
                        outcome="nat1_two_failures",
                        successes=new_actor["death_saves"]["successes"],
                        failures=new_actor["death_saves"]["failures"],
                    )
                    if new_actor["death_saves"]["failures"] >= 3:
                        new_actor["alive"] = False
                        tracker.emit("combatant_died", combatant_id=new_active_id, reason="death_save_failures")
                        tracker.hook(f"{new_actor['name']} rolls a Natural 1 on Death Save and dies.")
                    else:
                        tracker.hook(_death_save_tally(new_actor, "rolls a Natural 1 on a Death Save (two failures)"))
                elif val >= 10:
                    new_actor["death_saves"]["successes"] = min(3, int(new_actor["death_saves"]["successes"]) + 1)
                    tracker.emit(
                        "death_save",
                        actor_id=new_active_id,
                        roll=val,
                        outcome="success",
                        successes=new_actor["death_saves"]["successes"],
                        failures=new_actor["death_saves"]["failures"],
                    )
                    if new_actor["death_saves"]["successes"] >= 3:
                        tracker.emit("pc_stabilized", combatant_id=new_active_id)
                        tracker.hook(f"{new_actor['name']} succeeds on a Death Save ({val}) and stabilizes.")
                    else:
                        tracker.hook(_death_save_tally(new_actor, f"succeeds on a Death Save ({val})"))
                else:
                    new_actor["death_saves"]["failures"] = min(3, int(new_actor["death_saves"]["failures"]) + 1)
                    tracker.emit(
                        "death_save",
                        actor_id=new_active_id,
                        roll=val,
                        outcome="failure",
                        successes=new_actor["death_saves"]["successes"],
                        failures=new_actor["death_saves"]["failures"],
                    )
                    if new_actor["death_saves"]["failures"] >= 3:
                        new_actor["alive"] = False
                        tracker.emit("combatant_died", combatant_id=new_active_id, reason="death_save_failures")
                        tracker.hook(f"{new_actor['name']} fails a Death Save ({val}) and dies.")
                    else:
                        tracker.hook(_death_save_tally(new_actor, f"fails a Death Save ({val})"))

        s["rng"] = rng.to_dict()
        _update_encounter_status(s, tracker)
        return {
            "status": "ok",
            "error": None,
            "events": tracker.events,
            "state_diff": tracker.build_diff(s),
            "state": s,
            "legal_actions": compute_legal_actions(s, rules),
            "narrative_hooks": tracker.hooks,
        }

    # ------------------------------------------------------------------
    # 7. `reroll` (5e 2024 Heroic Inspiration post-roll reroll)
    # ------------------------------------------------------------------
    if intent_type == "reroll":
        if actor_id != active_id:
            return _invalid(copy.deepcopy(state), f"It is {active_id}'s turn, not {actor_id}'s.", rules)
        if not actor.get("alive"):
            return _invalid(copy.deepcopy(state), f"{actor_id} is not alive.", rules)
        if _has_modifier_flag(actor, "cannot_act", rules):
            return _invalid(
                copy.deepcopy(state),
                f"{actor_id} is Incapacitated and cannot spend Heroic Inspiration.",
                rules,
            )
        if actor.get("heroic_inspiration") is not True:
            return _invalid(
                copy.deepcopy(state),
                f"{actor['name']} does not have Heroic Inspiration available.",
                rules,
            )
        pending = s.get("pending_reroll")
        if (
            not isinstance(pending, dict)
            or pending.get("actor_id") != actor_id
            or not isinstance(pending.get("intent"), dict)
            or not isinstance(pending.get("rollback_combatants"), dict)
        ):
            return _invalid(
                copy.deepcopy(state),
                f"No recent missed roll is eligible for Heroic Inspiration reroll for {actor['name']}.",
                rules,
            )

        prev_d20 = int(pending.get("previous_d20", 0))
        prev_total = int(pending.get("previous_total", 0))
        target_ac = int(pending.get("target_ac", 0))
        label = str(pending.get("label") or "Attack")
        orig_intent = copy.deepcopy(pending["intent"])
        actor_name = str(actor["name"])

        # Restore pre-miss combatants while preserving monotonic RNG state.
        s["combatants"] = copy.deepcopy(pending["rollback_combatants"])
        if actor_id not in s["combatants"]:
            return _invalid(copy.deepcopy(state), f"Invalid reroll rollback state for '{actor_id}'.", rules)
        s["combatants"][actor_id]["heroic_inspiration"] = False
        # Only the d20 is rerolled (SRD 5.2: "reroll any one die"); the original Bless/Bane dice are kept (#85).
        s["combatants"][actor_id]["_pinned_bonus_dice"] = copy.deepcopy(pending.get("bonus_dice") or [])
        s["pending_reroll"] = None
        if s.get("status") == "ended":
            s["status"] = "active"

        re_res = step(s, orig_intent, rules, roll_overrides=roll_overrides)
        for comb in (re_res.get("state") or {}).get("combatants", {}).values():
            comb.pop("_pinned_bonus_dice", None)
        if re_res.get("status") != "ok":
            return _invalid(
                copy.deepcopy(state),
                str(re_res.get("error") or "Heroic Inspiration reroll failed."),
                rules,
            )

        final_state = re_res["state"]
        final_state["pending_reroll"] = None

        spent_event: dict[str, Any] = {
            "seq": 1,
            "type": "heroic_inspiration_spent",
            "actor_id": actor_id,
            "previous_d20": prev_d20,
            "previous_total": prev_total,
            "target_ac": target_ac,
            "label": label,
        }
        merged_events: list[dict[str, Any]] = [spent_event]
        for ev in re_res.get("events") or []:
            ev_copy = copy.deepcopy(ev)
            ev_copy["seq"] = len(merged_events) + 1
            merged_events.append(ev_copy)

        spent_hook = (
            f"{actor_name} spends Heroic Inspiration to reroll {label} "
            f"(previous total {prev_total})."
        )
        merged_hooks = [spent_hook, *(re_res.get("narrative_hooks") or [])]

        return {
            "status": "ok",
            "error": None,
            "events": merged_events,
            "state_diff": tracker.build_diff(final_state),
            "state": final_state,
            "legal_actions": compute_legal_actions(final_state, rules),
            "narrative_hooks": merged_hooks,
        }

    # ------------------------------------------------------------------
    # 8. `class_feature` (M6.3A Core 2024 SRD 5.2 Class Features)
    # ------------------------------------------------------------------
    if intent_type == "class_feature":
        s["pending_reroll"] = None
        if actor_id != active_id:
            return _invalid(copy.deepcopy(state), f"It is {active_id}'s turn, not {actor_id}'s.", rules)
        if not actor.get("alive") or int(actor.get("hp", 0)) <= 0:
            return _invalid(copy.deepcopy(state), f"{actor_id} is not conscious and cannot use class features.", rules)
        if _has_modifier_flag(actor, "cannot_act", rules):
            return _invalid(copy.deepcopy(state), f"{actor_id} is Incapacitated and cannot use class features.", rules)

        feature = str(intent.get("feature") or "").strip().lower()
        if not feature:
            return _invalid(copy.deepcopy(state), "class_feature intent requires a 'feature' field.", rules)

        actor_feats = {strip_prefix(str(f)) for f in (actor.get("features") or [])}
        res = actor["turn_resources"]
        cr = actor.setdefault("class_resources", {})

        # A. Fighter — Second Wind (Bonus Action, 1d10 + level, optional L5+ Tactical Shift move)
        if feature == "second_wind":
            if "fighter-second-wind" not in actor_feats:
                return _invalid(copy.deepcopy(state), f"{actor_id} does not have the Second Wind feature.", rules)
            if not res.get("bonus_action"):
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Bonus Action remaining for Second Wind.", rules)
            sw_res = cr.get("second_wind")
            if not isinstance(sw_res, dict) or int(sw_res.get("remaining", 0)) < 1:
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Second Wind uses remaining.", rules)

            raw_move_to = intent.get("move_to")
            parsed_move: dict[str, int] | None = None
            shift_cost = 0
            is_prone = "prone" in _effective_conditions(actor, rules)
            if raw_move_to is not None:
                if "fighter-tactical-shift" not in actor_feats:
                    return _invalid(
                        copy.deepcopy(state),
                        f"{actor_id} requires Tactical Shift (Fighter level 5+) to move with Second Wind.",
                        rules,
                    )
                parsed_move = _parse_point(raw_move_to)
                if parsed_move is None:
                    return _invalid(
                        copy.deepcopy(state),
                        "second_wind 'move_to' must be a dict with integer 'x' and 'y' coordinates.",
                        rules,
                    )
                eff_speed = _effective_speed(actor, rules)
                max_shift = eff_speed // 2
                if eff_speed <= 0 or max_shift <= 0:
                    return _invalid(
                        copy.deepcopy(state),
                        f"{actor_id} has 0 speed and cannot use Tactical Shift.",
                        rules,
                    )
                if distance_ft(actor["position"], parsed_move) > 0:
                    blocker_id = square_occupant(s, actor_id, parsed_move)
                    if blocker_id is not None:
                        blocker_name = str(s["combatants"][blocker_id].get("name") or blocker_id)
                        return _invalid(
                            copy.deepcopy(state),
                            f"Tactical Shift destination ({parsed_move['x']}, {parsed_move['y']}) is occupied by {blocker_name}.",
                            rules,
                        )
                    if "frightened" in _effective_conditions(actor, rules):
                        for fe in _condition_entries_for(actor, "frightened"):
                            fsrc_id = fe.get("source_id")
                            if fsrc_id and fsrc_id in s["combatants"] and s["combatants"][fsrc_id].get("alive"):
                                fpos = s["combatants"][fsrc_id]["position"]
                                if distance_ft(parsed_move, fpos) < distance_ft(actor["position"], fpos):
                                    return _invalid(
                                        copy.deepcopy(state),
                                        f"{actor_id} is Frightened of {fsrc_id} and cannot move closer to it.",
                                        rules,
                                    )
                    move_cost, path_err = movement_path_cost(
                        actor["position"],
                        parsed_move,
                        s.get("bounds"),
                        s.get("terrain"),
                        is_prone=is_prone,
                        max_cost=max_shift,
                    )
                    if path_err is not None or move_cost is None or move_cost > max_shift:
                        return _invalid(
                            copy.deepcopy(state),
                            path_err
                            or f"Tactical Shift destination ({parsed_move['x']}, {parsed_move['y']}) exceeds half speed ({max_shift} ft).",
                            rules,
                        )
                    shift_cost = move_cost

            res["bonus_action"] = False
            sw_res["remaining"] = int(sw_res["remaining"]) - 1
            sw_roll = rng.roll_dice_expr("1d10", crit=False)
            lvl_bonus = int(actor.get("level", 1))
            heal_amt = int(sw_roll["total"]) + lvl_bonus
            hp_before = int(actor["hp"])
            actor["hp"] = min(int(actor["hp_max"]), hp_before + heal_amt)

            tracker.emit(
                "class_feature_used",
                actor_id=actor_id,
                feature="second_wind",
                uses_left=sw_res["remaining"],
                dice="1d10",
                roll=int(sw_roll["total"]),
                level_bonus=lvl_bonus,
            )
            tracker.emit(
                "healing_applied",
                source_id=actor_id,
                target_id=actor_id,
                feature="second_wind",
                amount=heal_amt,
                hp_before=hp_before,
                hp_after=actor["hp"],
                revived=False,
            )
            hook_txt = f"{actor['name']} uses Second Wind (Bonus Action), regaining {heal_amt} HP."
            if parsed_move is not None and distance_ft(actor["position"], parsed_move) > 0:
                old_pos = copy.deepcopy(actor["position"])
                shift_dist = distance_ft(old_pos, parsed_move)
                actor["position"] = parsed_move
                tracker.emit(
                    "moved",
                    actor_id=actor_id,
                    from_pos=old_pos,
                    to_pos=parsed_move,
                    distance_ft=shift_dist,
                    movement_cost_ft=shift_cost,
                    crawled=is_prone,
                    tactical_shift=True,
                    provokes_oa=False,
                )
                hook_txt += (
                    f" Tactical Shift: moves to ({parsed_move['x']}, {parsed_move['y']}) "
                    "without provoking Opportunity Attacks."
                )
            tracker.hook(hook_txt)

        # B. Fighter — Action Surge (Free 1/turn, extra non-Magic action)
        elif feature == "action_surge":
            if "fighter-action-surge" not in actor_feats:
                return _invalid(copy.deepcopy(state), f"{actor_id} does not have the Action Surge feature.", rules)
            if res.get("action_surge_used_this_turn"):
                return _invalid(copy.deepcopy(state), f"{actor_id} has already used Action Surge this turn.", rules)
            as_res = cr.get("action_surge")
            if not isinstance(as_res, dict) or int(as_res.get("remaining", 0)) < 1:
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Action Surge uses remaining.", rules)

            as_res["remaining"] = int(as_res["remaining"]) - 1
            res["action_surge_used_this_turn"] = True
            res["surge_action"] = True
            tracker.emit(
                "class_feature_used",
                actor_id=actor_id,
                feature="action_surge",
                uses_left=as_res["remaining"],
            )
            tracker.hook(f"{actor['name']} activates Action Surge, gaining one extra non-Magic action this turn!")

        # C. Paladin — Lay on Hands (2024 Bonus Action, 5*level LR pool, Touch <= 5 ft, SRD 5.2 RAW no creature-type ban)
        elif feature == "lay_on_hands":
            if "paladin-lay-on-hands" not in actor_feats:
                return _invalid(copy.deepcopy(state), f"{actor_id} does not have the Lay on Hands feature.", rules)
            if not res.get("bonus_action"):
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Bonus Action remaining for Lay on Hands.", rules)
            loh_res = cr.get("lay_on_hands")
            pool_rem = int(loh_res.get("remaining", 0)) if isinstance(loh_res, dict) else 0
            if pool_rem < 1:
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Lay on Hands HP remaining.", rules)

            mode = str(intent.get("mode") or "heal").strip().lower()
            if mode not in ("heal", "cure_poison"):
                return _invalid(
                    copy.deepcopy(state),
                    f"Lay on Hands 'mode' must be 'heal' or 'cure_poison', got {mode!r}.",
                    rules,
                )
            target_id = str(intent.get("target_id") or actor_id)
            if target_id not in s["combatants"]:
                return _invalid(copy.deepcopy(state), f"Unknown Lay on Hands target '{target_id}'.", rules)
            target = s["combatants"][target_id]
            if not target.get("alive"):
                return _invalid(
                    copy.deepcopy(state),
                    f"Lay on Hands cannot affect dead target '{target_id}'.",
                    rules,
                )
            dist = distance_ft(actor["position"], target["position"])
            if dist > 5:
                return _invalid(
                    copy.deepcopy(state),
                    f"Target '{target_id}' is out of Touch range for Lay on Hands ({dist} ft > 5 ft).",
                    rules,
                )
            if dist > 0 and not has_line_of_sight(actor["position"], target["position"], s.get("terrain")):
                return _invalid(
                    copy.deepcopy(state),
                    f"No line of sight to '{target_id}' for Lay on Hands: blocked by a wall.",
                    rules,
                )

            if mode == "heal":
                amt = _parse_int(intent.get("amount"), allow_str=False)
                if amt is None or amt < 1 or amt > pool_rem:
                    return _invalid(
                        copy.deepcopy(state),
                        f"Lay on Hands 'amount' must be an integer between 1 and {pool_rem}.",
                        rules,
                    )
                res["bonus_action"] = False
                loh_res["remaining"] = pool_rem - amt
                hp_before = int(target["hp"])
                revived = False
                if hp_before == 0 and target.get("kind") == "pc" and amt > 0:
                    _remove_condition(target, "unconscious")
                    target["death_saves"] = {"successes": 0, "failures": 0}
                    revived = True
                target["hp"] = min(int(target["hp_max"]), hp_before + amt)
                tracker.emit(
                    "class_feature_used",
                    actor_id=actor_id,
                    feature="lay_on_hands",
                    mode="heal",
                    spent=amt,
                    pool_left=loh_res["remaining"],
                    target_id=target_id,
                )
                tracker.emit(
                    "healing_applied",
                    source_id=actor_id,
                    target_id=target_id,
                    feature="lay_on_hands",
                    amount=amt,
                    hp_before=hp_before,
                    hp_after=target["hp"],
                    revived=revived,
                )
                tracker.hook(
                    f"{actor['name']} uses Lay on Hands on {target['name']}, restoring {amt} HP "
                    f"({loh_res['remaining']} HP left in pool)."
                )
            else:
                if pool_rem < 5:
                    return _invalid(
                        copy.deepcopy(state),
                        f"Lay on Hands cure_poison requires 5 HP in pool (only {pool_rem} remaining).",
                        rules,
                    )
                if "poisoned" not in _direct_condition_slugs(target):
                    return _invalid(
                        copy.deepcopy(state),
                        f"Target '{target_id}' does not have the Poisoned condition.",
                        rules,
                    )
                res["bonus_action"] = False
                loh_res["remaining"] = pool_rem - 5
                _remove_condition(target, "poisoned")
                tracker.emit(
                    "class_feature_used",
                    actor_id=actor_id,
                    feature="lay_on_hands",
                    mode="cure_poison",
                    spent=5,
                    pool_left=loh_res["remaining"],
                    target_id=target_id,
                )
                tracker.emit(
                    "condition_ended",
                    target_id=target_id,
                    condition="poisoned",
                    reason="lay_on_hands",
                )
                tracker.hook(
                    f"{actor['name']} spends 5 HP of Lay on Hands to cure Poisoned on {target['name']} "
                    f"({loh_res['remaining']} HP left in pool)."
                )

        # D. Cleric (Life Domain) — Channel Divinity: Preserve Life (Magic Action — blocked by Action Surge!)
        elif feature == "preserve_life":
            if "life-preserve-life" not in actor_feats:
                return _invalid(copy.deepcopy(state), f"{actor_id} does not have the Preserve Life feature.", rules)
            if not res.get("action"):
                if res.get("surge_action"):
                    return _invalid(copy.deepcopy(state), _ACTION_SURGE_MAGIC_BAN_ERROR, rules)
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Action remaining for Preserve Life.", rules)
            cd_res = cr.get("channel_divinity")
            if not isinstance(cd_res, dict) or int(cd_res.get("remaining", 0)) < 1:
                return _invalid(copy.deepcopy(state), f"{actor_id} has no Channel Divinity uses remaining.", rules)

            pool_max = 5 * int(actor.get("level", 1))
            raw_allocs = intent.get("allocations")
            raw_tids = intent.get("target_ids")
            allocations: dict[str, int] = {}

            def _validate_preserve_target(tid: str) -> tuple[dict[str, Any] | None, str | None]:
                if tid not in s["combatants"]:
                    return None, f"Unknown Preserve Life target '{tid}'."
                t = s["combatants"][tid]
                if not t.get("alive"):
                    return None, f"Preserve Life cannot affect dead target '{tid}'."
                dist_t = distance_ft(actor["position"], t["position"])
                if dist_t > 30:
                    return None, f"Preserve Life target '{tid}' is out of 30-ft range ({dist_t} ft > 30 ft)."
                if tid != actor_id and not has_line_of_sight(actor["position"], t["position"], s.get("terrain")):
                    return None, f"No line of sight to '{tid}' for Preserve Life: blocked by a wall."
                half_max = int(t["hp_max"]) // 2
                if int(t["hp"]) > half_max:
                    return None, f"Preserve Life target '{tid}' is not Bloodied ({t['hp']}/{t['hp_max']} HP; half max is {half_max})."
                return t, None

            if raw_allocs is not None:
                if not isinstance(raw_allocs, dict) or not raw_allocs:
                    return _invalid(
                        copy.deepcopy(state),
                        "Preserve Life 'allocations' must be a non-empty mapping of target_id -> positive integer HP.",
                        rules,
                    )
                for raw_tid, raw_amt in raw_allocs.items():
                    tid = str(raw_tid)
                    amt = _parse_int(raw_amt, allow_str=False)
                    if amt is None or amt < 1:
                        return _invalid(
                            copy.deepcopy(state),
                            f"Preserve Life allocation for '{tid}' must be an integer >= 1.",
                            rules,
                        )
                    t, err = _validate_preserve_target(tid)
                    if err is not None or t is None:
                        return _invalid(copy.deepcopy(state), err or "Invalid Preserve Life target.", rules)
                    cap = max(0, (int(t["hp_max"]) // 2) - int(t["hp"]))
                    if amt > cap:
                        return _invalid(
                            copy.deepcopy(state),
                            f"Preserve Life allocation {amt} for '{tid}' exceeds half-HP-max cap ({cap} HP).",
                            rules,
                        )
                    allocations[tid] = amt
                if sum(allocations.values()) > pool_max:
                    return _invalid(
                        copy.deepcopy(state),
                        f"Preserve Life total allocation ({sum(allocations.values())} HP) exceeds pool maximum ({pool_max} HP).",
                        rules,
                    )
            elif isinstance(raw_tids, (list, tuple)) and raw_tids:
                dedup_tids = list(dict.fromkeys(str(x) for x in raw_tids))
                for tid in dedup_tids:
                    _, err = _validate_preserve_target(tid)
                    if err is not None:
                        return _invalid(copy.deepcopy(state), err, rules)
                ordered_tids = sorted(
                    dedup_tids,
                    key=lambda tid: (int(s["combatants"][tid]["hp"]) / max(1, int(s["combatants"][tid]["hp_max"])), tid),
                )
                rem_pool = pool_max
                for tid in ordered_tids:
                    if rem_pool <= 0:
                        break
                    t = s["combatants"][tid]
                    cap = max(0, (int(t["hp_max"]) // 2) - int(t["hp"]))
                    give = min(rem_pool, cap)
                    if give > 0:
                        allocations[tid] = give
                        rem_pool -= give
                if not allocations:
                    return _invalid(
                        copy.deepcopy(state),
                        "Preserve Life could not distribute any HP (all targets are already at half HP max).",
                        rules,
                    )
            else:
                return _invalid(
                    copy.deepcopy(state),
                    "Preserve Life requires either 'allocations' or 'target_ids'.",
                    rules,
                )

            res["action"] = False
            cd_res["remaining"] = int(cd_res["remaining"]) - 1
            total_distributed = sum(allocations.values())
            tracker.emit(
                "class_feature_used",
                actor_id=actor_id,
                feature="preserve_life",
                uses_left=cd_res["remaining"],
                pool_max=pool_max,
                total_distributed=total_distributed,
                allocations=dict(allocations),
            )
            healed_names: list[str] = []
            for tid, amt in allocations.items():
                t = s["combatants"][tid]
                hp_before = int(t["hp"])
                revived = False
                if hp_before == 0 and t.get("kind") == "pc" and amt > 0:
                    _remove_condition(t, "unconscious")
                    t["death_saves"] = {"successes": 0, "failures": 0}
                    revived = True
                t["hp"] = min(int(t["hp_max"]) // 2, hp_before + amt)
                tracker.emit(
                    "healing_applied",
                    source_id=actor_id,
                    target_id=tid,
                    feature="preserve_life",
                    amount=amt,
                    hp_before=hp_before,
                    hp_after=t["hp"],
                    revived=revived,
                )
                healed_names.append(str(t["name"]))
            tracker.hook(
                f"{actor['name']} channels Preserve Life, distributing {total_distributed} HP among "
                f"{', '.join(healed_names)}."
            )

        else:
            return _invalid(copy.deepcopy(state), f"Unsupported class feature '{feature}'.", rules)

        s["rng"] = rng.to_dict()
        _update_encounter_status(s, tracker)
        return {
            "status": "ok",
            "error": None,
            "events": tracker.events,
            "state_diff": tracker.build_diff(s),
            "state": s,
            "legal_actions": compute_legal_actions(s, rules),
            "narrative_hooks": tracker.hooks,
        }

    return _invalid(copy.deepcopy(state), f"Unsupported intent type '{intent_type}'.", rules)
