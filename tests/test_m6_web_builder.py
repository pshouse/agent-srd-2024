"""Milestone 6.4: Custom Party & Encounter Builder (`builder.py` + `web_server.py`).

Covers:
- All 120 level-parameterized PC presets (12 SRD classes x levels 1-10) compiling
  cleanly with valid=True, errors=[], warnings=[].
- Exact 5e 2024 (SRD 5.2) per-PC XP budget math (no 2014 multiplier).
- Full 341 SRD monster search/filter/pagination.
- Built-in & parameterizable map presets + #21 caps + deterministic auto-placement.
- Local 127.0.0.1 HTTP API security (401/403/405/413/422), driver allowlist,
  end-to-end single-encounter launch, 501 fallback when dungeon.py is absent,
  and CLI parity (`--builder` / no args vs `<scenario>`).
"""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from typing import Any

from srd2024.arena.builder import (
    ALL_ARCHETYPES,
    MAX_BUILDER_BODY_BYTES,
    MAX_COVER,
    MAX_DIFFICULT,
    MAX_WALLS,
    auto_place_combatants,
    build_map_preset,
    build_pc_preset,
    compute_encounter_budget,
    search_monsters,
    validate_builder_bounds_and_terrain,
    validate_pc_spec,
)
from srd2024.arena.web_server import (
    TabletopConfig,
    TabletopServer,
    TabletopSession,
    build_parser,
    client_view,
    config_from_args,
)
from srd2024.engine.store import RulesStore


