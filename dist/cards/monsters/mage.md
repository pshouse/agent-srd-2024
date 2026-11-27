---
id: monsters:mage
type: monsters
name: "Mage"
summary: "CR 6 Medium or small humanoid (neutral); AC 15, HP 81, XP 2300"
refs:
  - ability-scores:int
  - damage-types:force
  - proficiencies:saving-throw-int
  - proficiencies:saving-throw-wis
  - proficiencies:skill-arcana
  - proficiencies:skill-history
  - proficiencies:skill-perception
  - spells:cone-of-cold
  - spells:counterspell
  - spells:detect-magic
  - spells:fireball
  - spells:fly
  - spells:invisibility
  - spells:light
  - spells:mage-armor
  - spells:mage-hand
  - spells:misty-step
  - spells:prestidigitation
  - spells:shield
---
# Mage (`monsters:mage`)
*Medium or small humanoid, neutral* | **CR** 6 (XP 2,300; PB +3)
**AC** 15 | **HP** 81 (18d8) | **Speed** 30 ft.
**STR** 9 (-1) | **DEX** 14 (+2) | **CON** 11 (+0) | **INT** 17 (+3) | **WIS** 12 (+1) | **CHA** 11 (+0)
**Saving Throws:** [Saving Throw: INT](../proficiencies/saving-throw-int.md) +6, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +4
**Skills:** [Skill: Arcana](../proficiencies/skill-arcana.md) +6, [Skill: History](../proficiencies/skill-history.md) +6, [Skill: Perception](../proficiencies/skill-perception.md) +4
**Senses:** Passive Perception 14
**Languages:** Common plus three other languages
**Gear:** Wand

## Actions
- **Multiattack.** The mage makes three Arcane Burst attacks.
- **Arcane Burst.** Melee or Ranged Attack Roll: +6, reach 5 ft. or range 120 ft. Hit: 16 (3d8 + 3) Force damage.
- **Spellcasting.** The mage casts one of the following spells, using Intelligence as the spellcasting ability (spell save DC 14):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Light](../spells/light.md), [Mage Armor](../spells/mage-armor.md) (level 1; Included in AC), [Mage Hand](../spells/mage-hand.md), [Prestidigitation](../spells/prestidigitation.md)
  - 2/day: [Fireball](../spells/fireball.md) (level 4), [Invisibility](../spells/invisibility.md) (level 2)
  - 1/day: [Cone of Cold](../spells/cone-of-cold.md) (level 5), [Fly](../spells/fly.md) (level 3)

## Bonus Actions
- **Misty Step (3/day).** The mage casts Misty Step, using the same spellcasting ability as Spellcasting.
  - Other: [Misty Step](../spells/misty-step.md) (level 2)

## Reactions
- **Protective Magic (3/day).** The mage casts Counterspell or Shield in response to the spell’s trigger, using the same spellcasting ability as Spellcasting.
  - Other: [Counterspell](../spells/counterspell.md) (level 3), [Shield](../spells/shield.md) (level 1)
