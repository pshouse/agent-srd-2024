---
id: monsters:lich
type: monsters
name: "Lich"
summary: "CR 21 Medium undead (neutral evil); AC 20, HP 315, XP 33000"
refs:
  - ability-scores:con
  - ability-scores:int
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:paralyzed
  - conditions:poisoned
  - damage-types:cold
  - damage-types:force
  - damage-types:necrotic
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-int
  - proficiencies:saving-throw-wis
  - proficiencies:skill-arcana
  - proficiencies:skill-history
  - proficiencies:skill-insight
  - proficiencies:skill-perception
  - spells:animate-dead
  - spells:chain-lightning
  - spells:counterspell
  - spells:detect-magic
  - spells:detect-thoughts
  - spells:dimension-door
  - spells:dispel-magic
  - spells:fear
  - spells:finger-of-death
  - spells:fireball
  - spells:invisibility
  - spells:lightning-bolt
  - spells:mage-hand
  - spells:plane-shift
  - spells:power-word-kill
  - spells:prestidigitation
  - spells:scrying
  - spells:shield
---
# Lich (`monsters:lich`)
*Medium undead, neutral evil* | **CR** 21 (XP 33,000 (41,000 in lair); PB +7)
**AC** 20 | **HP** 315 (42d8 + 126) | **Speed** 30 ft.
**STR** 11 (+0) | **DEX** 16 (+3) | **CON** 16 (+3) | **INT** 21 (+5) | **WIS** 14 (+2) | **CHA** 16 (+3)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +10, [Saving Throw: CON](../proficiencies/saving-throw-con.md) +10, [Saving Throw: INT](../proficiencies/saving-throw-int.md) +12, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +9
**Skills:** [Skill: Arcana](../proficiencies/skill-arcana.md) +19, [Skill: History](../proficiencies/skill-history.md) +12, [Skill: Insight](../proficiencies/skill-insight.md) +9, [Skill: Perception](../proficiencies/skill-perception.md) +9
**Damage Resistances:** cold, lightning
**Damage Immunities:** necrotic, poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Paralyzed](../conditions/paralyzed.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Truesight 120 ft., Passive Perception 19
**Languages:** All
**Gear:** Component Pouch

## Traits
- **Legendary Resistance (4/day).** If the lich fails a saving throw, it can choose to succeed instead.
- **Spirit Jar.** If destroyed, the lich reforms in 1d10 days if it has a spirit jar, reviving with all its Hit Points. The new body appears in an unoccupied space within the lich’s lair.

## Actions
- **Multiattack.** The lich makes three attacks, using Eldritch Burst or Paralyzing Touch in any combination.
- **Eldritch Burst.** Melee or Ranged Attack Roll: +12, reach 5 ft. or range 120 ft. Hit: 31 (4d12 + 5) Force damage.
- **Paralyzing Touch.** Melee Attack Roll: +12, reach 5 ft. Hit: 15 (3d6 + 5) Cold damage, and the target has the Paralyzed condition until the start of the lich’s next turn.
- **Spellcasting.** The lich casts one of the following spells, using Intelligence as the spellcasting ability (spell save DC 20):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Detect Thoughts](../spells/detect-thoughts.md) (level 2), [Dispel Magic](../spells/dispel-magic.md) (level 3), [Fireball](../spells/fireball.md) (level 5), [Invisibility](../spells/invisibility.md) (level 2), [Lightning Bolt](../spells/lightning-bolt.md) (level 5), [Mage Hand](../spells/mage-hand.md), [Prestidigitation](../spells/prestidigitation.md)
  - 2/day: [Animate Dead](../spells/animate-dead.md) (level 3), [Dimension Door](../spells/dimension-door.md) (level 4), [Plane Shift](../spells/plane-shift.md) (level 7)
  - 1/day: [Chain Lightning](../spells/chain-lightning.md) (level 6), [Finger of Death](../spells/finger-of-death.md) (level 7), [Power Word Kill](../spells/power-word-kill.md) (level 9), [Scrying](../spells/scrying.md) (level 5)

## Reactions
- **Protective Magic.** The lich casts Counterspell or Shield in response to the spell’s trigger, using the same spellcasting ability as Spellcasting.
  - Other: [Counterspell](../spells/counterspell.md) (level 3), [Shield](../spells/shield.md) (level 1)

## Legendary Actions
- **Deathly Teleport.** The lich teleports up to 60 feet to an unoccupied space it can see, and each creature within 10 feet of the space it left takes 11 (2d10) Necrotic damage.
- **Disrupt Life.** Constitution Saving Throw: DC 20, each creature that isn’t an Undead in a 20-foot Emanation originating from the lich. Failure: 31 (9d6) Necrotic damage. Success: Half damage. Failure or Success: The lich can’t take this action again until the start of its next turn.
- **Frightening Gaze.** The lich casts Fear, using the same spellcasting ability as Spellcasting. The lich can’t take this action again until the start of its next turn.
  - Other: [Fear](../spells/fear.md) (level 3)
