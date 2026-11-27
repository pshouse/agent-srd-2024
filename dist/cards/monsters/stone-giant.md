---
id: monsters:stone-giant
type: monsters
name: "Stone Giant"
summary: "CR 7 Huge giant (neutral); AC 17, HP 126, XP 2900"
refs:
  - ability-scores:dex
  - damage-types:bludgeoning
  - damage-types:force
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-dex
  - proficiencies:saving-throw-wis
  - proficiencies:skill-athletics
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
---
# Stone Giant (`monsters:stone-giant`)
*Huge giant, neutral* | **CR** 7 (XP 2,900; PB +3)
**AC** 17 | **HP** 126 (11d12 + 55) | **Speed** 40 ft.
**STR** 23 (+6) | **DEX** 15 (+2) | **CON** 20 (+5) | **INT** 10 (+0) | **WIS** 12 (+1) | **CHA** 9 (-1)
**Saving Throws:** [Saving Throw: DEX](../proficiencies/saving-throw-dex.md) +5, [Saving Throw: CON](../proficiencies/saving-throw-con.md) +8, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +4
**Skills:** [Skill: Athletics](../proficiencies/skill-athletics.md) +12, [Skill: Perception](../proficiencies/skill-perception.md) +4, [Skill: Stealth](../proficiencies/skill-stealth.md) +5
**Senses:** Darkvision 60 ft., Passive Perception 14
**Languages:** Giant

## Actions
- **Multiattack.** The giant makes two attacks, using Stone Club or Boulder in any combination.
- **Stone Club.** Melee Attack Roll: +9, reach 15 ft. Hit: 22 (3d10 + 6) Bludgeoning damage.
- **Boulder.** Ranged Attack Roll: +9, range 60/240 ft. Hit: 15 (2d8 + 6) Bludgeoning damage. If the target is a Large or smaller creature, it has the Prone condition.

## Reactions
- **Deflect Missile (Recharge 5+ (1d6)).** Trigger: The giant is hit by a ranged attack roll and takes Bludgeoning, Piercing, or Slashing damage from it. Response: The giant reduces the damage it takes from the attack by 11 (1d10 + 6), and if that damage is reduced to 0, the giant can redirect some of the attack’s force. Dexterity Saving Throw: DC 17, one creature the giant can see within 60 feet. Failure: 11 (1d10 + 6) Force damage.
