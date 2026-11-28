"""#9: step() stops accepting intents once the encounter has ended.

Before the fix the status flipped to "ended" silently, and `end_turn`, `move` and `attack` kept resolving on an
ended state (crypt-1: "Round 5: Aldric's turn begins." after the fight was over).
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from srd2024.arena import load_scenario
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

_REPO = Path(__file__).resolve().parents[1]
_CRYPT_1 = _REPO / "examples" / "sessions" / "2026-11-26-crypt-1.final-state.json"


class TestEncounterEnded(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        scen = load_scenario("goblin-ambush", cls.rules)
        cls.start = step(None, {"type": "start_encounter", "seed": 1, "combatants": scen["combatants"],
                                "encounter_id": "ambush-9"}, cls.rules)["state"]

    def _one_goblin_left(self) -> dict[str, Any]:
        """Bram's turn; goblin-1 is the last enemy standing, at 1 HP, adjacent to him."""
        st = copy.deepcopy(self.start)
        st["active_index"] = st["turn_order"].index("pc-bram")
        for gid in ("goblin-2", "goblin-minion-1"):
            st["combatants"][gid]["hp"] = 0
            st["combatants"][gid]["alive"] = False
        st["combatants"]["goblin-1"]["hp"] = 1
        st["combatants"]["goblin-1"]["position"] = {"x": 5, "y": 0}
        return st

    def _kill(self) -> dict[str, Any]:
        intent = {"type": "attack", "actor_id": "pc-bram", "target_id": "goblin-1", "weapon": "longsword"}
        return step(self._one_goblin_left(), intent, self.rules, roll_overrides=[20, 8])

    def test_encounter_ended_event_emitted_on_last_enemy_death(self) -> None:
        r = self._kill()
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(r["state"]["status"], "ended")
        ended = [e for e in r["events"] if e["type"] == "encounter_ended"]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["winner_side"], "party")
        self.assertEqual(ended[0]["encounter_id"], "ambush-9")
        self.assertEqual(r["events"][-1]["type"], "encounter_ended", "the end is the last thing that happens")
        self.assertEqual(r["narrative_hooks"][-1], "Encounter 'ambush-9' ends: party victorious.")

    def test_no_encounter_ended_event_while_enemies_remain(self) -> None:
        st = self._one_goblin_left()
        st["combatants"]["goblin-2"].update(hp=7, alive=True)
        intent = {"type": "attack", "actor_id": "pc-bram", "target_id": "goblin-1", "weapon": "longsword"}
        r = step(st, intent, self.rules, roll_overrides=[20, 8])
        self.assertEqual(r["state"]["status"], "active")
        self.assertFalse(any(e["type"] == "encounter_ended" for e in r["events"]))

    def test_intents_rejected_after_encounter_ended(self) -> None:
        ended_state = self._kill()["state"]
        snapshot = copy.deepcopy(ended_state)
        for intent in (
            {"type": "end_turn", "actor_id": "pc-bram"},
            {"type": "move", "actor_id": "pc-bram", "to": {"x": 0, "y": 5}},
            {"type": "attack", "actor_id": "pc-bram", "target_id": "goblin-1", "weapon": "longsword"},
            {"type": "cast_spell", "actor_id": "pc-talia", "spell": "fire-bolt", "target_ids": ["goblin-1"]},
            {"type": "dodge", "actor_id": "pc-bram"},
            {"type": "class_feature", "actor_id": "pc-bram", "feature": "second_wind"},
        ):
            with self.subTest(intent=intent["type"]):
                r = step(ended_state, intent, self.rules)
                self.assertEqual(r["status"], "invalid_intent")
                self.assertEqual(r["error"], "Encounter has ended.")
                self.assertEqual(r["state"], snapshot)
                self.assertEqual(r["events"], [])
        self.assertEqual(ended_state, snapshot, "the caller's state must not be mutated")

    def test_start_encounter_still_allowed_after_ended(self) -> None:
        ended_state = self._kill()["state"]
        scen = load_scenario("goblin-ambush", self.rules)
        r = step(ended_state, {"type": "start_encounter", "seed": 2, "combatants": scen["combatants"]}, self.rules)
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(r["state"]["status"], "active")
        self.assertEqual(r["state"]["round"], 1)

    def test_end_turn_after_kill_does_not_advance_round(self) -> None:
        # A state from before #9 (or hand-edited): every enemy is down but status was never flipped.
        st = self._one_goblin_left()
        st["combatants"]["goblin-1"].update(hp=0, alive=False)
        self.assertEqual(st["status"], "active")
        r = step(st, {"type": "end_turn", "actor_id": "pc-bram"}, self.rules)
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(r["state"]["status"], "ended")
        self.assertEqual(r["state"]["round"], st["round"])
        self.assertEqual(r["state"]["active_index"], st["active_index"])
        types = [e["type"] for e in r["events"]]
        self.assertNotIn("turn_started", types)
        self.assertIn("turn_ended", types)
        self.assertEqual(types[-1], "encounter_ended")
        self.assertFalse(any("turn begins" in h for h in r["narrative_hooks"]))

    def test_last_pc_down_ends_with_enemy_victory(self) -> None:
        st = copy.deepcopy(self.start)
        st["active_index"] = st["turn_order"].index("goblin-1")
        for pid in ("pc-bram", "pc-talia"):
            st["combatants"][pid].update(hp=0, alive=False)
        r = step(st, {"type": "end_turn", "actor_id": "goblin-1"}, self.rules)
        ended = [e for e in r["events"] if e["type"] == "encounter_ended"]
        self.assertEqual([e["winner_side"] for e in ended], ["enemy"])
        self.assertEqual(r["narrative_hooks"][-1], "Encounter 'ambush-9' ends: enemy victorious.")


class TestCrypt1Repro(unittest.TestCase):
    """The issue's CLI repro against the committed crypt-1 final state."""

    def _cli(self, intent: dict[str, Any]) -> dict[str, Any]:
        out = subprocess.run(
            [sys.executable, str(_REPO / "bin" / "srd"), "encounter", "--intent", json.dumps(intent),
             "--state", str(_CRYPT_1), "--format", "json"],
            capture_output=True, text=True, check=True, cwd=_REPO,
        )
        return json.loads(out.stdout)

    def test_cli_rejects_end_turn_and_move_on_ended_crypt_1(self) -> None:
        before = json.loads(_CRYPT_1.read_text(encoding="utf-8"))
        self.assertEqual(before["status"], "ended")
        for intent in ({"type": "end_turn", "actor_id": "aldric"},
                       {"type": "move", "actor_id": "aldric", "to": {"x": 30, "y": 10}}):
            with self.subTest(intent=intent["type"]):
                r = self._cli(intent)
                self.assertEqual(r["status"], "invalid_intent")
                self.assertEqual(r["error"], "Encounter has ended.")
                self.assertEqual(r["state"]["round"], before["round"])
                self.assertEqual(r["narrative_hooks"], [])


if __name__ == "__main__":
    unittest.main()
