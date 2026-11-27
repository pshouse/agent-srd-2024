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

## Stage 2: Neuro-Symbolic Playbook (Deterministic Rules Engine)

Stage 2 layers a deterministic, zero-LLM 5e 2024 rules engine (`dist/rules.json`, `srd2024.engine`) on top of Stage 1 retrieval. Follow the neuro-symbolic division of labor: **you translate intent and narrate outcomes; Python computes every rule, roll, DC, modifier, and state transition.**

### 1. Inspect Compiled Rules & Coverage Before Assuming Automation
Call `srd_rule(id)` (or `bin/srd rule <id>`) or `srd_rule(coverage=true)` (or `bin/srd rule --coverage`) to check a rule's explicit coverage grade (`full` | `partial` | `narrative`) and `provenance.unmodeled_clauses`:
- **`full`**: All combat/character mechanics are compiled and executed deterministically (all 8 weapon masteries, all 15 conditions, all 38 weapons, all 13 armor/shields, core combat spells & monsters).
- **`partial`**: Core numeric mechanics (attacks, saves, damage, AC/HP, slots) run deterministically while listed `unmodeled_clauses` require GM/agent adjudication.
- **`narrative`**: Exploration/social/divination/utility spells and mundane gear—the engine validates and logs use honestly without inventing mechanics.

### 2. Compile & Validate Characters Deterministically (Never Hand-Calculate)
Instead of calculating AC, HP, saves, weapon bonuses, or 2024 legality by hand, pass a `CharacterBuildSpec` JSON to `srd_compile_character(spec)` (or `bin/srd character <spec.json>`):
- Enforces 2024 Background ASI (`+2/+1` or `+1/+1/+1` restricted to background abilities), point-buy / standard-array, Origin/General/Fighting-Style/Epic-Boon feats & prerequisites, species/subspecies traits, weapon mastery counts, and class spell lists/slots.
- Returns structured `errors`, `warnings`, and an authoritative `statblock` ready to feed directly into `srd_encounter`.

### 3. Run Combat Strictly Through `srd_encounter` & Narrate From Output
Drive encounters with structured intents via `srd_encounter` (or `bin/srd encounter`):
- **Supported intents:** `start_encounter`, `move`, `attack`, `cast_spell`, `dodge`, `dash`, `disengage`, `help`, `end_turn`.
- **Reactions are automatic:** the engine makes Opportunity Attacks when a creature leaves a hostile's reach without Disengage (teleporting and forced movement don't provoke), and casts Shield when an attack would hit and +5 AC turns it into a miss. Never send a reaction spell as your own action; it is rejected. Events made inside a reaction carry `"reaction": true`.
- **Stateless vs. session mode:** Pass `state` round-trip (CLI uses `--state` / `--out-state`) OR pass a `session_id` over MCP so `SRDMCPServer` caches the latest `EncounterState` in memory across turns.
- **Narrate faithfully:** Base every tactical narration strictly on the returned `events`, `state_diff`, `legal_actions`, and `narrative_hooks`. Never override or fudge engine math.

### 4. MCP Discovery Mode & Rules Path Override
- Connect via **`bin/srd-engine-mcp`** (or `bin/srd-mcp --engine`, or env `SRD2024_ENGINE=1`) to advertise all **8 MCP tools** (`srd_search`, `srd_get`, `srd_list`, `srd_graph`, `srd_validate`, `srd_rule`, `srd_compile_character`, `srd_encounter`) plus `srd://rules/coverage` and `srd://rule/<type>/<slug>` resources.
- Default `bin/srd-mcp` keeps Stage 1 5-tool discovery in `tools/list` while still accepting `tools/call` for all 8 tools.
- Rules IR override order: `--rules <path>` -> `SRD2024_RULES` env var -> `<repo_root>/dist/rules.json`.

### Stage 2 Tool & CLI Quick Reference

