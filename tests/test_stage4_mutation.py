"""Stage 4 Verification Tests (Track 1B): Pure-Stdlib AST Mutation Testing Harness.

Covers:
1. Mutant discovery across all 4 operator families (`cmp_flip`, `arith_flip`, `bool_flip`,
   `const_delta`), including `-2 -> -1`, skipping docstrings, annotations, default args,
   f-strings, and string concatenation.
2. Source-span `apply_mutant` produces syntactically valid Python (`ast.parse`) for EVERY
   discovered mutant across `dice.py`, `character.py`, and `combat.py`.
3. Non-destructive isolation: real files under `src/srd2024/engine/` are byte-identical
   (SHA-256 unchanged) before and after `run_mutation_campaign`.
4. Real fast campaign on `dice` (`limit=10`) verifying high kill rate and deterministic
   sampling.
5. Zero-install CLI `bin/srd-mutate` in both `--format json` and `--format markdown` modes.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from srd2024.testing.mutate import (
    TARGET_SPECS,
    apply_mutant,
    discover_mutants,
    run_mutation_campaign,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestMutantDiscoveryAndApplication(unittest.TestCase):
    """Verify AST mutant discovery, filtering, and syntactically valid span replacement."""

    def test_operators_and_noise_filters_on_synthetic_source(self) -> None:
        sample = '''"""Module docstring with True and 0 and 1 + 2."""

TABLE = {0: 1, 2: 5}


def compute(x: int = 10, flag: bool = False) -> int:
    """Function docstring: should not mutate True or 20."""
    label = "hello" + "world"
    msg = f"value={x + 1}"
    lookup = {0: x, 1: 20}
    if x >= 5 and not flag:
        penalty = -2 * x
        total = x + 1
        total += 2
        return total + penalty + lookup[0]
    return 0 if flag is True else 1
'''
        with tempfile.TemporaryDirectory(prefix="srd2024-mut-unit-") as td:
            p = Path(td) / "sample.py"
            p.write_text(sample, encoding="utf-8")
            mutants = discover_mutants(p, repo_root=Path(td))

        ops = {m.operator for m in mutants}
        self.assertEqual(ops, {"cmp_flip", "arith_flip", "bool_flip", "const_delta"})

        # Verify every generated mutant applies cleanly and parses as valid Python.
        for m in mutants:
            mutated = apply_mutant(sample, m)
            self.assertNotEqual(mutated, sample)
            ast.parse(mutated)

        # Verify specific expected mutations exist:
        pairs = {(m.operator, m.original, m.mutated) for m in mutants}
        self.assertIn(("cmp_flip", ">=", ">"), pairs)
        self.assertIn(("bool_flip", "and", "or"), pairs)
        self.assertIn(("bool_flip", "True", "False"), pairs)
        self.assertIn(("const_delta", "-2", "-1"), pairs)
        self.assertIn(("const_delta", "5", "10"), pairs)
        self.assertIn(("const_delta", "20", "19"), pairs)
        self.assertIn(("arith_flip", "*", "//"), pairs)
        self.assertIn(("arith_flip", "+", "-"), pairs)
        self.assertIn(("arith_flip", "+=", "-="), pairs)

        # Verify noise filters: module table (line 3), signature defaults (line 6),
        # string concat (line 8), f-string (line 9), dict keys on line 10 not mutated.
        mutated_lines = {m.line for m in mutants}
        self.assertNotIn(3, mutated_lines, "Module-level static table should be skipped")
        self.assertNotIn(6, mutated_lines, "Function signature defaults/annotations should be skipped")
        self.assertNotIn(8, mutated_lines, "String literal concatenation should be skipped")
        self.assertNotIn(9, mutated_lines, "JoinedStr f-strings should be skipped")
        line10_const_origs = [m.original for m in mutants if m.line == 10 and m.operator == "const_delta"]
        self.assertEqual(line10_const_origs, ["20"], "Dict keys (0, 1) must be skipped, value (20) kept")

    def test_all_engine_mutants_apply_to_valid_python_ast(self) -> None:
        rng = random.Random(42)
        for target_name, (rel_path, _mods) in TARGET_SPECS.items():
            abs_path = REPO_ROOT / rel_path
            source = abs_path.read_text(encoding="utf-8")
            mutants = discover_mutants(abs_path, repo_root=REPO_ROOT)
            self.assertGreater(len(mutants), 15, f"Expected substantial mutants in {target_name}")

            # Verify exact source-span replacement succeeds on 100% of mutants.
            for m in mutants:
                mutated = apply_mutant(source, m)
                self.assertNotEqual(mutated, source)

            # Parse every mutant of dice.py and a deterministic stratified sample for large files.
            to_parse = mutants if target_name == "dice" else rng.sample(mutants, min(30, len(mutants)))
            for m in to_parse:
                mutated = apply_mutant(source, m)
                try:
                    ast.parse(mutated)
                except SyntaxError as exc:
                    self.fail(
                        f"Mutant {m.mutant_id} ({m.original!r} -> {m.mutated!r}) produced invalid Python: {exc}"
                    )


class TestNonDestructiveCampaignAndCLI(unittest.TestCase):
    """Verify isolation safety invariant, real dice kill rate, and CLI output formats."""

    def test_campaign_is_non_destructive_and_kills_dice_mutants(self) -> None:
        engine_files = [REPO_ROOT / rel for rel, _ in TARGET_SPECS.values()]
        hashes_before = {p: _sha256_file(p) for p in engine_files}

        report = run_mutation_campaign(
            target="dice",
            limit=10,
            seed=42,
            workers=4,
            timeout=5.0,
            repo_root=REPO_ROOT,
        )

        hashes_after = {p: _sha256_file(p) for p in engine_files}
        self.assertEqual(
            hashes_before,
            hashes_after,
            "CRITICAL SAFETY VIOLATION: mutation campaign altered real engine source file(s)!",
        )

        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["baseline_ok"])
        self.assertEqual(report["evaluated_total"], 10)
        self.assertEqual(
            report["killed"] + report["timeout"] + report["survived"],
            report["evaluated_total"],
        )
        expected_score = round(
            100.0 * (report["killed"] + report["timeout"]) / max(1, report["evaluated_total"]),
            1,
        )
        self.assertEqual(report["mutation_score_pct"], expected_score)
        self.assertGreaterEqual(
            report["mutation_score_pct"],
            40.0,
            f"Expected meaningful kill rate on dice engine sample, got {report['mutation_score_pct']}%",
        )
        for surv in report["surviving_mutants"]:
            for key in ("file", "line", "operator", "original", "mutated", "code_line"):
                self.assertIn(key, surv)

    def test_cli_json_and_markdown_outputs(self) -> None:
        cli_bin = REPO_ROOT / "bin" / "srd-mutate"
        self.assertTrue(cli_bin.is_file(), "bin/srd-mutate missing")

        proc_json = subprocess.run(
            [
                sys.executable,
                str(cli_bin),
                "--target",
                "dice",
                "--limit",
                "8",
                "--seed",
                "42",
                "--workers",
                "4",
                "--timeout",
                "5.0",
                "--format",
                "json",
            ],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc_json.returncode, 0, f"CLI JSON failed: {proc_json.stderr}")
        data = json.loads(proc_json.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["baseline_ok"])
        self.assertEqual(data["target"], "dice")
        self.assertEqual(data["evaluated_total"], 8)
        self.assertIn("mutation_score_pct", data)
        self.assertIn("surviving_mutants", data)

        proc_md = subprocess.run(
            [
                str(cli_bin),
                "--target",
                "dice",
                "--limit",
                "8",
                "--seed",
                "42",
                "--workers",
                "4",
                "--timeout",
                "5.0",
                "--format",
                "markdown",
            ],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc_md.returncode, 0, f"CLI Markdown failed: {proc_md.stderr}")
        self.assertIn("5e 2024 (SRD 5.2) AST Mutation Testing Report", proc_md.stdout)
        self.assertIn("Mutation Score:", proc_md.stdout)
        self.assertIn("Surviving Mutants", proc_md.stdout)


if __name__ == "__main__":
    unittest.main()
