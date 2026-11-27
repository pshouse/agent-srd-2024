"""#55: retry feedback carries every intent rejected this turn, not just the latest one.

Before the fix, a driver saw only its last rejection. In the Phase 3 bench (wyrmling-lair seed 8), Opus 5.5
went offhand attack -> disengage -> the same offhand attack again, then lost the turn to a forced end_turn.
"""

from __future__ import annotations

import unittest
from typing import Any

from srd2024.arena import AgentDecision, HeuristicTacticalDriver, replay_match, run_match
from srd2024.arena.runner import _retry_feedback_with_history
from srd2024.engine.store import RulesStore

_GHOST_A = "ghost-target-a"
_GHOST_B = "ghost-target-b"


def _bad_attack(actor_id: str, target: str) -> dict[str, Any]:
    return {"type": "attack", "actor_id": actor_id, "target_id": target, "weapon": "longsword"}


class _ScriptedDriver:
    """Plays a fixed list of intents per party turn, then ends the turn. Records the feedback it saw."""

    name = "scripted"

    def __init__(self, scripts: list[list[str]]) -> None:
        # Each script entry is "A" / "B" (illegal attacks on missing targets), "dodge", or "end".
        self.scripts = scripts
        self.turns: list[list[str | None]] = []
        self._turn_key: tuple[int, str] | None = None
        self._step = 0

    def reset(self, seed: int | None = None) -> None:
        return

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        me = observation["self"]["id"]
        key = (int(observation["round"]), me)
        if key != self._turn_key:
            self._turn_key = key
            self._step = 0
            self.turns.append([])
        self.turns[-1].append(observation.get("retry_feedback"))
        script = self.scripts[len(self.turns) - 1] if len(self.turns) <= len(self.scripts) else []
        move = script[self._step] if self._step < len(script) else "end"
        self._step += 1
        if move == "A":
            intent = _bad_attack(me, _GHOST_A)
        elif move == "B":
            intent = _bad_attack(me, _GHOST_B)
        elif move == "dodge":
            intent = {"type": "dodge", "actor_id": me}
        else:
            intent = {"type": "end_turn", "actor_id": me}
        return AgentDecision(intent=intent, rationale="scripted")


class TestRetryFeedbackHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def _run(self, scripts: list[list[str]]) -> tuple[_ScriptedDriver, dict[str, Any]]:
        driver = _ScriptedDriver(scripts)
        transcript = run_match(
            "goblin-ambush",
            party_driver={"pc-bram": driver, "pc-talia": HeuristicTacticalDriver(self.rules)},
            enemy_driver=HeuristicTacticalDriver(self.rules),
            rules=self.rules,
            seed=42,
            max_rounds=3,
            max_retries_per_action=2,
        )
        return driver, transcript

    def test_third_attempt_sees_both_earlier_rejections(self) -> None:
        driver, transcript = self._run([["A", "B", "A"]])
        first_turn = driver.turns[0]
        self.assertIsNone(first_turn[0])
        # Attempt 2: only one rejection so far, so the message is the plain latest-rejection text.
        self.assertIn(_GHOST_A, first_turn[1])
        self.assertNotIn("Also rejected earlier this turn", first_turn[1])
        # Attempt 3: latest (B) plus the earlier A.
        self.assertIn(_GHOST_B, first_turn[2])
        self.assertIn("Also rejected earlier this turn", first_turn[2])
        earlier = first_turn[2].split("Also rejected earlier this turn", 1)[1]
        self.assertIn(_GHOST_A, earlier)
        self.assertNotIn(_GHOST_B, earlier)
        self.assertGreaterEqual(transcript["scorecard"]["metrics"]["forced_fallbacks"], 1)

    def test_history_spans_action_slots_within_a_turn(self) -> None:
        # Slot 0: A rejected, then Dodge accepted. Slot 1: B rejected; its retry should still list A.
        driver, _ = self._run([["A", "dodge", "B", "end"]])
        first_turn = driver.turns[0]
        self.assertEqual(len(first_turn), 4)
        slot1_retry = first_turn[3]
        self.assertIn(_GHOST_B, slot1_retry)
        self.assertIn(_GHOST_A, slot1_retry.split("Also rejected earlier this turn", 1)[1])

    def test_history_resets_on_the_next_turn(self) -> None:
        driver, _ = self._run([["A", "B", "A"], ["B", "end"]])
        self.assertGreaterEqual(len(driver.turns), 2)
        second_turn = driver.turns[1]
        self.assertIsNone(second_turn[0])
        self.assertIn(_GHOST_B, second_turn[1])
        self.assertNotIn(_GHOST_A, second_turn[1])
        self.assertNotIn("Also rejected earlier this turn", second_turn[1])

    def test_scripted_trace_still_replays_deterministically(self) -> None:
        _, transcript = self._run([["A", "dodge", "B", "end"], ["A", "B", "A"]])
        result = replay_match(transcript, rules=self.rules)
        self.assertTrue(result["deterministic_match"], result.get("diffs"))

    def test_helper_passthrough_and_cap(self) -> None:
        self.assertIsNone(_retry_feedback_with_history(None, [], 0, "x"))
        self.assertEqual(_retry_feedback_with_history("latest", [], 0, "x"), "latest")
        violations = [
            {"turn_index": 0, "actor_id": "x", "intent": {"type": "attack", "n": i}, "error": f"err-{i}"}
            for i in range(20)
        ]
        violations.append({"turn_index": 0, "actor_id": "y", "intent": {}, "error": "other-actor"})
        violations.append({"turn_index": 1, "actor_id": "x", "intent": {}, "error": "other-turn"})
        out = _retry_feedback_with_history("latest", violations[:20], 0, "x")
        lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(lines), 8)  # capped; the 20th is the "latest" one and isn't repeated
        self.assertIn("err-18", out)
        self.assertNotIn("err-19", out)
        self.assertNotIn("err-10\n", out)
        out_all = _retry_feedback_with_history("latest", violations, 0, "x")
        self.assertNotIn("other-actor", out_all)
        self.assertNotIn("other-turn", out_all)
        mixed = [
            {"turn_index": 0, "actor_id": "x", "intent": {}, "error": "bad json"},
            {"turn_index": 0, "actor_id": "x", "intent": {"type": "dash"}, "error": "e2"},
        ]
        self.assertIn("(no valid intent): bad json", _retry_feedback_with_history("latest", mixed, 0, "x"))


if __name__ == "__main__":
    unittest.main()
