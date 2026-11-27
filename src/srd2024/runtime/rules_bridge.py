"""Lazy bridge between runtime CLI/MCP surfaces and the Stage 2 rules engine.

Strictly read-only over `dist/rules.json` via `srd2024.engine`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .db import repo_root

ENV_RULES_VAR = "SRD2024_RULES"
DEFAULT_RULES_RELPATH = Path("dist") / "rules.json"


def resolve_rules_path(explicit_path: str | Path | None = None) -> Path:
    """Resolve compiled rules.json path: --rules -> SRD2024_RULES -> default."""
    if explicit_path is not None and str(explicit_path).strip():
        return Path(explicit_path).expanduser().resolve()
    env_val = os.environ.get(ENV_RULES_VAR, "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    return (repo_root() / DEFAULT_RULES_RELPATH).resolve()


def load_rules_store(rules_path: str | Path | None = None) -> Any:
    """Lazily import and load `srd2024.engine.store.RulesStore` read-only."""
    from srd2024.engine.store import RulesStore

    resolved = resolve_rules_path(rules_path)
    return RulesStore(resolved)


def get_coverage_payload(rules: Any) -> dict[str, Any]:
    """Extract the rules coverage summary payload from a RulesStore instance."""
    if hasattr(rules, "get_coverage"):
        cov = rules.get_coverage()
        if isinstance(cov, dict):
            summary = cov.get("summary") if isinstance(cov.get("summary"), dict) else cov
            return {
                "status": "ok",
                "mode": "coverage",
                "schema_version": str(cov.get("schema_version", "2")),
                "dataset_id": str(cov.get("dataset_id", "srd-2024-5.2")),
                "source_hash": str(cov.get("source_hash", "")),
                "upstream": cov.get("upstream", {}),
                "license": cov.get("license", {}),
                "summary": summary,
            }

    raw_data = getattr(rules, "data", None)
    meta = raw_data if isinstance(raw_data, dict) else {}
    return {
        "status": "ok",
        "mode": "coverage",
        "schema_version": str(meta.get("schema_version", "2")),
        "dataset_id": str(meta.get("dataset_id", "srd-2024-5.2")),
        "source_hash": str(meta.get("source_hash", "")),
        "upstream": meta.get("upstream", {}),
        "license": meta.get("license", {}),
        "summary": meta.get("coverage_summary") or {},
    }


def query_rule(
    rules: Any,
    *,
    identifier: str | None = None,
    coverage: bool = False,
) -> dict[str, Any]:
    """Execute `srd_rule` / `bin/srd rule` against a loaded RulesStore."""
    clean_id = identifier.strip() if isinstance(identifier, str) else ""
    if coverage or clean_id.lower() in ("coverage", "rules:coverage", "rules-coverage"):
        return get_coverage_payload(rules)
    if not clean_id:
        raise ValueError("Provide an entity identifier or set coverage=true / --coverage.")

    result = rules.get_rule(clean_id)
    if isinstance(result, dict):
        if "status" in result:
            return result
        return {"status": "ok", "rule": result}
    return {
        "status": "not_found",
        "query": clean_id,
        "suggestions": [],
    }


def get_rule_resource(rules: Any, rule_type: str, slug: str) -> dict[str, Any] | None:
    """Fetch a single compiled Rules IR object for `srd://rule/{type}/{slug}`."""
    rtype = rule_type.strip().lower()
    rslug = slug.strip().lower()
    if not rtype or not rslug:
        return None

    type_aliases = {
        "weapon_masteries": "weapon-mastery-properties",
        "ability_scores": "ability-scores",
    }
    candidates = [f"{rtype}:{rslug}"]
    mapped = type_aliases.get(rtype)
    if mapped:
        candidates.append(f"{mapped}:{rslug}")

    for ident in candidates:
        res = rules.get_rule(ident)
        if isinstance(res, dict) and res.get("status") == "ok":
            rule_obj = res.get("rule")
            if isinstance(rule_obj, dict):
                return rule_obj
            return res
    return None


def execute_compile_character(rules: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and compile a 2024 CharacterBuildSpec via `srd2024.engine.character`."""
    from srd2024.engine.character import compile_character

    if not isinstance(spec, dict):
        raise ValueError("Character spec must be a JSON object.")
    return compile_character(spec, rules)


def execute_encounter_step(
    rules: Any,
    *,
    intent: dict[str, Any],
    state: dict[str, Any] | None = None,
    roll_overrides: list[int] | None = None,
) -> dict[str, Any]:
    """Advance a 5e-2024 encounter deterministically via `srd2024.engine.combat.step`."""
    from srd2024.engine.combat import step

    if not isinstance(intent, dict):
        raise ValueError("Encounter 'intent' must be a JSON object.")
    if state is not None and not isinstance(state, dict):
        raise ValueError("Encounter 'state' must be a JSON object.")
    return step(state, intent, rules, roll_overrides=roll_overrides)
