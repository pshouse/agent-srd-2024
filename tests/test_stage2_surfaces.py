"""Stage 2 Verification Tests (§7.4): CLI, MCP, Sessions, Boundary Hygiene & Docs.

Covers:
1. CLI Stage 2 Subcommands:
   - bin/srd rule spells:fireball & bin/srd rule --coverage (markdown & json)
   - bin/srd character (<spec.json> and --spec <json>) (markdown & json)
   - bin/srd encounter (--intent, --state, --out-state) (markdown & json)
   - --rules flag & SRD2024_RULES env var path resolution
2. MCP Stage 2 Discovery, Parity & Sessions:
   - Default bin/srd-mcp tools/list still returns the exact 5 Stage 1 tools
     (Stage 1 test lock intact), while tools/call dispatches Stage 2 tools.
   - bin/srd-mcp --engine, SRD2024_ENGINE=1 bin/srd-mcp, AND bin/srd-engine-mcp
     advertise all 8 tools with valid JSON Schemas + srd://rules/coverage &
     srd://rule/{type}/{slug} resources.
   - Multi-turn srd_encounter works both statelessly (state round-trip) and via
     session_id, matching CLI JSON output 1:1.
3. Boundary Hygiene:
   - src/srd2024/runtime/** and src/srd2024/engine/** NEVER import
     srd2024.compiler or srd2024.rules_compiler.
4. Docs & Skill:
   - skill/SKILL.md and README.md document Stage 2 tools while preserving all
     Stage 1 checks and CC-BY-4.0 attribution.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from tests.helpers import (
    BIN_MCP,
    DIST_DIR,
    README_PATH,
    REPO_ROOT,
    SKILL_PATH,
    McpStdioClient,
    load_provenance,
    run_cli,
    run_cli_json,
    split_frontmatter,
)

BIN_ENGINE_MCP = REPO_ROOT / "bin" / "srd-engine-mcp"
RULES_PATH = DIST_DIR / "rules.json"

STAGE1_TOOLS = {
    "srd_search",
    "srd_get",
    "srd_list",
    "srd_graph",
    "srd_validate",
}

STAGE2_TOOLS = {
    "srd_rule",
    "srd_compile_character",
    "srd_encounter",
}

ALL_8_TOOLS = STAGE1_TOOLS | STAGE2_TOOLS


def _sample_fighter_spec() -> dict[str, Any]:
    return {
        "name": "Aldric Vance",
        "species": "human",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "class": "fighter",
        "subclass": "champion",
        "level": 5,
        "ability_method": "point_buy",
        "base_abilities": {"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 13, "cha": 10},
        "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}],
        "fighting_style": "defense",
        "equipment": {
            "armor": "chain-mail",
            "shield": True,
            "weapons": ["longsword", "greatsword", "javelin"],
        },
        "weapon_mastery_weapons": ["longsword", "greatsword", "javelin", "battleaxe"],
        "hp_method": "fixed",
    }


class _CustomMcpClient(McpStdioClient):
    """McpStdioClient allowing custom executable and extra CLI args (e.g. --engine, --rules)."""

    def __init__(
        self,
        cmd: list[str],
        *,
        env_extra: dict[str, str] | None = None,
        env_unset: list[str] | None = None,
    ) -> None:
        super().__init__(env_extra=env_extra)
        self.custom_cmd = cmd
        self.env_unset = env_unset or []

    def __enter__(self) -> "_CustomMcpClient":
        env = os.environ.copy()
        for key in self.env_unset:
            env.pop(key, None)
        if self.env_extra:
            env.update(self.env_extra)
        self.proc = subprocess.Popen(
            self.custom_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        return self


def _init_mcp(mcp: McpStdioClient) -> dict[str, Any]:
    resp = mcp.request(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "stage2-verifier", "version": "2.0"},
        },
    )
    assert "error" not in resp, f"initialize error: {resp}"
    mcp.send_notification("notifications/initialized")
    mcp.assert_no_output_within(0.15)
    return resp["result"]


def _call_tool_json(mcp: McpStdioClient, name: str, arguments: dict[str, Any]) -> Any:
    resp = mcp.request("tools/call", {"name": name, "arguments": arguments})
    assert "error" not in resp, f"JSON-RPC error calling {name}: {resp}"
    res = resp["result"]
    assert not res.get("isError", False), f"Tool {name} returned isError=True: {res}"
    return json.loads(res["content"][0]["text"])


class TestStage2CliSurfaces(unittest.TestCase):
    """§7.4.1: CLI Stage 2 subcommands (rule, character, encounter) & rules path resolution."""

    def test_cli_rule_and_coverage_markdown_and_json(self) -> None:
        # 1. bin/srd rule spells:fireball
        md = run_cli(["rule", "spells:fireball", "--format", "markdown"])
        self.assertEqual(md.returncode, 0, f"stderr={md.stderr}")
        self.assertIn("Fireball", md.stdout)

        js = run_cli_json(["rule", "spells:fireball"])
        self.assertEqual(js["status"], "ok")
        rule_obj = js.get("rule", js.get("entity", {}))
        self.assertEqual(rule_obj["slug"], "fireball")
        self.assertEqual(rule_obj["coverage"], "full")

        # 2. bin/srd rule --coverage
        cov_md = run_cli(["rule", "--coverage", "--format", "markdown"])
        self.assertEqual(cov_md.returncode, 0, f"stderr={cov_md.stderr}")
        self.assertTrue(cov_md.stdout.strip())

        cov_js = run_cli_json(["rule", "--coverage"])
        self.assertIn("by_domain", cov_js.get("summary", cov_js))

    def test_cli_character_and_encounter_markdown_and_json(self) -> None:
        spec = _sample_fighter_spec()
        with tempfile.TemporaryDirectory(prefix="srd2024-cli-s2-") as tmp:
            spec_path = Path(tmp) / "fighter.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            # bin/srd character <spec.json>
            char_md = run_cli(["character", str(spec_path), "--format", "markdown"])
            self.assertEqual(char_md.returncode, 0, f"stderr={char_md.stderr}")
            self.assertIn("Aldric Vance", char_md.stdout)

            char_js = run_cli_json(["character", str(spec_path)])
            self.assertTrue(char_js["valid"])
            self.assertEqual(char_js["statblock"]["ac"], 19)
            self.assertEqual(char_js["statblock"]["hp_max"], 44)

            # bin/srd character --spec <json>
            char_inline = run_cli_json(["character", "--spec", json.dumps(spec)])
            self.assertEqual(char_inline, char_js)

            # bin/srd encounter start -> end_turn with --state / --out-state
            state_path = Path(tmp) / "state.json"
            start_intent = {
                "type": "start_encounter",
                "seed": 77,
                "combatants": [
                    {"id": "pc-1", "side": "party", "statblock": char_js["statblock"], "position": {"x": 0, "y": 0}},
                    {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                ],
            }
            enc_md = run_cli(
                ["encounter", "--intent", json.dumps(start_intent), "--out-state", str(state_path), "--format", "markdown"]
            )
            self.assertEqual(enc_md.returncode, 0, f"stderr={enc_md.stderr}")
            self.assertTrue(enc_md.stdout.strip())
            self.assertTrue(state_path.is_file())

            enc_js = run_cli_json(
                ["encounter", "--intent", json.dumps(start_intent), "--out-state", str(state_path)]
            )
            self.assertEqual(enc_js["status"], "ok")
            active = enc_js["state"]["turn_order"][enc_js["state"]["active_index"]]

            # Step 2 using --state
            end_intent = {"type": "end_turn", "actor_id": active}
            step2_js = run_cli_json(
                ["encounter", "--state", str(state_path), "--intent", json.dumps(end_intent), "--out-state", str(state_path)]
            )
            self.assertEqual(step2_js["status"], "ok")
            self.assertTrue(step2_js["state_diff"]["turn_changed"])

    def test_rules_path_resolution_flag_and_env_var(self) -> None:
        with tempfile.TemporaryDirectory(prefix="srd2024-rulespath-") as tmp:
            copied_rules = Path(tmp) / "custom-rules.json"
            shutil.copy2(RULES_PATH, copied_rules)

            via_flag = run_cli_json(["--rules", str(copied_rules), "rule", "conditions:exhaustion"])
            self.assertEqual(via_flag["status"], "ok")
            self.assertEqual((via_flag.get("rule") or via_flag.get("entity"))["slug"], "exhaustion")

            via_env = run_cli_json(
                ["rule", "conditions:exhaustion"],
                env_extra={"SRD2024_RULES": str(copied_rules)},
            )
            self.assertEqual(via_env["status"], "ok")
            self.assertEqual((via_env.get("rule") or via_env.get("entity"))["slug"], "exhaustion")


class TestStage2McpDiscoveryParityAndSessions(unittest.TestCase):
    """§7.4.2: 5-tool default lock, 8-tool engine discovery, resources, CLI/MCP parity & session_id."""

    def test_engine_mcp_executable_present(self) -> None:
        self.assertTrue(BIN_ENGINE_MCP.is_file(), f"Missing {BIN_ENGINE_MCP}")
        mode = BIN_ENGINE_MCP.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR), f"Not executable (+x): {BIN_ENGINE_MCP}")
        first_line = BIN_ENGINE_MCP.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("python3", first_line)

    def test_default_srd_mcp_keeps_5_tool_lock_and_dispatches_stage2_calls(self) -> None:
        with _CustomMcpClient([str(BIN_MCP)], env_unset=["SRD2024_ENGINE"]) as mcp:
            _init_mcp(mcp)
            tools_resp = mcp.request("tools/list")
            tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
            self.assertEqual(tool_names, STAGE1_TOOLS, "Default bin/srd-mcp MUST preserve exact 5-tool Stage 1 lock")

            # tools/call still dispatches Stage 2 tools even without --engine
            rule_out = _call_tool_json(mcp, "srd_rule", {"id": "spells:fireball", "format": "json"})
            self.assertEqual(rule_out["status"], "ok")
            self.assertEqual((rule_out.get("rule") or rule_out.get("entity"))["slug"], "fireball")

            char_out = _call_tool_json(mcp, "srd_compile_character", {"spec": _sample_fighter_spec(), "format": "json"})
            self.assertTrue(char_out["valid"])

            rc, stderr = mcp.close()
            self.assertEqual(rc, 0, f"stderr={stderr}")

    def test_engine_modes_advertise_all_8_tools_and_resources(self) -> None:
        launchers: list[tuple[str, list[str], dict[str, str] | None]] = [
            ("bin/srd-mcp --engine", [str(BIN_MCP), "--engine"], None),
            ("SRD2024_ENGINE=1 bin/srd-mcp", [str(BIN_MCP)], {"SRD2024_ENGINE": "1"}),
            ("bin/srd-engine-mcp", [str(BIN_ENGINE_MCP)], None),
        ]
        for label, cmd, env_extra in launchers:
            with self.subTest(launcher=label):
                with _CustomMcpClient(cmd, env_extra=env_extra, env_unset=["SRD2024_ENGINE"]) as mcp:
                    _init_mcp(mcp)
                    tools_resp = mcp.request("tools/list")
                    self.assertNotIn("error", tools_resp)
                    tools = tools_resp["result"]["tools"]
                    tool_map = {t["name"]: t for t in tools}
                    self.assertEqual(set(tool_map.keys()), ALL_8_TOOLS, f"{label} did not advertise all 8 tools")
                    for name, spec in tool_map.items():
                        self.assertTrue(spec.get("description"), f"{label}: {name} missing description")
                        self.assertEqual(spec["inputSchema"].get("type"), "object", f"{label}: {name} bad inputSchema")

                    # Additive MCP resources
                    res_list = mcp.request("resources/list")
                    uris = {r["uri"] for r in res_list["result"]["resources"]}
                    self.assertIn("srd://rules/coverage", uris)

                    cov_read = mcp.request("resources/read", {"uri": "srd://rules/coverage"})
                    self.assertNotIn("error", cov_read)
                    cov_json = json.loads(cov_read["result"]["contents"][0]["text"])
                    self.assertIn("by_domain", cov_json.get("summary", cov_json))

                    rule_read = mcp.request("resources/read", {"uri": "srd://rule/spells/fireball"})
                    self.assertNotIn("error", rule_read)
                    rule_json = json.loads(rule_read["result"]["contents"][0]["text"])
                    self.assertEqual(rule_json.get("slug", rule_json.get("entity", {}).get("slug")), "fireball")

                    rc, stderr = mcp.close()
                    self.assertEqual(rc, 0, f"{label} exited with {rc}, stderr={stderr}")

    def test_cli_mcp_parity_and_stateless_vs_session_encounter(self) -> None:
        spec = _sample_fighter_spec()
        cli_rule = run_cli_json(["rule", "spells:fireball"])
        cli_char = run_cli_json(["character", "--spec", json.dumps(spec)])

        start_intent = {
            "type": "start_encounter",
            "seed": 4242,
            "combatants": [
                {"id": "pc-1", "side": "party", "statblock": cli_char["statblock"], "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        }
        with tempfile.TemporaryDirectory(prefix="srd2024-parity-") as tmp:
            st_path = Path(tmp) / "enc.json"
            cli_step1 = run_cli_json(
                ["encounter", "--intent", json.dumps(start_intent), "--out-state", str(st_path)]
            )
            active1 = cli_step1["state"]["turn_order"][cli_step1["state"]["active_index"]]
            target1 = "gob-1" if active1 == "pc-1" else "pc-1"
            step2_intent = (
                {"type": "attack", "actor_id": active1, "target_id": target1, "weapon": "longsword"}
                if active1 == "pc-1"
                else {"type": "attack", "actor_id": active1, "target_id": target1, "action": "Scimitar"}
            )
            cli_step2 = run_cli_json(
                ["encounter", "--state", str(st_path), "--intent", json.dumps(step2_intent), "--out-state", str(st_path)]
            )
            step3_intent = {"type": "end_turn", "actor_id": active1}
            cli_step3 = run_cli_json(
                ["encounter", "--state", str(st_path), "--intent", json.dumps(step3_intent), "--out-state", str(st_path)]
            )

        with _CustomMcpClient([str(BIN_ENGINE_MCP)]) as mcp:
            _init_mcp(mcp)

            # 1:1 parity on srd_rule & srd_compile_character
            mcp_rule = _call_tool_json(mcp, "srd_rule", {"id": "spells:fireball", "format": "json"})
            self.assertEqual(mcp_rule, cli_rule)

            mcp_char = _call_tool_json(mcp, "srd_compile_character", {"spec": spec, "format": "json"})
            self.assertEqual(mcp_char, cli_char)

            # Stateless encounter parity with CLI
            mcp_s1 = _call_tool_json(mcp, "srd_encounter", {"intent": start_intent, "format": "json"})
            self.assertEqual(mcp_s1, cli_step1)

            mcp_s2 = _call_tool_json(
                mcp, "srd_encounter", {"state": mcp_s1["state"], "intent": step2_intent, "format": "json"}
            )
            self.assertEqual(mcp_s2, cli_step2)

            mcp_s3 = _call_tool_json(
                mcp, "srd_encounter", {"state": mcp_s2["state"], "intent": step3_intent, "format": "json"}
            )
            self.assertEqual(mcp_s3, cli_step3)

            # Stateful session_id mode (no state resent on turn 2 & 3) -> identical to stateless/CLI!
            sess_s1 = _call_tool_json(
                mcp, "srd_encounter", {"session_id": "sess-alpha", "intent": start_intent, "format": "json"}
            )
            sess_s2 = _call_tool_json(
                mcp, "srd_encounter", {"session_id": "sess-alpha", "intent": step2_intent, "format": "json"}
            )
            sess_s3 = _call_tool_json(
                mcp, "srd_encounter", {"session_id": "sess-alpha", "intent": step3_intent, "format": "json"}
            )
            self.assertEqual(sess_s1["state"], cli_step1["state"])
            self.assertEqual(sess_s2["state"], cli_step2["state"])
            self.assertEqual(sess_s3["state"], cli_step3["state"])
            self.assertEqual(sess_s2["events"], cli_step2["events"])
            self.assertEqual(sess_s3["events"], cli_step3["events"])

            rc, stderr = mcp.close()
            self.assertEqual(rc, 0, f"stderr={stderr}")


class TestStage2BoundaryHygieneAndDocs(unittest.TestCase):
    """§7.4.3–§7.4.4: Import boundary isolation & Skill/README Stage 2 docs + attribution."""

    def test_runtime_and_engine_never_import_compilers(self) -> None:
        forbidden = (
            "srd2024.compiler",
            "srd2024.rules_compiler",
            "from ..compiler",
            "from ..rules_compiler",
            "from .compiler",
            "from .rules_compiler",
        )
        for subdir in ("runtime", "engine"):
            pkg_dir = REPO_ROOT / "src" / "srd2024" / subdir
            self.assertTrue(pkg_dir.is_dir(), f"Missing {pkg_dir}")
            for py_file in pkg_dir.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                for bad in forbidden:
                    self.assertNotIn(
                        bad,
                        content,
                        f"Boundary violation: {py_file} references {bad!r}",
                    )

    def test_skill_and_readme_document_stage2_and_preserve_stage1(self) -> None:
        prov = load_provenance()

        # skill/SKILL.md
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        fm, body = split_frontmatter(skill_text)
        self.assertEqual(fm.get("name"), "srd-2024")
        self.assertTrue(fm.get("description"))
        for expected in (
            "srd_search",
            "srd_get",
            "srd_list",
            "srd_graph",
            "srd_validate",
            "srd_rule",
            "srd_compile_character",
            "srd_encounter",
            "srd-engine-mcp",
        ):
            self.assertIn(expected, body, f"skill/SKILL.md should document {expected}")

        # README.md
        readme_text = README_PATH.read_text(encoding="utf-8")
        self.assertIn("SRD 5.2", readme_text)
        self.assertIn("Wizards of the Coast", readme_text)
        self.assertIn("creativecommons.org/licenses/by/4.0", readme_text)
        self.assertIn(prov["content_license"]["attribution"][:40], readme_text)
        for expected in (
            "srd-rules-build",
            "srd rule",
            "srd character",
            "srd encounter",
            "srd-engine-mcp",
        ):
            self.assertIn(expected, readme_text, f"README.md should document {expected}")


if __name__ == "__main__":
    unittest.main()
