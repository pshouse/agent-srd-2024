# Stage 2: Build-Time Prose-to-Deterministic 5e-2024 Rules Compiler & Engine — Architecture & Contract Specification

> **Status:** FINAL (locked against Stage 1 `docs/SPEC.md`, `docs/schema-audit.md`, empirical audit of `data/raw/2024/en/` — `5e-bits/5e-srd-api` commit `8a339e73`, `5e-database` `v7.10.0` — and all 35 existing Stage 1 verification tests).

---

## 1. Purpose, Neuro-Symbolic Principle & Stage 1 Compatibility

Stage 1 compiled the 25 upstream SRD 5.2 (2024) JSON files (2,029 entities) into a zero-server, token-tight retrieval pack (`dist/srd2024.sqlite`, `dist/cards/`, `dist/web/`, CLI `bin/srd`, MCP `bin/srd-mcp`, and `skill/SKILL.md`).

Stage 2 adds a **neuro-symbolic rules layer** on top of Stage 1:
- **Build-Time Symbolic Compiler (offline, deterministic, zero runtime LLM):** Parses structured SRD JSON plus regular 5e-2024 prose patterns into a typed, deterministic Intermediate Representation (`dist/rules.json`) plus a static coverage manifest (`dist/web/rules-coverage.json`), with **honest provenance and explicit coverage grading (`full` | `partial` | `narrative`)** — never faking executable mechanics for narrative prose.
- **Deterministic Character Compiler & Validator (`src/srd2024/engine/character.py`):** Validates a 2024 PC build JSON spec against SRD rules (species/subspecies, background ASI `+2/+1` or `+1/+1/+1` & Origin Feat, point-buy / standard-array, class/subclass/levels 1–20, weapon masteries, feats & prerequisites, spells) and derives the authoritative statblock.
- **Deterministic 5e-2024 Combat State Machine (`src/srd2024/engine/combat.py` + `dice.py`):** Pure, JSON-serializable encounter engine driven by structured agent intents (`start_encounter`, `move`, `attack`, `cast_spell`, `dodge`, `dash`, `disengage`, `help`, `end_turn`) with seeded deterministic RNG, roll overrides for exact unit tests, all 8 2024 Weapon Masteries, all 15 2024 Conditions, Concentration, 0 HP / death saves, and authoritative `events` + `state_diff` + `legal_actions` + `narrative_hooks`.
- **LLM Role:** The agent translates natural language into structured intents and narrates returned `events` / `narrative_hooks`. All dice, modifiers, AC/HP/DC math, mastery triggers, condition rules, and slot tracking stay 100% deterministic in Python.

### Hard Constraints (Non-Negotiable)
1. **Strictly Python 3.11+ stdlib only** (verified on Python 3.14.8): zero third-party packages (`json`, `re`, `math`, `hashlib`, `dataclasses`, `argparse`, `pathlib`, `copy`, `sys`, `os`, `unittest`).
2. **Zero breaking changes to Stage 1 — ALL 35 existing tests in `tests/test_*.py` MUST continue to pass unmodified.** Two subtle Stage 1 test locks are explicitly engineered around in this spec:
   - **Stage 1 Test Lock A (`tests/test_build_and_schema.py:180-188`):** `test_temp_dir_rebuild_is_byte_identical` invokes `bin/srd-build --out-dir <tmp_dist>` and asserts `(tmp_dist / "web").rglob("*.json") == dist/web.rglob("*.json")` byte-for-byte. Therefore, `rules-compiler` adds a minimal post-build hook at the end of `src/srd2024/compiler/build.py:build_all()` so `bin/srd-build` also emits `out_dir/rules.json` and `out_dir/web/rules-coverage.json` without changing `manifest.json`, `srd2024.sqlite`, `cards/**`, `web/index.json`, `web/graph.json`, or `web/types/*.json`.
   - **Stage 1 Test Lock B (`tests/test_cli_and_mcp.py:123-128`):** `test_full_mcp_lifecycle_tools_resources_and_parity` launches `bin/srd-mcp` with no flags and asserts `set(tools_list_names) == {"srd_search", "srd_get", "srd_list", "srd_graph", "srd_validate"}`. Therefore, default `bin/srd-mcp` keeps `tools/list` returning those exact 5 Stage 1 tools (while accepting `tools/call` for Stage 2 tools), and **`bin/srd-mcp --engine`** (or env `SRD2024_ENGINE=1` or executable wrapper **`bin/srd-engine-mcp`**) advertises all **8 tools** (5 Stage 1 + 3 Stage 2) in `tools/list`.
   - **Stage 1 Test Lock C (`tests/test_build_and_schema.py:322-328`):** `src/srd2024/runtime/**` must NEVER import `srd2024.compiler` (and by design also NEVER imports `srd2024.rules_compiler` — runtime reads `dist/rules.json` solely through `srd2024.engine`).

---

## 2. Strict Zero-Collision File Ownership Split (Phase 2B)

Every file touched in Stage 2 has **exactly one owner**:

| Worker | Owned Paths (Exclusive Write) | Read-Only Inputs | Must NEVER Modify |
| :--- | :--- | :--- | :--- |
| **`rules-compiler`** | `src/srd2024/rules_compiler/**`, `bin/srd-rules-build`, `pyproject.toml` (additive script entries only), `src/srd2024/compiler/build.py` (5-line post-build hook at end of `build_all()` only), `dist/rules.json`, `dist/web/rules-coverage.json` | `data/raw/**`, `docs/STAGE2-SPEC.md` | `src/srd2024/engine/**`, `src/srd2024/runtime/**`, `bin/srd`, `bin/srd-mcp`, `bin/srd-engine-mcp`, `skill/`, `README.md`, `tests/**`, Stage 1 `dist/manifest.json`/`srd2024.sqlite`/`cards/`/`web/{index,graph,types}` |
| **`rules-engine`** | `src/srd2024/engine/**` (`__init__.py`, `store.py`, `dice.py`, `character.py`, `combat.py`) | `docs/STAGE2-SPEC.md`, `dist/rules.json` (read-only) | `src/srd2024/compiler/**`, `src/srd2024/rules_compiler/**`, `src/srd2024/runtime/**`, `bin/**`, `pyproject.toml`, `dist/**`, `skill/`, `README.md`, `tests/**` |
| **`surface-v2`** | `src/srd2024/runtime/**` (additive Stage 2 CLI/MCP/render wiring), `bin/srd-engine-mcp`, `skill/SKILL.md`, `README.md` | `docs/STAGE2-SPEC.md`, `src/srd2024/engine/**` public API | `src/srd2024/compiler/**`, `src/srd2024/rules_compiler/**`, `src/srd2024/engine/**`, `bin/srd-build`, `bin/srd-rules-build`, `pyproject.toml`, `dist/**`, `tests/**` |
| **`rules-verifier`** | `tests/test_stage2_rules_compiler.py`, `tests/test_stage2_character_engine.py`, `tests/test_stage2_combat_engine.py`, `tests/test_stage2_surfaces.py` | Everything read-only | `data/raw/**`, `src/**`, `bin/**`, `dist/**`, `pyproject.toml`, `skill/`, `README.md`, **all 5 Stage 1 test files** (`tests/helpers.py`, `test_build_and_schema.py`, `test_cards_and_web.py`, `test_runtime_engine.py`, `test_cli_and_mcp.py`) |

---

## 3. Build-Time Rules IR Compiler (`src/srd2024/rules_compiler/**`)

### 3.1 CLI Wrapper & Determinism Contract
- **Standalone wrapper:** `bin/srd-rules-build` (`#!/usr/bin/env python3`, `chmod +x`), invoking `srd2024.rules_compiler.__main__:main`.
- **Flags:**
  - `--raw-dir <path>` (default: `<repo_root>/data/raw`)
  - `--out-dir <path>` (default: `<repo_root>/dist`)
- **Outputs:**
  1. `<out-dir>/rules.json` — canonical compiled Rules IR (`sort_keys=True`, `separators=(",", ":")` or `indent=2`, UTF-8, LF `\n`, **zero timestamps**). Compact `separators=(",", ":")` with trailing `\n` is preferred to keep file size lean (~1.5–2.5 MiB).
  2. `<out-dir>/web/rules-coverage.json` — lean static coverage report (`sort_keys=True`, `indent=2`, UTF-8, LF `\n`, **zero timestamps**) for static web frontends & audits.
- **Stage 1 `bin/srd-build` integration hook:** At the end of `src/srd2024/compiler/build.py:build_all(raw_dir, out_dir)` (strictly AFTER writing `manifest.json` so Stage 1 artifacts are 100% untouched), call:
  ```python
  from srd2024.rules_compiler.build import build_rules
  build_rules(raw_dir, out_dir)
  ```
  This guarantees `bin/srd-build --out-dir <tmp>` produces identical `web/` contents (including `web/rules-coverage.json`) so Stage 1 `test_temp_dir_rebuild_is_byte_identical` passes unmodified.
