"""#8: Magic Initiate feat spells are selectable and castable.

SRD 5.2 Magic Initiate: choose the Cleric, Druid or Wizard list; learn 2 cantrips and 1 level 1 spell from it;
INT, WIS or CHA is the spellcasting ability for them; the level 1 spell is always prepared and can be cast once
per Long Rest without a slot, or with your own slots. Repeatable with a different list each time.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from srd2024.arena.dungeon import build_room_scenario, extract_party_carryover, init_party_carryover
from srd2024.arena.scenarios import _SPEC_BRAM_L1, _SPEC_DORIAN_L5
from srd2024.engine.character import compile_character
from srd2024.engine.combat import compute_legal_actions, step
from srd2024.engine.store import RulesStore

_ALDRIC = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "characters" / "aldric-paladin-1.json").read_text("utf-8")
)
_MI_CLERIC = {"feat": "magic-initiate", "list": "cleric", "ability": "wis",
              "cantrips": ["guidance", "sacred-flame"], "spell": "healing-word"}
_MI_WIZARD = {"feat": "magic-initiate", "list": "wizard", "ability": "int",
              "cantrips": ["fire-bolt", "light"], "spell": "magic-missile"}


def _codes(res: dict[str, Any], key: str = "errors") -> list[str]:
    return [i["code"] for i in res[key]]


class TestMagicInitiateValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def _aldric(self, *feat_spells: dict[str, Any], **overrides: Any) -> dict[str, Any]:
        spec = copy.deepcopy(_ALDRIC)
        spec["feat_spells"] = [copy.deepcopy(f) for f in feat_spells]
        spec.update(overrides)
        return compile_character(spec, self.rules)

    def test_magic_initiate_cleric_on_paladin_valid(self) -> None:
        res = self._aldric(_MI_CLERIC)
        self.assertTrue(res["valid"], res["errors"])
        self.assertEqual(res["warnings"], [])
        fsc = res["statblock"]["feat_spellcasting"]
        self.assertEqual(len(fsc), 1)
        self.assertEqual(fsc[0]["cantrips"], ["guidance", "sacred-flame"])
        self.assertEqual(fsc[0]["spells"], ["healing-word"])
        self.assertEqual(fsc[0]["free_casts"], {"healing-word": 1})
        # The class's own spell list is untouched.
        self.assertEqual(res["statblock"]["spellcasting"]["cantrips"], [])

    def test_feat_spell_uses_feat_ability_dc(self) -> None:
        sb = self._aldric(_MI_CLERIC)["statblock"]
        pb, wis, cha = sb["proficiency_bonus"], sb["ability_mods"]["wis"], sb["ability_mods"]["cha"]
        self.assertEqual(sb["feat_spellcasting"][0]["save_dc"], 8 + pb + wis)
        self.assertEqual(sb["feat_spellcasting"][0]["attack_bonus"], pb + wis)
        self.assertEqual(sb["spellcasting"]["spell_save_dc"], 8 + pb + cha)
        self.assertNotEqual(wis, cha, "fixture must tell the two abilities apart")

    def test_magic_initiate_spell_off_list_rejected(self) -> None:
        res = self._aldric(dict(_MI_CLERIC, cantrips=["fire-bolt", "guidance"]))
        self.assertIn("feat_spell_not_on_list", _codes(res))
        self.assertFalse(res["valid"])

    def test_magic_initiate_requires_feat(self) -> None:
        spec = copy.deepcopy(_SPEC_BRAM_L1)  # Soldier: Savage Attacker + Alert, no Magic Initiate
        spec["feat_spells"] = [copy.deepcopy(_MI_WIZARD)]
        res = compile_character(spec, self.rules)
        self.assertIn("feat_spells_without_feat", _codes(res))

    def test_magic_initiate_wrong_counts_rejected(self) -> None:
        for label, entry in (
            ("one cantrip", dict(_MI_CLERIC, cantrips=["guidance"])),
            ("three cantrips", dict(_MI_CLERIC, cantrips=["guidance", "sacred-flame", "light"])),
            ("same cantrip twice", dict(_MI_CLERIC, cantrips=["guidance", "guidance"])),
            ("two level 1 spells", dict(_MI_CLERIC, spell=["healing-word", "bless"])),
            ("no level 1 spell", {k: v for k, v in _MI_CLERIC.items() if k != "spell"}),
            ("level 1 spell as a cantrip", dict(_MI_CLERIC, cantrips=["guidance", "bless"])),
            ("level 2 spell", dict(_MI_CLERIC, spell="aid")),
        ):
            with self.subTest(label):
                self.assertIn("feat_spell_count", _codes(self._aldric(entry)))

    def test_invalid_list_and_ability_rejected(self) -> None:
        self.assertIn("feat_spell_invalid_list", _codes(self._aldric(dict(_MI_CLERIC, list="bard"))))
        self.assertIn("feat_spell_invalid_ability", _codes(self._aldric(dict(_MI_CLERIC, ability="str"))))

    def test_magic_initiate_unselected_warns(self) -> None:
        spec = copy.deepcopy(_ALDRIC)
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], res["errors"])
        self.assertIn("feat_spells_unselected", _codes(res, "warnings"))
        self.assertEqual(res["statblock"]["feat_spellcasting"], [])

    def test_repeated_magic_initiate_needs_different_lists(self) -> None:
        # Human Versatile: a second Origin feat. Magic Initiate twice, Cleric and Wizard lists.
        ok = self._aldric(_MI_CLERIC, _MI_WIZARD, extra_origin_feat="magic-initiate")
        self.assertTrue(ok["valid"], ok["errors"])
        self.assertEqual([b["list"] for b in ok["statblock"]["feat_spellcasting"]], ["cleric", "wizard"])
        dup = self._aldric(_MI_CLERIC, dict(_MI_CLERIC, cantrips=["light", "thaumaturgy"], spell="bless"),
                           extra_origin_feat="magic-initiate")
        self.assertIn("feat_spell_duplicate_list", _codes(dup))
        one_pick = self._aldric(_MI_CLERIC, _MI_WIZARD)  # only one Magic Initiate pick
        self.assertIn("feat_spells_without_feat", _codes(one_pick))

    def test_feat_cantrips_not_counted_against_class_limit(self) -> None:
        spec = copy.deepcopy(_SPEC_DORIAN_L5)  # Life cleric (Acolyte) with a full set of class cantrips
        spec["feat_spells"] = [dict(_MI_CLERIC, cantrips=["mending", "thaumaturgy"], spell="guiding-bolt")]
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], res["errors"])
        self.assertEqual(res["warnings"], [])
        self.assertEqual(len(res["statblock"]["spellcasting"]["cantrips"]), len(spec["cantrips"]))
        self.assertEqual(res["statblock"]["feat_spellcasting"][0]["cantrips"], ["mending", "thaumaturgy"])

    def test_background_magic_initiate_uses_the_background_list(self) -> None:
        # SRD 5.2: Acolyte grants Magic Initiate (Cleric), Sage grants Magic Initiate (Wizard).
        res = self._aldric(dict(_MI_WIZARD))  # Aldric is an Acolyte
        self.assertIn("feat_spell_background_list", _codes(res))
        # A second, Versatile pick may use another list as long as one entry is the background's.
        ok = self._aldric(_MI_WIZARD, _MI_CLERIC, extra_origin_feat="magic-initiate")
        self.assertTrue(ok["valid"], ok["errors"])


class TestMagicInitiateCombat(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        spec = copy.deepcopy(_ALDRIC)
        spec["feat_spells"] = [copy.deepcopy(_MI_CLERIC)]
        cls.sb = compile_character(spec, cls.rules)["statblock"]

    def _start(self, sb: dict[str, Any] | None = None, **entry: Any) -> dict[str, Any]:
        pc = {"id": "aldric", "statblock": sb or self.sb, "position": {"x": 0, "y": 0}, **entry}
        st = step(None, {"type": "start_encounter", "seed": 3, "combatants": [
            pc, {"id": "gob", "monster": "goblin-warrior", "position": {"x": 30, "y": 0}},
        ]}, self.rules)["state"]
        st["active_index"] = st["turn_order"].index("aldric")
        return st

    def _cast(self, st, spell, **kw) -> dict[str, Any]:
        intent = {"type": "cast_spell", "actor_id": "aldric", "spell": spell, **kw}
        return step(st, intent, self.rules)

    def _next_own_turn(self, st) -> dict[str, Any]:
        st = step(st, {"type": "end_turn", "actor_id": "aldric"}, self.rules)["state"]
        while st["turn_order"][st["active_index"]] != "aldric":
            st = step(st, {"type": "end_turn"}, self.rules)["state"]
        return st

    def test_feat_cantrip_uses_feat_dc(self) -> None:
        for kw in ({"source": "magic-initiate"}, {}):  # explicit, or inferred because the class doesn't know it
            with self.subTest(**kw):
                r = self._cast(self._start(), "sacred-flame", target_ids=["gob"], **kw)
                self.assertEqual(r["status"], "ok", r.get("error"))
                saves = [e for e in r["events"] if e["type"] == "saving_throw"]
                self.assertEqual([e["dc"] for e in saves], [self.sb["feat_spellcasting"][0]["save_dc"]])
                self.assertNotEqual(saves[0]["dc"], self.sb["spellcasting"]["spell_save_dc"])

    def test_free_cast_consumed_then_falls_back_to_slot(self) -> None:
        st = self._start()
        r1 = self._cast(st, "healing-word", source="magic-initiate", target_ids=["aldric"])
        self.assertEqual(r1["status"], "ok", r1.get("error"))
        me = r1["state"]["combatants"]["aldric"]
        self.assertEqual(me["feat_spellcasting"][0]["free_casts"], {"healing-word": 0})
        self.assertEqual(me["spellcasting"]["slots"], st["combatants"]["aldric"]["spellcasting"]["slots"])
        self.assertFalse(me["turn_resources"].get("slot_spent_this_turn"), "a free cast isn't a spell slot (#52)")
        self.assertTrue(any(e["type"] == "feat_free_cast_used" for e in r1["events"]))
        # Still free to spend a slot this turn, e.g. on Bless.
        r_bless = self._cast(r1["state"], "bless", slot_level=1, target_ids=["aldric"])
        self.assertEqual(r_bless["status"], "ok", r_bless.get("error"))

        st2 = self._next_own_turn(r1["state"])
        before = int(st2["combatants"]["aldric"]["spellcasting"]["slots"]["1"])
        r2 = self._cast(st2, "healing-word", source="magic-initiate", target_ids=["aldric"])
        self.assertEqual(r2["status"], "ok", r2.get("error"))
        self.assertEqual(int(r2["state"]["combatants"]["aldric"]["spellcasting"]["slots"]["1"]), before - 1)
        self.assertFalse(any(e["type"] == "feat_free_cast_used" for e in r2["events"]))

    def test_explicit_slot_level_uses_a_slot_even_with_a_free_cast(self) -> None:
        st = self._start()
        r = self._cast(st, "healing-word", source="magic-initiate", slot_level=1, target_ids=["aldric"])
        self.assertEqual(r["status"], "ok", r.get("error"))
        me = r["state"]["combatants"]["aldric"]
        self.assertEqual(me["feat_spellcasting"][0]["free_casts"], {"healing-word": 1})
        self.assertEqual(int(me["spellcasting"]["slots"]["1"]), int(st["combatants"]["aldric"]["spellcasting"]["slots"]["1"]) - 1)

    def test_legal_actions_list_feat_spells(self) -> None:
        st = self._start()
        spells = compute_legal_actions(st, self.rules)["spells"]
        for sp in ("guidance", "sacred-flame", "healing-word", "bless"):
            self.assertIn(sp, spells)
        # Free cast gone and no slots left: Healing Word is no longer offered, the cantrips still are.
        st["combatants"]["aldric"]["feat_spellcasting"][0]["free_casts"]["healing-word"] = 0
        st["combatants"]["aldric"]["spellcasting"]["slots"] = {"1": 0}
        spells = compute_legal_actions(st, self.rules)["spells"]
        self.assertNotIn("healing-word", spells)
        self.assertIn("sacred-flame", spells)

    def test_free_cast_offered_after_slot_spent_this_turn(self) -> None:
        st = self._start()
        st = self._cast(st, "bless", slot_level=1, target_ids=["aldric"])["state"]
        self.assertIn("healing-word", compute_legal_actions(st, self.rules)["spells"])
        r = self._cast(st, "healing-word", source="magic-initiate", target_ids=["aldric"])
        self.assertEqual(r["status"], "ok", r.get("error"))

    def test_wrong_source_and_unknown_feat_spell_rejected(self) -> None:
        st = self._start()
        r = self._cast(st, "fire-bolt", source="magic-initiate", target_ids=["gob"])
        self.assertEqual(r["status"], "invalid_intent")
        self.assertIn("did not learn 'fire-bolt' from magic-initiate", r["error"])
        r = self._cast(st, "sacred-flame", source="ritual-caster", target_ids=["gob"])
        self.assertEqual(r["status"], "invalid_intent")
        self.assertIn("Unknown spell source", r["error"])

    def test_non_caster_casts_feat_spells(self) -> None:
        spec = copy.deepcopy(_SPEC_BRAM_L1)
        spec["extra_origin_feat"] = "magic-initiate"  # Human Versatile
        spec["feat_spells"] = [copy.deepcopy(_MI_WIZARD)]
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], res["errors"])
        self.assertIsNone(res["statblock"]["spellcasting"])
        st = self._start(res["statblock"])
        self.assertIn("fire-bolt", compute_legal_actions(st, self.rules)["spells"])
        r = self._cast(st, "fire-bolt", target_ids=["gob"])
        self.assertEqual(r["status"], "ok", r.get("error"))
        atk = [e for e in r["events"] if e["type"] == "spell_attack_roll"]
        self.assertEqual(len(atk), 1)
        r = self._cast(st, "magic-missile", target_ids=["gob"])
        self.assertEqual(r["status"], "ok", r.get("error"))
        st2 = self._next_own_turn(r["state"])
        r = self._cast(st2, "magic-missile", target_ids=["gob"])
        self.assertEqual(r["status"], "invalid_intent")
        self.assertIn("no free casts", r["error"])

    # #105 MF1: two Magic Initiate picks (different lists) may name the same level 1 spell. Each pick's free cast
    # must be usable, and legal_actions must offer exactly what step() accepts.
    _MI_DRUID_HW = {"feat": "magic-initiate", "list": "druid", "ability": "wis",
                    "cantrips": ["druidcraft", "produce-flame"], "spell": "healing-word"}

    def _double_hw(self, base: dict[str, Any], **overrides: Any) -> dict[str, Any]:
        spec = copy.deepcopy(base)
        spec.update(overrides)
        spec["extra_origin_feat"] = "magic-initiate"  # Human Versatile: second pick
        spec["feat_spells"] = [copy.deepcopy(_MI_CLERIC), copy.deepcopy(self._MI_DRUID_HW)]
        res = compile_character(spec, self.rules)
        self.assertTrue(res["valid"], res["errors"])
        return res["statblock"]

    def _free(self, st: dict[str, Any]) -> dict[str, int]:
        return {b["list"]: b["free_casts"]["healing-word"] for b in st["combatants"]["aldric"]["feat_spellcasting"]}

    def _hw_offered(self, st: dict[str, Any]) -> bool:
        return "healing-word" in compute_legal_actions(st, self.rules)["spells"]

    def test_same_spell_from_two_picks_caster_uses_both_free_casts_then_a_slot(self) -> None:
        st = self._start(self._double_hw(_ALDRIC))  # Paladin: 2 level 1 slots
        slots = lambda s: int(s["combatants"]["aldric"]["spellcasting"]["slots"]["1"])  # noqa: E731
        self.assertEqual((self._free(st), slots(st)), ({"cleric": 1, "druid": 1}, 2))
        expected = [({"cleric": 0, "druid": 1}, 2), ({"cleric": 0, "druid": 0}, 2), ({"cleric": 0, "druid": 0}, 1)]
        for i, want in enumerate(expected):
            with self.subTest(cast=i + 1):
                self.assertTrue(self._hw_offered(st))
                r = self._cast(st, "healing-word", source="magic-initiate", target_ids=["aldric"])
                self.assertEqual(r["status"], "ok", r.get("error"))
                self.assertEqual((self._free(r["state"]), slots(r["state"])), want)
                st = self._next_own_turn(r["state"])

    def test_same_spell_from_two_picks_non_caster_gets_two_casts_and_legal_actions_agree(self) -> None:
        sb = self._double_hw(
            _SPEC_BRAM_L1, background="acolyte", background_asi={"wis": 2, "int": 1}, origin_feat="magic-initiate",
            skill_proficiencies=["insight", "religion", "athletics", "perception"],
        )
        self.assertIsNone(sb["spellcasting"])
        st = self._start(sb)
        for i, want in enumerate(({"cleric": 0, "druid": 1}, {"cleric": 0, "druid": 0})):
            with self.subTest(cast=i + 1):
                self.assertTrue(self._hw_offered(st))
                r = self._cast(st, "healing-word", target_ids=["aldric"])  # source inferred: no class spells
                self.assertEqual(r["status"], "ok", r.get("error"))
                self.assertEqual(self._free(r["state"]), want)
                st = self._next_own_turn(r["state"])
        self.assertFalse(self._hw_offered(st), "no free casts and no slots: not offered")
        self.assertEqual(self._cast(st, "healing-word", target_ids=["aldric"])["status"], "invalid_intent")

    def test_carryover_is_keyed_by_list(self) -> None:
        st = self._start(self._double_hw(_ALDRIC), feat_free_casts={"druid": {"healing-word": 0}})
        self.assertEqual(self._free(st), {"cleric": 1, "druid": 0})

    def test_states_without_feat_spells_keep_their_shape(self) -> None:
        res = compile_character(copy.deepcopy(_ALDRIC), self.rules)
        st = self._start(res["statblock"])
        self.assertNotIn("feat_spellcasting", st["combatants"]["aldric"])

    def test_free_casts_carry_over_between_rooms(self) -> None:
        party = [{"id": "aldric", "name": "Aldric", "statblock": self.sb}]
        co = init_party_carryover(party, self.rules)
        self.assertEqual(co["aldric"]["feat_free_casts"], {"cleric": {"healing-word": 1}})
        st = self._start()
        st = self._cast(st, "healing-word", source="magic-initiate", target_ids=["aldric"])["state"]
        co = extract_party_carryover(st, co)
        self.assertEqual(co["aldric"]["feat_free_casts"], {"cleric": {"healing-word": 0}})
        dungeon = {"id": "d", "name": "D", "tier": 1, "party_level": 1, "party": party,
                   "rooms": [{"id": "r1", "name": "R1", "max_rounds": 5, "party_spawns": {"aldric": {"x": 0, "y": 0}},
                              "enemies": [{"id": "gob", "monster": "goblin-warrior", "position": {"x": 30, "y": 0}}]}]}
        scen = build_room_scenario(dungeon, 0, co)
        nxt = step(None, {"type": "start_encounter", "seed": 1, "combatants": scen["combatants"]}, self.rules)["state"]
        self.assertEqual(nxt["combatants"]["aldric"]["feat_spellcasting"][0]["free_casts"], {"healing-word": 0})


if __name__ == "__main__":
    unittest.main()
