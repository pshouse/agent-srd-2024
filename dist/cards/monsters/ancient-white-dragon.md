---
id: monsters:ancient-white-dragon
type: monsters
name: "Ancient White Dragon"
summary: "CR 20 Gargantuan dragon (chaotic evil); AC 20, HP 333, XP 25000"
refs:
  - ability-scores:cha
  - ability-scores:con
  - damage-types:cold
  - damage-types:slashing
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-wis
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
  - spells:fear
---
# Ancient White Dragon (`monsters:ancient-white-dragon`)
*Gargantuan dragon, chaotic evil* | **CR** 20 (XP 25,000 (33,000 in lair); PB +6)
**AC** 20 | **HP** 333 (18d20 + 144) | **Speed** 40 ft., burrow 40 ft., fly 80 ft., swim 40 ft.
**STR** 26 (+8) | **DEX** 10 (+0) | **CON** 26 (+8) | **INT** 10 (+0) | **WIS** 13 (+1) | **CHA** 18 (+4)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +6, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +7
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +13, [Skill: Stealth](../proficiencies/skill-stealth.md) +6
**Damage Immunities:** cold
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 23
**Languages:** Common, Draconic

## Traits
- **Ice Walk.** The dragon can move across and climb icy surfaces without needing to make an ability check. Additionally, Difficult Terrain composed of ice or snow doesn’t cost it extra movement.
- **Legendary Resistance (4/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks.
- **Rend.** Melee Attack Roll: +14, reach 15 ft. Hit: 17 (2d8 + 8) Slashing damage plus 7 (2d6) Cold damage.
- **Cold Breath (Recharge 5+ (1d6)).** Constitution Saving Throw: DC 22, each creature in a 90-foot Cone. Failure: 63 (14d8) Cold damage. Success: Half damage.

## Legendary Actions
- **Freezing Burst.** Constitution Saving Throw: DC 20, each creature in a 30-foot-radius Sphere centered on a point the dragon can see within 120 feet. Failure: 14 (4d6) Cold damage, and the target’s Speed is 0 until the end of the target’s next turn. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Frightful Presence.** The dragon casts Fear, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 18). The dragon can’t take this action again until the start of its next turn.
  - Other: [Fear](../spells/fear.md) (level 3)
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
