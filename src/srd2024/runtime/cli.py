"""Command-line interface for the Agent-Native 5e 2024 SRD Pack (`bin/srd`)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .engine import SRDStore
from .render import render_operation
from .rules_bridge import (
    execute_compile_character,
    execute_encounter_step,
    load_rules_store,
    query_rule,
)


def _parse_filter_args(raw_filters: list[str] | None) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    if not raw_filters:
        return parsed
    for item in raw_filters:
        if "=" not in item:
            raise ValueError(f"Invalid --filter '{item}': expected key=value format.")
        key, val = item.split("=", 1)
        key = key.strip().lower()
        val = val.strip().lower()
        if not key or not val:
            raise ValueError(f"Invalid --filter '{item}': both key and value must be non-empty.")
        parsed.setdefault(key, []).append(val)
    return parsed


def _load_json_input(raw: str, *, label: str) -> Any:
    """Load JSON from '-' (stdin), inline JSON text, or a file path."""
    text = raw.strip()
    if not text:
        raise ValueError(f"Empty {label} input.")
    if text == "-":
        content = sys.stdin.read()
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on stdin for {label}: {exc.msg}") from exc
    if text.startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid inline JSON for {label}: {exc.msg}") from exc
    path = Path(text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: '{path}'")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {label} file '{path}': {exc.msg}") from exc


def _parse_roll_overrides(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid --roll-overrides JSON: {exc.msg}") from exc
        if not isinstance(parsed, list) or any(
            isinstance(x, bool) or not isinstance(x, int) for x in parsed
        ):
            raise ValueError("--roll-overrides must be a JSON array of integers.")
        return parsed
    values: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError as exc:
            raise ValueError(f"Invalid integer '{part}' in --roll-overrides.") from exc
    return values


def build_parser() -> argparse.ArgumentParser:
    # Shared parent allowing --db / --rules after subcommands without clobbering top-level flags
    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument(
        "--db",
        dest="db_sub",
        default=None,
        help="Path to srd2024.sqlite (overrides SRD2024_DB and default dist/srd2024.sqlite).",
    )
    sub_common.add_argument(
        "--rules",
        dest="rules_sub",
        default=None,
        help="Path to rules.json (overrides SRD2024_RULES and default dist/rules.json).",
    )
    sub_common.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )

    parser = argparse.ArgumentParser(
        prog="srd",
        description="Zero-server CLI for querying the 5e 2024 (SRD 5.2) SQLite pack and Stage 2 rules engine.",
    )
    parser.add_argument(
        "--db",
        dest="db",
        default=None,
        help="Path to srd2024.sqlite (overrides SRD2024_DB and default dist/srd2024.sqlite).",
    )
    parser.add_argument(
        "--rules",
        dest="rules",
        default=None,
        help="Path to rules.json (overrides SRD2024_RULES and default dist/rules.json).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. srd search
    p_search = subparsers.add_parser(
        "search",
        parents=[sub_common],
        help="Ranked FTS5 search returning compact entity stubs.",
    )
    p_search.add_argument("query", nargs="+", help="Search query text.")
    p_search.add_argument(
        "--type",
        dest="types",
        action="append",
        default=None,
        help="Filter by resource type (repeatable).",
    )
    p_search.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max results (1..50, default: 10).",
    )

    # 2. srd get
    p_get = subparsers.add_parser(
        "get",
        parents=[sub_common],
        help="Fetch an entity and its depth-N resolved rule dependencies.",
    )
    p_get.add_argument("id", help="Canonical ID (type:slug), API URL, bare slug, or exact name.")
    p_get.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Outgoing dependency expansion depth (0..2, default: 1).",
    )
    p_get.add_argument(
        "--max-tokens",
        type=int,
        default=2000,
        help="Target token budget (200..16000, default: 2000).",
    )
    p_get.add_argument(
        "--inbound",
        action="store_true",
        default=False,
        help="Include up to 10 compact inbound references.",
    )
    p_get.add_argument(
        "--rel",
        dest="rels",
        action="append",
        default=None,
        help="Whitelist of outgoing relation names to traverse (repeatable).",
    )

    # 3. srd list
    p_list = subparsers.add_parser(
        "list",
        parents=[sub_common],
        help="Show the 25-type catalog or list facet-filtered entities of a type.",
    )
    p_list.add_argument(
        "--type",
        dest="entity_type",
        default=None,
        help="Resource type to list (omit to view full catalog and filterable facets).",
    )
    p_list.add_argument(
        "--filter",
        dest="filters",
        action="append",
        default=None,
        help="Facet filter in key=value form (repeatable; AND across keys, OR within same key).",
    )
    p_list.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max results (1..200, default: 50).",
    )
    p_list.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Pagination offset (>=0, default: 0).",
    )

    # 4. srd graph
    p_graph = subparsers.add_parser(
        "graph",
        parents=[sub_common],
        help="Inspect outgoing and/or inbound cross-reference edges.",
    )
    p_graph.add_argument("id", help="Canonical ID (type:slug), API URL, bare slug, or exact name.")
    p_graph.add_argument(
        "--direction",
        choices=("out", "in", "both"),
        default="both",
        help="Edge traversal direction (default: both).",
    )
    p_graph.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Traversal depth (1..2, default: 1).",
    )
    p_graph.add_argument(
        "--rel",
        default=None,
        help="Optional exact relation filter.",
    )
    p_graph.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max edges to return (1..100, default: 50).",
    )

    # 5. srd validate
    p_val = subparsers.add_parser(
        "validate",
        parents=[sub_common],
        help="Batch-resolve entity references, inspect inter-entity links, advisories, and provenance.",
    )
    p_val.add_argument("ids", nargs="+", help="One or more entity identifiers (1..50).")
    p_val.add_argument(
        "--no-links",
        action="store_true",
        default=False,
        help="Skip inter-entity link and advisory checks.",
    )

    # 6. srd rule (Stage 2)
    p_rule = subparsers.add_parser(
        "rule",
        parents=[sub_common],
        help="Fetch compiled typed Rules IR for an entity or global coverage summary.",
    )
    p_rule.add_argument(
        "id",
        nargs="?",
        default=None,
        help="Canonical ID (e.g. spells:fireball), bare slug, or 'coverage'.",
    )
    p_rule.add_argument(
        "--coverage",
        action="store_true",
        default=False,
        help="Display global rules coverage summary.",
    )

    # 7. srd character (Stage 2)
    p_char = subparsers.add_parser(
        "character",
        parents=[sub_common],
        help="Validate and compile a 2024 CharacterBuildSpec JSON into a derived statblock.",
    )
    p_char.add_argument(
        "spec_file",
        nargs="?",
        default=None,
        help="Path to CharacterBuildSpec JSON file (or '-' for stdin).",
    )
    p_char.add_argument(
        "--spec",
        dest="spec_inline",
        default=None,
        help="Inline CharacterBuildSpec JSON string (or file path).",
    )

    # 8. srd encounter (Stage 2)
    p_enc = subparsers.add_parser(
        "encounter",
        parents=[sub_common],
        help="Advance a deterministic 5e-2024 encounter state machine with a structured intent.",
    )
    p_enc.add_argument(
        "payload_file",
        nargs="?",
        default=None,
        help="Path to JSON file containing an intent or {intent, state, roll_overrides} payload (or '-' for stdin).",
    )
    p_enc.add_argument(
        "--intent",
        dest="intent_raw",
        default=None,
        help="Inline intent JSON object (or file path).",
    )
    p_enc.add_argument(
        "--state",
        dest="state_file",
        default=None,
        help="Path to existing EncounterState JSON file (or inline JSON / '-').",
    )
    p_enc.add_argument(
        "--out-state",
        dest="out_state",
        default=None,
        help="Write the updated EncounterState JSON to this file path.",
    )
    p_enc.add_argument(
        "--roll-overrides",
        dest="roll_overrides",
        default=None,
        help="Optional die override queue as JSON array (e.g. '[15,6]') or comma-separated ints.",
    )

    return parser


def _run_stage2_command(args: argparse.Namespace, rules_override: str | None) -> dict[str, Any]:
    rules = load_rules_store(rules_override)

    if args.command == "rule":
        return query_rule(rules, identifier=args.id, coverage=bool(args.coverage))

    if args.command == "character":
        if args.spec_file and args.spec_inline:
            raise ValueError("Specify either a positional spec file or --spec, not both.")
        raw_spec = args.spec_inline if args.spec_inline is not None else args.spec_file
        if raw_spec is None:
            raise ValueError("Provide a character spec JSON file or --spec <json>.")
        spec_obj = _load_json_input(raw_spec, label="character spec")
        if not isinstance(spec_obj, dict):
            raise ValueError("Character spec JSON must be an object.")
        return execute_compile_character(rules, spec_obj)

    if args.command == "encounter":
        intent_obj: dict[str, Any] | None = None
        state_obj: dict[str, Any] | None = None
        roll_overrides: list[int] | None = None

        if args.payload_file is not None:
            loaded_payload = _load_json_input(args.payload_file, label="encounter payload")
            if not isinstance(loaded_payload, dict):
                raise ValueError("Encounter payload JSON must be an object.")
            if "intent" in loaded_payload:
                if not isinstance(loaded_payload["intent"], dict):
                    raise ValueError("Encounter payload 'intent' field must be an object.")
                intent_obj = loaded_payload["intent"]
                if loaded_payload.get("state") is not None:
                    state_obj = loaded_payload["state"]
                if loaded_payload.get("roll_overrides") is not None:
                    ro = loaded_payload["roll_overrides"]
                    if not isinstance(ro, list) or any(
                        isinstance(x, bool) or not isinstance(x, int) for x in ro
                    ):
                        raise ValueError("Encounter payload 'roll_overrides' must be an array of integers.")
                    roll_overrides = ro
            elif "type" in loaded_payload:
                intent_obj = loaded_payload
            else:
                raise ValueError(
                    "Encounter payload JSON must contain an 'intent' object or a direct intent with 'type'."
                )

        if args.intent_raw is not None:
            loaded_intent = _load_json_input(args.intent_raw, label="intent")
            if not isinstance(loaded_intent, dict):
                raise ValueError("--intent must be a JSON object.")
            intent_obj = loaded_intent

        if intent_obj is None:
            raise ValueError("Provide --intent <json> or a positional encounter payload JSON file.")

        if args.state_file is not None:
            loaded_state = _load_json_input(args.state_file, label="encounter state")
            if not isinstance(loaded_state, dict):
                raise ValueError("--state must contain a JSON object.")
            if "combatants" not in loaded_state and isinstance(loaded_state.get("state"), dict):
                state_obj = loaded_state["state"]
            else:
                state_obj = loaded_state

        cli_overrides = _parse_roll_overrides(args.roll_overrides)
        if cli_overrides is not None:
            roll_overrides = cli_overrides

        result = execute_encounter_step(
            rules,
            intent=intent_obj,
            state=state_obj,
            roll_overrides=roll_overrides,
        )

        if args.out_state and isinstance(result.get("state"), dict):
            out_path = Path(args.out_state).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(result["state"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        return result

    raise ValueError(f"Unknown Stage 2 command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    db_override = args.db_sub if args.db_sub is not None else args.db
    rules_override = args.rules_sub if args.rules_sub is not None else args.rules
    fmt = args.format

    try:
        if args.command in ("rule", "character", "encounter"):
            payload = _run_stage2_command(args, rules_override)
        else:
            with SRDStore(db_path=db_override) as store:
                if args.command == "search":
                    query_text = " ".join(args.query)
                    payload = store.search(query=query_text, types=args.types, limit=args.limit)
                elif args.command == "get":
                    payload = store.get_bundle(
                        identifier=args.id,
                        depth=args.depth,
                        max_tokens=args.max_tokens,
                        include_inbound=args.inbound,
                        rels=args.rels,
                    )
                elif args.command == "list":
                    filters = _parse_filter_args(args.filters)
                    payload = store.list_entities(
                        entity_type=args.entity_type,
                        filters=filters,
                        limit=args.limit,
                        offset=args.offset,
                    )
                elif args.command == "graph":
                    payload = store.graph(
                        identifier=args.id,
                        direction=args.direction,
                        depth=args.depth,
                        rel=args.rel,
                        limit=args.limit,
                    )
                elif args.command == "validate":
                    payload = store.validate(
                        ids=args.ids,
                        check_links=not args.no_links,
                    )
                else:
                    parser.error(f"Unknown command: {args.command}")
                    return 2
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive top-level CLI guard
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output = render_operation(args.command, payload, fmt=fmt)
    sys.stdout.write(output + "\n")
    sys.stdout.flush()

    if payload.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
