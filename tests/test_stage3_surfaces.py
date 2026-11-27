"""Stage 3 Verification Tests (§8.4): CLI Parity, MCP Discovery & Locks, Boundary & Doc Hygiene.

Covers:
1. Packaging & Executable:
   - bin/srd-arena exists, is executable (+x), has python3 shebang.
   - pyproject.toml preserves all 5 existing entries, dependencies = [], and adds srd-arena.
2. CLI Parity (bin/srd-arena vs bin/srd arena):
   - scenarios, run goblin-ambush --seed 7 --out-trace <tmp>, replay <tmp>,
     and bench --scenarios goblin-ambush --agents heuristic-tactical,random --seeds 1,2
     succeed (exit 0) in BOTH markdown and json formats with 1:1 identical JSON parity.
3. MCP Stage 3 Discovery & Lock Preservation:
   - Default bin/srd-mcp still returns exact 5 Stage 1 tools in tools/list (Lock C) and rejects Stage 3 tools/calls.
   - bin/srd-engine-mcp, bin/srd-mcp --engine, SRD2024_ENGINE=1 still return exact 8 Stage 1+2 tools (Lock D).
   - bin/srd-mcp --arena, SRD2024_ARENA=1 bin/srd-mcp, and bin/srd-engine-mcp --arena advertise all 11 tools +
     srd://arena/scenarios resource, and tools/call for srd_arena_scenarios, srd_arena_run, srd_arena_bench
     returns 1:1 JSON parity with CLI.
4. Boundary Hygiene & Stdlib-Only Check:
   - src/srd2024/arena/**, src/srd2024/runtime/**, src/srd2024/engine/** NEVER reference
     srd2024.compiler or srd2024.rules_compiler.
   - Every Python module in src/srd2024/arena/** imports strictly Python 3.11+ stdlib or srd2024.
5. Docs, Attribution & Trademark Hygiene:
   - skill/SKILL.md and README.md document Stage 3 (srd-arena, srd arena, --arena) while preserving all
     Stage 1+2 checks, verbatim CC-BY-4.0 attribution, and trademark hygiene.
"""

from __future__ import annotations

import ast
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.helpers import (
    BIN_MCP,
    PYPROJECT_PATH,
    README_PATH,
    REPO_ROOT,
    SKILL_PATH,
    McpStdioClient,
    load_provenance,
    run_cli,
    run_cli_json,
    split_frontmatter,
)

BIN_ARENA = REPO_ROOT / "bin" / "srd-arena"
BIN_ENGINE_MCP = REPO_ROOT / "bin" / "srd-engine-mcp"

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
STAGE3_TOOLS = {
    "srd_arena_scenarios",
    "srd_arena_run",
    "srd_arena_bench",
}
ALL_8_TOOLS = STAGE1_TOOLS | STAGE2_TOOLS
ALL_11_TOOLS = STAGE1_TOOLS | STAGE2_TOOLS | STAGE3_TOOLS


def _run_arena_bin(
    args: list[str],
    *,
    env_extra: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(BIN_ARENA), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def _run_arena_bin_json(args: list[str], *, env_extra: dict[str, str] | None = None) -> Any:
    full = list(args)
    if "--format" not in full:
        full.extend(["--format", "json"])
    proc = _run_arena_bin(full, env_extra=env_extra)
    if proc.returncode != 0:
        raise AssertionError(
            f"bin/srd-arena failed (rc={proc.returncode}): {' '.join(full)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


class _CustomMcpClient(McpStdioClient):
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
            "clientInfo": {"name": "stage3-verifier", "version": "3.0"},
        },
    )
    assert "error" not in resp, f"initialize error: {resp}"
    mcp.send_notification("notifications/initialized")
    mcp.assert_no_output_within(0.15)
    return resp["result"]


def _call_tool_json(mcp: McpStdioClient, name: str, arguments: dict[str, Any], timeout: float = 30.0) -> Any:
    resp = mcp.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
    assert "error" not in resp, f"JSON-RPC error calling {name}: {resp}"
    res = resp["result"]
    assert not res.get("isError", False), f"Tool {name} returned isError=True: {res}"
    return json.loads(res["content"][0]["text"])


