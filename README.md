# 5e 2024 SRD Agent Pack

A zero-server, local-first **5e 2024 (SRD 5.2)** knowledge pack compiled for AI agents, CLIs, and static web frontends. Built from the upstream CC-BY-4.0 English dataset [`5e-bits/5e-srd-api`](https://github.com/5e-bits/5e-srd-api) (`packages/5e-database/src/2024/en`, 25 files, 2,029 entities), replacing Web 2.0 server infrastructure (MongoDB, Express, GraphQL, Docker) with deterministic offline artifacts and pure Python 3.11+ standard-library tooling.

## Key Capabilities

- **Zero dependencies & zero-install execution**: Runs strictly on Python 3.11+ stdlib (`sqlite3` + FTS5/BM25, `json`, `argparse`) with executable wrappers in `bin/` (`bin/srd-build`, `bin/srd`, `bin/srd-mcp`).
- **Single portable SQLite database (`dist/srd2024.sqlite`)**: Normalized entities across all 25 SRD 5.2 resource types, inverted facet index (`entity_facets`), contentless FTS5 search (`entities_fts`) with typographic punctuation normalization (`Dragon's` <-> `Dragon’s`), directed cross-reference graph (`edges` with preserved inline metadata), and CC-BY-4.0 provenance (`meta`).
- **Resolved-bundle query engine**: Single-call lookup (`srd_get`) that returns a root entity plus its depth-1 (configurable `0..2`) referenced mechanical rules under a deterministic token budget with a 3-tier priority scheme and anti-hub guard—eliminating N+1 lookup chatter.
- **Three thin agent surfaces over one read-only engine**:
  - **CLI** (`bin/srd`) — Markdown or JSON output (Stage 1 retrieval + Stage 2 `rule`, `character`, `encounter`)
  - **Stdio MCP Server** (`bin/srd-mcp` / `bin/srd-engine-mcp`) — newline-delimited JSON-RPC 2.0 with 5 Stage 1 tools (or all 8 Stage 1 + Stage 2 tools via `bin/srd-engine-mcp` / `--engine`) + MCP resources
  - **Drop-in Agent Skill** (`skill/SKILL.md`) — progressive disclosure, resolved-bundle & Stage 2 neuro-symbolic playbook
- **Stage 2 Neuro-Symbolic Rules Compiler & Engine (`dist/rules.json`, `src/srd2024/engine/`)**: Offline deterministic prose-to-IR compiler with honest `full` / `partial` / `narrative` coverage grading (`dist/web/rules-coverage.json`), deterministic 5e 2024 character validator/statblock compiler, and pure JSON-serializable combat state machine (all 8 weapon masteries, all 15 conditions, spells, concentration, death saves, seeded RNG).
- **Compiled Markdown cards (`dist/cards/`)**: 2,029 token-tight Markdown + YAML frontmatter files with relative cross-reference links and `dist/cards/INDEX.md` for pure file-reading agents.
- **Zero-server static web exports (`dist/web/`)**: Lean JSON catalog, compact directed graph, and lazy per-type card shards for browser-side explorers and bundle inspectors.

---

## Quickstart

### 1. Build (or Rebuild) Artifacts

```bash
./bin/srd-build
```

Generates deterministic outputs under `dist/`:
- `dist/srd2024.sqlite` — read-only query database
- `dist/cards/INDEX.md` & `dist/cards/<type>/<slug>.md` — 2,029 Markdown cards
- `dist/web/index.json`, `dist/web/graph.json`, `dist/web/types/<type>.json` — static web bundle
- `dist/manifest.json` — inventory, counts, SHA-256 `source_hash`, and attribution

### 2. Query via CLI (`bin/srd`)

```bash
# Discover all 25 resource types and filterable facet keys
./bin/srd list

# Filter spells by facets (AND across keys, OR across repeated keys)
./bin/srd list --type spells --filter level=3 --filter class=wizard --filter school=evocation

# Ranked FTS5 search (handles straight & curly apostrophes + punctuation safely)
./bin/srd search "Dragon's" --limit 5

# Fetch a single-shot resolved bundle (entity + depth-1 mechanical rules under a token budget)
./bin/srd get equipment:longsword --depth 1 --max-tokens 2000

# Reverse cross-reference lookup ("which weapons have Sap mastery?")
./bin/srd graph weapon-mastery-properties:sap --direction in

# Batch-validate a character build or encounter roster with links, advisories & provenance
./bin/srd validate species:elf subspecies:elven-lineage-high-elf classes:wizard spells:fireball shield nonexistent-xyz --format json
```

Every subcommand supports `--format markdown` (default) or `--format json`.

#### Database Path Resolution (`bin/srd` & `bin/srd-mcp`)
1. `--db <path>` CLI flag
2. `SRD2024_DB` environment variable
3. Default `<repo_root>/dist/srd2024.sqlite`

The runtime engine always opens SQLite strictly read-only (`file:<path>?mode=ro`, `uri=True`).

---

## Stage 2 Quickstart: Deterministic Rules Compiler & Engine

Stage 2 adds an offline, deterministic **neuro-symbolic rules layer** (`dist/rules.json`, `src/srd2024/engine/`) so agents translate intent and narrate outcomes while Python executes every 5e 2024 calculation, mastery, condition, spell slot, and dice roll.

### 1. Build Compiled Rules IR (`bin/srd-rules-build`)

```bash
./bin/srd-rules-build
```

Emits deterministic Stage 2 artifacts (also triggered automatically at the end of `./bin/srd-build`):
- `dist/rules.json` — compiled Rules IR across weapon masteries, conditions, equipment, spells, monsters, and character creation tables, with explicit `full` / `partial` / `narrative` coverage & provenance.
- `dist/web/rules-coverage.json` — lean static coverage manifest for dashboards and audits.

### 2. Inspect Rules IR, Compile Characters & Run Encounters (`bin/srd`)

```bash
# Inspect global compiled rules coverage or a single entity's typed Rules IR
./bin/srd rule --coverage
./bin/srd rule spells:fireball --format json

# Validate & compile a 2024 CharacterBuildSpec JSON into an authoritative statblock
./bin/srd character ./character-spec.json --format markdown
./bin/srd character --spec '{"name":"Aldric","species":"human","background":"soldier","background_asi":{"str":2,"con":1},"class":"fighter","level":1,"ability_method":"standard_array","base_abilities":{"str":15,"dex":13,"con":14,"int":8,"wis":12,"cha":10},"equipment":{"armor":"chain-mail","shield":true,"weapons":["longsword"]},"weapon_mastery_weapons":["longsword"]}' --format json

# Advance a deterministic 5e 2024 encounter (stateless --state / --out-state round-trip)
./bin/srd encounter --intent '{"type":"start_encounter","seed":42,"combatants":[{"id":"g1","side":"enemy","monster":"goblin-warrior","position":{"x":5,"y":0}},{"id":"g2","side":"party","monster":"goblin-warrior","position":{"x":0,"y":0}}]}' --out-state /tmp/enc-state.json --format markdown
./bin/srd encounter --state /tmp/enc-state.json --intent '{"type":"end_turn"}' --out-state /tmp/enc-state.json --format json
```

#### Rules IR Path Resolution (`bin/srd`, `bin/srd-mcp`, `bin/srd-engine-mcp`)
1. `--rules <path>` CLI flag
2. `SRD2024_RULES` environment variable
3. Default `<repo_root>/dist/rules.json`

### 3. Stage 2 MCP Server (`bin/srd-engine-mcp` / `bin/srd-mcp --engine`)

- Default `bin/srd-mcp` preserves the exact 5 Stage 1 tools in `tools/list` for strict backwards compatibility while accepting `tools/call` for all 8 tools.
- Launch **`bin/srd-engine-mcp`** (or `bin/srd-mcp --engine`, or set `SRD2024_ENGINE=1`) to advertise all **8 MCP tools** and Stage 2 resources:
  - Tools 6–8: `srd_rule`, `srd_compile_character`, `srd_encounter` (supports both stateless `state` and server-side `session_id` caching)
  - Resources: `srd://rules/coverage` (`application/json`) and `srd://rule/<type>/<slug>` (`application/json`)

```json
{
  "mcpServers": {
    "srd-2024-engine": {
      "command": "/path/to/agent-srd-2024/bin/srd-engine-mcp",
      "args": []
    }
  }
}
```

---

## Stdio MCP Server (`bin/srd-mcp`)

`bin/srd-mcp` implements a pure-stdlib JSON-RPC 2.0 MCP server over newline-delimited JSON on `stdin`/`stdout` (with errors strictly on `stderr`).

### Client Configuration Example

```json
{
  "mcpServers": {
    "srd-2024": {
      "command": "/path/to/agent-srd-2024/bin/srd-mcp",
      "args": []
    }
  }
}
```

Or pass an explicit database path via `"args": ["--db", "/path/to/srd2024.sqlite"]` or `env.SRD2024_DB`.

### Exposed MCP Methods, Tools & Resources

- **Lifecycle:** `initialize`, `notifications/initialized`, `ping`
- **Tools (`tools/list`, `tools/call`):**
  1. `srd_search` — ranked FTS5 search returning compact stubs (`id`, `type`, `name`, `summary`)
  2. `srd_get` — token-budgeted resolved-bundle fetch (`depth` `0..2`, `max_tokens` `200..16000`, `include_inbound`, `rels`)
  3. `srd_list` — 25-type catalog discovery or facet-filtered entity listing
  4. `srd_graph` — outgoing/inbound/both cross-reference graph traversal (`depth` `1..2`, optional `rel`)
  5. `srd_validate` — batch identifier validation (`valid` / `ambiguous` / `invalid`), inter-entity links, structural & feat-prerequisite advisories, and dataset provenance
- **Resources (`resources/list`, `resources/templates/list`, `resources/read`):**
  - `srd://meta` — JSON provenance, metadata, and resource catalog
  - `srd://card/<type>/<slug>` — full compiled Markdown card with YAML frontmatter

---

## Agent Skill (`skill/SKILL.md`)

Point your agent runtime at [`skill/SKILL.md`](skill/SKILL.md) to teach progressive disclosure (`srd_list`/`srd_search` -> `srd_get` resolved bundles -> `srd_graph` reverse lookups -> `srd_validate` build verification), canonical `type:slug` disambiguation, and the file-only `dist/cards/` fallback.

---

## Architecture Overview

```text
data/raw/2024/en/*.json (25 upstream files, 2,029 entities)
        │
        ├──▶ bin/srd-build (src/srd2024/compiler/)
        │    ┌──────────────────────────────────────────────────────────┐
        │    │ dist/srd2024.sqlite   (entities, facets, edges, FTS5)    │
        │    │ dist/cards/<type>/    (2,029 Markdown + YAML cards)      │
        │    │ dist/web/             (index.json, graph.json, types/*)  │
        │    │ dist/manifest.json    (inventory, counts, SHA-256 hash)  │
        │    └──────────────────────────────────────────────────────────┘
        │
        └──▶ bin/srd-rules-build (src/srd2024/rules_compiler/)
             ┌──────────────────────────────────────────────────────────┐
             │ dist/rules.json            (typed Rules IR + coverage)   │
             │ dist/web/rules-coverage.json (static coverage manifest)  │
             └──────────────────────────────────────────────────────────┘
                        │  strictly read-only (zero runtime LLM)
                        ▼  src/srd2024/engine/ + src/srd2024/runtime/
   bin/srd (CLI) │ bin/srd-mcp (5 tools) │ bin/srd-engine-mcp (8 tools) │ skill/SKILL.md
```

See [`docs/STAGE2-SPEC.md`](docs/STAGE2-SPEC.md) for the Stage 2 rules IR, character compiler, combat state machine, and agent surface contract.

### Identifier Resolution & Resolved-Bundle Packing
- **Canonical IDs:** `<type>:<slug>` (disambiguates all 53 cross-type slug collisions such as `spells:shield` vs `equipment:shield` and `classes:druid` vs `monsters:druid`).
- **Resolution order:** exact canonical ID -> exact `/api/2024/...` URL -> bare slug (returns explicit `ambiguous` with sorted candidates if >1 match) -> normalized exact name -> `not_found` with top 5 search suggestions.
- **3-tier bundle priority + anti-hub guard:** Dependencies sort by `(hop ASC, tier ASC, id ASC)`. Tier 1 core mechanics (`conditions`, `damage-types`, `weapon-mastery-properties`, `weapon-properties`, `magic-schools`, `skills`, `ability-scores`, `feats`, `poisons`, `spells`, `traits`) expand in full first; Tier 2/3 hubs larger than 400 estimated tokens automatically degrade to compact 1-line summary stubs rather than crowding out rules. Every Markdown bundle ends with `<!-- bundle: id=... depth=... tokens~=.../... full=... summary=... omitted=... -->`.

---

## Static Frontend Consumption (`dist/web/`)

Static web apps (interactive SRD explorers, cross-reference graph visualizers, resolved-bundle inspectors) can consume `dist/` directly as read-only static JSON with zero custom backend:

- `dist/manifest.json` — entry manifest, counts, CC-BY-4.0 attribution string, and resource type metadata.
- `dist/web/index.json` — lean sorted catalog of all 2,029 entities (`id`, `type`, `slug`, `name`, `summary`, `facets`, `tier`, `card_tokens_est`, `out_deg`, `in_deg`, `card_path`) for instant client-side search and facet filtering.
- `dist/web/graph.json` — compact directed graph (`nodes` map + sorted `[src_id, dst_id, rel, meta]` edge tuples) supporting client-side BFS, reverse lookups, and ego-graphs.
- `dist/web/types/<type>.json` — 25 lazy-loaded per-type shards containing `card_body_md` and `refs`, enabling full browser-side resolved-bundle parity using the same token estimator (`Math.ceil(text.length / 4)`) and anti-hub rule (`tier === 1 || card_tokens_est <= 400`).

See [`docs/SPEC.md`](docs/SPEC.md) for the complete schema and contract specification.

---

## Attribution & Licensing

### Underlying SRD 5.2 Content (CC-BY-4.0)

> This work includes material from the System Reference Document 5.2 (“SRD 5.2”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

### Upstream Structured Dataset (MIT)

Raw 2024 English JSON files under `data/raw/2024/en/` are mirrored from [`5e-bits/5e-srd-api`](https://github.com/5e-bits/5e-srd-api) (`packages/5e-database/src/2024/en`, commit `8a339e73ed5bba9ffec2ad6be9e76b5afa9c1b24`, `5e-database` `v7.10.0`), licensed under the **MIT License** (see [`data/raw/LICENSE.md`](data/raw/LICENSE.md), [`data/raw/ATTRIBUTION.md`](data/raw/ATTRIBUTION.md), and [`data/raw/PROVENANCE.json`](data/raw/PROVENANCE.json)).
