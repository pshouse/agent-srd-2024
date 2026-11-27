# ADR 0001: Plain JavaScript frontend, no build step

- Status: Accepted
- Date: 2026-11-26
- Scope approval: Patrick approved the M1 scope on Nov 26, 2026.

## Context

The pack is a Python 3.11+ repository. Milestone M1 adds a static explorer under `web/` that reads `dist/` as a read-only input. The page must carry a strict Content Security Policy, and opening it must not require an install step.

## Decision

The frontend is hand-written ES modules loaded by the browser. There is no npm install, bundler, framework, or TypeScript compile. `web/package.json` exists only so Node treats the files as ES modules when running `node --test`. The only third-party code is markdown-it 15.0.3, vendored under `web/vendor/` and configured with `html: false`.

This is a choice over Vite + TypeScript and over Astro, for these reasons:

- Supply-chain surface. A JS toolchain pulls a package graph this repository otherwise does not have. Vendoring one pinned renderer is the whole external surface.
- CSP. The page forbids inline scripts and styles. A bundler or framework runtime that injects inline boot code or style attributes would fight that policy. Plain modules and one stylesheet do not.
- No JS toolchain in a Python repo. The pack already builds and queries with the Python standard library. The site is served with `python -m http.server`. Node is used only to run the frontend tests.
- Fail-closed checks already covering type safety. The explorer renders no entities unless `schema_version` is `"1"` on both the manifest and the index, `dataset_id` is `srd-2024-5.2` on both, the entity count is 2,029, and the set of entity ids has size 2,029. Those checks are the runtime guard a typechecker would not replace.

## Consequences

- Routes are hashes (`#/` and `#/e/<type>:<slug>`), so the same files work under the Pages base path `/agent-srd-2024/` and from the repo root without a server rewrite.
- GitHub Pages is not enabled in this milestone. There is no Pages workflow.
- The page loads `../dist/` and links to `../bin/` and `../skill/` relative to `web/`. M3 has to publish that repository layout.

## Tests

From the repository root, with no directory argument:

```bash
node --disable-warning=ExperimentalWarning --test
```

`node --test web/tests` and `node --test tests/` fail on Node 24. The runner treats the argument as a module path, and a directory is not a module. Discovery with no path finds `web/tests/*.test.js`.

## Planned milestones

- M2: inspector and Copy Agent Context.
- M2b: visual ego-graph capped at 80 nodes, hand-rolled SVG or canvas, no dependency. Patrick kept this milestone.
- M3: Pages workflow, kept off until sign-off. Publish the repository root so `web/`, `dist/`, and `bin/` stay siblings. Do not set the Pages root to `web/`. A site rooted at `web/` cannot see `../dist` or `../bin`.
