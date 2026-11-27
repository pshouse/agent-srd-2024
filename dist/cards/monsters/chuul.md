---
id: monsters:chuul
type: monsters
name: "Chuul"
summary: "CR 4 Large aberration (chaotic evil); AC 16, HP 76, XP 1100"
refs:
  - ability-scores:con
  - ability-scores:str
  - conditions:poisoned
  - damage-types:bludgeoning
  - proficiencies:skill-perception
---
# Chuul (`monsters:chuul`)
*Large aberration, chaotic evil* | **CR** 4 (XP 1,100; PB +2)
**AC** 16 | **HP** 76 (9d10 + 27) | **Speed** 30 ft., swim 30 ft.
**STR** 19 (+4) | **DEX** 10 (+0) | **CON** 16 (+3) | **INT** 5 (-3) | **WIS** 11 (+0) | **CHA** 5 (-3)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +4
**Damage Immunities:** poison
**Condition Immunities:** [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 14
**Languages:** Understands Deep Speech but can’t speak

## Traits
- **Amphibious.** The chuul can breathe air and water.
- **Sense Magic.** The chuul senses magic within 120 feet of itself. This trait otherwise works like the Detect Magic spell but isn’t itself magical.

## Actions
- **Multiattack.** The chuul makes two Pincer attacks and uses Paralyzing Tentacles.
- **Pincer.** Melee Attack Roll: +6, reach 10 ft. Hit: 9 (1d10 + 4) Bludgeoning damage. If the target is a Large or smaller creature, it has the Grappled condition (escape DC 14) from one of two pincers.
- **Paralyzing Tentacles.** Constitution Saving Throw: DC 13, one creature Grappled by the chuul. Failure: The target has the Poisoned condition and repeats the save at the end of each of its turns, ending the effect on itself on a success. After 1 minute, it succeeds automatically. While Poisoned, the target has the Paralyzed condition.
