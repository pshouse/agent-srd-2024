"""Deterministic Simulation Testing (DST) & Stateful Fuzzing for 5e 2024 (SRD 5.2).

Pure Python 3.11+ stdlib + `srd2024.engine` / `srd2024.arena` only.
Never imports `srd2024.compiler` or `srd2024.rules_compiler`.

Provides:
- `check_state_invariants`: universal post-`step()` 5e 2024 state invariant checker.
- `build_procedural_scenario`: seeded random skirmish generator (compiled PCs +
  `coverage == "full"` SRD monsters + valid initial conditions/exhaustion/positions).
- `generate_random_legal_intent` / `generate_adversarial_intent`: mixed legal +
  adversarial/malformed intent generators covering all combat intents.
- `run_dst_seed` / `run_dst_suite`: seeded DST simulation runner with immutability,
  exception, invariant, and double-run byte-identical determinism verification.
- `main_dst`: CLI entry point for `bin/srd-dst`.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import sys
import traceback
from collections.abc import Iterable
from typing import Any

from srd2024.arena.scenarios import BUILTIN_SCENARIO_IDS, load_scenario
from srd2024.engine.character import compile_character
from srd2024.engine.combat import (
    _effective_speed,
    _has_modifier_flag,
    compute_legal_actions,
    distance_ft,
    grid_square,
    step,
)
from srd2024.engine.store import RulesStore, strip_prefix

_CONDITION_IMPLIES: dict[str, tuple[str, ...]] = {
    "paralyzed": ("incapacitated",),
    "petrified": ("incapacitated",),
    "stunned": ("incapacitated",),
    "unconscious": ("incapacitated", "prone"),
}

_CONCENTRATION_BREAKING_CONDITIONS: frozenset[str] = frozenset(
    {"incapacitated", "paralyzed", "unconscious", "stunned", "petrified"}
)

_SAFE_INITIAL_CONDITIONS: tuple[str, ...] = (
    "blinded",
    "charmed",
    "deafened",
    "frightened",
    "grappled",
    "invisible",
    "poisoned",
    "prone",
    "restrained",
)

_DEFAULT_SCENARIO_POOL: tuple[str, ...] = (
    "goblin-ambush",
    "crypt-of-ghouls",
    "wyrmling-lair",
    "procedural",
    "procedural",
)


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _direct_condition_set(combatant: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for entry in combatant.get("conditions") or []:
        if isinstance(entry, dict):
            sl = strip_prefix(str(entry.get("condition") or ""))
        else:
            sl = strip_prefix(str(entry))
        if sl:
            out.add(sl)
    return out


def _expand_conditions(direct: set[str]) -> set[str]:
    expanded = set(direct)
    queue = list(direct)
    while queue:
        cur = queue.pop()
        for imp in _CONDITION_IMPLIES.get(cur, ()):
            if imp not in expanded:
                expanded.add(imp)
                queue.append(imp)
    return expanded


def capture_initial_max_slots(state: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Snapshot each combatant's initial maximum spell slots for bound checking."""
    max_slots: dict[str, dict[str, int]] = {}
    combatants = state.get("combatants") if isinstance(state, dict) else None
    if not isinstance(combatants, dict):
        return max_slots
    for cid, comb in combatants.items():
        if not isinstance(comb, dict):
            continue
        sc = comb.get("spellcasting")
        if isinstance(sc, dict) and isinstance(sc.get("slots"), dict):
            max_slots[str(cid)] = {
                str(lvl): int(cnt) for lvl, cnt in sc["slots"].items() if _is_strict_int(cnt)
            }
    return max_slots


# ----------------------------------------------------------------------
# 1. Universal Engine Invariant Checker
# ----------------------------------------------------------------------


def _free_start(rng: random.Random, xs: list[int], taken: set[tuple[int, int]]) -> dict[str, int]:
    """A random start square from `xs` x {0..20} that no other combatant holds (#53: no shared squares)."""
    ys = [0, 5, 10, 15, 20]
    pos = {"x": rng.choice(xs), "y": rng.choice(ys)}
    while (pos["x"], pos["y"]) in taken:
        pos = {"x": rng.choice(xs), "y": rng.choice(ys)}
    taken.add((pos["x"], pos["y"]))
    return pos


