"""Text normalization, token estimation, and FTS5 query helpers."""

from __future__ import annotations

import math
import re

# Typographic punctuation translation table (§4.4)
_TYPOGRAPHIC_TRANS = str.maketrans(
    {
        "\u2019": "'",  # right single quotation mark ’
        "\u2018": "'",  # left single quotation mark ‘
        "\u201c": '"',  # left double quotation mark “
        "\u201d": '"',  # right double quotation mark ”
        "\u2013": "-",  # en dash –
        "\u2014": "-",  # em dash —
        "\u2212": "-",  # minus sign −
    }
)

# Bundle dependency priority tiers (§4.3)
TYPE_TIERS: dict[str, int] = {
    # Tier 1: Core mechanical rules
    "ability-scores": 1,
    "conditions": 1,
    "damage-types": 1,
    "feats": 1,
    "magic-schools": 1,
    "poisons": 1,
    "skills": 1,
    "spells": 1,
    "traits": 1,
    "weapon-mastery-properties": 1,
    "weapon-properties": 1,
    # Tier 2: Character / gameplay entities
    "alignments": 2,
    "backgrounds": 2,
    "classes": 2,
    "equipment": 2,
    "features": 2,
    "languages": 2,
    "magic-items": 2,
    "monsters": 2,
    "species": 2,
    "subclasses": 2,
    "subspecies": 2,
    # Tier 3: Structural / high-fan-out containers
    "equipment-categories": 3,
    "levels": 3,
    "proficiencies": 3,
}

_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_SLUGIFY_RE = re.compile(r"[^a-z0-9]+")


def normalize_search_text(text: str) -> str:
    """Normalize typographic punctuation to ASCII equivalents per SPEC §4.4."""
    return text.translate(_TYPOGRAPHIC_TRANS)


def normalize_name_key(text: str) -> str:
    """Normalize an entity name for exact case-insensitive lookup."""
    return normalize_search_text(text).strip().lower()


def slug_candidate(text: str) -> str:
    """Convert a query string into a normalized kebab-case slug candidate."""
    norm = normalize_name_key(text).replace("'", "")
    return _SLUGIFY_RE.sub("-", norm).strip("-")


def tokenize_fts_query(query: str) -> list[str]:
    """Extract safe alphanumeric FTS5 query tokens from arbitrary user input."""
    norm = normalize_search_text(query).strip().lower()
    if not norm:
        return []
    return _TOKEN_RE.findall(norm)


def build_fts_match_expr(tokens: list[str]) -> str:
    """Build a safe conjunctive FTS5 MATCH expression with prefix '*' on the last token."""
    if not tokens:
        return ""
    quoted = [f'"{tok}"' for tok in tokens]
    quoted[-1] = f"{quoted[-1]}*"
    return " ".join(quoted)


def escape_like(term: str) -> str:
    """Escape SQL LIKE wildcard characters using backslash as the escape character."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def est_tokens(text: str) -> int:
    """Deterministic token estimator per SPEC §8.2: ceil(len(text) / 4)."""
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def tier_for_type(entity_type: str) -> int:
    """Return the resolved-bundle priority tier (1..3) for a resource type."""
    return TYPE_TIERS.get(entity_type, 3)
