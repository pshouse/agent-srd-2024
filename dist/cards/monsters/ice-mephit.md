---
id: monsters:ice-mephit
type: monsters
name: "Ice Mephit"
summary: "CR 1/2 Small elemental (neutral evil); AC 11, HP 21, XP 100"
refs:
  - ability-scores:cha
  - ability-scores:con
  - conditions:exhaustion
  - conditions:poisoned
  - damage-types:cold
  - damage-types:slashing
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
  - spells:fog-cloud
---
# Ice Mephit (`monsters:ice-mephit`)
*Small elemental, neutral evil* | **CR** 1/2 (XP 100; PB +2)
**AC** 11 | **HP** 21 (6d6) | **Speed** 30 ft., fly 30 ft.
**STR** 7 (-2) | **DEX** 13 (+1) | **CON** 10 (+0) | **INT** 9 (-1) | **WIS** 11 (+0) | **CHA** 12 (+1)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +2, [Skill: Stealth](../proficiencies/skill-stealth.md) +3
**Damage Vulnerabilities:** fire
**Damage Immunities:** cold, poison
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 12
**Languages:** Primordial (Aquan, Auran)

## Traits
- **Death Burst.** The mephit explodes when it dies. Constitution Saving Throw: DC 10, each creature in a 5-foot Emanation originating from the mephit. Failure: 5 (2d4) Cold damage. Success: Half damage.

## Actions
- **Claw.** Melee Attack Roll: +3, reach 5 ft. Hit: 3 (1d4 + 1) Slashing damage plus 2 (1d4) Cold damage.
- **Fog Cloud (1/day).** The mephit casts Fog Cloud, requiring no spell components and using Charisma as the spellcasting ability.
  - Other: [Fog Cloud](../spells/fog-cloud.md) (level 1)
- **Frost Breath (Recharge 6+ (1d6)).** Constitution Saving Throw: DC 10, each creature in a 15-foot Cone. Failure: 7 (3d4) Cold damage. Success: Half damage.
