"""CLI entrypoint for `srd-rules-build` (`python3 -m srd2024.rules_compiler`)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import DATASET_ID, build_rules


def _default_repo_root() -> Path:
    # src/srd2024/rules_compiler/__main__.py -> repo root is 3 parents up
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    repo_root = _default_repo_root()
    parser = argparse.ArgumentParser(
        prog="srd-rules-build",
        description="Compile 5e 2024 (SRD 5.2) raw JSON into deterministic Rules IR (rules.json) and coverage manifest.",
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

    summary = build_rules(args.raw_dir.resolve(), args.out_dir.resolve())
    print(
        f"Built Rules IR for {DATASET_ID} -> {args.out_dir} "
        f"({summary['total_compiled']} compiled: "
        f"{summary['full']} full, {summary['partial']} partial, {summary['narrative']} narrative; "
        f"{summary['executable_pct']}% executable)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
