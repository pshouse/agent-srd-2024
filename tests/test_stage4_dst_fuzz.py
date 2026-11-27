"""Stage 4 Track 1A Verification Tests: Deterministic Simulation Testing & Stateful Fuzzing.

Pure Python 3.11+ stdlib `unittest`. Covers:
1. Universal 5e 2024 (SRD 5.2) invariant checker (`check_state_invariants`) against
   valid states and intentionally corrupted states (negative HP, concentration while
   unconscious/dead/incapacitated, negative/overflow spell slots, unconscious without
   prone or with positive HP, exhaustion 6 alive, monster at 0 HP alive, RNG regression,
   input state mutation, and mutated state on invalid_intent error).
2. Procedural skirmish generator determinism & full-coverage monster selection.
3. Fast seeded DST campaign (20 seeds x 40 steps + 100 adversarial malformed intents)
   verifying 100% determinism, input immutability, zero crashes, and zero invariant
   violations.
4. `bin/srd-dst` CLI in both `markdown` and `json` output formats.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import unittest
from typing import Any

from srd2024.arena.scenarios import load_scenario
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore
from srd2024.testing import (
    build_procedural_scenario,
    capture_initial_max_slots,
    check_state_invariants,
    generate_adversarial_intent,
    generate_random_legal_intent,
    render_dst_markdown,
    run_dst_seed,
    run_dst_suite,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_SRD_DST = REPO_ROOT / "bin" / "srd-dst"


def _fresh_state(rules: RulesStore, scenario: str = "crypt-of-ghouls", seed: int = 42) -> dict[str, Any]:
    scen = load_scenario(scenario, rules)
    res = step(
        None,
        {
            "type": "start_encounter",
            "encounter_id": scen["id"],
            "seed": seed,
            "combatants": copy.deepcopy(scen["combatants"]),
        },
        rules,
    )
    assert res["status"] == "ok", f"start_encounter failed: {res.get('error')}"
    return res["state"]


class TestInvariantChecker(unittest.TestCase):
    """Verify check_state_invariants accepts clean states and catches every corruption class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_clean_start_state_has_zero_violations(self) -> None:
        state = _fresh_state(self.rules)
        max_slots = capture_initial_max_slots(state)
        self.assertEqual(check_state_invariants(state, initial_max_slots=max_slots), [])

    def test_catches_negative_hp_and_hp_above_max(self) -> None:
        state = _fresh_state(self.rules)
        bad_neg = copy.deepcopy(state)
        bad_neg["combatants"]["pc-aldric"]["hp"] = -5
        v_neg = check_state_invariants(bad_neg)
        self.assertTrue(any("hp_out_of_bounds" in v for v in v_neg), v_neg)

        bad_over = copy.deepcopy(state)
        bad_over["combatants"]["pc-aldric"]["hp"] = bad_over["combatants"]["pc-aldric"]["hp_max"] + 10
        v_over = check_state_invariants(bad_over)
        self.assertTrue(any("hp_out_of_bounds" in v for v in v_over), v_over)

    def test_catches_negative_temp_hp_and_negative_movement(self) -> None:
        state = _fresh_state(self.rules)
        bad = copy.deepcopy(state)
        bad["combatants"]["pc-aldric"]["temp_hp"] = -2
        bad["combatants"]["pc-lyra"]["movement_remaining_ft"] = -10
        violations = check_state_invariants(bad)
        self.assertTrue(any("invalid_temp_hp" in v for v in violations), violations)
        self.assertTrue(any("negative_movement" in v for v in violations), violations)

    def test_catches_concentration_while_unconscious_or_incapacitated_or_dead(self) -> None:
        state = _fresh_state(self.rules)

        # Unconscious with concentration.
        bad_unconc = copy.deepcopy(state)
        lyra = bad_unconc["combatants"]["pc-lyra"]
        lyra["hp"] = 0
        lyra["conditions"] = [
            {"condition": "unconscious", "source_id": None, "duration": "indefinite"},
            {"condition": "prone", "source_id": None, "duration": "indefinite"},
        ]
        lyra["concentration"] = {"spell": "hold-person", "targets": ["ghoul-1"]}
        v1 = check_state_invariants(bad_unconc)
        self.assertTrue(any("incapacitated_holding_concentration" in v for v in v1), v1)

        # Paralyzed/Stunned/Petrified with concentration.
        for cond in ("incapacitated", "paralyzed", "stunned", "petrified"):
            bad_c = copy.deepcopy(state)
            dorian = bad_c["combatants"]["pc-dorian"]
            dorian["conditions"] = [{"condition": cond, "source_id": None, "duration": "indefinite"}]
            dorian["concentration"] = {"spell": "bless", "targets": ["pc-aldric"]}
            vc = check_state_invariants(bad_c)
            self.assertTrue(any("incapacitated_holding_concentration" in v for v in vc), (cond, vc))

        # Dead with concentration.
        bad_dead = copy.deepcopy(state)
        dorian = bad_dead["combatants"]["pc-dorian"]
        dorian["hp"] = 0
        dorian["alive"] = False
        dorian["concentration"] = {"spell": "bless", "targets": ["pc-aldric"]}
        vd = check_state_invariants(bad_dead)
        self.assertTrue(any("dead_holding_concentration" in v for v in vd), vd)

    def test_catches_unconscious_without_prone_and_unconscious_with_positive_hp(self) -> None:
        state = _fresh_state(self.rules)
        bad = copy.deepcopy(state)
        aldric = bad["combatants"]["pc-aldric"]
        aldric["hp"] = 10
        aldric["conditions"] = [{"condition": "unconscious", "source_id": None, "duration": "indefinite"}]
        violations = check_state_invariants(bad)
        self.assertTrue(any("unconscious_without_prone" in v for v in violations), violations)
        self.assertTrue(any("unconscious_with_positive_hp" in v for v in violations), violations)

    def test_catches_exhaustion_bounds_and_exhaustion_6_alive(self) -> None:
        state = _fresh_state(self.rules)
        bad = copy.deepcopy(state)
        bad["combatants"]["pc-aldric"]["exhaustion_level"] = 6
        bad["combatants"]["pc-aldric"]["alive"] = True
        bad["combatants"]["pc-lyra"]["exhaustion_level"] = 7
        violations = check_state_invariants(bad)
        self.assertTrue(any("exhaustion_6_alive" in v for v in violations), violations)
        self.assertTrue(any("exhaustion_out_of_bounds" in v for v in violations), violations)

    def test_catches_monster_zero_hp_alive_and_dead_positive_hp(self) -> None:
        state = _fresh_state(self.rules)
        bad = copy.deepcopy(state)
        bad["combatants"]["ghoul-1"]["hp"] = 0
        bad["combatants"]["ghoul-1"]["alive"] = True
        bad["combatants"]["ghoul-2"]["hp"] = 5
        bad["combatants"]["ghoul-2"]["alive"] = False
        violations = check_state_invariants(bad)
        self.assertTrue(any("monster_zero_hp_alive" in v for v in violations), violations)
        self.assertTrue(any("dead_with_positive_hp" in v for v in violations), violations)

    def test_catches_negative_and_overflow_spell_slots(self) -> None:
        state = _fresh_state(self.rules)
        max_slots = capture_initial_max_slots(state)
        bad = copy.deepcopy(state)
        bad["combatants"]["pc-lyra"]["spellcasting"]["slots"]["1"] = -1
        bad["combatants"]["pc-dorian"]["spellcasting"]["slots"]["1"] = 99
        violations = check_state_invariants(bad, initial_max_slots=max_slots)
        self.assertTrue(any("negative_spell_slot" in v for v in violations), violations)
        self.assertTrue(any("spell_slot_exceeds_max" in v for v in violations), violations)

    def test_catches_turn_order_rng_regression_and_mutated_state_on_error(self) -> None:
        prev = _fresh_state(self.rules)
        prev["rng"]["draw_index"] = 20

        bad = copy.deepcopy(prev)
        bad["round"] = 0
        bad["active_index"] = 999
        bad["turn_order"].append(bad["turn_order"][0])
        bad["turn_order"].append("ghost-id")
        bad["rng"]["draw_index"] = 10

        fake_error_result = {"status": "invalid_intent", "error": "bad", "state": bad}
        violations = check_state_invariants(
            bad,
            prev_state=prev,
            intent={"type": "dodge"},
            result=fake_error_result,
            input_state_snapshot=prev,
            input_state_after=bad,
        )
        self.assertTrue(any("input_state_mutated" in v for v in violations), violations)
        self.assertTrue(any("invalid_intent_mutated_state" in v for v in violations), violations)
        self.assertTrue(any("invalid_round" in v for v in violations), violations)
        self.assertTrue(any("duplicate_turn_order" in v for v in violations), violations)
        self.assertTrue(any("missing_turn_order_combatant" in v for v in violations), violations)
        self.assertTrue(any("active_index_out_of_range" in v for v in violations), violations)
        self.assertTrue(any("rng_draw_index_regressed" in v for v in violations), violations)


