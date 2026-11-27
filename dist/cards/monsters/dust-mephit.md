---
id: monsters:dust-mephit
type: monsters
name: "Dust Mephit"
summary: "CR 1/2 Small elemental (neutral evil); AC 12, HP 17, XP 100"
refs:
  - ability-scores:cha
  - ability-scores:dex
  - conditions:exhaustion
  - conditions:poisoned
  - damage-types:bludgeoning
  - damage-types:slashing
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
  - spells:sleep
---
# Dust Mephit (`monsters:dust-mephit`)
*Small elemental, neutral evil* | **CR** 1/2 (XP 100; PB +2)
**AC** 12 | **HP** 17 (5d6) | **Speed** 30 ft., fly 30 ft.
**STR** 5 (-3) | **DEX** 14 (+2) | **CON** 10 (+0) | **INT** 9 (-1) | **WIS** 11 (+0) | **CHA** 10 (+0)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +2, [Skill: Stealth](../proficiencies/skill-stealth.md) +4
**Damage Vulnerabilities:** fire
**Damage Immunities:** poison
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 12
**Languages:** Primordial (Auran, Terran)

## Traits
- **Death Burst.** The mephit explodes when it dies. Dexterity Saving Throw: DC 10, each creature in a 5-foot Emanation originating from the mephit. Failure: 5 (2d4) Bludgeoning damage. Success: Half damage.

## Actions
- **Claw.** Melee Attack Roll: +4, reach 5 ft. Hit: 4 (1d4 + 2) Slashing damage.
- **Blinding Breath (Recharge 6+ (1d6)).** Dexterity Saving Throw: DC 10, each creature in a 15-foot Cone. Failure: The target has the Blinded condition until the end of the mephit’s next turn.
- **Sleep (1/day).** The mephit casts the Sleep spell, requiring no spell components and using Charisma as the spellcasting ability (spell save DC 10).
  - Other: [Sleep](../spells/sleep.md) (level 1)
