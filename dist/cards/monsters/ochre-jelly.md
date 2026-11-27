---
id: monsters:ochre-jelly
type: monsters
name: "Ochre Jelly"
summary: "CR 2 Large ooze (unaligned); AC 8, HP 52, XP 450"
refs:
  - conditions:charmed
  - conditions:deafened
  - conditions:exhaustion
  - conditions:frightened
  - conditions:grappled
  - conditions:prone
  - conditions:restrained
  - damage-types:acid
---
# Ochre Jelly (`monsters:ochre-jelly`)
*Large ooze, unaligned* | **CR** 2 (XP 450; PB +2)
**AC** 8 | **HP** 52 (7d10 + 14) | **Speed** 20 ft., climb 20 ft.
**STR** 15 (+2) | **DEX** 6 (-2) | **CON** 14 (+2) | **INT** 2 (-4) | **WIS** 6 (-2) | **CHA** 1 (-5)
**Damage Resistances:** acid
**Damage Immunities:** lightning, slashing
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Deafened](../conditions/deafened.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Grappled](../conditions/grappled.md), [Prone](../conditions/prone.md), [Restrained](../conditions/restrained.md)
**Senses:** Blindsight 60 ft., Passive Perception 8
**Languages:** None

## Traits
- **Amorphous.** The jelly can move through a space as narrow as 1 inch without expending extra movement to do so.
- **Spider Climb.** The jelly can climb difficult surfaces, including along ceilings, without needing to make an ability check.

## Actions
- **Pseudopod.** Melee Attack Roll: +4, reach 5 ft. Hit: 12 (3d6 + 2) Acid damage.

## Reactions
- **Split.** Trigger: While the jelly is Large or Medium and has 10+ Hit Points, it becomes Bloodied or is subjected to Lightning or Slashing damage. Response: The jelly splits into two new Ochre Jellies. Each new jelly is one size smaller than the original jelly and acts on its Initiative. The original jelly’s Hit Points are divided evenly between the new jellies (round down).
