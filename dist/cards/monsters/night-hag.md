---
id: monsters:night-hag
type: monsters
name: "Night Hag"
summary: "CR 5 Medium fiend (neutral evil); AC 17, HP 112, XP 1800"
refs:
  - ability-scores:int
  - conditions:charmed
  - damage-types:slashing
  - proficiencies:skill-deception
  - proficiencies:skill-insight
  - proficiencies:skill-perception
  - proficiencies:skill-stealth
  - spells:augury
  - spells:detect-magic
  - spells:dream
  - spells:etherealness
  - spells:find-familiar
  - spells:identify
  - spells:locate-object
  - spells:magic-missile
  - spells:phantasmal-killer
  - spells:plane-shift
  - spells:scrying
  - spells:unseen-servant
---
# Night Hag (`monsters:night-hag`)
*Medium fiend, neutral evil* | **CR** 5 (XP 1,800; PB +3)
**AC** 17 | **HP** 112 (15d8 + 45) | **Speed** 30 ft.
**STR** 18 (+4) | **DEX** 15 (+2) | **CON** 16 (+3) | **INT** 16 (+3) | **WIS** 14 (+2) | **CHA** 16 (+3)
**Skills:** [Skill: Deception](../proficiencies/skill-deception.md) +6, [Skill: Insight](../proficiencies/skill-insight.md) +5, [Skill: Perception](../proficiencies/skill-perception.md) +5, [Skill: Stealth](../proficiencies/skill-stealth.md) +5
**Damage Resistances:** cold, fire
**Condition Immunities:** [Charmed](../conditions/charmed.md)
**Senses:** Darkvision 120 ft., Passive Perception 15
**Languages:** Abyssal, Common, Infernal, Primordial

## Traits
- **Coven Magic.** While within 30 feet of at least two hag allies, the hag can cast one of the following spells, requiring no Material components, using the spell’s normal casting time, and using Intelligence as the spellcasting ability (spell save DC 14): Augury, Find Familiar, Identify, Locate Object, Scrying, or Unseen Servant. The hag must finish a Long Rest before using this trait to cast that spell again.
  - Other: [Augury](../spells/augury.md) (level 2), [Find Familiar](../spells/find-familiar.md) (level 1), [Identify](../spells/identify.md) (level 1), [Locate Object](../spells/locate-object.md) (level 2), [Scrying](../spells/scrying.md) (level 5), [Unseen Servant](../spells/unseen-servant.md) (level 1)
- **Magic Resistance.** The hag has Advantage on saving throws against spells and other magical effects.
- **Soul Bag.** The hag has a soul bag. While holding or carrying the bag, the hag can use its Nightmare Haunting action. The bag has AC 15, HP 20, and Resistance to all damage. The bag turns to dust if reduced to 0 Hit Points. If the bag is destroyed, any souls the bag is holding are released. The hag can create a new bag after 7 days.

## Actions
- **Multiattack.** The hag makes two Claw attacks.
- **Claw.** Melee Attack Roll: +7, reach 5 ft. Hit: 13 (2d8 + 4) Slashing damage.
- **Nightmare Haunting (Requires Soul Bag) (1/day).** While on the Ethereal Plane, the hag casts Dream, using the same spellcasting ability as Spellcasting. Only the hag can serve as the spell’s messenger, and the target must be a creature the hag can see on the Material Plane. The spell fails and is wasted if the target is under the effect of the Protection from Evil and Good spell or within a Magic Circle spell. If the target takes damage from the Dream spell, the target’s Hit Point maximum decreases by an amount equal to that damage. If the spell kills the target, its soul is trapped in the hag’s soul bag, and the target can’t be raised from the dead until its soul is released.
  - Other: [Dream](../spells/dream.md) (level 5)
- **Spellcasting.** The hag casts one of the following spells, requiring no Material components and using Intelligence as the spellcasting ability (spell save DC 14):
  - At will: [Detect Magic](../spells/detect-magic.md) (level 1), [Etherealness](../spells/etherealness.md) (level 7), [Magic Missile](../spells/magic-missile.md) (level 4)
  - 2/day: [Phantasmal Killer](../spells/phantasmal-killer.md) (level 4), [Plane Shift](../spells/plane-shift.md) (level 7; Self only)

## Bonus Actions
- **Shape-Shift.** The hag shape-shifts into a Small or Medium Humanoid, or it returns to its true form. Other than its size, its game statistics are the same in each form. Any equipment it is wearing or carrying isn’t transformed.
