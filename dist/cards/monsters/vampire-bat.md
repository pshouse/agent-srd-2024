---
id: monsters:vampire-bat
type: monsters
name: "Vampire, Bat Form"
summary: "CR 13 Tiny undead (lawful evil); AC 16, HP 195, XP 10000"
refs:
  - ability-scores:cha
  - ability-scores:con
  - damage-types:necrotic
  - damage-types:piercing
  - monsters:vampire-mist
  - monsters:vampire-vampire
  - proficiencies:saving-throw-cha
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-wis
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
  - spells:charm-person
  - spells:command
---
# Vampire, Bat Form (`monsters:vampire-bat`)
*Tiny undead, lawful evil* | **CR** 13 (XP 10,000 (11,500 in lair); PB +5)
**AC** 16 | **HP** 195 (23d8 + 92) | **Speed** 5 ft., fly 30 ft.
**STR** 18 (+4) | **DEX** 18 (+4) | **CON** 18 (+4) | **INT** 17 (+3) | **WIS** 15 (+2) | **CHA** 18 (+4)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +9, [Saving Throw: CON](../proficiencies/saving-throw-con.md) +9, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +7, [Saving Throw: CHA](../proficiencies/saving-throw-cha.md) +9
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +7, [Skill: Stealth](../proficiencies/skill-stealth.md) +9
**Damage Resistances:** necrotic
**Senses:** Darkvision 120 ft., Passive Perception 17
**Languages:** Common plus two other languages

## Traits
- **Legendary Resistance (3/day).** If the vampire fails a saving throw, it can choose to succeed instead.
- **Misty Escape.** If the vampire drops to 0 Hit Points outside its resting place, the vampire uses Shape-Shift to become mist (no action required). If it can’t use ShapeShift, it is destroyed. While it has 0 Hit Points in mist form, it can’t return to its vampire form, and it must reach its resting place within 2 hours or be destroyed. Once in its resting place, it returns to its vampire form and has the Paralyzed condition until it regains any Hit Points, and it regains 1 Hit Point after spending 1 hour there.
- **Spider Climb.** The vampire can climb difficult surfaces, including along ceilings, without needing to make an ability check.
- **Vampire Weakness.** The vampire has these weaknesses:
- **Forbiddance.** The vampire can’t enter a residence without an invitation from an occupant.
- **Running Water.** The vampire takes 20 Acid damage if it ends its turn in running water.
- **Stake to the Heart.** If a weapon that deals Piercing damage is driven into the vampire’s heart while the vampire has the Incapacitated condition in its resting place, the vampire has the Paralyzed condition until the weapon is removed.
- **Sunlight.** The vampire takes 20 Radiant damage if it starts its turn in sunlight. While in sunlight, it has Disadvantage on attack rolls and ability checks.

## Actions
- **Bite.** Constitution Saving Throw: DC 17, one creature within 5 feet that is willing or that has the Grappled, Incapacitated, or Restrained condition. Failure: 6 (1d4 + 4) Piercing damage plus 13 (3d8) Necrotic damage. The target’s Hit Point maximum decreases by an amount equal to the Necrotic damage taken, and the vampire regains Hit Points equal to that amount. A Humanoid reduced to 0 Hit Points by this damage and then buried rises the following sunset as a Vampire Spawn under the vampire’s control.

## Bonus Actions
- **Charm (Recharge 5+ (1d6)).** The vampire casts Charm Person, requiring no spell components and using Charisma as the spellcasting ability (spell save DC 17), and the duration is 24 hours. The Charmed target is a willing recipient of the vampire’s Bite, the damage of which doesn’t end the spell. When the spell ends, the target is unaware it was Charmed by the vampire.
  - Other: [Charm Person](../spells/charm-person.md) (level 1)
- **Shape-Shift.** If the vampire isn’t in sunlight or running water, it shape-shifts into a Tiny bat (Speed 5 ft., Fly Speed 30 ft.) or a Medium cloud of mist (Speed 5 ft., Fly Speed 20 ft. [hover]), or it returns to its vampire form. Anything it is wearing transforms with it. While in bat form, the vampire can’t speak. Its game statistics, other than its size and Speed, are unchanged. While in mist form, the vampire can’t take any actions, speak, or manipulate objects. It is weightless and can enter an enemy’s space and stop there. If air can pass through a space, the mist can do so, but it can’t pass through liquid. It has Resistance to all damage, except the damage it takes from sunlight.

## Legendary Actions
- **Beguile.** The vampire casts Command, requiring no spell components and using Charisma as the spellcasting ability (spell save DC 17). The vampire can’t take this action again until the start of its next turn.
  - Other: [Command](../spells/command.md) (level 1)
- **Deathless Strike.** The vampire moves up to half its Speed, and it makes one Grave Strike attack.

**Forms:** [Vampire, Vampire Form](../monsters/vampire-vampire.md), [Vampire, Mist Form](../monsters/vampire-mist.md)
