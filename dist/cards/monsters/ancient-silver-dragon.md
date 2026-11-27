---
id: monsters:ancient-silver-dragon
type: monsters
name: "Ancient Silver Dragon"
summary: "CR 23 Gargantuan dragon (lawful good); AC 22, HP 468, XP 50000"
refs:
  - ability-scores:cha
  - ability-scores:con
  - ability-scores:dex
  - damage-types:cold
  - damage-types:slashing
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-wis
  - proficiencies:skill-history
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
  - spells:control-weather
  - spells:detect-magic
  - spells:hold-monster
  - spells:ice-knife
  - spells:ice-storm
  - spells:shapechange
  - spells:teleport
  - spells:zone-of-truth
---
# Ancient Silver Dragon (`monsters:ancient-silver-dragon`)
*Gargantuan dragon, lawful good* | **CR** 23 (XP 50,000 (62,000 in lair); PB +7)
**AC** 22 | **HP** 468 (24d20 + 216) | **Speed** 40 ft., fly 80 ft.
**STR** 30 (+10) | **DEX** 10 (+0) | **CON** 29 (+9) | **INT** 18 (+4) | **WIS** 15 (+2) | **CHA** 26 (+8)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +7, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +9
**Skills:** [Skill: History](../proficiencies/skill-history.md) +11, [Skill: Perception](../proficiencies/skill-perception.md) +16, [Skill: Stealth](../proficiencies/skill-stealth.md) +7
**Damage Immunities:** cold
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 26
**Languages:** Common, Draconic

## Traits
- **Legendary Resistance (4/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks. It can replace one attack with a use of (A) Paralyzing Breath or (B) Spellcasting to cast Ice Knife (level 2 version).
- **Rend.** Melee Attack Roll: +17, reach 15 ft. Hit: 19 (2d8 + 10) Slashing damage plus 9 (2d8) Cold damage.
- **Cold Breath (Recharge 5+ (1d6)).** Constitution Saving Throw: DC 24, each creature in a 90-foot Cone. Failure: 67 (15d8) Cold damage. Success: Half damage.
- **Paralyzing Breath.** Constitution Saving Throw: DC 24, each creature in a 90-foot Cone. First Failure: The target has the Incapacitated condition until the end of its next turn, when it repeats the save. Second Failure: The target has the Paralyzed condition, and it repeats the save at the end of each of its turns, ending the effect on itself on a success. After 1 minute, it succeeds automatically.
- **Spellcasting.** The dragon casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 23, +15 to hit with spell attacks):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Hold Monster](../spells/hold-monster.md) (level 5), [Ice Knife](../spells/ice-knife.md) (level 2), [Shapechange](../spells/shapechange.md) (level 9; Beast or Humanoid form only, no Temporary Hit Points gained from the spell, and no Concentration or Temporary Hit Points required to maintain the spell)
  - 1/day: [Control Weather](../spells/control-weather.md) (level 8), [Ice Storm](../spells/ice-storm.md) (level 7), [Teleport](../spells/teleport.md) (level 7), [Zone of Truth](../spells/zone-of-truth.md) (level 2)

## Legendary Actions
- **Chill.** The dragon uses Spellcasting to cast Hold Monster. The dragon can’t take this action again until the start of its next turn.
- **Cold Gale.** Dexterity Saving Throw: DC 23, each creature in a 60-foot-long, 10-foot-wide Line. Failure: 14 (4d6) Cold damage, and the target is pushed up to 30 feet straight away from the dragon. Success: Half damage only. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