- **Determinism:** Rebuilding `bin/srd-rules-build --out-dir <tmp>` twice produces **byte-identical** `rules.json` and `web/rules-coverage.json`.
- **Fixed `pyproject.toml` additions (owned by `rules-compiler`):** Keep all 3 Stage 1 entries verbatim and add:
  ```toml
  srd-rules-build = "srd2024.rules_compiler.__main__:main"
  srd-engine-mcp = "srd2024.runtime.mcp:main_engine"
  ```

### 3.2 Honest Coverage Grading Policy
Every compiled rule entity MUST carry:
```json
{
  "coverage": "full | partial | narrative",
  "provenance": {
    "source_id": "<type>:<slug>",
    "source_file": "5e-SRD-<Name>.json",
    "parser": "structured | regex+structured | template+structured",
    "unmodeled_clauses": []
  }
}
```
Definitions (never inflate `full`):
- **`full`**: Every combat/character mechanical effect described in the SRD entity is deterministically represented in typed IR and executed by `srd2024.engine` (with `unmodeled_clauses == []`).
- **`partial`**: Core numeric/structured mechanics (e.g., attack/save/damage/AC/HP/condition/slots/utilize DC) are compiled and executable, while secondary prose mechanics (e.g., summoned creature statblocks, complex terrain/zones, monster innate spellcasting/legendary/lair prose, optional narrative riders) remain unmodeled and are explicitly listed in `provenance.unmodeled_clauses`.
- **`narrative`**: Entity has no combat/statblock numeric mechanics modeled in the deterministic engine (e.g., divination/social/illusion/world-shaping spells like `augury`, `clairvoyance`, `comprehend-languages`, `prestidigitation`, `wish`, or mundane adventuring gear without combat/utilize rules). The engine still validates and logs its use honestly without hallucinating effects.

### 3.3 Top-Level `dist/rules.json` Schema
```json
{
  "schema_version": "2",
  "dataset_id": "srd-2024-5.2",
  "source_hash": "<sha256 matching Stage 1 manifest.json>",
  "upstream": {
    "repo": "https://github.com/5e-bits/5e-srd-api",
    "commit": "8a339e73ed5bba9ffec2ad6be9e76b5afa9c1b24",
    "database_version": "7.10.0"
  },
  "license": {
    "content": "CC-BY-4.0",
    "content_url": "https://creativecommons.org/licenses/by/4.0/legalcode",
    "attribution": "<exact string from PROVENANCE.json>"
  },
  "coverage_summary": { "...": "same schema as dist/web/rules-coverage.json summary" },
  "weapon_masteries": { "<slug>": { "...": "§3.4 (8 entries)" } },
  "conditions": { "<slug>": { "...": "§3.5 (15 entries)" } },
  "equipment": { "<slug>": { "...": "§3.6 (182 entries)" } },
  "spells": { "<slug>": { "...": "§3.7 (339 entries)" } },
  "monsters": { "<slug>": { "...": "§3.8 (341 entries)" } },
  "character_creation": {
    "ability_scores": { "<slug>": {} },
    "skills": { "<slug>": {} },
    "damage_types": ["acid", "..."],
    "classes": { "<slug>": { "...": "12 entries" } },
    "subclasses": { "<slug>": { "...": "12 entries" } },
    "species": { "<slug>": { "...": "9 entries" } },
    "subspecies": { "<slug>": { "...": "24 entries" } },
    "backgrounds": { "<slug>": { "...": "4 entries" } },
    "feats": { "<slug>": { "...": "17 entries" } },
    "levels": { "<slug>": { "...": "287 entries" } },
    "traits": { "<slug>": { "...": "68 entries" } }
  }
}
```
All dictionaries MUST be sorted by key.

---

### 3.4 Weapon Masteries IR (`weapon_masteries`, 8 / 8 = 100% `full`)
Compiled from `5e-SRD-Weapon-Mastery-Properties.json` (8 entries). Every mastery has `coverage: "full"` and typed executable trigger/effect fields:

```json
{
  "id": "weapon-mastery-properties:topple",
  "slug": "topple",
  "name": "Topple",
  "trigger": "on_hit",
  "requires_damage": false,
  "requires_melee": false,
  "once_per_turn": false,
  "max_target_size": null,
  "effect": {
    "kind": "save_condition",
    "save_ability": "con",
    "dc_formula": "8 + attack_ability_mod + proficiency_bonus",
    "on_fail_condition": "prone"
  },
  "description": "<verbatim SRD description>",
  "coverage": "full",
  "provenance": { "...": "..." }
}
```

Exact locked mechanics for all 8 masteries (verified verbatim against `5e-SRD-Weapon-Mastery-Properties.json`):

| Slug | `trigger` | Constraints | `effect.kind` & Parameters |
|---|---|---|---|
| `cleave` | `on_hit` | `requires_melee: true`, `once_per_turn: true`, 2nd target within `5` ft of primary & within reach | `extra_attack`: `add_positive_ability_mod: false` (negative mod still applies) |
| `graze` | `on_miss` | `requires_damage: false` | `guaranteed_damage`: `amount: "attack_ability_mod"` (min 0), same weapon damage type |
| `nick` | `on_light_extra_attack` | `once_per_turn: true` | `move_light_extra_to_attack_action`: makes Light property extra attack part of Attack action instead of Bonus Action (`add_positive_ability_mod: false` unless Two-Weapon Fighting) |
| `push` | `on_hit` | `max_target_size: "Large"` (`Tiny`/`Small`/`Medium`/`Large`) | `forced_move`: `distance_ft: 10`, `direction: "away"` |
| `sap` | `on_hit` | none | `apply_timed_effect`: `target_next_attack_disadvantage`, expires `start_of_attacker_next_turn` |
| `slow` | `on_hit` | `requires_damage: true` (`damage > 0`) | `apply_timed_effect`: `speed_penalty_ft: 10`, `stacking: "max"`, expires `start_of_attacker_next_turn` |
| `topple` | `on_hit` | none | `save_condition`: `save_ability: "con"`, `dc_formula: "8 + attack_ability_mod + proficiency_bonus"`, `on_fail_condition: "prone"` |
| `vex` | `on_hit` | `requires_damage: true` (`damage > 0`) | `apply_timed_effect`: `attacker_next_attack_advantage_vs_target`, expires `end_of_attacker_next_turn` |

---

### 3.5 Conditions IR (`conditions`, 15 / 15 = 100% `full`)
Compiled from `5e-SRD-Conditions.json` (15 entries). Every condition has `coverage: "full"` and typed mechanical modifiers executed directly by the combat engine:

```json
{
  "id": "conditions:paralyzed",
  "slug": "paralyzed",
  "name": "Paralyzed",
  "implies": ["incapacitated"],
  "modifiers": {
    "speed_zero": true,
    "cannot_act": true,
    "breaks_concentration": true,
    "attacks_made": null,
    "attacks_against": "advantage",
    "attacks_against_within_5ft": null,
    "attacks_against_beyond_5ft": null,
    "auto_crit_within_5ft": true,
    "auto_fail_saves": ["str", "dex"],
    "save_disadvantage": [],
    "ability_checks": null,
    "initiative": null,
    "resist_all_damage": false,
    "immune_conditions": [],
    "d20_test_penalty_per_level": 0,
    "speed_penalty_ft_per_level": 0,
    "death_at_level": null,
    "special_rules": []
  },
  "description": "<verbatim SRD description>",
  "coverage": "full",
  "provenance": { "...": "..." }
}
```

Exact locked modifier table for all 15 2024 conditions (verified verbatim against `5e-SRD-Conditions.json`):
1. **`blinded`**: `attacks_made: "disadvantage"`, `attacks_against: "advantage"`, `special_rules: ["auto_fail_sight_checks"]`.
2. **`charmed`**: `special_rules: ["cannot_target_charmer", "charmer_advantage_social_checks"]`.
3. **`deafened`**: `special_rules: ["auto_fail_hearing_checks"]`.
4. **`exhaustion`** (2024 rules!): `d20_test_penalty_per_level: -2` (applies to attack rolls, saving throws, ability checks), `speed_penalty_ft_per_level: -5`, `death_at_level: 6`.
5. **`frightened`**: `attacks_made: "disadvantage"` (while source in line of sight), `ability_checks: "disadvantage"`, `special_rules: ["cannot_move_closer_to_source"]`.
6. **`grappled`**: `speed_zero: true`, `special_rules: ["disadvantage_attacks_vs_non_grappler"]`.
7. **`incapacitated`**: `cannot_act: true` (no Action, Bonus Action, or Reaction), `breaks_concentration: true`, `initiative: "disadvantage"`.
8. **`invisible`**: `initiative: "advantage"`, `attacks_made: "advantage"`, `attacks_against: "disadvantage"`.
9. **`paralyzed`**: `implies: ["incapacitated"]`, `speed_zero: true`, `cannot_act: true`, `breaks_concentration: true`, `auto_fail_saves: ["str", "dex"]`, `attacks_against: "advantage"`, `auto_crit_within_5ft: true`.
10. **`petrified`**: `implies: ["incapacitated"]`, `speed_zero: true`, `cannot_act: true`, `breaks_concentration: true`, `auto_fail_saves: ["str", "dex"]`, `attacks_against: "advantage"`, `resist_all_damage: true`, `immune_conditions: ["poisoned"]`.
11. **`poisoned`**: `attacks_made: "disadvantage"`, `ability_checks: "disadvantage"`.
12. **`prone`**: `attacks_made: "disadvantage"`, `attacks_against_within_5ft: "advantage"`, `attacks_against_beyond_5ft: "disadvantage"`, `special_rules: ["crawl_double_cost", "stand_up_half_speed"]`.
13. **`restrained`**: `speed_zero: true`, `attacks_made: "disadvantage"`, `attacks_against: "advantage"`, `save_disadvantage: ["dex"]`.
14. **`stunned`**: `implies: ["incapacitated"]`, `cannot_act: true`, `breaks_concentration: true`, `auto_fail_saves: ["str", "dex"]`, `attacks_against: "advantage"`.
15. **`unconscious`**: `implies: ["incapacitated", "prone"]`, `speed_zero: true`, `cannot_act: true`, `breaks_concentration: true`, `auto_fail_saves: ["str", "dex"]`, `attacks_against: "advantage"`, `auto_crit_within_5ft: true`, `special_rules: ["remains_prone_when_ended"]`.

