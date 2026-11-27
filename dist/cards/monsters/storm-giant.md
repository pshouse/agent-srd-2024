---
id: monsters:storm-giant
type: monsters
name: "Storm Giant"
summary: "CR 13 Huge giant (chaotic good); AC 16, HP 230, XP 10000"
refs:
  - ability-scores:dex
  - ability-scores:wis
  - damage-types:lightning
  - damage-types:slashing
  - proficiencies:saving-throw-cha
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-str
  - proficiencies:saving-throw-wis
  - proficiencies:skill-arcana
  - proficiencies:skill-athletics
  - proficiencies:skill-history
  - proficiencies:skill-perception
  - spells:control-weather
  - spells:detect-magic
  - spells:light
---
# Storm Giant (`monsters:storm-giant`)
*Huge giant, chaotic good* | **CR** 13 (XP 10,000; PB +5)
**AC** 16 | **HP** 230 (20d12 + 100) | **Speed** 50 ft., fly 25 ft., hover True, swim 50 ft.
**STR** 29 (+9) | **DEX** 14 (+2) | **CON** 20 (+5) | **INT** 16 (+3) | **WIS** 20 (+5) | **CHA** 18 (+4)
**Saving Throws:** [Saving Throw: STR](../proficiencies/saving-throw-str.md) +14, [Saving Throw: CON](../proficiencies/saving-throw-con.md) +10, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +10, [Saving Throw: CHA](../proficiencies/saving-throw-cha.md) +9
**Skills:** [Skill: Arcana](../proficiencies/skill-arcana.md) +8, [Skill: Athletics](../proficiencies/skill-athletics.md) +14, [Skill: History](../proficiencies/skill-history.md) +8, [Skill: Perception](../proficiencies/skill-perception.md) +10
**Damage Resistances:** cold
**Damage Immunities:** lightning, thunder
**Senses:** Darkvision 120 ft., Truesight 30 ft., Passive Perception 20
**Languages:** Common, Giant

## Traits
- **Amphibious.** The giant can breathe air and water.

## Actions
- **Multiattack.** The giant makes two attacks, using Storm Sword or Thunderbolt in any combination.
- **Storm Sword.** Melee Attack Roll: +14, reach 10 ft. Hit: 23 (4d6 + 9) Slashing damage plus 13 (3d8) Lightning damage.
- **Thunderbolt.** Ranged Attack Roll: +14, range 500 ft. Hit: 22 (2d12 + 9) Lightning damage, and the target has the Blinded and Deafened conditions until the start of the giant’s next turn.
- **Lightning Storm (Recharge 5+ (1d6)).** Dexterity Saving Throw: DC 18, each creature in a 10-foot-radius, 40-foot-high Cylinder originating from a point the giant can see within 500 feet. Failure: 55 (10d10) Lightning damage. Success: Half damage.
- **Spellcasting.** The giant casts one of the following spells, requiring no Material components and using Wisdom as the spellcasting ability (spell save DC 18):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Light](../spells/light.md)
  - 1/day: [Control Weather](../spells/control-weather.md) (level 8)
