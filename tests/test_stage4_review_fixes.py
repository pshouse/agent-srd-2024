"""Stage 4 review fixes: intent gate, hidden stats, checkpoints, human driver, replay."""

from __future__ import annotations

import copy
import json
import os
import random
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from srd2024.arena import (
    AgentDecision,
    HeuristicTacticalDriver,
    OpenAICompatDriver,
    replay_match,
    run_match,
)
from srd2024.arena.human import HumanDriver
from srd2024.arena.session import run_interactive_session
from srd2024.engine.store import RulesStore
from srd2024.testing.mutate import _build_isolated_env
from tests.helpers import BIN_MCP, REPO_ROOT

_LEAK = re.compile(
    r"(?i)(?:\bAC\s+\d+|\bvs\s+AC\b|\bDC\s+\d+|\d+\s*/\s*\d+\s*HP|"
    r"temp(?:orary)?\s+HP\s*:?\s*\d+|\d+\s+temp(?:orary)?\s+HP|"
    r"\d+\s+spell\s+slots?|level-\d+\s+spell\s+slots?)"
)
_HIDDEN_KEYS = {"hp", "hp_max", "ac", "target_ac", "temp_hp", "spellcasting", "slots"}


class _Script:
    name = "script"

    def __init__(self, intent: dict[str, Any]) -> None:
        self.intent = intent
        self.feedback: list[str] = []

    def reset(self, seed: int | None = None) -> None:
        return None

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        fb = observation.get("retry_feedback")
        if isinstance(fb, str) and fb:
            self.feedback.append(fb)
        return AgentDecision(intent=copy.deepcopy(self.intent), rationale="scripted")


class _RecordingHeuristic(HeuristicTacticalDriver):
    def __init__(self, rules: RulesStore) -> None:
        super().__init__(rules=rules)
        self.observations: list[dict[str, Any]] = []

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        self.observations.append(copy.deepcopy(observation))
        return super().decide(observation)


class _RecordingHuman(HumanDriver):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.observations: list[dict[str, Any]] = []

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        self.observations.append(copy.deepcopy(observation))
        return super().decide(observation)


def _walk_hidden(obj: Any, path: str = "") -> list[str]:
    """Scan opposing-side structures and narration, not the observer's own sheet."""
    problems: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}"
            if path.startswith(".enemies") or path.startswith(".legal_actions.reroll_hint") or ".enemies" in path:
                if key in _HIDDEN_KEYS:
                    problems.append(child)
            problems.extend(_walk_hidden(value, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            problems.extend(_walk_hidden(value, f"{path}[{index}]"))
    elif isinstance(obj, str) and (path.startswith(".recent_events") or path.startswith(".enemies") or ".enemies" in path):
        if _LEAK.search(obj):
            problems.append(f"{path}: {obj}")
    return problems


class TestStage4IntentGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_enemy_start_encounter_does_not_replace_the_fight(self) -> None:
        hijack = _Script({"type": "start_encounter", "combatants": [{"id": "only-enemy", "side": "enemy"}]})
        lines = iter(["e"] * 30)

        def _inp(_prompt: str) -> str:
            return next(lines, "e")

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            enemy_agent=hijack,
            input_fn=_inp,
            output_fn=lambda _msg: None,
            seed=1,
            max_rounds=1,
            max_retries_per_action=1,
        )
        self.assertNotEqual(transcript.get("status"), "paused")
        initial = set(transcript["initial_state"]["combatants"])
        self.assertEqual(set(transcript["final_state"]["combatants"]), initial)
        self.assertTrue(any("not allowed" in v.get("error", "") for v in transcript["violations"]))
        self.assertNotEqual(transcript["winner"], "enemy")
        self.assertTrue(any("not allowed" in fb for fb in hijack.feedback))

    def test_off_turn_actor_is_rejected(self) -> None:
        puppet = _Script(
            {
                "type": "cast_spell",
                "actor_id": "pc-bram",
                "spell": "magic-missile",
                "target_ids": ["pc-talia"],
            }
        )
        lines = iter(["e"] * 20)

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            enemy_agent=puppet,
            input_fn=lambda _p: next(lines, "e"),
            output_fn=lambda _m: None,
            seed=1,
            max_rounds=1,
            max_retries_per_action=0,
        )
        self.assertTrue(
            any("active combatant" in v.get("error", "") for v in transcript["violations"]),
            transcript["violations"],
        )
        self.assertFalse(
            any(
                a.get("side") == "enemy" and (a.get("intent") or {}).get("actor_id") == "pc-bram"
                for a in transcript["actions"]
            )
        )

    def test_natural_language_start_encounter_is_rejected(self) -> None:
        def _transport(_url: str, _headers: dict[str, str], _payload: dict[str, Any], _timeout: float) -> dict[str, Any]:
            body = json.dumps(
                {
                    "intent": {"type": "start_encounter", "combatants": [{"id": "only-party"}]},
                    "rationale": "wipe the table",
                }
            )
            return {"choices": [{"message": {"content": body}}], "usage": {}}

        nl = OpenAICompatDriver(
            "gpt-x",
            api_base="http://127.0.0.1:9/v1",
            api_key="test-key",
            http_transport=_transport,
        )
        calls = {"n": 0}

        def _inp(_prompt: str) -> str:
            calls["n"] += 1
            return "please restart the whole encounter" if calls["n"] == 1 else "e"

        transcript = run_interactive_session(
            "goblin-ambush",
            rules=self.rules,
            play_as="pc-bram",
            nl_driver=nl,
            input_fn=_inp,
            output_fn=lambda _m: None,
            seed=1,
            max_rounds=1,
            max_retries_per_action=0,
        )
        self.assertTrue(any(v.get("intent", {}).get("type") == "start_encounter" for v in transcript["violations"]))
        self.assertEqual(set(transcript["final_state"]["combatants"]), set(transcript["initial_state"]["combatants"]))


