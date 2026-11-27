"""Stage 4 Track 2 Tests: Human-in-the-Loop Interactive Tabletop Play.

Covers:
1. Frontend-ready `build_human_action_menu` JSON schema & coverage (attacks w/ mastery,
   monster actions, spells/slots/AoE/healing, smart move destinations, stand_up when
   Prone, dodge/dash/disengage/help, end_turn).
2. `render_tactical_hud` ASCII 5-ft battlefield map, legend, actor/ally stats, strict
   partial observability (NO hidden enemy exact HP or AC), party `table_talk`, and menu.
3. `HumanDriver` unit behavior: numbered choices, `e`/`end`, `move x y`, `stand`,
   `say <msg>`, `status`/`map`, `help`, invalid input recovery, `retry_feedback`,
   `save`/`quit` -> `HumanSessionPause`, and `create_driver("human")`.
4. Interactive 1-PC session (`--as pc-bram` in `goblin-ambush` alongside AI `pc-talia`)
   to completion -> HUD check, partial observability check, `table_talk` check,
   final scorecard, and 100% deterministic `replay_match` verification.
5. Interactive DM session (`--as dm`) running monsters against an AI party + `--as party`.
6. Mixed per-seat overrides (`--seat pc-aldric=human --seat pc-lyra=greedy ...`).
7. Lossless Save & Resume (`quit` -> checkpoint JSON -> `--resume` completes match &
   verifies 100% deterministic `replay_match` parity!).
8. Natural-Language-to-Intent translation via mocked `http_transport` on
   `OpenAICompatDriver` (plus graceful recovery on malformed NL response).
9. CLI subprocess tests for BOTH `bin/srd-arena play` and `bin/srd arena play` with
   piped scripted stdin, `--out-trace`, `replay` verification, JSON parity, and CLI
   save/resume round-trip.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from srd2024.arena import (
    GreedyDriver,
    HeuristicTacticalDriver,
    HumanDriver,
    HumanSessionPause,
    OpenAICompatDriver,
    build_human_action_menu,
    build_observation,
    create_driver,
    load_scenario,
    load_session_checkpoint,
    render_match_markdown,
    render_tactical_hud,
    replay_match,
    resolve_seat_drivers,
    run_interactive_session,
)
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore
from tests.helpers import BIN_SRD, REPO_ROOT

BIN_ARENA = REPO_ROOT / "bin" / "srd-arena"


def _make_scripted_io(commands: list[str], *, fallback: str = "e") -> tuple[Any, Any, list[str]]:
    """Return `(input_fn, output_fn, captured_lines)` driven by a scripted command list."""
    queue = list(commands)
    outputs: list[str] = []

    def _in(_prompt: str) -> str:
        if queue:
            return queue.pop(0)
        return fallback

    def _out(text: str) -> None:
        outputs.append(str(text))

    return _in, _out, outputs


class TestHumanMenuAndHud(unittest.TestCase):
    """Frontend-ready action menu, ASCII tactical HUD, and strict partial observability."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def _start_scenario_state(self, scenario_id: str, seed: int = 42) -> tuple[dict[str, Any], dict[str, Any]]:
        compiled = load_scenario(scenario_id, self.rules)
        res = step(
            None,
            {
                "type": "start_encounter",
                "encounter_id": compiled["id"],
                "seed": seed,
                "combatants": copy.deepcopy(compiled["combatants"]),
            },
            self.rules,
        )
        self.assertEqual(res["status"], "ok")
        return compiled, res["state"]

    def test_action_menu_json_schema_pc_and_prone_stand_up(self) -> None:
        compiled, state = self._start_scenario_state("goblin-ambush", seed=42)
        # Put Bram adjacent to goblin-1 and mark him Prone to test stand_up + melee mastery
        state["combatants"]["pc-bram"]["position"] = {"x": 15, "y": 0}
        state["combatants"]["pc-bram"]["conditions"].append(
            {"condition": "prone", "source_id": None, "duration": "indefinite"}
        )
        obs = build_observation(
            state,
            "pc-bram",
            compiled,
            {"party": [{"round": 1, "actor_id": "pc-talia", "actor_name": "Talia Spark", "message": "Focus Scout A!"}], "enemy": []},
            ["Encounter started."],
            {},
            self.rules,
        )
        menu = build_human_action_menu(obs, self.rules)

        # Must be cleanly JSON-serializable for web UI consumption
        serialized = json.dumps(menu)
        roundtrip = json.loads(serialized)
        self.assertEqual(len(roundtrip), len(menu))
        self.assertEqual([m["index"] for m in menu], list(range(1, len(menu) + 1)))

        # First item when Prone should be Stand up
        self.assertEqual(menu[0]["category"], "move")
        self.assertTrue(menu[0]["intent"].get("stand_up"))

        # Must include weapon attacks (with mastery info), tactical actions, and end_turn last
        intent_types = {m["intent"]["type"] for m in menu}
        self.assertTrue({"move", "attack", "dodge", "dash", "disengage", "help", "end_turn"}.issubset(intent_types))
        self.assertEqual(menu[-1]["intent"]["type"], "end_turn")

        attack_items = [m for m in menu if m["category"] == "attack"]
        self.assertTrue(any(m["meta"].get("weapon") == "longsword" and m["meta"].get("mastery") == "sap" for m in attack_items))

        # Render HUD and verify ASCII map, party talk, and strict partial observability
        hud = render_tactical_hud(obs, menu)
        self.assertIn("Battlefield Map (5-ft squares):", hud)
        self.assertIn("Legend:", hud)
        self.assertIn("Bram Ironhand", hud)
        self.assertIn("Talia Spark", hud)
        self.assertIn("Focus Scout A!", hud)
        self.assertIn("[unharmed]", hud)
        # Strict partial observability: goblin exact HP/AC must NEVER appear
        goblin_hp = state["combatants"]["goblin-1"]["hp"]
        goblin_hp_max = state["combatants"]["goblin-1"]["hp_max"]
        goblin_ac = state["combatants"]["goblin-1"]["ac"]
        self.assertNotIn(f"{goblin_hp}/{goblin_hp_max}", hud)
        self.assertNotIn(f"AC {goblin_ac}", hud)

    def test_action_menu_spellcaster_and_monster_options(self) -> None:
        compiled, state = self._start_scenario_state("crypt-of-ghouls", seed=7)
        obs_lyra = build_observation(state, "pc-lyra", compiled, {"party": [], "enemy": []}, [], {}, self.rules)
        menu_lyra = build_human_action_menu(obs_lyra, self.rules)
        spell_slugs = {m["intent"]["spell"] for m in menu_lyra if m["intent"]["type"] == "cast_spell"}
        self.assertIn("fireball", spell_slugs)
        self.assertIn("magic-missile", spell_slugs)
        self.assertIn("mage-armor", spell_slugs)
        self.assertIn("shield", spell_slugs)

        obs_ghoul = build_observation(state, "ghoul-1", compiled, {"party": [], "enemy": []}, [], {}, self.rules)
        menu_ghoul = build_human_action_menu(obs_ghoul, self.rules)
        self.assertTrue(any(m["intent"]["type"] == "move" for m in menu_ghoul))
        self.assertEqual(menu_ghoul[-1]["intent"]["type"], "end_turn")

    def test_human_driver_commands_and_create_driver_human(self) -> None:
        compiled, state = self._start_scenario_state("goblin-ambush", seed=42)
        obs = build_observation(state, "pc-bram", compiled, {"party": [], "enemy": []}, [], {}, self.rules)

        in_fn, out_fn, outputs = _make_scripted_io(
            ["", "999", "bogus", "help", "status", "say Hold the line!", "move 10 0"]
        )
        drv = create_driver(
            "human",
            rules=self.rules,
            input_fn=in_fn,
            output_fn=out_fn,
            allow_human=True,
        )
        self.assertIsInstance(drv, HumanDriver)
        dec = drv.decide(obs)

        self.assertEqual(dec.intent, {"type": "move", "actor_id": "pc-bram", "to": {"x": 10, "y": 0}})
        self.assertEqual(dec.table_talk, "Hold the line!")
        combined = "\n".join(outputs)
        self.assertIn("Interactive Tabletop Commands:", combined)
        self.assertIn("Invalid selection", combined)
        self.assertIn("Unrecognized input", combined)

        # Verify save/quit raise HumanSessionPause
        for cmd, expected_reason in (("save", "save"), ("quit", "quit")):
            in_p, out_p, _ = _make_scripted_io([cmd])
            p_drv = HumanDriver(rules=self.rules, input_fn=in_p, output_fn=out_p, show_hud=False)
            with self.assertRaises(HumanSessionPause) as ctx:
                p_drv.decide(obs)
            self.assertEqual(ctx.exception.reason, expected_reason)

    def test_heroic_inspiration_hud_menu_and_reroll_shortcuts(self) -> None:
        compiled, state = self._start_scenario_state("goblin-ambush", seed=42)
        obs = build_observation(state, "pc-bram", compiled, {"party": [], "enemy": []}, [], {}, self.rules)
        obs["self"]["heroic_inspiration"] = True
        obs["legal_actions"]["can_reroll"] = True
        obs["legal_actions"]["reroll_hint"] = {
            "label": "Javelin",
            "target_id": "goblin-1",
            "target_name": "Goblin Scout A",
            "previous_d20": 9,
            "previous_total": 14,
            "target_ac": 15,
        }

        menu = build_human_action_menu(obs, self.rules)
        self.assertEqual(menu[0]["index"], 1)
        self.assertEqual(menu[0]["intent"], {"type": "reroll", "actor_id": "pc-bram"})
        self.assertEqual(
            menu[0]["label"],
            "Spend Heroic Inspiration — reroll Javelin vs Goblin Scout A (was 14)",
        )
        self.assertNotIn("target_ac", menu[0]["meta"]["reroll_hint"])

        hud = render_tactical_hud(obs, menu)
        self.assertIn("Heroic Inspiration: Ready", hud)
        self.assertIn("Spend Heroic Inspiration — reroll Javelin vs Goblin Scout A (was 14)", hud)
        self.assertNotIn("vs AC", hud)

        # Verify all reroll shortcuts emit {"type": "reroll", "actor_id": "pc-bram"}
        for shortcut in ("r", "reroll", "inspiration", "heroic inspiration"):
            in_fn, out_fn, outs = _make_scripted_io(["help", shortcut])
            drv = HumanDriver(rules=self.rules, input_fn=in_fn, output_fn=out_fn, show_hud=False)
            dec = drv.decide(obs)
            self.assertEqual(dec.intent, {"type": "reroll", "actor_id": "pc-bram"})
            self.assertIn("r | reroll   Spend Heroic Inspiration to reroll your last missed d20", "\n".join(outs))

    def test_render_match_markdown_formats_per_seat_driver_dicts_cleanly(self) -> None:
        in_fn, out_fn, _ = _make_scripted_io(["1", "e"] * 25, fallback="e")
        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            seed=42,
            input_fn=in_fn,
            output_fn=out_fn,
        )
        md = render_match_markdown(transcript)
        self.assertIn("| Party Driver | `pc-bram: human, pc-talia: heuristic-tactical` |", md)
        self.assertIn("| Enemy Driver | `goblin-1: heuristic-tactical, goblin-2: heuristic-tactical` |", md)
        self.assertNotIn("{'", md)

        # String drivers remain unchanged
        str_transcript = copy.deepcopy(transcript)
        str_transcript["config"]["party_driver"] = "heuristic-tactical"
        str_transcript["config"]["enemy_driver"] = "random"
        md_str = render_match_markdown(str_transcript)
        self.assertIn("| Party Driver | `heuristic-tactical` |", md_str)
        self.assertIn("| Enemy Driver | `random` |", md_str)


