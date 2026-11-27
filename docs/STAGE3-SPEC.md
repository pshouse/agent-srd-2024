# Stage 3: Multi-Agent 5e 2024 (SRD 5.2) Tabletop & Model Eval Arena — Architecture & Contract Specification

> **Status:** FINAL (empirically verified against Stage 1 `dist/srd2024.sqlite`, Stage 2 `dist/rules.json` + `srd2024.engine`, and all **94/94** existing `unittest` tests on Python 3.11+ stdlib).

---

## 1. Purpose, Design Principles & Verified Stage 1 + Stage 2 Compatibility Locks

Stage 1 compiled the 25 upstream **5e 2024 (SRD 5.2)** JSON files (2,029 entities) into a zero-server retrieval pack (`dist/srd2024.sqlite`, `dist/cards/`, `dist/web/`, `bin/srd`, `bin/srd-mcp`, `skill/SKILL.md`). Stage 2 layered an offline, deterministic neuro-symbolic rules compiler (`dist/rules.json`) and pure Python character/combat state machine (`srd2024.engine`).

Stage 3 builds a **Multi-Agent 5e 2024 (SRD 5.2) Tabletop & Model Evaluation Arena** (`src/srd2024/arena/`) strictly on top of Stage 1 + Stage 2:
- **Deterministic Adjudication, Pluggable Minds:** Every character sheet is compiled via `srd2024.engine.character.compile_character()`, and every combat move, attack, weapon mastery, spell, concentration check, and death saving throw is resolved strictly via `srd2024.engine.combat.step()`. Agents (offline bots or live LLMs) never roll dice or mutate state—they only emit structured intents + rationale + optional party `table_talk`.
- **Partial Observability & Shared Party Blackboard:** Each agent receives its own full sheet, ally tactical status, **strictly public** enemy state (never enemy exact HP/AC/private slots), legal action hints, recent public combat log, and its own side's private `table_talk` blackboard.
- **Reproducible Offline & Live Model Eval:** Ships 4 zero-network deterministic drivers (`random`, `greedy`, `heuristic-tactical`, `replay`) plus 3 pure-stdlib HTTP LLM drivers (`openai-compat`, `anthropic`, `gemini`) using `urllib.request`, a deterministic quantitative scorer (`0–100` across 4 dimensions), a multi-seed benchmark leaderboard runner, 100% byte-identical trace replay verification, and a readable Markdown tabletop session transcript renderer.

