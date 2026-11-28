"""`openai:`, `xai:` and Gemini request rules for current models, checked with a fake transport (no network).

Live probes (2026-11-27) found:
- gpt-6.2-astra rejects `max_tokens` (use `max_completion_tokens`) and any non-default `temperature`.
- grok-4.7 reasoning tokens count toward `max_tokens`, and its turns can run past 60 s.
- gemini-4-neon spent a 4096 output cap on thinking (finishReason MAX_TOKENS), returns thought parts, and timed out
  at 60 s.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from srd2024.arena import build_observation, load_scenario
from srd2024.arena.agents import GeminiDriver, OpenAICompatDriver, OpenAIDriver, XAIDriver, create_driver
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

_DECISION = json.dumps({"intent": {"type": "end_turn", "actor_id": "pc-bram"}, "rationale": "r", "table_talk": None})


class _Capture:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        return self.response


def _chat_response() -> dict[str, Any]:
    return {"choices": [{"message": {"content": _DECISION}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class TestProviderDrivers(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rules = RulesStore()
        sc = load_scenario("goblin-ambush", rules)
        st = step(None, {"type": "start_encounter", "seed": 1, "combatants": sc["combatants"]}, rules)["state"]
        st["active_index"] = st["turn_order"].index("pc-bram")
        cls.obs = build_observation(st, "pc-bram", sc, {}, [], {}, rules)

    def test_specs(self) -> None:
        self.assertIsInstance(create_driver("openai:gpt-6.2-astra", http_transport=_Capture({})), OpenAIDriver)
        self.assertIsInstance(create_driver("xai:grok-4.7", http_transport=_Capture({})), XAIDriver)
        self.assertIsInstance(create_driver("gemini:gemini-4-neon", http_transport=_Capture({})), GeminiDriver)
        self.assertIsInstance(create_driver("openai-compat:llama3", http_transport=_Capture({})), OpenAICompatDriver)

    def test_openai_sends_max_completion_tokens_and_no_temperature(self) -> None:
        cap = _Capture(_chat_response())
        d = create_driver("openai:gpt-6.2-astra", api_key="k", http_transport=cap, temperature=0.0, max_tokens=512)
        self.assertEqual(d.decide(self.obs).intent["type"], "end_turn")
        url, headers, payload, _ = cap.calls[0]
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer k")
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("temperature", payload)
        self.assertGreaterEqual(payload["max_completion_tokens"], 4096)

    def test_openai_compat_is_unchanged(self) -> None:
        cap = _Capture(_chat_response())
        create_driver("openai-compat:llama3", api_base="http://127.0.0.1:11434/v1", http_transport=cap, max_tokens=512).decide(self.obs)
        payload = cap.calls[0][2]
        self.assertEqual(payload["max_tokens"], 512)
        self.assertIn("temperature", payload)

    def test_xai_defaults_floor_and_timeout(self) -> None:
        cap = _Capture(_chat_response())
        d = create_driver("xai:grok-4.7", api_key="x", http_transport=cap, max_tokens=512)
        d.decide(self.obs)
        url, headers, payload, timeout = cap.calls[0]
        self.assertEqual(url, "https://api.x.ai/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer x")
        self.assertGreaterEqual(payload["max_tokens"], 4096)
        self.assertGreaterEqual(timeout, 180)
        self.assertEqual(d.api_key_env, "XAI_API_KEY")

    def test_gemini_floor_timeout_and_thought_parts(self) -> None:
        resp = {
            "candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": _DECISION}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
        }
        cap = _Capture(resp)
        d = create_driver("gemini:gemini-4-neon", api_key="g", http_transport=cap, max_tokens=512)
        self.assertEqual(d.decide(self.obs).intent["type"], "end_turn")
        _, headers, payload, timeout = cap.calls[0]
        self.assertEqual(headers["x-goog-api-key"], "g")
        self.assertGreaterEqual(payload["generationConfig"]["maxOutputTokens"], 16384)
        self.assertGreaterEqual(timeout, 180)

    def test_gemini_names_an_empty_max_tokens_reply(self) -> None:
        cap = _Capture({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]})
        d = create_driver("gemini:gemini-4-neon", api_key="g", http_transport=cap)
        with self.assertRaises(ValueError) as ctx:
            d.decide(self.obs)
        self.assertIn("MAX_TOKENS", str(ctx.exception))

    def test_mcp_treats_new_specs_as_paid(self) -> None:
        from srd2024.runtime.mcp import _check_mcp_agent_spec

        for spec in ("openai:gpt-6.2-astra", "xai:grok-4.7"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                _check_mcp_agent_spec(spec, allow_llm=False, rules=RulesStore())


if __name__ == "__main__":
    unittest.main()
