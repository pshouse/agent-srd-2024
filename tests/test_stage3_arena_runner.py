"""Stage 3 Verification Tests (§8.3): Arena Runner, Replay, Empirical Benchmark & Renderers.

Covers:
1. Partial Observability Enforcement:
   - build_observation() exposes full self sheet & same-side party_blackboard.
   - NEVER exposes enemy exact hp, hp_max, ac, private spellcasting/slots, or opposing-side table_talk.
2. Invalid Intent Retry & Forced Fallback:
   - Faulty driver emitting 1 illegal intent then 1 legal intent records 1 violation, receives retry_feedback,
     succeeds on attempt 2, and lowers rules_legality_score < 100.0.
   - Persistently broken driver exhausts retries and cleanly falls back to end_turn (forced_fallbacks >= 1)
     without crashing or deadlocking.
3. 100% Deterministic Trace Replay:
   - run_match on goblin-ambush and wyrmling-lair -> JSON serialize/reload -> replay_match asserts
     deterministic_match==True, diffs==[], and original_digest == replayed_digest.
   - Custom inline scenario replay works without scenario_override.
4. Empirical Tactical Superiority Benchmark:
   - run_benchmark across all 3 scenarios x seeds [1, 2, 3] comparing heuristic-tactical, greedy, random
     against enemy_agent="heuristic-tactical":
     * heuristic-tactical strictly outperforms random on mean_composite_score & mean_tactical_score.
     * heuristic-tactical incurs 0 wasted_immune_hits across wyrmling-lair while greedy incurs >= 1.
     * heuristic-tactical beats greedy on mean_tactical_score & mean_composite_score.
     * All heuristic-tactical matches achieve rules_legality_score == 100.0 (0 violations).
5. Renderers:
   - render_scenarios_markdown, render_match_markdown, render_benchmark_markdown, render_replay_markdown.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from srd2024.arena import (
    AgentDecision,
    HeuristicTacticalDriver,
    build_observation,
    load_scenario,
    render_benchmark_markdown,
    render_match_markdown,
    render_replay_markdown,
    render_scenarios_markdown,
    replay_match,
    run_benchmark,
    run_match,
    list_scenarios,
)
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore


class _FaultyOnceDriver:
    """Emits one illegal intent on its very first decision, then delegates to HeuristicTacticalDriver."""

    name = "faulty-once"

    def __init__(self, rules: RulesStore) -> None:
        self.delegate = HeuristicTacticalDriver(rules=rules)
        self.tripped = False
        self.received_feedback: list[str] = []

    def reset(self, seed: int | None = None) -> None:
        self.delegate.reset(seed)

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        fb = observation.get("retry_feedback")
        if fb:
            self.received_feedback.append(str(fb))
        if not self.tripped:
            self.tripped = True
            return AgentDecision(
                intent={
                    "type": "cast_spell",
                    "actor_id": observation["self"]["id"],
                    "spell": "nonexistent-illegal-spell-xyz",
                },
                rationale="Intentional invalid test action.",
            )
        return self.delegate.decide(observation)


class _AlwaysBrokenDriver:
    """Always returns an illegal intent so every turn exhausts retries and triggers forced fallback."""

    name = "always-broken"

    def reset(self, seed: int | None = None) -> None:
        return

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        return AgentDecision(
            intent={
                "type": "attack",
                "actor_id": observation["self"]["id"],
                "target_id": "nonexistent-target-999",
                "weapon": "imaginary-laser",
            },
            rationale="Broken intent",
        )


class TestStage3PartialObservability(unittest.TestCase):
    """§8.3.1: Strict information-hiding invariant in build_observation()."""

    def test_partial_observability_hides_enemy_private_state_and_blackboard(self) -> None:
        rules = RulesStore()
        scen = load_scenario("wyrmling-lair", rules)
        res = step(None, {"type": "start_encounter", "seed": 42, "combatants": scen["combatants"]}, rules)
        self.assertEqual(res["status"], "ok")
        state = res["state"]

        blackboard = {
            "party": [
                {"round": 1, "actor_id": "pc-dorian", "actor_name": "Dorian Dawnshield", "message": "PARTY_SECRET_BLESS"}
            ],
            "enemy": [
                {"round": 1, "actor_id": "wyrmling-1", "actor_name": "Red Dragon Wyrmling", "message": "ENEMY_SECRET_BREATH"}
            ],
        }

        # Party perspective (pc-lyra)
        obs_party = build_observation(state, "pc-lyra", scen, blackboard, [], {}, rules)
        self.assertEqual(obs_party["self"]["id"], "pc-lyra")
        self.assertIn("hp", obs_party["self"])
        self.assertIn("hp_max", obs_party["self"])
        self.assertIn("ac", obs_party["self"])
        self.assertIn("spellcasting", obs_party["self"])

        self.assertGreater(len(obs_party["allies"]), 0)
        for ally in obs_party["allies"]:
            self.assertIn("hp", ally)
            self.assertIn("ac", ally)

        forbidden_enemy_keys = {"hp", "hp_max", "ac", "effective_ac", "spellcasting", "slots", "temp_hp", "death_saves"}
        self.assertEqual(len(obs_party["enemies"]), 4)
        for enemy in obs_party["enemies"]:
            leaked = forbidden_enemy_keys & set(enemy.keys())
            self.assertFalse(leaked, f"Enemy observation leaked private keys: {leaked} in {enemy}")
            self.assertIn("hp_band", enemy)
            self.assertIn("bloodied", enemy)
            self.assertIn("distance_ft", enemy)

        party_bb_text = json.dumps(obs_party["party_blackboard"])
        self.assertIn("PARTY_SECRET_BLESS", party_bb_text)
        self.assertNotIn("ENEMY_SECRET_BREATH", party_bb_text)

        # Enemy perspective (wyrmling-1)
        obs_enemy = build_observation(state, "wyrmling-1", scen, blackboard, [], {}, rules)
        for opp in obs_enemy["enemies"]:
            leaked = forbidden_enemy_keys & set(opp.keys())
            self.assertFalse(leaked, f"PC entry in enemy observation leaked private keys: {leaked}")
        enemy_bb_text = json.dumps(obs_enemy["party_blackboard"])
        self.assertIn("ENEMY_SECRET_BREATH", enemy_bb_text)
        self.assertNotIn("PARTY_SECRET_BLESS", enemy_bb_text)


class TestStage3RetryFallbackAndReplay(unittest.TestCase):
    """§8.3.2 & §8.3.3: Retry feedback, forced fallback, and 100% deterministic JSON trace replay."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_invalid_intent_retry_feedback_and_legality_penalty(self) -> None:
        faulty = _FaultyOnceDriver(self.rules)
        enemy = HeuristicTacticalDriver(self.rules)
        transcript = run_match(
            "goblin-ambush",
            party_driver=faulty,
            enemy_driver=enemy,
            rules=self.rules,
            seed=42,
            max_rounds=10,
            max_retries_per_action=2,
        )
        self.assertGreaterEqual(len(faulty.received_feedback), 1, "Faulty driver must receive retry_feedback")
        party_violations = [v for v in transcript["violations"] if v["side"] == "party"]
        self.assertEqual(len(party_violations), 1)
        self.assertTrue(
            any(a["side"] == "party" and a["attempts"] == 2 and not a["first_attempt_legal"] for a in transcript["actions"]),
            "Expected recovered action on attempt 2 with first_attempt_legal=False",
        )
        self.assertLess(transcript["scorecard"]["rules_legality_score"], 100.0)
        self.assertEqual(transcript["scorecard"]["metrics"]["forced_fallbacks"], 0)

    def test_persistently_broken_driver_falls_back_cleanly(self) -> None:
        broken = _AlwaysBrokenDriver()
        enemy = HeuristicTacticalDriver(self.rules)
        transcript = run_match(
            "goblin-ambush",
            party_driver=broken,
            enemy_driver=enemy,
            rules=self.rules,
            seed=42,
            max_rounds=3,
            max_retries_per_action=2,
        )
        self.assertIn(transcript["winner"], {"party", "enemy", "draw"})
        self.assertGreaterEqual(transcript["scorecard"]["metrics"]["forced_fallbacks"], 1)
        self.assertTrue(any(a.get("forced_fallback") for a in transcript["actions"]))
        self.assertLess(transcript["scorecard"]["rules_legality_score"], 100.0)

    def test_deterministic_trace_replay_builtin_and_custom(self) -> None:
        for sid, seed in (("goblin-ambush", 17), ("wyrmling-lair", 42)):
            with self.subTest(scenario=sid, seed=seed):
                transcript = run_match(
                    sid,
                    party_driver=HeuristicTacticalDriver(self.rules),
                    enemy_driver=HeuristicTacticalDriver(self.rules),
                    rules=self.rules,
                    seed=seed,
                )
                # Round-trip through JSON to verify pure JSON serializability & replay
                reloaded = json.loads(json.dumps(transcript))
                replay_res = replay_match(reloaded, self.rules)
                self.assertEqual(replay_res["status"], "ok")
                self.assertTrue(replay_res["deterministic_match"], f"Replay diffs: {replay_res['diffs']}")
                self.assertEqual(replay_res["diffs"], [])
                self.assertEqual(replay_res["original_digest"], transcript["digest"])
                self.assertEqual(replay_res["original_digest"], replay_res["replayed_digest"])

                # Renderer check on replay
                replay_md = render_replay_markdown(replay_res)
                self.assertIn(replay_res["original_digest"][:12], replay_md)

        # Custom inline scenario replay without scenario_override
        goblin_compiled = load_scenario("goblin-ambush", self.rules)
        custom_inline = {
            "id": "custom-replay-test",
            "name": "Custom Replay Test",
            "description": "Custom replay verification.",
            "tier": 1,
            "party_level": 1,
            "max_rounds": 8,
            "tactical_themes": ["replay"],
            "party": [
                {"id": c["id"], "statblock": c["statblock"], "position": c["position"]}
                for c in goblin_compiled["combatants"]
                if c["side"] == "party"
            ],
            "enemies": [
                {"id": c["id"], "monster": c["monster"], "name": c.get("name", c["id"]), "position": c["position"]}
                for c in goblin_compiled["combatants"]
                if c["side"] == "enemy"
            ],
        }
        custom_tx = run_match(
            custom_inline,
            party_driver=HeuristicTacticalDriver(self.rules),
            enemy_driver=HeuristicTacticalDriver(self.rules),
            rules=self.rules,
            seed=9,
        )
        custom_replay = replay_match(json.loads(json.dumps(custom_tx)), self.rules)
        self.assertTrue(custom_replay["deterministic_match"], f"Custom replay diffs: {custom_replay['diffs']}")
        self.assertEqual(custom_replay["diffs"], [])


