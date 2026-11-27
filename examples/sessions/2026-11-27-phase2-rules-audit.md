# Phase 2: Rules audit of arena play (2026-11-27)

A line-by-line check of how well the Stage 2 engine follows SRD 5.2 in real arena matches. It covers 20 Crypt of
Ghouls matches (seeds 1–20) and 5 Wyrmling Lair matches (seeds 1–5) at `main` `acc80ba`, all with
`heuristic-tactical` on both sides. Phase 1 (the same day) covered auto-applied spells and is summarised at the end.

## How it was checked

1. **Every event, automatically.** [`scripts/audit_arena_trace.py`](../../scripts/audit_arena_trace.py) re-derives each
   roll from the trace alone (the starting sheets plus the events), keeping its own record of HP, conditions, death
   saves, concentration and AC. It doesn't use the engine, so it's a second opinion rather than a re-run.
2. **Stat blocks against the SRD cards** in `dist/cards/monsters/` (ghoul, ghast, skeleton).
3. **One busy match by hand:** `crypt-8` (7 rounds, 71 actions, 6 crits, Paralysis, a PC death, revival,
   concentration loss).
4. **Gaps:** checked for rules that should have shown up but never did, and searched the engine to confirm why.

To reproduce:
```sh
for s in $(seq 1 20); do bin/srd-arena run crypt-of-ghouls --seed $s --out-trace /tmp/crypt-$s.json; done
python3 scripts/audit_arena_trace.py /tmp/crypt-*.json
```

## Result: the dice and arithmetic are right

**4,236 checks across 38 rules, 0 violations.**

| Area | Checks | Rules covered |
|---|---:|---|
| Attack rolls | 1,555 (315 rolls × 5 rules) | d20 count and choice for advantage/disadvantage, mode follows its sources, total = d20 + bonus + Bless − exhaustion, nat 20/nat 1, hit iff total ≥ AC |
| Attack bonuses | 295 | PC weapon bonus = sheet (122); monster bonus = stat block (173) |
| Target AC | 315 | Sheet AC, +5 while Shield is up, 13 + DEX under Mage Armor |
| Damage | 222 | Monster dice ranges incl. crits doubling dice only (121); PC weapon dice + modifier (101) |
| Resistance, vulnerability, immunity | 308 | Ghast necrotic resistance, skeleton bludgeoning vulnerability, poison immunity |
| Conditions | 194 | Paralysis: CON DC 10 Claw rider, applied only on a failed save, ends at the end of the target's next turn; attacks vs paralyzed/unconscious have advantage; melee hits within 5 ft are crits, and no other non-20 crits |
| Saving throws | 354 | total = d20 + sheet save bonus + dice; success iff ≥ DC; modifier = sheet (118 each) |
| Fireball | 123 | 8d6 at slot 3, one roll shared by all targets, half on a success; DEX save at the caster's DC 15 |
| Concentration | 36 | Damage to a concentrating caster triggers a CON save at DC max(10, half damage) |
| HP bookkeeping | 628 | Running HP matches every `hp_before`; `hp_after` = max(0, before − damage); healing capped at max HP |
| Dying | 84 | Death save at the start of each downed PC's turn; 1 = two failures, 20 = 1 HP; running counters; 3 failures → dead, 3 successes → stable; damage at 0 HP adds failures (2 on a crit); massive damage kills |
| Turn structure | 122 | ≤ Extra Attack count per Attack action (Fighter 5: 2) |

A separate pass over the same traces found no paralyzed or unconscious creature taking an action (477 actions).

Stat blocks match the SRD cards exactly: ghoul (AC 12, HP 22, Bite +4 1d6+2 + 1d6 necrotic, Claw +4 1d4+2 with
Paralysis), ghast (AC 13, HP 36, necrotic resistance, Bite +5, Claw +5 2d6+3 with Paralysis), and skeleton (AC 14,
HP 13, bludgeoning vulnerability, +5 Shortsword and Shortbow).

The first run flagged 2 rules (21 events), and **all of them were bugs in the audit tool**, not the engine. Paralysis ends in the
same step as the target's `turn_ended` (the tool only looked after it), and the tool didn't track Mage Armor.
Both were fixed in the tool before the numbers above.

## crypt-8 by hand

- **R1:** the ghast's Claw paralyzes Lyra (failed DC 10 CON). A ghoul's two Bites against her are automatic crits
  (melee within 5 ft of a paralyzed target): the first drops her, and the second, at 0 HP, adds **2** death-save
  failures because it's a crit. Dorian's Healing Word brings her back for 8 (2d4 + 4), which resets her counters.
- **R2:** Lyra drops again with fresh counters. Dorian fails a concentration save, and **Bless ends**. Dorian is
  paralyzed by a Claw.
- **R3:** paralyzed Dorian takes two crit Bites, goes unconscious and prone, and fails his first death save.
- **R2–R4:** Lyra makes three death saves (11, 10, 15) and is **stable**.
- **R5:** Dorian's third failure (8): **Dorian dies.** With the healer dead and the caster stable at 0 HP, Aldric
  finishes the skeleton alone in R7.
- Party win, composite 90.3. Every roll in it passes the audit.

## Gaps: rules that never happen

| Finding | Evidence | Issue |
|---|---|---|
| **No opportunity attacks.** Leaving reach is free and Disengage does nothing | 19 moves left a conscious enemy's reach without Disengage, and none drew an attack. The engine has no opportunity-attack code. | [#38](https://github.com/pshouse/agent-srd-2024/issues/38) |
| **Monster traits never execute, yet are graded `full`** | Ghast **Stench**: 0 saves in 20 matches. Hell Hound **Pack Tactics**: 0 of 18 attacks. Magmin **Death Burst**: 0 of 5 deaths. Bestiary-wide, 139 of the 207 trait-bearing monsters are graded `coverage: full` with no unmodeled clauses | [#39](https://github.com/pshouse/agent-srd-2024/issues/39) |
| **Shield with no trigger** (Phase 1). The AI casts it on its own turn as free +5 AC; legality stays 100 | 30 of 30 casts | [#33](https://github.com/pshouse/agent-srd-2024/issues/33) |
| **Disciple of Life missing** (Phase 1); the Life Domain has no coverage grade | 64 heals, none with the bonus | [#34](https://github.com/pshouse/agent-srd-2024/issues/34) |

All four favor the party (the PCs face no opportunity attacks, no Stench and no Pack Tactics, and get free Shields),
which helps explain why the party won all 30 Phase 1 matches. #38 and #33 share a fix: a reaction window in the
engine. #39 has an easy first step: grade those monsters `partial` and list the traits in `unmodeled_clauses`, so
agents know what isn't automated.

## Phase 1 (auto-applied spells), for reference

Healing Word and Mass Healing Word (53 casts, 33 revivals), Bless (37 casts, the d4 on 148 attacks) and Mage Armor all
work in arena play, so issue #6 (auto-applied **damage** such as Divine Smite) doesn't affect the built-in scenarios.
Issue #9 (actions after combat ends) can't happen in the arena, because the runner stops when one side is defeated.

---
*This work includes material from the System Reference Document 5.2 ("SRD 5.2") by Wizards of the Coast LLC, available at
https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License,
available at https://creativecommons.org/licenses/by/4.0/legalcode.*
