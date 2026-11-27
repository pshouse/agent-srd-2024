"""Read-only Rules IR store for Stage 2 (`dist/rules.json`).

Implements STAGE2-SPEC §4.1 and §6.1:
- Path resolution: explicit `rules_path` -> `SRD2024_RULES` -> `<repo_root>/dist/rules.json`.
- Strictly read-only JSON loading (never writes to `dist/`).
- Canonical ID, domain-prefixed ID, bare slug, and normalized name lookup matching
  Stage 1 §4.2 ambiguity resolution rules.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

ENV_RULES_VAR = "SRD2024_RULES"
DEFAULT_RULES_RELPATH = Path("dist") / "rules.json"

# Maps internal rules.json domain keys -> canonical Stage 1 ID prefix.
TOP_LEVEL_DOMAINS: dict[str, str] = {
    "weapon_masteries": "weapon-mastery-properties",
    "conditions": "conditions",
    "equipment": "equipment",
    "spells": "spells",
    "monsters": "monsters",
}

CHAR_CREATION_DOMAINS: dict[str, str] = {
    "ability_scores": "ability-scores",
    "skills": "skills",
    "classes": "classes",
    "subclasses": "subclasses",
    "species": "species",
    "subspecies": "subspecies",
    "backgrounds": "backgrounds",
    "feats": "feats",
    "levels": "levels",
    "traits": "traits",
}

ALL_DOMAINS: dict[str, str] = {**TOP_LEVEL_DOMAINS, **CHAR_CREATION_DOMAINS}

# Prefix aliases accepted in `get_rule()` (both canonical Stage 1 type and rules.json key).
PREFIX_TO_DOMAIN: dict[str, str] = {}
for _dom, _prefix in ALL_DOMAINS.items():
    PREFIX_TO_DOMAIN[_prefix] = _dom
    PREFIX_TO_DOMAIN[_dom] = _dom
    PREFIX_TO_DOMAIN[_dom.replace("_", "-")] = _dom
PREFIX_TO_DOMAIN["weapon-masteries"] = "weapon_masteries"
PREFIX_TO_DOMAIN["weapon-mastery"] = "weapon_masteries"
PREFIX_TO_DOMAIN["mastery"] = "weapon_masteries"


def repo_root() -> Path:
    """Return the repository root derived from this module's location."""
    return Path(__file__).resolve().parents[3]


