---
id: monsters:doppelganger
type: monsters
name: "Doppelganger"
summary: "CR 3 Medium monstrosity (neutral); AC 14, HP 52, XP 700"
refs:
  - ability-scores:cha
  - ability-scores:wis
  - conditions:charmed
  - damage-types:bludgeoning
  - proficiencies:skill-deception
  - proficiencies:skill-insight
  - spells:detect-thoughts
---
# Doppelganger (`monsters:doppelganger`)
*Medium monstrosity, neutral* | **CR** 3 (XP 700; PB +2)
**AC** 14 | **HP** 52 (8d8 + 16) | **Speed** 30 ft.
**STR** 11 (+0) | **DEX** 18 (+4) | **CON** 14 (+2) | **INT** 11 (+0) | **WIS** 12 (+1) | **CHA** 14 (+2)
**Skills:** [Skill: Deception](../proficiencies/skill-deception.md) +6, [Skill: Insight](../proficiencies/skill-insight.md) +3
**Condition Immunities:** [Charmed](../conditions/charmed.md)
**Senses:** Darkvision 60 ft., Passive Perception 11
**Languages:** Common plus three other languages

## Actions
- **Multiattack.** The doppelganger makes two Slam attacks and uses Unsettling Visage if available.
- **Slam.** Melee Attack Roll: +6 (with Advantage during the first round of each combat), reach 5 ft. Hit: 11 (2d6 + 4) Bludgeoning damage.
- **Read Thoughts.** The doppelganger casts Detect Thoughts, requiring no spell components and using Charisma as the spellcasting ability (spell save DC 12).
  - Other: [Detect Thoughts](../spells/detect-thoughts.md) (level 2)
- **Unsettling Visage (Recharge 6+ (1d6)).** Wisdom Saving Throw: DC 12, each creature in a 15-foot Emanation originating from the doppelganger that can see the doppelganger. Failure: The target has the Frightened condition and repeats the save at the end of each of its turns, ending the effect on itself on a success. After 1 minute, it succeeds automatically.

## Bonus Actions
- **Shape-Shift.** The doppelganger shape-shifts into a Medium or Small Humanoid, or it returns to its true form. Its game statistics, other than its size, are the same in each form. Any equipment it is wearing or carrying isn’t transformed.
