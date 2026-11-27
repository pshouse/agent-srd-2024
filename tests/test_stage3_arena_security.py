"""Security fixes for the Stage 3 arena (M3, M4, S2–S4, S6).

No network beyond 127.0.0.1. Property checks cover the decision parser and
MCP arena arguments, both of which accept untrusted model or client input.
"""

from __future__ import annotations

import json
import random
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest import mock

import srd2024
from srd2024.arena.agents import (
    GeminiDriver,
    HeuristicTacticalDriver,
    OpenAICompatDriver,
    extract_decision_json,
    scrub_secrets,
)
from srd2024.arena.render import render_match_markdown
from srd2024.arena.runner import run_match
from srd2024.engine.store import RulesStore
from srd2024.runtime.engine import SRDStore
from srd2024.runtime.mcp import (
    SERVER_VERSION,
    SRDMCPServer,
    _MCP_MAX_LINE_CHARS,
)
from tests.helpers import PYPROJECT_PATH, README_PATH, SKILL_PATH

_FAKE_KEY = "gem-key-789"
_NOTICE = (
    "Experimental: arena scoring and isolation fixes are in progress. "
    "Do not run the arena or srd-mcp --arena with a model API key set in your environment."
)


def _obs() -> dict[str, Any]:
    return {"self": {"id": "pc-1"}, "legal_actions": {"active_actor_id": "pc-1"}}


class _ScriptedServer:
    """Loopback server. Records requests and can answer 500, 302, or a JSON body."""

    def __init__(self, *, status: int = 200, body: bytes = b"{}", location: str | None = None) -> None:
        self.status = status
        self.body = body
        self.location = location
        self.requests: list[dict[str, Any]] = []
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "_ScriptedServer":
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                parent.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": raw.decode("utf-8", errors="replace"),
                    }
                )
                self.send_response(parent.status)
                if parent.location:
                    self.send_header("Location", parent.location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(parent.body)))
                self.end_headers()
                self.wfile.write(parent.body)

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


