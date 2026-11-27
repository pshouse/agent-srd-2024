---
id: monsters:wight
type: monsters
name: "Wight"
summary: "CR 3 Medium undead (neutral evil); AC 14, HP 82, XP 700"
refs:
  - ability-scores:con
  - conditions:exhaustion
  - conditions:poisoned
  - damage-types:necrotic
  - damage-types:piercing
  - damage-types:slashing
  - equipment:studded-leather-armor
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
---
# Wight (`monsters:wight`)
*Medium undead, neutral evil* | **CR** 3 (XP 700; PB +2)
**AC** 14 ([Studded Leather Armor](../equipment/studded-leather-armor.md)) | **HP** 82 (11d8 + 33) | **Speed** 30 ft.
**STR** 15 (+2) | **DEX** 14 (+2) | **CON** 16 (+3) | **INT** 10 (+0) | **WIS** 13 (+1) | **CHA** 15 (+2)
**Skills:** [Skill: Perception](../proficiencies/skill-perception.md) +3, [Skill: Stealth](../proficiencies/skill-stealth.md) +4
**Damage Resistances:** necrotic
**Damage Immunities:** poison
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Poisoned](../conditions/poisoned.md)
**Senses:** Darkvision 60 ft., Passive Perception 13
**Languages:** Common plus one other language
**Gear:** Studded Leather Armor

## Traits
- **Sunlight Sensitivity.** While in sunlight, the wight has Disadvantage on ability checks and attack rolls.

## Actions
- **Multiattack.** The wight makes two attacks, using Necrotic Sword or Necrotic Bow in any combination. It can replace one attack with a use of Life Drain.
- **Necrotic Sword.** Melee Attack Roll: +4, reach 5 ft. Hit: 6 (1d8 + 2) Slashing damage plus 4 (1d8) Necrotic damage.
- **Necrotic Bow.** Ranged Attack Roll: +4, range 150/600 ft. Hit: 6 (1d8 + 2) Piercing damage plus 4 (1d8) Necrotic damage.
- **Life Drain.** Constitution Saving Throw: DC 13, one creature within 5 feet. Failure: 6 (1d8 + 2) Necrotic damage, and the target’s Hit Point maximum decreases by an amount equal to the damage taken. A Humanoid slain by this attack rises 24 hours later as a Zombie under the wight’s control, unless the Humanoid is restored to life or its body is destroyed. The wight can have no more than twelve zombies under its control at a time.
