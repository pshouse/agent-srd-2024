---
id: monsters:nalfeshnee
type: monsters
name: "Nalfeshnee"
summary: "CR 13 Large fiend (chaotic evil); AC 18, HP 184, XP 10000"
refs:
  - ability-scores:wis
  - conditions:frightened
  - conditions:poisoned
  - damage-types:force
  - damage-types:psychic
  - damage-types:slashing
  - proficiencies:saving-throw-cha
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-int
  - proficiencies:saving-throw-wis
---
# Nalfeshnee (`monsters:nalfeshnee`)
*Large fiend, chaotic evil* | **CR** 13 (XP 10,000; PB +5)
**AC** 18 | **HP** 184 (16d10 + 96) | **Speed** 20 ft., fly 30 ft.
**STR** 21 (+5) | **DEX** 10 (+0) | **CON** 22 (+6) | **INT** 19 (+4) | **WIS** 12 (+1) | **CHA** 15 (+2)
**Saving Throws:** [Saving Throw: CON](../proficiencies/saving-throw-con.md) +11, [Saving Throw: INT](../proficiencies/saving-throw-int.md) +9, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +6, [Saving Throw: CHA](../proficiencies/saving-throw-cha.md) +7
**Damage Resistances:** cold, fire, lightning
**Damage Immunities:** poison
**Condition Immunities:** [Frightened](../conditions/frightened.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Truesight 120 ft., Passive Perception 11
**Languages:** Abyssal; telepathy 120 ft.

## Traits
- **Demonic Restoration.** If the nalfeshnee dies outside the Abyss, its body dissolves into ichor, and it gains a new body instantly, reviving with all its Hit Points somewhere in the Abyss.
- **Magic Resistance.** The nalfeshnee has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The nalfeshnee makes three Rend attacks.
- **Rend.** Melee Attack Roll: +10, reach 10 ft. Hit: 16 (2d10 + 5) Slashing damage plus 11 (2d10) Force damage.
- **Teleport.** The nalfeshnee teleports up to 120 feet to an unoccupied space it can see.

## Bonus Actions
- **Horror Nimbus (Recharge 5+ (1d6)).** Wisdom Saving Throw: DC 15, each creature in a 15-foot Emanation originating from the nalfeshnee. Failure: 28 (8d6) Psychic damage, and the target has the Frightened condition for 1 minute, until it takes damage, or until it ends its turn with the nalfeshnee out of line of sight. Success: The target is immune to this nalfeshnee’s Horror Nimbus for 24 hours.

## Reactions
- **Pursuit.** Trigger: Another creature the nalfeshnee can see ends its move within 120 feet of the nalfeshnee. Response: The nalfeshnee uses Teleport, but its destination space must be within 10 feet of the triggering creature.
