---
id: monsters:ghost
type: monsters
name: "Ghost"
summary: "CR 4 Medium undead (neutral); AC 11, HP 45, XP 1100"
refs:
  - ability-scores:cha
  - ability-scores:wis
  - conditions:charmed
  - conditions:exhaustion
  - conditions:frightened
  - conditions:grappled
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - conditions:prone
  - conditions:restrained
  - damage-types:force
  - damage-types:necrotic
  - damage-types:psychic
  - spells:etherealness
---
# Ghost (`monsters:ghost`)
*Medium undead, neutral* | **CR** 4 (XP 1,100; PB +2)
**AC** 11 | **HP** 45 (10d8) | **Speed** 5 ft., fly 40 ft., hover True
**STR** 7 (-2) | **DEX** 13 (+1) | **CON** 10 (+0) | **INT** 10 (+0) | **WIS** 12 (+1) | **CHA** 17 (+3)
**Damage Resistances:** acid, bludgeoning, cold, fire, lightning, piercing, slashing, thunder
**Damage Immunities:** necrotic, poison
**Condition Immunities:** [Charmed](../conditions/charmed.md), [Exhaustion](../conditions/exhaustion.md), [Frightened](../conditions/frightened.md), [Grappled](../conditions/grappled.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md), [Prone](../conditions/prone.md), [Restrained](../conditions/restrained.md)
**Senses:** Darkvision 60 ft., Passive Perception 11
**Languages:** Common plus one other language

## Traits
- **Ethereal Sight.** The ghost can see 60 feet into the Ethereal Plane when it is on the Material Plane.
- **Incorporeal Movement.** The ghost can move through other creatures and objects as if they were Difficult Terrain. It takes 5 (1d10) Force damage if it ends its turn inside an object.

## Actions
- **Multiattack.** The ghost makes two Withering Touch attacks.
- **Withering Touch.** Melee Attack Roll: +5, reach 5 ft. Hit: 19 (3d10 + 3) Necrotic damage.
- **Etherealness.** The ghost casts the Etherealness spell, requiring no spell components and using Charisma as the spellcasting ability. The ghost is visible on the Material Plane while on the Border Ethereal and vice versa, but it can’t affect or be affected by anything on the other plane.
  - Other: [Etherealness](../spells/etherealness.md) (level 7)
- **Horrific Visage.** Wisdom Saving Throw: DC 13, each creature in a 60-foot Cone that can see the ghost and isn’t an Undead. Failure: 10 (2d6 + 3) Psychic damage, and the target has the Frightened condition until the start of the ghost’s next turn. Success: The target is immune to this ghost’s Horrific Visage for 24 hours.
- **Possession (Recharge 6+ (1d6)).** Charisma Saving Throw: DC 13, one Humanoid the ghost can see within 5 feet. Failure: The target is possessed by the ghost; the ghost disappears, and the target has the Incapacitated condition and loses control of its body. The ghost now controls the body, but the target retains awareness. The ghost can’t be targeted by any attack, spell, or other effect, except ones that specifically target Undead. The ghost’s game statistics are the same, except it uses the possessed target’s Speed, as well as the target’s Strength, Dexterity, and Constitution modifiers. The possession lasts until the body drops to 0 Hit Points or the ghost leaves as a Bonus Action. When the possession ends, the ghost appears in an unoccupied space within 5 feet of the target, and the target is immune to this ghost’s Possession for 24 hours. Success: The target is immune to this ghost’s Possession for 24 hours.
