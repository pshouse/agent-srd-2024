---
id: monsters:vrock
type: monsters
name: "Vrock"
summary: "CR 6 Large fiend (chaotic evil); AC 15, HP 152, XP 2300"
refs:
  - ability-scores:con
  - conditions:poisoned
  - damage-types:piercing
  - damage-types:poison
  - damage-types:thunder
  - proficiencies:saving-throw-cha
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-wis
---
# Vrock (`monsters:vrock`)
*Large fiend, chaotic evil* | **CR** 6 (XP 2,300; PB +3)
**AC** 15 | **HP** 152 (16d10 + 64) | **Speed** 40 ft., fly 60 ft.
**STR** 17 (+3) | **DEX** 15 (+2) | **CON** 18 (+4) | **INT** 8 (-1) | **WIS** 13 (+1) | **CHA** 8 (-1)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +5, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +4, [Saving Throw: CHA](../proficiencies/saving-throw-cha.md) +2
**Damage Resistances:** cold, fire, lightning
**Damage Immunities:** poison
**Condition Immunities:** [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 120 ft., Passive Perception 11
**Languages:** Abyssal; telepathy 120 ft.

## Traits
- **Demonic Restoration.** If the vrock dies outside the Abyss, its body dissolves into ichor, and it gains a new body instantly, reviving with all its Hit Points somewhere in the Abyss.
- **Magic Resistance.** The vrock has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The vrock makes two Shred attacks.
- **Shred.** Melee Attack Roll: +6, reach 5 ft. Hit: 10 (2d6 + 3) Piercing damage plus 10 (3d6) Poison damage.
- **Spores (Recharge 6+ (1d6)).** Constitution Saving Throw: DC 15, each creature in a 20-foot Emanation originating from the vrock. Failure: The target has the Poisoned condition and repeats the save at the end of each of its turns, ending the effect on itself on a success. While Poisoned, the target takes 5 (1d10) Poison damage at the start of each of its turns. Emptying a flask of Holy Water on the target ends the effect early.
- **Stunning Screech (1/day).** Constitution Saving Throw: DC 15, each creature in a 20-foot Emanation originating from the vrock (demons succeed automatically). Failure: 10 (3d6) Thunder damage, and the target has the Stunned condition until the end of the vrock’s next turn.