def check_state_invariants(
    state: dict[str, Any],
    prev_state: dict[str, Any] | None = None,
    intent: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    initial_max_slots: dict[str, dict[str, int]] | None = None,
    *,
    input_state_snapshot: dict[str, Any] | None = None,
    input_state_after: dict[str, Any] | None = None,
) -> list[str]:
    """Validate universal 5e 2024 (SRD 5.2) invariants on an EncounterState.

    Returns a list of human-readable violation strings (empty means 100% valid).
    """
    violations: list[str] = []

    # Input immutability check (when caller supplies before/after snapshots of input state).
    if input_state_snapshot is not None and input_state_after is not None:
        if input_state_snapshot != input_state_after:
            violations.append("input_state_mutated: caller input state was modified by step()")

    # Result status invariant: non-"ok" status must never alter state relative to prev_state.
    if result is not None:
        if not isinstance(result, dict):
            violations.append(f"invalid_result_type: expected dict, got {type(result).__name__}")
        else:
            status = result.get("status")
            if status != "ok" and prev_state is not None:
                res_state = result.get("state")
                if res_state != prev_state:
                    violations.append(
                        f"invalid_intent_mutated_state: status={status!r} returned state differs from prev_state"
                    )

    if not isinstance(state, dict):
        violations.append(f"state_not_dict: expected dict, got {type(state).__name__}")
        return violations

    # Turn & top-level structure invariants.
    round_num = state.get("round")
    if not _is_strict_int(round_num) or round_num < 1:
        violations.append(f"invalid_round: expected int >= 1, got {round_num!r}")

    combatants = state.get("combatants")
    if not isinstance(combatants, dict) or not combatants:
        violations.append("invalid_combatants: combatants must be a non-empty dict")
        return violations

    turn_order = state.get("turn_order")
    if not isinstance(turn_order, list) or not turn_order:
        violations.append("invalid_turn_order: turn_order must be a non-empty list")
    else:
        if len(set(turn_order)) != len(turn_order):
            violations.append(f"duplicate_turn_order: turn_order contains duplicates ({turn_order!r})")
        for tid in turn_order:
            if tid not in combatants:
                violations.append(f"missing_turn_order_combatant: '{tid}' not in combatants")

        active_idx = state.get("active_index")
        if not _is_strict_int(active_idx) or not (0 <= active_idx < len(turn_order)):
            violations.append(
                f"active_index_out_of_range: active_index={active_idx!r} for turn_order len={len(turn_order)}"
            )

    # RNG monotonicity & round monotonicity relative to prev_state.
    rng_state = state.get("rng")
    if not isinstance(rng_state, dict):
        violations.append("missing_rng_state: state['rng'] must be a dict")
    else:
        draw_idx = rng_state.get("draw_index")
        if not _is_strict_int(draw_idx) or draw_idx < 0:
            violations.append(f"invalid_rng_draw_index: expected int >= 0, got {draw_idx!r}")
        is_start = isinstance(intent, dict) and str(intent.get("type") or "").strip().lower() == "start_encounter"
        if prev_state is not None and isinstance(prev_state, dict) and not is_start:
            prev_rng = prev_state.get("rng")
            if isinstance(prev_rng, dict) and _is_strict_int(prev_rng.get("draw_index")) and _is_strict_int(draw_idx):
                if draw_idx < prev_rng["draw_index"]:
                    violations.append(
                        f"rng_draw_index_regressed: {draw_idx} < prev {prev_rng['draw_index']}"
                    )
                if rng_state.get("seed") != prev_rng.get("seed"):
                    violations.append(
                        f"rng_seed_changed: {rng_state.get('seed')!r} != prev {prev_rng.get('seed')!r}"
                    )
            prev_round = prev_state.get("round")
            if _is_strict_int(prev_round) and _is_strict_int(round_num) and round_num < prev_round:
                violations.append(f"round_regressed: {round_num} < prev {prev_round}")

    # Per-combatant bounds, conditions, death, and spell slot invariants.
    for cid in sorted(combatants.keys()):
        comb = combatants[cid]
        if not isinstance(comb, dict):
            violations.append(f"combatant_not_dict:{cid}: expected dict")
            continue

        hp = comb.get("hp")
        hp_max = comb.get("hp_max")
        if not _is_strict_int(hp_max) or hp_max < 1:
            violations.append(f"invalid_hp_max:{cid}: hp_max={hp_max!r} must be int >= 1")
        if not _is_strict_int(hp):
            violations.append(f"invalid_hp_type:{cid}: hp={hp!r} must be int")
        elif _is_strict_int(hp_max) and not (0 <= hp <= hp_max):
            violations.append(f"hp_out_of_bounds:{cid}: hp={hp} outside [0, {hp_max}]")

        temp_hp = comb.get("temp_hp", 0)
        if not _is_strict_int(temp_hp) or temp_hp < 0:
            violations.append(f"invalid_temp_hp:{cid}: temp_hp={temp_hp!r} must be int >= 0")

        exh = comb.get("exhaustion_level", 0)
        if not _is_strict_int(exh) or not (0 <= exh <= 6):
            violations.append(f"exhaustion_out_of_bounds:{cid}: exhaustion_level={exh!r} outside [0, 6]")

        mov = comb.get("movement_remaining_ft", 0)
        if not _is_strict_int(mov) or mov < 0:
            violations.append(
                f"negative_movement:{cid}: movement_remaining_ft={mov!r} must be int >= 0"
            )

        hi = comb.get("heroic_inspiration")
        if not isinstance(hi, bool):
            violations.append(
                f"invalid_heroic_inspiration:{cid}: heroic_inspiration={hi!r} must be bool"
            )

        # Spell slot bounds.
        sc = comb.get("spellcasting")
        if isinstance(sc, dict) and isinstance(sc.get("slots"), dict):
            max_map = initial_max_slots.get(cid) if isinstance(initial_max_slots, dict) else None
            for lvl_key, slot_cnt in sc["slots"].items():
                if not _is_strict_int(slot_cnt) or slot_cnt < 0:
                    violations.append(
                        f"negative_spell_slot:{cid}: slot level {lvl_key!r} has invalid count {slot_cnt!r}"
                    )
                elif max_map is not None:
                    max_allowed = int(max_map.get(str(lvl_key), 0))
                    if slot_cnt > max_allowed:
                        violations.append(
                            f"spell_slot_exceeds_max:{cid}: slot level {lvl_key!r} has {slot_cnt} > max {max_allowed}"
                        )

        # Death save bounds.
        ds = comb.get("death_saves")
        if isinstance(ds, dict):
            for field in ("successes", "failures"):
                val = ds.get(field, 0)
                if not _is_strict_int(val) or not (0 <= val <= 3):
                    violations.append(f"invalid_death_saves:{cid}: {field}={val!r} outside [0, 3]")

        alive = bool(comb.get("alive", False))
        direct_conds = _direct_condition_set(comb)
        effective_conds = _expand_conditions(direct_conds)
        conc = comb.get("concentration")

        # Dead or Incapacitated/Paralyzed/Unconscious/Stunned/Petrified NEVER hold concentration.
        if conc:
            if not alive:
                violations.append(
                    f"dead_holding_concentration:{cid}: dead combatant holds concentration {conc!r}"
                )
            breaking = sorted(effective_conds & _CONCENTRATION_BREAKING_CONDITIONS)
            if breaking:
                violations.append(
                    f"incapacitated_holding_concentration:{cid}: conditions {breaking} cannot hold concentration {conc!r}"
                )

        # Unconscious implies direct prone AND hp == 0 (equivalently: hp > 0 implies not unconscious).
        if "unconscious" in direct_conds:
            if "prone" not in direct_conds:
                violations.append(
                    f"unconscious_without_prone:{cid}: unconscious combatant missing direct 'prone' condition"
                )
            if _is_strict_int(hp) and hp != 0:
                violations.append(
                    f"unconscious_with_positive_hp:{cid}: combatant has hp={hp} > 0 while unconscious"
                )

        # Exhaustion level 6 implies not alive.
        if _is_strict_int(exh) and exh == 6 and alive:
            violations.append(
                f"exhaustion_6_alive:{cid}: exhaustion_level == 6 must have alive == False"
            )

        # Monsters at hp == 0 must have alive == False.
        if comb.get("kind") == "monster" and _is_strict_int(hp) and hp == 0 and alive:
            violations.append(
                f"monster_zero_hp_alive:{cid}: monster at hp == 0 must have alive == False"
            )

        # Dead combatants should not have positive HP.
        if not alive and _is_strict_int(hp) and hp > 0:
            violations.append(
                f"dead_with_positive_hp:{cid}: combatant has alive == False with hp={hp} > 0"
            )

    # Space occupancy (#53): no two living non-Tiny creatures share a 5-ft square.
    squares: dict[tuple[int, int], str] = {}
    for cid in sorted(combatants.keys()):
        comb = combatants[cid]
        if not isinstance(comb, dict) or comb.get("alive") is not True:
            continue
        if str(comb.get("size") or "Medium").strip().lower() == "tiny":
            continue
        pos = comb.get("position")
        if not (isinstance(pos, dict) and _is_strict_int(pos.get("x")) and _is_strict_int(pos.get("y"))):
            continue
        sq = grid_square(pos)
        if sq in squares:
            violations.append(f"shared_square:{squares[sq]},{cid}: two living creatures occupy square {sq}")
        else:
            squares[sq] = cid

    return violations


# ----------------------------------------------------------------------
# 2. Procedural Skirmish Generator
# ----------------------------------------------------------------------