class TestStage3PackagingAndCliParity(unittest.TestCase):
    """§8.4.1 & §8.4.2: Packaging, bin/srd-arena executable, and 1:1 CLI parity with bin/srd arena."""

    def test_packaging_and_srd_arena_executable(self) -> None:
        self.assertTrue(BIN_ARENA.is_file(), f"Missing executable: {BIN_ARENA}")
        mode = BIN_ARENA.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR), f"Not executable (+x): {BIN_ARENA}")
        first_line = BIN_ARENA.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("python3", first_line)

        pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")
        self.assertIn("dependencies = []", pyproject)
        for script_entry in (
            'srd-build = "srd2024.compiler.__main__:main"',
            'srd = "srd2024.runtime.cli:main"',
            'srd-mcp = "srd2024.runtime.mcp:main"',
            'srd-rules-build = "srd2024.rules_compiler.__main__:main"',
            'srd-engine-mcp = "srd2024.runtime.mcp:main_engine"',
            'srd-arena = "srd2024.runtime.arena_bridge:main_arena"',
        ):
            self.assertIn(script_entry, pyproject, f"pyproject.toml missing script entry: {script_entry}")

    def test_cli_parity_bin_srd_arena_vs_bin_srd_arena_subcommand(self) -> None:
        # 1. scenarios (markdown & json)
        a_scen_md = _run_arena_bin(["scenarios", "--format", "markdown"])
        s_scen_md = run_cli(["arena", "scenarios", "--format", "markdown"])
        self.assertEqual(a_scen_md.returncode, 0, a_scen_md.stderr)
        self.assertEqual(s_scen_md.returncode, 0, s_scen_md.stderr)
        for sid in ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"):
            self.assertIn(sid, a_scen_md.stdout)
            self.assertIn(sid, s_scen_md.stdout)

        a_scen_js = _run_arena_bin_json(["scenarios"])
        s_scen_js = run_cli_json(["arena", "scenarios"])
        self.assertEqual(a_scen_js, s_scen_js)

        with tempfile.TemporaryDirectory(prefix="srd2024-arena-cli-") as tmp:
            trace_a = Path(tmp) / "trace_a.json"
            trace_s = Path(tmp) / "trace_s.json"

            # 2. run goblin-ambush --seed 7 --out-trace <tmp> (markdown & json)
            a_run_md = _run_arena_bin(
                ["run", "goblin-ambush", "--seed", "7", "--out-trace", str(trace_a), "--format", "markdown"]
            )
            s_run_md = run_cli(
                ["arena", "run", "goblin-ambush", "--seed", "7", "--out-trace", str(trace_s), "--format", "markdown"],
                timeout=60.0,
            )
            self.assertEqual(a_run_md.returncode, 0, a_run_md.stderr)
            self.assertEqual(s_run_md.returncode, 0, s_run_md.stderr)
            self.assertTrue(trace_a.is_file())
            self.assertTrue(trace_s.is_file())
            self.assertIn("Round 1", a_run_md.stdout)

            a_run_js = _run_arena_bin_json(["run", "goblin-ambush", "--seed", "7"])
            s_run_js = run_cli_json(["arena", "run", "goblin-ambush", "--seed", "7"])
            self.assertEqual(a_run_js, s_run_js)
            self.assertEqual(json.loads(trace_a.read_text(encoding="utf-8")), a_run_js)
            self.assertEqual(json.loads(trace_s.read_text(encoding="utf-8")), s_run_js)

            # 3. replay <tmp> (markdown & json)
            a_rep_md = _run_arena_bin(["replay", str(trace_a), "--format", "markdown"])
            s_rep_md = run_cli(["arena", "replay", str(trace_s), "--format", "markdown"], timeout=60.0)
            self.assertEqual(a_rep_md.returncode, 0, a_rep_md.stderr)
            self.assertEqual(s_rep_md.returncode, 0, s_rep_md.stderr)

            a_rep_js = _run_arena_bin_json(["replay", str(trace_a)])
            s_rep_js = run_cli_json(["arena", "replay", str(trace_s)])
            self.assertTrue(a_rep_js["deterministic_match"])
            self.assertEqual(a_rep_js["diffs"], [])
            self.assertEqual(a_rep_js, s_rep_js)

        # 4. bench (markdown & json)
        bench_args = [
            "bench",
            "--scenarios",
            "goblin-ambush",
            "--agents",
            "heuristic-tactical,random",
            "--seeds",
            "1,2",
        ]
        a_bench_md = _run_arena_bin([*bench_args, "--format", "markdown"])
        s_bench_md = run_cli(["arena", *bench_args, "--format", "markdown"], timeout=60.0)
        self.assertEqual(a_bench_md.returncode, 0, a_bench_md.stderr)
        self.assertEqual(s_bench_md.returncode, 0, s_bench_md.stderr)
        self.assertIn("heuristic-tactical", a_bench_md.stdout)

        a_bench_js = _run_arena_bin_json(bench_args)
        s_bench_js = run_cli_json(["arena", *bench_args])
        self.assertEqual(a_bench_js, s_bench_js)
        self.assertEqual(len(a_bench_js["matches"]), 4)


