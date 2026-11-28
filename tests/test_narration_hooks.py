"""#56, #61, #62: narration hooks for monster crits, rider saves, death saves, and 0-HP ordering.

Checked as properties over real heuristic-vs-heuristic games, so every code path that fires in the built-in
scenarios is covered. Hooks are display-only. The digest covers events, final_state, winner and scorecard,
so it doesn't change (see `test_digests_unchanged_by_narration`).
"""

from __future__ import annotations

import re
import unittest
from typing import Any

from srd2024.arena import HeuristicTacticalDriver, run_match
from srd2024.engine.store import RulesStore

SCENARIOS = ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair")
SEEDS = range(1, 11)


class TestNarrationHooks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.games: list[dict[str, Any]] = []
        for s in SCENARIOS:
            for seed in SEEDS:
                cls.games.append(run_match(s, party_driver=HeuristicTacticalDriver(cls.rules),
                                           enemy_driver=HeuristicTacticalDriver(cls.rules), rules=cls.rules, seed=seed))

    def _actions(self):
        for g in self.games:
            for a in g["actions"]:
                yield g, a

    def _name(self, g: dict[str, Any], cid: str) -> str:
        return g["initial_state"]["combatants"][cid]["name"]

    def test_monster_crits_say_critical(self) -> None:
        seen = 0
        for g, a in self._actions():
            for e in a["events"]:
                if e["type"] == "attack_roll" and e.get("hit") and e.get("crit") and e.get("action") and not e.get("reaction"):
                    seen += 1
                    att, tgt = self._name(g, e["actor_id"]), self._name(g, e["target_id"])
                    self.assertTrue(any(h.startswith(f"{att} hits {tgt} with {e['action']} (CRITICAL HIT!)")
                                        for h in a["narrative_hooks"]), a["narrative_hooks"])
        self.assertGreater(seen, 0, "no monster crit in the sample; widen SEEDS")

    def test_rider_saves_are_narrated_with_their_condition(self) -> None:
        seen = 0
        for g, a in self._actions():
            evs = a["events"]
            for i, e in enumerate(evs):
                if e["type"] != "saving_throw" or not str(e.get("reason", "")).endswith("_rider"):
                    continue
                seen += 1
                name = self._name(g, e["target_id"])
                verb = "succeeds on" if e["success"] else "fails"
                line = next((h for h in a["narrative_hooks"]
                             if h.startswith(f"{name} {verb} a {e['ability'].upper()} save (DC {e['dc']})")), None)
                self.assertIsNotNone(line, a["narrative_hooks"])
                applied = [x["condition"] for x in evs[i + 1:i + 4]
                           if x["type"] == "condition_applied" and x["target_id"] == e["target_id"]]
                for c in applied:
                    self.assertIn(c.title(), line)
        self.assertGreater(seen, 0)

    def test_every_death_save_is_narrated_with_the_tally(self) -> None:
        seen = 0
        for g, a in self._actions():
            for e in a["events"]:
                if e["type"] == "death_save":
                    seen += 1
                    name = self._name(g, e["actor_id"])
                    self.assertTrue(any(h.startswith(name) and "Death Save" in h for h in a["narrative_hooks"]),
                                    (e, a["narrative_hooks"]))
                    if e["outcome"] in ("success", "failure") and e["successes"] < 3 and e["failures"] < 3:
                        self.assertTrue(any(f"{e['successes']}/3 successes, {e['failures']}/3 failures" in h
                                            for h in a["narrative_hooks"]))
        self.assertGreater(seen, 0)

    def test_hit_line_comes_before_zero_hp_and_zero_hp_before_concentration_loss(self) -> None:
        checked = 0
        for g, a in self._actions():
            hooks = a["narrative_hooks"]
            for i, h in enumerate(hooks):
                m = re.match(r"(.+) drops to 0 HP", h)
                if not m:
                    continue
                who = m.group(1)
                hit = [j for j, x in enumerate(hooks) if f" hits {who} with " in x or (x.startswith(who) and " against " in x and "takes" in x)]
                if hit:
                    checked += 1
                    self.assertLess(min(hit), i, hooks)
                conc = [j for j, x in enumerate(hooks) if x.startswith(f"{who} loses concentration") and "dropped_to_0_hp" in x]
                for j in conc:
                    self.assertLess(i, j, hooks)
        self.assertGreater(checked, 0)

    def test_digests_unchanged_by_narration(self) -> None:
        # Pinned from main @ cd27177 (before this change): hooks aren't part of the digest.
        g = next(x for x in self.games if x["scenario"]["id"] == "crypt-of-ghouls" and x["config"]["seed"] == 2)
        self.assertTrue(g["digest"])
        from srd2024.arena.runner import compute_transcript_digest

        g2 = dict(g)
        g2["actions"] = [{**a, "narrative_hooks": []} for a in g["actions"]]
        self.assertEqual(compute_transcript_digest(g2), g["digest"])


if __name__ == "__main__":
    unittest.main()
