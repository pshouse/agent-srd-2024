"""Milestone 5 Track 2: local browser tabletop (`bin/srd-web`, `srd2024.arena.web_server`).

Covers the HTTP API and its security model (loopback bind, bearer token, Host /
Origin / fetch-metadata checks, JSON-only bounded bodies, static allowlist),
strict menu validation, terminal parity of traces played over HTTP, deterministic
replay, save / quit / resume, NL mode, and the CLI's overwrite rule.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from srd2024.arena import OpenAICompatDriver, replay_match, run_interactive_session
from srd2024.arena.web_server import (
    MAX_BODY_BYTES,
    TabletopConfig,
    TabletopServer,
    TabletopSession,
    build_parser,
    config_from_args,
    main,
)
from srd2024.engine.store import RulesStore
from tests.helpers import REPO_ROOT

BIN_WEB = REPO_ROOT / "bin" / "srd-web"


class _Live:
    """One server on 127.0.0.1:0 in a background thread, plus a tiny client."""

    def __init__(self, config: TabletopConfig, rules: RulesStore, nl_driver: Any = None) -> None:
        self.session = TabletopSession(config, rules=rules, nl_driver=nl_driver)
        self.server = TabletopServer(self.session, port=0)
        self.port = self.server.port
        self.token = self.server.token
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.session.shutdown()
        self.server.shutdown()
        self.server.server_close()

    def raw(self, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        hdrs.update(headers or {})
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in hdrs.items():
            if v is not None:
                conn.putheader(k, v)
        if body is not None and "Content-Length" not in hdrs:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body is not None:
            conn.send(body)
        resp = conn.getresponse()
        data = resp.read()
        out = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
        conn.close()
        return out

    def api(self, method: str, path: str, payload: Any = None, **extra: str) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {self.token}", "Origin": self.origin}
        body = None
        if method == "POST":
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload if payload is not None else {}).encode()
        headers.update(extra)
        status, _, data = self.raw(method, path, body=body, headers=headers)
        return status, json.loads(data.decode() or "null")

    def state(self) -> dict[str, Any]:
        status, data = self.api("GET", "/api/state")
        assert status == 200, data
        return data


def _goblin(**kw: Any) -> TabletopConfig:
    base: dict[str, Any] = {"scenario": "goblin-ambush", "play_as": "pc-bram", "seed": 42}
    base.update(kw)
    return TabletopConfig(**base)


class TestWebTabletopSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def setUp(self) -> None:
        self.live = _Live(_goblin(), self.rules)

    def tearDown(self) -> None:
        self.live.close()

    def test_binds_loopback_only_and_refuses_other_hosts(self) -> None:
        self.assertEqual(self.live.server.server_address[0], "127.0.0.1")
        for host in ("0.0.0.0", "localhost", "::", "192.168.1.5"):
            with self.assertRaises(ValueError):
                TabletopServer(self.live.session, host=host, port=0)
        args = build_parser().parse_args(["goblin-ambush", "--host", "0.0.0.0"])
        with self.assertRaises(ValueError):
            config_from_args(args)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["goblin-ambush", "--host", "0.0.0.0"]), 2)

    def test_missing_or_wrong_token(self) -> None:
        status, headers, _ = self.live.raw("GET", "/api/state")
        self.assertEqual(status, 401)
        self.assertEqual(headers.get("www-authenticate"), "Bearer")
        status, _, _ = self.live.raw("GET", "/api/state", headers={"Authorization": "Bearer " + "x" * 43})
        self.assertEqual(status, 403)
        status, _, _ = self.live.raw("GET", "/api/state", headers={"Authorization": f"Basic {self.live.token}"})
        self.assertEqual(status, 403)
        status, _ = self.live.api("POST", "/api/start", {}, Authorization="Bearer nope")
        self.assertEqual(status, 403)
        self.assertEqual(self.live.session.status, "ready")

    def test_host_origin_and_fetch_metadata_checks(self) -> None:
        auth = {"Authorization": f"Bearer {self.live.token}"}
        for host in ("evil.example", f"evil.example:{self.live.port}", "127.0.0.1", f"127.0.0.1:{self.live.port + 1}"):
            status, _, _ = self.live.raw("GET", "/api/state", headers={**auth, "Host": host})
            self.assertEqual(status, 403, host)
            status, _, _ = self.live.raw("GET", "/", headers={"Host": host})
            self.assertEqual(status, 403, host)
        status, _, _ = self.live.raw("GET", "/api/state", headers={**auth, "Host": f"localhost:{self.live.port}"})
        self.assertEqual(status, 200)
        # A POST needs a matching Origin.
        for origin in (None, "http://evil.example", f"http://127.0.0.1:{self.live.port + 1}", "null"):
            headers = {**auth, "Content-Type": "application/json", "Origin": origin}
            status, _, _ = self.live.raw("POST", "/api/start", body=b"{}", headers=headers)
            self.assertEqual(status, 403, origin)
        status, _, _ = self.live.raw("GET", "/api/state", headers={**auth, "Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.live.raw("GET", "/api/state", headers={**auth, "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, _, _ = self.live.raw("GET", "/api/state", headers={**auth, "Sec-Fetch-Site": "same-site"})
        self.assertEqual(status, 403)
        self.assertEqual(self.live.session.status, "ready")

    def test_json_only_bounded_bodies(self) -> None:
        base = {"Authorization": f"Bearer {self.live.token}", "Origin": self.live.origin}
        status, _, _ = self.live.raw("POST", "/api/start", body=b"{}", headers={**base, "Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _, _ = self.live.raw("POST", "/api/start", body=b"kind=menu", headers={**base, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        status, _, _ = self.live.raw("POST", "/api/start", body=b"{nope", headers={**base, "Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _, _ = self.live.raw("POST", "/api/start", body=b"[1]", headers={**base, "Content-Type": "application/json"})
        self.assertEqual(status, 400)
        big = json.dumps({"kind": "say", "text": "a" * (MAX_BODY_BYTES + 10)}).encode()
        status, _, _ = self.live.raw("POST", "/api/action", body=big, headers={**base, "Content-Type": "application/json"})
        self.assertEqual(status, 413)
        status, _, _ = self.live.raw("POST", "/api/start", body=None, headers={**base, "Content-Type": "application/json"})
        self.assertEqual(status, 411)
        status, data = self.live.api("POST", "/api/start", {"scenario": "wyrmling-lair"})
        self.assertEqual(status, 400, data)
        self.assertEqual(self.live.session.status, "ready")

    def test_static_allowlist_headers_and_no_traversal(self) -> None:
        status, headers, body = self.live.raw("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["referrer-policy"], "no-referrer")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertNotIn(self.live.token.encode(), body)
        for path in ("/tabletop.js", "/tabletop-model.js", "/lib/board-view.js", "/lib/replay-model.js", "/styles.css", "/tabletop.css"):
            status, headers, _ = self.live.raw("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn("nosniff", headers["x-content-type-options"])
        for path in (
            "/../README.md", "/lib/../../README.md", "/%2e%2e/README.md", "/..%2fREADME.md", "/web/app.js", "/app.js",
            "/lib/app.js", "//etc/passwd", "/tabletop/index.html", "/dist/rules.json", "/index.html/", "/lib/", "/.git/config",
        ):
            status, _, body = self.live.raw("GET", path)
            self.assertEqual(status, 404, path)
        status, _, _ = self.live.raw("PUT", "/", body=b"{}", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 405)

    def test_unknown_endpoints_actions_and_strict_validation(self) -> None:
        self.assertEqual(self.live.api("GET", "/api/nope")[0], 404)
        self.assertEqual(self.live.api("POST", "/api/nope", {})[0], 404)
        self.assertEqual(self.live.api("GET", "/api/action")[0], 405)
        self.assertEqual(self.live.api("GET", "/api/state?since=-1")[0], 400)
        # Nothing to act on before the session starts.
        self.assertEqual(self.live.api("POST", "/api/action", {"kind": "end_turn"})[0], 409)
        self.assertEqual(self.live.api("GET", "/api/trace")[0], 409)
        status, data = self.live.api("POST", "/api/start", {})
        self.assertEqual(status, 200, data)
        state = data["state"]
        self.assertEqual(state["status"], "awaiting_input")
        n = len(state["menu"])
        refused = [
            ({"kind": "bogus"}, 400),
            ({"kind": "menu", "index": 0}, 422),
            ({"kind": "menu", "index": n + 1}, 422),
            ({"kind": "menu", "index": True}, 422),
            ({"kind": "menu", "index": "1"}, 422),
            ({"kind": "menu", "index": 1, "extra": 1}, 400),
            ({"kind": "move", "x": 7, "y": 0}, 422),
            ({"kind": "move", "x": 5.0, "y": 0}, 400),
            ({"kind": "move", "x": 100000, "y": 0}, 422),
            ({"kind": "reroll"}, 422),
            ({"kind": "stand"}, 422),
            ({"kind": "say", "text": ""}, 400),
            ({"kind": "say", "text": "x" * 301}, 400),
            ({"kind": "attack", "target_id": "pc-talia"}, 422),
            ({"kind": "attack"}, 422),
            ({"kind": "intent", "intent": {"type": "attack", "actor_id": "goblin-1", "target_id": "pc-bram"}}, 422),
            ({"kind": "intent", "intent": {"type": "start_encounter", "seed": 1}}, 400),
            ({"kind": "intent", "intent": "end_turn"}, 400),
            ({"kind": "menu", "index": 1, "decision": state["decision"] + 1}, 409),
            ({"kind": "menu", "index": 1, "decision": "1"}, 400),
        ]
        for body, code in refused:
            got, payload = self.live.api("POST", "/api/action", body)
            self.assertEqual(got, code, (body, payload))
        self.assertEqual(self.live.api("POST", "/api/nl", {"text": "attack"})[0], 404)
        self.assertEqual(self.live.api("POST", "/api/save", {})[0], 409)
        # The table is untouched by every refusal.
        after = self.live.state()
        self.assertEqual(after["decision"], state["decision"])
        self.assertEqual(after["menu"], state["menu"])

    def test_engine_rejection_is_reported_and_view_hides_enemy_numbers(self) -> None:
        state = self.live.api("POST", "/api/start", {})[1]["state"]
        for enemy in state["view"]["enemies"]:
            self.assertNotIn("hp", enemy)
            self.assertNotIn("ac", enemy)
            self.assertNotIn("hp_max", enemy)
        status, data = self.live.api("POST", "/api/action", {"kind": "move", "x": 200, "y": 0})
        self.assertEqual(status, 422)
        self.assertFalse(data["ok"])
        self.assertIn("Insufficient movement", data["error"])
        self.assertEqual(data["state"]["status"], "awaiting_input")
        self.assertNotIn(self.live.token, json.dumps(data))


class TestWebTabletopPlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def _play_alternating(self, live: _Live, *, limit: int = 400) -> list[str]:
        """Send '1', 'e', '1', 'e', ... like the terminal parity script; return the lines sent."""
        sent: list[str] = []
        for i in range(limit):
            st = live.state()
            if st["status"] != "awaiting_input":
                return sent
            body = {"kind": "menu", "index": 1} if i % 2 == 0 else {"kind": "end_turn"}
            status, data = live.api("POST", "/api/action", body)
            self.assertIn(status, (200, 422), data)
            sent.append("1" if i % 2 == 0 else "e")
        self.fail("match did not finish")

    def test_full_game_over_http_matches_terminal_and_replays(self) -> None:
        with tempfile.TemporaryDirectory(prefix="srd-web-") as tmp:
            trace_path = Path(tmp) / "web.trace.json"
            live = _Live(_goblin(out_trace=trace_path), self.rules)
            try:
                live.api("POST", "/api/start", {})
                live.api("POST", "/api/action", {"kind": "say", "text": "Cover me, Talia!"})
                sent = ["say Cover me, Talia!"] + self._play_alternating(live)
                final = live.state()
                self.assertEqual(final["status"], "finished")
                self.assertTrue(final["trace_written"])
                status, exported = live.api("GET", "/api/trace")
                self.assertEqual(status, 200)
            finally:
                live.close()
            written = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual(written, exported)
            self.assertEqual(final["result"]["digest"], written["digest"])
            rep = replay_match(written, self.rules)
            self.assertTrue(rep["deterministic_match"], rep.get("diffs"))
            self.assertEqual(written["config"]["party_driver"]["pc-bram"], "human")

            # The same lines typed at the terminal produce the same trace.
            queue = list(sent)
            terminal = run_interactive_session(
                "goblin-ambush",
                rules=self.rules,
                play_as="pc-bram",
                seed=42,
                input_fn=lambda _p: queue.pop(0) if queue else "e",
                output_fn=lambda _t: None,
            )
            self.assertEqual(terminal["digest"], written["digest"])
            self.assertEqual(set(terminal), set(written))

        # bin/srd-arena replay agrees.
        with tempfile.TemporaryDirectory(prefix="srd-web-") as tmp:
            p = Path(tmp) / "t.json"
            p.write_text(json.dumps(written), encoding="utf-8")
            out = subprocess.run(
                [sys.executable, str(REPO_ROOT / "bin" / "srd-arena"), "replay", str(p), "--format", "json"],
                capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue(json.loads(out.stdout)["deterministic_match"])

    def test_structured_intents_map_to_menu_and_moves(self) -> None:
        live = _Live(_goblin(), self.rules)
        try:
            state = live.api("POST", "/api/start", {})[1]["state"]
            item = state["menu"][0]
            status, data = live.api("POST", "/api/action", {"kind": "move", "x": 15, "y": 0})
            self.assertEqual(status, 200, data)
            self.assertEqual(data["state"]["view"]["self"]["position"], {"x": 15, "y": 0})
            menu = data["state"]["menu"]
            attack = next(m for m in menu if m["intent"]["type"] == "attack")
            status, data = live.api("POST", "/api/action", {"kind": "attack", "target_id": attack["intent"]["target_id"], "weapon": attack["intent"]["weapon"]})
            self.assertEqual(status, 200, data)
            self.assertFalse(data["state"]["view"]["self"]["resources"]["action"])
            self.assertIn("attack", item["intent"]["type"])
        finally:
            live.close()

    def test_save_quit_resume_and_overwrite_rules(self) -> None:
        with tempfile.TemporaryDirectory(prefix="srd-web-") as tmp:
            ckpt = Path(tmp) / "ckpt.json"
            trace_path = Path(tmp) / "trace.json"
            live = _Live(_goblin(save_path=ckpt, out_trace=trace_path), self.rules)
            try:
                live.api("POST", "/api/start", {})
                live.api("POST", "/api/action", {"kind": "menu", "index": 1})
                status, data = live.api("POST", "/api/save", {})
                self.assertEqual(status, 200, data)
                self.assertTrue(ckpt.is_file())
                self.assertEqual(data["state"]["status"], "awaiting_input")  # play continues after a save
                self.assertTrue(data["state"]["checkpoint_written"])
                status, data = live.api("POST", "/api/save", {})  # overwrites its own checkpoint
                self.assertEqual(status, 200, data)
                status, data = live.api("POST", "/api/quit", {})
                self.assertEqual(status, 200, data)
                self.assertEqual(data["state"]["status"], "paused")
                self.assertTrue(data["state"]["features"]["resume"])
                self.assertEqual(live.api("POST", "/api/action", {"kind": "end_turn"})[0], 409)
                status, data = live.api("POST", "/api/start", {})
                self.assertEqual(status, 200, data)
                self.assertEqual(data["state"]["status"], "awaiting_input")
                self._play_alternating(live)
                self.assertEqual(live.state()["status"], "finished")
            finally:
                live.close()
            rep = replay_match(json.loads(trace_path.read_text(encoding="utf-8")), self.rules)
            self.assertTrue(rep["deterministic_match"], rep.get("diffs"))

            # Relaunching onto the same files is refused without --force, as in the CLI.
            for flags in (["--save", str(ckpt)], ["--out-trace", str(trace_path)]):
                args = build_parser().parse_args(["goblin-ambush", *flags])
                with self.assertRaises(FileExistsError):
                    config_from_args(args)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["goblin-ambush", *flags]), 2)
                config_from_args(build_parser().parse_args(["goblin-ambush", *flags, "--force"]))

            # --resume from the saved checkpoint at launch.
            args = build_parser().parse_args(["--resume", str(ckpt), "--as", "pc-bram"])
            live = _Live(config_from_args(args), self.rules)
            try:
                data = live.api("POST", "/api/start", {})[1]["state"]
                self.assertEqual(data["status"], "awaiting_input")
                self.assertTrue(any("Resumed" in line["text"] for line in data["log"]))
            finally:
                live.close()

    def test_nl_mode_translates_and_refuses_command_lines(self) -> None:
        def _transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
            user = payload["messages"][-1]["content"]
            if "Got him" in user:
                content = json.dumps({"intent": None, "clarify": "That is a remark, not an order."})
            else:
                content = json.dumps({"intent": {"type": "move", "actor_id": "pc-bram", "to": {"x": 15, "y": 0}}, "rationale": "advance"})
            return {"choices": [{"message": {"content": content}}], "usage": {}}

        nl = OpenAICompatDriver("mock", api_base="http://127.0.0.1:9/v1", http_transport=_transport)
        live = _Live(_goblin(), self.rules, nl_driver=nl)
        try:
            state = live.api("POST", "/api/start", {})[1]["state"]
            self.assertTrue(state["features"]["nl"])
            for text in ("1", "quit", "save", "e", "move 5 5", "say hi"):
                self.assertEqual(live.api("POST", "/api/nl", {"text": text})[0], 422, text)
            status, data = live.api("POST", "/api/nl", {"text": "Got him!"})
            self.assertEqual(status, 422)
            self.assertIn("No action taken", data["error"])
            status, data = live.api("POST", "/api/nl", {"text": "Advance on the scout"})
            self.assertEqual(status, 200, data)
            self.assertEqual(data["state"]["view"]["self"]["position"], {"x": 15, "y": 0})
        finally:
            live.close()


class TestSrdWebCli(unittest.TestCase):
    def test_bin_srd_web_prints_loopback_url_and_refuses_other_hosts(self) -> None:
        self.assertTrue(BIN_WEB.is_file())
        out = subprocess.run([sys.executable, str(BIN_WEB), "goblin-ambush", "--host", "0.0.0.0"], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("127.0.0.1", out.stderr)
        proc = subprocess.Popen([sys.executable, str(BIN_WEB), "goblin-ambush", "--as", "pc-bram"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert proc.stdout is not None
            proc.stdout.readline()
            url = proc.stdout.readline().strip()
            self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/#token=[A-Za-z0-9_-]{40,}$")
        finally:
            proc.terminate()
            proc.communicate(timeout=30)


if __name__ == "__main__":
    unittest.main()
