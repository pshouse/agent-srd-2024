# Milestone 6 (M6.3 & M6.4): Core 2024 Class Features, Multi-Encounter Adventuring Day & Web Encounter Builder — Architecture & Contract Specification

> **Status:** FINAL (empirically audited against `data/raw/2024/en/` — SRD 5.2 / `5e-database v7.10.0`, `dist/rules.json`, `src/srd2024/engine/{character,combat}.py`, `src/srd2024/arena/{scenarios,runner,session,scoring,human,web_server}.py`, `scripts/audit_arena_trace.py`, `docs/adr/0001-plain-js-no-build-frontend.md`, and `docs/adr/0002-pages-deploy-gates.md`).
>
> **Wave 1 Alignment:** Assumes Wave 1 (`feat/m6-bounds-terrain`: battlefield `bounds` + `terrain: {walls, difficult_terrain, cover}` + `ruined-keep`; `fix/m6-replay-audit`: `#66` replay provenance + `#83` tabletop `clickNote`) merges to `main` before Wave 2 executes.

---

## 1. Scope, Non-Negotiable Constraints & Compatibility Locks

Milestone 6 Wave 2 extends the deterministic **5e 2024 (SRD 5.2)** rules engine, arena runner, and local browser tabletop (`bin/srd-web`) across three decoupled workstreams:

1. **M6.3A — Core 2024 Class Features (`m6-class-features`):** Exact SRD 5.2 mechanics for Fighter (`Second Wind`, `Action Surge`), Rogue (`Sneak Attack` incl. Opportunity Attacks), Paladin (`Lay on Hands`), and Cleric — Life Domain (`Channel Divinity: Preserve Life`, `#34` `Disciple of Life`, `Blessed Healer`, plus honest subclass coverage grading).
2. **M6.3B — Multi-Encounter Adventuring Day & Short Rests (`m6-adventuring-day`):** 3-room linked dungeons with full party state carryover, deterministic 2024 Short Rests (Hit Dice + Short-Rest resource recovery incl. Wizard Arcane Recovery), multi-room `dungeon_trace` format, resource-conservation scoring, and CLI/web support.
3. **M6.4 — Custom Party & Encounter Builder in `bin/srd-web` (`m6-web-builder`):** Local `127.0.0.1` HTTP API + plain-JS UI (strict CSP, zero npm deps) to assemble 1–6 PCs (levels 1–10), pick bounded/terrain maps or custom room presets, browse/filter all 341 SRD monsters with 2024 XP budgets, enforce combatant caps (`#21`), and launch live single-encounter or 3-room dungeon play.

### Hard Constraints & Existing Test Locks

| Lock | Existing Verification | Wave 2 Rule |
|---|---|---|
| **Stdlib & Zero-Build** | `pyproject.toml` `dependencies = []`; ADR 0001 (`web/` plain ES modules, strict CSP, no inline `<script>` or `style=`) | Strictly Python 3.11+ stdlib and hand-written browser ES modules. Never use `innerHTML` or inline styles. |
| **Trademark Hygiene** | Project-wide rule (`docs/STAGE3-SPEC.md` §1) | Always write **"5e 2024 (SRD 5.2)"** in code, docs, CLI help, and UI copy. |
| **Stage 1 & 2 Build Parity** | `tests/test_build_and_schema.py`, `tests/test_stage2_rules_compiler.py:189` (`summary["total_compiled"] == 885`) | `bin/srd-rules-build` must remain byte-identical across rebuilds. Keep `summary["total_compiled"] == 885` and existing 5 `by_domain` keys intact; add `#34` subclass grading additively via `character_creation.subclasses[*].coverage` and `summary["subclass_coverage"]`. |
| **Committed Replay Parity** | `tests/test_stage3_arena_integrity.py:769` (`TestCommittedTracesReplay`) | All 5 committed traces in `examples/sessions/*.trace.json` MUST keep 100% identical SHA-256 digests under `replay_match()`. Gated cleanly by `state_shape_version` (§2.2). |
| **Pages Deploy Allowlist** | ADR 0002, `scripts/site_files.py`, `bash scripts/check_site.sh` | `web/tabletop/*` is served only by local `bin/srd-web` (never added to GitHub Pages `ALLOWLIST`). `bash scripts/check_site.sh` MUST exit 0. |
| **Loopback Security** | `tests/test_stage5_web_tabletop.py` | `bin/srd-web` binds strictly to `127.0.0.1`, requires launch bearer token + `Host`/`Origin`/`Sec-Fetch-Site` checks + JSON-only bounded bodies + strict field allowlisting on every endpoint. |
| **Default CLI Parity** | `tests/test_stage5_web_tabletop.py` | Launching `bin/srd-web <scenario>` or `bin/srd-web --resume <ckpt>` preserves exact existing auto-start behavior; builder lobby mode activates only when `--builder` is passed or neither scenario nor `--resume` is given. |

---

## 2. M6.3A — Core 2024 Class Features (SRD 5.2 Exact Mechanics)

### 2.1 Empirical SRD 5.2 Rules Audit (Levels 1–10)

Every mechanic below was verified verbatim against `data/raw/2024/en/5e-SRD-Features.json` and `5e-SRD-Levels.json`.

#### A. Fighter — Second Wind (`fighter-second-wind`, L1+) & Tactical Shift (`fighter-tactical-shift`, L5+)
- **Verbatim SRD 5.2 (`fighter-second-wind`):**
  > *"As a Bonus Action, you can use it to regain Hit Points equal to 1d10 plus your Fighter level. You can use this feature twice. You regain one expended use when you finish a Short Rest, and you regain all expended uses when you finish a Long Rest. When you reach certain Fighter levels, you gain more uses of this feature, as shown in the Second Wind column of the Fighter Features table."*
- **Verbatim SRD 5.2 (`fighter-tactical-shift`, L5+):**
  > *"Whenever you activate your Second Wind with a Bonus Action, you can move up to half your Speed without provoking Opportunity Attacks."*
- **Verified Level Scaling (`5e-SRD-Levels.json` → `class_specific.second_wind_uses`):**

| Fighter Level | Uses (`max`) | Healing Formula | Recharge | Tactical Shift (L5+) |
|---:|---:|---|---|---|
| 1–3 | 2 | `1d10 + level` | +1 on Short Rest; all on Long Rest | No |
| 4 | 3 | `1d10 + level` | +1 on Short Rest; all on Long Rest | No |
| 5–9 | 3 | `1d10 + level` | +1 on Short Rest; all on Long Rest | Yes (optional free move `<= speed // 2`, no OA) |
| 10 (–20) | 4 | `1d10 + level` | +1 on Short Rest; all on Long Rest | Yes (optional free move `<= speed // 2`, no OA) |

- **Engine Rules:**
  - Consumes `turn_resources["bonus_action"]` and decrements `class_resources["second_wind"]["remaining"]` by `1`.
  - Self-only; requires `alive == True`, `hp > 0`, not Incapacitated.
  - Rolls `1d10` via `rng.roll_dice_expr("1d10", crit=False)` + `int(actor["level"])`; caps HP at `hp_max`.
  - Optional `move_to: {"x": int, "y": int}`: legal ONLY when `"fighter-tactical-shift"` is in `actor["features"]` (Fighter L5+), `effective_speed > 0`, destination respects `bounds`/walls/occupancy, and path cost (including `difficult_terrain` / Prone crawling) `<= effective_speed // 2`. Does NOT consume `movement_remaining_ft` and does NOT call `_opportunity_attacks()`.

#### B. Fighter — Action Surge (`fighter-action-surge`, L2+)
- **Verbatim SRD 5.2 (`fighter-action-surge`):**
  > *"On your turn, you can take one additional action, except the Magic action. Once you use this feature, you can't do so again until you finish a Short or Long Rest. Starting at level 17, you can use it twice before a rest but only once on a turn."*
- **Verified Level Scaling:**

| Fighter Level | Uses (`max`) | Turn Limit | Recharge | 2024 Restriction |
|---:|---:|---|---|---|
| 1 | 0 | — | — | — |
| 2–10 (–16) | 1 | 1/turn | All on Short or Long Rest | **Cannot be used for the Magic action** |
| 17–20 | 2 | 1/turn | All on Short or Long Rest | **Cannot be used for the Magic action** |