def _pc_archetype_specs() -> list[dict[str, Any]]:
    """Curated pool of valid 5e 2024 PC specs exercising all 8 masteries & spell families."""
    return [
        {
            "name": "Garrett Cleaver",
            "species": "human",
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
            "equipment": {
                "armor": "chain-mail",
                "shield": False,
                "weapons": ["greataxe", "greatsword", "warhammer", "javelin"],
            },
            "weapon_mastery_weapons": ["greataxe", "greatsword", "warhammer", "javelin"],
            "hp_method": "fixed",
        },
        {
            "name": "Sera Twinblade",
            "species": "halfling",
            "background": "criminal",
            "background_asi": {"dex": 2, "con": 1},
            "origin_feat": "alert",
            "class": "rogue",
            "subclass": "thief",
            "level": 5,
            "ability_method": "standard_array",
            "base_abilities": {"str": 8, "dex": 15, "con": 14, "int": 12, "wis": 13, "cha": 10},
            "asi_choices": [{"level": 4, "type": "asi", "increases": {"dex": 2}}],
            "equipment": {
                "armor": "studded-leather-armor",
                "shield": False,
                "weapons": ["shortsword", "dagger", "shortbow"],
            },
            "weapon_mastery_weapons": ["shortsword", "dagger"],
            "hp_method": "fixed",
        },
        {
            "name": "Orin Stonehammer",
            "species": "dwarf",
            "background": "soldier",
            "background_asi": {"str": 2, "con": 1},
            "origin_feat": "savage-attacker",
            "class": "fighter",
            "subclass": None,
            "level": 1,
            "ability_method": "point_buy",
            "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
            "asi_choices": [],
            "fighting_style": "dueling",
            "equipment": {
                "armor": "chain-mail",
                "shield": True,
                "weapons": ["longsword", "battleaxe", "javelin"],
            },
            "weapon_mastery_weapons": ["longsword", "battleaxe", "javelin"],
            "hp_method": "fixed",
        },
        {
            "name": "Vaelis Ember",
            "species": "elf",
            "subspecies": "elven-lineage-high-elf",
            "background": "sage",
            "background_asi": {"int": 2, "con": 1},
            "origin_feat": "magic-initiate",
            "class": "wizard",
            "subclass": "evoker",
            "level": 5,
            "ability_method": "standard_array",
            "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
            "asi_choices": [{"level": 4, "type": "asi", "increases": {"int": 2}}],
            "equipment": {"armor": None, "shield": False, "weapons": ["quarterstaff", "dagger"]},
            "cantrips": ["fire-bolt", "ray-of-frost", "acid-splash", "prestidigitation"],
            "prepared_spells": [
                "fireball",
                "lightning-bolt",
                "shatter",
                "misty-step",
                "scorching-ray",
                "magic-missile",
                "shield",
                "mage-armor",
                "hold-person",
                "false-life",
                "longstrider",
                "haste",
            ],
            "hp_method": "fixed",
        },
        {
            "name": "Maren Sunward",
            "species": "dwarf",
            "background": "acolyte",
            "background_asi": {"wis": 2, "cha": 1},
            "origin_feat": "magic-initiate",
            "class": "cleric",
            "subclass": "life-domain",
            "level": 5,
            "ability_method": "standard_array",
            "base_abilities": {"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 15, "cha": 12},
            "asi_choices": [{"level": 4, "type": "asi", "increases": {"wis": 2}}],
            "equipment": {"armor": "scale-mail", "shield": True, "weapons": ["mace"]},
            "cantrips": ["sacred-flame", "guidance", "light", "resistance"],
            "prepared_spells": [
                "bless",
                "bane",
                "cure-wounds",
                "healing-word",
                "guiding-bolt",
                "shield-of-faith",
                "hold-person",
                "mass-healing-word",
                "augury",
            ],
            "hp_method": "fixed",
        },
        {
            "name": "Nyx Shadowsong",
            "species": "human",
            "background": "acolyte",
            "background_asi": {"cha": 2, "con": 1},
            "origin_feat": "magic-initiate",
            "extra_origin_feat": "alert",
            "class": "warlock",
            "subclass": "fiend-patron",
            "level": 5,
            "ability_method": "standard_array",
            "base_abilities": {"str": 8, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 15},
            "asi_choices": [{"level": 4, "type": "asi", "increases": {"cha": 2}}],
            "equipment": {"armor": "leather-armor", "shield": False, "weapons": ["dagger", "light-crossbow"]},
            "cantrips": ["eldritch-blast", "chill-touch", "prestidigitation"],
            "prepared_spells": ["burning-hands", "hold-person", "misty-step", "shatter", "fireball"],
            "hp_method": "fixed",
        },
    ]


_CACHED_FULL_MONSTERS: dict[str, list[str]] = {}
_CACHED_PC_STATBLOCKS: dict[str, list[dict[str, Any]]] = {}


def _get_full_monster_slugs(rules: RulesStore) -> list[str]:
    cache_key = str(rules.rules_path)
    if cache_key not in _CACHED_FULL_MONSTERS:
        monsters = rules.list_domain("monsters")
        slugs = sorted(
            slug
            for slug, rec in monsters.items()
            if rec.get("coverage") == "full" and isinstance(rec.get("actions"), list) and rec["actions"]
        )
        _CACHED_FULL_MONSTERS[cache_key] = slugs
    return _CACHED_FULL_MONSTERS[cache_key]


def _get_compiled_pc_pool(rules: RulesStore) -> list[dict[str, Any]]:
    cache_key = str(rules.rules_path)
    if cache_key not in _CACHED_PC_STATBLOCKS:
        compiled_pool: list[dict[str, Any]] = []
        for spec in _pc_archetype_specs():
            res = compile_character(spec, rules)
            if res.get("valid") and isinstance(res.get("statblock"), dict):
                compiled_pool.append(res["statblock"])
        if not compiled_pool:
            raise RuntimeError("Failed to compile any procedural PC archetypes.")
        _CACHED_PC_STATBLOCKS[cache_key] = compiled_pool
    return _CACHED_PC_STATBLOCKS[cache_key]


def build_procedural_scenario(seed: int, rules: RulesStore) -> dict[str, Any]:
    """Generate a deterministic randomized 5e 2024 skirmish for DST fuzzing."""
    rng = random.Random(int(seed) ^ 0x445354)
    pc_pool = _get_compiled_pc_pool(rules)
    monster_slugs = _get_full_monster_slugs(rules)

    n_pcs = rng.randint(2, 3)
    n_monsters = rng.randint(2, 3)

    chosen_pcs = [copy.deepcopy(rng.choice(pc_pool)) for _ in range(n_pcs)]
    chosen_monsters = [rng.choice(monster_slugs) for _ in range(n_monsters)]

    combatants: list[dict[str, Any]] = []
    party_ids: list[str] = []
    enemy_ids: list[str] = []
    taken: set[tuple[int, int]] = set()

    for idx, sb in enumerate(chosen_pcs, start=1):
        cid = f"pc-{idx}"
        party_ids.append(cid)
        entry: dict[str, Any] = {
            "id": cid,
            "name": f"{sb['name']} #{idx}",
            "side": "party",
            "statblock": sb,
            "position": _free_start(rng, [0, 5, 10], taken),
        }
        # Inject initial exhaustion / conditions on ~25% of combatants.
        if rng.random() < 0.20:
            entry["exhaustion_level"] = rng.randint(1, 5)
        if rng.random() < 0.25:
            entry["conditions"] = [rng.choice(_SAFE_INITIAL_CONDITIONS)]
        combatants.append(entry)

    for idx, m_slug in enumerate(chosen_monsters, start=1):
        cid = f"mon-{idx}"
        enemy_ids.append(cid)
        m_rec = rules.get_monster(m_slug) or {}
        entry = {
            "id": cid,
            "name": f"{m_rec.get('name', m_slug)} #{idx}",
            "side": "enemy",
            "monster": m_slug,
            "position": _free_start(rng, [5, 10, 15, 20, 25], taken),
        }
        if rng.random() < 0.15:
            entry["exhaustion_level"] = rng.randint(1, 6)
        if rng.random() < 0.25:
            entry["conditions"] = [rng.choice(_SAFE_INITIAL_CONDITIONS)]
        combatants.append(entry)

    return {
        "id": f"procedural-seed-{seed}",
        "name": f"Procedural Skirmish (seed {seed})",
        "description": "Procedurally generated 5e 2024 (SRD 5.2) DST skirmish.",
        "tier": 2,
        "party_level": max(int(sb.get("level", 1)) for sb in chosen_pcs),
        "max_rounds": 15,
        "tactical_themes": ["dst-fuzzing"],
        "combatants": combatants,
        "party_ids": party_ids,
        "enemy_ids": enemy_ids,
        "source": "procedural",
    }


# ----------------------------------------------------------------------
# 3. Random Legal & Adversarial Intent Generators
# ----------------------------------------------------------------------


def _living_combatants(state: dict[str, Any], *, side: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cid in sorted(state["combatants"].keys()):
        c = state["combatants"][cid]
        if side is not None and c.get("side") != side:
            continue
        if c.get("alive") and int(c.get("hp", 0)) > 0:
            out.append(c)
    return out


def _available_slot_levels(actor: dict[str, Any], min_level: int) -> list[int]:
    sc = actor.get("spellcasting")
    if not isinstance(sc, dict):
        return []
    slots = sc.get("slots") or {}
    return sorted(int(k) for k, v in slots.items() if int(v) > 0 and int(k) >= min_level)


def generate_random_legal_intent(
    state: dict[str, Any],
    rules: RulesStore,
    rng: random.Random,
) -> dict[str, Any]:
    """Generate a plausible legal 5e 2024 combat intent exercising all intent types."""
    turn_order = state["turn_order"]
    active_id = turn_order[int(state["active_index"]) % len(turn_order)]
    actor = state["combatants"][active_id]

    # Occasionally trigger an off-turn reaction Shield from another living caster.
    if rng.random() < 0.10:
        reaction_candidates: list[tuple[str, int]] = []
        for cid in sorted(state["combatants"].keys()):
            if cid == active_id:
                continue
            other = state["combatants"][cid]
            if not other.get("alive") or _has_modifier_flag(other, "cannot_act", rules):
                continue
            if not (other.get("turn_resources") or {}).get("reaction"):
                continue
            sc = other.get("spellcasting")
            if not isinstance(sc, dict):
                continue
            prepped = [strip_prefix(s) for s in (sc.get("prepared_spells") or [])]
            if "shield" in prepped:
                avail = _available_slot_levels(other, 1)
                if avail:
                    reaction_candidates.append((cid, avail[0]))
        if reaction_candidates:
            rcid, rslot = rng.choice(reaction_candidates)
            return {
                "type": "cast_spell",
                "actor_id": rcid,
                "spell": "shield",
                "as_reaction": True,
                "slot_level": rslot,
            }

    # If active actor cannot act, end turn cleanly.
    if not actor.get("alive") or int(actor.get("hp", 0)) <= 0 or _has_modifier_flag(actor, "cannot_act", rules):
        return {"type": "end_turn", "actor_id": active_id}

    legal = compute_legal_actions(state, rules)
    if legal.get("can_reroll") and rng.random() < 0.75:
        return {"type": "reroll", "actor_id": active_id}

    res = actor.get("turn_resources") or {}
    my_side = str(actor.get("side") or "party")
    enemies = [
        c
        for c in state["combatants"].values()
        if str(c.get("side")) != my_side and c.get("alive") and int(c.get("hp", 0)) > 0
    ]
    allies = [
        c
        for c in state["combatants"].values()
        if str(c.get("side")) == my_side and c.get("alive")
    ]
    enemies.sort(key=lambda c: c["id"])
    allies.sort(key=lambda c: c["id"])

    candidates: list[dict[str, Any]] = []

    # 1. Move (walk, crawl, or stand up from prone).
    eff_speed = _effective_speed(actor, rules)
    rem_move = min(int(actor.get("movement_remaining_ft", 0)), eff_speed) if eff_speed > 0 else 0
    direct_conds = _direct_condition_set(actor)
    if eff_speed > 0 and rem_move >= 5:
        if "prone" in direct_conds and rem_move >= eff_speed // 2:
            candidates.append({"type": "move", "actor_id": active_id, "stand_up": True})
        elif enemies:
            tgt = rng.choice(enemies)
            cur_x = int(actor["position"]["x"])
            cur_y = int(actor["position"]["y"])
            tx = int(tgt["position"]["x"])
            ty = int(tgt["position"]["y"])
            step_ft = 5 if "prone" not in direct_conds else 5
            max_step = rem_move // 2 if "prone" in direct_conds else rem_move
            if max_step >= 5:
                move_ft = min(max_step, rng.choice([5, 10, 15]))
                dx = tx - cur_x
                dy = ty - cur_y
                nx = cur_x + (min(move_ft, dx) if dx > 0 else max(-move_ft, dx))
                ny = cur_y + (min(move_ft, dy) if dy > 0 else max(-move_ft, dy))
                if (nx, ny) != (cur_x, cur_y):
                    candidates.append(
                        {"type": "move", "actor_id": active_id, "to": {"x": nx, "y": ny}}
                    )

    # 2. Attacks (PC weapons incl. two-handed / offhand / cleave, or monster actions).
    if enemies and (res.get("action") or int(res.get("attacks_remaining", 0)) > 0):
        if actor.get("kind") == "monster":
            for m_act in actor.get("monster_actions") or []:
                aname = str(m_act.get("name") or "")
                if not aname:
                    continue
                is_save_only = m_act.get("attack_bonus") is None and isinstance(m_act.get("save"), dict)
                is_multi = isinstance(m_act.get("multiattack"), dict) or aname.strip().lower() == "multiattack"
                if is_save_only:
                    t_sample = [e["id"] for e in enemies[: rng.randint(1, len(enemies))]]
                    candidates.append(
                        {"type": "attack", "actor_id": active_id, "action": aname, "target_ids": t_sample}
                    )
                elif is_multi:
                    t_sample = [rng.choice(enemies)["id"] for _ in range(min(2, len(enemies)))]
                    candidates.append(
                        {"type": "attack", "actor_id": active_id, "action": aname, "target_ids": t_sample}
                    )
                else:
                    reach = int(m_act.get("reach_ft") or 5)
                    max_r = int(m_act.get("range_long_ft") or m_act.get("range_normal_ft") or reach)
                    in_range = [e for e in enemies if distance_ft(actor["position"], e["position"]) <= max_r]
                    if in_range:
                        tgt = rng.choice(in_range)
                        candidates.append(
                            {"type": "attack", "actor_id": active_id, "action": aname, "target_id": tgt["id"]}
                        )
        else:
            for wp in actor.get("weapons") or []:
                w_slug = str(wp.get("slug") or "")
                w_range = str(wp.get("weapon_range") or "melee").lower()
                reach = int(wp.get("reach_ft") or 5)
                throw_r = wp.get("throw_range")
                max_throw = int(throw_r.get("long_ft") or throw_r.get("normal_ft") or 0) if isinstance(throw_r, dict) else 0
                max_dist = (
                    int(wp.get("range_long_ft") or wp.get("range_normal_ft") or 80)
                    if w_range == "ranged"
                    else max(reach, max_throw)
                )
                in_range = [e for e in enemies if distance_ft(actor["position"], e["position"]) <= max_dist]
                if not in_range:
                    continue
                tgt = rng.choice(in_range)
                atk_intent: dict[str, Any] = {
                    "type": "attack",
                    "actor_id": active_id,
                    "weapon": w_slug,
                    "target_id": tgt["id"],
                    "use_mastery": rng.random() < 0.85,
                }
                if wp.get("versatile_dice") and rng.random() < 0.5:
                    atk_intent["two_handed"] = True
                if wp.get("mastery") == "cleave" and len(in_range) >= 2:
                    others = [
                        e["id"]
                        for e in in_range
                        if e["id"] != tgt["id"]
                        and distance_ft(tgt["position"], e["position"]) <= 5
                        and distance_ft(actor["position"], e["position"]) <= reach
                    ]
                    if others:
                        atk_intent["cleave_target_id"] = rng.choice(others)
                candidates.append(atk_intent)

    # Off-hand Light / Nick attack.
    if enemies and actor.get("kind") == "pc" and (res.get("bonus_action") or not res.get("nick_used")):
        light_weapons = [w for w in (actor.get("weapons") or []) if "light" in (w.get("properties") or [])]
        for wp in light_weapons:
            reach = int(wp.get("reach_ft") or 5)
            throw_r = wp.get("throw_range")
            max_throw = int(throw_r.get("long_ft") or throw_r.get("normal_ft") or 0) if isinstance(throw_r, dict) else 0
            in_range = [e for e in enemies if distance_ft(actor["position"], e["position"]) <= max(reach, max_throw)]
            if in_range:
                candidates.append(
                    {
                        "type": "attack",
                        "actor_id": active_id,
                        "weapon": wp["slug"],
                        "target_id": rng.choice(in_range)["id"],
                        "offhand": True,
                        "use_mastery": True,
                    }
                )

    # 3. Cast spell (cantrips, leveled spells, upcasting, teleport, healing, buffs, narrative).
    sc = actor.get("spellcasting")
    if isinstance(sc, dict):
        legal_spells = list(legal.get("spells") or [])
        for sp_slug in legal_spells:
            sp_rec = rules.get_spell(sp_slug)
            if sp_rec is None:
                continue
            sp_lvl = int(sp_rec.get("level", 0))
            if sp_lvl >= 1:
                avail = _available_slot_levels(actor, sp_lvl)
                if not avail:
                    continue
                chosen_slot = rng.choice(avail)
            else:
                chosen_slot = 0

            sp_intent: dict[str, Any] = {
                "type": "cast_spell",
                "actor_id": active_id,
                "spell": sp_slug,
            }
            if sp_lvl >= 1:
                sp_intent["slot_level"] = chosen_slot

            buffs = list(sp_rec.get("buffs") or [])
            if any(b.get("kind") == "teleport" for b in buffs):
                cur_x = int(actor["position"]["x"])
                cur_y = int(actor["position"]["y"])
                sp_intent["destination"] = {
                    "x": cur_x + rng.choice([-15, -10, -5, 5, 10, 15]),
                    "y": cur_y + rng.choice([-15, -10, -5, 5, 10, 15]),
                }
            elif sp_rec.get("healing"):
                # Target a living ally (prefer wounded or 0-HP unconscious PCs).
                living_allies = [a for a in allies if a.get("alive")]
                if not living_allies:
                    continue
                wounded = [a for a in living_allies if int(a["hp"]) < int(a["hp_max"])]
                tgt = rng.choice(wounded or living_allies)
                sp_intent["target_ids"] = [tgt["id"]]
            elif sp_rec.get("temp_hp") or str(sp_rec.get("range_kind") or "").lower() == "self":
                sp_intent["target_ids"] = [active_id]
            elif str(sp_rec.get("resolution") or "").lower() in ("attack", "save", "auto_hit"):
                if not enemies:
                    continue
                aoe = (sp_rec.get("targeting") or {}).get("aoe")
                if aoe or sp_slug in ("magic-missile", "scorching-ray", "eldritch-blast", "bane"):
                    k = rng.randint(1, min(3, len(enemies)))
                    sp_intent["target_ids"] = [e["id"] for e in rng.sample(enemies, k)]
                else:
                    sp_intent["target_ids"] = [rng.choice(enemies)["id"]]
            else:
                # Buff or narrative utility spell (bless, shield-of-faith, mage-armor, haste, etc.).
                if allies:
                    k = rng.randint(1, min(3, len(allies))) if sp_slug == "bless" else 1
                    sp_intent["target_ids"] = [a["id"] for a in rng.sample(allies, k)]
                else:
                    sp_intent["target_ids"] = [active_id]

            candidates.append(sp_intent)

    # 4. Standard actions: dodge, dash, disengage, help.
    if res.get("action"):
        candidates.append({"type": "dodge", "actor_id": active_id})
        candidates.append({"type": "dash", "actor_id": active_id})
        candidates.append({"type": "disengage", "actor_id": active_id})
        if enemies:
            help_intent: dict[str, Any] = {
                "type": "help",
                "actor_id": active_id,
                "target_id": rng.choice(enemies)["id"],
            }
            other_allies = [a["id"] for a in allies if a["id"] != active_id]
            if other_allies:
                help_intent["ally_id"] = rng.choice(other_allies)
            candidates.append(help_intent)

    # 5. Always include end_turn, biased slightly when no actions remain.
    if not candidates or rng.random() < 0.18:
        return {"type": "end_turn", "actor_id": active_id}

    return rng.choice(candidates)


def generate_adversarial_intent(
    state: dict[str, Any],
    rules: RulesStore,
    rng: random.Random,
) -> Any:
    """Generate an adversarial / malformed fuzz intent to stress error handling & immutability."""
    turn_order = state["turn_order"]
    active_id = turn_order[int(state["active_index"]) % len(turn_order)]
    all_ids = sorted(state["combatants"].keys())
    other_ids = [cid for cid in all_ids if cid != active_id]
    dead_ids = [cid for cid in all_ids if not state["combatants"][cid].get("alive")]
    non_casters = [cid for cid in all_ids if not isinstance(state["combatants"][cid].get("spellcasting"), dict)]

    payloads: list[Any] = [
        # Non-dict / missing type / unsupported type.
        None,
        "attack",
        42,
        [],
        {},
        {"type": ""},
        {"type": "unsupported_teleport_laser", "actor_id": active_id},
        # Unknown actor / unknown target / unknown weapon / unknown spell.
        {"type": "attack", "actor_id": "nonexistent-actor-999", "target_id": active_id, "weapon": "longsword"},
        {"type": "attack", "actor_id": active_id, "target_id": "nonexistent-target-999", "weapon": "longsword"},
        {"type": "attack", "actor_id": active_id, "target_id": other_ids[0] if other_ids else active_id, "weapon": "plasma-rifle"},
        {"type": "attack", "actor_id": active_id, "target_id": other_ids[0] if other_ids else active_id, "action": "Imaginary Breath"},
        {"type": "attack", "actor_id": active_id},  # missing target_id
        {"type": "cast_spell", "actor_id": active_id},  # missing spell
        {"type": "cast_spell", "actor_id": active_id, "spell": "nonexistent-wish-999", "target_ids": [active_id]},
        {"type": "cast_spell", "actor_id": active_id, "spell": "fireball"},  # missing target_ids
        {"type": "cast_spell", "actor_id": active_id, "spell": "fireball", "target_ids": ["missing-tgt"]},
        {"type": "cast_spell", "actor_id": active_id, "spell": "fireball", "slot_level": 1, "target_ids": [active_id]},  # below base level
        {"type": "cast_spell", "actor_id": active_id, "spell": "fireball", "slot_level": 9, "target_ids": [active_id]},  # no L9 slot
        {"type": "cast_spell", "actor_id": active_id, "spell": "misty-step"},  # missing destination
        {"type": "cast_spell", "actor_id": active_id, "spell": "misty-step", "destination": {"x": 9999, "y": -9999}},
        # Negative / huge movement coordinates.
        {"type": "move", "actor_id": active_id, "to": {"x": -100000, "y": 100000}},
        {"type": "move", "actor_id": active_id, "to": {"x": 5000, "y": 5000}},
        # Help with unknown target.
        {"type": "help", "actor_id": active_id, "target_id": "ghost-enemy-404"},
        # Off-hand attack with non-light weapon.
        {"type": "attack", "actor_id": active_id, "weapon": "greatsword", "target_id": other_ids[0] if other_ids else active_id, "offhand": True},
        # Invalid field types (stress type validation).
        {"type": "move", "actor_id": active_id, "to": "not-a-dict"},
        {"type": "move", "actor_id": active_id, "to": {"x": "bad", "y": 0}},
        {"type": "move", "actor_id": active_id, "to": {}},
        {"type": "cast_spell", "actor_id": active_id, "spell": "fireball", "slot_level": "nine", "target_ids": [active_id]},
        {"type": "cast_spell", "actor_id": active_id, "spell": "fireball", "target_ids": 12345},
        {"type": "cast_spell", "actor_id": active_id, "spell": "misty-step", "destination": {"x": "bad"}},
        {"type": "attack", "actor_id": active_id, "weapon": ["not", "a", "str"], "target_id": 123},
        {"type": "cast_spell", "actor_id": active_id, "spell": 999, "target_id": active_id},
        # Invalid / ineligible Heroic Inspiration reroll intents.
        {"type": "reroll", "actor_id": "nonexistent-actor-999"},
    ]

    active_comb = state["combatants"].get(active_id) or {}
    pending_reroll = state.get("pending_reroll")
    if (
        active_comb.get("heroic_inspiration") is not True
        or not isinstance(pending_reroll, dict)
        or pending_reroll.get("actor_id") != active_id
    ):
        payloads.append({"type": "reroll", "actor_id": active_id})

    if other_ids:
        wrong_id = rng.choice(other_ids)
        payloads.extend(
            [
                {"type": "move", "actor_id": wrong_id, "to": {"x": 5, "y": 5}},
                {"type": "dodge", "actor_id": wrong_id},
                {"type": "dash", "actor_id": wrong_id},
                {"type": "disengage", "actor_id": wrong_id},
                {"type": "end_turn", "actor_id": wrong_id},
                {"type": "reroll", "actor_id": wrong_id},
                {"type": "attack", "actor_id": wrong_id, "target_id": active_id, "weapon": "longsword"},
                {"type": "cast_spell", "actor_id": wrong_id, "spell": "fire-bolt", "target_id": active_id},
            ]
        )

    if dead_ids:
        did = rng.choice(dead_ids)
        payloads.extend(
            [
                {"type": "move", "actor_id": did, "to": {"x": 0, "y": 0}},
                {"type": "attack", "actor_id": did, "target_id": active_id, "weapon": "longsword"},
                {"type": "cast_spell", "actor_id": did, "spell": "shield", "as_reaction": True},
                {"type": "reroll", "actor_id": did},
            ]
        )

    if non_casters:
        nc = rng.choice(non_casters)
        payloads.append({"type": "cast_spell", "actor_id": nc, "spell": "shield", "as_reaction": True})

    return rng.choice(payloads)


# ----------------------------------------------------------------------
# 4. Deterministic Simulation Runner
# ----------------------------------------------------------------------


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _execute_single_pass(
    seed: int,
    scenario_key: str,
    max_steps: int,
    adversarial_rate: float,
    rules: RulesStore,
) -> dict[str, Any]:
    if scenario_key == "procedural":
        compiled = build_procedural_scenario(seed, rules)
    else:
        compiled = load_scenario(scenario_key, rules)

    start_intent = {
        "type": "start_encounter",
        "encounter_id": compiled["id"],
        "seed": int(seed),
        "combatants": copy.deepcopy(compiled["combatants"]),
    }

    failures: list[dict[str, Any]] = []
    all_events: list[list[dict[str, Any]]] = []
    steps_executed = 0
    ok_steps = 0
    invalid_steps = 0
    adversarial_steps = 0
    invariant_checks = 0

    repro_cmd = (
        f"bin/srd-dst --seed {seed} --max-steps {max_steps} --adversarial-rate {adversarial_rate}"
    )

    try:
        start_res = step(None, start_intent, rules)
    except Exception as exc:
        failures.append(
            {
                "kind": "exception",
                "seed": int(seed),
                "scenario": compiled["id"],
                "step_index": 0,
                "intent": start_intent,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "repro_cmd": repro_cmd,
            }
        )
        return {
            "seed": int(seed),
            "scenario": compiled["id"],
            "steps_executed": 0,
            "ok_steps": 0,
            "invalid_steps": 0,
            "adversarial_steps": 0,
            "invariant_checks": 0,
            "final_state": None,
            "state_digest": "",
            "events_digest": "",
            "failures": failures,
        }

    if start_res.get("status") != "ok":
        failures.append(
            {
                "kind": "start_failed",
                "seed": int(seed),
                "scenario": compiled["id"],
                "step_index": 0,
                "intent": start_intent,
                "error": str(start_res.get("error") or "start_encounter failed"),
                "repro_cmd": repro_cmd,
            }
        )
        return {
            "seed": int(seed),
            "scenario": compiled["id"],
            "steps_executed": 0,
            "ok_steps": 0,
            "invalid_steps": 0,
            "adversarial_steps": 0,
            "invariant_checks": 0,
            "final_state": start_res.get("state"),
            "state_digest": _canonical_digest(start_res.get("state")),
            "events_digest": _canonical_digest([]),
            "failures": failures,
        }

    state: dict[str, Any] = start_res["state"]
    initial_max_slots = capture_initial_max_slots(state)
    all_events.append(list(start_res.get("events") or []))

    invariant_checks += 1
    start_violations = check_state_invariants(
        state,
        prev_state=None,
        intent=start_intent,
        result=start_res,
        initial_max_slots=initial_max_slots,
    )
    if start_violations:
        failures.append(
            {
                "kind": "invariant_violation",
                "seed": int(seed),
                "scenario": compiled["id"],
                "step_index": 0,
                "intent": {"type": "start_encounter", "encounter_id": compiled["id"], "seed": int(seed)},
                "violations": start_violations,
                "repro_cmd": repro_cmd,
            }
        )

    fuzz_rng = random.Random(int(seed) * 1000003 + 17)

    for step_idx in range(1, max(0, int(max_steps)) + 1):
        if state.get("status") == "ended" and fuzz_rng.random() >= adversarial_rate:
            break

        is_adversarial = fuzz_rng.random() < float(adversarial_rate)
        if is_adversarial:
            intent = generate_adversarial_intent(state, rules, fuzz_rng)
            adversarial_steps += 1
        else:
            intent = generate_random_legal_intent(state, rules, fuzz_rng)

        before_snapshot = copy.deepcopy(state)
        steps_executed += 1

        try:
            res = step(state, intent, rules)
        except Exception as exc:
            failures.append(
                {
                    "kind": "exception",
                    "seed": int(seed),
                    "scenario": compiled["id"],
                    "step_index": step_idx,
                    "adversarial": is_adversarial,
                    "intent": intent,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                    "repro_cmd": repro_cmd,
                }
            )
            # Keep original state uncorrupted if possible and stop this seed on crash.
            break

        next_state = res.get("state")
        if res.get("status") == "ok":
            ok_steps += 1
        else:
            invalid_steps += 1

        all_events.append(list(res.get("events") or []))

        invariant_checks += 1
        violations = check_state_invariants(
            next_state,
            prev_state=before_snapshot,
            intent=intent if isinstance(intent, dict) else None,
            result=res,
            initial_max_slots=initial_max_slots,
            input_state_snapshot=before_snapshot,
            input_state_after=state,
        )
        if violations:
            failures.append(
                {
                    "kind": "invariant_violation",
                    "seed": int(seed),
                    "scenario": compiled["id"],
                    "step_index": step_idx,
                    "adversarial": is_adversarial,
                    "intent": intent,
                    "status": res.get("status"),
                    "violations": violations,
                    "repro_cmd": repro_cmd,
                }
            )

        if res.get("status") == "ok" and isinstance(next_state, dict):
            state = next_state
        else:
            # Ensure we continue from the pristine snapshot even if engine misbehaved.
            state = before_snapshot

    return {
        "seed": int(seed),
        "scenario": compiled["id"],
        "steps_executed": steps_executed,
        "ok_steps": ok_steps,
        "invalid_steps": invalid_steps,
        "adversarial_steps": adversarial_steps,
        "invariant_checks": invariant_checks,
        "final_state": state,
        "state_digest": _canonical_digest(state),
        "events_digest": _canonical_digest(all_events),
        "failures": failures,
    }


def run_dst_seed(
    seed: int,
    scenario: str | None = None,
    max_steps_per_seed: int = 60,
    adversarial_rate: float = 0.25,
    rules: RulesStore | None = None,
    *,
    verify_determinism: bool = True,
) -> dict[str, Any]:
    """Run a single seeded DST simulation and verify 100% byte-identical determinism."""
    rules_store = rules if rules is not None else RulesStore()
    scenario_key = (
        scenario
        if scenario is not None
        else _DEFAULT_SCENARIO_POOL[(int(seed) - 1) % len(_DEFAULT_SCENARIO_POOL)]
    )

    run1 = _execute_single_pass(seed, scenario_key, max_steps_per_seed, adversarial_rate, rules_store)
    deterministic = True

    if verify_determinism:
        run2 = _execute_single_pass(seed, scenario_key, max_steps_per_seed, adversarial_rate, rules_store)
        if run1["state_digest"] != run2["state_digest"] or run1["events_digest"] != run2["events_digest"]:
            deterministic = False
            run1["failures"].append(
                {
                    "kind": "determinism_mismatch",
                    "seed": int(seed),
                    "scenario": run1["scenario"],
                    "step_index": run1["steps_executed"],
                    "intent": None,
                    "error": (
                        f"Non-deterministic replay for seed {seed}: "
                        f"state_digest {run1['state_digest'][:12]} != {run2['state_digest'][:12]}, "
                        f"events_digest {run1['events_digest'][:12]} != {run2['events_digest'][:12]}"
                    ),
                    "repro_cmd": (
                        f"bin/srd-dst --seed {seed} --max-steps {max_steps_per_seed} "
                        f"--adversarial-rate {adversarial_rate}"
                    ),
                }
            )

    return {
        "seed": run1["seed"],
        "scenario": run1["scenario"],
        "steps_executed": run1["steps_executed"],
        "ok_steps": run1["ok_steps"],
        "invalid_steps": run1["invalid_steps"],
        "adversarial_steps": run1["adversarial_steps"],
        "invariant_checks": run1["invariant_checks"],
        "deterministic": deterministic,
        "state_digest": run1["state_digest"],
        "events_digest": run1["events_digest"],
        "passed": len(run1["failures"]) == 0,
        "failures": run1["failures"],
    }


def run_dst_suite(
    seeds: int | Iterable[int] = 25,
    scenarios: Iterable[str] | None = None,
    max_steps_per_seed: int = 60,
    adversarial_rate: float = 0.25,
    rules: RulesStore | None = None,
) -> dict[str, Any]:
    """Run a multi-seed Deterministic Simulation Testing & stateful fuzzing suite."""
    rules_store = rules if rules is not None else RulesStore()

    if isinstance(seeds, int):
        seed_list = list(range(1, max(1, seeds) + 1))
    else:
        seed_list = [int(s) for s in seeds]

    scenario_pool = list(scenarios) if scenarios is not None else list(_DEFAULT_SCENARIO_POOL)
    if not scenario_pool:
        scenario_pool = list(_DEFAULT_SCENARIO_POOL)

    seed_results: list[dict[str, Any]] = []
    all_failures: list[dict[str, Any]] = []

    for idx, seed in enumerate(seed_list):
        scen = scenario_pool[idx % len(scenario_pool)]
        res = run_dst_seed(
            seed=seed,
            scenario=scen,
            max_steps_per_seed=max_steps_per_seed,
            adversarial_rate=adversarial_rate,
            rules=rules_store,
            verify_determinism=True,
        )
        seed_results.append(res)
        all_failures.extend(res["failures"])

    total_steps = sum(r["steps_executed"] for r in seed_results)
    total_ok = sum(r["ok_steps"] for r in seed_results)
    total_invalid = sum(r["invalid_steps"] for r in seed_results)
    total_adversarial = sum(r["adversarial_steps"] for r in seed_results)
    total_checks = sum(r["invariant_checks"] for r in seed_results)
    determinism_failures = sum(1 for f in all_failures if f["kind"] == "determinism_mismatch")
    exception_failures = sum(1 for f in all_failures if f["kind"] == "exception")
    invariant_failures = sum(1 for f in all_failures if f["kind"] == "invariant_violation")
    failing_seeds = sorted({int(f["seed"]) for f in all_failures})

    return {
        "schema_version": "4",
        "dataset_id": rules_store.dataset_id,
        "status": "passed" if not all_failures else "failed",
        "passed": len(all_failures) == 0,
        "config": {
            "seeds_count": len(seed_list),
            "seeds": seed_list,
            "scenarios": scenario_pool,
            "max_steps_per_seed": int(max_steps_per_seed),
            "adversarial_rate": float(adversarial_rate),
        },
        "summary": {
            "seeds_run": len(seed_list),
            "seeds_passed": sum(1 for r in seed_results if r["passed"]),
            "seeds_failed": len(failing_seeds),
            "total_steps": total_steps,
            "ok_steps": total_ok,
            "invalid_steps": total_invalid,
            "adversarial_steps": total_adversarial,
            "invariant_checks": total_checks,
            "exception_failures": exception_failures,
            "invariant_failures": invariant_failures,
            "determinism_failures": determinism_failures,
            "total_failures": len(all_failures),
            "deterministic_all": determinism_failures == 0,
        },
        "failing_seeds": failing_seeds,
        "failures": all_failures,
        "seed_results": seed_results,
    }


# ----------------------------------------------------------------------
# 5. Markdown Renderer & CLI Entry Point (`bin/srd-dst`)
# ----------------------------------------------------------------------


def render_dst_markdown(report: dict[str, Any]) -> str:
    """Render a DST suite report as compact GitHub-flavored Markdown."""
    s = report["summary"]
    cfg = report["config"]
    det_summary = (
        "100% byte-identical"
        if s["deterministic_all"]
        else f"{s['determinism_failures']} mismatch(es)"
    )
    lines = [
        "# 5e 2024 (SRD 5.2) Deterministic Simulation Testing Report",
        "",
        f"- **Status:** `{report['status'].upper()}`",
        f"- **Dataset:** `{report['dataset_id']}`",
        f"- **Seeds:** {s['seeds_passed']}/{s['seeds_run']} passed (max {cfg['max_steps_per_seed']} steps/seed, adversarial rate {cfg['adversarial_rate']:.2f})",
        f"- **Steps Executed:** {s['total_steps']} ({s['ok_steps']} ok, {s['invalid_steps']} rejected, {s['adversarial_steps']} adversarial)",
        f"- **Invariant Checks:** {s['invariant_checks']} ({s['invariant_failures']} violations)",
        f"- **Unhandled Exceptions:** {s['exception_failures']}",
        f"- **Determinism:** {det_summary}",
    ]

    if report["failures"]:
        lines.extend(["", "## Failures", ""])
        for f in report["failures"][:20]:
            lines.append(
                f"- **Seed `{f['seed']}`** (`{f['scenario']}`, step {f['step_index']}, `{f['kind']}`): "
                f"{f.get('error') or '; '.join(f.get('violations') or [])}"
            )
            lines.append(f"  - Repro: `{f['repro_cmd']}`")
            lines.append(f"  - Intent: `{json.dumps(f.get('intent'), sort_keys=True)}`")
        if len(report["failures"]) > 20:
            lines.append(f"- ... and {len(report['failures']) - 20} more failure(s).")
    else:
        lines.extend(["", "All seeds passed with zero invariant violations, zero crashes, and 100% determinism."])

    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="srd-dst",
        description="Deterministic Simulation Testing & stateful fuzzer for the 5e 2024 (SRD 5.2) combat engine.",
    )
    parser.add_argument("--seeds", type=int, default=25, help="Number of sequential seeds to run (default: 25).")
    parser.add_argument("--seed", type=int, default=None, help="Run a single explicit seed (overrides --seeds).")
    parser.add_argument("--max-steps", type=int, default=60, help="Maximum steps per seed (default: 60).")
    parser.add_argument(
        "--adversarial-rate",
        type=float,
        default=0.25,
        help="Fraction of steps using malformed/adversarial intents (default: 0.25).",
    )
    parser.add_argument(
        "--scenarios",
        type=str,
        default=None,
        help="Optional comma-separated scenario list (built-ins and/or 'procedural').",
    )
    parser.add_argument("--rules", type=str, default=None, help="Optional path to compiled rules.json.")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    return parser


def main_dst(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    rules = RulesStore(args.rules)
    scenarios: list[str] | None = None
    if args.scenarios:
        scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    if args.seed is not None:
        # Preserve exact scenario assignment for single-seed repro when --scenarios not given.
        if scenarios is None:
            scen = _DEFAULT_SCENARIO_POOL[(int(args.seed) - 1) % len(_DEFAULT_SCENARIO_POOL)]
            scenarios = [scen]
        seeds_arg: int | list[int] = [int(args.seed)]
    else:
        seeds_arg = max(1, int(args.seeds))

    report = run_dst_suite(
        seeds=seeds_arg,
        scenarios=scenarios,
        max_steps_per_seed=max(1, int(args.max_steps)),
        adversarial_rate=max(0.0, min(1.0, float(args.adversarial_rate))),
        rules=rules,
    )

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_dst_markdown(report))

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main_dst())
