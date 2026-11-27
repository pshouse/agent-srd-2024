"""Deterministic Markdown and JSON renderers for Stage 1 & Stage 2 SRD operations."""

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


def _render_rule_unresolved(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    query = payload.get("query", "")
    if status == "ambiguous":
        lines = [
            f"# Ambiguous Rule Identifier: `{query}`",
            "",
            "Multiple compiled rules match this identifier. Re-query using a canonical ID:",
            "",
        ]
        for cand in payload.get("candidates", []):
            if isinstance(cand, dict):
                cid = cand.get("id", "")
                cname = cand.get("name", cid)
                ctype = cand.get("domain") or cand.get("type") or ""
                cov = cand.get("coverage")
                extra = f", {ctype}" if ctype else ""
                cov_str = f" [coverage: `{cov}`]" if cov else ""
                lines.append(f"- **{cname}** (`{cid}`{extra}){cov_str}")
            else:
                lines.append(f"- `{cand}`")
        return "\n".join(lines)

    if status == "not_found":
        lines = [f"# Rule Not Found: `{query}`"]
        suggestions = payload.get("suggestions", [])
        if suggestions:
            lines.extend(["", "Did you mean one of these?", ""])
            for sug in suggestions:
                if isinstance(sug, dict):
                    sid = sug.get("id", "")
                    sname = sug.get("name", sid)
                    lines.append(f"- **{sname}** (`{sid}`)")
                else:
                    lines.append(f"- `{sug}`")
        else:
            lines.extend(["", "No matching compiled rules found."])
        return "\n".join(lines)

    return _render_unresolved(payload)


def render_rule(payload: dict[str, Any]) -> str:
    """Render `srd_rule` (single compiled Rules IR or global coverage summary) as Markdown."""
    if payload.get("status") not in ("ok", None):
        return _render_rule_unresolved(payload)

    if payload.get("mode") == "coverage":
        summary = payload.get("summary", {})
        by_domain = summary.get("by_domain", {})
        cc_counts = summary.get("character_creation_counts", {})
        lines = [
            f"# SRD 2024 Rules IR Coverage (`{payload.get('dataset_id', 'srd-2024-5.2')}`)",
            "",
            (
                f"**Compiled:** {summary.get('total_compiled', 0)} | "
                f"**Full:** {summary.get('full', 0)} | "
                f"**Partial:** {summary.get('partial', 0)} | "
                f"**Narrative:** {summary.get('narrative', 0)} | "
                f"**Executable:** {summary.get('executable_pct', 0.0)}%"
            ),
            "",
            "| Domain | Total | Full | Partial | Narrative | Executable % |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for domain, stats in sorted(by_domain.items()):
            lines.append(
                f"| `{domain}` | {stats.get('total', 0)} | {stats.get('full', 0)} | "
                f"{stats.get('partial', 0)} | {stats.get('narrative', 0)} | "
                f"{stats.get('executable_pct', 0.0)}% |"
            )
        if cc_counts:
            cc_parts = [f"{k}: {v}" for k, v in sorted(cc_counts.items())]
            lines.extend(["", f"**Character Creation Tables:** {', '.join(cc_parts)}"])
        return "\n".join(lines)

    rule = payload.get("rule") if isinstance(payload.get("rule"), dict) else payload
    rule_id = rule.get("id") or payload.get("id") or rule.get("slug") or "rule"
    name = rule.get("name") or rule_id
    domain = payload.get("domain") or rule.get("domain") or ""
    coverage = rule.get("coverage", "structured")
    prov = rule.get("provenance") if isinstance(rule.get("provenance"), dict) else {}
    parser_name = prov.get("parser", "")
    unmodeled = prov.get("unmodeled_clauses") or []

    meta_bits = [f"**Coverage:** `{coverage}`"]
    if domain:
        meta_bits.append(f"**Domain:** `{domain}`")
    if parser_name:
        meta_bits.append(f"**Parser:** `{parser_name}`")

    lines = [
        f"# {name} (`{rule_id}`)",
        "",
        " | ".join(meta_bits),
    ]
    if unmodeled:
        lines.extend(["", "**Unmodeled Clauses:** " + "; ".join(str(c) for c in unmodeled)])

    lines.extend(
        [
            "",
            "```json",
            json.dumps(rule, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    )
    return "\n".join(lines)


def _fmt_signed(val: int) -> str:
    return f"+{val}" if val >= 0 else str(val)


def render_character(payload: dict[str, Any]) -> str:
    """Render `srd_compile_character` validation report & derived statblock as Markdown."""
    if payload.get("status") == "error":
        return _render_unresolved(payload)

    valid = bool(payload.get("valid", False))
    errors = payload.get("errors") or []
    warnings = payload.get("warnings") or []
    sb = payload.get("statblock")

    title = (
        f"# Character Sheet: {sb['name']}"
        if isinstance(sb, dict) and sb.get("name")
        else "# Character Compilation Report"
    )
    status_label = "VALID" if valid else "INVALID"
    lines = [
        title,
        "",
        f"**Status:** `{status_label}` | **Errors:** {len(errors)} | **Warnings:** {len(warnings)}",
    ]

    if errors:
        lines.extend(["", "## Errors"])
        for err in errors:
            lines.append(
                f"- [`{err.get('code', 'error')}`] (`{err.get('path', '')}`) {err.get('message', '')}"
            )

    if warnings:
        lines.extend(["", "## Warnings"])
        for warn in warnings:
            lines.append(
                f"- [`{warn.get('code', 'warning')}`] (`{warn.get('path', '')}`) {warn.get('message', '')}"
            )

    if isinstance(sb, dict):
        sub_part = f" / `{sb['subclass']}`" if sb.get("subclass") else ""
        subsp_part = f" ({sb['subspecies']})" if sb.get("subspecies") else ""
        lines.extend(
            [
                "",
                "## Statblock Summary",
                f"- **Class:** `{sb.get('class', '')}`{sub_part} (Level {sb.get('level', 1)}, PB {_fmt_signed(int(sb.get('proficiency_bonus', 2)))})",
                f"- **Species:** `{sb.get('species', '')}`{subsp_part} ({sb.get('size', 'Medium')}) | **Background:** `{sb.get('background', '')}`",
                (
                    f"- **AC:** {sb.get('ac')} ({sb.get('ac_breakdown', '')}) | "
                    f"**HP:** {sb.get('hp_max')} (d{sb.get('hit_die', 8)}) | "
                    f"**Init:** {_fmt_signed(int(sb.get('initiative', 0)))} | "
                    f"**Speed:** {sb.get('speed_ft', 30)} ft. | "
                    f"**Extra Attacks:** {sb.get('extra_attacks', 1)} | "
                    f"**Passive Perception:** {sb.get('passive_perception', 10)}"
                ),
            ]
        )

        abilities = sb.get("abilities") or {}
        mods = sb.get("ability_mods") or {}
        saves = sb.get("saving_throws") or {}
        save_profs = set(sb.get("save_proficiencies") or [])
        if abilities:
            lines.extend(
                [
                    "",
                    "| Ability | Score | Mod | Save |",
                    "|---|---:|---:|---:|",
                ]
            )
            for ab in ("str", "dex", "con", "int", "wis", "cha"):
                if ab in abilities:
                    star = " *" if ab in save_profs else ""
                    lines.append(
                        f"| {ab.upper()} | {abilities[ab]} | "
                        f"{_fmt_signed(int(mods.get(ab, 0)))} | "
                        f"{_fmt_signed(int(saves.get(ab, 0)))}{star} |"
                    )

        weapons = sb.get("weapon_profiles") or []
        if weapons:
            lines.extend(["", "### Weapons"])
            for w in weapons:
                mastery = w.get("mastery") or "none"
                m_state = "active" if w.get("mastery_active") else "inactive"
                dmg_bonus = int(w.get("damage_bonus", 0))
                bonus_str = f" {_fmt_signed(dmg_bonus)}" if dmg_bonus != 0 else ""
                lines.append(
                    f"- **{w.get('name', w.get('slug', ''))}**: "
                    f"{_fmt_signed(int(w.get('attack_bonus', 0)))} to hit, "
                    f"{w.get('damage_dice', '')}{bonus_str} {w.get('damage_type', '')} "
                    f"(mastery: `{mastery}`, {m_state})"
                )

        sc = sb.get("spellcasting")
        if isinstance(sc, dict):
            slots = sc.get("slots") or {}
            slots_str = ", ".join(f"L{k}:{v}" for k, v in sorted(slots.items(), key=lambda kv: int(kv[0]))) or "none"
            lines.extend(
                [
                    "",
                    "### Spellcasting",
                    (
                        f"- **Ability:** `{sc.get('ability', '')}` | "
                        f"**Save DC:** {sc.get('spell_save_dc')} | "
                        f"**Attack:** {_fmt_signed(int(sc.get('spell_attack_bonus', 0)))} | "
                        f"**Slots:** {slots_str}"
                    ),
                ]
            )
            if sc.get("cantrips"):
                lines.append(f"- **Cantrips:** {', '.join(f'`{c}`' for c in sc['cantrips'])}")
            if sc.get("prepared_spells"):
                lines.append(
                    f"- **Prepared Spells:** {', '.join(f'`{s}`' for s in sc['prepared_spells'])}"
                )

        if sb.get("feats"):
            lines.extend(["", f"**Feats:** {', '.join(f'`{f}`' for f in sb['feats'])}"])
        if sb.get("damage_resistances"):
            lines.append(
                f"**Resistances:** {', '.join(f'`{r}`' for r in sb['damage_resistances'])}"
            )

    return "\n".join(lines)


def render_encounter(payload: dict[str, Any]) -> str:
    """Render `srd_encounter` StepResult as concise tactical Markdown."""
    status = payload.get("status", "error")
    err = payload.get("error")
    state = payload.get("state")
    events = payload.get("events") or []
    hooks = payload.get("narrative_hooks") or []
    diff = payload.get("state_diff") or {}
    legal = payload.get("legal_actions") or {}

    if status == "error" and not isinstance(state, dict):
        return _render_unresolved(payload)

    lines: list[str] = []
    if isinstance(state, dict):
        enc_id = state.get("encounter_id", "encounter")
        rnd = state.get("round", 1)
        turn_order = state.get("turn_order") or []
        active_idx = int(state.get("active_index", 0))
        active_id = (
            turn_order[active_idx]
            if 0 <= active_idx < len(turn_order)
            else legal.get("active_actor_id", "")
        )
        enc_status = state.get("status", "active")
        lines.append(
            f"## Encounter `{enc_id}` — Round {rnd} | Active: `{active_id}` | Status: `{enc_status}`"
        )
    else:
        lines.append(f"## Encounter Step (`{status}`)")

    if status != "ok" and err:
        lines.extend(["", f"**Rejected ({status}):** {err}"])

    if hooks:
        lines.extend(["", "### Narrative Hooks"])
        for h in hooks:
            lines.append(f"- {h}")

    if events:
        lines.extend(["", f"### Events ({len(events)})"])
        for ev in events:
            seq = ev.get("seq", "")
            etype = ev.get("type", "event")
            actor = ev.get("actor_id")
            target = ev.get("target_id")
            who = ""
            if actor and target:
                who = f" `{actor}` -> `{target}`"
            elif actor:
                who = f" `{actor}`"
            elif target:
                who = f" `{target}`"
            lines.append(f"- #{seq} `{etype}`{who}")

    diff_lines: list[str] = []
    for ch in diff.get("hp_changes") or []:
        diff_lines.append(
            f"- HP `{ch.get('id')}`: {ch.get('before')} -> {ch.get('after')} ({_fmt_signed(int(ch.get('delta', 0)))})"
        )
    for ch in diff.get("condition_changes") or []:
        parts = []
        if ch.get("added"):
            parts.append("+" + ",".join(ch["added"]))
        if ch.get("removed"):
            parts.append("-" + ",".join(ch["removed"]))
        if parts:
            diff_lines.append(f"- Conditions `{ch.get('id')}`: {' '.join(parts)}")
    for ch in diff.get("position_changes") or []:
        diff_lines.append(f"- Position `{ch.get('id')}`: {ch.get('before')} -> {ch.get('after')}")
    for ch in diff.get("slot_changes") or []:
        diff_lines.append(f"- Slots `{ch.get('id')}`: {ch}")
    if diff.get("turn_changed"):
        diff_lines.append("- Turn advanced")
    if diff_lines:
        lines.extend(["", "### State Changes", *diff_lines])

    if isinstance(state, dict) and isinstance(state.get("combatants"), dict):
        combatants = state["combatants"]
        order = state.get("turn_order") or sorted(combatants.keys())
        lines.extend(
            [
                "",
                "### Combatants",
                "| ID | Name | Side | HP | AC | Pos | Conditions |",
                "|---|---|---|---:|---:|---|---|",
            ]
        )
        for cid in order:
            c = combatants.get(cid)
            if not isinstance(c, dict):
                continue
            pos = c.get("position") or {}
            pos_str = f"({pos.get('x', 0)},{pos.get('y', 0)})"
            conds = [
                cond["condition"] if isinstance(cond, dict) else str(cond)
                for cond in (c.get("conditions") or [])
            ]
            if int(c.get("exhaustion_level", 0)) > 0:
                conds.append(f"exhaustion:{c['exhaustion_level']}")
            if not c.get("alive", True):
                conds.append("dead")
            cond_str = ", ".join(conds) if conds else "-"
            temp_hp = int(c.get("temp_hp", 0))
            hp_str = f"{c.get('hp', 0)}/{c.get('hp_max', 0)}" + (f" (+{temp_hp})" if temp_hp > 0 else "")
            lines.append(
                f"| `{cid}` | {c.get('name', cid)} | {c.get('side', '')} | {hp_str} | {c.get('ac', 0)} | {pos_str} | {cond_str} |"
            )

    if legal:
        acts = ", ".join(f"`{a}`" for a in (legal.get("actions") or [])) or "*(none)*"
        lines.extend(
            [
                "",
                "### Legal Actions",
                (
                    f"- **Active:** `{legal.get('active_actor_id', '')}` | "
                    f"**Move Remaining:** {legal.get('movement_remaining_ft', 0)} ft. | "
                    f"**Actions:** {acts} | "
                    f"**End Turn:** {bool(legal.get('can_end_turn', True))}"
                ),
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
    if operation == "rule":
        return render_rule(payload)
    if operation == "character":
        return render_character(payload)
    if operation == "encounter":
        return render_encounter(payload)
    return render_json(payload)
