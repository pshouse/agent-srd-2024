"""Deterministic text normalization, YAML quoting, and summary utilities."""

from __future__ import annotations

import math
import re

_TYPO_TRANS = str.maketrans(
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

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_STYLE_RE = re.compile(r"[*_`]+")
_WS_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def normalize_search_text(text: str) -> str:
    """Normalize typographic punctuation for FTS5 indexing and matching (SPEC §4.4)."""
    return text.translate(_TYPO_TRANS)


def strip_markdown(text: str) -> str:
    """Strip Markdown links, headings, and emphasis syntax for FTS5 body indexing."""
    s = _MD_LINK_RE.sub(r"\1", text)
    s = _MD_HEADING_RE.sub("", s)
    s = _MD_STYLE_RE.sub("", s)
    s = s.replace("|", " ")
    return _WS_RE.sub(" ", s).strip()


def yaml_quote(value: str) -> str:
    """Safely double-quote a YAML scalar using pure stdlib escaping (SPEC §7.2)."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def est_tokens(text: str) -> int:
    """Estimate tokens per SPEC §5 & §8.2: ceil(len(text) / 4.0)."""
    return math.ceil(len(text) / 4.0)


def canonical_scalar(value: object) -> str:
    """Convert a facet value to a canonical lowercased string (SPEC §4.3)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    return str(value).strip().lower()


def collapse_ws(text: str) -> str:
    """Collapse all internal whitespace and newlines to single spaces."""
    return _WS_RE.sub(" ", text).strip()


def clean_plain_text(text: str) -> str:
    """Strip basic markdown emphasis and collapse whitespace for compact summaries."""
    s = _MD_LINK_RE.sub(r"\1", text)
    s = s.replace("**", "").replace("*", "").replace("`", "")
    return collapse_ws(s)


def first_sentence(text: str, max_len: int = 190) -> str:
    """Extract a crisp first sentence from prose text."""
    plain = clean_plain_text(text)
    if not plain:
        return ""
    parts = _SENTENCE_SPLIT_RE.split(plain, maxsplit=1)
    sent = parts[0].strip()
    if len(sent) <= max_len:
        return sent
    return truncate_summary(plain, max_len)


def truncate_summary(text: str, max_len: int = 200) -> str:
    """Ensure a summary is single-line, non-empty, and <= max_len characters."""
    s = collapse_ws(text)
    if len(s) <= max_len:
        return s
    cut = s[: max_len - 1].rstrip()
    # Avoid cutting mid-word when reasonable
    if " " in cut and len(cut) > 40:
        space_idx = cut.rfind(" ")
        if space_idx >= max_len - 30:
            cut = cut[:space_idx].rstrip(" ,;:-")
    return cut + "…"
