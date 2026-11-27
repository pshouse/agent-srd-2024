"""Shared constants and helpers for agent-srd-2024 verification tests."""

from __future__ import annotations

import contextlib
import json
import os
import re
import select
import sqlite3
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - PyYAML optional per SPEC §2
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
RAW_EN_DIR = RAW_DIR / "2024" / "en"
PROVENANCE_PATH = RAW_DIR / "PROVENANCE.json"

DIST_DIR = REPO_ROOT / "dist"
DB_PATH = DIST_DIR / "srd2024.sqlite"
CARDS_DIR = DIST_DIR / "cards"
WEB_DIR = DIST_DIR / "web"
MANIFEST_PATH = DIST_DIR / "manifest.json"

BIN_BUILD = REPO_ROOT / "bin" / "srd-build"
BIN_SRD = REPO_ROOT / "bin" / "srd"
BIN_MCP = REPO_ROOT / "bin" / "srd-mcp"
SKILL_PATH = REPO_ROOT / "skill" / "SKILL.md"
README_PATH = REPO_ROOT / "README.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

EXPECTED_ENTITY_COUNT = 2029
EXPECTED_TYPE_COUNT = 25

EXPECTED_TYPE_COUNTS: dict[str, int] = {
    "ability-scores": 6,
    "alignments": 10,
    "backgrounds": 4,
    "classes": 12,
    "conditions": 15,
    "damage-types": 13,
    "equipment": 182,
    "equipment-categories": 30,
    "feats": 17,
    "features": 232,
    "languages": 19,
    "levels": 287,
    "magic-items": 277,
    "magic-schools": 8,
    "monsters": 341,
    "poisons": 14,
    "proficiencies": 74,
    "skills": 18,
    "species": 9,
    "spells": 339,
    "subclasses": 12,
    "subspecies": 24,
    "traits": 68,
    "weapon-mastery-properties": 8,
    "weapon-properties": 10,
}

EXPECTED_TIERS: dict[str, int] = {
    "ability-scores": 1,
    "alignments": 2,
    "backgrounds": 2,
    "classes": 2,
    "conditions": 1,
    "damage-types": 1,
    "equipment": 2,
    "equipment-categories": 3,
    "feats": 1,
    "features": 2,
    "languages": 2,
    "levels": 3,
    "magic-items": 2,
    "magic-schools": 1,
    "monsters": 2,
    "poisons": 1,
    "proficiencies": 3,
    "skills": 1,
    "species": 2,
    "spells": 1,
    "subclasses": 2,
    "subspecies": 2,
    "traits": 1,
    "weapon-mastery-properties": 1,
    "weapon-properties": 1,
}

EXPECTED_REQUIRED_META_KEYS = {
    "schema_version",
    "dataset_id",
    "upstream_repo",
    "upstream_commit",
    "upstream_database_version",
    "license",
    "license_url",
    "attribution",
    "entity_count",
    "edge_count",
    "dangling_ref_count",
    "resource_types_json",
    "source_hash",
}


def load_provenance() -> dict[str, Any]:
    return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))


