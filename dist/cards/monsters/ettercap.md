---
id: monsters:ettercap
type: monsters
name: "Ettercap"
summary: "CR 2 Medium monstrosity (neutral evil); AC 13, HP 44, XP 450"
refs:
  - ability-scores:dex
  - damage-types:piercing
  - damage-types:poison
  - damage-types:slashing
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
  - proficiencies:skill-survival
---
# Ettercap (`monsters:ettercap`)
*Medium monstrosity, neutral evil* | **CR** 2 (XP 450; PB +2)
**AC** 13 | **HP** 44 (8d8 + 8) | **Speed** 30 ft., climb 30 ft.
**STR** 14 (+2) | **DEX** 15 (+2) | **CON** 13 (+1) | **INT** 7 (-2) | **WIS** 12 (+1) | **CHA** 8 (-1)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +3, [Skill: Stealth](../proficiencies/skill-stealth.md) +4, [Skill: Survival](../proficiencies/skill-survival.md) +3
**Senses:** Darkvision 60 ft., Passive Perception 13
**Languages:** None

## Traits
- **Spider Climb.** The ettercap can climb difficult surfaces, including along ceilings, without needing to make an ability check.
- **Web Walker.** The ettercap ignores movement restrictions caused by webs, and the ettercap knows the location of any other creature in contact with the same web.

## Actions
- **Multiattack.** The ettercap makes one Bite attack and one Claw attack.
- **Bite.** Melee Attack Roll: +4, reach 5 ft. Hit: 5 (1d6 + 2) Piercing damage plus 2 (1d4) Poison damage, and the target has the Poisoned condition until the start of the ettercap’s next turn.
- **Claw.** Melee Attack Roll: +4, reach 5 ft. Hit: 7 (2d4 + 2) Slashing damage.
- **Web Strand (Recharge 5+ (1d6)).** Dexterity Saving Throw: DC 12, one Large or smaller creature the ettercap can see within 30 feet. Failure: The target has the Restrained condition until the web is destroyed (AC 10; HP 5; Vulnerability to Fire damage; Immunity to Bludgeoning, Poison, and Psychic damage).

## Bonus Actions
- **Reel.** The ettercap pulls one creature within 30 feet of itself that is Restrained by its Web Strand up to 25 feet straight toward itself.
