"""Offline deterministic compiler for 5e 2024 (SRD 5.2) pack (SPEC §4-§7, §10-§11)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cards import build_summary, render_card_body, render_cards_index, render_full_card
from .edges import DanglingRefRow, EdgeRow, extract_graph
from .facets import TYPE_TIERS, extract_facets
from .text import est_tokens, normalize_search_text, strip_markdown

SCHEMA_VERSION = "1"
DATASET_ID = "srd-2024-5.2"

# Official SRD 5.2 CC-BY-4.0 notice. Single source of truth for every emitted
# attribution (manifest.json, SQLite meta, dist/cards/INDEX.md). The snapshot
# string in data/raw/PROVENANCE.json is not this text: it uses straight quotes
# and omits the comma before the second "available".
SRD_ATTRIBUTION = (
    "This work includes material from the System Reference Document 5.2 "
    "(“SRD 5.2”) by Wizards of the Coast LLC, available at "
    "https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the "
    "Creative Commons Attribution 4.0 International License, available at "
    "https://creativecommons.org/licenses/by/4.0/legalcode."
)

_SCHEMA_SQL = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE entities (
    rowid           INTEGER PRIMARY KEY,
    id              TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,
    slug            TEXT NOT NULL,
    name            TEXT NOT NULL,
    name_norm       TEXT NOT NULL,
    api_url         TEXT NOT NULL UNIQUE,
    summary         TEXT NOT NULL,
    facets_json     TEXT NOT NULL,
    raw_json        TEXT NOT NULL,
    card_md         TEXT NOT NULL,
    card_body_md    TEXT NOT NULL,
    card_tokens_est INTEGER NOT NULL
);

CREATE UNIQUE INDEX idx_entities_type_slug ON entities(type, slug);
CREATE INDEX idx_entities_type ON entities(type);
CREATE INDEX idx_entities_slug ON entities(slug);
CREATE INDEX idx_entities_name_norm ON entities(name_norm);

CREATE TABLE entity_facets (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (entity_id, key, value)
);

CREATE INDEX idx_facets_kv ON entity_facets(key, value, entity_id);

CREATE TABLE edges (
    src_id      TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dst_id      TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel         TEXT NOT NULL,
    json_path   TEXT NOT NULL,
    meta_json   TEXT,
    PRIMARY KEY (src_id, dst_id, rel)
);

CREATE INDEX idx_edges_src ON edges(src_id, rel);
CREATE INDEX idx_edges_dst ON edges(dst_id, rel);

CREATE TABLE dangling_refs (
    src_id      TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_raw  TEXT NOT NULL,
    rel         TEXT NOT NULL,
    json_path   TEXT NOT NULL,
    PRIMARY KEY (src_id, target_raw, rel, json_path)
);

CREATE VIRTUAL TABLE entities_fts USING fts5(
    name,
    summary,
    body,
    content='',
    tokenize='porter unicode61'
);
"""


@dataclass
class CompiledEntity:
    rowid: int
    id: str
    type: str
    slug: str
    name: str
    name_norm: str
    api_url: str
    summary: str
    facets: dict[str, list[str]]
    facets_json: str
    raw_json: str
    card_md: str
    card_body_md: str
    card_tokens_est: int
    refs: list[str]
    tier: int


def _compact_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _pretty_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _write_text_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Normalize strictly to LF line endings
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(normalized.encode("utf-8"))


def _resolve_raw_paths(raw_dir: Path) -> tuple[Path, Path]:
    """Locate 2024/en directory and PROVENANCE.json from --raw-dir."""
    if (raw_dir / "2024" / "en").is_dir():
        en_dir = raw_dir / "2024" / "en"
        prov_path = raw_dir / "PROVENANCE.json"
    else:
        en_dir = raw_dir
        prov_path = raw_dir.parent.parent / "PROVENANCE.json"
        if not prov_path.is_file():
            prov_path = raw_dir / "PROVENANCE.json"
    if not en_dir.is_dir():
        raise FileNotFoundError(f"Raw JSON directory not found: {en_dir}")
    if not prov_path.is_file():
        raise FileNotFoundError(f"PROVENANCE.json not found near: {raw_dir}")
    return en_dir, prov_path


