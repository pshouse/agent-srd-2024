---
id: monsters:mummy
type: monsters
name: "Mummy"
summary: "CR 3 Medium or small undead (lawful evil); AC 11, HP 58, XP 700"
refs:
  - ability-scores:wis
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:paralyzed
  - conditions:poisoned
  - damage-types:bludgeoning
  - damage-types:necrotic
  - proficiencies:saving-throw-wis
---
# Mummy (`monsters:mummy`)
*Medium or small undead, lawful evil* | **CR** 3 (XP 700; PB +2)
**AC** 11 | **HP** 58 (9d8 + 18) | **Speed** 20 ft.
**STR** 16 (+3) | **DEX** 8 (-1) | **CON** 15 (+2) | **INT** 6 (-2) | **WIS** 12 (+1) | **CHA** 12 (+1)
**Saving Throws:** [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +3
**Damage Vulnerabilities:** fire
**Damage Immunities:** necrotic, poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Paralyzed](../conditions/paralyzed.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 11
**Languages:** Common plus two other languages

## Actions
- **Multiattack.** The mummy makes two Rotting Fist attacks and uses Dreadful Glare.
- **Rotting Fist.** Melee Attack Roll: +5, reach 5 ft. Hit: 8 (1d10 + 3) Bludgeoning damage plus 10 (3d6) Necrotic damage. If the target is a creature, it is cursed. While cursed, the target can’t regain Hit Points, its Hit Point maximum doesn’t return to normal when finishing a Long Rest, and its Hit Point maximum decreases by 10 (3d6) every 24 hours that elapse. A creature dies and turns to dust if reduced to 0 Hit Points by this attack.
- **Dreadful Glare.** Wisdom Saving Throw: DC 11, one creature the mummy can see within 60 feet. Failure: The target has the Frightened condition until the end of the mummy’s next turn. Success: The target is immune to this mummy’s Dreadful Glare for 24 hours.
