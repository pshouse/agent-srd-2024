---
id: monsters:kraken
type: monsters
name: "Kraken"
summary: "CR 23 Gargantuan monstrosity (chaotic evil); AC 18, HP 481, XP 50000"
refs:
  - ability-scores:con
  - ability-scores:dex
  - ability-scores:str
  - conditions:frightened
  - conditions:grappled
  - conditions:paralyzed
  - conditions:restrained
  - damage-types:acid
  - damage-types:bludgeoning
  - damage-types:lightning
  - damage-types:piercing
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-str
  - proficiencies:saving-throw-wis
  - proficiencies:skill-history
  - proficiencies:skill-perception
---
# Kraken (`monsters:kraken`)
*Gargantuan monstrosity, chaotic evil* | **CR** 23 (XP 50,000 (62,000 in lair); PB +7)
**AC** 18 | **HP** 481 (26d20 + 208) | **Speed** 30 ft., swim 120 ft.
**STR** 30 (+10) | **DEX** 11 (+0) | **CON** 26 (+8) | **INT** 22 (+6) | **WIS** 18 (+4) | **CHA** 20 (+5)
**Saving Throws:** [Saving Throw: STR](../proficiencies/saving-throw-str.md) +17, [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +7, [Saving Throw: CON](../proficiencies/saving-throw-con.md) +15, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +11
**Skills:** [Skill: History](../proficiencies/skill-history.md) +13, [Skill: Perception](../proficiencies/skill-perception.md) +11
**Damage Immunities:** cold, lightning
**Condition Immunities:** [Frightened](../conditions/frightened.md), [Grappled](../conditions/grappled.md), [Paralyzed](../conditions/paralyzed.md), [Restrained](../conditions/restrained.md)
**Senses:** Truesight 120 ft., Passive Perception 21
**Languages:** Understands Abyssal, Celestial, Infernal, and Primordial but can’t speak; telepathy 120 ft.

## Traits
- **Amphibious.** The kraken can breathe air and water.
- **Legendary Resistance (4/day).** If the kraken fails a saving throw, it can choose to succeed instead.
- **Siege Monster.** The kraken deals double damage to objects and structures.

## Actions
- **Multiattack.** The kraken makes two Tentacle attacks and uses Fling, Lightning Strike, or Swallow.
- **Tentacle.** Melee Attack Roll: +17, reach 30 ft. Hit: 24 (4d6 + 10) Bludgeoning damage. The target has the Grappled condition (escape DC 20) from one of ten tentacles, and it has the Restrained condition until the grapple ends.
- **Fling.** The kraken throws a Large or smaller creature Grappled by it to a space it can see within 60 feet of itself that isn’t in the air. Dexterity Saving Throw: DC 25, the creature thrown and each creature in the destination space. Failure: 18 (4d8) Bludgeoning damage, and the target has the Prone condition. Success: Half damage only.
- **Lightning Strike.** Dexterity Saving Throw: DC 23, one creature the kraken can see within 120 feet. Failure: 33 (6d10) Lightning damage. Success: Half damage.
- **Swallow.** Dexterity Saving Throw: DC 25, one creature Grappled by the kraken (it can have up to four creatures swallowed at a time). Failure: 23 (3d8 + 10) Piercing damage. If the target is Large or smaller, it is swallowed and no longer Grappled. A swallowed creature has the Restrained condition, has Total Cover against attacks and other effects outside the kraken, and takes 24 (7d6) Acid damage at the start of each of its turns. If the kraken takes 50 damage or more on a single turn from a creature inside it, the kraken must succeed on a DC 25 Constitution saving throw at the end of that turn or regurgitate all swallowed creatures, each of which falls in a space within 10 feet of the kraken with the Prone condition. If the kraken dies, any swallowed creature no longer has the Restrained condition and can escape from the corpse using 15 feet of movement, exiting Prone.

## Legendary Actions
- **Storm Bolt.** The kraken uses Lightning Strike.
- **Toxic Ink.** Constitution Saving Throw: DC 23, each creature in a 15-foot Emanation originating from the kraken while it is underwater. Failure: The target has the Blinded and Poisoned conditions until the end of the kraken’s next turn. The kraken then moves up to its Speed. Failure or Success: The kraken can’t take this action again until the start of its next turn.
