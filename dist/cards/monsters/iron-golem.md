---
id: monsters:iron-golem
type: monsters
name: "Iron Golem"
summary: "CR 16 Large construct (unaligned); AC 20, HP 252, XP 15000"
refs:
  - ability-scores:con
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - damage-types:fire
  - damage-types:poison
  - damage-types:slashing
---
# Iron Golem (`monsters:iron-golem`)
*Large construct, unaligned* | **CR** 16 (XP 15,000; PB +5)
**AC** 20 | **HP** 252 (24d10 + 120) | **Speed** 30 ft.
**STR** 24 (+7) | **DEX** 9 (-1) | **CON** 20 (+5) | **INT** 3 (-4) | **WIS** 11 (+0) | **CHA** 1 (-5)
**Damage Immunities:** fire, poison, psychic
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 120 ft., Passive Perception 10
**Languages:** Understands Common plus two other languages but can’t speak

## Traits
- **Fire Absorption.** Whenever the golem is subjected to Fire damage, it regains a number of Hit Points equal to the Fire damage dealt.
- **Immutable Form.** The golem can’t shape-shift.
- **Magic Resistance.** The golem has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The golem makes two attacks, using Bladed Arm or Fiery Bolt in any combination.
- **Bladed Arm.** Melee Attack Roll: +12, reach 10 ft. Hit: 20 (3d8 + 7) Slashing damage plus 10 (3d6) Fire damage.
- **Fiery Bolt.** Ranged Attack Roll: +10, range 120 ft. Hit: 36 (8d8) Fire damage.
- **Poison Breath (Recharge 6+ (1d6)).** Constitution Saving Throw: DC 18, each creature in a 60-foot Cone. Failure: 55 (10d10) Poison damage. Success: Half damage.
