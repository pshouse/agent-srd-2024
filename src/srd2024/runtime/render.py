"""Deterministic Markdown and JSON renderers for all 5 SRD operations."""

from __future__ import annotations

import json
from typing import Any

from .text import est_tokens


def render_json(payload: dict[str, Any]) -> str:
    """Render any operation payload as formatted UTF-8 JSON."""
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_unresolved(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    query = payload.get("query", "")
    if status == "ambiguous":
        lines = [
            f"# Ambiguous Identifier: `{query}`",
            "",
            "Multiple SRD entities match this identifier. Re-query using one of these canonical IDs:",
            "",
        ]
        for cand in payload.get("candidates", []):
            lines.append(
                f"- **{cand['name']}** (`{cand['id']}`, {cand['type']}): {cand['summary']}"
            )
        return "\n".join(lines)

    if status == "not_found":
        lines = [f"# Entity Not Found: `{query}`"]
        suggestions = payload.get("suggestions", [])
        if suggestions:
            lines.extend(["", "Did you mean one of these?", ""])
            for sug in suggestions:
                lines.append(
                    f"- **{sug['name']}** (`{sug['id']}`, {sug['type']}): {sug['summary']}"
                )
        else:
            lines.extend(["", "No matching entities found."])
        return "\n".join(lines)

    if status == "error":
        lines = [f"**Error:** {payload.get('error', 'Unknown error')}"]
        valid_types = payload.get("valid_types")
        if valid_types:
            lines.extend(["", f"Valid types: {', '.join(f'`{t}`' for t in valid_types)}"])
        return "\n".join(lines)

    return render_json(payload)


def render_search(payload: dict[str, Any]) -> str:
    """Render `srd_search` payload as compact Markdown stubs."""
    if payload.get("status") != "ok":
        return _render_unresolved(payload)

    query = payload.get("query", "")
    results = payload.get("results", [])
    types = payload.get("types") or []
    scope = f" (types: {', '.join(types)})" if types else ""
    lines = [f"## Search: `{query}`{scope} — {len(results)} result(s)"]
    if not results:
        lines.extend(["", "No matching entities found."])
        return "\n".join(lines)

    lines.append("")
    for item in results:
        lines.append(f"- **{item['name']}** (`{item['id']}`): {item['summary']}")
    return "\n".join(lines)


def build_bundle_markdown_parts(
    root_body_md: str,
    dependencies: list[dict[str, Any]],
    inbound: list[dict[str, Any]],
    include_inbound: bool,
    inbound_total: int,
) -> str:
    """Build the Markdown body of a resolved bundle (excluding the trailing footer)."""
    sections: list[str] = [root_body_md.rstrip()]

    full_deps = [d for d in dependencies if d["detail"] == "full"]
    summary_deps = [d for d in dependencies if d["detail"] == "summary"]

    if full_deps or summary_deps:
        ref_parts: list[str] = ["## Referenced Rules"]
        for dep in full_deps:
            rels_str = ", ".join(dep["rels"])
            heading = f"### {dep['name']} (`{dep['id']}`) — via {rels_str}"
            body = (dep.get("card_body_md") or "").rstrip()
            ref_parts.append(f"{heading}\n\n{body}")
        if summary_deps:
            sum_lines = ["### Summarized References"]
            for dep in summary_deps:
                rels_str = ", ".join(dep["rels"])
                sum_lines.append(
                    f"- **{dep['name']}** (`{dep['id']}`, via {rels_str}): {dep['summary']}"
                )
            ref_parts.append("\n".join(sum_lines))
        sections.append("\n\n".join(ref_parts))

    if include_inbound:
        if inbound:
            header = f"## Referenced By ({len(inbound)} of {inbound_total})"
            in_lines = [header]
            for item in inbound:
                in_lines.append(
                    f"- **{item['name']}** (`{item['id']}`, via {item['rel']}): {item['summary']}"
                )
            sections.append("\n".join(in_lines))
        else:
            sections.append("## Referenced By\n\n*(none)*")

    return "\n\n".join(sections)


def format_bundle_footer(
    root_id: str,
    depth: int,
    estimated_tokens: int,
    max_tokens: int,
    full_count: int,
    summary_count: int,
    omitted_count: int,
) -> str:
    """Format the 1-line HTML comment footer required by SPEC §8.2."""
    return (
        f"<!-- bundle: id={root_id} depth={depth} "
        f"tokens~={estimated_tokens}/{max_tokens} "
        f"full={full_count} summary={summary_count} omitted={omitted_count} -->"
    )


def render_bundle(payload: dict[str, Any]) -> str:
    """Render `srd_get` resolved bundle payload as Markdown with bundle footer."""
    if payload.get("status") != "ok":
        return _render_unresolved(payload)

    root = payload["root"]
    dependencies = payload.get("dependencies", [])
    inbound = payload.get("inbound", [])
    stats = payload["stats"]
    include_inbound = bool(payload.get("include_inbound", False))
    inbound_total = int(stats.get("inbound_total", len(inbound)))

    body = build_bundle_markdown_parts(
        root_body_md=root["card_body_md"],
        dependencies=dependencies,
        inbound=inbound,
        include_inbound=include_inbound,
        inbound_total=inbound_total,
    )
    footer = format_bundle_footer(
        root_id=root["id"],
        depth=int(stats["depth"]),
        estimated_tokens=int(stats["estimated_tokens"]),
        max_tokens=int(stats["max_tokens"]),
        full_count=int(stats["full_count"]),
        summary_count=int(stats["summary_count"]),
        omitted_count=len(stats.get("omitted_ids", [])),
    )
    return f"{body}\n\n{footer}"


def compute_bundle_estimated_tokens(
    root_id: str,
    root_body_md: str,
    dependencies: list[dict[str, Any]],
    inbound: list[dict[str, Any]],
    include_inbound: bool,
    inbound_total: int,
    depth: int,
    max_tokens: int,
    full_count: int,
    summary_count: int,
    omitted_count: int,
) -> int:
    """Compute exact estimated tokens of the final rendered Markdown bundle."""
    body = build_bundle_markdown_parts(
        root_body_md=root_body_md,
        dependencies=dependencies,
        inbound=inbound,
        include_inbound=include_inbound,
        inbound_total=inbound_total,
    )
    # Two-pass stabilization in case digit count of token estimate changes footer length.
    est = est_tokens(body)
    for _ in range(2):
        footer = format_bundle_footer(
            root_id=root_id,
            depth=depth,
            estimated_tokens=est,
            max_tokens=max_tokens,
            full_count=full_count,
            summary_count=summary_count,
            omitted_count=omitted_count,
        )
        est = est_tokens(f"{body}\n\n{footer}")
    return est


def render_list(payload: dict[str, Any]) -> str:
    """Render `srd_list` catalog or filtered entity list as Markdown."""
    if payload.get("status") != "ok":
        return _render_unresolved(payload)

    mode = payload.get("mode")
    if mode == "catalog":
        meta = payload.get("meta", {})
        lines = [
            f"# SRD 2024 Catalog (`{meta.get('dataset_id', 'srd-2024-5.2')}`)",
            "",
            (
                f"**Entities:** {meta.get('entity_count', 0)} | "
                f"**Edges:** {meta.get('edge_count', 0)} | "
                f"**License:** {meta.get('license', 'CC-BY-4.0')}"
            ),
            "",
            "| Type | Count | Filterable Facets |",
            "|---|---:|---|",
        ]
        for entry in payload.get("resource_types", []):
            fkeys = entry.get("facet_keys") or []
            facets_str = ", ".join(f"`{k}`" for k in fkeys) if fkeys else "*(none)*"
            lines.append(f"| `{entry['type']}` | {entry['count']} | {facets_str} |")
        return "\n".join(lines)

    entity_type = payload.get("type", "")
    filters = payload.get("filters") or {}
    total = int(payload.get("total", 0))
    offset = int(payload.get("offset", 0))
    results = payload.get("results", [])

    filter_desc = ""
    if filters:
        parts = [f"{k}={'|'.join(v)}" for k, v in sorted(filters.items())]
        filter_desc = f" (filters: {', '.join(parts)})"

    start = offset + 1 if results else 0
    end = offset + len(results)
    lines = [
        f"## List `{entity_type}`{filter_desc} — showing {start}–{end} of {total}",
    ]
    if not results:
        lines.extend(["", "No matching entities found."])
        return "\n".join(lines)

    lines.append("")
    for item in results:
        lines.append(f"- **{item['name']}** (`{item['id']}`): {item['summary']}")
    return "\n".join(lines)


def _format_edge_meta(meta: Any) -> str:
    if meta is None:
        return ""
    if isinstance(meta, dict):
        parts = [f"{k}={v}" for k, v in sorted(meta.items())]
        return f" [{', '.join(parts)}]" if parts else ""
    if isinstance(meta, list):
        return f" [{json.dumps(meta, ensure_ascii=False, separators=(',', ':'))}]"
    return f" [{meta}]"


def render_graph(payload: dict[str, Any]) -> str:
    """Render `srd_graph` neighbor inspection payload as Markdown."""
    if payload.get("status") != "ok":
        return _render_unresolved(payload)

    root = payload["root"]
    neighbors = payload.get("neighbors", [])
    total = int(payload.get("total", len(neighbors)))
    direction = payload.get("direction", "both")
    depth = payload.get("depth", 1)
    rel_filter = payload.get("rel")

    rel_note = f", rel=`{rel_filter}`" if rel_filter else ""
    lines = [
        f"## Graph: {root['name']} (`{root['id']}`)",
        f"*{root['summary']}*",
        "",
        f"Direction: `{direction}` | Depth: `{depth}`{rel_note} | Showing {len(neighbors)} of {total} edge(s)",
    ]

    if not neighbors:
        lines.extend(["", "No matching graph edges found."])
        return "\n".join(lines)

    out_edges = [n for n in neighbors if n["direction"] == "out"]
    in_edges = [n for n in neighbors if n["direction"] == "in"]

    if out_edges:
        lines.extend(["", "### Outgoing (`->`)"])
        for edge in out_edges:
            meta_str = _format_edge_meta(edge.get("meta"))
            hop_prefix = f"[hop {edge['hop']}] " if depth > 1 else ""
            lines.append(
                f"- {hop_prefix}`{edge['rel']}`{meta_str} -> **{edge['name']}** (`{edge['id']}`): {edge['summary']}"
            )

    if in_edges:
        lines.extend(["", "### Inbound (`<-`)"])
        for edge in in_edges:
            meta_str = _format_edge_meta(edge.get("meta"))
            hop_prefix = f"[hop {edge['hop']}] " if depth > 1 else ""
            lines.append(
                f"- {hop_prefix}`{edge['rel']}`{meta_str} <- **{edge['name']}** (`{edge['id']}`): {edge['summary']}"
            )

    return "\n".join(lines)


def render_validate(payload: dict[str, Any]) -> str:
    """Render `srd_validate` batch validation payload as Markdown."""
    if payload.get("status") != "ok":
        return _render_unresolved(payload)

    valid = payload.get("valid", [])
    ambiguous = payload.get("ambiguous", [])
    invalid = payload.get("invalid", [])
    links = payload.get("links", [])
    advisories = payload.get("advisories", [])
    prov = payload.get("provenance", {})

    lines = [
        "## SRD Validation Report",
        "",
        f"**Valid:** {len(valid)} | **Ambiguous:** {len(ambiguous)} | **Invalid:** {len(invalid)}",
    ]

    if valid:
        lines.extend(["", "### Valid Entities"])
        for item in valid:
            alias = f" *(from `{item['query']}`)*" if item["query"] != item["id"] else ""
            lines.append(f"- **{item['name']}** (`{item['id']}`){alias}: {item['summary']}")

    if ambiguous:
        lines.extend(["", "### Ambiguous Identifiers"])
        for item in ambiguous:
            cands = ", ".join(f"`{c['id']}`" for c in item.get("candidates", []))
            lines.append(f"- `{item['query']}` -> candidates: {cands}")

    if invalid:
        lines.extend(["", "### Invalid Identifiers"])
        for item in invalid:
            sugs = item.get("suggestions", [])
            if sugs:
                sug_str = ", ".join(f"`{s['id']}`" for s in sugs)
                lines.append(f"- `{item['query']}` -> not found (suggestions: {sug_str})")
            else:
                lines.append(f"- `{item['query']}` -> not found")

    if links:
        lines.extend(["", "### Inter-Entity Links"])
        for link in links:
            meta_str = _format_edge_meta(link.get("meta"))
            lines.append(
                f"- `{link['src_id']}` --[`{link['rel']}`{meta_str}]--> `{link['dst_id']}`"
            )

    if advisories:
        lines.extend(["", "### Advisories"])
        for adv in advisories:
            lines.append(f"- [{adv['kind']}] {adv['message']}")

    if prov:
        lines.extend(
            [
                "",
                "### Provenance",
                f"- **Dataset:** `{prov.get('dataset_id', '')}` | **License:** {prov.get('license', '')}",
                f"- **Attribution:** {prov.get('attribution', '')}",
            ]
        )

    return "\n".join(lines)


def render_operation(operation: str, payload: dict[str, Any], fmt: str = "markdown") -> str:
    """Render an operation result in 'markdown' or 'json' format."""
    if fmt == "json":
        return render_json(payload)
    if operation == "search":
        return render_search(payload)
    if operation == "get":
        return render_bundle(payload)
    if operation == "list":
        return render_list(payload)
    if operation == "graph":
        return render_graph(payload)
    if operation == "validate":
        return render_validate(payload)
    return render_json(payload)
