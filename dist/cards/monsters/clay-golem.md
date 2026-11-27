---
id: monsters:clay-golem
type: monsters
name: "Clay Golem"
summary: "CR 9 Large construct (unaligned); AC 14, HP 123, XP 5000"
refs:
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - damage-types:acid
  - damage-types:bludgeoning
---
# Clay Golem (`monsters:clay-golem`)
*Large construct, unaligned* | **CR** 9 (XP 5,000; PB +4)
**AC** 14 | **HP** 123 (13d10 + 52) | **Speed** 30 ft.
**STR** 20 (+5) | **DEX** 9 (-1) | **CON** 18 (+4) | **INT** 3 (-4) | **WIS** 8 (-1) | **CHA** 1 (-5)
**Damage Resistances:** bludgeoning, piercing, slashing
**Damage Immunities:** acid, poison, psychic
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 9
**Languages:** Common plus one other language

## Traits
- **Acid Absorption.** Whenever the golem is subjected to Acid damage, it takes no damage and instead regains a number of Hit Points equal to the Acid damage dealt.
- **Berserk.** Whenever the golem starts its turn Bloodied, roll 1d6. On a 6, the golem goes berserk. On each of its turns while berserk, the golem attacks the nearest creature it can see. If no creature is near enough to move to and attack, the golem attacks an object. Once the golem goes berserk, it continues to be berserk until it is destroyed or it is no longer Bloodied.
- **Immutable Form.** The golem can’t shape-shift.
- **Magic Resistance.** The golem has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The golem makes two Slam attacks, or it makes three Slam attacks if it used Hasten this turn.
- **Slam.** Melee Attack Roll: +9, reach 5 ft. Hit: 10 (1d10 + 5) Bludgeoning damage plus 6 (1d12) Acid damage, and the target’s Hit Point maximum decreases by an amount equal to the Acid damage taken.

## Bonus Actions
- **Hasten (Recharge 5+ (1d6)).** The golem takes the Dash and Disengage actions.
