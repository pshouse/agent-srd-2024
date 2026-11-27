"""Stage 2 Verification Tests (§7.3): Deterministic 5e-2024 Combat State Machine.

Covers:
1. Determinism & Immutability (same seed -> byte-identical output; input state unmutated).
2. Core Attack Math (Advantage/Disadvantage cancellation, Nat-1 auto-miss, Nat-20 crit
   doubling dice only, Resistance, Immunity, Vulnerability).
3. All 8 2024 Weapon Masteries (cleave, graze, nick, push, sap, slow, topple, vex).
4. All 15 2024 Conditions & 2024 Exhaustion (-2 d20 tests/level, -5 ft speed/level,
   death at level 6; Paralyzed/Unconscious auto-fail STR/DEX & 5ft auto-crit;
   Prone distance adv/disadv & stand_up; Petrified resist all + poison immunity;
   Grappled/Restrained/Blinded/Invisible/Poisoned/Frightened/Stunned/Incapacitated/Charmed/Deafened).
5. Spells, Concentration & 0 HP Revival (fireball L3 & upcast L4 + fire resistance +
   concentration DC max(10, dmg//2), shield reaction +5 AC & magic-missile negation & expiry,
   bless +1d4 & concentration replacement by hold-person, cure-wounds/healing-word reviving
   0-HP PC leaving Prone, fire-bolt cantrip scaling L1 vs L5, narrative spell honest fallback).
6. Monsters & Turn Economy (goblin-warrior conditional Advantage +1d4, ghoul Claw CON DC 10
   paralyzed, adult-red-dragon Fire Breath DC 21 DEX half 17d6, dodge/dash/disengage/help,
   end_turn effect expiry & round increment, PC death saves at 0 HP).
"""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from srd2024.engine.character import compile_character
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore


def _make_fighter(
    rules: RulesStore,
    *,
    name: str = "Aldric",
    weapons: list[str] | None = None,
    mastery_weapons: list[str] | None = None,
    level: int = 5,
    fighting_style: str = "defense",
    armor: str | None = "chain-mail",
    shield: bool = True,
) -> dict[str, Any]:
    weapons = weapons or ["longsword", "greatsword", "javelin"]
    mastery_weapons = mastery_weapons if mastery_weapons is not None else weapons[:4]
    spec: dict[str, Any] = {
        "name": name,
        "species": "human",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "class": "fighter",
        "subclass": "champion" if level >= 3 else None,
        "level": level,
        "ability_method": "point_buy",
        "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
        "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}] if level >= 4 else [],
        "fighting_style": fighting_style,
        "equipment": {"armor": armor, "shield": shield, "weapons": weapons},
        "weapon_mastery_weapons": mastery_weapons,
        "hp_method": "fixed",
    }
    res = compile_character(spec, rules)
    assert res["valid"], f"Fighter fixture invalid: {res['errors']}"
    return res["statblock"]


