#!/usr/bin/env bash
# Check a Pages staging tree against the runtime allowlist.
# With no argument, assemble ./_site from this repo and then check it.
# Usage: scripts/check_site.sh [site_dir]
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
site="${1:-"$root/_site"}"

if [[ $# -eq 0 ]]; then
  python3 "$root/scripts/site_files.py" assemble "$site"
fi

python3 "$root/scripts/site_files.py" check "$site"
