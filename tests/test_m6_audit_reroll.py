"""Milestone 6.1 (#79): Audit script Heroic Inspiration reroll rollback & Bless bonus_dice preservation."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from srd2024.arena import load_scenario
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore
from tests.test_stage4_engine_hardening import _make_fighter, _start

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_arena_trace.py"
SESSIONS_DIR = Path(__file__).resolve().parents[1] / "examples" / "sessions"


def _audit(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _bless(target: str) -> dict[str, Any]:
    return {
        "id": "bless-audit-test",
        "kind": "bless",
        "source_id": "pc-dorian",
        "target_id": target,
        "value": "1d4",
        "applies_to": ["attack", "save"],
        "expires": "concentration",
        "created_round": 1,
        "concentration_spell": None,
    }


class TestAuditHeroicInspirationRollback(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(
            cls.rules,
            name="Aldric",
            level=5,
            weapons=["greatsword"],
            mastery_weapons=["greatsword"],
        )
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _write(self, name: str, trace: dict[str, Any]) -> Path:
        p = Path(self.tmp.name) / name
        p.write_text(json.dumps(trace), encoding="utf-8")
        return p

    def test_greatsword_graze_miss_then_reroll_hit_audits_cleanly(self) -> None:
        """Missed Greatsword deals Graze damage; Heroic Inspiration reroll rolls it back before hitting."""
        combatants = [
            {
                "id": "pc-fighter",
                "side": "party",
                "statblock": self.fighter,
                "position": {"x": 0, "y": 0},
                "heroic_inspiration": True,
            },
            {
                "id": "gob-1",
                "side": "enemy",
                "monster": "ghoul",
                "name": "Ghoul",
                "position": {"x": 5, "y": 0},
                "temp_hp": 2,
            },
        ]
        init_state = _start(self.rules, combatants, init_rolls=[20, 1])
        state = copy.deepcopy(init_state)

        # Action 1: Greatsword misses (d20=1) -> Graze deals ability-mod damage (absorbs 2 temp_hp + reduces HP).
        miss_intent = {"type": "attack", "actor_id": "pc-fighter", "target_id": "gob-1", "weapon": "greatsword"}
        miss_res = step(state, miss_intent, self.rules, roll_overrides=[1])
        self.assertEqual(miss_res["status"], "ok", miss_res.get("error"))
        graze_dmg = [e for e in miss_res["events"] if e.get("type") == "damage_applied"]
        self.assertEqual(len(graze_dmg), 1, "Expected Graze mastery damage on missed Greatsword")
        self.assertGreater(graze_dmg[0]["total_damage"], 0)
        self.assertEqual(graze_dmg[0]["temp_hp_absorbed"], 2)

        # Action 2: Heroic Inspiration reroll -> d20=18 hits, damage 2d6=[4, 5] -> must start from pre-Graze HP & temp_hp!
        reroll_intent = {"type": "reroll", "actor_id": "pc-fighter"}
        reroll_res = step(miss_res["state"], reroll_intent, self.rules, roll_overrides=[18, 4, 5])
        self.assertEqual(reroll_res["status"], "ok", reroll_res.get("error"))
        hit_dmg = [e for e in reroll_res["events"] if e.get("type") == "damage_applied"]
        self.assertEqual(len(hit_dmg), 1)
        self.assertEqual(hit_dmg[0]["hp_before"], init_state["combatants"]["gob-1"]["hp"])
        self.assertEqual(hit_dmg[0]["temp_hp_absorbed"], 2)

        trace = {
            "initial_state": init_state,
            "actions": [
                {"seq": 1, "actor_id": "pc-fighter", "intent": miss_intent, "events": miss_res["events"]},
                {"seq": 2, "actor_id": "pc-fighter", "intent": reroll_intent, "events": reroll_res["events"]},
            ],
        }
        res = _audit(self._write("graze-reroll-ok.json", trace))
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertRegex(res.stdout, r"ok\s+[1-9]\d* checks\s+0 bad\s+hp_before matches running HP")
        self.assertRegex(res.stdout, r"ok\s+[1-9]\d* checks\s+0 bad\s+temp_hp_absorbed matches running temp HP")

    def test_bless_d4_preservation_passes_and_rerolled_bless_is_flagged(self) -> None:
        """Bless d4 kept on reroll passes audit; re-rolling Bless d4 (pre-#85 bug) is caught."""
        scen = load_scenario("wyrmling-lair", self.rules)
        pc = copy.deepcopy(next(c for c in scen["combatants"] if c["id"] == "pc-aldric"))
        pc.update({"position": {"x": 0, "y": 0}, "heroic_inspiration": True})
        foe = {"id": "gob", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}}
        init_state = step(
            None,
            {"type": "start_encounter", "seed": 2, "combatants": [pc, foe]},
            self.rules,
            roll_overrides=[20, 1],
        )["state"]
        init_state["combatants"]["pc-aldric"]["active_effects"].append(_bless("pc-aldric"))

        atk_intent = {"type": "attack", "actor_id": "pc-aldric", "target_id": "gob", "weapon": "longsword"}
        miss_res = step(copy.deepcopy(init_state), atk_intent, self.rules, roll_overrides=[2, 1])  # d20=2, Bless=1
        reroll_intent = {"type": "reroll", "actor_id": "pc-aldric"}
        reroll_res = step(miss_res["state"], reroll_intent, self.rules, roll_overrides=[18, 4])  # d20=18, dmg=4

        good_trace = {
            "initial_state": init_state,
            "actions": [
                {"seq": 1, "actor_id": "pc-aldric", "intent": atk_intent, "events": miss_res["events"]},
                {"seq": 2, "actor_id": "pc-aldric", "intent": reroll_intent, "events": reroll_res["events"]},
            ],
        }
        ok = _audit(self._write("bless-reroll-ok.json", good_trace))
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.assertRegex(
            ok.stdout,
            r"ok\s+[1-9]\d* checks\s+0 bad\s+Heroic Inspiration reroll preserves original bonus_dice",
        )

        # Simulate pre-#85 bug: Bless d4 changed from 1 -> 2 on reroll (and total adjusted accordingly).
        bad_trace = copy.deepcopy(good_trace)
        for ev in bad_trace["actions"][1]["events"]:
            if ev.get("type") == "attack_roll":
                ev["bonus_dice"][0]["rolls"] = [2]
                ev["bonus_dice"][0]["delta"] = 2
                ev["total"] += 1
                break
        bad = _audit(self._write("bless-reroll-bad.json", bad_trace))
        self.assertEqual(bad.returncode, 1, bad.stdout)
        self.assertRegex(
            bad.stdout,
            r"FAIL\s+\d+ checks\s+[1-9]\d* bad\s+Heroic Inspiration reroll preserves original bonus_dice",
        )

    def test_committed_session_traces_pass_audit_with_zero_regressions(self) -> None:
        traces = sorted(SESSIONS_DIR.glob("*.trace.json"))
        self.assertTrue(traces, "expected committed traces under examples/sessions/")
        res = _audit(*traces)
        self.assertEqual(res.returncode, 0, res.stdout)


if __name__ == "__main__":
    unittest.main()
