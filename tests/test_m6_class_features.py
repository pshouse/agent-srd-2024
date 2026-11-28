"""Milestone 6.3A Core 2024 Class Features (#34) + Playtest Bugfixes (#97, #98)."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from srd2024.arena import (
    STATE_SHAPE_VERSION,
    HeuristicTacticalDriver,
    build_human_action_menu,
    build_observation,
    replay_match,
    run_match,
)
from srd2024.arena.jev import enumerate_candidates
from srd2024.engine.character import compile_character
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit_arena_trace.py"
RULES_PATH = REPO_ROOT / "dist" / "rules.json"
COVERAGE_PATH = REPO_ROOT / "dist" / "web" / "rules-coverage.json"


def _audit(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(AUDIT_SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _compile_ok(rules: RulesStore, spec: dict[str, Any]) -> dict[str, Any]:
    res = compile_character(spec, rules)
    assert res["valid"], f"Spec failed to compile: {res['errors']}"
    return res["statblock"]


def _fighter_sb(rules: RulesStore, level: int = 5, *, name: str = "Aldric") -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": name,
        "species": "human",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "class": "fighter",
        "level": level,
        "ability_method": "point_buy",
        "base_abilities": {"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 10, "cha": 10},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "fighting_style": "defense",
        "equipment": {
            "armor": "chain-mail",
            "shield": True,
            "weapons": ["longsword", "javelin"],
        },
        "weapon_mastery_weapons": ["longsword", "javelin"],
        "skill_proficiencies": ["athletics", "perception"],
    }
    if level >= 3:
        spec["subclass"] = "champion"
    asi: list[dict[str, Any]] = []
    if level >= 4:
        asi.append({"level": 4, "type": "asi", "increases": {"str": 2}})
    if level >= 6:
        asi.append({"level": 6, "type": "asi", "increases": {"con": 2}})
    if level >= 8:
        asi.append({"level": 8, "type": "asi", "increases": {"str": 1, "con": 1}})
    if asi:
        spec["asi_choices"] = asi
    return _compile_ok(rules, spec)


def _rogue_sb(rules: RulesStore, level: int = 5, *, name: str = "Kaelen") -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": name,
        "species": "halfling",
        "background": "criminal",
        "background_asi": {"dex": 2, "con": 1},
        "class": "rogue",
        "level": level,
        "ability_method": "standard_array",
        "base_abilities": {"str": 8, "dex": 15, "con": 14, "int": 12, "wis": 13, "cha": 10},
        "origin_feat": "alert",
        "equipment": {
            "armor": "studded-leather-armor",
            "shield": False,
            "weapons": ["shortsword", "dagger", "shortbow", "javelin"],
        },
        "weapon_mastery_weapons": ["shortsword", "dagger"],
        "skill_proficiencies": ["stealth", "acrobatics", "perception", "sleight-of-hand", "investigation", "deception"],
    }
    if level >= 3:
        spec["subclass"] = "thief"
    asi: list[dict[str, Any]] = []
    if level >= 4:
        asi.append({"level": 4, "type": "asi", "increases": {"dex": 2}})
    if level >= 8:
        asi.append({"level": 8, "type": "asi", "increases": {"dex": 1, "con": 1}})
    if level >= 10:
        asi.append({"level": 10, "type": "asi", "increases": {"con": 2}})
    if asi:
        spec["asi_choices"] = asi
    return _compile_ok(rules, spec)


def _paladin_sb(rules: RulesStore, level: int = 5, *, name: str = "Seren") -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": name,
        "species": "human",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "class": "paladin",
        "level": level,
        "ability_method": "point_buy",
        "base_abilities": {"str": 15, "dex": 10, "con": 14, "int": 8, "wis": 10, "cha": 14},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "equipment": {
            "armor": "chain-mail",
            "shield": True,
            "weapons": ["longsword", "javelin"],
        },
        "weapon_mastery_weapons": ["longsword", "javelin"],
        "prepared_spells": ["divine-smite", "bless", "cure-wounds", "shield-of-faith"][: max(2, min(4, level + 1))],
        "skill_proficiencies": ["athletics", "persuasion"],
    }
    if level >= 2:
        spec["fighting_style"] = "defense"
    if level >= 3:
        spec["subclass"] = "oath-of-devotion"
    if level >= 4:
        spec["asi_choices"] = [{"level": 4, "type": "asi", "increases": {"str": 2}}]
    return _compile_ok(rules, spec)


def _life_cleric_sb(rules: RulesStore, level: int = 5, *, name: str = "Dorian") -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": name,
        "species": "dwarf",
        "background": "acolyte",
        "background_asi": {"wis": 2, "cha": 1},
        "class": "cleric",
        "level": level,
        "ability_method": "standard_array",
        "base_abilities": {"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 15, "cha": 12},
        "origin_feat": "magic-initiate",
        "equipment": {
            "armor": "scale-mail",
            "shield": True,
            "weapons": ["mace"],
        },
        "cantrips": ["sacred-flame", "guidance", "light", "resistance"][: (3 if level < 4 else 4)],
        "prepared_spells": ["healing-word", "cure-wounds", "bless", "guiding-bolt", "aid", "mass-healing-word"][
            : (4 if level < 3 else (5 if level < 5 else 6))
        ],
        "skill_proficiencies": ["insight", "religion", "medicine", "persuasion"],
    }
    if level >= 3:
        spec["subclass"] = "life-domain"
    if level >= 4:
        spec["asi_choices"] = [{"level": 4, "type": "asi", "increases": {"wis": 2}}]
    return _compile_ok(rules, spec)


def _start(
    rules: RulesStore,
    combatants: list[dict[str, Any]],
    *,
    init_rolls: list[int] | None = None,
    bounds: dict[str, int] | None = None,
    terrain: dict[str, Any] | None = None,
    state_shape_version: int | None = None,
) -> dict[str, Any]:
    intent: dict[str, Any] = {
        "type": "start_encounter",
        "encounter_id": "m63a-test",
        "seed": 7,
        "combatants": combatants,
    }
    if bounds is not None:
        intent["bounds"] = bounds
    if terrain is not None:
        intent["terrain"] = terrain
    if state_shape_version is not None:
        intent["state_shape_version"] = state_shape_version
    res = step(None, intent, rules, roll_overrides=init_rolls)
    assert res["status"] == "ok", f"start_encounter failed: {res.get('error')}"
    return res["state"]


class TestSubclassCoverageGrading(unittest.TestCase):
    """#34: Honest subclass coverage grading while preserving Stage 2 885 lock."""

    def test_rules_and_coverage_subclass_grades(self) -> None:
        rules_doc = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        cov_doc = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))

        # Stage 2 lock preserved intact.
        self.assertEqual(cov_doc["summary"]["total_compiled"], 885)
        self.assertEqual(
            sorted(cov_doc["summary"]["by_domain"].keys()),
            ["conditions", "equipment", "monsters", "spells", "weapon_masteries"],
        )
        self.assertEqual(
            sorted(cov_doc["entities"].keys()),
            ["conditions", "equipment", "monsters", "spells", "weapon_masteries"],
        )

        # Additive subclass coverage block.
        sc_cov = cov_doc["summary"]["subclass_coverage"]
        self.assertEqual(
            sc_cov,
            {"total": 12, "full": 0, "partial": 1, "narrative": 11, "executable_pct": 8.3},
        )
        self.assertEqual(len(cov_doc["subclass_entities"]), 12)

        subclasses = rules_doc["character_creation"]["subclasses"]
        self.assertEqual(len(subclasses), 12)
        life = subclasses["life-domain"]
        self.assertEqual(life["coverage"], "partial")
        self.assertEqual(
            life["provenance"]["modeled_features"],
            ["life-disciple-of-life", "life-preserve-life", "life-blessed-healer"],
        )
        self.assertEqual(
            life["provenance"]["unmodeled_clauses"],
            ["feature:life-domain-spells", "feature:life-supreme-healing"],
        )

        for slug, sc in subclasses.items():
            if slug == "life-domain":
                continue
            self.assertEqual(sc["coverage"], "narrative", f"{slug} should be narrative")
            self.assertEqual(sc["provenance"]["modeled_features"], [])
            self.assertEqual(
                sc["provenance"]["unmodeled_clauses"],
                [f"feature:{f}" for f in sc["features"]],
            )


