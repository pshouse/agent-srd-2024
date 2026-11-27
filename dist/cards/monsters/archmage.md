---
id: monsters:archmage
type: monsters
name: "Archmage"
summary: "CR 12 Medium or small humanoid (neutral); AC 17, HP 170, XP 8000"
refs:
  - ability-scores:int
  - conditions:charmed
  - damage-types:force
  - proficiencies:saving-throw-int
  - proficiencies:saving-throw-wis
  - proficiencies:skill-arcana
  - proficiencies:skill-history
  - proficiencies:skill-perception
  - spells:cone-of-cold
  - spells:counterspell
  - spells:detect-magic
  - spells:detect-thoughts
  - spells:disguise-self
  - spells:fly
  - spells:invisibility
  - spells:light
  - spells:lightning-bolt
  - spells:mage-armor
  - spells:mage-hand
  - spells:mind-blank
  - spells:misty-step
  - spells:prestidigitation
  - spells:scrying
  - spells:shield
  - spells:teleport
---
# Archmage (`monsters:archmage`)
*Medium or small humanoid, neutral* | **CR** 12 (XP 8,000; PB +4)
**AC** 17 | **HP** 170 (31d8 + 31) | **Speed** 30 ft.
**STR** 10 (+0) | **DEX** 14 (+2) | **CON** 12 (+1) | **INT** 20 (+5) | **WIS** 15 (+2) | **CHA** 16 (+3)
**Saving Throws:** [Saving Throw: INT](../proficiencies/saving-throw-int.md) +9, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +6
**Skills:** [Skill: Arcana](../proficiencies/skill-arcana.md) +13, [Skill: History](../proficiencies/skill-history.md) +9, [Skill: Perception](../proficiencies/skill-perception.md) +6
**Damage Immunities:** psychic
**Condition Immunities:** [Charmed](../conditions/charmed.md) (with Mind Blank)
**Senses:** Passive Perception 16
**Languages:** Common plus five other languages
**Gear:** Wand

## Traits
- **Magic Resistance.** The archmage has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The archmage makes four Arcane Burst attacks.
- **Arcane Burst.** Melee or Ranged Attack Roll: +9, reach 5 ft. or range 150 ft. Hit: 27 (4d10 + 5) Force damage.
- **Spellcasting.** The archmage casts one of the following spells, using Intelligence as the spellcasting ability (spell save DC 17):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Detect Thoughts](../spells/detect-thoughts.md) (level 2), [Disguise Self](../spells/disguise-self.md) (level 1), [Invisibility](../spells/invisibility.md) (level 2), [Light](../spells/light.md), [Mage Armor](../spells/mage-armor.md) (level 1; Included in AC), [Mage Hand](../spells/mage-hand.md), [Prestidigitation](../spells/prestidigitation.md)
  - 2/day: [Fly](../spells/fly.md) (level 3), [Lightning Bolt](../spells/lightning-bolt.md) (level 7)
  - 1/day: [Cone of Cold](../spells/cone-of-cold.md) (level 9), [Mind Blank](../spells/mind-blank.md) (level 8; Cast before combat), [Scrying](../spells/scrying.md) (level 5), [Teleport](../spells/teleport.md) (level 7)

## Bonus Actions
- **Misty Step (3/day).** The mage casts Misty Step, using the same spellcasting ability as Spellcasting.
  - Other: [Misty Step](../spells/misty-step.md) (level 2)

## Reactions
- **Protective Magic (3/day).** The archmage casts Counterspell or Shield in response to the spell’s trigger, using the same spellcasting ability as Spellcasting.
  - Other: [Counterspell](../spells/counterspell.md) (level 3), [Shield](../spells/shield.md) (level 1)
