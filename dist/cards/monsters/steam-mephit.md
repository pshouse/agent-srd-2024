---
id: monsters:steam-mephit
type: monsters
name: "Steam Mephit"
summary: "CR 1/4 Small elemental (neutral evil); AC 10, HP 17, XP 50"
refs:
  - ability-scores:con
  - ability-scores:dex
  - conditions:exhaustion
  - conditions:poisoned
  - damage-types:fire
  - damage-types:slashing
  - proficiencies:skill-stealth
---
# Steam Mephit (`monsters:steam-mephit`)
*Small elemental, neutral evil* | **CR** 1/4 (XP 50; PB +2)
**AC** 10 | **HP** 17 (5d6) | **Speed** 30 ft., fly 30 ft.
**STR** 5 (-3) | **DEX** 11 (+0) | **CON** 10 (+0) | **INT** 11 (+0) | **WIS** 10 (+0) | **CHA** 12 (+1)
**Skills:** [Skill: Stealth](../proficiencies/skill-stealth.md) +2
**Damage Immunities:** fire, poison
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 10
**Languages:** Primordial (Aquan, Ignan)

## Traits
- **Blurred Form.** Attack rolls against the mephit are made with Disadvantage unless the mephit has the Incapacitated condition.
- **Death Burst.** The mephit explodes when it dies. Dexterity Saving Throw: DC 10, each creature in a 5-foot Emanation originating from the mephit. Failure: 5 (2d4) Fire damage. Success: Half damage.

## Actions
- **Claw.** Melee Attack Roll: +2, reach 5 ft. Hit: 2 (1d4) Slashing damage plus 2 (1d4) Fire damage.
- **Steam Breath (Recharge 6+ (1d6)).** Constitution Saving Throw: DC 10, each creature in a 15-foot Cone. Failure: 5 (2d4) Fire damage, and the target’s Speed decreases by 10 feet until the end of the mephit’s next turn. Success: Half damage only. Failure or Success: Being underwater doesn’t grant Resistance to this Fire damage.
