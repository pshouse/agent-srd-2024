"""Build the static replay manifest for the web viewer (`#/replay`).

Reads only the arena traces that git tracks under `examples/sessions/`
(`git ls-files`, so an untracked local trace is never published), checks
each one, and writes:

- `dist/replays/index.json`: one row per trace (id, scenario, seed,
  winner, rounds, digest, size, SHA-256, and the matching `.md` writeup).
- `dist/replays/<id>.trace.json`: a byte-for-byte copy of each trace.

The output lives beside `dist/web/`, not inside it, because the catalog
build (`bin/srd-build`) deletes and regenerates `dist/web/`.

The script is deterministic (sorted rows, no timestamps) and fails closed:
a malformed trace, an unknown schema_version, an unsafe file name, or a
file over 5 MB stops the build before anything is written.

Usage:
  python3 scripts/build_replay_index.py           # write dist/replays/
  python3 scripts/build_replay_index.py --check   # exit 1 if dist/replays/ is stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

INDEX_SCHEMA_VERSION = "1"
TRACE_SCHEMA_VERSION = "4"
DATASET_ID = "srd-2024-5.2"
SESSIONS_DIR = "examples/sessions"
OUT_DIR = "dist/replays"
MAX_BYTES = 5 * 1024 * 1024
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,120}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
SIDES = {"party", "enemy"}


class ReplayIndexError(Exception):
    """A trace or the repository state is not safe to publish."""


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git_ls(repo: Path, pattern: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--", f":(glob){pattern}"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReplayIndexError(f"git ls-files failed in {repo}: {exc}") from exc
    return sorted(p for p in out.decode("utf-8").split("\0") if p)


def tracked_traces(repo: Path) -> list[str]:
    """Repo-relative paths of committed traces, sorted."""
    return _git_ls(repo, f"{SESSIONS_DIR}/*.trace.json")


def _driver_label(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        names = sorted({str(v) for v in value.values()})
        return " + ".join(names)
    return "unknown"


def _require(cond: bool, rel: str, message: str) -> None:
    if not cond:
        raise ReplayIndexError(f"{rel}: {message}")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_trace(trace: object, rel: str) -> None:
    """Shape checks shared in spirit with web/lib/replay-model.js."""
    _require(isinstance(trace, dict), rel, "trace is not a JSON object")
    assert isinstance(trace, dict)
    _require(
        trace.get("schema_version") == TRACE_SCHEMA_VERSION,
        rel,
        f"schema_version is {trace.get('schema_version')!r} (expected {TRACE_SCHEMA_VERSION!r})",
    )
    _require(trace.get("dataset_id") == DATASET_ID, rel, f"dataset_id is {trace.get('dataset_id')!r}")
    scenario = trace.get("scenario")
    _require(isinstance(scenario, dict), rel, "scenario is not an object")
    assert isinstance(scenario, dict)
    _require(isinstance(scenario.get("id"), str) and scenario["id"], rel, "scenario.id is missing")
    _require(isinstance(scenario.get("name"), str) and scenario["name"], rel, "scenario.name is missing")
    config = trace.get("config")
    _require(isinstance(config, dict), rel, "config is not an object")
    assert isinstance(config, dict)
    _require(_is_int(config.get("seed")), rel, "config.seed is not an integer")
    _require(isinstance(trace.get("winner"), str), rel, "winner is not a string")
    _require(_is_int(trace.get("rounds")) and trace["rounds"] >= 0, rel, "rounds is not a non-negative integer")
    _require(isinstance(trace.get("digest"), str) and DIGEST.match(trace["digest"]) is not None, rel, "digest is not a sha256 hex string")
    for key in ("initial_state", "final_state"):
        state = trace.get(key)
        _require(isinstance(state, dict) and isinstance(state.get("combatants"), dict), rel, f"{key}.combatants is not an object")
    combatants = trace["initial_state"]["combatants"]
    _require(len(combatants) > 0, rel, "initial_state has no combatants")
    _require(set(combatants) == set(trace["final_state"]["combatants"]), rel, "initial and final combatant ids differ")
    for cid, row in combatants.items():
        _require(isinstance(row, dict) and row.get("id") == cid, rel, f"combatant {cid!r} id mismatch")
        _require(row.get("side") in SIDES, rel, f"combatant {cid!r} has side {row.get('side')!r}")
        pos = row.get("position")
        _require(isinstance(pos, dict) and _is_int(pos.get("x")) and _is_int(pos.get("y")), rel, f"combatant {cid!r} position is not integer x/y")
    actions = trace.get("actions")
    _require(isinstance(actions, list), rel, "actions is not a list")
    assert isinstance(actions, list)
    for i, action in enumerate(actions):
        _require(isinstance(action, dict), rel, f"actions[{i}] is not an object")
        _require(action.get("actor_id") in combatants, rel, f"actions[{i}].actor_id is not a combatant")
        _require(isinstance(action.get("events"), list), rel, f"actions[{i}].events is not a list")
        _require(isinstance(action.get("state_diff"), dict), rel, f"actions[{i}].state_diff is not an object")
    _require(isinstance(trace.get("violations"), list), rel, "violations is not a list")
    if "rejected_inputs" in trace:
        _require(isinstance(trace["rejected_inputs"], list), rel, "rejected_inputs is not a list")


def _writeup_for(trace_rel: str, tracked_md: set[str]) -> str | None:
    """`<stem>.md`, else the session prefix before the first dot (`arena-1.md`)."""
    name = trace_rel.rsplit("/", 1)[-1][: -len(".trace.json")]
    candidates = [name]
    if "." in name:
        candidates.append(name.split(".", 1)[0])
    for stem in candidates:
        md = f"{SESSIONS_DIR}/{stem}.md"
        if md in tracked_md:
            return md
    return None


def build(repo: Path) -> tuple[dict, dict[str, bytes]]:
    """Return (index, {file name: bytes}) without touching the disk."""
    tracked_md = set(_git_ls(repo, f"{SESSIONS_DIR}/*.md"))
    rows = []
    files: dict[str, bytes] = {}
    for rel in tracked_traces(repo):
        path = repo / rel
        if path.is_symlink() or not path.is_file():
            raise ReplayIndexError(f"{rel}: tracked trace is missing or is a symlink")
        name = rel.rsplit("/", 1)[-1]
        trace_id = name[: -len(".trace.json")]
        if not SAFE_ID.match(trace_id):
            raise ReplayIndexError(f"{rel}: unsafe trace id {trace_id!r}")
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            raise ReplayIndexError(f"{rel}: {len(data)} bytes is over the 5 MB publish limit")
        try:
            trace = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReplayIndexError(f"{rel}: not valid UTF-8 JSON: {exc}") from exc
        validate_trace(trace, rel)
        scorecard = trace.get("scorecard") if isinstance(trace.get("scorecard"), dict) else {}
        composite = scorecard.get("composite_score")
        rows.append(
            {
                "id": trace_id,
                "file": name,
                "source": rel,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "scenario_id": trace["scenario"]["id"],
                "scenario_name": trace["scenario"]["name"],
                "seed": trace["config"]["seed"],
                "winner": trace["winner"],
                "rounds": trace["rounds"],
                "actions": len(trace["actions"]),
                "digest": trace["digest"],
                "party_driver": _driver_label(trace["config"].get("party_driver")),
                "enemy_driver": _driver_label(trace["config"].get("enemy_driver")),
                "composite_score": composite if isinstance(composite, (int, float)) and not isinstance(composite, bool) else None,
                "writeup": _writeup_for(rel, tracked_md),
            }
        )
        files[name] = data
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "replays": rows,
    }
    return index, files


def render_index(index: dict) -> bytes:
    return (json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def expected_outputs(repo: Path) -> dict[str, bytes]:
    index, files = build(repo)
    out = dict(files)
    out["index.json"] = render_index(index)
    return out


def write(repo: Path, out_dir: Path) -> list[str]:
    outputs = expected_outputs(repo)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(out_dir.iterdir()):
        if stale.name not in outputs:
            if stale.is_dir() and not stale.is_symlink():
                raise ReplayIndexError(f"unexpected directory in {out_dir}: {stale.name}")
            stale.unlink()
    for name, data in sorted(outputs.items()):
        (out_dir / name).write_bytes(data)
    return sorted(outputs)


def check(repo: Path, out_dir: Path) -> list[str]:
    """Problems with out_dir compared with a fresh build. Empty means up to date."""
    outputs = expected_outputs(repo)
    errors = []
    present = {p.name for p in out_dir.iterdir()} if out_dir.is_dir() else set()
    for name in sorted(outputs):
        path = out_dir / name
        if name not in present:
            errors.append(f"missing: {name}")
        elif path.read_bytes() != outputs[name]:
            errors.append(f"stale: {name}")
    for name in sorted(present - set(outputs)):
        errors.append(f"not from a committed trace: {name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the output is stale")
    parser.add_argument("--repo", type=Path, default=repo_root())
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    out_dir = (args.out or repo / OUT_DIR).resolve()
    try:
        if args.check:
            errors = check(repo, out_dir)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                print("run: python3 scripts/build_replay_index.py", file=sys.stderr)
                return 1
            print(f"{out_dir} is up to date")
            return 0
        names = write(repo, out_dir)
    except ReplayIndexError as exc:
        print(f"replay index: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(names)} files to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
