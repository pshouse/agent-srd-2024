"""Stage 3 Verification Tests (§8.2): Drivers, JSON Extractor & Stdlib HTTP LLM Drivers.

Covers:
1. JSON Decision Extractor (extract_decision_json):
   - Raw JSON, ```json ... ``` fenced blocks, generic ``` ... ``` fenced blocks, prose-wrapped JSON.
   - Raises ValueError on non-JSON or missing intent.type.
2. Driver Factory (create_driver):
   - Offline & live driver specs; unknown driver spec raises ValueError.
3. Offline Deterministic Drivers:
   - RandomDriver: 100% deterministic for fixed seed, valid AgentDecision outputs.
   - GreedyDriver: picks highest raw dice damage (e.g., fireball against fire-immune wyrmling-lair enemies).
   - HeuristicTacticalDriver: inspects RulesStore immunities and refuses fire spells vs fire-immune targets,
     revives 0-HP allies with healing-word, casts bless/shield/mage-armor, recovers from prone, emits table_talk.
   - ReplayDriver: replays recorded decisions verbatim and raises RuntimeError when exhausted.
4. End-to-End Local HTTPServer Tests (127.0.0.1:0, zero external network!) for ALL 3 Live LLM Drivers:
   - OpenAICompatDriver: POST /v1/chat/completions, Authorization: Bearer, request JSON, fenced JSON parse, usage.
   - AnthropicDriver: POST /v1/messages, x-api-key + anthropic-version: 2023-06-01, request JSON, text blocks, usage.
   - GeminiDriver: POST /v1beta/models/<model>:generateContent with x-goog-api-key (never the query string), system_instruction + contents, usageMetadata.
   - Clean error handling on HTTP 500 and invalid/non-JSON model responses.
"""

from __future__ import annotations

import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from srd2024.arena import (
    AgentDecision,
    AnthropicDriver,
    GeminiDriver,
    GreedyDriver,
    HeuristicTacticalDriver,
    OpenAICompatDriver,
    RandomDriver,
    ReplayDriver,
    build_observation,
    create_driver,
    extract_decision_json,
    load_scenario,
)
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore


