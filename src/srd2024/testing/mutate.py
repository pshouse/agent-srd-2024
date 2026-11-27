"""Pure-stdlib AST Mutation Testing Harness for 5e 2024 (SRD 5.2).

Non-destructive mutation testing engine using Python 3.11+ standard library only:
- Exact source-span AST mutant discovery (`discover_mutants`) and application (`apply_mutant`).
- High-signal mutation operators: `cmp_flip`, `arith_flip`, `bool_flip`, `const_delta`.
- Isolated execution: every mutant runs in a fresh `tempfile.TemporaryDirectory()` copy
  of `src/` prepended to `PYTHONPATH` in a subprocess with strict timeout. Real files
  under `<repo_root>/src/` are NEVER modified.
- Parallel campaign runner (`run_mutation_campaign`) with deterministic sampling and
  Markdown / JSON reporting (`main_mutate`).
"""

from __future__ import annotations

import argparse
import ast
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
from typing import Any

# Target registry mapping logical target names -> (relative source path, test modules).
TARGET_SPECS: dict[str, tuple[str, list[str]]] = {
    "dice": (
        "src/srd2024/engine/dice.py",
        ["tests.test_stage2_combat_engine", "tests.test_stage4_engine_hardening"],
    ),
    "character": (
        "src/srd2024/engine/character.py",
        ["tests.test_stage2_character_engine", "tests.test_stage4_engine_hardening"],
    ),
    "combat": (
        "src/srd2024/engine/combat.py",
        ["tests.test_stage2_combat_engine", "tests.test_stage4_engine_hardening"],
    ),
}

CMP_FLIP_MAP: dict[type[ast.cmpop], tuple[str, str]] = {
    ast.Lt: ("<", "<="),
    ast.LtE: ("<=", "<"),
    ast.Gt: (">", ">="),
    ast.GtE: (">=", ">"),
    ast.Eq: ("==", "!="),
    ast.NotEq: ("!=", "=="),
}

ARITH_FLIP_MAP: dict[type[ast.operator], tuple[str, str]] = {
    ast.Add: ("+", "-"),
    ast.Sub: ("-", "+"),
    ast.Mult: ("*", "//"),
    ast.FloorDiv: ("//", "*"),
}

AUG_ARITH_FLIP_MAP: dict[type[ast.operator], tuple[str, str]] = {
    ast.Add: ("+=", "-="),
    ast.Sub: ("-=", "+="),
    ast.Mult: ("*=", "//="),
    ast.FloorDiv: ("//=", "*="),
}

CONST_DELTA_MAP: dict[int, int] = {
    0: 1,
    1: 0,
    2: 1,
    5: 10,
    10: 5,
    20: 19,
    -2: -1,
}


def default_repo_root() -> Path:
    """Return the repository root derived from this module's path."""
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Mutant:
    """Immutable descriptor of a single source-span AST mutation."""

    mutant_id: str
    file: str
    line: int
    col: int
    end_line: int
    end_col: int
    start_offset: int
    end_offset: int
    operator: str
    original: str
    mutated: str
    code_line: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutant_id": self.mutant_id,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "operator": self.operator,
            "original": self.original,
            "mutated": self.mutated,
            "code_line": self.code_line,
        }