class TestDSTGenerators(unittest.TestCase):
    """Verify procedural skirmish generation and legal/adversarial intent generators."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_procedural_scenario_is_deterministic_and_valid(self) -> None:
        s1 = build_procedural_scenario(101, self.rules)
        s2 = build_procedural_scenario(101, self.rules)
        self.assertEqual(s1, s2)
        self.assertGreaterEqual(len(s1["party_ids"]), 2)
        self.assertGreaterEqual(len(s1["enemy_ids"]), 2)

        for entry in s1["combatants"]:
            if "monster" in entry:
                m_rec = self.rules.get_monster(entry["monster"])
                self.assertIsNotNone(m_rec)
                self.assertEqual(m_rec["coverage"], "full")

        res = step(
            None,
            {"type": "start_encounter", "encounter_id": s1["id"], "seed": 101, "combatants": s1["combatants"]},
            self.rules,
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(
            check_state_invariants(res["state"], initial_max_slots=capture_initial_max_slots(res["state"])),
            [],
        )

    def test_generators_cover_legal_and_adversarial_intents(self) -> None:
        state = _fresh_state(self.rules, "wyrmling-lair", seed=7)
        rng = random.Random(7)
        seen_legal_types: set[str] = set()
        for _ in range(40):
            intent = generate_random_legal_intent(state, self.rules, rng)
            self.assertIsInstance(intent, dict)
            seen_legal_types.add(str(intent.get("type")))
        self.assertTrue({"move", "attack", "dodge", "dash", "disengage", "end_turn"} & seen_legal_types)

        adv_samples = [generate_adversarial_intent(state, self.rules, rng) for _ in range(40)]
        self.assertTrue(any(not isinstance(x, dict) for x in adv_samples))
        self.assertTrue(any(isinstance(x, dict) and x.get("type") == "move" for x in adv_samples))


class TestDSTCampaign(unittest.TestCase):
    """Run seeded DST campaign + 100 adversarial intents verifying determinism, immutability & zero failures."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_seeded_dst_campaign_20_seeds_40_steps(self) -> None:
        report = run_dst_suite(
            seeds=20,
            scenarios=None,
            max_steps_per_seed=40,
            adversarial_rate=0.25,
            rules=self.rules,
        )
        summary = report["summary"]
        self.assertEqual(summary["seeds_run"], 20)
        self.assertTrue(summary["deterministic_all"], report["failures"][:5])
        self.assertEqual(summary["exception_failures"], 0, report["failures"][:5])
        self.assertEqual(summary["invariant_failures"], 0, report["failures"][:5])
        self.assertTrue(report["passed"], report["failures"][:5])
        self.assertGreater(summary["total_steps"], 200)
        self.assertGreater(summary["adversarial_steps"], 20)

    def test_100_adversarial_malformed_intents_never_crash_or_corrupt_state(self) -> None:
        state = _fresh_state(self.rules, "wyrmling-lair", seed=99)
        max_slots = capture_initial_max_slots(state)
        rng = random.Random(9999)

        for idx in range(100):
            intent = generate_adversarial_intent(state, self.rules, rng)
            before = copy.deepcopy(state)
            try:
                res = step(state, intent, self.rules)
            except Exception as exc:
                self.fail(f"Adversarial intent #{idx} crashed step(): {intent!r} -> {type(exc).__name__}: {exc}")

            self.assertEqual(state, before, f"Caller input state mutated on adversarial intent #{idx}: {intent!r}")
            violations = check_state_invariants(
                res.get("state"),
                prev_state=before,
                intent=intent if isinstance(intent, dict) else None,
                result=res,
                initial_max_slots=max_slots,
                input_state_snapshot=before,
                input_state_after=state,
            )
            self.assertEqual(violations, [], f"Invariant violation on adversarial intent #{idx} ({intent!r}): {violations}")
            if res.get("status") == "ok":
                state = res["state"]

    def test_single_seed_double_run_determinism(self) -> None:
        r1 = run_dst_seed(17, scenario="crypt-of-ghouls", max_steps_per_seed=30, adversarial_rate=0.25, rules=self.rules)
        r2 = run_dst_seed(17, scenario="crypt-of-ghouls", max_steps_per_seed=30, adversarial_rate=0.25, rules=self.rules)
        self.assertTrue(r1["passed"], r1["failures"])
        self.assertTrue(r1["deterministic"])
        self.assertEqual(r1["state_digest"], r2["state_digest"])
        self.assertEqual(r1["events_digest"], r2["events_digest"])


