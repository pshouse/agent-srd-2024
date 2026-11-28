"""#51: either/or and delayed spell damage clauses must not be summed into one hit.

Spirit Guardians used to deal 3d8 radiant + 3d8 necrotic (6d8; 18 on a successful save, which half of 3d8 can't
reach). Flame Strike, Ice Storm and Meteor Swarm really do deal two types at once and must stay additive.
"""

from __future__ import annotations

import copy
import unittest

from srd2024.arena import load_scenario
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore


def _dmg(rules: RulesStore, slug: str) -> list[tuple[str, str]]:
    return [(d["dice"], d["type"]) for d in rules.get_spell(slug)["damage"]]


class TestSpellDamageClauses(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_either_or_damage_type_is_one_entry_with_choices(self) -> None:
        sg = self.rules.get_spell("spirit-guardians")
        self.assertEqual(_dmg(self.rules, "spirit-guardians"), [("3d8", "radiant")])
        self.assertEqual(sg["damage"][0]["type_choices"], ["radiant", "necrotic"])
        fs = self.rules.get_spell("fire-shield")
        self.assertEqual(_dmg(self.rules, "fire-shield"), [("2d8", "fire")])
        self.assertEqual(fs["damage"][0]["type_choices"], ["fire", "cold"])

    def test_delayed_and_secondary_clauses_are_dropped_and_graded_partial(self) -> None:
        expected = {
            "acid-arrow": [("4d4", "acid")],  # + 2d4 at the end of its next turn
            "vitriolic-sphere": [("10d4", "acid")],  # + another 5d4 at the end of its next turn
            "ice-knife": [("1d10", "piercing")],  # + 2d6 cold explosion with its own DEX save
            "wall-of-ice": [("10d6", "cold")],
            "wall-of-thorns": [("7d8", "piercing")],
            "weird": [("10d10", "psychic")],
            "tsunami": [("6d10", "bludgeoning")],
            "prismatic-spray": [("12d6", "acid")],
            "prismatic-wall": [("12d6", "acid")],
            "storm-of-vengeance": [("2d6", "thunder")],
        }
        for slug, dmg in expected.items():
            with self.subTest(spell=slug):
                rec = self.rules.get_spell(slug)
                self.assertEqual(_dmg(self.rules, slug), dmg)
                self.assertEqual(rec["coverage"], "partial")
                self.assertIn("secondary_or_delayed_damage_clause", rec["provenance"]["unmodeled_clauses"])

    def test_truly_additive_spells_stay_additive(self) -> None:
        self.assertEqual(_dmg(self.rules, "flame-strike"), [("5d6", "fire"), ("5d6", "radiant")])
        self.assertEqual(_dmg(self.rules, "ice-storm"), [("2d10", "bludgeoning"), ("4d6", "cold")])
        self.assertEqual(_dmg(self.rules, "meteor-swarm"), [("20d6", "fire"), ("20d6", "bludgeoning")])
        self.assertEqual(self.rules.get_spell("flame-strike")["coverage"], "full")
        self.assertEqual(self.rules.get_spell("meteor-swarm")["coverage"], "full")

    def test_no_multi_entry_spell_is_left_unclassified(self) -> None:
        multi = sorted(
            slug for slug, rec in self.rules.data["spells"].items() if len(rec.get("damage") or []) > 1
        )
        self.assertEqual(multi, ["flame-strike", "ice-storm", "meteor-swarm"])

    def test_spirit_guardians_damage_stays_within_3d8(self) -> None:
        """Across many seeds: a failed save takes <= 24, a successful one <= 12 (it used to reach 18 and 48)."""
        scen = load_scenario("crypt-of-ghouls", self.rules)
        combatants = copy.deepcopy(scen["combatants"])
        for c in combatants:
            if c["id"] == "pc-dorian":
                c["position"] = {"x": 20, "y": 0}
            elif c.get("side") == "enemy":
                c["position"] = {"x": 25, "y": combatants.index(c) * 5 % 15}
        fails = succ = 0
        for seed in range(1, 41):
            st = step(None, {"type": "start_encounter", "seed": seed, "combatants": combatants}, self.rules)["state"]
            st["active_index"] = st["turn_order"].index("pc-dorian")
            enemies = [cid for cid, c in st["combatants"].items() if c.get("side") == "enemy"]
            # Explicit targets: with none, the engine currently defaults a self-range spell to its caster (filed separately).
            intent = {"type": "cast_spell", "actor_id": "pc-dorian", "spell": "spirit-guardians", "slot_level": 3, "target_ids": enemies}
            res = step(st, intent, self.rules)
            self.assertEqual(res["status"], "ok", res.get("error"))
            saves = {e["target_id"]: e["success"] for e in res["events"] if e["type"] == "saving_throw"}
            for e in res["events"]:
                if e["type"] != "damage_applied" or e.get("source_id") != "pc-dorian":
                    continue
                self.assertEqual(len(e["breakdown"]), 1, e["breakdown"])
                raw = sum(b["raw"] for b in e["breakdown"])
                if saves.get(e["target_id"]):
                    succ += 1
                    self.assertLessEqual(raw, 12, e)
                else:
                    fails += 1
                    self.assertLessEqual(raw, 24, e)
        self.assertGreater(fails, 0)
        self.assertGreater(succ, 0)


if __name__ == "__main__":
    unittest.main()
