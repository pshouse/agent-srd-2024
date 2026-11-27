---
id: monsters:ancient-gold-dragon
type: monsters
name: "Ancient Gold Dragon"
summary: "CR 24 Gargantuan dragon (lawful good); AC 22, HP 546, XP 62000"
refs:
  - ability-scores:cha
  - ability-scores:dex
  - ability-scores:str
  - damage-types:fire
  - damage-types:force
  - damage-types:slashing
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-wis
  - proficiencies:skill-insight
  - proficiencies:skill-perception
  - proficiencies:skill-persuasion
  - proficiencies:skill-stealth
  - spells:detect-magic
  - spells:flame-strike
  - spells:guiding-bolt
  - spells:shapechange
  - spells:word-of-recall
  - spells:zone-of-truth
---
# Ancient Gold Dragon (`monsters:ancient-gold-dragon`)
*Gargantuan dragon, lawful good* | **CR** 24 (XP 62,000 (75,000 in lair); PB +7)
**AC** 22 | **HP** 546 (28d20 + 252) | **Speed** 40 ft., fly 80 ft., swim 40 ft.
**STR** 30 (+10) | **DEX** 14 (+2) | **CON** 29 (+9) | **INT** 18 (+4) | **WIS** 17 (+3) | **CHA** 28 (+9)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +9, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +10
**Skills:** [Skill: Insight](../proficiencies/skill-insight.md) +10, [Skill: Perception](../proficiencies/skill-perception.md) +17, [Skill: Persuasion](../proficiencies/skill-persuasion.md) +16, [Skill: Stealth](../proficiencies/skill-stealth.md) +9
**Damage Immunities:** fire
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 27
**Languages:** Common, Draconic

## Traits
- **Amphibious.** The dragon can breathe air and water.
- **Legendary Resistance (4/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks. It can replace one attack with a use of (A) Spellcasting to cast Guiding Bolt (level 4 version) or (B) Weakening Breath.
- **Rend.** Melee Attack Roll: +17 to hit, reach 15 ft. Hit: 19 (2d8 + 10) Slashing damage plus 9 (2d8) Fire damage.
- **Fire Breath (Recharge 5+ (1d6)).** Dexterity Saving Throw: DC 24, each creature in a 90-foot Cone. Failure: 71 (13d10) Fire damage. Success: Half damage.
- **Spellcasting.** The dragon casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 24, +16 to hit with spell attacks):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Guiding Bolt](../spells/guiding-bolt.md) (level 4), [Shapechange](../spells/shapechange.md) (level 9; Beast or Humanoid form only, no Temporary Hit Points gained from the spell, and no Concentration or Temporary Hit Points required to maintain the spell)
  - 1/day: [Flame Strike](../spells/flame-strike.md) (level 6), [Word of Recall](../spells/word-of-recall.md) (level 6), [Zone of Truth](../spells/zone-of-truth.md) (level 2)
- **Weakening Breath.** Strength Saving Throw: DC 24, each creature that isn’t currently affected by this breath in a 90-foot Cone. Failure: The target has Disadvantage on Strength-based D20 Tests and subtracts 5 (1d10) from its damage rolls. It repeats the save at the end of each of its turns, ending the effect on itself on a success. After 1 minute, it succeeds automatically.

## Legendary Actions
- **Banish.** Charisma Saving Throw: DC 24, one creature the dragon can see within 120 feet. Failure: 24 (7d6) Force damage, and the target has the Incapacitated condition and is transported to a harmless demiplane until the start of the dragon’s next turn, at which point it reappears in an unoccupied space of the dragon’s choice within 120 feet of the dragon. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Guiding Light.** The dragon uses Spellcasting to cast Guiding Bolt (level 4 version).
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
