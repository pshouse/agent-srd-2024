---
id: monsters:succubus
type: monsters
name: "Succubus"
summary: "CR 4 Medium fiend (neutral evil); AC 15, HP 71, XP 1100"
refs:
  - ability-scores:cha
  - ability-scores:con
  - damage-types:psychic
  - proficiencies:skill-deception
  - proficiencies:skill-insight
  - proficiencies:skill-perception
  - proficiencies:skill-persuasion
  - proficiencies:skill-stealth
  - spells:dominate-person
---
# Succubus (`monsters:succubus`)
*Medium fiend, neutral evil* | **CR** 4 (XP 1,100; PB +2)
**AC** 15 | **HP** 71 (13d8 + 13) | **Speed** 30 ft., fly 60 ft.
**STR** 8 (-1) | **DEX** 17 (+3) | **CON** 13 (+1) | **INT** 15 (+2) | **WIS** 12 (+1) | **CHA** 20 (+5)
**Skills:** [Skill: Deception](../proficiencies/skill-deception.md) +9, [Skill: Insight](../proficiencies/skill-insight.md) +5, [Skill: Perception](../proficiencies/skill-perception.md) +5, [Skill: Persuasion](../proficiencies/skill-persuasion.md) +9, [Skill: Stealth](../proficiencies/skill-stealth.md) +7
**Damage Resistances:** cold, fire, poison, psychic
**Senses:** Darkvision 60 ft., Passive Perception 15
**Languages:** Abyssal, Common, Infernal; telepathy 60 ft.

## Traits
- **Incubus Form.** When the succubus finishes a Long Rest, it can shape-shift into an Incubus, using that stat block instead of this one.

## Actions
- **Multiattack.** The succubus makes one Fiendish Touch attack and uses Charm or Draining Kiss.
- **Fiendish Touch.** Melee Attack Roll: +7, reach 5 ft. Hit: 16 (2d10 + 5) Psychic damage.
- **Charm.** The succubus casts Dominate Person (level 8 version), requiring no spell components and using Charisma as the spellcasting ability (spell save DC 15).
  - Other: [Dominate Person](../spells/dominate-person.md) (level 8)
- **Draining Kiss.** Constitution Saving Throw: DC 15, one creature Charmed by the succubus within 5 feet. Failure: 13 (3d8) Psychic damage. Success: Half damage. Failure or Success: The target’s Hit Point maximum decreases by an amount equal to the damage taken.

## Bonus Actions
- **Shape-Shift.** The succubus shape-shifts into a Medium or Small Humanoid, or it returns to its true form. Its game statistics are the same in each form, except its Fly Speed is available only in its true form. Any equipment it is wearing or carrying isn’t transformed.