class TestFighterClassFeatures(unittest.TestCase):
    """Fighter Second Wind, L5+ Tactical Shift, and Action Surge (with 2024 Magic Action ban)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_second_wind_level_scaling_and_heal(self) -> None:
        self.assertEqual(_fighter_sb(self.rules, 1)["class_resources_max"]["second_wind"]["max"], 2)
        self.assertNotIn("action_surge", _fighter_sb(self.rules, 1)["class_resources_max"])
        self.assertEqual(_fighter_sb(self.rules, 4)["class_resources_max"]["second_wind"]["max"], 3)
        self.assertEqual(_fighter_sb(self.rules, 5)["class_resources_max"]["action_surge"]["max"], 1)
        self.assertEqual(_fighter_sb(self.rules, 10)["class_resources_max"]["second_wind"]["max"], 4)

        sb = _fighter_sb(self.rules, 5)
        st = _start(
            self.rules,
            [
                {"id": "f1", "side": "party", "statblock": sb, "position": {"x": 0, "y": 0}, "hp": 20},
                {"id": "g1", "side": "enemy", "monster": "ghoul", "position": {"x": 5, "y": 0}},
            ],
            init_rolls=[20, 1],
        )
        res = step(st, {"type": "class_feature", "actor_id": "f1", "feature": "second_wind"}, self.rules, roll_overrides=[7])
        self.assertEqual(res["status"], "ok", res.get("error"))
        f1 = res["state"]["combatants"]["f1"]
        self.assertEqual(f1["hp"], 32)  # 20 + 7 + 5
        self.assertFalse(f1["turn_resources"]["bonus_action"])
        self.assertEqual(f1["class_resources"]["second_wind"]["remaining"], 2)
        self.assertEqual(
            res["state_diff"]["resource_changes"],
            [{"id": "f1", "resource": "second_wind", "before": 3, "after": 2}],
        )
        ev_types = [e["type"] for e in res["events"]]
        self.assertIn("class_feature_used", ev_types)
        self.assertIn("healing_applied", ev_types)
        self.assertTrue(any("Second Wind (Bonus Action), regaining 12 HP" in h for h in res["narrative_hooks"]))

    def test_tactical_shift_l5_no_oa_and_bounds_and_l4_refusal(self) -> None:
        # L4 Fighter lacks Tactical Shift -> move_to rejected.
        st4 = _start(
            self.rules,
            [
                {"id": "f4", "side": "party", "statblock": _fighter_sb(self.rules, 4), "position": {"x": 10, "y": 10}, "hp": 20},
                {"id": "g1", "side": "enemy", "monster": "ghoul", "position": {"x": 15, "y": 10}},
            ],
            init_rolls=[20, 1],
        )
        bad4 = step(
            st4,
            {"type": "class_feature", "actor_id": "f4", "feature": "second_wind", "move_to": {"x": 0, "y": 10}},
            self.rules,
        )
        self.assertEqual(bad4["status"], "invalid_intent")
        self.assertIn("Tactical Shift", bad4["error"])

        # L5 Fighter moves up to 15 ft (half of 30 ft) away from adjacent Ghoul WITHOUT provoking OA or using movement!
        bounds = {"min_x": 0, "min_y": 0, "max_x": 30, "max_y": 30}
        terrain = {"walls": [[0, 10]], "difficult_terrain": [], "cover": []}
        st5 = _start(
            self.rules,
            [
                {"id": "f5", "side": "party", "statblock": _fighter_sb(self.rules, 5), "position": {"x": 15, "y": 10}, "hp": 20},
                {"id": "g1", "side": "enemy", "monster": "ghoul", "position": {"x": 20, "y": 10}},
            ],
            init_rolls=[20, 1],
            bounds=bounds,
            terrain=terrain,
        )
        # Wall destination refused:
        bad_wall = step(
            st5,
            {"type": "class_feature", "actor_id": "f5", "feature": "second_wind", "move_to": {"x": 0, "y": 10}},
            self.rules,
        )
        self.assertEqual(bad_wall["status"], "invalid_intent")
        # Exceeds half speed (20 ft > 15 ft) refused:
        bad_far = step(
            st5,
            {"type": "class_feature", "actor_id": "f5", "feature": "second_wind", "move_to": {"x": 15, "y": 30}},
            self.rules,
        )
        self.assertEqual(bad_far["status"], "invalid_intent")
        # Valid 15-ft Tactical Shift away from adjacent Ghoul: NO OA, movement_remaining_ft still 30!
        ok5 = step(
            st5,
            {"type": "class_feature", "actor_id": "f5", "feature": "second_wind", "move_to": {"x": 15, "y": 25}},
            self.rules,
            roll_overrides=[6],
        )
        self.assertEqual(ok5["status"], "ok", ok5.get("error"))
        self.assertEqual(ok5["state"]["combatants"]["f5"]["position"], {"x": 15, "y": 25})
        self.assertEqual(ok5["state"]["combatants"]["f5"]["movement_remaining_ft"], 30)
        self.assertTrue(ok5["state"]["combatants"]["g1"]["turn_resources"]["reaction"])
        self.assertFalse(any(e["type"] == "opportunity_attack" for e in ok5["events"]))
        moved_ev = next(e for e in ok5["events"] if e["type"] == "moved")
        self.assertTrue(moved_ev["tactical_shift"])
        self.assertFalse(moved_ev["provokes_oa"])

    def test_action_surge_extra_attack_and_magic_action_ban(self) -> None:
        sb = _fighter_sb(self.rules, 5)
        # Give Fighter a spellcasting block to test Magic Action ban directly.
        sb_caster = copy.deepcopy(sb)
        sb_caster["spellcasting"] = {
            "ability": "int",
            "spell_save_dc": 12,
            "spell_attack_bonus": 4,
            "slots": {"1": 2},
            "is_pact_magic": False,
            "cantrips": ["fire-bolt"],
            "prepared_spells": ["magic-missile"],
        }
        sb_caster["features"] = list(sb_caster["features"]) + ["life-preserve-life"]
        sb_caster["class_resources_max"]["channel_divinity"] = {"max": 2, "recharge": "short_rest_one"}

        st = _start(
            self.rules,
            [
                {"id": "f5", "side": "party", "statblock": sb_caster, "position": {"x": 0, "y": 0}, "hp": 10},
                {"id": "g1", "side": "enemy", "monster": "ogre", "position": {"x": 5, "y": 0}},
            ],
            init_rolls=[20, 1],
        )

        # 1. Activate Action Surge (free!) -> surge_action = True, action = True.
        r_as = step(st, {"type": "class_feature", "actor_id": "f5", "feature": "action_surge"}, self.rules)
        self.assertEqual(r_as["status"], "ok", r_as.get("error"))
        self.assertTrue(r_as["state"]["combatants"]["f5"]["turn_resources"]["surge_action"])
        self.assertTrue(r_as["state"]["combatants"]["f5"]["turn_resources"]["action"])
        self.assertTrue(r_as["legal_actions"]["surge_action_available"])

        # Cannot activate Action Surge twice in one turn (even if we inject an extra use).
        st_two = copy.deepcopy(r_as["state"])
        st_two["combatants"]["f5"]["class_resources"]["action_surge"]["remaining"] = 1
        r_dup = step(st_two, {"type": "class_feature", "actor_id": "f5", "feature": "action_surge"}, self.rules)
        self.assertEqual(r_dup["status"], "invalid_intent")

        # 2. Cast Fire Bolt (consumes regular Action); surge_action remains True!
        r_cast1 = step(
            r_as["state"],
            {"type": "cast_spell", "actor_id": "f5", "spell": "fire-bolt", "target_ids": ["g1"]},
            self.rules,
            roll_overrides=[18, 5, 5],
        )
        self.assertEqual(r_cast1["status"], "ok", r_cast1.get("error"))
        self.assertFalse(r_cast1["state"]["combatants"]["f5"]["turn_resources"]["action"])
        self.assertTrue(r_cast1["state"]["combatants"]["f5"]["turn_resources"]["surge_action"])
        self.assertNotIn("cast_spell", r_cast1["legal_actions"]["actions"])
        self.assertNotIn("preserve_life", r_cast1["legal_actions"]["class_features"])
        self.assertIn("attack", r_cast1["legal_actions"]["actions"])

        # 3. Attempting a 2nd Magic action (cantrip OR preserve_life) with only surge_action left MUST fail!
        r_ban_spell = step(
            r_cast1["state"],
            {"type": "cast_spell", "actor_id": "f5", "spell": "fire-bolt", "target_ids": ["g1"]},
            self.rules,
        )
        self.assertEqual(r_ban_spell["status"], "invalid_intent")
        self.assertEqual(r_ban_spell["error"], "Action Surge cannot be used to take the Magic action (5e 2024 SRD 5.2).")

        r_ban_pl = step(
            r_cast1["state"],
            {"type": "class_feature", "actor_id": "f5", "feature": "preserve_life", "target_ids": ["f5"]},
            self.rules,
        )
        self.assertEqual(r_ban_pl["status"], "invalid_intent")
        self.assertEqual(r_ban_pl["error"], "Action Surge cannot be used to take the Magic action (5e 2024 SRD 5.2).")

        # 4. Taking the Attack action with surge_action succeeds AND grants full Extra Attack (2 attacks)!
        r_atk1 = step(
            r_cast1["state"],
            {"type": "attack", "actor_id": "f5", "target_id": "g1", "weapon": "longsword"},
            self.rules,
            roll_overrides=[15, 5],
        )
        self.assertEqual(r_atk1["status"], "ok", r_atk1.get("error"))
        self.assertEqual(r_atk1["state"]["combatants"]["f5"]["turn_resources"]["attacks_remaining"], 1)
        r_atk2 = step(
            r_atk1["state"],
            {"type": "attack", "actor_id": "f5", "target_id": "g1", "weapon": "longsword"},
            self.rules,
            roll_overrides=[15, 5],
        )
        self.assertEqual(r_atk2["status"], "ok", r_atk2.get("error"))
        self.assertEqual(r_atk2["state"]["combatants"]["f5"]["turn_resources"]["attacks_remaining"], 0)


class TestRogueSneakAttack(unittest.TestCase):
    """Rogue Sneak Attack: scaling, Finesse/Ranged, Advantage/Ally, Crit, Resistance, 1/turn & Off-turn OA."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_sneak_attack_ally_crit_resistance_once_per_turn_and_oa(self) -> None:
        rogue = _rogue_sb(self.rules, 5)  # 3d6 Sneak Attack
        fighter = _fighter_sb(self.rules, 5)
        st = _start(
            self.rules,
            [
                {"id": "rog", "side": "party", "statblock": rogue, "position": {"x": 0, "y": 0}},
                {"id": "ally", "side": "party", "statblock": fighter, "position": {"x": 5, "y": 5}},
                {"id": "foe", "side": "enemy", "monster": "ogre", "position": {"x": 5, "y": 0}},
            ],
            init_rolls=[20, 1, 18],
        )
        # Give foe piercing resistance to verify single halving of combined (weapon + SA) damage!
        st["combatants"]["foe"]["damage_resistances"] = ["piercing"]

        # 1. Normal roll + non-incapacitated ally within 5 ft -> Sneak Attack (3d6) fires!
        # Shortsword (1d6+4) roll=3 => 7; SA (3d6) rolls=[1, 1, 1] => 3; combined raw = 10 -> resistant = 5!
        # (If double-floored: 7//2 + 3//2 = 3 + 1 = 4 != 5!)
        r1 = step(
            st,
            {"type": "attack", "actor_id": "rog", "target_id": "foe", "weapon": "shortsword"},
            self.rules,
            roll_overrides=[15, 3, 1, 1, 1],
        )
        self.assertEqual(r1["status"], "ok", r1.get("error"))
        sa_evs = [e for e in r1["events"] if e["type"] == "sneak_attack_triggered"]
        self.assertEqual(len(sa_evs), 1)
        self.assertEqual(sa_evs[0]["dice"], "3d6")
        self.assertEqual(sa_evs[0]["damage"], 3)
        self.assertEqual(sa_evs[0]["trigger"], "ally_within_5ft")
        dmg_ev = next(e for e in r1["events"] if e["type"] == "damage_applied")
        self.assertEqual(dmg_ev["breakdown"][0]["raw"], 10)
        self.assertEqual(dmg_ev["breakdown"][0]["effective"], 5)
        self.assertTrue(r1["state"]["combatants"]["rog"]["turn_resources"]["sneak_attack_used"])
        self.assertFalse(r1["legal_actions"]["sneak_attack_available"])

        # 2. Second hit on Rogue's own turn (Nick off-hand dagger) MUST NOT trigger Sneak Attack again!
        r2 = step(
            r1["state"],
            {"type": "attack", "actor_id": "rog", "target_id": "foe", "weapon": "dagger", "offhand": True},
            self.rules,
            roll_overrides=[15, 16, 3],  # Vex gave advantage
        )
        self.assertEqual(r2["status"], "ok", r2.get("error"))
        self.assertEqual([e for e in r2["events"] if e["type"] == "sneak_attack_triggered"], [])

        # 3. End Rogue's turn -> Foe's turn begins -> Rogue's sneak_attack_used resets!
        # Foe moves away from Rogue without Disengage -> Opportunity Attack triggers Sneak Attack AGAIN!
        r_end = step(r2["state"], {"type": "end_turn", "actor_id": "rog"}, self.rules)
        self.assertFalse(r_end["state"]["combatants"]["rog"]["turn_resources"]["sneak_attack_used"])
        r_move = step(
            r_end["state"],
            {"type": "move", "actor_id": "foe", "to": {"x": 20, "y": 0}},
            self.rules,
            roll_overrides=[18, 4, 5, 5, 5],  # OA hits (d20=18), dmg=4, SA 3d6=[5,5,5]=15
        )
        self.assertEqual(r_move["status"], "ok", r_move.get("error"))
        oa_sa = [e for e in r_move["events"] if e["type"] == "sneak_attack_triggered"]
        self.assertEqual(len(oa_sa), 1)
        self.assertTrue(oa_sa[0].get("reaction"))
        self.assertEqual(oa_sa[0]["damage"], 15)

    def test_sneak_attack_crit_doubles_dice_and_non_finesse_javelin_excluded(self) -> None:
        rogue = _rogue_sb(self.rules, 5)
        st = _start(
            self.rules,
            [
                {"id": "rog", "side": "party", "statblock": rogue, "position": {"x": 0, "y": 0}},
                {"id": "foe", "side": "enemy", "monster": "ogre", "position": {"x": 15, "y": 0}},
            ],
            init_rolls=[20, 1],
        )
        # Thrown non-Finesse melee javelin with Advantage MUST NOT trigger Sneak Attack!
        r_jav = step(
            st,
            {"type": "attack", "actor_id": "rog", "target_id": "foe", "weapon": "javelin", "advantage_override": True},
            self.rules,
            roll_overrides=[18, 12, 4],
        )
        self.assertEqual(r_jav["status"], "ok", r_jav.get("error"))
        self.assertEqual([e for e in r_jav["events"] if e["type"] == "sneak_attack_triggered"], [])

        # Ranged shortbow crit with Advantage doubles 3d6 -> 6d6!
        r_bow = step(
            st,
            {"type": "attack", "actor_id": "rog", "target_id": "foe", "weapon": "shortbow", "advantage_override": True},
            self.rules,
            roll_overrides=[20, 5, 4, 4, 6, 6, 6, 6, 6, 6],  # nat20, 2d6 bow + 6d6 SA
        )
        self.assertEqual(r_bow["status"], "ok", r_bow.get("error"))
        sa = next(e for e in r_bow["events"] if e["type"] == "sneak_attack_triggered")
        self.assertTrue(sa["crit"])
        self.assertEqual(len(sa["rolls"]), 6)
        self.assertEqual(sa["damage"], 36)

        # Suppressing Sneak Attack with use_sneak_attack=False works.
        r_no_sa = step(
            st,
            {
                "type": "attack",
                "actor_id": "rog",
                "target_id": "foe",
                "weapon": "shortbow",
                "advantage_override": True,
                "use_sneak_attack": False,
            },
            self.rules,
            roll_overrides=[15, 10, 4],
        )
        self.assertEqual([e for e in r_no_sa["events"] if e["type"] == "sneak_attack_triggered"], [])


