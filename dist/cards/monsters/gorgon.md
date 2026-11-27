---
id: monsters:gorgon
type: monsters
name: "Gorgon"
summary: "CR 5 Large construct (unaligned); AC 19, HP 114, XP 1800"
refs:
  - ability-scores:con
  - ability-scores:dex
  - conditions:exhaustion
  - conditions:petrified
  - damage-types:bludgeoning
  - damage-types:piercing
  - proficiencies:skill-perception
---
# Gorgon (`monsters:gorgon`)
*Large construct, unaligned* | **CR** 5 (XP 1,800; PB +3)
**AC** 19 | **HP** 114 (12d10 + 48) | **Speed** 40 ft.
**STR** 20 (+5) | **DEX** 11 (+0) | **CON** 18 (+4) | **INT** 2 (-4) | **WIS** 12 (+1) | **CHA** 7 (-2)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +7
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Petrified](../conditions/petrified.md)
**Senses:** Darkvision 60 ft., Passive Perception 17
**Languages:** None

## Actions
- **Gore.** Melee Attack Roll: +8, reach 5 ft. Hit: 18 (2d12 + 5) Piercing damage. If the target is a Large or smaller creature and the gorgon moved 20+ feet straight toward it immediately before the hit, the target has the Prone condition.
- **Petrifying Breath (Recharge 5+ (1d6)).** Constitution Saving Throw: DC 15, each creature in a 30-foot Cone. First Failure: The target has the Restrained condition and repeats the save at the end of its next turn if it is still Restrained, ending the effect on itself on a success. Second Failure: The target has the Petrified condition instead of the Restrained condition.

## Bonus Actions
- **Trample.** Dexterity Saving Throw: DC 16, one creature within 5 feet that has the Prone condition. Failure: 16 (2d10 + 5) Bludgeoning damage. Success: Half damage.
