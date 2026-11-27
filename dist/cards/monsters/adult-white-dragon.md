---
id: monsters:adult-white-dragon
type: monsters
name: "Adult White Dragon"
summary: "CR 13 Huge dragon (chaotic evil); AC 18, HP 200, XP 10000"
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
# Adult White Dragon (`monsters:adult-white-dragon`)
*Huge dragon, chaotic evil* | **CR** 13 (XP 10,000 (11,500 in lair); PB +5)
**AC** 18 | **HP** 200 (16d12 + 96) | **Speed** 40 ft., burrow 30 ft., fly 80 ft., swim 40 ft.
**STR** 22 (+6) | **DEX** 10 (+0) | **CON** 22 (+6) | **INT** 8 (-1) | **WIS** 12 (+1) | **CHA** 12 (+1)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +5, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +6
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +11, [Skill: Stealth](../proficiencies/skill-stealth.md) +5
**Damage Immunities:** cold
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 21
**Languages:** Common, Draconic

## Traits
- **Ice Walk.** The dragon can move across and climb icy surfaces without needing to make an ability check. Additionally, Difficult Terrain composed of ice or snow doesn’t cost it extra movement.
- **Legendary Resistance (3/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks.
- **Rend.** Melee Attack Roll: +11, reach 10 ft. Hit: 13 (2d6 + 6) Slashing damage plus 4 (1d8) Cold damage.
- **Cold Breath (Recharge 5+ (1d6)).** Constitution Saving Throw: DC 19, each creature in a 60-foot Cone. Failure: 54 (12d8) Cold damage. Success: Half damage.

## Legendary Actions
- **Freezing Burst.** Constitution Saving Throw: DC 14, each creature in a 30-foot-radius Sphere centered on a point the dragon can see within 120 feet. Failure: 7 (2d6) Cold damage, and the target’s Speed is 0 until the end of the target’s next turn. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Frightful Presence.** The dragon casts Fear, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 14). The dragon can’t take this action again until the start of its next turn.
  - Other: [Fear](../spells/fear.md) (level 3)
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
