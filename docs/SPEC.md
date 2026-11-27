# Agent-Native 5e 2024 SRD Pack — Architecture & Contract Specification

> **Status:** FINAL (locked against `docs/schema-audit.md` and upstream snapshot `5e-bits/5e-srd-api` commit `8a339e73`, `5e-database` `v7.10.0`).

---

## 1. Purpose & Design Principles

Compile the `5e-bits/5e-srd-api` 2024 (SRD 5.2) English JSON dataset (`data/raw/2024/en/`, 25 files, 2,029 entities, 3.07 MiB) into a **zero-server, local-first, agent-native pack**:

1. **Zero Web 2.0 bloat**: No MongoDB, Express, GraphQL, Docker, or background HTTP daemon.
2. **Offline compilation → static deterministic artifacts**:
   - `dist/srd2024.sqlite` — single portable SQLite DB with normalized entities, inverted facet index, FTS5 full-text search, directed cross-reference graph (`edges`), compiled Markdown cards, and CC-BY-4.0 provenance metadata.
   - `dist/cards/<type>/<slug>.md` + `dist/cards/INDEX.md` — token-tight Markdown + YAML frontmatter cards with relative cross-reference links.
   - `dist/web/` — lean static JSON index, compact cross-reference graph, and per-type card shards for zero-server web frontends (§10).
   - `dist/manifest.json` — build inventory, counts, SHA-256 checksums, and graph audit stats.
3. **Eliminate N+1 lookup chatter**: The **resolved-bundle** query engine returns an entity plus its depth-1 (configurable `0..2`) referenced rules in a single compact, token-budgeted payload.
4. **Three thin surfaces over one read-only query engine**:
   - **MCP server** (stdio JSON-RPC 2.0, 5 orthogonal tools)
   - **CLI** (same 5 subcommands, markdown or JSON output)
   - **Agent Skill** (`skill/SKILL.md`, progressive disclosure playbook)
5. **Zero-dependency Python stdlib**: Runs offline immediately with no package manager required.

---

## 2. Runtime Stack & Zero-Install Execution

Locked to **Python 3.11+ (verified on local Python 3.14.8)** using **strictly the Python standard library** (`sqlite3` with SQLite 3.54.0 + FTS5/BM25 + JSON, `json`, `argparse`, `pathlib`, `re`, `math`, `hashlib`, `sys`, `os`, `unittest`).

- **Zero runtime/build dependencies**: Neither `pip`/`uv` nor `npm`/`bun` are installed locally, so neither compiler nor runtime may import third-party packages (`PyYAML`, `mcp`, `pydantic`, `pytest`, etc.).
- **YAML frontmatter**: Compiler emits deterministic YAML frontmatter using pure stdlib formatting (verifier tests may optionally parse it with stdlib or locally installed `PyYAML 6.0.3`).
- **Zero-install executables**: Provide executable Python wrapper scripts in `bin/` (`#!/usr/bin/env python3`, `chmod +x`) that prepend `<repo_root>/src` to `sys.path` and invoke module `main()`, alongside a standard `pyproject.toml` for environments that do use `pip`/`uv`.

---

## 3. Repository Layout & Strict Worker Ownership

Every path has **exactly one owner** so `compiler`, `surface`, and `verifier` can work cleanly without file collisions:

| Path | Owner | Purpose |
| :--- | :--- | :--- |
| `data/raw/**` | `scout` *(read-only)* | Upstream 2024 SRD JSON (25 files), `LICENSE.md`, `ATTRIBUTION.md`, `PROVENANCE.json` |
| `docs/schema-audit.md` | `scout` *(read-only)* | Empirical schema & runtime audit |
| `docs/SPEC.md` | `architect` *(read-only)* | This contract specification |
| `.gitignore` | `repo-ops` | Git ignore rules |
| `pyproject.toml` | `compiler` | Package metadata & `[project.scripts]` (exact contents in §12) |
| `src/srd2024/__init__.py` | `compiler` | Package marker (`__version__ = "0.1.0"`) |
| `src/srd2024/compiler/**` | `compiler` | JSON loader, normalizer, facet/edge extractor, SQLite builder, card renderer, web JSON & manifest writer |
| `bin/srd-build` | `compiler` | Executable build wrapper (`srd2024.compiler.__main__:main`) |
| `dist/**` | `compiler` *(generated)* | `dist/srd2024.sqlite`, `dist/cards/**`, `dist/web/**`, `dist/manifest.json` |
| `src/srd2024/runtime/**` | `surface` | Read-only SQLite query engine, resolved-bundle engine, renderers, CLI, stdio MCP server |
| `bin/srd` | `surface` | Executable CLI wrapper (`srd2024.runtime.cli:main`) |
| `bin/srd-mcp` | `surface` | Executable MCP stdio server wrapper (`srd2024.runtime.mcp:main`) |
| `skill/SKILL.md` | `surface` | Drop-in Agent Skill teaching progressive disclosure & tool usage |
| `README.md` | `surface` *(expands `repo-ops` stub)* | Quickstart, architecture, CLI/MCP/Skill docs, CC-BY-4.0 attribution |
| `tests/**` | `verifier` | `unittest` verification suite covering build, schema, determinism, graph, bundle, CLI, MCP |

**Hard boundary rules:**
- `surface` opens `dist/srd2024.sqlite` strictly read-only (`file:<path>?mode=ro`, `uri=True`) against the schema in §5 and NEVER imports from `srd2024.compiler` or writes to `pyproject.toml` / `src/srd2024/__init__.py`.
- `compiler` writes `dist/*` conforming to §4–§7 and NEVER creates or modifies `src/srd2024/runtime/**`, `bin/srd`, `bin/srd-mcp`, `skill/`, `README.md`, or `tests/`.
- `verifier` NEVER modifies `data/raw/`, `src/`, `bin/`, `skill/`, or `dist/` (runs determinism/build tests in a temp dir or verifies `dist/` non-destructively).

