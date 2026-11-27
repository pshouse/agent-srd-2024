---
id: monsters:adult-gold-dragon
type: monsters
name: "Adult Gold Dragon"
summary: "CR 17 Huge dragon (lawful good); AC 19, HP 243, XP 18000"
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
  - spells:zone-of-truth
---
# Adult Gold Dragon (`monsters:adult-gold-dragon`)
*Huge dragon, lawful good* | **CR** 17 (XP 18,000 (20,000 in lair); PB +6)
**AC** 19 | **HP** 243 (18d12 + 126) | **Speed** 40 ft., fly 80 ft., swim 40 ft.
**STR** 27 (+8) | **DEX** 14 (+2) | **CON** 25 (+7) | **INT** 16 (+3) | **WIS** 15 (+2) | **CHA** 24 (+7)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +8, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +8
**Skills:** [Skill: Insight](../proficiencies/skill-insight.md) +8, [Skill: Perception](../proficiencies/skill-perception.md) +14, [Skill: Persuasion](../proficiencies/skill-persuasion.md) +13, [Skill: Stealth](../proficiencies/skill-stealth.md) +8
**Damage Immunities:** fire
**Senses:** Darkvision 120 ft., Blindsight 60 ft., Passive Perception 24
**Languages:** Common, Draconic

## Traits
- **Amphibious.** The dragon can breathe air and water.
- **Legendary Resistance (3/day).** If the dragon fails a saving throw, it can choose to succeed instead.

## Actions
- **Multiattack.** The dragon makes three Rend attacks. It can replace one attack with a use of (A) Spellcasting to cast Guiding Bolt (level 2 version) or (B) Weakening Breath.
- **Rend.** Melee Attack Roll: +14, reach 10 ft. Hit: 17 (2d8 + 8) Slashing damage plus 4 (1d8) Fire damage.
- **Fire Breath (Recharge 5+ (1d6)).** Dexterity Saving Throw: DC 21, each creature in a 60-foot Cone. Failure: 66 (12d10) Fire damage. Success: Half damage.
- **Spellcasting.** The dragon casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 21, +13 to hit with spell attacks):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Guiding Bolt](../spells/guiding-bolt.md) (level 2), [Shapechange](../spells/shapechange.md) (level 9; Beast or Humanoid form only, no Temporary Hit Points gained from the spell, and no Concentration or Temporary Hit Points required to maintain the spell)
  - 1/day: [Flame Strike](../spells/flame-strike.md) (level 5), [Zone of Truth](../spells/zone-of-truth.md) (level 2)
- **Weakening Breath.** Strength Saving Throw: DC 21, each creature that isn’t currently affected by this breath in a 60-foot Cone. Failure: The target has Disadvantage on Strength-based D20 Tests and subtracts 3 (1d6) from its damage rolls. It repeats the save at the end of each of its turns, ending the effect on itself on a success. After 1 minute, it succeeds automatically.

## Legendary Actions
- **Banish.** Charisma Saving Throw: DC 21, one creature the dragon can see within 120 feet. Failure: 10 (3d6) Force damage, and the target has the Incapacitated condition and is transported to a harmless demiplane until the start of the dragon’s next turn, at which point it re appears in an unoccupied space of the dragon’s choice within 120 feet of the dragon. Failure or Success: The dragon can’t take this action again until the start of its next turn.
- **Guiding Light.** The dragon uses Spellcasting to cast Guiding Bolt (level 2 version).
- **Pounce.** The dragon moves up to half its Speed, and it makes one Rend attack.
