# 5e 2024 SRD (v5.2) Schema & Environment Audit

**Upstream source:** `https://github.com/5e-bits/5e-srd-api` (`packages/5e-database/src/2024/en/`)
**Snapshot:** commit `8a339e73ed5bba9ffec2ad6be9e76b5afa9c1b24` (2026-11-23, `5e-database` `v7.10.0`)
**Local raw mirror:** `data/raw/2024/en/` + `data/raw/{LICENSE.md,UPSTREAM-README.md,ATTRIBUTION.md,PROVENANCE.json}`
**Licensing:** Underlying SRD 5.2 content is **CC-BY-4.0** (Wizards of the Coast LLC); upstream JSON packaging is **MIT**.

---

## 1. Local Runtime & Stack Audit

| Tool / Library | Status | Details |
|---|---|---|
| `python3` | **Available** | `/usr/bin/python3` — **Python 3.14.8** |
| Python `sqlite3` | **Available** | **SQLite 3.54.0**, **FTS5 (`bm25`) enabled**, built-in **JSON functions** (`json_extract`, etc.) verified |
| Python packages | Minimal | stdlib (`json`, `sqlite3`, `argparse`, `unittest`, `pathlib`, `venv`) + `PyYAML 6.0.3`; **no** `mcp`, `fastmcp`, `pydantic`, `pytest`, `click`, `typer` |
| `pip` / `pip3` / `uv` | **NOT installed** | No Python package manager on machine |
| `sqlite3` CLI | **Available** | `/usr/bin/sqlite3` (3.54.0) |
| `node` | **Available** | `/usr/bin/node` — **Node v26.12.0** with built-in `node:sqlite` (`DatabaseSync`, FTS5 + JSON verified) |
| `npm` / `npx` / `bun` | **NOT installed** | No JS package manager on machine |
| `git` / `jq` / `gcc` / `make` | **Available** | `git 2.56.0`, `jq 1.8.2` |

### Runtime Recommendation

**Use zero-dependency Python 3.14 stdlib** (`sqlite3`, `json`, `argparse`, `unittest`, `pathlib`, `sys.stdin`/`sys.stdout`).
- Zero install friction, works 100% offline/sandboxed with no `pip`/`uv`/`npm` needed.
- Native SQLite 3.54.0 with FTS5 (`bm25`) and JSON functions out of the box.
- Stdio MCP server is ~150 lines of clean JSON-RPC 2.0 over stdin/stdout (no SDK bloat, deterministic framing, easy to test via `unittest` + subprocess).
- Avoid third-party YAML deps for card frontmatter (emitting deterministic YAML frontmatter in stdlib is safer and zero-dep, though `PyYAML 6.0.3` is available for test verification).

---

## 2. Dataset Inventory (25 Files, 2,029 Entities, 3.07 MiB)

All 25 files are top-level JSON arrays of objects. Every single entity has a unique `url` (2,029 unique URLs).