class TestInteractiveSessionsAndSeats(unittest.TestCase):
    """Per-seat resolution, 1-PC play, DM play, mixed seats, save/resume, and NL mode."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_play_one_pc_bram_in_goblin_ambush_and_replay_determinism(self) -> None:
        # Script Bram: queue table_talk, pick option 1 (attack or move) repeatedly until match ends
        script = [
            "say Focusing Goblin Scout A — cover my flank Talia!",
            "1",
            "1",
            "e",
        ] + ["1", "e"] * 25
        in_fn, out_fn, outputs = _make_scripted_io(script, fallback="e")

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            seed=42,
            input_fn=in_fn,
            output_fn=out_fn,
        )

        self.assertIn(transcript["winner"], ("party", "enemy", "draw"))
        self.assertGreater(len(transcript["actions"]), 0)
        self.assertIn("scorecard", transcript)
        self.assertIn("composite_score", transcript["scorecard"])
        self.assertEqual(transcript["config"]["party_driver"]["pc-bram"], "human")
        self.assertEqual(transcript["config"]["party_driver"]["pc-talia"], "heuristic-tactical")

        # Verify Bram's say table_talk was recorded on party blackboard and in actions
        party_talk = [m["message"] for m in transcript["blackboard"]["party"]]
        self.assertTrue(any("Focusing Goblin Scout A" in msg for msg in party_talk))

        # Verify ASCII HUD rendered and strict partial observability held (no goblin exact HP or AC leaked)
        joined_out = "\n".join(outputs)
        self.assertIn("Battlefield Map (5-ft squares):", joined_out)
        self.assertIn("VISIBLE ENEMIES:", joined_out)
        goblin_hp_max = transcript["initial_state"]["combatants"]["goblin-1"]["hp_max"]
        self.assertNotIn(f"{goblin_hp_max}/{goblin_hp_max}", joined_out)
        for line in joined_out.splitlines():
            if "Goblin Scout" in line and ("VISIBLE ENEMIES" in line or line.strip().startswith("- Goblin Scout")):
                self.assertNotIn("AC ", line)
                self.assertNotIn("HP ", line)
            if "hits Goblin Scout" in line or "misses Goblin Scout" in line:
                self.assertNotIn("vs AC", line)

        # Verify 100% deterministic replay parity!
        rep = replay_match(transcript, self.rules)
        self.assertTrue(rep["deterministic_match"], f"Replay diverged: {rep['diffs']}")
        self.assertEqual(rep["original_digest"], rep["replayed_digest"])
        self.assertEqual(rep["diffs"], [])

    def test_play_as_dm_monsters_against_ai_party(self) -> None:
        script = ["1", "e"] * 30
        in_fn, out_fn, outputs = _make_scripted_io(script, fallback="e")

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="dm",
            seed=7,
            input_fn=in_fn,
            output_fn=out_fn,
        )

        self.assertEqual(transcript["config"]["enemy_driver"]["goblin-1"], "human")
        self.assertEqual(transcript["config"]["enemy_driver"]["goblin-2"], "human")
        self.assertEqual(transcript["config"]["party_driver"]["pc-bram"], "heuristic-tactical")
        self.assertEqual(transcript["config"]["party_driver"]["pc-talia"], "heuristic-tactical")

        # DM should see Goblin HUD and NOT see PC exact AC (Bram has AC 19)
        joined_out = "\n".join(outputs)
        self.assertIn("Goblin Scout", joined_out)
        self.assertNotIn("AC 19", joined_out)

        rep = replay_match(transcript, self.rules)
        self.assertTrue(rep["deterministic_match"], f"DM replay diverged: {rep['diffs']}")

    def test_mixed_seat_overrides_and_validation(self) -> None:
        in_fn, out_fn, _ = _make_scripted_io(["1", "e"] * 40, fallback="e")
        h_drv = HumanDriver(rules=self.rules, input_fn=in_fn, output_fn=out_fn, show_hud=False)

        p_map, e_map = resolve_seat_drivers(
            "crypt-of-ghouls",
            seat_overrides=[
                "pc-aldric=human",
                "pc-lyra=greedy",
                "pc-dorian=heuristic-tactical",
                "skeleton-1=random",
            ],
            human_driver=h_drv,
            rules=self.rules,
            seed=11,
        )
        self.assertIs(p_map["pc-aldric"], h_drv)
        self.assertIsInstance(p_map["pc-lyra"], GreedyDriver)
        self.assertIsInstance(p_map["pc-dorian"], HeuristicTacticalDriver)
        self.assertEqual(e_map["skeleton-1"].name, "random")
        self.assertEqual(e_map["ghoul-1"].name, "heuristic-tactical")

        # Invalid seat ID and invalid --as target raise clear ValueError
        with self.assertRaises(ValueError):
            resolve_seat_drivers("goblin-ambush", play_as="nonexistent-pc", rules=self.rules)
        with self.assertRaises(ValueError):
            resolve_seat_drivers("goblin-ambush", seat_overrides=["bad-id=human"], rules=self.rules)

        # Full --as party smoke run on goblin-ambush
        t_party = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="party",
            seed=5,
            max_rounds=4,
            input_fn=in_fn,
            output_fn=out_fn,
        )
        self.assertEqual(t_party["config"]["party_driver"]["pc-bram"], "human")
        self.assertEqual(t_party["config"]["party_driver"]["pc-talia"], "human")
        rep = replay_match(t_party, self.rules)
        self.assertTrue(rep["deterministic_match"], rep["diffs"])

    def test_save_and_resume_checkpoint_completes_and_replays_deterministically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="srd2024-s4-ckpt-") as tmp:
            ckpt_path = Path(tmp) / "goblin-ckpt.json"
            trace_path = Path(tmp) / "goblin-resumed-trace.json"

            # Phase 1: Take one action, then quit mid-turn to trigger checkpoint save
            in1, out1, _ = _make_scripted_io(["1", "quit"])
            paused = run_interactive_session(
                "goblin-ambush",
                rules=self.rules,
                play_as="pc-bram",
                seed=42,
                save_path=ckpt_path,
                input_fn=in1,
                output_fn=out1,
            )
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(paused["reason"], "quit")
            self.assertTrue(ckpt_path.is_file())
            loaded_ckpt = load_session_checkpoint(ckpt_path)
            self.assertEqual(loaded_ckpt["kind"], "arena_session_checkpoint")
            self.assertGreaterEqual(len(loaded_ckpt["actions"]), 1)

            # Phase 2: Resume seamlessly to completion and write out_trace
            in2, out2, out2_lines = _make_scripted_io(["1", "e"] * 30, fallback="e")
            transcript = run_interactive_session(
                rules=self.rules,
                resume_path=ckpt_path,
                out_trace=trace_path,
                input_fn=in2,
                output_fn=out2,
            )
            self.assertIn("Resumed 5e 2024", "\n".join(out2_lines))
            self.assertTrue(trace_path.is_file())
            self.assertIn(transcript["winner"], ("party", "enemy", "draw"))
            self.assertGreater(len(transcript["actions"]), len(loaded_ckpt["actions"]))

            # Verify the saved trace replays with 100% deterministic digest match!
            saved_trace = json.loads(trace_path.read_text(encoding="utf-8"))
            rep = replay_match(saved_trace, self.rules)
            self.assertTrue(rep["deterministic_match"], f"Resumed replay diverged: {rep['diffs']}")
            self.assertEqual(rep["diffs"], [])

    def test_natural_language_to_intent_translation_via_mock_http_transport(self) -> None:
        calls: list[dict[str, Any]] = []

        def _mock_transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
            calls.append({"url": url, "payload": payload})
            user_msg = payload["messages"][-1]["content"]
            # Simulate one malformed response first when asked "garbled", then clean intent JSON
            if "garbled" in user_msg:
                content = "I am not valid JSON at all!"
            elif "advance" in user_msg.lower():
                content = json.dumps(
                    {
                        "intent": {"type": "move", "actor_id": "pc-bram", "to": {"x": 15, "y": 0}},
                        "rationale": "Advance into melee range of Goblin Scout A.",
                        "table_talk": "Closing in on the scouts!",
                    }
                )
            else:
                content = json.dumps(
                    {
                        "intent": {"type": "attack", "actor_id": "pc-bram", "target_id": "goblin-1", "weapon": "shortbow"},
                        "rationale": "Loose a shortbow arrow at Goblin Scout A.",
                        "table_talk": None,
                    }
                )
            return {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }

        nl_drv = OpenAICompatDriver(
            "mock-nl-model",
            api_base="http://127.0.0.1:11434/v1",
            http_transport=_mock_transport,
        )

        # Feed malformed NL command (recovers cleanly), then 2 plain-English commands, then end turn / finish
        script = [
            "garbled gibberish",
            "I loose a shortbow arrow at Goblin Scout A",
            "Advance up to Goblin Scout A",
            "e",
        ] + ["1", "e"] * 25
        in_fn, out_fn, outputs = _make_scripted_io(script, fallback="e")

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            nl_driver=nl_drv,
            seed=42,
            input_fn=in_fn,
            output_fn=out_fn,
        )

        self.assertGreaterEqual(len(calls), 3)
        joined = "\n".join(outputs)
        self.assertIn("Natural-language translation failed", joined)
        self.assertIn("[NL -> Intent]", joined)

        # Verify NL-translated intents actually executed in the transcript
        bram_intents = [a["intent"] for a in transcript["actions"] if a["actor_id"] == "pc-bram"]
        self.assertTrue(any(i.get("type") == "attack" and i.get("weapon") == "shortbow" for i in bram_intents))
        self.assertTrue(any(i.get("type") == "move" and i.get("to") == {"x": 15, "y": 0} for i in bram_intents))

        rep = replay_match(transcript, self.rules)
        self.assertTrue(rep["deterministic_match"], rep["diffs"])

    def test_retry_feedback_handled_cleanly_on_invalid_human_move(self) -> None:
        # First human move exceeds 30 ft movement budget -> step() rejects -> HumanDriver receives retry_feedback and recovers!
        script = ["move 100 100", "1", "e"] + ["1", "e"] * 25
        in_fn, out_fn, outputs = _make_scripted_io(script, fallback="e")

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            seed=42,
            input_fn=in_fn,
            output_fn=out_fn,
        )
        joined = "\n".join(outputs)
        self.assertIn("RETRY FEEDBACK:", joined)
        self.assertGreaterEqual(len(transcript["violations"]), 1)
        self.assertIn(transcript["winner"], ("party", "enemy", "draw"))

    def test_rejected_human_inputs_captured_in_transcript_audit_trail(self) -> None:
        script = ["do a backflip", "999", "1", "e"] + ["1", "e"] * 25
        in_fn, out_fn, _ = _make_scripted_io(script, fallback="e")

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            seed=42,
            input_fn=in_fn,
            output_fn=out_fn,
        )
        self.assertIn("rejected_inputs", transcript)
        self.assertIsInstance(transcript["rejected_inputs"], list)
        inputs_seen = [entry["input"] for entry in transcript["rejected_inputs"]]
        self.assertIn("do a backflip", inputs_seen)
        self.assertIn("999", inputs_seen)
        for entry in transcript["rejected_inputs"]:
            self.assertEqual(entry["actor_id"], "pc-bram")
            self.assertIn("round", entry)
            self.assertTrue(entry.get("reason"))

        rep = replay_match(transcript, self.rules)
        self.assertTrue(rep["deterministic_match"], f"Replay diverged: {rep['diffs']}")
        self.assertEqual(rep["diffs"], [])

    def test_heroic_inspiration_reroll_on_round2_javelin_miss_talia_hooks_and_replay(self) -> None:
        # R1: Bram closes to (15,0) [5], Longsword hits Goblin Scout A [1], end [e].
        # Talia casts Shield & Magic Missile (defeating Scout A).
        # R2: Bram throws Javelin at Goblin Scout B [2] -> misses (14 vs AC 15),
        #     sees Heroic Inspiration option #1 in HUD/menu, types 'r' to reroll -> hits!
        #     Talia casts Ray of Frost in R2.
        script = ["5", "1", "e", "2", "r", "e"] + ["1", "e"] * 15
        in_fn, out_fn, outputs = _make_scripted_io(script, fallback="e")

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            seed=42,
            input_fn=in_fn,
            output_fn=out_fn,
        )

        joined_out = "\n".join(outputs)
        self.assertIn("Heroic Inspiration: Ready", joined_out)
        self.assertIn(
            "Spend Heroic Inspiration — reroll Javelin vs Goblin Scout B (was 14)",
            joined_out,
        )
        self.assertIn("Bram Ironhand spends Heroic Inspiration to reroll Javelin", joined_out)

        # Verify reroll action was executed cleanly and Heroic Inspiration was consumed
        reroll_actions = [
            a for a in transcript["actions"]
            if a["actor_id"] == "pc-bram" and a["intent"].get("type") == "reroll"
        ]
        self.assertEqual(len(reroll_actions), 1)
        self.assertEqual(reroll_actions[0]["round"], 2)
        self.assertFalse(transcript["final_state"]["combatants"]["pc-bram"].get("heroic_inspiration", True))

        # Verify Talia's spell casts emitted readable narrative_hooks in streamed interactive output
        self.assertIn("Talia Spark casts Shield (+5 AC", joined_out)
        self.assertIn("Talia Spark casts Magic Missile at Goblin Scout A", joined_out)
        self.assertIn("Ray of Frost", joined_out)

        # Verify 100% deterministic replay parity!
        rep = replay_match(transcript, self.rules)
        self.assertTrue(rep["deterministic_match"], f"Reroll replay diverged: {rep['diffs']}")
        self.assertEqual(rep["diffs"], [])

    def test_reroll_when_heroic_inspiration_unavailable_or_spent_records_violation(self) -> None:
        # 1st 'reroll': before any miss -> engine rejects (no eligible roll)
        # 2nd 'reroll': immediately after spending Heroic Inspiration with 'r' -> engine rejects (already spent)
        script = ["reroll", "5", "1", "e", "2", "r", "reroll", "e"] + ["1", "e"] * 15
        in_fn, out_fn, outputs = _make_scripted_io(script, fallback="e")

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            seed=42,
            input_fn=in_fn,
            output_fn=out_fn,
        )

        reroll_viols = [
            v for v in transcript["violations"]
            if v.get("intent", {}).get("type") == "reroll"
        ]
        self.assertGreaterEqual(len(reroll_viols), 2)
        self.assertTrue(all(v["actor_id"] == "pc-bram" for v in reroll_viols))
        err_texts = " | ".join(str(v.get("error") or "") for v in reroll_viols)
        self.assertIn("Heroic Inspiration", err_texts)

        joined_out = "\n".join(outputs)
        self.assertIn("! Rejected:", joined_out)
        self.assertIn("Heroic Inspiration", joined_out)


class TestStage4PlayCliSubprocess(unittest.TestCase):
    """Subprocess tests for `bin/srd-arena play` AND `bin/srd arena play`."""

    def _run_bin(self, cmd: list[str], *, stdin_text: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=60.0,
            check=False,
        )

    def test_cli_bin_srd_arena_play_and_bin_srd_arena_play_parity_and_replay(self) -> None:
        script_stdin = "\n".join(["say Ready for ambush!", "1", "e"] + ["1", "e"] * 20) + "\n"

        with tempfile.TemporaryDirectory(prefix="srd2024-s4-cli-") as tmp:
            trace_a = Path(tmp) / "trace_arena.json"
            trace_s = Path(tmp) / "trace_srd.json"

            # 1. bin/srd-arena play (markdown + --out-trace)
            proc_a = self._run_bin(
                [
                    str(BIN_ARENA),
                    "play",
                    "goblin-ambush",
                    "--as",
                    "pc-bram",
                    "--seed",
                    "42",
                    "--out-trace",
                    str(trace_a),
                ],
                stdin_text=script_stdin,
            )
            self.assertEqual(proc_a.returncode, 0, f"STDERR:\n{proc_a.stderr}\nSTDOUT:\n{proc_a.stdout}")
            self.assertTrue(trace_a.is_file())
            self.assertIn("Battlefield Map (5-ft squares):", proc_a.stdout)
            self.assertIn("Quantitative Scorecard", proc_a.stdout)

            # 2. Verify bin/srd-arena replay returns deterministic_match: true!
            rep_proc = self._run_bin([str(BIN_ARENA), "replay", str(trace_a), "--format", "json"])
            self.assertEqual(rep_proc.returncode, 0, rep_proc.stderr)
            rep_json = json.loads(rep_proc.stdout)
            self.assertTrue(rep_json["deterministic_match"], rep_json["diffs"])
            self.assertEqual(rep_json["diffs"], [])

            # 3. bin/srd-arena play --format json vs bin/srd arena play --format json (1:1 JSON parity!)
            proc_aj = self._run_bin(
                [
                    str(BIN_ARENA),
                    "play",
                    "goblin-ambush",
                    "--as",
                    "pc-bram",
                    "--seed",
                    "42",
                    "--format",
                    "json",
                ],
                stdin_text=script_stdin,
            )
            proc_sj = self._run_bin(
                [
                    str(BIN_SRD),
                    "arena",
                    "play",
                    "goblin-ambush",
                    "--as",
                    "pc-bram",
                    "--seed",
                    "42",
                    "--out-trace",
                    str(trace_s),
                    "--format",
                    "json",
                ],
                stdin_text=script_stdin,
            )
            self.assertEqual(proc_aj.returncode, 0, proc_aj.stderr)
            self.assertEqual(proc_sj.returncode, 0, proc_sj.stderr)
            json_a = json.loads(proc_aj.stdout)
            json_s = json.loads(proc_sj.stdout)
            self.assertEqual(json_a, json_s)
            self.assertEqual(json_s, json.loads(trace_a.read_text(encoding="utf-8")))
            self.assertEqual(json_s, json.loads(trace_s.read_text(encoding="utf-8")))

    def test_cli_save_and_resume_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="srd2024-s4-cli-ckpt-") as tmp:
            ckpt = Path(tmp) / "save.json"
            trace = Path(tmp) / "resumed.json"

            # Pause with 'save'
            p1 = self._run_bin(
                [
                    str(BIN_ARENA),
                    "play",
                    "goblin-ambush",
                    "--as",
                    "pc-bram",
                    "--seed",
                    "42",
                    "--save",
                    str(ckpt),
                    "--format",
                    "json",
                ],
                stdin_text="1\nsave\n",
            )
            self.assertEqual(p1.returncode, 0, p1.stderr)
            p1_json = json.loads(p1.stdout)
            self.assertEqual(p1_json["status"], "paused")
            self.assertTrue(ckpt.is_file())

            # Resume via bin/srd arena play --resume
            resume_stdin = "\n".join(["1", "e"] * 25) + "\n"
            p2 = self._run_bin(
                [
                    str(BIN_SRD),
                    "arena",
                    "play",
                    "--resume",
                    str(ckpt),
                    "--out-trace",
                    str(trace),
                    "--format",
                    "json",
                ],
                stdin_text=resume_stdin,
            )
            self.assertEqual(p2.returncode, 0, p2.stderr)
            p2_json = json.loads(p2.stdout)
            self.assertIn(p2_json["winner"], ("party", "enemy", "draw"))
            self.assertTrue(trace.is_file())

            # Replay resumed trace
            rep = self._run_bin([str(BIN_ARENA), "replay", str(trace), "--format", "json"])
            self.assertEqual(rep.returncode, 0, rep.stderr)
            self.assertTrue(json.loads(rep.stdout)["deterministic_match"])


if __name__ == "__main__":
    unittest.main()