class TestStage3McpDiscoveryLocksAndParity(unittest.TestCase):
    """§8.4.3: Lock C (5 tools), Lock D (8 tools), Arena 11-tool discovery, resource & CLI/MCP JSON parity."""

    def test_lock_c_default_mcp_keeps_5_tools_and_rejects_arena(self) -> None:
        with _CustomMcpClient([str(BIN_MCP)], env_unset=["SRD2024_ENGINE", "SRD2024_ARENA"]) as mcp:
            _init_mcp(mcp)
            tools_resp = mcp.request("tools/list")
            tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
            self.assertEqual(tool_names, STAGE1_TOOLS, "Lock C violated: default bin/srd-mcp MUST list exact 5 tools")

            res_resp = mcp.request("resources/list")
            uris = {r["uri"] for r in res_resp["result"]["resources"]}
            self.assertIn("srd://meta", uris)
            self.assertNotIn("srd://arena/scenarios", uris)

            for tool_name in ("srd_arena_scenarios", "srd_arena_run", "srd_arena_bench"):
                resp = mcp.request("tools/call", {"name": tool_name, "arguments": {"format": "json"}})
                self.assertNotIn("error", resp, resp)
                self.assertTrue(resp["result"].get("isError"), resp)
                self.assertIn("Unknown tool", resp["result"]["content"][0]["text"])

            res_read = mcp.request("resources/read", {"uri": "srd://arena/scenarios"})
            self.assertIn("error", res_read)
            self.assertIn("Unknown resource", res_read["error"]["message"])

            rc, stderr = mcp.close()
            self.assertEqual(rc, 0, f"stderr={stderr}")

    def test_lock_d_engine_modes_keep_exact_8_tools(self) -> None:
        launchers: list[tuple[str, list[str], dict[str, str] | None]] = [
            ("bin/srd-engine-mcp", [str(BIN_ENGINE_MCP)], None),
            ("bin/srd-mcp --engine", [str(BIN_MCP), "--engine"], None),
            ("SRD2024_ENGINE=1 bin/srd-mcp", [str(BIN_MCP)], {"SRD2024_ENGINE": "1"}),
        ]
        for label, cmd, env_extra in launchers:
            with self.subTest(launcher=label):
                with _CustomMcpClient(cmd, env_extra=env_extra, env_unset=["SRD2024_ENGINE", "SRD2024_ARENA"]) as mcp:
                    _init_mcp(mcp)
                    tools_resp = mcp.request("tools/list")
                    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
                    self.assertEqual(tool_names, ALL_8_TOOLS, f"Lock D violated on {label}: got {tool_names}")

                    rc, stderr = mcp.close()
                    self.assertEqual(rc, 0, f"{label} stderr={stderr}")

    def test_arena_modes_advertise_11_tools_resource_and_cli_parity(self) -> None:
        launchers: list[tuple[str, list[str], dict[str, str] | None]] = [
            ("bin/srd-mcp --arena", [str(BIN_MCP), "--arena"], None),
            ("SRD2024_ARENA=1 bin/srd-mcp", [str(BIN_MCP)], {"SRD2024_ARENA": "1"}),
            ("bin/srd-engine-mcp --arena", [str(BIN_ENGINE_MCP), "--arena"], None),
        ]
        for label, cmd, env_extra in launchers:
            with self.subTest(launcher=label):
                with _CustomMcpClient(cmd, env_extra=env_extra, env_unset=["SRD2024_ENGINE", "SRD2024_ARENA"]) as mcp:
                    _init_mcp(mcp)
                    tools_resp = mcp.request("tools/list")
                    tools = tools_resp["result"]["tools"]
                    tool_map = {t["name"]: t for t in tools}
                    self.assertEqual(set(tool_map.keys()), ALL_11_TOOLS, f"{label} must advertise exact 11 tools")
                    for name, spec in tool_map.items():
                        self.assertTrue(spec.get("description"), f"{label}: {name} missing description")
                        self.assertEqual(spec["inputSchema"].get("type"), "object", f"{label}: {name} bad schema")

                    res_list = mcp.request("resources/list")
                    uris = {r["uri"] for r in res_list["result"]["resources"]}
                    self.assertIn("srd://meta", uris)
                    self.assertIn("srd://rules/coverage", uris)
                    self.assertIn("srd://arena/scenarios", uris)

                    res_read = mcp.request("resources/read", {"uri": "srd://arena/scenarios"})
                    self.assertNotIn("error", res_read)
                    res_json = json.loads(res_read["result"]["contents"][0]["text"])
                    res_ids = {s["id"] for s in (res_json if isinstance(res_json, list) else res_json["scenarios"])}
                    self.assertEqual(res_ids, {"goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"})

                    rc, stderr = mcp.close()
                    self.assertEqual(rc, 0, f"{label} stderr={stderr}")

        # 1:1 CLI/MCP JSON parity for all 3 Stage 3 tools
        cli_scen = _run_arena_bin_json(["scenarios"])
        cli_run = _run_arena_bin_json(["run", "goblin-ambush", "--seed", "7"])
        cli_bench = _run_arena_bin_json(
            ["bench", "--scenarios", "goblin-ambush", "--agents", "heuristic-tactical,random", "--seeds", "1,2"]
        )

        with _CustomMcpClient([str(BIN_MCP), "--arena"], env_unset=["SRD2024_ENGINE", "SRD2024_ARENA"]) as mcp:
            _init_mcp(mcp)

            mcp_scen = _call_tool_json(mcp, "srd_arena_scenarios", {"format": "json"})
            self.assertEqual(mcp_scen, cli_scen)

            mcp_run = _call_tool_json(
                mcp,
                "srd_arena_run",
                {"scenario": "goblin-ambush", "seed": 7, "format": "json"},
                timeout=60.0,
            )
            self.assertEqual(mcp_run, cli_run)

            mcp_bench = _call_tool_json(
                mcp,
                "srd_arena_bench",
                {
                    "scenarios": ["goblin-ambush"],
                    "agents": ["heuristic-tactical", "random"],
                    "seeds": [1, 2],
                    "format": "json",
                },
                timeout=60.0,
            )
            self.assertEqual(mcp_bench, cli_bench)

            rc, stderr = mcp.close()
            self.assertEqual(rc, 0, f"stderr={stderr}")


