"""Milestone 6 (M6.1 #84 Battlefield Bounds & M6.2 Terrain, Walls, Cover, LoS) tests.

Pure Python 3.11+ stdlib `unittest`. Covers:
1. Battlefield bounds validation & enforcement on start_encounter, load_scenario, move, and teleport.
2. Forced movement (Push mastery) stopping cleanly at boundary edge or wall tile.
3. Difficult terrain 2x movement cost (10 ft per 5-ft step) and grid-step wall pathfinding.
4. Deterministic grid-raycast Line of Sight (walls block ranged weapons, melee across wall, targeted spells, teleport).
5. Half Cover (+2 AC / +2 DEX save) and Three-Quarters Cover (+5 AC / +5 DEX save), taking highest bonus,
   recorded in events and narrative hooks.
6. New built-in `ruined-keep` tactical dungeon scenario, deterministic replay, and `TestCommittedTracesReplay` parity.
7. Property test over seeds: HeuristicTacticalDriver, RandomDriver, GreedyDriver, Human menu, and Jev candidates
   never propose out-of-bounds moves, wall tiles, or wall-blocked attacks/spells.
8. DST invariant checks for out-of-bounds & wall placement + bounded/terrain DST campaign.
9. `web_server.client_view` bounds/terrain forwarding without leaking hidden enemy stats.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from srd2024.arena import (
    GreedyDriver,
    HeuristicTacticalDriver,
    RandomDriver,
    build_human_action_menu,
    build_observation,
    load_scenario,
    render_tactical_hud,
    replay_match,
    run_match,
)
from srd2024.arena.jev import enumerate_candidates
from srd2024.arena.web_server import client_view
from srd2024.engine.character import compile_character
from srd2024.engine.combat import (
    compute_cover,
    has_line_of_sight,
    in_bounds,
    is_wall,
    movement_path_cost,
    square_occupant,
    step,
)
from srd2024.engine.store import RulesStore
from srd2024.testing.dst import check_state_invariants, run_dst_seed

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_pc_spec(
    name: str = "Bram",
    *,
    cls: str = "fighter",
    level: int = 5,
    weapons: list[str] | None = None,
    masteries: list[str] | None = None,
    cantrips: list[str] | None = None,
    spells: list[str] | None = None,
) -> dict[str, Any]:
    if cls == "wizard":
        return {
            "name": name,
            "species": "elf",
            "subspecies": "elven-lineage-high-elf",
            "size": "Medium",
            "background": "sage",
            "background_asi": {"int": 2, "con": 1},
            "origin_feat": "magic-initiate",
            "class": "wizard",
            "subclass": "evoker" if level >= 3 else None,
            "level": level,
            "ability_method": "standard_array",
            "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
            "asi_choices": [{"level": 4, "type": "asi", "increases": {"int": 2}}] if level >= 4 else [],
            "skill_proficiencies": ["arcana", "history", "investigation", "insight"],
            "equipment": {"armor": None, "shield": False, "weapons": weapons or ["quarterstaff", "dagger"]},
            "cantrips": cantrips or ["fire-bolt", "ray-of-frost"],
            "prepared_spells": spells or ["fireball", "magic-missile", "misty-step", "shield"],
            "hp_method": "fixed",
        }
    return {
        "name": name,
        "species": "human",
        "subspecies": None,
        "size": "Medium",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "class": "fighter",
        "subclass": "champion" if level >= 3 else None,
        "level": level,
        "ability_method": "point_buy",
        "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
        "asi_choices": [{"level": 4, "type": "asi", "increases": {"str": 2}}] if level >= 4 else [],
        "fighting_style": "defense",
        "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
        "equipment": {
            "armor": "chain-mail",
            "shield": False,
            "weapons": weapons or ["warhammer", "shortbow", "longsword"],
        },
        "weapon_mastery_weapons": masteries or ["warhammer", "shortbow", "longsword"],
        "cantrips": [],
        "prepared_spells": [],
        "hp_method": "fixed",
    }


def _start_box(
    rules: RulesStore,
    combatants: list[dict[str, Any]],
    *,
    bounds: dict[str, int] | None = None,
    terrain: dict[str, Any] | None = None,
    roll_overrides: list[int] | None = None,
    seed: int = 1,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "start_encounter",
        "encounter_id": "m6-test",
        "seed": seed,
        "combatants": combatants,
    }
    if bounds is not None:
        payload["bounds"] = bounds
    if terrain is not None:
        payload["terrain"] = terrain
    res = step(None, payload, rules, roll_overrides=roll_overrides)
    assert res["status"] == "ok", f"start_encounter failed: {res.get('error')}"
    return res["state"]


class TestM6BoundsAndMovement(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter_sb = compile_character(_make_pc_spec("Aldric"), cls.rules)["statblock"]
        cls.wizard_sb = compile_character(_make_pc_spec("Lyra", cls="wizard"), cls.rules)["statblock"]

    def test_start_encounter_rejects_bad_bounds_or_oob_or_wall_spawn(self) -> None:
        base_c = [
            {"id": "pc-1", "side": "party", "statblock": self.fighter_sb, "position": {"x": 0, "y": 0}},
            {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 15, "y": 0}},
        ]
        # Malformed bounds (not 5-ft aligned / min > max)
        r_bad = step(
            None,
            {"type": "start_encounter", "seed": 1, "combatants": base_c, "bounds": {"min_x": 0, "min_y": 0, "max_x": 13, "max_y": 20}},
            self.rules,
        )
        self.assertEqual(r_bad["status"], "invalid_intent")
        self.assertIn("5-ft", r_bad["error"])

        # Spawn outside bounds
        r_oob = step(
            None,
            {"type": "start_encounter", "seed": 1, "combatants": base_c, "bounds": {"min_x": 0, "min_y": 0, "max_x": 10, "max_y": 10}},
            self.rules,
        )
        self.assertEqual(r_oob["status"], "invalid_intent")
        self.assertIn("bounds", r_oob["error"].lower())

        # Spawn on wall
        r_wall = step(
            None,
            {"type": "start_encounter", "seed": 1, "combatants": base_c, "terrain": {"walls": [[15, 0]]}},
            self.rules,
        )
        self.assertEqual(r_wall["status"], "invalid_intent")
        self.assertIn("wall", r_wall["error"].lower())

    def test_move_and_teleport_reject_out_of_bounds_and_walls(self) -> None:
        bounds = {"min_x": 0, "min_y": 0, "max_x": 30, "max_y": 20}
        terrain = {"walls": [[10, 0], [10, 5]], "difficult_terrain": [], "cover": []}
        st = _start_box(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.wizard_sb, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 25, "y": 15}},
            ],
            bounds=bounds,
            terrain=terrain,
            roll_overrides=[20, 1],
        )

        # Out of bounds move
        r_oob = step(st, {"type": "move", "actor_id": "pc-1", "to": {"x": -5, "y": 0}}, self.rules)
        self.assertEqual(r_oob["status"], "invalid_intent")
        self.assertIn("bounds", r_oob["error"].lower())
        self.assertEqual(r_oob["state"], st)

        # Move onto wall tile
        r_wall = step(st, {"type": "move", "actor_id": "pc-1", "to": {"x": 10, "y": 0}}, self.rules)
        self.assertEqual(r_wall["status"], "invalid_intent")
        self.assertIn("wall", r_wall["error"].lower())
        self.assertEqual(r_wall["state"], st)

        # Teleport out of bounds
        r_tp_oob = step(
            st,
            {"type": "cast_spell", "actor_id": "pc-1", "spell": "misty-step", "slot_level": 2, "destination": {"x": -10, "y": 0}},
            self.rules,
        )
        self.assertEqual(r_tp_oob["status"], "invalid_intent")
        self.assertIn("bounds", r_tp_oob["error"].lower())
        self.assertEqual(r_tp_oob["state"], st)

        # Teleport onto wall
        r_tp_wall = step(
            st,
            {"type": "cast_spell", "actor_id": "pc-1", "spell": "misty-step", "slot_level": 2, "destination": {"x": 10, "y": 0}},
            self.rules,
        )
        self.assertEqual(r_tp_wall["status"], "invalid_intent")
        self.assertIn("wall", r_tp_wall["error"].lower())

        # Teleport behind wall (no LoS)
        r_tp_los = step(
            st,
            {"type": "cast_spell", "actor_id": "pc-1", "spell": "misty-step", "slot_level": 2, "destination": {"x": 15, "y": 0}},
            self.rules,
        )
        self.assertEqual(r_tp_los["status"], "invalid_intent")
        self.assertIn("line of sight", r_tp_los["error"].lower())

        # Legal teleport in bounds, off wall, with LoS
        r_tp_ok = step(
            st,
            {"type": "cast_spell", "actor_id": "pc-1", "spell": "misty-step", "slot_level": 2, "destination": {"x": 5, "y": 15}},
            self.rules,
        )
        self.assertEqual(r_tp_ok["status"], "ok", r_tp_ok.get("error"))
        self.assertEqual(r_tp_ok["state"]["combatants"]["pc-1"]["position"], {"x": 5, "y": 15})

    def test_push_mastery_stops_at_bounds_and_walls(self) -> None:
        # 1. Push stops cleanly at battlefield boundary edge (5 ft pushed instead of 10 ft)
        bounds = {"min_x": 0, "min_y": 0, "max_x": 15, "max_y": 15}
        st_edge = _start_box(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter_sb, "position": {"x": 5, "y": 5}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 10, "y": 5}},
            ],
            bounds=bounds,
            roll_overrides=[20, 1],
        )
        r_push = step(
            st_edge,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "warhammer", "use_mastery": True},
            self.rules,
            roll_overrides=[18, 1],  # hit with low damage so goblin survives or moves cleanly
        )
        self.assertEqual(r_push["status"], "ok")
        self.assertEqual(r_push["state"]["combatants"]["gob-1"]["position"], {"x": 15, "y": 5})
        push_evs = [e for e in r_push["events"] if e.get("type") == "mastery_triggered" and e.get("mastery") == "push"]
        self.assertEqual(len(push_evs), 1)
        self.assertEqual(push_evs[0]["distance_ft"], 5)
        self.assertEqual(push_evs[0]["stopped_by"], "bounds")

        # 2. Push stops cleanly at wall edge (0 ft pushed when already flush against wall!)
        terrain = {"walls": [[15, 5]], "difficult_terrain": [], "cover": []}
        st_wall = _start_box(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter_sb, "position": {"x": 5, "y": 5}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 10, "y": 5}},
            ],
            bounds={"min_x": 0, "min_y": 0, "max_x": 30, "max_y": 30},
            terrain=terrain,
            roll_overrides=[20, 1],
        )
        r_push_w = step(
            st_wall,
            {"type": "attack", "actor_id": "pc-1", "target_id": "gob-1", "weapon": "warhammer", "use_mastery": True},
            self.rules,
            roll_overrides=[18, 1],
        )
        self.assertEqual(r_push_w["status"], "ok")
        self.assertEqual(r_push_w["state"]["combatants"]["gob-1"]["position"], {"x": 10, "y": 5})
        push_w_evs = [e for e in r_push_w["events"] if e.get("type") == "mastery_triggered" and e.get("mastery") == "push"]
        self.assertEqual(push_w_evs[0]["distance_ft"], 0)
        self.assertEqual(push_w_evs[0]["stopped_by"], "wall")

    def test_difficult_terrain_costs_2x_and_walls_block_movement_paths(self) -> None:
        bounds = {"min_x": 0, "min_y": 0, "max_x": 30, "max_y": 15}
        # Complete wall column at x=15 blocks reaching x=20
        terrain_sealed = {
            "walls": [[15, 0], [15, 5], [15, 10], [15, 15]],
            "difficult_terrain": [[5, 0], [10, 0]],
            "cover": [],
        }
        st = _start_box(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter_sb, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 25, "y": 0}},
            ],
            bounds=bounds,
            terrain=terrain_sealed,
            roll_overrides=[20, 1],
        )

        # Cannot walk through sealed wall
        r_blocked = step(st, {"type": "move", "actor_id": "pc-1", "to": {"x": 20, "y": 0}}, self.rules)
        self.assertEqual(r_blocked["status"], "invalid_intent")
        self.assertIn("wall", r_blocked["error"].lower())

        # Entering (5, 0) difficult terrain costs 10 ft (2x), leaving 20 ft of 30 ft speed
        r_diff1 = step(st, {"type": "move", "actor_id": "pc-1", "to": {"x": 5, "y": 0}}, self.rules)
        self.assertEqual(r_diff1["status"], "ok")
        self.assertEqual(r_diff1["state"]["combatants"]["pc-1"]["movement_remaining_ft"], 20)
        moved_ev = [e for e in r_diff1["events"] if e["type"] == "moved"][0]
        self.assertEqual(moved_ev["movement_cost_ft"], 10)

        # Direct difficult terrain corridor test (bounded to 1 row so no detour around difficult terrain)
        cost_corridor, err = movement_path_cost(
            {"x": 0, "y": 0},
            {"x": 20, "y": 0},
            {"min_x": 0, "min_y": 0, "max_x": 20, "max_y": 0},
            {"walls": [], "difficult_terrain": [[5, 0], [10, 0], [15, 0], [20, 0]], "cover": []},
        )
        self.assertIsNone(err)
        self.assertEqual(cost_corridor, 40)  # 4 difficult squares * 10 ft = 40 ft

        # Attempting 20 ft through all-difficult corridor with only 30 ft movement fails cleanly
        st_corr = _start_box(
            self.rules,
            [
                {"id": "pc-1", "side": "party", "statblock": self.fighter_sb, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 25, "y": 0}},
            ],
            bounds={"min_x": 0, "min_y": 0, "max_x": 25, "max_y": 0},
            terrain={"walls": [], "difficult_terrain": [[5, 0], [10, 0], [15, 0], [20, 0]], "cover": []},
            roll_overrides=[20, 1],
        )
        r_short = step(st_corr, {"type": "move", "actor_id": "pc-1", "to": {"x": 20, "y": 0}}, self.rules)
        self.assertEqual(r_short["status"], "invalid_intent")
        self.assertIn("40 ft", r_short["error"])
        self.assertIn("difficult terrain", r_short["error"].lower())


class TestM6LineOfSightAndCover(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.fighter_sb = compile_character(_make_pc_spec("Aldric"), cls.rules)["statblock"]
        cls.wizard_sb = compile_character(_make_pc_spec("Lyra", cls="wizard"), cls.rules)["statblock"]

    def test_walls_block_ranged_weapon_and_targeted_spells(self) -> None:
        terrain = {"walls": [[10, 0]], "difficult_terrain": [], "cover": []}
        st = _start_box(
            self.rules,
            [
                {"id": "pc-f", "side": "party", "statblock": self.fighter_sb, "position": {"x": 0, "y": 0}},
                {"id": "pc-w", "side": "party", "statblock": self.wizard_sb, "position": {"x": -5, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}},
            ],
            terrain=terrain,
            roll_overrides=[20, 15, 1],
        )
        self.assertFalse(has_line_of_sight({"x": 0, "y": 0}, {"x": 20, "y": 0}, terrain))
        self.assertTrue(has_line_of_sight({"x": 0, "y": 5}, {"x": 20, "y": 5}, terrain))

        # Ranged weapon blocked by wall
        r_bow = step(st, {"type": "attack", "actor_id": "pc-f", "target_id": "gob-1", "weapon": "shortbow"}, self.rules)
        self.assertEqual(r_bow["status"], "invalid_intent")
        self.assertIn("line of sight", r_bow["error"].lower())
        self.assertEqual(r_bow["state"], st)

        # Advance to wizard's turn and test fire-bolt & magic-missile blocked by wall
        st_w = step(st, {"type": "end_turn", "actor_id": "pc-f"}, self.rules)["state"]
        r_fb = step(st_w, {"type": "cast_spell", "actor_id": "pc-w", "spell": "fire-bolt", "target_ids": ["gob-1"]}, self.rules)
        self.assertEqual(r_fb["status"], "invalid_intent")
        self.assertIn("line of sight", r_fb["error"].lower())

        r_mm = step(
            st_w,
            {"type": "cast_spell", "actor_id": "pc-w", "spell": "magic-missile", "slot_level": 1, "target_ids": ["gob-1"]},
            self.rules,
        )
        self.assertEqual(r_mm["status"], "invalid_intent")
        self.assertIn("line of sight", r_mm["error"].lower())

        # Diagonal wall barrier blocks LoS across vertex
        diag_wall = {"walls": [[5, 0], [0, 5]], "difficult_terrain": [], "cover": []}
        self.assertFalse(has_line_of_sight({"x": 0, "y": 0}, {"x": 5, "y": 5}, diag_wall))

    def test_half_and_three_quarters_cover_ac_and_dex_save_bonuses(self) -> None:
        # Half cover (+2 AC / +2 DEX save)
        half_terrain = {"walls": [], "difficult_terrain": [], "cover": [{"x": 10, "y": 0, "kind": "half"}]}
        self.assertEqual(compute_cover({"x": 0, "y": 0}, {"x": 20, "y": 0}, half_terrain), ("half", 2))

        # Three-quarters cover (+5 AC / +5 DEX save)
        tq_terrain = {"walls": [], "difficult_terrain": [], "cover": [{"x": 10, "y": 0, "kind": "three_quarters"}]}
        self.assertEqual(compute_cover({"x": 0, "y": 0}, {"x": 20, "y": 0}, tq_terrain), ("three_quarters", 5))

        # Highest cover wins (half + three_quarters -> +5, NOT +7)
        both_terrain = {
            "walls": [],
            "difficult_terrain": [],
            "cover": [
                {"x": 5, "y": 0, "kind": "half"},
                {"x": 15, "y": 0, "kind": "three_quarters"},
            ],
        }
        self.assertEqual(compute_cover({"x": 0, "y": 0}, {"x": 20, "y": 0}, both_terrain), ("three_quarters", 5))

        # Weapon attack against goblin (base AC 15) behind half cover -> AC 17!
        # Fighter shortbow attack bonus is +5 (DEX +2, PB +3). Roll 11 -> 16 (would hit AC 15, misses AC 17!)
        st_half = _start_box(
            self.rules,
            [
                {"id": "pc-f", "side": "party", "statblock": self.fighter_sb, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}},
            ],
            terrain=half_terrain,
            roll_overrides=[20, 1],
        )
        r_atk = step(
            st_half,
            {"type": "attack", "actor_id": "pc-f", "target_id": "gob-1", "weapon": "shortbow"},
            self.rules,
            roll_overrides=[11],
        )
        self.assertEqual(r_atk["status"], "ok")
        atk_ev = [e for e in r_atk["events"] if e["type"] == "attack_roll"][0]
        self.assertEqual(atk_ev["target_ac"], 17)
        self.assertEqual(atk_ev["cover"], "half")
        self.assertEqual(atk_ev["cover_ac_bonus"], 2)
        self.assertFalse(atk_ev["hit"])
        self.assertTrue(any("half cover +2 AC" in h for h in r_atk["narrative_hooks"]))

        # Spell attack & Fireball DEX save behind three-quarters cover (+5 AC / +5 DEX save)!
        st_3q = _start_box(
            self.rules,
            [
                {"id": "pc-w", "side": "party", "statblock": self.wizard_sb, "position": {"x": 0, "y": 0}},
                {"id": "gob-1", "side": "enemy", "monster": "goblin-warrior", "position": {"x": 20, "y": 0}},
            ],
            terrain=tq_terrain,
            roll_overrides=[20, 1],
        )
        # Wizard spell DC is 15 (8 + 3 PB + 4 INT). Goblin DEX save is +2.
        # Roll 8 on DEX save: 8 + 2 + 5 (three-quarters cover) = 15 >= DC 15 -> SUCCEEDS!
        r_fb = step(
            st_3q,
            {"type": "cast_spell", "actor_id": "pc-w", "spell": "fireball", "slot_level": 3, "target_ids": ["gob-1"]},
            self.rules,
            roll_overrides=[1, 1, 1, 1, 1, 1, 1, 1, 8],
        )
        self.assertEqual(r_fb["status"], "ok")
        save_ev = [e for e in r_fb["events"] if e["type"] == "saving_throw"][0]
        self.assertEqual(save_ev["ability"], "dex")
        self.assertEqual(save_ev["cover"], "three_quarters")
        self.assertEqual(save_ev["cover_save_bonus"], 5)
        self.assertEqual(save_ev["total"], 15)
        self.assertTrue(save_ev["success"])
        self.assertTrue(any("three-quarters cover +5 DEX save" in h for h in r_fb["narrative_hooks"]))


class TestM6RuinedKeepDriversAndDST(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_ruined_keep_runs_cleanly_and_replays_deterministically(self) -> None:
        scen = load_scenario("ruined-keep", self.rules)
        self.assertIn("bounds", scen)
        self.assertIn("terrain", scen)
        self.assertTrue(scen["terrain"]["walls"])
        self.assertTrue(scen["terrain"]["difficult_terrain"])
        self.assertTrue(scen["terrain"]["cover"])

        tx = run_match(
            "ruined-keep",
            party_driver=HeuristicTacticalDriver(self.rules),
            enemy_driver=HeuristicTacticalDriver(self.rules),
            rules=self.rules,
            seed=11,
        )
        self.assertEqual(tx["violations"], [], f"HeuristicTacticalDriver violated rules on ruined-keep: {tx['violations']}")
        self.assertEqual(tx["initial_state"]["bounds"], scen["bounds"])
        self.assertEqual(tx["initial_state"]["terrain"], scen["terrain"])
        self.assertEqual(check_state_invariants(tx["final_state"]), [])

        replayed = replay_match(json.loads(json.dumps(tx)), self.rules)
        self.assertTrue(replayed["deterministic_match"], replayed.get("diffs"))
        self.assertEqual(replayed["original_digest"], replayed["replayed_digest"])

    def test_property_drivers_human_jev_never_propose_oob_or_wall_moves(self) -> None:
        """Across multiple seeds on bounded/terrain scenarios, no driver/menu/Jev proposes OOB or wall moves."""
        for sid in ("ruined-keep", "goblin-ambush"):
            scen = load_scenario(sid, self.rules)
            bounds = scen.get("bounds")
            terrain = scen.get("terrain")
            for seed in (1, 2, 3, 7, 13):
                tx = run_match(
                    sid,
                    party_driver=HeuristicTacticalDriver(self.rules),
                    enemy_driver=HeuristicTacticalDriver(self.rules),
                    rules=self.rules,
                    seed=seed,
                    max_rounds=3,
                )
                self.assertEqual(tx["violations"], [], f"Heuristic violation in {sid} seed={seed}: {tx['violations']}")

                # Also probe RandomDriver, GreedyDriver, Human menu, and Jev candidates on the initial & mid states.
                for state in (tx["initial_state"], tx["final_state"]):
                    if state.get("status") == "ended":
                        continue
                    for cid, comb in state["combatants"].items():
                        if not comb.get("alive") or int(comb.get("hp", 0)) <= 0:
                            continue
                        obs = build_observation(state, cid, scen, {"party": [], "enemy": []}, [], {}, self.rules)

                        # Check Human menu
                        menu = build_human_action_menu(obs, self.rules)
                        for item in menu:
                            self._assert_intent_respects_bounds_terrain(
                                item["intent"], obs["self"]["position"], state, bounds, terrain, f"human/{sid}/{seed}/{cid}"
                            )
                        hud = render_tactical_hud(obs, menu)
                        if terrain and terrain.get("walls"):
                            self.assertIn("#", hud)

                        # Check Jev candidates (for PCs)
                        if comb.get("kind") == "pc":
                            for _name, _desc, intent in enumerate_candidates(obs, self.rules):
                                self._assert_intent_respects_bounds_terrain(
                                    intent, obs["self"]["position"], state, bounds, terrain, f"jev/{sid}/{seed}/{cid}"
                                )

                        # Check RandomDriver, GreedyDriver, HeuristicTacticalDriver
                        for drv in (
                            RandomDriver(seed=seed, rules=self.rules),
                            GreedyDriver(rules=self.rules),
                            HeuristicTacticalDriver(rules=self.rules),
                        ):
                            dec = drv.decide(obs)
                            self._assert_intent_respects_bounds_terrain(
                                dec.intent, obs["self"]["position"], state, bounds, terrain, f"{drv.name}/{sid}/{seed}/{cid}"
                            )

    def _assert_intent_respects_bounds_terrain(
        self,
        intent: dict[str, Any],
        actor_pos: dict[str, int],
        state: dict[str, Any],
        bounds: dict[str, int] | None,
        terrain: dict[str, Any] | None,
        ctx: str,
    ) -> None:
        itype = intent.get("type")
        actor_id = str(intent.get("actor_id") or "")
        if itype == "move" and isinstance(intent.get("to"), dict):
            dest = intent["to"]
            self.assertTrue(in_bounds(dest, bounds), f"{ctx}: OOB move {dest} vs {bounds}")
            self.assertFalse(is_wall(dest, terrain), f"{ctx}: move onto wall {dest}")
            if dest != actor_pos:
                self.assertIsNone(
                    square_occupant(state, actor_id, dest),
                    f"{ctx}: move onto occupied square {dest}",
                )
        elif itype == "cast_spell":
            if isinstance(intent.get("destination"), dict):
                dest = intent["destination"]
                self.assertTrue(in_bounds(dest, bounds), f"{ctx}: OOB teleport {dest} vs {bounds}")
                self.assertFalse(is_wall(dest, terrain), f"{ctx}: teleport onto wall {dest}")
                self.assertTrue(has_line_of_sight(actor_pos, dest, terrain), f"{ctx}: teleport through wall {dest}")
                if dest != actor_pos:
                    self.assertIsNone(
                        square_occupant(state, actor_id, dest),
                        f"{ctx}: teleport onto occupied square {dest}",
                    )
            for tid in intent.get("target_ids") or []:
                if tid != intent.get("actor_id") and tid in state["combatants"]:
                    tpos = state["combatants"][tid]["position"]
                    self.assertTrue(has_line_of_sight(actor_pos, tpos, terrain), f"{ctx}: spell {intent.get('spell')} blocked by wall to {tid}")
        elif itype == "attack":
            tids = list(intent.get("target_ids") or [])
            if intent.get("target_id"):
                tids.append(intent["target_id"])
            for tid in tids:
                if tid in state["combatants"]:
                    tpos = state["combatants"][tid]["position"]
                    self.assertTrue(has_line_of_sight(actor_pos, tpos, terrain), f"{ctx}: attack blocked by wall to {tid}")

    def test_dst_invariants_catch_oob_and_wall_and_pass_on_ruined_keep(self) -> None:
        scen = load_scenario("ruined-keep", self.rules)
        st = _start_box(
            self.rules,
            copy.deepcopy(scen["combatants"]),
            bounds=scen["bounds"],
            terrain=scen["terrain"],
        )
        self.assertEqual(check_state_invariants(st), [])

        bad_oob = copy.deepcopy(st)
        bad_oob["combatants"]["pc-aldric"]["position"] = {"x": -5, "y": 0}
        v_oob = check_state_invariants(bad_oob)
        self.assertTrue(any("combatant_out_of_bounds" in v for v in v_oob), v_oob)

        bad_wall = copy.deepcopy(st)
        wx, wy = scen["terrain"]["walls"][0]
        bad_wall["combatants"]["pc-aldric"]["position"] = {"x": wx, "y": wy}
        v_wall = check_state_invariants(bad_wall)
        self.assertTrue(any("combatant_on_wall" in v for v in v_wall), v_wall)

        dst_res = run_dst_seed(4, scenario="ruined-keep", max_steps_per_seed=45, adversarial_rate=0.30, rules=self.rules)
        self.assertTrue(dst_res["passed"], dst_res["failures"])
        self.assertTrue(dst_res["deterministic"])

    def test_web_client_view_forwards_bounds_and_terrain_safely(self) -> None:
        scen = load_scenario("ruined-keep", self.rules)
        st = _start_box(
            self.rules,
            copy.deepcopy(scen["combatants"]),
            bounds=scen["bounds"],
            terrain=scen["terrain"],
        )
        obs = build_observation(st, "pc-aldric", scen, {"party": [], "enemy": []}, [], {}, self.rules)
        cv = client_view(obs)
        self.assertEqual(cv["bounds"], scen["bounds"])
        self.assertEqual(cv["terrain"], scen["terrain"])
        for enemy in cv["enemies"]:
            self.assertNotIn("hp", enemy)
            self.assertNotIn("ac", enemy)

    def test_committed_traces_stay_unbounded_and_replay_cleanly(self) -> None:
        traces = sorted((REPO_ROOT / "examples" / "sessions").glob("*.trace.json"))
        self.assertTrue(traces)
        for path in traces:
            with self.subTest(trace=path.name):
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("bounds", raw["initial_state"])
                rep = replay_match(raw, self.rules)
                self.assertTrue(rep["deterministic_match"], rep.get("diffs"))


if __name__ == "__main__":
    unittest.main()