class TestGeminiKeyDoesNotLeak(unittest.TestCase):
    def test_key_stays_in_header_and_out_of_errors_transcript_and_prompts(self) -> None:
        rules = RulesStore()
        error_body = json.dumps({"error": f"upstream {_FAKE_KEY}"}).encode("utf-8")
        with _ScriptedServer(status=500, body=error_body) as srv:
            driver = GeminiDriver(model="gemini-test", api_base=srv.base_url, api_key=_FAKE_KEY)
            transcript = run_match(
                "goblin-ambush",
                party_driver=driver,
                enemy_driver=HeuristicTacticalDriver(rules),
                rules=rules,
                seed=1,
                max_rounds=1,
                max_actions_per_turn=1,
                max_retries_per_action=1,
            )
            self.assertGreaterEqual(len(srv.requests), 2)
            for req in srv.requests:
                self.assertNotIn(_FAKE_KEY, req["path"])
                self.assertNotIn("key=", req["path"])
                self.assertEqual(req["headers"].get("x-goog-api-key"), _FAKE_KEY)
                self.assertNotIn(_FAKE_KEY, req["body"])
            blob = json.dumps(transcript)
            markdown = render_match_markdown(transcript)
            self.assertNotIn(_FAKE_KEY, blob)
            self.assertNotIn(_FAKE_KEY, markdown)
            self.assertNotIn(srv.base_url, blob)

    def test_http_500_message_has_no_url_or_body(self) -> None:
        with _ScriptedServer(status=500, body=f"leak {_FAKE_KEY}".encode()) as srv:
            driver = GeminiDriver(model="gemini-test", api_base=srv.base_url, api_key=_FAKE_KEY)
            with self.assertRaises(RuntimeError) as ctx:
                driver.decide(_obs())
            text = str(ctx.exception)
            self.assertIn("500", text)
            self.assertNotIn(_FAKE_KEY, text)
            self.assertNotIn(srv.base_url, text)
            self.assertNotIn("leak", text)

    def test_plain_http_off_loopback_is_refused_before_connect(self) -> None:
        called = {"n": 0}

        def _transport(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            called["n"] += 1
            raise AssertionError("transport must not run")

        driver = OpenAICompatDriver(
            model="gpt-x",
            api_base="http://example.com/v1",
            api_key=_FAKE_KEY,
            http_transport=_transport,
        )
        with self.assertRaises(RuntimeError) as ctx:
            driver.decide(_obs())
        self.assertIn("https", str(ctx.exception).lower())
        self.assertNotIn(_FAKE_KEY, str(ctx.exception))
        self.assertEqual(called["n"], 0)

    def test_query_string_key_is_refused(self) -> None:
        called = {"n": 0}

        def _transport(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            called["n"] += 1
            raise AssertionError("transport must not run")

        driver = GeminiDriver(
            model="gemini-test",
            api_base=f"https://generativelanguage.googleapis.com?key={_FAKE_KEY}",
            api_key=_FAKE_KEY,
            http_transport=_transport,
        )
        with self.assertRaises(RuntimeError) as ctx:
            driver.decide(_obs())
        self.assertNotIn(_FAKE_KEY, str(ctx.exception))
        self.assertEqual(called["n"], 0)

    def test_cross_host_redirect_is_refused(self) -> None:
        with _ScriptedServer(status=200, body=b'{"ok":true}') as sink:
            sink_port = sink.server.server_address[1]
            location = f"http://localhost:{sink_port}/stolen"
            with _ScriptedServer(status=302, body=b"", location=location) as origin:
                driver = OpenAICompatDriver(
                    model="gpt-x",
                    api_base=origin.base_url + "/v1",
                    api_key=_FAKE_KEY,
                )
                with self.assertRaises(RuntimeError) as ctx:
                    driver.decide(_obs())
                self.assertIn("redirect", str(ctx.exception).lower())
                self.assertNotIn(_FAKE_KEY, str(ctx.exception))
                self.assertNotIn(sink.base_url, str(ctx.exception))
                self.assertEqual(sink.requests, [])
                self.assertEqual(len(origin.requests), 1)
                self.assertNotIn(_FAKE_KEY, origin.requests[0]["path"])

    def test_oversized_body_is_rejected_without_echoing_it(self) -> None:
        payload = b'{"k":"' + _FAKE_KEY.encode() + (b"x" * (1024 * 1024)) + b'"}'
        self.assertGreater(len(payload), 1024 * 1024)
        with _ScriptedServer(status=200, body=payload) as srv:
            driver = OpenAICompatDriver(model="gpt-x", api_base=srv.base_url + "/v1", api_key=_FAKE_KEY)
            with self.assertRaises(RuntimeError) as ctx:
                driver.decide(_obs())
            self.assertIn("1MB", str(ctx.exception))
            self.assertNotIn(_FAKE_KEY, str(ctx.exception))


class TestDecisionParserBounds(unittest.TestCase):
    def test_unbalanced_braces_stay_fast(self) -> None:
        text = "{" * 16000
        started = time.perf_counter()
        with self.assertRaises(ValueError):
            extract_decision_json(text)
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_deep_nesting_is_value_error_not_recursion(self) -> None:
        inner = '{"type":"end_turn"}'
        for _ in range(40):
            inner = '{"x":' + inner + "}"
        text = '{"intent":' + inner + "}"
        with self.assertRaises(ValueError):
            extract_decision_json(text)

    def test_random_inputs_do_not_escape_value_error(self) -> None:
        rng = random.Random(20241127)
        alphabet = list('{}[]":, \n\t012abcintenttype')
        for _ in range(200):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 180)))
            try:
                parsed = extract_decision_json(text)
            except ValueError:
                continue
            self.assertIsInstance(parsed, dict)
            self.assertIn("intent", parsed)

    def test_scrub_removes_explicit_secret_and_bearer(self) -> None:
        raw = f"Authorization: Bearer {_FAKE_KEY} and key={_FAKE_KEY}"
        cleaned = scrub_secrets(raw, [_FAKE_KEY])
        self.assertNotIn(_FAKE_KEY, cleaned)
        self.assertIn("[REDACTED]", cleaned)


