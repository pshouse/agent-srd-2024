---
id: monsters:mummy-lord
type: monsters
name: "Mummy Lord"
summary: "CR 15 Medium or small undead (lawful evil); AC 17, HP 187, XP 13000"
refs:
  - ability-scores:wis
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:paralyzed
  - conditions:poisoned
  - damage-types:bludgeoning
  - damage-types:necrotic
  - damage-types:psychic
  - proficiencies:saving-throw-int
  - proficiencies:saving-throw-wis
  - proficiencies:skill-history
  - proficiencies:skill-perception
  - proficiencies:skill-religion
  - spells:animate-dead
  - spells:command
  - spells:dispel-magic
  - spells:harm
  - spells:insect-plague
  - spells:thaumaturgy
---
# Mummy Lord (`monsters:mummy-lord`)
*Medium or small undead, lawful evil* | **CR** 15 (XP 13,000 (15,000 in lair); PB +5)
**AC** 17 | **HP** 187 (25d8 + 75) | **Speed** 30 ft.
**STR** 18 (+4) | **DEX** 10 (+0) | **CON** 17 (+3) | **INT** 11 (+0) | **WIS** 19 (+4) | **CHA** 16 (+3)
**Saving Throws:** [Saving Throw: INT](../proficiencies/saving-throw-int.md) +5, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +9
**Skills:** [Skill: History](../proficiencies/skill-history.md) +5, [Skill: Perception](../proficiencies/skill-perception.md) +9, [Skill: Religion](../proficiencies/skill-religion.md) +5
**Damage Vulnerabilities:** fire
**Damage Immunities:** necrotic, poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Paralyzed](../conditions/paralyzed.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Truesight 60 ft., Passive Perception 19
**Languages:** Common plus three other languages

## Traits
- **Legendary Resistance (3/day).** If the mummy fails a saving throw, it can choose to succeed instead.
- **Magic Resistance.** The mummy has Advantage on saving throws against spells and other magical effects.
- **Undead Restoration.** If destroyed, the mummy gains a new body in 24 hours if its heart is intact, reviving with all its Hit Points. The new body appears in an unoccupied space within the mummy’s lair. The heart is a Tiny object that has AC 17, HP 10, and Immunity to all damage except Fire.

## Actions
- **Multiattack.** The mummy makes one Rotting Fist or Channel Negative Energy attack, and it uses Dreadful Glare.
- **Rotting Fist.** Melee Attack Roll: +9, reach 5 ft. Hit: 15 (2d10 + 4) Bludgeoning damage plus 10 (3d6) Necrotic damage. If the target is a creature, it is cursed. While cursed, the target can’t regain Hit Points, it gains no benefit from finishing a Long Rest, and its Hit Point maximum decreases by 10 (3d6) every 24 hours that elapse. A creature dies and turns to dust if reduced to 0 Hit Points by this attack.
- **Channel Negative Energy.** Ranged Attack Roll: +9, range 60 ft. Hit: 25 (6d6 + 4) Necrotic damage.
- **Dreadful Glare.** Wisdom Saving Throw: DC 17, one creature the mummy can see within 60 feet. Failure: 25 (6d6 + 4) Psychic damage, and the target has the Paralyzed condition until the end of the mummy’s next turn.
- **Spellcasting.** The mummy casts one of the following spells, requiring no Material components and using Wisdom as the spellcasting ability (spell save DC 17, +9 to hit with spell attacks):
  - At will: [Dispel Magic](../spells/dispel-magic.md) (level 3), [Thaumaturgy](../spells/thaumaturgy.md)
  - 1/day: [Animate Dead](../spells/animate-dead.md) (level 3), [Harm](../spells/harm.md) (level 6), [Insect Plague](../spells/insect-plague.md) (level 7)

## Reactions
- **Whirlwind of Sand.** Trigger: The mummy is hit by an attack roll. Response: The mummy adds 2 to its AC against the attack, possibly causing the attack to miss, and the mummy teleports up to 60 feet to an unoccupied space it can see. Each creature of its choice that it can see within 5 feet of its destination space has the Blinded condition until the end of the mummy’s next turn.

## Legendary Actions
- **Dread Command.** The mummy casts Command (level 2 version), using the same spellcasting ability as Spellcasting. The mummy can’t take this action again until the start of its next turn.
  - Other: [Command](../spells/command.md) (level 2)
- **Glare.** The mummy uses Dreadful Glare. The mummy can’t take this action again until the start of its next turn.
- **Necrotic Strike.** The mummy makes one Rotting Fist or Channel Negative Energy attack.
