"""Instant death from massive damage (SRD 5.2, #46). Dice are fixed with `roll_overrides`."""

from __future__ import annotations

import unittest
from typing import Any

from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

from tests.test_stage4_engine_hardening import _make_fighter, _start


class TestMassiveDamage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(cls.rules, name="Bram", level=1, weapons=["longsword"], mastery_weapons=["longsword"])

    def _hit(self, hp: int, hp_max: int, rolls: list[int], *, temp_hp: int = 0, at_zero: bool = False) -> dict[str, Any]:
        st = _start(self.rules, [
            {"id": "gob", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
        ])
        pc = st["combatants"]["pc"]
        pc["hp"], pc["hp_max"], pc["temp_hp"] = hp, hp_max, temp_hp
        if at_zero:
            pc["conditions"] += [{"condition": "unconscious", "duration": "indefinite"}, {"condition": "prone", "duration": "indefinite"}]
        res = step(st, {"type": "attack", "actor_id": "gob", "action": "Scimitar", "target_ids": ["pc"]}, self.rules,
                   roll_overrides=rolls)
        self.assertEqual(res["status"], "ok", res.get("error"))
        return res

    @staticmethod
    def _died(res: dict[str, Any]) -> list[dict[str, Any]]:
        return [e for e in res["events"] if e["type"] == "combatant_died"]

    def test_overflow_equal_to_max_hp_kills(self) -> None:
        res = self._hit(1, 4, [15, 3])  # 1d6+2 = 5 at 1 HP: 4 left over = max HP
        died = self._died(res)
        self.assertEqual((died[0]["reason"], died[0]["overflow"], died[0]["hp_max"]), ("massive_damage", 4, 4))
        self.assertFalse(res["state"]["combatants"]["pc"]["alive"])
        self.assertFalse([e for e in res["events"] if e["type"] == "pc_unconscious"])

    def test_overflow_below_max_hp_knocks_unconscious(self) -> None:
        res = self._hit(1, 5, [15, 3])  # 4 left over < 5
        self.assertFalse(self._died(res))
        self.assertTrue([e for e in res["events"] if e["type"] == "pc_unconscious"])
        self.assertTrue(res["state"]["combatants"]["pc"]["alive"])

    def test_temp_hp_soaks_before_overflow_is_counted(self) -> None:
        res = self._hit(1, 4, [15, 3], temp_hp=2)  # 5 damage: 2 temp, 1 HP, 2 left over < 4
        self.assertFalse(self._died(res))

    def test_damage_at_zero_hp_equal_to_max_kills(self) -> None:
        # vs Unconscious: Advantage (two d20s, and the goblin's +1d4), and a melee hit within 5 ft is a crit:
        # 2d6+2 + 2d4 = 3+3+2 + 1+1 = 10 = max HP.
        res = self._hit(0, 10, [15, 15, 3, 3, 1, 1], at_zero=True)
        died = self._died(res)
        self.assertEqual((died[0]["reason"], died[0]["overflow"]), ("massive_damage", 10))
        res = self._hit(0, 11, [15, 15, 3, 3, 1, 1], at_zero=True)  # 10 < 11: two death-save failures instead
        self.assertFalse(self._died(res))
        fail = next(e for e in res["events"] if e["type"] == "death_save_damage_failure")
        self.assertEqual(fail["failures_added"], 2)


if __name__ == "__main__":
    unittest.main()