- **Engine Rules (Order-Independent Action Economy):**
  - Free activation on the Fighter's own turn (consumes neither Action nor Bonus Action).
  - Requires `alive == True`, not Incapacitated, `class_resources["action_surge"]["remaining"] >= 1`, and `turn_resources["action_surge_used_this_turn"] is False`.
  - Sets `turn_resources["action_surge_used_this_turn"] = True`, `turn_resources["surge_action"] = True`, and decrements `class_resources["action_surge"]["remaining"]` by `1`.
  - **Action consumption priority:**
    - Non-Magic actions (`attack`, `dodge`, `dash`, `disengage`, `help`): consume `turn_resources["action"]` first if `True`; otherwise consume `turn_resources["surge_action"]` if `True`. When `attack` consumes `surge_action`, it resets `turn_resources["attacks_remaining"] = max(0, extra_attacks - 1)` (granting a full second Extra Attack sequence!).
    - **Magic actions** (`cast_spell` where `action_type == "action"`, and `class_feature` where `feature == "preserve_life"`): ONLY consume `turn_resources["action"]`. If `turn_resources["action"]` is `False` and `turn_resources["surge_action"]` is `True`, return `status: "invalid_intent"` with error `"Action Surge cannot be used to take the Magic action (5e 2024 SRD 5.2)."`
  - Preserving `action` and `surge_action` separately guarantees that activating Action Surge *before* casting a spell still allows 1 Magic action + 1 non-Magic action, never 2 Magic actions!

#### C. Rogue — Sneak Attack (`rogue-sneak-attack`, L1+)
- **Verbatim SRD 5.2 (`rogue-sneak-attack`):**
  > *"Once per turn, you can deal an extra 1d6 damage to one creature you hit with an attack roll if you have Advantage on the roll and the attack uses a Finesse or a Ranged weapon. The extra damage's type is the same as the weapon's type. You don't need Advantage on the attack roll if at least one of your allies is within 5 feet of the target, the ally doesn't have the Incapacitated condition, and you don't have Disadvantage on the attack roll. The extra damage increases as you gain Rogue levels, as shown in the Sneak Attack column of the Rogue Features table."*
- **Verified Level Scaling (`5e-SRD-Levels.json` → `class_specific.sneak_attack` = `ceil(level / 2)d6`):**

| Rogue Level | 1–2 | 3–4 | 5–6 | 7–8 | 9–10 | Formula (L1–20) |
|---|---:|---:|---:|---:|---:|---|
| Extra Dice | `1d6` | `2d6` | `3d6` | `4d6` | `5d6` | `((level + 1) // 2)d6` |