class TestStage3BoundaryHygieneAndDocs(unittest.TestCase):
    """§8.4.4 & §8.4.5: Import isolation, stdlib-only check, docs, attribution & trademark hygiene."""

    def test_arena_runtime_engine_boundary_and_stdlib_only(self) -> None:
        forbidden = (
            "srd2024.compiler",
            "srd2024.rules_compiler",
            "from ..compiler",
            "from ..rules_compiler",
            "from .compiler",
            "from .rules_compiler",
        )
        for subdir in ("arena", "runtime", "engine"):
            pkg_dir = REPO_ROOT / "src" / "srd2024" / subdir
            self.assertTrue(pkg_dir.is_dir(), f"Missing package directory: {pkg_dir}")
            for py_file in pkg_dir.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                for bad in forbidden:
                    self.assertNotIn(bad, content, f"Boundary violation: {py_file} references {bad!r}")

        # Verify strictly Python 3.11+ stdlib imports in src/srd2024/arena/** and arena_bridge.py
        stdlib_names = set(sys.stdlib_module_names)
        arena_files = list((REPO_ROOT / "src" / "srd2024" / "arena").rglob("*.py"))
        bridge = REPO_ROOT / "src" / "srd2024" / "runtime" / "arena_bridge.py"
        self.assertTrue(bridge.is_file(), f"Missing {bridge}")
        arena_files.append(bridge)

        for py_file in arena_files:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        self.assertIn(
                            top,
                            stdlib_names | {"srd2024"},
                            f"Non-stdlib import {alias.name!r} in {py_file}",
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        top = node.module.split(".")[0]
                        self.assertIn(
                            top,
                            stdlib_names | {"srd2024"},
                            f"Non-stdlib import {node.module!r} in {py_file}",
                        )

    def test_skill_readme_stage3_docs_attribution_and_trademark_hygiene(self) -> None:
        prov = load_provenance()
        trademark_re = re.compile(r"\b(?:Dungeons\s*&\s*Dragons|D&D|DnD)\b", re.IGNORECASE)

        def _strip_allowed_urls(text: str) -> str:
            return re.sub(r"https?://\S+", "", text)

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
            "srd-arena",
            "srd arena",
            "--arena",
            "srd_arena_scenarios",
            "srd_arena_run",
            "srd_arena_bench",
        ):
            self.assertIn(expected, body, f"skill/SKILL.md missing expected Stage 1/2/3 reference: {expected}")
        self.assertIn(prov["content_license"]["attribution"][:40], skill_text)
        self.assertIsNone(
            trademark_re.search(_strip_allowed_urls(skill_text)),
            "skill/SKILL.md contains prohibited third-party trademark outside upstream URLs",
        )

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
            "srd-arena",
            "srd arena",
            "--arena",
        ):
            self.assertIn(expected, readme_text, f"README.md missing expected Stage 1/2/3 reference: {expected}")
        self.assertIsNone(
            trademark_re.search(_strip_allowed_urls(readme_text)),
            "README.md contains prohibited third-party trademark outside upstream URLs",
        )

        # Arena source prose/docstrings trademark check
        for py_file in (REPO_ROOT / "src" / "srd2024" / "arena").rglob("*.py"):
            content = _strip_allowed_urls(py_file.read_text(encoding="utf-8"))
            self.assertIsNone(
                trademark_re.search(content),
                f"Prohibited third-party trademark found in {py_file}",
            )


if __name__ == "__main__":
    unittest.main()
