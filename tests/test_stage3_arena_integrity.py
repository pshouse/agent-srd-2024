"""Scorer and intent integrity for the Stage 3 arena (M1, M2, M5, S1).

No network. Ordering is pinned on the built-in scenarios with fixed seeds.
"""

from __future__ import annotations

import copy
import json
import random
import re
import unittest
from typing import Any

from srd2024.arena import (
    AgentDecision,
    GreedyDriver,
    HeuristicTacticalDriver,
    RandomDriver,
    build_observation,
    load_scenario,
    replay_match,
    run_match,
)
from srd2024.arena.runner import _arena_intent_rejection
from srd2024.engine.combat import step
from srd2024.engine.store import RulesStore

_SCENARIOS = ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair")
_SEEDS = (1, 2, 3)
_LEAK_RES = (
    re.compile(r"\bAC\s+\d+", re.IGNORECASE),
    re.compile(r"\bvs\s+AC\b", re.IGNORECASE),
    re.compile(r"\bDC\s+\d+", re.IGNORECASE),
    re.compile(r"\d+\s*/\s*\d+\s*HP", re.IGNORECASE),
    re.compile(r"\d+\s+temp(?:orary)?\s+HP", re.IGNORECASE),
    re.compile(r"\d+\s+spell\s+slots?", re.IGNORECASE),
    re.compile(r"level-\d+\s+spell\s+slots?", re.IGNORECASE),
)
_FORBIDDEN_ENEMY_KEYS = {
    "hp",
    "hp_max",
    "ac",
    "effective_ac",
    "spellcasting",
    "slots",
    "temp_hp",
    "death_saves",
    "turn_resources",
}


class _FixedIntent:
    def __init__(self, intent: dict[str, Any], *, name: str) -> None:
        self.name = name
        self.intent = intent
        self.feedback: list[str] = []
        self.observations: list[dict[str, Any]] = []

    def reset(self, seed: int | None = None) -> None:
        return None

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        self.observations.append(copy.deepcopy(observation))
        fb = observation.get("retry_feedback")
        if isinstance(fb, str) and fb:
            self.feedback.append(fb)
        intent = copy.deepcopy(self.intent)
        if intent.get("actor_id") == "$self":
            intent["actor_id"] = observation["self"]["id"]
        return AgentDecision(intent=intent, rationale="scripted")


class _NoOp:
    name = "noop"

    def reset(self, seed: int | None = None) -> None:
        return None

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        return AgentDecision(
            intent={"type": "end_turn", "actor_id": observation["self"]["id"]},
            rationale="idle",
        )


class _TalkSpam:
    """Issue #22 exploit: dodge, make one real attack, then end every turn while chatting.

    Before #22 the single attack unlocked a full legality score and most ratio bonuses,
    so this near-no-op policy scored ~48-53. It must now score close to the pure no-op.
    """

    name = "talk-spam"

    def __init__(self, rules: RulesStore) -> None:
        self.rules = rules
        self.reset()

    def reset(self, seed: int | None = None) -> None:
        self.inner = HeuristicTacticalDriver(self.rules)
        self.turns: dict[str, int] = {}
        self.last_round: dict[str, int] = {}
        self.attacked: set[str] = set()

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        me = observation["self"]["id"]
        rnd = int(observation["round"])
        if self.last_round.get(me) != rnd:
            self.last_round[me] = rnd
            self.turns[me] = self.turns.get(me, 0) + 1
        if self.turns[me] == 1:
            if observation["self"]["turn_resources"]["action"]:
                return AgentDecision(intent={"type": "dodge", "actor_id": me}, table_talk="Focus the leader!")
            return AgentDecision(intent={"type": "end_turn", "actor_id": me}, table_talk="Holding!")
        if me not in self.attacked:
            decision = self.inner.decide(observation)
            if decision.intent.get("type") in ("attack", "cast_spell"):
                self.attacked.add(me)
            return decision
        return AgentDecision(intent={"type": "end_turn", "actor_id": me}, table_talk="Stay together, team!")


class _HelpOnly:
    """CTO M1 on #22: take Help every turn and never attack, optionally chatting.

    Help counted as a fully productive turn, so this scored ~45-48 with 0 damage.
    """

    def __init__(self, *, talk: bool = False) -> None:
        self.talk = talk
        self.name = "help-talk" if talk else "help-only"

    def reset(self, seed: int | None = None) -> None:
        return None

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        me = observation["self"]["id"]
        foes = [e for e in observation["enemies"] if e.get("alive", True)]
        allies = [a for a in observation.get("allies", []) if a.get("id") != me]
        if observation["self"]["turn_resources"]["action"] and foes:
            return AgentDecision(
                intent={
                    "type": "help",
                    "actor_id": me,
                    "target_id": foes[0]["id"],
                    "ally_id": allies[0]["id"] if allies else None,
                },
                table_talk="I've got your back!" if self.talk else None,
            )
        return AgentDecision(intent={"type": "end_turn", "actor_id": me})


