---
id: monsters:ancient-green-dragon
type: monsters
name: "Ancient Green Dragon"
summary: "CR 22 Gargantuan dragon (lawful evil); AC 21, HP 402, XP 41000"
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
  - spells:modify-memory
---
# Ancient Green Dragon (`monsters:ancient-green-dragon`)
*Gargantuan dragon, lawful evil* | **CR** 22 (XP 41,000 (50,000 in lair); PB +7)
**AC** 21 | **HP** 402 (23d20 + 161) | **Speed** 40 ft., fly 80 ft., swim 40 ft.
**STR** 27 (+8) | **DEX** 12 (+1) | **CON** 25 (+7) | **INT** 20 (+5) | **WIS** 17 (+3) | **CHA** 22 (+6)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +8, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +10
**Skills:** [Skill: Deception](../proficiencies/skill-deception.md) +13, [Skill: Perception](../proficiencies/skill-perception.md) +17, [Skill: Persuasion](../proficiencies/skill-persuasion.md) +13, [Skill: Stealth](../proficiencies/skill-stealth.md) +8
**Damage Immunities:** poison
**Condition Immunities:** [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 27
**Languages:** Common, Draconic

## Traits
- **Amphibious.** The dragon can breathe air and water.
- **Legendary Resistance (4/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks. It can replace one attack with a use of Spellcasting to cast Mind Spike (level 5 version).
- **Rend.** Melee Attack Roll: +15, reach 15 ft. Hit: 17 (2d8 + 8) Slashing damage plus 10 (3d6) Poison damage.
- **Poison Breath (Recharge 5+ (1d6)).** Constitution Saving Throw: DC 22, each creature in a 90-foot Cone. Failure: 77 (22d6) Poison damage. Success: Half damage.
- **Spellcasting.** The dragon casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 21):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Mind Spike](../spells/mind-spike.md) (level 5)
  - 1/day: [Geas](../spells/geas.md) (level 5), [Modify Memory](../spells/modify-memory.md) (level 5)

## Legendary Actions
- **Mind Invasion.** The dragon uses Spellcasting to cast Mind Spike (level 5 version).
- **Noxious Miasma.** Constitution Saving Throw: DC 21, each creature in a 30-foot-radius Sphere centered on a point the dragon can see within 90 feet. Failure: 17 (5d6) Poison damage, and the target takes a −2 penalty to AC until the end of its next turn. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
