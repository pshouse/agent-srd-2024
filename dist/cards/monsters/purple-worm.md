---
id: monsters:purple-worm
type: monsters
name: "Purple Worm"
summary: "CR 15 Gargantuan monstrosity (unaligned); AC 18, HP 247, XP 13000"
refs:
  - ability-scores:str
  - damage-types:acid
  - damage-types:piercing
  - damage-types:poison
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-wis
---
# Purple Worm (`monsters:purple-worm`)
*Gargantuan monstrosity, unaligned* | **CR** 15 (XP 13,000; PB +5)
**AC** 18 | **HP** 247 (15d20 + 90) | **Speed** 50 ft., burrow 50 ft.
**STR** 28 (+9) | **DEX** 7 (-2) | **CON** 22 (+6) | **INT** 1 (-5) | **WIS** 8 (-1) | **CHA** 4 (-3)
**Saving Throws:** [Saving Throw: CON](../proficiencies/saving-throw-con.md) +11, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +4
**Senses:** Blindsight 30 ft., Tremorsense 60 ft., Passive Perception 9
**Languages:** None

## Traits
- **Tunneler.** The worm can burrow through solid rock at half its Burrow Speed and leaves a 10-foot-diameter tunnel in its wake.

## Actions
- **Multiattack.** The worm makes one Bite attack and one Tail Stinger attack.
- **Bite.** Melee Attack Roll: +14, reach 10 ft. Hit: 22 (3d8 + 9) Piercing damage. If the target is a Large or smaller creature, it has the Grappled condition (escape DC 19), and it has the Restrained condition until the grapple ends.
- **Tail Stinger.** Melee Attack Roll: +14, reach 10 ft. Hit: 16 (2d6 + 9) Piercing damage plus 35 (10d6) Poison damage.

## Bonus Actions
- **Swallow.** Strength Saving Throw: DC 19, one Large or smaller creature Grappled by the worm (it can have up to three creatures swallowed at a time). Failure: The target is swallowed by the worm, and the Grappled condition ends. A swallowed creature has the Blinded and Restrained conditions, has Total Cover against attacks and other effects outside the worm, and takes 17 (5d6) Acid damage at the start of each of the worm’s turns. If the worm takes 30 damage or more on a single turn from a creature inside it, the worm must succeed on a DC 21 Constitution saving throw at the end of that turn or regurgitate all swallowed creatures, each of which falls in a space within 5 feet of the worm and has the Prone condition. If the worm dies, any swallowed creature no longer has the Restrained condition and can escape from the corpse using 20 feet of movement, exiting Prone.