class TestSrdDstCLI(unittest.TestCase):
    """Verify `bin/srd-dst` CLI in both markdown and json formats."""

    def test_cli_markdown_and_json_formats(self) -> None:
        self.assertTrue(BIN_SRD_DST.is_file(), f"Missing CLI wrapper: {BIN_SRD_DST}")
        self.assertTrue(os.access(BIN_SRD_DST, os.X_OK), f"CLI wrapper not executable: {BIN_SRD_DST}")

        md_proc = subprocess.run(
            [str(BIN_SRD_DST), "--seeds", "5", "--max-steps", "20", "--format", "markdown"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(md_proc.returncode, 0, f"stderr={md_proc.stderr}\nstdout={md_proc.stdout}")
        self.assertIn("5e 2024 (SRD 5.2)", md_proc.stdout)
        self.assertIn("PASSED", md_proc.stdout)
        self.assertNotIn("D&D", md_proc.stdout)

        json_proc = subprocess.run(
            [str(BIN_SRD_DST), "--seed", "2", "--max-steps", "20", "--format", "json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(json_proc.returncode, 0, f"stderr={json_proc.stderr}\nstdout={json_proc.stdout}")
        payload = json.loads(json_proc.stdout)
        self.assertTrue(payload["passed"], payload.get("failures"))
        self.assertEqual(payload["summary"]["seeds_run"], 1)
        self.assertEqual(payload["summary"]["exception_failures"], 0)
        self.assertEqual(payload["summary"]["invariant_failures"], 0)
        self.assertTrue(payload["summary"]["deterministic_all"])

    def test_render_markdown_includes_repro_command_on_failure(self) -> None:
        fake_report = {
            "dataset_id": "srd-2024-5.2",
            "status": "failed",
            "passed": False,
            "config": {"max_steps_per_seed": 40, "adversarial_rate": 0.25},
            "summary": {
                "seeds_run": 1,
                "seeds_passed": 0,
                "seeds_failed": 1,
                "total_steps": 12,
                "ok_steps": 9,
                "invalid_steps": 2,
                "adversarial_steps": 3,
                "invariant_checks": 12,
                "exception_failures": 1,
                "invariant_failures": 0,
                "determinism_failures": 0,
                "total_failures": 1,
                "deterministic_all": True,
            },
            "failures": [
                {
                    "kind": "exception",
                    "seed": 7,
                    "scenario": "goblin-ambush",
                    "step_index": 12,
                    "intent": {"type": "move", "to": "bad"},
                    "error": "TypeError: bad coords",
                    "repro_cmd": "bin/srd-dst --seed 7 --max-steps 40 --adversarial-rate 0.25",
                }
            ],
        }
        md = render_dst_markdown(fake_report)
        self.assertIn("FAILED", md)
        self.assertIn("bin/srd-dst --seed 7", md)
        self.assertNotIn("D&D", md)


if __name__ == "__main__":
    unittest.main()