- **Engine Rules:**
  - Passive automatic trigger inside `_resolve_single_weapon_attack()` (may be suppressed with optional `use_sneak_attack: false` on `attack` intent; defaults to `True`).
  - Applies **once per turn** (NOT once per round!) — including off-turn **Opportunity Attacks** (`_opportunity_attacks()` → `_resolve_single_weapon_attack()`), because an Opportunity Attack happens on another creature's turn!
  - Reset rule: `turn_resources["sneak_attack_used"] = False` is cleared for **every** combatant whenever a turn ends / a new turn starts in `end_turn` (and initialized to `False` on `start_encounter`).
  - Eligibility predicate (all MUST hold):
    1. `state.get("state_shape_version", 1) >= 3` (using `#94`'s `STATE_SHAPE_VERSION`).
    2. `"rogue-sneak-attack"` in `attacker.get("features", [])` (or `attacker["source_id"] == "classes:rogue"`).
    3. `hit is True` and `not attacker["turn_resources"].get("sneak_attack_used", False)`.
    4. Qualifying weapon: `"finesse"` in `weapon["properties"]` OR `weapon["weapon_range"] == "ranged"`. (Thrown non-Finesse melee weapons such as `javelin` do NOT qualify; `dagger` qualifies via `finesse`; `shortbow`/`light-crossbow` qualify via `ranged`.)
    5. Qualifying roll condition:
       - `mode == "advantage"` (`trigger = "advantage"`), **OR**
       - `mode == "normal"` (no net Disadvantage — per 5e cancellation, Advantage + Disadvantage yields `normal`, meaning neither Advantage nor Disadvantage) AND there exists at least one combatant `ally` with `ally["id"] != attacker_id`, `ally["side"] == attacker["side"]`, `ally["alive"] is True`, `int(ally["hp"]) > 0`, `not _has_modifier_flag(ally, "cannot_act", rules)`, and `distance_ft(ally["position"], target["position"]) <= 5` (`trigger = "ally_within_5ft"`).
  - Resolution:
    - Roll `rng.roll_dice_expr(f"{dice_count}d6", crit=crit)` — **critical hits double Sneak Attack dice**!
    - Set `attacker["turn_resources"]["sneak_attack_used"] = True`.
    - Emit `sneak_attack_triggered` BEFORE `damage_applied` (`actor_id`, `target_id`, `weapon`, `dice`, `rolls`, `damage`, `damage_type`, `crit`, `trigger`, plus `reaction: True` automatically via `tracker.in_reaction` when on an Opportunity Attack).
    - **Resistance math invariant:** Add `sa_damage` to `raw_dmg` of the weapon's single damage part (`dmg_type`) BEFORE calling `_apply_damage_parts()`, so damage resistance halves the combined total once (avoiding double-flooring).

#### D. Paladin — Lay on Hands (`paladin-lay-on-hands`, L1+)
- **Verbatim SRD 5.2 (`paladin-lay-on-hands`):**
  > *"Your blessed touch can heal wounds. You have a pool of healing power that replenishes when you finish a Long Rest. With that pool, you can restore a total number of Hit Points equal to five times your Paladin level. As a Bonus Action, you can touch a creature (which could be yourself) and draw power from the pool of healing to restore a number of Hit Points to that creature, up to the maximum amount remaining in the pool. You can also expend 5 Hit Points from the pool of healing power to remove the Poisoned condition from the creature; those points don't also restore Hit Points to the creature."*
- **SRD 5.2 RAW Ruling (No Undead/Construct Exclusion):**
  - Verbatim 2024 SRD 5.2 removed the 2014 *"no effect on undead and constructs"* restriction from `paladin-lay-on-hands` (matching 2024 `cure-wounds` and `healing-word`). Per Patrick's ruling, Lay on Hands follows **SRD 5.2 RAW** with **no Undead or Construct exclusion**.
- **Verified Level Scaling (L1–10):**
  - Pool `max = 5 * paladin_level` (L1: 5, L2: 10, L3: 15, L4: 20, L5: 25, L6: 30, L7: 35, L8: 40, L9: 45, L10: 50).
  - Recharge: **Long Rest only** (does NOT recharge on Short Rest).
- **Engine Rules:**
  - Action economy: **Bonus Action** (`turn_resources["bonus_action"] = False`).
  - Range: **Touch** (`distance_ft(actor["position"], target["position"]) <= 5`; may target self or ally).
  - Target validation: `target["alive"] is True` (refuses dead creatures; any living creature type is valid per SRD 5.2 RAW).
  - Modes:
    - `mode == "heal"` (default): `amount` must be an integer with `1 <= amount <= pool_remaining`. Deducts `amount` from pool; caps target HP at `hp_max`; revives 0-HP living unconscious PC (removes `unconscious`, leaves `prone`, resets `death_saves`).
    - `mode == "cure_poison"`: requires `pool_remaining >= 5` and `"poisoned"` in `_direct_condition_slugs(target)`. Deducts `5` from pool, removes `"poisoned"`, restores `0` HP.

#### E. Cleric (Life Domain) — Channel Divinity: Preserve Life (`life-preserve-life`, L3+), Disciple of Life (`#34`, L3+) & Blessed Healer (L6+)
- **Verbatim SRD 5.2 (`cleric-channel-divinity`, L2+):**
  > *"You can use this class's Channel Divinity twice. You regain one of its expended uses when you finish a Short Rest, and you regain all expended uses when you finish a Long Rest. You gain additional uses when you reach certain Cleric levels, as shown in the Channel Divinity column of the Cleric Features table."*
- **Verified Channel Divinity Scaling (`5e-SRD-Levels.json` → `class_specific.channel_divinity_charges`):**

| Cleric Level | Channel Divinity `max` | Recharge | Preserve Life Pool (`5 * level`) |
|---:|---:|---|---:|
| 1 | 0 | — | — |
| 2 | 2 | +1 Short Rest; all Long Rest | — (subclass at L3) |
| 3–5 | 2 | +1 Short Rest; all Long Rest | 15 / 20 / 25 HP |
| 6–10 (–17) | 3 | +1 Short Rest; all Long Rest | 30 / 35 / 40 / 45 / 50 HP |

- **Verbatim SRD 5.2 (`life-preserve-life`, L3+):**
  > *"As a Magic action, you present your Holy Symbol and expend a use of your Channel Divinity to evoke healing energy that can restore a number of Hit Points equal to five times your Cleric level. Choose Bloodied creatures within 30 feet of yourself (which can include you), and divide those Hit Points among them. This feature can restore a creature to no more than half its Hit Point maximum."*
  - **Engine Rules:**
    - Consumes `turn_resources["action"]` ONLY (**Magic action** — blocked by Action Surge!).
    - Requires `"life-preserve-life"` in `actor["features"]` and `class_resources["channel_divinity"]["remaining"] >= 1`; decrements by `1`.
    - **Bloodied definition (2024 SRD 5.2):** `target["alive"] is True` and `int(target["hp"]) <= int(target["hp_max"]) // 2`.
    - Maximum healable per target: `cap = max(0, (int(target["hp_max"]) // 2) - int(target["hp"]))`.
    - Range: every target must satisfy `distance_ft(actor["position"], target["position"]) <= 30` (and Line of Sight if `terrain` walls exist).
    - Pool: `5 * int(actor["level"])`.
    - Allocation input:
      1. Explicit `allocations: {target_id: int, ...}`: every value must be an integer `>= 1`, `<= cap` for that target, and `sum(allocations.values()) <= 5 * level`.
      2. Convenience `target_ids: [str, ...]` (when `allocations` omitted): deterministic greedy split in order of `(hp / hp_max ASC, id ASC)`, filling each target up to its `cap` until the pool is exhausted (rejects if total distributed `== 0`).
    - Revives 0-HP living unconscious PCs that receive `>= 1` HP (removes `unconscious`, leaves `prone`, resets `death_saves`). Does NOT trigger Disciple of Life or Blessed Healer (not a spell cast with a spell slot).

- **Verbatim SRD 5.2 (`#34` `life-disciple-of-life`, L3+):**
  > *"When a spell you cast with a spell slot restores Hit Points to a creature, that creature regains additional Hit Points on the turn you cast the spell. The additional Hit Points equal 2 plus the spell slot's level."*
  - **Engine Rules:**
    - Fires inside `cast_spell` healing loop when `state.get("state_shape_version", 1) >= 3`, `"life-disciple-of-life"` in `actor.get("features", [])`, `slot_level >= 1`, `target["alive"] is True`, and base `heal_amt > 0`.
    - Adds `bonus = 2 + slot_level` to `heal_amt` for each healed living target (`cure-wounds`, `healing-word`, `mass-healing-word`, `mass-cure-wounds`, `heal`, `aid` HP restoration, etc.).
    - Emits `disciple_of_life_bonus: bonus` on each `healing_applied` event and appends ` (+{bonus} Disciple of Life)` to the narrative hook.

- **Verbatim SRD 5.2 (`life-blessed-healer`, L6+):**
  > *"Immediately after you cast a spell with a spell slot that restores Hit Points to one or more creatures other than yourself, you regain Hit Points equal to 2 plus the spell slot's level."*
  - **Engine Rules:**
    - Fires immediately after the `cast_spell` healing loop when `state.get("state_shape_version", 1) >= 3`, `"life-blessed-healer"` in `actor.get("features", [])`, `slot_level >= 1`, `actor["alive"] is True`, and at least one living target `tid != actor_id` received `> 0` HP.
    - Restores `2 + slot_level` HP to `actor_id` (capped at `hp_max`), emitting a `healing_applied` event with `feature="blessed_healer"`, `spell=spell_slug`, `slot_level=slot_level`.

#### F. Honest Subclass Coverage Grading (`#34`)
- Compiled in `src/srd2024/rules_compiler/character.py` (`_compile_subclasses`) and summarized in `src/srd2024/rules_compiler/build.py`:
  - Each of the 12 subclasses in `dist/rules.json` → `character_creation.subclasses[slug]` receives:
    ```json
    {
      "coverage": "partial",
      "provenance": {
        "source_id": "subclasses:life-domain",
        "source_file": "5e-SRD-Subclasses.json",
        "parser": "structured",
        "modeled_features": [
          "life-disciple-of-life",
          "life-preserve-life",
          "life-blessed-healer"
        ],
        "unmodeled_clauses": [
          "feature:life-domain-spells",
          "feature:life-supreme-healing"
        ]
      }
    }
    ```
  - **Exact grades across all 12 SRD subclasses after M6.3A:**
    - `life-domain`: **`"partial"`** (3 of 5 features modeled; `life-domain-spells` and L17 `life-supreme-healing` honestly listed in `unmodeled_clauses`).
    - All other 11 subclasses (`champion`, `circle-of-the-land`, `college-of-lore`, `draconic-sorcery`, `evoker`, `fiend-patron`, `hunter`, `oath-of-devotion`, `path-of-the-berserker`, `thief`, `warrior-of-the-open-hand`): **`"narrative"`**, with every subclass feature slug listed as `"feature:<slug>"` in `unmodeled_clauses`.
  - **Stage 2 Lock Preservation:** `summary["total_compiled"]` remains `885` (`8 + 15 + 182 + 339 + 341`). Add a separate additive block `summary["subclass_coverage"] = {"total": 12, "full": 0, "partial": 1, "narrative": 11, "executable_pct": 8.3}` and `entities["subclasses"]` ONLY under a new top-level key `subclass_entities` in `dist/web/rules-coverage.json` (never altering `entities` keys or `by_domain` sums tested by Stage 2).

---

### 2.2 State Schema Changes & `STATE_SHAPE_VERSION` Contract

#### Top-Level `EncounterState`
Reuse **PR #94's `STATE_SHAPE_VERSION`** constant (no separate `CURRENT_STATE_SHAPE_VERSION` constant; `#94` introduces `STATE_SHAPE_VERSION = 1` in `src/srd2024/arena/runner.py`, `#95` bumps it to `2` for `bounds`/`terrain`, and M6.3A bumps `STATE_SHAPE_VERSION` to the next integer `3`, updating `#94`'s key-set guard test in lockstep):
- `start_encounter` accepts optional `"state_shape_version": int` (defaulting to `STATE_SHAPE_VERSION`, i.e., `3` for M6.3A; legacy replays pass the recorded `state_shape_version` or `1`).
- When replaying pre-M6.3A traces (`state_shape_version < 3`, e.g., `1` or `2`):
  - `combat.py` omits all new M6.3A combatant fields (`subclass`, `features`, `hit_die`, `hit_dice`, `class_resources`), omits `surge_action` / `action_surge_used_this_turn` / `sneak_attack_used` from `turn_resources`, and skips passive Sneak Attack / Disciple of Life / Blessed Healer hooks.
  - **Guarantee:** All committed legacy traces in `examples/sessions/*.trace.json` replay to the exact existing SHA-256 digest with zero drift.
- When `state_shape_version >= 3` (default for all new M6.3A+ encounters & dungeons):
  - `new_state["state_shape_version"] = STATE_SHAPE_VERSION`.

#### Derived Statblock Additions (`src/srd2024/engine/character.py`)
`compile_character()` adds one additive field `class_resources_max` to `statblock` (preserving every existing Stage 2 field unchanged):
```json
"class_resources_max": {
  "second_wind": {"max": 3, "recharge": "short_rest_one"},
  "action_surge": {"max": 1, "recharge": "short_rest_all"}
}
```
Derived deterministically from `class_slug`, `subclass_slug`, `level`, and `levels[f"{class_slug}-{level}"]["class_specific"]`:
- Fighter: `second_wind` (`max` = `class_specific.second_wind_uses`, `recharge`: `"short_rest_one"`); if `level >= 2`: `action_surge` (`max` = `2 if level >= 17 else 1`, `recharge`: `"short_rest_all"`).
- Paladin: `lay_on_hands` (`max` = `5 * level`, `recharge`: `"long_rest"`); if `level >= 3`: `channel_divinity` (`max` = `class_specific.channel_divinity_charges`, `recharge`: `"short_rest_one"`).
- Cleric: if `level >= 2`: `channel_divinity` (`max` = `class_specific.channel_divinity_charges`, `recharge`: `"short_rest_one"`).
- Wizard: `arcane_recovery` (`max` = `1`, `budget_levels` = `(level + 1) // 2`, `max_slot_level` = `5`, `recharge`: `"long_rest"`).

#### PC Combatant State Additions (`state_shape_version >= 3`)
Inside `_build_pc_combatant(entry)`:
```json
{
  "subclass": "subclasses:life-domain",
  "features": ["cleric-channel-divinity", "life-disciple-of-life", "life-preserve-life"],
  "hit_die": 8,
  "hit_dice": {"die": 8, "max": 5, "remaining": 5},
  "slots_max": {"1": 4, "2": 3, "3": 2},
  "class_resources": {
    "channel_divinity": {"remaining": 2, "max": 2, "recharge": "short_rest_one"}
  },
  "turn_resources": {
    "action": true,
    "surge_action": false,
    "action_surge_used_this_turn": false,
    "bonus_action": true,
    "reaction": true,
    "attacks_remaining": 0,
    "cleave_used": false,
    "nick_used": false,
    "disengaged": false,
    "light_attack_made": false,
    "sneak_attack_used": false
  }
}
```
- Optional carryover overrides accepted on `start_encounter` combatant entries (used by M6.3B between dungeon rooms!):
  - `"slots"` (overrides starting `spellcasting["slots"]` while keeping `"slots_max"` from statblock)
  - `"class_resources"` (overrides starting remaining counts, clamped `0..max`)
  - `"hit_dice"` (overrides starting `remaining`, clamped `0..max`)
  - `"heroic_inspiration"` (already supported)
  - `"death_saves"` (optional override, default `{"successes": 0, "failures": 0}`)
- `_StepTracker.build_diff()` adds an additive `"resource_changes": [{"id": cid, "resource": str, "before": int, "after": int}]` list when `state_shape_version >= 3`.

---

### 2.3 Structured Intent Schemas, `legal_actions`, Events & `narrative_hooks`

#### New Intent Type: `class_feature`
Add `"class_feature"` to `combat.py:step()`, `runner.py:_ARENA_INTENT_TYPES`, and `web_server.py:_STRUCTURED_KINDS` / `_INTENT_KEYS` (`"feature"`, `"mode"`, `"amount"`, `"allocations"`, `"move_to"`).

| Feature | Exact Intent JSON | Economy | Validation Errors (`invalid_intent`) |
|---|---|---|---|
| **Second Wind** | `{"type": "class_feature", "actor_id": "pc-aldric", "feature": "second_wind", "move_to": {"x": 10, "y": 5}}` (`move_to` optional, L5+ only) | Bonus Action, 1 `second_wind` | Missing feature; no Bonus Action; 0 uses remaining; `move_to` before L5 or exceeds `speed // 2` or out of bounds/wall |
| **Action Surge** | `{"type": "class_feature", "actor_id": "pc-aldric", "feature": "action_surge"}` | Free (1/turn), 1 `action_surge` | Missing feature; already used this turn; 0 uses remaining |
| **Lay on Hands (Heal)** | `{"type": "class_feature", "actor_id": "pc-seren", "feature": "lay_on_hands", "mode": "heal", "target_id": "pc-aldric", "amount": 10}` | Bonus Action, `amount` pool | Missing feature; no Bonus Action; `amount < 1` or `> remaining`; distance `> 5` ft; dead target |
| **Lay on Hands (Cure Poison)** | `{"type": "class_feature", "actor_id": "pc-seren", "feature": "lay_on_hands", "mode": "cure_poison", "target_id": "pc-aldric"}` | Bonus Action, 5 pool | Missing feature; no Bonus Action; `remaining < 5`; distance `> 5` ft; target not `poisoned`; dead target |
| **Preserve Life** | `{"type": "class_feature", "actor_id": "pc-dorian", "feature": "preserve_life", "allocations": {"pc-aldric": 15, "pc-lyra": 10}}` *(or `"target_ids": ["pc-aldric", "pc-lyra"]`)* | **Magic Action** (no Action Surge!), 1 `channel_divinity` | Missing feature; no regular Action (explicit error if only `surge_action` left); 0 CD remaining; target `> 30` ft / no LoS / dead / not Bloodied (`hp > hp_max // 2`); allocation exceeds half-max cap or `5 * level` pool |

#### `legal_actions` Additions (`compute_legal_actions`)
When `state_shape_version >= 3`, `compute_legal_actions()` includes:
- `"class_features": list[str]` — currently usable feature slugs from `["second_wind", "action_surge", "lay_on_hands", "preserve_life"]` (checking action/bonus-action economy, remaining pool, and `action_surge_used_this_turn`).
- `"surge_action_available": bool` — `True` while `turn_resources["surge_action"]` is `True` (so drivers know non-Magic actions are still legal even when `turn_resources["action"]` is `False`).
- `"sneak_attack_available": bool` — `True` when actor has `rogue-sneak-attack` and `not turn_resources["sneak_attack_used"]`.
- `actions` includes `"attack"`, `"dodge"`, `"dash"`, `"disengage"`, `"help"` whenever `res["action"] or res.get("surge_action")`, includes `"cast_spell"` (Action) and `"class_feature"` (`preserve_life`) ONLY when `res["action"]` is `True`.
- `bonus_actions` includes `"class_feature"` whenever `second_wind` or `lay_on_hands` is usable.

#### Events & `narrative_hooks`

| Feature | Emitted Events | Exact `narrative_hooks` Template |
|---|---|---|
| **Second Wind** | `class_feature_used` (`feature="second_wind"`, `uses_left`, `dice="1d10"`, `roll`, `level_bonus`), `healing_applied` (`feature="second_wind"`, `amount`, `hp_before`, `hp_after`, `revived=False`), optional `moved` (`tactical_shift=True`, `provokes_oa=False`) | `"{name} uses Second Wind (Bonus Action), regaining {amount} HP."` (+ `" Tactical Shift: moves to ({x}, {y}) without provoking Opportunity Attacks."` if moved) |
| **Action Surge** | `class_feature_used` (`feature="action_surge"`, `uses_left`) | `"{name} activates Action Surge, gaining one extra non-Magic action this turn!"` |
| **Sneak Attack** | `sneak_attack_triggered` (`actor_id`, `target_id`, `weapon`, `dice`, `rolls`, `damage`, `damage_type`, `crit`, `trigger` `"advantage"\|"ally_within_5ft"`) | `"Sneak Attack ({dice}, {trigger_label}) adds {damage} {damage_type} damage!"` |
| **Lay on Hands** | `class_feature_used` (`feature="lay_on_hands"`, `mode`, `spent`, `pool_left`, `target_id`), plus `healing_applied` (`feature="lay_on_hands"`, ...) OR `condition_ended` (`condition="poisoned"`, `reason="lay_on_hands"`) | `"{name} uses Lay on Hands on {target}, restoring {amount} HP ({pool_left} HP left in pool)."` / `"{name} spends 5 HP of Lay on Hands to cure Poisoned on {target} ({pool_left} HP left in pool)."` |
| **Preserve Life** | `class_feature_used` (`feature="preserve_life"`, `uses_left`, `pool_max`, `total_distributed`, `allocations`), plus one `healing_applied` (`feature="preserve_life"`, ...) per healed target | `"{name} channels Preserve Life, distributing {total} HP among {target_names}."` |
| **Disciple of Life** | `disciple_of_life_bonus` field on `healing_applied` | Appends `"(+{bonus} Disciple of Life)"` onto the spell healing hook |
| **Blessed Healer** | `healing_applied` (`feature="blessed_healer"`, `spell`, `slot_level`, `amount`) | `"Blessed Healer restores {amount} HP to {name}."` |

---

### 2.4 Independent Trace Auditor Checks (`scripts/audit_arena_trace.py`)

Add 7 new independent rules (exercised automatically whenever a trace contains M6.3A events; zero regressions on legacy traces):

1. **Second Wind formula & pool:** `healing_applied(feature="second_wind")` has `amount` in `[1 + level, 10 + level]`, `source_id == target_id`, Fighter class, Bonus Action available, and uses spent `<= max_uses(level)`.
2. **Action Surge Magic-action ban & 1/turn limit:** After `class_feature_used(feature="action_surge")`, a Fighter may take at most 1 extra non-Magic Action that turn (`attack`/`dodge`/`dash`/`disengage`/`help`); a second Action-casting-time `spell_cast` or `preserve_life` on the same turn is flagged as a violation.
3. **Sneak Attack eligibility, scaling & 1/turn (incl. OA):** On `sneak_attack_triggered`, verify Rogue class, weapon is Finesse or Ranged, `damage` in `[dice_count * (2 if crit else 1), 6 * dice_count * (2 if crit else 1)]` where `dice_count == (level + 1) // 2`, either `mode == "advantage"` or (`mode == "normal"` and a living non-incapacitated ally was within 5 ft of target), and at most 1 trigger per `(round, turn_index)` (allowing a separate trigger on an off-turn Opportunity Attack!). Subtract `sneak_attack` raw damage when checking PC weapon damage bounds!
4. **Lay on Hands pool & Touch range (SRD 5.2 RAW):** On `class_feature_used(feature="lay_on_hands")`, verify Paladin class, `cheb(pos[actor], pos[target]) <= 5`, cumulative spent `<= 5 * level`, and `mode == "cure_poison"` spends exactly `5` and removes `poisoned` (no Undead/Construct exclusion per SRD 5.2 RAW).
5. **Preserve Life Bloodied & half-max cap:** On `class_feature_used(feature="preserve_life")`, verify Life Domain Cleric L3+, `cheb(pos[actor], pos[target]) <= 30`, total distributed `<= 5 * level`, every healed target started at `hp_before <= hp_max // 2`, and `hp_after <= hp_max // 2`.
6. **Disciple of Life bonus (`#34`):** When a L3+ Life Domain Cleric casts a healing spell with `slot_level >= 1`, verify `disciple_of_life_bonus == 2 + slot_level` on each living target's `healing_applied` event.
7. **Blessed Healer bonus:** When a L6+ Life Domain Cleric heals at least one living ally (`tid != actor_id`) with `slot_level >= 1`, verify a `healing_applied(feature="blessed_healer")` event for `actor_id` with `amount == 2 + slot_level`.

---

## 3. M6.3B — Multi-Encounter Adventuring Day & Short Rests

### 3.1 Built-In 3-Room Dungeon Spec (`src/srd2024/arena/dungeon.py`)

#### Schema: `DungeonSpec`
```json
{
  "id": "ashfall-keep",
  "name": "Ashfall Keep",
  "description": "Three-room Tier 2 adventuring day testing resource conservation and Short Rest recovery across bounded terrain rooms.",
  "tier": 2,
  "party_level": 5,
  "allow_short_rest_between_rooms": true,
  "party": [
    {"id": "pc-aldric", "spec": { "...CharacterBuildSpec..." }},
    {"id": "pc-kaelen", "spec": { "...CharacterBuildSpec..." }},
    {"id": "pc-seren",  "spec": { "...CharacterBuildSpec..." }},
    {"id": "pc-dorian", "spec": { "...CharacterBuildSpec..." }},
    {"id": "pc-lyra",   "spec": { "...CharacterBuildSpec..." }}
  ],
  "rooms": [
    {
      "id": "entrance-hall",
      "name": "Room 1: Entrance Hall",
      "max_rounds": 10,
      "bounds": {"min_x": 0, "min_y": 0, "max_x": 50, "max_y": 35},
      "terrain": {
        "walls": [[20, 0], [20, 5], [20, 30], [20, 35]],
        "difficult_terrain": [[15, 15], [15, 20], [20, 15], [20, 20]],
        "cover": [
          {"x": 10, "y": 10, "kind": "half"},
          {"x": 10, "y": 25, "kind": "half"},
          {"x": 30, "y": 10, "kind": "half"},
          {"x": 30, "y": 25, "kind": "half"}
        ]
      },
      "party_spawns": {
        "pc-aldric": {"x": 5, "y": 15},
        "pc-seren":  {"x": 5, "y": 20},
        "pc-kaelen": {"x": 5, "y": 10},
        "pc-dorian": {"x": 0, "y": 15},
        "pc-lyra":   {"x": 0, "y": 20}
      },
      "enemies": [
        {"id": "r1-ghoul-1",    "monster": "ghoul",    "name": "Gate Ghoul A",   "position": {"x": 35, "y": 10}},
        {"id": "r1-ghoul-2",    "monster": "ghoul",    "name": "Gate Ghoul B",   "position": {"x": 35, "y": 25}},
        {"id": "r1-skeleton-1", "monster": "skeleton", "name": "Gate Archer A",  "position": {"x": 45, "y": 10}},
        {"id": "r1-skeleton-2", "monster": "skeleton", "name": "Gate Archer B",  "position": {"x": 45, "y": 25}},
        {"id": "r1-armor-1",    "monster": "animated-armor", "name": "Gate Sentinel", "position": {"x": 30, "y": 15}}
      ]
    },
    {
      "id": "guardroom",
      "name": "Room 2: Sunken Guardroom",
      "max_rounds": 12,
      "bounds": {"min_x": 0, "min_y": 0, "max_x": 55, "max_y": 40},
      "terrain": {
        "walls": [[25, 0], [25, 5], [25, 10], [25, 30], [25, 35], [25, 40]],
        "difficult_terrain": [[20, 15], [20, 20], [20, 25], [30, 15], [30, 20], [30, 25]],
        "cover": [
          {"x": 15, "y": 15, "kind": "three_quarters"},
          {"x": 15, "y": 25, "kind": "three_quarters"},
          {"x": 35, "y": 15, "kind": "three_quarters"},
          {"x": 35, "y": 25, "kind": "three_quarters"}
        ]
      },
      "party_spawns": {
        "pc-aldric": {"x": 5, "y": 20},
        "pc-seren":  {"x": 5, "y": 15},
        "pc-kaelen": {"x": 5, "y": 25},
        "pc-dorian": {"x": 0, "y": 15},
        "pc-lyra":   {"x": 0, "y": 25}
      },
      "enemies": [
        {"id": "r2-ghast-1",    "monster": "ghast",          "name": "Guardroom Ghast",  "position": {"x": 40, "y": 20}},
        {"id": "r2-knight-1",   "monster": "knight",         "name": "Oathbound Knight", "position": {"x": 35, "y": 15}},
        {"id": "r2-ghoul-1",    "monster": "ghoul",          "name": "Barracks Ghoul A", "position": {"x": 40, "y": 10}},
        {"id": "r2-ghoul-2",    "monster": "ghoul",          "name": "Barracks Ghoul B", "position": {"x": 40, "y": 30}}
      ]
    },
    {
      "id": "boss-chamber",
      "name": "Room 3: Inner Sanctum",
      "max_rounds": 15,
      "bounds": {"min_x": 0, "min_y": 0, "max_x": 60, "max_y": 45},
      "terrain": {
        "walls": [[20, 0], [20, 5], [20, 40], [20, 45], [40, 0], [40, 5], [40, 40], [40, 45]],
        "difficult_terrain": [[35, 15], [35, 20], [35, 25], [35, 30]],
        "cover": [
          {"x": 15, "y": 15, "kind": "half"},
          {"x": 15, "y": 30, "kind": "half"},
          {"x": 30, "y": 10, "kind": "half"},
          {"x": 30, "y": 35, "kind": "half"}
        ]
      },
      "party_spawns": {
        "pc-aldric": {"x": 5, "y": 20},
        "pc-seren":  {"x": 5, "y": 25},
        "pc-kaelen": {"x": 5, "y": 15},
        "pc-dorian": {"x": 0, "y": 20},
        "pc-lyra":   {"x": 0, "y": 25}
      },
      "enemies": [
        {"id": "r3-mummy-1",    "monster": "mummy",                "name": "Sanctum Mummy",     "position": {"x": 50, "y": 20}},
        {"id": "r3-minotaur-1", "monster": "minotaur-of-baphomet", "name": "Vault Minotaur",    "position": {"x": 45, "y": 25}},
        {"id": "r3-ghast-1",    "monster": "ghast",                "name": "Sanctum Ghast",     "position": {"x": 45, "y": 15}},
        {"id": "r3-ghoul-1",    "monster": "ghoul",                "name": "Crypt Stalker A",   "position": {"x": 40, "y": 10}},
        {"id": "r3-ghoul-2",    "monster": "ghoul",                "name": "Crypt Stalker B",   "position": {"x": 40, "y": 35}}
      ]
    }
  ]
}
```
- **Empirical Verification of `ashfall-keep`:**
  - All 5 PCs (`Aldric` L5 Fighter, `Kaelen` L5 Rogue, `Seren` L5 Paladin, `Dorian` L5 Life Cleric, `Lyra` L5 Wizard) compile with **`valid == True`, `errors == []`, `warnings == []`** and exercise all 5 M6.3A/B class features (`Second Wind`, `Action Surge`, `Sneak Attack`, `Lay on Hands`, `Preserve Life` + `Disciple of Life`, and `Arcane Recovery`).
  - Every enemy slug (`ghoul`, `skeleton`, `animated-armor`, `ghast`, `knight`, `mummy`, `minotaur-of-baphomet`) has **`coverage == "full"`** in `dist/rules.json` (zero unmodeled clauses).
  - Encounter XP progression across the 3 rooms: Room 1 = `700 XP` (warmup), Room 2 = `1,550 XP` (moderate), Room 3 = `2,250 XP` (boss) — total `4,500 XP` across the adventuring day.
  - Note on `pc-seren` (`_SPEC_SEREN_L5`, verified via `compile_character` with 0 errors/warnings): Human Soldier, L5 Oath of Devotion Paladin, `background_asi: {"str": 2, "con": 1}`, `origin_feat: "savage-attacker"`, `extra_origin_feat: "alert"`, `base_abilities: {"str": 15, "dex": 10, "con": 14, "int": 8, "wis": 10, "cha": 14}`, `asi_choices: [{"level": 4, "type": "asi", "increases": {"str": 2}}]`, `fighting_style: "defense"`, `chain-mail` + shield, `longsword`/`javelin` masteries, `prepared_spells: ["divine-smite", "bless", "cure-wounds", "shield-of-faith", "aid", "shining-smite"]` → `HP 44, AC 19, d10, Slots {1: 4, 2: 2}`.

---

### 3.2 Party State Carryover & Deterministic Short Rest (`resolve_short_rest`)

#### Party Carryover Rules (Room *N* → Intermission → Room *N+1*)

| Field | Carryover Behavior |
|---|---|
| `hp` | Carried over verbatim. Living 0-HP PCs stabilize on victory (`death_saves = {0, 0}`); if healed to `>= 1` during Short Rest they stand and lose `unconscious`/`prone`; if still `0 HP` entering Room *N+1*, they enter Unconscious & Prone unless revived. |
| `alive` | Dead PCs (`alive == False`) remain dead and are omitted from Room *N+1* `combatants`. If 0 conscious living PCs remain (or Room *N* ended in `"enemy"` or `"draw"`), the dungeon ends immediately without entering Room *N+1*. |
| `death_saves` | Reset to `{"successes": 0, "failures": 0}` on victory stabilization for all living PCs. |
| `spellcasting.slots` | Exact remaining slots carried over; `slots_max` preserved from initial compiled statblock. |
| `class_resources` | Exact remaining uses/pools carried over (`second_wind`, `action_surge`, `lay_on_hands`, `channel_divinity`, `arcane_recovery`). |
| `hit_dice` | `{"die": hit_die, "max": level, "remaining": int}` carried over. |
| `heroic_inspiration` | Carried over unchanged (`bool`). |
| `exhaustion_level` | Carried over unchanged (`int`). |
| `temp_hp` | Reset to `0` on Short Rest (1-hour duration expires); carried over if Short Rest is skipped. |
| `concentration` & `active_effects` | Cleared to `None` / `[]` between rooms (combat durations expire). |
| `conditions` | Combat-duration conditions (`prone`, `grappled`, `restrained`, `frightened`, `paralyzed`, `stunned`, `blinded`, `incapacitated`) cleared between rooms for conscious PCs; `poisoned` persists unless cured (e.g. Lay on Hands); `unconscious` + `prone` persist if still at `0 HP`. |

#### Deterministic Short Rest Contract (`resolve_short_rest`)
```python
def resolve_short_rest(
    party_carryover: dict[str, dict[str, Any]],
    *,
    rng: DeterministicRNG,
    plan: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Apply a 5e 2024 (SRD 5.2) Short Rest purely and deterministically.

    `plan` (optional per-PC overrides for human/UI play):
      {
        "pc-aldric": {"hit_dice_to_spend": 2},
        "pc-lyra":   {"hit_dice_to_spend": 1, "arcane_recovery_slots": {"2": 1, "1": 1}}
      }
    When `plan` is None, uses the deterministic `auto_short_rest_plan()`.
    Returns `(updated_carryover, short_rest_record)`.
    """
```
Exact 2024 SRD 5.2 recovery rules executed per living PC:
1. **Hit Dice:** Spend `k` dice (`0 <= k <= hit_dice["remaining"]`) one at a time. Each die rolls `r = rng.roll_die(hit_die)` and restores `max(1, r + con_mod)` HP (capped at `hp_max`). Stop early if `hp == hp_max` (do not waste extra dice). If `hp_before == 0` and `hp_after > 0`, remove `unconscious` and `prone`.
   - **Auto policy (`auto_short_rest_plan`):** Spend Hit Dice one at a time while `hp < hp_max` and (`hp == 0` or `hp_max - hp >= max(1, hit_die // 2 + 1 + con_mod)` or `hp * 4 <= hp_max * 3`).
2. **Short-Rest Resource Recovery:**
   - `recharge == "short_rest_one"` (`second_wind`, `channel_divinity`): `remaining = min(max, remaining + 1)`.
   - `recharge == "short_rest_all"` (`action_surge`): `remaining = max`.
   - `recharge == "long_rest"` (`lay_on_hands`, `arcane_recovery`): unchanged on Short Rest.
   - **Warlock Pact Magic** (`spellcasting.is_pact_magic is True`): restore all slots to `slots_max`.
3. **Wizard Arcane Recovery (1/Long Rest):**
   - Eligible when `class_resources.get("arcane_recovery", {}).get("remaining", 0) >= 1` and at least one spell slot of level `1..5` is expended.
   - Budget: `budget = (level + 1) // 2`; max slot level = `5`.
   - Validate explicit `arcane_recovery_slots` (`sum(lvl * cnt) <= budget`, each `1 <= lvl <= 5`, `current + cnt <= slots_max[lvl]`), OR in auto policy greedily recover highest expended slot levels `<= 5` that fit `budget` (when at least 1 slot can be recovered). Sets `class_resources["arcane_recovery"]["remaining"] = 0`.
4. **PRNG Isolation:** Room `i` (`0, 1, 2`) combat uses `room_seed = seed + i * 1000`. Short Rest after room `i` uses `DeterministicRNG(seed=(seed ^ 0x52455354) + i)`. Each room's `MatchTranscript` therefore remains independently replayable via `replay_match()`!

---

### 3.3 `DungeonTranscript` Schema, Scorer Extension & Entry Points

#### `DungeonTranscript` Schema (`kind: "dungeon_trace"`)
```json
{
  "schema_version": "5",
  "kind": "dungeon_trace",
  "dataset_id": "srd-2024-5.2",
  "dungeon": {
    "id": "ashfall-keep",
    "name": "Ashfall Keep",
    "tier": 2,
    "party_level": 5,
    "total_rooms": 3
  },
  "config": {
    "seed": 42,
    "party_driver": "heuristic-tactical",
    "enemy_driver": "heuristic-tactical",
    "allow_short_rest": true,
    "max_actions_per_turn": 6,
    "max_retries_per_action": 2
  },
  "winner": "party",
  "rooms_cleared": 3,
  "total_rooms": 3,
  "rooms": [ { "...MatchTranscript (room 1)..." } ],
  "rests": [
    {
      "after_room_index": 0,
      "after_room_id": "entrance-hall",
      "taken": true,
      "per_pc": {
        "pc-aldric": {
          "hp_before": 26,
          "hp_after": 41,
          "hit_dice_spent": 2,
          "hit_dice_rolls": [7, 4],
          "hit_dice_remaining": 3,
          "resources_recovered": {"second_wind": 1, "action_surge": 1},
          "slots_recovered": {}
        }
      }
    }
  ],
  "final_party_state": { "pc-aldric": { "hp": 30, "hp_max": 44, "alive": true, "hit_dice": {"remaining": 2, "max": 5}, "slots": {}, "class_resources": {} } },
  "scorecard": { "...DungeonScorecard..." },
  "digest": "<sha256>"
}
```
- `compute_dungeon_digest(trace)`: SHA-256 hex digest over canonical JSON (`sort_keys=True, separators=(",", ":")`) of `{"winner", "rooms_cleared", "room_digests": [r["digest"] for r in rooms], "rests", "final_party_state", "scorecard"}`.
- `replay_dungeon(trace, rules)`: Replays every room through `replay_match()` (carrying over initial room states) AND re-runs every `ShortRestRecord` using recorded `hit_dice_spent` & `arcane_recovery_slots` through `resolve_short_rest()`, asserting `original_digest == replayed_digest`.

#### Multi-Encounter Scorer (`score_dungeon`)
Does **NOT** alter single-encounter `score_match()`. Adds `score_dungeon(dungeon_trace, rules=None) -> dict` with 5 weighted dimensions (weights sum to `1.00`):

| Dimension | Weight | Formula (0–100) |
|---|---:|---|
| `outcome_score` | `0.35` | `40 * (rooms_cleared / total_rooms) + (15 if winner == "party" else 0) + 25 * final_survival_ratio + 20 * final_hp_ratio` |
| `rules_legality_score` | `0.20` | Mean of per-room `rules_legality_score` across attempted rooms |
| `tactical_score` | `0.20` | Mean of per-room `tactical_score` across attempted rooms |
| `coordination_score` | `0.10` | Mean of per-room `coordination_score` across attempted rooms |
| `resource_conservation_score` | `0.15` | Scaled by `rooms_cleared / total_rooms` (no conservation points for wiping in Room 1!): `40 * slots_remaining_ratio + 30 * hit_dice_remaining_ratio + 30 * class_resources_remaining_ratio` |

#### CLI & Web Entry Points
- **CLI (`bin/srd-arena` via `src/srd2024/runtime/arena_bridge.py`):**
  - `bin/srd-arena dungeons` — list built-in dungeons (`ashfall-keep`).
  - `bin/srd-arena dungeon <id|path> [--party-agent ...] [--enemy-agent ...] [--seed 42] [--no-short-rest] [--out-trace <file>] [--force]` — run automated multi-room dungeon, emit `DungeonTranscript`.
  - `bin/srd-arena dungeon-replay <trace.json>` — verify 100% deterministic multi-room + Short Rest replay parity.
  - `bin/srd-arena dungeon-play <id|path> [--as ...] [--seat ...] [--party-agent ...] [--enemy-agent ...] [--seed 42] [--out-trace <file>]` — interactive CLI dungeon with Short Rest prompt between rooms.
- **Web (`bin/srd-web`):**
  - `bin/srd-web --dungeon <id|path>` (or launched from M6.4 Custom Builder).
  - Between rooms, `TabletopSession` enters `status: "short_rest"`, exposing `dungeon_progress: {room_index, total_rooms, next_room_name, party_summary}` in `/api/state`, and accepts `POST /api/short-rest` (`{"take_rest": bool, "plan": {...}}`) to resolve the Short Rest and start the next room!

---

## 4. M6.4 — Custom Party & Encounter Builder in `bin/srd-web`

### 4.1 Security Model, Validation Bounds & Combatant Cap (`#21`)

All builder endpoints inherit `web_server.py`'s existing local-only security model:
- Binds strictly to `127.0.0.1` (refuses all other hosts).
- Requires `Authorization: Bearer <launch_token>` (constant-time `hmac.compare_digest`), strict `Host` allowlist (`127.0.0.1:<port>`, `localhost:<port>`), `Origin` allowlist on all `POST`s (and non-empty `Origin` if present on `GET`), `Sec-Fetch-Site` in `{"same-origin", "none"}`, and strict `SECURITY_HEADERS` including CSP.
- **Body caps:** Existing gameplay `POST` endpoints stay at `MAX_BODY_BYTES = 8 * 1024`; builder `POST` endpoints (`/api/builder/validate-pc`, `/api/builder/budget`, `/api/builder/launch`) use `MAX_BUILDER_BODY_BYTES = 64 * 1024` (with `Content-Length` required, chunked refused, `Content-Type: application/json` required, unknown top-level keys refused with `400`).
- **Combatant & Map Caps (`#21`):**

| Limit Constant | Value | Enforcement |
|---|---:|---|
| `MIN_PARTY_SIZE` / `MAX_PARTY_SIZE` | `1` / `6` | Reject with `422` if `len(party)` outside `1..6` |
| `MIN_PC_LEVEL` / `MAX_PC_LEVEL` | `1` / `10` | Reject with `422` if any PC `level` outside `1..10` |
| `MIN_ENEMIES_PER_ROOM` / `MAX_ENEMIES_PER_ROOM` | `1` / `12` | Reject with `422` if any room's enemy count outside `1..12` |
| `MAX_COMBATANTS_PER_ENCOUNTER` | `18` | Reject with `422` if `len(party) + len(room.enemies) > 18` |
| `MAX_DUNGEON_ROOMS` | `3` | Custom launch supports `1` (single encounter) or `3` (dungeon) rooms |
| `MIN_MAP_FT` / `MAX_MAP_FT` | `20` / `120` | Both `width_ft` and `height_ft` must be multiples of `5` in `20..120` |
| `MAX_WALLS` / `MAX_DIFFICULT` / `MAX_COVER` | `200` / `200` / `100` | Prevents payload amplification; every tile must be 5-ft aligned inside `bounds` |

### 4.2 Exact HTTP API Endpoints (`src/srd2024/arena/web_server.py` + `builder.py`)

| Method & Path | Purpose | Request / Query | Response (`200 OK`) |
|---|---|---|---|
| `GET /api/builder/catalog` | Static options, PC presets, map presets, dungeon presets, limits | None | `{presets, character_options, maps, dungeons, limits, xp_table}` |
| `GET /api/builder/monsters` | Filter & search all **341** SRD monsters | `?cr_min=&cr_max=&type=&coverage=&q=&limit=50&offset=0` (strictly validated; `limit` capped `1..100`) | `{total, offset, limit, items: [{slug, name, cr, xp, ac, hp_max, size, creature_type, coverage, unmodeled_clauses, speed_ft, actions_summary}]}` |
| `POST /api/builder/validate-pc` | Compile & validate one PC spec via `compile_character()` | `{"spec": {...CharacterBuildSpec...}}` | `{valid, errors, warnings, statblock_summary: {name, class, subclass, level, hp_max, ac, ac_breakdown, initiative, speed_ft, hit_die, spell_save_dc, slots, class_resources_max, weapons}}` |
| `POST /api/builder/budget` | Compute 2024 XP budget & difficulty | `{"party_levels": [5, 5, 5, 5], "monsters": ["ghoul", "ghoul", "ghast"]}` | `{party_xp_thresholds: {low, moderate, high}, encounter_xp, difficulty: "trivial"\|"low"\|"moderate"\|"high"\|"deadly", combatant_count, warnings}` |
| `POST /api/builder/launch` | Compile, validate & start live single encounter or 3-room dungeon | `{"mode": "encounter"\|"dungeon", "name": str, "party": [{id, spec}], "rooms": [{name, map_preset, bounds, terrain, enemies: [{monster, count, name?}]}], "play_as": "party"\|"dm"\|"<pc_id>", "party_agent": str, "enemy_agent": str, "seed": int, "allow_short_rest": bool}` | `{"ok": true, "error": null, "state": <TabletopSession.snapshot()>}` |
| `POST /api/short-rest` | Resolve inter-room Short Rest in dungeon play | `{"take_rest": bool, "plan": {<pc_id>: {"hit_dice_to_spend": int, "arcane_recovery_slots": {str: int}}} \| null}` | `{"ok": true, "error": null, "rest": <ShortRestRecord>, "state": <TabletopSession.snapshot()>}` |

#### Exact 2024 Encounter XP Budget Table (`src/srd2024/arena/builder.py`)
Per-character XP thresholds (5e 2024 rules — no group-size multiplier; sum monster `xp` directly against sum of party thresholds):

| PC Level | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Low** | 50 | 100 | 150 | 250 | 500 | 600 | 750 | 1,000 | 1,300 | 1,600 |
| **Moderate** | 75 | 150 | 225 | 375 | 750 | 1,000 | 1,300 | 1,700 | 2,000 | 2,300 |
| **High** | 100 | 200 | 400 | 500 | 1,100 | 1,400 | 1,700 | 2,100 | 2,600 | 3,100 |

- Classification: `xp < low` → `"trivial"`; `xp < moderate` → `"low"`; `xp < high` → `"moderate"`; `xp <= high` → `"high"`; `xp > high` → `"deadly"`.
- **Server-side driver allowlist on `/api/builder/launch`:** Browser-selected `party_agent` and `enemy_agent` MUST be restricted to offline deterministic drivers (`{"heuristic-tactical", "greedy", "random"}`) — the browser NEVER supplies API bases, API key env names, or file paths! (Live LLM drivers remain strictly CLI-configured at launch via `--party-agent` / `--enemy-agent` / `--nl-driver`, preserving `TabletopConfig`'s security boundary.)
- **Deterministic Auto-Placement:** When `position` is omitted on builder combatants, `builder.py` deterministically places party PCs on the West side (`x = min_x + 5`, spaced vertically around center `y`) and enemies on the East side (`x = max_x - 10`, wrapping columns inward if > 6), skipping any square in `terrain.walls` or already occupied, and raising `422` if no legal spawn squares exist.

#### Built-In PC Presets & Level Scaling (Levels 1–10)
`builder.py` provides deterministic level-parameterized preset builders `build_pc_preset(archetype, level)` for **5 core archetypes** (`fighter`, `rogue`, `paladin`, `cleric-life`, `wizard-evoker`) plus all remaining 7 SRD classes (`barbarian`, `bard`, `druid`, `monk`, `ranger`, `sorcerer`, `warlock`) across **every level 1–10**:
- Automatically attaches the SRD subclass at `level >= 3` (and omits it at `level < 3`).
- Automatically populates valid `asi_choices` at class ASI levels (`[4, 8]` standard, `[4, 6, 8]` Fighter, `[4, 8, 10]` Rogue) without exceeding ability cap `20`.
- Automatically scales `cantrips`, `prepared_spells`, and `weapon_mastery_weapons` to compile with `valid == True, errors == [], warnings == []` at every level 1–10!

#### Built-In Map & Terrain Presets
1. All built-in scenario maps (`goblin-ambush`, `crypt-of-ghouls`, `wyrmling-lair`, `ruined-keep` from Wave 1).
2. Three parameterizable room presets (`width_ft`, `height_ft` in `20..120`, multiples of 5):
   - **`open-hall`**: Bounded room with empty interior.
   - **`pillared-crypt`**: Bounded room with 4 symmetric `half` cover pillars + central `difficult_terrain` rubble patch.
   - **`chokepoint-bridge`**: Bounded room divided at `mid_x` by two `walls` wings leaving a 15-ft central passage flanked by `three_quarters` cover battlements and `difficult_terrain` water margins.

### 4.3 Plain-JS UI Flow (ADR 0001 Strict CSP)

- **New Static Files** (served ONLY via `web_server.py:STATIC_FILES`, NOT in `scripts/site_files.py`):
  - `web/tabletop/builder-model.js` — pure state container, query-string builder, local XP sum preview, validation guards (0 DOM, unit-tested in `web/tests/builder.test.js`).
  - `web/tabletop/builder-view.js` — plain-DOM UI using `el()`, `badge()`, `notice()`, and `createBoard()` from `../lib/board-view.js`.
- **Session Lifecycle States in `tabletop-model.js`:**
  - Extend `STATUSES` to `new Set(["builder", "ready", "running", "awaiting_input", "short_rest", "finished", "paused", "error"])`.
  - When `state.status === "builder"`: `tabletop.js` mounts `builder-view.js` inside `ui.stage` with 4 clear panels:
    1. **Party Roster (1–6 PCs, L1–10):** Quick-add preset buttons (`+ Fighter`, `+ Rogue`, `+ Paladin`, `+ Life Cleric`, `+ Wizard`, `+ Other Class`), level picker `1..10`, inline customization drawer (name, species, background, subclass with `#34` coverage pill, ability scores, armor/shield/weapons, prepared spells), live `/api/builder/validate-pc` statblock summary badge (`HP / AC / Init / DC`), remove/duplicate buttons.
    2. **Battlefield & Mode:** Mode switch (`Single Encounter` vs `3-Room Dungeon`, with Room 1 / Room 2 / Room 3 tabs in dungeon mode), built-in map selector or custom room `width × height` + wall/cover preset, live SVG preview via `createBoard()` showing bounds, walls, difficult terrain, cover, and spawn zones.
    3. **Monster Picker (all 341 SRD):** Search input, CR min/max dropdowns, Creature Type filter, Coverage filter (`All | Full only | Partial only`), paginated results list showing CR, XP, AC, HP, coverage pill (`full` / `partial` with `unmodeled_clauses` hint), `+`/`−` add buttons, live **XP Budget & Difficulty Meter** + combatant count badge (`N / 18`).
    4. **Table Settings & Launch:** `Play as` selector (`Entire Party`, individual PC, or `DM`), AI driver selector (`heuristic-tactical`, `greedy`, `random`), integer Seed input, and **Launch** button (`POST /api/builder/launch`) transitioning straight into live tactical combat!
  - When `state.status === "short_rest"`: `tabletop.js` renders a compact Short Rest intermission panel showing each PC's current HP, Hit Dice stepper (`0..remaining`), Short-Rest resources that will recover (`+1 Second Wind`, `Action Surge`, `+1 Channel Divinity`, `Pact Magic`), optional Wizard Arcane Recovery slot checkboxes (enforcing combined level `<= ceil(level/2)` and `<= L5`), and **Take Short Rest & Enter Next Room** / **Skip Rest & Enter Next Room** buttons (`POST /api/short-rest`).
  - In live combat (`paintMe()`), display active PC's `class_resources` pills (e.g., `Second Wind 2/3`, `Action Surge 1/1`, `Lay on Hands 25/25 HP`, `Channel Divinity 2/2`, `Hit Dice 5/5 d10`).

---

## 5. Wave 2 Parallel File Ownership Plan (Zero Collisions)

All 3 Wave 2 workers execute in parallel in isolated git worktrees branched from post-Wave-1 `main`. **Every file modified or created has strictly one owner:**

| Wave 2 Worker | Exclusive Owned Files (Write) | Read-Only Inputs / Contracts | Must NEVER Modify |
|---|---|---|---|
| **`m6-class-features`** *(M6.3A + `#34`)* | `src/srd2024/rules_compiler/character.py`, `src/srd2024/rules_compiler/build.py`, `dist/rules.json`, `dist/web/rules-coverage.json`, `src/srd2024/engine/character.py`, `src/srd2024/engine/combat.py`, `src/srd2024/arena/agents.py`, `src/srd2024/arena/human.py`, `src/srd2024/arena/jev.py`, `src/srd2024/arena/runner.py`, `scripts/audit_arena_trace.py`, `tests/test_m6_class_features.py` | `docs/M6-DUNGEON-SPEC.md` §2, `data/raw/2024/en/**` | `src/srd2024/arena/{dungeon,builder,web_server,scenarios,scoring,session,render}.py`, `src/srd2024/runtime/arena_bridge.py`, `web/**`, `scripts/site_files.py`, existing test files |
| **`m6-adventuring-day`** *(M6.3B)* | `src/srd2024/arena/dungeon.py` *(new)*, `src/srd2024/runtime/arena_bridge.py`, `tests/test_m6_adventuring_day.py` *(new)* | `docs/M6-DUNGEON-SPEC.md` §2.2 & §3, `src/srd2024/engine/**`, `src/srd2024/arena/{scenarios,runner,scoring,session}.py` | `src/srd2024/rules_compiler/**`, `dist/**`, `src/srd2024/engine/**`, `src/srd2024/arena/{agents,human,jev,runner,scenarios,scoring,session,render,builder,web_server}.py`, `web/**`, `scripts/**`, existing test files |
| **`m6-web-builder`** *(M6.4)* | `src/srd2024/arena/builder.py` *(new)*, `src/srd2024/arena/web_server.py`, `web/tabletop/builder-model.js` *(new)*, `web/tabletop/builder-view.js` *(new)*, `web/tabletop/tabletop.js`, `web/tabletop/tabletop-model.js`, `web/tabletop/tabletop.css`, `tests/test_m6_web_builder.py` *(new)*, `web/tests/builder.test.js` *(new)* | `docs/M6-DUNGEON-SPEC.md` §2.3, §3.2 & §4, `docs/adr/0001-plain-js-no-build-frontend.md`, `docs/adr/0002-pages-deploy-gates.md`, `dist/rules.json` | `src/srd2024/rules_compiler/**`, `dist/**`, `src/srd2024/engine/**`, `src/srd2024/arena/{agents,human,jev,runner,scenarios,scoring,session,render,dungeon}.py`, `src/srd2024/runtime/**`, `scripts/**`, `web/lib/**`, `web/index.html`, `web/app.js`, existing test files |

### Cross-Worker Decoupling Guarantees (Why All 3 Branches Pass Independently & Merge Cleanly)
1. **`m6-adventuring-day` vs `m6-class-features`:** `dungeon.py` reads/writes `class_resources`, `hit_dice`, and `slots_max` using §2.2's exact JSON schema, initializing sensible defaults from `statblock` if a pre-M6.3A `combat.py` hasn't merged yet. Once both merge, `combat.py`'s `start_encounter` carryover hooks consume `dungeon.py`'s overrides seamlessly.
2. **`m6-web-builder` vs `m6-class-features`:** `web_server.py` adds `class_feature` and its keys (`feature`, `mode`, `amount`, `allocations`, `move_to`) to `_STRUCTURED_KINDS` / `_INTENT_KEYS`, and `client_view()` copies `me.get("class_resources") or {}` and `me.get("hit_dice")` safely (defaulting to `{}` / `None` before M6.3A merges).
3. **`m6-web-builder` vs `m6-adventuring-day`:** `web_server.py` lazy-imports `srd2024.arena.dungeon` inside dungeon launch / `/api/short-rest` handlers with a clear `501` fallback only if `dungeon.py` is absent on its isolated branch; in `tests/test_m6_web_builder.py`, single-encounter builder tests run directly against `TabletopServer` and dungeon-mode tests can stub or exercise `resolve_short_rest` via the §3.2 contract, then run full end-to-end once all 3 merge!
4. **Post-Wave-2 Verification Gate (on merged `main`):**
   - `PYTHONPATH=src python3 -m unittest discover -s tests` (all Stage 1–5 + M6 Wave 1 + Wave 2 tests, incl. `TestCommittedTracesReplay` and `test_rules_build_is_byte_identical_across_temp_rebuilds`)
   - `node --disable-warning=ExperimentalWarning --test` (all `web/tests/*.test.js` incl. `builder.test.js`)
   - `bash scripts/check_site.sh` (ADR 0002 Pages allowlist check)
   - `python3 scripts/audit_arena_trace.py examples/sessions/*.trace.json` + fresh M6.3A/B generated traces
   - `bin/srd-dst --seeds 50 --max-steps 50` (0 invariant violations, 100% determinism)
