"""Command-line interface for the Agent-Native 5e 2024 SRD Pack (`bin/srd`)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .engine import SRDStore
from .render import render_operation


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


def build_parser() -> argparse.ArgumentParser:
    # Shared parent allowing --db after subcommands without clobbering top-level --db
    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument(
        "--db",
        dest="db_sub",
        default=None,
        help="Path to srd2024.sqlite (overrides SRD2024_DB and default dist/srd2024.sqlite).",
    )
    sub_common.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )

    parser = argparse.ArgumentParser(
        prog="srd",
        description="Zero-server CLI for querying the 5e 2024 (SRD 5.2) SQLite pack.",
    )
    parser.add_argument(
        "--db",
        dest="db",
        default=None,
        help="Path to srd2024.sqlite (overrides SRD2024_DB and default dist/srd2024.sqlite).",
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    db_override = args.db_sub if args.db_sub is not None else args.db
    fmt = args.format

    try:
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
