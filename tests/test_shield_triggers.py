"""#33: Shield is only ever a Reaction to its SRD 5.2 triggers.

#43 covered the main part: own-turn Shield is refused, and the engine casts Shield when an attack roll would
hit. This adds the other trigger, being targeted by Magic Missile, and locks in that no built-in driver tries
an own-turn Shield.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.arena import load_scenario, run_match
from srd2024.arena.agents import create_driver
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore


class TestShieldTriggers(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        scen = load_scenario("wyrmling-lair", cls.rules)
        cls.lyra = next(c for c in scen["combatants"] if c["id"] == "pc-lyra")

    def _duel(self, *, target_tweak=None) -> dict[str, Any]:
        """An enemy copy of Lyra (Magic Missile) acts first against pc-lyra (Shield prepared)."""
        caster = copy.deepcopy(self.lyra)
        caster.update({"id": "mage", "side": "enemy", "position": {"x": 30, "y": 0}})
        target = copy.deepcopy(self.lyra)
        target["position"] = {"x": 0, "y": 0}
        st = step(None, {"type": "start_encounter", "seed": 3, "combatants": [caster, target]}, self.rules,
                  roll_overrides=[20, 1])["state"]
        self.assertEqual(st["turn_order"][st["active_index"]], "mage")
        if target_tweak:
            target_tweak(st["combatants"]["pc-lyra"])
        return st

    def _missile(self, st: dict[str, Any]) -> dict[str, Any]:
        r = step(st, {"type": "cast_spell", "actor_id": "mage", "spell": "magic-missile", "slot_level": 1,
                      "target_ids": ["pc-lyra"]}, self.rules)
        self.assertEqual(r["status"], "ok", r.get("error"))
        return r

    def test_magic_missile_triggers_reactive_shield(self) -> None:
        st = self._duel()
        slots_before = dict(st["combatants"]["pc-lyra"]["spellcasting"]["slots"])
        r = self._missile(st)
        lyra = r["state"]["combatants"]["pc-lyra"]
        casts = [e for e in r["events"] if e["type"] == "spell_cast" and e.get("spell") == "shield"]
        self.assertEqual(len(casts), 1)
        self.assertEqual(casts[0]["trigger"], "targeted_by_magic_missile")
        self.assertTrue(casts[0]["reaction"])
        self.assertEqual(lyra["hp"], lyra["hp_max"])
        self.assertFalse(lyra["turn_resources"]["reaction"])
        self.assertEqual(sum(slots_before.values()) - 1, sum(lyra["spellcasting"]["slots"].values()))
        self.assertIn("spell_blocked", [e["type"] for e in r["events"]])
        hooks = r["narrative_hooks"]
        self.assertLess(
            next(i for i, h in enumerate(hooks) if "casts Shield as a Reaction" in h),
            next(i for i, h in enumerate(hooks) if "blocks Magic Missile" in h),
        )

    def test_no_shield_without_reaction_or_with_policy_none(self) -> None:
        for tweak in (
            lambda c: c["turn_resources"].update({"reaction": False}),
            lambda c: c.update({"reaction_policy": "none"}),
            lambda c: c["spellcasting"].update({"slots": {}}),
        ):
            with self.subTest():
                r = self._missile(self._duel(target_tweak=tweak))
                lyra = r["state"]["combatants"]["pc-lyra"]
                self.assertFalse(any(e.get("spell") == "shield" for e in r["events"] if e["type"] == "spell_cast"))
                self.assertLess(lyra["hp"], lyra["hp_max"])

    def test_own_turn_shield_is_still_refused(self) -> None:
        st = self._duel()
        st["active_index"] = st["turn_order"].index("pc-lyra")
        r = step(st, {"type": "cast_spell", "actor_id": "pc-lyra", "spell": "shield", "slot_level": 1}, self.rules)
        self.assertEqual(r["status"], "invalid_intent")

    def test_built_in_drivers_never_plan_an_own_turn_shield(self) -> None:
        for drv in ("heuristic-tactical", "greedy", "random"):
            for scenario in ("crypt-of-ghouls", "wyrmling-lair"):
                for seed in (1, 2, 3):
                    t = run_match(scenario, party_driver=create_driver(drv, rules=self.rules, seed=seed),
                                  enemy_driver=create_driver("heuristic-tactical", rules=self.rules),
                                  rules=self.rules, seed=seed)
                    planned = [a for a in t["actions"] if a["intent"].get("spell") == "shield"]
                    planned += [v for v in t["violations"] if (v.get("intent") or {}).get("spell") == "shield"]
                    self.assertEqual(planned, [], f"{drv} {scenario} seed {seed}")


if __name__ == "__main__":
    unittest.main()
