"""scripts/audit_arena_trace.py: clean on a real match, and catches an illegal own-turn Shield (#41)."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from srd2024.arena.agents import HeuristicTacticalDriver
from srd2024.arena.runner import run_match
from srd2024.engine.store import RulesStore

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_arena_trace.py"


def _audit(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, paths)], capture_output=True, text=True, timeout=120)


class TestAuditArenaTrace(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rules = RulesStore()
        cls.trace = None
        # A Crypt of Ghouls match with a Shield and an Opportunity Attack the mover survives.
        for seed in range(1, 40):
            t = run_match("crypt-of-ghouls", party_driver=HeuristicTacticalDriver(rules),
                          enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=seed)
            escaped_oa = any(
                e["type"] == "opportunity_attack" and any(x["type"] == "moved" and x["actor_id"] == e["target_id"] for x in a["events"][k:])
                for a in t["actions"] for k, e in enumerate(a["events"]))
            if escaped_oa and any(
                    e["type"] == "spell_cast" and e.get("spell") == "shield" for a in t["actions"] for e in a["events"]):
                cls.trace = t
                break
        assert cls.trace is not None, "no Crypt seed in 1-39 had a Shield and a survived Opportunity Attack"
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _write(self, name: str, trace: dict) -> Path:
        p = Path(self.tmp.name) / name
        p.write_text(json.dumps(trace))
        return p

    def test_real_match_is_clean_and_reactions_are_checked(self) -> None:
        res = _audit(self._write("clean.json", self.trace))
        self.assertEqual(res.returncode, 0, res.stdout[-2000:])
        self.assertRegex(res.stdout, r"ok\s+[1-9]\d* checks\s+0 bad\s+Shield is a reaction to being hit")
        self.assertRegex(res.stdout, r"ok\s+[1-9]\d* checks\s+0 bad\s+leaving a hostile's reach without Disengage")

    def test_own_turn_shield_and_missing_opportunity_attack_are_flagged(self) -> None:
        bad = copy.deepcopy(self.trace)
        # Move a reactive Shield into the caster's own action, as the pre-#33 AI did.
        for a in bad["actions"]:
            for e in a["events"]:
                if e["type"] == "spell_cast" and e.get("spell") == "shield":
                    a["actor_id"] = e["actor_id"]
                    break
        # And delete one Opportunity Attack whose mover still gets away (a dropped mover never "moves").
        for a in bad["actions"]:
            evs = a["events"]
            i = next((k for k, e in enumerate(evs) if e["type"] == "opportunity_attack"
                      and any(x["type"] == "moved" and x["actor_id"] == e["target_id"] for x in evs[k:])), None)
            if i is not None:
                del evs[i]
                break
        res = _audit(self._write("bad.json", bad))
        self.assertEqual(res.returncode, 1, res.stdout[-2000:])
        self.assertRegex(res.stdout, r"FAIL\s+\d+ checks\s+[1-9]\d* bad\s+Shield is a reaction to being hit")
        self.assertRegex(res.stdout, r"FAIL\s+\d+ checks\s+[1-9]\d* bad\s+leaving a hostile's reach without Disengage")


if __name__ == "__main__":
    unittest.main()
