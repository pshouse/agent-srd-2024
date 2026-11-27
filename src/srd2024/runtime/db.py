"""Read-only SQLite connection and DB path resolution per SPEC §5 & §9.2."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

ENV_DB_VAR = "SRD2024_DB"
DEFAULT_DB_RELPATH = Path("dist") / "srd2024.sqlite"


def repo_root() -> Path:
    """Return the repository root derived from this module's location."""
    return Path(__file__).resolve().parents[3]


def resolve_db_path(explicit_path: str | Path | None = None) -> Path:
    """Resolve SQLite DB path in SPEC §9.2 order: --db -> SRD2024_DB -> default."""
    if explicit_path is not None and str(explicit_path).strip():
        return Path(explicit_path).expanduser().resolve()
    env_val = os.environ.get(ENV_DB_VAR, "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    return (repo_root() / DEFAULT_DB_RELPATH).resolve()


def open_readonly_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open the SQLite database strictly read-only via URI mode=ro."""
    resolved = Path(db_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"SRD SQLite database not found at '{resolved}'. "
            "Run 'bin/srd-build' first or pass --db / set SRD2024_DB."
        )
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    return conn


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """Full entity row loaded from the SQLite `entities` table."""

    rowid: int
    id: str
    type: str
    slug: str
    name: str
    name_norm: str
    api_url: str
    summary: str
    facets: dict[str, Any]
    raw_json: str
    card_md: str
    card_body_md: str
    card_tokens_est: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> EntityRecord:
        facets_raw = row["facets_json"]
        facets = json.loads(facets_raw) if facets_raw else {}
        return cls(
            rowid=int(row["rowid"]),
            id=str(row["id"]),
            type=str(row["type"]),
            slug=str(row["slug"]),
            name=str(row["name"]),
            name_norm=str(row["name_norm"]),
            api_url=str(row["api_url"]),
            summary=str(row["summary"]),
            facets=facets,
            raw_json=str(row["raw_json"]),
            card_md=str(row["card_md"]),
            card_body_md=str(row["card_body_md"]),
            card_tokens_est=int(row["card_tokens_est"]),
        )

    def to_stub(self) -> dict[str, str]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "summary": self.summary,
        }


def parse_meta_json(raw: str | None) -> Any:
    """Parse an edge's optional `meta_json` column safely."""
    if raw is None or raw == "":
        return None
    return json.loads(raw)
