"""`srd-arena bench` progress: a line per finished match on stderr, optional incremental JSONL, no effect on results.

A 30-seed LLM bench used to print nothing for hours and lose every finished match if it was stopped.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from srd2024.arena import run_benchmark
from srd2024.engine.store import RulesStore
from srd2024.runtime.arena_bridge import main_arena, make_bench_progress

ARGS = dict(scenarios=["goblin-ambush", "crypt-of-ghouls"], contenders=["heuristic-tactical", "random"], seeds=[1, 2])


class TestBenchProgress(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_callback_sees_every_match_in_order_and_changes_nothing(self) -> None:
        seen = []
        with_cb = run_benchmark(rules=self.rules, on_match=lambda m, done, total: seen.append((dict(m), done, total)), **ARGS)
        without = run_benchmark(rules=self.rules, **ARGS)
        self.assertEqual(json.dumps(with_cb, sort_keys=True), json.dumps(without, sort_keys=True))
        self.assertEqual([d for _, d, _ in seen], list(range(1, 9)))
        self.assertTrue(all(t == 8 for _, _, t in seen))
        self.assertEqual([m for m, _, _ in seen], with_cb["matches"])

    def test_progress_lines_eta_and_jsonl(self) -> None:
        ticks = iter([0.0, 30.0, 60.0])
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "m.jsonl"
            cb = make_bench_progress(stream=out, jsonl_path=path, clock=lambda: next(ticks))
            row = {"contender": "anthropic:x", "scenario_id": "goblin-ambush", "seed": 3, "winner": "party",
                   "rounds": 4, "composite_score": 81.234, "violations": 1}
            cb(row, 1, 4)
            cb({**row, "seed": 4}, 2, 4)
            lines = out.getvalue().splitlines()
            self.assertEqual(lines[0], "[bench 1/4] anthropic:x goblin-ambush seed 3: party in 4 rounds, composite 81.23, "
                                       "1 violations | 0m30s elapsed, ETA 1m30s")
            self.assertIn("1m00s elapsed, ETA 1m00s", lines[1])
            rows = [json.loads(x) for x in path.read_text().splitlines()]
            self.assertEqual([r["seed"] for r in rows], [3, 4])

    def test_quiet_still_writes_jsonl(self) -> None:
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.jsonl"
            cb = make_bench_progress(stream=out, jsonl_path=path, quiet=True)
            cb({"contender": "c", "scenario_id": "s", "seed": 1, "winner": "enemy", "rounds": 1,
                "composite_score": 1.0, "violations": 0}, 1, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_cli_prints_progress_to_stderr_and_report_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "m.jsonl"
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main_arena(["bench", "--scenarios", "goblin-ambush", "--seeds", "1,2", "--agents",
                                   "heuristic-tactical", "--matches-jsonl", str(jsonl)])
            self.assertEqual(code, 0)
            self.assertEqual(len([l for l in err.getvalue().splitlines() if l.startswith("[bench ")]), 2)
            self.assertNotIn("[bench ", out.getvalue())
            self.assertIn("Leaderboard", out.getvalue())
            self.assertEqual(len(jsonl.read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