class _MockLLMServer:
    """Real stdlib HTTPServer on 127.0.0.1:0 recording requests and serving scripted responses."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.next_status: int = 200
        self.next_body: Any = {}
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def set_response(self, body: Any, *, status: int = 200) -> None:
        self.next_status = status
        self.next_body = body

    def __enter__(self) -> "_MockLLMServer":
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8") if length > 0 else ""
                try:
                    parsed_body = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    parsed_body = raw
                parent.requests.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": parsed_body,
                    }
                )
                payload = (
                    parent.next_body
                    if isinstance(parent.next_body, str)
                    else json.dumps(parent.next_body)
                ).encode("utf-8")
                self.send_response(parent.next_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    @property
    def base_url(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=3)


class TestStage3JsonExtractorAndFactory(unittest.TestCase):
    """§8.2.1: extract_decision_json & create_driver."""

    def test_extract_decision_json_variants(self) -> None:
        raw = '{"intent": {"type": "attack", "target_id": "goblin-1", "weapon": "longsword"}, "rationale": "Strike", "table_talk": "Go!"}'
        parsed = extract_decision_json(raw)
        self.assertEqual(parsed["intent"]["type"], "attack")
        self.assertEqual(parsed["table_talk"], "Go!")

        fenced_json = f"Sure, here is my tactical choice:\n```json\n{raw}\n```\nGood luck!"
        self.assertEqual(extract_decision_json(fenced_json)["intent"]["target_id"], "goblin-1")

        fenced_generic = f"Decision:\n```\n{raw}\n```"
        self.assertEqual(extract_decision_json(fenced_generic)["intent"]["weapon"], "longsword")

        prose_wrapped = (
            'I think we should attack. {"intent": {"type": "cast_spell", "spell": "magic-missile", '
            '"target_ids": ["wyrmling-1"]}, "rationale": "Avoids \\"fire\\" {immunity} cleanly."} Done.'
        )
        parsed_prose = extract_decision_json(prose_wrapped)
        self.assertEqual(parsed_prose["intent"]["type"], "cast_spell")
        self.assertEqual(parsed_prose["intent"]["spell"], "magic-missile")

    def test_extract_decision_json_invalid_inputs_raise_value_error(self) -> None:
        bad_inputs = [
            "",
            "just plain English with no JSON at all",
            "[1, 2, 3]",
            '{"rationale": "missing intent key"}',
            '{"intent": "not-a-dict"}',
            '{"intent": {"target_id": "goblin-1"}}',
            '{"intent": {"type": 123}}',
        ]
        for text in bad_inputs:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    extract_decision_json(text)

    def test_create_driver_factory(self) -> None:
        rules = RulesStore()
        self.assertIsInstance(create_driver("random", rules=rules, seed=5), RandomDriver)
        self.assertIsInstance(create_driver("greedy", rules=rules), GreedyDriver)
        self.assertIsInstance(create_driver("heuristic-tactical", rules=rules), HeuristicTacticalDriver)
        self.assertIsInstance(
            create_driver("replay", rules=rules, replay_transcript={"actions": []}),
            ReplayDriver,
        )
        self.assertIsInstance(
            create_driver("openai-compat:gpt-4o-mini", api_key="k"),
            OpenAICompatDriver,
        )
        self.assertIsInstance(
            create_driver("anthropic:claude-3-5-sonnet", api_key="k"),
            AnthropicDriver,
        )
        self.assertIsInstance(
            create_driver("gemini:gemini-2.5-flash", api_key="k"),
            GeminiDriver,
        )
        with self.assertRaises(ValueError):
            create_driver("unknown-driver-xyz", rules=rules)


class TestStage3OfflineDrivers(unittest.TestCase):
    """§8.2.2: RandomDriver, GreedyDriver, HeuristicTacticalDriver, ReplayDriver."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.wyrmling_scen = load_scenario("wyrmling-lair", cls.rules)
        cls.crypt_scen = load_scenario("crypt-of-ghouls", cls.rules)
        cls.goblin_scen = load_scenario("goblin-ambush", cls.rules)

    def _start_state(self, scen: dict[str, Any], seed: int = 42) -> dict[str, Any]:
        res = step(None, {"type": "start_encounter", "seed": seed, "combatants": scen["combatants"]}, self.rules)
        self.assertEqual(res["status"], "ok")
        return res["state"]

    def _obs_for_actor(self, state: dict[str, Any], actor_id: str, scen: dict[str, Any]) -> dict[str, Any]:
        st = copy.deepcopy(state)
        st["active_index"] = st["turn_order"].index(actor_id)
        return build_observation(
            st,
            actor_id,
            scen,
            {"party": [], "enemy": []},
            [],
            {},
            self.rules,
        )

    def test_random_driver_determinism(self) -> None:
        state = self._start_state(self.goblin_scen, seed=7)
        actor_id = state["turn_order"][state["active_index"]]
        obs = build_observation(state, actor_id, self.goblin_scen, {"party": [], "enemy": []}, [], {}, self.rules)

        d1 = RandomDriver(seed=123, rules=self.rules)
        d2 = RandomDriver(seed=123, rules=self.rules)
        seq1 = [d1.decide(obs).intent for _ in range(8)]
        seq2 = [d2.decide(obs).intent for _ in range(8)]
        self.assertEqual(seq1, seq2)

        d1.reset(123)
        self.assertEqual(d1.decide(obs).intent, seq1[0])
        dec = d1.decide(obs)
        self.assertIsInstance(dec, AgentDecision)
        self.assertIsInstance(dec.intent.get("type"), str)

    def test_greedy_vs_heuristic_tactical_immunity_awareness_in_wyrmling_lair(self) -> None:
        state = self._start_state(self.wyrmling_scen, seed=42)
        # Ensure pc-lyra already has mage_armor active and is at safe distance (>15ft)
        # so both drivers evaluate offensive spells against the 3 fire-immune enemies.
        lyra = state["combatants"]["pc-lyra"]
        lyra["active_effects"] = [{"type": "mage_armor", "name": "mage_armor", "source": "pc-lyra"}]
        lyra["position"] = {"x": 0, "y": 25}
        obs = self._obs_for_actor(state, "pc-lyra", self.wyrmling_scen)

        greedy = GreedyDriver(rules=self.rules)
        g_dec = greedy.decide(obs)
        self.assertEqual(g_dec.intent["type"], "cast_spell")
        self.assertIn(
            g_dec.intent.get("spell"),
            {"fireball", "scorching-ray", "fire-bolt", "burning-hands"},
            f"GreedyDriver should pick highest raw dice damage (fireball) ignoring fire immunity, got {g_dec.intent}",
        )
        self.assertIsNone(g_dec.table_talk)

        tactical = HeuristicTacticalDriver(rules=self.rules)
        t_dec = tactical.decide(obs)
        self.assertEqual(t_dec.intent["type"], "cast_spell", f"Expected non-fire spell, got {t_dec.intent}")
        self.assertNotIn(
            t_dec.intent.get("spell"),
            {"fireball", "scorching-ray", "fire-bolt", "burning-hands"},
            f"HeuristicTacticalDriver MUST refuse fire spells vs fire-immune wyrmling-lair enemies, got {t_dec.intent}",
        )
        self.assertIn(
            t_dec.intent.get("spell"),
            {"lightning-bolt", "shatter", "magic-missile", "ray-of-frost", "chill-touch"},
        )

    def test_heuristic_tactical_revival_buffs_shield_mage_armor_and_prone(self) -> None:
        tactical = HeuristicTacticalDriver(rules=self.rules)

        # 1. Downed 0-HP ally revival via healing-word + table_talk
        crypt_state = self._start_state(self.crypt_scen, seed=11)
        crypt_state["combatants"]["pc-aldric"]["hp"] = 0
        crypt_state["combatants"]["pc-aldric"]["conditions"] = ["unconscious", "prone"]
        obs_dorian_revive = self._obs_for_actor(crypt_state, "pc-dorian", self.crypt_scen)
        dec_revive = tactical.decide(obs_dorian_revive)
        self.assertEqual(dec_revive.intent["type"], "cast_spell")
        self.assertIn(dec_revive.intent.get("spell"), {"healing-word", "mass-healing-word", "cure-wounds"})
        self.assertTrue(dec_revive.table_talk, "Expected tactical table_talk on ally revival")

        # 2. Round 1 Bless by Cleric when party is healthy
        crypt_healthy = self._start_state(self.crypt_scen, seed=11)
        obs_dorian_bless = self._obs_for_actor(crypt_healthy, "pc-dorian", self.crypt_scen)
        dec_bless = tactical.decide(obs_dorian_bless)
        self.assertEqual(dec_bless.intent["type"], "cast_spell")
        self.assertEqual(dec_bless.intent.get("spell"), "bless")
        self.assertTrue(dec_bless.table_talk)

        # 3. Wizard Shield reaction when enemy is within 15 ft and reaction available
        gob_state = self._start_state(self.goblin_scen, seed=5)
        gob_state["combatants"]["goblin-1"]["position"] = {"x": 5, "y": 15}  # 5 ft from Talia (0, 15)
        obs_talia_shield = self._obs_for_actor(gob_state, "pc-talia", self.goblin_scen)
        dec_shield = tactical.decide(obs_talia_shield)
        self.assertEqual(dec_shield.intent["type"], "cast_spell")
        self.assertEqual(dec_shield.intent.get("spell"), "shield")
        self.assertTrue(dec_shield.intent.get("as_reaction"))

        # 4. Wizard Mage Armor when unarmored, reaction spent/no adjacent enemy, no multi-target AoE kill shot
        gob_far = self._start_state(self.goblin_scen, seed=5)
        gob_far["combatants"]["goblin-1"]["position"] = {"x": 60, "y": 0}
        gob_far["combatants"]["goblin-2"]["position"] = {"x": 60, "y": 50}
        obs_talia_ma = self._obs_for_actor(gob_far, "pc-talia", self.goblin_scen)
        dec_ma = tactical.decide(obs_talia_ma)
        self.assertEqual(dec_ma.intent["type"], "cast_spell")
        self.assertEqual(dec_ma.intent.get("spell"), "mage-armor")

        # 5. Prone recovery
        prone_state = self._start_state(self.goblin_scen, seed=5)
        prone_state["combatants"]["pc-bram"]["conditions"] = ["prone"]
        obs_bram_prone = self._obs_for_actor(prone_state, "pc-bram", self.goblin_scen)
        dec_stand = tactical.decide(obs_bram_prone)
        self.assertEqual(dec_stand.intent["type"], "move")
        self.assertTrue(dec_stand.intent.get("stand_up"))

    def test_replay_driver_verbatim_and_exhaustion(self) -> None:
        transcript = {
            "actions": [
                {
                    "round": 1,
                    "actor_id": "pc-1",
                    "side": "party",
                    "auto_skipped": False,
                    "auto_end_turn": False,
                    "forced_fallback": False,
                    "intent": {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "longsword"},
                    "rationale": "First hit",
                    "table_talk": "Attacking!",
                },
                {
                    "round": 1,
                    "actor_id": "pc-1",
                    "side": "party",
                    "auto_skipped": False,
                    "auto_end_turn": False,
                    "forced_fallback": False,
                    "intent": {"type": "end_turn", "actor_id": "pc-1"},
                    "rationale": "Done",
                    "table_talk": None,
                },
            ]
        }
        driver = ReplayDriver(transcript, side="party")
        obs = {"round": 1, "self": {"id": "pc-1", "side": "party"}}
        d1 = driver.decide(obs)
        self.assertEqual(d1.intent["type"], "attack")
        self.assertEqual(d1.rationale, "First hit")
        self.assertEqual(d1.table_talk, "Attacking!")

        d2 = driver.decide(obs)
        self.assertEqual(d2.intent["type"], "end_turn")

        with self.assertRaises(RuntimeError):
            driver.decide(obs)


