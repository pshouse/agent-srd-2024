"""Top-level orchestrator for the Stage 2 Rules IR Compiler (§3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from srd2024.compiler.build import SRD_ATTRIBUTION

from .character import compile_character_creation
from .common import (
    DATASET_ID,
    SCHEMA_VERSION,
    compact_json,
    compute_source_hash,
    pretty_json,
    resolve_raw_paths,
    write_text_lf,
)
from .conditions import compile_conditions
from .equipment import compile_equipment
from .masteries import compile_weapon_masteries
from .monsters import compile_monsters
from .spells import compile_spells


def _domain_coverage_stats(entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total = len(entries)
    full = sum(1 for e in entries.values() if e["coverage"] == "full")
    partial = sum(1 for e in entries.values() if e["coverage"] == "partial")
    narrative = sum(1 for e in entries.values() if e["coverage"] == "narrative")
    executable_pct = round(100.0 * (full + partial) / total, 1) if total else 0.0
    return {
        "total": total,
        "full": full,
        "partial": partial,
        "narrative": narrative,
        "executable_pct": executable_pct,
    }


def _domain_coverage_entities(entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": e["id"],
            "name": e["name"],
            "coverage": e["coverage"],
            "unmodeled_clauses": list(e["provenance"]["unmodeled_clauses"]),
        }
        for _, e in sorted(entries.items(), key=lambda kv: kv[0])
    ]


def build_rules(raw_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Compile raw 2024 SRD JSON into deterministic dist/rules.json and dist/web/rules-coverage.json."""
    en_dir, prov_path = resolve_raw_paths(raw_dir)
    provenance = json.loads(prov_path.read_text(encoding="utf-8"))
    source_hash = compute_source_hash(en_dir)

    weapon_masteries = compile_weapon_masteries(en_dir)
    conditions = compile_conditions(en_dir)
    equipment = compile_equipment(en_dir)
    spells = compile_spells(en_dir)
    monsters = compile_monsters(en_dir)
    character_creation = compile_character_creation(en_dir)

    domains: dict[str, dict[str, dict[str, Any]]] = {
        "weapon_masteries": weapon_masteries,
        "conditions": conditions,
        "equipment": equipment,
        "spells": spells,
        "monsters": monsters,
    }

    by_domain = {name: _domain_coverage_stats(entries) for name, entries in domains.items()}
    total_compiled = sum(d["total"] for d in by_domain.values())
    total_full = sum(d["full"] for d in by_domain.values())
    total_partial = sum(d["partial"] for d in by_domain.values())
    total_narrative = sum(d["narrative"] for d in by_domain.values())
    total_exec_pct = round(100.0 * (total_full + total_partial) / total_compiled, 1) if total_compiled else 0.0

    character_creation_counts = {
        "classes": len(character_creation["classes"]),
        "subclasses": len(character_creation["subclasses"]),
        "species": len(character_creation["species"]),
        "subspecies": len(character_creation["subspecies"]),
        "backgrounds": len(character_creation["backgrounds"]),
        "feats": len(character_creation["feats"]),
        "levels": len(character_creation["levels"]),
        "traits": len(character_creation["traits"]),
    }

    subclass_coverage = _domain_coverage_stats(character_creation["subclasses"])

    coverage_summary = {
        "total_compiled": total_compiled,
        "full": total_full,
        "partial": total_partial,
        "narrative": total_narrative,
        "executable_pct": total_exec_pct,
        "by_domain": by_domain,
        "character_creation_counts": character_creation_counts,
        "subclass_coverage": subclass_coverage,
    }

    content_license = provenance["content_license"]
    rules_doc = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "source_hash": source_hash,
        "upstream": {
            "repo": provenance["upstream_repo"],
            "commit": provenance["upstream_commit"],
            "database_version": provenance["upstream_database_version"],
        },
        "license": {
            "content": content_license["name"],
            "content_url": content_license["url"],
            "attribution": SRD_ATTRIBUTION,
        },
        "coverage_summary": coverage_summary,
        "weapon_masteries": weapon_masteries,
        "conditions": conditions,
        "equipment": equipment,
        "spells": spells,
        "monsters": monsters,
        "character_creation": character_creation,
    }

    coverage_manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "source_hash": source_hash,
        "summary": coverage_summary,
        "entities": {
            name: _domain_coverage_entities(entries)
            for name, entries in domains.items()
        },
        "subclass_entities": _domain_coverage_entities(character_creation["subclasses"]),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    write_text_lf(out_dir / "rules.json", compact_json(rules_doc))
    write_text_lf(out_dir / "web" / "rules-coverage.json", pretty_json(coverage_manifest))

    return coverage_summary