def _is_non_numeric_expr(node: ast.AST) -> bool:
    """Heuristic check to skip obvious string/bytes/sequence concatenation or repetition."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes))
    if isinstance(node, (ast.JoinedStr, ast.List, ast.Tuple, ast.ListComp)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in ("str", "bytes", "list", "tuple"):
            return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_non_numeric_expr(node.left) or _is_non_numeric_expr(node.right)
    return False


def _has_docstring(body: list[ast.stmt]) -> bool:
    return (
        bool(body)
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )


class _SourceIndex:
    """Maps AST (lineno, UTF-8 byte col) and tokenize spans to exact character offsets."""

    def __init__(self, source_text: str) -> None:
        self.source_text = source_text
        self.lines = source_text.splitlines(keepends=True)
        self.line_starts: list[int] = []
        offset = 0
        for ln in self.lines:
            self.line_starts.append(offset)
            offset += len(ln)
        self._line_bytes = [ln.encode("utf-8") for ln in self.lines]

        # Collect OP and NAME tokens with character offsets for exact operator span lookup.
        self._tokens: list[tuple[int, int, int, str, int, int, int, int]] = []
        for tok in tokenize.generate_tokens(io.StringIO(source_text).readline):
            if tok.type in (tokenize.OP, tokenize.NAME):
                s_line, s_col = tok.start
                e_line, e_col = tok.end
                if 1 <= s_line <= len(self.line_starts) and 1 <= e_line <= len(self.line_starts):
                    s_off = self.line_starts[s_line - 1] + s_col
                    e_off = self.line_starts[e_line - 1] + e_col
                    self._tokens.append(
                        (s_off, e_off, tok.type, tok.string, s_line, s_col, e_line, e_col)
                    )
        self._token_starts = [t[0] for t in self._tokens]

    def ast_offset(self, lineno: int, byte_col: int) -> int:
        raw_prefix = self._line_bytes[lineno - 1][:byte_col]
        char_col = len(raw_prefix.decode("utf-8"))
        return self.line_starts[lineno - 1] + char_col

    def node_span(self, node: ast.AST) -> tuple[int, int, int, int, int, int] | None:
        lineno = getattr(node, "lineno", None)
        col = getattr(node, "col_offset", None)
        end_lineno = getattr(node, "end_lineno", None)
        end_col = getattr(node, "end_col_offset", None)
        if lineno is None or col is None or end_lineno is None or end_col is None:
            return None
        s_off = self.ast_offset(lineno, col)
        e_off = self.ast_offset(end_lineno, end_col)
        s_char_col = s_off - self.line_starts[lineno - 1]
        e_char_col = e_off - self.line_starts[end_lineno - 1]
        return (s_off, e_off, lineno, s_char_col, end_lineno, e_char_col)

    def find_token_between(
        self,
        start_offset: int,
        end_offset: int,
        tok_type: int,
        tok_string: str,
    ) -> tuple[int, int, int, int, int, int] | None:
        idx = bisect_left(self._token_starts, start_offset)
        n = len(self._tokens)
        while idx < n:
            s_off, e_off, t_type, t_str, s_line, s_col, e_line, e_col = self._tokens[idx]
            if s_off >= end_offset:
                break
            if e_off <= end_offset and t_type == tok_type and t_str == tok_string:
                return (s_off, e_off, s_line, s_col, e_line, e_col)
            idx += 1
        return None

    def code_line(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].rstrip("\r\n").strip()
        return ""


class _MutantCollector(ast.NodeVisitor):
    """AST visitor collecting high-signal executable mutations inside function bodies."""

    def __init__(self, rel_file: str, index: _SourceIndex) -> None:
        self.rel_file = rel_file
        self.index = index
        self.mutants: list[Mutant] = []
        self._in_function = 0
        self._in_dict_key = 0
        self._seen_spans: set[tuple[int, int, str]] = set()

    def _add(
        self,
        span: tuple[int, int, int, int, int, int] | None,
        operator: str,
        original: str,
        mutated: str,
    ) -> None:
        if span is None:
            return
        s_off, e_off, s_line, s_col, e_line, e_col = span
        actual = self.index.source_text[s_off:e_off]
        if actual != original:
            return
        key = (s_off, e_off, mutated)
        if key in self._seen_spans:
            return
        self._seen_spans.add(key)
        mutant_id = f"{self.rel_file}:{s_line}:{s_col}:{operator}:{len(self.mutants) + 1}"
        self.mutants.append(
            Mutant(
                mutant_id=mutant_id,
                file=self.rel_file,
                line=s_line,
                col=s_col,
                end_line=e_line,
                end_col=e_col,
                start_offset=s_off,
                end_offset=e_off,
                operator=operator,
                original=original,
                mutated=mutated,
                code_line=self.index.code_line(s_line),
            )
        )

    def _visit_body(self, body: list[ast.stmt]) -> None:
        start_idx = 1 if _has_docstring(body) else 0
        for stmt in body[start_idx:]:
            self.visit(stmt)

    def visit_Module(self, node: ast.Module) -> None:
        self._visit_body(node.body)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Skip base-class annotations/decorators; inspect methods and inner classes.
        self._visit_body(node.body)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Deliberately skip annotations, decorators, and parameter default-arg boilerplate.
        self._in_function += 1
        try:
            self._visit_body(node.body)
        finally:
            self._in_function -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._in_function += 1
        try:
            self._visit_body(node.body)
        finally:
            self._in_function -= 1

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._in_function == 0:
            return
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._in_function == 0:
            return
        for target in node.targets:
            self.visit(target)
        self.visit(node.value)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        # Skip display / diagnostic f-strings to avoid noisy prose-only mutations.
        return

    def visit_Dict(self, node: ast.Dict) -> None:
        self._in_dict_key += 1
        try:
            for k in node.keys:
                if k is not None:
                    self.visit(k)
        finally:
            self._in_dict_key -= 1
        for v in node.values:
            self.visit(v)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self._in_function > 0:
            prev_node: ast.AST = node.left
            for op, comp in zip(node.ops, node.comparators):
                mapping = CMP_FLIP_MAP.get(type(op))
                if mapping is not None:
                    orig_op, mut_op = mapping
                    left_span = self.index.node_span(prev_node)
                    right_span = self.index.node_span(comp)
                    if left_span is not None and right_span is not None:
                        tok_span = self.index.find_token_between(
                            left_span[1], right_span[0], tokenize.OP, orig_op
                        )
                        self._add(tok_span, "cmp_flip", orig_op, mut_op)
                prev_node = comp
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if self._in_function > 0:
            mapping = ARITH_FLIP_MAP.get(type(node.op))
            if mapping is not None:
                skip = False
                if isinstance(node.op, (ast.Add, ast.Mult)):
                    if _is_non_numeric_expr(node.left) or _is_non_numeric_expr(node.right):
                        skip = True
                if not skip:
                    orig_op, mut_op = mapping
                    left_span = self.index.node_span(node.left)
                    right_span = self.index.node_span(node.right)
                    if left_span is not None and right_span is not None:
                        tok_span = self.index.find_token_between(
                            left_span[1], right_span[0], tokenize.OP, orig_op
                        )
                        self._add(tok_span, "arith_flip", orig_op, mut_op)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self._in_function > 0:
            mapping = AUG_ARITH_FLIP_MAP.get(type(node.op))
            if mapping is not None:
                skip = isinstance(node.op, (ast.Add, ast.Mult)) and _is_non_numeric_expr(
                    node.value
                )
                if not skip:
                    orig_op, mut_op = mapping
                    target_span = self.index.node_span(node.target)
                    val_span = self.index.node_span(node.value)
                    if target_span is not None and val_span is not None:
                        tok_span = self.index.find_token_between(
                            target_span[1], val_span[0], tokenize.OP, orig_op
                        )
                        self._add(tok_span, "arith_flip", orig_op, mut_op)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if self._in_function > 0:
            if isinstance(node.op, ast.And):
                orig_op, mut_op = "and", "or"
            elif isinstance(node.op, ast.Or):
                orig_op, mut_op = "or", "and"
            else:
                orig_op = mut_op = ""
            if orig_op:
                for left_v, right_v in zip(node.values, node.values[1:]):
                    left_span = self.index.node_span(left_v)
                    right_span = self.index.node_span(right_v)
                    if left_span is not None and right_span is not None:
                        tok_span = self.index.find_token_between(
                            left_span[1], right_span[0], tokenize.NAME, orig_op
                        )
                        self._add(tok_span, "bool_flip", orig_op, mut_op)
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        # Handle signed negative integer literals (e.g., `-2` -> `-1`) atomically
        # without descending into the inner positive Constant (which would turn `-1` into `-0`).
        if (
            self._in_function > 0
            and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and type(node.operand.value) is int
        ):
            if self._in_dict_key == 0:
                neg_val = -node.operand.value
                if neg_val in CONST_DELTA_MAP:
                    span = self.index.node_span(node)
                    if span is not None:
                        raw = self.index.source_text[span[0] : span[1]]
                        if raw.replace(" ", "") == str(neg_val):
                            self._add(span, "const_delta", raw, str(CONST_DELTA_MAP[neg_val]))
            return
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if self._in_function == 0:
            return
        val = node.value
        if isinstance(val, bool):
            orig = "True" if val else "False"
            mut = "False" if val else "True"
            span = self.index.node_span(node)
            self._add(span, "bool_flip", orig, mut)
        elif type(val) is int and self._in_dict_key == 0:
            if val in CONST_DELTA_MAP:
                orig = str(val)
                mut = str(CONST_DELTA_MAP[val])
                span = self.index.node_span(node)
                self._add(span, "const_delta", orig, mut)


def discover_mutants(
    source_path: str | Path,
    *,
    repo_root: Path | None = None,
) -> list[Mutant]:
    """Discover all high-signal AST mutants in `source_path` without modifying any file."""
    path = Path(source_path).resolve()
    source_text = path.read_text(encoding="utf-8")
    tree = ast.parse(source_text, filename=str(path))
    index = _SourceIndex(source_text)

    if repo_root is not None:
        try:
            rel_file = path.relative_to(Path(repo_root).resolve()).as_posix()
        except ValueError:
            rel_file = path.as_posix()
    else:
        rel_file = path.as_posix()

    collector = _MutantCollector(rel_file, index)
    collector.visit(tree)
    return collector.mutants


def apply_mutant(source_text: str, mutant: Mutant) -> str:
    """Return a mutated copy of `source_text` via exact source-span replacement."""
    actual = source_text[mutant.start_offset : mutant.end_offset]
    if actual != mutant.original:
        raise ValueError(
            f"Mutant span mismatch at {mutant.file}:{mutant.line}:{mutant.col}: "
            f"expected {mutant.original!r}, found {actual!r}"
        )
    return source_text[: mutant.start_offset] + mutant.mutated + source_text[mutant.end_offset :]


def _build_isolated_env(tmp_src: Path, repo_root: Path) -> dict[str, str]:
    """Pass a minimal environment to mutant subprocesses. No API keys or other secrets."""
    env: dict[str, str] = {}
    for key in ("PATH", "PYTHONPATH", "PYTHONHASHSEED"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    env["PYTHONPATH"] = f"{tmp_src}{os.pathsep}{repo_root}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    rules_file = (repo_root / "dist" / "rules.json").resolve()
    if rules_file.is_file():
        env["SRD2024_RULES"] = str(rules_file)
    db_file = (repo_root / "dist" / "srd2024.sqlite").resolve()
    if db_file.is_file():
        env["SRD2024_DB"] = str(db_file)
    return env


def _copy_src_isolated(repo_root: Path, tmp_dir: Path) -> Path:
    """Copy `<repo_root>/src` into `<tmp_dir>/src`, skipping bytecode caches."""
    src_origin = repo_root / "src"
    tmp_src = tmp_dir / "src"
    shutil.copytree(
        src_origin,
        tmp_src,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    return tmp_src


def _run_unittest_subprocess(
    tmp_src: Path,
    repo_root: Path,
    test_modules: list[str],
    timeout: float,
) -> tuple[str, float, str]:
    """Run targeted unittest modules in a subprocess. Returns `(status, elapsed_s, detail)`."""
    cmd = [sys.executable, "-m", "unittest", *test_modules, "--failfast"]
    env = _build_isolated_env(tmp_src, repo_root)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ("timeout", time.monotonic() - t0, f"Timed out after {timeout:.1f}s")

    elapsed = time.monotonic() - t0
    if proc.returncode == 0:
        return ("passed", elapsed, "")
    detail = (proc.stderr or proc.stdout or "").strip()
    if len(detail) > 600:
        detail = detail[-600:]
    return ("failed", elapsed, detail)


def _evaluate_single_mutant(
    mutant: Mutant,
    original_source: str,
    rel_under_src: Path,
    test_modules: list[str],
    repo_root: Path,
    timeout: float,
) -> tuple[Mutant, str, float]:
    """Evaluate one mutant inside its own isolated TemporaryDirectory copy of `src/`."""
    mutated_source = apply_mutant(original_source, mutant)
    with tempfile.TemporaryDirectory(prefix="srd2024-mut-") as tmp_root:
        tmp_src = _copy_src_isolated(repo_root, Path(tmp_root))
        target_copy = tmp_src / rel_under_src
        # Safety invariant: assert target_copy is strictly inside tmp_root, NEVER repo_root.
        target_copy_resolved = target_copy.resolve()
        tmp_root_resolved = Path(tmp_root).resolve()
        if not target_copy_resolved.is_relative_to(tmp_root_resolved):
            raise RuntimeError(
                f"Refusing to write outside isolated temp dir: {target_copy_resolved}"
            )
        target_copy.write_text(mutated_source, encoding="utf-8")

        run_status, elapsed, _ = _run_unittest_subprocess(
            tmp_src, repo_root, test_modules, timeout
        )
        if run_status == "passed":
            outcome = "survived"
        elif run_status == "timeout":
            outcome = "timeout"
        else:
            outcome = "killed"
        return (mutant, outcome, elapsed)


def _resolve_targets(target: str) -> list[str]:
    if target == "all":
        return ["dice", "character", "combat"]
    if target not in TARGET_SPECS:
        valid = ", ".join(list(TARGET_SPECS.keys()) + ["all"])
        raise ValueError(f"Unknown mutation target {target!r}. Expected one of: {valid}")
    return [target]


def run_mutation_campaign(
    *,
    target: str = "dice",
    limit: int = 40,
    seed: int = 42,
    workers: int = 4,
    timeout: float = 15.0,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Run a non-destructive parallel mutation testing campaign and return structured report."""
    root = Path(repo_root).resolve() if repo_root is not None else default_repo_root()
    target_names = _resolve_targets(target)
    t_campaign_start = time.monotonic()

    # Collect unique baseline test modules in deterministic order.
    baseline_modules: list[str] = []
    for tname in target_names:
        for mod in TARGET_SPECS[tname][1]:
            if mod not in baseline_modules:
                baseline_modules.append(mod)

    # 1. Run baseline in an isolated temp copy first (fail fast if not green).
    with tempfile.TemporaryDirectory(prefix="srd2024-mut-base-") as base_tmp:
        base_src = _copy_src_isolated(root, Path(base_tmp))
        base_status, base_elapsed, base_detail = _run_unittest_subprocess(
            base_src, root, baseline_modules, max(timeout, 30.0)
        )

    if base_status != "passed":
        return {
            "status": "baseline_failed",
            "target": target,
            "seed": seed,
            "limit": limit,
            "workers": workers,
            "timeout_s": timeout,
            "baseline_ok": False,
            "baseline_duration_s": round(base_elapsed, 2),
            "baseline_error": base_detail,
            "duration_s": round(time.monotonic() - t_campaign_start, 2),
            "discovered_total": 0,
            "evaluated_total": 0,
            "killed": 0,
            "survived": 0,
            "timeout": 0,
            "mutation_score_pct": 0.0,
            "by_file": {},
            "by_operator": {},
            "surviving_mutants": [],
        }

    # 2. Discover mutants across requested targets.
    source_cache: dict[str, str] = {}
    file_rel_under_src: dict[str, Path] = {}
    file_test_modules: dict[str, list[str]] = {}
    discovered_by_file: dict[str, int] = {}
    all_mutants: list[Mutant] = []

    for tname in target_names:
        rel_path_str, test_mods = TARGET_SPECS[tname]
        abs_path = (root / rel_path_str).resolve()
        source_cache[rel_path_str] = abs_path.read_text(encoding="utf-8")
        file_rel_under_src[rel_path_str] = Path(rel_path_str).relative_to("src")
        file_test_modules[rel_path_str] = list(test_mods)
        file_mutants = discover_mutants(abs_path, repo_root=root)
        discovered_by_file[rel_path_str] = len(file_mutants)
        all_mutants.extend(file_mutants)

    # 3. Deterministic sampling if `--limit` > 0.
    if limit > 0 and len(all_mutants) > limit:
        rng = random.Random(seed)
        sampled_indices = sorted(rng.sample(range(len(all_mutants)), limit))
        selected_mutants = [all_mutants[i] for i in sampled_indices]
    else:
        selected_mutants = list(all_mutants)

    # 4. Evaluate mutants in parallel via ThreadPoolExecutor.
    killed = 0
    survived = 0
    timed_out = 0
    surviving_mutants: list[Mutant] = []

    by_file_counts: dict[str, dict[str, int]] = {
        rel_p: {
            "discovered": discovered_by_file[rel_p],
            "evaluated": 0,
            "killed": 0,
            "survived": 0,
            "timeout": 0,
        }
        for rel_p in discovered_by_file
    }
    by_op_counts: dict[str, dict[str, int]] = {}

    max_workers = max(1, int(workers))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                _evaluate_single_mutant,
                m,
                source_cache[m.file],
                file_rel_under_src[m.file],
                file_test_modules[m.file],
                root,
                timeout,
            )
            for m in selected_mutants
        ]
        for fut in as_completed(futures):
            m, outcome, _elapsed = fut.result()
            f_bucket = by_file_counts[m.file]
            f_bucket["evaluated"] += 1
            op_bucket = by_op_counts.setdefault(
                m.operator, {"evaluated": 0, "killed": 0, "survived": 0, "timeout": 0}
            )
            op_bucket["evaluated"] += 1

            if outcome == "killed":
                killed += 1
                f_bucket["killed"] += 1
                op_bucket["killed"] += 1
            elif outcome == "timeout":
                timed_out += 1
                f_bucket["timeout"] += 1
                op_bucket["timeout"] += 1
            else:
                survived += 1
                f_bucket["survived"] += 1
                op_bucket["survived"] += 1
                surviving_mutants.append(m)

    total = len(selected_mutants)
    score = round(100.0 * (killed + timed_out) / max(1, total), 1)

    for bucket in by_file_counts.values():
        ev = bucket["evaluated"]
        bucket["mutation_score_pct"] = (
            round(100.0 * (bucket["killed"] + bucket["timeout"]) / max(1, ev), 1)
            if ev > 0
            else 0.0
        )
    for bucket in by_op_counts.values():
        ev = bucket["evaluated"]
        bucket["mutation_score_pct"] = round(
            100.0 * (bucket["killed"] + bucket["timeout"]) / max(1, ev), 1
        )

    surviving_mutants.sort(key=lambda m: (m.file, m.line, m.col, m.operator))

    return {
        "status": "ok",
        "target": target,
        "seed": seed,
        "limit": limit,
        "workers": max_workers,
        "timeout_s": timeout,
        "baseline_ok": True,
        "baseline_duration_s": round(base_elapsed, 2),
        "duration_s": round(time.monotonic() - t_campaign_start, 2),
        "discovered_total": len(all_mutants),
        "evaluated_total": total,
        "killed": killed,
        "survived": survived,
        "timeout": timed_out,
        "mutation_score_pct": score,
        "by_file": by_file_counts,
        "by_operator": dict(sorted(by_op_counts.items())),
        "surviving_mutants": [m.to_dict() for m in surviving_mutants],
    }


