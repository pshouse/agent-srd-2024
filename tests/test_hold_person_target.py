"""Hold Person targets a Humanoid (#63).

The engine refuses every other creature type, including an unknown type (fail closed).
The legal action menu and Jev's candidate list use the same check, so they do not offer
a cast `step()` would reject. Every driver submits intents to `step()`, so the refusal
is not implemented in a driver.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from srd2024.arena.agents import AgentDecision, GreedyDriver, HeuristicTacticalDriver, RandomDriver
from srd2024.arena.human import build_human_action_menu
from srd2024.arena.jev import enumerate_candidates
from srd2024.arena.runner import build_observation, run_match
from srd2024.arena.scenarios import load_scenario
from srd2024.engine.character import compile_character
from srd2024.engine.combat import creature_type_of, spell_target_allowed, step
from srd2024.engine.store import RulesStore

from tests.test_stage3_arena_integrity import _TalkSpam

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

    def test_smite_on_undead_and_hold_person_checks_both_run(self) -> None:
        """#69's smite retarget and the Hold Person type check share step().

        Divine Smite still adds +1d8 against an Undead. Hold Person on that same Undead is refused,
        and Hold Person on a Humanoid in the same fight is accepted. The smite check does not
        replace the Humanoid error.
        """
        paladin = compile_character(
            json.loads((Path(__file__).resolve().parents[1] / "examples" / "characters" / "aldric-paladin-1.json").read_text()),
            self.rules,
        )["statblock"]
        started = step(
            None,
            {
                "type": "start_encounter",
                "seed": 1,
                "combatants": [
                    {"id": "pal", "side": "party", "statblock": paladin, "position": {"x": 0, "y": 0}},
                    {"id": "wiz", "side": "party", "statblock": self.wizard, "position": {"x": 0, "y": 5}},
                    {"id": "skel", "side": "enemy", "monster": "skeleton", "name": "Skeleton", "position": {"x": 5, "y": 0}},
                    {"id": "bandit", "side": "enemy", "monster": "bandit", "name": "Bandit", "position": {"x": 25, "y": 0}},
                ],
            },
            self.rules,
            roll_overrides=[20, 14, 6, 1],
        )
        self.assertEqual(started["status"], "ok", started.get("error"))
        st = started["state"]
        self.assertEqual(st["turn_order"][st["active_index"]], "pal")
        hit = step(
            st,
            {"type": "attack", "actor_id": "pal", "target_id": "skel", "weapon": "longsword"},
            self.rules,
            roll_overrides=[15, 1],
        )
        self.assertEqual(hit["status"], "ok", hit.get("error"))
        smite = step(
            hit["state"],
            {"type": "cast_spell", "actor_id": "pal", "spell": "divine-smite", "slot_level": 1, "target_ids": ["skel"]},
            self.rules,
            roll_overrides=[2, 2, 2],
        )
        self.assertEqual(smite["status"], "ok", smite.get("error"))
        radiant = sum(
            part["effective"]
            for event in smite["events"]
            if event["type"] == "damage_applied"
            for part in event["breakdown"]
            if part["type"] == "radiant"
        )
        self.assertEqual(radiant, 6)  # 2d8 + 1d8 vs Undead; the type check does not refuse the smite
        self.assertIn("vs Undead", smite["narrative_hooks"][0])

        st = step(smite["state"], {"type": "end_turn", "actor_id": "pal"}, self.rules)["state"]
        self.assertEqual(st["turn_order"][st["active_index"]], "wiz")
        refused = self._cast(st, "skel")
        # _cast hardcodes actor wiz / hold-person. The encounter state uses those ids.
        self.assertEqual(refused["status"], "invalid_intent", refused.get("error"))
        self.assertIn("Hold Person requires a Humanoid target", refused["error"])
        self.assertNotIn("melee", refused["error"])
        accepted = self._cast(st, "bandit")
        self.assertEqual(accepted["status"], "ok", accepted.get("error"))
        self.assertIn("paralyzed", _names(accepted["state"]["combatants"]["bandit"]))

    def test_offline_drivers_do_not_land_hold_person_on_non_humanoids(self) -> None:
        """Every built-in offline driver goes through step(), which refuses the cast."""
        class _AlwaysHoldPerson:
            """Submits Hold Person at the nearest living enemy, whatever its creature type."""

            name = "always-hold-person"

            def reset(self, seed: int | None = None) -> None:
                return None

            def decide(self, observation: dict[str, Any]) -> AgentDecision:
                me = observation["self"]["id"]
                enemies = [e for e in (observation.get("enemies") or []) if e.get("alive")]
                resources = observation["self"].get("turn_resources") or {}
                if enemies and resources.get("action"):
                    return AgentDecision(
                        intent={
                            "type": "cast_spell",
                            "actor_id": me,
                            "spell": "hold-person",
                            "slot_level": 2,
                            "target_ids": [enemies[0]["id"]],
                        }
                    )
                return AgentDecision(intent={"type": "end_turn", "actor_id": me})

        factories = [
            lambda seed: HeuristicTacticalDriver(self.rules),
            lambda seed: GreedyDriver(self.rules),
            lambda seed: RandomDriver(seed=seed, rules=self.rules),
            lambda seed: _TalkSpam(self.rules),
            lambda seed: _AlwaysHoldPerson(),
        ]
        for factory in factories:
            for sid in ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"):
                driver = factory(1)
                if hasattr(driver, "reset"):
                    driver.reset(1)
                transcript = run_match(
                    sid,
                    party_driver=driver,
                    enemy_driver=HeuristicTacticalDriver(self.rules),
                    rules=self.rules,
                    seed=1,
                )
                sheets = transcript["initial_state"]["combatants"]
                for action in transcript["actions"]:
                    for event in action["events"]:
                        if event.get("type") == "spell_cast" and event.get("spell") == "hold-person":
                            for tid in event.get("target_ids") or []:
                                self.assertEqual(
                                    creature_type_of(sheets[tid], self.rules),
                                    "humanoid",
                                    f"{getattr(driver, 'name', driver)} {sid} {tid}",
                                )


if __name__ == "__main__":
    unittest.main()
