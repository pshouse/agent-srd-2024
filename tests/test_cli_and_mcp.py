"""Verification tests for Criterion 9 (SPEC §13) and §9.2–§9.3.

Covers:
- CLI (bin/srd) all 5 subcommands in both markdown and json formats (exit code 0).
- DB path resolution via --db flag and SRD2024_DB env var.
- Live stdio subprocess MCP server (bin/srd-mcp) JSON-RPC 2.0 protocol:
  initialize -> notifications/initialized -> ping -> tools/list (all 5 tools)
  -> tools/call for all 5 tools -> resources/list -> resources/read
  -> error handling without crashing -> zero stray stdout output.
- CLI <-> MCP parity across all 5 operations.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.helpers import DB_PATH, McpStdioClient, run_cli, run_cli_json

EXPECTED_TOOLS = {
    "srd_search",
    "srd_get",
    "srd_list",
    "srd_graph",
    "srd_validate",
}


class TestCliCommandsAndDbResolution(unittest.TestCase):
    """Criterion 9 (CLI): all 5 subcommands in markdown & json + DB path resolution."""

    def test_all_5_subcommands_markdown_and_json(self) -> None:
        commands = [
            ["search", "fireball"],
            ["get", "equipment:longsword"],
            ["list", "--type", "weapon-mastery-properties"],
            ["graph", "weapon-mastery-properties:sap", "--direction", "in"],
            ["validate", "classes:wizard", "spells:fireball"],
        ]
        for base_cmd in commands:
            # Markdown
            md_proc = run_cli([*base_cmd, "--format", "markdown"])
            self.assertEqual(
                md_proc.returncode,
                0,
                f"CLI markdown failed for {base_cmd}: stderr={md_proc.stderr}",
            )
            self.assertTrue(md_proc.stdout.strip(), f"Empty markdown stdout for {base_cmd}")

            # JSON
            json_proc = run_cli([*base_cmd, "--format", "json"])
            self.assertEqual(
                json_proc.returncode,
                0,
                f"CLI json failed for {base_cmd}: stderr={json_proc.stderr}",
            )
            parsed = json.loads(json_proc.stdout)
            self.assertIsInstance(parsed, (dict, list))

    def test_get_markdown_structure_and_footer(self) -> None:
        proc = run_cli(["get", "equipment:longsword", "--depth", "1", "--format", "markdown"])
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout
        self.assertIn("# Longsword (`equipment:longsword`)", out)
        self.assertIn("## Referenced Rules", out)
        self.assertRegex(
            out,
            r"<!-- bundle: id=equipment:longsword depth=1 tokens~=\d+/\d+ full=\d+ summary=\d+ omitted=\d+ -->",
        )

    def test_db_resolution_flag_and_env_var(self) -> None:
        with tempfile.TemporaryDirectory(prefix="srd2024-dbpath-") as tmp:
            copied_db = Path(tmp) / "custom.sqlite"
            shutil.copy2(DB_PATH, copied_db)

            # 1. --db flag
            via_flag = run_cli_json(["get", "conditions:blinded", "--depth", "0"], db_path=copied_db)
            self.assertEqual(via_flag["root"]["id"], "conditions:blinded")

            # 2. SRD2024_DB env var
            via_env = run_cli_json(
                ["get", "conditions:blinded", "--depth", "0"],
                env_extra={"SRD2024_DB": str(copied_db)},
            )
            self.assertEqual(via_env["root"]["id"], "conditions:blinded")


class TestMcpStdioProtocolAndParity(unittest.TestCase):
    """Criterion 9 (MCP): live stdio JSON-RPC 2.0 protocol + CLI/MCP parity."""

    def test_full_mcp_lifecycle_tools_resources_and_parity(self) -> None:
        with McpStdioClient() as mcp:
            # 1. initialize
            init_resp = mcp.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "verifier-test", "version": "1.0"},
                },
            )
            self.assertNotIn("error", init_resp)
            init_result = init_resp["result"]
            self.assertEqual(init_result["protocolVersion"], "2024-11-05")
            self.assertIn("tools", init_result["capabilities"])
            self.assertIn("resources", init_result["capabilities"])
            self.assertEqual(init_result["serverInfo"]["name"], "agent-srd-2024")

            # 2. notifications/initialized -> MUST emit no response
            mcp.send_notification("notifications/initialized")
            mcp.assert_no_output_within(0.2)

            # 3. ping
            ping_resp = mcp.request("ping")
            self.assertNotIn("error", ping_resp)
            self.assertEqual(ping_resp["result"], {})

            # 4. tools/list -> all 5 tools with JSON Schema inputSchema
            tools_resp = mcp.request("tools/list")
            self.assertNotIn("error", tools_resp)
            tools = tools_resp["result"]["tools"]
            tool_map = {t["name"]: t for t in tools}
            self.assertEqual(set(tool_map.keys()), EXPECTED_TOOLS)
            for name, spec in tool_map.items():
                self.assertTrue(spec.get("description"), f"Tool {name} missing description")
                self.assertIn("inputSchema", spec, f"Tool {name} missing inputSchema")
                self.assertEqual(spec["inputSchema"].get("type"), "object")

            # 5. tools/call for all 5 tools (JSON mode) + exact parity with CLI JSON
            parity_cases = [
                (
                    "srd_search",
                    {"query": "Dragon's", "limit": 5, "format": "json"},
                    ["search", "Dragon's", "--limit", "5", "--format", "json"],
                ),
                (
                    "srd_get",
                    {"id": "equipment:longsword", "depth": 1, "max_tokens": 2000, "format": "json"},
                    ["get", "equipment:longsword", "--depth", "1", "--max-tokens", "2000", "--format", "json"],
                ),
                (
                    "srd_list",
                    {"type": "spells", "filters": {"level": "3", "class": "wizard", "school": "evocation"}, "format": "json"},
                    ["list", "--type", "spells", "--filter", "level=3", "--filter", "class=wizard", "--filter", "school=evocation", "--format", "json"],
                ),
                (
                    "srd_graph",
                    {"id": "weapon-mastery-properties:sap", "direction": "in", "format": "json"},
                    ["graph", "weapon-mastery-properties:sap", "--direction", "in", "--format", "json"],
                ),
                (
                    "srd_validate",
                    {"ids": ["classes:wizard", "spells:fireball", "shield", "nonexistent-xyz"], "format": "json"},
                    ["validate", "classes:wizard", "spells:fireball", "shield", "nonexistent-xyz", "--format", "json"],
                ),
            ]

            for tool_name, tool_args, cli_args in parity_cases:
                call_resp = mcp.request("tools/call", {"name": tool_name, "arguments": tool_args})
                self.assertNotIn("error", call_resp, f"Unexpected JSON-RPC error for {tool_name}: {call_resp}")
                result = call_resp["result"]
                self.assertFalse(result.get("isError", False), f"Tool {tool_name} returned isError=True: {result}")
                content = result["content"]
                self.assertEqual(len(content), 1)
                self.assertEqual(content[0]["type"], "text")
                mcp_parsed = json.loads(content[0]["text"])
                cli_parsed = run_cli_json(cli_args)
                self.assertEqual(mcp_parsed, cli_parsed, f"CLI/MCP JSON parity mismatch for {tool_name}")

            # 6. tools/call in default markdown mode
            md_call = mcp.request("tools/call", {"name": "srd_get", "arguments": {"id": "equipment:longsword"}})
            self.assertFalse(md_call["result"].get("isError", False))
            md_text = md_call["result"]["content"][0]["text"]
            self.assertIn("# Longsword (`equipment:longsword`)", md_text)
            self.assertIn("<!-- bundle:", md_text)

            # 7. resources/list & resources/read
            res_list = mcp.request("resources/list")
            self.assertNotIn("error", res_list)
            resources = res_list["result"]["resources"]
            uris = {r["uri"] for r in resources}
            self.assertIn("srd://meta", uris)

            meta_read = mcp.request("resources/read", {"uri": "srd://meta"})
            self.assertNotIn("error", meta_read)
            meta_contents = meta_read["result"]["contents"]
            self.assertTrue(meta_contents)
            meta_json = json.loads(meta_contents[0]["text"])
            self.assertIn("srd-2024-5.2", json.dumps(meta_json))

            card_read = mcp.request("resources/read", {"uri": "srd://card/equipment/longsword"})
            self.assertNotIn("error", card_read)
            card_contents = card_read["result"]["contents"]
            self.assertTrue(card_contents)
            self.assertIn("equipment:longsword", card_contents[0]["text"])

            # 8. Punctuation-only query over MCP & unknown tool/resource error handling
            punct_call = mcp.request("tools/call", {"name": "srd_search", "arguments": {"query": "---", "format": "json"}})
            self.assertFalse(punct_call["result"].get("isError", False))
            self.assertEqual(json.loads(punct_call["result"]["content"][0]["text"])["results"], [])

            bad_tool = mcp.request("tools/call", {"name": "srd_nonexistent", "arguments": {}})
            self.assertTrue(
                "error" in bad_tool or bad_tool.get("result", {}).get("isError") is True,
                f"Expected error or isError=True for unknown tool, got {bad_tool}",
            )

            bad_res = mcp.request("resources/read", {"uri": "srd://card/spells/does-not-exist"})
            self.assertIn("error", bad_res)

            # Server still responsive after error
            post_ping = mcp.request("ping")
            self.assertEqual(post_ping["result"], {})

            # Clean shutdown with zero stray stdout
            rc, _stderr = mcp.close()
            self.assertEqual(rc, 0, f"MCP server exited with non-zero code {rc}, stderr:\n{_stderr}")


if __name__ == "__main__":
    unittest.main()
