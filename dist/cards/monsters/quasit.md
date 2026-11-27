---
id: monsters:quasit
type: monsters
name: "Quasit"
summary: "CR 1 Tiny fiend (chaotic evil); AC 13, HP 25, XP 200"
refs:
  - ability-scores:cha
  - ability-scores:wis
  - conditions:poisoned
  - damage-types:slashing
  - proficiencies:skill-stealth
  - spells:invisibility
---
# Quasit (`monsters:quasit`)
*Tiny fiend, chaotic evil* | **CR** 1 (XP 200; PB +2)
**AC** 13 | **HP** 25 (10d4) | **Speed** 40 ft.
**STR** 5 (-3) | **DEX** 17 (+3) | **CON** 10 (+0) | **INT** 7 (-2) | **WIS** 10 (+0) | **CHA** 10 (+0)
**Skills:** [Skill: Stealth](../proficiencies/skill-stealth.md) +5
**Damage Resistances:** cold, fire, lightning
**Damage Immunities:** poison
**Condition Immunities:** [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 120 ft., Passive Perception 10
**Languages:** Abyssal, Common

## Traits
- **Magic Resistance.** The quasit has Advantage on saving throws against spells and other magical effects.

## Actions
- **Rend.** Melee Attack Roll: +5, reach 5 ft. Hit: 5 (1d4 + 3) Slashing damage, and the target has the Poisoned condition until the start of the quasit’s next turn.
- **Invisibility.** The quasit casts Invisibility on itself, requiring no spell components and using Charisma as the spellcasting ability.
  - Other: [Invisibility](../spells/invisibility.md) (level 2; Self only)
- **Scare (1/day).** Wisdom Saving Throw: DC 10, one creature within 20 feet. Failure: The target has the Frightened condition. At the end of each of its turns, the target repeats the save, ending the effect on itself on a success. After 1 minute, it succeeds automatically.
- **Shape-Shift.** The quasit shape-shifts to resemble a bat (Speed 10 ft., Fly 40 ft.), a centipede (40 ft., Climb 40 ft.), or a toad (40 ft., Swim 40 ft.), or it returns to its true form. Its game statistics are the same in each form, except for its Speed. Any equipment it is wearing or carrying isn’t transformed.
