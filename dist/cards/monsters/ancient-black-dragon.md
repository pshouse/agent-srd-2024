---
id: monsters:ancient-black-dragon
type: monsters
name: "Ancient Black Dragon"
summary: "CR 21 Gargantuan dragon (chaotic evil); AC 22, HP 367, XP 33000"
refs:
  - ability-scores:cha
  - ability-scores:dex
  - damage-types:acid
  - damage-types:poison
  - damage-types:slashing
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-wis
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
  - spells:acid-arrow
  - spells:create-undead
  - spells:detect-magic
  - spells:fear
  - spells:speak-with-dead
  - spells:vitriolic-sphere
---
# Ancient Black Dragon (`monsters:ancient-black-dragon`)
*Gargantuan dragon, chaotic evil* | **CR** 21 (XP 33,000 (41,000 in lair); PB +7)
**AC** 22 | **HP** 367 (21d20 + 147) | **Speed** 40 ft., fly 80 ft., swim 40 ft.
**STR** 27 (+8) | **DEX** 14 (+2) | **CON** 25 (+7) | **INT** 16 (+3) | **WIS** 15 (+2) | **CHA** 22 (+6)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +9, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +9
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +16, [Skill: Stealth](../proficiencies/skill-stealth.md) +9
**Damage Immunities:** acid
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 26
**Languages:** Common, Draconic

## Traits
- **Amphibious.** The dragon can breathe air and water.
- **Legendary Resistance (4/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks. It can replace one attack with a use of Spellcasting to cast Acid Arrow (level 4 version).
- **Rend.** Melee Attack Roll: +15, reach 15 ft. Hit: 17 (2d8 + 8) Slashing damage plus 9 (2d8) Acid damage.
- **Acid Breath (Recharge 5+ (1d6)).** Dexterity Saving Throw: DC 22, each creature in a 90-foot-long, 10-footwide Line. Failure: 67 (15d8) Acid damage. Success: Half damage.
- **Spellcasting.** The dragon casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 21, +13 to hit with spell attacks):
  - At will: [Acid Arrow](../spells/acid-arrow.md) (level 4), [Detect Magic](../spells/detect-magic.md) (level 1), [Fear](../spells/fear.md) (level 3)
  - 1/day: [Create Undead](../spells/create-undead.md) (level 6), [Speak with Dead](../spells/speak-with-dead.md) (level 3), [Vitriolic Sphere](../spells/vitriolic-sphere.md) (level 5)

## Legendary Actions
- **Cloud of Insects.** Dexterity Saving Throw: DC 21, one creature the dragon can see within 120 feet. Failure: 33 (6d10) Poison damage, and the target has Disadvantage on saving throws to maintain Concentration until the end of its next turn. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Frightful Presence.** The dragon uses Spellcasting to cast Fear. The dragon can’t take this action again until the start of its next turn.
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