| MCP Tool | CLI Equivalent | Purpose |
|---|---|---|
| `srd_rule` | `bin/srd rule [<id>] [--coverage]` | Inspect compiled Rules IR or global coverage summary |
| `srd_compile_character` | `bin/srd character (<spec.json> \| --spec <json>)` | Validate & compile a 2024 PC spec into a derived statblock |
| `srd_encounter` | `bin/srd encounter (--intent <json> \| <payload.json>) [--state S] [--out-state O]` | Advance a deterministic 5e 2024 encounter step |

---

## Stage 3: Multi-Agent Tabletop & Eval Arena

Experimental: arena scoring and isolation fixes are in progress. Do not run the arena or srd-mcp --arena with a model API key set in your environment.

Stage 3 layers a deterministic, partial-observability **Multi-Agent 5e 2024 (SRD 5.2) Tabletop & Model Evaluation Arena** (`src/srd2024/arena/`) on top of Stage 2. Every PC is compiled through `compile_character()` and every move, attack, mastery, spell, concentration save, and death save is adjudicated strictly by `combat.step()`—drivers only emit structured intents, rationales, and private party `table_talk`.

Off-turn reaction spells such as Shield, sent through `srd encounter` or `srd_encounter`, are accepted only when the encounter state already has a `reaction_window` naming that caster. The engine does not open that window when an attack hits; the caller has to set it. On the caster's own turn a reaction spell still spends their reaction.

### 1. Scenarios & CLI Surfaces (`bin/srd-arena` / `bin/srd arena`)
Both `bin/srd-arena <subcommand>` and `bin/srd arena <subcommand>` expose identical subcommands and 1:1 JSON parity:
- **Built-in scenarios:** `goblin-ambush` (Tier 1, L1), `crypt-of-ghouls` (Tier 2, L5), `wyrmling-lair` (Tier 2, L5 fire-immune lair) — plus custom scenario JSON files.
- **Offline deterministic drivers (zero network):** `heuristic-tactical`, `greedy`, `random`, `replay`.
- **Pure-stdlib live LLM drivers (`urllib.request`, zero external SDKs):** `openai-compat:<model>` (works with OpenAI, xAI, OpenRouter, Ollama, vLLM, LM Studio via `--api-base` / `--api-key-env`), `anthropic:<model>`, and `gemini:<model>`. `anthropic:` uses `ANTHROPIC_API_KEY` if set, else `ANTHROPIC_AUTH_TOKEN`, else the Anthropic CLI sign-in (`ant auth login`; the driver asks `ant` for a fresh token per request and sends it only to `https://api.anthropic.com`). Use a current model such as `anthropic:claude-opus-5-5` or `anthropic:claude-haiku-5-5`.

### 2. Quantitative Evaluation & Deterministic Trace Replay
- Every match produces a `MatchScorecard` (`0–100`) combining `outcome_score` (35%), `rules_legality_score` (25%), `tactical_score` (25%, including immunity discipline & mastery execution), and `coordination_score` (15%, including zero friendly-fire, party `table_talk`, focus-fire, and ally support) into `composite_score`.
- Save any match transcript via `--out-trace <path>` and verify deterministic replay via `bin/srd-arena replay <trace.json>`. **Judge it by `deterministic_match` (and exit code 0), not by `status` or the digests:** a replay that diverges still reports `"status": "ok"`, and the two digests can match for a tampered trace. Only `deterministic_match: false` (exit 1, plus a `diffs` list naming the action) catches the difference.

### 3. Stage 3 MCP Discovery Mode (`--arena` / `SRD2024_ARENA=1`)
- Launch **`bin/srd-mcp --arena`** (or set `SRD2024_ARENA=1`, or `bin/srd-engine-mcp --arena`) to advertise all **11 MCP tools** (5 Stage 1 + 3 Stage 2 + 3 Stage 3: `srd_arena_scenarios`, `srd_arena_run`, `srd_arena_bench`) plus `srd://arena/scenarios`.
- Default `bin/srd-mcp` keeps the 5-tool Stage 1 lock and `bin/srd-engine-mcp` / `--engine` keeps the 8-tool Stage 1+2 lock. **Stage 3 tools and resources exist only in arena mode:** without `--arena`, `tools/call` on `srd_arena_*` returns `Unknown tool` and `resources/read` on `srd://arena/scenarios` returns `Unknown resource URI`. (Stage 2 tools are still callable from the default launcher.)
- **Paid LLM drivers are off by default over MCP:** `srd_arena_run` / `srd_arena_bench` with an `openai-compat:`, `anthropic:` or `gemini:` agent fail unless the server was started with `--arena-allow-llm` (or `SRD2024_ARENA_ALLOW_LLM=1`). The CLI (`bin/srd-arena`) has no such gate.

