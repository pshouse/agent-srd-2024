"""CLI entrypoint for `srd-build` (`python3 -m srd2024.compiler`)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import build_all


def _default_repo_root() -> Path:
    # src/srd2024/compiler/__main__.py -> repo root is 3 parents up
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    repo_root = _default_repo_root()
    parser = argparse.ArgumentParser(
        prog="srd-build",
        description="Compile 5e 2024 (SRD 5.2) raw JSON into deterministic SQLite, cards, web bundle, and manifest.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=repo_root / "data" / "raw",
        help="Path to raw data directory (default: <repo_root>/data/raw)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=repo_root / "dist",
        help="Path to output artifact directory (default: <repo_root>/dist)",
    )
    args = parser.parse_args(argv)

    manifest = build_all(args.raw_dir.resolve(), args.out_dir.resolve())
    counts = manifest["counts"]
    print(
        f"Built {manifest['dataset_id']} -> {args.out_dir} "
        f"({counts['entities']} entities, {counts['edges']} edges, "
        f"{counts['facets']} facets, {counts['dangling_refs']} dangling, "
        f"{counts['cards']} cards, {counts['web_type_shards']} web shards)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
