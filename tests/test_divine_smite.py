"""#6: Divine Smite (and Shining Smite) deal their damage on the melee hit they follow.

Before the fix, `cast_spell` dropped all damage for `resolution: auto_apply` spells: Divine Smite spent its slot
and did nothing. Other auto_apply spells with damage the engine doesn't model now say so
(`spell_damage_unresolved`), rather than failing silently.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from srd2024.engine.character import compile_character
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

ROOT = Path(__file__).resolve().parents[1]


class TestDivineSmite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        spec = json.loads((ROOT / "examples" / "characters" / "aldric-paladin-1.json").read_text())
        cls.sb = compile_character(spec, cls.rules)["statblock"]

    def _start(self, monster: str = "skeleton", *, slots: dict[str, int] | None = None, extra_spells: list[str] | None = None,
               monster_pos: dict[str, int] | None = None) -> dict[str, Any]:
        sb = copy.deepcopy(self.sb)
        if slots is not None:
            sb["spellcasting"]["slots"] = slots
        if extra_spells:
            sb["spellcasting"]["prepared_spells"] = list(sb["spellcasting"]["prepared_spells"]) + extra_spells
        st = step(None, {"type": "start_encounter", "seed": 1, "combatants": [
            {"id": "pc", "side": "party", "statblock": sb, "position": {"x": 0, "y": 0}},
            {"id": "m", "side": "enemy", "monster": monster, "position": monster_pos or {"x": 5, "y": 0}}]},
            self.rules, roll_overrides=[20, 1])["state"]
        self.assertEqual(st["turn_order"][st["active_index"]], "pc")
        return st

    def _hit(self, st: dict[str, Any], d20: int = 15, weapon: str = "longsword") -> dict[str, Any]:
        r = step(st, {"type": "attack", "actor_id": "pc", "target_id": "m", "weapon": weapon}, self.rules, roll_overrides=[d20, 1])
        self.assertEqual(r["status"], "ok", r.get("error"))
        return r["state"]

    def _smite(self, st: dict[str, Any], rolls: list[int], slot: int = 1, targets: list[str] | None = None) -> dict[str, Any]:
        return step(st, {"type": "cast_spell", "actor_id": "pc", "spell": "divine-smite", "slot_level": slot,
                         "target_ids": ["m"] if targets is None else targets}, self.rules, roll_overrides=rolls)

    @staticmethod
    def _radiant(res: dict[str, Any]) -> int:
        return sum(b["effective"] for e in res["events"] if e["type"] == "damage_applied"
                   for b in e["breakdown"] if b["type"] == "radiant")

    def test_requires_a_melee_hit_this_turn_and_spends_nothing_otherwise(self) -> None:
        st = self._start()
        r = self._smite(st, [8, 8])
        self.assertEqual(r["status"], "invalid_intent")
        self.assertIn("melee", r["error"])
        self.assertEqual(r["state"]["combatants"]["pc"]["spellcasting"]["slots"], st["combatants"]["pc"]["spellcasting"]["slots"])
        self.assertTrue(r["state"]["combatants"]["pc"]["turn_resources"]["bonus_action"])

    def test_undead_takes_extra_d8(self) -> None:
        r = self._smite(self._hit(self._start("skeleton")), [2, 2, 2])
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(self._radiant(r), 6)  # 2d8 + 1d8 vs Undead
        self.assertIn("vs Undead", r["narrative_hooks"][0])
        self.assertNotIn("last_melee_hit", r["state"]["combatants"]["pc"]["turn_resources"])

    def test_other_creature_types_take_base_dice(self) -> None:
        r = self._smite(self._hit(self._start("goblin-warrior")), [3, 3, 3])
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(self._radiant(r), 6)  # 2d8 only; the third override roll is unused

    def test_upcast_adds_a_d8_per_slot_level(self) -> None:
        st = self._start("goblin-warrior", slots={"1": 2, "2": 1})
        r = self._smite(self._hit(st), [1, 1, 1, 9], slot=2)
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(self._radiant(r), 3)  # 2d8 + 1d8 (slot 2)

    def test_crit_doubles_smite_dice(self) -> None:
        st = self._hit(self._start("goblin-warrior"), d20=20)
        self.assertTrue(st["combatants"]["pc"]["turn_resources"]["last_melee_hit"]["crit"])
        r = self._smite(st, [1, 1, 1, 1, 9])
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(self._radiant(r), 4)  # 4d8 on a crit
        self.assertIn("critical", r["narrative_hooks"][0])

    def test_wrong_target_and_ranged_hits_are_rejected(self) -> None:
        r = self._smite(self._hit(self._start()), [4, 4, 4], targets=["pc-nobody"])
        self.assertEqual(r["status"], "invalid_intent")
        st = self._start("goblin-warrior", monster_pos={"x": 20, "y": 0})
        st = self._hit(st, weapon="javelin")  # thrown from 20 ft: a ranged attack
        self.assertNotIn("last_melee_hit", st["combatants"]["pc"]["turn_resources"])
        self.assertEqual(self._smite(st, [4, 4])["status"], "invalid_intent")

    def test_marker_resets_next_turn(self) -> None:
        st = self._hit(self._start("goblin-warrior"))
        for _ in range(2):  # pc ends, monster ends: back to pc
            active = st["turn_order"][st["active_index"]]
            st = step(st, {"type": "end_turn", "actor_id": active}, self.rules)["state"]
        self.assertEqual(st["turn_order"][st["active_index"]], "pc")
        self.assertNotIn("last_melee_hit", st["combatants"]["pc"]["turn_resources"])
        self.assertEqual(self._smite(st, [4, 4])["status"], "invalid_intent")

    def test_unmodeled_auto_apply_damage_is_reported(self) -> None:
        st = self._start("goblin-warrior", extra_spells=["divine-favor"])
        r = step(st, {"type": "cast_spell", "actor_id": "pc", "spell": "divine-favor", "slot_level": 1}, self.rules)
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertIn("spell_damage_unresolved", [e["type"] for e in r["events"]])
        self.assertTrue(any("isn't modeled" in h for h in r["narrative_hooks"]))

    def test_non_smite_casters_get_no_marker(self) -> None:
        st = self._start("goblin-warrior")
        st["combatants"]["pc"]["spellcasting"]["prepared_spells"] = ["bless"]
        st = self._hit(st)
        self.assertNotIn("last_melee_hit", st["combatants"]["pc"]["turn_resources"])


if __name__ == "__main__":
    unittest.main()
