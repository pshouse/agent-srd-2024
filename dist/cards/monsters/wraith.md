---
id: monsters:wraith
type: monsters
name: "Wraith"
summary: "CR 5 Medium or small undead (neutral evil); AC 13, HP 67, XP 1800"
refs:
  - conditions:charmed
  - conditions:exhaustion
  - conditions:grappled
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - conditions:prone
  - conditions:restrained
  - conditions:unconscious
  - damage-types:force
  - damage-types:necrotic
---
# Wraith (`monsters:wraith`)
*Medium or small undead, neutral evil* | **CR** 5 (XP 1,800; PB +3)
**AC** 13 | **HP** 67 (9d8 + 27) | **Speed** 5 ft., fly 60 ft., hover True
**STR** 6 (-2) | **DEX** 16 (+3) | **CON** 16 (+3) | **INT** 12 (+1) | **WIS** 14 (+2) | **CHA** 15 (+2)
**Damage Resistances:** acid, bludgeoning, cold, fire, piercing, slashing
**Damage Immunities:** necrotic, poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Grappled](../conditions/grappled.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md), [Prone](../conditions/prone.md), [Restrained](../conditions/restrained.md), [Unconscious](../conditions/unconscious.md)
**Senses:** Darkvision 60 ft., Passive Perception 12
**Languages:** Common plus two other languages

## Traits
- **Incorporeal Movement.** The wraith can move through other creatures and objects as if they were Difficult Terrain. It takes 5 (1d10) Force damage if it ends its turn inside an object.
- **Sunlight Sensitivity.** While in sunlight, the wraith has Disadvantage on ability checks and attack rolls.

## Actions
- **Life Drain.** Melee Attack Roll: +6, reach 5 ft. Hit: 21 (4d8 + 3) Necrotic damage. If the target is a creature, its Hit Point maximum decreases by an amount equal to the damage taken.
- **Create Specter.** The wraith targets a Humanoid corpse within 10 feet of itself that has been dead for no longer than 1 minute. The target’s spirit rises as a Specter in the space of its corpse or in the nearest unoccupied space. The specter is under the wraith’s control. The wraith can have no more than seven specters under its control at a time.
