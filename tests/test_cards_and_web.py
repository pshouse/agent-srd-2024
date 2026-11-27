"""Verification tests for Criteria 4 and 5 (SPEC §13).

Covers:
4. Full-corpus card quality & YAML validity across all 2,029 cards:
   - valid YAML frontmatter (PyYAML + structural checks),
   - byte-identical to entities.card_md,
   - zero leaked /api/2024/ or /api/images/ URLs,
   - all 44 magic-items Markdown pipe tables intact,
   - all relative links resolve to existing files in dist/cards/.
5. Frontend static bundle contract (dist/manifest.json, dist/web/index.json,
   dist/web/graph.json, all 25 dist/web/types/<type>.json shards match SQLite 1:1).
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.helpers import (
    CARDS_DIR,
    EXPECTED_ENTITY_COUNT,
    EXPECTED_TIERS,
    EXPECTED_TYPE_COUNT,
    EXPECTED_TYPE_COUNTS,
    MANIFEST_PATH,
    RAW_EN_DIR,
    WEB_DIR,
    open_db_ro,
    split_frontmatter,
)

REL_LINK_RE = re.compile(r"\[[^\]]+\]\((\.\./[^)\s]+)\)")
RAW_API_URL_RE = re.compile(r"/api/(?:2024|2014|images)/|/api \+2/")


class TestCardQuality(unittest.TestCase):
    """Criterion 4: Full-corpus Markdown card quality & YAML validity."""

    def test_all_2029_cards_valid_yaml_links_and_clean_bodies(self) -> None:
        with open_db_ro() as conn:
            entities = conn.execute(
                "SELECT id, type, slug, name, summary, card_md, card_body_md FROM entities ORDER BY id"
            ).fetchall()
            self.assertEqual(len(entities), EXPECTED_ENTITY_COUNT)

            edges_by_src: dict[str, set[str]] = {}
            for row in conn.execute("SELECT src_id, dst_id FROM edges"):
                edges_by_src.setdefault(row["src_id"], set()).add(row["dst_id"])

        yaml_errors: list[str] = []
        leaked_urls: list[str] = []
        broken_links: list[str] = []
        mismatched_disk: list[str] = []
        refs_mismatches: list[str] = []

        for ent in entities:
            eid = ent["id"]
            etype = ent["type"]
            slug = ent["slug"]
            card_path = CARDS_DIR / etype / f"{slug}.md"

            if not card_path.is_file():
                mismatched_disk.append(f"missing:{eid}")
                continue

            disk_text = card_path.read_text(encoding="utf-8")
            if disk_text != ent["card_md"]:
                mismatched_disk.append(f"diff:{eid}")
            if "\r\n" in disk_text:
                mismatched_disk.append(f"crlf:{eid}")

            try:
                fm, body = split_frontmatter(disk_text)
            except Exception as exc:
                yaml_errors.append(f"{eid}: {exc}")
                continue

            if fm.get("id") != eid or fm.get("type") != etype or fm.get("name") != ent["name"]:
                yaml_errors.append(f"{eid}: frontmatter id/type/name mismatch")
            if fm.get("summary") != ent["summary"]:
                yaml_errors.append(f"{eid}: frontmatter summary mismatch")

            expected_refs = sorted(edges_by_src.get(eid, set()))
            actual_refs = fm.get("refs", [])
            if actual_refs != expected_refs:
                refs_mismatches.append(f"{eid}: expected {len(expected_refs)} refs, got {len(actual_refs)}")

            if body != ent["card_body_md"]:
                yaml_errors.append(f"{eid}: parsed body != entities.card_body_md")

            if RAW_API_URL_RE.search(body):
                leaked_urls.append(eid)

            # Check every relative markdown link resolves to an existing card file
            for match in REL_LINK_RE.finditer(body):
                rel_target = match.group(1)
                resolved = (card_path.parent / rel_target).resolve()
                if not resolved.is_file() or not str(resolved).startswith(str(CARDS_DIR.resolve())):
                    broken_links.append(f"{eid} -> {rel_target}")

        self.assertEqual(mismatched_disk, [], f"Card disk/DB mismatches: {mismatched_disk[:10]}")
        self.assertEqual(yaml_errors, [], f"YAML frontmatter errors: {yaml_errors[:10]}")
        self.assertEqual(refs_mismatches, [], f"Frontmatter refs mismatches: {refs_mismatches[:10]}")
        self.assertEqual(leaked_urls, [], f"Raw /api/ URLs leaked into card bodies: {leaked_urls[:10]}")
        self.assertEqual(broken_links, [], f"Broken relative links in cards: {broken_links[:10]}")

    def test_all_44_magic_items_pipe_tables_intact(self) -> None:
        raw_magic_items = json.loads((RAW_EN_DIR / "5e-SRD-Magic-Items.json").read_text(encoding="utf-8"))
        items_with_tables: list[tuple[str, int]] = []
        for item in raw_magic_items:
            raw_pipe_lines = [line.strip() for line in item["desc"] if line.strip().startswith("|")]
            if raw_pipe_lines:
                items_with_tables.append((item["index"], len(raw_pipe_lines)))

        self.assertEqual(len(items_with_tables), 44, "Expected 44 magic items with pipe tables in raw data")

        broken_tables: list[str] = []
        for slug, expected_pipe_lines in items_with_tables:
            card_path = CARDS_DIR / "magic-items" / f"{slug}.md"
            text = card_path.read_text(encoding="utf-8")
            _, body = split_frontmatter(text)
            lines = body.splitlines()
            pipe_indices = [i for i, ln in enumerate(lines) if ln.strip().startswith("|")]
            if len(pipe_indices) < expected_pipe_lines:
                broken_tables.append(f"{slug}: expected >= {expected_pipe_lines} pipe rows, got {len(pipe_indices)}")
                continue
            # Ensure consecutive pipe table rows weren't torn apart by blank lines
            # Check that every table header separator row has a pipe row directly above and below it
            sep_indices = [i for i in pipe_indices if re.match(r"^\|[\s:|-]+\|$", lines[i].strip())]
            if not sep_indices:
                broken_tables.append(f"{slug}: missing markdown table separator row")
                continue
            for si in sep_indices:
                if si == 0 or not lines[si - 1].strip().startswith("|"):
                    broken_tables.append(f"{slug}: separator at line {si} detached from header row")
                if si + 1 >= len(lines) or not lines[si + 1].strip().startswith("|"):
                    broken_tables.append(f"{slug}: separator at line {si} detached from first data row")

        self.assertEqual(broken_tables, [], f"Broken magic-item pipe tables: {broken_tables[:10]}")

    def test_known_audit_quirks_in_cards(self) -> None:
        # 1. Entertainer's Pack omits weight without crashing
        ep_text = (CARDS_DIR / "equipment" / "entertainers-pack.md").read_text(encoding="utf-8")
        _, ep_body = split_frontmatter(ep_text)
        self.assertIn("Entertainer", ep_body)
        self.assertNotIn("None", ep_body)

        # 2. armor-2 omits corrupted image URL
        a2_text = (CARDS_DIR / "magic-items" / "armor-2.md").read_text(encoding="utf-8")
        self.assertNotIn("/api +2/", a2_text)

        # 3. Subclasses render inline features
        berserker_text = (CARDS_DIR / "subclasses" / "path-of-the-berserker.md").read_text(encoding="utf-8")
        _, berserker_body = split_frontmatter(berserker_text)
        self.assertIn("Frenzy", berserker_body)

        # 4. cards/INDEX.md lists all 25 types
        index_md = (CARDS_DIR / "INDEX.md").read_text(encoding="utf-8")
        for etype in EXPECTED_TYPE_COUNTS:
            self.assertIn(etype, index_md)


class TestFrontendWebBundleContract(unittest.TestCase):
    """Criterion 5: Frontend static bundle contract (SPEC §10 & §11)."""

    def test_manifest_json_contract(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "1")
        self.assertEqual(manifest["dataset_id"], "srd-2024-5.2")
        self.assertEqual(manifest["artifacts"]["sqlite"], "srd2024.sqlite")
        self.assertEqual(manifest["artifacts"]["cards_index"], "cards/INDEX.md")
        self.assertEqual(manifest["artifacts"]["web_index"], "web/index.json")
        self.assertEqual(manifest["artifacts"]["web_graph"], "web/graph.json")
        self.assertEqual(manifest["artifacts"]["web_types_dir"], "web/types")

        with open_db_ro() as conn:
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            facet_count = conn.execute("SELECT COUNT(*) FROM entity_facets").fetchone()[0]
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())

        counts = manifest["counts"]
        self.assertEqual(counts["files"], EXPECTED_TYPE_COUNT)
        self.assertEqual(counts["entities"], EXPECTED_ENTITY_COUNT)
        self.assertEqual(counts["facets"], facet_count)
        self.assertEqual(counts["edges"], edge_count)
        self.assertEqual(counts["dangling_refs"], 0)
        self.assertEqual(counts["cards"], EXPECTED_ENTITY_COUNT)
        self.assertEqual(counts["web_type_shards"], EXPECTED_TYPE_COUNT)
        self.assertEqual(manifest["source_hash"], meta["source_hash"])
        self.assertEqual(len(manifest["resource_types"]), EXPECTED_TYPE_COUNT)

    def test_web_index_json_matches_sqlite_1_to_1(self) -> None:
        web_index = json.loads((WEB_DIR / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(web_index["schema_version"], "1")
        self.assertEqual(web_index["dataset_id"], "srd-2024-5.2")
        entries = web_index["entities"]
        self.assertEqual(len(entries), EXPECTED_ENTITY_COUNT)

        ids = [e["id"] for e in entries]
        self.assertEqual(ids, sorted(ids), "dist/web/index.json entities must be sorted by id ASC")

        with open_db_ro() as conn:
            db_entities = {
                r["id"]: r
                for r in conn.execute(
                    "SELECT id, type, slug, name, summary, facets_json, card_tokens_est FROM entities"
                )
            }
            out_deg = dict(conn.execute("SELECT src_id, COUNT(*) FROM edges GROUP BY src_id").fetchall())
            in_deg = dict(conn.execute("SELECT dst_id, COUNT(*) FROM edges GROUP BY dst_id").fetchall())

        for entry in entries:
            eid = entry["id"]
            self.assertIn(eid, db_entities)
            row = db_entities[eid]
            self.assertEqual(entry["type"], row["type"])
            self.assertEqual(entry["slug"], row["slug"])
            self.assertEqual(entry["name"], row["name"])
            self.assertEqual(entry["summary"], row["summary"])
            self.assertEqual(entry["facets"], json.loads(row["facets_json"]))
            self.assertEqual(entry["tier"], EXPECTED_TIERS[row["type"]])
            self.assertEqual(entry["card_tokens_est"], row["card_tokens_est"])
            self.assertEqual(entry["out_deg"], out_deg.get(eid, 0))
            self.assertEqual(entry["in_deg"], in_deg.get(eid, 0))
            self.assertEqual(entry["card_path"], f"cards/{row['type']}/{row['slug']}.md")
            self.assertNotIn("card_body_md", entry)
            self.assertNotIn("raw_json", entry)

    def test_web_graph_json_matches_sqlite_1_to_1(self) -> None:
        web_graph = json.loads((WEB_DIR / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(web_graph["schema_version"], "1")
        nodes = web_graph["nodes"]
        edges = web_graph["edges"]
        self.assertEqual(len(nodes), EXPECTED_ENTITY_COUNT)

        with open_db_ro() as conn:
            db_nodes = {
                r["id"]: {"type": r["type"], "name": r["name"], "tier": EXPECTED_TIERS[r["type"]]}
                for r in conn.execute("SELECT id, type, name FROM entities")
            }
            db_edges = [
                [
                    r["src_id"],
                    r["dst_id"],
                    r["rel"],
                    json.loads(r["meta_json"]) if r["meta_json"] is not None else None,
                ]
                for r in conn.execute(
                    "SELECT src_id, dst_id, rel, meta_json FROM edges ORDER BY src_id, dst_id, rel"
                )
            ]

        self.assertEqual(nodes, db_nodes)
        self.assertEqual(len(edges), len(db_edges))
        self.assertEqual(edges, db_edges)

    def test_web_type_shards_match_sqlite_1_to_1(self) -> None:
        with open_db_ro() as conn:
            db_entities = {
                r["id"]: r
                for r in conn.execute(
                    "SELECT id, type, slug, name, summary, facets_json, card_body_md, card_tokens_est FROM entities"
                )
            }
            refs_by_src: dict[str, set[str]] = {}
            for r in conn.execute("SELECT src_id, dst_id FROM edges"):
                refs_by_src.setdefault(r["src_id"], set()).add(r["dst_id"])

        seen_ids: set[str] = set()
        for etype, expected_cnt in EXPECTED_TYPE_COUNTS.items():
            shard_path = WEB_DIR / "types" / f"{etype}.json"
            self.assertTrue(shard_path.is_file(), f"Missing shard {shard_path}")
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            self.assertEqual(len(shard), expected_cnt, f"Shard count mismatch for {etype}")
            self.assertEqual(list(shard.keys()), sorted(shard.keys()), f"Unsorted keys in {shard_path.name}")

            for eid, item in shard.items():
                seen_ids.add(eid)
                row = db_entities[eid]
                self.assertEqual(item["id"], row["id"])
                self.assertEqual(item["type"], row["type"])
                self.assertEqual(item["slug"], row["slug"])
                self.assertEqual(item["name"], row["name"])
                self.assertEqual(item["summary"], row["summary"])
                self.assertEqual(item["facets"], json.loads(row["facets_json"]))
                self.assertEqual(item["card_body_md"], row["card_body_md"])
                self.assertEqual(item["card_tokens_est"], row["card_tokens_est"])
                self.assertEqual(item["refs"], sorted(refs_by_src.get(eid, set())))
                self.assertNotIn("raw_json", item)

        self.assertEqual(len(seen_ids), EXPECTED_ENTITY_COUNT)


if __name__ == "__main__":
    unittest.main()