---

### 3.6 Equipment IR (`equipment`, all 182 items)
Compiled from `5e-SRD-Equipment.json` (182 items). Empirically verified quirks handled explicitly:
- **All 38 Weapons (`coverage: "full"`):**
  - `kind: "weapon"`, `weapon_category: "simple" | "martial"`, `weapon_range: "melee" | "ranged"`.
  - `damage_dice` (str, e.g., `"1d8"`, `"1"` for `blowgun`), `damage_type` (slug).
  - `versatile_dice` (str | null, from `two_handed_damage.damage_dice` — 7 weapons).
  - `properties`: sorted list of property slugs (`finesse`, `light`, `heavy`, `reach`, `thrown`, `two-handed`, `versatile`, `ammunition`, `loading`, `range`).
  - `mastery`: mastery slug (all 38/38 weapons have `mastery` in raw JSON!).
  - `attack_ability`: derived deterministically (note: raw JSON `ability` key exists ONLY on 37 tools, NOT weapons!):
    - If `"finesse"` in properties → `"finesse"` (engine uses `max(str_mod, dex_mod)`).
    - Else if `weapon_range == "ranged"` → `"dex"`.
    - Else → `"str"`.
  - `reach_ft`: `10` if `"reach"` in properties else (`5` if `weapon_range == "melee"` else `null`). Handles `morningstar` (the 1/38 weapon omitting `range` in raw JSON!).
  - `range_normal_ft`, `range_long_ft`: from `range` (for ranged weapons) or `null`.
  - `throw_range`: `{"normal_ft": int, "long_ft": int} | null` (from `throw_range` — 7 weapons).
  - `ammunition_slug`: from `ammunition.index` (9 weapons) or `null`.
- **All 13 Armors & Shields (`coverage: "full"`):**
  - `kind: "shield"` for `shield` (`armor_class.base == 2`, category `shields`), else `kind: "armor"`.
  - `armor_category`: `"light" | "medium" | "heavy" | "shield"`. **Upstream quirk note:** `hide-armor` is tagged `light-armor` in upstream `equipment_categories` despite having `armor_class: {base: 12, dex_bonus: true, max_bonus: 2}`; compiler MUST classify `armor_category` as `"medium"` when `max_bonus == 2` (or honor `armor_class.max_bonus` directly in AC computation).
  - `armor_class`: `{"base": int, "dex_bonus": bool, "max_bonus": int | null}`.
  - `str_minimum`: int (0 if none; 13 for `chain-mail`, 15 for `splint-armor`/`plate-armor`).
  - `stealth_disadvantage`: bool.
  - `don_time`, `doff_time`: str | null.
- **Remaining 131 Items (tools, gear, packs, mounts):**
  - Tools with structured `utilize[]` (36 items) and packs/containers with `contents[]` (7 items): `coverage: "partial"` (preserves `ability`, `utilize[]` with DC/action, `craft[]`, `contents[]`, `cost`, `weight` — defaulting `weight: null` for `entertainers-pack` which omits `weight`).
  - Pure mundane gear without combat/utilize mechanics (~88 items): `coverage: "narrative"` (preserves `cost`, `weight`, `categories`, `description`).
- **Empirical coverage floor:** `full == 51` (38 weapons + 13 armor/shields), `partial >= 40`, total `182`.

---

### 3.7 Spells IR (`spells`, all 339 spells)
Compiled from `5e-SRD-Spells.json` (339 spells). Must normalize known upstream quirks:
- **Upstream `range` quirk (9 spells):** Strip trailing `r"\s+Component:.*$"` from `range` before parsing (`contagion`, `divine-smite`, `find-steed`, `guidance`, `power-word-heal`, `power-word-kill`, `resistance`, `searing-smite`, `sorcerous-burst`).
- **Inline upcast/cantrip prose:** Parse scaling from BOTH `higher_level` AND inline `"Using a Higher-Level Spell Slot."` / `"Cantrip Upgrade."` paragraphs inside `description` (e.g., `cure-wounds` puts upcast scaling in `description`, not `higher_level`).
- **Typographic normalization:** Normalize `’` → `'`, `–`/`−` → `-` during regex extraction while preserving original `name` / `description`.

#### Spell IR Schema
```json
{
  "id": "spells:fireball",
  "slug": "fireball",
  "name": "Fireball",
  "level": 3,
  "school": "evocation",
  "classes": ["sorcerer", "wizard"],
  "casting_time": "Action",
  "action_type": "action",
  "reaction_trigger": null,
  "range_raw": "150 feet",
  "range_kind": "ranged",
  "range_ft": 150,
  "components": ["V", "S", "M"],
  "material": "a ball of bat guano and sulfur",
  "duration_raw": "Instantaneous",
  "duration_rounds": 0,
  "concentration": false,
  "ritual": false,
  "targeting": {
    "mode": "aoe",
    "max_targets": null,
    "aoe": {"shape": "sphere", "size_ft": 20, "width_ft": null}
  },
  "resolution": "save",
  "attack_kind": null,
  "save": {
    "ability": "dex",
    "on_success": "half"
  },
  "damage": [
    {"dice": "8d6", "type": "fire", "add_spell_mod": false}
  ],
  "healing": null,
  "temp_hp": null,
  "conditions_applied": [],
  "conditions_ended": [],
  "buffs": [],
  "special_effect": null,
  "scaling": {
    "cantrip_tiers": null,
    "slot_damage_per_level": "1d6",
    "slot_healing_per_level": null,
    "slot_targets_per_level": 0,
    "slot_projectiles_per_level": 0
  },
  "coverage": "full",
  "provenance": {
    "source_id": "spells:fireball",
    "source_file": "5e-SRD-Spells.json",
    "parser": "regex+structured",
    "unmodeled_clauses": []
  }
}
```

#### Field Specifications
- `action_type`: `"action" | "bonus_action" | "reaction" | "long"` (parsed from leading phrase of `casting_time`).
- `duration_rounds`: `0` (`Instantaneous`), `1` (`1 round` / `until the start of your next turn`), `10` (`1 minute`), `100` (`10 minutes`), `600` (`1 hour`), `4800` (`8 hours`), `14400` (`24 hours`), or `null` (`Until dispelled` / `Special` / days).
- `resolution`: `"attack" | "save" | "auto_hit" | "auto_apply" | "utility"`.
- `healing`: `{"dice": "2d8", "flat": 0, "add_spell_mod": true} | null` (e.g., `cure-wounds` `2d8 + mod`, `healing-word` `2d4 + mod`, `heal` flat `70`).
- `temp_hp`: `{"dice": "2d4", "flat": 4, "add_spell_mod": false} | null` (e.g., `false-life`).
- `conditions_applied`: list of `{"condition": "<slug>", "trigger": "on_fail | on_hit | auto", "duration": "concentration | end_of_next_turn | start_of_next_turn | save_ends | spell_duration", "repeat_save_ability": "<ab>|null"}`.
- `buffs`: typed list of executable buff objects:
  - `{"kind": "ac_bonus", "value": 5, "duration": "start_of_next_turn", "blocks_spell": "magic-missile"}` (`shield`)
  - `{"kind": "ac_bonus", "value": 2, "duration": "concentration"}` (`shield-of-faith`, `haste`)
  - `{"kind": "ac_formula", "base": 13, "add_dex": true, "requires_unarmored": true}` (`mage-armor`)
  - `{"kind": "ac_min", "value": 17}` (`barkskin`)
  - `{"kind": "roll_bonus_dice", "dice": "1d4", "applies_to": ["attack", "save"], "sign": 1}` (`bless`) / `sign: -1` (`bane`)
  - `{"kind": "speed_bonus_ft", "value": 10}` (`longstrider`) / `{"kind": "speed_multiplier", "value": 2}` (`haste`)
  - `{"kind": "next_attack_advantage"}` (`guiding-bolt`, `true-strike`)
  - `{"kind": "teleport", "max_distance_ft": 30}` (`misty-step`)
