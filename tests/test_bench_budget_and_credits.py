"""Bench spend cap and graceful handling of providers that run out of credits or keep failing.

Before this, an out-of-credits provider made every decision fail. Each failure was recorded as a rules violation
with a forced end_turn, and the bench kept "playing" that contender for hours.
"""

from __future__ import annotations

import io
import json
import multiprocessing
import tempfile
import time
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
from srd2024.arena.budget import (
    BudgetLedger,
    LedgerError,
    MatchBudgetExceeded,
    MatchMeter,
    contender_price,
    match_cost,
    parse_price,
)
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
        self.assertEqual(d._send("https://x.test", {}, {})[0], _chat("pc-bram"))
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

    def test_missing_api_key_stops_the_contender_instead_of_scoring_violations(self) -> None:
        with mock.patch.dict("os.environ", {"XAI_API_KEY": ""}):
            rep = run_benchmark(scenarios=["goblin-ambush"], contenders=["xai:grok-4.7", "random"], seeds=[1, 2],
                                rules=self.rules)
        self.assertEqual([(a["label"], a["reason"], a["matches_completed"]) for a in rep["aborted_contenders"]],
                         [("xai:grok-4.7", "unavailable", 0)])
        self.assertEqual(len(rep["matches"]), 2)  # random's two; nothing scored for grok

    def test_unpriced_paid_model_is_refused_up_front(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            budget = BudgetLedger.init(5.0, Path(tmp) / "l.jsonl", match_limit_usd=1.0)
            with self.assertRaises(ValueError):
                run_benchmark(scenarios=["goblin-ambush"], contenders=["xai:grok-9"], seeds=[1], rules=self.rules, budget=budget)



def _settled_ledger(path: Path, cap: float, settled: float, limit: float = 5.0) -> BudgetLedger:
    """A ledger that already has `settled` dollars of finished matches."""
    led = BudgetLedger.init(cap, path, match_limit_usd=limit)
    remaining = settled
    while remaining > 1e-9:
        chunk = min(limit, remaining)
        rid = led.reserve({"contender": "seed"})
        led.settle(rid, chunk, {"contender": "seed"})
        remaining -= chunk
    return led


def _race_worker(path: str, cap: float, limit: float, out: Any) -> None:
    led = BudgetLedger(cap, path, match_limit_usd=limit)
    rid = led.reserve({"contender": "racer"})
    if rid is not None:
        time.sleep(0.2)  # "play" the match while the others try to start
        led.settle(rid, limit, {"contender": "racer"})  # worst case: spend the whole reservation
    out.put(rid is not None)


class TestLedgerReservations(unittest.TestCase):
    def test_concurrent_starts_cannot_overrun_the_cap(self) -> None:
        """CTO repro: cap $150 with $139 spent, 8 processes start at once. Only 2 may start (139 + 5 + 5 <= 150)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            led = _settled_ledger(path, 150.0, 139.0)
            ctx = multiprocessing.get_context("fork")
            q = ctx.Queue()
            procs = [ctx.Process(target=_race_worker, args=(str(path), 150.0, 5.0, q)) for _ in range(8)]
            for pr in procs:
                pr.start()
            for pr in procs:
                pr.join(timeout=30)
            started = sum(q.get(timeout=5) for _ in procs)
            self.assertEqual(started, 2)
            self.assertLessEqual(led.spent(), 150.0)
            self.assertAlmostEqual(led.spent(), 149.0)
            self.assertAlmostEqual(led.committed(), 149.0)

    def test_open_reservations_count_until_settled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            led = BudgetLedger.init(12.0, Path(tmp) / "l.jsonl", match_limit_usd=5.0)
            a, b = led.reserve({}), led.reserve({})
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
            self.assertIsNone(led.reserve({}), "5 + 5 reserved; a third 5 would pass 12")
            self.assertEqual((led.spent(), led.committed()), (0.0, 10.0))
            led.settle(a, 0.75, {})
            self.assertEqual((led.spent(), led.committed()), (0.75, 5.75))
            self.assertIsNotNone(led.reserve({}))  # 0.75 + 5 + 5 <= 12
            # A crashed process never settles: its reservation stays counted at full value.
            self.assertEqual(led.committed(), 10.75)


class TestLedgerFailsClosed(unittest.TestCase):
    def _ledger(self, tmp: str) -> tuple[BudgetLedger, Path]:
        path = Path(tmp) / "l.jsonl"
        led = BudgetLedger.init(20.0, path, match_limit_usd=5.0)
        led.settle(led.reserve({"contender": "x"}), 1.0, {})
        return led, path

    def test_missing_ledger_is_refused_and_never_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LedgerError):
                BudgetLedger(20.0, Path(tmp) / "nope.jsonl")  # not created implicitly
            led, path = self._ledger(tmp)
            path.unlink()
            with self.assertRaises(LedgerError):
                led.reserve({})
            self.assertFalse(path.exists())
            with self.assertRaises(LedgerError):
                BudgetLedger.init(20.0, self._ledger(tmp)[1])  # init refuses to overwrite

    def test_corrupt_truncated_negative_and_wrong_cap_are_refused(self) -> None:
        bad_tails = {
            "corrupt": "{not json\n",
            "truncated": '{"type": "reserve", "id": "z", "amount_usd": 5.0}',
            "negative": '{"type": "settle", "id": "q", "amount_usd": -1.0}\n',
            "missing amount": '{"type": "reserve", "id": "m"}\n',
            "settle unknown id": '{"type": "settle", "id": "never-reserved", "amount_usd": 1.0}\n',
        }
        for name, tail in bad_tails.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                led, path = self._ledger(tmp)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(tail)
                for op in (led.spent, led.committed, lambda: led.reserve({})):
                    with self.assertRaises(LedgerError):
                        op()
        with tempfile.TemporaryDirectory() as tmp:
            _, path = self._ledger(tmp)
            with self.assertRaises(LedgerError):
                BudgetLedger(999.0, path)  # created for a $20 cap
            with self.assertRaises(LedgerError):
                BudgetLedger(20.0, path).settle("x", -0.5, {})

    def test_corrupt_ledger_stops_paid_contenders_but_not_free_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            led, path = self._ledger(tmp)
            with path.open("a", encoding="utf-8") as fh:
                fh.write("garbage\n")
            paid = {"driver": "openai:gpt-6.2-astra", "api_key": "k", "http_transport": _Transport()}
            rep = run_benchmark(scenarios=["goblin-ambush"], contenders=[paid, "random"], seeds=[1, 2],
                                rules=RulesStore(), budget=led)
            self.assertEqual([a["reason"] for a in rep["aborted_contenders"]], ["ledger_error"])
            self.assertEqual(len([m for m in rep["matches"] if m["contender"] == "random"]), 2)
            self.assertIn("ledger_error", rep["budget"])


class TestMeterFailsClosed(unittest.TestCase):
    def test_missing_or_negative_usage_is_charged_the_worst_case(self) -> None:
        m = MatchMeter((8.0, 40.0), 5.0)
        for usage in (None, {}, {"prompt_tokens": 10}, {"prompt_tokens": -5, "completion_tokens": 3},
                      {"prompt_tokens": "10", "completion_tokens": 3}, {"prompt_tokens": True, "completion_tokens": 3}):
            with self.subTest(usage=usage):
                before = m.spent_usd
                m.charge_usage(usage, 0.25)
                self.assertAlmostEqual(m.spent_usd - before, 0.25)
        self.assertEqual(m.worst_case_charges, 6)

    def test_check_blocks_the_call_that_could_cross_the_limit(self) -> None:
        m = MatchMeter((8.0, 40.0), 1.0)
        m.charge_bound(0.8)
        m.check(0.2)  # exactly at the limit is fine
        with self.assertRaises(MatchBudgetExceeded):
            m.check(0.21)

    def test_driver_charges_billed_retries_and_unparseable_replies(self) -> None:
        def make(transport) -> OpenAIDriver:
            d = create_driver("openai:gpt-6.2-astra", api_key="k", http_transport=transport)
            d._sleep = lambda _s: None
            d.meter = MatchMeter((8.0, 40.0), 5.0)
            return d

        obs = {"self": {"id": "pc-bram"}, "retry_feedback": None}
        seq = [RetryableLLMError("LLM request timed out.", billed=True), RetryableLLMError("LLM HTTP 429"), _chat("pc-bram")]

        def flaky(*_a):
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        d = make(flaky)
        d.decide(obs)
        bound = d.meter.call_bound_usd(d._build_request("s", "u")[2], 4096)
        self.assertEqual(d.meter.worst_case_charges, 1, "the timeout is billed at the worst case; the 429 isn't")
        self.assertGreater(d.meter.spent_usd, 0)

        d = make(lambda *_a: {"choices": "not-a-list"})
        with self.assertRaises(Exception):
            d.decide(obs)
        self.assertEqual(d.meter.worst_case_charges, 1, "an unparseable reply is charged the worst case")
        self.assertGreater(bound, 0)

    def test_driver_refuses_a_call_that_could_pass_the_match_limit(self) -> None:
        calls = []

        def transport(*_a):
            calls.append(1)
            return _chat("pc-bram")

        d = create_driver("openai:gpt-6.2-astra", api_key="k", http_transport=transport)
        d.meter = MatchMeter((8.0, 40.0), 0.01)  # less than one call's worst case
        with self.assertRaises(MatchBudgetExceeded):
            d.decide({"self": {"id": "pc-bram"}, "retry_feedback": None})
        self.assertEqual(calls, [], "the call is never made")


class TestBenchBudget(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_cap_stops_new_paid_matches_and_spend_stays_under_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            led = BudgetLedger.init(1.0, Path(tmp) / "l.jsonl", match_limit_usd=0.3)
            paid = {"driver": "openai:gpt-6.2-astra", "api_key": "k", "http_transport": _Transport(prompt_tokens=1000)}
            rep = run_benchmark(scenarios=["goblin-ambush"], contenders=[paid, "random"], seeds=list(range(1, 9)),
                                rules=self.rules, budget=led)
            stopped = {a["reason"] for a in rep["aborted_contenders"]}
            self.assertTrue(stopped <= {"budget_cap", "match_budget"} and stopped, rep.get("aborted_contenders"))
            self.assertLessEqual(led.spent(), 1.0)
            self.assertEqual(led.committed(), led.spent(), "every reservation settled")
            paid_costs = [m["cost_usd"] for m in rep["matches"] if m["contender"] != "random"]
            self.assertTrue(all(0 < c <= 0.3 for c in paid_costs))
            self.assertEqual(len([m for m in rep["matches"] if m["contender"] == "random"]), 8)
            self.assertIn("Budget:", render_benchmark_markdown(rep))

    def test_per_match_limit_aborts_an_expensive_match_and_records_its_spend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.jsonl"
            led = BudgetLedger.init(50.0, path, match_limit_usd=0.5)
            # 20k prompt tokens a call at $8/M = $0.16 a call, so the third call's worst case can't fit in $0.50.
            paid = {"driver": "openai:gpt-6.2-astra", "api_key": "k", "http_transport": _Transport(prompt_tokens=20_000)}
            rep = run_benchmark(scenarios=["wyrmling-lair"], contenders=[paid], seeds=[1], rules=self.rules, budget=led)
            self.assertEqual([a["reason"] for a in rep["aborted_contenders"]], ["match_budget"])
            self.assertEqual(rep["matches"], [], "the aborted match isn't scored")
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            settles = [r for r in rows if r["type"] == "settle"]
            self.assertEqual(len(settles), 1)
            self.assertEqual(settles[0]["outcome"], "aborted:match_budget")
            self.assertGreater(settles[0]["amount_usd"], 0)
            self.assertLessEqual(settles[0]["amount_usd"], 0.5)

    def test_out_of_credits_match_is_settled_with_its_partial_spend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.jsonl"
            led = BudgetLedger.init(50.0, path)
            transport = _Transport(fail_after=4, fail=ProviderUnavailable("out_of_credits", "no credits (HTTP 402)."))
            paid = {"driver": "openai:gpt-6.2-astra", "api_key": "k", "http_transport": transport}
            run_benchmark(scenarios=["goblin-ambush"], contenders=[paid], seeds=[1, 2], rules=self.rules, budget=led)
            settles = [json.loads(l) for l in path.read_text().splitlines() if '"settle"' in l]
            self.assertTrue(any(r["outcome"] == "aborted:out_of_credits" and r["amount_usd"] > 0 for r in settles))
            self.assertEqual(led.committed(), led.spent())


class TestBudgetPieces(unittest.TestCase):
    def test_prices_and_costs(self) -> None:
        self.assertEqual(contender_price("heuristic-tactical"), (0.0, 0.0))
        self.assertEqual(contender_price("anthropic:claude-opus-5-5"), (0.0, 0.0))  # Claude Max login
        self.assertEqual(contender_price("xai:grok-4.7"), (2.0, 6.0))
        self.assertIsNone(contender_price("xai:grok-9"))
        self.assertEqual(contender_price("xai:grok-9", {"grok-9": (1.0, 2.0)}), (1.0, 2.0))
        self.assertEqual(parse_price("gemini-4-neon=3/15"), ("gemini-4-neon", (3.0, 15.0)))
        with self.assertRaises(ValueError):
            parse_price("grok-4.7=-2/6")
        t = {"actions": [
            {"side": "party", "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 10, "billable_output_tokens": 100_000}},
            {"side": "enemy", "usage": {"prompt_tokens": 5_000_000}},
            {"side": "party", "usage": {"prompt_tokens": 0, "completion_tokens": 100_000}},
        ]}
        self.assertAlmostEqual(match_cost(t, (2.0, 6.0)), 2.0 + 0.6 + 0.6)
        with self.assertRaises(LedgerError):
            match_cost({"actions": [{"side": "party", "seq": 3, "usage": {"prompt_tokens": -1}}]}, (2.0, 6.0))

    def test_progress_line_shows_committed_spend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            led = BudgetLedger.init(150, Path(tmp) / "l.jsonl")
            led.settle(led.reserve({}, 15.0), 12.345, {})
            out = io.StringIO()
            cb = make_bench_progress(stream=out, budget=led, clock=iter([0.0, 5.0]).__next__)
            cb({"contender": "c", "scenario_id": "s", "seed": 1, "winner": "enemy", "rounds": 1,
                "composite_score": 1.0, "violations": 0}, 1, 2)
            self.assertIn("committed $12.35 of $150.00", out.getvalue())


def _inflated_bench_worker(path: str, out: Any) -> None:
    """One bench process whose provider over-reports usage (10M prompt tokens a call)."""
    led = BudgetLedger(150.0, path, match_limit_usd=5.0)
    paid = {"driver": "openai:gpt-6.2-astra", "api_key": "k", "http_transport": _Transport(prompt_tokens=10_000_000)}
    rep = run_benchmark(scenarios=["goblin-ambush"], contenders=[paid], seeds=[1], rules=RulesStore(), budget=led)
    out.put(len(rep["matches"]) + len(rep.get("aborted_contenders") or []))


class TestR3ClampAndMissingUsage(unittest.TestCase):
    def test_inflated_usage_in_parallel_cannot_pass_the_cap(self) -> None:
        """CTO r2 repro: N=8 processes, $139 already spent, stub usage far above the bound. Must end <= $150."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            led = _settled_ledger(path, 150.0, 139.0)
            ctx = multiprocessing.get_context("fork")
            q = ctx.Queue()
            procs = [ctx.Process(target=_inflated_bench_worker, args=(str(path), q)) for _ in range(8)]
            for pr in procs:
                pr.start()
            for pr in procs:
                pr.join(timeout=60)
            self.assertEqual(sum(q.get(timeout=5) for _ in procs), 8)
            self.assertLessEqual(led.spent(), 150.0)
            self.assertEqual(led.committed(), led.spent(), "every reservation settled")
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            racers = [r for r in rows if r["type"] == "settle" and r.get("contender") != "seed"]
            self.assertTrue(racers and all(r["amount_usd"] <= 5.0 for r in racers))
            self.assertTrue(any(r.get("clamped_charges", 0) > 0 for r in racers))

    def test_clamp_and_settle_never_exceed_the_bound_or_reservation(self) -> None:
        m = MatchMeter((8.0, 40.0), 5.0)
        charged = m.charge_usage({"prompt_tokens": 10_000_000, "completion_tokens": 10}, 0.2)
        self.assertEqual((charged, m.clamped_charges), (0.2, 1))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.jsonl"
            led = BudgetLedger.init(20.0, path, match_limit_usd=5.0)
            rid = led.reserve({})
            led.settle(rid, 999.0, {})
            row = json.loads(path.read_text().splitlines()[-1])
            self.assertEqual((row["amount_usd"], row["metered_usd"]), (5.0, 999.0))
            self.assertEqual(led.spent(), 5.0)

    def test_reply_without_usage_is_charged_the_worst_case_on_the_driver_path(self) -> None:
        def no_usage(*_a):
            reply = _chat("pc-bram")
            reply.pop("usage")
            return reply

        for spec in ("openai:gpt-6.2-astra", "xai:grok-4.7"):
            with self.subTest(spec=spec):
                d = create_driver(spec, api_key="k", http_transport=no_usage)
                price = (8.0, 40.0) if "astra" in spec else (2.0, 6.0)
                d.meter = MatchMeter(price, 5.0)
                dec = d.decide({"self": {"id": "pc-bram"}, "retry_feedback": None})
                self.assertIs(dec.usage.get("usage_reported"), False)
                self.assertEqual(d.meter.worst_case_charges, 1)
                self.assertGreater(d.meter.spent_usd, 0)
        with tempfile.TemporaryDirectory() as tmp:  # CTO repro: 2 calls with no usage, spent() must not be 0
            led = BudgetLedger.init(50.0, Path(tmp) / "l.jsonl", match_limit_usd=5.0)
            paid = {"driver": "openai:gpt-6.2-astra", "api_key": "k", "http_transport": no_usage}
            run_benchmark(scenarios=["goblin-ambush"], contenders=[paid], seeds=[1], rules=RulesStore(), budget=led)
            self.assertGreater(led.spent(), 0)

    def test_gemini_and_anthropic_flag_missing_usage(self) -> None:
        from srd2024.arena.agents import AnthropicDriver, GeminiDriver

        g = GeminiDriver(model="gemini-4-neon", api_key="k")
        _, gu = g._parse_response({"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}]})
        self.assertIs(gu.get("usage_reported"), False)
        a = AnthropicDriver(model="m", api_key="k")
        _, au = a._parse_response({"content": [{"type": "text", "text": "x"}], "usage": {"input_tokens": 3}})
        self.assertIs(au.get("usage_reported"), False)
        _, ok = a._parse_response({"content": [{"type": "text", "text": "x"}], "usage": {"input_tokens": 3, "output_tokens": 4}})
        self.assertNotIn("usage_reported", ok)

    def test_per_provider_match_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            led = BudgetLedger.init(150.0, Path(tmp) / "l.jsonl")
            self.assertEqual({s: led.limit_for(s) for s in ("typesafe:jev-1.14.0", "openai:gpt-6.2-sol", "xai:grok-4.7",
                                                             "openai:gpt-6.2-astra", "gemini:gemini-4-neon")},
                             {"typesafe:jev-1.14.0": 1.0, "openai:gpt-6.2-sol": 8.0, "xai:grok-4.7": 8.0,
                              "openai:gpt-6.2-astra": 15.0, "gemini:gemini-4-neon": 20.0})
            self.assertIsNone(led.limit_for("xai:grok-9"))
            over = BudgetLedger(150.0, Path(tmp) / "l.jsonl", match_limit_usd=3.0)
            self.assertEqual(over.limit_for("gemini:gemini-4-neon"), 3.0)
            priced_only = BudgetLedger(150.0, Path(tmp) / "l.jsonl", price_overrides={"grok-9": (1.0, 1.0)})
            with self.assertRaises(ValueError):  # priced, but no limit for it
                run_benchmark(scenarios=["goblin-ambush"], contenders=["xai:grok-9"], seeds=[1], rules=RulesStore(),
                              budget=priced_only)


if __name__ == "__main__":
    unittest.main()