---

## 4. Canonical Data Model, 25 Resource Types & Facets

### 4.1 Canonical Entity ID & URL Map
Because `index` has **53 cross-type collisions** (e.g. `druid` in `classes` & `monsters`; `shield`, `light`, `acid`, `ammunition` across multiple types):

- **Canonical ID:** `<type>:<slug>` (always lowercase)
  - `<type>`: derived from raw filename `5e-SRD-<Name>.json` → lowercase kebab-case (e.g., `5e-SRD-Weapon-Mastery-Properties.json` → `weapon-mastery-properties`, `5e-SRD-Levels.json` → `levels`).
  - `<slug>`: exact `record["index"]`.
- **Authoritative URL index:** All 2,029 entities have a unique `record["url"]`. The compiler MUST build `url_to_id: dict[str, str]` from all loaded records before extracting edges. (Critical because `levels` uses nested URLs like `/api/2024/subclasses/berserker/levels/3` where the URL slug differs from `subclass.index = "path-of-the-berserker"`.)

### 4.2 Runtime Identifier Resolution Order
Every query taking an entity identifier (`srd_get`, `srd_graph`, `srd_validate`) resolves input `q` (trimmed) in this exact order:
1. **Exact canonical ID**: `entities.id = lower(q)` (if `:` in `q`).
2. **Exact API URL**: `entities.api_url = q` (if `q.startswith("/api/")`).
3. **Bare slug**: `entities.slug = lower(q)`:
   - 1 match → resolved.
   - >1 matches → return `status: "ambiguous"` with sorted `candidates: [{id, type, name, summary}]`. Never guess silently.
4. **Exact name match** (case-insensitive, typographic punctuation normalized per §4.4):
   - 1 match → resolved.
   - >1 matches → return `status: "ambiguous"` with candidates.
5. **Not found**: return `status: "not_found"` with up to 5 `suggestions` from search.

### 4.3 Resource Inventory & Facet Extraction Contract (All 25 Types)

All facet `key` and `value` strings in `entity_facets` are stored **lowercased** (`bool` → `"true"` / `"false"`, numbers as compact canonical strings e.g. `"0"`, `"0.25"`, `"3"`).

| `type` | Raw File (`5e-SRD-*.json`) | Count | Bundle Tier | Extracted Facet Keys (`entity_facets`) |
|---|---|---:|:---:|---|
| `ability-scores` | `Ability-Scores.json` | 6 | 1 | `skill` (from `skills[].index`) |
| `alignments` | `Alignments.json` | 10 | 2 | `abbreviation` |
| `backgrounds` | `Backgrounds.json` | 4 | 2 | `ability` (`ability_scores[].index`), `feat` (`feat.index`), `proficiency` (`proficiencies[].index`) |
| `classes` | `Classes.json` | 12 | 2 | `hit_die`, `saving_throw` (`saving_throws[].index`), `spellcaster` (`true`/`false`), `subclass` (`subclasses[].index`) |
| `conditions` | `Conditions.json` | 15 | 1 | *(none)* |
| `damage-types` | `Damage-Types.json` | 13 | 1 | *(none)* |
| `equipment` | `Equipment.json` | 182 | 2 | `category` (`equipment_categories[].index`), `mastery` (`mastery.index`), `property` (`properties[].index`), `damage_type` (`damage.damage_type.index`), `ability` (`ability.index`), `cost_unit` (`cost.unit`) |
| `equipment-categories` | `Equipment-Categories.json` | 30 | 3 | *(none)* |
| `feats` | `Feats.json` | 17 | 1 | `feat_type` (`type`), `repeatable` (`true` if key present else `false`), `has_prerequisite` (`true`/`false`) |
| `features` | `Features.json` | 232 | 2 | `class` (`class.index`), `subclass` (`subclass.index` if present), `level` (int parsed from `level.index` trailing number or linked level) |
| `languages` | `Languages.json` | 19 | 2 | `is_rare` (`true`/`false`) |
| `levels` | `Levels.json` | 287 | 3 | `class` (`class.index`), `subclass` (`subclass.index` if present), `level` (int) |
| `magic-items` | `Magic-Items.json` | 277 | 2 | `category` (`equipment_category.index`), `rarity` (lowercased `rarity.name`), `attunement` (`true`/`false`), `variant` (`true`/`false`), `limited_to` (lowercased `limited-to` if present) |
| `magic-schools` | `Magic-Schools.json` | 8 | 1 | *(none)* |
| `monsters` | `Monsters.json` | 341 | 2 | `cr` (`challenge_rating`), `creature_type` (`type`), `size`, `alignment`, `condition_immunity` (`condition_immunities[].index`) |
| `poisons` | `Poisons.json` | 14 | 1 | `poison_type` (`type`), `cost` |
| `proficiencies` | `Proficiencies.json` | 74 | 3 | `proficiency_type` (`type`), `class` (`classes[].index`), `background` (`backgrounds[].index`) |
| `skills` | `Skills.json` | 18 | 1 | `ability` (`ability_score.index`) |
| `species` | `Species.json` | 9 | 2 | `creature_type` (`type`), `size` (if present), `speed`, `has_subspecies` (`true`/`false`) |
| `spells` | `Spells.json` | 339 | 1 | `level`, `school` (`school.index`), `class` (`classes[].index`), `ritual` (`true`/`false`), `concentration` (`true`/`false`), `damage_type` (`damage.damage_type.index`), `attack_type` (if present) |
| `subclasses` | `Subclasses.json` | 12 | 2 | `class` (`class.index`) |
| `subspecies` | `Subspecies.json` | 24 | 2 | `species` (`species.index`), `damage_type` (`damage_type.index` if present) |
| `traits` | `Traits.json` | 68 | 1 | `species` (`species[].index`), `subspecies` (`subspecies[].index`) |
| `weapon-mastery-properties` | `Weapon-Mastery-Properties.json` | 8 | 1 | *(none)* |
| `weapon-properties` | `Weapon-Properties.json` | 10 | 1 | *(none)* |

