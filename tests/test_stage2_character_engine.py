"""Stage 2 Verification Tests (§7.2): Deterministic Character Compiler & Validator.

Covers:
1. RulesStore lookup (canonical ID, bare slug, ambiguous slug collision).
2. Valid Martial PC: Level 5 Human Fighter / Champion / Soldier / Point Buy.
3. Valid Caster PC: Level 5 High Elf Wizard / Evoker / Sage / Standard Array.
4. Valid Unarmored & Species-Trait PCs:
   - Level 1 Goliath Barbarian (speed 35, Unarmored Defense 10+DEX+CON, 2 masteries)
   - Level 3 Wood Elf Monk (speed 35+10=45, Unarmored Defense 10+DEX+WIS)
   - Level 1 Dwarf Fighter (dwarven-toughness +level HP, poison resistance, darkvision 120)
   - Canonical Stage 1 ID input flexibility
5. Negative Validation Tests & Non-Fatal Equipment Warnings:
   - Background ASI disallowed ability & invalid point split
   - Point-buy > 27 & invalid standard array
   - Subclass at level 2, missing subclass at level 3, mismatched subclass
   - Missing required subspecies
   - General feat level prerequisite violation (grappler at L1) & duplicate non-repeatable feat
   - Wrong-class spell (Wizard preparing cure-wounds) & too-high-level spell (fireball at L1)
   - Exceeding class weapon mastery allowance
   - Heavy armor STR minimum warning + -10 ft speed penalty
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.engine.character import compile_character
from srd2024.engine.store import RulesStore


def _fighter_l5_spec() -> dict[str, Any]:
    return {
        "name": "Aldric Vance",
        "species": "human",
        "subspecies": None,
        "size": "Medium",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "class": "fighter",
        "subclass": "champion",
        "level": 5,
        "ability_method": "point_buy",
        "base_abilities": {"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 13, "cha": 10},
        "asi_choices": [
            {"level": 4, "type": "asi", "increases": {"str": 2}},
        ],
        "fighting_style": "defense",
        "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
        "equipment": {
            "armor": "chain-mail",
            "shield": True,
            "weapons": ["longsword", "greatsword", "javelin"],
        },
        "weapon_mastery_weapons": ["longsword", "greatsword", "javelin", "battleaxe"],
        "cantrips": [],
        "prepared_spells": [],
        "hp_method": "fixed",
        "hp_rolls": [],
    }


def _wizard_l5_spec() -> dict[str, Any]:
    return {
        "name": "Lyra Starweave",
        "species": "elf",
        "subspecies": "elven-lineage-high-elf",
        "size": "Medium",
        "background": "sage",
        "background_asi": {"int": 2, "con": 1},
        "origin_feat": "magic-initiate",
        "class": "wizard",
        "subclass": "evoker",
        "level": 5,
        "ability_method": "standard_array",
        "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
        "asi_choices": [
            {"level": 4, "type": "asi", "increases": {"int": 2}},
        ],
        "skill_proficiencies": ["arcana", "history", "investigation", "insight"],
        "equipment": {
            "armor": None,
            "shield": False,
            "weapons": ["quarterstaff", "dagger"],
        },
        "cantrips": ["fire-bolt", "ray-of-frost", "prestidigitation"],
        "prepared_spells": ["fireball", "shield", "magic-missile", "mage-armor", "misty-step"],
        "hp_method": "fixed",
    }


class TestRulesStoreLookup(unittest.TestCase):
    """§4.1: RulesStore read-only loading & ambiguity handling."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_get_rule_canonical_bare_and_ambiguous(self) -> None:
        fb = self.rules.get_rule("spells:fireball")
        self.assertEqual(fb["status"], "ok")
        self.assertEqual(fb["rule"]["slug"], "fireball")

        bare = self.rules.get_rule("fireball")
        self.assertEqual(bare["status"], "ok")
        self.assertEqual(bare["rule"]["slug"], "fireball")

        # "shield" exists in both equipment and spells -> ambiguous with sorted candidates
        ambig = self.rules.get_rule("shield")
        self.assertEqual(ambig["status"], "ambiguous")
        cand_ids = [c["id"] if isinstance(c, dict) else c for c in ambig["candidates"]]
        self.assertEqual(cand_ids, sorted(cand_ids))
        self.assertIn("equipment:shield", cand_ids)
        self.assertIn("spells:shield", cand_ids)

        missing = self.rules.get_rule("does-not-exist-xyz")
        self.assertEqual(missing["status"], "not_found")


