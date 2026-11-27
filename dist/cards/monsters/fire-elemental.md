---
id: monsters:fire-elemental
type: monsters
name: "Fire Elemental"
summary: "CR 5 Large elemental (neutral); AC 13, HP 93, XP 1800"
refs:
  - conditions:exhaustion
  - conditions:grappled
  - conditions:paralyzed
  - conditions:petrified
  - conditions:poisoned
  - conditions:prone
  - conditions:restrained
  - conditions:unconscious
  - damage-types:cold
  - damage-types:fire
---
# Fire Elemental (`monsters:fire-elemental`)
*Large elemental, neutral* | **CR** 5 (XP 1,800; PB +3)
**AC** 13 | **HP** 93 (11d10 + 33) | **Speed** 50 ft.
**STR** 10 (+0) | **DEX** 17 (+3) | **CON** 16 (+3) | **INT** 6 (-2) | **WIS** 10 (+0) | **CHA** 7 (-2)
**Damage Resistances:** bludgeoning, piercing, slashing
**Damage Immunities:** fire, poison
**Condition Immunities:** [Exhaustion](../conditions/exhaustion.md), [Grappled](../conditions/grappled.md), [Paralyzed](../conditions/paralyzed.md), [Petrified](../conditions/petrified.md), [Poisoned](../conditions/poisoned.md), [Prone](../conditions/prone.md), [Restrained](../conditions/restrained.md), [Unconscious](../conditions/unconscious.md)
**Senses:** Darkvision 60 ft., Passive Perception 10
**Languages:** Primordial (Ignan)

## Traits
- **Fire Aura.** At the end of each of the elemental’s turns, each creature in a 10-foot Emanation originating from the elemental takes 5 (1d10) Fire damage. Creatures and flammable objects in the Emanation start burning.
- **Fire Form.** The elemental can move through a space as narrow as 1 inch without expending extra movement to do so, and it can enter a creature’s space and stop there. The first time it enters a creature’s space on a turn, that creature takes 5 (1d10) Fire damage.
- **Illumination.** The elemental sheds Bright Light in a 30foot radius and Dim Light for an additional 30 feet.
- **Water Susceptibility.** The elemental takes 3 (1d6) Cold damage for every 5 feet the elemental moves in water or for every gallon of water splashed on it.

## Actions
- **Multiattack.** The elemental makes two Burn attacks.
- **Burn.** Melee Attack Roll: +6, reach 5 ft. Hit: 10 (2d6 + 3) Fire damage. If the target is a creature or a flammable object, it starts burning.
