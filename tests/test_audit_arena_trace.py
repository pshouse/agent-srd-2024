"""scripts/audit_arena_trace.py: clean on a real match, and catches an illegal own-turn Shield (#41).

Also #63: Hold Person paralysis is keyed separately from Ghoul Claw paralysis, and a paralysis
that outlives the caster's concentration is flagged.
"""

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
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

from tests.test_reactions import _wizard
from tests.test_stage4_engine_hardening import _make_fighter, _start

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


def _rule_line(stdout: str, rule: str) -> str | None:
    for line in stdout.splitlines():
        if line.endswith(rule) and ("checks" in line):
            return line
    return None


class TestHoldPersonParalysisAudit(unittest.TestCase):
    """Concentration paralysis and Claw paralysis are checked against their own rules (#63)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.wizard = _wizard(cls.rules)
        cls.fighter = _make_fighter(
            cls.rules, name="Bram", level=5, weapons=["longsword"], mastery_weapons=["longsword"]
        )
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _write(self, name: str, trace: dict) -> Path:
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(trace))
        return path

    def _play(
        self,
        combatants: list[dict],
        steps: list[tuple[dict, list[int] | None]],
        *,
        init_rolls: list[int],
    ) -> dict:
        state = _start(self.rules, combatants, init_rolls=init_rolls)
        actions = []
        for seq, (intent, rolls) in enumerate(steps, start=1):
            res = step(state, intent, self.rules, roll_overrides=rolls)
            self.assertEqual(res["status"], "ok", res.get("error"))
            actions.append({"seq": seq, "actor_id": intent.get("actor_id"), "intent": intent, "events": res["events"]})
            state = res["state"]
        return {"initial_state": _start(self.rules, combatants, init_rolls=init_rolls), "actions": actions}

    def _hold_person_trace(self) -> dict:
        """Wizard paralyzes a Bandit, the Bandit fails the end-of-turn save, then a Wolf
        breaks the Wizard's concentration. The Fighter then attacks the Bandit."""
        combatants = [
            {"id": "wiz", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
            {"id": "bandit", "side": "enemy", "monster": "bandit", "name": "Bandit", "position": {"x": 30, "y": 0}},
            {"id": "wolf", "side": "enemy", "monster": "wolf", "name": "Wolf", "position": {"x": 5, "y": 0}},
            {"id": "fighter", "side": "party", "statblock": self.fighter, "position": {"x": 30, "y": 5}},
        ]
        steps: list[tuple[dict, list[int] | None]] = [
            ({"type": "cast_spell", "actor_id": "wiz", "spell": "hold-person", "slot_level": 2, "target_ids": ["bandit"]}, [1]),
            ({"type": "end_turn", "actor_id": "wiz"}, None),
            ({"type": "end_turn", "actor_id": "bandit"}, [1]),  # repeat WIS save fails; paralysis continues
            # Bite hits (d20 15 + 4), 1d6+2 = 6, concentration CON save 1 fails. Shield would not turn 19 into a miss.
            ({"type": "attack", "actor_id": "wolf", "action": "Bite", "target_ids": ["wiz"]}, [15, 4, 1]),
            ({"type": "end_turn", "actor_id": "wolf"}, None),
            ({"type": "attack", "actor_id": "fighter", "target_id": "bandit", "weapon": "longsword"}, [12, 4, 14, 3]),
        ]
        return self._play(combatants, steps, init_rolls=[20, 18, 12, 1])

    def _claw_trace(self) -> dict:
        combatants = [
            {"id": "ghoul", "side": "enemy", "monster": "ghoul", "position": {"x": 5, "y": 0}},
            {"id": "fighter", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
        ]
        steps: list[tuple[dict, list[int] | None]] = [
            # Claw +4 vs AC 18-ish. d20 18 hits; 1d4+2; CON save fails.
            ({"type": "attack", "actor_id": "ghoul", "action": "Claw", "target_ids": ["fighter"]}, [18, 3, 1]),
            ({"type": "end_turn", "actor_id": "ghoul"}, None),
            ({"type": "end_turn", "actor_id": "fighter"}, None),
        ]
        # Ghoul's d20 is 20. The fighter has Alert, so its roll has to be low or it acts first.
        return self._play(combatants, steps, init_rolls=[20, 1])

    def test_concentration_break_ends_hold_person_and_not_the_claw_clock(self) -> None:
        trace = self._hold_person_trace()
        # The Bandit's own turn ended while the paralysis was still up. That must not be
        # scored against the Ghoul Claw "end of next turn" rule.
        self.assertTrue(
            any(
                e["type"] == "turn_ended" and e["actor_id"] == "bandit"
                for a in trace["actions"]
                for e in a["events"]
            )
        )
        self.assertFalse(
            any(
                e["type"] == "condition_ended" and e.get("condition") == "paralyzed" and e.get("target_id") == "bandit"
                for a in trace["actions"][:3]
                for e in a["events"]
            )
        )
        res = _audit(self._write("hold-ok.json", trace))
        self.assertEqual(res.returncode, 0, res.stdout[-2500:])
        line = _rule_line(res.stdout, "Hold Person paralysis ends when concentration ends")
        self.assertIsNotNone(line, res.stdout)
        self.assertIn("ok", line)
        self.assertNotIn("paralysis ends at end of target's next turn", res.stdout)

    def test_paralysis_that_outlives_concentration_is_flagged(self) -> None:
        trace = self._hold_person_trace()
        marked = False
        for action in trace["actions"]:
            if action["intent"].get("weapon") != "longsword":
                continue
            for event in action["events"]:
                if event["type"] == "attack_roll" and event.get("target_id") == "bandit":
                    event["advantage_sources"] = list(event.get("advantage_sources") or []) + ["target_paralyzed"]
                    event["disadvantage_sources"] = list(event.get("disadvantage_sources") or []) + ["audit_probe"]
                    marked = True
                    break
            if marked:
                break
        self.assertTrue(marked, "fighter attack on the bandit was not in the trace")
        res = _audit(self._write("hold-persist.json", trace))
        self.assertEqual(res.returncode, 1, res.stdout[-2500:])
        self.assertRegex(res.stdout, r"FAIL\s+\d+ checks\s+[1-9]\d* bad\s+Hold Person paralysis ends when concentration ends")

    def test_claw_duration_is_not_applied_to_hold_person(self) -> None:
        claw = self._claw_trace()
        clean = _audit(self._write("claw-ok.json", claw))
        self.assertEqual(clean.returncode, 0, clean.stdout[-2500:])
        claw_line = _rule_line(clean.stdout, "paralysis ends at end of target's next turn")
        self.assertIsNotNone(claw_line, clean.stdout)
        self.assertIn("ok", claw_line)

        for action in claw["actions"]:
            action["events"] = [
                e for e in action["events"]
                if not (e["type"] == "condition_ended" and e.get("condition") == "paralyzed")
            ]
        bad = _audit(self._write("claw-bad.json", claw))
        self.assertEqual(bad.returncode, 1, bad.stdout[-2500:])
        self.assertRegex(bad.stdout, r"FAIL\s+\d+ checks\s+[1-9]\d* bad\s+paralysis ends at end of target's next turn")


if __name__ == "__main__":
    unittest.main()
