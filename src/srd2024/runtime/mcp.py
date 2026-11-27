"""Pure-stdlib stdio MCP server (JSON-RPC 2.0) for Agent-Native 5e 2024 SRD Pack (§9.3)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any, TextIO

from .engine import SRDStore
from .render import render_operation

SERVER_NAME = "agent-srd-2024"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

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

STATIC_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "srd://meta",
        "name": "SRD 2024 Metadata & Catalog",
        "description": "Dataset provenance, CC-BY-4.0 attribution, and all 25 resource types with facet keys.",
        "mimeType": "application/json",
    }
]

RESOURCE_TEMPLATES: list[dict[str, Any]] = [
    {
        "uriTemplate": "srd://card/{type}/{slug}",
        "name": "SRD 2024 Entity Card",
        "description": "Full compiled Markdown card (with YAML frontmatter) for a single SRD entity.",
        "mimeType": "text/markdown",
    }
]


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


class SRDMCPServer:
    """Stateless JSON-RPC 2.0 stdio MCP server backed by a read-only SRDStore."""

    def __init__(self, store: SRDStore) -> None:
        self.store = store

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
                return _jsonrpc_result(req_id, {"tools": TOOL_DEFINITIONS})
            if method == "tools/call":
                return _jsonrpc_result(req_id, self._handle_tools_call(params))
            if method == "resources/list":
                return _jsonrpc_result(req_id, {"resources": STATIC_RESOURCES})
            if method == "resources/templates/list":
                return _jsonrpc_result(req_id, {"resourceTemplates": RESOURCE_TEMPLATES})
            if method == "resources/read":
                return self._handle_resources_read(req_id, params)
            return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")
        except _InvalidParamsError as exc:
            return _jsonrpc_error(req_id, -32602, str(exc))
        except Exception as exc:  # pragma: no cover - defensive loop guard
            print(f"[srd-mcp] internal error handling {method}: {exc}", file=sys.stderr)
            return _jsonrpc_error(req_id, -32603, f"Internal error: {exc}")

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

            else:
                return self._tool_error(f"Unknown tool: '{tool_name}'.")
        except ValueError as exc:
            return self._tool_error(str(exc))

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

        prefix = "srd://card/"
        if uri.startswith(prefix):
            remainder = uri[len(prefix) :]
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="srd-mcp",
        description="Stdio JSON-RPC 2.0 MCP server for the 5e 2024 (SRD 5.2) SQLite pack.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to srd2024.sqlite (overrides SRD2024_DB and default dist/srd2024.sqlite).",
    )
    args = parser.parse_args(argv)

    try:
        with SRDStore(db_path=args.db) as store:
            server = SRDMCPServer(store)
            return server.serve_stdio()
    except FileNotFoundError as exc:
        print(f"[srd-mcp] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
