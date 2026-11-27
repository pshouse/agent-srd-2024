---
id: monsters:stone-golem
type: monsters
name: "Stone Golem"
summary: "CR 10 Large construct (unaligned); AC 18, HP 220, XP 5900"
refs:
  - ability-scores:con
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - damage-types:bludgeoning
  - damage-types:force
  - spells:slow
---
# Stone Golem (`monsters:stone-golem`)
*Large construct, unaligned* | **CR** 10 (XP 5,900; PB +4)
**AC** 18 | **HP** 220 (21d10 + 105) | **Speed** 30 ft.
**STR** 22 (+6) | **DEX** 9 (-1) | **CON** 20 (+5) | **INT** 3 (-4) | **WIS** 11 (+0) | **CHA** 1 (-5)
**Damage Immunities:** poison, psychic
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 120 ft., Passive Perception 10
**Languages:** Understands Common plus two other languages but can’t speak

## Traits
- **Immutable Form.** The golem can’t shape-shift.
- **Magic Resistance.** The golem has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The golem makes two attacks, using Slam or Force Bolt in any combination.
- **Slam.** Melee Attack Roll: +10, reach 5 ft. Hit: 15 (2d8 + 6) Bludgeoning damage plus 9 (2d8) Force damage.
- **Force Bolt.** Ranged Attack Roll: +9, range 120 ft. Hit: 22 (4d10) Force damage.

## Bonus Actions
- **Slow (Recharge 5+ (1d6)).** The golem casts the Slow spell, requiring no spell components and using Constitution as the spellcasting ability (spell save DC 17).
  - Other: [Slow](../spells/slow.md) (level 3)
