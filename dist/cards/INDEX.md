# 5e 2024 (SRD 5.2) Compiled Cards Index

Compiled offline catalog of **2,029** entities across **25** resource types.

## Progressive Disclosure Guide
1. Start here or with `srd list` / `srd search` to locate canonical `<type>:<slug>` IDs without loading large rule blocks.
2. Open `dist/cards/<type>/<slug>.md` for a single entity, or call `srd get <type>:<slug> --depth 1` for a token-budgeted resolved bundle.
3. Follow relative `[Name](../<type>/<slug>.md)` cross-reference links or inspect `refs:` in YAML frontmatter to resolve mechanical dependencies.

## Resource Catalog

| Type | Count | Card Path | Filterable Facets |
|---|---:|---|---|
| `ability-scores` | 6 | `ability-scores/<slug>.md` | `skill` |
| `alignments` | 10 | `alignments/<slug>.md` | `abbreviation` |
| `backgrounds` | 4 | `backgrounds/<slug>.md` | `ability`, `feat`, `proficiency` |
| `classes` | 12 | `classes/<slug>.md` | `hit_die`, `saving_throw`, `spellcaster`, `subclass` |
| `conditions` | 15 | `conditions/<slug>.md` | *(none)* |
| `damage-types` | 13 | `damage-types/<slug>.md` | *(none)* |
| `equipment` | 182 | `equipment/<slug>.md` | `ability`, `category`, `cost_unit`, `damage_type`, `mastery`, `property` |
| `equipment-categories` | 30 | `equipment-categories/<slug>.md` | *(none)* |
| `feats` | 17 | `feats/<slug>.md` | `feat_type`, `has_prerequisite`, `repeatable` |
| `features` | 232 | `features/<slug>.md` | `class`, `level`, `subclass` |
| `languages` | 19 | `languages/<slug>.md` | `is_rare` |
| `levels` | 287 | `levels/<slug>.md` | `class`, `level`, `subclass` |
| `magic-items` | 277 | `magic-items/<slug>.md` | `attunement`, `category`, `limited_to`, `rarity`, `variant` |
| `magic-schools` | 8 | `magic-schools/<slug>.md` | *(none)* |
| `monsters` | 341 | `monsters/<slug>.md` | `alignment`, `condition_immunity`, `cr`, `creature_type`, `size` |
| `poisons` | 14 | `poisons/<slug>.md` | `cost`, `poison_type` |
| `proficiencies` | 74 | `proficiencies/<slug>.md` | `background`, `class`, `proficiency_type` |
| `skills` | 18 | `skills/<slug>.md` | `ability` |
| `species` | 9 | `species/<slug>.md` | `creature_type`, `has_subspecies`, `size`, `speed` |
| `spells` | 339 | `spells/<slug>.md` | `attack_type`, `class`, `concentration`, `damage_type`, `level`, `ritual`, `school` |
| `subclasses` | 12 | `subclasses/<slug>.md` | `class` |
| `subspecies` | 24 | `subspecies/<slug>.md` | `damage_type`, `species` |
| `traits` | 68 | `traits/<slug>.md` | `species`, `subspecies` |
| `weapon-mastery-properties` | 8 | `weapon-mastery-properties/<slug>.md` | *(none)* |
| `weapon-properties` | 10 | `weapon-properties/<slug>.md` | *(none)* |

## Attribution (CC-BY-4.0)

This work includes material from the System Reference Document 5.2 ("SRD 5.2") by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.
