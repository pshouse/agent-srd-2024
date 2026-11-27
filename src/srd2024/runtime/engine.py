"""Read-only SQLite query engine and resolved-bundle builder (SPEC §4.2, §8, §9.1)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import EntityRecord, open_readonly_connection, parse_meta_json, resolve_db_path
from .render import compute_bundle_estimated_tokens
from .text import (
    build_fts_match_expr,
    escape_like,
    est_tokens,
    normalize_name_key,
    normalize_search_text,
    slug_candidate,
    tier_for_type,
    tokenize_fts_query,
)

ANTI_HUB_TOKEN_THRESHOLD = 400
INBOUND_DEFAULT_LIMIT = 10
BUNDLE_HEADER_RESERVE_TOKENS = 40

_ENTITY_COLUMNS = (
    "rowid, id, type, slug, name, name_norm, api_url, summary, "
    "facets_json, raw_json, card_md, card_body_md, card_tokens_est"
)


class SRDStore:
    """Read-only query and resolved-bundle engine over `dist/srd2024.sqlite`."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path: Path = resolve_db_path(db_path)
        self._conn: sqlite3.Connection = open_readonly_connection(self.db_path)
        self._meta_cache: dict[str, str] | None = None
        self._valid_types_cache: list[str] | None = None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SRDStore:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Metadata & low-level entity loaders
    # ------------------------------------------------------------------

    def get_meta_map(self) -> dict[str, str]:
        if self._meta_cache is None:
            rows = self._conn.execute("SELECT key, value FROM meta ORDER BY key ASC").fetchall()
            self._meta_cache = {str(r["key"]): str(r["value"]) for r in rows}
        return dict(self._meta_cache)

    def get_resource_types(self) -> list[dict[str, Any]]:
        meta = self.get_meta_map()
        raw = meta.get("resource_types_json")
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        rows = self._conn.execute(
            "SELECT type, COUNT(*) AS cnt FROM entities GROUP BY type ORDER BY type ASC"
        ).fetchall()
        return [{"type": str(r["type"]), "count": int(r["cnt"]), "facet_keys": []} for r in rows]

    def get_valid_types(self) -> list[str]:
        if self._valid_types_cache is None:
            self._valid_types_cache = sorted(entry["type"] for entry in self.get_resource_types())
        return list(self._valid_types_cache)

    def get_provenance(self) -> dict[str, Any]:
        meta = self.get_meta_map()
        return {
            "dataset_id": meta.get("dataset_id", "srd-2024-5.2"),
            "license": meta.get("license", "CC-BY-4.0"),
            "license_url": meta.get(
                "license_url", "https://creativecommons.org/licenses/by/4.0/legalcode"
            ),
            "attribution": meta.get("attribution", ""),
            "upstream_repo": meta.get("upstream_repo", ""),
            "upstream_commit": meta.get("upstream_commit", ""),
            "upstream_database_version": meta.get("upstream_database_version", ""),
        }

    def get_card_md(self, entity_type: str, slug: str) -> str | None:
        row = self._conn.execute(
            "SELECT card_md FROM entities WHERE type = ? AND slug = ?",
            (entity_type.strip().lower(), slug.strip().lower()),
        ).fetchone()
        return str(row["card_md"]) if row else None

    def _fetch_entities_by_ids(self, ids: list[str]) -> dict[str, EntityRecord]:
        if not ids:
            return {}
        unique_ids = sorted(set(ids))
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()
        return {str(r["id"]): EntityRecord.from_row(r) for r in rows}

    # ------------------------------------------------------------------
    # §4.2 Runtime Identifier Resolution Order
    # ------------------------------------------------------------------

    def resolve_identifier(self, raw_query: str) -> tuple[str, EntityRecord | dict[str, Any]]:
        """Resolve an identifier strictly per SPEC §4.2.

        Returns:
            ("ok", EntityRecord) on exact single resolution, or
            ("ambiguous" | "not_found", structured_payload) when unresolved.
        """
        q = raw_query.strip()
        if not q:
            return ("not_found", {"status": "not_found", "query": q, "suggestions": []})

        q_lower = q.lower()

        # 1. Exact canonical ID (if ':' in q)
        if ":" in q:
            row = self._conn.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE id = ?",
                (q_lower,),
            ).fetchone()
            if row is not None:
                return ("ok", EntityRecord.from_row(row))

        # 2. Exact API URL (if q.startswith("/api/"))
        if q.startswith("/api/"):
            row = self._conn.execute(
                f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE api_url = ?",
                (q,),
            ).fetchone()
            if row is not None:
                return ("ok", EntityRecord.from_row(row))

        # 3. Bare slug: entities.slug = lower(q)
        slug_rows = self._conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE slug = ? ORDER BY id ASC",
            (q_lower,),
        ).fetchall()
        if len(slug_rows) == 1:
            return ("ok", EntityRecord.from_row(slug_rows[0]))
        if len(slug_rows) > 1:
            candidates = [EntityRecord.from_row(r).to_stub() for r in slug_rows]
            return (
                "ambiguous",
                {"status": "ambiguous", "query": q, "candidates": candidates},
            )

        # 4. Exact name match (case-insensitive, typographic punctuation normalized)
        norm_name = normalize_name_key(q)
        name_rows = self._conn.execute(
            f"SELECT {_ENTITY_COLUMNS} FROM entities WHERE name_norm = ? ORDER BY id ASC",
            (norm_name,),
        ).fetchall()
        if len(name_rows) == 1:
            return ("ok", EntityRecord.from_row(name_rows[0]))
        if len(name_rows) > 1:
            candidates = [EntityRecord.from_row(r).to_stub() for r in name_rows]
            return (
                "ambiguous",
                {"status": "ambiguous", "query": q, "candidates": candidates},
            )

        # 5. Not found: up to 5 suggestions from search
        search_res = self.search(q, limit=5)
        suggestions = search_res.get("results", [])[:5]
        return (
            "not_found",
            {"status": "not_found", "query": q, "suggestions": suggestions},
        )

    # ------------------------------------------------------------------
    # §9.1 #1: srd_search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        types: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Ranked FTS5 search with typographic normalization and LIKE fallback."""
        clamped_limit = max(1, min(50, int(limit)))
        clean_types = sorted({t.strip().lower() for t in (types or []) if t and t.strip()})
        q_norm = normalize_search_text(query).strip().lower()

        if not q_norm:
            return {
                "status": "ok",
                "query": query,
                "types": clean_types,
                "count": 0,
                "results": [],
            }

        type_clause = ""
        type_params: list[Any] = []
        if clean_types:
            placeholders = ",".join("?" for _ in clean_types)
            type_clause = f" AND e.type IN ({placeholders})"
            type_params = list(clean_types)

        tokens = tokenize_fts_query(q_norm)
        slug_q = slug_candidate(q_norm)
        escaped_q = escape_like(q_norm)
        escaped_prefix = escaped_q + "%"
        escaped_contains = f"%{escaped_q}%"
        escaped_slug_prefix = escape_like(slug_q) + "%" if slug_q else escaped_prefix

        rows: list[sqlite3.Row] = []

        if tokens:
            match_expr = build_fts_match_expr(tokens)
            sql = (
                "SELECT e.id, e.type, e.name, e.summary, "
                "bm25(entities_fts, 10.0, 4.0, 1.0) AS rank_score "
                "FROM entities_fts "
                "JOIN entities e ON e.rowid = entities_fts.rowid "
                f"WHERE entities_fts MATCH ?{type_clause} "
                "ORDER BY "
                "CASE "
                "  WHEN e.id = ? THEN 0 "
                "  WHEN e.slug = ? OR e.slug = ? THEN 1 "
                "  WHEN e.name_norm = ? THEN 2 "
                "  WHEN e.name_norm LIKE ? ESCAPE '\\' THEN 3 "
                "  WHEN e.slug LIKE ? ESCAPE '\\' THEN 4 "
                "  WHEN e.name_norm LIKE ? ESCAPE '\\' THEN 5 "
                "  ELSE 6 "
                "END ASC, "
                "rank_score ASC, "
                "e.id ASC "
                "LIMIT ?"
            )
            params: list[Any] = [
                match_expr,
                *type_params,
                q_norm,
                q_norm,
                slug_q,
                q_norm,
                escaped_prefix,
                escaped_slug_prefix,
                escaped_contains,
                clamped_limit,
            ]
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                rows = []

        # LIKE fallback if FTS5 yielded 0 hits or query had only punctuation
        if not rows:
            rows = self._search_like_fallback(
                q_norm=q_norm,
                slug_q=slug_q,
                tokens=tokens,
                type_clause=type_clause,
                type_params=type_params,
                limit=clamped_limit,
            )

        results = [
            {
                "id": str(r["id"]),
                "type": str(r["type"]),
                "name": str(r["name"]),
                "summary": str(r["summary"]),
            }
            for r in rows
        ]
        return {
            "status": "ok",
            "query": query,
            "types": clean_types,
            "count": len(results),
            "results": results,
        }

    def _search_like_fallback(
        self,
        q_norm: str,
        slug_q: str,
        tokens: list[str],
        type_clause: str,
        type_params: list[Any],
        limit: int,
    ) -> list[sqlite3.Row]:
        def _run_like(term: str) -> list[sqlite3.Row]:
            if not term:
                return []
            like_pat = f"%{escape_like(term)}%"
            prefix_pat = f"{escape_like(term)}%"
            sql = (
                "SELECT e.id, e.type, e.name, e.summary "
                "FROM entities e "
                "WHERE (e.name_norm LIKE ? ESCAPE '\\' "
                "   OR e.slug LIKE ? ESCAPE '\\' "
                "   OR lower(e.summary) LIKE ? ESCAPE '\\')"
                f"{type_clause} "
                "ORDER BY "
                "CASE "
                "  WHEN e.slug = ? THEN 0 "
                "  WHEN e.name_norm = ? THEN 1 "
                "  WHEN e.slug LIKE ? ESCAPE '\\' THEN 2 "
                "  WHEN e.name_norm LIKE ? ESCAPE '\\' THEN 3 "
                "  ELSE 4 "
                "END ASC, "
                "e.name_norm ASC, e.id ASC "
                "LIMIT ?"
            )
            params: list[Any] = [
                like_pat,
                like_pat,
                like_pat,
                *type_params,
                term,
                term,
                prefix_pat,
                prefix_pat,
                limit,
            ]
            return self._conn.execute(sql, params).fetchall()

        rows = _run_like(q_norm)
        if not rows and slug_q and slug_q != q_norm:
            rows = _run_like(slug_q)
        if not rows and len(tokens) >= 2:
            compact = "".join(tokens)
            if compact != q_norm:
                rows = _run_like(compact)
        return rows

    # ------------------------------------------------------------------
    # §8 & §9.1 #2: srd_get (Resolved-Bundle Query Engine)
    # ------------------------------------------------------------------

    def get_bundle(
        self,
        identifier: str,
        depth: int = 1,
        max_tokens: int = 2000,
        include_inbound: bool = False,
        rels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve an entity and pack its depth-N referenced rules under a token budget."""
        clamped_depth = max(0, min(2, int(depth)))
        clamped_max_tokens = max(200, min(16000, int(max_tokens)))
        clean_rels = sorted({r.strip() for r in (rels or []) if r and r.strip()})

        status, resolved = self.resolve_identifier(identifier)
        if status != "ok":
            assert isinstance(resolved, dict)
            return resolved
        assert isinstance(resolved, EntityRecord)
        root = resolved

        # Step 2: BFS outgoing expansion up to depth, recording shortest-hop edges
        min_hop: dict[str, int] = {root.id: 0}
        dep_rels_map: dict[str, set[str]] = {}
        dep_meta_raw_map: dict[str, set[str]] = {}

        frontier: list[str] = [root.id]
        for current_hop in range(clamped_depth):
            if not frontier:
                break
            placeholders = ",".join("?" for _ in frontier)
            sql = (
                "SELECT src_id, dst_id, rel, meta_json "
                f"FROM edges WHERE src_id IN ({placeholders})"
            )
            params: list[Any] = list(frontier)
            if clean_rels:
                rel_placeholders = ",".join("?" for _ in clean_rels)
                sql += f" AND rel IN ({rel_placeholders})"
                params.extend(clean_rels)
            sql += " ORDER BY src_id ASC, dst_id ASC, rel ASC"

            edge_rows = self._conn.execute(sql, params).fetchall()
            next_hop = current_hop + 1
            next_frontier_set: set[str] = set()

            for erow in edge_rows:
                dst_id = str(erow["dst_id"])
                if dst_id == root.id:
                    continue
                existing_hop = min_hop.get(dst_id)
                if existing_hop is not None and existing_hop < next_hop:
                    continue
                if existing_hop is None:
                    min_hop[dst_id] = next_hop
                    next_frontier_set.add(dst_id)
                dep_rels_map.setdefault(dst_id, set()).add(str(erow["rel"]))
                mraw = erow["meta_json"]
                if mraw is not None and mraw != "":
                    dep_meta_raw_map.setdefault(dst_id, set()).add(str(mraw))

            frontier = sorted(next_frontier_set)

        dep_ids = [eid for eid in min_hop if eid != root.id]
        dep_records = self._fetch_entities_by_ids(dep_ids)

        # Step 3: Priority sorting by (hop ASC, tier ASC, id ASC)
        sorted_dep_ids = sorted(
            dep_records.keys(),
            key=lambda eid: (min_hop[eid], tier_for_type(dep_records[eid].type), eid),
        )

        # Step 4: Token-budgeted packing
        root_tokens = est_tokens(root.card_body_md)
        budget_exceeded = root_tokens > clamped_max_tokens
        budget_left = max(0, clamped_max_tokens - root_tokens - BUNDLE_HEADER_RESERVE_TOKENS)

        packed_deps: list[dict[str, Any]] = []
        omitted_ids: list[str] = []
        truncated = False
        full_count = 0
        summary_count = 0

        for dep_id in sorted_dep_ids:
            dep = dep_records[dep_id]
            dep_tier = tier_for_type(dep.type)
            dep_rels = sorted(dep_rels_map.get(dep_id, set()))
            rels_str = ", ".join(dep_rels)
            edge_meta = self._collect_edge_meta(dep_meta_raw_map.get(dep_id, set()))

            full_block = f"### {dep.name} (`{dep.id}`) — via {rels_str}\n\n{dep.card_body_md}"
            stub_block = f"- **{dep.name}** (`{dep.id}`, via {rels_str}): {dep.summary}"
            full_tokens = est_tokens(full_block)
            stub_tokens = est_tokens(stub_block)

            # Anti-hub guard (§8.2 step 4)
            eligible_full = (full_tokens <= budget_left) and (
                dep_tier == 1 or dep.card_tokens_est <= ANTI_HUB_TOKEN_THRESHOLD
            )

            if eligible_full:
                budget_left -= full_tokens
                full_count += 1
                packed_deps.append(
                    {
                        "id": dep.id,
                        "type": dep.type,
                        "name": dep.name,
                        "hop": min_hop[dep_id],
                        "rels": dep_rels,
                        "edge_meta": edge_meta,
                        "tier": dep_tier,
                        "detail": "full",
                        "summary": dep.summary,
                        "card_body_md": dep.card_body_md,
                        "tokens_est": full_tokens,
                    }
                )
            elif stub_tokens <= budget_left:
                budget_left -= stub_tokens
                summary_count += 1
                truncated = True
                packed_deps.append(
                    {
                        "id": dep.id,
                        "type": dep.type,
                        "name": dep.name,
                        "hop": min_hop[dep_id],
                        "rels": dep_rels,
                        "edge_meta": edge_meta,
                        "tier": dep_tier,
                        "detail": "summary",
                        "summary": dep.summary,
                        "card_body_md": None,
                        "tokens_est": stub_tokens,
                    }
                )
            else:
                omitted_ids.append(dep.id)
                truncated = True

        # Optional inbound references (up to 10 compact stubs while budget allows)
        inbound_items: list[dict[str, Any]] = []
        inbound_total = 0
        if include_inbound:
            in_rows = self._conn.execute(
                "SELECT e.src_id, e.rel, e.meta_json, s.type, s.name, s.summary "
                "FROM edges e "
                "JOIN entities s ON s.id = e.src_id "
                "WHERE e.dst_id = ? "
                "ORDER BY e.rel ASC, e.src_id ASC",
                (root.id,),
            ).fetchall()
            inbound_total = len(in_rows)
            if budget_left > 0:
                for irow in in_rows[:INBOUND_DEFAULT_LIMIT]:
                    src_id = str(irow["src_id"])
                    rel = str(irow["rel"])
                    src_name = str(irow["name"])
                    src_summary = str(irow["summary"])
                    in_stub = f"- **{src_name}** (`{src_id}`, via {rel}): {src_summary}"
                    in_tokens = est_tokens(in_stub)
                    if in_tokens <= budget_left:
                        budget_left -= in_tokens
                        inbound_items.append(
                            {
                                "id": src_id,
                                "type": str(irow["type"]),
                                "name": src_name,
                                "rel": rel,
                                "meta": parse_meta_json(irow["meta_json"]),
                                "summary": src_summary,
                            }
                        )
                    else:
                        break

        estimated_tokens = compute_bundle_estimated_tokens(
            root_id=root.id,
            root_body_md=root.card_body_md,
            dependencies=packed_deps,
            inbound=inbound_items,
            include_inbound=include_inbound,
            inbound_total=inbound_total,
            depth=clamped_depth,
            max_tokens=clamped_max_tokens,
            full_count=full_count,
            summary_count=summary_count,
            omitted_count=len(omitted_ids),
        )

        return {
            "status": "ok",
            "include_inbound": include_inbound,
            "root": {
                "id": root.id,
                "type": root.type,
                "slug": root.slug,
                "name": root.name,
                "summary": root.summary,
                "facets": root.facets,
                "card_body_md": root.card_body_md,
                "tokens_est": root_tokens,
            },
            "dependencies": packed_deps,
            "inbound": inbound_items,
            "stats": {
                "depth": clamped_depth,
                "max_tokens": clamped_max_tokens,
                "estimated_tokens": estimated_tokens,
                "budget_exceeded": budget_exceeded,
                "truncated": truncated,
                "full_count": full_count,
                "summary_count": summary_count,
                "omitted_ids": omitted_ids,
                "inbound_total": inbound_total,
            },
        }

    @staticmethod
    def _collect_edge_meta(raw_metas: set[str]) -> list[Any]:
        if not raw_metas:
            return []
        canonical_items: dict[str, Any] = {}
        for raw in sorted(raw_metas):
            parsed = json.loads(raw)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                key = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                canonical_items[key] = item
        return [canonical_items[k] for k in sorted(canonical_items.keys())]

    # ------------------------------------------------------------------
    # §9.1 #3: srd_list
    # ------------------------------------------------------------------

    def list_entities(
        self,
        entity_type: str | None = None,
        filters: dict[str, str | list[str]] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return the 25-type catalog (if type is None) or facet-filtered entities."""
        if entity_type is None or not str(entity_type).strip():
            meta = self.get_meta_map()
            resource_types = self.get_resource_types()
            return {
                "status": "ok",
                "mode": "catalog",
                "meta": {
                    "dataset_id": meta.get("dataset_id", "srd-2024-5.2"),
                    "license": meta.get("license", "CC-BY-4.0"),
                    "license_url": meta.get(
                        "license_url", "https://creativecommons.org/licenses/by/4.0/legalcode"
                    ),
                    "attribution": meta.get("attribution", ""),
                    "entity_count": int(meta.get("entity_count", "0")),
                    "edge_count": int(meta.get("edge_count", "0")),
                },
                "resource_types": resource_types,
            }

        clean_type = str(entity_type).strip().lower()
        valid_types = self.get_valid_types()
        if clean_type not in valid_types:
            return {
                "status": "error",
                "error": f"Unknown resource type: '{entity_type}'",
                "valid_types": valid_types,
            }

        clamped_limit = max(1, min(200, int(limit)))
        clamped_offset = max(0, int(offset))

        norm_filters: dict[str, list[str]] = {}
        if filters:
            for k, v in filters.items():
                key = str(k).strip().lower()
                if not key:
                    continue
                if isinstance(v, (list, tuple, set)):
                    vals = sorted({str(item).strip().lower() for item in v if str(item).strip()})
                else:
                    val_str = str(v).strip().lower()
                    vals = [val_str] if val_str else []
                if vals:
                    existing = set(norm_filters.get(key, []))
                    existing.update(vals)
                    norm_filters[key] = sorted(existing)

        where_clauses = ["e.type = ?"]
        params: list[Any] = [clean_type]

        # AND across distinct keys, OR across multiple values for the same key
        for key, vals in sorted(norm_filters.items()):
            placeholders = ",".join("?" for _ in vals)
            where_clauses.append(
                "EXISTS ("
                "SELECT 1 FROM entity_facets ef "
                f"WHERE ef.entity_id = e.id AND ef.key = ? AND ef.value IN ({placeholders})"
                ")"
            )
            params.append(key)
            params.extend(vals)

        where_sql = " AND ".join(where_clauses)
        total_row = self._conn.execute(
            f"SELECT COUNT(*) AS cnt FROM entities e WHERE {where_sql}",
            params,
        ).fetchone()
        total = int(total_row["cnt"]) if total_row else 0

        rows = self._conn.execute(
            f"SELECT e.id, e.name, e.summary FROM entities e "
            f"WHERE {where_sql} "
            "ORDER BY e.name_norm ASC, e.id ASC "
            "LIMIT ? OFFSET ?",
            [*params, clamped_limit, clamped_offset],
        ).fetchall()

        results = [
            {"id": str(r["id"]), "name": str(r["name"]), "summary": str(r["summary"])}
            for r in rows
        ]
        return {
            "status": "ok",
            "mode": "entities",
            "type": clean_type,
            "filters": norm_filters,
            "total": total,
            "offset": clamped_offset,
            "limit": clamped_limit,
            "count": len(results),
            "results": results,
        }

    # ------------------------------------------------------------------
    # §9.1 #4: srd_graph
    # ------------------------------------------------------------------

    def graph(
        self,
        identifier: str,
        direction: str = "both",
        depth: int = 1,
        rel: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Inspect outgoing and/or inbound cross-reference neighbors."""
        clean_dir = direction.strip().lower()
        if clean_dir not in ("out", "in", "both"):
            return {
                "status": "error",
                "error": f"Invalid direction '{direction}'. Expected one of: out, in, both.",
            }

        clamped_depth = max(1, min(2, int(depth)))
        clamped_limit = max(1, min(100, int(limit)))
        clean_rel = rel.strip() if rel and rel.strip() else None

        status, resolved = self.resolve_identifier(identifier)
        if status != "ok":
            assert isinstance(resolved, dict)
            return resolved
        assert isinstance(resolved, EntityRecord)
        root = resolved

        neighbors: list[dict[str, Any]] = []

        def _expand(dir_name: str) -> None:
            visited: set[str] = {root.id}
            frontier: list[str] = [root.id]
            seen_edges: set[tuple[str, int, str, str]] = set()

            for current_hop in range(clamped_depth):
                if not frontier:
                    break
                next_hop = current_hop + 1
                placeholders = ",".join("?" for _ in frontier)
                params: list[Any] = list(frontier)

                if dir_name == "out":
                    sql = (
                        "SELECT e.dst_id AS nbr_id, e.rel, e.meta_json, "
                        "n.type, n.name, n.summary "
                        "FROM edges e "
                        "JOIN entities n ON n.id = e.dst_id "
                        f"WHERE e.src_id IN ({placeholders})"
                    )
                else:
                    sql = (
                        "SELECT e.src_id AS nbr_id, e.rel, e.meta_json, "
                        "n.type, n.name, n.summary "
                        "FROM edges e "
                        "JOIN entities n ON n.id = e.src_id "
                        f"WHERE e.dst_id IN ({placeholders})"
                    )

                if clean_rel is not None:
                    sql += " AND e.rel = ?"
                    params.append(clean_rel)

                sql += " ORDER BY e.rel ASC, nbr_id ASC"
                rows = self._conn.execute(sql, params).fetchall()

                next_frontier: set[str] = set()
                for r in rows:
                    nbr_id = str(r["nbr_id"])
                    if nbr_id == root.id:
                        continue
                    edge_rel = str(r["rel"])
                    edge_key = (dir_name, next_hop, edge_rel, nbr_id)
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        neighbors.append(
                            {
                                "direction": dir_name,
                                "hop": next_hop,
                                "rel": edge_rel,
                                "meta": parse_meta_json(r["meta_json"]),
                                "id": nbr_id,
                                "type": str(r["type"]),
                                "name": str(r["name"]),
                                "summary": str(r["summary"]),
                            }
                        )
                    if nbr_id not in visited:
                        visited.add(nbr_id)
                        next_frontier.add(nbr_id)

                frontier = sorted(next_frontier)

        if clean_dir in ("out", "both"):
            _expand("out")
        if clean_dir in ("in", "both"):
            _expand("in")

        neighbors.sort(
            key=lambda item: (
                0 if item["direction"] == "out" else 1,
                int(item["hop"]),
                str(item["rel"]),
                str(item["id"]),
            )
        )

        total = len(neighbors)
        capped = neighbors[:clamped_limit]
        return {
            "status": "ok",
            "root": root.to_stub(),
            "direction": clean_dir,
            "depth": clamped_depth,
            "rel": clean_rel,
            "limit": clamped_limit,
            "total": total,
            "truncated": total > clamped_limit,
            "neighbors": capped,
        }

    # ------------------------------------------------------------------
    # §9.1 #5: srd_validate
    # ------------------------------------------------------------------

    def validate(
        self,
        ids: list[str],
        check_links: bool = True,
    ) -> dict[str, Any]:
        """Batch-resolve identifiers, find inter-entity links, advisories, and provenance."""
        if not ids:
            return {
                "status": "error",
                "error": "At least 1 identifier is required for srd_validate.",
            }
        if len(ids) > 50:
            return {
                "status": "error",
                "error": f"Too many identifiers ({len(ids)}); maximum is 50.",
            }

        valid: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        resolved_records: dict[str, EntityRecord] = {}

        for raw_id in ids:
            status, res = self.resolve_identifier(str(raw_id))
            if status == "ok":
                assert isinstance(res, EntityRecord)
                resolved_records[res.id] = res
                valid.append(
                    {
                        "query": str(raw_id).strip(),
                        "id": res.id,
                        "type": res.type,
                        "name": res.name,
                        "summary": res.summary,
                    }
                )
            elif status == "ambiguous":
                assert isinstance(res, dict)
                ambiguous.append(
                    {
                        "query": res["query"],
                        "candidates": res["candidates"],
                    }
                )
            else:
                assert isinstance(res, dict)
                invalid.append(
                    {
                        "query": res["query"],
                        "suggestions": res["suggestions"],
                    }
                )

        links: list[dict[str, Any]] = []
        advisories: list[dict[str, Any]] = []
        valid_id_set = set(resolved_records.keys())

        if check_links and valid_id_set:
            if len(valid_id_set) >= 2:
                sorted_ids = sorted(valid_id_set)
                placeholders = ",".join("?" for _ in sorted_ids)
                link_rows = self._conn.execute(
                    "SELECT src_id, dst_id, rel, meta_json "
                    "FROM edges "
                    f"WHERE src_id IN ({placeholders}) AND dst_id IN ({placeholders}) "
                    "ORDER BY src_id ASC, dst_id ASC, rel ASC",
                    [*sorted_ids, *sorted_ids],
                ).fetchall()
                links = [
                    {
                        "src_id": str(r["src_id"]),
                        "dst_id": str(r["dst_id"]),
                        "rel": str(r["rel"]),
                        "meta": parse_meta_json(r["meta_json"]),
                    }
                    for r in link_rows
                ]

            advisories = self._collect_validation_advisories(resolved_records, valid_id_set)

        return {
            "status": "ok",
            "all_valid": (len(ambiguous) == 0 and len(invalid) == 0),
            "valid": valid,
            "ambiguous": ambiguous,
            "invalid": invalid,
            "links": links,
            "advisories": advisories,
            "provenance": self.get_provenance(),
        }

    def _collect_validation_advisories(
        self,
        records: dict[str, EntityRecord],
        valid_id_set: set[str],
    ) -> list[dict[str, Any]]:
        advisories: list[dict[str, Any]] = []

        for eid in sorted(records.keys()):
            ent = records[eid]

            # Subclass -> parent class check
            if ent.type == "subclasses":
                parent = self._find_single_parent(ent.id, "classes")
                if parent and parent[0] not in valid_id_set:
                    advisories.append(
                        {
                            "entity_id": ent.id,
                            "kind": "missing_parent_class",
                            "expected_id": parent[0],
                            "message": (
                                f"Subclass '{ent.name}' ({ent.id}) belongs to class "
                                f"'{parent[1]}' ({parent[0]}), which is not in the validated set."
                            ),
                        }
                    )

            # Subspecies -> parent species check
            elif ent.type == "subspecies":
                parent = self._find_single_parent(ent.id, "species")
                if parent and parent[0] not in valid_id_set:
                    advisories.append(
                        {
                            "entity_id": ent.id,
                            "kind": "missing_parent_species",
                            "expected_id": parent[0],
                            "message": (
                                f"Subspecies '{ent.name}' ({ent.id}) belongs to species "
                                f"'{parent[1]}' ({parent[0]}), which is not in the validated set."
                            ),
                        }
                    )

            # Features & Levels -> parent class / subclass check
            elif ent.type in ("features", "levels"):
                parent_class = self._find_single_parent(ent.id, "classes")
                if parent_class and parent_class[0] not in valid_id_set:
                    advisories.append(
                        {
                            "entity_id": ent.id,
                            "kind": "missing_parent_class",
                            "expected_id": parent_class[0],
                            "message": (
                                f"{ent.type[:-1].title()} '{ent.name}' ({ent.id}) belongs to class "
                                f"'{parent_class[1]}' ({parent_class[0]}), which is not in the validated set."
                            ),
                        }
                    )
                parent_sub = self._find_single_parent(ent.id, "subclasses")
                if parent_sub and parent_sub[0] not in valid_id_set:
                    advisories.append(
                        {
                            "entity_id": ent.id,
                            "kind": "missing_parent_subclass",
                            "expected_id": parent_sub[0],
                            "message": (
                                f"{ent.type[:-1].title()} '{ent.name}' ({ent.id}) belongs to subclass "
                                f"'{parent_sub[1]}' ({parent_sub[0]}), which is not in the validated set."
                            ),
                        }
                    )

            # Feats -> prerequisites / prerequisite_options check
            elif ent.type == "feats":
                req_text = self._format_feat_prerequisites(ent.raw_json)
                if req_text:
                    advisories.append(
                        {
                            "entity_id": ent.id,
                            "kind": "feat_prerequisite",
                            "requirement": req_text,
                            "message": (
                                f"Feat '{ent.name}' ({ent.id}) has prerequisites: {req_text}."
                            ),
                        }
                    )

        return advisories

    def _find_single_parent(self, src_id: str, target_type: str) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT d.id, d.name "
            "FROM edges e "
            "JOIN entities d ON d.id = e.dst_id "
            "WHERE e.src_id = ? AND d.type = ? "
            "ORDER BY d.id ASC LIMIT 1",
            (src_id, target_type),
        ).fetchone()
        if row is None:
            return None
        return (str(row["id"]), str(row["name"]))

    @staticmethod
    def _format_feat_prerequisites(raw_json_str: str) -> str:
        try:
            raw = json.loads(raw_json_str)
        except json.JSONDecodeError:
            return ""
        parts: list[str] = []

        prereqs = raw.get("prerequisites")
        if isinstance(prereqs, dict):
            if "minimum_level" in prereqs:
                parts.append(f"Level {prereqs['minimum_level']}+")
            if "feature_named" in prereqs:
                parts.append(f"Feature '{prereqs['feature_named']}'")
            for k, v in sorted(prereqs.items()):
                if k not in ("minimum_level", "feature_named"):
                    parts.append(f"{k}: {v}")
        elif isinstance(prereqs, list) and prereqs:
            parts.append(json.dumps(prereqs, ensure_ascii=False))

        prereq_opts = raw.get("prerequisite_options")
        if isinstance(prereq_opts, dict):
            desc = prereq_opts.get("desc")
            if isinstance(desc, str) and desc.strip():
                parts.append(desc.strip())
            else:
                opts = (prereq_opts.get("from") or {}).get("options") or []
                opt_strs: list[str] = []
                for opt in opts:
                    if isinstance(opt, dict):
                        ab = (opt.get("ability_score") or {}).get("name")
                        ms = opt.get("minimum_score")
                        if ab and ms is not None:
                            opt_strs.append(f"{ab} {ms}+")
                if opt_strs:
                    parts.append(" or ".join(opt_strs))

        return "; ".join(parts)