| Resource Type (`type`) | Raw File (`5e-SRD-*.json`) | Entities | Bytes | URL Prefix | Text Field |
|---|---|---:|---:|---|---|
| `ability-scores` | `Ability-Scores.json` | 6 | 3,321 | `/api/2024/ability-scores` | `description` (6/6, str) |
| `alignments` | `Alignments.json` | 10 | 3,607 | `/api/2024/alignments` | `description` (10/10, str) |
| `backgrounds` | `Backgrounds.json` | 4 | 15,611 | `/api/2024/backgrounds` | *(none)* |
| `classes` | `Classes.json` | 12 | 190,941 | `/api/2024/classes` | *(none)* |
| `conditions` | `Conditions.json` | 15 | 8,050 | `/api/2024/conditions` | `description` (15/15, str) |
| `damage-types` | `Damage-Types.json` | 13 | 1,892 | `/api/2024/damage-types` | `description` (13/13, str) |
| `equipment-categories` | `Equipment-Categories.json` | 30 | 80,683 | `/api/2024/equipment-categories` | *(none)* |
| `equipment` | `Equipment.json` | 182 | 192,438 | `/api/2024/equipment` | `description` (81/182, str) |
| `feats` | `Feats.json` | 17 | 9,785 | `/api/2024/feats` | `description` (17/17, str) |
| `features` | `Features.json` | 232 | 184,942 | `/api/2024/features` | `description` (232/232, str) |
| `languages` | `Languages.json` | 19 | 2,797 | `/api/2024/languages` | `note` (19/19; 18 empty) |
| `levels` | `Levels.json` | 287 | 190,898 | `/api/2024/{classes,subclasses}/*/levels` | *(none)* |
| `magic-items` | `Magic-Items.json` | 277 | 324,166 | `/api/2024/magic-items` | **`desc` (277/277, `list[str]`)** |
| `magic-schools` | `Magic-Schools.json` | 8 | 1,319 | `/api/2024/magic-schools` | `description` (8/8, str) |
| `monsters` | `Monsters.json` | 341 | 1,343,339 | `/api/2024/monsters` | nested `*.desc` (str) |
| `poisons` | `Poisons.json` | 14 | 6,137 | `/api/2024/poisons` | `description` (14/14, str) |
| `proficiencies` | `Proficiencies.json` | 74 | 33,894 | `/api/2024/proficiencies` | *(none)* |
| `skills` | `Skills.json` | 18 | 5,439 | `/api/2024/skills` | `description` (18/18, str) |
| `species` | `Species.json` | 9 | 9,153 | `/api/2024/species` | *(none)* |
| `spells` | `Spells.json` | 339 | 495,225 | `/api/2024/spells` | `description` (339/339, str) |
| `subclasses` | `Subclasses.json` | 12 | 32,939 | `/api/2024/subclasses` | `description` + `summary` (12/12, str) |
| `subspecies` | `Subspecies.json` | 24 | 16,292 | `/api/2024/subspecies` | *(none)* |
| `traits` | `Traits.json` | 68 | 54,574 | `/api/2024/traits` | `description` (68/68, str) |
| `weapon-mastery-properties` | `Weapon-Mastery-Properties.json` | 8 | 2,811 | `/api/2024/weapon-mastery-properties` | `description` (8/8, str) |
| `weapon-properties` | `Weapon-Properties.json` | 10 | 3,874 | `/api/2024/weapon-properties` | `description` (10/10, str) |
| **TOTAL** | **25 files** | **2,029** | **3,214,127** | **2,029 unique URLs** | |

### Completeness & 2014→2024 Delta

This upstream snapshot is a **complete SRD 5.2 (2024) release** (zero stubs, zero `null` fields, zero `TODO`/`FIXME`/`TBD` strings):
- **All 12 core classes** (`barbarian`–`wizard`) + **12 subclasses** (1 per class) + **240 class levels** (12×20) + **47 subclass levels** (3–5 per subclass) + **232 features**.
- **All 9 species** (`dragonborn`, `dwarf`, `elf`, `gnome`, `goliath`, `halfling`, `human`, `orc`, `tiefling`) + **24 subspecies** + **68 traits**.
- **All 4 SRD 5.2 backgrounds** (`acolyte`, `criminal`, `sage`, `soldier`) + **17 feats** (4 origin, 2 general, 4 fighting-style, 7 epic-boon).
- **339 spells** (levels 0–9: 27/57/57/42/34/38/31/20/17/16; 0 empty descriptions; 29 ritual; 132 concentration).
- **341 monsters** (CR 0–30 across 14 creature types + 2 swarm types; 2 monsters legitimately lack `actions`: `shrieker-fungus`, `vampire-mist`).
- **277 magic items** (19 marked `variant=true`, 6 with non-empty `variants` lists), **182 equipment** (38 with 2024 weapon `mastery`, 36 with `utilize`, 20 with `craft`), **14 poisons**, **15 conditions**, **13 damage types**, **8 weapon mastery properties**, **10 weapon properties**.
- **Delta vs 2014:** 2024 adds `Poisons`, `Species`, `Subspecies`, `Weapon-Mastery-Properties` (replacing `Races`/`Subraces`). **Not present in 2024 upstream:** `Rules` (the 2024 Rules Glossary is not yet a separate JSON collection in `5e-database` v7.10.0).

---

## 3. Common Fields & Entity-Specific Structures

### Universal Fields (100% of entities)
- `index` (`str`), `name` (`str`), `url` (`str`) are present on **2,029 / 2,029** entities.
- **`updated_at` is absent (0 / 2,029)** in all 2024 raw JSON files (do not expect it in raw input).
- **`index` is NOT globally unique — 53 collisions across resource types**, e.g.:
  - `druid` (`classes` & `monsters`), `acid` (`damage-types` & `equipment`), `ammunition` (`equipment-categories`, `magic-items`, `weapon-properties`), `light` / `shield` / `magic-weapon` / etc. (28 `equipment`↔`proficiencies`, 8 `subspecies`↔`traits`, 6 `equipment-categories`↔`proficiencies`).
  - **Contract implication:** Canonical entity ID **must** be `type:index` (e.g., `spells:fireball`, `levels:barbarian-1`) and/or `url` (`/api/2024/...`), never bare `index`.