class _LiveBuilder:
    def __init__(self, config: TabletopConfig, rules: RulesStore) -> None:
        self.session = TabletopSession(config, rules=rules)
        self.server = TabletopServer(self.session, port=0)
        self.port = self.server.port
        self.token = self.server.token
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.session.shutdown()
        self.server.shutdown()
        self.server.server_close()

    def raw(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
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
            body = json.dumps(payload if payload is not None else {}).encode("utf-8")
        headers.update(extra)
        status, _, data = self.raw(method, path, body=body, headers=headers)
        return status, json.loads(data.decode("utf-8") or "null")


class TestBuilderEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_all_120_pc_presets_compile_with_zero_errors_and_warnings(self) -> None:
        self.assertEqual(len(ALL_ARCHETYPES), 12)
        for arch in ALL_ARCHETYPES:
            for lvl in range(1, 11):
                spec = build_pc_preset(arch, lvl, self.rules)
                res = validate_pc_spec(spec, self.rules)
                self.assertTrue(res["valid"], f"{arch} L{lvl} invalid: {res['errors']}")
                self.assertEqual(res["errors"], [], f"{arch} L{lvl} errors")
                self.assertEqual(res["warnings"], [], f"{arch} L{lvl} warnings")
                if lvl < 3:
                    self.assertIsNone(spec["subclass"], f"{arch} L{lvl} should omit subclass")
                else:
                    self.assertIsNotNone(spec["subclass"], f"{arch} L{lvl} should set subclass")
                self.assertIsNotNone(res["statblock_summary"])

    def test_2024_xp_budget_exact_math_without_multiplier(self) -> None:
        # 4x L5 PCs -> Low 2000, Moderate 3000, High 4400 (no 2014 group multiplier).
        res = compute_encounter_budget([5, 5, 5, 5], ["ghoul", "ghoul", "ghast"], self.rules)
        self.assertEqual(res["party_xp_thresholds"], {"low": 2000, "moderate": 3000, "high": 4400})
        # ghoul=200, ghoul=200, ghast=450 -> 850 XP (< 2000 -> trivial)
        self.assertEqual(res["encounter_xp"], 850)
        self.assertEqual(res["difficulty"], "trivial")
        self.assertEqual(res["combatant_count"], 7)

        # 1x L1 PC -> Low 50, Moderate 75, High 100.
        r_low = compute_encounter_budget([1], ["goblin-warrior"], self.rules)  # 50 XP -> low
        self.assertEqual(r_low["encounter_xp"], 50)
        self.assertEqual(r_low["difficulty"], "low")
        r_high = compute_encounter_budget([1], ["goblin-warrior", "goblin-warrior"], self.rules)  # 100 XP -> high
        self.assertEqual(r_high["difficulty"], "high")
        r_deadly = compute_encounter_budget([1], ["ghoul"], self.rules)  # 200 XP -> deadly
        self.assertEqual(r_deadly["difficulty"], "deadly")

    def test_monster_catalog_covers_all_341_and_filters(self) -> None:
        all_m = search_monsters(self.rules, limit=100, offset=0)
        self.assertEqual(all_m["total"], 341)
        self.assertEqual(len(all_m["items"]), 100)

        undead = search_monsters(self.rules, creature_type="undead", cr_min=0.25, cr_max=3.0, coverage="full", limit=50)
        self.assertGreater(undead["total"], 0)
        for m in undead["items"]:
            self.assertEqual(m["creature_type"], "undead")
            self.assertEqual(m["coverage"], "full")
            self.assertGreaterEqual(m["cr"], 0.25)
            self.assertLessEqual(m["cr"], 3.0)

        q_res = search_monsters(self.rules, q="ghoul")
        self.assertTrue(any(m["slug"] == "ghoul" for m in q_res["items"]))

    def test_map_presets_caps_and_auto_placement(self) -> None:
        for pid in ("open-hall", "pillared-crypt", "chokepoint-bridge", "ruined-keep", "goblin-ambush"):
            m = build_map_preset(pid, width_ft=50, height_ft=35)
            bounds, terrain = validate_builder_bounds_and_terrain(m["bounds"], m["terrain"])
            placed = auto_place_combatants(
                ["pc-1", "pc-2", "pc-3"],
                ["e-1", "e-2", "e-3", "e-4"],
                bounds,
                terrain,
            )
            self.assertEqual(len(placed), 7)
            coords = {(p["x"], p["y"]) for p in placed.values()}
            self.assertEqual(len(coords), 7)
            wall_set = {(w[0], w[1]) for w in terrain["walls"]}
            self.assertTrue(coords.isdisjoint(wall_set))

        # Enforce terrain size caps (#21).
        b = {"min_x": 0, "min_y": 0, "max_x": 115, "max_y": 115}
        too_many_walls = [[(i % 20) * 5, (i // 20) * 5] for i in range(MAX_WALLS + 1)]
        with self.assertRaises(ValueError):
            validate_builder_bounds_and_terrain(b, {"walls": too_many_walls, "difficult_terrain": [], "cover": []})
        too_many_diff = [[(i % 20) * 5, (i // 20) * 5] for i in range(MAX_DIFFICULT + 1)]
        with self.assertRaises(ValueError):
            validate_builder_bounds_and_terrain(b, {"walls": [], "difficult_terrain": too_many_diff, "cover": []})
        too_many_cov = [{"x": (i % 20) * 5, "y": (i // 20) * 5, "kind": "half"} for i in range(MAX_COVER + 1)]
        with self.assertRaises(ValueError):
            validate_builder_bounds_and_terrain(b, {"walls": [], "difficult_terrain": [], "cover": too_many_cov})


class TestBuilderWebApiAndSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def setUp(self) -> None:
        self.live = _LiveBuilder(TabletopConfig(builder_mode=True), self.rules)

    def tearDown(self) -> None:
        self.live.close()

    def test_cli_parity_builder_vs_scenario(self) -> None:
        # No args -> builder_mode=True.
        cfg_default = config_from_args(build_parser().parse_args([]))
        self.assertTrue(cfg_default.builder_mode)
        # --builder -> builder_mode=True.
        cfg_flag = config_from_args(build_parser().parse_args(["--builder"]))
        self.assertTrue(cfg_flag.builder_mode)
        # Scenario arg -> builder_mode=False (existing auto-start parity).
        cfg_scen = config_from_args(build_parser().parse_args(["goblin-ambush"]))
        self.assertFalse(cfg_scen.builder_mode)
        self.assertEqual(cfg_scen.scenario, "goblin-ambush")

    def test_security_and_static_serving(self) -> None:
        # Builder JS files served on local loopback server.
        for path in ("/builder-model.js", "/builder-view.js"):
            status, hdrs, _ = self.live.raw("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn("nosniff", hdrs["x-content-type-options"])

        # Missing token -> 401; wrong token -> 403; wrong origin -> 403; cross-site -> 403.
        self.assertEqual(self.live.raw("GET", "/api/builder/catalog")[0], 401)
        self.assertEqual(self.live.raw("GET", "/api/builder/monsters", headers={"Authorization": "Bearer wrong"})[0], 403)
        self.assertEqual(
            self.live.raw(
                "POST",
                "/api/builder/budget",
                body=b"{}",
                headers={"Authorization": f"Bearer {self.live.token}", "Content-Type": "application/json", "Origin": "http://evil.example"},
            )[0],
            403,
        )
        self.assertEqual(
            self.live.raw(
                "GET",
                "/api/builder/catalog",
                headers={"Authorization": f"Bearer {self.live.token}", "Sec-Fetch-Site": "cross-site"},
            )[0],
            403,
        )
        # Method mismatch -> 405.
        self.assertEqual(self.live.api("POST", "/api/builder/catalog", {})[0], 405)
        self.assertEqual(self.live.api("GET", "/api/builder/launch")[0], 405)
        # Oversized builder body -> 413.
        big = b'{"spec":{"name":"' + (b"a" * (MAX_BUILDER_BODY_BYTES + 100)) + b'"}}'
        status, _, _ = self.live.raw(
            "POST",
            "/api/builder/validate-pc",
            body=big,
            headers={
                "Authorization": f"Bearer {self.live.token}",
                "Origin": self.live.origin,
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 413)

    def test_builder_endpoints_and_caps(self) -> None:
        st_code, st = self.live.api("GET", "/api/state")
        self.assertEqual(st_code, 200)
        self.assertEqual(st["status"], "builder")

        cat_code, cat = self.live.api("GET", "/api/builder/catalog")
        self.assertEqual(cat_code, 200)
        self.assertEqual(len(cat["presets"]), 12)
        self.assertEqual(cat["limits"]["max_combatants_per_encounter"], 18)
        self.assertIn("5", cat["xp_table"])

        mon_code, mon = self.live.api("GET", "/api/builder/monsters?limit=25&offset=0")
        self.assertEqual(mon_code, 200)
        self.assertEqual(mon["total"], 341)
        self.assertEqual(len(mon["items"]), 25)
        self.assertEqual(self.live.api("GET", "/api/builder/monsters?bogus=1")[0], 400)

        spec = build_pc_preset("fighter", 5, self.rules)
        val_code, val = self.live.api("POST", "/api/builder/validate-pc", {"spec": spec})
        self.assertEqual(val_code, 200)
        self.assertTrue(val["valid"])
        self.assertGreater(val["statblock_summary"]["hp_max"], 0)

        bad_lvl = dict(spec, level=11)
        val_code2, val2 = self.live.api("POST", "/api/builder/validate-pc", {"spec": bad_lvl})
        self.assertEqual(val_code2, 200)
        self.assertFalse(val2["valid"])

        b_code, b_res = self.live.api("POST", "/api/builder/budget", {"party_levels": [5, 5], "monsters": ["ghoul", "skeleton"]})
        self.assertEqual(b_code, 200)
        self.assertEqual(b_res["combatant_count"], 4)

        # Cap & driver allowlist rejections on /api/builder/launch.
        base_launch = {
            "mode": "encounter",
            "name": "Test Skirmish",
            "party": [{"id": "pc-1", "spec": build_pc_preset("fighter", 1, self.rules)}],
            "rooms": [{"name": "Hall", "map_preset": "open-hall", "width_ft": 40, "height_ft": 30, "enemies": [{"monster": "goblin-warrior", "count": 1}]}],
            "play_as": "party",
            "party_agent": "heuristic-tactical",
            "enemy_agent": "heuristic-tactical",
            "seed": 42,
            "allow_short_rest": True,
        }
        # Disallow LLM driver from browser.
        bad_drv = {**base_launch, "enemy_agent": "openai-compat:gpt-4o"}
        self.assertEqual(self.live.api("POST", "/api/builder/launch", bad_drv)[0], 422)
        # Disallow > 6 PCs.
        too_many_pcs = {
            **base_launch,
            "party": [{"id": f"pc-{i}", "spec": build_pc_preset("fighter", 1, self.rules)} for i in range(7)],
        }
        self.assertEqual(self.live.api("POST", "/api/builder/launch", too_many_pcs)[0], 422)
        # Disallow > 12 enemies.
        too_many_enemies = {
            **base_launch,
            "rooms": [{**base_launch["rooms"][0], "enemies": [{"monster": "goblin-warrior", "count": 13}]}],
        }
        self.assertEqual(self.live.api("POST", "/api/builder/launch", too_many_enemies)[0], 422)
        # Disallow unknown top-level key -> 400.
        self.assertEqual(self.live.api("POST", "/api/builder/launch", {**base_launch, "api_base": "http://evil"})[0], 400)

    def test_end_to_end_single_encounter_launch_and_play(self) -> None:
        payload = {
            "mode": "encounter",
            "name": "Pillared Test",
            "party": [
                {"id": "pc-aldric", "spec": build_pc_preset("fighter", 5, self.rules)},
                {"id": "pc-dorian", "spec": build_pc_preset("cleric-life", 5, self.rules)},
            ],
            "rooms": [
                {
                    "name": "Crypt",
                    "map_preset": "pillared-crypt",
                    "width_ft": 50,
                    "height_ft": 35,
                    "enemies": [{"monster": "ghoul", "count": 2}],
                }
            ],
            "play_as": "party",
            "party_agent": "heuristic-tactical",
            "enemy_agent": "heuristic-tactical",
            "seed": 42,
            "allow_short_rest": True,
        }
        code, data = self.live.api("POST", "/api/builder/launch", payload)
        self.assertEqual(code, 200, data)
        self.assertTrue(data["ok"])
        state = data["state"]
        self.assertEqual(state["status"], "awaiting_input")
        self.assertEqual(state["scenario"]["name"], "Pillared Test")
        self.assertIn("class_resources", state["view"]["self"])
        self.assertIn("hit_dice", state["view"]["self"])
        # Take one action (end_turn) cleanly.
        act_code, act_data = self.live.api("POST", "/api/action", {"kind": "end_turn", "decision": state["decision"]})
        self.assertEqual(act_code, 200, act_data)

    def test_dungeon_launch_501_fallback_when_dungeon_module_absent(self) -> None:
        try:
            import srd2024.arena.dungeon  # noqa: F401
            dungeon_present = True
        except ImportError:
            dungeon_present = False
        if dungeon_present:
            self.skipTest("srd2024.arena.dungeon is merged; 501 fallback not applicable")
        room = {"name": "R", "map_preset": "open-hall", "width_ft": 40, "height_ft": 30, "enemies": [{"monster": "goblin-warrior", "count": 1}]}
        payload = {
            "mode": "dungeon",
            "name": "Three Room Test",
            "party": [{"id": "pc-1", "spec": build_pc_preset("fighter", 1, self.rules)}],
            "rooms": [room, dict(room, name="R2"), dict(room, name="R3")],
            "play_as": "party",
            "party_agent": "heuristic-tactical",
            "enemy_agent": "heuristic-tactical",
            "seed": 42,
            "allow_short_rest": True,
        }
        code, data = self.live.api("POST", "/api/builder/launch", payload)
        self.assertEqual(code, 501, data)
        rest_code, _ = self.live.api("POST", "/api/short-rest", {"take_rest": True, "plan": None})
        self.assertEqual(rest_code, 501)

    def test_client_view_exposes_class_resources_and_hit_dice_safely(self) -> None:
        obs = {
            "round": 1,
            "scenario": {"id": "x", "name": "X"},
            "self": {
                "id": "pc-1",
                "name": "Aldric",
                "side": "party",
                "kind": "pc",
                "position": {"x": 0, "y": 0},
                "hp": 44,
                "hp_max": 44,
                "ac": 19,
                "speed_ft": 30,
                "movement_remaining_ft": 30,
                "turn_resources": {"action": True, "surge_action": True, "bonus_action": True, "reaction": True},
                "class_resources": {"second_wind": {"remaining": 2, "max": 3, "recharge": "short_rest_one"}},
                "hit_dice": {"die": 10, "max": 5, "remaining": 5},
            },
            "allies": [],
            "enemies": [],
            "legal_actions": {},
        }
        view = client_view(obs)
        self.assertEqual(view["self"]["class_resources"]["second_wind"]["remaining"], 2)
        self.assertEqual(view["self"]["hit_dice"]["die"], 10)
        self.assertTrue(view["self"]["resources"]["surge_action"])


if __name__ == "__main__":
    unittest.main()