class TestPaladinLayOnHands(unittest.TestCase):
    """Paladin Lay on Hands: 2024 Bonus Action, 5*level LR pool, Touch, Cure Poison, NO Undead/Construct ban."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_lay_on_hands_heal_revive_cure_poison_and_srd52_raw_undead_construct(self) -> None:
        pal = _paladin_sb(self.rules, 5)  # 25 HP pool
        self.assertEqual(pal["class_resources_max"]["lay_on_hands"], {"max": 25, "recharge": "long_rest"})
        ally = _fighter_sb(self.rules, 5)
        st = _start(
            self.rules,
            [
                {"id": "pal", "side": "party", "statblock": pal, "position": {"x": 0, "y": 0}},
                {"id": "ally", "side": "party", "statblock": ally, "position": {"x": 5, "y": 0}, "hp": 1, "conditions": ["poisoned"]},
                {"id": "far", "side": "party", "statblock": ally, "position": {"x": 15, "y": 0}, "hp": 10},
                {"id": "skel", "side": "enemy", "monster": "skeleton", "position": {"x": 0, "y": 5}, "hp": 5},
                {"id": "arm", "side": "enemy", "monster": "animated-armor", "position": {"x": 5, "y": 5}, "hp": 10},
            ],
            init_rolls=[20, 15, 10, 5, 1],
        )

        # Out of Touch range (> 5 ft) rejected.
        bad_far = step(
            st,
            {"type": "class_feature", "actor_id": "pal", "feature": "lay_on_hands", "mode": "heal", "target_id": "far", "amount": 5},
            self.rules,
        )
        self.assertEqual(bad_far["status"], "invalid_intent")

        # Cure Poisoned costs 5 HP, restores 0 HP, removes poisoned.
        r_cure = step(
            st,
            {"type": "class_feature", "actor_id": "pal", "feature": "lay_on_hands", "mode": "cure_poison", "target_id": "ally"},
            self.rules,
        )
        self.assertEqual(r_cure["status"], "ok", r_cure.get("error"))
        self.assertEqual(r_cure["state"]["combatants"]["pal"]["class_resources"]["lay_on_hands"]["remaining"], 20)
        self.assertEqual(r_cure["state"]["combatants"]["ally"]["hp"], 1)
        self.assertEqual(r_cure["state"]["combatants"]["ally"]["conditions"], [])
        self.assertTrue(any(e["type"] == "condition_ended" and e["condition"] == "poisoned" for e in r_cure["events"]))

        # SRD 5.2 RAW: heals living Undead (skeleton) and Construct (animated-armor) with NO exclusion!
        r_undead = step(
            st,
            {"type": "class_feature", "actor_id": "pal", "feature": "lay_on_hands", "mode": "heal", "target_id": "skel", "amount": 4},
            self.rules,
        )
        self.assertEqual(r_undead["status"], "ok", r_undead.get("error"))
        self.assertEqual(r_undead["state"]["combatants"]["skel"]["hp"], 9)

        r_construct = step(
            st,
            {"type": "class_feature", "actor_id": "pal", "feature": "lay_on_hands", "mode": "heal", "target_id": "arm", "amount": 6},
            self.rules,
        )
        self.assertEqual(r_construct["status"], "ok", r_construct.get("error"))
        self.assertEqual(r_construct["state"]["combatants"]["arm"]["hp"], 16)

        # Dead target refused!
        st_dead = copy.deepcopy(st)
        st_dead["combatants"]["ally"]["hp"] = 0
        st_dead["combatants"]["ally"]["alive"] = False
        r_dead = step(
            st_dead,
            {"type": "class_feature", "actor_id": "pal", "feature": "lay_on_hands", "mode": "heal", "target_id": "ally", "amount": 5},
            self.rules,
        )
        self.assertEqual(r_dead["status"], "invalid_intent")


class TestLifeClericFeatures(unittest.TestCase):
    """Life Domain Cleric: Preserve Life, #34 Disciple of Life, Blessed Healer."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_preserve_life_bloodied_half_max_cap_and_los(self) -> None:
        cleric = _life_cleric_sb(self.rules, 5)  # pool 25, 2 CD uses
        f = _fighter_sb(self.rules, 5)  # hp_max 44 -> half = 22
        r = _rogue_sb(self.rules, 5)    # hp_max 33 -> half = 16
        terrain = {"walls": [[10, 0]], "difficult_terrain": [], "cover": []}
        st = _start(
            self.rules,
            [
                {"id": "clr", "side": "party", "statblock": cleric, "position": {"x": 0, "y": 0}},
                {"id": "f1", "side": "party", "statblock": f, "position": {"x": 0, "y": 15}, "hp": 10},
                {"id": "r1", "side": "party", "statblock": r, "position": {"x": 0, "y": 20}, "hp": 4},
                {"id": "healthy", "side": "party", "statblock": f, "position": {"x": 0, "y": 5}, "hp": 30},
                {"id": "walled", "side": "party", "statblock": f, "position": {"x": 20, "y": 0}, "hp": 10},
            ],
            init_rolls=[20, 15, 10, 5, 1],
            terrain=terrain,
        )

        # Non-Bloodied target rejected.
        bad_nb = step(
            st,
            {"type": "class_feature", "actor_id": "clr", "feature": "preserve_life", "allocations": {"healthy": 5}},
            self.rules,
        )
        self.assertEqual(bad_nb["status"], "invalid_intent")

        # Behind wall (no LoS) rejected.
        bad_wall = step(
            st,
            {"type": "class_feature", "actor_id": "clr", "feature": "preserve_life", "allocations": {"walled": 5}},
            self.rules,
        )
        self.assertEqual(bad_wall["status"], "invalid_intent")

        # Allocation exceeding half-max cap rejected (f1 has 10/44 -> cap 12; 13 refused).
        bad_cap = step(
            st,
            {"type": "class_feature", "actor_id": "clr", "feature": "preserve_life", "allocations": {"f1": 13}},
            self.rules,
        )
        self.assertEqual(bad_cap["status"], "invalid_intent")

        # Deterministic greedy split via target_ids: r1 (4/38 -> half=19, cap 15) then f1 (10/44 -> half=22, gets remaining 10 -> 20), total 25!
        ok_greedy = step(
            st,
            {"type": "class_feature", "actor_id": "clr", "feature": "preserve_life", "target_ids": ["f1", "r1"]},
            self.rules,
        )
        self.assertEqual(ok_greedy["status"], "ok", ok_greedy.get("error"))
        r_half = r["hp_max"] // 2
        self.assertEqual(ok_greedy["state"]["combatants"]["r1"]["hp"], r_half)
        self.assertEqual(ok_greedy["state"]["combatants"]["f1"]["hp"], 10 + (25 - (r_half - 4)))
        self.assertEqual(ok_greedy["state"]["combatants"]["clr"]["class_resources"]["channel_divinity"]["remaining"], 1)
        # Preserve Life is NOT a spell slot -> no disciple_of_life_bonus!
        for ev in ok_greedy["events"]:
            if ev["type"] == "healing_applied":
                self.assertNotIn("disciple_of_life_bonus", ev)

    def test_disciple_of_life_and_blessed_healer(self) -> None:
        cleric6 = _life_cleric_sb(self.rules, 6)  # L6 has Disciple of Life + Blessed Healer + 3 CD
        self.assertEqual(cleric6["class_resources_max"]["channel_divinity"]["max"], 3)
        ally = _fighter_sb(self.rules, 5)
        st = _start(
            self.rules,
            [
                {"id": "clr", "side": "party", "statblock": cleric6, "position": {"x": 0, "y": 0}, "hp": 20},
                {"id": "ally", "side": "party", "statblock": ally, "position": {"x": 15, "y": 0}, "hp": 10},
            ],
            init_rolls=[20, 1],
        )
        # Cast Healing Word at slot level 2 on ally: 4d4 (roll=[2,2,2,2]=8) + WIS(4) = 12 base + (2+2=4) DoL = 16 HP to ally;
        # AND Blessed Healer self-heals Cleric for 2+2 = 4 HP!
        r = step(
            st,
            {"type": "cast_spell", "actor_id": "clr", "spell": "healing-word", "slot_level": 2, "target_ids": ["ally"]},
            self.rules,
            roll_overrides=[2, 2, 2, 2],
        )
        self.assertEqual(r["status"], "ok", r.get("error"))
        heals = [e for e in r["events"] if e["type"] == "healing_applied"]
        self.assertEqual(len(heals), 2)
        ally_h = next(h for h in heals if h["target_id"] == "ally")
        self_h = next(h for h in heals if h["target_id"] == "clr")
        self.assertEqual(ally_h["disciple_of_life_bonus"], 4)
        self.assertEqual(ally_h["amount"], 16)
        self.assertEqual(r["state"]["combatants"]["ally"]["hp"], 26)
        self.assertEqual(self_h["feature"], "blessed_healer")
        self.assertEqual(self_h["amount"], 4)
        self.assertEqual(r["state"]["combatants"]["clr"]["hp"], 24)
        self.assertTrue(any("(+4 Disciple of Life)" in h for h in r["narrative_hooks"]))
        self.assertTrue(any("Blessed Healer restores 4 HP" in h for h in r["narrative_hooks"]))

        # Self-only heal triggers Disciple of Life, NOT Blessed Healer!
        r_self = step(
            st,
            {"type": "cast_spell", "actor_id": "clr", "spell": "healing-word", "slot_level": 1, "target_ids": ["clr"]},
            self.rules,
            roll_overrides=[2, 2],
        )
        self.assertEqual(r_self["status"], "ok", r_self.get("error"))
        heals_self = [e for e in r_self["events"] if e["type"] == "healing_applied"]
        self.assertEqual(len(heals_self), 1)
        self.assertEqual(heals_self[0]["disciple_of_life_bonus"], 3)
        self.assertFalse(any(h.get("feature") == "blessed_healer" for h in heals_self))


