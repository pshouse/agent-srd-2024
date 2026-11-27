"""Read-only query engine, resolved-bundle engine, CLI, and stdio MCP server."""

from .db import EntityRecord, open_readonly_connection, resolve_db_path
from .engine import SRDStore
from .mcp import SRDMCPServer, main_engine
from .render import (
    render_bundle,
    render_character,
    render_encounter,
    render_graph,
    render_json,
    render_list,
    render_operation,
    render_rule,
    render_search,
    render_validate,
)
from .rules_bridge import resolve_rules_path
from .text import TYPE_TIERS, est_tokens, normalize_search_text, tier_for_type

__all__ = [
    "EntityRecord",
    "SRDMCPServer",
    "SRDStore",
    "TYPE_TIERS",
    "est_tokens",
    "main_engine",
    "normalize_search_text",
    "open_readonly_connection",
    "render_bundle",
    "render_character",
    "render_encounter",
    "render_graph",
    "render_json",
    "render_list",
    "render_operation",
    "render_rule",
    "render_search",
    "render_validate",
    "resolve_db_path",
    "resolve_rules_path",
    "tier_for_type",
]