def _make_wizard(
    rules: RulesStore,
    *,
    name: str = "Lyra",
    level: int = 5,
    prepared_spells: list[str] | None = None,
    cantrips: list[str] | None = None,
) -> dict[str, Any]:
    if prepared_spells is None:
        prepared_spells = (
            ["fireball", "shield", "magic-missile", "hold-person", "mage-armor", "misty-step"]
            if level >= 5
            else ["shield", "magic-missile"]
        )
    if cantrips is None:
        cantrips = ["fire-bolt", "prestidigitation"]
    spec: dict[str, Any] = {
        "name": name,
        "species": "elf",
        "subspecies": "elven-lineage-high-elf",
        "background": "sage",
        "background_asi": {"int": 2, "con": 1},
        "class": "wizard",
        "subclass": "evoker" if level >= 3 else None,
        "level": level,
        "ability_method": "standard_array",
        "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
        "asi_choices": [{"level": 4, "type": "asi", "increases": {"int": 2}}] if level >= 4 else [],
        "equipment": {"armor": None, "shield": False, "weapons": ["quarterstaff"]},
        "cantrips": cantrips,
        "prepared_spells": prepared_spells,
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
        prepared_spells = ["bless", "cure-wounds", "healing-word", "hold-person", "augury"]
    spec: dict[str, Any] = {
        "name": name,
        "species": "human",
        "background": "acolyte",
        "background_asi": {"wis": 2, "cha": 1},
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
        # Force deterministic turn order matching combatants list order (even when PCs have Alert +5)
        init_rolls = [20] + [1] * (len(combatants) - 1) if len(combatants) == 2 else [max(1, 20 - i * 6) for i in range(len(combatants))]
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


def _add_cond(
    state: dict[str, Any],
    cid: str,
    condition: str,
    *,
    source_id: str | None = None,
    duration: str = "indefinite",
) -> dict[str, Any]:
    st = copy.deepcopy(state)
    st["combatants"][cid]["conditions"].append(
        {
            "condition": condition,
            "source_id": source_id,
            "duration": duration,
            "save_ability": None,
            "save_dc": None,
        }
    )
    return st


class TestDeterminismAndCoreAttackMath(unittest.TestCase):
    """§7.3.1–§7.3.2: Determinism, immutability, adv/disadv cancellation, crits, resist/immune/vuln."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(cls.rules)

    def test_determinism_and_state_immutability(self) -> None:
        def run_sequence() -> tuple[str, list[dict[str, Any]]]:
            r1 = step(
                None,
                {
                    "type": "start_encounter",
                    "seed": 98765,
                    "combatants": [
                        {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                        {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                    ],
                },
                self.rules,
            )
            st = r1["state"]
            snapshot = copy.deepcopy(st)
            active = st["turn_order"][st["active_index"]]
            target = "gob-1" if active == "pc-1" else "pc-1"
            atk_intent = (
                {"type": "attack", "actor_id": active, "target_id": target, "weapon": "longsword"}
                if active == "pc-1"
                else {"type": "attack", "actor_id": active, "target_id": target, "action": "Scimitar"}
            )
            r2 = step(st, atk_intent, self.rules)
            # Immutability check: input state MUST NOT be mutated
            self.assertEqual(st, snapshot, "step() mutated input state dict in place!")
            r3 = step(r2["state"], {"type": "end_turn", "actor_id": active}, self.rules)
            return json.dumps(r3["state"], sort_keys=True), r1["events"] + r2["events"] + r3["events"]

        s1, e1 = run_sequence()
        s2, e2 = run_sequence()
        self.assertEqual(s1, s2, "Identical seed & intents must produce byte-identical final state")
        self.assertEqual(e1, e2, "Identical seed & intents must produce identical events")

    def test_advantage_disadvantage_cancellation_and_nat1_nat20(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        # 2 Advantage sources (attacker invisible + target prone within 5ft) + 1 Disadvantage (attacker poisoned) -> normal!
        st_cancel = _add_cond(st, "pc-1", "invisible")
        st_cancel = _add_cond(st_cancel, "gob-1", "prone")
        st_cancel = _add_cond(st_cancel, "pc-1", "poisoned")
        res = step(
            st_cancel,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[12, 4],  # single d20=12 (+7=19 >= 15 hit), 1d8=4 (+4=8)
        )
        self.assertEqual(res["status"], "ok")
        atk_evt = next(e for e in res["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk_evt["mode"], "normal")
        self.assertEqual(len(atk_evt["rolls"]), 1)
        self.assertEqual(res["state"]["combatants"]["gob-1"]["hp"], 10 - 8)

        # Nat-1 auto-miss even against AC 5
        st_low_ac = copy.deepcopy(st)
        st_low_ac["combatants"]["gob-1"]["ac"] = 5
        res_nat1 = step(
            st_low_ac,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[1],
        )
        self.assertEqual(res_nat1["state"]["combatants"]["gob-1"]["hp"], 10)
        atk_nat1 = next(e for e in res_nat1["events"] if e["type"] == "attack_roll")
        self.assertFalse(atk_nat1["hit"])

        # Nat-20 crit doubles damage dice ONLY (1d8+4 -> 2d8+4: 6 + 5 + 4 = 15)
        st_tank = copy.deepcopy(st)
        st_tank["combatants"]["gob-1"]["hp"] = 50
        st_tank["combatants"]["gob-1"]["hp_max"] = 50
        res_crit = step(
            st_tank,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[20, 6, 5],
        )
        self.assertEqual(res_crit["state"]["combatants"]["gob-1"]["hp"], 50 - (6 + 5 + 4))
        atk_crit = next(e for e in res_crit["events"] if e["type"] == "attack_roll")
        self.assertTrue(atk_crit["crit"])

    def test_damage_resistance_immunity_vulnerability(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        # Base slashing damage with d8=5 + STR(+4) = 9
        # Resistance: floor(9 / 2) = 4
        st_res = copy.deepcopy(st)
        st_res["combatants"]["gob-1"]["hp"] = 40
        st_res["combatants"]["gob-1"]["hp_max"] = 40
        st_res["combatants"]["gob-1"]["damage_resistances"] = ["slashing"]
        r_res = step(
            st_res,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[15, 5],
        )
        self.assertEqual(r_res["state"]["combatants"]["gob-1"]["hp"], 40 - 4)

        # Immunity: 0 damage
        st_imm = copy.deepcopy(st_res)
        st_imm["combatants"]["gob-1"]["damage_resistances"] = []
        st_imm["combatants"]["gob-1"]["damage_immunities"] = ["slashing"]
        r_imm = step(
            st_imm,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[15, 5],
        )
        self.assertEqual(r_imm["state"]["combatants"]["gob-1"]["hp"], 40)

        # Vulnerability: 2 * 9 = 18 damage
        st_vul = copy.deepcopy(st_res)
        st_vul["combatants"]["gob-1"]["damage_resistances"] = []
        st_vul["combatants"]["gob-1"]["damage_vulnerabilities"] = ["slashing"]
        r_vul = step(
            st_vul,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[15, 5],
        )
        self.assertEqual(r_vul["state"]["combatants"]["gob-1"]["hp"], 40 - 18)


class TestAllEightWeaponMasteries(unittest.TestCase):
    """§7.3.3: Explicit verification of all 8 2024 Weapon Masteries."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_cleave_mastery_and_once_per_turn_limit(self) -> None:
        fighter = _make_fighter(self.rules, weapons=["greataxe"], mastery_weapons=["greataxe"], shield=False)
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "gob-2", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 5}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 30
        st["combatants"]["gob-1"]["hp_max"] = 30
        st["combatants"]["gob-2"]["hp"] = 30
        st["combatants"]["gob-2"]["hp_max"] = 30

        # Primary hit: d20=15 (+7=22 vs 15), 1d12=6 (+4=10); Cleave hit: d20=14 (+7=21 vs 15), 1d12=7 (NO +4 STR mod -> 7!)
        r1 = step(
            st,
            {
                "type": "attack",
                "actor_id": "pc-1",
                "target_id": "gob-1",
                "weapon": "greataxe",
                "cleave_target_id": "gob-2",
            },
            self.rules,
            roll_overrides=[15, 6, 14, 7],
        )
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r1["state"]["combatants"]["gob-1"]["hp"], 30 - 10)
        self.assertEqual(r1["state"]["combatants"]["gob-2"]["hp"], 30 - 7)
        self.assertTrue(r1["state"]["combatants"]["pc-1"]["turn_resources"]["cleave_used"])

        # Extra Attack in same turn: Cleave already used -> gob-2 must NOT take a second Cleave attack
        r2 = step(
            r1["state"],
            {
                "type": "attack",
                "actor_id": "pc-1",
                "target_id": "gob-1",
                "weapon": "greataxe",
                "cleave_target_id": "gob-2",
            },
            self.rules,
            roll_overrides=[15, 5],
        )
        self.assertEqual(r2["status"], "ok")
        self.assertEqual(r2["state"]["combatants"]["gob-1"]["hp"], 30 - 10 - 9)
        self.assertEqual(r2["state"]["combatants"]["gob-2"]["hp"], 30 - 7)

    def test_graze_mastery_deals_ability_mod_on_miss(self) -> None:
        fighter = _make_fighter(self.rules, weapons=["greatsword"], mastery_weapons=["greatsword"], shield=False)
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        # d20=2 (+7=9 vs AC 15 -> miss), Graze deals STR mod (+4) slashing damage
        res = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "greatsword"},
            self.rules,
            roll_overrides=[2],
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["state"]["combatants"]["gob-1"]["hp"], 10 - 4)

    def test_nick_mastery_preserves_bonus_action(self) -> None:
        fighter = _make_fighter(
            self.rules,
            weapons=["scimitar", "shortsword"],
            mastery_weapons=["scimitar"],
            shield=False,
        )
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 40
        st["combatants"]["gob-1"]["hp_max"] = 40

        # Primary Light attack with shortsword (d20=15, d6=3 -> 3+4=7)
        r1 = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "shortsword"},
            self.rules,
            roll_overrides=[15, 3],
        )
        self.assertEqual(r1["status"], "ok")
        self.assertTrue(r1["state"]["combatants"]["pc-1"]["turn_resources"]["bonus_action"])

        # Offhand Light attack using Nick weapon (scimitar) -> part of Attack action, bonus_action stays True!
        # No Two-Weapon Fighting -> positive STR mod omitted on offhand (d6=4 -> 4 dmg)
        r2 = step(
            r1["state"],
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "scimitar", "offhand": True},
            self.rules,
            roll_overrides=[15, 4],
        )
        self.assertEqual(r2["status"], "ok")
        self.assertTrue(r2["state"]["combatants"]["pc-1"]["turn_resources"]["bonus_action"])
        self.assertTrue(r2["state"]["combatants"]["pc-1"]["turn_resources"]["nick_used"])
        self.assertEqual(r2["state"]["combatants"]["gob-1"]["hp"], 40 - 7 - 4)

    def test_push_mastery_moves_medium_10ft_and_ignores_huge(self) -> None:
        fighter = _make_fighter(self.rules, weapons=["warhammer"], mastery_weapons=["warhammer"])
        # 1. Medium/Small target pushed 10ft away: (5,0) -> (15,0)
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 30
        st["combatants"]["gob-1"]["hp_max"] = 30
        r1 = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "warhammer"},
            self.rules,
            roll_overrides=[15, 4],
        )
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r1["state"]["combatants"]["gob-1"]["position"], {"x": 15, "y": 0})

        # 2. Huge target (adult-red-dragon) is NOT pushed
        st_huge = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "drag-1", "side": "enemy", "monster": "adult-red-dragon", "position": {"x": 5, "y": 0}},
            ],
        )
        r2 = step(
            st_huge,
            {"type": "attack", "actor_id": "pc-1", "target_id": "drag-1", "weapon": "warhammer"},
            self.rules,
            roll_overrides=[18, 4],
        )
        self.assertEqual(r2["status"], "ok")
        self.assertEqual(r2["state"]["combatants"]["drag-1"]["position"], {"x": 5, "y": 0})

    def test_sap_mastery_gives_target_disadvantage_on_next_attack(self) -> None:
        fighter = _make_fighter(self.rules, weapons=["longsword"], mastery_weapons=["longsword"])
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 30
        st["combatants"]["gob-1"]["hp_max"] = 30

        # Fighter hits with Longsword (Sap)
        r1 = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword"},
            self.rules,
            roll_overrides=[15, 4],
        )
        self.assertTrue(any(eff["kind"] == "sap" for eff in r1["state"]["combatants"]["gob-1"]["active_effects"]))

        r_end = step(r1["state"], {"type": "end_turn", "actor_id": "pc-1"}, self.rules)
        # Goblin attacks with roll_overrides=[18, 2]: Disadvantage picks 2 (+4=6 vs AC 19 -> miss!)
        r_gob = step(
            r_end["state"],
            {"type": "attack", "actor_id": "gob-1", "target_id": "pc-1", "action": "Scimitar"},
            self.rules,
            roll_overrides=[18, 2],
        )
        atk_evt = next(e for e in r_gob["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk_evt["mode"], "disadvantage")
        self.assertFalse(atk_evt["hit"])

    def test_slow_mastery_reduces_speed_by_10_non_stacking(self) -> None:
        fighter = _make_fighter(self.rules, weapons=["javelin"], mastery_weapons=["javelin"])
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 40
        st["combatants"]["gob-1"]["hp_max"] = 40

        # Two Slow hits in same turn -> speed penalty MUST NOT stack beyond 10ft (30 -> 20, not 10)
        r1 = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "javelin"},
            self.rules,
            roll_overrides=[15, 3],
        )
        r2 = step(
            r1["state"],
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "javelin"},
            self.rules,
            roll_overrides=[15, 3],
        )
        r_end = step(r2["state"], {"type": "end_turn", "actor_id": "pc-1"}, self.rules)

        # Goblin tries to move 25ft -> invalid (effective speed is 20ft)
        r_too_far = step(
            r_end["state"],
            {"type": "move", "actor_id": "gob-1", "to": {"x": 30, "y": 0}},
            self.rules,
        )
        self.assertNotEqual(r_too_far["status"], "ok")

        # Goblin moves 20ft -> succeeds (proves non-stacking: speed is 20, not 10!)
        r_ok = step(
            r_end["state"],
            {"type": "move", "actor_id": "gob-1", "to": {"x": 25, "y": 0}},
            self.rules,
        )
        self.assertEqual(r_ok["status"], "ok")
        self.assertEqual(r_ok["state"]["combatants"]["gob-1"]["position"], {"x": 25, "y": 0})

    def test_topple_mastery_con_save_inflicts_prone(self) -> None:
        fighter = _make_fighter(self.rules, weapons=["battleaxe"], mastery_weapons=["battleaxe"])
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 30
        st["combatants"]["gob-1"]["hp_max"] = 30

        # Hit (d20=15), damage (d8=3), target CON save vs DC 8+4+3=15 (d20=5 -> fail -> Prone!)
        res = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "battleaxe"},
            self.rules,
            roll_overrides=[15, 3, 5],
        )
        self.assertEqual(res["status"], "ok")
        self.assertIn("prone", _condition_names(res["state"]["combatants"]["gob-1"]))

    def test_vex_mastery_grants_advantage_on_next_attack(self) -> None:
        fighter = _make_fighter(self.rules, weapons=["rapier"], mastery_weapons=["rapier"])
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 40
        st["combatants"]["gob-1"]["hp_max"] = 40

        # First hit applies Vex
        r1 = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "rapier"},
            self.rules,
            roll_overrides=[15, 4],
        )
        self.assertEqual(r1["status"], "ok")

        # Second attack (Extra Attack) against same target has Advantage: [2, 18] -> chooses 18 -> hit!
        r2 = step(
            r1["state"],
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "rapier"},
            self.rules,
            roll_overrides=[2, 18, 4],
        )
        self.assertEqual(r2["status"], "ok")
        atk_evt = next(e for e in r2["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk_evt["mode"], "advantage")
        self.assertTrue(atk_evt["hit"])


class TestConditionsAnd2024Rules(unittest.TestCase):
    """§7.3.4: All 15 conditions & 2024 Exhaustion rules."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(cls.rules, weapons=["longsword", "longbow"], mastery_weapons=[])
        cls.wizard = _make_wizard(cls.rules)

    def test_exhaustion_2024_penalties_and_level_6_death(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        # Level 2 Exhaustion: -4 to attack rolls & saves, -10 ft speed (30 -> 20)
        st2 = copy.deepcopy(st)
        st2["combatants"]["pc-1"]["exhaustion_level"] = 2
        st2["combatants"]["pc-1"]["movement_remaining_ft"] = 20

        # Attack roll d20=10 + 7 - 4 (exhaustion) = 13 < AC 15 -> MISS (without exhaustion 17 would hit!)
        r_atk = step(
            st2,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[10],
        )
        atk_evt = next(e for e in r_atk["events"] if e["type"] == "attack_roll")
        self.assertFalse(atk_evt["hit"], "Exhaustion level 2 (-4) should turn 10+7=17 into 13 (miss vs AC 15)")

        # Movement > 20ft blocked; 20ft allowed
        r_far = step(st2, {"type": "move", "actor_id": "pc-1", "to": {"x": 0, "y": 25}}, self.rules)
        self.assertNotEqual(r_far["status"], "ok")
        r_ok = step(st2, {"type": "move", "actor_id": "pc-1", "to": {"x": 0, "y": 20}}, self.rules)
        self.assertEqual(r_ok["status"], "ok")

        # Exhaustion level 6 kills combatant on turn resolution/check
        st6 = copy.deepcopy(st)
        st6["combatants"]["gob-1"]["exhaustion_level"] = 6
        r_end = step(st6, {"type": "end_turn", "actor_id": "pc-1"}, self.rules)
        self.assertFalse(r_end["state"]["combatants"]["gob-1"]["alive"])

    def test_paralyzed_and_unconscious_auto_crit_auto_fail_and_incapacitated(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "wiz-1", "side": "party", "statblock": self.wizard, "position": {"x": 15, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 50
        st["combatants"]["gob-1"]["hp_max"] = 50

        # Paralyzed target within 5ft: non-nat20 hit (12) auto-crits (2d8+4 -> 6+5+4=15)!
        st_para = _add_cond(st, "gob-1", "paralyzed")
        r_crit = step(
            st_para,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[12, 10, 6, 5],  # adv d20s [12, 10] -> 12 hits & auto-crits -> 2d8 [6, 5] + 4 = 15
        )
        atk_evt = next(e for e in r_crit["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk_evt["mode"], "advantage")
        self.assertTrue(atk_evt["crit"])
        self.assertEqual(r_crit["state"]["combatants"]["gob-1"]["hp"], 50 - 15)

        # Paralyzed target auto-fails DEX save (Fireball) even if override roll were 20
        r_t1 = step(st_para, {"type": "end_turn", "actor_id": "pc-1"}, self.rules)
        r_fb = step(
            r_t1["state"],
            {"type": "cast_spell", "actor_id": "wiz-1", "spell": "fireball", "target_ids": ["gob-1"]},
            self.rules,
            roll_overrides=[3, 3, 3, 3, 3, 3, 3, 3],  # 8d6 = 24 full fire damage (auto-fail DEX save!)
        )
        self.assertEqual(r_fb["status"], "ok")
        self.assertEqual(r_fb["state"]["combatants"]["gob-1"]["hp"], 50 - 24)

        # Incapacitated/Paralyzed actor cannot act
        st_incap = _add_cond(st, "pc-1", "incapacitated")
        r_blocked = step(
            st_incap,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword"},
            self.rules,
        )
        self.assertNotEqual(r_blocked["status"], "ok")

        # Unconscious within 5ft also auto-crits
        st_unc = _add_cond(st, "gob-1", "unconscious")
        r_unc = step(
            st_unc,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[12, 10, 4, 4],  # 4+4+4 = 12
        )
        self.assertEqual(r_unc["state"]["combatants"]["gob-1"]["hp"], 50 - 12)

    def test_prone_melee_adv_ranged_disadv_and_stand_up(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "gob-far", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 30, "y": 0}},
            ],
        )
        st = _add_cond(st, "gob-1", "prone")
        st = _add_cond(st, "gob-far", "prone")

        # Melee within 5ft vs Prone -> Advantage
        r_melee = step(
            st,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[2, 15, 4],
        )
        self.assertEqual(next(e for e in r_melee["events"] if e["type"] == "attack_roll")["mode"], "advantage")

        # Ranged beyond 5ft vs Prone -> Disadvantage (move pc-1 or attack gob-far when gob-1 not adjacent)
        st_far = copy.deepcopy(st)
        st_far["combatants"]["gob-1"]["position"] = {"x": 20, "y": 0}
        r_ranged = step(
            st_far,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-far", "weapon": "longbow", "use_mastery": False},
            self.rules,
            roll_overrides=[18, 2],
        )
        self.assertEqual(next(e for e in r_ranged["events"] if e["type"] == "attack_roll")["mode"], "disadvantage")

        # Stand up costs floor(speed/2) = 15 ft and removes prone
        st_prone_pc = _add_cond(st, "pc-1", "prone")
        r_stand = step(
            st_prone_pc,
            {"type": "move", "actor_id": "pc-1", "to": {"x": 0, "y": 10}, "stand_up": True},
            self.rules,
        )
        self.assertEqual(r_stand["status"], "ok")
        self.assertNotIn("prone", _condition_names(r_stand["state"]["combatants"]["pc-1"]))
        # 30 - 15 (stand up) - 10 (move) = 5 ft remaining
        self.assertEqual(r_stand["state"]["combatants"]["pc-1"]["movement_remaining_ft"], 5)

    def test_petrified_grappled_restrained_blinded_invisible_poisoned_frightened_stunned_charmed(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["gob-1"]["hp"] = 40
        st["combatants"]["gob-1"]["hp_max"] = 40

        # Petrified: all-damage resistance (d8=5 + 4 = 9 -> 4) & immune to poisoned
        st_pet = _add_cond(st, "gob-1", "petrified")
        r_pet = step(
            st_pet,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[15, 10, 5],  # adv vs petrified, 9 -> 4
        )
        self.assertEqual(r_pet["state"]["combatants"]["gob-1"]["hp"], 40 - 4)

        # Grappled: speed 0 -> cannot move
        st_grap = _add_cond(st, "pc-1", "grappled", source_id="gob-1")
        r_grap = step(st_grap, {"type": "move", "actor_id": "pc-1", "to": {"x": 0, "y": 5}}, self.rules)
        self.assertNotEqual(r_grap["status"], "ok")

        # Restrained: speed 0 + attacks_made disadvantage
        st_rest = _add_cond(st, "pc-1", "restrained")
        r_rest_mv = step(st_rest, {"type": "move", "actor_id": "pc-1", "to": {"x": 0, "y": 5}}, self.rules)
        self.assertNotEqual(r_rest_mv["status"], "ok")
        r_rest_atk = step(
            st_rest,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[18, 2],
        )
        self.assertEqual(next(e for e in r_rest_atk["events"] if e["type"] == "attack_roll")["mode"], "disadvantage")

        # Blinded & Poisoned: attacks_made disadvantage
        for cond in ("blinded", "poisoned"):
            st_c = _add_cond(st, "pc-1", cond)
            r_c = step(
                st_c,
                {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
                self.rules,
                roll_overrides=[18, 2],
            )
            self.assertEqual(
                next(e for e in r_c["events"] if e["type"] == "attack_roll")["mode"],
                "disadvantage",
                f"Expected {cond} to impose Disadvantage on attacks made",
            )

        # Invisible: attacks_made advantage
        st_inv = _add_cond(st, "pc-1", "invisible")
        r_inv = step(
            st_inv,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[2, 18, 4],
        )
        self.assertEqual(next(e for e in r_inv["events"] if e["type"] == "attack_roll")["mode"], "advantage")

        # Frightened: cannot move closer to fear source & attacks_made disadvantage
        st_fright = copy.deepcopy(st)
        st_fright["combatants"]["gob-1"]["position"] = {"x": 20, "y": 0}
        st_fright = _add_cond(st_fright, "pc-1", "frightened", source_id="gob-1")
        r_closer = step(st_fright, {"type": "move", "actor_id": "pc-1", "to": {"x": 10, "y": 0}}, self.rules)
        self.assertNotEqual(r_closer["status"], "ok")
        r_away = step(st_fright, {"type": "move", "actor_id": "pc-1", "to": {"x": -10, "y": 0}}, self.rules)
        self.assertEqual(r_away["status"], "ok")

        # Stunned: cannot act
        st_stun = _add_cond(st, "pc-1", "stunned")
        self.assertNotEqual(
            step(st_stun, {"type": "dodge", "actor_id": "pc-1"}, self.rules)["status"],
            "ok",
        )

        # Charmed: cannot attack the charmer
        st_charm = _add_cond(st, "pc-1", "charmed", source_id="gob-1")
        r_charm = step(
            st_charm,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword"},
            self.rules,
        )
        self.assertNotEqual(r_charm["status"], "ok")

        # Deafened: accepted cleanly without interfering with melee attack
        st_deaf = _add_cond(st, "pc-1", "deafened")
        r_deaf = step(
            st_deaf,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[15, 4],
        )
        self.assertEqual(r_deaf["status"], "ok")


class TestSpellsConcentrationAndRevival(unittest.TestCase):
    """§7.3.5: Fireball upcast/resist/concentration, Shield reaction & Magic Missile, Bless/Hold Person, 0 HP healing, cantrip scaling, narrative fallback."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.wizard = _make_wizard(cls.rules, level=5)
        cls.wizard_l1 = _make_wizard(cls.rules, name="Apprentice", level=1)
        cls.cleric = _make_cleric(cls.rules, level=5)
        cls.fighter = _make_fighter(cls.rules, level=5)

    def test_fireball_save_half_resistance_upcast_and_concentration(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "wiz-1", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "clr-1", "side": "enemy", "statblock": self.cleric, "position": {"x": 30, "y": 0}},
            ],
        )
        # Cleric is concentrating on Bless and has Fire Resistance
        st["combatants"]["clr-1"]["concentration"] = {"spell": "bless", "effect_ids": ["eff-bless-1"]}
        st["combatants"]["clr-1"]["active_effects"] = [
            {
                "id": "eff-bless-1",
                "kind": "bless",
                "source_id": "clr-1",
                "target_id": "clr-1",
                "value": "1d4",
                "expires": "concentration",
                "concentration_spell": "bless",
            }
        ]
        st["combatants"]["clr-1"]["damage_resistances"] = ["fire"]
        hp_before = st["combatants"]["clr-1"]["hp"]

        # 3rd-level Fireball (8d6 -> all 4s = 32); Cleric fails DEX save (d20=2, bless d4=1 -> 3 < 15) -> 32 fire -> resisted to 16!
        # Concentration DC = max(10, 16 // 2) = 10 -> Cleric rolls CON save d20=2 (+2=4, bless d4=1 -> 5 < 10) -> concentration BROKEN!
        r_fb = step(
            st,
            {"type": "cast_spell", "actor_id": "wiz-1", "spell": "fireball", "slot_level": 3, "target_ids": ["clr-1"]},
            self.rules,
            roll_overrides=[4, 4, 4, 4, 4, 4, 4, 4, 2, 1, 2, 1],
        )
        self.assertEqual(r_fb["status"], "ok")
        self.assertEqual(r_fb["state"]["combatants"]["clr-1"]["hp"], hp_before - 16)
        self.assertIsNone(r_fb["state"]["combatants"]["clr-1"]["concentration"])
        self.assertEqual(r_fb["state"]["combatants"]["clr-1"]["active_effects"], [])
        self.assertEqual(r_fb["state"]["combatants"]["wiz-1"]["spellcasting"]["slots"]["3"], 1)

        # Upcast 4th-level Fireball (give wizard a 4th-level slot) -> 9d6! Target succeeds DEX save -> half damage
        st_up = copy.deepcopy(st)
        st_up["combatants"]["wiz-1"]["spellcasting"]["slots"]["4"] = 1
        st_up["combatants"]["clr-1"]["damage_resistances"] = []
        st_up["combatants"]["clr-1"]["concentration"] = None
        st_up["combatants"]["clr-1"]["active_effects"] = []
        # 9d6 all 4s = 36; DEX save d20=18 (>= 15 success) -> half = 18!
        r_up = step(
            st_up,
            {"type": "cast_spell", "actor_id": "wiz-1", "spell": "fireball", "slot_level": 4, "target_ids": ["clr-1"]},
            self.rules,
            roll_overrides=[4, 4, 4, 4, 4, 4, 4, 4, 4, 18],
        )
        self.assertEqual(r_up["status"], "ok")
        self.assertEqual(r_up["state"]["combatants"]["clr-1"]["hp"], hp_before - 18)

    def test_shield_reaction_blocks_attack_negates_magic_missile_and_expires(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "wiz-1", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "wiz-e", "side": "enemy", "statblock": self.wizard, "position": {"x": 20, "y": 0}},
            ],
        )
        # Wizard base AC is 11. Cast Shield as off-turn reaction -> AC becomes 16!
        r_sh = step(
            st,
            {"type": "cast_spell", "actor_id": "wiz-1", "spell": "shield", "target_ids": ["wiz-1"], "as_reaction": True},
            self.rules,
        )
        self.assertEqual(r_sh["status"], "ok")
        self.assertFalse(r_sh["state"]["combatants"]["wiz-1"]["turn_resources"]["reaction"])

        # Goblin rolls 10 (+4 = 14): would hit AC 11, MISSES AC 16!
        r_atk = step(
            r_sh["state"],
            {"type": "attack", "actor_id": "gob-1", "target_id": "wiz-1", "action": "Scimitar"},
            self.rules,
            roll_overrides=[10],
        )
        self.assertFalse(next(e for e in r_atk["events"] if e["type"] == "attack_roll")["hit"])

        # Enemy wizard casts Magic Missile at shielded wiz-1 -> NEGATED (0 damage)!
        hp_wiz = r_atk["state"]["combatants"]["wiz-1"]["hp"]
        r_t1 = step(r_atk["state"], {"type": "end_turn", "actor_id": "gob-1"}, self.rules)
        # Note: advancing to wiz-1's turn expires Shield! So let's test Magic Missile BEFORE wiz-1's turn:
        st_mm = copy.deepcopy(r_sh["state"])
        st_mm["active_index"] = 2  # enemy wizard's turn while wiz-1's Shield is active
        r_mm = step(
            st_mm,
            {"type": "cast_spell", "actor_id": "wiz-e", "spell": "magic-missile", "slot_level": 1, "target_ids": ["wiz-1"]},
            self.rules,
            roll_overrides=[4, 4, 4],
        )
        self.assertEqual(r_mm["status"], "ok")
        self.assertEqual(r_mm["state"]["combatants"]["wiz-1"]["hp"], hp_wiz, "Shield must negate Magic Missile")

        # Advancing to start of wiz-1's turn expires Shield
        self.assertFalse(any(e["kind"] == "shield" for e in r_t1["state"]["combatants"]["wiz-1"]["active_effects"]))

    def test_bless_bonus_and_concentration_replacement(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "clr-1", "side": "party", "statblock": self.cleric, "position": {"x": 0, "y": 0}},
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 5, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 10, "y": 0}},
            ],
        )
        r_bless = step(
            st,
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "bless", "slot_level": 1, "target_ids": ["pc-1", "clr-1"]},
            self.rules,
        )
        self.assertEqual(r_bless["status"], "ok")
        self.assertEqual(r_bless["state"]["combatants"]["clr-1"]["concentration"]["spell"], "bless")
        self.assertTrue(any(e["kind"] == "bless" for e in r_bless["state"]["combatants"]["pc-1"]["active_effects"]))

        # Fighter rolls 6 (+7 = 13 < AC 15), Bless adds +3 (1d4) -> 16 >= 15 HIT!
        r_t1 = step(r_bless["state"], {"type": "end_turn", "actor_id": "clr-1"}, self.rules)
        r_atk = step(
            r_t1["state"],
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[6, 3, 2],  # d20=6, bless d4=3 -> 16 hit!, dmg d8=2
        )
        self.assertTrue(next(e for e in r_atk["events"] if e["type"] == "attack_roll")["hit"])

        # Back to Cleric's turn: cast Hold Person (concentration) -> replaces Bless & removes Bless effects from all allies!
        r_t2 = step(r_atk["state"], {"type": "end_turn", "actor_id": "pc-1"}, self.rules)
        r_t3 = step(r_t2["state"], {"type": "end_turn", "actor_id": "gob-1"}, self.rules)
        r_hp = step(
            r_t3["state"],
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "hold-person", "slot_level": 2, "target_ids": ["gob-1"]},
            self.rules,
            roll_overrides=[2],  # goblin fails WIS save -> paralyzed
        )
        self.assertEqual(r_hp["status"], "ok")
        self.assertEqual(r_hp["state"]["combatants"]["clr-1"]["concentration"]["spell"], "hold-person")
        self.assertFalse(any(e["kind"] == "bless" for e in r_hp["state"]["combatants"]["pc-1"]["active_effects"]))
        self.assertIn("paralyzed", _condition_names(r_hp["state"]["combatants"]["gob-1"]))

    def test_healing_revives_0_hp_pc_leaving_prone(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "clr-1", "side": "party", "statblock": self.cleric, "position": {"x": 0, "y": 0}},
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 5, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 25, "y": 0}},
            ],
        )
        # Drop PC to 0 HP (unconscious + prone)
        st["combatants"]["pc-1"]["hp"] = 0
        st = _add_cond(st, "pc-1", "unconscious")
        st = _add_cond(st, "pc-1", "prone")

        # Healing Word (bonus action, 2d4 + WIS mod) revives PC -> removes unconscious, KEEPS prone!
        r_hw = step(
            st,
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "healing-word", "slot_level": 1, "target_ids": ["pc-1"]},
            self.rules,
            roll_overrides=[3, 4],  # 3 + 4 + 4 (WIS +4) = 11 HP
        )
        self.assertEqual(r_hw["status"], "ok")
        pc = r_hw["state"]["combatants"]["pc-1"]
        self.assertEqual(pc["hp"], 11)
        self.assertNotIn("unconscious", _condition_names(pc))
        self.assertIn("prone", _condition_names(pc), "2024 RAW: reviving from Unconscious leaves creature Prone")

        # Also verify Cure Wounds (2d8 + WIS mod)
        r_cw = step(
            st,
            {"type": "cast_spell", "actor_id": "clr-1", "spell": "cure-wounds", "slot_level": 1, "target_ids": ["pc-1"]},
            self.rules,
            roll_overrides=[5, 6],  # 5 + 6 + 4 = 15 HP
        )
        self.assertEqual(r_cw["state"]["combatants"]["pc-1"]["hp"], 15)
        self.assertNotIn("unconscious", _condition_names(r_cw["state"]["combatants"]["pc-1"]))
        self.assertIn("prone", _condition_names(r_cw["state"]["combatants"]["pc-1"]))

    def test_cantrip_scaling_and_narrative_spell_fallback(self) -> None:
        # Level 1 wizard fire-bolt = 1d10
        st1 = _start(
            self.rules,
            [
                {"id": "wiz-1", "side": "party", "statblock": self.wizard_l1, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 30, "y": 0}},
            ],
        )
        st1["combatants"]["gob-1"]["hp"] = 30
        st1["combatants"]["gob-1"]["hp_max"] = 30
        r1 = step(
            st1,
            {"type": "cast_spell", "actor_id": "wiz-1", "spell": "fire-bolt", "target_ids": ["gob-1"]},
            self.rules,
            roll_overrides=[15, 7],  # d20=15 hit, 1d10=7
        )
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r1["state"]["combatants"]["gob-1"]["hp"], 30 - 7)

        # Level 5 wizard fire-bolt = 2d10
        st5 = _start(
            self.rules,
            [
                {"id": "wiz-5", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 30, "y": 0}},
            ],
        )
        st5["combatants"]["gob-1"]["hp"] = 30
        st5["combatants"]["gob-1"]["hp_max"] = 30
        r5 = step(
            st5,
            {"type": "cast_spell", "actor_id": "wiz-5", "spell": "fire-bolt", "target_ids": ["gob-1"]},
            self.rules,
            roll_overrides=[15, 7, 8],  # d20=15 hit, 2d10 = 7 + 8 = 15
        )
        self.assertEqual(r5["status"], "ok")
        self.assertEqual(r5["state"]["combatants"]["gob-1"]["hp"], 30 - 15)

        # Narrative spell (prestidigitation) emits honest spell_cast_narrative event
        r_nar = step(
            st5,
            {"type": "cast_spell", "actor_id": "wiz-5", "spell": "prestidigitation", "target_ids": []},
            self.rules,
        )
        self.assertEqual(r_nar["status"], "ok")
        nar_evts = [e for e in r_nar["events"] if e["type"] == "spell_cast_narrative"]
        self.assertTrue(nar_evts, f"Expected spell_cast_narrative event, got {r_nar['events']}")
        self.assertEqual(nar_evts[0]["coverage"], "narrative")