### Per-Type Key Schemas (`REQ` = 100% present; `OPT` = partial)

- **Simple reference types (`index`, `name`, `url`, `description`):** `conditions`, `damage-types`, `magic-schools`, `weapon-mastery-properties`, `weapon-properties`.
- **`ability-scores`:** + `full_name`, `description`, `skills[]`.
- **`alignments`:** + `abbreviation`, `description`.
- **`languages`:** + `is_rare` (bool), `note` (str — empty `""` on 18/19).
- **`poisons`:** + `type` (str), `cost` (int), `description`.
- **`skills`:** + `ability_score` (ref), `description`.
- **`proficiencies`:** + `type` (str), `reference` (ref → `equipment`/`skills`/`equipment-categories`/`ability-scores`), `classes[]`, `backgrounds[]`.
- **`equipment-categories`:** + `equipment[]` (refs → `equipment` and `magic-items`).
- **`backgrounds`:** + `ability_scores[]`, `feat` (ref w/ optional `note`), `proficiencies[]`, `equipment_options[]`; OPT `proficiency_choices[]` (1/4).
- **`classes`:** + `hit_die` (int), `class_levels` (endpoint str), `primary_ability` (dict), `saving_throws[]`, `proficiencies[]`, `proficiency_choices[]`, `starting_equipment_options[]`, `multi_classing` (dict), `subclasses[]`; OPT `spellcasting` (8/12, dict), `spells` (8/12, endpoint str).
- **`subclasses`:** + `class` (ref), `summary` (str), `description` (str), `features[]` (list of **inline** `{name, level, description}` dicts — no `url`).
- **`levels` (287):** + `level` (int), `class` (ref), `features[]` (refs); OPT `prof_bonus` (240/287, class levels only), `class_specific` (220/287), `spellcasting` (160/287), `subclass` (47/287, subclass levels only).
- **`features` (232):** + `class` (ref), `level` (ref → `levels`), `description`; OPT `subclass` (58/232, ref).
- **`species` (9):** + `type` (str), `speed` (int), `traits[]` (refs); OPT `size` (8/9), `size_options` (1/9), `subspecies[]` (5/9).
- **`subspecies` (24):** + `species` (ref), `traits[]` (refs w/ `level`); OPT `damage_type` (10/24, Dragonborn ancestries).
- **`traits` (68):** + `description`, `species[]`; OPT `subspecies[]` (40/68), `spells[]` (21/68, `{level, spell}`), `proficiency_choices` (2/68), `speed` (1/68).
- **`feats` (17):** + `type` (`origin`|`general`|`fighting-style`|`epic-boon`), `description`; OPT `prerequisites` (13/17, dict), `prerequisite_options` (1/17, choice dict), `repeatable` (3/17, str).
- **`equipment` (182):** + `cost` (`{quantity, unit}`), `equipment_categories[]`; OPT `weight` (181/182; only `entertainers-pack` omits `weight`), `description` (81/182), `damage` (38), `mastery` (38), `properties[]` (38), `ability` (37), `range` (37), `utilize[]` (36), `craft[]` (20), `armor_class` (13), `stealth_disadvantage` (13), `str_minimum` (13), `don_time`/`doff_time` (12), `ammunition` (9), `contents[]` (7), `throw_range` (7), `two_handed_damage` (7), `quantity`/`storage` (5), `image` (2), `notes` (1).
- **`magic-items` (277):** + `desc` (**`list[str]`**), `equipment_category` (ref), `rarity` (`{name}`), `attunement` (bool), `variant` (bool), `variants[]` (refs), `image` (str); OPT **`limited-to`** (24/277, str — **hyphenated key**).
- **`spells` (339):** + `level` (int 0–9), `school` (ref), `classes[]` (refs, always non-empty), `casting_time`, `range`, `components[]`, `duration`, `concentration` (bool), `ritual` (bool), `description`; OPT `material` (189/339), `higher_level` (103/339), `damage` (89/339, `{damage_type, damage_at_slot_level}`), `attack_type` (21/339).
- **`monsters` (341):** + `size`, `type`, `alignment`, `armor_class[]`, `hit_points`, `hit_dice`, `hit_points_roll`, `speed` (dict), 6 ability ints (`strength`..`charisma`), `proficiencies[]`, `damage_vulnerabilities[]` (`list[str]`), `damage_resistances[]` (`list[str]`), `damage_immunities[]` (`list[str]`), `condition_immunities[]` (refs), `senses` (dict), `languages` (str), `challenge_rating` (int|float), `proficiency_bonus` (int), `xp` (int), `image` (str); OPT `actions[]` (340/341), `special_abilities[]` (329), `legendary_actions[]` (326), `bonus_actions[]` (82), `gear` (55, str), `xp_in_lair` (29), `reactions[]` (24), `forms[]` (18, refs to other monsters). Nested action blocks share `{name, desc}` + optional `attack_bonus`, `damage[]`, `dc`, `usage`, `spellcasting`, `multiattack_type`, `actions[]`, `action_options`.

