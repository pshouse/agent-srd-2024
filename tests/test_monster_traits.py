"""Monster traits the engine executes (#39). Dice are fixed with `roll_overrides`."""

from __future__ import annotations

import unittest
from typing import Any

from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

from tests.test_reactions import _wizard
from tests.test_stage4_engine_hardening import _make_fighter, _start


def _evs(res: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [e for e in res["events"] if e["type"] == kind]


class TestMonsterTraits(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(cls.rules, name="Bram", level=5, weapons=["longsword"], mastery_weapons=["longsword"])
        cls.wizard = _wizard(cls.rules)

    def _ok(self, res: dict[str, Any]) -> dict[str, Any]:
        self.assertEqual(res["status"], "ok", res.get("error"))
        return res

    def _attack(self, st: dict[str, Any], who: str, action: str, target: str, rolls: list[int]) -> dict[str, Any]:
        return self._ok(step(st, {"type": "attack", "actor_id": who, "action": action, "target_ids": [target]},
                             self.rules, roll_overrides=rolls))

    def test_pack_tactics_needs_an_able_ally_next_to_the_target(self) -> None:
        def setup(ally_pos: dict[str, int]) -> dict[str, Any]:
            st = _start(self.rules, [
                {"id": "hound", "side": "enemy", "monster": "hell-hound", "position": {"x": 5, "y": 0}},
                {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "hound2", "side": "enemy", "monster": "hell-hound", "position": ally_pos},
            ])
            st["combatants"]["pc"]["hp"] = st["combatants"]["pc"]["hp_max"] = 99
            return st

        res = self._attack(setup({"x": 0, "y": 5}), "hound", "Bite", "pc", [5, 15, 3, 3])
        atk = _evs(res, "attack_roll")[0]
        self.assertIn("pack_tactics", atk["advantage_sources"])
        self.assertEqual(atk["mode"], "advantage")

        res = self._attack(setup({"x": 40, "y": 40}), "hound", "Bite", "pc", [15, 3, 3])
        self.assertNotIn("pack_tactics", _evs(res, "attack_roll")[0]["advantage_sources"])

        st = setup({"x": 0, "y": 5})
        st["combatants"]["hound2"]["conditions"].append({"condition": "paralyzed", "duration": "indefinite"})
        res = self._attack(st, "hound", "Bite", "pc", [15, 3, 3])
        self.assertNotIn("pack_tactics", _evs(res, "attack_roll")[0]["advantage_sources"])

    def test_magic_resistance_gives_advantage_on_spell_saves(self) -> None:
        st = _start(self.rules, [
            {"id": "wiz", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
            {"id": "devil", "side": "enemy", "monster": "barbed-devil", "position": {"x": 30, "y": 0}},
        ])
        res = self._ok(step(st, {"type": "cast_spell", "actor_id": "wiz", "spell": "fireball", "slot_level": 3,
                                 "target_ids": ["devil"]}, self.rules, roll_overrides=[3] * 8 + [4, 17]))
        sv = next(e for e in _evs(res, "saving_throw") if e["target_id"] == "devil")
        self.assertEqual((sv["mode"], sv["rolls"], sv["chosen"]), ("advantage", [4, 17], 17))

    def test_legendary_resistance_turns_failures_into_successes_until_spent(self) -> None:
        st = _start(self.rules, [
            {"id": "wiz", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
            {"id": "dragon", "side": "enemy", "monster": "adult-red-dragon", "position": {"x": 60, "y": 0}},
        ])
        self.assertEqual(st["combatants"]["dragon"]["legendary_resistance_uses"], 3)
        hold = {"type": "cast_spell", "actor_id": "wiz", "spell": "hold-person", "slot_level": 2, "target_ids": ["dragon"]}
        for uses_left in (2, 1, 0, None):  # each save rolls a 1 and fails; the first three become successes
            st["combatants"]["wiz"]["turn_resources"]["action"] = True
            st["combatants"]["wiz"]["spellcasting"]["slots"]["2"] = 3
            st["combatants"]["wiz"]["concentration"] = None
            res = self._ok(step(st, hold, self.rules, roll_overrides=[1]))
            sv = _evs(res, "saving_throw")[0]
            if uses_left is None:
                self.assertFalse(sv["success"], "no uses left")
                self.assertFalse(_evs(res, "legendary_resistance_used"))
            else:
                self.assertTrue(sv["success"] and sv.get("legendary_resistance"))
                self.assertEqual(_evs(res, "legendary_resistance_used")[0]["uses_left"], uses_left)
            st = res["state"]

    def test_stench_save_poisons_until_next_turn_and_success_grants_immunity(self) -> None:
        st = _start(self.rules, [
            {"id": "ghast", "side": "enemy", "monster": "ghast", "position": {"x": 5, "y": 0}},
            {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
        ])
        # The fighter's turn starts next to the ghast: CON DC 10, fail -> Poisoned.
        res = self._ok(step(st, {"type": "end_turn", "actor_id": "ghast"}, self.rules, roll_overrides=[2]))
        sv = next(e for e in _evs(res, "saving_throw") if e["target_id"] == "pc")
        self.assertEqual((sv["ability"], sv["dc"], sv["reason"], sv["success"]), ("con", 10, "Stench", False))
        self.assertIn("poisoned", {c["condition"] if isinstance(c, dict) else c for c in res["state"]["combatants"]["pc"]["conditions"]})
        # Next turn start: Poisoned ends, then a fresh save; this one succeeds -> immune.
        st = self._ok(step(res["state"], {"type": "end_turn", "actor_id": "pc"}, self.rules))["state"]
        res = self._ok(step(st, {"type": "end_turn", "actor_id": "ghast"}, self.rules, roll_overrides=[19]))
        ended = [e for e in _evs(res, "condition_ended") if e["target_id"] == "pc" and e["condition"] == "poisoned"]
        self.assertTrue(ended)
        self.assertTrue(next(e for e in _evs(res, "saving_throw") if e["target_id"] == "pc")["success"])
        self.assertIn("ghast", res["state"]["combatants"]["pc"]["aura_immune_to"])
        # Immune: no more Stench saves.
        st = self._ok(step(res["state"], {"type": "end_turn", "actor_id": "pc"}, self.rules))["state"]
        res = self._ok(step(st, {"type": "end_turn", "actor_id": "ghast"}, self.rules))
        self.assertFalse([e for e in _evs(res, "saving_throw") if e.get("reason") == "Stench"])

    def test_death_burst_hits_everyone_in_range_once_with_half_on_success(self) -> None:
        st = _start(self.rules, [
            {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            {"id": "pc2", "side": "party", "statblock": self.fighter, "position": {"x": 10, "y": 5}},
            {"id": "far", "side": "party", "statblock": self.fighter, "position": {"x": 40, "y": 0}},
            {"id": "magmin", "side": "enemy", "monster": "magmin", "position": {"x": 5, "y": 0}},
        ])
        for cid in ("pc", "pc2", "far"):
            st["combatants"][cid]["hp"] = st["combatants"][cid]["hp_max"] = 99
        st["combatants"]["magmin"]["hp"] = 1
        # Longsword hit kills it; burst 2d6 = 4+5 = 9 fire; pc fails (DEX 1), pc2 succeeds (DEX 20).
        res = self._ok(step(st, {"type": "attack", "actor_id": "pc", "target_id": "magmin", "weapon": "longsword"},
                            self.rules, roll_overrides=[15, 4, 4, 5, 1, 20]))
        burst = _evs(res, "death_burst")[0]
        self.assertEqual((burst["damage"], burst["damage_type"], sorted(burst["targets"])), (9, "fire", ["pc", "pc2"]))
        dmg = {e["target_id"]: e["total_damage"] for e in _evs(res, "damage_applied") if e.get("source_id") == "magmin"}
        self.assertEqual(dmg, {"pc": 9, "pc2": 4})

    def test_regeneration_heals_at_turn_start(self) -> None:
        st = _start(self.rules, [
            {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            {"id": "oni", "side": "enemy", "monster": "oni", "position": {"x": 60, "y": 0}},
        ])
        st["combatants"]["oni"]["hp"] = st["combatants"]["oni"]["hp_max"] - 25
        res = self._ok(step(st, {"type": "end_turn", "actor_id": "pc"}, self.rules))
        heal = _evs(res, "healing_applied")[0]
        self.assertEqual((heal["target_id"], heal["trait"], heal["amount"]), ("oni", "regeneration", 10))

    def test_undead_fortitude(self) -> None:
        def zombie_at(hp: int) -> dict[str, Any]:
            st = _start(self.rules, [
                {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "zed", "side": "enemy", "monster": "zombie", "position": {"x": 5, "y": 0}},
            ])
            st["combatants"]["zed"]["hp"] = hp
            return st

        hit = {"type": "attack", "actor_id": "pc", "target_id": "zed", "weapon": "longsword"}
        # 15 to hit, 1d8+4 = 6 slashing vs 3 HP: CON save DC 11, roll 18 -> drops to 1 HP instead.
        res = self._ok(step(zombie_at(3), hit, self.rules, roll_overrides=[15, 2, 18]))
        dmg = _evs(res, "damage_applied")[0]
        self.assertEqual((dmg["hp_after"], dmg.get("undead_fortitude")), (1, True))
        self.assertEqual(next(e for e in _evs(res, "saving_throw") if e["reason"] == "Undead Fortitude")["dc"], 11)
        self.assertFalse(_evs(res, "combatant_defeated"))
        # Failed save -> defeated.
        res = self._ok(step(zombie_at(3), hit, self.rules, roll_overrides=[15, 2, 1]))
        self.assertTrue(_evs(res, "combatant_defeated"))
        # A critical hit: no save.
        res = self._ok(step(zombie_at(3), hit, self.rules, roll_overrides=[20, 2, 2]))
        self.assertFalse([e for e in _evs(res, "saving_throw") if e["reason"] == "Undead Fortitude"])
        self.assertTrue(_evs(res, "combatant_defeated"))

    def test_parry_turns_a_close_melee_hit_into_a_miss(self) -> None:
        def setup() -> dict[str, Any]:
            st = _start(self.rules, [
                {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "cap", "side": "enemy", "monster": "bandit-captain", "position": {"x": 5, "y": 0}},
            ])
            return st

        st = setup()
        ac = st["combatants"]["cap"]["ac"]
        bonus = st["combatants"]["pc"]["weapons"][0]["attack_bonus"]
        res = self._ok(step(st, {"type": "attack", "actor_id": "pc", "target_id": "cap", "weapon": "longsword"},
                            self.rules, roll_overrides=[ac - bonus + 1, 4]))  # total = AC + 1
        parry = _evs(res, "reaction_parry")[0]
        self.assertEqual((parry["actor_id"], parry["ac_bonus"], parry["reaction"]), ("cap", 2, True))
        atk = _evs(res, "attack_roll")[0]
        self.assertEqual((atk["target_ac"], atk["hit"]), (ac + 2, False))
        self.assertFalse(res["state"]["combatants"]["cap"]["turn_resources"]["reaction"])
        # A hit by 2 or more over AC can't be parried away.
        res = self._ok(step(setup(), {"type": "attack", "actor_id": "pc", "target_id": "cap", "weapon": "longsword"},
                            self.rules, roll_overrides=[ac - bonus + 2, 4]))
        self.assertFalse(_evs(res, "reaction_parry"))


if __name__ == "__main__":
    unittest.main()