**Total:** 25 resource types, **2,029 entities**. (No `Rules` collection exists in upstream 2024 v7.10.0.)

### 4.4 Search Text Normalization (Typographic Punctuation)
Because the raw corpus contains 985 typographic characters (`’` 529, `–` 179, `—` 24, `−` 13, `“`/`”` 8):
- Preserve original UTF-8 verbatim in `entities.name`, `entities.card_md`, `entities.card_body_md`, and `entities.raw_json`.
- Apply `normalize_search_text(s)` when populating `entities_fts` and when processing runtime search/name-match queries:
  - Replace `’`, `‘` → `'`
  - Replace `“`, `”` → `"`
  - Replace `–`, `—`, `−` → `-`
  - Strip Markdown emphasis/link syntax (`[Text](../path.md)` → `Text`, `**`/`*`/`` ` ``) in `entities_fts.body`.

---

## 5. SQLite Schema (`dist/srd2024.sqlite`)

Build with `PRAGMA journal_mode=DELETE; PRAGMA foreign_keys=ON;` and finalize with `ANALYZE; VACUUM;`.

```sql
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE entities (
    rowid           INTEGER PRIMARY KEY,
    id              TEXT NOT NULL UNIQUE,   -- '<type>:<slug>'
    type            TEXT NOT NULL,          -- one of the 25 types in §4.3
    slug            TEXT NOT NULL,          -- upstream 'index'
    name            TEXT NOT NULL,          -- display name (original UTF-8)
    name_norm       TEXT NOT NULL,          -- lower(normalize_search_text(name))
    api_url         TEXT NOT NULL UNIQUE,   -- upstream 'url' (2,029 unique)
    summary         TEXT NOT NULL,          -- compact 1-line summary (<= 200 chars)
    facets_json     TEXT NOT NULL,          -- sorted-key JSON object of facets
    raw_json        TEXT NOT NULL,          -- sorted-key canonical raw JSON
    card_md         TEXT NOT NULL,          -- full card with YAML frontmatter (matches dist/cards/)
    card_body_md    TEXT NOT NULL,          -- card body without YAML frontmatter
    card_tokens_est INTEGER NOT NULL        -- ceil(length(card_body_md) / 4.0)
);

CREATE UNIQUE INDEX idx_entities_type_slug ON entities(type, slug);
CREATE INDEX idx_entities_type ON entities(type);
CREATE INDEX idx_entities_slug ON entities(slug);
CREATE INDEX idx_entities_name_norm ON entities(name_norm);

CREATE TABLE entity_facets (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (entity_id, key, value)
);

CREATE INDEX idx_facets_kv ON entity_facets(key, value, entity_id);

CREATE TABLE edges (
    src_id      TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dst_id      TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel         TEXT NOT NULL,              -- normalized snake_case relation (§6)
    json_path   TEXT NOT NULL,              -- lexicographically first dotted path
    meta_json   TEXT,                       -- NULL or JSON of extra ref keys (level/usage/note/notes)
    PRIMARY KEY (src_id, dst_id, rel)
);

CREATE INDEX idx_edges_src ON edges(src_id, rel);
CREATE INDEX idx_edges_dst ON edges(dst_id, rel);

CREATE TABLE dangling_refs (
    src_id      TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_raw  TEXT NOT NULL,
    rel         TEXT NOT NULL,
    json_path   TEXT NOT NULL,
    PRIMARY KEY (src_id, target_raw, rel, json_path)
);

-- Contentless FTS5 index keyed by entities.rowid (exact 1:1 rowid alignment)
CREATE VIRTUAL TABLE entities_fts USING fts5(
    name,
    summary,
    body,
    content='',
    tokenize='porter unicode61'
);
```

### FTS5 Population & Query Contract
- Compiler inserts `INSERT INTO entities_fts(rowid, name, summary, body) VALUES (?, ?, ?, ?)` using the exact `entities.rowid`:
  - `name`: `normalize_search_text(entity.name)`
  - `summary`: `normalize_search_text(entity.summary)`
  - `body`: `normalize_search_text(strip_markdown(entity.card_body_md)) + " " + entity.type + " " + entity.slug`
- Runtime queries join `JOIN entities e ON e.rowid = entities_fts.rowid` and rank by `bm25(entities_fts, 10.0, 4.0, 1.0)` (lower = better in SQLite FTS5), boosted for exact `e.slug` or `e.name_norm` match.

### Required `meta` Keys
| Key | Value |
|---|---|
| `schema_version` | `"1"` |
| `dataset_id` | `"srd-2024-5.2"` |
| `upstream_repo` | `"https://github.com/5e-bits/5e-srd-api"` |
| `upstream_commit` | Read from `data/raw/PROVENANCE.json` (`"8a339e73ed5bba9ffec2ad6be9e76b5afa9c1b24"`) |
| `upstream_database_version` | Read from `data/raw/PROVENANCE.json` (`"7.10.0"`) |
| `license` | `"CC-BY-4.0"` |
| `license_url` | `"https://creativecommons.org/licenses/by/4.0/legalcode"` |
| `attribution` | Official SRD 5.2 statement from `SRD_ATTRIBUTION` in `src/srd2024/compiler/build.py` |
| `entity_count` | `"2029"` (computed at build time) |
| `edge_count` | computed count of rows in `edges` |
| `dangling_ref_count` | computed count of rows in `dangling_refs` (expected `"0"`) |
| `resource_types_json` | sorted JSON array of `{"type", "count", "facet_keys": [...]}` |
| `source_hash` | deterministic SHA-256 hex digest over sorted `(filename, bytes)` of `data/raw/2024/en/*.json` |

---

## 6. Cross-Reference Graph Extraction Rules

Informed directly by the audit (7,780 nested `dict.url` occurrences, 100% resolved, 0 self-refs, 0 dangling refs, 438 with inline metadata, 639 bare-string non-entity `/api/` paths to ignore):

1. **Nested-object walk only**: Recursively traverse every entity's raw JSON value **strictly below the top-level object**.
   - A `dict` node is a **reference** iff `"url"` is in `node` and `isinstance(node["url"], str)` and `node["url"].startswith("/api/2024/")`.
   - **Ignore bare string values**: Never turn string fields (`image`, `classes.class_levels`, `classes.spells`, or `armor-2`'s corrupted `"/api +2/..."` image string) into edges.
2. **Resolution via `url_to_id`**:
   - Look up `dst_id = url_to_id.get(node["url"])`.
   - Suppress self-loops (`dst_id == src_id`).
   - If `dst_id` is None → insert into `dangling_refs`. (For the current snapshot, `dangling_refs` MUST have 0 rows.)
3. **Deterministic `rel` derivation**:
   - Build the logical path by joining ancestor dict keys (skipping list indices and generic choice wrapper segments `from`, `options`, `option`, `item`, `of`, `choice`), lowercased and joined with `_` (hyphens → `_`).
   - Collapse verbose paths to clean semantic relation names:
     - Take the trailing meaningful key segment (or parent+child for nested action/option paths, e.g. `actions_damage_type`, `actions_spell`, `actions_dc_type`, `special_abilities_spell`, `starting_equipment`, `proficiency_choices`, `equipment_categories`, `weapon_mastery` for `mastery`, `weapon_property` for `properties`, `condition_immunity` for `condition_immunities`, `damage_type`, `school`, `class`, `subclass`, `level`, `feature`, `trait`, `species`, `subspecies`, `feat`, `skill`, `ability_score`, `proficiency`, `variant`, `craft`, `contents`, `ammunition`, `storage`, `armor`, `form`, `reference`).
     - Whatever normalization table the compiler uses MUST be purely deterministic, produce concise `snake_case` names, and ensure distinct semantic roles (e.g. `damage_type` vs `saving_throw`) do not collide.
4. **Preserve inline edge metadata (`meta_json`)**:
   - Collect extra keys on the reference dict beyond `{"index", "name", "url"}` (specifically `level`, `usage`, `note`, `notes` across the 438 annotated refs).
   - If multiple occurrences map to the same `(src_id, dst_id, rel)`: keep the lexicographically smallest `json_path`, and if multiple distinct metadata dicts exist, store sorted unique metadata entries (or single object if 1), serialized as compact sorted-key JSON in `edges.meta_json` (else `NULL`).

---

## 7. Compiled Markdown Cards (`dist/cards/`)

### 7.1 Output Files
- `dist/cards/INDEX.md`: Compact catalog table (`type`, `count`, `card path`, `filterable facets`) + 3-line progressive-disclosure guide + CC-BY-4.0 attribution.
- `dist/cards/<type>/<slug>.md`: One file per entity (2,029 files total), byte-identical to `entities.card_md`.

### 7.2 Card Format & Rules
```markdown
---
id: equipment:longsword
type: equipment
name: Longsword
summary: "Martial Melee Weapon; 1d8 slashing (Versatile 1d10), Mastery: Sap, 15 gp, 3 lb."
refs:
  - damage-types:slashing
  - equipment-categories:martial-melee-weapons
  - equipment-categories:martial-weapons
  - equipment-categories:melee-weapons
  - equipment-categories:weapon
  - weapon-mastery-properties:sap
  - weapon-properties:versatile
---
# Longsword (`equipment:longsword`)
**Categories:** [Martial Melee Weapons](../equipment-categories/martial-melee-weapons.md), ... | **Cost:** 15 gp | **Weight:** 3 lb.
**Damage:** 1d8 [Slashing](../damage-types/slashing.md) (Two-Handed: 1d10) | **Mastery:** [Sap](../weapon-mastery-properties/sap.md) | **Properties:** [Versatile](../weapon-properties/versatile.md)
```

Strict card compilation rules:
1. **Deterministic & zero-timestamp**: Sorted `refs` (unique outgoing `dst_id`s from `edges`), `\n` line endings, no build timestamps inside cards.
2. **YAML safety**: Quote string scalars (`name`, `summary`) safely with double quotes + backslash escaping (`\"`, `\\`, `\n`) so YAML is always valid without `PyYAML`.
3. **Relative cross-reference links**: Render resolved references inside `card_body_md` as `[Name](../<dst_type>/<dst_slug>.md)` so file-reading agents can follow links directly.
4. **Type-specific token-tight formatting** (handling all audit quirks cleanly):
   - **`spells`**: Header line with level/school/ritual/concentration, casting time, range, components (+ material), duration, classes; then `description`, optional `higher_level`, optional compact damage scaling table.
   - **`monsters`**: Compact statblock header (Size Type, Alignment | AC, HP, Speed | 6 ability scores | CR/XP/PB | saves/skills/immunities/resistances/vulnerabilities/senses/languages/gear), then concise sections for Traits (`special_abilities`), Actions, Bonus Actions, Reactions, Legendary Actions, Forms.
   - **`equipment`**: Cost, optional weight (note: `entertainers-pack` omits `weight`), categories, weapon stats (damage, two-handed damage, range/throw_range, ability, properties, mastery, ammunition) or armor stats (AC, str_min, stealth_disadvantage, don/doff) or tool stats (`utilize`, `craft`, `contents`, `storage`), plus optional `description`/`notes`.
   - **`magic-items`**: Category, rarity, attunement, optional `limited-to` (hyphenated key!), variant/variants; join `desc: list[str]` cleanly (keep consecutive `|` Markdown pipe-table rows separated by `\n`, non-table paragraphs by `\n\n` so all 44 tables render intact!). Omit corrupted `image` URLs.
   - **`classes` / `subclasses` / `levels` / `features`**: Concise mechanical tables/lists; `subclasses` renders `summary`, `description`, and inline `features[]` (grouped by level).
   - **`species` / `subspecies` / `traits` / `backgrounds` / `feats`**: Compact header (speed/size/type, feat category, prerequisites/prerequisite_options, repeatable, starting options) + description + linked traits/spells/proficiencies.
   - **`equipment-categories` / `proficiencies`**: Compact comma-separated relative link list (not bloated multi-line bullets) to stay token-tight.
   - **Simple reference types** (`conditions`, `damage-types`, `magic-schools`, `weapon-mastery-properties`, `weapon-properties`, `ability-scores`, `alignments`, `skills`, `poisons`, `languages`): Compact header + verbatim description (omit empty `languages.note`).
5. **1-line `summary` (`<= 200` chars)**: Must capture the key mechanical identity (e.g. spell level/school/casting time/range; monster CR/type/HP/AC; weapon damage/properties/mastery/cost; magic item rarity/attunement/category; first crisp sentence of description for conditions/masteries/traits). Never empty, no newlines.

---

## 8. Resolved-Bundle Query Engine

### 8.1 Parameters
- `id` (`str`, required): canonical ID, API URL, bare slug, or exact name (§4.2)
- `depth` (`int`, `0..2`, default `1`): outgoing BFS hop limit
- `max_tokens` (`int`, `200..16000`, default `2000`): total payload token budget
- `include_inbound` (`bool`, default `False`): optionally append up to `inbound_limit=10` compact inbound references
- `rels` (`list[str] | None`, default `None`): optional whitelist of outgoing edge `rel` values to traverse
- `format` (`"markdown" | "json"`, default `"markdown"`)

### 8.2 Deterministic Algorithm
1. **Resolve root** via §4.2. Return structured `ambiguous` or `not_found` payload immediately if unresolved.
2. **BFS outgoing expansion** up to `depth`:
   - Queue starts at `(root.id, hop=0)`.
   - For each node with `hop < depth`, fetch outgoing `edges` (filtered by `rels` if given).
   - For each discovered `dst_id` (excluding `root.id`), record minimum `hop` and sorted unique `(rel, meta_json)` pairs along shortest-hop edges.
3. **Priority sorting**:
   - Assign `tier` from the table in §4.3 (`1` = core mechanical rules, `2` = character/gameplay entities, `3` = structural/high-fan-out containers).
   - Sort all discovered dependencies strictly by `(hop ASC, tier ASC, id ASC)`.
4. **Token-budgeted packing**:
   - Estimator: `est_tokens(s) = math.ceil(len(s) / 4)`.
   - Always emit `root.card_body_md` in full (never truncate root; set `budget_exceeded = True` if root alone exceeds `max_tokens`).
   - `budget_left = max(0, max_tokens - est_tokens(root_section) - 40)`.
   - **Anti-hub guard**: To prevent one huge Tier 2/3 hub (e.g. `classes:wizard` or `equipment-categories:weapon`) from crowding out smaller rules, a dependency qualifies for `full` rendering only if:
     - `est_tokens(full_block) <= budget_left`, AND
     - (`tier == 1` OR `dep.card_tokens_est <= 400`).
   - For each dependency in sorted order:
     - If eligible for `full`: include full card (`detail = "full"`), subtract `est_tokens(full_block)` from `budget_left`.
     - Else if `est_tokens(stub_block) <= budget_left`: include 1-line stub (`- **<Name>** (\`<id>\`, via <rels>): <summary>`, `detail = "summary"`), subtract `est_tokens(stub_block)`, set `truncated = True`.
     - Else: append `dep.id` to `omitted_ids`, set `truncated = True`.
   - If `include_inbound` is true and `budget_left > 0`: query inbound `edges` (`dst_id = root.id`), sort by `(rel ASC, src_id ASC)`, and append up to 10 compact 1-line stubs while budget allows (record total inbound count).
5. **Output rendering**:
   - **`markdown`**:
     - Root `card_body_md`
     - `## Referenced Rules` (full dependency cards under `### <Name> (\`<id>\`) — via <rels>`, followed by `### Summarized References` bullet list for any `summary`-detail items)
     - Optional `## Referenced By` (when `include_inbound=True`)
     - 1-line footer: `<!-- bundle: id=<id> depth=<d> tokens~=<n>/<max> full=<f> summary=<s> omitted=<o> -->`
   - **`json`**:
     ```json
     {
       "status": "ok",
       "root": {"id": "...", "type": "...", "slug": "...", "name": "...", "summary": "...", "facets": {}, "card_body_md": "...", "tokens_est": 0},
       "dependencies": [{"id": "...", "type": "...", "name": "...", "hop": 1, "rels": ["..."], "edge_meta": [], "tier": 1, "detail": "full|summary", "summary": "...", "card_body_md": "...|null", "tokens_est": 0}],
       "inbound": [],
       "stats": {"depth": 1, "max_tokens": 2000, "estimated_tokens": 0, "budget_exceeded": false, "truncated": false, "full_count": 0, "summary_count": 0, "omitted_ids": []}
     }
     ```

---

## 9. Agent Surfaces Contract (MCP, CLI, Skill)

All 5 operations live in `src/srd2024/runtime/` and are exposed identically through `bin/srd` (CLI) and `bin/srd-mcp` (stdio MCP server).

| # | MCP Tool | CLI Command | Purpose |
|---|---|---|---|
| 1 | `srd_search` | `srd search <query> [--type T]... [--limit N] [--format markdown\|json]` | Ranked FTS5 search → compact stubs |
| 2 | `srd_get` | `srd get <id> [--depth 0..2] [--max-tokens N] [--inbound] [--rel R]... [--format markdown\|json]` | Resolved-bundle fetch (entity + depth-N rules) |
| 3 | `srd_list` | `srd list [--type T] [--filter k=v]... [--limit N] [--offset N] [--format markdown\|json]` | Discover resource catalog/facets, or filter entities |
| 4 | `srd_graph` | `srd graph <id> [--direction out\|in\|both] [--depth 1..2] [--rel R] [--limit N] [--format markdown\|json]` | Inspect outgoing/inbound cross-ref graph |
| 5 | `srd_validate` | `srd validate <id>... [--no-links] [--format markdown\|json]` | Batch-resolve/validate entity refs, graph links, prerequisites & provenance |

### 9.1 Operation Contracts

1. **`srd_search`**
   - Args: `query: str` (required), `types: list[str] | None` (CLI: repeatable `--type`), `limit: int = 10` (clamped `1..50`), `format: "markdown" | "json" = "markdown"`.
   - Query behavior:
     - Normalize `query` via `normalize_search_text`.
     - Tokenize into safe alphanumeric terms and run FTS5 `MATCH` (conjunctive tokens + prefix `*` on final token) ordered by exact slug/name boost then `bm25(entities_fts, 10.0, 4.0, 1.0)`.
     - If FTS5 yields 0 hits (or query has only punctuation), fall back to `LIKE '%<term>%'` on `name_norm`, `slug`, `summary`.
     - Never crash on arbitrary user punctuation (`"`, `*`, `:`, `-`, `(`, `)`, `'`).
   - Output: Ranked compact stubs `(id, type, name, summary)` — NEVER full cards.

2. **`srd_get`**
   - Args: `id: str`, `depth: int = 1` (`0..2`), `max_tokens: int = 2000` (`200..16000`), `include_inbound: bool = False`, `rels: list[str] | None = None`, `format: "markdown" | "json" = "markdown"`.
   - Output: Resolved bundle per §8, or `ambiguous` / `not_found` response.

3. **`srd_list`**
   - Args: `type: str | None = None`, `filters: dict[str, str | list[str]] | None = None` (CLI: repeatable `--filter key=value`), `limit: int = 50` (`1..200`), `offset: int = 0` (`>=0`), `format: "markdown" | "json" = "markdown"`.
   - Behavior:
     - If `type` is None: return catalog of all 25 resource types (`type`, `count`, `facet_keys`) plus dataset metadata (`dataset_id`, `license`, `entity_count`, `edge_count`).
     - If `type` is given: validate `type` exists (return error + valid types if not); apply `entity_facets` filters (AND across distinct keys, OR across multiple values for the same key; case-insensitive lowercased); return `total`, `offset`, `limit`, and compact `(id, name, summary)` rows sorted by `name_norm ASC, id ASC`.

4. **`srd_graph`**
   - Args: `id: str`, `direction: "out" | "in" | "both" = "both"`, `depth: int = 1` (`1..2`), `rel: str | None = None`, `limit: int = 50` (`1..100`), `format: "markdown" | "json" = "markdown"`.
   - Behavior: Resolve root via §4.2; return root stub + outgoing and/or inbound neighbors (up to `depth`, filtered by optional `rel`, capped by `limit`), each with `(direction, hop, rel, meta, id, type, name, summary)`.

5. **`srd_validate`**
   - Args: `ids: list[str]` (1..50 items), `check_links: bool = True`, `format: "markdown" | "json" = "markdown"`.
   - Behavior:
     - Resolve every ID via §4.2 into `valid`, `ambiguous`, or `invalid` (with top suggestions).
     - When `check_links=True` and `len(valid) >= 1`:
       - Report direct `edges` between any two valid entities (`links`: `[{src_id, dst_id, rel, meta}]`).
       - Report contextual dependency hints (`advisories`) for structural mismatches among the validated set: e.g., a `subclasses` entity whose parent `class` isn't in the set, a `subspecies` entity whose parent `species` isn't in the set, or a `feats` entity that carries `prerequisites` / `prerequisite_options` in `raw_json` (quote the requirement concisely so the agent can verify it).
     - Include dataset provenance (`dataset_id`, `license`, `attribution`).

### 9.2 DB Path Resolution (CLI & MCP)
Both `bin/srd` and `bin/srd-mcp` resolve the SQLite database in this order:
1. `--db <path>` flag
2. `SRD2024_DB` environment variable
3. Default `<repo_root>/dist/srd2024.sqlite`

### 9.3 Stdio MCP Server Protocol (`bin/srd-mcp`)
- Pure stdlib JSON-RPC 2.0 over newline-delimited JSON on `stdin`/`stdout` (support standard MCP stdio line-delimited JSON messages; ignore blank lines; write ONLY JSON-RPC responses to `stdout`, flush after every message; log errors strictly to `stderr`).
- Supported methods:
  - `initialize` → returns `{protocolVersion: "2024-11-05", capabilities: {tools: {}, resources: {}}, serverInfo: {name: "agent-srd-2024", version: "0.1.0"}}` (echo/accept client protocolVersion cleanly).
  - `notifications/initialized` → no response (notification).
  - `ping` → `{}`.
  - `tools/list` → returns the 5 tools (`srd_search`, `srd_get`, `srd_list`, `srd_graph`, `srd_validate`) with complete JSON Schema `inputSchema`s.
  - `tools/call` → dispatches to query engine, returns `{content: [{type: "text", text: <rendered>}], isError: <bool>}`. Unknown tool or invalid args return JSON-RPC error or `isError: true` cleanly without crashing the server loop.
  - `resources/list` & `resources/read` → lightweight exposure of `srd://meta` (provenance + catalog JSON) and `srd://card/<type>/<slug>` (full card markdown).

### 9.4 Agent Skill (`skill/SKILL.md`)
- YAML frontmatter (`name: srd-2024`, `description: ...`).
- Concise, operational guide teaching agents:
  1. **Progressive disclosure**: Start with `srd_list` or `srd_search` (cheap stubs) before fetching full rules.
  2. **One-shot resolved bundles**: Use `srd_get(id, depth=1, max_tokens=2000)` to get an entity + its mechanical dependencies (weapon mastery, properties, conditions, damage types, schools) in a single turn.
  3. **Disambiguation**: Always use canonical `type:slug` IDs (e.g., `spells:shield` vs `equipment:shield`, `classes:druid` vs `monsters:druid`) when known.
  4. **Reverse graph lookups**: Use `srd_graph(id, direction="in")` for questions like "which weapons have Sap?" or "what monsters inflict Paralyzed?".
  5. **Validation**: Call `srd_validate` to verify a character build / encounter roster and inspect links & feat prerequisites.
  6. **File-only fallback**: Read `dist/cards/INDEX.md` and follow relative links in `dist/cards/<type>/<slug>.md` when MCP/CLI is unavailable.

---

## 10. Frontend Consumption Contract (`dist/web/`)

To let a static web frontend (e.g. interactive SRD Explorer, Cross-Reference Graph Visualizer, and Resolved-Bundle Token Inspector) run with **zero custom backend server**, `compiler` emits deterministic static JSON assets under `dist/web/` alongside `dist/srd2024.sqlite` and `dist/cards/`:

### 10.1 Static Assets & Schemas

1. **`dist/manifest.json`** (~4 KB — entry manifest, §11)
   - Dataset metadata, CC-BY-4.0 attribution string (render in app footer), entity/edge counts, all 25 resource types with counts & filterable `facet_keys`, and relative paths to `web/` assets.

2. **`dist/web/index.json`** (single-fetch entity catalog for instant client-side search, browse & facet filtering)
   - Deterministic JSON object:
     ```json
     {
       "schema_version": "1",
       "dataset_id": "srd-2024-5.2",
       "entities": [
         {
           "id": "equipment:longsword",
           "type": "equipment",
           "slug": "longsword",
           "name": "Longsword",
           "summary": "Martial Melee Weapon; 1d8 slashing (Versatile 1d10), Mastery: Sap, 15 gp, 3 lb.",
           "facets": {"category": ["martial-melee-weapons", "..."], "mastery": ["sap"], "property": ["versatile"]},
           "tier": 2,
           "card_tokens_est": 82,
           "out_deg": 7,
           "in_deg": 4,
           "card_path": "cards/equipment/longsword.md"
         }
       ]
     }
     ```
   - Sorted by `id ASC`. No `card_body_md` or `raw_json` here so initial boot payload stays lean (~450–650 KB raw, ~120 KB gzipped).

3. **`dist/web/graph.json`** (compact full directed graph for visualization & client-side bundle resolution)
   - Deterministic JSON object:
     ```json
     {
       "schema_version": "1",
       "nodes": {
         "equipment:longsword": {"type": "equipment", "name": "Longsword", "tier": 2}
       },
       "edges": [
         ["equipment:longsword", "weapon-mastery-properties:sap", "weapon_mastery", null]
       ]
     }
     ```
   - `edges` is an array of compact 4-tuples `[src_id, dst_id, rel, meta_or_null]` sorted by `(src_id, dst_id, rel)` (~400 KB raw, <100 KB gzipped). Powers ego-graphs, reverse lookups (`dst_id == target`), and client-side BFS.

4. **`dist/web/types/<type>.json`** (25 lazy-loaded per-type card shards)
   - Keyed by canonical `id` (sorted keys):
     ```json
     {
       "equipment:longsword": {
         "id": "equipment:longsword",
         "type": "equipment",
         "slug": "longsword",
         "name": "Longsword",
         "summary": "...",
         "facets": {},
         "card_body_md": "# Longsword (`equipment:longsword`)\n...",
         "card_tokens_est": 82,
         "refs": ["damage-types:slashing", "weapon-mastery-properties:sap", "..."]
       }
     }
     ```
   - Compact JSON (`separators=(",", ":")`, `sort_keys=True`, UTF-8) without duplicating raw upstream JSON. Individual raw Markdown cards remain directly fetchable at `dist/cards/<type>/<slug>.md`.

### 10.2 Client-Side Resolved-Bundle Parity
A static frontend can replicate the exact resolved-bundle engine (§8) in the browser with zero server calls:
1. Load `dist/web/index.json` + `dist/web/graph.json`.
2. Run BFS over `graph.edges` from `root_id` up to `depth` (`0..2`), sorting dependencies by `(hop ASC, tier ASC, id ASC)`.
3. Lazy-fetch only the `dist/web/types/<type>.json` shards needed for the root + discovered dependencies (most mechanical Tier 1 shards like `conditions.json`, `weapon-mastery-properties.json`, `damage-types.json` are <10 KB each and cache permanently).
4. Apply the exact token estimator (`Math.ceil(text.length / 4)`) and anti-hub rule (`tier === 1 || card_tokens_est <= 400`) to render live interactive bundles and copy-to-clipboard agent prompts.

### 10.3 Frontend Repo Boundary
- The frontend application itself (owned by CoS & team) should live in a dedicated directory (e.g. `web/` or `apps/web/`) and treat `dist/` as a **read-only static build input** (never editing `src/srd2024/`, `bin/`, or `data/raw/`).

---

## 11. Build Manifest (`dist/manifest.json`)

Compiler emits deterministic `dist/manifest.json` (sorted keys, 2-space indent, no wall-clock timestamp so rebuilds are byte-identical):
```json
{
  "schema_version": "1",
  "dataset_id": "srd-2024-5.2",
  "upstream": {
    "repo": "https://github.com/5e-bits/5e-srd-api",
    "commit": "8a339e73ed5bba9ffec2ad6be9e76b5afa9c1b24",
    "database_version": "7.10.0"
  },
  "license": {
    "content": "CC-BY-4.0",
    "content_url": "https://creativecommons.org/licenses/by/4.0/legalcode",
    "attribution": "...",
    "packaging": "MIT"
  },
  "artifacts": {
    "sqlite": "srd2024.sqlite",
    "cards_index": "cards/INDEX.md",
    "web_index": "web/index.json",
    "web_graph": "web/graph.json",
    "web_types_dir": "web/types"
  },
  "source_hash": "<sha256>",
  "counts": {
    "files": 25,
    "entities": 2029,
    "facets": 0,
    "edges": 0,
    "dangling_refs": 0,
    "cards": 2029,
    "web_type_shards": 25
  },
  "resource_types": [
    {"type": "ability-scores", "count": 6, "facet_keys": ["skill"]}
  ]
}
```

---

## 12. Fixed `pyproject.toml` Contract (Owned by `compiler`)

`compiler` creates `/home/pshouse/Work/agent-srd-2024/pyproject.toml` with these exact entrypoints so `surface`'s module paths match with zero coordination friction:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "agent-srd-2024"
version = "0.1.0"
description = "Zero-server, agent-native 5e 2024 (SRD 5.2) SQLite, Markdown card, CLI, and MCP pack"
readme = "README.md"
requires-python = ">=3.11"
dependencies = []

[project.scripts]
srd-build = "srd2024.compiler.__main__:main"
srd = "srd2024.runtime.cli:main"
srd-mcp = "srd2024.runtime.mcp:main"

[tool.setuptools.packages.find]
where = ["src"]
```

---

## 13. Verification Acceptance Criteria (Owned by `verifier`)

`verifier` builds an automated `unittest` suite under `tests/` runnable via `PYTHONPATH=src python3 -m unittest discover -s tests -v` verifying:

1. **Build & Completeness**: `bin/srd-build` runs cleanly; `dist/srd2024.sqlite`, `dist/cards/INDEX.md`, 2,029 card files, `dist/web/index.json`, `dist/web/graph.json`, 25 `dist/web/types/<type>.json` shards, and `dist/manifest.json` exist; SQLite `entities` has exactly 2,029 rows across all 25 types.
2. **Determinism**: Rebuilding into a temp output directory produces byte-identical `manifest.json`, `dist/cards/**`, and `dist/web/**`, identical `source_hash`, and identical row/edge/facet counts.
3. **Graph & Schema Integrity**: `PRAGMA foreign_key_check` is empty; `dangling_refs` count is `0`; zero self-edges; `edges` count >= 7,300; inline edge metadata (`meta_json`) preserved on monster spells (`actions`) and `subspecies.traits`; zero edges point to `/api/images/` or REST collection endpoints; `armor-2` compiles cleanly without error.
4. **Card Quality & YAML Validity**: Sample + full-corpus checks confirm valid YAML frontmatter on all 2,029 cards, zero raw `/api/2024/` URLs leaked into bodies, all 44 `magic-items` pipe tables intact, relative links resolve to existing files in `dist/cards/`.
5. **Frontend Static Bundle Contract**: `dist/web/index.json` has 2,029 entries sorted by `id` matching SQLite; `dist/web/graph.json` node/edge counts match SQLite `entities`/`edges`; all 25 `dist/web/types/<type>.json` shards load cleanly and match `entities.card_body_md`.
6. **Search & Unicode Normalization**: `srd_search("Dragon's")` matches typographic `Dragon’s`; FTS5 handles punctuation safely (`"fire-ball"`, `C++`, `'""*'`); exact slug/name queries rank #1; facet filtering works on `spells` (`level=3`, `class=wizard`, `school=evocation`), `equipment` (`mastery=sap`), `monsters` (`cr=1`, `creature_type=undead`), `magic-items` (`rarity=legendary`, `attunement=true`).
7. **Resolved-Bundle Engine**:
   - `srd_get("equipment:longsword", depth=1)` includes `weapon-mastery-properties:sap`, `weapon-properties:versatile`, and `damage-types:slashing` in a single response.
   - `srd_get("spells:fireball", depth=1)` expands Tier 1 rules (`magic-schools:evocation`, `damage-types:fire`) in full while respecting `max_tokens` and anti-hub guard for `classes:wizard` / `classes:sorcerer`.
   - Ambiguous bare slugs (`shield`, `druid`, `light`, `acid`) return `status: "ambiguous"` with all matching candidates instead of guessing.
   - Tight `max_tokens` deterministically downgrades dependencies to summary stubs / `omitted_ids` without truncating the root card.
8. **Graph & Validate**:
   - `srd_graph("weapon-mastery-properties:sap", direction="in")` returns weapons with Sap mastery.
   - `srd_validate(["species:elf", "subspecies:high-elf", "classes:wizard", "spells:fireball", "shield", "nonexistent-xyz"])` classifies valid/ambiguous/invalid correctly, finds graph links, and emits provenance.
9. **CLI & MCP Parity + Protocol**:
   - `bin/srd` subcommands (`search`, `get`, `list`, `graph`, `validate`) execute cleanly with exit code 0 in both `markdown` and `json` formats.
   - `bin/srd-mcp` handles `initialize` → `notifications/initialized` → `tools/list` (all 5 tools) → `tools/call` for each tool → `resources/read` over stdio subprocess cleanly with zero stray `stdout` output.
10. **Attribution**: CC-BY-4.0 SRD 5.2 attribution present in SQLite `meta`, `dist/manifest.json`, `dist/cards/INDEX.md`, and `README.md`.
