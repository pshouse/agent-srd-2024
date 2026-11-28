"""#67: a harmful Self-range spell never targets its own caster.

`cast_spell` defaulted every Self-range spell with no `target_ids` to the caster. That's right for Shield or Misty
Step, but Spirit Guardians, Burning Hands, Thunderwave and the rest start at the caster and affect others, so the
caster saved against and took their own damage. Now:
- the caster is dropped from `target_ids` for any Self-range spell resolved by an attack roll or a save;
- an area spell (cone, cube, line, emanation) with nobody else in it is still legal and affects no one;
- a single-target one (Produce Flame, Vampiric Touch, ...) with no other target is refused, like Fire Bolt.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.arena import load_scenario
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore


def _harmful_self_spells(rules: RulesStore) -> list[str]:
    return sorted(
        slug
        for slug, rec in rules._data["spells"].items()
        if str(rec.get("range_kind") or "").lower() == "self"
        and str(rec.get("resolution") or "").lower() in ("attack", "save")
        and rec.get("coverage") != "narrative"
    )


class TestSelfRangeHarmfulSpells(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.spells = _harmful_self_spells(cls.rules)
        scen = load_scenario("wyrmling-lair", cls.rules)
        caster = copy.deepcopy(next(c for c in scen["combatants"] if c["id"] == "pc-lyra"))
        sc = caster["statblock"]["spellcasting"]
        sc["cantrips"] = [s for s in cls.spells if cls.rules.get_spell(s)["level"] == 0]
        sc["prepared_spells"] = [s for s in cls.spells if cls.rules.get_spell(s)["level"] > 0]
        sc["slots"] = {str(lvl): 9 for lvl in range(1, 10)}
        enemy = next(c for c in scen["combatants"] if c["id"] == "hound-1")
        enemy = {**copy.deepcopy(enemy), "position": {"x": 5, "y": 0}}
        caster["position"] = {"x": 0, "y": 0}
        st = step(None, {"type": "start_encounter", "seed": 5, "combatants": [caster, enemy]}, cls.rules)["state"]
        st["active_index"] = st["turn_order"].index("pc-lyra")
        cls.state = st

    def _cast(self, slug: str, targets: list[str] | None) -> dict[str, Any]:
        rec = self.rules.get_spell(slug)
        intent: dict[str, Any] = {"type": "cast_spell", "actor_id": "pc-lyra", "spell": slug}
        if rec["level"] > 0:
            intent["slot_level"] = rec["level"]
        if targets is not None:
            intent["target_ids"] = targets
        st = copy.deepcopy(self.state)
        st["combatants"]["pc-lyra"]["turn_resources"].update({"action": True, "bonus_action": True, "reaction": True})
        return step(st, intent, self.rules)

    def _hits_caster(self, res: dict[str, Any]) -> list[dict[str, Any]]:
        return [e for e in res["events"] if e.get("target_id") == "pc-lyra"
                and e["type"] in ("damage_applied", "saving_throw", "spell_attack_roll", "condition_applied")
                and e.get("reason") != "concentration"]

    def test_the_known_cases_are_covered(self) -> None:
        for slug in ("spirit-guardians", "burning-hands", "thunderwave", "lightning-bolt", "cone-of-cold",
                     "color-spray", "fear", "produce-flame", "vampiric-touch"):
            self.assertIn(slug, self.spells)

    def test_caster_is_never_hit_by_its_own_spell(self) -> None:
        for slug in self.spells:
            for targets in (None, [], ["pc-lyra"], ["pc-lyra", "hound-1"]):
                with self.subTest(spell=slug, targets=targets):
                    res = self._cast(slug, targets)
                    self.assertEqual(self._hits_caster(res), [], res.get("narrative_hooks"))

    def test_empty_area_is_a_legal_cast_and_single_target_is_refused(self) -> None:
        for slug in self.spells:
            rec = self.rules.get_spell(slug)
            area = (rec.get("targeting") or {}).get("mode") == "aoe"
            with self.subTest(spell=slug, area=area):
                res = self._cast(slug, ["pc-lyra"])
                if area:
                    self.assertEqual(res["status"], "ok", res.get("error"))
                    self.assertIn("no other creature is in its area", " ".join(res["narrative_hooks"]))
                    slots_before = sum(self.state["combatants"]["pc-lyra"]["spellcasting"]["slots"].values())
                    slots_after = sum(res["state"]["combatants"]["pc-lyra"]["spellcasting"]["slots"].values())
                    self.assertEqual(slots_after, slots_before - (1 if rec["level"] > 0 else 0))
                else:
                    self.assertEqual(res["status"], "invalid_intent")
                    self.assertIn("other than its caster", res["error"])

    def test_spirit_guardians_with_an_enemy_targets_only_the_enemy(self) -> None:
        res = self._cast("spirit-guardians", ["pc-lyra", "hound-1"])
        self.assertEqual(res["status"], "ok", res.get("error"))
        saves = [e["target_id"] for e in res["events"] if e["type"] == "saving_throw" and e.get("reason") != "concentration"]
        self.assertEqual(saves, ["hound-1"])

    def test_self_buffs_still_default_to_the_caster(self) -> None:
        st = copy.deepcopy(self.state)
        st["combatants"]["pc-lyra"]["spellcasting"]["prepared_spells"].append("mage-armor")
        res = step(st, {"type": "cast_spell", "actor_id": "pc-lyra", "spell": "mage-armor", "slot_level": 1}, self.rules)
        self.assertEqual(res["status"], "ok", res.get("error"))
        self.assertTrue(any(e.get("kind") == "mage_armor" for e in res["state"]["combatants"]["pc-lyra"]["active_effects"]))


if __name__ == "__main__":
    unittest.main()