@contextlib.contextmanager
def open_db_ro(path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _parse_yaml_stdlib(fm_text: str) -> dict[str, Any]:
    """Minimal fallback parser for deterministic card YAML frontmatter."""
    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in fm_text.splitlines():
        if not raw_line.strip():
            continue
        if raw_line.startswith("  - "):
            if current_list_key is None:
                raise ValueError(f"List item without key: {raw_line!r}")
            item = raw_line[4:].strip()
            if item.startswith('"') and item.endswith('"'):
                item = json.loads(item)
            data[current_list_key].append(item)
            continue
        if ":" not in raw_line:
            raise ValueError(f"Invalid YAML line: {raw_line!r}")
        key, rest = raw_line.split(":", 1)
        key = key.strip()
        val = rest.strip()
        if val == "":
            data[key] = []
            current_list_key = key
        elif val == "[]":
            data[key] = []
            current_list_key = None
        else:
            current_list_key = None
            if val.startswith('"') and val.endswith('"'):
                data[key] = json.loads(val)
            else:
                data[key] = val
    return data


def split_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    """Split and parse YAML frontmatter from a compiled Markdown card."""
    if not md_text.startswith("---\n"):
        raise ValueError("Card does not start with '---\\n'")
    end_idx = md_text.find("\n---\n", 4)
    if end_idx == -1:
        raise ValueError("Card missing closing '\\n---\\n' frontmatter delimiter")
    fm_text = md_text[4:end_idx]
    body = md_text[end_idx + 5 :]
    if yaml is not None:
        parsed = yaml.safe_load(fm_text)
        if not isinstance(parsed, dict):
            raise ValueError(f"Frontmatter YAML did not parse to a mapping: {type(parsed)}")
    else:
        parsed = _parse_yaml_stdlib(fm_text)
    return parsed, body


def detect_build_flags() -> tuple[list[str], list[str]]:
    """Detect CLI flags supported by bin/srd-build for output dir and input dir."""
    proc = subprocess.run(
        [str(BIN_BUILD), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    help_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    out_flag = None
    for candidate in ("--out-dir", "--dist-dir", "--output-dir", "--output", "--out", "--dist", "-o"):
        if re.search(rf"(?:^|[\s,/]){re.escape(candidate)}(?:[\s,=]|$)", help_text, re.MULTILINE):
            out_flag = candidate
            break
    if out_flag is None:
        raise AssertionError(f"Could not detect output directory flag from bin/srd-build --help:\n{help_text}")
    in_flag = None
    for candidate in ("--raw-dir", "--data-dir", "--input-dir", "--input", "--raw", "--src", "-i"):
        if re.search(rf"(?:^|[\s,/]){re.escape(candidate)}(?:[\s,=]|$)", help_text, re.MULTILINE):
            in_flag = candidate
            break
    return ([out_flag], [in_flag] if in_flag else [])


def run_cli(
    args: list[str],
    *,
    db_path: Path | None = None,
    env_extra: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> subprocess.CompletedProcess[str]:
    cmd = [str(BIN_SRD)]
    if db_path is not None:
        cmd.extend(["--db", str(db_path)])
    cmd.extend(args)
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def run_cli_json(
    args: list[str],
    *,
    db_path: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> Any:
    full_args = list(args)
    if "--format" not in full_args:
        full_args.extend(["--format", "json"])
    proc = run_cli(full_args, db_path=db_path, env_extra=env_extra)
    if proc.returncode != 0:
        raise AssertionError(
            f"CLI failed (rc={proc.returncode}): {' '.join(full_args)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"CLI returned non-JSON stdout for {' '.join(full_args)}: {exc}\nSTDOUT:\n{proc.stdout}"
        ) from exc


class McpStdioClient:
    """Line-delimited JSON-RPC 2.0 client over stdio subprocess for bin/srd-mcp."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> None:
        self.db_path = db_path
        self.env_extra = env_extra
        self.proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self.raw_stdout_lines: list[str] = []

    def __enter__(self) -> "McpStdioClient":
        cmd = [str(BIN_MCP)]
        if self.db_path is not None:
            cmd.extend(["--db", str(self.db_path)])
        env = os.environ.copy()
        if self.env_extra:
            env.update(self.env_extra)
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> tuple[int | None, str]:
        if self.proc is None:
            return (None, "")
        proc = self.proc
        self.proc = None
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        stderr_text = ""
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        if proc.stderr:
            stderr_text = proc.stderr.read()
            proc.stderr.close()
        if proc.stdout:
            proc.stdout.close()
        return (proc.returncode, stderr_text)

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None
        req_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

        deadline = time.monotonic() + timeout
        fd = self.proc.stdout.fileno()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for MCP response to {method} (id={req_id})")
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise TimeoutError(f"Timed out waiting for MCP response to {method} (id={req_id})")
            line = self.proc.stdout.readline()
            if line == "":
                stderr_out = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(f"MCP server exited prematurely while waiting for {method}. stderr:\n{stderr_out}")
            stripped = line.strip()
            self.raw_stdout_lines.append(stripped)
            try:
                msg = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Stray non-JSON line on MCP stdout: {line!r}") from exc
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                raise AssertionError(f"Invalid JSON-RPC 2.0 frame on MCP stdout: {msg!r}")
            if msg.get("id") == req_id:
                return msg

    def assert_no_output_within(self, wait_seconds: float = 0.25) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        ready, _, _ = select.select([self.proc.stdout.fileno()], [], [], wait_seconds)
        if ready:
            unexpected = self.proc.stdout.readline()
            raise AssertionError(f"Expected no output after notification, got: {unexpected!r}")