### Hard Constraints (Non-Negotiable)
1. **Strictly Python 3.11+ stdlib only** (`json`, `urllib.request`, `urllib.error`, `dataclasses`, `typing`, `argparse`, `hashlib`, `math`, `re`, `copy`, `pathlib`, `os`, `sys`, `random`, `unittest`, `http.server` in tests). `pyproject.toml` MUST keep `dependencies = []`.
2. **Trademark Hygiene (matching PR #1, commit `da06e72`):** Always write **"5e 2024 (SRD 5.2)"** in project prose, docstrings, CLI help, docs, and README—never use third-party trademarks outside verbatim upstream attribution strings/URLs.
3. **Zero Breaking Changes — ALL 94 Existing Stage 1 + Stage 2 Tests MUST Pass Unmodified.** Every existing test lock verified in `tests/` is tabulated below:

| Lock ID | Exact Test Location | Verified Assertion | Stage 3 Engineering Rule |
|---|---|---|---|
| **Lock A** | `tests/test_build_and_schema.py:62-77`, `tests/test_stage2_rules_compiler.py` | `pyproject.toml` contains `srd-build`, `srd`, `srd-mcp`, `srd-rules-build`, `srd-engine-mcp`; existing `bin/*` wrappers are `+x` with `python3` shebang. | Only **append** `srd-arena = "srd2024.runtime.arena_bridge:main_arena"` to `pyproject.toml`; add `bin/srd-arena` (`0755`, `#!/usr/bin/env python3`). Never modify existing entries or binaries. |
| **Lock B** | `tests/test_build_and_schema.py:142-200`, `tests/test_stage2_rules_compiler.py` | `bin/srd-build --out-dir <tmp>` and `bin/srd-rules-build --out-dir <tmp>` produce byte-identical `dist/manifest.json`, `dist/cards/**`, `dist/web/**`, `dist/rules.json`. | Stage 3 makes **zero changes** to `dist/**`, `src/srd2024/compiler/**`, or `src/srd2024/rules_compiler/**`. |
| **Lock C** | `tests/test_cli_and_mcp.py:123-128`, `tests/test_stage2_surfaces.py:253-270` | Default `bin/srd-mcp` (no `--engine` / `--arena`, `SRD2024_ENGINE` unset) MUST return the exact **5 Stage 1 tools** in `tools/list` (`srd_search`, `srd_get`, `srd_list`, `srd_graph`, `srd_validate`) and `srd://meta` in `resources/list`. | Default `bin/srd-mcp` continues advertising exact 5 tools in `tools/list`. |
| **Lock D** | `tests/test_stage2_surfaces.py:271-306` | `bin/srd-mcp --engine`, `SRD2024_ENGINE=1 bin/srd-mcp`, AND `bin/srd-engine-mcp` MUST return the exact **8 Stage 1 + Stage 2 tools** in `tools/list` (`srd_search`, `srd_get`, `srd_list`, `srd_graph`, `srd_validate`, `srd_rule`, `srd_compile_character`, `srd_encounter`). | Stage 3 MCP tools (`srd_arena_scenarios`, `srd_arena_run`, `srd_arena_bench`) are ALWAYS dispatchable via `tools/call` on all launchers, and advertised in `tools/list` ONLY when `--arena` or `SRD2024_ARENA=1` is passed (advertising all **11 tools** + `srd://arena/scenarios` resource). Neither default `bin/srd-mcp` (5 tools) nor `bin/srd-engine-mcp` / `--engine` (8 tools) changes! |
| **Lock E** | `tests/test_build_and_schema.py:323-330`, `tests/test_stage2_surfaces.py:388-408` | `src/srd2024/runtime/**` and `src/srd2024/engine/**` MUST NEVER import `srd2024.compiler` or `srd2024.rules_compiler`. | Extend this invariant to `src/srd2024/arena/**`: arena code imports ONLY stdlib + `srd2024.engine.*` (and internal `srd2024.arena.*`), never `compiler` or `rules_compiler`. |
| **Lock F** | `tests/test_build_and_schema.py:335-375`, `tests/test_stage2_surfaces.py:409-444` | `skill/SKILL.md` keeps YAML frontmatter `name: srd-2024`, `description`, `dist/cards/INDEX.md`, CC-BY-4.0 `SRD_ATTRIBUTION`, and all 8 Stage 1+2 tool names + `srd-engine-mcp`; `README.md` keeps CC-BY-4.0 attribution and all Stage 1+2 commands. | Append Stage 3 sections additively to `skill/SKILL.md` and `README.md` without removing or altering any Stage 1/2 lines or attribution text. |
| **Lock G** | All 9 existing test files in `tests/` + `src/srd2024/engine/**` | All 94 Stage 1+2 tests pass unmodified against Stage 2 `engine/`. | Stage 3 makes **zero edits** to `src/srd2024/engine/**` or any Stage 1/2 test file. |

---

## 2. Strict Zero-Collision Phase 3B File Ownership Split

Every file created or modified in Phase 3B has **exactly one owner**:

| Worker | Exclusive Owned Paths (Write) | Read-Only Inputs | Must NEVER Modify |
|---|---|---|---|
| **`arena-core`** | `src/srd2024/arena/__init__.py`, `src/srd2024/arena/scenarios.py`, `src/srd2024/arena/scoring.py`, `src/srd2024/arena/render.py`, `src/srd2024/arena/runner.py` | `docs/STAGE3-SPEC.md`, `dist/rules.json`, `src/srd2024/engine/**`, `src/srd2024/arena/agents.py` (public interface §4) | `src/srd2024/arena/agents.py`, `src/srd2024/engine/**`, `src/srd2024/runtime/**`, `src/srd2024/compiler/**`, `src/srd2024/rules_compiler/**`, `bin/**`, `pyproject.toml`, `dist/**`, `skill/**`, `README.md`, `tests/**` |
| **`arena-drivers`** | `src/srd2024/arena/agents.py` | `docs/STAGE3-SPEC.md`, `dist/rules.json`, `src/srd2024/engine/**` | `src/srd2024/arena/{__init__,scenarios,scoring,render,runner}.py`, `src/srd2024/engine/**`, `src/srd2024/runtime/**`, `bin/**`, `pyproject.toml`, `dist/**`, `skill/**`, `README.md`, `tests/**` |
| **`arena-surfaces`** | `bin/srd-arena` (`0755`), `pyproject.toml` (additive `srd-arena` script entry only), `src/srd2024/runtime/arena_bridge.py`, additive Stage 3 wiring in `src/srd2024/runtime/{cli,mcp,render}.py`, `skill/SKILL.md`, `README.md` | `docs/STAGE3-SPEC.md`, `src/srd2024/arena/**` public API | `src/srd2024/arena/**`, `src/srd2024/engine/**`, `src/srd2024/compiler/**`, `src/srd2024/rules_compiler/**`, `bin/{srd-build,srd-rules-build,srd,srd-mcp,srd-engine-mcp}`, `dist/**`, `tests/**` |
| **`arena-verifier`** | `tests/test_stage3_scenarios_and_scoring.py`, `tests/test_stage3_drivers_and_llm.py`, `tests/test_stage3_arena_runner.py`, `tests/test_stage3_surfaces.py` | Everything read-only | `src/**`, `bin/**`, `pyproject.toml`, `dist/**`, `skill/**`, `README.md`, **all 9 existing Stage 1+2 test files** |

---

## 3. Curated 5e 2024 Arena Scenarios (`src/srd2024/arena/scenarios.py`)

### 3.1 Empirically Verified Built-In Scenarios

Every PC `CharacterBuildSpec` below was empirically compiled against `srd2024.engine.character.compile_character(spec, RulesStore())` and verified to produce **`valid == True`, `errors == []`, `warnings == []`**. Every monster slug and action was empirically verified against `dist/rules.json` (all selected monsters have **`coverage == "full"`**) and smoke-tested in `srd2024.engine.combat.step()`.

#### Scenario 1: `goblin-ambush` (Tier 1, Level 1 — 2 PCs vs 2 `goblin-warrior`s)
- **Tactical Challenges Tested:** Reach vs. ranged positioning, 2024 Weapon Masteries (`Sap`, `Vex`, `Slow`), `fire-bolt` / `ray-of-frost` / `magic-missile`, `shield` reaction AC economy & `mage-armor`, and focus fire against `goblin-warrior`'s conditional Advantage (`+1d4` bonus damage).
- **`max_rounds_default`:** `10`
- **Party Combatants:**
  1. **`pc-bram` — Bram Ironhand** at `{"x": 0, "y": 0}` (Verified derived: `L1`, `AC 19`, `HP 12`, `Init +4`, `Speed 30`, `Extra Attacks 1`):
     ```json
     {
       "name": "Bram Ironhand",
       "species": "human",
       "subspecies": null,
       "size": "Medium",
       "background": "soldier",
       "background_asi": {"str": 2, "con": 1},
       "origin_feat": "savage-attacker",
       "extra_origin_feat": "alert",
       "class": "fighter",
       "subclass": null,
       "level": 1,
       "ability_method": "point_buy",
       "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
       "asi_choices": [],
       "fighting_style": "defense",
       "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
       "equipment": {
         "armor": "chain-mail",
         "shield": true,
         "weapons": ["longsword", "shortbow", "javelin"]
       },
       "weapon_mastery_weapons": ["longsword", "shortbow", "javelin"],
       "cantrips": [],
       "prepared_spells": [],
       "hp_method": "fixed"
     }
     ```
     *(Active masteries: `longsword` → `sap`, `shortbow` → `vex`, `javelin` → `slow`.)*
  2. **`pc-talia` — Talia Spark** at `{"x": 0, "y": 15}` (Verified derived: `L1`, `AC 12`, `HP 8`, `Init +2`, `DC 13`, `Spell Atk +5`, `Slots {"1": 2}`):
     ```json
     {
       "name": "Talia Spark",
       "species": "elf",
       "subspecies": "elven-lineage-high-elf",
       "size": "Medium",
       "background": "sage",
       "background_asi": {"int": 2, "con": 1},
       "origin_feat": "magic-initiate",
       "class": "wizard",
       "subclass": null,
       "level": 1,
       "ability_method": "standard_array",
       "base_abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
       "asi_choices": [],
       "skill_proficiencies": ["arcana", "history", "investigation", "insight"],
       "equipment": {
         "armor": null,
         "shield": false,
         "weapons": ["quarterstaff", "dagger"]
       },
       "cantrips": ["fire-bolt", "ray-of-frost", "acid-splash"],
       "prepared_spells": ["shield", "magic-missile", "mage-armor", "burning-hands"],
       "hp_method": "fixed"
     }
     ```
- **Enemy Combatants:**
  1. **`goblin-1`** (`monster: "goblin-warrior"`, `name: "Goblin Scout A"`, `position: {"x": 20, "y": 0}`) — CR 0.25, HP 10, AC 15, actions: `Scimitar` (`+4`, 5 ft, `1d6+2` slashing + `1d4` if Advantage), `Shortbow` (`+4`, 80/320 ft, `1d6+2` piercing + `1d4` if Advantage).
  2. **`goblin-2`** (`monster: "goblin-warrior"`, `name: "Goblin Scout B"`, `position: {"x": 25, "y": 15}`) — same verified statblock.

#### Scenario 2: `crypt-of-ghouls` (Tier 2, Level 5 — 3 PCs vs 4 Undead)
- **Tactical Challenges Tested:** AoE `fireball` against clustered non-fire-immune undead, `paralyzed` threat (`ghoul` and `ghast` Claw CON DC 10 → `paralyzed` + automatic melee crit within 5 ft), 0-HP revival via Bonus Action `healing-word` or Action `cure-wounds`, `bless` Concentration + Concentration save protection, Extra Attack, and Weapon Masteries (`Sap`, `Graze`, `Slow`).
- **`max_rounds_default`:** `12`
- **Party Combatants:**
  1. **`pc-aldric` — Aldric Vance** at `{"x": 0, "y": 5}` (Verified derived: `L5`, `AC 19`, `HP 44`, `Init +5`, `Extra Attacks 2`):
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
       "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
       "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}],
       "fighting_style": "defense",
       "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
       "equipment": {
         "armor": "chain-mail",
         "shield": true,
         "weapons": ["longsword", "greatsword", "javelin"]
       },
       "weapon_mastery_weapons": ["longsword", "greatsword", "javelin", "battleaxe"],
       "cantrips": [],
       "prepared_spells": [],
       "hp_method": "fixed"
     }
     ```
  2. **`pc-lyra` — Lyra Starweave** at `{"x": 0, "y": 20}` (Verified derived: `L5`, `AC 11`, `HP 32`, `Init +1`, `DC 15`, `Spell Atk +7`, `Slots {"1": 4, "2": 3, "3": 2}`):
     ```json
     {
       "name": "Lyra Starweave",
       "species": "elf",
       "subspecies": "elven-lineage-high-elf",
       "size": "Medium",
       "background": "sage",
       "background_asi": {"int": 2, "con": 1},
       "origin_feat": "magic-initiate",
       "class": "wizard",
       "subclass": "evoker",
       "level": 5,
       "ability_method": "standard_array",
       "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
       "asi_choices": [{"level": 4, "type": "asi", "increases": {"int": 2}}],
       "skill_proficiencies": ["arcana", "history", "investigation", "insight"],
       "equipment": {
         "armor": null,
         "shield": false,
         "weapons": ["quarterstaff", "dagger"]
       },
       "cantrips": ["fire-bolt", "ray-of-frost", "acid-splash", "chill-touch"],
       "prepared_spells": [
         "fireball", "lightning-bolt", "shatter", "misty-step", "scorching-ray",
         "magic-missile", "shield", "mage-armor", "burning-hands", "hold-person"
       ],
       "hp_method": "fixed"
     }
     ```
     *(Note: `elven-lineage-high-elf` grants `misty-step` as a lineage spell at L5, so the 9 remaining prepared spells exactly satisfy Wizard L5 `prepared_spells == 9` with zero warnings.)*
  3. **`pc-dorian` — Dorian Dawnshield** at `{"x": 5, "y": 15}` (Verified derived: `L5`, `AC 16`, `HP 43`, `Init +0`, `DC 15`, `Spell Atk +7`, `Slots {"1": 4, "2": 3, "3": 2}`, `damage_resistances: ["poison"]`):
     ```json
     {
       "name": "Dorian Dawnshield",
       "species": "dwarf",
       "subspecies": null,
       "size": "Medium",
       "background": "acolyte",
       "background_asi": {"wis": 2, "cha": 1},
       "origin_feat": "magic-initiate",
       "class": "cleric",
       "subclass": "life-domain",
       "level": 5,
       "ability_method": "standard_array",
       "base_abilities": {"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 15, "cha": 12},
       "asi_choices": [{"level": 4, "type": "asi", "increases": {"wis": 2}}],
       "skill_proficiencies": ["insight", "religion", "medicine", "persuasion"],
       "equipment": {
         "armor": "scale-mail",
         "shield": true,
         "weapons": ["mace"]
       },
       "cantrips": ["sacred-flame", "guidance", "light", "resistance"],
       "prepared_spells": [
         "bless", "cure-wounds", "healing-word", "guiding-bolt",
         "shield-of-faith", "aid", "hold-person", "mass-healing-word", "spirit-guardians"
       ],
       "hp_method": "fixed"
     }
     ```
- **Enemy Combatants (all `coverage: "full"` in `dist/rules.json`):**
  1. **`ghoul-1`** (`monster: "ghoul"`, `name: "Crypt Ghoul A"`, `position: {"x": 25, "y": 0}`) — CR 1, HP 22, AC 12, `Multiattack` (2× `Bite`), `Bite`, `Claw` (CON DC 10 → `paralyzed`), `poison` immune.
  2. **`ghoul-2`** (`monster: "ghoul"`, `name: "Crypt Ghoul B"`, `position: {"x": 25, "y": 10}`) — same verified statblock.
  3. **`ghast-1`** (`monster: "ghast"`, `name: "Crypt Ghast"`, `position: {"x": 30, "y": 5}`) — CR 2, HP 36, AC 13, `Bite` (`1d8+3` piercing + `2d8` necrotic), `Claw` (CON DC 10 → `paralyzed`), `poison` immune, `necrotic` resistant.
  4. **`skeleton-1`** (`monster: "skeleton"`, `name: "Crypt Archer"`, `position: {"x": 35, "y": 15}`) — CR 0.25, HP 13, AC 14, `Shortsword`, `Shortbow` (`+5`, 80/320 ft).

#### Scenario 3: `wyrmling-lair` (Tier 2, Level 5 — 4 PCs vs 3 Fire-Immune Enemies)
- **Tactical Challenges Tested:** **Damage Immunity Awareness** (every enemy is `fire`-immune — Wizard MUST NOT waste `fireball`, `fire-bolt`, `scorching-ray`, or `burning-hands`, and MUST pivot to `lightning-bolt`, `shatter`, `magic-missile`, `ray-of-frost`, or `chill-touch`), spreading out vs. 15-ft cone `Fire Breath` (`red-dragon-wyrmling` 7d6 DC 13 DEX half, `hell-hound` 5d6 DC 12 DEX half), `bless` + `shield` economy, Rogue `Vex` + `Nick` combo, and multi-round focus fire & revival.
- **`max_rounds_default`:** `15`
- **Party Combatants:**
  1. **`pc-aldric` — Aldric Vance** (same L5 Fighter spec) at `{"x": 0, "y": 0}`.
  2. **`pc-lyra` — Lyra Starweave** (same L5 Wizard spec) at `{"x": 0, "y": 25}`.
  3. **`pc-dorian` — Dorian Dawnshield** (same L5 Cleric spec) at `{"x": 5, "y": 15}`.
  4. **`pc-kaelen` — Kaelen Whisper** at `{"x": 10, "y": 20}` (Verified derived: `L5`, `AC 16`, `HP 38`, `Init +7`, `shortsword` `+7 1d6+4` [Vex active], `dagger` `+7 1d4+4` [Nick active], `shortbow` `+7 1d6+4`):
     ```json
     {
       "name": "Kaelen Whisper",
       "species": "halfling",
       "subspecies": null,
       "size": "Small",
       "background": "criminal",
       "background_asi": {"dex": 2, "con": 1},
       "origin_feat": "alert",
       "class": "rogue",
       "subclass": "thief",
       "level": 5,
       "ability_method": "standard_array",
       "base_abilities": {"str": 8, "dex": 15, "con": 14, "int": 12, "wis": 13, "cha": 10},
       "asi_choices": [{"level": 4, "type": "asi", "increases": {"dex": 2}}],
       "skill_proficiencies": ["stealth", "sleight-of-hand", "acrobatics", "perception", "investigation", "deception"],
       "equipment": {
         "armor": "studded-leather-armor",
         "shield": false,
         "weapons": ["shortsword", "dagger", "shortbow"]
       },
       "weapon_mastery_weapons": ["shortsword", "dagger"],
       "cantrips": [],
       "prepared_spells": [],
       "hp_method": "fixed"
     }
     ```
- **Enemy Combatants (all `coverage: "full"` and `fire`-immune in `dist/rules.json`):**
  1. **`wyrmling-1`** (`monster: "red-dragon-wyrmling"`, `name: "Red Dragon Wyrmling"`, `position: {"x": 30, "y": 10}`) — CR 4, HP 75, AC 17, `damage_immunities: ["fire"]`, `Multiattack` (2× `Rend`), `Rend` (`+6`, 5 ft, `1d10+4` slashing + `1d6` fire), `Fire Breath` (`7d6` fire, DC 13 DEX half, 15-ft cone, recharge 5–6).
  2. **`hound-1`** (`monster: "hell-hound"`, `name: "Cinder Hound"`, `position: {"x": 25, "y": 0}`) — CR 3, HP 58, AC 15, `damage_immunities: ["fire"]`, `Multiattack` (2× `Bite`), `Bite` (`+5`, 5 ft, `1d8+3` piercing + `1d6` fire), `Fire Breath` (`5d6` fire, DC 12 DEX half, 15-ft cone, recharge 5–6).
  3. **`magmin-1`** (`monster: "magmin"`, `name: "Ember Magmin"`, `position: {"x": 25, "y": 20}`) — CR 0.5, HP 13, AC 14, `damage_immunities: ["fire"]`, `Touch` (`+4`, 5 ft, `2d4+2` fire).

### 3.2 Scenario Public API & Custom Scenario JSON Loading (`scenarios.py`)

```python
BUILTIN_SCENARIO_IDS: tuple[str, ...] = ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair")

def list_scenarios() -> list[dict[str, Any]]:
    """Return compact sorted metadata for all built-in scenarios (id, name, tier, party_level, max_rounds, description, party_summary, enemy_summary, tactical_themes)."""

def load_scenario(name_or_path: str | Path | dict[str, Any], rules: RulesStore) -> dict[str, Any]:
    """Load a built-in scenario slug, a custom scenario JSON file path, or an inline scenario dict.

    Compiles every PC spec via `compile_character(spec, rules)` (raising `ValueError` with
    formatted errors if any PC is invalid), validates every monster slug exists in `rules`,
    and returns a `CompiledScenario` dict:
    {
      "id": str,
      "name": str,
      "description": str,
      "tier": int,
      "party_level": int,
      "max_rounds": int,
      "tactical_themes": list[str],
      "combatants": list[dict],   # ready to pass directly to start_encounter
      "party_ids": list[str],
      "enemy_ids": list[str],
      "source": "builtin" | "file" | "inline"
    }
    """
```

Custom Scenario JSON Schema:
```json
{
  "id": "my-custom-skirmish",
  "name": "My Custom Skirmish",
  "description": "Custom 5e 2024 arena encounter.",
  "tier": 1,
  "party_level": 1,
  "max_rounds": 10,
  "tactical_themes": ["positioning"],
  "party": [
    {"id": "pc-1", "spec": { "...CharacterBuildSpec..." }, "position": {"x": 0, "y": 0}}
  ],
  "enemies": [
    {"id": "enemy-1", "monster": "goblin-warrior", "name": "Goblin A", "position": {"x": 20, "y": 0}}
  ]
}
```
*(A party entry may supply either `"spec"` — compiled via `compile_character` — or a pre-compiled `"statblock"`. Duplicate combatant IDs, empty `party` or `enemies`, invalid PC specs, or unknown monster slugs MUST raise `ValueError`.)*

---

## 4. Pluggable Multi-Agent Driver Architecture (`src/srd2024/arena/agents.py`)

### 4.1 Core Data Types & Protocol

```python
@dataclass(frozen=True)
class AgentDecision:
    intent: dict[str, Any]
    rationale: str = ""
    table_talk: str | None = None
    raw_response: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

class AgentDriver(Protocol):
    name: str
    def decide(self, observation: dict[str, Any]) -> AgentDecision: ...
    def reset(self, seed: int | None = None) -> None: ...
```

### 4.2 Partial Observability Observation Schema (`observation: dict`)

Built by `runner.py:build_observation(state, actor_id, scenario, blackboard, recent_hooks, cooldowns, rules, retry_feedback=None)`. **Strict information-hiding invariant:** opposing-side entries NEVER contain exact `hp`, `hp_max`, `ac`, private `spellcasting.slots`, or enemy `table_talk`.

```json
{
  "round": 1,
  "scenario": {"id": "wyrmling-lair", "name": "Wyrmling Lair", "description": "..."},
  "self": {
    "id": "pc-lyra",
    "name": "Lyra Starweave",
    "side": "party",
    "kind": "pc",
    "source_id": "classes:wizard",
    "level": 5,
    "size": "Medium",
    "position": {"x": 0, "y": 25},
    "hp": 32,
    "hp_max": 32,
    "temp_hp": 0,
    "ac": 11,
    "effective_ac": 14,
    "speed_ft": 30,
    "movement_remaining_ft": 30,
    "conditions": [],
    "concentration": null,
    "active_effects": [],
    "turn_resources": {"action": true, "bonus_action": true, "reaction": true, "attacks_remaining": 0},
    "weapons": [],
    "monster_actions": [],
    "available_monster_actions": [],
    "spellcasting": {"ability": "int", "spell_save_dc": 15, "spell_attack_bonus": 7, "slots": {"1": 4, "2": 3, "3": 2}, "cantrips": [], "prepared_spells": []},
    "damage_resistances": [],
    "damage_immunities": [],
    "death_saves": {"successes": 0, "failures": 0}
  },
  "allies": [
    {
      "id": "pc-aldric",
      "name": "Aldric Vance",
      "position": {"x": 0, "y": 0},
      "distance_ft": 25,
      "hp": 44,
      "hp_max": 44,
      "ac": 19,
      "alive": true,
      "unconscious": false,
      "conditions": [],
      "concentration": null,
      "active_effects": ["bless"]
    }
  ],
  "enemies": [
    {
      "id": "wyrmling-1",
      "name": "Red Dragon Wyrmling",
      "source_id": "monsters:red-dragon-wyrmling",
      "size": "Medium",
      "position": {"x": 30, "y": 10},
      "distance_ft": 30,
      "alive": true,
      "bloodied": false,
      "hp_band": "unharmed",
      "conditions": [],
      "visible_effects": []
    }
  ],
  "legal_actions": {
    "active_actor_id": "pc-lyra",
    "movement_remaining_ft": 30,
    "actions": ["attack", "dodge", "dash", "disengage", "help", "cast_spell"],
    "bonus_actions": ["cast_spell"],
    "weapons": ["quarterstaff", "dagger"],
    "spells": ["fire-bolt", "ray-of-frost", "acid-splash", "chill-touch", "fireball", "lightning-bolt", "shatter", "misty-step", "scorching-ray", "magic-missile", "mage-armor", "burning-hands", "hold-person"],
    "reaction_spells": ["shield"],
    "available_monster_actions": [],
    "can_stand_up": false,
    "can_end_turn": true,
    "in_reach_enemies": [],
    "downed_allies": []
  },
  "party_blackboard": [
    {"round": 1, "actor_id": "pc-dorian", "actor_name": "Dorian Dawnshield", "message": "Casting Bless on Aldric, Kaelen, and Lyra!"}
  ],
  "recent_events": [
    "Encounter 'wyrmling-lair' begins! Round 1: Kaelen Whisper acts first."
  ],
  "retry_feedback": null
}
```

`hp_band` deterministic mapping (for enemies only):
- `not alive` or `hp == 0` → `"defeated"`
- `hp == hp_max` → `"unharmed"`
- `hp > hp_max // 2` → `"scratched"`
- `hp > hp_max // 4` → `"bloodied"` (`bloodied = True`)
- `hp <= hp_max // 4` → `"critical"` (`bloodied = True`)

### 4.3 Four Offline Deterministic Drivers (Zero Network)

1. **`RandomDriver(seed: int = 1, rules: RulesStore | None = None)`** (`name = "random"`):
   - Uses an internal `random.Random(seed)` instance (resettable via `reset(seed)`).
   - Builds a candidate pool of simple legal intents for the active actor (e.g., random short movement step within remaining movement, attack a random alive enemy in range with a random weapon or available monster action, cast a random available spell on a random target, `dodge`, `dash`, or `end_turn`) and picks uniformly at random.
   - Deliberately unoptimized: does not check immunities, does not heal downed allies, emits `table_talk = None`. Serves as the seeded legal-action floor baseline.

2. **`GreedyDriver(rules: RulesStore | None = None)`** (`name = "greedy"`):
   - Purely maximizes **raw immediate expected dice damage** without tactical reasoning:
     - Computes naive average dice value (`N * (M + 1) / 2 + flat`) for all available offensive spells, weapons, and monster actions.
     - **Deliberately ignores** target `damage_immunities` and `damage_resistances` (so in `wyrmling-lair`, Lyra greedily fires `fireball` `8d6` and `fire-bolt` `2d10` at fire-immune enemies!).
     - Ignores healing/revival (`healing-word`, `cure-wounds`), buffs (`bless`, `shield`, `mage-armor`), Weapon Mastery synergies, and defensive positioning.
     - If no enemy is in range of its highest-raw-damage option and movement remains, moves straight toward the nearest alive enemy.
     - Emits `table_talk = None`.

3. **`HeuristicTacticalDriver(rules: RulesStore)`** (`name = "heuristic-tactical"`):
   - Strong, purely deterministic **5e 2024 (SRD 5.2)** tactical baseline that works cleanly for BOTH PCs and Monsters:
     - **Prone Recovery:** If `self.conditions` includes `"prone"` and `legal_actions.can_stand_up`, immediately emits `move(stand_up=True, to=self.position)`.
     - **0-HP Revival & Triage:** If any ally has `alive and hp == 0` (`unconscious`):
       - Prefer Bonus Action `healing-word` (60 ft range, keeps Action free!) or `mass-healing-word`, else move within 5 ft and cast Action `cure-wounds`. Announces revival on `table_talk`.
     - **Immunity & Resistance Lookup via `RulesStore`:** For every alive enemy with `source_id` (e.g., `monsters:red-dragon-wyrmling`), inspects `rules.get_monster(source_id)` for `damage_immunities`, `damage_resistances`, and `damage_vulnerabilities`. Multiplies expected damage by `0.0` (immune), `0.5` (resistant), `2.0` (vulnerable), or `1.0` (normal). **Never casts a spell or attack whose total effective expected damage is `0.0`!** (In `wyrmling-lair`, Wizard skips `fireball`/`fire-bolt`/`scorching-ray`/`burning-hands` and casts `lightning-bolt`, `shatter`, `magic-missile`, `ray-of-frost`, or `chill-touch`.)
     - **Buff, Concentration & Reaction Shield Economy:**
       - Cleric casts `bless` (Action, Concentration) on up to 3 conscious party members (prioritizing Fighters/Rogues/Casters) on Round 1 when not concentrating and slots `>= 1`.
       - Wizard casts `shield` (`as_reaction=True`, consuming Reaction) if `reaction` is available, `shield` is prepared with slots `>= 1`, no `shield` effect is active, and any conscious enemy is within 15 ft (or Wizard HP <= 60%).
       - Wizard casts `mage-armor` if unarmored (`ac < 13 + dex_mod`), no `mage_armor` active, slots `>= 1`, and no immediate 2+-target AoE kill shot is available.
       - Wizard uses Bonus Action `misty-step` to teleport 20–30 ft away if adjacent (`<= 5 ft`) to a conscious enemy and has level 2+ slots.
     - **AoE Discipline & Positioning:**
       - Evaluates AoE spells (`fireball`, `lightning-bolt`, `shatter`, `burning-hands`) over subsets of alive enemies within AoE geometry (`fireball` 20-ft radius sphere → enemies within 40 ft of each other; `lightning-bolt` 100-ft line; `shatter` 10-ft radius sphere) using effective non-immune damage, strictly **excluding allies** (zero friendly fire).
       - Ranged PCs/casters kite away from enemies within 5 ft (avoiding `ranged_hostile_within_5ft` Disadvantage) and spread `>= 20 ft` from allies when fighting breath-weapon enemies (`Fire Breath`).
       - Melee PCs close to 5 ft of priority targets and consume ALL Extra Attacks (`attacks_remaining > 0`) plus Rogue `shortsword` (Vex) → off-hand `dagger` (Nick!) combo before ending turn.
     - **Focus Fire & Weapon Masteries:**
       - Target priority score favors: (a) `critical` / `bloodied` enemies (eliminate enemy actions!), (b) `paralyzed` enemies within 5 ft (auto-crit!), (c) high-threat paralyzers/breath monsters (`ghast`, `ghoul`, `red-dragon-wyrmling`, `hell-hound`).
       - Weapon selection: `longsword` (`Sap`) vs dangerous adjacent melee threats without active `sap`, `greatsword` (`Graze`) for high-DPR follow-up or high-AC enemies, `shortsword` (`Vex`) + `dagger` (`Nick`) for Rogue, `shortbow` (`Vex`) / `javelin` (`Slow`) at range.
     - **Monster Tactics:** Uses recharged `Fire Breath` when 1+ PCs are within 15-ft cone range (moving closer if needed to hit 2+ PCs), uses `ghoul`/`ghast` `Claw` against conscious non-paralyzed PCs and `Multiattack` (`Bite`) on already-paralyzed PCs, uses `goblin-warrior` `Shortbow` from range or `Scimitar` in melee, and moves within reach before calling `Multiattack`.
     - **Party Blackboard (`table_talk`):** Emits concise tactical callouts on key decisions (focus target, immunity pivot, revival, Bless, mastery setup) so `coordination_score` and transcripts showcase genuine multi-agent teamwork.

4. **`ReplayDriver(transcript: dict[str, Any], side: str | None = None)`** (`name = "replay"`):
   - Extracts the ordered list of accepted `intent` dicts (and `rationale`/`table_talk`) from `transcript["actions"]` (filtered by `side` if specified, excluding `auto_skipped` and `forced_fallback` entries — or replaying exact sequence per `(round, actor_id)`).
   - Pops the next recorded decision verbatim on each `decide(observation)` call (verifying `actor_id` matches; raising `RuntimeError` if transcript is exhausted unexpectedly).
   - Enables 100% deterministic verification in `replay_match()`.

### 4.4 Three Live LLM Drivers (Strictly Python Stdlib `urllib.request`, Zero External SDKs)

All three live drivers inherit from `BaseLLMDriver` and share:
- **Constructor Parameters:** `model: str`, `api_base: str | None = None`, `api_key_env: str | None = None`, `api_key: str | None = None`, `temperature: float = 0.0`, `max_tokens: int = 512`, `timeout_s: float = 60.0`, `http_transport: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None`.
  - Default `http_transport` uses strictly stdlib `urllib.request.Request` + `urllib.request.urlopen`, raising `RuntimeError` with status code + body snippet on `urllib.error.HTTPError` / `URLError`.
  - Allowing both `api_base` pointing to a local `http.server.HTTPServer` AND optional `http_transport` makes unit testing 100% deterministic with zero external network calls!
- **Prompt & Strict JSON Decision Contract:**
  - `build_system_prompt()` teaches the exact 5e 2024 (SRD 5.2) intent schemas (`move`, `attack`, `cast_spell`, `dodge`, `dash`, `disengage`, `help`, `end_turn`) and instructs the model to respond with **only** a JSON object:
    ```json
    {
      "intent": {"type": "attack", "actor_id": "pc-bram", "target_id": "goblin-1", "weapon": "longsword"},
      "rationale": "Apply Sap mastery to weaken Goblin Scout A's counterattack.",
      "table_talk": "Engaging Goblin Scout A in melee—focus fire!"
    }
    ```
  - `build_user_prompt(observation: dict)` serializes the partial-observability `observation` compactly (including `retry_feedback` prominently when `attempt > 0`).
- **Robust JSON Extractor (`extract_decision_json(raw_text: str) -> dict[str, Any]`):**
  1. Try `json.loads(raw_text.strip())`.
  2. Else search for fenced code blocks (`` ```json ... ``` `` or `` ``` ... ``` ``) and parse the first valid JSON object.
  3. Else perform a balanced `{ ... }` scan respecting JSON double-quoted strings and escapes to extract the first valid JSON object containing `"intent"`.
  4. Validate that the parsed result is a `dict` containing `"intent"` as a `dict` with string `"type"` (else raise `ValueError` so the runner logs a parse violation and retries with feedback).

#### Concrete Provider Drivers
| Driver Class | Type Spec | Default `api_base` | Default `api_key_env` | Endpoint & Auth | Response & Usage Mapping |
|---|---|---|---|---|---|
| **`OpenAICompatDriver`** | `openai-compat:<model>` | `https://api.openai.com/v1` (works with OpenAI, xAI Grok `https://api.x.ai/v1`, OpenRouter `https://openrouter.ai/api/v1`, Ollama `http://localhost:11434/v1`, vLLM, LM Studio) | `OPENAI_API_KEY` (optional when `api_base` is `localhost`/`127.0.0.1`) | `POST {api_base}/chat/completions`, header `Authorization: Bearer <key>` (omitted if key empty) | `choices[0].message.content`; usage `{prompt_tokens, completion_tokens, total_tokens}` |
| **`AnthropicDriver`** | `anthropic:<model>` | `https://api.anthropic.com` | `ANTHROPIC_API_KEY` | `POST {api_base}/v1/messages`, headers `x-api-key: <key>`, `anthropic-version: 2023-06-01` | Concatenated `content[i].text` where `type == "text"`; usage `{prompt_tokens: input_tokens, completion_tokens: output_tokens, total_tokens: input+output}` |
| **`GeminiDriver`** | `gemini:<model>` | `https://generativelanguage.googleapis.com` | `GEMINI_API_KEY` | `POST {api_base}/v1beta/models/{model}:generateContent?key={key}` | Concatenated `candidates[0].content.parts[].text`; usage `{prompt_tokens: promptTokenCount, completion_tokens: candidatesTokenCount, total_tokens: totalTokenCount}` |

### 4.5 Driver Factory (`create_driver`)

```python
def create_driver(
    spec: str | dict[str, Any],
    *,
    rules: RulesStore | None = None,
    seed: int = 1,
    api_base: str | None = None,
    api_key_env: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    timeout_s: float = 60.0,
    replay_transcript: dict[str, Any] | None = None,
    replay_side: str | None = None,
) -> AgentDriver: ...
```
Accepted string specs: `"random"`, `"greedy"`, `"heuristic-tactical"`, `"replay"`, `"openai-compat:<model>"`, `"anthropic:<model>"`, `"gemini:<model>"`. Unknown driver types MUST raise `ValueError`.

---

## 5. Match Runner, Shared Party Blackboard & Benchmark Suite (`src/srd2024/arena/runner.py`)

### 5.1 Match Execution Contract (`run_match`)

```python
def run_match(
    scenario: str | Path | dict[str, Any],
    *,
    party_driver: AgentDriver | dict[str, AgentDriver],
    enemy_driver: AgentDriver | dict[str, AgentDriver],
    rules: RulesStore,
    seed: int = 42,
    max_rounds: int | None = None,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
) -> dict[str, Any]:
    """Run a complete multi-turn 5e 2024 arena match and return a JSON-serializable MatchTranscript."""
```

Key Runner Guarantees:
1. **Strict Engine Adjudication:** Calls `step(None, start_intent, rules)` to initialize state, then advances strictly via `step(state, intent, rules)`. Never mutates `state` directly.
2. **Monster Recharge Sidecar (Non-Invasive to Stage 2 `combat.py`):** Tracks `action_cooldowns: dict[str, dict[str, bool]]` for monster actions with `usage.type == "recharge on roll"` (e.g., `Fire Breath` 5–6) using a separate deterministic `DeterministicRNG(seed=seed ^ 0x5A5A5A5A)` so `combat.py`'s RNG draw stream is untouched. At the start of a monster's turn, any on-cooldown action rolls `1d6` and recharges if `roll >= min_value` (recorded in `transcript["recharge_events"]`). Attempting a recharging action before recharge fails validation with `"Action '<name>' is currently recharging."`.
3. **Auto-Skip Incapacitated / 0-HP Turns:** If the active combatant cannot act (`hp == 0`, `unconscious`, `paralyzed`, `stunned`, `petrified`, `incapacitated`, or `not alive`), the runner immediately submits `{"type": "end_turn", "actor_id": active_id}` with `auto_skipped: True` (triggers 2024 Death Saves / end-of-turn `save_ends` saves cleanly without wasting a driver decision or incurring false violations).
4. **Strict Intent Validation, Retry Feedback & Safe Fallback:**
   - On each action slot (`0..max_actions_per_turn - 1`), attempts `0..max_retries_per_action`:
     - Calls `driver.decide(observation)`. Parse/runtime errors from LLM JSON decoding are caught and converted into validation errors.
     - Defaults omitted `actor_id` to `active_id` on active-turn intents (`move`, `attack`, `dodge`, `dash`, `disengage`, `help`, `end_turn`, non-reaction `cast_spell`).
     - Validates recharge cooldown and passes `intent` to `step(state, intent, rules)`.
     - On `status == "ok"`: records accepted action entry, appends non-empty `table_talk` to `blackboard[side]`, updates `state`, and advances.
     - On `status != "ok"`: records a structured violation in `transcript["violations"]` and populates `observation["retry_feedback"]` for the next retry.
   - If all retries fail, executes forced fallback `step(state, {"type": "end_turn", "actor_id": active_id}, rules)` tagged `forced_fallback: True` so matches **never crash or deadlock**.
   - If `max_actions_per_turn` finishes without `end_turn` while `state["status"] == "active"`, automatically submits `end_turn` (`auto_end_turn: True`).
5. **Deterministic Winner Resolution:**
   - `party_alive = sum(1 for c in party if c["alive"])`, `party_conscious = sum(1 for c in party if c["alive"] and c["hp"] > 0)`, `enemies_alive = sum(1 for c in enemies if c["alive"] and c["hp"] > 0)`.
   - `"party"` if `enemies_alive == 0 and party_alive > 0`.
   - `"enemy"` if `party_alive == 0` OR (`enemies_alive > 0 and party_conscious == 0`).
   - `"draw"` otherwise (e.g., `max_rounds` reached with both sides conscious).

### 5.2 `MatchTranscript` JSON Schema

```json
{
  "schema_version": "3",
  "dataset_id": "srd-2024-5.2",
  "scenario": {"id": "wyrmling-lair", "name": "Wyrmling Lair", "tier": 2, "party_level": 5, "max_rounds": 15, "description": "..."},
  "config": {
    "seed": 42,
    "max_rounds": 15,
    "max_actions_per_turn": 6,
    "max_retries_per_action": 2,
    "party_driver": "heuristic-tactical",
    "enemy_driver": "heuristic-tactical"
  },
  "winner": "party",
  "rounds": 5,
  "initial_state": { "...EncounterState after start_encounter..." },
  "final_state": { "...EncounterState at match end..." },
  "actions": [
    {
      "seq": 1,
      "round": 1,
      "turn_index": 0,
      "action_index": 0,
      "actor_id": "pc-kaelen",
      "actor_name": "Kaelen Whisper",
      "side": "party",
      "driver": "heuristic-tactical",
      "attempts": 1,
      "first_attempt_legal": true,
      "auto_skipped": false,
      "auto_end_turn": false,
      "forced_fallback": false,
      "intent": {"type": "attack", "...": "..."},
      "rationale": "...",
      "table_talk": "...",
      "events": [],
      "state_diff": {},
      "narrative_hooks": [],
      "usage": {}
    }
  ],
  "violations": [
    {"round": 1, "turn_index": 0, "actor_id": "pc-1", "side": "party", "attempt": 1, "intent": {}, "error": "..."}
  ],
  "recharge_events": [
    {"round": 2, "actor_id": "wyrmling-1", "action": "Fire Breath", "roll": 5, "min_value": 5, "recharged": true}
  ],
  "blackboard": {
    "party": [{"round": 1, "actor_id": "pc-dorian", "actor_name": "Dorian Dawnshield", "message": "..."}],
    "enemy": []
  },
  "scorecard": { "...§6.1 MatchScorecard..." },
  "digest": "<sha256 of canonical final_state + events + scorecard>"
}
```

### 5.3 Deterministic Trace Replay (`replay_match`)

```python
def compute_transcript_digest(transcript: dict[str, Any]) -> str:
    """SHA-256 hex digest of canonical JSON (`sort_keys=True, separators=(',',':')`) over final_state, all action events, winner, and scorecard."""

def replay_match(
    transcript: dict[str, Any],
    rules: RulesStore,
    *,
    scenario_override: str | Path | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay a recorded MatchTranscript through `ReplayDriver` + `srd2024.engine.combat.step()`.

    Returns:
    {
      "status": "ok",
      "scenario_id": str,
      "seed": int,
      "deterministic_match": bool,
      "original_digest": str,
      "replayed_digest": str,
      "diffs": list[str],
      "replayed_transcript": dict
    }
    """
```
*(For custom scenarios not in `BUILTIN_SCENARIO_IDS`, `replay_match` reconstructs the exact initial combatants directly from `transcript["initial_state"]` when `scenario_override` is omitted, guaranteeing even custom-scenario traces replay 100% deterministically!)*

### 5.4 Multi-Seed / Multi-Scenario Benchmark Runner (`run_benchmark`)

```python
def run_benchmark(
    *,
    scenarios: list[str | Path] | None = None,        # default: list(BUILTIN_SCENARIO_IDS)
    contenders: list[str | dict[str, Any]] | None = None,  # default: ["heuristic-tactical", "greedy", "random"]
    enemy_agent: str | dict[str, Any] = "heuristic-tactical",
    seeds: list[int] | None = None,                   # default: [1, 2, 3]
    rules: RulesStore,
    max_rounds: int | None = None,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
    include_traces: bool = False,
) -> dict[str, Any]:
    """Run all (contender × scenario × seed) matches and return a deterministic BenchmarkReport."""
```

`BenchmarkReport` Schema:
```json
{
  "schema_version": "3",
  "status": "ok",
  "scenarios": ["goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"],
  "seeds": [1, 2, 3],
  "enemy_agent": "heuristic-tactical",
  "leaderboard": [
    {
      "rank": 1,
      "label": "heuristic-tactical",
      "driver": "heuristic-tactical",
      "matches": 9,
      "wins": 9,
      "losses": 0,
      "draws": 0,
      "win_rate": 1.0,
      "mean_composite_score": 89.4,
      "mean_outcome_score": 94.2,
      "mean_rules_legality_score": 100.0,
      "mean_tactical_score": 84.1,
      "mean_coordination_score": 69.5,
      "mean_rounds": 4.2,
      "total_violations": 0,
      "wasted_immune_hits": 0,
      "by_scenario": {
        "goblin-ambush": {"wins": 3, "matches": 3, "win_rate": 1.0, "mean_composite_score": 91.0}
      }
    }
  ],
  "matches": [
    {
      "contender": "heuristic-tactical",
      "scenario_id": "goblin-ambush",
      "seed": 1,
      "winner": "party",
      "rounds": 3,
      "composite_score": 91.0,
      "outcome_score": 95.0,
      "rules_legality_score": 100.0,
      "tactical_score": 85.0,
      "coordination_score": 76.0,
      "violations": 0,
      "wasted_immune_hits": 0,
      "digest": "..."
    }
  ]
}
```
Leaderboard sorting key: `(-mean_composite_score, -win_rate, -mean_rules_legality_score, label)`.

---

## 6. Deterministic Quantitative Scorer & Tabletop Transcript Renderer (`scoring.py` & `render.py`)

### 6.1 Quantitative Scorer (`src/srd2024/arena/scoring.py`)

```python
def score_match(
    transcript: dict[str, Any],
    rules: RulesStore | None = None,
    *,
    side: str = "party",
) -> dict[str, Any]:
    """Compute purely mechanical, deterministic 0–100 eval scores from a MatchTranscript."""
```

Every sub-score is clamped to `[0.0, 100.0]` and rounded to 2 decimal places.

**Contribution factor (#22).** A `productive_turn` is an active (non-auto-skipped) turn by `side` that contains at least one `attack`, `cast_spell`, or `help`. Let `contribution = productive_turns / active_turns` (`0.0` if there are no active turns). Ratio terms that a tiny sample can max out (legality, immunity discipline, zero friendly-fire, focus fire) are multiplied by `contribution`. Without this, one attack followed by idle, chatty turns scored about 50 points. A do-nothing policy now scores about 1. Count-based terms (damage, features, teamwork) already measure contribution, so they are not scaled.

1. **`outcome_score` (weight `0.35`):**
   - Win component (`0..50`): `50.0` if `winner == side`, `20.0` if `winner == "draw"`, `0.0` otherwise.
   - Survival component (`0..25`): `25.0 * (sum(1.0 if (alive and hp > 0) else (0.5 if (alive and winner == side) else 0.0)) / max(1, total_side_combatants))`. A downed-but-alive ally earns half credit only on a win, so a wiped party earns no survival points.
   - Remaining HP ratio (`0..15`): `15.0 * (sum(final_hp) / max(1, sum(initial_hp_max)))`.
   - Rounds efficiency (`0..10`): if `winner == side`, `10.0 * max(0.0, 1.0 - (rounds - 1) / max(1, max_rounds))`; else `0.0`.
2. **`rules_legality_score` (weight `0.25`):**
   - Let `total_decisions` = count of `side` actions where `not auto_skipped` and `not auto_end_turn`.
   - If `total_decisions == 0`: `100.0`.
   - `first_attempt_rate = first_attempt_legal_count / total_decisions`.
   - `fallback_penalty = 15.0 * (forced_fallbacks / total_decisions)`.
   - `extra_retry_penalty = min(20.0, 2.0 * max(0, side_violations - (total_decisions - first_attempt_legal_count)))`.
   - `raw_legality = clamp(100.0 * first_attempt_rate - fallback_penalty - extra_retry_penalty, 0.0, 100.0)`.
   - `rules_legality_score = raw_legality * max(0.05, contribution)`.
   - *(0 violations and full contribution → `100.0`. Invalid intents and forced fallbacks strictly lower the score. A fully legal but idle side keeps a 5% floor, so it still ranks above pure violations.)*
3. **`tactical_score` (weight `0.25`):**
   - **Damage efficiency (`0..40`):** `40.0 * min(1.0, effective_damage_dealt_to_opponents / max(1, total_opponent_initial_hp))`.
   - **Immunity discipline (`0..25`):** `max(0.0, 25.0 - 12.5 * wasted_immune_hits) * contribution`, where a `wasted_immune_hit` is any `damage_applied` event caused by `side` on an opposing combatant with a breakdown entry where `modifier == "immune"` and `raw > 0`.
   - **Action economy utilization (`0..20`):** `20.0 * contribution`.
   - **Tactical feature execution (`0..15`):** `min(15.0, mastery_triggers * 3.0 + effective_buff_casts * 3.0 + ally_revivals * 5.0 + effective_heals * 2.0)`.
4. **`coordination_score` (weight `0.15`):**
   - **Zero friendly-fire (`0..40`):** `max(0.0, 40.0 - 20.0 * friendly_fire_incidents) * contribution` (where a friendly-fire incident is any `damage_applied` event caused by `side` with `total_damage > 0` targeting a same-side combatant).
   - **Party blackboard communication (`0..20`):** `20.0 * min(1.0, productive_talk_turns / max(1.0, active_turns * 0.5))`, where `productive_talk_turns` counts productive turns that carry at least one `table_talk` message. Chat on idle turns earns nothing. `metrics.table_talk_messages` still reports the full message count.
   - **Focus-fire coordination (`0..20`):** `20.0 * focus_fire_ratio * contribution`, where `focus_fire_ratio` is the fraction of `side`'s offensive attacks/spells that target an already-damaged opponent (or the highest-threat target when all enemies are unharmed), defaulting to `1.0` if `<= 1` opponent is alive or `0` offensive actions were taken with `winner == side`.
   - **Teamwork & ally support (`0..20`):** `min(20.0, ally_revivals * 12.0 + ally_heals * 6.0 + ally_buff_targets * 4.0 + help_actions * 8.0 + mastery_setup_triggers * 4.0)` (where `mastery_setup_triggers` counts `sap`, `topple`, `vex`, `slow` triggers supporting the team).
5. **`composite_score`:**
   ```python
   composite_score = round(
       0.35 * outcome_score
       + 0.25 * rules_legality_score
       + 0.25 * tactical_score
       + 0.15 * coordination_score,
       2,
   )
   ```

Returned `MatchScorecard` dict:
```json
{
  "side": "party",
  "outcome_score": 95.0,
  "rules_legality_score": 100.0,
  "tactical_score": 88.5,
  "coordination_score": 92.0,
  "composite_score": 94.18,
  "weights": {"outcome": 0.35, "rules_legality": 0.25, "tactical": 0.25, "coordination": 0.15},
  "metrics": {
    "winner": "party",
    "rounds": 4,
    "max_rounds": 15,
    "survivors": 4,
    "total_allies": 4,
    "hp_ratio": 0.78,
    "effective_damage_dealt": 146,
    "wasted_immune_hits": 0,
    "wasted_immune_raw_damage": 0,
    "mastery_triggers": 5,
    "effective_buff_casts": 2,
    "ally_revivals": 1,
    "ally_heals": 2,
    "ally_buff_targets": 3,
    "help_actions": 0,
    "friendly_fire_incidents": 0,
    "friendly_fire_damage": 0,
    "table_talk_messages": 8,
    "active_turns": 14,
    "productive_turns": 14,
    "total_decisions": 18,
    "first_attempt_legal": 18,
    "total_violations": 0,
    "forced_fallbacks": 0,
    "llm_usage_totals": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
  }
}
```

### 6.2 Tabletop Transcript & Leaderboard Renderer (`src/srd2024/arena/render.py`)

Public functions:
- `render_scenarios_markdown(scenarios: list[dict[str, Any]]) -> str` — Markdown catalog table + scenario descriptions & tactical themes.
- `render_match_markdown(transcript: dict[str, Any]) -> str` — Readable play-by-play **Tabletop Session Transcript**:
  - Title & match metadata (`Scenario`, `Seed`, `Party Driver`, `Enemy Driver`, `Winner`, `Rounds`, `Digest`)
  - Starting Roster & Initiative Order table
  - Round headers (`## Round N`), turn blocks showing actor HP/pos, `> **Table Talk:** "..."`, `**Intent:** ...` + `*Rationale:* ...`, retry violations (if any), and authoritative dice/engine `narrative_hooks`
  - Final Combatant Status table
  - Final Quantitative Scorecard table (all 4 sub-scores + `composite_score` + key telemetry metrics)
- `render_benchmark_markdown(report: dict[str, Any]) -> str` — Ranked Markdown Leaderboard table + per-scenario win-rate & composite score breakdown.
- `render_replay_markdown(replay_result: dict[str, Any]) -> str` — Replay verification report showing `deterministic_match`, `original_digest`, `replayed_digest`, and any `diffs`.

### 6.3 Package Public Exports (`src/srd2024/arena/__init__.py`)

Re-exports:
- `BUILTIN_SCENARIO_IDS`, `list_scenarios`, `load_scenario`
- `AgentDecision`, `AgentDriver`, `RandomDriver`, `GreedyDriver`, `HeuristicTacticalDriver`, `ReplayDriver`, `OpenAICompatDriver`, `AnthropicDriver`, `GeminiDriver`, `extract_decision_json`, `create_driver`
- `build_observation`, `run_match`, `replay_match`, `run_benchmark`, `compute_transcript_digest`
- `score_match`
- `render_scenarios_markdown`, `render_match_markdown`, `render_benchmark_markdown`, `render_replay_markdown`

---

## 7. Additive Agent Surfaces (`arena-surfaces`)

### 7.1 CLI Parity: `bin/srd-arena` AND `bin/srd arena`

Create `src/srd2024/runtime/arena_bridge.py` (lazy-importing `srd2024.arena` and `srd2024.engine.store.RulesStore`, respecting Lock E!) and executable wrapper `bin/srd-arena` (`0755`, `#!/usr/bin/env python3`).

Both **`bin/srd-arena <subcommand>`** and **`bin/srd arena <subcommand>`** MUST support the exact same 4 subcommands and flags with **1:1 JSON parity**:

| Subcommand | Flags | Output (`--format markdown\|json`) |
|---|---|---|
| `scenarios` | `[--rules <path>] [--format markdown\|json]` | Lists all 3 built-in scenarios with tier, level, roster, and tactical themes. |
| `run <scenario_or_json>` | `[--party-agent <spec>]` (default `heuristic-tactical`), `[--enemy-agent <spec>]` (default `heuristic-tactical`), `[--seed <int>]` (default `42`), `[--max-rounds <int>]`, `[--max-actions-per-turn <int>]` (default `6`), `[--max-retries <int>]` (default `2`), `[--api-base <url>]`, `[--api-key-env <var>]`, `[--enemy-api-base <url>]`, `[--enemy-api-key-env <var>]`, `[--temperature <float>]` (default `0.0`), `[--out-trace <path>]`, `[--rules <path>]`, `[--format markdown\|json]` | Runs a match, writes full `MatchTranscript` JSON to `--out-trace` if provided, prints Tabletop Session Transcript (Markdown) or full `MatchTranscript` JSON. |
| `bench` | `[--scenarios s1,s2,...]` (default all 3), `[--agents a1,a2,...]` (default `heuristic-tactical,greedy,random`), `[--enemy-agent <spec>]` (default `heuristic-tactical`), `[--seeds 1,2,3]` (default `1,2,3`), `[--max-rounds <int>]`, `[--api-base <url>]`, `[--api-key-env <var>]`, `[--out-json <path>]`, `[--rules <path>]`, `[--format markdown\|json]` | Runs multi-seed/multi-scenario benchmark, writes JSON report to `--out-json` if provided, prints Leaderboard (Markdown) or `BenchmarkReport` JSON. |
| `replay <trace.json>` | `[--rules <path>] [--format markdown\|json]` | Loads saved `MatchTranscript` JSON, runs `replay_match()`, exits `0` if `deterministic_match` else `1`, prints Replay Report (Markdown) or JSON. |

`pyproject.toml` additive script entry (preserving all 5 existing entries verbatim):
```toml
srd-arena = "srd2024.runtime.arena_bridge:main_arena"
```

### 7.2 MCP Strategy (Strict Preservation of BOTH Lock C [5-tool] and Lock D [8-tool])

In `src/srd2024/runtime/mcp.py`:
- Add `ENV_ARENA_VAR = "SRD2024_ARENA"` and CLI flag `--arena` on `srd-mcp` / `srd-engine-mcp` (`expose_arena_tools: bool = False`).
- Define `STAGE3_TOOL_DEFINITIONS` (3 tools):
  1. **`srd_arena_scenarios`**: `{format?: "markdown"|"json" = "markdown"}`
  2. **`srd_arena_run`**: `{scenario: str, party_agent?: str = "heuristic-tactical", enemy_agent?: str = "heuristic-tactical", seed?: int = 42, max_rounds?: int, format?: "markdown"|"json" = "markdown"}`
  3. **`srd_arena_bench`**: `{scenarios?: array[str], agents?: array[str], enemy_agent?: str = "heuristic-tactical", seeds?: array[int] = [1, 2, 3], max_rounds?: int, format?: "markdown"|"json" = "markdown"}`
- Define `STAGE3_STATIC_RESOURCES` (1 resource):
  - `srd://arena/scenarios` (`application/json`) — metadata list of built-in scenarios.
- **Discovery Matrix (Locks C & D 100% intact):**
  - `bin/srd-mcp` (default): `tools/list` = **5 tools** (Stage 1); `resources/list` = `srd://meta`.
  - `bin/srd-mcp --engine` / `SRD2024_ENGINE=1` / `bin/srd-engine-mcp`: `tools/list` = **8 tools** (Stage 1 + Stage 2); `resources/list` = `srd://meta`, `srd://rules/coverage`.
  - `bin/srd-mcp --arena` / `SRD2024_ARENA=1` / `bin/srd-engine-mcp --arena`: `tools/list` = **11 tools** (5 Stage 1 + 3 Stage 2 + 3 Stage 3); `resources/list` = `srd://meta`, `srd://rules/coverage`, `srd://arena/scenarios`.
  - `tools/call` dispatches all 11 tools on every launcher mode, and `resources/read` serves `srd://arena/scenarios` on every launcher mode.

### 7.3 Skill & README Additive Documentation
- `skill/SKILL.md`: Preserve YAML frontmatter (`name: srd-2024`), all Stage 1 and Stage 2 sections, and verbatim CC-BY-4.0 attribution. Append a concise **Stage 3: Multi-Agent Tabletop & Eval Arena** section documenting `bin/srd-arena`, `bin/srd arena`, scenarios, offline & stdlib LLM drivers, scoring dimensions, deterministic trace replay, and `bin/srd-mcp --arena`.
- `README.md`: Preserve all Stage 1 and Stage 2 sections and CC-BY-4.0 attribution. Append a **Stage 3 Quickstart: Multi-Agent Tabletop & Model Eval Arena** section with examples for `bin/srd-arena scenarios`, `run`, `bench`, `replay`, live OpenAI-compatible/Anthropic/Gemini usage, and updated architecture diagram. Zero third-party trademarks in new prose.

---

## 8. Concrete Verification Acceptance Criteria (`arena-verifier`)

`arena-verifier` writes 4 independent `unittest` suites runnable alongside all 94 existing tests via:
```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
**ALL 94 existing Stage 1 + Stage 2 tests MUST continue to pass with ZERO edits**, plus:

### 8.1 `tests/test_stage3_scenarios_and_scoring.py`
1. **Built-in Scenarios Verification:** `list_scenarios()` returns `goblin-ambush`, `crypt-of-ghouls`, `wyrmling-lair`; every PC in all 3 scenarios compiles via `compile_character()` with `valid == True`, `errors == []`, `warnings == []`; every monster exists in `dist/rules.json` with `coverage == "full"`; `load_scenario()` produces valid `start_encounter` payloads.
2. **Custom Scenario JSON Loading:** Loading a valid temporary custom scenario JSON file succeeds and starts an encounter; invalid PC specs, unknown monster slugs, or missing combatants raise `ValueError`.
3. **Quantitative Scorer Unit Tests:** Deterministic synthetic transcripts verify:
   - `outcome_score` cleanly ranks victory > draw > defeat and rewards survival + remaining HP + round efficiency.
   - `rules_legality_score` equals `100.0` with 0 violations and strictly decreases on violations & forced fallbacks.
   - `tactical_score` penalizes `modifier == "immune"` wasted damage hits (`-12.5` each) and rewards effective damage, productive turns, masteries, buffs, and revivals.
   - `coordination_score` penalizes friendly-fire incidents and rewards party `table_talk`, focus-fire, revivals/heals, and ally buffs.
   - All sub-scores and `composite_score` remain within `[0.0, 100.0]` and satisfy `0.35*outcome + 0.25*legality + 0.25*tactical + 0.15*coord`.

### 8.2 `tests/test_stage3_drivers_and_llm.py`
1. **JSON Decision Extractor (`extract_decision_json`):** Parses raw JSON, `` ```json ... ``` `` fenced blocks, generic fenced blocks, and prose-wrapped JSON; raises `ValueError` on non-JSON or missing `intent.type`.
2. **Offline Drivers:**
   - `RandomDriver` is 100% deterministic for a fixed seed and produces valid `AgentDecision` objects.
   - `GreedyDriver` chooses highest raw damage (e.g., picks `fireball` against fire-immune `red-dragon-wyrmling` when available!).
   - `HeuristicTacticalDriver` inspects `RulesStore` immunities and **refuses** fire spells against fire-immune `red-dragon-wyrmling` (pivots to `lightning-bolt` / `shatter` / `magic-missile` / `ray-of-frost`), revives 0-HP allies with `healing-word`, casts `bless` / `shield` / `mage-armor`, and emits `table_talk`.
   - `ReplayDriver` replays recorded decisions verbatim and raises cleanly if exhausted.
3. **Mocked HTTP Tests for ALL 3 Live LLM Drivers (`openai-compat`, `anthropic`, `gemini`):**
   - Spins up a real stdlib `http.server.HTTPServer` on `127.0.0.1:0` (zero external network!) and exercises `urllib.request` end-to-end for:
     - `OpenAICompatDriver`: verifies `POST /chat/completions`, `Authorization: Bearer <key>`, request JSON, markdown-fenced JSON parsing, and token usage extraction.
     - `AnthropicDriver`: verifies `POST /v1/messages`, `x-api-key` + `anthropic-version: 2023-06-01`, request JSON, content block parsing, and usage mapping.
     - `GeminiDriver`: verifies `POST /v1beta/models/<model>:generateContent?key=<key>`, `system_instruction` + `contents` JSON, candidate text parsing, and `usageMetadata` mapping.
   - Verifies clean error handling on HTTP 500 and invalid/non-JSON model responses.

### 8.3 `tests/test_stage3_arena_runner.py`
1. **Partial Observability Enforcement:** `build_observation()` exposes full `self` sheet and same-side `party_blackboard`, and NEVER exposes enemy exact `hp`, `hp_max`, `ac`, private `spellcasting`, or opposing-side `table_talk`.
2. **Invalid Intent Retry & Forced Fallback:**
   - A faulty driver that emits 1 illegal intent then 1 legal intent records `1` violation, receives structured `retry_feedback`, succeeds on attempt 2, and lowers `rules_legality_score < 100.0`.
   - A persistently broken driver exhausts `max_retries_per_action` and cleanly falls back to `end_turn` (`forced_fallbacks >= 1`) without crashing.
3. **100% Deterministic Trace Replay:** Running `run_match` on `goblin-ambush` and `wyrmling-lair`, serializing to JSON, reloading, and calling `replay_match(transcript, rules)` asserts `deterministic_match == True`, `diffs == []`, and `original_digest == replayed_digest`.
4. **Empirical Tactical Superiority Benchmark:** Running `run_benchmark` across `["goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"]` with `seeds=[1, 2, 3]` comparing `["heuristic-tactical", "greedy", "random"]` against `enemy_agent="heuristic-tactical"` asserts:
   - `heuristic-tactical` strictly outperforms `random` on `mean_composite_score` (and `win_rate` / `mean_tactical_score`).
   - `heuristic-tactical` incurs **0** `wasted_immune_hits` across `wyrmling-lair` seeds, while `greedy` incurs `>= 1` `wasted_immune_hits` (and `heuristic-tactical` beats `greedy` on `mean_tactical_score` and `mean_composite_score`).
   - All `heuristic-tactical` matches achieve `rules_legality_score == 100.0` (0 violations).
5. **Renderers:** `render_match_markdown` includes scenario title, round headers, table talk, narrative hooks, and final scorecard; `render_benchmark_markdown` includes ranked leaderboard table.

### 8.4 `tests/test_stage3_surfaces.py`
1. **Packaging & Executable:** `bin/srd-arena` exists, is executable (`+x`), has `python3` shebang; `pyproject.toml` preserves all 5 existing entries + adds `srd-arena`.
2. **CLI Parity (`bin/srd-arena` vs `bin/srd arena`):** `scenarios`, `run goblin-ambush --seed 7 --out-trace <tmp>`, `replay <tmp>`, and `bench --scenarios goblin-ambush --agents heuristic-tactical,random --seeds 1,2` succeed (exit 0) in BOTH `markdown` and `json` formats, and `bin/srd arena ...` produces 1:1 identical JSON output to `bin/srd-arena ...`.
3. **MCP Stage 3 Discovery & Lock Preservation:**
   - Default `bin/srd-mcp` still returns exact 5 tools in `tools/list` (Lock C).
   - `bin/srd-engine-mcp` and `bin/srd-mcp --engine` still return exact 8 tools in `tools/list` (Lock D).
   - `bin/srd-mcp --arena` and `SRD2024_ARENA=1 bin/srd-mcp` advertise all **11 tools** + `srd://arena/scenarios` resource, and `tools/call` for `srd_arena_scenarios`, `srd_arena_run`, `srd_arena_bench` returns 1:1 JSON parity with CLI.
4. **Boundary Hygiene & Stdlib-Only Check:** `src/srd2024/arena/**`, `src/srd2024/runtime/**`, and `src/srd2024/engine/**` NEVER reference `srd2024.compiler` or `srd2024.rules_compiler`.
5. **Docs & Attribution:** `skill/SKILL.md` and `README.md` document Stage 3 (`srd-arena`, `srd arena`, `--arena`) while preserving all Stage 1+2 checks and verbatim CC-BY-4.0 attribution.
