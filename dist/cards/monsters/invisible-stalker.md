---
id: monsters:invisible-stalker
type: monsters
name: "Invisible Stalker"
summary: "CR 6 Large elemental (neutral); AC 14, HP 97, XP 2300"
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
  - damage-types:thunder
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
---
# Invisible Stalker (`monsters:invisible-stalker`)
*Large elemental, neutral* | **CR** 6 (XP 2,300; PB +3)
**AC** 14 | **HP** 97 (13d10 + 26) | **Speed** 50 ft., fly 50 ft., hover True
**STR** 16 (+3) | **DEX** 19 (+4) | **CON** 14 (+2) | **INT** 10 (+0) | **WIS** 15 (+2) | **CHA** 11 (+0)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +8, [Skill: Stealth](../proficiencies/skill-stealth.md) +10
**Damage Resistances:** bludgeoning, piercing, slashing
**Damage Immunities:** poison
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Grappled](../conditions/grappled.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md), [Prone](../conditions/prone.md), [Restrained](../conditions/restrained.md), [Unconscious](../conditions/unconscious.md)
**Senses:** Darkvision 60 ft., Passive Perception 18
**Languages:** Common, Primordial (Auran)

## Traits
- **Air Form.** The stalker can enter an enemy’s space and stop there. It can move through a space as narrow as 1 inch without expending extra movement to do so.
- **Invisibility.** The stalker has the Invisible condition.

## Actions
- **Multiattack.** The stalker makes three Wind Swipe attacks. It can replace one attack with a use of Vortex.
- **Wind Swipe.** Melee Attack Roll: +7, reach 5 ft. Hit: 11 (2d6 + 4) Force damage.
- **Vortex.** Constitution Saving Throw: DC 14, one Large or smaller creature in the stalker’s space. Failure: 7 (1d8 + 3) Thunder damage, and the target has the Grappled condition (escape DC 13). Until the grapple ends, the target can’t cast spells with a Verbal component and takes 7 (2d6) Thunder damage at the start of each of the stalker’s turns.
