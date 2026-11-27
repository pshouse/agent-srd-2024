---
id: monsters:incubus
type: monsters
name: "Incubus"
summary: "CR 4 Medium fiend (neutral evil); AC 15, HP 66, XP 1100"
refs:
  - ability-scores:cha
  - ability-scores:wis
  - damage-types:psychic
  - proficiencies:skill-deception
  - proficiencies:skill-insight
  - proficiencies:skill-perception
  - proficiencies:skill-persuasion
  - proficiencies:skill-stealth
  - spells:disguise-self
  - spells:dream
  - spells:etherealness
  - spells:hypnotic-pattern
---
# Incubus (`monsters:incubus`)
*Medium fiend, neutral evil* | **CR** 4 (XP 1,100; PB +2)
**AC** 15 | **HP** 66 (12d8 + 12) | **Speed** 30 ft., fly 60 ft.
**STR** 8 (-1) | **DEX** 17 (+3) | **CON** 13 (+1) | **INT** 15 (+2) | **WIS** 12 (+1) | **CHA** 20 (+5)
**Skills:** [Skill: Deception](../proficiencies/skill-deception.md) +9, [Skill: Insight](../proficiencies/skill-insight.md) +5, [Skill: Perception](../proficiencies/skill-perception.md) +5, [Skill: Persuasion](../proficiencies/skill-persuasion.md) +9, [Skill: Stealth](../proficiencies/skill-stealth.md) +7
**Damage Resistances:** cold, fire, poison, psychic
**Senses:** Darkvision 60 ft., Passive Perception 15
**Languages:** Abyssal, Common, Infernal; telepathy 60 ft.

## Traits
- **Succubus Form.** When the incubus finishes a Long Rest, it can shape-shift into a Succubus, using that stat block instead of this one. Any equipment it is wearing or carrying isn’t transformed.

## Actions
- **Multiattack.** The incubus makes two Restless Touch attacks.
- **Restless Touch.** Melee Attack Roll: +7, reach 5 ft. Hit: 15 (3d6 + 5) Psychic damage, and the target is cursed for 24 hours or until the incubus dies. Until the curse ends, the target gains no benefit from finishing Short Rests.
- **Spellcasting.** The incubus casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 15):
  - At will: [Disguise Self](../spells/disguise-self.md) (level 1), [Etherealness](../spells/etherealness.md) (level 7)
  - 1/day: [Dream](../spells/dream.md) (level 5), [Hypnotic Pattern](../spells/hypnotic-pattern.md) (level 3)

## Bonus Actions
- **Nightmare (Recharge 6+ (1d6)).** Wisdom Saving Throw: DC 15, one creature the incubus can see within 60 feet. Failure: If the target has 20 Hit Points or fewer, it has the Unconscious condition for 1 hour, until it takes damage, or until a creature within 5 feet of it takes an action to wake it. Otherwise, the target takes 18 (4d8) Psychic damage.
