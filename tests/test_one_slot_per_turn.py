"""#52: SRD 5.2 "One Spell with a Spell Slot per Turn".

"On a turn, you can expend only one spell slot to cast a spell." Cantrips spend no slot, and a Reaction on another
creature's turn is a different turn. Before the fix Dorian could cast Bless (Action, slot) and Healing Word (Bonus
Action, slot) on the same turn, and legality scoring counted both as legal.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.arena import AgentDecision, HeuristicTacticalDriver, build_observation, load_scenario, run_match
from srd2024.arena.agents import _available_slots_map, _can_cast_spell_now
from srd2024.engine.combat import _maybe_reactive_shield, _StepTracker, compute_legal_actions, step
from srd2024.engine.store import RulesStore


class TestOneSlotPerTurn(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.scen = load_scenario("crypt-of-ghouls", cls.rules)
        cls.start = step(None, {"type": "start_encounter", "seed": 4, "combatants": cls.scen["combatants"]}, cls.rules)["state"]

    def _on_turn(self, cid: str) -> dict[str, Any]:
        st = copy.deepcopy(self.start)
        st["active_index"] = st["turn_order"].index(cid)
        return st

    def _cast(self, st, actor, spell, slot=None, targets=None) -> dict[str, Any]:
        intent: dict[str, Any] = {"type": "cast_spell", "actor_id": actor, "spell": spell}
        if slot is not None:
            intent["slot_level"] = slot
        if targets is not None:
            intent["target_ids"] = targets
        return step(st, intent, self.rules)

    def test_second_slotted_spell_is_refused_and_spends_nothing(self) -> None:
        st = self._on_turn("pc-dorian")
        r1 = self._cast(st, "pc-dorian", "bless", 1, ["pc-dorian", "pc-aldric", "pc-lyra"])
        self.assertEqual(r1["status"], "ok", r1.get("error"))
        self.assertTrue(r1["state"]["combatants"]["pc-dorian"]["turn_resources"]["slot_spent_this_turn"])
        r2 = self._cast(r1["state"], "pc-dorian", "healing-word", 1, ["pc-lyra"])
        self.assertEqual(r2["status"], "invalid_intent")
        self.assertIn("one spell slot per turn", r2["error"])
        dorian = r2["state"]["combatants"]["pc-dorian"]
        self.assertEqual(dorian["spellcasting"]["slots"], r1["state"]["combatants"]["pc-dorian"]["spellcasting"]["slots"])
        self.assertTrue(dorian["turn_resources"]["bonus_action"], "the refused cast must not spend the Bonus Action")

    def test_cantrips_still_combine_with_a_slot_either_order(self) -> None:
        st = self._on_turn("pc-dorian")
        a = self._cast(st, "pc-dorian", "healing-word", 1, ["pc-lyra"])
        self.assertEqual(a["status"], "ok", a.get("error"))
        b = self._cast(a["state"], "pc-dorian", "sacred-flame", None, ["ghoul-1"])
        self.assertEqual(b["status"], "ok", b.get("error"))
        c = self._cast(st, "pc-dorian", "sacred-flame", None, ["ghoul-1"])
        self.assertEqual(c["status"], "ok", c.get("error"))
        d = self._cast(c["state"], "pc-dorian", "healing-word", 1, ["pc-lyra"])
        self.assertEqual(d["status"], "ok", d.get("error"))

    def test_flag_resets_for_the_next_turn(self) -> None:
        st = self._on_turn("pc-dorian")
        st = self._cast(st, "pc-dorian", "healing-word", 1, ["pc-lyra"])["state"]
        st = step(st, {"type": "end_turn", "actor_id": "pc-dorian"}, self.rules)["state"]
        self.assertNotIn("slot_spent_this_turn", st["combatants"]["pc-dorian"]["turn_resources"])
        while st["turn_order"][st["active_index"]] != "pc-dorian":
            st = step(st, {"type": "end_turn", "actor_id": st["turn_order"][st["active_index"]]}, self.rules)["state"]
        self.assertEqual(self._cast(st, "pc-dorian", "healing-word", 1, ["pc-lyra"])["status"], "ok")

    def test_reactive_shield_on_another_turn_is_allowed_but_not_on_your_own(self) -> None:
        st = self._on_turn("ghoul-1")
        lyra = st["combatants"]["pc-lyra"]
        lyra["turn_resources"]["slot_spent_this_turn"] = True  # stale from her own turn: irrelevant now
        tracker = _StepTracker(st)
        self.assertIsNotNone(_maybe_reactive_shield(st, "pc-lyra", "ghoul-1", {"nat20": False}, 12, 11, self.rules, tracker))

        st = self._on_turn("pc-lyra")
        st["combatants"]["pc-lyra"]["turn_resources"]["slot_spent_this_turn"] = True
        tracker = _StepTracker(st)
        self.assertIsNone(_maybe_reactive_shield(st, "pc-lyra", "ghoul-1", {"nat20": False}, 12, 11, self.rules, tracker))

    def test_drivers_stop_offering_slotted_spells(self) -> None:
        st = self._on_turn("pc-dorian")
        st = self._cast(st, "pc-dorian", "bless", 1, ["pc-dorian", "pc-aldric", "pc-lyra"])["state"]
        legal = compute_legal_actions(st, self.rules)
        self.assertNotIn("healing-word", legal["spells"])
        obs = build_observation(st, "pc-dorian", self.scen, {}, [], {}, self.rules)
        me = obs["self"]
        hw = self.rules.get_spell("healing-word")
        self.assertFalse(_can_cast_spell_now("healing-word", hw, me, _available_slots_map(me))[0])

    def test_legality_score_no_longer_credits_a_double_cast(self) -> None:
        rules = self.rules

        class DoubleCaster:
            """Dorian tries Bless then Healing Word on his first turn; everyone else plays the heuristic."""

            name = "double-caster"

            def __init__(self) -> None:
                self.inner = HeuristicTacticalDriver(rules)
                self.tries = 0

            def reset(self, seed=None) -> None:
                self.inner.reset(seed)

            def decide(self, obs):
                me = obs["self"]
                if me["id"] == "pc-dorian" and self.tries < 2 and int(obs["round"]) == 1:
                    self.tries += 1
                    if self.tries == 1:
                        return AgentDecision(intent={"type": "cast_spell", "actor_id": me["id"], "spell": "bless",
                                                     "slot_level": 1, "target_ids": [me["id"]]}, rationale="slot 1")
                    return AgentDecision(intent={"type": "cast_spell", "actor_id": me["id"], "spell": "healing-word",
                                                 "slot_level": 1, "target_ids": [me["id"]]}, rationale="slot 2")
                return self.inner.decide(obs)

        # Seed 2: Dorian acts in round 1. (Seed 4 stopped working with #53: the ghouls now spread out and
        # paralyze him before his first turn, so he never gets to try the double cast.)
        t = run_match("crypt-of-ghouls", party_driver=DoubleCaster(), enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=2)
        rejected = [v for v in t["violations"] if v["actor_id"] == "pc-dorian" and "one spell slot per turn" in v["error"]]
        self.assertEqual(len(rejected), 1)
        accepted = [a for a in t["actions"] if a["actor_id"] == "pc-dorian" and a["intent"].get("spell") == "healing-word"
                    and int(a["round"]) == 1]
        self.assertTrue(all(not a["first_attempt_legal"] for a in accepted))
        self.assertLess(t["scorecard"]["rules_legality_score"], 100.0)


if __name__ == "__main__":
    unittest.main()