def _load_raw_dataset(
    en_dir: Path,
) -> tuple[
    list[tuple[str, str, dict[str, Any]]],
    dict[str, str],
    dict[str, dict[str, Any]],
    int,
    str,
]:
    """Load all 25 5e-SRD-*.json files and compute deterministic source_hash."""
    files = sorted(en_dir.glob("5e-SRD-*.json"), key=lambda p: p.name)
    if not files:
        raise FileNotFoundError(f"No 5e-SRD-*.json files found in {en_dir}")

    hasher = hashlib.sha256()
    raw_records: list[tuple[str, str, dict[str, Any]]] = []
    url_to_id: dict[str, str] = {}
    levels_by_url: dict[str, dict[str, Any]] = {}

    for path in files:
        raw_bytes = path.read_bytes()
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(raw_bytes)
        hasher.update(b"\0")

        rtype = path.stem.removeprefix("5e-SRD-").lower()
        if rtype not in TYPE_TIERS:
            raise ValueError(f"Unexpected resource type {rtype!r} from {path.name}")

        records = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(records, list):
            raise ValueError(f"Expected JSON array in {path.name}")

        for rec in records:
            slug = rec["index"]
            eid = f"{rtype}:{slug}"
            url = rec["url"]
            if url in url_to_id:
                raise ValueError(f"Duplicate entity URL {url!r}: {url_to_id[url]} vs {eid}")
            url_to_id[url] = eid
            raw_records.append((rtype, eid, rec))
            if rtype == "levels":
                levels_by_url[url] = rec

    # Sort deterministically by canonical id ASC
    raw_records.sort(key=lambda item: item[1])
    return raw_records, url_to_id, levels_by_url, len(files), hasher.hexdigest()


