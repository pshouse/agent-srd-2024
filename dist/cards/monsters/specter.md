---
id: monsters:specter
type: monsters
name: "Specter"
summary: "CR 1 Medium undead (chaotic evil); AC 12, HP 22, XP 200"
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
# Specter (`monsters:specter`)
*Medium undead, chaotic evil* | **CR** 1 (XP 200; PB +2)
**AC** 12 | **HP** 22 (5d8) | **Speed** 30 ft., fly 50 ft., hover True
**STR** 1 (-5) | **DEX** 14 (+2) | **CON** 11 (+0) | **INT** 10 (+0) | **WIS** 10 (+0) | **CHA** 11 (+0)
**Damage Resistances:** acid, bludgeoning, cold, fire, lightning, piercing, slashing, thunder
**Damage Immunities:** necrotic, poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Grappled](../conditions/grappled.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md), [Prone](../conditions/prone.md), [Restrained](../conditions/restrained.md), [Unconscious](../conditions/unconscious.md)
**Senses:** Darkvision 60 ft., Passive Perception 10
**Languages:** Understands Common plus one other language but can’t speak

## Traits
- **Incorporeal Movement.** The specter can move through other creatures and objects as if they were Difficult Terrain. It takes 5 (1d10) Force damage if it ends its turn inside an object.
- **Sunlight Sensitivity.** While in sunlight, the specter has Disadvantage on ability checks and attack rolls.

## Actions
- **Life Drain.** Melee Attack Roll: +4, reach 5 ft. Hit: 7 (2d6) Necrotic damage. If the target is a creature, its Hit Point maximum decreases by an amount equal to the damage taken.
