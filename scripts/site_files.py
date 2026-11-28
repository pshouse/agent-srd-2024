"""Assemble and check the GitHub Pages staging tree.

The allowlist is the only set of files that may be published. It is the
runtime set the browser fetches or that the footer links to: the static
app, the generated catalog and cards, the two local tools the footer
names, and the skill page. The SQLite database, sources, tests, and
design mocks are not on it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

# Explicit publish set. Globs only name runtime files; they are not a denylist.
ALLOWLIST = (
    "index.html",
    "web/index.html",
    "web/styles.css",
    "web/app.js",
    "web/lib/*.js",
    "web/vendor/markdown-it/markdown-it.esm.min.mjs",
    "web/vendor/markdown-it/LICENSE",
    "dist/manifest.json",
    "dist/web/index.json",
    "dist/web/graph.json",
    "dist/web/types/*.json",
    "dist/cards/*/*.md",
    "dist/replays/index.json",
    "dist/replays/*.trace.json",
    "bin/srd",
    "bin/srd-mcp",
    "skill/SKILL.md",
)

MAX_BYTES = 5 * 1024 * 1024
EXPECTED_DATASET_ID = "srd-2024-5.2"
EXPECTED_SCHEMA_VERSION = "1"
EXPECTED_ENTITIES = 2029
EXPECTED_EDGES = 7614

SENSITIVE = re.compile(
    r"(^|/)\.env($|[._-])"
    r"|(^|/)\.pem$"
    r"|\.(pem|key|p12|pfx|keystore)$"
    r"|(^|/)id_rsa($|\.)"
    r"|(^|/)[^/]*\.pem$"
    r"|(^|/)[^/]*private.*\.key$",
    re.IGNORECASE,
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _refuse_symlink(repo: Path, path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            rel = current.relative_to(repo).as_posix() if current.is_relative_to(repo) else str(current)
            raise SystemExit(f"allowlisted path is a symlink: {rel}")
        if current == repo or current == current.parent:
            break
        current = current.parent


def expand_allowlist(repo: Path) -> list[str]:
    """Return repo-relative paths matched by the allowlist, in sorted order."""
    found: list[str] = []
    for pattern in ALLOWLIST:
        matches = sorted(p for p in repo.glob(pattern) if p.is_file() or p.is_symlink())
        if not matches:
            raise SystemExit(f"allowlist pattern matched nothing: {pattern}")
        for path in matches:
            _refuse_symlink(repo, path)
            rel = path.relative_to(repo).as_posix()
            if rel not in found:
                found.append(rel)
    return sorted(found)


def assemble(repo: Path, site: Path) -> list[str]:
    """Copy the allowlist into site and return the relative paths."""
    paths = expand_allowlist(repo)
    if site.exists():
        shutil.rmtree(site)
    for rel in paths:
        src = repo / rel
        dest = site / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return paths


def _sensitive(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name == ".env" or name.startswith(".env."):
        return True
    return SENSITIVE.search(rel) is not None


def check(repo: Path, site: Path) -> list[str]:
    """Return a list of problems. An empty list means the tree may be uploaded."""
    errors: list[str] = []
    if not site.is_dir():
        return [f"site directory is missing: {site}"]
    try:
        expected = set(expand_allowlist(repo))
    except SystemExit as exc:
        return [str(exc)]

    actual: list[str] = []
    for path in site.rglob("*"):
        rel = path.relative_to(site).as_posix()
        if path.is_symlink():
            errors.append(f"symlink: {rel}")
            continue
        if not path.is_file():
            continue
        actual.append(rel)
        if rel not in expected:
            errors.append(f"outside allowlist: {rel}")
        if path.stat().st_size > MAX_BYTES:
            errors.append(f"over 5 MB: {rel}")
        if _sensitive(rel):
            errors.append(f"sensitive file: {rel}")
    for rel in sorted(expected - set(actual)):
        errors.append(f"missing required file: {rel}")
    errors.extend(_check_manifest(site))
    errors.extend(_check_replays(site))
    return errors


def _check_replays(site: Path) -> list[str]:
    """The replay index must list exactly the staged traces, each under 5 MB, by SHA-256."""
    replay_dir = site / "dist" / "replays"
    index_path = replay_dir / "index.json"
    if not index_path.is_file():
        return ["missing dist/replays/index.json"]
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"replays/index.json is not JSON: {exc}"]
    if not isinstance(index, dict) or index.get("schema_version") != "1":
        return ["replays/index.json schema_version is not '1'"]
    if index.get("dataset_id") != EXPECTED_DATASET_ID:
        return [f"replays/index.json dataset_id is {index.get('dataset_id')!r}"]
    rows = index.get("replays")
    if not isinstance(rows, list):
        return ["replays/index.json replays is not a list"]
    errors: list[str] = []
    listed: set[str] = set()
    for row in rows:
        name = row.get("file") if isinstance(row, dict) else None
        if not isinstance(name, str) or "/" in name or not name.endswith(".trace.json"):
            errors.append(f"replays/index.json has a bad file entry: {name!r}")
            continue
        listed.add(name)
        path = replay_dir / name
        if not path.is_file():
            errors.append(f"replay listed but not staged: {name}")
            continue
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            errors.append(f"over 5 MB: dist/replays/{name}")
        if hashlib.sha256(data).hexdigest() != row.get("sha256"):
            errors.append(f"replay sha256 does not match index: {name}")
    staged = {p.name for p in replay_dir.glob("*.trace.json")}
    for name in sorted(staged - listed):
        errors.append(f"replay staged but not in index: {name}")
    return errors


def _check_manifest(site: Path) -> list[str]:
    manifest_path = site / "dist" / "manifest.json"
    index_path = site / "dist" / "web" / "index.json"
    graph_path = site / "dist" / "web" / "graph.json"
    errors: list[str] = []
    if not manifest_path.is_file():
        return ["missing dist/manifest.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"manifest.json is not JSON: {exc}"]
    if manifest.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        errors.append(
            f"manifest schema_version is {manifest.get('schema_version')!r} "
            f"(expected {EXPECTED_SCHEMA_VERSION!r})"
        )
    if manifest.get("dataset_id") != EXPECTED_DATASET_ID:
        errors.append(
            f"manifest dataset_id is {manifest.get('dataset_id')!r} "
            f"(expected {EXPECTED_DATASET_ID!r})"
        )
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    if counts.get("entities") != EXPECTED_ENTITIES:
        errors.append(
            f"manifest counts.entities is {counts.get('entities')!r} "
            f"(expected {EXPECTED_ENTITIES})"
        )
    if counts.get("edges") != EXPECTED_EDGES:
        errors.append(
            f"manifest counts.edges is {counts.get('edges')!r} (expected {EXPECTED_EDGES})"
        )
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            entities = index.get("entities")
            count = len(entities) if isinstance(entities, list) else None
            if count != counts.get("entities"):
                errors.append(
                    f"index entity count {count} does not match manifest counts.entities "
                    f"{counts.get('entities')}"
                )
        except json.JSONDecodeError as exc:
            errors.append(f"index.json is not JSON: {exc}")
    if graph_path.is_file():
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            edges = graph.get("edges")
            nodes = graph.get("nodes")
            edge_count = len(edges) if isinstance(edges, list) else None
            node_count = len(nodes) if isinstance(nodes, dict) else None
            if edge_count != counts.get("edges"):
                errors.append(
                    f"graph edge count {edge_count} does not match manifest counts.edges "
                    f"{counts.get('edges')}"
                )
            if node_count != counts.get("entities"):
                errors.append(
                    f"graph node count {node_count} does not match manifest counts.entities "
                    f"{counts.get('entities')}"
                )
        except json.JSONDecodeError as exc:
            errors.append(f"graph.json is not JSON: {exc}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"assemble", "check"}:
        print("usage: site_files.py assemble|check <site-dir>", file=sys.stderr)
        return 2
    repo = repo_root()
    site = Path(argv[2]).resolve() if len(argv) > 2 else repo / "_site"
    if argv[1] == "assemble":
        paths = assemble(repo, site)
        print(f"assembled {len(paths)} files into {site}")
        return 0
    errors = check(repo, site)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"{site} matches the allowlist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