- `special_effect`: for iconic multi-projectile/special spells cleanly modeled via `template+structured`:
  - `magic-missile`: `resolution: "auto_hit"`, `damage: [{"dice": "1d4+1", "type": "force"}]`, `projectiles_base: 3`, `slot_projectiles_per_level: 1`, blocked by active `shield`.
  - `scorching-ray`: `resolution: "attack"`, `attack_kind: "ranged"`, `damage: [{"dice": "2d6", "type": "fire"}]`, `projectiles_base: 3`, `slot_projectiles_per_level: 1`.
  - `eldritch-blast`: `resolution: "attack"`, `attack_kind: "ranged"`, `damage: [{"dice": "1d10", "type": "force"}]`, cantrip beams `1/2/3/4` at character levels `1/5/11/17`.
  - Standard damage cantrips (`fire-bolt`, `sacred-flame`, `chill-touch`, `acid-splash`, `poison-spray`, `ray-of-frost`, `shocking-grasp`, `vicious-mockery`, etc.): `cantrip_tiers: {"1": "1dX", "5": "2dX", "11": "3dX", "17": "4dX"}`.
- **Empirical Spell Coverage Floors (verified against all 339 spells):**
  - `full >= 80` (MUST include `fireball`, `lightning-bolt`, `burning-hands`, `shatter`, `magic-missile`, `scorching-ray`, `guiding-bolt`, `fire-bolt`, `sacred-flame`, `eldritch-blast`, `acid-splash`, `ray-of-frost`, `chill-touch`, `vicious-mockery`, `cure-wounds`, `healing-word`, `heal`, `shield`, `mage-armor`, `shield-of-faith`, `bless`, `bane`, `hold-person`, `misty-step`, `false-life`, `longstrider`).
  - `full + partial >= 200` (out of 339, >= 59%).
  - Remaining pure exploration/social/illusion/divination/world-shaping spells (~100–139) honestly classified as `narrative`.

---

