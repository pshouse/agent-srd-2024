---
id: monsters:sphinx-of-valor
type: monsters
name: "Sphinx of Valor"
summary: "CR 17 Large celestial (lawful neutral); AC 17, HP 199, XP 18000"
refs:
  - ability-scores:con
  - ability-scores:wis
  - conditions:charmed
  - conditions:frightened
  - damage-types:slashing
  - damage-types:thunder
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-int
  - proficiencies:saving-throw-wis
  - proficiencies:skill-arcana
  - proficiencies:skill-perception
  - proficiencies:skill-religion
  - spells:detect-evil-and-good
  - spells:detect-magic
  - spells:dispel-magic
  - spells:greater-restoration
  - spells:heroes-feast
  - spells:thaumaturgy
  - spells:zone-of-truth
---
# Sphinx of Valor (`monsters:sphinx-of-valor`)
*Large celestial, lawful neutral* | **CR** 17 (XP 18,000 (20,000 in lair); PB +6)
**AC** 17 | **HP** 199 (19d10 + 95) | **Speed** 40 ft., fly 60 ft.
**STR** 22 (+6) | **DEX** 10 (+0) | **CON** 20 (+5) | **INT** 16 (+3) | **WIS** 23 (+6) | **CHA** 18 (+4)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +6, [Saving Throw: CON](../proficiencies/saving-throw-con.md) +11, [Saving Throw: INT](../proficiencies/saving-throw-int.md) +9, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +12
**Skills:** [Skill: Arcana](../proficiencies/skill-arcana.md) +9, [Skill: Perception](../proficiencies/skill-perception.md) +12, [Skill: Religion](../proficiencies/skill-religion.md) +15
**Damage Resistances:** necrotic, radiant
**Damage Immunities:** psychic
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Frightened](../conditions/frightened.md)
**Senses:** Truesight 120 ft., Passive Perception 22
**Languages:** Celestial, Common

## Traits
- **Inscrutable.** No magic can observe the sphinx remotely or detect its thoughts without its permission. Wisdom (Insight) checks made to ascertain its intentions or sincerity are made with Disadvantage.
- **Legendary Resistance (3/day).** If the sphinx fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The sphinx makes two Claw attacks and uses Roar.
- **Claw.** Melee Attack Roll: +12, reach 5 ft. Hit: 20 (4d6 + 6) Slashing damage.
- **Roar (3/day).** The sphinx emits a magical roar. Whenever it roars, the roar has a different effect, as detailed below (the sequence resets when it takes a Long Rest):
- **First Roar.** Wisdom Saving Throw: DC 20, each enemy in a 500-foot Emanation originating from the sphinx. Failure: The target has the Frightened condition for 1 minute.
- **Second Roar.** Wisdom Saving Throw: DC 20, each enemy in a 500-foot Emanation originating from the sphinx. Failure: The target has the Paralyzed condition, and it repeats the save at the end of each of its turns, ending the effect on itself on a success. After 1 minute, it succeeds automatically.
- **Third Roar.** Constitution Saving Throw: DC 20, each enemy in a 500-foot Emanation originating from the sphinx. Failure: 44 (8d10) Thunder damage, and the target has the Prone condition. Success: Half damage only.
- **Spellcasting.** The sphinx casts one of the following spells, requiring no Material components and using Wisdom as the spellcasting ability (spell save DC 20):
  - At will: [Detect Evil and Good](../spells/detect-evil-and-good.md) (level 1), [Thaumaturgy](../spells/thaumaturgy.md)
  - 1/day: [Detect Magic](../spells/detect-magic.md) (level 1), [Dispel Magic](../spells/dispel-magic.md) (level 3), [Greater Restoration](../spells/greater-restoration.md) (level 5), [Heroes' Feast](../spells/heroes-feast.md) (level 6), [Zone of Truth](../spells/zone-of-truth.md) (level 2)

## Legendary Actions
- **Arcane Prowl.** The sphinx can teleport up to 30 feet to an unoccupied space it can see, and it makes one Claw attack.
- **Weight of Years.** Constitution Saving Throw: DC 16, one creature the sphinx can see within 120 feet. Failure: The target gains 1 Exhaustion level. While the target has any Exhaustion levels, it appears 3d10 years older. Failure or Success: The sphinx can’t take this action again until the start of its next turn.
