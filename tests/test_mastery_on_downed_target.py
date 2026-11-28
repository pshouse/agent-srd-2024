"""#27: on-hit weapon masteries don't land on a creature the hit just dropped to 0 HP.

goblin-nl-1 seq 11: Bram's longsword dropped Goblin Scout A to 0 HP and Sap still put its effect on the body. Push,
Sap, Slow, Topple and Vex now record `mastery_skipped` (`reason: "target_down"`) instead. Cleave is about a
second creature and still triggers.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.arena import load_scenario
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

_TARGET_MASTERIES = ("push", "sap", "slow", "topple", "vex")


class TestMasteryOnDownedTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        scen = load_scenario("goblin-ambush", cls.rules)
        cls.start = step(None, {"type": "start_encounter", "seed": 5, "combatants": scen["combatants"]},
                         cls.rules)["state"]

    def _state(self, mastery: str, goblin_hp: int) -> dict[str, Any]:
        """Bram's turn, longsword carrying `mastery`; goblin-1 adjacent with `goblin_hp`, goblin-2 next to it."""
        st = copy.deepcopy(self.start)
        st["active_index"] = st["turn_order"].index("pc-bram")
        bram = st["combatants"]["pc-bram"]
        sword = next(w for w in bram["weapons"] if w["slug"] == "longsword")
        sword.update(mastery=mastery, mastery_active=True)
        bram["position"] = {"x": 0, "y": 0}
        st["combatants"]["goblin-1"].update(hp=goblin_hp, position={"x": 5, "y": 0})
        st["combatants"]["goblin-2"]["position"] = {"x": 5, "y": 5}
        st["combatants"]["goblin-minion-1"]["position"] = {"x": 40, "y": 40}
        return st

    def _hit(self, st: dict[str, Any], **extra: Any) -> dict[str, Any]:
        intent = {"type": "attack", "actor_id": "pc-bram", "target_id": "goblin-1", "weapon": "longsword", **extra}
        r = step(st, intent, self.rules, roll_overrides=[19, 8, 1, 1, 1, 1])
        self.assertEqual(r["status"], "ok", r.get("error"))
        return r

    def test_target_masteries_skip_a_creature_the_hit_drops(self) -> None:
        for mastery in _TARGET_MASTERIES:
            with self.subTest(mastery=mastery):
                st = self._state(mastery, goblin_hp=1)
                r = self._hit(st)
                gob = r["state"]["combatants"]["goblin-1"]
                bram = r["state"]["combatants"]["pc-bram"]
                self.assertEqual(gob["hp"], 0)
                types = [e["type"] for e in r["events"]]
                self.assertNotIn("mastery_triggered", types)
                skipped = [e for e in r["events"] if e["type"] == "mastery_skipped"]
                self.assertEqual([(e["mastery"], e["reason"]) for e in skipped], [(mastery, "target_down")])
                self.assertEqual(gob["active_effects"], [])
                self.assertEqual([e["kind"] for e in bram["active_effects"]], [])
                self.assertEqual(gob["position"], {"x": 5, "y": 0}, "no Push on the body")
                self.assertNotIn("prone", [c if isinstance(c, str) else c.get("condition") for c in gob["conditions"]])
                self.assertFalse(any(e["type"] == "saving_throw" for e in r["events"]), "no Topple save rolled")
                self.assertFalse(any("mastery" in h.lower() for h in r["narrative_hooks"]))

    def test_target_masteries_still_trigger_when_the_target_survives(self) -> None:
        for mastery in _TARGET_MASTERIES:
            with self.subTest(mastery=mastery):
                r = self._hit(self._state(mastery, goblin_hp=50))
                triggered = [e for e in r["events"] if e["type"] == "mastery_triggered"]
                self.assertEqual([e["mastery"] for e in triggered], [mastery])

    def test_dying_pc_gets_no_mastery_effect(self) -> None:
        st = copy.deepcopy(self.start)
        st["active_index"] = st["turn_order"].index("pc-bram")
        bram = st["combatants"]["pc-bram"]
        next(w for w in bram["weapons"] if w["slug"] == "longsword").update(mastery="sap", mastery_active=True)
        talia = st["combatants"]["pc-talia"]
        talia.update(hp=0, position={"x": 5, "y": 0})
        bram["position"] = {"x": 0, "y": 0}
        r = step(st, {"type": "attack", "actor_id": "pc-bram", "target_id": "pc-talia", "weapon": "longsword"},
                 self.rules, roll_overrides=[19, 8])
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(r["state"]["combatants"]["pc-talia"]["active_effects"], [])
        self.assertIn(("sap", "target_down"),
                      [(e["mastery"], e["reason"]) for e in r["events"] if e["type"] == "mastery_skipped"])

    def test_cleave_still_triggers_when_the_primary_drops(self) -> None:
        st = self._state("cleave", goblin_hp=1)
        r = self._hit(st, cleave_target_id="goblin-2")
        cleave = [e for e in r["events"] if e["type"] == "mastery_triggered" and e["mastery"] == "cleave"]
        self.assertEqual(len(cleave), 1)
        self.assertEqual(cleave[0]["cleave_target_id"], "goblin-2")


if __name__ == "__main__":
    unittest.main()
