"""The Pages staging tree is an allowlist, and the local check enforces it."""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.site_files import assemble, check

ROOT = Path(__file__).resolve().parent.parent


class CheckSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "_site"
        assemble(ROOT, self.site)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_assembled_tree_passes_and_omits_private_files(self) -> None:
        self.assertEqual(check(ROOT, self.site), [])
        names = {p.relative_to(self.site).as_posix() for p in self.site.rglob("*") if p.is_file()}
        self.assertIn("web/index.html", names)
        self.assertIn("dist/manifest.json", names)
        self.assertIn("dist/web/graph.json", names)
        self.assertIn("bin/srd", names)
        self.assertIn("skill/SKILL.md", names)
        self.assertNotIn("dist/srd2024.sqlite", names)
        self.assertFalse(any(name.startswith("src/") for name in names))
        self.assertFalse(any(name.startswith("tests/") for name in names))
        self.assertFalse(any(name.startswith(".git/") for name in names))
        self.assertFalse(any(name.endswith("mock-fonts.css") for name in names))
        self.assertFalse(any("/INDEX.md" in f"/{name}" and name.startswith("dist/cards/") for name in names))
        script = subprocess.run(
            ["bash", str(ROOT / "scripts" / "check_site.sh"), str(self.site)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(script.returncode, 0, script.stderr)

    def test_file_outside_the_allowlist_fails(self) -> None:
        extra = self.site / "src" / "secret.py"
        extra.parent.mkdir()
        extra.write_text("nope\n", encoding="utf-8")
        errors = check(ROOT, self.site)
        self.assertTrue(any("outside allowlist: src/secret.py" in item for item in errors))

    def test_env_and_pem_files_fail(self) -> None:
        env = self.site / ".env"
        env.write_text("TOKEN=x\n", encoding="utf-8")
        pem = self.site / "web" / "id_rsa.pem"
        pem.write_text("-----BEGIN-----\n", encoding="utf-8")
        errors = "\n".join(check(ROOT, self.site))
        self.assertIn("sensitive file: .env", errors)
        self.assertIn("sensitive file: web/id_rsa.pem", errors)

    def test_over_5mb_fails(self) -> None:
        blob = self.site / "web" / "huge.js"
        blob.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
        errors = check(ROOT, self.site)
        self.assertTrue(any(item.startswith("over 5 MB:") for item in errors))

    def test_manifest_dataset_mismatch_fails(self) -> None:
        path = self.site / "dist" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["dataset_id"] = "not-the-dataset"
        manifest["counts"]["entities"] = 1
        path.write_text(json.dumps(manifest), encoding="utf-8")
        errors = "\n".join(check(ROOT, self.site))
        self.assertIn("dataset_id", errors)
        self.assertIn("counts.entities", errors)
        self.assertIn("does not match manifest", errors)

    def test_shell_script_is_executable(self) -> None:
        mode = (ROOT / "scripts" / "check_site.sh").stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
