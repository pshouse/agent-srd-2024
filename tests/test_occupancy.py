"""#53: SRD 5.2 "Moving Around Other Creatures" — you can't willingly end a move in another creature's space.

Before the fix the engine let any number of creatures end their moves on one 5-ft square (wyrm-web-1 had three
enemies on (10,15)), and every scripted driver's move generator aimed at the same nearest square.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from srd2024.arena import GreedyDriver, HeuristicTacticalDriver, RandomDriver, run_match
from srd2024.arena.agents import _occupied_squares, _step_toward
from srd2024.arena.human import build_human_action_menu
from srd2024.engine.combat import grid_square, square_occupant, step
from srd2024.engine.store import RulesStore

from test_stage2_combat_engine import _make_fighter, _make_wizard, _start


def _active(st: dict[str, Any], cid: str) -> dict[str, Any]:
    st = copy.deepcopy(st)
    st["active_index"] = st["turn_order"].index(cid)
    return st


def _shared_squares(state: dict[str, Any]) -> list[tuple[int, int]]:
    seen: dict[tuple[int, int], str] = {}
    shared: list[tuple[int, int]] = []
    for cid, c in sorted(state["combatants"].items()):
        if not c.get("alive") or str(c.get("size") or "").lower() == "tiny":
            continue
        sq = grid_square(c["position"])
        if sq in seen:
            shared.append(sq)
        seen[sq] = cid
    return shared


class TestEngineOccupancy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        fighter = _make_fighter(cls.rules, weapons=["warhammer", "longsword"], mastery_weapons=["warhammer"])
        wizard = _make_wizard(cls.rules)
        cls.start = _start(
            cls.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": fighter, "position": {"x": 0, "y": 0}},
                {"id": "pc-2", "side": "party", "statblock": wizard, "position": {"x": 0, "y": 10}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 5, "y": 0}},
                {"id": "gob-2", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}},
            ],
        )

    def _move(self, st: dict[str, Any], actor: str, x: int, y: int, **extra: Any) -> dict[str, Any]:
        return step(_active(st, actor), {"type": "move", "actor_id": actor, "to": {"x": x, "y": y}, **extra}, self.rules)

    def test_cannot_end_a_move_on_an_enemy_or_an_ally(self) -> None:
        for x, y, who in ((5, 0, "Goblin"), (0, 10, "Lyra")):
            r = self._move(self.start, "pc-1", x, y)
            self.assertEqual(r["status"], "invalid_intent", (x, y))
            self.assertIn("occupied by", r["error"])
            self.assertIn(who, r["error"])
            self.assertEqual(r["state"]["combatants"]["pc-1"]["position"], {"x": 0, "y": 0})

    def test_refused_move_spends_no_movement(self) -> None:
        st = _active(self.start, "pc-1")
        before = st["combatants"]["pc-1"]["movement_remaining_ft"]
        r = step(st, {"type": "move", "actor_id": "pc-1", "to": {"x": 5, "y": 0}}, self.rules)
        self.assertEqual(r["state"]["combatants"]["pc-1"]["movement_remaining_ft"], before)

    def test_a_point_inside_an_occupied_square_is_still_occupied(self) -> None:
        r = self._move(self.start, "pc-1", 7, 3)
        self.assertEqual(r["status"], "invalid_intent")

    def test_free_squares_and_staying_put_are_fine(self) -> None:
        self.assertEqual(self._move(self.start, "pc-1", 0, 5)["status"], "ok")
        st = copy.deepcopy(self.start)
        st["combatants"]["pc-1"]["conditions"].append({"condition": "prone"})
        r = self._move(st, "pc-1", 0, 0, stand_up=True)
        self.assertEqual(r["status"], "ok", r.get("error"))

    def test_unconscious_creature_still_blocks_defeated_one_does_not(self) -> None:
        st = copy.deepcopy(self.start)
        st["combatants"]["pc-2"]["hp"] = 0
        st["combatants"]["pc-2"]["conditions"].append({"condition": "unconscious"})
        self.assertEqual(self._move(st, "pc-1", 0, 10)["status"], "invalid_intent")
        st["combatants"]["gob-1"]["hp"] = 0
        st["combatants"]["gob-1"]["alive"] = False
        self.assertEqual(self._move(st, "pc-1", 5, 0)["status"], "ok")

    def test_tiny_creatures_neither_block_nor_are_blocked(self) -> None:
        st = copy.deepcopy(self.start)
        st["combatants"]["gob-1"]["size"] = "Tiny"
        self.assertIsNone(square_occupant(st, "pc-1", {"x": 5, "y": 0}))
        self.assertEqual(self._move(st, "pc-1", 5, 0)["status"], "ok")
        st = copy.deepcopy(self.start)
        st["combatants"]["pc-1"]["size"] = "Tiny"
        self.assertEqual(self._move(st, "pc-1", 5, 0)["status"], "ok")

    def test_misty_step_onto_an_occupied_square_is_refused_and_spends_nothing(self) -> None:
        st = _active(self.start, "pc-2")
        slots_before = copy.deepcopy(st["combatants"]["pc-2"]["spellcasting"]["slots"])
        intent = {"type": "cast_spell", "actor_id": "pc-2", "spell": "misty-step", "slot_level": 2,
                  "target_ids": ["pc-2"]}
        r = step(st, {**intent, "destination": {"x": 0, "y": 0}}, self.rules)
        self.assertEqual(r["status"], "invalid_intent")
        self.assertIn("occupied by", r["error"])
        self.assertEqual(r["state"]["combatants"]["pc-2"]["spellcasting"]["slots"], slots_before)
        ok = step(st, {**intent, "destination": {"x": 0, "y": 25}}, self.rules)
        self.assertEqual(ok["status"], "ok", ok.get("error"))

    def _push(self, st: dict[str, Any]) -> dict[str, Any]:
        r = step(_active(st, "pc-1"), {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "warhammer"},
                 self.rules, roll_overrides=[18, 4])
        self.assertEqual(r["status"], "ok", r.get("error"))
        return r

    def _push_event(self, r: dict[str, Any]) -> dict[str, Any]:
        return next(e for e in r["events"] if e["type"] == "mastery_triggered" and e["mastery"] == "push")

    def test_push_stops_short_of_an_occupied_square(self) -> None:
        base = copy.deepcopy(self.start)
        base["combatants"]["gob-1"]["hp"] = base["combatants"]["gob-1"]["hp_max"] = 40
        # Free: the full 10 ft.
        r = self._push(base)
        self.assertEqual(r["state"]["combatants"]["gob-1"]["position"], {"x": 15, "y": 0})
        self.assertNotIn("blocked_by", self._push_event(r))
        # 10 ft away is taken: 5 ft.
        st = copy.deepcopy(base)
        st["combatants"]["gob-2"]["position"] = {"x": 15, "y": 0}
        r = self._push(st)
        self.assertEqual(r["state"]["combatants"]["gob-1"]["position"], {"x": 10, "y": 0})
        self.assertEqual(self._push_event(r)["distance_ft"], 5)
        self.assertEqual(self._push_event(r)["blocked_by"], "gob-2")
        # Both squares behind it are taken: it stays put.
        st["combatants"]["pc-2"]["position"] = {"x": 10, "y": 0}
        r = self._push(st)
        self.assertEqual(r["state"]["combatants"]["gob-1"]["position"], {"x": 5, "y": 0})
        self.assertEqual(self._push_event(r)["distance_ft"], 0)


class TestDriversNeverStack(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_step_toward_routes_around_occupied_squares(self) -> None:
        origin, target = {"x": 0, "y": 0}, {"x": 30, "y": 0}
        straight = _step_toward(origin, target, 30, desired_dist_ft=5)
        self.assertEqual(straight, {"x": 25, "y": 0})
        dest = _step_toward(origin, target, 30, desired_dist_ft=5, occupied={grid_square(straight)})
        self.assertNotEqual(grid_square(dest), grid_square(straight))
        self.assertLessEqual(max(abs(dest["x"] - 30), abs(dest["y"])), 5, "still ends adjacent to the target")
        # Every square that gets closer is blocked: stay put.
        blocked = {(dx, dy) for dx in range(-1, 8) for dy in range(-7, 8)} - {(0, 0)}
        self.assertEqual(_step_toward(origin, target, 30, desired_dist_ft=5, occupied=blocked), origin)

    def test_occupied_squares_from_observation(self) -> None:
        obs = {
            "self": {"id": "a", "size": "Medium", "position": {"x": 0, "y": 0}},
            "allies": [{"id": "b", "alive": True, "position": {"x": 5, "y": 0}},
                       {"id": "c", "alive": False, "position": {"x": 10, "y": 0}}],
            "enemies": [{"id": "d", "alive": True, "size": "Medium", "position": {"x": 15, "y": 0}},
                        {"id": "e", "alive": True, "size": "Tiny", "position": {"x": 20, "y": 0}},
                        {"id": "f", "alive": False, "size": "Medium", "position": {"x": 25, "y": 0}}],
        }
        self.assertEqual(_occupied_squares(obs), {(1, 0), (3, 0)})

    def test_scripted_matches_never_put_two_creatures_on_one_square(self) -> None:
        drivers = {
            "heuristic": lambda: HeuristicTacticalDriver(self.rules),
            "greedy": lambda: GreedyDriver(self.rules),
            "random": lambda: RandomDriver(seed=3, rules=self.rules),
        }
        for scenario in ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"):
            for name, make in drivers.items():
                for seed in (1, 2, 3):
                    with self.subTest(scenario=scenario, party=name, seed=seed):
                        party = make()
                        party.reset(seed)
                        t = run_match(scenario, party_driver=party, enemy_driver=HeuristicTacticalDriver(self.rules),
                                      rules=self.rules, seed=seed)
                        occ_violations = [v for v in t["violations"] if "occupied by" in v["error"]]
                        if name != "random":
                            self.assertEqual(occ_violations, [], "a scripted driver proposed an occupied square")
                        self._assert_no_stack(t)

    def _assert_no_stack(self, t: dict[str, Any]) -> None:
        positions = {cid: c["position"] for cid, c in t["initial_state"]["combatants"].items()}
        alive = {cid: True for cid in positions}
        sizes = {cid: c.get("size") for cid, c in t["initial_state"]["combatants"].items()}
        for a in t["actions"]:
            for e in a["events"]:
                if e["type"] in ("moved", "teleported"):
                    positions[e["actor_id"]] = e["to_pos"]
                elif e["type"] == "mastery_triggered" and e.get("mastery") == "push":
                    positions[e["target_id"]] = e["to_pos"]
                elif e["type"] in ("combatant_defeated", "combatant_died"):
                    alive[e.get("combatant_id") or e.get("target_id")] = False
            state = {"combatants": {cid: {"alive": alive[cid], "size": sizes[cid], "position": positions[cid]}
                                    for cid in positions}}
            self.assertEqual(_shared_squares(state), [], f"after seq {a['seq']} ({a['actor_id']} {a['intent']['type']})")
        self.assertEqual(_shared_squares(t["final_state"]), [])

    def test_human_menu_never_offers_an_occupied_square(self) -> None:
        rules = self.rules
        offered: list[tuple[dict[str, Any], set[tuple[int, int]]]] = []

        class MenuProbe:
            """Plays the heuristic, and at every decision records the move targets the human menu would offer."""

            name = "menu-probe"

            def __init__(self) -> None:
                self.inner = HeuristicTacticalDriver(rules)

            def reset(self, seed: int | None = None) -> None:
                self.inner.reset(seed)

            def decide(self, obs: dict[str, Any]) -> Any:
                occupied = _occupied_squares(obs)
                here = obs["self"]["position"]
                for item in build_human_action_menu(obs, rules):
                    intent = item["intent"]
                    dest = intent.get("to") if intent.get("type") == "move" else intent.get("destination")
                    if isinstance(dest, dict) and dest != here:
                        offered.append((dest, occupied))
                return self.inner.decide(obs)

        for scenario, seed in (("wyrmling-lair", 598), ("crypt-of-ghouls", 2), ("goblin-ambush", 1)):
            probe = MenuProbe()
            probe.reset(seed)
            run_match(scenario, party_driver=probe, enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=seed)
        self.assertGreater(len(offered), 10, "too few menu moves to be a meaningful check")
        for dest, occupied in offered:
            self.assertNotIn(grid_square(dest), occupied, f"menu offered occupied square {dest}")

if __name__ == "__main__":
    unittest.main()
