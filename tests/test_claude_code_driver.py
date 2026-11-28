"""`claude-code:<model>`: Claude through the Claude Code CLI on a claude.ai (Pro/Max) login, never API billing.

`anthropic:` with an `ant` sign-in bills the Console org's prepaid API credits; the 30-seed bench on 586d7f5 ran
Opus and Sonnet that way until "Your credit balance is too low". These tests use a fake CLI runner: no real calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from typing import Any
from unittest import mock

from srd2024.arena.agents import ClaudeCodeDriver, ProviderUnavailable, RetryableLLMError, create_driver
from srd2024.arena.budget import contender_price

DECISION = json.dumps({"intent": {"type": "end_turn", "actor_id": "pc-a"}, "rationale": "wait"})
MAX_LOGIN = {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}


class FakeCLI:
    def __init__(self, results: list[Any], status: dict[str, Any] | None = None) -> None:
        self.results = list(results)
        self.status = MAX_LOGIN if status is None else status
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv, **kw):
        self.calls.append({"argv": list(argv), **kw})
        if argv[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.status), "")
        res = self.results.pop(0)
        if isinstance(res, BaseException):
            raise res
        if isinstance(res, str):
            return subprocess.CompletedProcess(argv, 1, res, "boom")
        return subprocess.CompletedProcess(argv, 0, json.dumps(res), "")


def ok(text: str = DECISION) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": False, "result": text, "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "cache_creation_input_tokens": 500, "cache_read_input_tokens": 100,
                      "output_tokens": 40}}


def err(text: str, code: int | None = None) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": True, "result": text, "api_error_status": code}


OBS = {"self": {"id": "pc-a"}, "round": 1}


class TestClaudeCodeDriver(unittest.TestCase):
    def _driver(self, fake: FakeCLI) -> ClaudeCodeDriver:
        d = ClaudeCodeDriver("claude-opus-5-5", cli_path="/usr/bin/claude", runner=fake)
        d._sleep = lambda s: None
        return d

    def _decide(self, d: ClaudeCodeDriver):
        with mock.patch("srd2024.arena.agents.build_user_prompt", return_value="state"):
            return d.decide(OBS)

    def test_spec_name_and_price(self) -> None:
        d = create_driver("claude-code:claude-sonnet-5-5")
        self.assertIsInstance(d, ClaudeCodeDriver)
        self.assertEqual(d.name, "claude-code:claude-sonnet-5-5")
        self.assertEqual(contender_price(d.name), (0.0, 0.0))

    def test_one_shot_cli_call_without_tools_settings_or_api_env(self) -> None:
        fake = FakeCLI([ok()])
        env = {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_AUTH_TOKEN": "tok", "ANTHROPIC_BASE_URL": "http://x", "HOME": "/h"}
        with mock.patch.dict(os.environ, env, clear=True):
            dec = self._decide(self._driver(fake))
        self.assertEqual(dec.intent["type"], "end_turn")
        self.assertEqual(dec.usage["prompt_tokens"], 603)
        self.assertEqual(dec.usage["completion_tokens"], 40)
        call = fake.calls[-1]
        argv = call["argv"]
        for flag, val in (("--model", "claude-opus-5-5"), ("--tools", ""), ("--setting-sources", ""),
                          ("--output-format", "json"), ("--effort", "high")):
            self.assertEqual(argv[argv.index(flag) + 1], val)
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("SRD 5.2", argv[argv.index("--system-prompt") + 1])
        self.assertEqual(call["input"], "state")
        for cfg in fake.calls:
            self.assertEqual(set(cfg["env"]) & {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}, set())
        self.assertNotEqual(call["cwd"], os.getcwd())

    def test_refuses_api_key_or_console_logins(self) -> None:
        for status in ({"loggedIn": True, "authMethod": "api_key"}, {"loggedIn": False}, {}):
            fake = FakeCLI([ok()], status=status)
            with self.assertRaises(ProviderUnavailable) as cm:
                self._decide(self._driver(fake))
            self.assertEqual(cm.exception.reason, "unavailable")
            self.assertEqual([c["argv"][1:3] for c in fake.calls], [["auth", "status"]], "no inference call made")

    def test_login_checked_once(self) -> None:
        fake = FakeCLI([ok(), ok()])
        d = self._driver(fake)
        self._decide(d)
        self._decide(d)
        self.assertEqual(sum(c["argv"][1:3] == ["auth", "status"] for c in fake.calls), 1)

    def test_subscription_limit_stops_the_contender(self) -> None:
        for text in ("Claude AI usage limit reached|1795900000", "Your credit balance is too low to access the API."):
            fake = FakeCLI([err(text, 429)])
            with self.assertRaises(ProviderUnavailable) as cm:
                self._decide(self._driver(fake))
            self.assertEqual(cm.exception.reason, "out_of_credits")

    def test_transient_errors_retry_then_succeed(self) -> None:
        fake = FakeCLI([err("Overloaded", 529), subprocess.TimeoutExpired("claude", 300), "not json", ok()])
        dec = self._decide(self._driver(fake))
        self.assertEqual(dec.intent["type"], "end_turn")
        self.assertEqual(sum(c["argv"][1] == "-p" for c in fake.calls), 4)

    def test_other_cli_errors_fail_the_decision(self) -> None:
        fake = FakeCLI([err("invalid model name", 400)])
        with self.assertRaises(RuntimeError) as cm:
            self._decide(self._driver(fake))
        self.assertNotIsInstance(cm.exception, ProviderUnavailable)

    def test_missing_cli_is_unavailable(self) -> None:
        d = ClaudeCodeDriver("claude-opus-5-5", runner=FakeCLI([ok()]))
        with mock.patch("srd2024.arena.agents.shutil.which", return_value=None):
            with self.assertRaises(ProviderUnavailable):
                self._decide(d)


if __name__ == "__main__":
    unittest.main()
