# ADR 0002: Pages deployment, kept off until sign-off

- Status: Accepted
- Date: 2026-11-27

## Context

The static site reads `../dist/` from `web/` and the footer links to `../bin/srd`, `../bin/srd-mcp`, and `../skill/SKILL.md`. GitHub Pages has to publish that sibling layout. Publishing the `web/` directory alone would break those relative URLs. Publishing the whole repository would ship the SQLite database, sources, and tests.

Pages is a public URL. Turning it on is a product decision, not a side effect of merging a workflow.

## Decision

`.github/workflows/pages.yml` deploys only when someone runs it by hand (`workflow_dispatch`). It has no `push` trigger. The workflow file does not enable Pages, and this change does not change repository settings.

The build and deploy jobs run only when `github.ref` is `refs/heads/main` or a release tag (`refs/tags/`), and only after the `main-only` job succeeds. A separate step fails the workflow immediately for any other ref, including a pull-request branch. A commit SHA is not a valid dispatch ref. The workflow token starts from `permissions: {}`. `build` receives only `contents: read`. `deploy` receives only `pages: write` and `id-token: write`, and it is the only job with the `github-pages` environment. `deploy` needs `build`, so the Pages artifact exists before `actions/deploy-pages` lists it. Every action is pinned to a full commit SHA. `concurrency.group` is `pages`.

The published root is `index.html`. It has the same Content Security Policy as `web/index.html`, no script, a meta refresh to `web/`, and a plain link. Without that file, `https://pshouse.github.io/agent-srd-2024/` 404s even while the app is live.

Before upload, the build job runs the Node tests and the Python tests, then builds `_site/` from the allowlist in `scripts/site_files.py`:

- `index.html`
- `web/` runtime files (HTML, CSS, app, `lib/*.js`, vendored markdown-it and its license)
- `dist/manifest.json`, `dist/web/index.json`, `dist/web/graph.json`, `dist/web/types/*.json`, `dist/cards/<type>/<slug>.md`
- `dist/replays/index.json` and `dist/replays/*.trace.json` (M5 replay viewer; generated from git-tracked traces by `scripts/build_replay_index.py`, each listed by SHA-256)
- `bin/srd`, `bin/srd-mcp`
- `skill/SKILL.md`

`scripts/check_site.sh` then refuses the tree if any file is outside that list, any symlink is present in `_site` or in an allowlisted source path, any file is over 5 MB, a `.env` / key / pem-like name appears, or `dataset_id` / counts in the manifest disagree with the expected dataset or with the index and graph sitting next to it. It also refuses the tree if `dist/replays/index.json` is malformed, lists a file that is not staged, misses a staged trace, or records a SHA-256 that does not match the staged copy.

`.github/workflows/ci.yml` runs on pull requests and on pushes to `main`. It runs the same tests, checks the vendored markdown-it SHA-256, and on a push to `main` runs `gitleaks git` over a full-history checkout (`fetch-depth: 0`) of gitleaks 8.30.1 after verifying the release checksum. It does not deploy.

The kill switch, the 404 check, and rollback are in `docs/SECURITY-PAGES.md`.

## Consequences

- Merging this ADR does not publish the site. Enabling Pages, and the first dispatch, wait for the CTO and Patrick.
- A later change that adds `on: push` is a separate decision. It is not implied by this workflow.
- The published URL, once it exists, will be `https://pshouse.github.io/agent-srd-2024/`. Hash routes keep working under that base path because the app ignores the path before the hash.