### 3.8 Monsters IR (`monsters`, all 341 monsters)
Compiled from `5e-SRD-Monsters.json` (341 monsters). Upstream quirks handled explicitly:
- `armor_class` entries have `{"value": int}` and optional `"armor": [...]` (NO `"type"` key!). Use `armor_class[0]["value"]`.
- `speed` values are strings like `"30 ft."` (plus boolean `hover: true` on 17 monsters) — parse numeric integer feet per movement mode (`walk_ft`, `fly_ft`, `swim_ft`, `climb_ft`, `burrow_ft`, `hover`).
- `damage_resistances` / `damage_immunities` / `damage_vulnerabilities`: separate standard 13 SRD damage types (lowercased slugs) from the 2 prose strings (`"damage type chosen for the draconic origin trait below"`, `"piercing damage from weapons wielded by creatures under the effect of a bless spell"`), storing those in `special_damage_traits: list[str]`.
- `proficiencies`: derive full `saves: {"str": int, ..., "cha": int}` (ability mod unless overridden by `saving-throw-<ab>` proficiency value) and `skills: {"perception": int, ...}` (from `skill-<slug>` entries).
- `actions` (838 total across 339 monsters; 2 monsters — `shrieker-fungus` and `vampire-mist` — legitimately have `actions: []`):
  - **Attack actions (440/838):** parse `attack_kind` (`"melee" | "ranged" | "melee_or_ranged"`), `attack_bonus` (int), `reach_ft` (int | null), `range_normal_ft` / `range_long_ft` (int | null), `damage_parts` (`[{"dice", "type", "conditional": null | "advantage"}]` — detects conditional bonus dice like `goblin-warrior`'s `"if the attack roll had Advantage"`!), plus optional rider `save` (`dc`, `ability`, `on_success`) and `conditions_applied` (e.g., `ghoul` Claw CON DC 10 → `paralyzed`, `wolf` Bite → `prone`).
  - **Save-only / Breath / AoE actions (184/838):** parse `save: {"ability", "dc", "on_success"}`, `aoe: {"shape", "size_ft", "width_ft"}`, `damage_parts`, `conditions_applied`, `usage` (e.g. `{"type": "recharge on roll", "min_value": 5, "dice": "1d6"}` or `per_day`).
  - **Multiattack (188/838):** normalize all 3 upstream shapes (`actions` only: 93, `actions`+`action_options`: 41, `action_options` only: 54) into deterministic `multiattack: {"total_attacks": int | null, "sequence": [{"action_name": str, "count": int}], "options": [...], "desc": str}`.
  - **Spellcasting (60/838):** extract `ability`, `save_dc`, `attack_bonus`, and structured spell list with usage (`at_will`, `per_day`).
- **Coverage grading for monsters (honest):**
  - `full`: Has executable statblock AND all primary combat actions parsed cleanly with no unmodeled `spellcasting` / `legendary_actions` / complex prose traits. Stat-block traits (`special_abilities`), bonus actions and reactions are not executed, so each is listed in `unmodeled_clauses` (`special_ability:<slug>`, `bonus_action:<slug>`, `reaction:<slug>`) and grades the monster `partial`, apart from a short list of traits with no effect in the 2D combat model (#39). Floor after #39: **`full >= 115`** (121 at the fix; was 262 while traits went uncounted).
  - `partial`: Full defensive/offensive statblock & core attacks executable, plus unmodeled spellcasting / legendary actions / complex special abilities / zero actions (`shrieker-fungus`, `vampire-mist`) recorded in `unmodeled_clauses`.
  - `narrative`: **`0`** (all 341 monsters have full deterministic statblocks).

---

### 3.9 Character Creation Rules IR (`character_creation`)
Compiled directly from the structured SRD JSON files (100% deterministic, zero prose guessing):
- **`ability_scores` (6):** `str`, `dex`, `con`, `int`, `wis`, `cha` + linked skill slugs.
- **`skills` (18):** `slug -> {"name", "ability"}`.
- **`classes` (12):** `hit_die`, `saving_throws` (2 abilities), `primary_ability`, `proficiencies` (armor/weapon/tool/skill slugs), `proficiency_choices` (`choose`, `from_skills[]`), `spellcasting_ability` (8 casters, else `null`), `subclasses[]`, `weapon_mastery_progression`:
  - Derived cleanly from `5e-SRD-Levels.json`:
    - `fighter`: `class_specific.weapon_mastery` (L1–3: 3, L4–9: 4, L10–15: 5, L16–20: 6)
    - `barbarian`: `class_specific.weapon_mastery` (L1–3: 2, L4–9: 3, L10–20: 4)
    - `paladin`, `ranger`, `rogue`: have `*-weapon-mastery` feature at level 1 → fixed `2` across levels 1–20
    - All other 7 classes: `0`
  - `asi_levels`: levels whose `features[]` contain `ability-score-improvement` (standard `[4, 8, 12, 16]`; Fighter `[4, 6, 8, 12, 14, 16]`; Rogue `[4, 8, 10, 12, 16]`; Epic Boon at `19` for all 12 classes).
- **`subclasses` (12):** `slug`, `name`, `class`, `feature_levels`, `features[]`.
- **`species` (9):** `slug`, `name`, `creature_type`, `speed_ft`, `size` (or `size_options: ["Medium", "Small"]` for `tiefling`), `subspecies[]`, `traits[]`.
- **`subspecies` (24):** `slug`, `name`, `species`, `damage_type` (10 dragonborn ancestries), `traits[]` (`{trait, level}`).
- **`traits` (68):** `slug`, `name`, `species[]`, `subspecies[]`, `speed_override_ft` (35 for `wood-elf-speed-increase`), `darkvision_ft` (60/120), `damage_resistances[]` (e.g. `dwarven-resilience` -> `poison`, `lineage-resistance-*`, `draconic-damage-resistance-*`), `hp_per_level_bonus` (`1` for `dwarven-toughness`), `extra_origin_feat` (`true` for Human `versatile`), `extra_skill_choice` (`true` for Human `skillful`), `spells[]`.
- **`backgrounds` (4):** `slug`, `name`, `ability_scores` (exact 3 allowed abilities for 2024 `+2/+1` or `+1/+1/+1`), `feat` (origin feat slug), `proficiencies[]` (skills/tools), `proficiency_choices[]`.
- **`feats` (17):** `slug`, `name`, `feat_type` (`origin` | `general` | `fighting-style` | `epic-boon`), `repeatable` (bool), `prerequisites` (`minimum_level`, `feature_named`), `prerequisite_options` (e.g. `grappler`: STR >= 13 OR DEX >= 13), `mechanical_effects` (structured for `alert`: `+PB` initiative; `defense`: `+1 AC` in armor; `archery`: `+2` ranged weapon attack; `two-weapon-fighting`: add positive ability mod to off-hand Light attack; `ability-score-improvement`: `+2` to one or `+1/+1` to two abilities up to 20).
- **`levels` (287):** keyed by `slug` (e.g. `fighter-5`, `wizard-5`, `path-of-the-berserker-3`), preserving `level`, `class`, `subclass`, `prof_bonus`, `features[]`, `class_specific`, `spellcasting` (exact slot counts `spell_slots_level_1..9`, `cantrips_known`, `prepared_spells`).

---

### 3.10 Static Coverage Manifest (`dist/web/rules-coverage.json`)
Lean static JSON emitted by `rules-compiler` for dashboards and audits:
```json
{
  "schema_version": "2",
  "dataset_id": "srd-2024-5.2",
  "source_hash": "<sha256>",
  "summary": {
    "total_compiled": 885,
    "full": 0,
    "partial": 0,
    "narrative": 0,
    "executable_pct": 0.0,
    "by_domain": {
      "weapon_masteries": {"total": 8, "full": 8, "partial": 0, "narrative": 0, "executable_pct": 100.0},
      "conditions": {"total": 15, "full": 15, "partial": 0, "narrative": 0, "executable_pct": 100.0},
      "equipment": {"total": 182, "full": 51, "partial": 43, "narrative": 88, "executable_pct": 51.6},
      "spells": {"total": 339, "full": 95, "partial": 120, "narrative": 124, "executable_pct": 63.4},
      "monsters": {"total": 341, "full": 260, "partial": 81, "narrative": 0, "executable_pct": 100.0}
    },
    "character_creation_counts": {
      "classes": 12, "subclasses": 12, "species": 9, "subspecies": 24,
      "backgrounds": 4, "feats": 17, "levels": 287, "traits": 68
    }
  },
  "entities": {
    "weapon_masteries": [{"id": "weapon-mastery-properties:cleave", "name": "Cleave", "coverage": "full", "unmodeled_clauses": []}],
    "conditions": [],
    "equipment": [],
    "spells": [],
    "monsters": []
  }
}
```
(`executable_pct = round(100.0 * (full + partial) / total, 1)`.)

---

## 4. Deterministic Character Compiler & Validator (`src/srd2024/engine/character.py`)

### 4.1 Rules Store (`src/srd2024/engine/store.py`)
`RulesStore` loads `dist/rules.json` strictly read-only (default path resolution: explicit `rules_path` → `SRD2024_RULES` env var → `<repo_root>/dist/rules.json`). Exposes `get_rule(identifier: str)` supporting canonical ID (`spells:fireball`), domain-prefixed ID, or bare slug (returning `status: "ambiguous"` with sorted candidates on collision — matching Stage 1 §4.2!).

### 4.2 Input Spec (`CharacterBuildSpec`)
```json
{
  "name": "Aldric Vance",
  "species": "human",
  "subspecies": null,
  "size": "Medium",
  "background": "soldier",
  "background_asi": {"str": 2, "con": 1},
  "origin_feat": "savage-attacker",
  "extra_origin_feat": "alert",
  "class": "fighter",
  "subclass": "champion",
  "level": 5,
  "ability_method": "point_buy",
  "base_abilities": {"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 13, "cha": 10},
  "asi_choices": [
    {"level": 4, "type": "asi", "increases": {"str": 2}}
  ],
  "fighting_style": "defense",
  "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
  "equipment": {
    "armor": "chain-mail",
    "shield": true,
    "weapons": ["longsword", "greatsword", "javelin"]
  },
  "weapon_masteries": ["sap", "graze", "slow", "topple"],
  "weapon_mastery_weapons": ["longsword", "greatsword", "javelin", "battleaxe"],
  "cantrips": [],
  "prepared_spells": [],
  "hp_method": "fixed",
  "hp_rolls": []
}
```
Flexible input ergonomics:
- Accepts either bare slugs (`"fighter"`, `"longsword"`) OR canonical Stage 1 IDs (`"classes:fighter"`, `"equipment:longsword"`).
- `weapon_mastery_weapons` (2024 RAW: you pick N weapon kinds like `["longsword", "greatsword", "javelin", "battleaxe"]`) is preferred; if omitted and `weapon_masteries` (mastery slugs) is passed instead, validate count <= class allowance and match against equipped weapons cleanly.
- `asi_choices` supports `{"level": 4, "type": "asi", "increases": {"str": 2}}` OR `{"level": 4, "type": "feat", "feat": "grappler", "ability_increase": "str"}`.

### 4.3 Validation Rules (`errors` vs `warnings`)
`compile_character(spec: dict, rules: RulesStore) -> dict` returns:
```json
{
  "status": "ok | invalid",
  "valid": true,
  "errors": [{"code": "...", "path": "...", "message": "..."}],
  "warnings": [{"code": "...", "path": "...", "message": "..."}],
  "statblock": { "...": "§4.4 (always populated on valid, best-effort or null on fatal unknown entity)" }
}
```
Exact validation checks:
1. **Entity existence:** `species`, optional `subspecies` (must belong to `species`; error if `species` requires subspecies — `dragonborn`, `elf`, `gnome`, `goliath`, `tiefling` — and none supplied), `background`, `class`, optional `subclass` (error if `level >= 3` and missing; error if `level < 3` and provided; error if subclass doesn't belong to `class`), `level` in `1..20`, all `equipment`/`weapons`/`feats`/`spells` exist in `rules.json`.
2. **Base ability scores (`ability_method`):**
   - `"standard_array"`: `sorted(base_abilities.values()) == [8, 10, 12, 13, 14, 15]`.
   - `"point_buy"`: every base score in `8..15`; cost table `{8:0, 9:1, 10:2, 11:3, 12:4, 13:5, 14:7, 15:9}`; total cost `<= 27` (error if `> 27`, warning if `< 27`).
   - `"manual"`: every base score in `3..20`.
3. **2024 Background ASI (`background_asi`):**
   - Total increase must be `3`, strictly either `{a: 2, b: 1}` (2 distinct abilities) or `{a: 1, b: 1, c: 1}` (3 distinct abilities).
   - Every ability key MUST be in `background.ability_scores` (e.g., Soldier allows `str`, `dex`, `con`).
4. **Origin Feats & General/Fighting-Style/Epic-Boon Feats:**
   - Default `origin_feat` to `background.feat` if omitted; if explicitly supplied, must match `background.feat` (error if non-origin feat).
   - `extra_origin_feat` only allowed if species has `versatile` trait (`human`) and must be an `origin` feat.
   - Level ASI/feat picks checked against class `asi_levels` + `19` (Epic Boon); feat prerequisites checked (`minimum_level`, `feature_named`, `prerequisite_options` like `grappler` requiring `STR >= 13 or DEX >= 13`); non-repeatable feat duplicates rejected.
   - Final ability scores capped at `20` (error if ASI/feat pushes > 20; Epic Boon can go up to 30 if applicable).
5. **Weapon Masteries:**
   - Count of chosen mastery weapons `<= class.weapon_mastery_progression[level]` (error if exceeded).
6. **Spells & Cantrips:**
   - Every cantrip (`level == 0`) and prepared spell (`level >= 1`) must exist, include `class` in `spell.classes` (or come from species lineage trait), have `spell.level <= max_castable_slot_level`, and not exceed `cantrips_known` / `prepared_spells` from `levels` (warning if under-prepared, error if over-prepared or wrong class/level).
7. **Equipment Warnings (non-fatal unless invalid item):**
   - Wearing armor with `str_minimum > final_str` → warning (`heavy_armor_str_penalty`, speed `-10` ft).
   - Wearing armor/shield without class proficiency → warning (`unproficient_armor`).
   - Weapon without class proficiency → warning (`unproficient_weapon`, PB omitted from weapon attack bonus).

### 4.4 Derived Statblock Schema (`statblock`)
```json
{
  "name": "Aldric Vance",
  "species": "species:human",
  "subspecies": null,
  "size": "Medium",
  "background": "backgrounds:soldier",
  "class": "classes:fighter",
  "subclass": "subclasses:champion",
  "level": 5,
  "proficiency_bonus": 3,
  "abilities": {"str": 19, "dex": 12, "con": 15, "int": 8, "wis": 13, "cha": 10},
  "ability_mods": {"str": 4, "dex": 1, "con": 2, "int": -1, "wis": 1, "cha": 0},
  "hp_max": 44,
  "hit_die": 10,
  "ac": 19,
  "ac_breakdown": "Chain Mail (16) + Shield (+2) + Defense (+1)",
  "stealth_disadvantage": true,
  "initiative": 4,
  "speed_ft": 30,
  "saving_throws": {"str": 7, "dex": 1, "con": 5, "int": -1, "wis": 1, "cha": 0},
  "save_proficiencies": ["str", "con"],
  "skills": {"acrobatics": 4, "animal-handling": 1, "...": 0},
  "skill_proficiencies": ["acrobatics", "athletics", "intimidation", "perception", "survival"],
  "passive_perception": 14,
  "senses": {"darkvision_ft": 0},
  "damage_resistances": [],
  "features": ["fighter-fighting-style", "fighter-second-wind", "fighter-weapon-mastery", "fighter-extra-attack", "..."],
  "traits": ["resourceful", "skillful", "versatile"],
  "feats": ["savage-attacker", "alert", "defense"],
  "extra_attacks": 2,
  "weapon_profiles": [
    {
      "weapon_id": "equipment:longsword",
      "slug": "longsword",
      "name": "Longsword",
      "proficient": true,
      "attack_ability": "str",
      "attack_bonus": 7,
      "damage_dice": "1d8",
      "damage_bonus": 4,
      "damage_type": "slashing",
      "versatile_dice": "1d10",
      "properties": ["versatile"],
      "mastery": "sap",
      "mastery_active": true,
      "reach_ft": 5,
      "range_normal_ft": null,
      "range_long_ft": null,
      "throw_range": null
    }
  ],
  "spellcasting": null
}
```
Exact derivation formulas (2024 RAW):
- `ability_mod(score) = (score - 10) // 2`.
- `proficiency_bonus`: read from `levels["<class>-<level>"].prof_bonus` (`2 + (level - 1) // 4`).
- `hp_max` (`hp_method="fixed"`): `hit_die + con_mod + (level - 1) * (hit_die // 2 + 1 + con_mod) + (level if dwarf_toughness else 0)` (minimum 1 HP gained per level).
- `ac`:
  - Armored: `armor.base + (min(dex_mod, armor.max_bonus) if armor.max_bonus is not None else dex_mod if armor.dex_bonus else 0) + (2 if shield else 0) + (1 if defense_fighting_style else 0)`.
  - Unarmored: `max(10 + dex_mod, 10 + dex_mod + con_mod if barbarian else 0, 10 + dex_mod + wis_mod if (monk and not shield) else 0) + (2 if (shield and not monk_unarmored_used) else 0)`.
- `initiative`: `dex_mod + (proficiency_bonus if "alert" in feats else 0)`.
- `speed_ft`: species base speed (overridden to `35` for `elven-lineage-wood-elf`), `+ monk.unarmored_movement_bonus` (if unarmored & no shield), `- 10` if heavy armor STR minimum unmet.
- `extra_attacks`: `2` at level >= 5 for Barbarian/Fighter/Monk/Paladin/Ranger (`3` Fighter L11+, `4` Fighter L20), else `1`.
- `spellcasting` (for spellcasters): `{"ability": ab, "spell_save_dc": 8 + pb + mod, "spell_attack_bonus": pb + mod, "slots": {"1": n, ...}, "is_pact_magic": class == "warlock", "cantrips": [...], "prepared_spells": [...]}`.

---

## 5. Deterministic 5e-2024 Encounter & Combat State Machine (`src/srd2024/engine/dice.py` & `combat.py`)

### 5.1 Deterministic RNG & Dice Engine (`dice.py`)
- **Counter-based SHA-256 PRNG (`DeterministicRNG`):**
  - State is strictly `{"seed": int, "draw_index": int}` — tiny, human-readable, 100% JSON-serializable, and identical across Python builds/platforms!
  - `roll_die(sides: int) -> int`: if `roll_overrides` queue is non-empty, pop next int (clamped/validated `1..sides`); else compute `int.from_bytes(hashlib.sha256(f"{seed}:{draw_index}:{sides}".encode()).digest()[:8], "big") % sides + 1`, increment `draw_index`.
- **`roll_d20(mode: "normal" | "advantage" | "disadvantage")`:** returns `{"rolls": [r1] | [r1, r2], "chosen": int, "mode": mode, "nat20": bool, "nat1": bool}`.
- **`roll_dice_expr(expr: str, crit: bool = False)`:** parses `NdM[+/-K]` or flat integer `"1"` (for `blowgun`); on `crit=True`, doubles dice count (`2*N` dice) while adding flat `+K` once.

### 5.2 Encounter State Schema (`EncounterState`)
Pure JSON dict (never mutated in-place; `step()` returns a fresh copy):
```json
{
  "schema_version": "2",
  "encounter_id": "enc-1",
  "status": "active | ended",
  "round": 1,
  "turn_order": ["pc-1", "goblin-1"],
  "active_index": 0,
  "rng": {"seed": 42, "draw_index": 0},
  "combatants": {
    "pc-1": {
      "id": "pc-1",
      "name": "Aldric Vance",
      "side": "party",
      "kind": "pc",
      "source_id": "classes:fighter",
      "size": "Medium",
      "position": {"x": 0, "y": 0},
      "hp": 44,
      "hp_max": 44,
      "temp_hp": 0,
      "ac": 19,
      "speed_ft": 30,
      "movement_remaining_ft": 30,
      "abilities": {"str": 19, "dex": 12, "con": 15, "int": 8, "wis": 13, "cha": 10},
      "ability_mods": {"str": 4, "dex": 1, "con": 2, "int": -1, "wis": 1, "cha": 0},
      "proficiency_bonus": 3,
      "saves": {"str": 7, "dex": 1, "con": 5, "int": -1, "wis": 1, "cha": 0},
      "damage_resistances": [],
      "damage_immunities": [],
      "damage_vulnerabilities": [],
      "condition_immunities": [],
      "conditions": [],
      "exhaustion_level": 0,
      "concentration": null,
      "active_effects": [],
      "turn_resources": {
        "action": true,
        "bonus_action": true,
        "reaction": true,
        "attacks_remaining": 0,
        "cleave_used": false,
        "nick_used": false,
        "disengaged": false
      },
      "death_saves": {"successes": 0, "failures": 0},
      "alive": true,
      "weapons": [],
      "monster_actions": [],
      "spellcasting": null
    }
  }
}
```
- **2D Grid Distance:** Positions are `(x, y)` in feet (multiples of 5). Distance uses standard 5e grid Chebyshev metric: `distance_ft(a, b) = max(abs(a.x - b.x), abs(a.y - b.y))`.
- **Condition entries:** `{"condition": "<slug>", "source_id": "<cid>|null", "duration": "indefinite | save_ends | start_of_source_next_turn | end_of_source_next_turn", "save_ability": "<ab>|null", "save_dc": int|null}`.
- **Active effect entries:** `{"id": str, "kind": "sap | slow | vex | dodge | help | shield | bless | bane | mage_armor | shield_of_faith | barkskin | longstrider | haste | guiding_bolt_advantage", "source_id": str, "target_id": str|null, "value": any, "expires": "start_of_source_next_turn | end_of_source_next_turn | start_of_target_next_turn | consumed | concentration", "concentration_spell": str|null}`.

### 5.3 Structured Agent Intents (`step(state, intent, rules, roll_overrides=None)`)
All intents take `intent: dict` with `"type"`:

1. **`start_encounter`**:
   - Fields: `seed: int` (default `1`), `encounter_id: str` (optional), `combatants: list[dict]` — each entry is either:
     - `{"id": "pc-1", "side": "party", "statblock": <compiled PC statblock>, "position": {"x": 0, "y": 0}}`, OR
     - `{"id": "goblin-1", "side": "enemy", "monster": "goblin-warrior", "name": "Goblin A", "position": {"x": 5, "y": 0}}` (loaded directly from `rules.json`!).
   - Rolls initiative (`1d20 + initiative_mod`, with Invisible Advantage / Incapacitated Disadvantage / Exhaustion penalty), sorts `turn_order` by `(initiative DESC, dex_score DESC, id ASC)`, starts Round 1 Turn 0.
2. **`move`**:
   - Fields: `actor_id`, `to: {"x": int, "y": int}`, `stand_up: bool = False`.
   - Enforces: active actor, not Incapacitated/speed-0 condition, effective speed (`max(0, speed_ft - 5*exhaustion - slow_penalty)`), Prone stand-up costs `floor(effective_speed / 2)` (clears `prone`), crawling while Prone costs `2x` distance, Frightened cannot end closer to fear source.
3. **`attack`**:
   - Fields: `actor_id`, `target_id`, `weapon` (PC weapon slug) OR `action` (monster action name, e.g. `"Scimitar"`, `"Rend"`, `"Multiattack"`), `two_handed: bool = False`, `offhand: bool = False` (Light/Nick), `use_mastery: bool = True`, `cleave_target_id: str | None = None`, `advantage_override: bool = False`, `disadvantage_override: bool = False`.
   - Resolution pipeline (exact 5e-2024 order):
     1. Validate active turn, not Incapacitated, not Charmed targeting charmer, action/bonus-action/Extra-Attack/Nick economy, reach/range (ranged attack within 5ft of conscious hostile or in long range → Disadvantage).
     2. Gather ALL Advantage & Disadvantage sources (all 15 conditions, Dodge, Help, Vex, Sap, Prone distance, Invisible, overrides). **Strict 5e cancellation:** `if adv_sources and disadv_sources: mode = "normal"`.
     3. Roll d20 + `attack_bonus` + Bless/Bane (`±1d4`) + Exhaustion (`-2 * level`).
     4. Nat-1 auto-miss; Nat-20 auto-hit & Critical Hit; else hit iff `total >= target_effective_ac` (including `shield` `+5`, `shield-of-faith` `+2`, `mage-armor`, `barkskin`, `haste`).
     5. If hit and target has `paralyzed` or `unconscious` and `distance_ft <= 5` → **Automatic Critical Hit**.
     6. **On Miss:** if `use_mastery` and weapon has active `graze` → deal `max(0, attack_ability_mod)` weapon damage (subject to resist/immune/vuln, triggers Concentration check if >0). Consume Sap/Vex/Help flags as applicable.
     7. **On Hit:** roll damage (doubling dice on crit; adding conditional monster damage like Goblin Warrior Advantage `+1d4`), apply Resistance (`floor(d/2)`), Vulnerability (`2*d`), Immunity (`0`), Petrified all-damage resistance; absorb via `temp_hp` first, then `hp`.
     8. Trigger monster rider saves/conditions (e.g. `ghoul` Claw CON DC 10 → `paralyzed`; `wolf` Bite → `prone`).
     9. Trigger active 2024 Weapon Mastery (`cleave`, `push`, `sap`, `slow`, `topple`, `vex`; `nick` handled in step 1 economy).
     10. Trigger target Concentration CON save (`DC = max(10, floor(dmg_taken / 2))`, with Exhaustion/Bless/Bane/auto-fail rules) if target is concentrating and took >0 damage.
     11. Handle 0 HP (monsters `alive = False`, PCs gain `unconscious` + `prone`, drop concentration).
4. **`cast_spell`**:
   - Fields: `actor_id`, `spell` (slug or `spells:<slug>`), `slot_level: int | None` (defaults to `spell.level`), `target_ids: list[str]`, `destination: {"x": int, "y": int} | None` (for `misty-step`), `as_reaction: bool = False` (for `shield` / `counterspell`).
   - Enforces: action/bonus_action/reaction availability (allows `as_reaction=True` off-turn for reaction spells like `shield`!), not Incapacitated, spell slot availability & consumption (cantrips free), Concentration replacement (automatically drops previous concentration & cleans up all its `active_effects` / `conditions`).
   - Resolves attack spells, save spells (with Paralyzed/Petrified/Stunned/Unconscious auto-fail STR/DEX, Restrained/Dodge DEX mods, half-on-save, cantrip & upcast scaling), auto-hit (`magic-missile`, negated by active `shield`), healing (revives 0-HP PC removing `unconscious` while keeping `prone`), temp HP, conditions, buffs, and honest `narrative` fallback (`spell_cast_narrative` event with `coverage` & `unmodeled_clauses`).
5. **`dodge`**, **`dash`**, **`disengage`**, **`help`** (`ally_id`, `target_id`): consume Action, apply standard 2024 effects.
6. **`end_turn`**:
   - Expire `end_of_source_next_turn` / end-of-turn effects owned by ending actor; roll end-of-turn `save_ends` saves if present.
   - Advance `active_index` to next alive combatant (wrap increments `round`).
   - Expire `start_of_source_next_turn` effects owned by new active actor (e.g., `shield`, `dodge`, `sap`, `slow`).
   - Refresh new active actor's `turn_resources` (`action`, `bonus_action`, `reaction`, `movement_remaining_ft`, `cleave_used=False`, `nick_used=False`, `disengaged=False`).
   - If new active actor is a PC at 0 HP (`unconscious`), automatically roll a 2024 Death Saving Throw (nat-20 regains 1 HP & wakes Prone; nat-1 adds 2 failures; >=10 +1 success; <10 +1 failure; 3 successes stabilizes; 3 failures marks dead).

### 5.4 Step Output Schema (`StepResult`)
```json
{
  "status": "ok | invalid_intent | error",
  "error": null,
  "events": [
    {"seq": 1, "type": "attack_roll", "actor_id": "pc-1", "target_id": "goblin-1", "...": "..."}
  ],
  "state_diff": {
    "hp_changes": [{"id": "goblin-1", "before": 10, "after": 2, "delta": -8}],
    "condition_changes": [{"id": "goblin-1", "added": ["prone"], "removed": []}],
    "position_changes": [],
    "slot_changes": [],
    "effect_changes": [],
    "turn_changed": false
  },
  "state": { "...": "full updated EncounterState" },
  "legal_actions": {
    "active_actor_id": "pc-1",
    "movement_remaining_ft": 30,
    "actions": ["attack", "dodge", "dash", "disengage", "help"],
    "bonus_actions": [],
    "weapons": ["longsword", "greatsword", "javelin"],
    "spells": [],
    "can_end_turn": true
  },
  "narrative_hooks": [
    "Aldric Vance hits Goblin A with Longsword (19 vs AC 15) for 8 slashing damage.",
    "Sap mastery leaves Goblin A with Disadvantage on its next attack roll."
  ]
}
```

---

## 6. Additive Agent Surfaces (`surface-v2`)

### 6.1 Rules Path Resolution (CLI & MCP)
1. `--rules <path>` flag
2. `SRD2024_RULES` environment variable
3. Default `<repo_root>/dist/rules.json`

### 6.2 Three Additive Operations (CLI + MCP Parity)

| # | MCP Tool | CLI Subcommand (`bin/srd`) | Purpose |
|---|---|---|---|
| 6 | `srd_rule` | `srd rule <id> [--coverage] [--format markdown\|json]` | Fetch compiled typed Rules IR for an entity (or global `rules-coverage` summary with `--coverage` / `id="coverage"`). |
| 7 | `srd_compile_character` | `srd character (<spec.json> \| --spec <json>) [--format markdown\|json]` | Validate & compile a 2024 CharacterBuildSpec JSON into a full derived statblock + errors/warnings. |
| 8 | `srd_encounter` | `srd encounter (--intent <json> \| <payload.json>) [--state <state.json>] [--out-state <state.json>] [--format markdown\|json]` | Advance a 5e-2024 encounter deterministically (`start_encounter`, `move`, `attack`, `cast_spell`, `dodge`, `dash`, `disengage`, `help`, `end_turn`). |

#### MCP Tool Schemas & Session Support (`src/srd2024/runtime/mcp.py`)
- **`srd_rule`**: `{id?: str, coverage?: bool = false, format?: "markdown"|"json" = "markdown"}`.
- **`srd_compile_character`**: `{spec: object, format?: "markdown"|"json" = "markdown"}`.
- **`srd_encounter`**: `{intent: object, state?: object, session_id?: str, roll_overrides?: array[int], format?: "markdown"|"json" = "markdown"}`.
  - **Stateless mode:** pass `state` (omitted for `start_encounter`), get updated `state` back.
  - **Optional MCP session mode:** pass `session_id` (without resending `state`); `SRDMCPServer` caches the latest `EncounterState` in memory keyed by `session_id` so multi-turn agent combat is token-lean while remaining 100% deterministic.
- **MCP Resources (additive):**
  - `srd://rules/coverage` (`application/json`) — contents of `coverage_summary` from `rules.json`.
  - URI template `srd://rule/{type}/{slug}` (`application/json`) — single compiled Rules IR object.
- **Stage 1 Backwards-Compatibility Lock:**
  - `bin/srd-mcp` (default, no `--engine` and `SRD2024_ENGINE` unset): `tools/list` returns the exact 5 Stage 1 tools (`srd_search`, `srd_get`, `srd_list`, `srd_graph`, `srd_validate`) so `tests/test_cli_and_mcp.py:127` passes 100% unmodified; `tools/call` accepts all 8 tools.
  - `bin/srd-mcp --engine` (or `SRD2024_ENGINE=1`) AND `bin/srd-engine-mcp` (`chmod +x`, invoking `srd2024.runtime.mcp:main_engine`): `tools/list` advertises all **8 tools** (5 Stage 1 + 3 Stage 2).

### 6.3 Skill & README Updates
- `skill/SKILL.md`: Preserve YAML frontmatter (`name: srd-2024`) and all Stage 1 sections intact; append a concise **Stage 2 Neuro-Symbolic Playbook** section teaching agents: (1) inspect `srd_rule` / coverage before assuming a spell is automated, (2) validate/compile PCs via `srd_compile_character` instead of hand-calculating AC/HP/saves, (3) resolve combat strictly via `srd_encounter` and narrate from `events`/`narrative_hooks`, and (4) use `bin/srd-engine-mcp` or `bin/srd-mcp --engine` for 8-tool MCP discovery.
- `README.md`: Preserve all Stage 1 sections and CC-BY-4.0 attribution; add Stage 2 Quickstart (`bin/srd-rules-build`, `bin/srd rule`, `bin/srd character`, `bin/srd encounter`, `bin/srd-engine-mcp`) and neuro-symbolic architecture diagram.

---

## 7. Concrete Verification Acceptance Criteria (`rules-verifier`)

`rules-verifier` writes 4 independent `unittest` suites runnable alongside Stage 1 via `PYTHONPATH=src python3 -m unittest discover -s tests -v`. **All 35 Stage 1 tests MUST pass with zero modifications**, plus:

### 7.1 `tests/test_stage2_rules_compiler.py`
1. **Build & Artifacts:** `bin/srd-rules-build` succeeds (exit 0), creating `dist/rules.json` and `dist/web/rules-coverage.json` with valid JSON, `schema_version == "2"`, matching `source_hash` from Stage 1 `manifest.json`, and CC-BY-4.0 attribution.
2. **Determinism:** Rebuilding via `bin/srd-rules-build --out-dir <tmp>` produces byte-identical `rules.json` and `web/rules-coverage.json`.
3. **Entity Completeness & Honest Coverage Floors:**
   - `weapon_masteries`: `8/8` (`full == 8`).
   - `conditions`: `15/15` (`full == 15`), including 2024 `exhaustion` (`-2` d20 penalty, `-5` ft speed, death at 6).
   - `equipment`: `182` total, `38` weapons (`full`, including `morningstar` reach 5 ft), `13` armor/shields (`full`, including `hide-armor` `max_bonus == 2` and `shield` `+2`).
   - `spells`: `339` total, `full >= 80`, `full + partial >= 200`, `narrative >= 50` (honesty check — e.g. `augury`, `clairvoyance`, `wish` MUST NOT be marked `full`), 9 `"Component:"` range quirks cleanly parsed (`divine-smite`, `guidance`, `sorcerous-burst`, etc.).
   - `monsters`: `341` total, `full >= 115` (honest trait/bonus-action/reaction grading, #39), `narrative == 0`, `shrieker-fungus` & `vampire-mist` clean `partial`, `adult-red-dragon` Multiattack + Fire Breath DC 21 DEX half + Rend parsed, `goblin-warrior` Scimitar Advantage `1d4` conditional parsed, `ghoul` Claw CON DC 10 `paralyzed` parsed.
   - `character_creation`: 12 classes, 12 subclasses, 9 species, 24 subspecies, 4 backgrounds, 17 feats, 287 levels, 68 traits.

### 7.2 `tests/test_stage2_character_engine.py`
1. **Valid Martial PC (Level 5 Human Fighter / Champion / Soldier / Point Buy):**
   - Validates `valid == True`, `errors == []`, `PB == 3`, `HP == 44` (or exact formula check), `AC == 19` (Chain Mail 16 + Shield 2 + Defense 1), `initiative == DEX + 3` (Alert), `extra_attacks == 2`, weapon profiles (`Longsword` to-hit `+7`, `1d8+4`, Sap active; `Greatsword` Graze active), 4 weapon masteries legal.
2. **Valid Caster PC (Level 5 High Elf Wizard / Evoker / Sage / Standard Array):**
   - Validates `valid == True`, `spell_save_dc == 8 + 3 + INT_mod`, `spell_attack_bonus == 3 + INT_mod`, slots `{"1": 4, "2": 3, "3": 2}`, `fireball` + `shield` + `magic-missile` + `fire-bolt` legal.
3. **Valid Unarmored & Species-Trait PCs:**
   - Level 1 Goliath Barbarian (`speed == 35`, Unarmored Defense `10 + DEX + CON`, 2 masteries).
   - Level 3 Wood Elf Monk (`speed == 35 + unarmored_movement`, Unarmored Defense `10 + DEX + WIS`).
   - Level 1 Dwarf Fighter (`dwarven-toughness` adds `+level` HP, `poison` damage resistance).
4. **Negative Validation Tests (all must return `valid == False` with clear error codes):**
   - Background ASI using disallowed ability (Soldier picking `int +2`).
   - Point-buy exceeding 27 points (`all 15s`) or invalid standard array.
   - Level 2 character selecting a subclass; Level 3 character omitting subclass; mismatched subclass (`fighter` + `evoker`).
   - Level 1 character picking General Feat `grappler` (`minimum_level: 4` violation) or non-repeatable feat twice.
   - Wizard preparing Cleric-only spell (`cure-wounds`) or 3rd-level spell (`fireball`) at Level 1.
   - Exceeding class weapon mastery allowance (Level 1 Barbarian picking 4 masteries).
   - Heavy armor STR minimum warning (`plate-armor` with STR 10 emits warning + `-10` ft speed).

### 7.3 `tests/test_stage2_combat_engine.py`
1. **Determinism & Immutability:** Same seed + same intents produce byte-identical final states and event logs; calling `step()` does not mutate the input `state` dict.
2. **Core Attack Math:** Advantage/Disadvantage cancellation (2 adv + 1 disadv → normal roll), Nat-1 auto-miss, Nat-20 crit doubles damage dice only (not flat modifier), Damage Resistance (`floor(d/2)`), Immunity (`0`), Vulnerability (`2*d`).
3. **All 8 Weapon Masteries (each tested explicitly with `roll_overrides`):**
   - `cleave` (melee hit triggers 2nd attack vs adjacent target within 5ft without positive mod; blocked 2nd time same turn).
   - `graze` (miss still deals ability mod damage of weapon's type).
   - `nick` (Light off-hand attack executes as part of Attack action, leaving Bonus Action available; once per turn).
   - `push` (hit pushes Medium/Large target 10ft straight away; Huge target not pushed).
   - `sap` (hit gives target Disadvantage on its next attack roll before start of attacker's next turn).
   - `slow` (hit dealing damage reduces target speed by 10ft, non-stacking on 2nd hit).
   - `topple` (hit forces CON save `DC 8 + mod + PB`; failure inflicts `prone`).
   - `vex` (hit dealing damage grants attacker Advantage on next attack vs same target before end of next turn).
4. **Conditions & 2024 Rules:**
   - **Exhaustion (2024):** level 2 applies `-4` to attack rolls & saves and `-10` ft speed; level 6 kills combatant.
   - **Paralyzed / Unconscious:** auto-fail STR & DEX saves; hits within 5ft auto-crit; Incapacitated breaks Concentration & blocks actions.
   - **Prone:** melee attack within 5ft has Advantage; ranged attack beyond 5ft has Disadvantage; `move(stand_up=True)` spends `floor(speed/2)` ft and removes `prone`.
   - **Petrified:** Resistance to all damage + immune to `poisoned`.
   - **Grappled / Restrained / Blinded / Invisible / Poisoned / Frightened / Stunned / Charmed:** verified via attack/save/movement checks.
5. **Spells, Concentration & 0 HP:**
   - `fireball` (3rd-level `8d6`, upcast 4th-level `9d6`, DEX save half, fire resistance halves, Concentration check `max(10, floor(dmg/2))`).
   - `shield` (reaction `+5 AC` blocks triggering attack & negates `magic-missile`; expires at start of caster's next turn).
   - `bless` (`+1d4` on attack/save; casting `hold-person` drops `bless` concentration and cleans up `bless` effects).
   - `cure-wounds` / `healing-word` revives 0-HP PC (removes `unconscious`, leaves `prone` per 2024 rules!).
   - `fire-bolt` cantrip scaling (`1d10` at L1 → `2d10` at L5).
   - `narrative` spell (e.g. `augury` or `prestidigitation`) consumes slot/action and emits honest `spell_cast_narrative` event without faking mechanics.
6. **Monsters & Turn Economy:**
   - `monsters:goblin-warrior` Scimitar (conditional `+1d4` only when roll had Advantage), `monsters:ghoul` Claw (CON DC 10 → `paralyzed`), `monsters:adult-red-dragon` Fire Breath (DC 21 DEX half, 17d6 Fire).
   - `dodge`, `dash`, `disengage`, `help`, `end_turn` effect expiry and round increment, plus PC death save roll at 0 HP.

### 7.4 `tests/test_stage2_surfaces.py`
1. **CLI Stage 2 Subcommands:** `bin/srd rule spells:fireball`, `bin/srd rule --coverage`, `bin/srd character <spec.json>`, and `bin/srd encounter` run cleanly (exit 0) in both `markdown` and `json` formats; `--rules` flag and `SRD2024_RULES` env var override work.
2. **MCP Stage 2 Discovery, Parity & Sessions:**
   - Default `bin/srd-mcp` `tools/list` still returns the exact 5 Stage 1 tools (Stage 1 test lock intact), while `tools/call` dispatches `srd_rule`, `srd_compile_character`, `srd_encounter`.
   - `bin/srd-mcp --engine` AND `bin/srd-engine-mcp` advertise all **8 tools** with valid JSON Schemas + `srd://rules/coverage` resource.
   - Multi-turn `srd_encounter` works both statelessly (`state` passed round-trip) and via `session_id`, matching CLI JSON output 1:1.
3. **Boundary Hygiene:** `src/srd2024/runtime/**` and `src/srd2024/engine/**` NEVER import `srd2024.compiler` or `srd2024.rules_compiler`.
4. **Docs & Skill:** `skill/SKILL.md` and `README.md` document Stage 2 tools while preserving all Stage 1 checks and CC-BY-4.0 attribution.
