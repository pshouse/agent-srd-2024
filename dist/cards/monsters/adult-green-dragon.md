---
id: monsters:adult-green-dragon
type: monsters
name: "Adult Green Dragon"
summary: "CR 15 Huge dragon (lawful evil); AC 19, HP 207, XP 13000"
refs:
  - ability-scores:cha
  - ability-scores:con
  - conditions:poisoned
  - damage-types:poison
  - damage-types:slashing
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-wis
  - proficiencies:skill-deception
  - proficiencies:skill-perception
  - proficiencies:skill-persuasion
  - proficiencies:skill-stealth
  - spells:detect-magic
  - spells:geas
  - spells:mind-spike
---
# Adult Green Dragon (`monsters:adult-green-dragon`)
*Huge dragon, lawful evil* | **CR** 15 (XP 13,000 (15,000 in lair); PB +5)
**AC** 19 | **HP** 207 (18d12 + 90) | **Speed** 40 ft., fly 80 ft., swim 40 ft.
**STR** 23 (+6) | **DEX** 12 (+1) | **CON** 21 (+5) | **INT** 18 (+4) | **WIS** 15 (+2) | **CHA** 18 (+4)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +6, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +7
**Skills:** [Skill: Deception](../proficiencies/skill-deception.md) +9, [Skill: Perception](../proficiencies/skill-perception.md) +12, [Skill: Persuasion](../proficiencies/skill-persuasion.md) +9, [Skill: Stealth](../proficiencies/skill-stealth.md) +6
**Damage Immunities:** poison
**Condition Immunities:** [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 22
**Languages:** Common, Draconic

## Traits
- **Amphibious.** The dragon can breathe air and water.
- **Legendary Resistance (3/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks. It can replace one attack with a use of Spellcasting to cast Mind Spike (level 3 version).
- **Rend.** Melee Attack Roll: +11, reach 10 ft. Hit: 15 (2d8 + 6) Slashing damage plus 7 (2d6) Poison damage.
- **Poison Breath (Recharge 5+ (1d6)).** Constitution Saving Throw: DC 18, each creature in a 60-foot Cone. Failure: 56 (16d6) Poison damage. Success: Half damage.
- **Spellcasting.** The dragon casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 17):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Mind Spike](../spells/mind-spike.md) (level 3)
  - 1/day: [Geas](../spells/geas.md) (level 5)

## Legendary Actions
- **Mind Invasion.** The dragon uses Spellcasting to cast Mind Spike (level 3 version).
- **Noxious Miasma.** Constitution Saving Throw: DC 17, each creature in a 20-foot-radius Sphere centered on a point the dragon can see within 90 feet. Failure: 7 (2d6) Poison damage, and the target takes a −2 penalty to AC until the end of its next turn. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
