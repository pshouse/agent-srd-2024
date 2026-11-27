"""Stage 4 Engine Hardening & Mutant-Killing Tests (5e 2024 / SRD 5.2).

Pure Python 3.11+ stdlib `unittest` suite covering:
1. Regression tests for all 6 real bugs in `src/srd2024/engine/combat.py` uncovered by
   DST & stateful fuzzing:
   - `start_encounter.seed` non-int / bool rejection -> `invalid_intent` + untouched state
   - `move.to` malformed dict / missing keys / bool / non-int rejection -> `invalid_intent`
   - `cast_spell.slot_level` non-int / bool rejection -> `invalid_intent`
   - `cast_spell.target_ids` non-list/tuple rejection (int, str, dict, bool) -> `invalid_intent`
   - `cast_spell.destination` malformed dict / missing keys / bool / non-int rejection -> `invalid_intent`
   - 5e 2024 healing rules: normal healing spells MUST NOT heal or resurrect dead
     (`alive == False`) monsters or dead PCs, while still reviving living unconscious
     (`alive == True, hp == 0`) PCs (removing `unconscious`, keeping `prone`, resetting death saves).
2. High-signal mutant-killing tests for `src/srd2024/engine/dice.py`:
   - Live SHA-256 PRNG draw range `1..sides` across d4/d6/d8/d10/d12/d20 and exact SHA-256 draw check
   - `draw_index` monotonic +1 progression per live roll & `from_dict` resumption
   - `roll_d20` advantage/disadvantage hitting 20 in both roll slots & picking exact max/min
   - `scale_dice_expr` with `steps=2` and `steps=3` (dice counts & flat modifiers)
   - `roll_dice_expr` flat/negative modifiers, negative dice terms, and crit doubling dice only
3. High-signal mutant-killing tests for `src/srd2024/engine/character.py`:
   - Level 4 ASI feat prerequisite enforcement (e.g. Grappler with STR 10 & DEX 10 rejected on prereq)
   - Ability cap 20 enforcement (`abilities[ab] + delta > cap`) across ASI & feat ability increases
   - `hp_method="rolled"` with explicit `hp_rolls` (exact HP math & out-of-range roll rejection)
   - Non-Alert initiative strictly equals `dex_mod` (and Alert adds proficiency bonus)
4. High-signal mutant-killing tests for `src/srd2024/engine/combat.py`:
   - Level 5 Fighter blocked on 3rd attack after exhausting `extra_attacks`
   - Monster action consumed so 2nd attack same turn is rejected
   - Bless `+1d4` & Bane `-1d4` exact sign on BOTH monster and PC attack rolls
   - Exhaustion `-2 * level` exact sign on initiative rolls and turn order
   - `incapacitated` blocks actions & breaks concentration, while non-incapacitating conditions
     (e.g. `prone` / `poisoned`) do NOT break concentration.
"""

from __future__ import annotations

import copy
import hashlib
import unittest
from typing import Any

from srd2024.engine.character import compile_character
from srd2024.engine.combat import step
from srd2024.engine.dice import DeterministicRNG, parse_dice_expr, roll_dice_expr, scale_dice_expr
from srd2024.engine.store import RulesStore
from srd2024.testing import capture_initial_max_slots, check_state_invariants


def _make_fighter(
    rules: RulesStore,
    *,
    name: str = "Aldric",
    level: int = 5,
    weapons: list[str] | None = None,
    mastery_weapons: list[str] | None = None,
    extra_origin_feat: str | None = "alert",
) -> dict[str, Any]:
    weapons = weapons or ["longsword", "greatsword", "battleaxe"]
    mastery_weapons = mastery_weapons if mastery_weapons is not None else weapons[:4]
    spec: dict[str, Any] = {
        "name": name,
        "species": "human",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "class": "fighter",
        "subclass": "champion" if level >= 3 else None,
        "level": level,
        "ability_method": "point_buy",
        "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
        "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}] if level >= 4 else [],
        "fighting_style": "defense",
        "equipment": {"armor": "chain-mail", "shield": True, "weapons": weapons},
        "weapon_mastery_weapons": mastery_weapons,
        "hp_method": "fixed",
    }
    if extra_origin_feat:
        spec["extra_origin_feat"] = extra_origin_feat
    res = compile_character(spec, rules)
    assert res["valid"], f"Fighter fixture invalid: {res['errors']}"
    return res["statblock"]


def _make_wizard(rules: RulesStore, *, name: str = "Lyra", level: int = 5) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": name,
        "species": "elf",
        "subspecies": "elven-lineage-high-elf",
        "background": "sage",
        "background_asi": {"int": 2, "con": 1},
        "origin_feat": "magic-initiate",
        "class": "wizard",
        "subclass": "evoker" if level >= 3 else None,
        "level": level,
        "ability_method": "standard_array",
        "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
        "asi_choices": [{"level": 4, "type": "asi", "increases": {"int": 2}}] if level >= 4 else [],
        "equipment": {"armor": None, "shield": False, "weapons": ["quarterstaff"]},
        "cantrips": ["fire-bolt", "prestidigitation"],
        "prepared_spells": ["fireball", "shield", "magic-missile", "misty-step", "hold-person"],
        "hp_method": "fixed",
    }
    res = compile_character(spec, rules)
    assert res["valid"], f"Wizard fixture invalid: {res['errors']}"
    return res["statblock"]


