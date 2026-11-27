"""Reactions resolved by the engine (#33 Shield's trigger, #38 Opportunity Attacks).

Dice are fixed with `roll_overrides` so every branch is deterministic. Goblin Warrior: AC 15, Scimitar +4 (1d6+2),
reach 5 ft. The Elf Wizard fixture has Shield prepared and AC 11.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

from tests.test_stage4_engine_hardening import _make_fighter, _start
from srd2024.engine.character import compile_character


def _wizard(rules: RulesStore) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": "Talia",
        "species": "elf",
        "subspecies": "elven-lineage-high-elf",
        "background": "sage",
        "background_asi": {"int": 2, "con": 1},
        "origin_feat": "magic-initiate",
        "class": "wizard",
        "subclass": "evoker",
        "level": 5,
        "ability_method": "standard_array",
        "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
        "asi_choices": [{"level": 4, "type": "asi", "increases": {"int": 2}}],
        "equipment": {"armor": None, "shield": False, "weapons": ["quarterstaff"]},
        "cantrips": ["ray-of-frost", "fire-bolt", "prestidigitation"],
        "prepared_spells": ["fireball", "shield", "magic-missile", "misty-step", "hold-person", "false-life"],
        "hp_method": "fixed",
    }
    res = compile_character(spec, rules)
    assert res["valid"], res["errors"]
    return res["statblock"]


def _types(res: dict[str, Any]) -> list[str]:
    return [e["type"] for e in res["events"]]


def _ok(test: unittest.TestCase, res: dict[str, Any]) -> dict[str, Any]:
    test.assertEqual(res["status"], "ok", res.get("error"))
    return res["state"]


class TestOpportunityAttacks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter = _make_fighter(cls.rules, name="Bram", level=5, weapons=["longsword"], mastery_weapons=["longsword"])
        cls.wizard = _wizard(cls.rules)

    def _fighter_vs_goblin(self, gob_pos: dict[str, int] | None = None) -> dict[str, Any]:
        st = _start(
            self.rules,
            [
                {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
                {"id": "gob", "side": "enemy", "monster": "goblin-warrior", "position": gob_pos or {"x": 5, "y": 0}},
            ],
        )
        st["combatants"]["pc"]["hp"] = st["combatants"]["pc"]["hp_max"] = 60
        return st

    def test_leaving_reach_provokes_one_attack(self) -> None:
        st = self._fighter_vs_goblin()
        res = step(st, {"type": "move", "actor_id": "pc", "to": {"x": -10, "y": 0}}, self.rules, roll_overrides=[19, 4])
        st = _ok(self, res)
        oa = [e for e in res["events"] if e["type"] == "opportunity_attack"]
        self.assertEqual(len(oa), 1)
        self.assertEqual((oa[0]["actor_id"], oa[0]["target_id"], oa[0]["action"]), ("gob", "pc", "Scimitar"))
        atk = next(e for e in res["events"] if e["type"] == "attack_roll")
        self.assertEqual((atk["actor_id"], atk["target_id"], atk["total"], atk["hit"]), ("gob", "pc", 23, True))
        self.assertLess(_types(res).index("attack_roll"), _types(res).index("moved"), "attack happens before the move")
        self.assertEqual(st["combatants"]["pc"]["position"], {"x": -10, "y": 0})
        self.assertFalse(st["combatants"]["gob"]["turn_resources"]["reaction"])
        self.assertEqual(st["combatants"]["pc"]["hp"], 60 - (4 + 2))

        # Reaction spent: coming back and leaving again this round provokes nothing.
        st = _ok(self, step(st, {"type": "move", "actor_id": "pc", "to": {"x": 0, "y": 0}}, self.rules))
        res2 = step(st, {"type": "move", "actor_id": "pc", "to": {"x": -5, "y": 0}}, self.rules)
        self.assertNotIn("opportunity_attack", _types(_ok_res(self, res2)))

    def test_disengage_staying_in_reach_and_teleport_do_not_provoke(self) -> None:
        st = self._fighter_vs_goblin()
        # Moving within reach (still 5 ft away) is fine.
        res = step(st, {"type": "move", "actor_id": "pc", "to": {"x": 0, "y": 5}}, self.rules)
        self.assertNotIn("opportunity_attack", _types(_ok_res(self, res)))
        # Disengage, then leave reach: no attack.
        st = _ok(self, step(st, {"type": "disengage", "actor_id": "pc"}, self.rules))
        res = step(st, {"type": "move", "actor_id": "pc", "to": {"x": -20, "y": 0}}, self.rules)
        self.assertNotIn("opportunity_attack", _types(_ok_res(self, res)))
        self.assertTrue(res["state"]["combatants"]["gob"]["turn_resources"]["reaction"])

        # Teleporting (Misty Step) out of reach never provokes.
        st = _start(
            self.rules,
            [
                {"id": "wiz", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "gob", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        res = step(st, {"type": "cast_spell", "actor_id": "wiz", "spell": "misty-step", "slot_level": 2,
                        "destination": {"x": -30, "y": 0}}, self.rules)
        self.assertNotIn("opportunity_attack", _types(_ok_res(self, res)))

    def test_attack_that_drops_the_mover_ends_the_move(self) -> None:
        st = self._fighter_vs_goblin()
        st["combatants"]["pc"]["hp"] = 3
        res = step(st, {"type": "move", "actor_id": "pc", "to": {"x": -10, "y": 0}}, self.rules, roll_overrides=[19, 4])
        st = _ok(self, res)
        pc = st["combatants"]["pc"]
        self.assertEqual(pc["hp"], 0)
        self.assertEqual(pc["position"], {"x": 0, "y": 0}, "dropped before getting away")
        self.assertEqual(pc["movement_remaining_ft"], 0)
        self.assertNotIn("moved", _types(res))

    def test_pc_reacts_to_a_monster_leaving_with_its_best_melee_weapon(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "gob", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "pc", "side": "party", "statblock": self.fighter, "position": {"x": 0, "y": 0}},
            ],
        )
        res = step(st, {"type": "move", "actor_id": "gob", "to": {"x": 30, "y": 0}}, self.rules, roll_overrides=[15, 5])
        _ok(self, res)
        oa = next(e for e in res["events"] if e["type"] == "opportunity_attack")
        self.assertEqual((oa["actor_id"], oa["weapon"]), ("pc", "longsword"))
        atk = next(e for e in res["events"] if e["type"] == "attack_roll")
        self.assertEqual(atk["weapon"], "longsword")

    def test_incapacitated_or_policy_none_creatures_do_not_react(self) -> None:
        st = self._fighter_vs_goblin()
        st["combatants"]["gob"]["conditions"].append({"condition": "paralyzed", "duration": "indefinite"})
        res = step(st, {"type": "move", "actor_id": "pc", "to": {"x": -10, "y": 0}}, self.rules)
        self.assertNotIn("opportunity_attack", _types(_ok_res(self, res)))

        st = self._fighter_vs_goblin()
        st["combatants"]["gob"]["reaction_policy"] = "none"
        res = step(st, {"type": "move", "actor_id": "pc", "to": {"x": -10, "y": 0}}, self.rules)
        self.assertNotIn("opportunity_attack", _types(_ok_res(self, res)))


class TestShieldReaction(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.wizard = _wizard(cls.rules)

    def _goblins_vs_wizard(self) -> dict[str, Any]:
        st = _start(
            self.rules,
            [
                {"id": "gob", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "gob2", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 5}},
                {"id": "wiz", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
            ],
        )
        st["combatants"]["wiz"]["hp"] = st["combatants"]["wiz"]["hp_max"] = 40
        return st

    def _scimitar(self, st: dict[str, Any], who: str, rolls: list[int]) -> dict[str, Any]:
        return step(st, {"type": "attack", "actor_id": who, "action": "Scimitar", "target_ids": ["wiz"]}, self.rules,
                    roll_overrides=rolls)

    def test_shield_turns_a_close_hit_into_a_miss_and_lasts_the_round(self) -> None:
        st = self._goblins_vs_wizard()
        base_ac = st["combatants"]["wiz"]["ac"]
        slots_before = int(st["combatants"]["wiz"]["spellcasting"]["slots"]["1"])
        d20 = base_ac - 4 + 1  # total = AC + 1: a hit that +5 AC turns into a miss
        res = self._scimitar(st, "gob", [d20, 3])
        st = _ok(self, res)
        cast = next(e for e in res["events"] if e["type"] == "spell_cast")
        self.assertEqual((cast["actor_id"], cast["spell"], cast["as_reaction"], cast["trigger"], cast["trigger_source_id"]),
                         ("wiz", "shield", True, "hit_by_attack", "gob"))
        atk = next(e for e in res["events"] if e["type"] == "attack_roll")
        self.assertEqual((atk["total"], atk["target_ac"], atk["hit"]), (base_ac + 1, base_ac + 5, False))
        self.assertLess(_types(res).index("spell_cast"), _types(res).index("attack_roll"))
        wiz = st["combatants"]["wiz"]
        self.assertEqual(wiz["hp"], 40)
        self.assertFalse(wiz["turn_resources"]["reaction"])
        self.assertEqual(int(wiz["spellcasting"]["slots"]["1"]), slots_before - 1)

        # The +5 stays up against later attacks until the start of the wizard's next turn.
        st = _ok(self, step(st, {"type": "end_turn", "actor_id": "gob"}, self.rules))
        res = self._scimitar(st, "gob2", [base_ac - 4 + 3, 3])  # would hit base AC, misses AC + 5
        atk = next(e for e in _ok_res(self, res)["events"] if e["type"] == "attack_roll")
        self.assertEqual((atk["target_ac"], atk["hit"]), (base_ac + 5, False))
        self.assertNotIn("spell_cast", _types(res))
        st = _ok(self, step(res["state"], {"type": "end_turn", "actor_id": "gob2"}, self.rules))
        self.assertFalse(any(e.get("kind") == "shield" for e in st["combatants"]["wiz"]["active_effects"]),
                         "Shield ends at the start of the caster's next turn")

    def test_no_shield_when_it_would_not_help(self) -> None:
        for label, rolls in (("hit by 5 or more", lambda ac: [ac - 4 + 6, 3]), ("natural 20", lambda ac: [20, 3, 3])):
            with self.subTest(label):
                st = self._goblins_vs_wizard()
                res = self._scimitar(st, "gob", rolls(st["combatants"]["wiz"]["ac"]))
                _ok(self, res)
                self.assertNotIn("spell_cast", _types(res))
                self.assertTrue(next(e for e in res["events"] if e["type"] == "attack_roll")["hit"])
                self.assertTrue(res["state"]["combatants"]["wiz"]["turn_resources"]["reaction"])

    def test_no_shield_without_reaction_or_slots(self) -> None:
        st = self._goblins_vs_wizard()
        st["combatants"]["wiz"]["turn_resources"]["reaction"] = False
        res = self._scimitar(st, "gob", [st["combatants"]["wiz"]["ac"] - 4 + 1, 3])
        self.assertNotIn("spell_cast", _types(_ok_res(self, res)))

        st = self._goblins_vs_wizard()
        st["combatants"]["wiz"]["spellcasting"]["slots"] = {k: 0 for k in st["combatants"]["wiz"]["spellcasting"]["slots"]}
        res = self._scimitar(st, "gob", [st["combatants"]["wiz"]["ac"] - 4 + 1, 3])
        self.assertNotIn("spell_cast", _types(_ok_res(self, res)))

    def test_casting_shield_on_your_own_turn_is_illegal(self) -> None:
        st = _start(
            self.rules,
            [
                {"id": "wiz", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 0}},
                {"id": "gob", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
            ],
        )
        before = copy.deepcopy(st)
        res = step(st, {"type": "cast_spell", "actor_id": "wiz", "spell": "shield", "as_reaction": True, "slot_level": 1},
                   self.rules)
        self.assertEqual(res["status"], "invalid_intent")
        self.assertIn("No valid reaction trigger", res["error"])
        self.assertEqual(res["state"], before)


def _ok_res(test: unittest.TestCase, res: dict[str, Any]) -> dict[str, Any]:
    test.assertEqual(res["status"], "ok", res.get("error"))
    return res


if __name__ == "__main__":
    unittest.main()
