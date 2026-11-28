"""Bench spend cap and graceful handling of providers that run out of credits or keep failing.

Before this, an out-of-credits provider made every decision fail. Each failure was recorded as a rules violation
with a forced end_turn, and the bench kept "playing" that contender for hours.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock

from srd2024.arena import agents, render_benchmark_markdown, run_benchmark
from srd2024.arena.agents import (
    OpenAIDriver,
    ProviderUnavailable,
    RetryableLLMError,
    _classify_http_error,
    create_driver,
)
from srd2024.arena.budget import BudgetLedger, contender_price, match_cost, parse_price
from srd2024.engine.store import RulesStore
from srd2024.runtime.arena_bridge import make_bench_progress

_END = json.dumps({"intent": {"type": "end_turn", "actor_id": "ACTOR"}, "rationale": "r", "table_talk": None})


def _chat(actor: str, prompt_tokens: int = 100, completion_tokens: int = 10) -> dict[str, Any]:
    return {"choices": [{"message": {"content": _END.replace("ACTOR", actor)}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}


class _Transport:
    """Ends every turn; raises `fail` once `fail_after` calls have succeeded."""

    def __init__(self, *, fail_after: int | None = None, fail: Exception | None = None, prompt_tokens: int = 100) -> None:
        self.calls = 0
        self.fail_after, self.fail, self.prompt_tokens = fail_after, fail, prompt_tokens

    def __call__(self, url, headers, payload, timeout):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise self.fail
        user = payload["messages"][-1]["content"]
        actor = json.loads(user.split("OBSERVATION:\n", 1)[1].split("\n\nRespond", 1)[0])["self"]["id"]
        return _chat(actor, prompt_tokens=self.prompt_tokens)


class TestErrorClassification(unittest.TestCase):
    def test_http_errors_map_to_the_right_kind(self) -> None:
        cases = [
            (402, b"", ProviderUnavailable, "out_of_credits"),
            (429, b'{"error":{"code":"insufficient_quota","message":"You exceeded your current quota"}}', ProviderUnavailable, "out_of_credits"),
            (403, b'{"error":"Your team has run out of credits"}', ProviderUnavailable, "out_of_credits"),
            (429, b'{"error":{"status":"RESOURCE_EXHAUSTED"}}', ProviderUnavailable, "out_of_credits"),
            (401, b"bad key", ProviderUnavailable, "unavailable"),
            (429, b'{"error":"rate_limit"}', RetryableLLMError, None),
            (503, b"", RetryableLLMError, None),
            (529, b"overloaded", RetryableLLMError, None),
        ]
        for code, body, kind, reason in cases:
            with self.subTest(code=code, body=body):
                err = _classify_http_error(code, body, "7")
                self.assertIsInstance(err, kind)
                if reason:
                    self.assertEqual(err.reason, reason)
                if body:
                    self.assertNotIn(body.decode(), str(err))
        self.assertEqual(_classify_http_error(429, b"slow down", "7").retry_after_s, 7.0)
        self.assertNotIsInstance(_classify_http_error(400, b"bad request", None), (ProviderUnavailable, RetryableLLMError))

    def test_real_transport_raises_provider_unavailable_without_leaking_the_body(self) -> None:
        hdrs = Message()
        body = io.BytesIO(b'{"error":{"code":"insufficient_quota","account":"acct-SECRET"}}')

        class _Opener:
            def open(self, req, timeout):
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", hdrs, body)

        with mock.patch.object(agents, "_LLM_OPENER", _Opener()):
            with self.assertRaises(ProviderUnavailable) as ctx:
                agents._default_http_transport("https://api.example.test/v1/chat", {}, {}, 5)
        self.assertEqual(ctx.exception.reason, "out_of_credits")
        self.assertNotIn("acct-SECRET", str(ctx.exception))


class TestRetriesAndBreaker(unittest.TestCase):
    def _driver(self, transport) -> OpenAIDriver:
        d = create_driver("openai:gpt-6.2-astra", api_key="k", http_transport=transport)
        d.sleeps = []
        d._sleep = d.sleeps.append
        return d

    def test_transient_errors_are_retried_then_succeed(self) -> None:
        seq = [RetryableLLMError("LLM HTTP 503"), RetryableLLMError("LLM HTTP 429", retry_after_s=20)]

        def transport(url, headers, payload, timeout):
            if seq:
                raise seq.pop(0)
            return _chat("pc-bram")

        d = self._driver(transport)
        self.assertEqual(d._send("https://x.test", {}, {}), _chat("pc-bram"))
        self.assertEqual(d.sleeps, [2.0, 20.0])  # backoff, then the server's Retry-After

    def test_persistent_failure_trips_the_breaker(self) -> None:
        def transport(*_a):
            raise RetryableLLMError("LLM request timed out.")

        d = self._driver(transport)
        for _ in range(d.MAX_CONSECUTIVE_FAILURES - 1):
            with self.assertRaises(RuntimeError) as ctx:
                d._send("https://x.test", {}, {})
            self.assertNotIsInstance(ctx.exception, ProviderUnavailable)
        with self.assertRaises(ProviderUnavailable) as ctx:
            d._send("https://x.test", {}, {})
        self.assertEqual(ctx.exception.reason, "unavailable")

    def test_success_resets_the_breaker(self) -> None:
        state = {"fail": True}

        def transport(*_a):
            if state["fail"]:
                raise RetryableLLMError("LLM HTTP 503")
            return _chat("pc-bram")

        d = self._driver(transport)
        for _ in range(d.MAX_CONSECUTIVE_FAILURES - 1):
            with self.assertRaises(RuntimeError):
                d._send("https://x.test", {}, {})
        state["fail"] = False
        d._send("https://x.test", {}, {})
        self.assertEqual(d._consecutive_failures, 0)

    def test_out_of_credits_is_not_retried(self) -> None:
        calls = []

        def transport(*_a):
            calls.append(1)
            raise ProviderUnavailable("out_of_credits", "LLM provider reports no remaining credits or quota (HTTP 402).")

        d = self._driver(transport)
        with self.assertRaises(ProviderUnavailable):
            d._send("https://x.test", {}, {})
        self.assertEqual((len(calls), d.sleeps), (1, []))


class TestBenchStopsGracefully(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_out_of_credits_mid_bench_keeps_finished_matches_and_other_contenders(self) -> None:
        transport = _Transport(fail_after=6, fail=ProviderUnavailable("out_of_credits", "no credits (HTTP 402)."))
        paid = {"driver": "openai:gpt-6.2-astra", "api_key": "k", "http_transport": transport}
        seen, aborts = [], []
        rep = run_benchmark(scenarios=["goblin-ambush"], contenders=[paid, "heuristic-tactical"], seeds=[1, 2, 3, 4],
                            rules=self.rules, on_match=lambda m, d, t: seen.append(m), on_abort=aborts.append)
        stopped = rep["aborted_contenders"]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["reason"], "out_of_credits")
        done = stopped[0]["matches_completed"]
        self.assertLess(done, 4)
        self.assertEqual(aborts, stopped)
        rows = {r["label"]: r for r in rep["leaderboard"]}
        self.assertEqual(rows["heuristic-tactical"]["matches"], 4)
        self.assertEqual(rows[stopped[0]["label"]]["aborted"], "out_of_credits")
        self.assertEqual(rows[stopped[0]["label"]]["matches"], done)
        # No violations from the outage: the unfinished match is dropped, not scored.
        paid_matches = [m for m in rep["matches"] if m["contender"] == stopped[0]["label"]]
        self.assertEqual(len(paid_matches), done)
        self.assertTrue(all(m["violations"] == 0 for m in paid_matches))
        self.assertIn("Stopped Contenders", render_benchmark_markdown(rep))

    def test_budget_cap_stops_new_paid_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            budget = BudgetLedger(5.0, ledger, reserve_usd=1.0)
            # gpt-6.2-astra: $8 per million input tokens; 100k prompt tokens a call = $0.80 a decision.
            paid = {"driver": "openai:gpt-6.2-astra", "api_key": "k", "http_transport": _Transport(prompt_tokens=100_000)}
            rep = run_benchmark(scenarios=["goblin-ambush"], contenders=[paid, "random"], seeds=list(range(1, 9)),
                                rules=self.rules, budget=budget)
            stopped = rep["aborted_contenders"]
            self.assertEqual([s["reason"] for s in stopped], ["budget_cap"])
            costs = [m["cost_usd"] for m in rep["matches"] if m["contender"] == stopped[0]["label"]]
            self.assertTrue(costs and all(c > 0 for c in costs))
            running = 0.0
            for c in costs:  # every paid match started below cap - reserve
                self.assertLess(running + budget.reserve_usd, budget.cap_usd)
                running += c
            self.assertAlmostEqual(budget.spent(), round(sum(costs), 6), places=5)
            self.assertEqual(len([m for m in rep["matches"] if m["contender"] == "random"]), 8)
            self.assertTrue(all(m["cost_usd"] == 0 for m in rep["matches"] if m["contender"] == "random"))
            self.assertIn("Budget:", render_benchmark_markdown(rep))

    def test_missing_api_key_stops_the_contender_instead_of_scoring_violations(self) -> None:
        with mock.patch.dict("os.environ", {"XAI_API_KEY": ""}):
            rep = run_benchmark(scenarios=["goblin-ambush"], contenders=["xai:grok-4.7", "random"], seeds=[1, 2],
                                rules=self.rules)
        self.assertEqual([(a["label"], a["reason"], a["matches_completed"]) for a in rep["aborted_contenders"]],
                         [("xai:grok-4.7", "unavailable", 0)])
        self.assertEqual(len(rep["matches"]), 2)  # random's two; nothing scored for grok

    def test_unpriced_paid_model_is_refused_up_front(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            budget = BudgetLedger(5.0, Path(tmp) / "l.jsonl", reserve_usd=1.0)
            with self.assertRaises(ValueError):
                run_benchmark(scenarios=["goblin-ambush"], contenders=["xai:grok-9"], seeds=[1], rules=self.rules, budget=budget)


class TestBudgetPieces(unittest.TestCase):
    def test_prices_and_costs(self) -> None:
        self.assertEqual(contender_price("heuristic-tactical"), (0.0, 0.0))
        self.assertEqual(contender_price("anthropic:claude-opus-5-5"), (0.0, 0.0))  # Claude Max login
        self.assertEqual(contender_price("xai:grok-4.7"), (2.0, 6.0))
        self.assertIsNone(contender_price("xai:grok-9"))
        self.assertEqual(contender_price("xai:grok-9", {"grok-9": (1.0, 2.0)}), (1.0, 2.0))
        self.assertEqual(parse_price("gemini-4-neon=3/15"), ("gemini-4-neon", (3.0, 15.0)))
        t = {"actions": [
            {"side": "party", "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 10, "billable_output_tokens": 100_000}},
            {"side": "enemy", "usage": {"prompt_tokens": 5_000_000}},
            {"side": "party", "usage": {"prompt_tokens": 0, "completion_tokens": 100_000}},
        ]}
        self.assertAlmostEqual(match_cost(t, (2.0, 6.0)), 2.0 + 0.6 + 0.6)

    def test_ledger_is_shared_between_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.jsonl"
            a, b = BudgetLedger(10, path, reserve_usd=1), BudgetLedger(10, path, reserve_usd=1)
            a.record({"cost_usd": 4.5})
            self.assertTrue(b.can_start_paid_match())  # 4.5 + 1 reserve < 10
            b.record({"cost_usd": 4.6})
            self.assertAlmostEqual(a.spent(), 9.1)
            self.assertFalse(b.can_start_paid_match())  # 9.1 + 1 reserve >= 10

    def test_progress_line_shows_spend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            budget = BudgetLedger(150, Path(tmp) / "l.jsonl")
            budget.record({"cost_usd": 12.345})
            out = io.StringIO()
            cb = make_bench_progress(stream=out, budget=budget, clock=iter([0.0, 5.0]).__next__)
            cb({"contender": "c", "scenario_id": "s", "seed": 1, "winner": "enemy", "rounds": 1,
                "composite_score": 1.0, "violations": 0}, 1, 2)
            self.assertIn("spent $12.35 of $150.00", out.getvalue())


if __name__ == "__main__":
    unittest.main()
