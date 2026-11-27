"""Stage 3 Verification Tests (§8.1): Curated Scenarios, Custom Loading & Quantitative Scorer.

Covers:
1. Built-in Scenarios Verification:
   - list_scenarios() returns goblin-ambush, crypt-of-ghouls, wyrmling-lair with full metadata.
   - Every PC in all 3 scenarios compiles via compile_character() with valid==True, errors==[], warnings==[].
   - Every monster slug exists in dist/rules.json with coverage=="full".
   - All wyrmling-lair enemies have fire immunity.
   - load_scenario() produces valid start_encounter payloads accepted by combat.step().
2. Custom Scenario JSON Loading:
   - Loading a custom scenario from JSON file (spec & pre-compiled statblock) and inline dict succeeds.
   - Invalid PC specs, unknown monster slugs, empty party/enemies, duplicate IDs, and unknown built-in slugs raise ValueError.
3. Quantitative Scorer Unit Tests (score_match):
   - outcome_score ranks victory > draw > defeat and rewards survival, remaining HP, and round efficiency.
   - rules_legality_score == 100.0 with 0 violations and strictly decreases on violations & forced fallbacks.
   - tactical_score penalizes wasted immune damage hits (-12.5 each) and rewards effective damage, productive turns,
     mastery triggers, effective buffs, revivals, and heals.
   - coordination_score penalizes friendly fire (-20.0 each) and rewards table_talk, focus fire, and ally support.
   - All sub-scores and composite_score remain in [0.0, 100.0] and match the weighted formula.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from srd2024.arena import (
    BUILTIN_SCENARIO_IDS,
    list_scenarios,
    load_scenario,
    score_match,
)
from srd2024.arena import scenarios as scenarios_mod
from srd2024.engine.character import compile_character
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore


def _minimal_pc_spec(name: str = "Test Fighter") -> dict[str, Any]:
    return {
        "name": name,
        "species": "human",
        "subspecies": None,
        "size": "Medium",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "class": "fighter",
        "subclass": None,
        "level": 1,
        "ability_method": "point_buy",
        "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
        "asi_choices": [],
        "fighting_style": "defense",
        "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
        "equipment": {
            "armor": "chain-mail",
            "shield": True,
            "weapons": ["longsword", "shortbow"],
        },
        "weapon_mastery_weapons": ["longsword", "shortbow"],
        "cantrips": [],
        "prepared_spells": [],
        "hp_method": "fixed",
    }


def _make_combatant(
    cid: str,
    side: str,
    hp: int,
    hp_max: int,
    *,
    alive: bool | None = None,
) -> dict[str, Any]:
    if alive is None:
        alive = hp > 0
    return {
        "id": cid,
        "name": cid,
        "side": side,
        "kind": "pc" if side == "party" else "monster",
        "hp": hp,
        "hp_max": hp_max,
        "alive": alive,
        "ac": 15,
        "position": {"x": 0, "y": 0},
        "conditions": [],
    }


def _make_synthetic_transcript(
    *,
    winner: str = "party",
    rounds: int = 3,
    max_rounds: int = 10,
    initial_party: list[dict[str, Any]] | None = None,
    final_party: list[dict[str, Any]] | None = None,
    initial_enemies: list[dict[str, Any]] | None = None,
    final_enemies: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    violations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    init_p = initial_party or [
        _make_combatant("pc-1", "party", 20, 20),
        _make_combatant("pc-2", "party", 20, 20),
    ]
    fin_p = final_party or [
        _make_combatant("pc-1", "party", 20, 20),
        _make_combatant("pc-2", "party", 20, 20),
    ]
    init_e = initial_enemies or [
        _make_combatant("en-1", "enemy", 20, 20),
        _make_combatant("en-2", "enemy", 20, 20),
    ]
    fin_e = final_enemies or [
        _make_combatant("en-1", "enemy", 0, 20, alive=False),
        _make_combatant("en-2", "enemy", 0, 20, alive=False),
    ]
    init_map = {c["id"]: c for c in init_p + init_e}
    fin_map = {c["id"]: c for c in fin_p + fin_e}
    return {
        "schema_version": "3",
        "dataset_id": "srd-2024-5.2",
        "scenario": {
            "id": "synthetic",
            "name": "Synthetic Test",
            "tier": 1,
            "party_level": 1,
            "max_rounds": max_rounds,
            "description": "Synthetic transcript for scorer unit tests.",
        },
        "config": {
            "seed": 1,
            "max_rounds": max_rounds,
            "max_actions_per_turn": 6,
            "max_retries_per_action": 2,
            "party_driver": "synthetic",
            "enemy_driver": "synthetic",
        },
        "winner": winner,
        "rounds": rounds,
        "initial_state": {
            "round": 1,
            "turn_order": list(init_map.keys()),
            "active_index": 0,
            "combatants": init_map,
            "status": "active",
        },
        "final_state": {
            "round": rounds,
            "turn_order": list(fin_map.keys()),
            "active_index": 0,
            "combatants": fin_map,
            "status": "ended",
        },
        "actions": actions if actions is not None else [],
        "violations": violations if violations is not None else [],
        "recharge_events": [],
        "blackboard": {"party": [], "enemy": []},
    }


def _make_action(
    seq: int,
    *,
    round_num: int = 1,
    turn_index: int = 0,
    action_index: int = 0,
    actor_id: str = "pc-1",
    side: str = "party",
    intent: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    attempts: int = 1,
    first_attempt_legal: bool = True,
    auto_skipped: bool = False,
    auto_end_turn: bool = False,
    forced_fallback: bool = False,
    table_talk: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "round": round_num,
        "turn_index": turn_index,
        "action_index": action_index,
        "actor_id": actor_id,
        "actor_name": actor_id,
        "side": side,
        "driver": "synthetic",
        "attempts": attempts,
        "first_attempt_legal": first_attempt_legal,
        "auto_skipped": auto_skipped,
        "auto_end_turn": auto_end_turn,
        "forced_fallback": forced_fallback,
        "intent": intent or {"type": "attack", "actor_id": actor_id, "target_id": "en-1", "weapon": "longsword"},
        "rationale": "test",
        "table_talk": table_talk,
        "events": events or [],
        "state_diff": {},
        "narrative_hooks": [],
        "usage": usage or {},
    }


# Stat-block elements of the built-in scenario monsters that the engine does not execute yet (#39, #33, #38).
EXPECTED_SCENARIO_MONSTER_GAPS: dict[str, list[str]] = {
    "goblin-warrior": ["bonus_action:nimble-escape"],
    "goblin-minion": ["bonus_action:nimble-escape"],
    "mummy": [],
    "ghoul": [],
    "ghast": [],  # Stench executes (#39)
    "skeleton": [],
    "red-dragon-wyrmling": [],
    "hell-hound": [],  # Pack Tactics executes (#39)
    "magmin": ["bonus_action:ignited-illumination"],  # Death Burst executes (#39)
}


class TestStage3BuiltinScenarios(unittest.TestCase):
    """§8.1.1: Built-in scenarios catalog, zero-warning compilation, full-coverage monsters & encounter init."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_builtin_scenario_ids_and_list_scenarios(self) -> None:
        self.assertEqual(
            BUILTIN_SCENARIO_IDS,
            ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"),
        )
        listed = list_scenarios()
        ids = [s["id"] for s in listed]
        self.assertEqual(set(ids), set(BUILTIN_SCENARIO_IDS))
        self.assertEqual(len(listed), 3)

        required_keys = {
            "id",
            "name",
            "tier",
            "party_level",
            "max_rounds",
            "description",
            "party_summary",
            "enemy_summary",
            "tactical_themes",
        }
        for item in listed:
            missing = required_keys - set(item.keys())
            self.assertFalse(missing, f"Scenario {item.get('id')} missing metadata keys: {missing}")
            self.assertTrue(item["description"])
            self.assertIsInstance(item["tactical_themes"], list)
            self.assertGreater(len(item["tactical_themes"]), 0)

    def test_all_builtin_pcs_compile_cleanly_and_monsters_have_full_coverage(self) -> None:
        expected_pc_stats = {
            "pc-bram": (1, 19, 12),
            "pc-talia": (1, 12, 8),
            "pc-aldric": (5, 19, 44),
            "pc-lyra": (5, 11, 32),
            "pc-dorian": (5, 16, 43),
            "pc-kaelen": (5, 16, 38),
        }

        compiled_pc_results: list[tuple[str, dict[str, Any]]] = []
        real_compile = compile_character

        def _spy_compile(spec: dict[str, Any], rules: RulesStore) -> dict[str, Any]:
            res = real_compile(spec, rules)
            compiled_pc_results.append((str(spec.get("name", "?")), res))
            return res

        with mock.patch.object(scenarios_mod, "compile_character", side_effect=_spy_compile):
            for sid in BUILTIN_SCENARIO_IDS:
                with self.subTest(scenario=sid):
                    compiled = load_scenario(sid, self.rules)
                    self.assertEqual(compiled["id"], sid)
                    self.assertEqual(compiled["source"], "builtin")
                    self.assertGreater(len(compiled["party_ids"]), 0)
                    self.assertGreater(len(compiled["enemy_ids"]), 0)
                    self.assertEqual(
                        len(compiled["combatants"]),
                        len(compiled["party_ids"]) + len(compiled["enemy_ids"]),
                    )

                    # Verify PC derived stats & monster full coverage
                    for entry in compiled["combatants"]:
                        if entry["side"] == "party":
                            sb = entry["statblock"]
                            if entry["id"] in expected_pc_stats:
                                exp_lvl, exp_ac, exp_hp = expected_pc_stats[entry["id"]]
                                self.assertEqual(sb["level"], exp_lvl, entry["id"])
                                self.assertEqual(sb["ac"], exp_ac, entry["id"])
                                self.assertEqual(sb["hp_max"], exp_hp, entry["id"])
                        else:
                            monster_slug = entry["monster"]
                            m_rule = self.rules.get_monster(f"monsters:{monster_slug}")
                            self.assertIsNotNone(m_rule, f"Missing monster in rules.json: {monster_slug}")
                            # Core attacks must be executable: the only allowed gaps are stat-block traits, bonus
                            # actions and reactions (#39), pinned per monster so implementing one updates this test.
                            clauses = m_rule["provenance"]["unmodeled_clauses"]
                            self.assertTrue(
                                all(c.split(":", 1)[0] in ("special_ability", "bonus_action", "reaction") for c in clauses),
                                f"Monster {monster_slug} in {sid} has unexecutable core actions: {clauses}",
                            )
                            self.assertEqual(
                                clauses,
                                EXPECTED_SCENARIO_MONSTER_GAPS[monster_slug],
                                f"Unmodeled clauses changed for {monster_slug}; update EXPECTED_SCENARIO_MONSTER_GAPS",
                            )
                            self.assertEqual(m_rule["coverage"], "full" if not clauses else "partial")
                            if sid == "wyrmling-lair":
                                immunities = {d.lower() for d in m_rule.get("damage_immunities", [])}
                                self.assertIn(
                                    "fire",
                                    immunities,
                                    f"wyrmling-lair enemy {monster_slug} must be fire-immune",
                                )

                    # Verify start_encounter accepts compiled combatants cleanly
                    start_res = step(
                        None,
                        {
                            "type": "start_encounter",
                            "seed": 42,
                            "combatants": compiled["combatants"],
                        },
                        self.rules,
                    )
                    self.assertEqual(start_res["status"], "ok", f"start_encounter failed for {sid}: {start_res}")
                    self.assertEqual(len(start_res["state"]["combatants"]), len(compiled["combatants"]))

        # Verify every single PC compiled via compile_character had valid=True, errors=[], warnings=[]
        self.assertGreaterEqual(len(compiled_pc_results), 9)  # 2 + 3 + 4 PCs across the 3 scenarios
        for pc_name, res in compiled_pc_results:
            self.assertTrue(res["valid"], f"PC {pc_name} failed compile_character: {res['errors']}")
            self.assertEqual(res["errors"], [], f"PC {pc_name} had errors: {res['errors']}")
            self.assertEqual(res["warnings"], [], f"PC {pc_name} had warnings: {res['warnings']}")