class TestMonstersAndTurnEconomy(unittest.TestCase):
    """§7.3.6: Goblin Warrior, Ghoul, Adult Red Dragon, Dodge/Dash/Disengage/Help, end_turn & death saves."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(cls.rules, level=5)

    def test_goblin_warrior_conditional_advantage_damage(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            ],
        )
        hp0 = st["combatants"]["pc-1"]["hp"]

        # Normal hit: 1d6+2 only (d6=4 -> 6 dmg)
        r_norm = step(
            st,
            {"type": "attack", "actor_id": "gob-1", "target_id": "pc-1", "action": "Scimitar"},
            self.rules,
            roll_overrides=[16, 4],  # 16+4=20 vs AC 19 hit, 1d6=4 (+2=6)
        )
        self.assertEqual(r_norm["state"]["combatants"]["pc-1"]["hp"], hp0 - 6)

        # Hit WITH Advantage: 1d6+2 + 1d4 (d6=4 + 2 + d4=3 = 9 dmg!)
        r_adv = step(
            st,
            {
                "type": "attack",
                "actor_id": "gob-1",
                "target_id": "pc-1",
                "action": "Scimitar",
                "advantage_override": True,
            },
            self.rules,
            roll_overrides=[3, 16, 4, 3],  # adv [3, 16] -> 16 hit, 1d6=4 (+2), bonus 1d4=3 -> 9 dmg
        )
        self.assertEqual(r_adv["state"]["combatants"]["pc-1"]["hp"], hp0 - 9)

    def test_ghoul_claw_con_save_paralyzed(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "ghoul-1", "side": "enemy", "monster": "ghoul", "position": {"x": 5, "y": 0}},
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            ],
        )
        # Claw (+4 vs AC 19 -> d20=16 hits), 1d4+2 (d4=3 -> 5 dmg), PC CON save (+5 vs DC 10 -> d20=2 -> 7 fail -> paralyzed!)
        res = step(
            st,
            {"type": "attack", "actor_id": "ghoul-1", "target_id": "pc-1", "action": "Claw"},
            self.rules,
            roll_overrides=[16, 3, 2],
        )
        self.assertEqual(res["status"], "ok")
        self.assertIn("paralyzed", _condition_names(res["state"]["combatants"]["pc-1"]))

    def test_adult_red_dragon_fire_breath_dc21_17d6(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "drag-1", "side": "enemy", "monster": "adult-red-dragon", "position": {"x": 15, "y": 0}},
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            ],
        )
        st["combatants"]["pc-1"]["hp"] = 100
        st["combatants"]["pc-1"]["hp_max"] = 100

        # 17d6 all 3s = 51; PC DEX save (+2 vs DC 21 -> d20=19 -> 21 success -> half = 25!)
        r_save = step(
            st,
            {"type": "attack", "actor_id": "drag-1", "target_id": "pc-1", "action": "Fire Breath"},
            self.rules,
            roll_overrides=[3] * 17 + [19],
        )
        self.assertEqual(r_save["status"], "ok")
        self.assertEqual(r_save["state"]["combatants"]["pc-1"]["hp"], 100 - 25)

        # PC fails DEX save (d20=5 -> 7 < 21) -> full 51 damage
        r_fail = step(
            st,
            {"type": "attack", "actor_id": "drag-1", "target_id": "pc-1", "action": "Fire Breath"},
            self.rules,
            roll_overrides=[3] * 17 + [5],
        )
        self.assertEqual(r_fail["state"]["combatants"]["pc-1"]["hp"], 100 - 51)

    def test_dodge_dash_disengage_help_end_turn_and_death_saves(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "pc-2", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 5}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        # Dash doubles movement_remaining_ft (30 -> 60)
        r_dash = step(st, {"type": "dash", "actor_id": "pc-1"}, self.rules)
        self.assertEqual(r_dash["state"]["combatants"]["pc-1"]["movement_remaining_ft"], 60)

        # Disengage sets disengaged=True
        r_dis = step(st, {"type": "disengage", "actor_id": "pc-1"}, self.rules)
        self.assertTrue(r_dis["state"]["combatants"]["pc-1"]["turn_resources"]["disengaged"])

        # Help gives pc-2 Advantage on next attack vs gob-1
        r_help = step(st, {"type": "help", "actor_id": "pc-1", "ally_id": "pc-2", "target_id": "gob-1"}, self.rules)
        r_t1 = step(r_help["state"], {"type": "end_turn", "actor_id": "pc-1"}, self.rules)
        r_pc2_atk = step(
            r_t1["state"],
            {"type": "attack", "actor_id": "pc-2", "target_id": "gob-1", "weapon": "longsword", "use_mastery": False},
            self.rules,
            roll_overrides=[2, 16, 2],
        )
        self.assertEqual(next(e for e in r_pc2_atk["events"] if e["type"] == "attack_roll")["mode"], "advantage")

        # Dodge imposes Disadvantage on enemy attacks and expires at start of pc-1's next turn
        r_dodge = step(st, {"type": "dodge", "actor_id": "pc-1"}, self.rules)
        r_e1 = step(r_dodge["state"], {"type": "end_turn", "actor_id": "pc-1"}, self.rules)
        r_e2 = step(r_e1["state"], {"type": "end_turn", "actor_id": "pc-2"}, self.rules)
        r_gob = step(
            r_e2["state"],
            {"type": "attack", "actor_id": "gob-1", "target_id": "pc-1", "action": "Scimitar"},
            self.rules,
            roll_overrides=[18, 2],
        )
        self.assertEqual(next(e for e in r_gob["events"] if e["type"] == "attack_roll")["mode"], "disadvantage")
        # End goblin turn -> Round 2, Dodge expires
        r_r2 = step(r_gob["state"], {"type": "end_turn", "actor_id": "gob-1"}, self.rules)
        self.assertEqual(r_r2["state"]["round"], 2)
        self.assertFalse(any(e["kind"] == "dodge" for e in r_r2["state"]["combatants"]["pc-1"]["active_effects"]))

        # Death Saving Throws at 0 HP on start of PC's turn:
        # 1) Normal failure (roll 5 -> failures=1)
        st_dying = copy.deepcopy(r_e2["state"])
        st_dying["combatants"]["pc-1"]["hp"] = 0
        st_dying = _add_cond(st_dying, "pc-1", "unconscious")
        st_dying = _add_cond(st_dying, "pc-1", "prone")

        r_ds_fail = step(st_dying, {"type": "end_turn", "actor_id": "gob-1"}, self.rules, roll_overrides=[5])
        self.assertEqual(r_ds_fail["state"]["combatants"]["pc-1"]["death_saves"]["failures"], 1)

        # 2) Nat-1 adds 2 failures (2 + 1 = 3 -> dead!)
        st_dying2 = copy.deepcopy(st_dying)
        st_dying2["combatants"]["pc-1"]["death_saves"]["failures"] = 1
        r_ds_nat1 = step(st_dying2, {"type": "end_turn", "actor_id": "gob-1"}, self.rules, roll_overrides=[1])
        self.assertFalse(r_ds_nat1["state"]["combatants"]["pc-1"]["alive"])

        # 3) Nat-20 regains 1 HP and wakes Prone!
        r_ds_nat20 = step(st_dying, {"type": "end_turn", "actor_id": "gob-1"}, self.rules, roll_overrides=[20])
        pc_woke = r_ds_nat20["state"]["combatants"]["pc-1"]
        self.assertEqual(pc_woke["hp"], 1)
        self.assertNotIn("unconscious", _condition_names(pc_woke))
        self.assertIn("prone", _condition_names(pc_woke))


if __name__ == "__main__":
    unittest.main()
