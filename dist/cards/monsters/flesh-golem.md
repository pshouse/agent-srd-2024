---
id: monsters:flesh-golem
type: monsters
name: "Flesh Golem"
summary: "CR 5 Medium construct (neutral); AC 9, HP 127, XP 1800"
refs:
  - ability-scores:cha
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - damage-types:bludgeoning
  - damage-types:lightning
---
# Flesh Golem (`monsters:flesh-golem`)
*Medium construct, neutral* | **CR** 5 (XP 1,800; PB +3)
**AC** 9 | **HP** 127 (15d8 + 60) | **Speed** 30 ft.
**STR** 19 (+4) | **DEX** 9 (-1) | **CON** 18 (+4) | **INT** 6 (-2) | **WIS** 10 (+0) | **CHA** 5 (-3)
**Damage Immunities:** lightning, poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 10
**Languages:** Understands Common plus one other language but can’t speak

## Traits
- **Aversion to Fire.** If the golem takes Fire damage, it has Disadvantage on attack rolls and ability checks until the end of its next turn.
- **Berserk.** Whenever the golem starts its turn Bloodied, roll 1d6. On a 6, the golem goes berserk. On each of its turns while berserk, the golem attacks the nearest creature it can see. If no creature is near enough to move to and attack, the golem attacks an object. Once the golem goes berserk, it remains so until it is destroyed or it is no longer Bloodied. The golem’s creator, if within 60 feet of the berserk golem, can try to calm it by taking an action to make a DC 15 Charisma (Persuasion) check; the golem must be able to hear its creator. If this check succeeds, the golem ceases being berserk until the start of its next turn, at which point it resumes rolling for the Berserk trait again if it is still Bloodied.
- **Immutable Form.** The golem can’t shape-shift.
- **Lightning Absorption.** Whenever the golem is subjected to Lightning damage, it regains a number of Hit Points equal to the Lightning damage dealt.
- **Magic Resistance.** The golem has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The golem makes two Slam attacks.
- **Slam.** Melee Attack Roll: +7, reach 5 ft. Hit: 13 (2d8 + 4) Bludgeoning damage plus 4 (1d8) Lightning damage.
