---
id: monsters:goblin-boss
type: monsters
name: "Goblin Boss"
summary: "CR 1 Small fey (chaotic neutral); AC 17, HP 21, XP 200"
refs:
  - damage-types:piercing
  - damage-types:slashing
  - equipment:chain-shirt
  - equipment:shield
  - proficiencies:skill-stealth
---
# Goblin Boss (`monsters:goblin-boss`)
*Small fey, chaotic neutral* | **CR** 1 (XP 200; PB +2)
**AC** 17 ([Chain Shirt](../equipment/chain-shirt.md), [Shield](../equipment/shield.md)) | **HP** 21 (6d6) | **Speed** 30 ft.
**STR** 10 (+0) | **DEX** 15 (+2) | **CON** 10 (+0) | **INT** 10 (+0) | **WIS** 8 (-1) | **CHA** 10 (+0)
**Skills:** [Skill: Stealth](../proficiencies/skill-stealth.md) +6
**Senses:** Darkvision 60 ft., Passive Perception 9
**Languages:** Common, Goblin
**Gear:** Chain Shirt, Scimitar, Shield, Shortbow

## Actions
- **Multiattack.** The goblin makes two attacks, using Scimitar or Shortbow in any combination.
- **Scimitar.** Melee Attack Roll: +4, reach 5 ft. Hit: 5 (1d6 + 2) Slashing damage, plus 2 (1d4) Slashing damage if the attack roll had Advantage.
- **Shortbow.** Ranged Attack Roll: +4, range 80/320 ft. Hit: 5 (1d6 + 2) Piercing damage, plus 2 (1d4) Piercing damage if the attack roll had Advantage.

## Bonus Actions
- **Nimble Escape.** The goblin takes the Disengage or Hide action.

## Reactions
- **Redirect Attack.** Trigger: A creature the goblin can see makes an attack roll against it. Response: The goblin chooses a Small or Medium ally within 5 feet of itself. The goblin and that ally swap places, and the ally becomes the target of the attack instead.
