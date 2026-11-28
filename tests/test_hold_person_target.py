"""Hold Person targets a Humanoid (#63).

The engine refuses every other creature type, including an unknown type (fail closed).
The legal action menu and Jev's candidate list use the same check, so they do not offer
a cast `step()` would reject. Every driver submits intents to `step()`, so the refusal
is not implemented in a driver.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.arena.human import build_human_action_menu
from srd2024.arena.jev import enumerate_candidates
from srd2024.arena.runner import build_observation
from srd2024.arena.scenarios import load_scenario
from srd2024.engine.combat import creature_type_of, spell_target_allowed, step
from srd2024.engine.store import RulesStore

from tests.test_reactions import _wizard
from tests.test_stage4_engine_hardening import _make_fighter, _start


def _names(combatant: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for cond in combatant.get("conditions") or []:
        out.add(cond["condition"] if isinstance(cond, dict) else str(cond))
    return out


class TestHoldPersonTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.wizard = _wizard(cls.rules)
        cls.fighter = _make_fighter(cls.rules, name="Bram", level=5, weapons=["longsword"], mastery_weapons=["longsword"])

    def _encounter(self) -> dict[str, Any]:
        st = _start(
            self.rules,
            [
                {"id": "wiz", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 5, "y": 5}},
                {"id": "guard", "side": "enemy", "monster": "bandit", "name": "Bandit", "position": {"x": 15, "y": 0}},
                {"id": "ghoul", "side": "enemy", "monster": "ghoul", "name": "Ghoul", "position": {"x": 20, "y": 0}},
                {"id": "wolf", "side": "enemy", "monster": "wolf", "name": "Wolf", "position": {"x": 25, "y": 0}},
                {"id": "mystery", "side": "enemy", "monster": "wolf", "name": "Mystery", "position": {"x": 30, "y": 0}},
            ],
        )
        # A monster id the rules store cannot resolve. No creature_type is stored on the combatant.
        st["combatants"]["mystery"]["source_id"] = "monsters:not-a-real-creature"
        return st

    def _cast(self, st: dict[str, Any], target: str) -> dict[str, Any]:
        return step(
            st,
            {"type": "cast_spell", "actor_id": "wiz", "spell": "hold-person", "slot_level": 2, "target_ids": [target]},
            self.rules,
            roll_overrides=[1],
        )

    def test_unknown_creature_types_fail_closed(self) -> None:
        """A missing record or a blank creature_type is unknown, and unknown is not a Humanoid."""
        self.assertEqual(creature_type_of({"kind": "pc", "source_id": "classes:wizard"}, self.rules), "humanoid")
        self.assertEqual(creature_type_of({"kind": "monster", "source_id": "monsters:ghoul"}, self.rules), "undead")
        self.assertEqual(creature_type_of({"kind": "monster", "source_id": "monsters:wolf"}, self.rules), "beast")
        self.assertIsNone(creature_type_of({"kind": "monster", "source_id": ""}, self.rules))
        self.assertIsNone(creature_type_of({"kind": "monster", "source_id": "monsters:not-a-real-creature"}, self.rules))

        class _Blank:
            def get_monster(self, _identifier: str) -> dict[str, str]:
                return {"creature_type": "  "}

        self.assertIsNone(creature_type_of({"kind": "monster", "source_id": "monsters:whatever"}, _Blank()))  # type: ignore[arg-type]
        spell = self.rules.get_spell("hold-person")
        self.assertFalse(spell_target_allowed("hold-person", spell, {"kind": "monster", "source_id": ""}, self.rules))
        self.assertTrue(spell_target_allowed("hold-person", spell, {"kind": "pc"}, self.rules))
        self.assertTrue(spell_target_allowed("fireball", self.rules.get_spell("fireball"), {"kind": "monster", "source_id": ""}, self.rules))

    def test_engine_refuses_undead_beast_and_unknown_and_accepts_humanoid(self) -> None:
        """`step()` is the intent check every driver hits: heuristic, greedy, random, talk-spam, LLM, Jev."""
        st = self._encounter()
        slots_before = copy.deepcopy(st["combatants"]["wiz"]["spellcasting"]["slots"])

        for target in ("ghoul", "wolf", "mystery"):
            before = copy.deepcopy(st)
            res = self._cast(st, target)
            self.assertEqual(res["status"], "invalid_intent", res.get("error"))
            self.assertIn("Hold Person requires a Humanoid target", res["error"])
            self.assertEqual(res["events"], [])
            self.assertEqual(st, before, "a refused cast must not change the caller's state")
            self.assertEqual(res["state"]["combatants"]["wiz"]["spellcasting"]["slots"], slots_before)
            self.assertIsNone(res["state"]["combatants"]["wiz"]["concentration"])
            self.assertNotIn("paralyzed", _names(res["state"]["combatants"][target]))
        self.assertIn("unknown creature types are refused", self._cast(st, "mystery")["error"])

        res = self._cast(st, "guard")
        self.assertEqual(res["status"], "ok", res.get("error"))
        self.assertEqual(res["state"]["combatants"]["wiz"]["concentration"]["spell"], "hold-person")
        self.assertIn("paralyzed", _names(res["state"]["combatants"]["guard"]))

        # A player character is a Humanoid even though source_id is a class, not a monster.
        st_pc = self._encounter()
        res_pc = self._cast(st_pc, "pc")
        self.assertEqual(res_pc["status"], "ok", res_pc.get("error"))
        self.assertIn("paralyzed", _names(res_pc["state"]["combatants"]["pc"]))

        # One illegal target refuses the whole cast. The Humanoid is not paralyzed either.
        st_mix = self._encounter()
        res_mix = step(
            st_mix,
            {
                "type": "cast_spell",
                "actor_id": "wiz",
                "spell": "hold-person",
                "slot_level": 3,
                "target_ids": ["guard", "ghoul"],
            },
            self.rules,
            roll_overrides=[1, 1],
        )
        self.assertEqual(res_mix["status"], "invalid_intent")
        self.assertIn("Hold Person requires a Humanoid target", res_mix["error"])
        self.assertNotIn("paralyzed", _names(res_mix["state"]["combatants"]["guard"]))
        self.assertEqual(res_mix["state"]["combatants"]["wiz"]["spellcasting"]["slots"], slots_before)

    def test_menu_and_jev_omit_hold_person_on_non_humanoids(self) -> None:
        st = self._encounter()
        scenario = {"id": "mixed", "name": "Mixed", "description": ""}
        obs = build_observation(st, "wiz", scenario, {"party": [], "enemy": []}, [], {}, self.rules)
        menu = build_human_action_menu(obs, self.rules)
        hold = [m for m in menu if m["intent"].get("spell") == "hold-person"]
        self.assertTrue(hold, "a Humanoid in range should still be offered")
        self.assertEqual({m["intent"]["target_ids"][0] for m in hold}, {"guard"})

        names = [name for name, _, _ in enumerate_candidates(obs, self.rules)]
        self.assertTrue(any("hold-person" in name and "guard" in name for name in names), names)
        self.assertFalse(any("ghoul" in name and "hold-person" in name for name in names), names)
        self.assertFalse(any("wolf" in name and "hold-person" in name for name in names), names)
        self.assertFalse(any("mystery" in name and "hold-person" in name for name in names), names)

        # Built-in crypt enemies are Undead. The menu matches validation: no Hold Person rows.
        crypt = load_scenario("crypt-of-ghouls", self.rules)
        started = step(None, {"type": "start_encounter", "seed": 1, "combatants": crypt["combatants"]}, self.rules)
        self.assertEqual(started["status"], "ok", started.get("error"))
        crypt_obs = build_observation(
            started["state"], "pc-dorian", crypt, {"party": [], "enemy": []}, [], {}, self.rules
        )
        crypt_menu = build_human_action_menu(crypt_obs, self.rules)
        self.assertFalse(any(m["intent"].get("spell") == "hold-person" for m in crypt_menu))


if __name__ == "__main__":
    unittest.main()
