"""#85: Heroic Inspiration rerolls one die, the d20, and keeps the original Bless/Bane dice.

SRD 5.2: "you can expend it to reroll any one die immediately after rolling it, and you must use the new roll."
Before the fix the reroll re-resolved the whole attack and rolled Bless's d4 again (wyrm-web-1, round 2: d4 1 -> 2).

Documented choice: under Advantage or Disadvantage the reroll replaces the d20 test (both dice), as before.
Replacing only the die that counted would conflict with the Advantage/Disadvantage pick the rest of the engine and
the audit assume, so that stays a follow-up.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.arena import load_scenario
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore


def _bless(target: str, sign_kind: str = "bless") -> dict[str, Any]:
    return {"id": f"{sign_kind}-t", "kind": sign_kind, "source_id": "pc-dorian", "target_id": target, "value": "1d4",
            "applies_to": ["attack", "save"], "expires": "concentration", "created_round": 1, "concentration_spell": None}


class TestHeroicInspirationOneDie(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        scen = load_scenario("wyrmling-lair", cls.rules)
        cls.combatants = {c["id"]: c for c in scen["combatants"]}

    def _duel(self, attacker: str, *, effects: list[dict[str, Any]], foe_pos: dict[str, int]) -> dict[str, Any]:
        pc = copy.deepcopy(self.combatants[attacker])
        pc.update({"position": {"x": 0, "y": 0}, "heroic_inspiration": True})
        foe = {"id": "gob", "side": "enemy", "monster": "goblin-warrior", "position": foe_pos}
        st = step(None, {"type": "start_encounter", "seed": 2, "combatants": [pc, foe]}, self.rules, roll_overrides=[20, 1])["state"]
        self.assertEqual(st["turn_order"][st["active_index"]], attacker)
        st["combatants"][attacker]["active_effects"].extend(copy.deepcopy(effects))
        return st

    @staticmethod
    def _rolls(res: dict[str, Any], etype: str) -> list[dict[str, Any]]:
        return [e for e in res["events"] if e["type"] == etype and not e.get("reaction")]

    def test_weapon_reroll_keeps_the_bless_die(self) -> None:
        st = self._duel("pc-aldric", effects=[_bless("pc-aldric")], foe_pos={"x": 5, "y": 0})
        miss = step(st, {"type": "attack", "actor_id": "pc-aldric", "target_id": "gob", "weapon": "longsword"},
                    self.rules, roll_overrides=[2, 1])  # d20 2, Bless d4 1
        first = self._rolls(miss, "attack_roll")[0]
        self.assertFalse(first["hit"])
        self.assertEqual(first["bonus_dice"][0]["rolls"], [1])
        self.assertEqual(miss["state"]["pending_reroll"]["bonus_dice"], [first["bonus_dice"]])

        rr = step(miss["state"], {"type": "reroll", "actor_id": "pc-aldric"}, self.rules, roll_overrides=[18, 4])
        self.assertEqual(rr["status"], "ok", rr.get("error"))
        again = self._rolls(rr, "attack_roll")[0]
        self.assertEqual(again["chosen"], 18)
        self.assertEqual(again["bonus_dice"], first["bonus_dice"], "the Bless d4 must not be rerolled")
        self.assertEqual(again["total"], 18 + again["attack_bonus"] + 1 - again.get("exhaustion_penalty", 0))
        dmg = [e for e in rr["events"] if e["type"] == "damage_applied"][0]
        sword = next(w for w in st["combatants"]["pc-aldric"]["weapons"] if w["slug"] == "longsword")
        self.assertEqual(dmg["total_damage"], 4 + int(sword["damage_bonus"]),
                         "the next override die is damage, not a second Bless d4")
        state_combatants = rr["state"]["combatants"].values()
        self.assertTrue(all("_pinned_bonus_dice" not in c for c in state_combatants))

    def test_bane_penalty_is_kept_too(self) -> None:
        st = self._duel("pc-aldric", effects=[_bless("pc-aldric", "bane")], foe_pos={"x": 5, "y": 0})
        miss = step(st, {"type": "attack", "actor_id": "pc-aldric", "target_id": "gob", "weapon": "longsword"},
                    self.rules, roll_overrides=[3, 4])
        first = self._rolls(miss, "attack_roll")[0]
        self.assertEqual(first["bonus_dice"][0]["delta"], -4)
        rr = step(miss["state"], {"type": "reroll", "actor_id": "pc-aldric"}, self.rules, roll_overrides=[10, 1])
        self.assertEqual(self._rolls(rr, "attack_roll")[0]["bonus_dice"], first["bonus_dice"])

    def test_multi_ray_spell_reroll_keeps_each_rays_bless_die(self) -> None:
        st = self._duel("pc-lyra", effects=[_bless("pc-lyra")], foe_pos={"x": 30, "y": 0})
        cast = {"type": "cast_spell", "actor_id": "pc-lyra", "spell": "scorching-ray", "slot_level": 2, "target_ids": ["gob"]}
        miss = step(st, cast, self.rules, roll_overrides=[1, 1, 1, 2, 1, 3])  # three rays: d20 1 + d4 1/2/3, all miss
        rays = self._rolls(miss, "spell_attack_roll")
        self.assertEqual(len(rays), 3)
        self.assertFalse(any(r["hit"] for r in rays))
        self.assertIsNotNone(miss["state"]["pending_reroll"])
        rr = step(miss["state"], {"type": "reroll", "actor_id": "pc-lyra"}, self.rules)
        self.assertEqual(rr["status"], "ok", rr.get("error"))
        self.assertEqual([r["bonus_dice"] for r in self._rolls(rr, "spell_attack_roll")], [r["bonus_dice"] for r in rays])

    def test_without_bonus_dice_reroll_is_unchanged(self) -> None:
        st = self._duel("pc-aldric", effects=[], foe_pos={"x": 5, "y": 0})
        miss = step(st, {"type": "attack", "actor_id": "pc-aldric", "target_id": "gob", "weapon": "longsword"},
                    self.rules, roll_overrides=[2])
        self.assertEqual(miss["state"]["pending_reroll"]["bonus_dice"], [[]])
        rr = step(miss["state"], {"type": "reroll", "actor_id": "pc-aldric"}, self.rules, roll_overrides=[19, 5])
        again = self._rolls(rr, "attack_roll")[0]
        self.assertEqual((again["chosen"], again["bonus_dice"]), (19, []))


if __name__ == "__main__":
    unittest.main()
