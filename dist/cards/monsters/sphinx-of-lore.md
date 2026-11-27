---
id: monsters:sphinx-of-lore
type: monsters
name: "Sphinx of Lore"
summary: "CR 11 Large celestial (lawful neutral); AC 17, HP 170, XP 7200"
refs:
  - ability-scores:con
  - ability-scores:int
  - ability-scores:wis
  - conditions:charmed
  - conditions:frightened
  - damage-types:psychic
  - damage-types:slashing
  - proficiencies:skill-arcana
  - proficiencies:skill-history
  - proficiencies:skill-perception
  - proficiencies:skill-religion
  - spells:detect-magic
  - spells:dispel-magic
  - spells:identify
  - spells:legend-lore
  - spells:locate-object
  - spells:mage-hand
  - spells:minor-illusion
  - spells:plane-shift
  - spells:prestidigitation
  - spells:remove-curse
  - spells:tongues
---
# Sphinx of Lore (`monsters:sphinx-of-lore`)
*Large celestial, lawful neutral* | **CR** 11 (XP 7,200 (8,400 in lair); PB +4)
**AC** 17 | **HP** 170 (20d10 + 60) | **Speed** 40 ft., fly 60 ft.
**STR** 18 (+4) | **DEX** 15 (+2) | **CON** 16 (+3) | **INT** 18 (+4) | **WIS** 18 (+4) | **CHA** 18 (+4)
**Skills:** [Skill: Arcana](../proficiencies/skill-arcana.md) +12, [Skill: History](../proficiencies/skill-history.md) +12, [Skill: Perception](../proficiencies/skill-perception.md) +8, [Skill: Religion](../proficiencies/skill-religion.md) +12
**Damage Resistances:** necrotic, radiant
**Damage Immunities:** psychic
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Frightened](../conditions/frightened.md)
**Senses:** Truesight 120 ft., Passive Perception 18
**Languages:** Celestial, Common

## Traits
- **Inscrutable.** No magic can observe the sphinx remotely or detect its thoughts without its permission. Wisdom (Insight) checks made to ascertain its intentions or sincerity are made with Disadvantage.
- **Legendary Resistance (3/day).** If the sphinx fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The sphinx makes three Claw attacks.
- **Claw.** Melee Attack Roll: +8, reach 5 ft. Hit: 14 (3d6 + 4) Slashing damage.
- **Mind-Rending Roar (Recharge 5+ (1d6)).** Wisdom Saving Throw: DC 16, each enemy in a 300-foot Emanation originating from the sphinx. Failure: 35 (10d6) Psychic damage, and the target has the Incapacitated condition until the start of the sphinx’s next turn.
- **Spellcasting.** The sphinx casts one of the following spells, requiring no Material components and using Intelligence as the spellcasting ability (spell save DC 16):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Identify](../spells/identify.md) (level 1), [Mage Hand](../spells/mage-hand.md), [Minor Illusion](../spells/minor-illusion.md), [Prestidigitation](../spells/prestidigitation.md)
  - 1/day: [Dispel Magic](../spells/dispel-magic.md) (level 3), [Legend Lore](../spells/legend-lore.md) (level 5), [Locate Object](../spells/locate-object.md) (level 2), [Plane Shift](../spells/plane-shift.md) (level 7), [Remove Curse](../spells/remove-curse.md) (level 3), [Tongues](../spells/tongues.md) (level 3)

## Legendary Actions
- **Arcane Prowl.** The sphinx can teleport up to 30 feet to an unoccupied space it can see, and it makes one Claw attack.
- **Weight of Years.** Constitution Saving Throw: DC 16, one creature the sphinx can see within 120 feet. Failure: The target gains 1 Exhaustion level. While the target has any Exhaustion levels, it appears 3d10 years older. Failure or Success: The sphinx can’t take this action again until the start of its next turn.