---

## 4. Cross-Reference Graph Audit

### Resolution & Hygiene
- **Nested `dict.url` references:** **7,780** raw occurrences → **7,614** unique `(src, field_path, dst)` edges → **7,396** unique `(src, dst)` pairs.
- **Broken / dangling entity references:** **0 / 7,780 (100.0% resolve cleanly to one of the 2,029 entities)**.
- **Index-only references (missing `url`):** **0**.
- **Self-references:** **0**.
- **Stray `/api/2014/...` URLs:** **0**.
- **Non-entity `/api/...` bare strings (639 total — do NOT create graph edges from these):**
  - 619 image asset strings: `monsters.image` (341), `magic-items.image` (276), `equipment.image` (2), all `/api/images/...` (plus 1 corrupted string in `armor-2`, see §5).
  - 20 REST collection endpoint strings in `classes`: `class_levels` (12 × `/api/2024/classes/*/levels`), `spells` (8 × `/api/2024/classes/*/spells`).

### Nested Reference Shapes (all 7,780)
- `7,342` standard `{"index", "name", "url"}`.
- `438` carry extra inline edge metadata that the graph/card compiler should preserve or inspect:
  - `379` monster spellcasting refs (`{index, name, url, level, [usage], [notes]}`) across `actions` (315), `bonus_actions` (33), `special_abilities` (18), `legendary_actions` (7), `reactions` (6).
  - `53` `subspecies.traits[]` (`{index, name, url, level}`).
  - `6` `{index, name, url, note}` (`backgrounds.feat`: 2, `backgrounds.equipment_options...of`: 2, `monsters.condition_immunities[]`: 2).

### Graph Topology & Key Edge Families
- **Nodes:** 2,029 total | `out_deg = 0`: 119 | `in_deg = 0`: 786 | **isolated (degree 0): 63** (`languages` 19, `poisons` 14, `feats` 13, `alignments` 10, `monsters` 5, `conditions` 1, `weapon-properties` 1).
- **Highest out-degree:** `equipment-categories:wondrous-items` (133), `equipment-categories:adventuring-gear` (86), `classes:bard` (70), `classes:monk` (53), `classes:rogue` (45), `classes:ranger` (43), `equipment:smiths-tools` (41), `monsters:lich` (39).
- **Highest in-degree:** `classes:wizard` (261), `damage-types:piercing` (221), `proficiencies:skill-perception` (201), `classes:sorcerer` (183), `classes:bard` (174), `classes:druid` (173), `classes:cleric` (155), `damage-types:slashing` (150).
- **Major edge clusters (72 distinct `(src_type, json_path, dst_type)` families):**
  - `monsters` → `proficiencies` (726), `damage-types` (734 across actions/abilities/etc.), `spells` (379), `conditions` (371), `ability-scores` (330), `monsters.forms` (36), `equipment.armor` (34).
  - `spells` → `classes` (879), `magic-schools` (339), `damage-types` (89).
  - `equipment` ↔ `equipment-categories` (364 + 360), `equipment` → `craft` (148), `weapon-properties` (70), `contents` (65), `utilize.dc` (57), `damage-types` (45), `weapon-mastery-properties` (38), `ability-scores` (37), `ammunition` (9), `storage` (5).
  - `magic-items` ↔ `equipment-categories` (277 + 203), `variants` (19).
  - `classes` / `subclasses` / `levels` / `features`: `levels→features` (316), `levels→classes` (287), `levels→subclasses` (47), `features→classes` (232), `features→levels` (232), `features→subclasses` (58), `classes→proficiencies/equipment/ability-scores/subclasses` (415).
  - `species` / `subspecies` / `traits`: bidirectional `species↔traits` (31/73), `subspecies↔traits` (53/50), `species↔subspecies` (24/24), `traits→spells` (21), `traits→skills` (21), `subspecies→damage-types` (10).
  - `backgrounds` → `equipment` (20), `ability-scores` (12), `proficiencies` (15), `feats` (4), `equipment-categories` (2).
  - `proficiencies` → `classes` (74), `reference` (74: 44 equipment, 18 skills, 6 eq-cats, 6 abilities), `backgrounds` (14).
  - `ability-scores` ↔ `skills` (18/18); `feats` → `ability-scores` (2).

