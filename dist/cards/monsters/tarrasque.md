---
id: monsters:tarrasque
type: monsters
name: "Tarrasque"
summary: "CR 30 Gargantuan monstrosity (unaligned); AC 25, HP 697, XP 155000"
refs:
  - ability-scores:con
  - ability-scores:str
  - conditions:charmed
  - conditions:deafened
  - conditions:frightened
  - conditions:paralyzed
  - conditions:poisoned
  - damage-types:acid
  - damage-types:bludgeoning
  - damage-types:piercing
  - damage-types:slashing
  - damage-types:thunder
  - proficiencies:saving-throw-cha
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-int
  - proficiencies:saving-throw-wis
  - proficiencies:skill-perception
---
# Tarrasque (`monsters:tarrasque`)
*Gargantuan monstrosity, unaligned* | **CR** 30 (XP 155,000; PB +9)
**AC** 25 | **HP** 697 (34d20 + 340) | **Speed** 60 ft., burrow 40 ft., climb 60 ft.
**STR** 30 (+10) | **DEX** 11 (+0) | **CON** 30 (+10) | **INT** 3 (-4) | **WIS** 11 (+0) | **CHA** 11 (+0)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +9, [Saving Throw: INT](../proficiencies/saving-throw-int.md) +5, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +9, [Saving Throw: CHA](../proficiencies/saving-throw-cha.md) +9
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +9
**Damage Resistances:** bludgeoning, piercing, slashing
**Damage Immunities:** fire, poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Deafened](../conditions/deafened.md), [Frightened](../conditions/frightened.md), [Paralyzed](../conditions/paralyzed.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Blindsight 120 ft., Passive Perception 19
**Languages:** None

## Traits
- **Legendary Resistance (6/day).** If the tarrasque fails a saving throw, it can choose to succeed instead.
- **Magic Resistance.** The tarrasque has Advantage on saving throws against spells and other magical effects.
- **Reflective Carapace.** If the tarrasque is targeted by a Magic Missile spell or a spell that requires a ranged attack roll, roll 1d6. On a 1–5, the tarrasque is unaffected. On a 6, the tarrasque is unaffected and reflects the spell, turning the caster into the target.
- **Siege Monster.** The tarrasque deals double damage to objects and structures.

## Actions
- **Multiattack.** The tarrasque makes one Bite attack and three other attacks, using Claw or Tail in any combination.
- **Bite.** Melee Attack Roll: +19, reach 15 ft. Hit: 36 (4d12 + 10) Piercing damage, and the target has the Grappled condition (escape DC 20). Until the grapple ends, the target has the Restrained condition and can’t teleport.
- **Claw.** Melee Attack Roll: +19, reach 15 ft. Hit: 28 (4d8 + 10) Slashing damage.
- **Tail.** Melee Attack Roll: +19, reach 30 ft. Hit: 23 (3d8 + 10) Bludgeoning damage. If the target is a Huge or smaller creature, it has the Prone condition.
- **Thunderous Bellow (Recharge 5+ (1d6)).** Constitution Saving Throw: DC 27, each creature and each object that isn’t being worn or carried in a 150-foot Cone. Failure: 78 (12d12) Thunder damage, and the target has the Deafened and Frightened conditions until the end of its next turn. Success: Half damage only.

## Bonus Actions
- **Swallow.** Strength Saving Throw: DC 27, one Large or smaller creature Grappled by the tarrasque (it can have up to six creatures swallowed at a time). Failure: The target is swallowed, and the Grappled condition ends. A swallowed creature has the Blinded and Restrained conditions and can’t teleport, it has Total Cover against attacks and other effects outside the tarrasque, and it takes 56 (16d6) Acid damage at the start of each of the tarrasque’s turns. If the tarrasque takes 60 damage or more on a single turn from a creature inside it, the tarrasque must succeed on a DC 20 Constitution saving throw at the end of that turn or regurgitate all swallowed creatures, each of which falls in a space within 10 feet of the tarrasque and has the Prone condition. If the tarrasque dies, any swallowed creature no longer has the Restrained condition and can escape from the corpse using 20 feet of movement, exiting Prone.

## Legendary Actions
- **Onslaught.** The tarrasque moves up to half its Speed, and it makes one Claw or Tail attack.
- **World-Shaking Movement.** The tarrasque moves up to its Speed. At the end of this movement, the tarrasque creates an instantaneous shock wave in a 60-foot Emanation originating from itself. Creatures in that area lose Concentration and, if Medium or smaller, have the Prone condition. The tarrasque can’t take this action again until the start of its next turn.
