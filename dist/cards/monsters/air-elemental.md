---
id: monsters:air-elemental
type: monsters
name: "Air Elemental"
summary: "CR 5 Large elemental (neutral); AC 15, HP 90, XP 1800"
refs:
  - ability-scores:str
  - conditions:exhaustion
  - conditions:grappled
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - conditions:prone
  - conditions:restrained
  - conditions:unconscious
  - damage-types:thunder
---
# Air Elemental (`monsters:air-elemental`)
*Large elemental, neutral* | **CR** 5 (XP 1,800; PB +3)
**AC** 15 | **HP** 90 (12d10 + 24) | **Speed** 10 ft., fly 90 ft., hover True
**STR** 14 (+2) | **DEX** 20 (+5) | **CON** 14 (+2) | **INT** 6 (-2) | **WIS** 10 (+0) | **CHA** 6 (-2)
**Damage Resistances:** bludgeoning, lightning, piercing, slashing
**Damage Immunities:** poison, thunder
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Grappled](../conditions/grappled.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md), [Prone](../conditions/prone.md), [Restrained](../conditions/restrained.md), [Unconscious](../conditions/unconscious.md)
**Senses:** Darkvision 60 ft., Passive Perception 10
**Languages:** Primordial (Auran)

## Traits
- **Air Form.** The elemental can enter a creature’s space and stop there. It can move through a space as narrow as 1 inch without expending extra movement to do so.

## Actions
- **Multiattack.** The elemental makes two Thunderous Slam attacks.
- **Thunderous Slam.** Melee Attack Roll: +8, reach 10 ft. Hit: 14 (2d8 + 5) Thunder damage.
- **Whirlwind (Recharge 4+ (1d6)).** Strength Saving Throw: DC 13, one Medium or smaller creature in the elemental’s space. Failure: 24 (4d10 + 2) Thunder damage, and the target is pushed up to 20 feet straight away from the elemental and has the Prone condition. Success: Half damage only.
