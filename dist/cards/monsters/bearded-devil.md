---
id: monsters:bearded-devil
type: monsters
name: "Bearded Devil"
summary: "CR 3 Medium fiend (lawful evil); AC 13, HP 58, XP 700"
refs:
  - ability-scores:con
  - conditions:frightened
  - conditions:poisoned
  - damage-types:piercing
  - damage-types:slashing
  - proficiencies:saving-throw-cha
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-str
---
# Bearded Devil (`monsters:bearded-devil`)
*Medium fiend, lawful evil* | **CR** 3 (XP 700; PB +2)
**AC** 13 | **HP** 58 (9d8 + 18) | **Speed** 30 ft.
**STR** 16 (+3) | **DEX** 15 (+2) | **CON** 15 (+2) | **INT** 9 (-1) | **WIS** 11 (+0) | **CHA** 14 (+2)
**Saving Throws:** [Saving Throw: STR](../proficiencies/saving-throw-str.md) +5, [Saving Throw: CON](../proficiencies/saving-throw-con.md) +4, [Saving Throw: CHA](../proficiencies/saving-throw-cha.md) +4
**Damage Resistances:** cold
**Damage Immunities:** fire, poison
**Condition Immunities:** [Frightened](../conditions/frightened.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 120 ft. (unimpeded by magical darkness), Passive Perception 10
**Languages:** Infernal; telepathy 120 ft.

## Traits
- **Magic Resistance.** The devil has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The devil makes one Beard attack and one Infernal Glaive attack.
- **Beard.** Melee Attack Roll: +5, reach 5 ft. Hit: 7 (1d8 + 3) Piercing damage, and the target has the Poisoned condition until the start of the devil’s next turn. Until this poison ends, the target can’t regain Hit Points.
- **Infernal Glaive.** Melee Attack Roll: +5, reach 10 ft. Hit: 8 (1d10 + 3) Slashing damage. If the target is a creature and doesn’t already have an infernal wound, it is subjected to the following effect. Constitution Saving Throw: DC 12. Failure: The target receives an infernal wound. While wounded, the target loses 5 (1d10) Hit Points at the start of each of its turns. The wound closes after 1 minute, after a spell restores Hit Points to the target, or after the target or a creature within 5 feet of it takes an action to stanch the wound, doing so by succeeding on a DC 12 Wisdom (Medicine) check.