### Stage 3 Tool & CLI Quick Reference

| MCP Tool | CLI Equivalent (`bin/srd-arena` or `bin/srd arena`) | Purpose |
|---|---|---|
| `srd_arena_scenarios` | `bin/srd-arena scenarios` | List built-in 5e 2024 arena scenarios |
| `srd_arena_run` | `bin/srd-arena run <scenario_or_json> [--party-agent A] [--enemy-agent E] [--seed N] [--out-trace P]` | Run a deterministic multi-agent match + scorecard |
| `srd_arena_bench` | `bin/srd-arena bench [--scenarios S] [--agents A] [--enemy-agent E] [--seeds 1,2,3] [--out-json P]` | Run a multi-seed / multi-scenario leaderboard benchmark |
| *(CLI replay)* | `bin/srd-arena replay <trace.json>` | Replay a saved transcript and verify SHA-256 digest match |

---

## Stage 4: Human-in-the-Loop Interactive Play & Adversarial QA

Stage 4 extends the 5e 2024 (SRD 5.2) pack with interactive human-in-the-loop tabletop play and automated adversarial engine verification (strictly Python 3.11+ stdlib, zero external dependencies):

- **Interactive Play (`bin/srd-arena play` / `bin/srd arena play`):** Join any built-in or custom scenario with an ASCII 5-ft battlefield HUD. Opposing observations and menus use HP bands and do not include the other side's exact HP, temporary HP, AC, save DC, or spell slots. Numbered legal action menus, tactical shortcuts (`e`, `move <x> <y>`, `stand`, `say <msg>`, `save`, `quit`), and optional natural-language intent translation (`--nl-driver <spec>`). The `human` driver exists only on this command. Checkpoints hold RNG state and exact HP for the player's own resume; `--resume` takes drivers from the current command, and `--save` / `--out-trace` will not overwrite a file without `--force`.
  - Control one PC alongside AI companions (`--as pc-bram`), the whole party (`--as party`), the enemy side (`--as dm`), or mix arbitrary seats (`--seat pc-aldric=human --seat pc-lyra=heuristic-tactical`).
  - Pause/resume mid-match with `--save <checkpoint.json>` / `--resume <checkpoint.json>` and export deterministic traces via `--out-trace <trace.json>` (verifiable with `bin/srd-arena replay`).
- **Deterministic Simulation Testing (`bin/srd-dst`):** Runs seeded stateful fuzzing across scenarios and adversarial intents, checking universal 5e 2024 invariants after every `step()` (HP/slot bounds, concentration/condition rules, immutability, RNG monotonicity, and 100% seed determinism).
- **AST Mutation Testing (`bin/srd-mutate`):** Runs isolated, non-destructive AST mutation campaigns against `src/srd2024/engine/{dice,character,combat}.py` to measure test suite fault-detection strength without modifying repository files.

### Stage 4 CLI Quick Reference

| CLI Surface | Purpose |
|---|---|
| `bin/srd-arena play <scenario> [--as pc\|party\|dm] [--seat id=spec]... [--save P] [--resume P] [--nl-driver S] [--out-trace T]` | Play interactively alongside or against AI agents (also `bin/srd arena play`) |
| `bin/srd-dst [--seeds N] [--max-steps M] [--format markdown\|json]` | Run deterministic simulation & invariant fuzzing over `srd2024.engine` |
| `bin/srd-mutate [--target dice\|character\|combat\|all] [--limit N] [--format markdown\|json]` | Run isolated AST mutation testing on the rules engine |

---

## Attribution (CC-BY-4.0)

When quoting or summarizing rules from this pack, include this notice:

This work includes material from the System Reference Document 5.2 (“SRD 5.2”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.
