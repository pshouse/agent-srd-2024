"""TypeSafe AI Jev Arena driver (`typesafe:<model>`): request shape, ranking, retries, secrets, candidate legality."""

from __future__ import annotations

import json
import os
import unittest
import zlib
from typing import Any
from unittest import mock

from srd2024.arena import HeuristicTacticalDriver, build_observation, load_scenario, replay_match, run_match
from srd2024.arena.agents import create_driver
from srd2024.arena.jev import QUESTION_ID, JevDriver, enumerate_candidates
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

KEY = "ts-test-key-123"


class _FakeJev:
    """Stands in for POST /v1/systemone. Prefers attacks and spells, with a stable hash tie-break."""

    def __init__(self, prefer: str | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.prefer = prefer

    def __call__(self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.calls.append((url, headers, payload))
        names = list(payload["questions"][QUESTION_ID]["criteria"])

        def w(n: str) -> float:
            if self.prefer and n == self.prefer:
                return 1000.0
            base = 5 if n.startswith(("attack", "off-hand", "cast")) else 2 if n.startswith("move") else 0.2 if n == "end turn" else 1
            return base + (zlib.crc32(n.encode()) % 100) / 100

        tot = sum(w(n) for n in names)
        probs = {n: w(n) / tot for n in names}
        return {
            "model": "jev-1.14.0",
            "answers": {QUESTION_ID: {"type": "choice", "choice": max(probs, key=probs.get), "confidence": 0.5, "probabilities": probs}},
            "usage": {"input_tokens": 1234, "output_tokens": 0},
        }


def _obs(rules: RulesStore, scenario: str, actor: str, seed: int = 8, mutate=None) -> dict[str, Any]:
    sc = load_scenario(scenario, rules)
    st = step(None, {"type": "start_encounter", "seed": seed, "combatants": sc["combatants"]}, rules)["state"]
    st["active_index"] = st["turn_order"].index(actor)
    if mutate:
        mutate(st)
    return build_observation(st, actor, sc, {}, [], {}, rules)


class TestJevDriver(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def _driver(self, fake: _FakeJev) -> JevDriver:
        d = create_driver("typesafe:jev-1.14.0", rules=self.rules, api_key=KEY, http_transport=fake)
        self.assertIsInstance(d, JevDriver)
        return d

    def test_spec_and_request_shape(self) -> None:
        fake = _FakeJev()
        d = self._driver(fake)
        self.assertEqual(d.name, "typesafe:jev-1.14.0")
        obs = _obs(self.rules, "wyrmling-lair", "pc-lyra")
        obs["retry_feedback"] = None
        decision = d.decide(obs)
        url, headers, payload = fake.calls[0]
        self.assertEqual(url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
        self.assertEqual(payload["model"], "jev-1.14.0")
        q = payload["questions"][QUESTION_ID]
        self.assertEqual(q["type"], "choice")
        self.assertIn("end turn", q["criteria"])
        self.assertLessEqual(len(q["criteria"]), 80)
        self.assertNotIn(KEY, json.dumps(payload))
        state = json.loads(payload["state"])
        self.assertNotIn("retry_feedback", state)
        self.assertEqual(state["self"]["id"], "pc-lyra")
        self.assertEqual(decision.usage["prompt_tokens"], 1234)
        self.assertIsNone(decision.table_talk)
        self.assertIn("Jev:", decision.rationale)

    def test_plays_top_option_and_retries_without_a_new_call(self) -> None:
        fake = _FakeJev(prefer="dodge")
        d = self._driver(fake)
        obs = _obs(self.rules, "wyrmling-lair", "pc-kaelen")
        first = d.decide(obs)
        self.assertEqual(first.intent["type"], "dodge")
        obs["retry_feedback"] = "Previous intent was rejected."
        second = d.decide(obs)
        self.assertNotEqual(second.intent, first.intent)
        self.assertEqual(len(fake.calls), 1, "a retry must reuse the ranking, not call Jev again")
        self.assertIn("Retry", second.rationale)

    def test_monster_seat_uses_heuristic_without_calls(self) -> None:
        fake = _FakeJev()
        d = self._driver(fake)
        d.decide(_obs(self.rules, "wyrmling-lair", "wyrmling-1"))
        self.assertEqual(fake.calls, [])

    def test_missing_key_and_error_scrubbing(self) -> None:
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            d = create_driver("typesafe:jev-1.14.0", rules=self.rules)
            with self.assertRaises(RuntimeError) as ctx:
                d.decide(_obs(self.rules, "wyrmling-lair", "pc-lyra"))
            self.assertIn("TYPESAFE_API_KEY", str(ctx.exception))

        def boom(url, headers, payload, timeout):
            raise RuntimeError(f"connection reset; auth={headers['Authorization']}")

        d = create_driver("typesafe:jev-1.14.0", rules=self.rules, api_key=KEY, http_transport=boom)
        with self.assertRaises(RuntimeError) as ctx:
            d.decide(_obs(self.rules, "wyrmling-lair", "pc-lyra"))
        self.assertNotIn(KEY, str(ctx.exception))

    def test_malformed_response_raises(self) -> None:
        d = create_driver(
            "typesafe:jev-1.14.0", rules=self.rules, api_key=KEY, http_transport=lambda *a: {"answers": {}}
        )
        with self.assertRaises(ValueError):
            d.decide(_obs(self.rules, "wyrmling-lair", "pc-lyra"))

    def test_candidate_filters(self) -> None:
        def after_nick(st: dict[str, Any]) -> None:
            res = st["combatants"]["pc-kaelen"]["turn_resources"]
            res.update({"action": False, "light_attack_made": True, "nick_used": True})

        names = [n for n, _, _ in enumerate_candidates(_obs(self.rules, "wyrmling-lair", "pc-kaelen", mutate=after_nick), self.rules)]
        self.assertFalse(any(n.startswith("off-hand") for n in names), names)

        def light_made(st: dict[str, Any]) -> None:
            st["combatants"]["pc-kaelen"]["turn_resources"].update({"light_attack_made": True})
            st["combatants"]["pc-kaelen"]["position"] = {"x": 25, "y": 5}

        names = [n for n, _, _ in enumerate_candidates(_obs(self.rules, "wyrmling-lair", "pc-kaelen", mutate=light_made), self.rules)]
        self.assertTrue(any(n.startswith("off-hand") for n in names), names)

        # Hold Person needs a Humanoid; every crypt enemy is Undead.
        names = [n for n, _, _ in enumerate_candidates(_obs(self.rules, "crypt-of-ghouls", "pc-dorian", seed=1), self.rules)]
        self.assertFalse(any("hold-person" in n for n in names), names)

        def frightened(st: dict[str, Any]) -> None:
            st["combatants"]["pc-aldric"]["conditions"].append({"condition": "frightened", "source_id": "mummy-1"})

        obs = _obs(self.rules, "crypt-of-ghouls", "pc-aldric", seed=1, mutate=frightened)
        pos = obs["self"]["position"]
        from srd2024.engine.combat import distance_ft

        for name, _, intent in enumerate_candidates(obs, self.rules):
            if intent["type"] == "move":
                for e in obs["enemies"]:
                    self.assertGreaterEqual(distance_ft(intent["to"], e["position"]), distance_ft(pos, e["position"]), name)

    def test_full_matches_are_legal_and_replay(self) -> None:
        for scenario in ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"):
            for seed in (1, 2):
                d = self._driver(_FakeJev())
                t = run_match(scenario, party_driver=d, enemy_driver=HeuristicTacticalDriver(self.rules), rules=self.rules, seed=seed)
                self.assertEqual(t["violations"], [], f"{scenario} seed {seed}")
                self.assertEqual(t["config"]["party_driver"], "typesafe:jev-1.14.0")
                self.assertTrue(replay_match(json.loads(json.dumps(t)), self.rules)["deterministic_match"])

    def test_mcp_treats_jev_as_a_paid_driver(self) -> None:
        from srd2024.runtime.mcp import _check_mcp_agent_spec

        with self.assertRaises(ValueError):
            _check_mcp_agent_spec("typesafe:jev-1.14.0", allow_llm=False, rules=self.rules)
        _check_mcp_agent_spec("typesafe:jev-1.14.0", allow_llm=True, rules=self.rules)


if __name__ == "__main__":
    unittest.main()
