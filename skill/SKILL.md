---
name: srd-2024
description: Query, bundle, filter, traverse, and validate 5e 2024 (SRD 5.2) rules locally via MCP (`bin/srd-mcp`), CLI (`bin/srd`), or compiled Markdown cards (`dist/cards/`) using progressive disclosure and single-shot resolved bundles.
---

# 5e 2024 SRD (v5.2) Agent Playbook

Use this skill whenever answering 5e 2024 (SRD 5.2) rules questions, building or validating characters, designing encounters, or resolving mechanical cross-references (weapon masteries, conditions, damage types, spells, feats, species, monsters).

All queries run locally and read-only against `dist/srd2024.sqlite` (25 resource types, 2,029 entities, zero external server calls).

---

## Core Workflow Rules

### 1. Progressive Disclosure First (Cheap Stubs Before Full Rules)
Do **not** fetch large cards blindly. Start with low-token discovery calls:
- **Unknown category or facets:** call `srd_list()` (or `bin/srd list`) to see all 25 resource types, entity counts, and filterable facet keys.
- **Structured filtering:** call `srd_list(type=..., filters={...})` (e.g., `spells` with `level=3`, `class=wizard`, `school=evocation`; `monsters` with `cr=1`, `creature_type=undead`; `equipment` with `mastery=sap`; `magic-items` with `rarity=legendary`, `attunement=true`).
- **Keyword / fuzzy lookup:** call `srd_search(query, types=[...], limit=10)` (returns compact 1-line stubs only—never full cards).

### 2. One-Shot Resolved Bundles (Eliminate N+1 Lookups)
Once you know the entity you need, fetch it with `srd_get(id, depth=1, max_tokens=2000)` (or `bin/srd get <id> --depth 1 --max-tokens 2000`).
- A single call returns the root card **plus** its referenced mechanical rules prioritized by tier:
  - **Tier 1 (expanded in full first):** `conditions`, `damage-types`, `weapon-mastery-properties`, `weapon-properties`, `magic-schools`, `skills`, `ability-scores`, `feats`, `poisons`, `spells`, `traits`.
  - **Tier 2 / Tier 3:** character/gameplay entities and high-fan-out containers (`classes`, `equipment-categories`, `levels`, `proficiencies`); large hubs (>400 est. tokens) automatically downgrade to 1-line summary stubs so they never crowd out core rules.
- Example: `srd_get("equipment:longsword", depth=1)` returns Longsword together with Sap mastery, Versatile property, and Slashing damage in one round trip.
- Use `depth=0` when you only want the root card itself; increase `max_tokens` (up to `16000`) or `depth=2` only when a multi-hop mechanic requires it.
- Check the trailing HTML footer (`<!-- bundle: id=... depth=... tokens~=.../... full=... summary=... omitted=... -->`) to see if any referenced rules were summarized or omitted.

### 3. Always Disambiguate With Canonical `type:slug` IDs
53 bare slugs exist in multiple resource types (for example: `shield` -> `equipment:shield` & `spells:shield`; `druid` -> `classes:druid` & `monsters:druid`; `light` -> `spells:light` & `weapon-properties:light`; `acid` -> `damage-types:acid` & `equipment:acid`).
- Prefer canonical `type:slug` IDs whenever known.
- If a bare slug or name returns `status: "ambiguous"`, inspect the returned `candidates` list and re-query with the exact canonical ID. Never guess silently.

### 4. Reverse & Directed Graph Lookups
Use `srd_graph(id, direction="in")` (or `bin/srd graph <id> --direction in`) for inverse mechanical questions:
- *"Which weapons have the Sap mastery?"* -> `srd_graph("weapon-mastery-properties:sap", direction="in")`
- *"Which monsters inflict or are immune to Paralyzed?"* -> `srd_graph("conditions:paralyzed", direction="in")`
- *"Which spells belong to Evocation?"* -> `srd_graph("magic-schools:evocation", direction="in")`
- Use `direction="out"` or `direction="both"` and optional `rel` / `depth=2` to explore relationships without loading full card bodies.

### 5. Batch Validation for Character Builds & Encounters
Before finalizing a character build, spell loadout, or encounter roster, call `srd_validate(ids=[...])` (1..50 IDs):
- Classifies every reference into `valid`, `ambiguous`, or `invalid` (with top search suggestions).
- Reports direct inter-entity `links` (e.g., `spells:fireball -> classes:wizard`, `subspecies:elven-lineage-high-elf -> species:elf`).
- Emits `advisories` for structural gaps (e.g., a subclass without its parent class, a subspecies without its parent species) and quotes `feats` prerequisites (`minimum_level`, `feature_named`, `prerequisite_options`) so you can verify eligibility.
- Attaches dataset provenance and CC-BY-4.0 attribution.

### 6. File-Only Fallback (When MCP and CLI Are Unavailable)
If you only have filesystem read tools and cannot run `bin/srd` or connect to `bin/srd-mcp`:
1. Read `dist/cards/INDEX.md` to locate the relevant resource type and facet overview.
2. Read `dist/cards/<type>/<slug>.md` directly.
3. Follow the inline relative Markdown links (`[Name](../<dst_type>/<dst_slug>.md)`) and YAML frontmatter `refs:` list to resolve only the rules you need.

---

## Tool & CLI Quick Reference

| MCP Tool | CLI Equivalent | Purpose |
|---|---|---|
| `srd_search` | `bin/srd search <query> [--type T]... [--limit N]` | Ranked FTS5 search returning compact stubs |
| `srd_get` | `bin/srd get <id> [--depth 0..2] [--max-tokens N] [--inbound] [--rel R]...` | Single-shot token-budgeted resolved bundle |
| `srd_list` | `bin/srd list [--type T] [--filter k=v]... [--limit N] [--offset N]` | Resource catalog or facet-filtered listing |
| `srd_graph` | `bin/srd graph <id> [--direction out\|in\|both] [--depth 1..2] [--rel R]` | Outgoing/inbound cross-reference graph inspection |
| `srd_validate` | `bin/srd validate <id>... [--no-links]` | Batch resolve, check links, advisories & provenance |

- Pass `--format json` on the CLI or `format: "json"` on any MCP tool when structured JSON is preferred over Markdown.
- MCP Resources: read `srd://meta` for provenance/catalog JSON and `srd://card/<type>/<slug>` for a single full Markdown card with YAML frontmatter.
- Database override order: `--db <path>` -> `SRD2024_DB` env var -> `<repo_root>/dist/srd2024.sqlite`.

---

## Attribution (CC-BY-4.0)

When quoting or summarizing rules from this pack, include this notice:

This work includes material from the System Reference Document 5.2 (“SRD 5.2”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.