class TestValidCharacterBuilds(unittest.TestCase):
    """§7.2.1–§7.2.3: Valid martial, caster, unarmored, and species-trait PC builds."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_valid_level5_human_fighter_champion_soldier(self) -> None:
        res = compile_character(_fighter_l5_spec(), self.rules)
        self.assertEqual(res["status"], "ok", f"Unexpected errors: {res['errors']}")
        self.assertTrue(res["valid"])
        self.assertEqual(res["errors"], [])

        sb = res["statblock"]
        self.assertEqual(sb["name"], "Aldric Vance")
        self.assertEqual(sb["species"], "species:human")
        self.assertEqual(sb["class"], "classes:fighter")
        self.assertEqual(sb["subclass"], "subclasses:champion")
        self.assertEqual(sb["level"], 5)
        self.assertEqual(sb["proficiency_bonus"], 3)

        # STR 15 + 2 (bg) + 2 (ASI) = 19 (+4); CON 14 + 1 (bg) = 15 (+2); DEX 12 (+1)
        self.assertEqual(sb["abilities"]["str"], 19)
        self.assertEqual(sb["abilities"]["con"], 15)
        self.assertEqual(sb["ability_mods"]["str"], 4)
        self.assertEqual(sb["ability_mods"]["con"], 2)
        self.assertEqual(sb["ability_mods"]["dex"], 1)

        # HP: 10 + 2 + 4*(6 + 2) = 44
        self.assertEqual(sb["hp_max"], 44)
        self.assertEqual(sb["hit_die"], 10)

        # AC: Chain Mail (16) + Shield (+2) + Defense (+1) = 19
        self.assertEqual(sb["ac"], 19)
        self.assertTrue(sb["stealth_disadvantage"])

        # Initiative: DEX (+1) + Alert PB (+3) = 4
        self.assertEqual(sb["initiative"], 4)
        self.assertEqual(sb["speed_ft"], 30)
        self.assertEqual(sb["extra_attacks"], 2)

        # Saving throws: STR +7, CON +5
        self.assertEqual(sb["saving_throws"]["str"], 7)
        self.assertEqual(sb["saving_throws"]["con"], 5)
        self.assertEqual(sb["saving_throws"]["dex"], 1)

        # Weapon profiles
        profiles = {w["slug"]: w for w in sb["weapon_profiles"]}
        self.assertIn("longsword", profiles)
        self.assertIn("greatsword", profiles)
        self.assertIn("javelin", profiles)

        ls = profiles["longsword"]
        self.assertTrue(ls["proficient"])
        self.assertEqual(ls["attack_bonus"], 7)  # STR +4 + PB +3
        self.assertEqual(ls["damage_dice"], "1d8")
        self.assertEqual(ls["damage_bonus"], 4)
        self.assertEqual(ls["damage_type"], "slashing")
        self.assertEqual(ls["mastery"], "sap")
        self.assertTrue(ls["mastery_active"])

        gs = profiles["greatsword"]
        self.assertEqual(gs["attack_bonus"], 7)
        self.assertEqual(gs["damage_dice"], "2d6")
        self.assertEqual(gs["mastery"], "graze")
        self.assertTrue(gs["mastery_active"])

        self.assertIsNone(sb["spellcasting"])

    def test_valid_level5_high_elf_wizard_evoker_sage(self) -> None:
        res = compile_character(_wizard_l5_spec(), self.rules)
        self.assertEqual(res["status"], "ok", f"Unexpected errors: {res['errors']}")
        self.assertTrue(res["valid"])
        self.assertEqual(res["errors"], [])

        sb = res["statblock"]
        self.assertEqual(sb["proficiency_bonus"], 3)
        # INT 15 + 2 + 2 = 19 (+4); CON 14 + 1 = 15 (+2); DEX 13 (+1)
        self.assertEqual(sb["abilities"]["int"], 19)
        self.assertEqual(sb["ability_mods"]["int"], 4)
        # HP: 6 + 2 + 4*(4 + 2) = 32
        self.assertEqual(sb["hp_max"], 32)
        self.assertEqual(sb["ac"], 11)  # 10 + DEX (+1)
        self.assertEqual(sb["senses"]["darkvision_ft"], 60)

        sc = sb["spellcasting"]
        self.assertIsNotNone(sc)
        self.assertEqual(sc["ability"], "int")
        self.assertEqual(sc["spell_save_dc"], 8 + 3 + 4)
        self.assertEqual(sc["spell_attack_bonus"], 3 + 4)
        self.assertEqual(sc["slots"].get("1"), 4)
        self.assertEqual(sc["slots"].get("2"), 3)
        self.assertEqual(sc["slots"].get("3"), 2)
        self.assertIn("fireball", sc["prepared_spells"])
        self.assertIn("shield", sc["prepared_spells"])
        self.assertIn("magic-missile", sc["prepared_spells"])
        self.assertIn("fire-bolt", sc["cantrips"])

    def test_valid_goliath_barbarian_unarmored_and_speed(self) -> None:
        spec = {
            "name": "Krag Stonefist",
            "species": "goliath",
            "subspecies": "giant-ancestry-stones-endurance",
            "background": "soldier",
            "background_asi": {"str": 2, "con": 1},
            "class": "barbarian",
            "level": 1,
            "ability_method": "standard_array",
            "base_abilities": {"str": 15, "dex": 14, "con": 13, "int": 8, "wis": 12, "cha": 10},
            "equipment": {"armor": None, "shield": False, "weapons": ["greataxe", "javelin"]},
            "weapon_mastery_weapons": ["greataxe", "javelin"],
            "hp_method": "fixed",
        }
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], f"Unexpected errors: {res['errors']}")
        sb = res["statblock"]
        self.assertEqual(sb["speed_ft"], 35)
        # DEX 14 (+2), CON 14 (+2) -> Unarmored Defense 10 + 2 + 2 = 14
        self.assertEqual(sb["ac"], 14)
        # HP: d12 + CON(+2) = 14
        self.assertEqual(sb["hp_max"], 14)

    def test_valid_level3_wood_elf_monk_speed_and_unarmored_ac(self) -> None:
        spec = {
            "name": "Sylva Windstep",
            "species": "elf",
            "subspecies": "elven-lineage-wood-elf",
            "background": "criminal",
            "background_asi": {"dex": 2, "con": 1},
            "class": "monk",
            "subclass": "warrior-of-the-open-hand",
            "level": 3,
            "ability_method": "point_buy",
            "base_abilities": {"str": 8, "dex": 14, "con": 13, "int": 10, "wis": 15, "cha": 10},
            "equipment": {"armor": None, "shield": False, "weapons": ["shortsword", "dart"]},
            "hp_method": "fixed",
        }
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], f"Unexpected errors: {res['errors']}")
        sb = res["statblock"]
        # Wood Elf speed (35) + Monk L3 Unarmored Movement (+10) = 45
        self.assertEqual(sb["speed_ft"], 45)
        # DEX 16 (+3), WIS 15 (+2) -> Unarmored Defense 10 + 3 + 2 = 15
        self.assertEqual(sb["ac"], 15)

    def test_valid_level1_dwarf_fighter_toughness_and_poison_resistance(self) -> None:
        spec = {
            "name": "Borin Ironbeard",
            "species": "dwarf",
            "background": "soldier",
            "background_asi": {"str": 2, "con": 1},
            "class": "fighter",
            "level": 1,
            "ability_method": "point_buy",
            "base_abilities": {"str": 15, "dex": 10, "con": 13, "int": 10, "wis": 13, "cha": 10},
            "equipment": {"armor": "chain-mail", "shield": True, "weapons": ["warhammer"]},
            "weapon_mastery_weapons": ["warhammer"],
            "hp_method": "fixed",
        }
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], f"Unexpected errors: {res['errors']}")
        sb = res["statblock"]
        # CON 14 (+2): Fighter d10 + 2 + Dwarven Toughness (+1 at L1) = 13
        self.assertEqual(sb["hp_max"], 13)
        self.assertIn("poison", sb["damage_resistances"])
        self.assertEqual(sb["senses"]["darkvision_ft"], 120)

    def test_canonical_stage1_id_inputs_accepted(self) -> None:
        spec = _fighter_l5_spec()
        spec["species"] = "species:human"
        spec["background"] = "backgrounds:soldier"
        spec["class"] = "classes:fighter"
        spec["subclass"] = "subclasses:champion"
        spec["equipment"]["armor"] = "equipment:chain-mail"
        spec["equipment"]["weapons"] = ["equipment:longsword", "equipment:greatsword"]
        spec["weapon_mastery_weapons"] = ["equipment:longsword", "equipment:greatsword"]
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], f"Canonical IDs should be accepted: {res['errors']}")
        self.assertEqual(res["statblock"]["ac"], 19)


class TestNegativeValidationAndWarnings(unittest.TestCase):
    """§7.2.4: Invalid builds return valid=False with clear error codes; warnings emitted cleanly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def _assert_invalid(self, spec: dict[str, Any], label: str) -> dict[str, Any]:
        res = compile_character(spec, self.rules)
        self.assertFalse(res["valid"], f"{label}: expected valid=False, got valid=True")
        self.assertEqual(res["status"], "invalid", f"{label}: expected status='invalid'")
        self.assertTrue(res["errors"], f"{label}: expected non-empty errors list")
        for err in res["errors"]:
            self.assertTrue(err.get("code"), f"{label}: error missing code: {err}")
            self.assertTrue(err.get("message"), f"{label}: error missing message: {err}")
        return res

    def test_disallowed_background_asi_ability_and_invalid_split(self) -> None:
        # Soldier only allows str, dex, con -> picking int must fail
        spec = _fighter_l5_spec()
        spec["background_asi"] = {"int": 2, "str": 1}
        self._assert_invalid(spec, "Soldier disallowed int ASI")

        # Invalid split (+3 to one stat)
        spec2 = _fighter_l5_spec()
        spec2["background_asi"] = {"str": 3}
        self._assert_invalid(spec2, "Invalid +3 background ASI split")

    def test_point_buy_over_27_and_invalid_standard_array(self) -> None:
        # All 15s = 54 points > 27
        spec = _fighter_l5_spec()
        spec["ability_method"] = "point_buy"
        spec["base_abilities"] = {"str": 15, "dex": 15, "con": 15, "int": 15, "wis": 15, "cha": 15}
        self._assert_invalid(spec, "Point-buy exceeding 27")

        # Invalid standard array
        spec2 = _wizard_l5_spec()
        spec2["ability_method"] = "standard_array"
        spec2["base_abilities"] = {"str": 15, "dex": 15, "con": 14, "int": 12, "wis": 10, "cha": 8}
        self._assert_invalid(spec2, "Invalid standard array")

    def test_subclass_level_and_class_mismatch_rules(self) -> None:
        # Level 2 selecting a subclass -> invalid
        spec_l2 = _fighter_l5_spec()
        spec_l2["level"] = 2
        spec_l2["asi_choices"] = []
        spec_l2["weapon_mastery_weapons"] = ["longsword", "greatsword", "javelin"]
        spec_l2["subclass"] = "champion"
        self._assert_invalid(spec_l2, "Level 2 with subclass")

        # Level 3 omitting subclass -> invalid
        spec_l3 = copy.deepcopy(spec_l2)
        spec_l3["level"] = 3
        spec_l3["subclass"] = None
        self._assert_invalid(spec_l3, "Level 3 missing subclass")

        # Mismatched subclass (Fighter + Evoker) -> invalid
        spec_mismatch = copy.deepcopy(spec_l3)
        spec_mismatch["subclass"] = "evoker"
        self._assert_invalid(spec_mismatch, "Fighter with Wizard Evoker subclass")

    def test_missing_required_subspecies(self) -> None:
        spec = _wizard_l5_spec()
        spec["subspecies"] = None
        self._assert_invalid(spec, "Elf missing required subspecies")

    def test_feat_level_prerequisite_and_duplicate_non_repeatable(self) -> None:
        # Level 1 picking General Feat grappler (minimum_level: 4)
        spec_l1 = _fighter_l5_spec()
        spec_l1["level"] = 1
        spec_l1["subclass"] = None
        spec_l1["weapon_mastery_weapons"] = ["longsword", "greatsword", "javelin"]
        spec_l1["asi_choices"] = [{"level": 1, "type": "feat", "feat": "grappler", "ability_increase": "str"}]
        self._assert_invalid(spec_l1, "Level 1 taking Grappler feat")

        # Non-repeatable feat taken twice: Criminal gives alert + Human extra_origin_feat alert
        spec_dup = _fighter_l5_spec()
        spec_dup["background"] = "criminal"
        spec_dup["background_asi"] = {"dex": 2, "con": 1}
        spec_dup["origin_feat"] = "alert"
        spec_dup["extra_origin_feat"] = "alert"
        self._assert_invalid(spec_dup, "Duplicate non-repeatable feat alert")

    def test_invalid_spell_class_and_spell_level(self) -> None:
        # Wizard preparing Cleric-only spell cure-wounds
        spec_wrong_cls = _wizard_l5_spec()
        spec_wrong_cls["prepared_spells"] = ["fireball", "cure-wounds"]
        self._assert_invalid(spec_wrong_cls, "Wizard preparing cure-wounds")

        # Level 1 Wizard preparing 3rd-level spell fireball
        spec_too_high = _wizard_l5_spec()
        spec_too_high["level"] = 1
        spec_too_high["subclass"] = None
        spec_too_high["asi_choices"] = []
        spec_too_high["prepared_spells"] = ["fireball"]
        self._assert_invalid(spec_too_high, "Level 1 Wizard preparing fireball")

    def test_exceeding_weapon_mastery_allowance(self) -> None:
        # Level 1 Barbarian allowance is 2; picking 4 must fail
        spec = {
            "name": "Overmastered",
            "species": "human",
            "background": "soldier",
            "background_asi": {"str": 2, "con": 1},
            "class": "barbarian",
            "level": 1,
            "ability_method": "standard_array",
            "base_abilities": {"str": 15, "dex": 14, "con": 13, "int": 8, "wis": 12, "cha": 10},
            "equipment": {"armor": None, "shield": False, "weapons": ["greataxe", "greatsword", "longsword", "javelin"]},
            "weapon_mastery_weapons": ["greataxe", "greatsword", "longsword", "javelin"],
        }
        self._assert_invalid(spec, "Level 1 Barbarian picking 4 weapon masteries")

    def test_heavy_armor_str_minimum_warning_and_speed_penalty(self) -> None:
        spec = _fighter_l5_spec()
        # Drop STR to 10 (plate-armor requires STR 15)
        spec["background_asi"] = {"dex": 2, "con": 1}
        spec["base_abilities"] = {"str": 10, "dex": 15, "con": 14, "int": 8, "wis": 13, "cha": 10}
        spec["asi_choices"] = [{"level": 4, "type": "asi", "increases": {"dex": 2}}]
        spec["equipment"]["armor"] = "plate-armor"

        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], f"Heavy armor STR shortfall should be a warning, not error: {res['errors']}")
        warn_codes = [w["code"] for w in res["warnings"]]
        self.assertIn("heavy_armor_str_penalty", warn_codes)
        # Base Human speed 30 - 10 penalty = 20 ft
        self.assertEqual(res["statblock"]["speed_ft"], 20)


if __name__ == "__main__":
    unittest.main()
