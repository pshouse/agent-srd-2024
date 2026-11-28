"""Tests for M6.3B Multi-Encounter Adventuring Day & Short Rests."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from srd2024.arena.agents import HeuristicTacticalDriver
from srd2024.arena.dungeon import (
    BUILTIN_DUNGEON_IDS,
    DUNGEON_SCHEMA_VERSION,
    DUNGEON_SCORE_WEIGHTS,
    _SPEC_SEREN_L5,
    auto_short_rest_plan,
    build_room_scenario,
    compute_dungeon_digest,
    extract_party_carryover,
    init_party_carryover,
    list_builtin_dungeons,
    load_dungeon,
    render_dungeon_markdown,
    render_dungeon_replay_markdown,
    render_dungeons_markdown,
    replay_dungeon,
    resolve_short_rest,
    run_dungeon,
    score_dungeon,
)
from srd2024.engine.character import compile_character
from srd2024.engine.combat import in_bounds, is_wall
from srd2024.engine.dice import DeterministicRNG
from srd2024.runtime.arena_bridge import main_arena
from srd2024.runtime.rules_bridge import load_rules_store


class TestM6AdventuringDay(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_rules_store()

    # ------------------------------------------------------------------
    # 1. Built-In Ashfall Keep Compilation & Validation
    # ------------------------------------------------------------------

    def test_builtin_dungeon_registry_and_ashfall_keep_validation(self) -> None:
        dungeons = list_builtin_dungeons()
        self.assertEqual(BUILTIN_DUNGEON_IDS, ("ashfall-keep",))
        self.assertEqual(len(dungeons), 1)
        meta = dungeons[0]
        self.assertEqual(meta["id"], "ashfall-keep")
        self.assertEqual(meta["tier"], 2)
        self.assertEqual(meta["party_level"], 5)
        self.assertEqual(meta["total_rooms"], 3)
        self.assertTrue(meta["allow_short_rest_between_rooms"])

        # Verify Paladin _SPEC_SEREN_L5 compiles cleanly with 0 errors and 0 warnings
        seren_res = compile_character(_SPEC_SEREN_L5, self.rules)
        self.assertTrue(seren_res["valid"])
        self.assertEqual(seren_res["errors"], [])
        self.assertEqual(seren_res["warnings"], [])
        self.assertEqual(seren_res["statblock"]["hp_max"], 44)
        self.assertEqual(seren_res["statblock"]["ac"], 19)
        self.assertEqual(seren_res["statblock"]["hit_die"], 10)
        self.assertEqual(seren_res["statblock"]["spellcasting"]["slots"], {"1": 4, "2": 2})

        # Load full dungeon & check all 5 PCs, all 3 rooms, monsters, bounds, walls, occupancy
        compiled = load_dungeon("ashfall-keep", self.rules)
        self.assertEqual(compiled["id"], "ashfall-keep")
        self.assertEqual(
            compiled["party_ids"],
            ["pc-aldric", "pc-kaelen", "pc-seren", "pc-dorian", "pc-lyra"],
        )
        self.assertEqual([r["id"] for r in compiled["rooms"]], ["entrance-hall", "guardroom", "boss-chamber"])

        for room in compiled["rooms"]:
            bounds = room["bounds"]
            terrain = room["terrain"]
            occupied: set[tuple[int, int]] = set()
            for cid, pos in room["party_spawns"].items():
                self.assertTrue(in_bounds(pos, bounds), f"{cid} out of bounds in {room['id']}")
                self.assertFalse(is_wall(pos, terrain), f"{cid} on wall in {room['id']}")
                sq = (pos["x"], pos["y"])
                self.assertNotIn(sq, occupied, f"Collision at {sq} in {room['id']}")
                occupied.add(sq)
            for enemy in room["enemies"]:
                m_rec = self.rules.get_monster(enemy["monster"])
                self.assertIsNotNone(m_rec)
                self.assertEqual(m_rec["coverage"], "full", f"{enemy['monster']} must be full coverage")
                pos = enemy["position"]
                self.assertTrue(in_bounds(pos, bounds), f"{enemy['id']} out of bounds in {room['id']}")
                self.assertFalse(is_wall(pos, terrain), f"{enemy['id']} on wall in {room['id']}")
                sq = (pos["x"], pos["y"])
                self.assertNotIn(sq, occupied, f"Collision at {sq} in {room['id']}")
                occupied.add(sq)

    def test_custom_dungeon_validation_rejects_bad_spawns(self) -> None:
        base = {
            "id": "bad-dungeon",
            "name": "Bad Dungeon",
            "party": [{"id": "pc-seren", "spec": _SPEC_SEREN_L5}],
            "rooms": [
                {
                    "id": "r1",
                    "bounds": {"min_x": 0, "min_y": 0, "max_x": 20, "max_y": 20},
                    "terrain": {"walls": [[10, 10]], "difficult_terrain": [], "cover": []},
                    "party_spawns": {"pc-seren": {"x": 10, "y": 10}},
                    "enemies": [{"id": "e1", "monster": "ghoul", "position": {"x": 15, "y": 10}}],
                }
            ],
        }
        with self.assertRaises(ValueError):
            load_dungeon(base, self.rules)

        # Collision between party and enemy
        collide = copy.deepcopy(base)
        collide["rooms"][0]["party_spawns"]["pc-seren"] = {"x": 15, "y": 10}
        with self.assertRaises(ValueError):
            load_dungeon(collide, self.rules)

    # ------------------------------------------------------------------
    # 2. Party Carryover Across Rooms
    # ------------------------------------------------------------------

    def test_party_carryover_hp_slots_resources_conditions_and_deaths(self) -> None:
        compiled = load_dungeon("ashfall-keep", self.rules)
        co0 = init_party_carryover(compiled["party"], self.rules)

        # Verify initial SRD 5.2 defaults (works pre- and post-M6.3A)
        self.assertEqual(co0["pc-aldric"]["class_resources"]["second_wind"]["remaining"], 3)
        self.assertEqual(co0["pc-aldric"]["class_resources"]["action_surge"]["remaining"], 1)
        self.assertEqual(co0["pc-seren"]["class_resources"]["lay_on_hands"]["remaining"], 25)
        self.assertEqual(co0["pc-dorian"]["class_resources"]["channel_divinity"]["remaining"], 2)
        self.assertEqual(co0["pc-lyra"]["class_resources"]["arcane_recovery"]["remaining"], 1)
        self.assertEqual(co0["pc-lyra"]["class_resources"]["arcane_recovery"]["budget_levels"], 3)
        self.assertEqual(co0["pc-lyra"]["hit_dice"], {"die": 6, "max": 5, "remaining": 5})

        # Simulate end of Room 1 final_state:
        # - Aldric at 18 HP, poisoned + prone (poisoned persists, prone clears)
        # - Kaelen dead (alive=False -> omitted from Room 2)
        # - Seren at 0 HP living with 2 death save failures (stabilizes on victory -> death_saves reset, unconscious+prone)
        # - Lyra spent 2 L3 slots and 1 L2 slot
        fake_final = {
            "combatants": {
                "pc-aldric": {
                    "id": "pc-aldric",
                    "alive": True,
                    "hp": 18,
                    "temp_hp": 5,
                    "heroic_inspiration": False,
                    "exhaustion_level": 1,
                    "death_saves": {"successes": 0, "failures": 0},
                    "conditions": [
                        {"condition": "poisoned", "source_id": "r1-ghast", "duration": "indefinite"},
                        {"condition": "prone", "source_id": None, "duration": "indefinite"},
                    ],
                    "class_resources": {
                        "second_wind": {"remaining": 1, "max": 3, "recharge": "short_rest_one"},
                        "action_surge": {"remaining": 0, "max": 1, "recharge": "short_rest_all"},
                    },
                },
                "pc-kaelen": {
                    "id": "pc-kaelen",
                    "alive": False,
                    "hp": 0,
                    "death_saves": {"successes": 0, "failures": 3},
                    "conditions": [],
                },
                "pc-seren": {
                    "id": "pc-seren",
                    "alive": True,
                    "hp": 0,
                    "death_saves": {"successes": 1, "failures": 2},
                    "conditions": [{"condition": "frightened", "source_id": "mummy", "duration": "1_min"}],
                    "class_resources": {
                        "lay_on_hands": {"remaining": 10, "max": 25, "recharge": "long_rest"},
                        "channel_divinity": {"remaining": 0, "max": 2, "recharge": "short_rest_one"},
                    },
                },
                "pc-dorian": {
                    "id": "pc-dorian",
                    "alive": True,
                    "hp": 30,
                    "spellcasting": {"slots": {"1": 2, "2": 2, "3": 1}},
                    "class_resources": {
                        "channel_divinity": {"remaining": 0, "max": 2, "recharge": "short_rest_one"},
                    },
                },
                "pc-lyra": {
                    "id": "pc-lyra",
                    "alive": True,
                    "hp": 20,
                    "spellcasting": {"slots": {"1": 3, "2": 2, "3": 0}},
                },
            }
        }

        co1 = extract_party_carryover(fake_final, co0, room_won=True)
        self.assertEqual(co1["pc-aldric"]["hp"], 18)
        self.assertEqual(co1["pc-aldric"]["exhaustion_level"], 1)
        self.assertEqual([c["condition"] for c in co1["pc-aldric"]["conditions"]], ["poisoned"])
        self.assertFalse(co1["pc-kaelen"]["alive"])
        self.assertEqual(co1["pc-seren"]["hp"], 0)
        self.assertEqual(co1["pc-seren"]["death_saves"], {"successes": 0, "failures": 0})
        self.assertEqual(
            sorted(c["condition"] for c in co1["pc-seren"]["conditions"]),
            ["prone", "unconscious"],
        )
        self.assertEqual(co1["pc-lyra"]["slots"], {"1": 3, "2": 2, "3": 0})
        self.assertEqual(co1["pc-lyra"]["slots_max"], {"1": 4, "2": 3, "3": 2})

        # Room 2 scenario omits dead Kaelen and carries over Aldric's HP and Lyra's spent slots
        r2_scen = build_room_scenario(compiled, 1, co1)
        self.assertNotIn("pc-kaelen", r2_scen["party_ids"])
        by_id = {c["id"]: c for c in r2_scen["combatants"]}
        self.assertEqual(by_id["pc-aldric"]["hp"], 18)
        self.assertEqual(by_id["pc-lyra"]["slots"], {"1": 3, "2": 2, "3": 0})

    # ------------------------------------------------------------------
    # 3. Deterministic resolve_short_rest()
    # ------------------------------------------------------------------

    def test_short_rest_determinism_hit_dice_resources_and_arcane_recovery(self) -> None:
        compiled = load_dungeon("ashfall-keep", self.rules)
        co = init_party_carryover(compiled["party"], self.rules)

        # Injure PCs and expend resources
        co["pc-aldric"]["hp"] = 20  # max 44, d10, con +2
        co["pc-aldric"]["temp_hp"] = 8
        co["pc-aldric"]["class_resources"]["second_wind"]["remaining"] = 1  # +1 -> 2
        co["pc-aldric"]["class_resources"]["action_surge"]["remaining"] = 0  # all -> 1

        co["pc-seren"]["hp"] = 0  # living downed PC, d10, con +2
        co["pc-seren"]["conditions"] = [
            {"condition": "unconscious", "source_id": None, "duration": "indefinite"},
            {"condition": "prone", "source_id": None, "duration": "indefinite"},
        ]
        co["pc-seren"]["class_resources"]["lay_on_hands"]["remaining"] = 5  # long_rest ONLY -> stays 5!
        co["pc-seren"]["class_resources"]["channel_divinity"]["remaining"] = 0  # +1 -> 1

        co["pc-dorian"]["hp"] = 41  # max 43, near full
        co["pc-dorian"]["class_resources"]["channel_divinity"]["remaining"] = 0  # +1 -> 1

        co["pc-lyra"]["hp"] = 18  # max 32, d6, con +2
        co["pc-lyra"]["slots"] = {"1": 2, "2": 1, "3": 0}  # budget ceil(5/2) = 3 -> greedy recovers L3 (+1)

        # Run twice with identical RNG seed -> byte-identical result
        rng_a = DeterministicRNG(seed=12345)
        rng_b = DeterministicRNG(seed=12345)
        after_a, rec_a = resolve_short_rest(co, rng=rng_a, plan=None)
        after_b, rec_b = resolve_short_rest(co, rng=rng_b, plan=None)
        self.assertEqual(after_a, after_b)
        self.assertEqual(rec_a, rec_b)

        # Input was not mutated
        self.assertEqual(co["pc-aldric"]["hp"], 20)
        self.assertEqual(co["pc-aldric"]["temp_hp"], 8)

        # Verify temp_hp cleared on Short Rest
        self.assertEqual(after_a["pc-aldric"]["temp_hp"], 0)

        # Verify Fighter recharge: +1 second_wind (1->2), all action_surge (0->1)
        self.assertEqual(after_a["pc-aldric"]["class_resources"]["second_wind"]["remaining"], 2)
        self.assertEqual(after_a["pc-aldric"]["class_resources"]["action_surge"]["remaining"], 1)
        self.assertEqual(rec_a["per_pc"]["pc-aldric"]["resources_recovered"], {"second_wind": 1, "action_surge": 1})

        # Verify Paladin: Lay on Hands does NOT recharge on Short Rest; Channel Divinity +1; revived from 0 HP!
        self.assertEqual(after_a["pc-seren"]["class_resources"]["lay_on_hands"]["remaining"], 5)
        self.assertEqual(after_a["pc-seren"]["class_resources"]["channel_divinity"]["remaining"], 1)
        self.assertNotIn("lay_on_hands", rec_a["per_pc"]["pc-seren"]["resources_recovered"])
        self.assertGreater(after_a["pc-seren"]["hp"], 0)
        self.assertEqual(after_a["pc-seren"]["conditions"], [])

        # Verify Cleric Channel Divinity +1
        self.assertEqual(after_a["pc-dorian"]["class_resources"]["channel_divinity"]["remaining"], 1)

        # Verify Wizard Arcane Recovery: greedy picks 1x L3 slot (cost 3 == budget 3) and marks arcane_recovery used (0)
        self.assertEqual(rec_a["per_pc"]["pc-lyra"]["slots_recovered"], {"3": 1})
        self.assertEqual(after_a["pc-lyra"]["slots"]["3"], 1)
        self.assertEqual(after_a["pc-lyra"]["class_resources"]["arcane_recovery"]["remaining"], 0)

        # Second Short Rest before Long Rest: Wizard cannot use Arcane Recovery again!
        after_a["pc-lyra"]["slots"]["1"] = 0
        after_2, rec_2 = resolve_short_rest(after_a, rng=DeterministicRNG(seed=99), plan=None)
        self.assertEqual(rec_2["per_pc"]["pc-lyra"]["slots_recovered"], {})

        # Explicit plan validation: Arcane Recovery exceeding budget (3) or > L5 raises ValueError
        with self.assertRaises(ValueError):
            resolve_short_rest(
                co,
                rng=DeterministicRNG(seed=1),
                plan={"pc-lyra": {"hit_dice_to_spend": 0, "arcane_recovery_slots": {"2": 2}}},
            )
        with self.assertRaises(ValueError):
            resolve_short_rest(
                co,
                rng=DeterministicRNG(seed=1),
                plan={"pc-lyra": {"hit_dice_to_spend": 0, "arcane_recovery_slots": {"6": 1}}},
            )

        # Hit Dice healing stops early at hp_max (does not waste dice) and respects max(1, r + con_mod)
        co_cap = copy.deepcopy(co)
        co_cap["pc-dorian"]["hp"] = 42  # max 43 -> 1 die suffices even if 3 requested
        after_cap, rec_cap = resolve_short_rest(
            co_cap,
            rng=DeterministicRNG(seed=7, roll_overrides=[1]),
            plan={"pc-dorian": {"hit_dice_to_spend": 3}},
        )
        self.assertEqual(after_cap["pc-dorian"]["hp"], 43)
        self.assertEqual(rec_cap["per_pc"]["pc-dorian"]["hit_dice_spent"], 1)
        self.assertEqual(rec_cap["per_pc"]["pc-dorian"]["hit_dice_remaining"], 4)

        # Verify auto_short_rest_plan helper returns valid plan structure
        preview = auto_short_rest_plan(co)
        self.assertIn("pc-aldric", preview)
        self.assertIn("arcane_recovery_slots", preview["pc-lyra"])

    def test_short_rest_pact_magic_recharges_all_slots(self) -> None:
        pact_co = {
            "pc-warlock": {
                "id": "pc-warlock",
                "name": "Vex",
                "class": "classes:warlock",
                "level": 5,
                "hp": 30,
                "hp_max": 38,
                "temp_hp": 0,
                "alive": True,
                "con_mod": 2,
                "hit_die": 8,
                "hit_dice": {"die": 8, "max": 5, "remaining": 5},
                "slots": {"3": 0},
                "slots_max": {"3": 2},
                "is_pact_magic": True,
                "class_resources": {},
                "heroic_inspiration": False,
                "exhaustion_level": 0,
                "death_saves": {"successes": 0, "failures": 0},
                "conditions": [],
            }
        }
        after, rec = resolve_short_rest(pact_co, rng=DeterministicRNG(seed=5), plan={"pc-warlock": {"hit_dice_to_spend": 0}})
        self.assertEqual(after["pc-warlock"]["slots"], {"3": 2})
        self.assertEqual(rec["per_pc"]["pc-warlock"]["slots_recovered"], {"3": 2})

    # ------------------------------------------------------------------
    # 4. Full 3-Room run_dungeon(), replay_dungeon(), score_dungeon() & Renderers
    # ------------------------------------------------------------------

    def test_full_three_room_dungeon_run_replay_and_scoring(self) -> None:
        party_drv = HeuristicTacticalDriver(self.rules)
        enemy_drv = HeuristicTacticalDriver(self.rules)

        trace = run_dungeon(
            "ashfall-keep",
            party_driver=party_drv,
            enemy_driver=enemy_drv,
            rules=self.rules,
            seed=42,
            allow_short_rest=True,
        )

        self.assertEqual(trace["schema_version"], DUNGEON_SCHEMA_VERSION)
        self.assertEqual(trace["kind"], "dungeon_trace")
        self.assertEqual(trace["dungeon"]["id"], "ashfall-keep")
        self.assertEqual(trace["total_rooms"], 3)
        self.assertGreaterEqual(len(trace["rooms"]), 1)
        self.assertEqual(trace["digest"], compute_dungeon_digest(trace))

        # Check 5-dimension scorecard weights sum to 1.00
        sc = trace["scorecard"]
        self.assertAlmostEqual(sum(DUNGEON_SCORE_WEIGHTS.values()), 1.0, places=6)
        for key in (
            "outcome_score",
            "rules_legality_score",
            "tactical_score",
            "coordination_score",
            "resource_conservation_score",
            "composite_score",
        ):
            self.assertIn(key, sc)
            self.assertGreaterEqual(sc[key], 0.0)
            self.assertLessEqual(sc[key], 100.0)

        # Deterministic replay matches 100%
        rep = replay_dungeon(trace, self.rules)
        self.assertEqual(rep["status"], "ok")
        self.assertTrue(rep["deterministic_match"], f"Replay diffs: {rep['diffs']}")
        self.assertEqual(rep["original_digest"], rep["replayed_digest"])
        self.assertEqual(rep["diffs"], [])

        # Tampering with digest or rest triggers mismatch
        tampered = copy.deepcopy(trace)
        tampered["digest"] = "0" * 64
        rep_bad = replay_dungeon(tampered, self.rules)
        self.assertFalse(rep_bad["deterministic_match"])

        # Markdown renderers produce non-empty valid Markdown
        md_report = render_dungeon_markdown(trace)
        self.assertIn("Ashfall Keep", md_report)
        self.assertIn("Resource Conservation", md_report)
        md_rep = render_dungeon_replay_markdown(rep)
        self.assertIn("MATCH", md_rep)
        md_cat = render_dungeons_markdown(list_builtin_dungeons())
        self.assertIn("ashfall-keep", md_cat)

    def test_score_dungeon_zero_conservation_when_wiping_in_room_1(self) -> None:
        wipe_trace = {
            "winner": "enemy",
            "rooms_cleared": 0,
            "total_rooms": 3,
            "rooms": [
                {
                    "rounds": 3,
                    "scorecard": {
                        "rules_legality_score": 100.0,
                        "tactical_score": 50.0,
                        "coordination_score": 40.0,
                    },
                }
            ],
            "rests": [],
            "final_party_state": {
                "pc-aldric": {
                    "hp": 0,
                    "hp_max": 44,
                    "alive": False,
                    "hit_dice": {"die": 10, "remaining": 5, "max": 5},
                    "slots": {},
                    "slots_max": {},
                    "class_resources": {"second_wind": {"remaining": 3, "max": 3, "recharge": "short_rest_one"}},
                }
            },
        }
        sc = score_dungeon(wipe_trace, self.rules)
        self.assertEqual(sc["resource_conservation_score"], 0.0)
        self.assertEqual(sc["outcome_score"], 0.0)

    # ------------------------------------------------------------------
    # 5. CLI Subcommands (`dungeons`, `dungeon`, `dungeon-replay`)
    # ------------------------------------------------------------------

    def test_cli_dungeons_dungeon_and_dungeon_replay(self) -> None:
        # 1. `srd-arena dungeons --format json`
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main_arena(["dungeons", "--format", "json"])
        self.assertEqual(rc, 0)
        cat_payload = json.loads(buf.getvalue())
        self.assertEqual(cat_payload["status"], "ok")
        self.assertEqual([d["id"] for d in cat_payload["dungeons"]], ["ashfall-keep"])

        # 2. `srd-arena dungeon <custom-2room.json> --out-trace ... --format json`
        # Use a fast 2-room custom dungeon for snappy CLI roundtrip testing
        mini_dungeon = {
            "id": "mini-crypt",
            "name": "Mini Crypt",
            "tier": 2,
            "party_level": 5,
            "allow_short_rest_between_rooms": True,
            "party": [
                {"id": "pc-seren", "spec": _SPEC_SEREN_L5},
            ],
            "rooms": [
                {
                    "id": "entry",
                    "name": "Entry",
                    "max_rounds": 8,
                    "bounds": {"min_x": 0, "min_y": 0, "max_x": 25, "max_y": 15},
                    "party_spawns": {"pc-seren": {"x": 5, "y": 5}},
                    "enemies": [
                        {"id": "skel-1", "monster": "skeleton", "position": {"x": 15, "y": 5}},
                    ],
                },
                {
                    "id": "sanctum",
                    "name": "Sanctum",
                    "max_rounds": 8,
                    "bounds": {"min_x": 0, "min_y": 0, "max_x": 25, "max_y": 15},
                    "party_spawns": {"pc-seren": {"x": 5, "y": 5}},
                    "enemies": [
                        {"id": "skel-2", "monster": "skeleton", "position": {"x": 15, "y": 5}},
                    ],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            d_path = Path(tmpdir) / "mini-crypt.json"
            t_path = Path(tmpdir) / "mini-crypt.trace.json"
            d_path.write_text(json.dumps(mini_dungeon), encoding="utf-8")

            buf_run = io.StringIO()
            with redirect_stdout(buf_run):
                rc_run = main_arena(
                    [
                        "dungeon",
                        str(d_path),
                        "--seed",
                        "7",
                        "--out-trace",
                        str(t_path),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(rc_run, 0)
            self.assertTrue(t_path.is_file())
            run_payload = json.loads(buf_run.getvalue())
            self.assertEqual(run_payload["schema_version"], "5")
            self.assertEqual(run_payload["kind"], "dungeon_trace")
            self.assertEqual(run_payload["dungeon"]["id"], "mini-crypt")

            # 3. `srd-arena dungeon-replay <trace.json> --format json`
            buf_rep = io.StringIO()
            with redirect_stdout(buf_rep):
                rc_rep = main_arena(["dungeon-replay", str(t_path), "--format", "json"])
            self.assertEqual(rc_rep, 0)
            rep_payload = json.loads(buf_rep.getvalue())
            self.assertTrue(rep_payload["deterministic_match"])
            self.assertEqual(rep_payload["original_digest"], run_payload["digest"])


if __name__ == "__main__":
    unittest.main()
