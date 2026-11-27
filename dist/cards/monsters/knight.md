---
id: monsters:knight
type: monsters
name: "Knight"
summary: "CR 3 Medium or small humanoid (neutral); AC 18, HP 52, XP 700"
refs:
  - conditions:frightened
  - damage-types:piercing
  - damage-types:radiant
  - damage-types:slashing
  - equipment:plate-armor
  - proficiencies:saving-throw-con
  - proficiencies:saving-throw-wis
---
# Knight (`monsters:knight`)
*Medium or small humanoid, neutral* | **CR** 3 (XP 700; PB +2)
**AC** 18 ([Plate Armor](../equipment/plate-armor.md)) | **HP** 52 (8d8 + 16) | **Speed** 30 ft.
**STR** 16 (+3) | **DEX** 11 (+0) | **CON** 14 (+2) | **INT** 11 (+0) | **WIS** 11 (+0) | **CHA** 15 (+2)
**Saving Throws:** [Saving Throw: CON](../proficiencies/saving-throw-con.md) +4, [Saving Throw: WIS](../proficiencies/saving-throw-wis.md) +2
**Condition Immunities:** [Frightened](../conditions/frightened.md)
**Senses:** Passive Perception 10
**Languages:** Common plus one other language
**Gear:** Greatsword, Heavy Crossbow, Plate Armor

## Actions
- **Multiattack.** The knight makes two attacks, using Greatsword or Heavy Crossbow in any combination.
- **Greatsword.** Melee Attack Roll: +5, reach 5 ft. Hit: 10 (2d6 + 3) Slashing damage plus 4 (1d8) Radiant damage.
- **Heavy Crossbow.** Ranged Attack Roll: +2, range 100/400 ft. Hit: 11 (2d10) Piercing damage plus 4 (1d8) Radiant damage.

## Reactions
- **Parry.** Trigger: The knight is hit by a melee attack roll while holding a weapon. Response: The knight adds 2 to its AC against that attack, possibly causing it to miss.
