"""Shared helpers, constants, and prose normalization for Stage 2 Rules Compiler."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2"
DATASET_ID = "srd-2024-5.2"

ABILITY_SLUGS = ("str", "dex", "con", "int", "wis", "cha")

ABILITY_NAME_TO_SLUG: dict[str, str] = {
    "strength": "str",
    "dexterity": "dex",
    "constitution": "con",
    "intelligence": "int",
    "wisdom": "wis",
    "charisma": "cha",
    "str": "str",
    "dex": "dex",
    "con": "con",
    "int": "int",
    "wis": "wis",
    "cha": "cha",
}

DAMAGE_TYPE_SLUGS: frozenset[str] = frozenset(
    {
        "acid",
        "bludgeoning",
        "cold",
        "fire",
        "force",
        "lightning",
        "necrotic",
        "piercing",
        "poison",
        "psychic",
        "radiant",
        "slashing",
        "thunder",
    }
)

CONDITION_SLUGS: frozenset[str] = frozenset(
    {
        "blinded",
        "charmed",
        "deafened",
        "exhaustion",
        "frightened",
        "grappled",
        "incapacitated",
        "invisible",
        "paralyzed",
        "petrified",
        "poisoned",
        "prone",
        "restrained",
        "stunned",
        "unconscious",
    }
)

_TYPO_TRANS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
    }
)


def normalize_prose(text: str) -> str:
    """Normalize typographic punctuation for deterministic regex extraction."""
    return text.translate(_TYPO_TRANS)


def compact_json(obj: Any) -> str:
    """Serialize deterministic compact JSON with sorted keys and trailing LF."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def pretty_json(obj: Any) -> str:
    """Serialize deterministic pretty JSON with sorted keys and trailing LF."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_text_lf(path: Path, text: str) -> None:
    """Write UTF-8 text strictly with LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(normalized.encode("utf-8"))


def resolve_raw_paths(raw_dir: Path) -> tuple[Path, Path]:
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


def compute_source_hash(en_dir: Path) -> str:
    """Compute exact Stage 1 deterministic SHA-256 across all 25 5e-SRD-*.json files."""
    files = sorted(en_dir.glob("5e-SRD-*.json"), key=lambda p: p.name)
    if not files:
        raise FileNotFoundError(f"No 5e-SRD-*.json files found in {en_dir}")
    hasher = hashlib.sha256()
    for path in files:
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def load_raw_json(en_dir: Path, filename: str) -> list[dict[str, Any]]:
    """Load a raw SRD JSON list file."""
    return json.loads((en_dir / filename).read_text(encoding="utf-8"))


def make_provenance(
    source_id: str,
    source_file: str,
    parser: str,
    unmodeled_clauses: list[str] | None = None,
) -> dict[str, Any]:
    """Build standard §3.2 provenance block."""
    clauses = sorted(set(unmodeled_clauses or []))
    return {
        "source_id": source_id,
        "source_file": source_file,
        "parser": parser,
        "unmodeled_clauses": clauses,
    }


def ability_mod(score: int) -> int:
    """Standard 5e ability modifier."""
    return (score - 10) // 2


_AOE_LINE_RE = re.compile(
    r"(\d+)-foot-long,\s*(\d+)-foot-wide\s+Line",
    re.IGNORECASE,
)
_AOE_CYL_RE = re.compile(
    r"(\d+)-foot-radius(?:,\s*(\d+)-foot-high)?\s+Cylinder",
    re.IGNORECASE,
)
_AOE_SPHERE_RE = re.compile(
    r"(\d+)-foot-radius\s+Sphere",
    re.IGNORECASE,
)
_AOE_SIMPLE_RE = re.compile(
    r"(\d+)-foot(?:-radius)?\s+(Cone|Cube|Emanation|Sphere|Cylinder|Line)\b",
    re.IGNORECASE,
)


def parse_aoe(text: str) -> dict[str, Any] | None:
    """Extract standard 5e-2024 Area of Effect geometry from prose."""
    norm = normalize_prose(text)
    m = _AOE_LINE_RE.search(norm)
    if m:
        return {
            "shape": "line",
            "size_ft": int(m.group(1)),
            "width_ft": int(m.group(2)),
        }
    m = _AOE_CYL_RE.search(norm)
    if m:
        return {
            "shape": "cylinder",
            "size_ft": int(m.group(1)),
            "width_ft": int(m.group(2)) if m.group(2) else None,
        }
    m = _AOE_SPHERE_RE.search(norm)
    if m:
        return {
            "shape": "sphere",
            "size_ft": int(m.group(1)),
            "width_ft": None,
        }
    m = _AOE_SIMPLE_RE.search(norm)
    if m:
        shape = m.group(2).lower()
        return {
            "shape": shape,
            "size_ft": int(m.group(1)),
            "width_ft": 5 if shape == "line" else None,
        }
    return None


def clean_dice_expr(raw: str) -> str:
    """Normalize dice expression spacing, e.g. '1d6 + 2' -> '1d6+2'."""
    return re.sub(r"\s+", "", normalize_prose(raw)).lower()