def _make_cleric(
    rules: RulesStore,
    *,
    name: str = "Dorian",
    level: int = 5,
    prepared_spells: list[str] | None = None,
) -> dict[str, Any]:
    if prepared_spells is None:
        prepared_spells = ["bless", "bane", "cure-wounds", "healing-word", "mass-healing-word", "hold-person"]
    spec: dict[str, Any] = {
        "name": name,
        "species": "human",
        "background": "acolyte",
        "background_asi": {"wis": 2, "cha": 1},
        "origin_feat": "magic-initiate",
        "class": "cleric",
        "subclass": "life-domain" if level >= 3 else None,
        "level": level,
        "ability_method": "standard_array",
        "base_abilities": {"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 15, "cha": 12},
        "asi_choices": [{"level": 4, "type": "asi", "increases": {"wis": 2}}] if level >= 4 else [],
        "equipment": {"armor": "chain-shirt", "shield": True, "weapons": ["mace"]},
        "cantrips": ["sacred-flame"],
        "prepared_spells": prepared_spells,
        "hp_method": "fixed",
    }
    res = compile_character(spec, rules)
    assert res["valid"], f"Cleric fixture invalid: {res['errors']}"
    return res["statblock"]


def _start(
    rules: RulesStore,
    combatants: list[dict[str, Any]],
    *,
    seed: int = 42,
    init_rolls: list[int] | None = None,
) -> dict[str, Any]:
    if init_rolls is None:
        # Ensure combatants[0] acts first even when another combatant has Alert (+5).
        init_rolls = [20] + [max(1, 12 - i * 3) for i in range(len(combatants) - 1)]
    res = step(
        None,
        {"type": "start_encounter", "seed": seed, "combatants": combatants},
        rules,
        roll_overrides=init_rolls,
    )
    assert res["status"] == "ok", f"start_encounter failed: {res}"
    return res["state"]


def _condition_names(combatant: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for c in combatant.get("conditions", []):
        out.add(c["condition"] if isinstance(c, dict) else str(c))
    return out


class TestDSTCombatHardening(unittest.TestCase):
    """Regression tests for all 6 real bugs in `src/srd2024/engine/combat.py`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(cls.rules)
        cls.wizard = _make_wizard(cls.rules)
        cls.cleric = _make_cleric(cls.rules)

    def _assert_invalid_untouched(self, state: dict[str, Any] | None, intent: dict[str, Any]) -> dict[str, Any]:
        before = copy.deepcopy(state)
        res = step(state, intent, self.rules)
        self.assertEqual(res["status"], "invalid_intent", f"Expected invalid_intent for {intent!r}, got {res!r}")
        self.assertTrue(res.get("error"), f"Expected error message for {intent!r}")
        self.assertEqual(state, before, f"Input state mutated on invalid_intent: {intent!r}")
        expected_returned = before if isinstance(before, dict) else {}
        self.assertEqual(res["state"], expected_returned, f"Returned state modified on invalid_intent: {intent!r}")
        return res

    def test_start_encounter_rejects_malformed_seed(self) -> None:
        base_combatants = [
            {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
        ]
        existing = _start(self.rules, copy.deepcopy(base_combatants), seed=11)

        for bad_seed in ("bad", "42", None, True, False, 3.14, [], {}):
            intent = {"type": "start_encounter", "seed": bad_seed, "combatants": copy.deepcopy(base_combatants)}
            self._assert_invalid_untouched(None, intent)
            self._assert_invalid_untouched(existing, intent)

        # Valid integer seeds (including 0 and negative) still succeed.
        for good_seed in (0, -7, 12345):
            ok_res = step(
                None,
                {"type": "start_encounter", "seed": good_seed, "combatants": copy.deepcopy(base_combatants)},
                self.rules,
            )
            self.assertEqual(ok_res["status"], "ok")
            self.assertEqual(ok_res["state"]["rng"]["seed"], good_seed)

    def test_move_rejects_malformed_to_field_and_bools(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}},
            ],
        )
        bad_targets: list[Any] = [
            "not-a-dict",
            123,
            [5, 0],
            {},
            {"x": 5},
            {"y": 5},
            {"x": "bad", "y": 0},
            {"x": 0, "y": "bad"},
            {"x": "", "y": 0},
            {"x": None, "y": 0},
            {"x": True, "y": 0},
            {"x": 0, "y": False},
            {"x": 5.5, "y": 0},
        ]
        for bad_to in bad_targets:
            self._assert_invalid_untouched(st, {"type": "move", "actor_id": "pc-1", "to": bad_to})

        # Valid int and int-convertible string coordinates succeed.
        ok_int = step(st, {"type": "move", "actor_id": "pc-1", "to": {"x": 5, "y": 0}}, self.rules)
        self.assertEqual(ok_int["status"], "ok")
        self.assertEqual(ok_int["state"]["combatants"]["pc-1"]["position"], {"x": 5, "y": 0})

        ok_str = step(st, {"type": "move", "actor_id": "pc-1", "to": {"x": "10", "y": "0"}}, self.rules)
        self.assertEqual(ok_str["status"], "ok")
        self.assertEqual(ok_str["state"]["combatants"]["pc-1"]["position"], {"x": 10, "y": 0})

    def test_cast_spell_rejects_malformed_slot_level_and_bools(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "wiz-1", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}},
            ],
        )
        for bad_slot in ("nine", "3", True, False, 3.0, [], {}):
            self._assert_invalid_untouched(
                st,
                {
                    "type": "cast_spell",
                    "actor_id": "wiz-1",
                    "spell": "fireball",
                    "slot_level": bad_slot,
                    "target_ids": ["gob-1"],
                },
            )

    def test_cast_spell_rejects_non_sequence_target_ids(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "wiz-1", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}},
            ],
        )
        for bad_targets in (12345, 0, "gob-1", "", {"id": "gob-1"}, {}, True, False):
            self._assert_invalid_untouched(
                st,
                {
                    "type": "cast_spell",
                    "actor_id": "wiz-1",
                    "spell": "fireball",
                    "slot_level": 3,
                    "target_ids": bad_targets,
                },
            )

        # Tuples and lists of valid target IDs succeed.
        ok_tuple = step(
            st,
            {"type": "cast_spell", "actor_id": "wiz-1", "spell": "magic-missile", "slot_level": 1, "target_ids": ("gob-1",)},
            self.rules,
            roll_overrides=[2, 2, 2],
        )
        self.assertEqual(ok_tuple["status"], "ok")

    def test_cast_spell_teleport_rejects_malformed_destination_and_bools(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "wiz-1", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}},
            ],
        )
        bad_dests: list[Any] = [
            None,
            "not-a-dict",
            [10, 0],
            {},
            {"x": 10},
            {"y": 10},
            {"x": "bad", "y": 0},
            {"x": 0, "y": "bad"},
            {"x": True, "y": 0},
            {"x": 0, "y": False},
            {"x": 10.5, "y": 0},
        ]
        for bad_dest in bad_dests:
            self._assert_invalid_untouched(
                st,
                {
                    "type": "cast_spell",
                    "actor_id": "wiz-1",
                    "spell": "misty-step",
                    "slot_level": 2,
                    "destination": bad_dest,
                },
            )

        ok_dest = step(
            st,
            {
                "type": "cast_spell",
                "actor_id": "wiz-1",
                "spell": "misty-step",
                "slot_level": 2,
                "destination": {"x": "15", "y": "0"},
            },
            self.rules,
        )
        self.assertEqual(ok_dest["status"], "ok")
        self.assertEqual(ok_dest["state"]["combatants"]["wiz-1"]["position"], {"x": 15, "y": 0})

    def test_healing_never_heals_or_resurrects_dead_creatures_but_revives_unconscious_pc(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "clr-1", "side": "party", "statblock": self.cleric, "position": {"x": 0, "y": 0}},
                {"id": "pc-dead", "side": "party", "statblock": self.fighter, "position": {"x": 5, "y": 0}},
                {"id": "pc-unconc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 5}},
                {"id": "gob-dead", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 5}},
            ],
        )
        max_slots = capture_initial_max_slots(st)

        # Dead PC (3 failed death saves, alive=False, hp=0).
        st["combatants"]["pc-dead"]["hp"] = 0
        st["combatants"]["pc-dead"]["alive"] = False
        st["combatants"]["pc-dead"]["death_saves"] = {"successes": 0, "failures": 3}

        # Dead monster (alive=False, hp=0).
        st["combatants"]["gob-dead"]["hp"] = 0
        st["combatants"]["gob-dead"]["alive"] = False

        # Living unconscious PC (alive=True, hp=0, unconscious + prone, partial death saves).
        st["combatants"]["pc-unconc"]["hp"] = 0
        st["combatants"]["pc-unconc"]["alive"] = True
        st["combatants"]["pc-unconc"]["death_saves"] = {"successes": 1, "failures": 2}
        st["combatants"]["pc-unconc"]["conditions"] = [
            {"condition": "unconscious", "source_id": None, "duration": "indefinite"},
            {"condition": "prone", "source_id": None, "duration": "indefinite"},
        ]

        # 1. Cure Wounds on dead monster MUST NOT heal it (`dead_with_positive_hp` prevented).
        r_mon = step(
            st,
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "cure-wounds", "slot_level": 1, "target_ids": ["gob-dead"]},
            self.rules,
            roll_overrides=[6, 6],
        )
        self.assertEqual(r_mon["status"], "ok")
        gob_after = r_mon["state"]["combatants"]["gob-dead"]
        self.assertFalse(gob_after["alive"])
        self.assertEqual(gob_after["hp"], 0)
        heal_ev_mon = next(e for e in r_mon["events"] if e["type"] == "healing_applied")
        self.assertEqual(heal_ev_mon["amount"], 0)
        self.assertFalse(heal_ev_mon["revived"])
        self.assertEqual(heal_ev_mon.get("reason"), "target_dead")
        self.assertEqual(check_state_invariants(r_mon["state"], initial_max_slots=max_slots), [])

        # 2. Healing Word on dead PC MUST NOT resurrect or heal it.
        r_pc_dead = step(
            st,
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "healing-word", "slot_level": 1, "target_ids": ["pc-dead"]},
            self.rules,
            roll_overrides=[4, 4],
        )
        self.assertEqual(r_pc_dead["status"], "ok")
        dead_pc_after = r_pc_dead["state"]["combatants"]["pc-dead"]
        self.assertFalse(dead_pc_after["alive"])
        self.assertEqual(dead_pc_after["hp"], 0)
        self.assertEqual(dead_pc_after["death_saves"]["failures"], 3)
        self.assertEqual(check_state_invariants(r_pc_dead["state"], initial_max_slots=max_slots), [])

        # 3. Mass Healing Word targeting dead PC + dead monster + living unconscious PC:
        #    revives living unconscious PC (removes unconscious, keeps prone, resets death saves)
        #    while leaving BOTH dead creatures dead at 0 HP!
        r_mass = step(
            st,
            {
                "type": "cast_spell",
                "actor_id": "clr-1",
                "spell": "mass-healing-word",
                "slot_level": 3,
                "target_ids": ["pc-dead", "gob-dead", "pc-unconc"],
            },
            self.rules,
            roll_overrides=[3, 4],
        )
        self.assertEqual(r_mass["status"], "ok")
        self.assertFalse(r_mass["state"]["combatants"]["pc-dead"]["alive"])
        self.assertEqual(r_mass["state"]["combatants"]["pc-dead"]["hp"], 0)
        self.assertFalse(r_mass["state"]["combatants"]["gob-dead"]["alive"])
        self.assertEqual(r_mass["state"]["combatants"]["gob-dead"]["hp"], 0)

        revived_pc = r_mass["state"]["combatants"]["pc-unconc"]
        self.assertTrue(revived_pc["alive"])
        self.assertGreater(revived_pc["hp"], 0)
        self.assertNotIn("unconscious", _condition_names(revived_pc))
        self.assertIn("prone", _condition_names(revived_pc))
        self.assertEqual(revived_pc["death_saves"], {"successes": 0, "failures": 0})
        self.assertEqual(check_state_invariants(r_mass["state"], initial_max_slots=max_slots), [])


class TestDiceEngineMutantKills(unittest.TestCase):
    """Kill surviving mutants in `src/srd2024/engine/dice.py`."""

    def test_live_sha256_prng_range_exact_formula_and_draw_index_progression(self) -> None:
        # Verify exact SHA-256 draw value and strict +1 draw_index progression across standard dice.
        for sides in (4, 6, 8, 10, 12, 20):
            rng = DeterministicRNG(seed=100 + sides, draw_index=0)
            draws: list[int] = []
            for expected_idx in range(240):
                self.assertEqual(rng.draw_index, expected_idx)
                val = rng.roll_die(sides)
                self.assertEqual(rng.draw_index, expected_idx + 1)
                self.assertGreaterEqual(val, 1, f"d{sides} draw below 1: {val}")
                self.assertLessEqual(val, sides, f"d{sides} draw above {sides}: {val}")
                if expected_idx < 5:
                    token = f"{100 + sides}:{expected_idx}:{sides}".encode("utf-8")
                    expected_val = (int.from_bytes(hashlib.sha256(token).digest()[:8], "big") % sides) + 1
                    self.assertEqual(val, expected_val)
                draws.append(val)
            self.assertEqual(min(draws), 1, f"d{sides} never rolled 1 over 240 live draws")
            self.assertEqual(max(draws), sides, f"d{sides} never rolled {sides} over 240 live draws")
            self.assertEqual(rng.to_dict(), {"seed": 100 + sides, "draw_index": 240})

        # Resuming via from_dict continues seamlessly from draw_index.
        base = DeterministicRNG(seed=77)
        for _ in range(5):
            base.roll_die(20)
        resumed = DeterministicRNG.from_dict(base.to_dict())
        self.assertEqual([base.roll_die(20) for _ in range(10)], [resumed.roll_die(20) for _ in range(10)])
        self.assertEqual(DeterministicRNG.from_dict(None).to_dict(), {"seed": 1, "draw_index": 0})

        # Override clamping and non-consumption of draw_index, plus invalid sides error.
        ovr = DeterministicRNG(seed=9, roll_overrides=[0, 99, 4])
        self.assertEqual(ovr.remaining_overrides, 3)
        self.assertEqual(ovr.roll_die(6), 1)
        self.assertEqual(ovr.roll_die(6), 6)
        self.assertEqual(ovr.roll_die(6), 4)
        self.assertEqual(ovr.draw_index, 0)
        with self.assertRaises(ValueError):
            ovr.roll_die(0)
        with self.assertRaises(ValueError):
            ovr.roll_die(-4)

    def test_roll_d20_advantage_disadvantage_hits_20_in_both_slots_and_picks_max_min(self) -> None:
        for mode, picker in (("advantage", max), ("disadvantage", min)):
            rng = DeterministicRNG(seed=31415 if mode == "advantage" else 27182)
            slot0_vals: list[int] = []
            slot1_vals: list[int] = []
            saw_r1_gt_r2 = False
            saw_r1_lt_r2 = False
            for idx in range(250):
                before_idx = rng.draw_index
                out = rng.roll_d20(mode)
                self.assertEqual(rng.draw_index, before_idx + 2)
                self.assertEqual(out["mode"], mode)
                self.assertEqual(len(out["rolls"]), 2)
                r1, r2 = out["rolls"]
                self.assertTrue(1 <= r1 <= 20 and 1 <= r2 <= 20)
                self.assertEqual(out["chosen"], picker(r1, r2))
                self.assertEqual(out["nat20"], out["chosen"] == 20)
                self.assertEqual(out["nat1"], out["chosen"] == 1)
                slot0_vals.append(r1)
                slot1_vals.append(r2)
                if r1 > r2:
                    saw_r1_gt_r2 = True
                elif r1 < r2:
                    saw_r1_lt_r2 = True
            # Explicitly kills roll_die(20) -> roll_die(19) on BOTH first and second roll lines!
            self.assertEqual(max(slot0_vals), 20, f"{mode} slot 0 never hit 20")
            self.assertEqual(max(slot1_vals), 20, f"{mode} slot 1 never hit 20")
            self.assertEqual(min(slot0_vals), 1, f"{mode} slot 0 never hit 1")
            self.assertEqual(min(slot1_vals), 1, f"{mode} slot 1 never hit 1")
            self.assertTrue(saw_r1_gt_r2 and saw_r1_lt_r2)

        with self.assertRaises(ValueError):
            DeterministicRNG(seed=1).roll_d20("super_advantage")

    def test_scale_dice_expr_multi_step_and_roll_dice_expr_modifiers_and_crit(self) -> None:
        # Multi-step scaling kills count * steps -> count // steps and flat scaling mutations.
        self.assertEqual(scale_dice_expr("8d6", "1d6", 0), "8d6")
        self.assertEqual(scale_dice_expr("8d6", None, 3), "8d6")
        self.assertEqual(scale_dice_expr("8d6", "1d6", 2), "10d6")
        self.assertEqual(scale_dice_expr("8d6", "1d6", 3), "11d6")
        self.assertEqual(scale_dice_expr("2d8", "2d8", 2), "6d8")
        self.assertEqual(scale_dice_expr("2d8", "2d8", 3), "8d8")
        self.assertEqual(scale_dice_expr("1d4+1", "1d4+1", 2), "3d4+3")
        self.assertEqual(scale_dice_expr("1d4+1", "1d4+1", 3), "4d4+4")
        self.assertEqual(scale_dice_expr("1d8", "1d6", 2), "1d8+2d6")
        self.assertEqual(scale_dice_expr("1d6-1", "-2", 2), "1d6-5")

        # parse_dice_expr validation & unicode minus.
        self.assertEqual(parse_dice_expr("2d6 \u2212 3"), ([(1, 2, 6)], -3))
        self.assertEqual(parse_dice_expr("0"), ([], 0))
        for bad_expr in ("", "   ", "2x6", "1d8 1d4"):
            with self.assertRaises(ValueError):
                parse_dice_expr(bad_expr)

        # roll_dice_expr: flat/negative modifiers & crit doubles dice ONLY.
        r_norm = roll_dice_expr("2d6+3", DeterministicRNG(roll_overrides=[4, 5]), crit=False)
        self.assertEqual(r_norm["rolls"], [4, 5])
        self.assertEqual(r_norm["modifier"], 3)
        self.assertEqual(r_norm["total"], 12)
        self.assertFalse(r_norm["crit"])

        r_crit = roll_dice_expr("2d6+3", DeterministicRNG(roll_overrides=[4, 5, 6, 2]), crit=True)
        self.assertEqual(r_crit["rolls"], [4, 5, 6, 2])
        self.assertEqual(r_crit["modifier"], 3)
        self.assertEqual(r_crit["total"], 20)
        self.assertTrue(r_crit["crit"])

        r_neg = roll_dice_expr("1d8-2", DeterministicRNG(roll_overrides=[6, 7]), crit=True)
        self.assertEqual(r_neg["rolls"], [6, 7])
        self.assertEqual(r_neg["modifier"], -2)
        self.assertEqual(r_neg["total"], 11)

        r_sub_die = roll_dice_expr("2d6-1d4+1", DeterministicRNG(roll_overrides=[5, 4, 3]), crit=False)
        self.assertEqual(r_sub_die["rolls"], [5, 4, -3])
        self.assertEqual(r_sub_die["total"], 7)

        # Shorthand bool crit & default RNG path.
        r_short = roll_dice_expr("1d8+2", True)
        self.assertTrue(r_short["crit"])
        self.assertEqual(len(r_short["rolls"]), 2)
        self.assertEqual(r_short["total"], sum(r_short["rolls"]) + 2)


class TestCharacterEngineMutantKills(unittest.TestCase):
    """Kill surviving mutants in `src/srd2024/engine/character.py`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def _base_l4_fighter_spec(self, *, base_abilities: dict[str, int] | None = None) -> dict[str, Any]:
        return {
            "name": "Test Fighter",
            "species": "dwarf",
            "background": "soldier",
            "background_asi": {"con": 2, "str": 1},
            "origin_feat": "savage-attacker",
            "class": "fighter",
            "subclass": "champion",
            "level": 4,
            "ability_method": "point_buy",
            "base_abilities": base_abilities
            or {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
            "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}],
            "fighting_style": "defense",
            "equipment": {"armor": "chain-mail", "shield": True, "weapons": ["longsword"]},
            "weapon_mastery_weapons": ["longsword"],
            "hp_method": "fixed",
        }

    def test_level4_asi_feat_prerequisite_enforced(self) -> None:
        # Grappler requires Level 4+ AND STR 13+ or DEX 13+.
        # STR 10 + 0 (bg) = 10, DEX 10 + 0 = 10 -> MUST fail feat_prerequisite_failed!
        spec_fail = {
            "name": "Weak Scholar Fighter",
            "species": "dwarf",
            "background": "sage",
            "background_asi": {"int": 2, "wis": 1},
            "origin_feat": "magic-initiate",
            "class": "fighter",
            "subclass": "champion",
            "level": 4,
            "ability_method": "manual",
            "base_abilities": {"str": 10, "dex": 10, "con": 14, "int": 14, "wis": 12, "cha": 10},
            "asi_choices": [{"level": 4, "type": "feat", "feat": "grappler"}],
            "fighting_style": "defense",
            "equipment": {"armor": None, "shield": False, "weapons": ["longsword"]},
            "weapon_mastery_weapons": ["longsword"],
            "hp_method": "fixed",
        }
        res_fail = compile_character(spec_fail, self.rules)
        self.assertFalse(res_fail["valid"], "Expected Grappler with STR 10 & DEX 10 to fail prereq")
        err_codes = {e["code"] for e in res_fail["errors"]}
        self.assertIn("feat_prerequisite_failed", err_codes, res_fail["errors"])

        # Same Level 4 build with STR 14 meets Grappler's prerequisite -> valid!
        spec_ok = copy.deepcopy(spec_fail)
        spec_ok["base_abilities"]["str"] = 14
        res_ok = compile_character(spec_ok, self.rules)
        self.assertTrue(res_ok["valid"], f"Expected Grappler with STR 14 to pass: {res_ok['errors']}")

    def test_ability_cap_20_enforced_across_asi_and_feat_increases(self) -> None:
        # Level 6 Fighter gets ASIs at L4 and L6. Base STR 15 + bg +2 = 17; L4 ASI +2 -> 19.
        base_l6 = {
            "name": "Cap Tester",
            "species": "dwarf",
            "background": "soldier",
            "background_asi": {"str": 2, "con": 1},
            "origin_feat": "savage-attacker",
            "class": "fighter",
            "subclass": "champion",
            "level": 6,
            "ability_method": "point_buy",
            "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
            "asi_choices": [
                {"level": 4, "type": "asi", "increases": {"str": 2}},
                {"level": 6, "type": "feat", "feat": "grappler", "ability_increase": {"str": 2}},
            ],
            "fighting_style": "defense",
            "equipment": {"armor": "chain-mail", "shield": True, "weapons": ["longsword"]},
            "weapon_mastery_weapons": ["longsword"],
            "hp_method": "fixed",
        }
        # Dict ability_increase pushing STR 19 + 2 = 21 > 20 MUST fail with ability_cap_exceeded!
        res_feat_dict = compile_character(base_l6, self.rules)
        self.assertFalse(res_feat_dict["valid"])
        self.assertIn("ability_cap_exceeded", {e["code"] for e in res_feat_dict["errors"]}, res_feat_dict["errors"])

        # Dict ability_increase pushing STR 19 + 1 = 20 == cap succeeds!
        ok_cap = copy.deepcopy(base_l6)
        ok_cap["asi_choices"][1]["ability_increase"] = {"str": 1}
        res_ok = compile_character(ok_cap, self.rules)
        self.assertTrue(res_ok["valid"], res_ok["errors"])
        self.assertEqual(res_ok["statblock"]["abilities"]["str"], 20)

        # Standard ASI pushing STR 19 + 2 = 21 > 20 MUST fail with ability_cap_exceeded!
        asi_over = copy.deepcopy(base_l6)
        asi_over["asi_choices"][1] = {"level": 6, "type": "asi", "increases": {"str": 2}}
        res_asi = compile_character(asi_over, self.rules)
        self.assertFalse(res_asi["valid"])
        self.assertIn("ability_cap_exceeded", {e["code"] for e in res_asi["errors"]}, res_asi["errors"])

        # String ability_increase pushing STR 20 + 1 = 21 > 20 at L8 MUST fail!
        str_over = copy.deepcopy(ok_cap)
        str_over["level"] = 8
        str_over["asi_choices"].append({"level": 8, "type": "feat", "feat": "boon-of-combat-prowess"})
        # Use a general feat with string ability_increase at STR 20:
        str_over["asi_choices"][2] = {"level": 8, "type": "feat", "feat": "ability-score-improvement", "increases": {"str": 2}}
        res_asi_feat = compile_character(str_over, self.rules)
        self.assertFalse(res_asi_feat["valid"])
        self.assertIn("ability_cap_exceeded", {e["code"] for e in res_asi_feat["errors"]}, res_asi_feat["errors"])

    def test_hp_method_rolled_exact_sum_and_bounds_validation(self) -> None:
        # Level 5 Human Fighter (no dwarven-toughness), hit_die=10, CON 14+1=15 (+2).
        spec = {
            "name": "Rolled HP Fighter",
            "species": "human",
            "background": "soldier",
            "background_asi": {"str": 2, "con": 1},
            "origin_feat": "savage-attacker",
            "class": "fighter",
            "subclass": "champion",
            "level": 5,
            "ability_method": "point_buy",
            "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
            "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}],
            "fighting_style": "defense",
            "equipment": {"armor": "chain-mail", "shield": True, "weapons": ["longsword"]},
            "weapon_mastery_weapons": ["longsword"],
            "hp_method": "rolled",
            "hp_rolls": [8, 1, 10, 4],
        }
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], res["errors"])
        # L1: (10 + 2) = 12; L2..L5: (8+2)+(1+2)+(10+2)+(4+2) = 10 + 3 + 12 + 6 = 31; total = 43 (fixed would be 44)
        self.assertEqual(res["statblock"]["hp_max"], 43)

        # Out-of-range low roll (0) and high roll (11 on d10) MUST be rejected!
        for bad_rolls in ([0, 5, 5, 5], [5, 11, 5, 5]):
            bad_spec = copy.deepcopy(spec)
            bad_spec["hp_rolls"] = bad_rolls
            bad_res = compile_character(bad_spec, self.rules)
            self.assertFalse(bad_res["valid"], f"Expected invalid_hp_roll for {bad_rolls}")
            self.assertIn("invalid_hp_roll", {e["code"] for e in bad_res["errors"]}, bad_res["errors"])

    def test_non_alert_initiative_strictly_equals_dex_mod(self) -> None:
        # Dwarf Fighter with DEX 14 (+2) and NO Alert feat -> initiative MUST strictly equal +2 (not +3 or +5).
        spec_no_alert = self._base_l4_fighter_spec()
        res_no_alert = compile_character(spec_no_alert, self.rules)
        self.assertTrue(res_no_alert["valid"], res_no_alert["errors"])
        sb = res_no_alert["statblock"]
        self.assertNotIn("alert", sb["feats"])
        self.assertEqual(sb["ability_mods"]["dex"], 2)
        self.assertEqual(sb["initiative"], 2)

        # Human Fighter WITH Alert feat at L5 (PB +3, DEX 14 -> +2) -> initiative == 2 + 3 == 5.
        sb_alert = _make_fighter(self.rules, level=5, extra_origin_feat="alert")
        self.assertIn("alert", sb_alert["feats"])
        self.assertEqual(sb_alert["initiative"], sb_alert["ability_mods"]["dex"] + sb_alert["proficiency_bonus"])


class TestCombatEngineMutantKills(unittest.TestCase):
    """Kill surviving mutants in `src/srd2024/engine/combat.py`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(cls.rules, level=5, weapons=["longsword", "battleaxe"], mastery_weapons=["longsword", "battleaxe"])
        cls.cleric = _make_cleric(cls.rules, level=5)

    def test_level5_fighter_exhausts_extra_attacks_and_3rd_attack_rejected(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 100
        st["combatants"]["gob-1"]["hp_max"] = 100

        # 1st attack consumes Action and sets attacks_remaining == 1 (extra_attacks=2 -> 2 - 1 = 1).
        r1 = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[15, 4],
        )
        self.assertEqual(r1["status"], "ok")
        self.assertFalse(r1["state"]["combatants"]["pc-1"]["turn_resources"]["action"])
        self.assertEqual(r1["state"]["combatants"]["pc-1"]["turn_resources"]["attacks_remaining"], 1)

        # 2nd attack consumes Extra Attack and sets attacks_remaining == 0.
        r2 = step(
            r1["state"],
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[15, 4],
        )
        self.assertEqual(r2["status"], "ok")
        self.assertEqual(r2["state"]["combatants"]["pc-1"]["turn_resources"]["attacks_remaining"], 0)

        # 3rd attack on the same turn MUST be rejected!
        before_r3 = copy.deepcopy(r2["state"])
        r3 = step(
            r2["state"],
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[15, 4],
        )
        self.assertEqual(r3["status"], "invalid_intent")
        self.assertEqual(r3["state"], before_r3)

    def test_monster_action_consumed_and_second_attack_rejected(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            ],
        )
        r1 = step(
            st,
            {"type": "attack", "actor_id": "gob-1", "target_id": "pc-1", "action": "Scimitar"},
            self.rules,
            roll_overrides=[16, 4],
        )
        self.assertEqual(r1["status"], "ok")
        self.assertFalse(r1["state"]["combatants"]["gob-1"]["turn_resources"]["action"])

        # 2nd attack by the monster on the same turn MUST be rejected!
        before_r2 = copy.deepcopy(r1["state"])
        r2 = step(
            r1["state"],
            {"type": "attack", "actor_id": "gob-1", "target_id": "pc-1", "action": "Scimitar"},
            self.rules,
            roll_overrides=[16, 4],
        )
        self.assertEqual(r2["status"], "invalid_intent")
        self.assertEqual(r2["state"], before_r2)

    def test_bless_plus_1d4_and_bane_minus_1d4_exact_sign_on_monster_and_pc_attacks(self) -> None:
        # 1. Monster attacks (kills combat.py:1396 `+ bb_bonus` -> `- bb_bonus`):
        st_mon = _start(
            self.rules,
            [
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            ],
        )
        # Goblin Scimitar has +4 to hit vs Fighter AC 19.
        # With Bless (+1d4 = +4): d20=12 + 4 + 4 = 20 >= 19 HIT! (If sign flipped: 12 + 4 - 4 = 12 MISS!)
        st_mon_bless = copy.deepcopy(st_mon)
        st_mon_bless["combatants"]["gob-1"]["active_effects"].append(
            {
                "id": "eff-bless-mon",
                "kind": "bless",
                "source_id": "gob-1",
                "target_id": "gob-1",
                "value": "1d4",
                "applies_to": ["attack", "save"],
                "expires": "concentration",
            }
        )
        r_mb = step(
            st_mon_bless,
            {"type": "attack", "actor_id": "gob-1", "target_id": "pc-1", "action": "Scimitar"},
            self.rules,
            roll_overrides=[12, 4, 3],  # d20=12, bless d4=4 -> total 20 hit, dmg d6=3
        )
        self.assertEqual(r_mb["status"], "ok")
        atk_mb = next(e for e in r_mb["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk_mb["total"], 20)
        self.assertTrue(atk_mb["hit"])

        # With Bane (-1d4 = -4): d20=16 + 4 - 4 = 16 < 19 MISS! (If sign flipped: 16 + 4 + 4 = 24 HIT!)
        st_mon_bane = copy.deepcopy(st_mon)
        st_mon_bane["combatants"]["gob-1"]["active_effects"].append(
            {
                "id": "eff-bane-mon",
                "kind": "bane",
                "source_id": "pc-1",
                "target_id": "gob-1",
                "value": "1d4",
                "applies_to": ["attack", "save"],
                "expires": "concentration",
            }
        )
        r_mba = step(
            st_mon_bane,
            {"type": "attack", "actor_id": "gob-1", "target_id": "pc-1", "action": "Scimitar"},
            self.rules,
            roll_overrides=[16, 4],  # d20=16, bane d4=4 -> total 16 miss
        )
        self.assertEqual(r_mba["status"], "ok")
        atk_mba = next(e for e in r_mba["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk_mba["total"], 16)
        self.assertFalse(atk_mba["hit"])

        # 2. PC attacks with Bless (+1d4) and Bane (-1d4) via real spell effects:
        st_pc = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        # Fighter longsword +7 vs Goblin AC 15.
        st_pc_bless = copy.deepcopy(st_pc)
        st_pc_bless["combatants"]["pc-1"]["active_effects"].append(
            {"id": "b1", "kind": "bless", "value": "1d4", "applies_to": ["attack", "save"]}
        )
        r_pb = step(
            st_pc_bless,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[5, 4, 3],  # 5 + 7 + 4 = 16 >= 15 HIT
        )
        atk_pb = next(e for e in r_pb["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk_pb["total"], 16)
        self.assertTrue(atk_pb["hit"])

        st_pc_bane = copy.deepcopy(st_pc)
        st_pc_bane["combatants"]["pc-1"]["active_effects"].append(
            {"id": "bn1", "kind": "bane", "value": "1d4", "applies_to": ["attack", "save"]}
        )
        r_pba = step(
            st_pc_bane,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[10, 4],  # 10 + 7 - 4 = 13 < 15 MISS
        )
        atk_pba = next(e for e in r_pba["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk_pba["total"], 13)
        self.assertFalse(atk_pba["hit"])

    def test_exhaustion_penalty_exact_sign_on_initiative_roll(self) -> None:
        # Kills combat.py:1703 `+ exh_pen` -> `- exh_pen`!
        # Fighter init_mod = +5 (DEX +2 + Alert +3); with exhaustion_level=3 -> exh_pen = -6.
        # Raw d20=12 -> total MUST be 12 + 5 - 6 = 11 (NOT 12 + 5 + 6 = 23!).
        # Goblin init_mod = +2, exhaustion=0, raw d20=12 -> total = 14 -> Goblin acts FIRST!
        res = step(
            None,
            {
                "type": "start_encounter",
                "seed": 55,
                "combatants": [
                    {
                        "id": "pc-exh",
                        "side": "party",
                        "statblock": self.fighter,
                        "position": {"x": 0, "y": 0},
                        "exhaustion_level": 3,
                    },
                    {
                        "id": "gob-fresh",
                        "side": "enemy",
                        "monster": "goblin-warrior",
                        "position": {"x": 5, "y": 0},
                    },
                ],
            },
            self.rules,
            roll_overrides=[12, 12],
        )
        self.assertEqual(res["status"], "ok")
        init_evts = {e["combatant_id"]: e for e in res["events"] if e["type"] == "initiative_rolled"}
        pc_init = init_evts["pc-exh"]
        gob_init = init_evts["gob-fresh"]
        self.assertEqual(pc_init["exhaustion_penalty"], -6)
        self.assertEqual(pc_init["total"], 12 + pc_init["modifier"] - 6)
        self.assertEqual(res["state"]["combatants"]["pc-exh"]["initiative"], pc_init["total"])
        self.assertEqual(gob_init["exhaustion_penalty"], 0)
        self.assertEqual(gob_init["total"], 12 + gob_init["modifier"])
        # Because Exhaustion subtracted 6 instead of adding 6, the fresh Goblin acts before the exhausted Fighter!
        self.assertEqual(res["state"]["turn_order"], ["gob-fresh", "pc-exh"])

    def test_incapacitated_blocks_actions_and_only_incapacitating_conditions_break_concentration(self) -> None:
        # Kills combat.py:397 `cond_slug == "incapacitated"` `==` -> `!=`!
        # If `==` mutates to `!=`, applying a non-incapacitating condition like `prone` (via Battleaxe Topple)
        # would falsely drop concentration!
        st = _start(
            self.rules,
            [
                {"id": "clr-1", "side": "party", "statblock": self.cleric, "position": {"x": 0, "y": 0}},
                {"id": "pc-1", "side": "enemy", "statblock": self.fighter, "position": {"x": 5, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 10, "y": 0}},
            ],
        )
        # 1. Cleric casts Bless (concentration).
        r_bless = step(
            st,
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "bless", "slot_level": 1, "target_ids": ["clr-1"]},
            self.rules,
        )
        self.assertEqual(r_bless["status"], "ok")
        self.assertEqual(r_bless["state"]["combatants"]["clr-1"]["concentration"]["spell"], "bless")

        # 2. Incapacitated condition directly blocks move, attack, dodge, and cast_spell.
        st_incap = copy.deepcopy(r_bless["state"])
        st_incap["combatants"]["clr-1"]["turn_resources"]["action"] = True
        st_incap["combatants"]["clr-1"]["conditions"].append(
            {"condition": "incapacitated", "source_id": None, "duration": "indefinite"}
        )
        for blocked_intent in (
            {"type": "move", "actor_id": "clr-1", "to": {"x": 5, "y": 0}},
            {"type": "dodge", "actor_id": "clr-1"},
            {"type": "attack", "actor_id": "clr-1", "target_id": "pc-1", "weapon": "mace"},
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "sacred-flame", "target_ids": ["pc-1"]},
        ):
            r_blk = step(st_incap, blocked_intent, self.rules)
            self.assertEqual(r_blk["status"], "invalid_intent", f"Incapacitated should block {blocked_intent!r}")

        # 3. End Cleric turn -> Enemy Fighter hits Cleric with Battleaxe (Topple mastery -> applies `prone`).
        #    Cleric succeeds Concentration CON save (d20=19) and fails Topple CON save (d20=1) -> becomes `prone`,
        #    which is NOT incapacitating, so Bless concentration MUST REMAIN INTACT!
        r_end = step(r_bless["state"], {"type": "end_turn", "actor_id": "clr-1"}, self.rules)
        r_topple = step(
            r_end["state"],
            {"type": "attack", "actor_id": "pc-1", "target_id": "clr-1", "weapon": "battleaxe", "use_mastery": True},
            self.rules,
            # d20=18 (hit), 1d8=2 (+4=6 dmg), conc CON save d20=19 + bless d4=2 (pass!), Topple CON save d20=1 + bless d4=1 (fail -> prone!)
            roll_overrides=[18, 2, 19, 2, 1, 1],
        )
        self.assertEqual(r_topple["status"], "ok")
        clr_after_prone = r_topple["state"]["combatants"]["clr-1"]
        self.assertIn("prone", _condition_names(clr_after_prone))
        self.assertIsNotNone(
            clr_after_prone["concentration"],
            "Applying non-incapacitating 'prone' condition MUST NOT break concentration!",
        )
        self.assertEqual(clr_after_prone["concentration"]["spell"], "bless")

        # 4. Applying an incapacitating condition (`paralyzed` via Ghoul Claw, which implies `incapacitated`)
        #    DOES break concentration even if the damage concentration save succeeded!
        st_ghoul = _start(
            self.rules,
            [
                {"id": "clr-1", "side": "party", "statblock": self.cleric, "position": {"x": 0, "y": 0}},
                {"id": "ghoul-1", "side": "enemy", "monster": "ghoul", "position": {"x": 5, "y": 0}},
            ],
        )
        r_b2 = step(
            st_ghoul,
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "bless", "slot_level": 1, "target_ids": ["clr-1"]},
            self.rules,
        )
        r_e2 = step(r_b2["state"], {"type": "end_turn", "actor_id": "clr-1"}, self.rules)
        r_claw = step(
            r_e2["state"],
            {"type": "attack", "actor_id": "ghoul-1", "target_id": "clr-1", "action": "Claw"},
            self.rules,
            # Claw hit d20=16, dmg d4=2, conc CON save d20=19 + bless d4=4 (pass!), rider CON save d20=1 + bless d4=1 (fail -> paralyzed!)
            roll_overrides=[16, 2, 19, 4, 1, 1],
        )
        self.assertEqual(r_claw["status"], "ok")
        clr_par = r_claw["state"]["combatants"]["clr-1"]
        self.assertIn("paralyzed", _condition_names(clr_par))
        self.assertIsNone(clr_par["concentration"], "Paralyzed (implies Incapacitated) MUST break concentration")


if __name__ == "__main__":
    unittest.main()