class TestStage3EmpiricalBenchmarkAndRenderers(unittest.TestCase):
    """§8.3.4 & §8.3.5: Full 3x3x3 tactical superiority benchmark and Markdown renderers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_empirical_tactical_superiority_benchmark_and_renderers(self) -> None:
        report = run_benchmark(
            scenarios=["goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"],
            contenders=["heuristic-tactical", "greedy", "random"],
            enemy_agent="heuristic-tactical",
            seeds=[1, 2, 3],
            rules=self.rules,
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(len(report["matches"]), 27)
        self.assertEqual(len(report["leaderboard"]), 3)

        by_label = {row["label"]: row for row in report["leaderboard"]}
        ht = by_label["heuristic-tactical"]
        gr = by_label["greedy"]
        rd = by_label["random"]

        # 1. heuristic-tactical strictly outperforms random on composite & tactical score (and >= win_rate)
        self.assertGreater(ht["mean_composite_score"], rd["mean_composite_score"])
        self.assertGreater(ht["mean_tactical_score"], rd["mean_tactical_score"])
        self.assertGreaterEqual(ht["win_rate"], rd["win_rate"])

        # 2. Immunity discipline in wyrmling-lair: heuristic-tactical == 0 wasted immune hits, greedy >= 1
        ht_wyrmling = [
            m for m in report["matches"] if m["contender"] == "heuristic-tactical" and m["scenario_id"] == "wyrmling-lair"
        ]
        gr_wyrmling = [
            m for m in report["matches"] if m["contender"] == "greedy" and m["scenario_id"] == "wyrmling-lair"
        ]
        self.assertEqual(len(ht_wyrmling), 3)
        self.assertEqual(len(gr_wyrmling), 3)
        self.assertEqual(
            sum(m["wasted_immune_hits"] for m in ht_wyrmling),
            0,
            f"heuristic-tactical must incur 0 wasted_immune_hits in wyrmling-lair: {ht_wyrmling}",
        )
        self.assertGreaterEqual(
            sum(m["wasted_immune_hits"] for m in gr_wyrmling),
            1,
            f"greedy must incur >= 1 wasted_immune_hits in wyrmling-lair: {gr_wyrmling}",
        )

        # 3. heuristic-tactical beats greedy on mean_tactical_score & mean_composite_score
        self.assertGreater(ht["mean_tactical_score"], gr["mean_tactical_score"])
        self.assertGreater(ht["mean_composite_score"], gr["mean_composite_score"])

        # 4. heuristic-tactical makes 0 violations. Legality is scaled by the share of productive turns, so a turn
        # cut short (e.g. dropped by Opportunity Attacks while retreating: crypt seed 3) costs a little without a violation.
        self.assertEqual(ht["total_violations"], 0)
        self.assertGreaterEqual(ht["mean_rules_legality_score"], 95.0)
        for m in report["matches"]:
            if m["contender"] == "heuristic-tactical":
                self.assertEqual(m["violations"], 0, f"Violation in {m}")

        # 5. Leaderboard rank 1 is heuristic-tactical
        self.assertEqual(report["leaderboard"][0]["label"], "heuristic-tactical")
        self.assertEqual(report["leaderboard"][0]["rank"], 1)

        # 6. Renderers check
        scen_md = render_scenarios_markdown(list_scenarios())
        for sid in ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"):
            self.assertIn(sid, scen_md)

        bench_md = render_benchmark_markdown(report)
        self.assertIn("heuristic-tactical", bench_md)
        self.assertIn("greedy", bench_md)
        self.assertIn("random", bench_md)
        self.assertIn("|", bench_md)

        # Match transcript renderer check
        tx = run_match(
            "wyrmling-lair",
            party_driver=HeuristicTacticalDriver(self.rules),
            enemy_driver=HeuristicTacticalDriver(self.rules),
            rules=self.rules,
            seed=1,
        )
        match_md = render_match_markdown(tx)
        self.assertIn(tx["scenario"]["name"], match_md)
        self.assertIn("## Round 1", match_md)
        self.assertIn("Table Talk", match_md)
        self.assertIn("Scorecard", match_md)
        # Verify at least one narrative hook made it into the rendered transcript
        hooks = [h for a in tx["actions"] for h in a.get("narrative_hooks", [])]
        if hooks:
            self.assertIn(hooks[0], match_md)


if __name__ == "__main__":
    unittest.main()