def resolve_rules_path(explicit_path: str | Path | None = None) -> Path:
    """Resolve `rules.json` path: explicit arg -> SRD2024_RULES -> <repo_root>/dist/rules.json."""
    if explicit_path is not None and str(explicit_path).strip():
        return Path(explicit_path).expanduser().resolve()
    env_val = os.environ.get(ENV_RULES_VAR, "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    return (repo_root() / DEFAULT_RULES_RELPATH).resolve()


def strip_prefix(identifier: str) -> str:
    """Extract the lowercased bare slug from either `type:slug` or `slug`."""
    raw = str(identifier).strip().lower()
    if ":" in raw:
        return raw.split(":", 1)[1].strip()
    return raw


def _normalize_name(text: str) -> str:
    """Normalize punctuation and whitespace for case-insensitive name lookups."""
    cleaned = (
        text.strip()
        .lower()
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    return re.sub(r"\s+", " ", cleaned)


class RulesStore:
    """Read-only in-memory index over compiled Stage 2 `dist/rules.json`."""

    def __init__(self, rules_path: str | Path | None = None) -> None:
        self.rules_path: Path = resolve_rules_path(rules_path)
        if not self.rules_path.is_file():
            raise FileNotFoundError(
                f"Compiled Stage 2 rules not found at '{self.rules_path}'. "
                "Run 'bin/srd-rules-build' first or pass --rules / set SRD2024_RULES."
            )
        with self.rules_path.open("r", encoding="utf-8") as fh:
            self._data: dict[str, Any] = json.load(fh)

        self._by_id: dict[str, tuple[str, str, str, dict[str, Any]]] = {}
        self._by_slug: dict[str, list[tuple[str, str, str, dict[str, Any]]]] = {}
        self._by_name: dict[str, list[tuple[str, str, str, dict[str, Any]]]] = {}
        self._all_ids: list[str] = []
        self._build_indexes()

    def _build_indexes(self) -> None:
        seen_canonical: set[str] = set()

        def _index_domain(domain: str, prefix: str, mapping: Any) -> None:
            if not isinstance(mapping, dict):
                return
            for slug_key in sorted(mapping.keys()):
                entry = mapping[slug_key]
                if not isinstance(entry, dict):
                    continue
                slug = str(entry.get("slug") or slug_key).strip().lower()
                canonical_id = str(entry.get("id") or f"{prefix}:{slug}").strip().lower()
                record = (canonical_id, domain, slug, entry)

                # Index canonical ID, prefix:slug, and domain:slug aliases.
                self._by_id[canonical_id] = record
                self._by_id[f"{prefix}:{slug}"] = record
                self._by_id[f"{domain}:{slug}"] = record
                self._by_id[f"{domain.replace('_', '-')}:{slug}"] = record

                if canonical_id not in seen_canonical:
                    seen_canonical.add(canonical_id)
                    self._all_ids.append(canonical_id)
                    self._by_slug.setdefault(slug, []).append(record)
                    name = entry.get("name")
                    if isinstance(name, str) and name.strip():
                        self._by_name.setdefault(_normalize_name(name), []).append(record)

        for dom, prefix in TOP_LEVEL_DOMAINS.items():
            _index_domain(dom, prefix, self._data.get(dom))

        cc = self._data.get("character_creation")
        if isinstance(cc, dict):
            for dom, prefix in CHAR_CREATION_DOMAINS.items():
                _index_domain(dom, prefix, cc.get(dom))

        self._all_ids.sort()
        for records in self._by_slug.values():
            records.sort(key=lambda r: r[0])
        for records in self._by_name.values():
            records.sort(key=lambda r: r[0])

    @property
    def schema_version(self) -> str:
        return str(self._data.get("schema_version", "2"))

    @property
    def dataset_id(self) -> str:
        return str(self._data.get("dataset_id", "srd-2024-5.2"))

    @property
    def source_hash(self) -> str:
        return str(self._data.get("source_hash", ""))

    @property
    def data(self) -> dict[str, Any]:
        """Return a read-only deep copy of the raw compiled rules dict."""
        return copy.deepcopy(self._data)

    def get_coverage(self) -> dict[str, Any]:
        """Return the global coverage summary and provenance metadata."""
        summary = copy.deepcopy(self._data.get("coverage_summary", {}))
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "source_hash": self.source_hash,
            "upstream": copy.deepcopy(self._data.get("upstream", {})),
            "license": copy.deepcopy(self._data.get("license", {})),
            "summary": summary,
        }

    def get_rule(self, identifier: str) -> dict[str, Any]:
        """Resolve a compiled rule by canonical ID, domain prefix, bare slug, or name.

        Resolution order (matching Stage 1 §4.2):
        1. Exact canonical or domain-prefixed ID (`spells:fireball`, `weapon_masteries:topple`)
        2. Exact bare slug (returns `status: "ambiguous"` with sorted candidates on collision)
        3. Exact case/punctuation-normalized name (returns `status: "ambiguous"` on collision)
        4. `status: "not_found"` with deterministic suggestions
        """
        q = str(identifier).strip()
        if not q:
            return {"status": "not_found", "query": q, "suggestions": []}

        q_lower = q.lower()
        if q_lower in ("coverage", "rules:coverage", "rules-coverage"):
            cov = self.get_coverage()
            return {
                "status": "ok",
                "id": "coverage",
                "domain": "coverage",
                "slug": "coverage",
                "rule": cov,
            }

        # 1. Exact ID or prefix:slug lookup.
        if ":" in q_lower:
            rec = self._by_id.get(q_lower)
            if rec is not None:
                return self._ok_payload(rec)
            prefix, slug = q_lower.split(":", 1)
            mapped_dom = PREFIX_TO_DOMAIN.get(prefix.strip())
            if mapped_dom is not None:
                rec = self._by_id.get(f"{mapped_dom}:{slug.strip()}")
                if rec is not None:
                    return self._ok_payload(rec)

        # 2. Bare slug lookup.
        slug_matches = self._by_slug.get(q_lower)
        if slug_matches:
            if len(slug_matches) == 1:
                return self._ok_payload(slug_matches[0])
            return self._ambiguous_payload(q, slug_matches)

        # 3. Exact normalized name lookup.
        name_matches = self._by_name.get(_normalize_name(q))
        if name_matches:
            if len(name_matches) == 1:
                return self._ok_payload(name_matches[0])
            return self._ambiguous_payload(q, name_matches)

        # 4. Not found with deterministic suggestions.
        needle = strip_prefix(q_lower)
        suggestions: list[str] = []
        if needle:
            prefix_hits = [cid for cid in self._all_ids if cid.split(":", 1)[1].startswith(needle)]
            substr_hits = [
                cid
                for cid in self._all_ids
                if needle in cid and cid not in prefix_hits
            ]
            suggestions = (prefix_hits + substr_hits)[:5]
        return {"status": "not_found", "query": q, "suggestions": suggestions}

    @staticmethod
    def _ok_payload(rec: tuple[str, str, str, dict[str, Any]]) -> dict[str, Any]:
        cid, domain, slug, entry = rec
        return {
            "status": "ok",
            "id": cid,
            "domain": domain,
            "type": ALL_DOMAINS.get(domain, domain),
            "slug": slug,
            "rule": copy.deepcopy(entry),
        }

    @staticmethod
    def _ambiguous_payload(
        query: str, matches: list[tuple[str, str, str, dict[str, Any]]]
    ) -> dict[str, Any]:
        candidates = [
            {
                "id": cid,
                "type": ALL_DOMAINS.get(domain, domain),
                "domain": domain,
                "slug": slug,
                "name": str(entry.get("name") or slug),
            }
            for cid, domain, slug, entry in sorted(matches, key=lambda r: r[0])
        ]
        return {
            "status": "ambiguous",
            "query": query,
            "candidates": candidates,
        }

    # ------------------------------------------------------------------
    # Typed Domain Accessors (return deep copies to guarantee immutability)
    # ------------------------------------------------------------------

    def _get_top_entry(self, domain: str, identifier: str) -> dict[str, Any] | None:
        slug = strip_prefix(identifier)
        mapping = self._data.get(domain)
        if not isinstance(mapping, dict):
            return None
        entry = mapping.get(slug)
        return copy.deepcopy(entry) if isinstance(entry, dict) else None

    def _get_cc_entry(self, domain: str, identifier: str) -> dict[str, Any] | None:
        slug = strip_prefix(identifier)
        cc = self._data.get("character_creation")
        if not isinstance(cc, dict):
            return None
        mapping = cc.get(domain)
        if not isinstance(mapping, dict):
            return None
        entry = mapping.get(slug)
        return copy.deepcopy(entry) if isinstance(entry, dict) else None

    def get_weapon_mastery(self, identifier: str) -> dict[str, Any] | None:
        return self._get_top_entry("weapon_masteries", identifier)

    def get_condition(self, identifier: str) -> dict[str, Any] | None:
        return self._get_top_entry("conditions", identifier)

    def get_equipment(self, identifier: str) -> dict[str, Any] | None:
        return self._get_top_entry("equipment", identifier)

    def get_spell(self, identifier: str) -> dict[str, Any] | None:
        return self._get_top_entry("spells", identifier)

    def get_monster(self, identifier: str) -> dict[str, Any] | None:
        return self._get_top_entry("monsters", identifier)

    def get_class(self, identifier: str) -> dict[str, Any] | None:
        return self._get_cc_entry("classes", identifier)

    def get_subclass(self, identifier: str) -> dict[str, Any] | None:
        return self._get_cc_entry("subclasses", identifier)

    def get_species(self, identifier: str) -> dict[str, Any] | None:
        return self._get_cc_entry("species", identifier)

    def get_subspecies(self, identifier: str) -> dict[str, Any] | None:
        return self._get_cc_entry("subspecies", identifier)

    def get_background(self, identifier: str) -> dict[str, Any] | None:
        return self._get_cc_entry("backgrounds", identifier)

    def get_feat(self, identifier: str) -> dict[str, Any] | None:
        return self._get_cc_entry("feats", identifier)

    def get_level(self, identifier: str) -> dict[str, Any] | None:
        return self._get_cc_entry("levels", identifier)

    def get_trait(self, identifier: str) -> dict[str, Any] | None:
        return self._get_cc_entry("traits", identifier)

    def get_ability_scores(self) -> dict[str, Any]:
        cc = self._data.get("character_creation", {})
        return copy.deepcopy(cc.get("ability_scores", {}))

    def get_skills(self) -> dict[str, Any]:
        cc = self._data.get("character_creation", {})
        return copy.deepcopy(cc.get("skills", {}))

    def get_damage_types(self) -> list[str]:
        cc = self._data.get("character_creation", {})
        return list(cc.get("damage_types", []))

    def list_domain(self, domain: str) -> dict[str, Any]:
        """Return a deep copy of an entire domain dict."""
        if domain in TOP_LEVEL_DOMAINS:
            return copy.deepcopy(self._data.get(domain, {}))
        if domain in CHAR_CREATION_DOMAINS:
            return copy.deepcopy(self._data.get("character_creation", {}).get(domain, {}))
        return {}