def _write_sqlite(
    db_path: Path,
    meta_rows: list[tuple[str, str]],
    entities: list[CompiledEntity],
    facet_rows: list[tuple[str, str, str]],
    edges: list[EdgeRow],
    dangling: list[DanglingRefRow],
) -> None:
    tmp_path = db_path.with_suffix(".sqlite.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = sqlite3.connect(str(tmp_path))
    try:
        conn.execute("PRAGMA journal_mode=DELETE;")
        conn.execute("PRAGMA synchronous=FULL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(_SCHEMA_SQL)

        with conn:
            conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                sorted(meta_rows, key=lambda kv: kv[0]),
            )
            conn.executemany(
                """
                INSERT INTO entities (
                    rowid, id, type, slug, name, name_norm, api_url,
                    summary, facets_json, raw_json, card_md, card_body_md, card_tokens_est
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e.rowid,
                        e.id,
                        e.type,
                        e.slug,
                        e.name,
                        e.name_norm,
                        e.api_url,
                        e.summary,
                        e.facets_json,
                        e.raw_json,
                        e.card_md,
                        e.card_body_md,
                        e.card_tokens_est,
                    )
                    for e in entities
                ],
            )
            conn.executemany(
                "INSERT INTO entity_facets (entity_id, key, value) VALUES (?, ?, ?)",
                facet_rows,
            )
            conn.executemany(
                """
                INSERT INTO edges (src_id, dst_id, rel, json_path, meta_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (ed.src_id, ed.dst_id, ed.rel, ed.json_path, ed.meta_json)
                    for ed in edges
                ],
            )
            if dangling:
                conn.executemany(
                    """
                    INSERT INTO dangling_refs (src_id, target_raw, rel, json_path)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(d.src_id, d.target_raw, d.rel, d.json_path) for d in dangling],
                )
            conn.executemany(
                "INSERT INTO entities_fts (rowid, name, summary, body) VALUES (?, ?, ?, ?)",
                [
                    (
                        e.rowid,
                        normalize_search_text(e.name),
                        normalize_search_text(e.summary),
                        f"{normalize_search_text(strip_markdown(e.card_body_md))} {e.type} {e.slug}",
                    )
                    for e in entities
                ],
            )

        fk_errors = conn.execute("PRAGMA foreign_key_check;").fetchall()
        if fk_errors:
            raise RuntimeError(f"SQLite foreign_key_check failed: {fk_errors[:5]}")

        conn.execute("ANALYZE;")
        conn.execute("VACUUM;")
    finally:
        conn.close()

    os.replace(tmp_path, db_path)


def build_all(raw_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Compile raw 2024 SRD JSON into deterministic SQLite, cards, web bundle, and manifest."""
    en_dir, prov_path = _resolve_raw_paths(raw_dir)
    provenance = json.loads(prov_path.read_text(encoding="utf-8"))

    raw_records, url_to_id, levels_by_url, file_count, source_hash = _load_raw_dataset(en_dir)
    edges, dangling = extract_graph(raw_records, url_to_id)

    # Precompute outgoing refs & degrees
    out_refs_map: dict[str, set[str]] = {}
    out_deg: dict[str, int] = {}
    in_deg: dict[str, int] = {}
    for ed in edges:
        out_refs_map.setdefault(ed.src_id, set()).add(ed.dst_id)
        out_deg[ed.src_id] = out_deg.get(ed.src_id, 0) + 1
        in_deg[ed.dst_id] = in_deg.get(ed.dst_id, 0) + 1

    # Compile all entities in deterministic id ASC order
    entities: list[CompiledEntity] = []
    facet_rows: list[tuple[str, str, str]] = []
    type_counts: dict[str, int] = {}
    type_facet_keys: dict[str, set[str]] = {t: set() for t in TYPE_TIERS}

    for rowid, (rtype, eid, rec) in enumerate(raw_records, start=1):
        slug = rec["index"]
        name = rec["name"]
        api_url = rec["url"]
        name_norm = normalize_search_text(name).lower()
        facets = extract_facets(rtype, rec, levels_by_url=levels_by_url)
        for fk, vals in facets.items():
            type_facet_keys[rtype].add(fk)
            for fv in vals:
                facet_rows.append((eid, fk, fv))

        summary = build_summary(rtype, rec)
        refs = sorted(out_refs_map.get(eid, ()))
        card_body_md = render_card_body(rtype, eid, rec, url_to_id)
        card_md = render_full_card(eid, rtype, name, summary, refs, card_body_md)
        tokens = est_tokens(card_body_md)

        type_counts[rtype] = type_counts.get(rtype, 0) + 1
        entities.append(
            CompiledEntity(
                rowid=rowid,
                id=eid,
                type=rtype,
                slug=slug,
                name=name,
                name_norm=name_norm,
                api_url=api_url,
                summary=summary,
                facets=facets,
                facets_json=_compact_json(facets),
                raw_json=_compact_json(rec),
                card_md=card_md,
                card_body_md=card_body_md,
                card_tokens_est=tokens,
                refs=refs,
                tier=TYPE_TIERS[rtype],
            )
        )

    facet_rows.sort()

    resource_types = [
        {
            "type": rtype,
            "count": type_counts[rtype],
            "facet_keys": sorted(type_facet_keys[rtype]),
        }
        for rtype in sorted(type_counts.keys())
    ]

    content_license = provenance["content_license"]
    attribution = SRD_ATTRIBUTION
    upstream_repo = provenance["upstream_repo"]
    upstream_commit = provenance["upstream_commit"]
    upstream_db_ver = provenance["upstream_database_version"]

    meta_rows = [
        ("schema_version", SCHEMA_VERSION),
        ("dataset_id", DATASET_ID),
        ("upstream_repo", upstream_repo),
        ("upstream_commit", upstream_commit),
        ("upstream_database_version", upstream_db_ver),
        ("license", content_license["name"]),
        ("license_url", content_license["url"]),
        ("attribution", attribution),
        ("entity_count", str(len(entities))),
        ("edge_count", str(len(edges))),
        ("dangling_ref_count", str(len(dangling))),
        ("resource_types_json", _compact_json(resource_types)),
        ("source_hash", source_hash),
    ]

    # Prepare clean output directories
    out_dir.mkdir(parents=True, exist_ok=True)
    cards_dir = out_dir / "cards"
    web_dir = out_dir / "web"
    web_types_dir = web_dir / "types"

    if cards_dir.exists():
        shutil.rmtree(cards_dir)
    if web_dir.exists():
        shutil.rmtree(web_dir)

    cards_dir.mkdir(parents=True, exist_ok=True)
    web_types_dir.mkdir(parents=True, exist_ok=True)

    # 1. SQLite database (§5)
    db_path = out_dir / "srd2024.sqlite"
    _write_sqlite(db_path, meta_rows, entities, facet_rows, edges, dangling)

    # 2. Markdown cards & INDEX.md (§7)
    index_md = render_cards_index(resource_types, len(entities), attribution)
    _write_text_lf(cards_dir / "INDEX.md", index_md)
    for e in entities:
        _write_text_lf(cards_dir / e.type / f"{e.slug}.md", e.card_md)

    # 3. Frontend static JSON bundle (§10)
    web_index = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "entities": [
            {
                "id": e.id,
                "type": e.type,
                "slug": e.slug,
                "name": e.name,
                "summary": e.summary,
                "facets": e.facets,
                "tier": e.tier,
                "card_tokens_est": e.card_tokens_est,
                "out_deg": out_deg.get(e.id, 0),
                "in_deg": in_deg.get(e.id, 0),
                "card_path": f"cards/{e.type}/{e.slug}.md",
            }
            for e in entities
        ],
    }
    _write_text_lf(web_dir / "index.json", _compact_json(web_index) + "\n")

    web_graph = {
        "schema_version": SCHEMA_VERSION,
        "nodes": {
            e.id: {
                "type": e.type,
                "name": e.name,
                "tier": e.tier,
            }
            for e in entities
        },
        "edges": [
            [ed.src_id, ed.dst_id, ed.rel, ed.meta_obj]
            for ed in edges
        ],
    }
    _write_text_lf(web_dir / "graph.json", _compact_json(web_graph) + "\n")

    shards_by_type: dict[str, dict[str, Any]] = {t: {} for t in sorted(type_counts.keys())}
    for e in entities:
        shards_by_type[e.type][e.id] = {
            "id": e.id,
            "type": e.type,
            "slug": e.slug,
            "name": e.name,
            "summary": e.summary,
            "facets": e.facets,
            "card_body_md": e.card_body_md,
            "card_tokens_est": e.card_tokens_est,
            "refs": e.refs,
        }
    for rtype, shard in sorted(shards_by_type.items()):
        _write_text_lf(web_types_dir / f"{rtype}.json", _compact_json(shard) + "\n")

    # 4. Build manifest (§11)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "upstream": {
            "repo": upstream_repo,
            "commit": upstream_commit,
            "database_version": upstream_db_ver,
        },
        "license": {
            "content": content_license["name"],
            "content_url": content_license["url"],
            "attribution": attribution,
            "packaging": provenance["dataset_packaging_license"]["name"],
        },
        "artifacts": {
            "sqlite": "srd2024.sqlite",
            "cards_index": "cards/INDEX.md",
            "web_index": "web/index.json",
            "web_graph": "web/graph.json",
            "web_types_dir": "web/types",
        },
        "source_hash": source_hash,
        "counts": {
            "files": file_count,
            "entities": len(entities),
            "facets": len(facet_rows),
            "edges": len(edges),
            "dangling_refs": len(dangling),
            "cards": len(entities),
            "web_type_shards": len(shards_by_type),
        },
        "resource_types": resource_types,
    }
    _write_text_lf(out_dir / "manifest.json", _pretty_json(manifest))

    # Stage 2 post-build hook: emit rules.json & web/rules-coverage.json without altering Stage 1 artifacts
    from srd2024.rules_compiler.build import build_rules

    build_rules(raw_dir, out_dir)
    return manifest
