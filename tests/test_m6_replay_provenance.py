"""Milestone 6.1 (#66): Replay provenance, state-schema drift separation, and key-set guard."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest

from srd2024 import __version__ as PKG_VERSION
from srd2024.arena import (
    ENGINE_VERSION,
    STATE_SHAPE_VERSION,
    HeuristicTacticalDriver,
    compute_rules_digest,
    compute_transcript_digest,
    load_scenario,
    render_replay_markdown,
    replay_match,
    run_interactive_session,
    run_match,
)
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

# Snapshots for STATE_SHAPE_VERSION == 1.
# If EncounterState or combatant serialization fields change, bump STATE_SHAPE_VERSION
# in src/srd2024/arena/runner.py and update these snapshots deliberately.
EXPECTED_STATE_SHAPE_VERSION = 1

EXPECTED_ENCOUNTER_STATE_KEYS = [
    "active_index",
    "combatants",
    "encounter_id",
    "pending_reroll",
    "rng",
    "round",
    "schema_version",
    "status",
    "turn_order",
]

EXPECTED_BASE_COMBATANT_KEYS = [
    "abilities",
    "ability_mods",
    "ac",
    "active_effects",
    "alive",
    "concentration",
    "condition_immunities",
    "conditions",
    "damage_immunities",
    "damage_resistances",
    "damage_vulnerabilities",
    "death_saves",
    "exhaustion_level",
    "extra_attacks",
    "feats",
    "heroic_inspiration",
    "hp",
    "hp_max",
    "id",
    "initiative",
    "initiative_bonus",
    "kind",
    "level",
    "monster_actions",
    "movement_remaining_ft",
    "name",
    "position",
    "proficiency_bonus",
    "saves",
    "side",
    "size",
    "source_id",
    "speed_ft",
    "spellcasting",
    "temp_hp",
    "turn_resources",
    "weapons",
    "wearing_armor",
]

KNOWN_OPTIONAL_COMBATANT_KEYS = frozenset(
    {
        "aura_immune_to",
        "legendary_resistance_uses",
        "reaction_policy",
        "traits_ir",
    }
)


class TestReplayProvenanceAndDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.crypt_tx = run_match(
            "crypt-of-ghouls",
            party_driver=HeuristicTacticalDriver(cls.rules),
            enemy_driver=HeuristicTacticalDriver(cls.rules),
            rules=cls.rules,
            seed=7,
            max_rounds=4,
        )
        cls.goblin_tx = run_match(
            "goblin-ambush",
            party_driver=HeuristicTacticalDriver(cls.rules),
            enemy_driver=HeuristicTacticalDriver(cls.rules),
            rules=cls.rules,
            seed=11,
            max_rounds=4,
        )

    def test_emitted_traces_record_provenance_and_replay_cleanly(self) -> None:
        expected_rules_sha = hashlib.sha256(self.rules.rules_path.read_bytes()).hexdigest()
        self.assertEqual(compute_rules_digest(self.rules), expected_rules_sha)
        self.assertEqual(ENGINE_VERSION, PKG_VERSION)

        for tx in (self.crypt_tx, self.goblin_tx):
            self.assertEqual(tx["engine_version"], ENGINE_VERSION)
            self.assertEqual(tx["rules_digest"], expected_rules_sha)
            self.assertEqual(tx["state_shape_version"], STATE_SHAPE_VERSION)

            rep = replay_match(json.loads(json.dumps(tx)), self.rules)
            self.assertEqual(rep["status"], "ok")
            self.assertEqual(rep["replay_status"], "match")
            self.assertTrue(rep["deterministic_match"])
            self.assertTrue(rep["events_match"])
            self.assertEqual(rep["state_schema_drift"], [])
            self.assertEqual(rep["provenance_warnings"], [])
            self.assertEqual(rep["diffs"], [])

        # Interactive sessions emit the same provenance keys.
        session_tx = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            seed=3,
            max_rounds=1,
            input_fn=lambda _p: "e",
            output_fn=lambda _m: None,
        )
        self.assertEqual(session_tx["engine_version"], ENGINE_VERSION)
        self.assertEqual(session_tx["rules_digest"], expected_rules_sha)
        self.assertEqual(session_tx["state_shape_version"], STATE_SHAPE_VERSION)
        s_rep = replay_match(session_tx, self.rules)
        self.assertEqual(s_rep["replay_status"], "match")
        self.assertTrue(s_rep["deterministic_match"])
        self.assertEqual(s_rep["provenance_warnings"], [])

    def test_removed_combatant_schema_field_reported_as_drift_not_event_mismatch(self) -> None:
        # Simulate a pre-#39 trace where monsters lacked `traits_ir` in both initial_state and final_state.
        drifted = copy.deepcopy(self.crypt_tx)
        removed_any = False
        for state_key in ("initial_state", "final_state"):
            for comb in drifted[state_key]["combatants"].values():
                if "traits_ir" in comb:
                    del comb["traits_ir"]
                    removed_any = True
        self.assertTrue(removed_any, "crypt-of-ghouls must have a combatant with traits_ir")
        drifted["digest"] = compute_transcript_digest(drifted)

        rep = replay_match(drifted, self.rules)
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["replay_status"], "match_with_drift")
        self.assertTrue(rep["events_match"])
        self.assertFalse(rep["deterministic_match"])
        self.assertEqual(rep["state_schema_drift"], ["combatants.*.traits_ir"])

        md = render_replay_markdown(rep)
        self.assertIn("match_with_drift", md)
        self.assertIn("combatants.*.traits_ir", md)

    def test_added_legacy_combatant_field_reported_as_drift(self) -> None:
        drifted = copy.deepcopy(self.goblin_tx)
        for state_key in ("initial_state", "final_state"):
            for comb in drifted[state_key]["combatants"].values():
                comb["legacy_schema_tag"] = "v0.3"
        drifted["digest"] = compute_transcript_digest(drifted)

        rep = replay_match(drifted, self.rules)
        self.assertEqual(rep["replay_status"], "match_with_drift")
        self.assertTrue(rep["events_match"])
        self.assertFalse(rep["deterministic_match"])
        self.assertEqual(rep["state_schema_drift"], ["combatants.*.legacy_schema_tag"])

    def test_tampered_damage_event_is_mismatch_with_event_diff(self) -> None:
        tampered = copy.deepcopy(self.goblin_tx)
        edited = False
        for act in tampered["actions"]:
            for ev in act["events"]:
                if ev.get("type") == "damage_applied":
                    ev["total_damage"] = int(ev["total_damage"]) + 99
                    edited = True
                    break
            if edited:
                break
        self.assertTrue(edited, "expected at least one damage_applied event in goblin-ambush")
        tampered["digest"] = compute_transcript_digest(tampered)

        rep = replay_match(tampered, self.rules)
        self.assertEqual(rep["replay_status"], "mismatch")
        self.assertFalse(rep["deterministic_match"])
        self.assertFalse(rep["events_match"])
        self.assertTrue(any("events differ" in d for d in rep["diffs"]), rep["diffs"])

    def test_final_state_only_field_deletion_is_not_masked_as_drift(self) -> None:
        tampered = copy.deepcopy(self.goblin_tx)
        first_cid = next(iter(tampered["final_state"]["combatants"]))
        del tampered["final_state"]["combatants"][first_cid]["hp"]
        tampered["digest"] = compute_transcript_digest(tampered)

        rep = replay_match(tampered, self.rules)
        self.assertEqual(rep["replay_status"], "mismatch")
        self.assertFalse(rep["deterministic_match"])
        self.assertEqual(rep["state_schema_drift"], [])

    def test_provenance_mismatch_warnings_emitted(self) -> None:
        skewed = copy.deepcopy(self.goblin_tx)
        skewed["rules_digest"] = "0" * 64
        skewed["engine_version"] = "0.0.0-old"
        skewed["state_shape_version"] = 999

        rep = replay_match(skewed, self.rules)
        self.assertEqual(rep["replay_status"], "match")
        self.assertTrue(rep["deterministic_match"])
        joined = "\n".join(rep["provenance_warnings"])
        self.assertIn("rules_digest mismatch", joined)
        self.assertIn("engine_version mismatch", joined)
        self.assertIn("state_shape_version mismatch", joined)

        md = render_replay_markdown(rep)
        self.assertIn("Provenance Warnings", md)
        self.assertIn("rules_digest mismatch", md)

    def test_state_and_combatant_key_set_guard(self) -> None:
        """Fail loudly if EncounterState or combatant fields change without bumping STATE_SHAPE_VERSION."""
        self.assertEqual(
            STATE_SHAPE_VERSION,
            EXPECTED_STATE_SHAPE_VERSION,
            "STATE_SHAPE_VERSION changed; update EXPECTED_STATE_SHAPE_VERSION and key-set snapshots in this test.",
        )
        for sid in ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"):
            with self.subTest(scenario=sid):
                sc = load_scenario(sid, self.rules)
                res = step(
                    None,
                    {
                        "type": "start_encounter",
                        "encounter_id": sc["id"],
                        "seed": 1,
                        "combatants": sc["combatants"],
                    },
                    self.rules,
                )
                self.assertEqual(res["status"], "ok")
                st = res["state"]
                self.assertEqual(
                    sorted(st.keys()),
                    EXPECTED_ENCOUNTER_STATE_KEYS,
                    "EncounterState top-level keys changed! Bump STATE_SHAPE_VERSION in "
                    "src/srd2024/arena/runner.py and update EXPECTED_ENCOUNTER_STATE_KEYS.",
                )
                for cid, comb in st["combatants"].items():
                    actual = set(comb.keys())
                    base = set(EXPECTED_BASE_COMBATANT_KEYS)
                    missing = sorted(base - actual)
                    unknown = sorted(actual - base - KNOWN_OPTIONAL_COMBATANT_KEYS)
                    self.assertEqual(
                        missing,
                        [],
                        f"Combatant '{cid}' is missing expected base keys {missing}. "
                        "Bump STATE_SHAPE_VERSION in src/srd2024/arena/runner.py and update the guard snapshot.",
                    )
                    self.assertEqual(
                        unknown,
                        [],
                        f"Combatant '{cid}' introduced untracked keys {unknown}. "
                        "Bump STATE_SHAPE_VERSION in src/srd2024/arena/runner.py and update the guard snapshot.",
                    )


if __name__ == "__main__":
    unittest.main()
