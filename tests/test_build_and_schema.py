"""Verification tests for Criteria 1, 2, 3, and 10 (SPEC §13).

Covers:
1. Build & completeness (2,029 entities, 25 types, all artifacts present).
2. Determinism (temp-dir rebuild byte-identical for manifest, cards, web, counts, source_hash).
3. Graph & schema integrity (FK check, 0 dangling refs, 0 self-edges, >=7,300 edges,
   meta_json preserved, no /api/images/ or REST collection edges, armor-2 clean).
10. CC-BY-4.0 SRD 5.2 attribution & skill/SKILL.md quality.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from srd2024.compiler.build import SRD_ATTRIBUTION
from tests.helpers import (
    BIN_BUILD,
    BIN_MCP,
    BIN_SRD,
    CARDS_DIR,
    DB_PATH,
    DIST_DIR,
    EXPECTED_ENTITY_COUNT,
    EXPECTED_REQUIRED_META_KEYS,
    EXPECTED_TYPE_COUNT,
    EXPECTED_TYPE_COUNTS,
    MANIFEST_PATH,
    PYPROJECT_PATH,
    RAW_EN_DIR,
    README_PATH,
    REPO_ROOT,
    SKILL_PATH,
    WEB_DIR,
    detect_build_flags,
    load_provenance,
    open_db_ro,
    split_frontmatter,
)


def _compute_expected_source_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(RAW_EN_DIR.glob("*.json"), key=lambda x: x.name):
        h.update(p.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(p.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()


class TestBuildAndCompleteness(unittest.TestCase):
    """Criterion 1: Build & completeness."""

    def test_executables_and_packaging_present(self) -> None:
        self.assertTrue(PYPROJECT_PATH.is_file(), "pyproject.toml missing")
        pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")
        for script_entry in (
            'srd-build = "srd2024.compiler.__main__:main"',
            'srd = "srd2024.runtime.cli:main"',
            'srd-mcp = "srd2024.runtime.mcp:main"',
        ):
            self.assertIn(script_entry, pyproject_text)

        for binary in (BIN_BUILD, BIN_SRD, BIN_MCP):
            self.assertTrue(binary.is_file(), f"Missing executable: {binary}")
            mode = binary.stat().st_mode
            self.assertTrue(bool(mode & stat.S_IXUSR), f"Not executable (+x): {binary}")
            first_line = binary.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("python3", first_line, f"Missing python3 shebang in {binary}")

    def test_all_dist_artifacts_exist(self) -> None:
        self.assertTrue(DB_PATH.is_file(), "dist/srd2024.sqlite missing")
        self.assertTrue(MANIFEST_PATH.is_file(), "dist/manifest.json missing")
        self.assertTrue((CARDS_DIR / "INDEX.md").is_file(), "dist/cards/INDEX.md missing")
        self.assertTrue((WEB_DIR / "index.json").is_file(), "dist/web/index.json missing")
        self.assertTrue((WEB_DIR / "graph.json").is_file(), "dist/web/graph.json missing")
        types_dir = WEB_DIR / "types"
        self.assertTrue(types_dir.is_dir(), "dist/web/types/ missing")

        shard_files = sorted(p.name for p in types_dir.glob("*.json"))
        expected_shards = sorted(f"{t}.json" for t in EXPECTED_TYPE_COUNTS)
        self.assertEqual(shard_files, expected_shards)

        card_files = [
            p for p in CARDS_DIR.rglob("*.md")
            if p != CARDS_DIR / "INDEX.md"
        ]
        self.assertEqual(len(card_files), EXPECTED_ENTITY_COUNT)

    def test_sqlite_entity_counts_and_meta(self) -> None:
        prov = load_provenance()
        with open_db_ro() as conn:
            total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            self.assertEqual(total, EXPECTED_ENTITY_COUNT)

            by_type = {
                row["type"]: row["cnt"]
                for row in conn.execute("SELECT type, COUNT(*) AS cnt FROM entities GROUP BY type")
            }
            self.assertEqual(len(by_type), EXPECTED_TYPE_COUNT)
            self.assertEqual(by_type, EXPECTED_TYPE_COUNTS)

            meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
            missing_meta = EXPECTED_REQUIRED_META_KEYS - set(meta)
            self.assertEqual(missing_meta, set(), f"Missing meta keys: {missing_meta}")
            self.assertEqual(meta["schema_version"], "1")
            self.assertEqual(meta["dataset_id"], "srd-2024-5.2")
            self.assertEqual(meta["upstream_repo"], prov["upstream_repo"])
            self.assertEqual(meta["upstream_commit"], prov["upstream_commit"])
            self.assertEqual(meta["upstream_database_version"], prov["upstream_database_version"])
            self.assertEqual(meta["license"], "CC-BY-4.0")
            self.assertEqual(meta["license_url"], prov["content_license"]["url"])
            self.assertEqual(meta["attribution"], SRD_ATTRIBUTION)
            self.assertEqual(meta["entity_count"], str(EXPECTED_ENTITY_COUNT))
            self.assertEqual(meta["dangling_ref_count"], "0")
            self.assertEqual(len(meta["source_hash"]), 64)
            int(meta["source_hash"], 16)  # valid hex

            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            self.assertEqual(meta["edge_count"], str(edge_count))

            resource_types = json.loads(meta["resource_types_json"])
            self.assertEqual(len(resource_types), EXPECTED_TYPE_COUNT)
            self.assertEqual([r["type"] for r in resource_types], sorted(EXPECTED_TYPE_COUNTS))
            for rt in resource_types:
                self.assertEqual(rt["count"], EXPECTED_TYPE_COUNTS[rt["type"]])
                self.assertIsInstance(rt["facet_keys"], list)
                self.assertEqual(rt["facet_keys"], sorted(rt["facet_keys"]))


class TestDeterminism(unittest.TestCase):
    """Criterion 2: Determinism across non-destructive temp-dir rebuilds."""

    def test_temp_dir_rebuild_is_byte_identical(self) -> None:
        out_flags, in_flags = detect_build_flags()
        with tempfile.TemporaryDirectory(prefix="srd2024-determinism-") as tmp:
            tmp_dist = Path(tmp) / "dist"
            cmd = [str(BIN_BUILD), *out_flags, str(tmp_dist)]
            if in_flags:
                # Pass data/raw or data/raw/2024/en depending on help semantics; default works too
                pass
            proc = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"Temp rebuild failed:\nCMD: {cmd}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )

            # 1. Byte-identical manifest.json
            self.assertEqual(
                (tmp_dist / "manifest.json").read_bytes(),
                MANIFEST_PATH.read_bytes(),
                "Rebuilt manifest.json differs from dist/manifest.json",
            )

            # 2. Byte-identical dist/cards/**
            orig_cards = sorted(p.relative_to(CARDS_DIR) for p in CARDS_DIR.rglob("*.md"))
            rebuilt_cards = sorted(p.relative_to(tmp_dist / "cards") for p in (tmp_dist / "cards").rglob("*.md"))
            self.assertEqual(rebuilt_cards, orig_cards)
            mismatched_cards: list[str] = []
            for rel in orig_cards:
                if (tmp_dist / "cards" / rel).read_bytes() != (CARDS_DIR / rel).read_bytes():
                    mismatched_cards.append(str(rel))
            self.assertEqual(mismatched_cards, [], f"Non-deterministic cards: {mismatched_cards[:10]}")

            # 3. Byte-identical dist/web/**
            orig_web = sorted(p.relative_to(WEB_DIR) for p in WEB_DIR.rglob("*.json"))
            rebuilt_web = sorted(p.relative_to(tmp_dist / "web") for p in (tmp_dist / "web").rglob("*.json"))
            self.assertEqual(rebuilt_web, orig_web)
            mismatched_web: list[str] = []
            for rel in orig_web:
                if (tmp_dist / "web" / rel).read_bytes() != (WEB_DIR / rel).read_bytes():
                    mismatched_web.append(str(rel))
            self.assertEqual(mismatched_web, [], f"Non-deterministic web files: {mismatched_web}")

            # 4. Matching SQLite counts, meta, and source_hash
            with open_db_ro(DB_PATH) as orig_db, open_db_ro(tmp_dist / "srd2024.sqlite") as new_db:
                orig_meta = dict(orig_db.execute("SELECT key, value FROM meta").fetchall())
                new_meta = dict(new_db.execute("SELECT key, value FROM meta").fetchall())
                self.assertEqual(new_meta, orig_meta)
                for table in ("entities", "entity_facets", "edges", "dangling_refs"):
                    orig_n = orig_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    new_n = new_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    self.assertEqual(new_n, orig_n, f"Row count mismatch in {table}")


class TestGraphAndSchemaIntegrity(unittest.TestCase):
    """Criterion 3: Graph & SQLite schema integrity."""

    def test_foreign_keys_and_zero_dangling_and_zero_self_edges(self) -> None:
        with open_db_ro() as conn:
            fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_errors, [], f"Foreign key violations: {fk_errors[:5]}")

            dangling = conn.execute("SELECT COUNT(*) FROM dangling_refs").fetchone()[0]
            self.assertEqual(dangling, 0, "Expected 0 dangling_refs")

            self_edges = conn.execute("SELECT COUNT(*) FROM edges WHERE src_id = dst_id").fetchone()[0]
            self.assertEqual(self_edges, 0, "Expected 0 self-edges")

            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            self.assertGreaterEqual(edge_count, 7300, f"Expected >= 7300 edges, got {edge_count}")

    def test_edge_relations_and_metadata_preservation(self) -> None:
        with open_db_ro() as conn:
            # All rel names must be clean non-empty snake_case
            rels = [r[0] for r in conn.execute("SELECT DISTINCT rel FROM edges").fetchall()]
            self.assertGreater(len(rels), 10)
            for rel in rels:
                self.assertRegex(rel, r"^[a-z][a-z0-9_]*$", f"Non-snake_case rel: {rel!r}")

            # Inline metadata preserved on monster action spells
            monster_spell_meta = conn.execute(
                """
                SELECT COUNT(*) FROM edges
                WHERE src_id LIKE 'monsters:%'
                  AND dst_id LIKE 'spells:%'
                  AND meta_json IS NOT NULL
                """
            ).fetchone()[0]
            self.assertGreater(
                monster_spell_meta,
                200,
                f"Expected inline meta_json preserved on monster spell edges, got {monster_spell_meta}",
            )

            # Inline metadata preserved on subspecies.traits (level metadata)
            subsp_trait_meta = conn.execute(
                """
                SELECT COUNT(*) FROM edges
                WHERE src_id LIKE 'subspecies:%'
                  AND dst_id LIKE 'traits:%'
                  AND meta_json IS NOT NULL
                """
            ).fetchone()[0]
            self.assertGreater(
                subsp_trait_meta,
                40,
                f"Expected inline meta_json preserved on subspecies->traits edges, got {subsp_trait_meta}",
            )

            # Validate every non-NULL meta_json is valid JSON
            for row in conn.execute("SELECT src_id, dst_id, rel, meta_json FROM edges WHERE meta_json IS NOT NULL"):
                parsed = json.loads(row["meta_json"])
                self.assertIsInstance(parsed, (dict, list))

            # Zero edges pointing to /api/images/ or REST collection paths
            bad_dst = conn.execute(
                """
                SELECT e.src_id, e.dst_id, e.json_path
                FROM edges e
                WHERE e.dst_id LIKE '%image%'
                  AND e.dst_id NOT IN (SELECT id FROM entities)
                """
            ).fetchall()
            self.assertEqual(bad_dst, [])

            bad_paths = conn.execute(
                """
                SELECT src_id, dst_id, json_path FROM edges
                WHERE json_path = 'image'
                   OR json_path = 'class_levels'
                   OR (src_id LIKE 'classes:%' AND json_path = 'spells')
                """
            ).fetchall()
            self.assertEqual(bad_paths, [], f"Non-entity bare strings leaked into edges: {bad_paths}")

    def test_armor_2_clean_and_facets_and_fts_alignment(self) -> None:
        with open_db_ro() as conn:
            armor2 = conn.execute(
                "SELECT id, name, summary, card_md, card_body_md, card_tokens_est FROM entities WHERE id = ?",
                ("magic-items:armor-2",),
            ).fetchone()
            self.assertIsNotNone(armor2, "magic-items:armor-2 missing from entities")
            self.assertNotIn("/api +2/", armor2["card_md"])
            self.assertNotIn("/api/images/", armor2["card_md"])

            # Verify entity_facets lowercase & token estimate formula across all entities
            bad_case = conn.execute(
                "SELECT COUNT(*) FROM entity_facets WHERE key != lower(key) OR value != lower(value)"
            ).fetchone()[0]
            self.assertEqual(bad_case, 0, "entity_facets must store lowercased key and value")

            rows = conn.execute(
                "SELECT rowid, id, name, name_norm, summary, card_body_md, card_tokens_est FROM entities"
            ).fetchall()
            self.assertEqual(len(rows), EXPECTED_ENTITY_COUNT)
            for row in rows:
                expected_tok = math.ceil(len(row["card_body_md"]) / 4.0)
                self.assertEqual(
                    row["card_tokens_est"],
                    expected_tok,
                    f"card_tokens_est mismatch for {row['id']}",
                )
                self.assertTrue(0 < len(row["summary"]) <= 200, f"Invalid summary length for {row['id']}")
                self.assertNotIn("\n", row["summary"], f"Newline in summary for {row['id']}")

            # Verify FTS5 1:1 rowid alignment (querying via rowid match)
            fts_hit = conn.execute(
                """
                SELECT e.id FROM entities_fts
                JOIN entities e ON e.rowid = entities_fts.rowid
                WHERE entities_fts MATCH 'longsword'
                """
            ).fetchall()
            self.assertIn("equipment:longsword", {r["id"] for r in fts_hit})

    def test_runtime_never_imports_compiler(self) -> None:
        runtime_dir = REPO_ROOT / "src" / "srd2024" / "runtime"
        self.assertTrue(runtime_dir.is_dir(), "src/srd2024/runtime missing")
        for py_file in runtime_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            self.assertNotIn("srd2024.compiler", content, f"{py_file} imports compiler (violates §3)")
            self.assertNotIn("from ..compiler", content, f"{py_file} imports compiler (violates §3)")


class TestAttributionAndSkill(unittest.TestCase):
    """Criterion 10: CC-BY-4.0 SRD 5.2 attribution & skill/SKILL.md quality."""

    def test_manifest_attribution_equals_official_statement(self) -> None:
        """manifest.json attribution is the official SRD 5.2 statement."""
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["license"]["attribution"], SRD_ATTRIBUTION)

    def test_cc_by_4_attribution_in_all_required_locations(self) -> None:
        expected_attr = SRD_ATTRIBUTION

        # 1. SQLite meta
        with open_db_ro() as conn:
            meta_attr = conn.execute("SELECT value FROM meta WHERE key = 'attribution'").fetchone()
            self.assertIsNotNone(meta_attr)
            self.assertEqual(meta_attr[0], expected_attr)

        # 2. dist/manifest.json
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["license"]["content"], "CC-BY-4.0")
        self.assertEqual(manifest["license"]["attribution"], expected_attr)

        # 3. dist/cards/INDEX.md
        index_md = (CARDS_DIR / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn(expected_attr, index_md)

        # 4. README.md
        self.assertTrue(README_PATH.is_file(), "README.md missing")
        readme_md = README_PATH.read_text(encoding="utf-8")
        self.assertIn(expected_attr, readme_md)

        # 5. skill/SKILL.md
        self.assertIn(expected_attr, SKILL_PATH.read_text(encoding="utf-8"))

    def test_skill_md_quality(self) -> None:
        self.assertTrue(SKILL_PATH.is_file(), "skill/SKILL.md missing")
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        fm, body = split_frontmatter(skill_text)
        self.assertEqual(fm.get("name"), "srd-2024")
        self.assertTrue(fm.get("description"), "skill/SKILL.md missing frontmatter description")
        for expected_tool in ("srd_search", "srd_get", "srd_list", "srd_graph", "srd_validate"):
            self.assertIn(expected_tool, body, f"skill/SKILL.md should document {expected_tool}")
        self.assertIn("dist/cards/INDEX.md", body)


if __name__ == "__main__":
    unittest.main()
