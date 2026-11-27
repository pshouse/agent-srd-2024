---
id: monsters:solar
type: monsters
name: "Solar"
summary: "CR 21 Large celestial (lawful good); AC 21, HP 297, XP 33000"
refs:
  - ability-scores:cha
  - ability-scores:con
  - ability-scores:dex
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:poisoned
  - damage-types:piercing
  - damage-types:radiant
  - damage-types:slashing
  - proficiencies:skill-perception
  - spells:commune
  - spells:control-weather
  - spells:cure-wounds
  - spells:detect-evil-and-good
  - spells:dispel-evil-and-good
  - spells:lesser-restoration
  - spells:remove-curse
  - spells:resurrection
---
# Solar (`monsters:solar`)
*Large celestial, lawful good* | **CR** 21 (XP 33,000; PB +7)
**AC** 21 | **HP** 297 (22d10 + 176) | **Speed** 50 ft., fly 150 ft., hover True
**STR** 26 (+8) | **DEX** 22 (+6) | **CON** 26 (+8) | **INT** 25 (+7) | **WIS** 25 (+7) | **CHA** 30 (+10)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +14
**Damage Immunities:** poison, radiant
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Truesight 120 ft., Passive Perception 24
**Languages:** All; telepathy 120 ft.

## Traits
- **Divine Awareness.** The solar knows if it hears a lie.
- **Exalted Restoration.** If the solar dies outside Mount Celestia, its body disappears, and it gains a new body instantly, reviving with all its Hit Points somewhere in Mount Celestia.
- **Legendary Resistance (4/day).** If the solar fails a saving throw, it can choose to succeed instead.
- **Magic Resistance.** The solar has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The solar makes two Flying Sword attacks. It can replace one attack with a use of Slaying Bow.
- **Flying Sword.** Melee or Ranged Attack Roll: +15, reach 10 ft. or range 120 ft. Hit: 22 (4d6 + 8) Slashing damage plus 36 (8d8) Radiant damage. Hit or Miss: The sword magically returns to the solar’s hand or hovers within 5 feet of the solar immediately after a ranged attack.
- **Slaying Bow.** Dexterity Saving Throw: DC 21, one creature the solar can see within 600 feet. Failure: If the creature has 100 Hit Points or fewer, it dies. It otherwise takes 24 (4d8 + 6) Piercing damage plus 36 (8d8) Radiant damage.
- **Spellcasting.** The solar casts one of the following spells, requiring no Material components and using Charisma as the spellcasting ability (spell save DC 25):
  - At will: [Detect Evil and Good](../spells/detect-evil-and-good.md) (level 1)
  - 1/day: [Commune](../spells/commune.md) (level 5), [Control Weather](../spells/control-weather.md) (level 8), [Dispel Evil and Good](../spells/dispel-evil-and-good.md) (level 5), [Resurrection](../spells/resurrection.md) (level 7)

## Bonus Actions
- **Divine Aid (3/day).** The solar casts Cure Wounds (level 2 version), Lesser Restoration, or Remove Curse, using the same spellcasting ability as Spellcasting.
  - Other: [Cure Wounds](../spells/cure-wounds.md) (level 2), [Lesser Restoration](../spells/lesser-restoration.md) (level 2), [Remove Curse](../spells/remove-curse.md) (level 3)

## Legendary Actions
- **Blinding Gaze.** Constitution Saving Throw: DC 25, one creature the solar can see within 120 feet. Failure: The target has the Blinded condition for 1 minute. Failure or Success: The solar can’t take this action again until the start of its next turn.
- **Radiant Teleport.** The solar teleports up to 60 feet to an unoccupied space it can see. Dexterity Saving Throw: DC 25, each creature in a 10-foot Emanation originating from the solar at its destination space. Failure: 11 (2d10) Radiant damage. Success: Half damage.
