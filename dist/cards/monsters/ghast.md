---
id: monsters:ghast
type: monsters
name: "Ghast"
summary: "CR 2 Medium undead (chaotic evil); AC 13, HP 36, XP 450"
refs:
  - ability-scores:con
  - conditions:charmed
  - conditions:exhaustion
  - conditions:poisoned
  - damage-types:necrotic
  - damage-types:piercing
  - damage-types:slashing
  - proficiencies:saving-throw-wis
---
# Ghast (`monsters:ghast`)
*Medium undead, chaotic evil* | **CR** 2 (XP 450; PB +2)
**AC** 13 | **HP** 36 (8d8) | **Speed** 30 ft.
**STR** 16 (+3) | **DEX** 17 (+3) | **CON** 10 (+0) | **INT** 11 (+0) | **WIS** 10 (+0) | **CHA** 8 (-1)
**Saving Throws:** [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +2
**Damage Resistances:** necrotic
**Damage Immunities:** poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 10
**Languages:** Common

## Traits
- **Stench.** Constitution Saving Throw: DC 10, any creature that starts its turn in a 5-foot Emanation originating from the ghast. Failure: The target has the Poisoned condition until the start of its next turn. Success: The target is immune to this ghast’s Stench for 24 hours.

## Actions
- **Bite.** Melee Attack Roll: +5, reach 5 ft. Hit: 7 (1d8 + 3) Piercing damage plus 9 (2d8) Necrotic damage.
- **Claw.** Melee Attack Roll: +5, reach 5 ft. Hit: 10 (2d6 + 3) Slashing damage. If the target is a non-Undead creature, it is subjected to the following effect. Constitution Saving Throw: DC 10. Failure: The target has the Paralyzed condition until the end of its next turn.