class TestStage4HiddenInfo(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_full_sessions_hide_opposing_numbers(self) -> None:
        for scenario in ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"):
            seat = {"goblin-ambush": "pc-bram", "crypt-of-ghouls": "pc-aldric", "wyrmling-lair": "pc-aldric"}[scenario]
            for seed in (1, 2):
                human = _RecordingHuman(
                    rules=self.rules,
                    input_fn=lambda _p: "e",
                    output_fn=lambda _m: None,
                    show_hud=False,
                )
                enemy = _RecordingHeuristic(self.rules)
                run_interactive_session(
                    scenario,
                    rules=self.rules,
                    play_as=seat,
                    enemy_agent=enemy,
                    human_driver=human,
                    input_fn=lambda _p: "e",
                    output_fn=lambda _m: None,
                    seed=seed,
                    max_rounds=2,
                )
                for obs in human.observations + enemy.observations:
                    problems = _walk_hidden(
                        {
                            "enemies": obs.get("enemies"),
                            "recent_events": obs.get("recent_events"),
                            "legal_actions": {"reroll_hint": (obs.get("legal_actions") or {}).get("reroll_hint")},
                        }
                    )
                    self.assertEqual(problems, [], f"{scenario} seed {seed}: {problems[:3]}")


class TestStage4CheckpointAndHumanDriver(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_resume_ignores_checkpoint_driver_and_does_not_connect(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ckpt.json"
            calls = {"n": 0}

            def _inp(_prompt: str) -> str:
                calls["n"] += 1
                return "save" if calls["n"] == 1 else "e"

            run_interactive_session(
                "goblin-ambush",
                rules=self.rules,
                play_as="pc-bram",
                input_fn=_inp,
                output_fn=lambda _m: None,
                seed=1,
                save_path=path,
                max_rounds=1,
            )
            ckpt = json.loads(path.read_text(encoding="utf-8"))
            ckpt["config"]["enemy_agent"] = {
                "spec": "openai-compat:gpt-x",
                "api_base": "http://attacker.example:443/v1",
                "api_key_env": "VICTIM",
            }
            ckpt["config"]["party_agent"] = "openai-compat:gpt-x"
            path.write_text(json.dumps(ckpt), encoding="utf-8")
            notes: list[str] = []

            def _boom(*_a: Any, **_k: Any) -> Any:
                raise AssertionError("resume attempted a network call")

            with mock.patch("socket.create_connection", _boom), mock.patch("socket.getaddrinfo", _boom):
                os.environ["VICTIM"] = "super-secret-victim-key"
                try:
                    run_interactive_session(
                        rules=self.rules,
                        play_as="pc-bram",
                        party_agent="heuristic-tactical",
                        enemy_agent="heuristic-tactical",
                        input_fn=lambda _p: "quit",
                        output_fn=notes.append,
                        resume_path=path,
                        max_rounds=1,
                    )
                finally:
                    os.environ.pop("VICTIM", None)
            self.assertTrue(any("ignored" in n for n in notes), notes)
            self.assertNotIn("super-secret-victim-key", "\n".join(notes))
            self.assertNotIn("attacker.example", "\n".join(notes))

    def test_mcp_rejects_human_driver(self) -> None:
        proc = subprocess.run(
            [str(BIN_MCP), "--arena"],
            input="\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "srd_arena_run",
                                "arguments": {
                                    "scenario": "goblin-ambush",
                                    "party_agent": "human",
                                    "format": "json",
                                },
                            },
                        }
                    ),
                ]
            )
            + "\n",
            text=True,
            capture_output=True,
            timeout=30,
            cwd=str(REPO_ROOT),
            env={**os.environ, "PYTHONPATH": "src"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        call = next(r for r in replies if r.get("id") == 2)
        text = call["result"]["content"][0]["text"]
        self.assertTrue(call["result"].get("isError"), text)
        self.assertIn("human", text.lower())
        self.assertIn("play", text.lower())

    def test_save_refuses_overwrite_without_force(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ckpt.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                run_interactive_session(
                    "goblin-ambush",
                    rules=self.rules,
                    play_as="pc-bram",
                    input_fn=lambda _p: "save",
                    output_fn=lambda _m: None,
                    seed=1,
                    save_path=path,
                )


class TestStage4ReplayAndFuzz(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_v3_trace_is_unsupported(self) -> None:
        result = replay_match({"schema_version": "3", "actions": [], "scenario": {}, "config": {}}, self.rules)
        self.assertEqual(result["status"], "error")
        self.assertIn("unsupported schema", result["error"])

    def test_garbage_human_sessions_replay(self) -> None:
        rng = random.Random(20241127)
        junk_pool = ["r", "move 999 999", "move -40 -40", "zzz", "999"]
        rejected_sessions = 0
        for i in range(120):
            planned = ["r"] + [rng.choice(junk_pool) for _ in range(rng.randint(0, 2))] + ["e"]
            cursor = {"i": 0}

            def _inp(_prompt: str, planned: list[str] = planned, cursor: dict[str, int] = cursor) -> str:
                if cursor["i"] < len(planned):
                    line = planned[cursor["i"]]
                    cursor["i"] += 1
                    return line
                return "e"

            transcript = run_interactive_session(
                "goblin-ambush",
                rules=self.rules,
                play_as="pc-bram",
                input_fn=_inp,
                output_fn=lambda _m: None,
                seed=i + 1,
                max_rounds=1,
                max_retries_per_action=1,
            )
            rejected_sessions += int(bool(transcript["violations"]))
            replayed = replay_match(transcript, self.rules)
            self.assertTrue(replayed["deterministic_match"], f"seed {i + 1}: {replayed['diffs']}")
            self.assertEqual(replayed["original_digest"], replayed["replayed_digest"])
        self.assertGreaterEqual(rejected_sessions, 100)

    def test_match_intent_fuzz_does_not_crash(self) -> None:
        rng = random.Random(7)
        types = ["start_encounter", "wish", "attack", "cast_spell", "reroll", "end_turn", ""]

        class _Fuzz:
            name = "fuzz"

            def __init__(self) -> None:
                self.n = 0

            def reset(self, seed: int | None = None) -> None:
                self.n = 0

            def decide(self, observation: dict[str, Any]) -> AgentDecision:
                self.n += 1
                actor = observation["self"]["id"]
                if self.n > 2:
                    return AgentDecision(intent={"type": "end_turn", "actor_id": actor})
                return AgentDecision(
                    intent={
                        "type": rng.choice(types),
                        "actor_id": rng.choice([actor, "other", None]),
                        "spell": "fireball",
                    }
                )

        for seed in range(8):
            run_match(
                "goblin-ambush",
                party_driver=_Fuzz(),
                enemy_driver=HeuristicTacticalDriver(self.rules),
                rules=self.rules,
                seed=seed,
                max_rounds=1,
                max_retries_per_action=1,
            )

    def test_mutate_env_drops_secrets(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-secret", "PATH": "/usr/bin", "PYTHONHASHSEED": "0"}):
            env = _build_isolated_env(REPO_ROOT / "src", REPO_ROOT)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("sk-test-secret", " ".join(env.values()))
        self.assertIn("PYTHONPATH", env)
        self.assertEqual(env.get("PYTHONHASHSEED"), "0")


if __name__ == "__main__":
    unittest.main()
