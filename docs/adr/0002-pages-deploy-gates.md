# ADR 0002: Pages deployment, kept off until sign-off

- Status: Accepted
- Date: 2026-11-27

## Context

The static site reads `../dist/` from `web/` and the footer links to `../bin/srd`, `../bin/srd-mcp`, and `../skill/SKILL.md`. GitHub Pages has to publish that sibling layout. Publishing the `web/` directory alone would break those relative URLs. Publishing the whole repository would ship the SQLite database, sources, and tests.

Pages is a public URL. Turning it on is a product decision, not a side effect of merging a workflow.

## Decision

`.github/workflows/pages.yml` deploys only when someone runs it by hand (`workflow_dispatch`). It has no `push` trigger. The workflow file does not enable Pages, and this change does not change repository settings.

The deploy job runs only when `github.ref` is `refs/heads/main` or a release tag (`refs/tags/`). A separate step fails the workflow immediately for any other ref, including a pull-request branch. A commit SHA is not a valid dispatch ref. The job's token starts from `permissions: {}` and receives only `contents: read`, `pages: write`, and `id-token: write`. Every action is pinned to a full commit SHA. `concurrency.group` is `pages`.

The published root is `index.html`. It has the same Content Security Policy as `web/index.html`, no script, a meta refresh to `web/`, and a plain link. Without that file, `https://pshouse.github.io/agent-srd-2024/` 404s even while the app is live.

Before upload, the job runs the Node tests and the Python tests, then builds `_site/` from the allowlist in `scripts/site_files.py`:

- `index.html`
- `web/` runtime files (HTML, CSS, app, `lib/*.js`, vendored markdown-it and its license)
- `dist/manifest.json`, `dist/web/index.json`, `dist/web/graph.json`, `dist/web/types/*.json`, `dist/cards/<type>/<slug>.md`
- `bin/srd`, `bin/srd-mcp`
- `skill/SKILL.md`

`scripts/check_site.sh` then refuses the tree if any file is outside that list, any symlink is present in `_site` or in an allowlisted source path, any file is over 5 MB, a `.env` / key / pem-like name appears, or `dataset_id` / counts in the manifest disagree with the expected dataset or with the index and graph sitting next to it.

`.github/workflows/ci.yml` runs on pull requests and on pushes to `main`. It runs the same tests, checks the vendored markdown-it SHA-256, and on a push to `main` runs `gitleaks git` over a full-history checkout (`fetch-depth: 0`) of gitleaks 8.30.1 after verifying the release checksum. It does not deploy.

The kill switch, the 404 check, and rollback are in `docs/SECURITY-PAGES.md`.

## Consequences

- Merging this ADR does not publish the site. Enabling Pages, and the first dispatch, wait for the CTO and Patrick.
- A later change that adds `on: push` is a separate decision. It is not implied by this workflow.
- The published URL, once it exists, will be `https://pshouse.github.io/agent-srd-2024/`. Hash routes keep working under that base path because the app ignores the path before the hash.
