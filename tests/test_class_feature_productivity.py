"""Legal `class_feature` turns count as productive (CTO follow-up to #102).

Before the fix `_PRODUCTIVE_INTENT_TYPES` left out `class_feature`, so a turn spent only on Preserve Life, Second
Wind or Lay on Hands counted as idle. That lowered `contribution`, which scales the legality score and the
tactical and coordination terms, even when the turn had 0 violations. Class features now follow the same "did it
change anything" rule as `cast_spell` (#75): healing that raised HP or revived someone, or a condition removed,
earns credit. Healing at full HP and a bare Action Surge (which only grants an action) don't.
"""

from __future__ import annotations

import unittest
from typing import Any

from srd2024.arena import HeuristicTacticalDriver, run_match
from srd2024.arena.scoring import score_match
from srd2024.engine.store import RulesStore


def _transcript(cf_intent: dict[str, Any], cf_events: list[dict[str, Any]], *, state_diff: Any = None) -> dict[str, Any]:
    """pc-a spends turn 0 on one class feature only; pc-b attacks on turn 1."""
    combatants = {
        "pc-a": {"id": "pc-a", "side": "party", "hp": 10, "hp_max": 20, "alive": True},
        "pc-b": {"id": "pc-b", "side": "party", "hp": 5, "hp_max": 20, "alive": True},
        "gob": {"id": "gob", "side": "enemy", "hp": 7, "hp_max": 7, "alive": True},
    }

    def act(seq: int, actor: str, turn: int, intent: dict[str, Any], events: list[dict[str, Any]], diff: Any = None):
        return {
            "seq": seq, "round": 1, "turn_index": turn, "actor_id": actor, "side": "party",
            "first_attempt_legal": True, "intent": intent, "events": events,
            "state_diff": {} if diff is None else diff,
        }

    return {
        "winner": "draw",
        "rounds": 1,
        "config": {"max_rounds": 5},
        "initial_state": {"combatants": combatants},
        "final_state": {"combatants": combatants},
        "actions": [
            act(1, "pc-a", 0, cf_intent, cf_events, state_diff),
            act(2, "pc-a", 0, {"type": "end_turn", "actor_id": "pc-a"}, []),
            act(3, "pc-b", 1, {"type": "attack", "actor_id": "pc-b", "target_id": "gob", "weapon": "longsword"},
                [{"type": "attack_roll", "actor_id": "pc-b", "target_id": "gob", "hit": False}]),
            act(4, "pc-b", 1, {"type": "end_turn", "actor_id": "pc-b"}, []),
        ],
    }


def _heal(target: str, before: int, after: int, feature: str, *, revived: bool = False) -> dict[str, Any]:
    return {"type": "healing_applied", "source_id": "pc-a", "target_id": target, "feature": feature,
            "amount": max(1, after - before), "hp_before": before, "hp_after": after, "revived": revived}


def _cf(feature: str, **extra: Any) -> dict[str, Any]:
    return {"type": "class_feature", "actor_id": "pc-a", "feature": feature, **extra}


class TestClassFeatureProductivity(unittest.TestCase):
    def _metrics(self, intent, events, **kw) -> tuple[dict[str, Any], float]:
        card = score_match(_transcript(intent, events, **kw))
        return card["metrics"], card["rules_legality_score"]

    def test_second_wind_that_heals_is_productive(self) -> None:
        m, legality = self._metrics(_cf("second_wind"), [
            {"type": "class_feature_used", "actor_id": "pc-a", "feature": "second_wind"},
            _heal("pc-a", 10, 17, "second_wind"),
        ])
        self.assertEqual((m["active_turns"], m["productive_turns"]), (2, 2))
        self.assertEqual(legality, 100.0, "0 violations and every turn productive")

    def test_second_wind_at_full_hp_is_not_productive(self) -> None:
        m, legality = self._metrics(_cf("second_wind"), [
            {"type": "class_feature_used", "actor_id": "pc-a", "feature": "second_wind"},
            _heal("pc-a", 20, 20, "second_wind"),
        ])
        self.assertEqual(m["productive_turns"], 1)
        self.assertLess(legality, 100.0)

    def test_lay_on_hands_heal_and_revive_are_productive(self) -> None:
        for ev in (_heal("pc-b", 5, 10, "lay_on_hands"), _heal("pc-b", 0, 3, "lay_on_hands", revived=True)):
            with self.subTest(hp_before=ev["hp_before"]):
                m, _ = self._metrics(_cf("lay_on_hands", target_id="pc-b"), [
                    {"type": "class_feature_used", "actor_id": "pc-a", "feature": "lay_on_hands", "target_id": "pc-b"},
                    ev,
                ])
                self.assertEqual(m["productive_turns"], 2)

    def test_lay_on_hands_cure_poison_is_productive(self) -> None:
        m, _ = self._metrics(_cf("lay_on_hands", target_id="pc-b", mode="cure_poison"), [
            {"type": "class_feature_used", "actor_id": "pc-a", "feature": "lay_on_hands", "mode": "cure_poison"},
            {"type": "condition_ended", "target_id": "pc-b", "condition": "poisoned", "reason": "lay_on_hands"},
        ])
        self.assertEqual(m["productive_turns"], 2)

    def test_preserve_life_is_productive(self) -> None:
        m, legality = self._metrics(_cf("preserve_life", target_ids=["pc-b"]), [
            {"type": "class_feature_used", "actor_id": "pc-a", "feature": "preserve_life", "total_distributed": 5},
            _heal("pc-b", 5, 10, "preserve_life"),
        ])
        self.assertEqual(m["productive_turns"], 2)
        self.assertEqual(legality, 100.0)

    def test_healing_an_enemy_earns_nothing(self) -> None:
        m, _ = self._metrics(_cf("lay_on_hands", target_id="gob"), [_heal("gob", 3, 7, "lay_on_hands")])
        self.assertEqual(m["productive_turns"], 1)

    def test_bare_action_surge_is_not_productive(self) -> None:
        m, _ = self._metrics(_cf("action_surge"), [
            {"type": "class_feature_used", "actor_id": "pc-a", "feature": "action_surge", "uses_left": 0},
        ])
        self.assertEqual(m["productive_turns"], 1, "Action Surge counts only through the action it buys")

    def test_synthetic_class_feature_without_events_keeps_credit(self) -> None:
        tx = _transcript(_cf("second_wind"), [])
        tx["actions"][0].pop("state_diff")
        self.assertEqual(score_match(tx)["metrics"]["productive_turns"], 2)


class TestRealMatchClassFeatureTurns(unittest.TestCase):
    """End to end on the real engine: ruined-keep seed 2, where the Life cleric spends a turn on Preserve Life alone."""

    def test_preserve_life_turn_counts_and_legality_is_perfect(self) -> None:
        rules = RulesStore()
        t = run_match("ruined-keep", party_driver=HeuristicTacticalDriver(rules),
                      enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=2)
        card = t["scorecard"]
        party_violations = [v for v in t["violations"] if v.get("side") == "party"]
        self.assertEqual(party_violations, [])
        cf_turns = {(a["round"], a["turn_index"]) for a in t["actions"]
                    if a["side"] == "party" and (a.get("intent") or {}).get("feature") == "preserve_life"}
        self.assertTrue(cf_turns, "fixture drifted: no Preserve Life turn on ruined-keep seed 2")
        self.assertEqual(card["metrics"]["productive_turns"], card["metrics"]["active_turns"])
        self.assertEqual(card["rules_legality_score"], 100.0)  # was 75.0 before the fix


if __name__ == "__main__":
    unittest.main()