class TestArenaMcpGate(unittest.TestCase):
    def _server(self, *, arena: bool, allow_llm: bool = False) -> tuple[Any, SRDMCPServer]:
        store = SRDStore()
        server = SRDMCPServer(store, expose_arena_tools=arena, allow_arena_llm=allow_llm)
        return store, server

    def _call(self, server: SRDMCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        resp = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        self.assertIsInstance(resp, dict)
        return resp

    def test_arena_tools_unknown_without_flag(self) -> None:
        store, server = self._server(arena=False)
        try:
            for name in ("srd_arena_scenarios", "srd_arena_run", "srd_arena_bench"):
                resp = self._call(server, name, {"scenario": "goblin-ambush", "format": "json"})
                self.assertNotIn("error", resp)
                self.assertTrue(resp["result"]["isError"])
                self.assertIn("Unknown tool", resp["result"]["content"][0]["text"])
            read = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": "srd://arena/scenarios"},
                }
            )
            self.assertIn("error", read)
            self.assertIn("Unknown resource", read["error"]["message"])
        finally:
            store.close()

    def test_paths_are_rejected_and_not_opened(self) -> None:
        store, server = self._server(arena=True)
        try:
            for scenario in ("/etc/passwd", "/etc/shadow", "../../etc/passwd", "/tmp/secret.json", "C:\\Windows\\system32"):
                resp = self._call(server, "srd_arena_run", {"scenario": scenario, "format": "json"})
                text = resp["result"]["content"][0]["text"]
                self.assertTrue(resp["result"]["isError"], text)
                self.assertIn("filesystem path", text)
                self.assertNotIn("Permission denied", text)
                self.assertNotIn("No such file", text)
                self.assertNotIn("passwd", text)
                self.assertNotIn("shadow", text)
        finally:
            store.close()

    def test_llm_driver_requires_opt_in_and_does_not_connect(self) -> None:
        store, server = self._server(arena=True, allow_llm=False)

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("MCP attempted a network call")

        try:
            with mock.patch("socket.create_connection", _boom), mock.patch("socket.getaddrinfo", _boom):
                resp = self._call(
                    server,
                    "srd_arena_run",
                    {
                        "scenario": "goblin-ambush",
                        "party_agent": "openai-compat:gpt-x",
                        "format": "json",
                    },
                )
            text = resp["result"]["content"][0]["text"]
            self.assertTrue(resp["result"]["isError"], text)
            self.assertIn("arena-allow-llm", text)
        finally:
            store.close()

    def test_json_caps_reject_without_echoing_the_payload(self) -> None:
        store, server = self._server(arena=True)
        try:
            huge = "A" * 300
            resp = self._call(server, "srd_arena_run", {"scenario": huge, "format": "json"})
            text = resp["result"]["content"][0]["text"]
            self.assertTrue(resp["result"]["isError"])
            self.assertIn("256", text)
            self.assertNotIn(huge, text)

            inline = "{" * 80
            resp = self._call(server, "srd_arena_run", {"scenario": inline, "format": "json"})
            text = resp["result"]["content"][0]["text"]
            self.assertTrue(resp["result"]["isError"])
            self.assertNotIn(inline, text)

            resp = self._call(
                server,
                "srd_arena_bench",
                {"scenarios": ["goblin-ambush"], "seeds": list(range(11)), "format": "json"},
            )
            self.assertTrue(resp["result"]["isError"])
            self.assertIn("seeds", resp["result"]["content"][0]["text"])

            resp = self._call(
                server,
                "srd_arena_run",
                {"scenario": "goblin-ambush", "max_rounds": 51, "format": "json"},
            )
            self.assertTrue(resp["result"]["isError"])
            self.assertIn("max_rounds", resp["result"]["content"][0]["text"])
        finally:
            store.close()

    def test_stdio_line_cap(self) -> None:
        store, server = self._server(arena=False)
        try:
            from io import StringIO

            incoming = StringIO("x" * (_MCP_MAX_LINE_CHARS + 8) + "\n")
            outgoing = StringIO()
            server.serve_stdio(incoming, outgoing)
            parsed = json.loads(outgoing.getvalue().splitlines()[0])
            self.assertEqual(parsed["error"]["code"], -32700)
            self.assertNotIn("x" * 100, parsed["error"]["message"])
        finally:
            store.close()

    def test_random_arena_arguments_stay_well_formed(self) -> None:
        rng = random.Random(11)
        store, server = self._server(arena=True, allow_llm=False)
        samples: list[Any] = [
            None,
            1,
            True,
            "",
            "/etc/passwd",
            "../secret",
            "not-a-scenario",
            "{",
            "{" * 40,
            "A" * 400,
            ["goblin-ambush"],
            {"nested": 1},
        ]
        try:
            for i in range(60):
                name = rng.choice(["srd_arena_scenarios", "srd_arena_run", "srd_arena_bench", "nope"])
                arguments: Any
                kind = rng.randrange(4)
                if kind == 0:
                    arguments = rng.choice(samples)
                elif kind == 1:
                    arguments = {"scenario": rng.choice(samples), "format": rng.choice(["json", "markdown", 1, None])}
                elif kind == 2:
                    arguments = {
                        "scenario": rng.choice(samples),
                        "party_agent": rng.choice(["heuristic-tactical", "openai-compat:gpt-x", 5, ""]),
                        "seeds": rng.choice([list(range(11)), ["nope"], [1, 2], 3]),
                        "max_rounds": rng.choice([0, 51, -1, 2, "x"]),
                        "format": "json",
                    }
                else:
                    arguments = {f"k{j}": rng.choice(samples) for j in range(rng.randint(0, 4))}
                resp = server.handle_message(
                    {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
                )
                self.assertIsInstance(resp, dict)
                self.assertEqual(resp.get("jsonrpc"), "2.0")
                self.assertTrue("result" in resp or "error" in resp)
                if "result" in resp:
                    self.assertIn("content", resp["result"])
        finally:
            store.close()


class TestVersionAndExperimentalNotice(unittest.TestCase):
    def test_versions_match_0_3_1(self) -> None:
        pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")
        self.assertIn('version = "0.3.1"', pyproject)
        self.assertEqual(srd2024.__version__, "0.3.1")
        self.assertEqual(SERVER_VERSION, "0.3.1")

    def test_experimental_notice(self) -> None:
        self.assertIn(_NOTICE, README_PATH.read_text(encoding="utf-8"))
        self.assertIn(_NOTICE, SKILL_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
