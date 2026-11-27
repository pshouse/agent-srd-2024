---
id: monsters:chain-devil
type: monsters
name: "Chain Devil"
summary: "CR 8 Medium fiend (lawful evil); AC 15, HP 85, XP 3900"
refs:
  - ability-scores:dex
  - ability-scores:str
  - ability-scores:wis
  - conditions:poisoned
  - damage-types:fire
  - damage-types:slashing
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-wis
---
# Chain Devil (`monsters:chain-devil`)
*Medium fiend, lawful evil* | **CR** 8 (XP 3,900; PB +3)
**AC** 15 | **HP** 85 (10d8 + 40) | **Speed** 30 ft.
**STR** 18 (+4) | **DEX** 15 (+2) | **CON** 18 (+4) | **INT** 11 (+0) | **WIS** 12 (+1) | **CHA** 14 (+2)
**Saving Throws:** [Saving Throw: CON](../proficiencies/saving-throw-con.md) +7, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +4
**Damage Resistances:** bludgeoning, cold, piercing, slashing
**Damage Immunities:** fire, poison
**Condition Immunities:** [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 120 ft. (unimpeded by magical darkness), Passive Perception 11
**Languages:** Infernal; telepathy 120 ft.

## Traits
- **Diabolical Restoration.** If the devil dies outside the Nine Hells, its body disappears in sulfurous smoke, and it gains a new body instantly, reviving with all its Hit Points somewhere in the Nine Hells.
- **Magic Resistance.** The devil has Advantage on saving throws against spells and other magical effects.

## Actions
- **Multiattack.** The devil makes two Chain attacks and uses Conjure Infernal Chain.
- **Chain.** Melee Attack Roll: +7, reach 10 ft. Hit: 11 (2d6 + 4) Slashing damage. If the target is a Large or smaller creature, it has the Grappled condition (escape DC 14) from one of two chains, and it has the Restrained condition until the grapple ends.
- **Conjure Infernal Chain.** The devil conjures a fiery chain to bind a creature. Dexterity Saving Throw: DC 15, one creature the devil can see within 60 feet. Failure: 9 (2d4 + 4) Fire damage, and the target has the Restrained condition until the end of the devil’s next turn, at which point the chain disappears. If the target is Large or smaller, the devil moves the target up to 30 feet straight toward itself. Success: The chain disappears.

## Reactions
- **Unnerving Gaze.** Trigger: A creature the devil can see starts its turn within 30 feet of the devil and can see the devil. Response—Wisdom Saving Throw: DC 15, the triggering creature. Failure: The target has the Frightened condition until the end of its turn. Success: The target is immune to this devil’s Unnerving Gaze for 24 hours.
