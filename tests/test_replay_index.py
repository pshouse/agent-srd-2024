"""The replay manifest is built only from committed traces, deterministically, and fails closed."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import build_replay_index as bri
from scripts.site_files import assemble, check

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "examples" / "sessions"
GOBLIN = "2026-11-27-goblin-nl-1"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class ReplayIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "examples" / "sessions").mkdir(parents=True)
        _git(self.repo, "init", "-q")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add(self, name: str, data: bytes | None = None, *, track: bool = True) -> Path:
        path = self.repo / "examples" / "sessions" / name
        if data is None:
            data = (SESSIONS / name).read_bytes()
        path.write_bytes(data)
        if track:
            _git(self.repo, "add", "--", str(path.relative_to(self.repo)))
        return path

    def _goblin(self) -> dict:
        return json.loads((SESSIONS / f"{GOBLIN}.trace.json").read_text(encoding="utf-8"))

    def test_committed_replays_are_up_to_date(self) -> None:
        self.assertEqual(bri.check(ROOT, ROOT / bri.OUT_DIR), [], "run: python3 scripts/build_replay_index.py")
        index = json.loads((ROOT / bri.OUT_DIR / "index.json").read_text(encoding="utf-8"))
        ids = [row["id"] for row in index["replays"]]
        self.assertEqual(ids, sorted(ids))
        self.assertIn(GOBLIN, ids)
        for row in index["replays"]:
            self.assertLess(row["bytes"], bri.MAX_BYTES)
            self.assertTrue(row["source"].startswith("examples/sessions/"))

    def test_build_is_deterministic(self) -> None:
        self._add(f"{GOBLIN}.trace.json")
        self._add(f"{GOBLIN}.md")
        self._add("2026-11-27-arena-1.sonnet-goblin-10.trace.json")
        self._add("2026-11-27-arena-1.md")
        first = bri.expected_outputs(self.repo)
        second = bri.expected_outputs(self.repo)
        self.assertEqual(first, second)
        out_a = Path(self.tmp.name) / "a"
        out_b = Path(self.tmp.name) / "b"
        bri.write(self.repo, out_a)
        bri.write(self.repo, out_b)
        for name in first:
            self.assertEqual((out_a / name).read_bytes(), (out_b / name).read_bytes(), name)
        index = json.loads(first["index.json"])
        rows = {row["id"]: row for row in index["replays"]}
        self.assertEqual(rows[GOBLIN]["writeup"], f"examples/sessions/{GOBLIN}.md")
        self.assertEqual(rows["2026-11-27-arena-1.sonnet-goblin-10"]["writeup"], "examples/sessions/2026-11-27-arena-1.md")
        self.assertEqual(rows[GOBLIN]["seed"], 0)
        self.assertEqual(rows[GOBLIN]["winner"], "party")
        self.assertEqual(rows[GOBLIN]["party_driver"], "heuristic-tactical + human")
        self.assertNotIn("generated_at", json.dumps(index))

    def test_untracked_traces_and_writeups_are_never_listed(self) -> None:
        self._add(f"{GOBLIN}.trace.json")
        self._add("2026-11-27-local-only.trace.json", (SESSIONS / f"{GOBLIN}.trace.json").read_bytes(), track=False)
        self._add(f"{GOBLIN}.md", track=False)
        out = Path(self.tmp.name) / "out"
        out.mkdir()
        (out / "stale.trace.json").write_text("{}", encoding="utf-8")
        names = bri.write(self.repo, out)
        self.assertEqual(names, [f"{GOBLIN}.trace.json", "index.json"])
        self.assertEqual(sorted(p.name for p in out.iterdir()), names)
        index = json.loads((out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual([row["id"] for row in index["replays"]], [GOBLIN])
        self.assertIsNone(index["replays"][0]["writeup"])

    def test_malformed_traces_fail_closed_and_write_nothing(self) -> None:
        out = Path(self.tmp.name) / "out"
        out.mkdir()
        (out / "index.json").write_text("previous\n", encoding="utf-8")
        cases = {
            "schema": lambda t: t.__setitem__("schema_version", "3"),
            "dataset": lambda t: t.__setitem__("dataset_id", "other"),
            "seed": lambda t: t["config"].__setitem__("seed", "0"),
            "digest": lambda t: t.__setitem__("digest", "nope"),
            "actions": lambda t: t.__setitem__("actions", {}),
            "actor": lambda t: t["actions"][0].__setitem__("actor_id", "ghost"),
            "position": lambda t: t["initial_state"]["combatants"]["pc-bram"].__setitem__("position", {"x": 1.5, "y": 0}),
            "final": lambda t: t["final_state"]["combatants"].pop("pc-bram"),
            "rejected": lambda t: t.__setitem__("rejected_inputs", "x"),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                trace = self._goblin()
                mutate(trace)
                self._add(f"{GOBLIN}.trace.json", json.dumps(trace).encode("utf-8"))
                with self.assertRaises(bri.ReplayIndexError):
                    bri.write(self.repo, out)
                self.assertEqual((out / "index.json").read_text(encoding="utf-8"), "previous\n")
                self.assertEqual([p.name for p in out.iterdir()], ["index.json"])

    def test_bad_json_unsafe_names_and_oversize_files_fail_closed(self) -> None:
        self._add(f"{GOBLIN}.trace.json", b"{not json")
        with self.assertRaisesRegex(bri.ReplayIndexError, "not valid UTF-8 JSON"):
            bri.build(self.repo)
        (self.repo / "examples" / "sessions" / f"{GOBLIN}.trace.json").unlink()
        _git(self.repo, "rm", "-q", "--cached", "--", f"examples/sessions/{GOBLIN}.trace.json")

        self._add("Bad Name.trace.json", (SESSIONS / f"{GOBLIN}.trace.json").read_bytes())
        with self.assertRaisesRegex(bri.ReplayIndexError, "unsafe trace id"):
            bri.build(self.repo)
        _git(self.repo, "rm", "-q", "--cached", "--", "examples/sessions/Bad Name.trace.json")

        trace = self._goblin()
        trace["padding"] = "x" * bri.MAX_BYTES
        self._add(f"{GOBLIN}.trace.json", json.dumps(trace).encode("utf-8"))
        with self.assertRaisesRegex(bri.ReplayIndexError, "over the 5 MB publish limit"):
            bri.build(self.repo)

    def test_tracked_but_deleted_trace_fails_closed(self) -> None:
        path = self._add(f"{GOBLIN}.trace.json")
        path.unlink()
        with self.assertRaisesRegex(bri.ReplayIndexError, "missing or is a symlink"):
            bri.build(self.repo)

    def test_check_mode_reports_stale_output(self) -> None:
        self._add(f"{GOBLIN}.trace.json")
        out = Path(self.tmp.name) / "out"
        quiet = io.StringIO()
        with redirect_stderr(quiet), redirect_stdout(quiet):
            self.assertEqual(bri.main(["--check", "--repo", str(self.repo), "--out", str(out)]), 1)
            self.assertEqual(bri.main(["--repo", str(self.repo), "--out", str(out)]), 0)
            self.assertEqual(bri.main(["--check", "--repo", str(self.repo), "--out", str(out)]), 0)
        (out / "index.json").write_text("{}\n", encoding="utf-8")
        self.assertEqual(bri.check(self.repo, out), ["stale: index.json"])
        (out / "extra.trace.json").write_text("{}\n", encoding="utf-8")
        self.assertIn("not from a committed trace: extra.trace.json", bri.check(self.repo, out))


class ReplaySiteTests(unittest.TestCase):
    """check_site.sh enforces the replay index the same way it enforces the catalog."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        assemble(ROOT, self.site)
        self.replays = self.site / "dist" / "replays"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_replays_are_staged_and_pass(self) -> None:
        self.assertEqual(check(ROOT, self.site), [])
        self.assertTrue((self.replays / "index.json").is_file())
        self.assertTrue((self.replays / f"{GOBLIN}.trace.json").is_file())
        staged = {p.relative_to(self.site).as_posix() for p in self.site.rglob("*") if p.is_file()}
        self.assertFalse(any(name.startswith("examples/") for name in staged))

    def test_tampered_replay_fails(self) -> None:
        path = self.replays / f"{GOBLIN}.trace.json"
        path.write_bytes(path.read_bytes().replace(b'"winner": "party"', b'"winner": "enemy"', 1))
        self.assertIn(f"replay sha256 does not match index: {GOBLIN}.trace.json", check(ROOT, self.site))

    def test_unlisted_or_oversize_replay_fails(self) -> None:
        shutil.copy2(self.replays / f"{GOBLIN}.trace.json", self.replays / "sneaky.trace.json")
        errors = check(ROOT, self.site)
        self.assertIn("outside allowlist: dist/replays/sneaky.trace.json", errors)
        self.assertIn("replay staged but not in index: sneaky.trace.json", errors)
        (self.replays / "sneaky.trace.json").unlink()
        (self.replays / f"{GOBLIN}.trace.json").write_bytes(b"x" * (5 * 1024 * 1024 + 1))
        errors = check(ROOT, self.site)
        self.assertIn(f"over 5 MB: dist/replays/{GOBLIN}.trace.json", errors)

    def test_bad_replay_index_fails(self) -> None:
        index = self.replays / "index.json"
        data = json.loads(index.read_text(encoding="utf-8"))
        data["replays"][0]["file"] = "../../web/app.js"
        index.write_text(json.dumps(data), encoding="utf-8")
        errors = check(ROOT, self.site)
        self.assertTrue(any("bad file entry" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