def format_report_markdown(report: dict[str, Any]) -> str:
    """Format a mutation campaign report as concise GitHub-style Markdown."""
    lines: list[str] = [
        "# 5e 2024 (SRD 5.2) AST Mutation Testing Report",
        "",
    ]
    if not report.get("baseline_ok"):
        lines.extend(
            [
                f"- **Status:** `baseline_failed` (target: `{report['target']}`)",
                f"- **Baseline Duration:** {report['baseline_duration_s']}s",
                "",
                "```text",
                str(report.get("baseline_error") or "Baseline suite failed."),
                "```",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            f"- **Target:** `{report['target']}` (seed=`{report['seed']}`, limit=`{report['limit']}`, workers=`{report['workers']}`)",
            f"- **Mutation Score:** **{report['mutation_score_pct']}%** "
            f"({report['killed']} killed, {report['timeout']} timeout, {report['survived']} survived / "
            f"{report['evaluated_total']} evaluated of {report['discovered_total']} discovered)",
            f"- **Duration:** {report['duration_s']}s (baseline {report['baseline_duration_s']}s)",
            "",
            "## Breakdown by File",
            "",
            "| File | Discovered | Evaluated | Killed | Timeout | Survived | Score |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for fpath, stats in report["by_file"].items():
        lines.append(
            f"| `{fpath}` | {stats['discovered']} | {stats['evaluated']} | "
            f"{stats['killed']} | {stats['timeout']} | {stats['survived']} | "
            f"{stats['mutation_score_pct']}% |"
        )

    if report["by_operator"]:
        lines.extend(
            [
                "",
                "## Breakdown by Operator",
                "",
                "| Operator | Evaluated | Killed | Timeout | Survived | Score |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for op_name, stats in report["by_operator"].items():
            lines.append(
                f"| `{op_name}` | {stats['evaluated']} | {stats['killed']} | "
                f"{stats['timeout']} | {stats['survived']} | {stats['mutation_score_pct']}% |"
            )

    survivors = report.get("surviving_mutants") or []
    lines.extend(["", f"## Surviving Mutants ({len(survivors)})", ""])
    if not survivors:
        lines.append("None — 100% of evaluated mutants were killed.")
    else:
        lines.append("| Location | Operator | Mutation | Code Line |")
        lines.append("| --- | --- | --- | --- |")
        for s in survivors:
            code_escaped = str(s["code_line"]).replace("|", "\\|").replace("`", "'")
            if len(code_escaped) > 90:
                code_escaped = code_escaped[:87] + "..."
            lines.append(
                f"| `{s['file']}:{s['line']}` | `{s['operator']}` | "
                f"`{s['original']}` -> `{s['mutated']}` | `{code_escaped}` |"
            )

    return "\n".join(lines)


def format_report_json(report: dict[str, Any]) -> str:
    """Serialize a mutation campaign report to deterministic JSON."""
    return json.dumps(report, indent=2, sort_keys=False)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="srd-mutate",
        description="Non-destructive pure-stdlib AST mutation testing harness for 5e 2024 (SRD 5.2).",
    )
    parser.add_argument(
        "--target",
        choices=("dice", "character", "combat", "all"),
        default="dice",
        help="Engine target module(s) to mutate (default: dice).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Maximum mutants to evaluate; 0 evaluates all discovered mutants (default: 40).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic PRNG seed for mutant sampling (default: 42).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel subprocess worker count (default: 4).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Per-mutant subprocess timeout in seconds (default: 15.0).",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output report format (default: markdown).",
    )
    return parser


def main_mutate(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `bin/srd-mutate`."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    report = run_mutation_campaign(
        target=args.target,
        limit=args.limit,
        seed=args.seed,
        workers=args.workers,
        timeout=args.timeout,
    )
    if args.format == "json":
        print(format_report_json(report))
    else:
        print(format_report_markdown(report))
    return 0 if report.get("baseline_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main_mutate())