**Bundle-engine design tip:** Because high-fan-out hub nodes (`equipment-categories`, `classes`, `proficiencies`) have 40–133 outgoing edges and up to 261 incoming edges, depth-1 bundle resolution should follow **outgoing edges by default**, cap/summarize high-fan-out lists (e.g., `equipment-categories.equipment[]` or `classes.proficiency_choices`), and prioritize **rules-defining leaf refs** (`conditions`, `weapon-mastery-properties`, `weapon-properties`, `damage-types`, `magic-schools`, `skills`, `ability-scores`, `feats`, `traits`, `spells`).

---

## 5. Notable Quirks & Edge Cases for Compiler / Spec

1. **`desc` vs `description` split:** Only `magic-items` uses `desc: list[str]` (paragraphs + table rows as array elements); 15 types use `description: str`; `monsters` uses `desc: str` inside nested action/ability arrays; 8 structural types have no top-level text field.
2. **Markdown & tables inside text:** `magic-items.desc` contains **44 Markdown pipe tables**, 30 emphasis blocks, 1 heading. `conditions` (15/15), `feats` (10/17), `spells` (235/339), `features` (120/232), `traits` (12/68), `equipment` (9/182) contain `\n` and/or `*`/`**` emphasis. Compiler should preserve Markdown cleanly when rendering cards and strip/normalize markup for FTS5 indexing if desired.
3. **`Levels` nested URLs & subclass slug mismatch:** `levels` URLs are `/api/2024/classes/<c>/levels/<n>` and `/api/2024/subclasses/<s>/levels/<n>`. **Gotcha:** the subclass URL segment does NOT always match `subclass.index` (e.g., URL `/api/2024/subclasses/berserker/levels/3` references `subclass.index = "path-of-the-berserker"`). Always derive resource type from source file (or pattern match `/levels/\d+$`) and follow `subclass.url` rather than parsing the level URL slug.
4. **Upstream corrupted `image` string in `magic-items`:** `armor-2` has `"image": "/api +2/images/magic-items/armor.png"` (only non-`/api/...` image value in the corpus). Treat `image` strictly as opaque metadata, never as a graph edge.
5. **`Subclasses.features` inline duplication:** `5e-SRD-Subclasses.json` embeds `{name, level, description}` inline without `url`, while the same 58 features also exist as standalone records in `5e-SRD-Features.json` (with `subclass.url` pointing back).
6. **Monster damage traits are plain strings, not refs:** `damage_immunities`, `damage_resistances`, `damage_vulnerabilities` are `list[str]` (12 standard damage-type names + 2 prose strings, e.g., `"piercing damage from weapons wielded by creatures under the effect of a bless spell"`), unlike `condition_immunities` which are proper `dict.url` refs.
7. **Choice objects (`choose` / `from` / `option_set_type`):** 141 choice objects across `monsters` (95 `action_options`), `classes` (36), `backgrounds` (6), `traits` (2), `feats` (1), `species` (1) — can nest up to 3 levels deep in `classes.starting_equipment_options` (`options_array` → `choice` → `equipment_category`). Recursive dict-walk handles all embedded `dict.url` refs uniformly.
8. **Hyphenated key & sparse fields:** `magic-items` has optional key `limited-to` (24/277); `equipment` omits `weight` on 1 item (`entertainers-pack`); `languages.note` is `""` on 18/19 items; `magic-items.rarity.name` has 13 distinct string values (including composite strings like `"Uncommon (+1), Rare (+2), or Very Rare (+3)"`).
9. **Unicode punctuation:** 985 typographic characters across the corpus (`’` 529, `–` 179, `•` 45, `—` 24, `−` 13, `×` 7, `“`/`”` 8) — ensure UTF-8 everywhere and note that FTS5 `unicode61` handles these cleanly (consider normalizing curly apostrophes `’` → `'` in search indexing/queries so `Dragon's` matches `Dragon’s`).
