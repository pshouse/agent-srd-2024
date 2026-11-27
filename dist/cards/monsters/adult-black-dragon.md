---
id: monsters:adult-black-dragon
type: monsters
name: "Adult Black Dragon"
summary: "CR 14 Huge dragon (chaotic evil); AC 19, HP 195, XP 11500"
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
  - spells:detect-magic
  - spells:fear
  - spells:speak-with-dead
  - spells:vitriolic-sphere
---
# Adult Black Dragon (`monsters:adult-black-dragon`)
*Huge dragon, chaotic evil* | **CR** 14 (XP 11,500 (13,000 in lair); PB +5)
**AC** 19 | **HP** 195 (17d12 + 85) | **Speed** 40 ft., fly 80 ft., swim 40 ft.
**STR** 23 (+6) | **DEX** 14 (+2) | **CON** 21 (+5) | **INT** 14 (+2) | **WIS** 13 (+1) | **CHA** 19 (+4)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +7, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +6
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +11, [Skill: Stealth](../proficiencies/skill-stealth.md) +7
**Damage Immunities:** acid
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 21
**Languages:** Common, Draconic

## Traits
- **Amphibious.** The dragon can breathe air and water.
- **Legendary Resistance (3/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks. It can replace one attack with a use of Spellcasting to cast Acid Arrow (level 3 version).
- **Rend.** Melee Attack Roll: +11, reach 10 ft. Hit: 13 (2d6 + 6) Slashing damage plus 4 (1d8) Acid damage.
- **Acid Breath (Recharge 5+ (1d6)).** Dexterity Saving Throw: DC 18, each creature in a 60-foot-long, 5-footwide Line. Failure: 54 (12d8) Acid damage. Success: Half damage.
- **Spellcasting.** The dragon casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 17, +9 to hit with spell attacks):
  - At will: [Acid Arrow](../spells/acid-arrow.md) (level 3), [Detect Magic](../spells/detect-magic.md) (level 1), [Fear](../spells/fear.md) (level 3)
  - 1/day: [Speak with Dead](../spells/speak-with-dead.md) (level 3), [Vitriolic Sphere](../spells/vitriolic-sphere.md) (level 4)

## Legendary Actions
- **Cloud of Insects.** Dexterity Saving Throw: DC 17, one creature the dragon can see within 120 feet. Failure: 22 (4d10) Poison damage, and the target has Disadvantage on saving throws to maintain Concentration until the end of its next turn. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Frightful Presence.** The dragon uses Spellcasting to cast Fear. The dragon can’t take this action again until the start of its next turn.
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