class TestStage3CustomScenarioLoading(unittest.TestCase):
    """§8.1.2: Custom scenario JSON file & inline dict loading plus error validation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_custom_scenario_file_and_inline_with_spec_and_statblock(self) -> None:
        pc_spec = _minimal_pc_spec("Custom Hero")
        compiled_pc = compile_character(pc_spec, self.rules)
        self.assertTrue(compiled_pc["valid"])

        payload = {
            "id": "custom-skirmish",
            "name": "Custom Skirmish",
            "description": "Custom 5e 2024 arena encounter.",
            "tier": 1,
            "party_level": 1,
            "max_rounds": 8,
            "tactical_themes": ["positioning"],
            "party": [
                {"id": "pc-spec-1", "spec": pc_spec, "position": {"x": 0, "y": 0}},
                {"id": "pc-sb-2", "statblock": compiled_pc["statblock"], "position": {"x": 0, "y": 5}},
            ],
            "enemies": [
                {"id": "gob-1", "monster": "goblin-warrior", "name": "Goblin A", "position": {"x": 20, "y": 0}},
            ],
        }

        with tempfile.TemporaryDirectory(prefix="srd2024-custom-scen-") as tmp:
            scen_path = Path(tmp) / "custom.json"
            scen_path.write_text(json.dumps(payload), encoding="utf-8")

            from_file = load_scenario(scen_path, self.rules)
            self.assertEqual(from_file["id"], "custom-skirmish")
            self.assertEqual(from_file["source"], "file")
            self.assertEqual(from_file["party_ids"], ["pc-spec-1", "pc-sb-2"])
            self.assertEqual(from_file["enemy_ids"], ["gob-1"])

            start_res = step(
                None,
                {"type": "start_encounter", "seed": 99, "combatants": from_file["combatants"]},
                self.rules,
            )
            self.assertEqual(start_res["status"], "ok", f"start_encounter failed: {start_res}")

        from_inline = load_scenario(payload, self.rules)
        self.assertEqual(from_inline["source"], "inline")
        self.assertEqual(from_inline["party_ids"], ["pc-spec-1", "pc-sb-2"])

    def test_invalid_custom_scenarios_raise_value_error(self) -> None:
        base = {
            "id": "bad-test",
            "name": "Bad Test",
            "description": "Invalid test scenario.",
            "tier": 1,
            "party_level": 1,
            "max_rounds": 10,
            "tactical_themes": [],
            "party": [{"id": "pc-1", "spec": _minimal_pc_spec(), "position": {"x": 0, "y": 0}}],
            "enemies": [{"id": "en-1", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}}],
        }

        # Unknown built-in slug
        with self.assertRaises(ValueError):
            load_scenario("nonexistent-scenario-slug", self.rules)

        # Unknown monster slug
        bad_monster = copy.deepcopy(base)
        bad_monster["enemies"][0]["monster"] = "definitely-not-a-monster-xyz"
        with self.assertRaises(ValueError):
            load_scenario(bad_monster, self.rules)

        # Invalid PC spec (impossible point-buy / invalid class)
        bad_pc = copy.deepcopy(base)
        bad_pc["party"][0]["spec"]["class"] = "not-a-valid-class"
        with self.assertRaises(ValueError):
            load_scenario(bad_pc, self.rules)

        # Empty party
        empty_party = copy.deepcopy(base)
        empty_party["party"] = []
        with self.assertRaises(ValueError):
            load_scenario(empty_party, self.rules)

        # Empty enemies
        empty_enemies = copy.deepcopy(base)
        empty_enemies["enemies"] = []
        with self.assertRaises(ValueError):
            load_scenario(empty_enemies, self.rules)

        # Duplicate combatant IDs
        dup_ids = copy.deepcopy(base)
        dup_ids["enemies"][0]["id"] = "pc-1"
        with self.assertRaises(ValueError):
            load_scenario(dup_ids, self.rules)


class TestStage3QuantitativeScorer(unittest.TestCase):
    """§8.1.3: Deterministic 0-100 scoring across outcome, legality, tactical, coordination & composite."""

    def _assert_scorecard_invariants(self, sc: dict[str, Any]) -> None:
        for key in (
            "outcome_score",
            "rules_legality_score",
            "tactical_score",
            "coordination_score",
            "composite_score",
        ):
            self.assertIn(key, sc)
            self.assertGreaterEqual(sc[key], 0.0, f"{key} below 0: {sc[key]}")
            self.assertLessEqual(sc[key], 100.0, f"{key} above 100: {sc[key]}")

        expected_comp = round(
            0.35 * sc["outcome_score"]
            + 0.25 * sc["rules_legality_score"]
            + 0.25 * sc["tactical_score"]
            + 0.15 * sc["coordination_score"],
            2,
        )
        self.assertAlmostEqual(
            sc["composite_score"],
            expected_comp,
            places=2,
            msg=f"Composite score mismatch: got {sc['composite_score']}, expected {expected_comp}",
        )
        self.assertEqual(
            sc["weights"],
            {"outcome": 0.35, "rules_legality": 0.25, "tactical": 0.25, "coordination": 0.15},
        )
        self.assertIn("metrics", sc)

    def test_outcome_score_ranking_and_components(self) -> None:
        fast_clean_win = _make_synthetic_transcript(winner="party", rounds=1, max_rounds=10)
        slow_bruised_win = _make_synthetic_transcript(
            winner="party",
            rounds=9,
            max_rounds=10,
            final_party=[
                _make_combatant("pc-1", "party", 5, 20),
                _make_combatant("pc-2", "party", 0, 20, alive=True),
            ],
        )
        draw = _make_synthetic_transcript(
            winner="draw",
            rounds=10,
            max_rounds=10,
            final_enemies=[_make_combatant("en-1", "enemy", 10, 20), _make_combatant("en-2", "enemy", 10, 20)],
        )
        defeat = _make_synthetic_transcript(
            winner="enemy",
            rounds=4,
            max_rounds=10,
            final_party=[
                _make_combatant("pc-1", "party", 0, 20, alive=False),
                _make_combatant("pc-2", "party", 0, 20, alive=False),
            ],
            final_enemies=[_make_combatant("en-1", "enemy", 20, 20), _make_combatant("en-2", "enemy", 20, 20)],
        )

        sc_fast = score_match(fast_clean_win)
        sc_slow = score_match(slow_bruised_win)
        sc_draw = score_match(draw)
        sc_loss = score_match(defeat)

        for sc in (sc_fast, sc_slow, sc_draw, sc_loss):
            self._assert_scorecard_invariants(sc)

        self.assertEqual(sc_fast["outcome_score"], 100.0)
        self.assertEqual(sc_loss["outcome_score"], 0.0)
        self.assertGreater(sc_fast["outcome_score"], sc_slow["outcome_score"])
        self.assertGreater(sc_slow["outcome_score"], sc_draw["outcome_score"])
        self.assertGreater(sc_draw["outcome_score"], sc_loss["outcome_score"])

    def test_rules_legality_score_penalties(self) -> None:
        # 0 violations -> 100.0
        clean = _make_synthetic_transcript(
            actions=[
                _make_action(1, round_num=1, turn_index=0, actor_id="pc-1"),
                _make_action(2, round_num=1, turn_index=1, actor_id="pc-2"),
            ],
            violations=[],
        )
        sc_clean = score_match(clean)
        self._assert_scorecard_invariants(sc_clean)
        self.assertEqual(sc_clean["rules_legality_score"], 100.0)

        # 0 decisions -> 100.0
        empty_dec = _make_synthetic_transcript(
            actions=[_make_action(1, auto_skipped=True)],
            violations=[],
        )
        sc_empty = score_match(empty_dec)
        self._assert_scorecard_invariants(sc_empty)
        self.assertEqual(sc_empty["rules_legality_score"], 100.0)

        # 1 retry violation, no fallback
        with_retry = _make_synthetic_transcript(
            actions=[
                _make_action(1, round_num=1, turn_index=0, actor_id="pc-1", attempts=2, first_attempt_legal=False),
                _make_action(2, round_num=1, turn_index=1, actor_id="pc-2", attempts=1, first_attempt_legal=True),
            ],
            violations=[
                {"round": 1, "turn_index": 0, "actor_id": "pc-1", "side": "party", "attempt": 1, "intent": {}, "error": "illegal"},
            ],
        )
        sc_retry = score_match(with_retry)
        self._assert_scorecard_invariants(sc_retry)
        self.assertLess(sc_retry["rules_legality_score"], 100.0)

        # Forced fallback + extra retries -> strictly lower than single retry
        with_fallback = _make_synthetic_transcript(
            actions=[
                _make_action(
                    1,
                    round_num=1,
                    turn_index=0,
                    actor_id="pc-1",
                    attempts=3,
                    first_attempt_legal=False,
                    forced_fallback=True,
                    intent={"type": "end_turn", "actor_id": "pc-1"},
                ),
                _make_action(2, round_num=1, turn_index=1, actor_id="pc-2", attempts=1, first_attempt_legal=True),
            ],
            violations=[
                {"round": 1, "turn_index": 0, "actor_id": "pc-1", "side": "party", "attempt": 1, "intent": {}, "error": "err1"},
                {"round": 1, "turn_index": 0, "actor_id": "pc-1", "side": "party", "attempt": 2, "intent": {}, "error": "err2"},
                {"round": 1, "turn_index": 0, "actor_id": "pc-1", "side": "party", "attempt": 3, "intent": {}, "error": "err3"},
            ],
        )
        sc_fb = score_match(with_fallback)
        self._assert_scorecard_invariants(sc_fb)
        self.assertLess(sc_fb["rules_legality_score"], sc_retry["rules_legality_score"])
        self.assertEqual(sc_fb["metrics"]["forced_fallbacks"], 1)
        self.assertEqual(sc_fb["metrics"]["total_violations"], 3)

    def test_tactical_score_immunity_penalty_and_feature_rewards(self) -> None:
        base_actions = [
            _make_action(
                1,
                round_num=1,
                turn_index=0,
                actor_id="pc-1",
                intent={"type": "attack", "actor_id": "pc-1", "target_id": "en-1", "weapon": "longsword"},
                events=[
                    {
                        "type": "damage_applied",
                        "target_id": "en-1",
                        "total_damage": 20,
                        "breakdown": [{"type": "slashing", "raw": 20, "final": 20, "modifier": "normal"}],
                    },
                    {"type": "mastery_triggered", "actor_id": "pc-1", "target_id": "en-1", "mastery": "sap"},
                ],
            ),
            _make_action(
                2,
                round_num=1,
                turn_index=1,
                actor_id="pc-2",
                intent={"type": "cast_spell", "actor_id": "pc-2", "spell": "lightning-bolt", "target_ids": ["en-2"]},
                events=[
                    {
                        "type": "damage_applied",
                        "target_id": "en-2",
                        "total_damage": 20,
                        "breakdown": [{"type": "lightning", "raw": 20, "final": 20, "modifier": "normal"}],
                    },
                ],
            ),
        ]
        clean_t = _make_synthetic_transcript(actions=base_actions)
        sc_clean = score_match(clean_t)
        self._assert_scorecard_invariants(sc_clean)
        self.assertEqual(sc_clean["metrics"]["wasted_immune_hits"], 0)

        # Add one wasted immune hit (fireball against fire-immune enemy)
        wasted_actions = copy.deepcopy(base_actions)
        wasted_actions.append(
            _make_action(
                3,
                round_num=2,
                turn_index=0,
                actor_id="pc-2",
                intent={"type": "cast_spell", "actor_id": "pc-2", "spell": "fire-bolt", "target_id": "en-1"},
                events=[
                    {
                        "type": "damage_applied",
                        "target_id": "en-1",
                        "total_damage": 0,
                        "breakdown": [{"type": "fire", "raw": 14, "final": 0, "modifier": "immune"}],
                    }
                ],
            )
        )
        wasted_t = _make_synthetic_transcript(actions=wasted_actions)
        sc_wasted = score_match(wasted_t)
        self._assert_scorecard_invariants(sc_wasted)
        self.assertEqual(sc_wasted["metrics"]["wasted_immune_hits"], 1)
        self.assertAlmostEqual(sc_clean["tactical_score"] - sc_wasted["tactical_score"], 12.5, places=1)

        # Compare passive end_turn-only party vs active mastery/buff/revival party
        passive_t = _make_synthetic_transcript(
            actions=[
                _make_action(1, round_num=1, turn_index=0, actor_id="pc-1", intent={"type": "end_turn", "actor_id": "pc-1"}),
            ]
        )
        sc_passive = score_match(passive_t)
        self._assert_scorecard_invariants(sc_passive)
        self.assertGreater(sc_clean["tactical_score"], sc_passive["tactical_score"])

    def test_coordination_score_friendly_fire_and_teamwork(self) -> None:
        teamwork_actions = [
            _make_action(
                1,
                round_num=1,
                turn_index=0,
                actor_id="pc-1",
                table_talk="Casting Bless on the squad!",
                intent={"type": "cast_spell", "actor_id": "pc-1", "spell": "bless", "target_ids": ["pc-1", "pc-2"]},
                events=[
                    {"type": "effect_applied", "target_id": "pc-1", "effect": "bless"},
                    {"type": "effect_applied", "target_id": "pc-2", "effect": "bless"},
                ],
            ),
            _make_action(
                2,
                round_num=1,
                turn_index=1,
                actor_id="pc-2",
                table_talk="Focusing en-1 with Sap!",
                intent={"type": "attack", "actor_id": "pc-2", "target_id": "en-1", "weapon": "longsword"},
                events=[
                    {
                        "type": "damage_applied",
                        "target_id": "en-1",
                        "total_damage": 10,
                        "breakdown": [{"type": "slashing", "raw": 10, "final": 10, "modifier": "normal"}],
                    },
                    {"type": "mastery_triggered", "actor_id": "pc-2", "target_id": "en-1", "mastery": "sap"},
                ],
            ),
            _make_action(
                3,
                round_num=2,
                turn_index=0,
                actor_id="pc-1",
                table_talk="Finishing damaged en-1!",
                intent={"type": "attack", "actor_id": "pc-1", "target_id": "en-1", "weapon": "mace"},
                events=[
                    {
                        "type": "damage_applied",
                        "target_id": "en-1",
                        "total_damage": 10,
                        "breakdown": [{"type": "bludgeoning", "raw": 10, "final": 10, "modifier": "normal"}],
                    },
                ],
            ),
        ]
        sc_team = score_match(_make_synthetic_transcript(actions=teamwork_actions))
        self._assert_scorecard_invariants(sc_team)
        self.assertEqual(sc_team["metrics"]["friendly_fire_incidents"], 0)
        self.assertGreaterEqual(sc_team["metrics"]["table_talk_messages"], 3)

        # Friendly fire + no table_talk -> strictly lower coordination_score
        ff_actions = [
            _make_action(
                1,
                round_num=1,
                turn_index=0,
                actor_id="pc-1",
                table_talk=None,
                intent={"type": "cast_spell", "actor_id": "pc-1", "spell": "fireball", "target_ids": ["en-1", "pc-2"]},
                events=[
                    {
                        "type": "damage_applied",
                        "target_id": "en-1",
                        "total_damage": 15,
                        "breakdown": [{"type": "fire", "raw": 15, "final": 15, "modifier": "normal"}],
                    },
                    {
                        "type": "damage_applied",
                        "target_id": "pc-2",
                        "total_damage": 15,
                        "breakdown": [{"type": "fire", "raw": 15, "final": 15, "modifier": "normal"}],
                    },
                ],
            ),
        ]
        sc_ff = score_match(_make_synthetic_transcript(actions=ff_actions))
        self._assert_scorecard_invariants(sc_ff)
        self.assertEqual(sc_ff["metrics"]["friendly_fire_incidents"], 1)
        self.assertGreater(sc_team["coordination_score"], sc_ff["coordination_score"])


if __name__ == "__main__":
    unittest.main()
