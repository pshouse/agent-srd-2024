---
id: monsters:will-o-wisp
type: monsters
name: "Will-o’-Wisp"
summary: "CR 2 Tiny undead (chaotic evil); AC 19, HP 27, XP 450"
refs:
  - ability-scores:con
  - conditions:exhaustion
  - conditions:grappled
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - conditions:prone
  - conditions:restrained
  - conditions:unconscious
  - damage-types:force
  - damage-types:lightning
---
# Will-o’-Wisp (`monsters:will-o-wisp`)
*Tiny undead, chaotic evil* | **CR** 2 (XP 450; PB +2)
**AC** 19 | **HP** 27 (11d4) | **Speed** 5 ft., fly 50 ft., hover True
**STR** 1 (-5) | **DEX** 28 (+9) | **CON** 10 (+0) | **INT** 13 (+1) | **WIS** 14 (+2) | **CHA** 11 (+0)
**Damage Resistances:** acid, bludgeoning, cold, fire, necrotic, piercing, slashing
**Damage Immunities:** lightning, poison
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Grappled](../conditions/grappled.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md), [Prone](../conditions/prone.md), [Restrained](../conditions/restrained.md), [Unconscious](../conditions/unconscious.md)
**Senses:** Darkvision 120 ft., Passive Perception 12
**Languages:** Common plus one other language

## Traits
- **Ephemeral.** The wisp can’t wear or carry anything.
- **Illumination.** The wisp sheds Bright Light in a 20-foot radius and Dim Light for an additional 20 feet.
- **Incorporeal Movement.** The wisp can move through other creatures and objects as if they were Difficult Terrain. It takes 5 (1d10) Force damage if it ends its turn inside an object.

## Actions
- **Shock.** Melee Attack Roll: +4, reach 5 ft. Hit: 11 (2d8 + 2) Lightning damage.

## Bonus Actions
- **Consume Life.** Constitution Saving Throw: DC 10, one living creature the wisp can see within 5 feet that has 0 Hit Points. Failure: The target dies, and the wisp regains 10 (3d6) Hit Points.
- **Vanish.** The wisp and its light have the Invisible condition until the wisp’s Concentration ends on this effect, which ends early immediately after the wisp makes an attack roll or uses Consume Life.