class TestStage3LiveLLMDriversOverLocalHTTP(unittest.TestCase):
    """§8.2.3: Real stdlib HTTPServer on 127.0.0.1:0 testing urllib.request end-to-end for all 3 LLM drivers."""

    def _sample_observation(self) -> dict[str, Any]:
        return {
            "round": 1,
            "scenario": {"id": "goblin-ambush", "name": "Goblin Ambush", "description": "Test"},
            "self": {"id": "pc-bram", "name": "Bram Ironhand", "side": "party", "hp": 12, "hp_max": 12},
            "allies": [],
            "enemies": [{"id": "goblin-1", "name": "Goblin Scout A", "hp_band": "unharmed", "distance_ft": 5}],
            "legal_actions": {"active_actor_id": "pc-bram", "actions": ["attack", "end_turn"], "weapons": ["longsword"]},
            "party_blackboard": [],
            "recent_events": [],
            "retry_feedback": None,
        }

    def test_openai_compat_driver_end_to_end(self) -> None:
        fenced_content = (
            "Here is Bram's tactical action:\n```json\n"
            + json.dumps(
                {
                    "intent": {"type": "attack", "actor_id": "pc-bram", "target_id": "goblin-1", "weapon": "longsword"},
                    "rationale": "Apply Sap mastery.",
                    "table_talk": "Engaging Goblin Scout A!",
                }
            )
            + "\n```"
        )
        with _MockLLMServer() as srv:
            srv.set_response(
                {
                    "id": "chatcmpl-test",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": fenced_content}}],
                    "usage": {"prompt_tokens": 111, "completion_tokens": 29, "total_tokens": 140},
                }
            )
            driver = OpenAICompatDriver(
                model="gpt-4o-mini-test",
                api_base=f"{srv.base_url}/v1",
                api_key="sk-test-123",
                temperature=0.0,
            )
            dec = driver.decide(self._sample_observation())
            self.assertEqual(dec.intent["type"], "attack")
            self.assertEqual(dec.intent["weapon"], "longsword")
            self.assertEqual(dec.rationale, "Apply Sap mastery.")
            self.assertEqual(dec.table_talk, "Engaging Goblin Scout A!")
            self.assertEqual(dec.usage, {"prompt_tokens": 111, "completion_tokens": 29, "total_tokens": 140})

            self.assertEqual(len(srv.requests), 1)
            req = srv.requests[0]
            self.assertEqual(req["path"], "/v1/chat/completions")
            self.assertEqual(req["headers"].get("authorization"), "Bearer sk-test-123")
            self.assertEqual(req["body"]["model"], "gpt-4o-mini-test")
            self.assertEqual(len(req["body"]["messages"]), 2)
            self.assertEqual(req["body"]["messages"][0]["role"], "system")
            self.assertEqual(req["body"]["messages"][1]["role"], "user")

            # Also verify localhost works with empty API key (omits Authorization header)
            driver_local = OpenAICompatDriver(
                model="llama3-local",
                api_base=f"{srv.base_url}/v1",
                api_key="",
            )
            dec_local = driver_local.decide(self._sample_observation())
            self.assertEqual(dec_local.intent["type"], "attack")
            self.assertNotIn("authorization", srv.requests[1]["headers"])

    def test_anthropic_driver_end_to_end(self) -> None:
        decision_json = json.dumps(
            {
                "intent": {"type": "dodge", "actor_id": "pc-bram"},
                "rationale": "Hold the choke point.",
                "table_talk": "Holding line!",
            }
        )
        with _MockLLMServer() as srv:
            srv.set_response(
                {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": decision_json}],
                    "usage": {"input_tokens": 200, "output_tokens": 45},
                }
            )
            driver = AnthropicDriver(
                model="claude-3-5-sonnet-test",
                api_base=srv.base_url,
                api_key="anth-key-456",
            )
            dec = driver.decide(self._sample_observation())
            self.assertEqual(dec.intent, {"type": "dodge", "actor_id": "pc-bram"})
            self.assertEqual(dec.table_talk, "Holding line!")
            self.assertEqual(dec.usage, {"prompt_tokens": 200, "completion_tokens": 45, "total_tokens": 245})

            self.assertEqual(len(srv.requests), 1)
            req = srv.requests[0]
            self.assertEqual(req["path"], "/v1/messages")
            self.assertEqual(req["headers"].get("x-api-key"), "anth-key-456")
            self.assertEqual(req["headers"].get("anthropic-version"), "2023-06-01")
            self.assertEqual(req["body"]["model"], "claude-3-5-sonnet-test")
            self.assertIn("system", req["body"])
            self.assertIn("messages", req["body"])

    def test_gemini_driver_end_to_end(self) -> None:
        decision_json = json.dumps(
            {
                "intent": {"type": "attack", "actor_id": "pc-bram", "target_id": "goblin-1", "weapon": "longsword"},
                "rationale": "Press melee advantage.",
                "table_talk": "Focus Goblin Scout A!",
            }
        )
        with _MockLLMServer() as srv:
            srv.set_response(
                {
                    "candidates": [{"content": {"parts": [{"text": decision_json}]}}],
                    "usageMetadata": {
                        "promptTokenCount": 150,
                        "candidatesTokenCount": 35,
                        "totalTokenCount": 185,
                    },
                }
            )
            driver = GeminiDriver(
                model="gemini-2.5-flash-test",
                api_base=srv.base_url,
                api_key="gem-key-789",
            )
            dec = driver.decide(self._sample_observation())
            self.assertEqual(dec.intent["type"], "attack")
            self.assertEqual(dec.rationale, "Press melee advantage.")
            self.assertEqual(dec.usage, {"prompt_tokens": 150, "completion_tokens": 35, "total_tokens": 185})

            self.assertEqual(len(srv.requests), 1)
            req = srv.requests[0]
            self.assertEqual(req["path"], "/v1beta/models/gemini-2.5-flash-test:generateContent")
            self.assertNotIn("key=", req["path"])
            self.assertNotIn("gem-key-789", req["path"])
            self.assertEqual(req["headers"].get("x-goog-api-key"), "gem-key-789")
            self.assertIn("system_instruction", req["body"])
            self.assertIn("contents", req["body"])

    def test_llm_drivers_error_handling_http_500_and_invalid_json(self) -> None:
        with _MockLLMServer() as srv:
            # HTTP 500 -> clean RuntimeError mentioning status 500
            srv.set_response({"error": "upstream failure"}, status=500)
            for drv in (
                OpenAICompatDriver(model="m", api_base=f"{srv.base_url}/v1", api_key="k"),
                AnthropicDriver(model="m", api_base=srv.base_url, api_key="k"),
                GeminiDriver(model="m", api_base=srv.base_url, api_key="k"),
            ):
                with self.subTest(driver=drv.name, case="http_500"):
                    with self.assertRaises(RuntimeError) as ctx:
                        drv.decide(self._sample_observation())
                    self.assertIn("500", str(ctx.exception))

            # Non-JSON model text -> ValueError from extract_decision_json
            srv.set_response(
                {
                    "choices": [{"message": {"content": "I am sorry, I cannot output JSON right now."}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
                status=200,
            )
            oa = OpenAICompatDriver(model="m", api_base=f"{srv.base_url}/v1", api_key="k")
            with self.assertRaises(ValueError):
                oa.decide(self._sample_observation())


if __name__ == "__main__":
    unittest.main()