class _Garbage:
    name = "garbage"

    def reset(self, seed: int | None = None) -> None:
        return None

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        return AgentDecision(
            intent={
                "type": "attack",
                "actor_id": observation["self"]["id"],
                "target_id": "does-not-exist",
                "weapon": "no-such-weapon",
            },
            rationale="illegal",
        )


class _Recording:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "recording")
        self.observations: list[dict[str, Any]] = []

    def reset(self, seed: int | None = None) -> None:
        reset = getattr(self.inner, "reset", None)
        if reset is not None:
            reset(seed)

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        self.observations.append(copy.deepcopy(observation))
        return self.inner.decide(observation)


def _strings(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, dict):
        for value in obj.values():
            found.extend(_strings(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_strings(value))
    return found


def _mean_composite(driver: Any, rules: RulesStore, *, enemy: Any | None = None) -> float:
    scores: list[float] = []
    for scenario in _SCENARIOS:
        for seed in _SEEDS:
            foe = enemy if enemy is not None else HeuristicTacticalDriver(rules)
            if hasattr(driver, "reset"):
                driver.reset(seed)
            transcript = run_match(
                scenario,
                party_driver=driver,
                enemy_driver=foe,
                rules=rules,
                seed=seed,
            )
            scores.append(float(transcript["scorecard"]["composite_score"]))
    return sum(scores) / len(scores)


class TestIntentAllowlistAndActor(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_start_encounter_hijack_is_a_violation(self) -> None:
        hijack = _FixedIntent(
            {"type": "start_encounter", "combatants": [{"id": "only-party"}]},
            name="hijack",
        )
        transcript = run_match(
            "goblin-ambush",
            party_driver=hijack,
            enemy_driver=HeuristicTacticalDriver(self.rules),
            rules=self.rules,
            seed=1,
            max_rounds=3,
        )
        accepted = [a.get("intent", {}).get("type") for a in transcript["actions"] if a.get("side") == "party"]
        self.assertNotIn("start_encounter", accepted)
        self.assertTrue(any("not allowed" in v.get("error", "") for v in transcript["violations"]))
        initial_ids = set(transcript["initial_state"]["combatants"])
        final_ids = set(transcript["final_state"]["combatants"])
        self.assertEqual(initial_ids, final_ids)
        self.assertNotEqual(transcript["scorecard"]["outcome_score"], 100.0)
        self.assertLess(transcript["scorecard"]["composite_score"], 25.0)

    def test_off_turn_cast_and_cross_side_puppet_hide_private_resources(self) -> None:
        class _OffTurn(_FixedIntent):
            def decide(self, observation: dict[str, Any]) -> AgentDecision:
                allies = observation.get("allies") or []
                other = allies[0]["id"] if allies else "not-the-active-combatant"
                self.intent = {
                    "type": "cast_spell",
                    "actor_id": other,
                    "spell": "fireball",
                    "slot_level": 3,
                    "as_reaction": True,
                    "target_ids": ["wyrmling-1"],
                }
                return super().decide(observation)

        off_turn = _OffTurn({}, name="off-turn")
        puppet = _FixedIntent(
            {
                "type": "cast_spell",
                "actor_id": "pc-lyra",
                "spell": "fireball",
                "slot_level": 3,
                "as_reaction": True,
                "target_ids": ["pc-aldric", "pc-dorian", "pc-kaelen"],
            },
            name="puppet",
        )
        transcript = run_match(
            "wyrmling-lair",
            party_driver=off_turn,
            enemy_driver=puppet,
            rules=self.rules,
            seed=1,
            max_rounds=2,
        )
        for action in transcript["actions"]:
            intent = action.get("intent") or {}
            self.assertEqual(intent.get("actor_id"), action.get("actor_id"))
            self.assertNotEqual(intent.get("spell"), "fireball")
        visible = "\n".join(
            [v.get("error", "") for v in transcript["violations"]] + off_turn.feedback + puppet.feedback
        )
        self.assertNotIn("spell slot", visible.lower())
        self.assertNotIn("reaction remaining", visible.lower())
        self.assertEqual(transcript["scorecard"]["metrics"]["friendly_fire_incidents"], 0)
        lyra_slots = transcript["final_state"]["combatants"]["pc-lyra"]["spellcasting"]["slots"]
        initial_slots = transcript["initial_state"]["combatants"]["pc-lyra"]["spellcasting"]["slots"]
        self.assertEqual(lyra_slots, initial_slots)

    def test_engine_as_reaction_requires_a_reaction_spell_and_a_trigger(self) -> None:
        scen = load_scenario("wyrmling-lair", self.rules)
        state = step(
            None,
            {"type": "start_encounter", "seed": 1, "combatants": scen["combatants"]},
            self.rules,
        )["state"]
        active = state["turn_order"][int(state["active_index"]) % len(state["turn_order"])]
        if active == "pc-lyra":
            state = step(state, {"type": "end_turn", "actor_id": active}, self.rules)["state"]
        before = copy.deepcopy(state["combatants"]["pc-lyra"])
        denied = step(
            state,
            {
                "type": "cast_spell",
                "actor_id": "pc-lyra",
                "spell": "fireball",
                "slot_level": 3,
                "as_reaction": True,
                "target_ids": ["pc-aldric"],
            },
            self.rules,
        )
        self.assertNotEqual(denied["status"], "ok")
        self.assertNotIn("spell slot", str(denied["error"]).lower())
        self.assertNotIn("reaction remaining", str(denied["error"]).lower())
        self.assertEqual(denied["state"]["combatants"]["pc-lyra"]["spellcasting"]["slots"], before["spellcasting"]["slots"])
        self.assertTrue(denied["state"]["combatants"]["pc-lyra"]["turn_resources"]["reaction"])

        no_window = step(
            state,
            {
                "type": "cast_spell",
                "actor_id": "pc-lyra",
                "spell": "shield",
                "as_reaction": True,
                "target_ids": ["pc-lyra"],
            },
            self.rules,
        )
        self.assertNotEqual(no_window["status"], "ok")
        self.assertIn("trigger", no_window["error"].lower())

        opened = copy.deepcopy(state)
        opened["reaction_window"] = {"actor_id": "pc-lyra", "trigger": "hit"}
        shielded = step(
            opened,
            {
                "type": "cast_spell",
                "actor_id": "pc-lyra",
                "spell": "shield",
                "as_reaction": True,
                "target_ids": ["pc-lyra"],
            },
            self.rules,
        )
        self.assertEqual(shielded["status"], "ok", shielded.get("error"))
        self.assertFalse(shielded["state"]["combatants"]["pc-lyra"]["turn_resources"]["reaction"])

    def test_intent_rejection_fuzz(self) -> None:
        rng = random.Random(20241127)
        types = [None, "", "start_encounter", "attack", "cast_spell", "MOVE", "wish", 1, True, "x" * 80]
        actors = [None, "", "pc-lyra", "other", 3, "pc-lyra "]
        for _ in range(300):
            intent = {
                "type": rng.choice(types),
                "actor_id": rng.choice(actors),
                "as_reaction": rng.choice([True, False, "yes"]),
                "spell": rng.choice(["fireball", "shield", None]),
            }
            result = _arena_intent_rejection(intent, "pc-lyra")
            self.assertTrue(result is None or isinstance(result, str))
            if isinstance(result, str):
                self.assertNotIn("spell slot", result.lower())
                self.assertNotIn("reaction remaining", result.lower())
        self.assertIsNone(_arena_intent_rejection({"type": "reroll", "actor_id": "pc-lyra"}, "pc-lyra"))


class TestObservationHiddenStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_narration_patterns_are_stripped_before_a_match(self) -> None:
        scen = load_scenario("goblin-ambush", self.rules)
        state = step(
            None,
            {"type": "start_encounter", "seed": 1, "combatants": scen["combatants"]},
            self.rules,
        )["state"]
        hooks = [
            "Bram hits the goblin (18 vs AC 17) for 5 slashing.",
            "Topple mastery knocks the goblin Prone (failed CON DC 13).",
            "The goblin is at (18/32 HP) and gains 6 Temporary HP.",
            "A ward shows (AC 19) after a save (DC 15 Dexterity).",
            "The caster has no level-3 spell slots remaining.",
        ]
        obs = build_observation(
            state,
            "pc-bram",
            scen,
            {"party": [], "enemy": []},
            hooks,
            {},
            self.rules,
        )
        blob = "\n".join(obs["recent_events"])
        for pattern in _LEAK_RES:
            self.assertIsNone(pattern.search(blob), f"{pattern.pattern} in {blob!r}")

    def test_false_life_temp_hp_is_hidden_from_the_opposing_side(self) -> None:
        scen = load_scenario("goblin-ambush", self.rules)
        state = step(
            None,
            {"type": "start_encounter", "seed": 1, "combatants": scen["combatants"]},
            self.rules,
        )["state"]
        active = ""
        for _ in range(len(state["turn_order"]) + 1):
            active = state["turn_order"][int(state["active_index"]) % len(state["turn_order"])]
            if active == "pc-talia":
                break
            state = step(state, {"type": "end_turn", "actor_id": active}, self.rules)["state"]
        self.assertEqual(active, "pc-talia")
        cast = step(
            state,
            {
                "type": "cast_spell",
                "actor_id": "pc-talia",
                "spell": "false-life",
                "slot_level": 1,
                "target_ids": ["pc-talia"],
            },
            self.rules,
        )
        self.assertEqual(cast["status"], "ok", cast.get("error"))
        amount = next(ev["amount"] for ev in cast["events"] if ev.get("type") == "temp_hp_applied")
        self.assertGreater(int(amount), 0)
        hooks = list(cast.get("narrative_hooks") or []) + [
            f"(Temp HP: {amount})",
            "(Temp HP 15)",
            "( temp  hp:15 )",
            "(TEMPORARY HP: 15)",
            "(Temporary HP:15)",
        ]
        enemy_id = next(
            cid for cid, comb in cast["state"]["combatants"].items() if comb.get("side") == "enemy"
        )
        obs = build_observation(
            cast["state"],
            enemy_id,
            scen,
            {"party": [], "enemy": []},
            hooks,
            {},
            self.rules,
        )
        visible = "\n".join(_strings(obs))
        recent = "\n".join(obs["recent_events"])
        leaked = re.compile(r"(?i)(?:temp(?:orary)?\s+HP\s*:?\s*\d+|\d+\s+temp(?:orary)?\s+HP)")
        self.assertIsNone(leaked.search(visible), visible)
        self.assertIsNone(leaked.search(recent), recent)
        self.assertNotIn("temp_hp", obs["enemies"][0])

    def test_full_matches_hide_opposing_ac_hp_dc_and_slots(self) -> None:
        for scenario in _SCENARIOS:
            for seed in _SEEDS:
                party = _Recording(HeuristicTacticalDriver(self.rules))
                enemy = _Recording(HeuristicTacticalDriver(self.rules))
                run_match(
                    scenario,
                    party_driver=party,
                    enemy_driver=enemy,
                    rules=self.rules,
                    seed=seed,
                )
                for label, recorder in (("party", party), ("enemy", enemy)):
                    self.assertGreater(len(recorder.observations), 0, f"{scenario} seed {seed}")
                    for obs in recorder.observations:
                        for foe in obs["enemies"]:
                            leaked = _FORBIDDEN_ENEMY_KEYS & set(foe)
                            self.assertFalse(leaked, f"{scenario} seed {seed} {label} keys {leaked}")
                        for text in _strings({"recent_events": obs["recent_events"], "enemies": obs["enemies"]}):
                            for pattern in _LEAK_RES:
                                self.assertIsNone(
                                    pattern.search(text),
                                    f"{scenario} seed {seed} {label} leaked {pattern.pattern}: {text}",
                                )


class TestScoreFloorAndOrdering(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        cls.heuristic = _mean_composite(HeuristicTacticalDriver(cls.rules), cls.rules)
        cls.greedy = _mean_composite(GreedyDriver(cls.rules), cls.rules)
        cls.random = _mean_composite(RandomDriver(seed=1, rules=cls.rules), cls.rules)
        cls.noop = _mean_composite(_NoOp(), cls.rules)
        cls.talk_spam = _mean_composite(_TalkSpam(cls.rules), cls.rules)
        cls.garbage = _mean_composite(_Garbage(), cls.rules)
        cls.help_only = _mean_composite(_HelpOnly(), cls.rules)
        cls.help_talk = _mean_composite(_HelpOnly(talk=True), cls.rules)

    def test_noop_baseline_near_zero(self) -> None:
        # #22: a do-nothing policy must score near zero (was 7.52; ~1.25 is the legality floor).
        self.assertLess(self.noop, 2.0)

    def test_talk_spam_exploit_scores_near_noop(self) -> None:
        # #22: one attack plus idle chatter scored ~48-53. It now earns only its real damage and mastery.
        self.assertLess(self.talk_spam, 20.0)
        self.assertLess(self.talk_spam, self.random * 0.5)

    def test_help_only_scores_near_noop(self) -> None:
        # CTO M1: Help that never sets up a hit is not a contribution (was 45.18 / 48.18 with talk).
        self.assertLess(self.help_only, 20.0)
        self.assertLess(self.help_talk, 20.0)
        self.assertLess(self.help_only, self.random * 0.5)

    def test_reasonable_policies_score_clearly_above_noop(self) -> None:
        self.assertGreater(self.heuristic, 80.0)
        self.assertGreater(self.greedy, 80.0)
        self.assertGreater(self.random, self.noop + 25.0)

    def test_score_ordering(self) -> None:
        self.assertGreater(self.heuristic, self.greedy)
        self.assertGreater(self.greedy, self.random)
        self.assertGreater(self.random, self.talk_spam)
        self.assertGreater(self.talk_spam, self.noop)
        self.assertGreater(self.noop, self.garbage)

    def test_same_seed_replay_is_byte_identical(self) -> None:
        rules = self.rules
        first = run_match(
            "goblin-ambush",
            party_driver=HeuristicTacticalDriver(rules),
            enemy_driver=HeuristicTacticalDriver(rules),
            rules=rules,
            seed=7,
        )
        second = run_match(
            "goblin-ambush",
            party_driver=HeuristicTacticalDriver(rules),
            enemy_driver=HeuristicTacticalDriver(rules),
            rules=rules,
            seed=7,
        )
        self.assertEqual(json.dumps(first), json.dumps(second))
        replayed = replay_match(json.loads(json.dumps(first)), rules)
        self.assertTrue(replayed["deterministic_match"], replayed.get("diffs"))
        self.assertEqual(replayed["original_digest"], replayed["replayed_digest"])


class TestHelpCredit(unittest.TestCase):
    """Help counts as productive only when the helped ally attack happens and hits."""

    @staticmethod
    def _transcript(*, hit: bool) -> dict[str, Any]:
        combatants = {
            "pc-a": {"id": "pc-a", "side": "party", "hp": 10, "hp_max": 10, "alive": True},
            "pc-b": {"id": "pc-b", "side": "party", "hp": 10, "hp_max": 10, "alive": True},
            "gob": {"id": "gob", "side": "enemy", "hp": 7, "hp_max": 7, "alive": True},
        }

        def act(seq: int, actor: str, turn: int, intent: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "seq": seq, "round": 1, "turn_index": turn, "actor_id": actor, "side": "party",
                "first_attempt_legal": True, "intent": intent, "events": events, "state_diff": {},
            }

        attack_events: list[dict[str, Any]] = [
            {"type": "attack_roll", "actor_id": "pc-b", "target_id": "gob", "advantage_sources": ["help"], "hit": hit}
        ]
        if hit:
            attack_events.append(
                {"type": "damage_applied", "source_id": "pc-b", "target_id": "gob", "total_damage": 3,
                 "hp_before": 7, "hp_after": 4, "breakdown": []}
            )
        return {
            "winner": "draw",
            "rounds": 1,
            "config": {"max_rounds": 5},
            "initial_state": {"combatants": combatants},
            "final_state": {"combatants": combatants},
            "actions": [
                act(1, "pc-a", 0, {"type": "help", "actor_id": "pc-a", "target_id": "gob", "ally_id": "pc-b"},
                    [{"type": "action_help", "actor_id": "pc-a", "ally_id": "pc-b", "target_id": "gob"}]),
                act(2, "pc-a", 0, {"type": "end_turn", "actor_id": "pc-a"}, []),
                act(3, "pc-b", 1, {"type": "attack", "actor_id": "pc-b", "target_id": "gob", "weapon": "longsword"},
                    attack_events),
                act(4, "pc-b", 1, {"type": "end_turn", "actor_id": "pc-b"}, []),
            ],
        }

    def test_help_that_sets_up_a_hit_is_productive(self) -> None:
        from srd2024.arena.scoring import score_match

        m = score_match(self._transcript(hit=True))["metrics"]
        self.assertEqual(m["productive_turns"], 2)
        self.assertEqual(m["effective_help_actions"], 1)
        self.assertEqual(m["contribution"], 1.0)

    def test_help_spent_on_a_miss_is_not_productive(self) -> None:
        from srd2024.arena.scoring import score_match

        m = score_match(self._transcript(hit=False))["metrics"]
        self.assertEqual(m["productive_turns"], 1)  # only pc-b's attack turn
        self.assertEqual(m["effective_help_actions"], 0)
        self.assertEqual(m["help_actions"], 1)  # raw count still reported


if __name__ == "__main__":
    unittest.main()