class TestCarryoverAndLegacyGating(unittest.TestCase):
    """STATE_SHAPE_VERSION = 3 carryover overrides & v1/v2 legacy gating."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_start_encounter_carryover_overrides(self) -> None:
        cleric = _life_cleric_sb(self.rules, 5)
        st = _start(
            self.rules,
            [
                {
                    "id": "clr",
                    "side": "party",
                    "statblock": cleric,
                    "position": {"x": 0, "y": 0},
                    "hp": 18,
                    "slots": {"1": 1, "2": 2, "3": 0},
                    "class_resources": {"channel_divinity": {"remaining": 1}},
                    "hit_dice": {"remaining": 3},
                    "heroic_inspiration": False,
                    "death_saves": {"successes": 1, "failures": 0},
                },
                {"id": "g1", "side": "enemy", "monster": "ghoul", "position": {"x": 15, "y": 0}},
            ],
            init_rolls=[20, 1],
        )
        self.assertEqual(st["state_shape_version"], STATE_SHAPE_VERSION)
        clr = st["combatants"]["clr"]
        self.assertEqual(clr["hp"], 18)
        self.assertEqual(clr["spellcasting"]["slots"], {"1": 1, "2": 2, "3": 0})
        self.assertEqual(clr["slots_max"], {"1": 4, "2": 3, "3": 2})
        self.assertEqual(clr["class_resources"]["channel_divinity"]["remaining"], 1)
        self.assertEqual(clr["class_resources"]["channel_divinity"]["max"], 2)
        self.assertEqual(clr["hit_dice"], {"die": 8, "max": 5, "remaining": 3})
        self.assertEqual(clr["death_saves"], {"successes": 1, "failures": 0})

    def test_legacy_state_shape_version_2_omits_v3_fields_and_skips_passives(self) -> None:
        rogue = _rogue_sb(self.rules, 5)
        st2 = _start(
            self.rules,
            [
                {"id": "rog", "side": "party", "statblock": rogue, "position": {"x": 0, "y": 0}},
                {"id": "foe", "side": "enemy", "monster": "ogre", "position": {"x": 5, "y": 0}},
            ],
            init_rolls=[20, 1],
            state_shape_version=2,
        )
        self.assertNotIn("state_shape_version", st2)
        self.assertNotIn("class_resources", st2["combatants"]["rog"])
        self.assertNotIn("sneak_attack_used", st2["combatants"]["rog"]["turn_resources"])
        r = step(
            st2,
            {"type": "attack", "actor_id": "rog", "target_id": "foe", "weapon": "shortsword", "advantage_override": True},
            self.rules,
            roll_overrides=[18, 12, 4],
        )
        self.assertEqual([e for e in r["events"] if e["type"] == "sneak_attack_triggered"], [])
        self.assertNotIn("resource_changes", r["state_diff"])


class TestBugfixes97And98AndAuditRules(unittest.TestCase):
    """#97 cover audit fix, #98 human.py Nick off-hand menu fix, and all 7 M6.3A audit rules."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _write(self, name: str, trace: dict[str, Any]) -> Path:
        p = Path(self.tmp.name) / name
        p.write_text(json.dumps(trace), encoding="utf-8")
        return p

    def test_bugfix_98_human_menu_hides_offhand_after_nick_used_or_no_bonus(self) -> None:
        # Rogue Kaelen has Shortsword (Vex, Light) + Dagger (Nick, Light).
        rogue = _rogue_sb(self.rules, 5)
        st = _start(
            self.rules,
            [
                {"id": "rog", "side": "party", "statblock": rogue, "position": {"x": 0, "y": 0}},
                {"id": "g1", "side": "enemy", "monster": "ogre", "position": {"x": 5, "y": 0}},
            ],
            init_rolls=[20, 1],
        )
        # Step 1: Shortsword main attack -> light_attack_made=True, nick_used=False, bonus_action=True.
        r1 = step(st, {"type": "attack", "actor_id": "rog", "target_id": "g1", "weapon": "shortsword"}, self.rules, roll_overrides=[18, 4])
        obs1 = build_observation(r1["state"], "rog", {"id": "t", "name": "t"}, {"party": [], "enemy": []}, [], {}, self.rules)
        menu1 = build_human_action_menu(obs1, self.rules)
        offhand1 = [m for m in menu1 if m["intent"].get("type") == "attack" and m["intent"].get("offhand") is True]
        self.assertTrue(offhand1, "Expected Nick off-hand option before nick_used is True")

        # Step 2: Execute Nick off-hand dagger -> nick_used=True, bonus_action STILL True!
        r2 = step(r1["state"], {"type": "attack", "actor_id": "rog", "target_id": "g1", "weapon": "dagger", "offhand": True}, self.rules, roll_overrides=[15, 16, 3])
        self.assertTrue(r2["state"]["combatants"]["rog"]["turn_resources"]["nick_used"])
        self.assertTrue(r2["state"]["combatants"]["rog"]["turn_resources"]["bonus_action"])

        # #98: Menu MUST NOT offer ANY offhand: True attack now!
        obs2 = build_observation(r2["state"], "rog", {"id": "t", "name": "t"}, {"party": [], "enemy": []}, [], {}, self.rules)
        menu2 = build_human_action_menu(obs2, self.rules)
        offhand2 = [m for m in menu2 if m["intent"].get("type") == "attack" and m["intent"].get("offhand") is True]
        self.assertEqual(offhand2, [], f"Bug #98 regression: offhand still offered after nick_used: {offhand2}")

        # Non-Nick dual-wielder with light_attack_made=True and bonus_action=False -> NO offhand!
        st_nonick = copy.deepcopy(r1["state"])
        for w in st_nonick["combatants"]["rog"]["weapons"]:
            w["mastery_active"] = False
        st_nonick["combatants"]["rog"]["turn_resources"]["bonus_action"] = False
        obs3 = build_observation(st_nonick, "rog", {"id": "t", "name": "t"}, {"party": [], "enemy": []}, [], {}, self.rules)
        menu3 = build_human_action_menu(obs3, self.rules)
        offhand3 = [m for m in menu3 if m["intent"].get("type") == "attack" and m["intent"].get("offhand") is True]
        self.assertEqual(offhand3, [])

    def test_human_menu_and_jev_expose_class_features(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "f5", "side": "party", "statblock": _fighter_sb(self.rules, 5), "position": {"x": 0, "y": 0}, "hp": 20},
                {"id": "pal", "side": "party", "statblock": _paladin_sb(self.rules, 5), "position": {"x": 5, "y": 0}, "hp": 20},
                {"id": "clr", "side": "party", "statblock": _life_cleric_sb(self.rules, 5), "position": {"x": 10, "y": 0}, "hp": 10},
                {"id": "g1", "side": "enemy", "monster": "ogre", "position": {"x": 5, "y": 5}},
            ],
            init_rolls=[20, 15, 10, 1],
        )
        obs_f = build_observation(st, "f5", {"id": "t", "name": "t"}, {"party": [], "enemy": []}, [], {}, self.rules)
        f_feats = {m["intent"].get("feature") for m in build_human_action_menu(obs_f, self.rules) if m["intent"].get("type") == "class_feature"}
        self.assertEqual(f_feats, {"second_wind", "action_surge"})
        jev_f = {i.get("feature") for _, _, i in enumerate_candidates(obs_f, self.rules) if i.get("type") == "class_feature"}
        self.assertIn("second_wind", jev_f)

    def test_audit_97_cover_and_all_7_m63a_rules_pass_and_catch_violations(self) -> None:
        # Build a clean trace covering cover (#97) AND all 7 M6.3A features!
        terrain = {
            "walls": [],
            "difficult_terrain": [],
            "cover": [{"x": 10, "y": 0, "kind": "half"}],
        }
        combatants = [
            {"id": "f5", "side": "party", "statblock": _fighter_sb(self.rules, 5), "position": {"x": 0, "y": 0}, "hp": 15},
            {"id": "rog", "side": "party", "statblock": _rogue_sb(self.rules, 5), "position": {"x": 0, "y": 5}},
            {"id": "pal", "side": "party", "statblock": _paladin_sb(self.rules, 5), "position": {"x": 5, "y": 0}, "conditions": ["poisoned"]},
            {"id": "clr", "side": "party", "statblock": _life_cleric_sb(self.rules, 6), "position": {"x": 5, "y": 5}, "hp": 25},
            {"id": "foe", "side": "enemy", "monster": "ogre", "position": {"x": 15, "y": 0}},
        ]
        init_state = _start(self.rules, combatants, init_rolls=[20, 14, 12, 10, 1], terrain=terrain)
        st = copy.deepcopy(init_state)
        actions: list[dict[str, Any]] = []

        def do(intent: dict[str, Any], rolls: list[int] | None = None) -> None:
            nonlocal st
            res = step(st, intent, self.rules, roll_overrides=rolls)
            self.assertEqual(res["status"], "ok", f"{intent} failed: {res.get('error')}")
            actions.append({"seq": len(actions) + 1, "actor_id": intent["actor_id"], "intent": intent, "events": res["events"]})
            st = res["state"]

        # Fighter: Second Wind + Tactical Shift (no OA!), Action Surge, Dodge + Dash.
        do({"type": "class_feature", "actor_id": "f5", "feature": "second_wind", "move_to": {"x": 0, "y": 10}}, [6])
        do({"type": "class_feature", "actor_id": "f5", "feature": "action_surge"})
        do({"type": "dodge", "actor_id": "f5"})
        do({"type": "dash", "actor_id": "f5"})
        do({"type": "end_turn", "actor_id": "f5"})

        # Rogue: Shortbow attack across half cover (+2 AC!) with Advantage -> Sneak Attack 3d6!
        do({"type": "attack", "actor_id": "rog", "target_id": "foe", "weapon": "shortbow", "advantage_override": True}, [18, 12, 4, 3, 4, 5])
        do({"type": "end_turn", "actor_id": "rog"})

        # Paladin: Lay on Hands cure_poison on self (5 HP), then end_turn.
        do({"type": "class_feature", "actor_id": "pal", "feature": "lay_on_hands", "mode": "cure_poison", "target_id": "pal"})
        do({"type": "end_turn", "actor_id": "pal"})

        # Life Cleric L6: Healing Word (Bonus Action, slot 1) on f5 -> Disciple of Life (+3) + Blessed Healer (+3 self)!
        # Then Preserve Life (Magic Action) on f5? Wait: f5 has 15+11=26 > 22, so drop pal HP via direct test or heal clr/f5 via Preserve Life after Sacred Flame!
        # Let's test Sacred Flame (cover_ignored!) on foe across half cover, AND Healing Word L1 on f5 (DoL + Blessed Healer)!
        do({"type": "cast_spell", "actor_id": "clr", "spell": "healing-word", "slot_level": 1, "target_ids": ["f5"]}, [2, 2])
        do({"type": "cast_spell", "actor_id": "clr", "spell": "sacred-flame", "target_ids": ["foe"]}, [1, 4, 5])
        do({"type": "end_turn", "actor_id": "clr"})

        # Round 2: Foe ends turn -> Cleric uses Preserve Life on Bloodied f5? Let's injure pal to 10 in another short trace or just run foe attack!
        # Even simpler: run a second turn for Pal Lay on Hands heal and Cleric Preserve Life!
        do({"type": "end_turn", "actor_id": "foe"})
        do({"type": "end_turn", "actor_id": "f5"})
        do({"type": "end_turn", "actor_id": "rog"})
        # Paladin Lay on Hands heal on f5? Move next to f5 (0,10) -> move to (5,10) and heal f5!
        do({"type": "move", "actor_id": "pal", "to": {"x": 5, "y": 10}})
        do({"type": "class_feature", "actor_id": "pal", "feature": "lay_on_hands", "mode": "heal", "target_id": "f5", "amount": 5})
        do({"type": "end_turn", "actor_id": "pal"})
        # Cleric uses Preserve Life on Bloodied? Drop clr or use a separate Preserve Life trace to keep running HP honest!
        trace = {"state_shape_version": STATE_SHAPE_VERSION, "initial_state": init_state, "actions": actions}
        res = _audit(self._write("m63a-clean.json", trace))
        self.assertEqual(res.returncode, 0, res.stdout)
        for rule in (
            "Second Wind formula, self-only, Bonus Action & pool",
            "Action Surge Fighter L2+ & 1/turn limit",
            "Sneak Attack eligibility, scaling & 1/turn (incl. OA)",
            "Lay on Hands pool, Touch range & SRD 5.2 RAW",
            "Disciple of Life adds 2 + slot_level to slot healing spells",
            "Blessed Healer self-heals 2 + slot_level when healing another living creature",
            "cover-ignoring spell applies +0 cover save bonus",
        ):
            escaped = re.escape(rule)
            self.assertRegex(res.stdout, rf"ok\s+[1-9]\d* checks\s+0 bad\s+{escaped}")

        # Also audit Preserve Life cleanly + tampered violation!
        pl_init = _start(
            self.rules,
            [
                {"id": "clr", "side": "party", "statblock": _life_cleric_sb(self.rules, 5), "position": {"x": 0, "y": 0}},
                {"id": "f5", "side": "party", "statblock": _fighter_sb(self.rules, 5), "position": {"x": 10, "y": 0}, "hp": 10},
                {"id": "foe", "side": "enemy", "monster": "ogre", "position": {"x": 20, "y": 0}},
            ],
            init_rolls=[20, 10, 1],
        )
        r_pl = step(pl_init, {"type": "class_feature", "actor_id": "clr", "feature": "preserve_life", "allocations": {"f5": 12}}, self.rules)
        pl_trace = {
            "state_shape_version": STATE_SHAPE_VERSION,
            "initial_state": pl_init,
            "actions": [{"seq": 1, "actor_id": "clr", "intent": {"type": "class_feature", "actor_id": "clr", "feature": "preserve_life", "allocations": {"f5": 12}}, "events": r_pl["events"]}],
        }
        pl_ok = _audit(self._write("pl-ok.json", pl_trace))
        self.assertEqual(pl_ok.returncode, 0, pl_ok.stdout)
        self.assertRegex(pl_ok.stdout, r"ok\s+[1-9]\d* checks\s+0 bad\s+Preserve Life Bloodied")

        # Tamper Preserve Life to exceed half HP max -> auditor MUST fail!
        pl_bad = copy.deepcopy(pl_trace)
        for ev in pl_bad["actions"][0]["events"]:
            if ev["type"] == "healing_applied":
                ev["amount"] = 20
                ev["hp_after"] = 30  # > 22 half max!
        res_bad = _audit(self._write("pl-bad.json", pl_bad))
        self.assertEqual(res_bad.returncode, 1, res_bad.stdout)
        self.assertRegex(res_bad.stdout, r"FAIL\s+\d+ checks\s+[1-9]\d* bad\s+Preserve Life Bloodied")

    def test_v3_arena_match_replays_deterministically_and_audits_cleanly(self) -> None:
        tx = run_match(
            "ruined-keep",
            party_driver=HeuristicTacticalDriver(self.rules),
            enemy_driver=HeuristicTacticalDriver(self.rules),
            rules=self.rules,
            seed=7,
            max_rounds=8,
        )
        self.assertEqual(tx["state_shape_version"], STATE_SHAPE_VERSION)
        rep = replay_match(json.loads(json.dumps(tx)), self.rules)
        self.assertTrue(rep["deterministic_match"], rep.get("diffs"))
        self.assertEqual(rep["replay_status"], "match")
        audit_res = _audit(self._write("ruined-keep-v3.json", tx))
        self.assertEqual(audit_res.returncode, 0, audit_res.stdout)


if __name__ == "__main__":
    unittest.main()
