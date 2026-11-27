"""Pure-stdlib stdio MCP server (JSON-RPC 2.0) for Agent-Native 5e 2024 SRD Pack (§9.3 & Stage 2 §6.2)."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from .arena_bridge import (
    execute_arena_bench,
    execute_arena_run,
    execute_arena_scenarios,
    get_arena_scenarios_resource,
)
from .engine import SRDStore
from .render import render_operation
from .rules_bridge import (
    execute_compile_character,
    execute_encounter_step,
    get_coverage_payload,
    get_rule_resource,
    load_rules_store,
    query_rule,
)

SERVER_NAME = "agent-srd-2024"
SERVER_VERSION = "0.4.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
ENV_ENGINE_VAR = "SRD2024_ENGINE"
ENV_ARENA_VAR = "SRD2024_ARENA"
ENV_ARENA_ALLOW_LLM_VAR = "SRD2024_ARENA_ALLOW_LLM"

_ARENA_TOOL_NAMES = frozenset({"srd_arena_scenarios", "srd_arena_run", "srd_arena_bench"})
_MCP_MAX_LINE_CHARS = 256 * 1024
_MCP_MAX_SEEDS = 10
_MCP_MAX_ROUNDS = 50
_MCP_MAX_SCENARIO_ID_CHARS = 256
_MCP_MAX_INLINE_SCENARIO_CHARS = 64 * 1024
_MCP_MAX_AGENT_SPEC_CHARS = 256
_MCP_MAX_ERROR_CHARS = 300
# seeds × scenarios × agents × rounds. 15 is the longest built-in scenario.
_MCP_MAX_BENCH_WORK = 500
_MCP_ASSUMED_ROUNDS = 15

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "srd_search",
        "description": (
            "Search the 5e 2024 (SRD 5.2) dataset using ranked full-text search with "
            "typographic normalization. Returns compact entity stubs (id, type, name, summary) "
            "— never full cards."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (handles punctuation and curly/straight apostrophes safely).",
                },
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of resource types to restrict search (e.g. ['spells', 'monsters']).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": "Maximum number of results to return (1..50, default 10).",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "srd_get",
        "description": (
            "Fetch a 5e 2024 SRD entity plus its depth-N referenced rules (weapon masteries, "
            "properties, conditions, damage types, magic schools, etc.) in a single "
            "token-budgeted resolved bundle."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Canonical ID ('type:slug'), API URL ('/api/2024/...'), bare slug, or exact name.",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 2,
                    "default": 1,
                    "description": "Outgoing BFS dependency depth (0..2, default 1).",
                },
                "max_tokens": {
                    "type": "integer",
                    "minimum": 200,
                    "maximum": 16000,
                    "default": 2000,
                    "description": "Target token budget for the resolved bundle (200..16000, default 2000).",
                },
                "include_inbound": {
                    "type": "boolean",
                    "default": False,
                    "description": "Optionally append up to 10 compact inbound references.",
                },
                "rels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional whitelist of outgoing relation names to follow.",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "srd_list",
        "description": (
            "Discover all 25 SRD resource types and their filterable facet keys (when type is omitted), "
            "or list entities of a specific type filtered by facets (AND across keys, OR within a key)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Optional resource type (e.g. 'spells', 'equipment', 'monsters'). Omit for full catalog.",
                },
                "filters": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ]
                    },
                    "description": "Facet key -> value (or array of OR values), e.g. {'level': '3', 'class': 'wizard'}.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                    "description": "Maximum entities to return (1..200, default 50).",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Pagination offset (>=0, default 0).",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "srd_graph",
        "description": (
            "Inspect outgoing and/or inbound cross-reference edges around an entity "
            "(e.g. direction='in' on 'weapon-mastery-properties:sap' to find all Sap weapons)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Canonical ID ('type:slug'), API URL, bare slug, or exact name.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["out", "in", "both"],
                    "default": "both",
                    "description": "Traversal direction: 'out', 'in', or 'both'.",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2,
                    "default": 1,
                    "description": "Graph traversal depth (1..2, default 1).",
                },
                "rel": {
                    "type": "string",
                    "description": "Optional exact relation name filter.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                    "description": "Maximum edges to return (1..100, default 50).",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "srd_validate",
        "description": (
            "Batch-resolve 1..50 SRD entity identifiers into valid/ambiguous/invalid buckets, "
            "report inter-entity graph links, surface structural/feat-prerequisite advisories, "
            "and attach CC-BY-4.0 dataset provenance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 50,
                    "description": "List of 1..50 entity identifiers to validate.",
                },
                "check_links": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to inspect inter-entity links and structural/feat advisories.",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
    },
]

STAGE2_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "srd_rule",
        "description": (
            "Fetch compiled deterministic 5e 2024 Rules IR (with explicit full/partial/narrative "
            "coverage and provenance) for a single entity, or inspect the global rules coverage summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Canonical ID (e.g. 'spells:fireball', 'monsters:goblin-warrior'), bare slug, or 'coverage'.",
                },
                "coverage": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return the global compiled rules coverage summary.",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "srd_compile_character",
        "description": (
            "Deterministically validate and compile a 5e 2024 CharacterBuildSpec JSON into an "
            "authoritative derived statblock (abilities, AC, HP, saves, skills, weapon mastery "
            "profiles, spellcasting) plus structured validation errors and warnings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "description": "CharacterBuildSpec JSON object (species, background, background_asi, class, level, base_abilities, equipment, etc.).",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "required": ["spec"],
            "additionalProperties": False,
        },
    },
    {
        "name": "srd_encounter",
        "description": (
            "Advance the deterministic 5e 2024 combat state machine with a structured intent "
            "(start_encounter, move, attack, cast_spell, dodge, dash, disengage, help, end_turn). "
            "Supports both stateless 'state' round-tripping and stateful server-side 'session_id' caching."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "object",
                    "description": "Structured combat intent object with 'type' and intent-specific parameters.",
                },
                "state": {
                    "type": "object",
                    "description": "Current EncounterState JSON (omit for 'start_encounter' or when using 'session_id').",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional server-side session key to cache EncounterState across turns.",
                },
                "roll_overrides": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional deterministic die override queue for exact testing or replay.",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "required": ["intent"],
            "additionalProperties": False,
        },
    },
]

ALL_TOOL_DEFINITIONS: list[dict[str, Any]] = TOOL_DEFINITIONS + STAGE2_TOOL_DEFINITIONS

STAGE3_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "srd_arena_scenarios",
        "description": (
            "List built-in 5e 2024 (SRD 5.2) multi-agent tabletop arena scenarios with tier, "
            "party level, roster summaries, and tactical evaluation themes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "srd_arena_run",
        "description": (
            "Run a deterministic multi-agent 5e 2024 (SRD 5.2) arena match adjudicated strictly "
            "by the rules engine, returning a Tabletop Session Transcript (Markdown) or full "
            "MatchTranscript JSON with quantitative 0-100 evaluation scorecard and replay digest."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "string",
                    "description": (
                        "Built-in scenario ID ('goblin-ambush', 'crypt-of-ghouls', 'wyrmling-lair') "
                        "or inline scenario JSON (64KB cap). Filesystem paths are rejected."
                    ),
                },
                "party_agent": {
                    "type": "string",
                    "default": "heuristic-tactical",
                    "description": (
                        "Party driver spec ('heuristic-tactical', 'greedy', 'random', "
                        "'openai-compat:<model>', 'anthropic:<model>', 'gemini:<model>', 'typesafe:<model>')."
                    ),
                },
                "enemy_agent": {
                    "type": "string",
                    "default": "heuristic-tactical",
                    "description": "Enemy driver spec (default: 'heuristic-tactical').",
                },
                "seed": {
                    "type": "integer",
                    "default": 42,
                    "description": "Deterministic PRNG seed (default: 42).",
                },
                "max_rounds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional override for maximum combat rounds.",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "required": ["scenario"],
            "additionalProperties": False,
        },
    },
    {
        "name": "srd_arena_bench",
        "description": (
            "Run a multi-seed, multi-scenario 5e 2024 (SRD 5.2) agent evaluation benchmark "
            "and produce a ranked leaderboard across outcome, rules legality, tactical execution, "
            "and party coordination."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scenarios": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Scenario IDs or JSON paths (default: all 3 built-in scenarios).",
                },
                "agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Contender driver specs (default: ['heuristic-tactical', 'greedy', 'random']).",
                },
                "enemy_agent": {
                    "type": "string",
                    "default": "heuristic-tactical",
                    "description": "Enemy driver spec (default: 'heuristic-tactical').",
                },
                "seeds": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "default": [1, 2, 3],
                    "description": "Integer PRNG seeds (default: [1, 2, 3]).",
                },
                "max_rounds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional override for maximum combat rounds per match.",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Response rendering format.",
                },
            },
            "additionalProperties": False,
        },
    },
]

ALL_ARENA_TOOL_DEFINITIONS: list[dict[str, Any]] = ALL_TOOL_DEFINITIONS + STAGE3_TOOL_DEFINITIONS

STATIC_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "srd://meta",
        "name": "SRD 2024 Metadata & Catalog",
        "description": "Dataset provenance, CC-BY-4.0 attribution, and all 25 resource types with facet keys.",
        "mimeType": "application/json",
    }
]

STAGE2_STATIC_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "srd://rules/coverage",
        "name": "SRD 2024 Rules IR Coverage",
        "description": "Compiled Stage 2 rules coverage summary across weapon masteries, conditions, equipment, spells, monsters, and character creation.",
        "mimeType": "application/json",
    }
]

ALL_STATIC_RESOURCES: list[dict[str, Any]] = STATIC_RESOURCES + STAGE2_STATIC_RESOURCES

STAGE3_STATIC_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "srd://arena/scenarios",
        "name": "SRD 2024 Arena Scenarios",
        "description": "Metadata list of built-in 5e 2024 (SRD 5.2) multi-agent tabletop arena scenarios.",
        "mimeType": "application/json",
    }
]

ALL_ARENA_STATIC_RESOURCES: list[dict[str, Any]] = ALL_STATIC_RESOURCES + STAGE3_STATIC_RESOURCES

RESOURCE_TEMPLATES: list[dict[str, Any]] = [
    {
        "uriTemplate": "srd://card/{type}/{slug}",
        "name": "SRD 2024 Entity Card",
        "description": "Full compiled Markdown card (with YAML frontmatter) for a single SRD entity.",
        "mimeType": "text/markdown",
    }
]

STAGE2_RESOURCE_TEMPLATES: list[dict[str, Any]] = [
    {
        "uriTemplate": "srd://rule/{type}/{slug}",
        "name": "SRD 2024 Compiled Rule IR",
        "description": "Single compiled deterministic Rules IR JSON object for an SRD entity.",
        "mimeType": "application/json",
    }
]

ALL_RESOURCE_TEMPLATES: list[dict[str, Any]] = RESOURCE_TEMPLATES + STAGE2_RESOURCE_TEMPLATES


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _env_flag_enabled(name: str) -> bool:
    val = os.environ.get(name, "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _looks_like_filesystem_path(text: str) -> bool:
    if text.startswith(("/", "\\", "~", ".")) or ".." in text:
        return True
    if "/" in text or "\\" in text:
        return True
    if len(text) >= 3 and text[1] == ":" and text[0].isalpha():
        return True
    return False


def _spec_has_whitespace_or_control(spec: str) -> bool:
    return any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in spec)


def _builtin_scenario_ids() -> set[str]:
    from srd2024.arena.scenarios import BUILTIN_SCENARIO_IDS

    return {sid.lower() for sid in BUILTIN_SCENARIO_IDS}


def _resolve_mcp_scenario(raw: str) -> str | dict[str, Any]:
    """Accept a built-in scenario id or inline JSON. Never a filesystem path."""
    text = raw.strip()
    if not text:
        raise ValueError("Scenario must be a built-in id or inline JSON.")
    if text.startswith("{"):
        if len(text) > _MCP_MAX_INLINE_SCENARIO_CHARS:
            raise ValueError("Inline scenario JSON exceeds 64KB.")
        from srd2024.arena.agents import _json_nesting_exceeds

        if _json_nesting_exceeds(text):
            raise ValueError("JSON nesting exceeds the limit.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Inline scenario JSON is not valid.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Inline scenario JSON must be an object.")
        return parsed
    if len(text) > _MCP_MAX_SCENARIO_ID_CHARS:
        raise ValueError("Scenario id exceeds 256 characters.")
    if _looks_like_filesystem_path(text):
        raise ValueError("Scenario must be a built-in id or inline JSON, not a filesystem path.")
    if text.lower() not in _builtin_scenario_ids():
        raise ValueError("Unknown built-in scenario.")
    return text


def _check_mcp_agent_spec(spec: str, *, allow_llm: bool, rules: Any) -> None:
    """Reject whitespace tricks and any driver that would call a paid LLM.

    The paid-LLM decision uses the object `create_driver` actually returns.
    A second parse of the spec string is not the gate: `openai-compat :gpt-x`
    still constructs `OpenAICompatDriver`.
    """
    if len(spec) > _MCP_MAX_AGENT_SPEC_CHARS:
        raise ValueError("Agent spec exceeds 256 characters.")
    if _spec_has_whitespace_or_control(spec):
        raise ValueError("Agent spec must not contain whitespace or control characters.")
    from srd2024.arena.agents import BaseLLMDriver, create_driver

    driver = create_driver(spec, rules=rules, seed=1)
    if isinstance(driver, BaseLLMDriver) and not allow_llm:
        raise ValueError(
            "Paid LLM drivers require --arena-allow-llm or SRD2024_ARENA_ALLOW_LLM=1 at server start."
        )


def _bench_work_limit_exceeded(
    *,
    n_scenarios: int,
    n_agents: int,
    n_seeds: int,
    rounds: int,
) -> bool:
    return n_scenarios * n_agents * n_seeds * rounds > _MCP_MAX_BENCH_WORK


def _clip_error(message: str) -> str:
    if len(message) <= _MCP_MAX_ERROR_CHARS:
        return message
    return message[:_MCP_MAX_ERROR_CHARS] + "…"


class SRDMCPServer:
    """JSON-RPC 2.0 stdio MCP server backed by read-only SRDStore and lazy Stage 2 RulesStore."""

    def __init__(
        self,
        store: SRDStore,
        *,
        rules_path: str | Path | None = None,
        rules_store: Any | None = None,
        expose_engine_tools: bool = False,
        expose_arena_tools: bool = False,
        allow_arena_llm: bool = False,
    ) -> None:
        self.store = store
        self._rules_path = rules_path
        self._rules_store = rules_store
        self.expose_engine_tools = expose_engine_tools
        self.expose_arena_tools = expose_arena_tools
        self.allow_arena_llm = allow_arena_llm
        self._encounter_sessions: dict[str, dict[str, Any]] = {}

    def _get_rules_store(self) -> Any:
        if self._rules_store is None:
            self._rules_store = load_rules_store(self._rules_path)
        return self._rules_store

    def handle_message(self, msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict):
            return _jsonrpc_error(None, -32600, "Invalid Request: expected JSON object.")

        if msg.get("jsonrpc") != "2.0":
            req_id = msg.get("id")
            return _jsonrpc_error(req_id, -32600, "Invalid Request: 'jsonrpc' must be '2.0'.")

        method = msg.get("method")
        if not isinstance(method, str) or not method:
            return _jsonrpc_error(msg.get("id"), -32600, "Invalid Request: missing 'method'.")

        is_notification = "id" not in msg
        req_id = msg.get("id")
        params = msg.get("params")

        # Notifications produce no response per JSON-RPC 2.0 / MCP spec
        if is_notification:
            return None

        try:
            if method == "initialize":
                return _jsonrpc_result(req_id, self._handle_initialize(params))
            if method == "ping":
                return _jsonrpc_result(req_id, {})
            if method == "tools/list":
                if self.expose_arena_tools:
                    tools = ALL_ARENA_TOOL_DEFINITIONS
                elif self.expose_engine_tools:
                    tools = ALL_TOOL_DEFINITIONS
                else:
                    tools = TOOL_DEFINITIONS
                return _jsonrpc_result(req_id, {"tools": tools})
            if method == "tools/call":
                return _jsonrpc_result(req_id, self._handle_tools_call(params))
            if method == "resources/list":
                if self.expose_arena_tools:
                    resources = ALL_ARENA_STATIC_RESOURCES
                elif self.expose_engine_tools:
                    resources = ALL_STATIC_RESOURCES
                else:
                    resources = STATIC_RESOURCES
                return _jsonrpc_result(req_id, {"resources": resources})
            if method == "resources/templates/list":
                templates = (
                    ALL_RESOURCE_TEMPLATES
                    if (self.expose_arena_tools or self.expose_engine_tools)
                    else RESOURCE_TEMPLATES
                )
                return _jsonrpc_result(req_id, {"resourceTemplates": templates})
            if method == "resources/read":
                return self._handle_resources_read(req_id, params)
            return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")
        except _InvalidParamsError as exc:
            return _jsonrpc_error(req_id, -32602, str(exc))
        except Exception as exc:  # pragma: no cover - defensive loop guard
            detail = _clip_error(str(exc))
            print(f"[srd-mcp] internal error handling {method}: {detail}", file=sys.stderr)
            return _jsonrpc_error(req_id, -32603, f"Internal error: {detail}")

    def _handle_initialize(self, params: Any) -> dict[str, Any]:
        proto = DEFAULT_PROTOCOL_VERSION
        if isinstance(params, dict):
            client_proto = params.get("protocolVersion")
            if isinstance(client_proto, str) and client_proto.strip():
                proto = client_proto.strip()
        return {
            "protocolVersion": proto,
            "capabilities": {
                "tools": {},
                "resources": {},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        }

    def _handle_tools_call(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise _InvalidParamsError("tools/call requires an object params.")
        tool_name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(tool_name, str) or not tool_name:
            raise _InvalidParamsError("tools/call requires a non-empty string 'name'.")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return self._tool_error("Tool 'arguments' must be a JSON object.")

        fmt = arguments.get("format", "markdown")
        if fmt not in ("markdown", "json"):
            return self._tool_error(f"Invalid format '{fmt}'. Expected 'markdown' or 'json'.")

        try:
            if tool_name == "srd_search":
                query = arguments.get("query")
                if not isinstance(query, str):
                    return self._tool_error("srd_search requires string argument 'query'.")
                types = arguments.get("types")
                if types is not None and (
                    not isinstance(types, list) or not all(isinstance(t, str) for t in types)
                ):
                    return self._tool_error("'types' must be an array of strings.")
                limit = _coerce_int(arguments.get("limit", 10), "limit")
                payload = self.store.search(query=query, types=types, limit=limit)
                op = "search"

            elif tool_name == "srd_get":
                identifier = arguments.get("id")
                if not isinstance(identifier, str) or not identifier.strip():
                    return self._tool_error("srd_get requires non-empty string argument 'id'.")
                depth = _coerce_int(arguments.get("depth", 1), "depth")
                max_tokens = _coerce_int(arguments.get("max_tokens", 2000), "max_tokens")
                include_inbound = _coerce_bool(
                    arguments.get("include_inbound", False), "include_inbound"
                )
                rels = arguments.get("rels")
                if rels is not None and (
                    not isinstance(rels, list) or not all(isinstance(r, str) for r in rels)
                ):
                    return self._tool_error("'rels' must be an array of strings.")
                payload = self.store.get_bundle(
                    identifier=identifier,
                    depth=depth,
                    max_tokens=max_tokens,
                    include_inbound=include_inbound,
                    rels=rels,
                )
                op = "get"

            elif tool_name == "srd_list":
                entity_type = arguments.get("type")
                if entity_type is not None and not isinstance(entity_type, str):
                    return self._tool_error("'type' must be a string.")
                filters = arguments.get("filters")
                if filters is not None and not isinstance(filters, dict):
                    return self._tool_error("'filters' must be an object mapping facet keys to values.")
                limit = _coerce_int(arguments.get("limit", 50), "limit")
                offset = _coerce_int(arguments.get("offset", 0), "offset")
                payload = self.store.list_entities(
                    entity_type=entity_type,
                    filters=filters,
                    limit=limit,
                    offset=offset,
                )
                op = "list"

            elif tool_name == "srd_graph":
                identifier = arguments.get("id")
                if not isinstance(identifier, str) or not identifier.strip():
                    return self._tool_error("srd_graph requires non-empty string argument 'id'.")
                direction = arguments.get("direction", "both")
                if not isinstance(direction, str):
                    return self._tool_error("'direction' must be a string ('out', 'in', or 'both').")
                depth = _coerce_int(arguments.get("depth", 1), "depth")
                rel = arguments.get("rel")
                if rel is not None and not isinstance(rel, str):
                    return self._tool_error("'rel' must be a string.")
                limit = _coerce_int(arguments.get("limit", 50), "limit")
                payload = self.store.graph(
                    identifier=identifier,
                    direction=direction,
                    depth=depth,
                    rel=rel,
                    limit=limit,
                )
                op = "graph"

            elif tool_name == "srd_validate":
                ids = arguments.get("ids")
                if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                    return self._tool_error("srd_validate requires 'ids' as an array of strings.")
                check_links = _coerce_bool(arguments.get("check_links", True), "check_links")
                payload = self.store.validate(ids=ids, check_links=check_links)
                op = "validate"

            elif tool_name == "srd_rule":
                identifier = arguments.get("id")
                if identifier is not None and not isinstance(identifier, str):
                    return self._tool_error("'id' must be a string.")
                coverage = _coerce_bool(arguments.get("coverage", False), "coverage")
                rules = self._get_rules_store()
                payload = query_rule(rules, identifier=identifier, coverage=coverage)
                op = "rule"

            elif tool_name == "srd_compile_character":
                spec = arguments.get("spec")
                if not isinstance(spec, dict):
                    return self._tool_error("srd_compile_character requires object argument 'spec'.")
                rules = self._get_rules_store()
                payload = execute_compile_character(rules, spec)
                op = "character"

            elif tool_name == "srd_encounter":
                intent = arguments.get("intent")
                if not isinstance(intent, dict):
                    return self._tool_error("srd_encounter requires object argument 'intent'.")
                state = arguments.get("state")
                if state is not None and not isinstance(state, dict):
                    return self._tool_error("'state' must be a JSON object.")
                session_id = arguments.get("session_id")
                if session_id is not None and (
                    not isinstance(session_id, str) or not session_id.strip()
                ):
                    return self._tool_error("'session_id' must be a non-empty string.")
                roll_overrides = arguments.get("roll_overrides")
                if roll_overrides is not None and (
                    not isinstance(roll_overrides, list)
                    or any(isinstance(x, bool) or not isinstance(x, int) for x in roll_overrides)
                ):
                    return self._tool_error("'roll_overrides' must be an array of integers.")

                rules = self._get_rules_store()
                intent_type = intent.get("type")
                effective_state = state

                if intent_type != "start_encounter" and effective_state is None and session_id is not None:
                    cached = self._encounter_sessions.get(session_id)
                    if cached is None:
                        payload = {
                            "status": "error",
                            "error": (
                                f"Unknown encounter session_id '{session_id}'. "
                                "Call start_encounter with this session_id first or pass 'state'."
                            ),
                        }
                        op = "encounter"
                    else:
                        effective_state = copy.deepcopy(cached)
                        payload = execute_encounter_step(
                            rules,
                            intent=intent,
                            state=effective_state,
                            roll_overrides=roll_overrides,
                        )
                        op = "encounter"
                else:
                    payload = execute_encounter_step(
                        rules,
                        intent=intent,
                        state=effective_state,
                        roll_overrides=roll_overrides,
                    )
                    op = "encounter"

                if (
                    session_id is not None
                    and payload.get("status") == "ok"
                    and isinstance(payload.get("state"), dict)
                ):
                    self._encounter_sessions[session_id] = copy.deepcopy(payload["state"])

            elif tool_name in _ARENA_TOOL_NAMES and not self.expose_arena_tools:
                return self._tool_error(f"Unknown tool: '{tool_name}'.")

            elif tool_name == "srd_arena_scenarios":
                payload = execute_arena_scenarios()
                op = "arena_scenarios"

            elif tool_name == "srd_arena_run":
                scenario = arguments.get("scenario")
                if not isinstance(scenario, str) or not scenario.strip():
                    return self._tool_error("srd_arena_run requires non-empty string argument 'scenario'.")
                party_agent = arguments.get("party_agent", "heuristic-tactical")
                if not isinstance(party_agent, str) or not party_agent.strip():
                    return self._tool_error("'party_agent' must be a non-empty string.")
                enemy_agent = arguments.get("enemy_agent", "heuristic-tactical")
                if not isinstance(enemy_agent, str) or not enemy_agent.strip():
                    return self._tool_error("'enemy_agent' must be a non-empty string.")
                seed = _coerce_int(arguments.get("seed", 42), "seed")
                max_rounds_raw = arguments.get("max_rounds")
                max_rounds = (
                    _coerce_int(max_rounds_raw, "max_rounds")
                    if max_rounds_raw is not None
                    else None
                )
                if max_rounds is not None and (max_rounds < 1 or max_rounds > _MCP_MAX_ROUNDS):
                    return self._tool_error(f"'max_rounds' must be between 1 and {_MCP_MAX_ROUNDS}.")
                try:
                    scenario_ref = _resolve_mcp_scenario(scenario)
                    rules = self._get_rules_store()
                    _check_mcp_agent_spec(party_agent, allow_llm=self.allow_arena_llm, rules=rules)
                    _check_mcp_agent_spec(enemy_agent, allow_llm=self.allow_arena_llm, rules=rules)
                except ValueError as exc:
                    return self._tool_error(str(exc))
                payload = execute_arena_run(
                    rules,
                    scenario=scenario_ref,
                    party_agent=party_agent,
                    enemy_agent=enemy_agent,
                    seed=seed,
                    max_rounds=max_rounds,
                )
                op = "arena_run"

            elif tool_name == "srd_arena_bench":
                scenarios = arguments.get("scenarios")
                if scenarios is not None and (
                    not isinstance(scenarios, list) or not all(isinstance(s, str) for s in scenarios)
                ):
                    return self._tool_error("'scenarios' must be an array of strings.")
                if isinstance(scenarios, list) and len(scenarios) > _MCP_MAX_SEEDS:
                    return self._tool_error(f"'scenarios' must contain at most {_MCP_MAX_SEEDS} ids.")
                agents = arguments.get("agents")
                if agents is not None and (
                    not isinstance(agents, list) or not all(isinstance(a, str) for a in agents)
                ):
                    return self._tool_error("'agents' must be an array of strings.")
                if isinstance(agents, list) and len(agents) > _MCP_MAX_SEEDS:
                    return self._tool_error(f"'agents' must contain at most {_MCP_MAX_SEEDS} specs.")
                enemy_agent = arguments.get("enemy_agent", "heuristic-tactical")
                if not isinstance(enemy_agent, str) or not enemy_agent.strip():
                    return self._tool_error("'enemy_agent' must be a non-empty string.")
                seeds_raw = arguments.get("seeds", [1, 2, 3])
                if not isinstance(seeds_raw, list) or any(
                    isinstance(x, bool) or not isinstance(x, int) for x in seeds_raw
                ):
                    return self._tool_error("'seeds' must be an array of integers.")
                if len(seeds_raw) > _MCP_MAX_SEEDS:
                    return self._tool_error(f"'seeds' must contain at most {_MCP_MAX_SEEDS} integers.")
                max_rounds_raw = arguments.get("max_rounds")
                max_rounds = (
                    _coerce_int(max_rounds_raw, "max_rounds")
                    if max_rounds_raw is not None
                    else None
                )
                if max_rounds is not None and (max_rounds < 1 or max_rounds > _MCP_MAX_ROUNDS):
                    return self._tool_error(f"'max_rounds' must be between 1 and {_MCP_MAX_ROUNDS}.")
                n_scenarios = len(scenarios) if isinstance(scenarios, list) else len(_builtin_scenario_ids())
                n_agents = len(agents) if isinstance(agents, list) else 3
                rounds_for_cap = max_rounds if max_rounds is not None else _MCP_ASSUMED_ROUNDS
                if _bench_work_limit_exceeded(
                    n_scenarios=n_scenarios,
                    n_agents=n_agents,
                    n_seeds=len(seeds_raw),
                    rounds=rounds_for_cap,
                ):
                    return self._tool_error(
                        "Bench work exceeds the limit (seeds × scenarios × agents × rounds)."
                    )
                try:
                    if scenarios is not None:
                        scenarios = [_resolve_mcp_scenario(s) for s in scenarios]
                        if any(not isinstance(s, str) for s in scenarios):
                            return self._tool_error("Bench scenarios must be built-in ids.")
                    rules = self._get_rules_store()
                    if agents is not None:
                        for spec in agents:
                            _check_mcp_agent_spec(spec, allow_llm=self.allow_arena_llm, rules=rules)
                    _check_mcp_agent_spec(enemy_agent, allow_llm=self.allow_arena_llm, rules=rules)
                except ValueError as exc:
                    return self._tool_error(str(exc))
                payload = execute_arena_bench(
                    rules,
                    scenarios=scenarios,
                    agents=agents,
                    enemy_agent=enemy_agent,
                    seeds=seeds_raw,
                    max_rounds=max_rounds,
                )
                op = "arena_bench"

            else:
                return self._tool_error(f"Unknown tool: '{tool_name}'.")
        except (ValueError, FileNotFoundError) as exc:
            return self._tool_error(_clip_error(str(exc)))

        rendered = render_operation(op, payload, fmt=fmt)
        is_error = payload.get("status") == "error"
        return {
            "content": [{"type": "text", "text": rendered}],
            "isError": is_error,
        }

    def _handle_resources_read(self, req_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise _InvalidParamsError("resources/read requires an object params.")
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            raise _InvalidParamsError("resources/read requires a non-empty string 'uri'.")

        if uri == "srd://meta":
            meta_payload = {
                "provenance": self.store.get_provenance(),
                "meta": self.store.get_meta_map(),
                "resource_types": self.store.get_resource_types(),
            }
            return _jsonrpc_result(
                req_id,
                {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": json.dumps(meta_payload, ensure_ascii=False, indent=2),
                        }
                    ]
                },
            )

        if uri == "srd://rules/coverage":
            try:
                rules = self._get_rules_store()
            except FileNotFoundError as exc:
                return _jsonrpc_error(req_id, -32002, str(exc))
            cov = get_coverage_payload(rules)
            return _jsonrpc_result(
                req_id,
                {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": json.dumps(cov["summary"], ensure_ascii=False, indent=2),
                        }
                    ]
                },
            )

        if uri == "srd://arena/scenarios":
            if not self.expose_arena_tools:
                return _jsonrpc_error(req_id, -32002, f"Unknown resource URI: {uri}")
            arena_scenarios_payload = get_arena_scenarios_resource()
            return _jsonrpc_result(
                req_id,
                {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": json.dumps(arena_scenarios_payload, ensure_ascii=False, indent=2),
                        }
                    ]
                },
            )

        card_prefix = "srd://card/"
        if uri.startswith(card_prefix):
            remainder = uri[len(card_prefix) :]
            parts = remainder.split("/")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                return _jsonrpc_error(
                    req_id,
                    -32602,
                    f"Invalid card URI '{uri}'. Expected format: srd://card/<type>/<slug>",
                )
            card_md = self.store.get_card_md(parts[0], parts[1])
            if card_md is None:
                return _jsonrpc_error(req_id, -32002, f"Resource not found: {uri}")
            return _jsonrpc_result(
                req_id,
                {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "text/markdown",
                            "text": card_md,
                        }
                    ]
                },
            )

        rule_prefix = "srd://rule/"
        if uri.startswith(rule_prefix):
            remainder = uri[len(rule_prefix) :]
            parts = remainder.split("/")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                return _jsonrpc_error(
                    req_id,
                    -32602,
                    f"Invalid rule URI '{uri}'. Expected format: srd://rule/<type>/<slug>",
                )
            try:
                rules = self._get_rules_store()
            except FileNotFoundError as exc:
                return _jsonrpc_error(req_id, -32002, str(exc))
            rule_obj = get_rule_resource(rules, parts[0], parts[1])
            if rule_obj is None:
                return _jsonrpc_error(req_id, -32002, f"Resource not found: {uri}")
            return _jsonrpc_result(
                req_id,
                {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": json.dumps(rule_obj, ensure_ascii=False, indent=2),
                        }
                    ]
                },
            )

        return _jsonrpc_error(req_id, -32002, f"Unknown resource URI: {uri}")

    @staticmethod
    def _tool_error(message: str) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": f"Error: {message}"}],
            "isError": True,
        }

    def serve_stdio(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
        in_stream = stdin if stdin is not None else sys.stdin
        out_stream = stdout if stdout is not None else sys.stdout

        for raw_line in in_stream:
            if len(raw_line) > _MCP_MAX_LINE_CHARS:
                resp = _jsonrpc_error(None, -32700, "Parse error: message exceeds 256KB.")
                out_stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out_stream.flush()
                continue
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                resp = _jsonrpc_error(None, -32700, f"Parse error: {exc.msg}")
                out_stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out_stream.flush()
                continue

            resp = self.handle_message(msg)
            if resp is not None:
                out_stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out_stream.flush()

        return 0


class _InvalidParamsError(Exception):
    pass


def _coerce_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Argument '{name}' must be an integer.")
    return value


def _coerce_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Argument '{name}' must be a boolean.")
    return value


def main(argv: Sequence[str] | None = None, *, default_engine: bool = False) -> int:
    prog = "srd-engine-mcp" if default_engine else "srd-mcp"
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Stdio JSON-RPC 2.0 MCP server for the 5e 2024 (SRD 5.2) SQLite pack and Stage 2 rules engine.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to srd2024.sqlite (overrides SRD2024_DB and default dist/srd2024.sqlite).",
    )
    parser.add_argument(
        "--rules",
        default=None,
        help="Path to rules.json (overrides SRD2024_RULES and default dist/rules.json).",
    )
    parser.add_argument(
        "--engine",
        action="store_true",
        default=default_engine,
        help="Advertise Stage 2 deterministic rules/character/encounter tools and resources in MCP discovery.",
    )
    parser.add_argument(
        "--arena",
        action="store_true",
        default=False,
        help=(
            "Advertise and enable arena MCP tools and srd://arena/scenarios. "
            "Without this flag those tools are not advertised or callable."
        ),
    )
    parser.add_argument(
        "--arena-allow-llm",
        action="store_true",
        default=False,
        help=(
            "Allow MCP arena calls to select a paid LLM driver "
            "(openai-compat, anthropic, gemini, typesafe). Requires --arena. "
            "Default: deterministic built-in drivers only."
        ),
    )
    args = parser.parse_args(argv)

    expose_engine = bool(args.engine) or _env_flag_enabled(ENV_ENGINE_VAR)
    expose_arena = bool(args.arena) or _env_flag_enabled(ENV_ARENA_VAR)
    allow_arena_llm = bool(args.arena_allow_llm) or _env_flag_enabled(ENV_ARENA_ALLOW_LLM_VAR)

    try:
        with SRDStore(db_path=args.db) as store:
            server = SRDMCPServer(
                store,
                rules_path=args.rules,
                expose_engine_tools=expose_engine,
                expose_arena_tools=expose_arena,
                allow_arena_llm=allow_arena_llm,
            )
            return server.serve_stdio()
    except FileNotFoundError as exc:
        print(f"[{prog}] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


def main_engine(argv: Sequence[str] | None = None) -> int:
    """Entry point for `bin/srd-engine-mcp` advertising all 8 Stage 1 + Stage 2 tools."""
    return main(argv, default_engine=True)


if __name__ == "__main__":
    raise SystemExit(main())
