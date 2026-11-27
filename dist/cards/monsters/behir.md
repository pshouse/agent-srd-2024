---
id: monsters:behir
type: monsters
name: "Behir"
summary: "CR 11 Huge monstrosity (neutral evil); AC 17, HP 168, XP 7200"
refs:
  - ability-scores:dex
  - ability-scores:str
  - damage-types:acid
  - damage-types:bludgeoning
  - damage-types:lightning
  - damage-types:piercing
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
---
# Behir (`monsters:behir`)
*Huge monstrosity, neutral evil* | **CR** 11 (XP 7,200; PB +4)
**AC** 17 | **HP** 168 (16d12 + 64) | **Speed** 50 ft., climb 50 ft.
**STR** 23 (+6) | **DEX** 16 (+3) | **CON** 18 (+4) | **INT** 7 (-2) | **WIS** 14 (+2) | **CHA** 12 (+1)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +6, [Skill: Stealth](../proficiencies/skill-stealth.md) +7
**Damage Immunities:** lightning
**Senses:** Darkvision 90 ft., Passive Perception 16
**Languages:** Draconic

## Actions
- **Multiattack.** The behir makes one Bite attack and uses Constrict.
- **Bite.** Melee Attack Roll: +10, reach 10 ft. Hit: 19 (2d12 + 6) Piercing damage plus 11 (2d10) Lightning damage.
- **Constrict.** Strength Saving Throw: DC 18, one Large or smaller creature the behir can see within 5 feet. Failure: 28 (5d8 + 6) Bludgeoning damage. The target has the Grappled condition (escape DC 16), and it has the Restrained condition until the grapple ends.
- **Lightning Breath (Recharge 5+ (1d6)).** Dexterity Saving Throw: DC 16, each creature in a 90-foot-long, 5-footwide Line. Failure: 66 (12d10) Lightning damage. Success: Half damage.

## Bonus Actions
- **Swallow.** Dexterity Saving Throw: DC 18, one Large or smaller creature Grappled by the behir (the behir can have only one creature swallowed at a time). Failure: The behir swallows the target, which is no longer Grappled. While swallowed, a creature has the Blinded and Restrained conditions, has Total Cover against attacks and other effects outside the behir, and takes 21 (6d6) Acid damage at the start of each of the behir’s turns. If the behir takes 30 damage or more on a single turn from the swallowed creature, the behir must succeed on a DC 14 Constitution saving throw at the end of that turn or regurgitate the creature, which falls in a space within 10 feet of the behir and has the Prone condition. If the behir dies, a swallowed creature is no longer Restrained and can escape from the corpse by using 15 feet of movement, exiting Prone.
