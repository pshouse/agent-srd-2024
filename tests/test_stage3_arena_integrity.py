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
# Policy-ordering and talk-spam guards average over seeds 1-10 (30 games per policy). Nine games (seeds 1-3) were
# too noisy to rank heuristic-tactical vs greedy: after #53 the heuristic trailed greedy there (59.83 vs 67.15) while
# beating it on seeds 11-40 (82.43 vs 68.21). See #90.
_ORDER_SEEDS = tuple(range(1, 11))
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


def _mean_composite(
    driver: Any, rules: RulesStore, *, enemy: Any | None = None, seeds: tuple[int, ...] = _SEEDS
) -> float:
    scores: list[float] = []
    for scenario in _SCENARIOS:
        for seed in seeds:
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


# Enemy line-ups from before the difficulty retune (#48), pinned here so the exploit bound below stays calibrated.
# On the tuned scenarios a random policy dies within a few turns and plays about as unproductively as talk-spam
# (both get 1-2 productive turns), so a ratio against random there measures the baseline's weakness, not the exploit.
_CALIBRATION_ENEMIES: dict[str, list[dict[str, Any]]] = {
    "goblin-ambush": [
        {"id": "goblin-1", "monster": "goblin-warrior", "name": "Goblin Scout A", "position": {"x": 20, "y": 0}},
        {"id": "goblin-2", "monster": "goblin-warrior", "name": "Goblin Scout B", "position": {"x": 25, "y": 15}},
    ],
    "crypt-of-ghouls": [
        {"id": "ghoul-1", "monster": "ghoul", "name": "Crypt Ghoul A", "position": {"x": 25, "y": 0}},
        {"id": "ghoul-2", "monster": "ghoul", "name": "Crypt Ghoul B", "position": {"x": 25, "y": 10}},
        {"id": "ghast-1", "monster": "ghast", "name": "Crypt Ghast", "position": {"x": 30, "y": 5}},
        {"id": "skeleton-1", "monster": "skeleton", "name": "Crypt Archer", "position": {"x": 35, "y": 15}},
    ],
    "wyrmling-lair": [
        {"id": "wyrmling-1", "monster": "red-dragon-wyrmling", "name": "Red Dragon Wyrmling", "position": {"x": 30, "y": 10}},
        {"id": "hound-1", "monster": "hell-hound", "name": "Cinder Hound", "position": {"x": 25, "y": 0}},
        {"id": "magmin-1", "monster": "magmin", "name": "Ember Magmin", "position": {"x": 25, "y": 20}},
    ],
}


_NOOP_SPELLS = ("light", "divine-favor", "hunters-mark", "hex")


def _noop_cast_scenario(sid: str, rules: RulesStore, *, temp_hp: int | None = None) -> dict[str, Any]:
    """A built-in scenario where every PC also knows Light and three per-hit riders the engine doesn't resolve."""
    from srd2024.arena.scenarios import _BUILTIN_SCENARIOS
    from srd2024.engine.character import compile_character

    raw = copy.deepcopy(_BUILTIN_SCENARIOS[sid])
    raw["id"] = f"{sid}-noop-cast"
    for entry in raw["party"]:
        sb = compile_character(entry.pop("spec"), rules)["statblock"]
        sc = sb.get("spellcasting") or {"ability": "wis", "spell_save_dc": 13, "spell_attack_bonus": 5, "is_pact_magic": False}
        sc["cantrips"] = list(sc.get("cantrips") or []) + ["light"]
        sc["prepared_spells"] = list(sc.get("prepared_spells") or []) + [
            "divine-favor", "hunters-mark", "hex", "false-life", "cure-wounds", "misty-step",
        ]
        sc["slots"] = {**(sc.get("slots") or {}), "1": 20, "2": 20}
        sb["spellcasting"] = sc
        entry["statblock"] = sb
        if temp_hp is not None:
            entry["temp_hp"] = temp_hp
    return raw


class _NoopCaster:
    """Issue #75 exploit: spend every turn on legal casts that do nothing (Light, unresolved rider damage).

    Before #75 every successful cast counted as a productive turn, so this scored 42.25 on all three
    scenarios with 0 damage and 0 wins, against random's ~25-32.
    """

    name = "noop-caster"

    def reset(self, seed: int | None = None) -> None:
        return

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        me = observation["self"]
        res = me.get("turn_resources") or {}
        sc = me.get("spellcasting") or {}
        slots = sorted(int(k) for k, v in (sc.get("slots") or {}).items() if int(v) > 0)
        for spell in _NOOP_SPELLS:
            cantrip = spell == "light"
            econ = "action" if cantrip else "bonus_action"
            if not res.get(econ) or (not cantrip and not slots):
                continue
            intent: dict[str, Any] = {"type": "cast_spell", "actor_id": me["id"], "spell": spell, "target_ids": [me["id"]]}
            if not cantrip:
                intent["slot_level"] = slots[0]
            return AgentDecision(intent=intent, rationale="no-op cast")
        return AgentDecision(intent={"type": "end_turn", "actor_id": me["id"]}, rationale="idle")


class _FriendlyNoop:
    """#77 MF1 exploits: friendly casts that change nothing.

    `false-life` re-casts while temp HP is at least 12, the most False Life can give; `heal-full` heals an ally who is at full HP; `misty-self`
    teleports onto your own square. Before the fix these scored 59.15, 40.12 and 45.25 on goblin.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.name = f"friendly-noop-{mode}"

    def reset(self, seed: int | None = None) -> None:
        return

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        me = observation["self"]
        res = me.get("turn_resources") or {}
        end = AgentDecision(intent={"type": "end_turn", "actor_id": me["id"]}, rationale="idle")
        if self.mode == "false-life" and res.get("action") and int(me.get("temp_hp") or 0) >= 12:
            return AgentDecision(intent={"type": "cast_spell", "actor_id": me["id"], "spell": "false-life", "slot_level": 1,
                                         "target_ids": [me["id"]]}, rationale="noop")
        if self.mode == "heal-full" and res.get("action"):
            full = [a for a in [me, *observation.get("allies", [])]
                    if int(a.get("hp", 0)) > 0 and int(a.get("hp", 0)) >= int(a.get("hp_max", 0) or 0)]
            if full:
                return AgentDecision(intent={"type": "cast_spell", "actor_id": me["id"], "spell": "cure-wounds", "slot_level": 1,
                                             "target_ids": [full[0]["id"]]}, rationale="noop")
        if self.mode == "misty-self" and res.get("bonus_action"):
            return AgentDecision(intent={"type": "cast_spell", "actor_id": me["id"], "spell": "misty-step", "slot_level": 2,
                                         "destination": dict(me["position"])}, rationale="noop")
        return end


class TestNoopCastRegression(unittest.TestCase):
    """#75: casts with no modeled effect earn no productive-turn credit."""

    def test_friendly_noop_casts_score_at_or_below_random(self) -> None:
        rules = RulesStore()
        scen = _noop_cast_scenario("goblin-ambush", rules)
        # PCs start with 50 temp HP, so a False Life re-cast (2d4 + 4, at most 12) can't raise it.
        scen_temp = _noop_cast_scenario("goblin-ambush", rules, temp_hp=50)
        rand = []
        for seed in _SEEDS:
            r = run_match(scen, party_driver=RandomDriver(seed=seed), enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=seed)
            rand.append(float(r["scorecard"]["composite_score"]))
        rand_mean = sum(rand) / len(rand)
        for mode in ("false-life", "heal-full", "misty-self"):
            with self.subTest(probe=mode):
                scores, wins, casts = [], 0, 0
                for seed in _SEEDS:
                    t = run_match(scen_temp if mode == "false-life" else scen, party_driver=_FriendlyNoop(mode),
                                  enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=seed)
                    casts += sum(1 for a in t["actions"] if a["side"] == "party" and a["intent"].get("type") == "cast_spell")
                    scores.append(float(t["scorecard"]["composite_score"]))
                    wins += t["winner"] == "party"
                self.assertGreater(casts, 0, "the probe must actually cast")
                self.assertEqual(wins, 0)
                self.assertLessEqual(sum(scores) / len(scores), rand_mean, f"{mode}: {sum(scores) / len(scores):.2f} vs random {rand_mean:.2f}")

    def test_noop_caster_scores_at_or_below_random_with_zero_wins(self) -> None:
        rules = RulesStore()
        noop_scores, rand_scores, noop_wins, noop_damage = [], [], 0, 0
        for sid in ("goblin-ambush", "crypt-of-ghouls", "wyrmling-lair"):
            scen = _noop_cast_scenario(sid, rules)
            for seed in _SEEDS:
                t = run_match(scen, party_driver=_NoopCaster(), enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=seed)
                casts = sum(1 for a in t["actions"] if a["side"] == "party" and a["intent"].get("type") == "cast_spell")
                self.assertGreater(casts, 0, "the probe must actually cast")
                noop_scores.append(float(t["scorecard"]["composite_score"]))
                noop_wins += t["winner"] == "party"
                noop_damage += int(t["scorecard"]["metrics"].get("effective_damage_dealt", 0))
                r = run_match(scen, party_driver=RandomDriver(seed=seed), enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=seed)
                rand_scores.append(float(r["scorecard"]["composite_score"]))
        noop, rand = sum(noop_scores) / len(noop_scores), sum(rand_scores) / len(rand_scores)
        self.assertEqual(noop_wins, 0)
        self.assertEqual(noop_damage, 0)
        self.assertLessEqual(noop, rand, f"no-op caster {noop:.2f} vs random {rand:.2f}")

    def test_cast_credit_rules(self) -> None:
        from srd2024.arena.scoring import _cast_had_effect

        side = {"pc-a", "pc-b"}
        base = {"state_diff": {"effect_changes": []}}
        self.assertFalse(_cast_had_effect({**base, "events": [{"type": "spell_cast"}, {"type": "spell_damage_unresolved"}]}, side, {}))
        self.assertFalse(_cast_had_effect({**base, "events": [{"type": "spell_cast_narrative"}]}, side, {}))
        self.assertTrue(_cast_had_effect({**base, "events": [{"type": "spell_attack_roll", "target_id": "gob"}]}, side, {}))
        self.assertTrue(_cast_had_effect({**base, "events": [{"type": "saving_throw", "target_id": "gob", "reason": "fireball"}]}, side, {}))
        # Damage only to your own side (#67's self-hit) isn't credit.
        self.assertFalse(_cast_had_effect({**base, "events": [{"type": "damage_applied", "target_id": "pc-a"}]}, side, {}))
        self.assertTrue(_cast_had_effect({**base, "events": [{"type": "healing_applied", "target_id": "pc-b", "hp_before": 4, "hp_after": 11}]}, side, {}))
        buff = {"events": [{"type": "spell_cast"}], "state_diff": {"effect_changes": [{"id": "pc-a", "added": ["mage_armor"], "removed": []}]}}
        self.assertTrue(_cast_had_effect(buff, side, {"effects": {"pc-a": set()}}))
        self.assertFalse(_cast_had_effect(buff, side, {"effects": {"pc-a": {"mage_armor"}}}), "re-cast of an active buff")
        self.assertTrue(_cast_had_effect({"intent": {"type": "cast_spell"}}, side, {}), "synthetic transcript keeps credit")
        # #77 MF1: friendly events that changed nothing earn nothing.
        heal_full = {**base, "events": [{"type": "healing_applied", "target_id": "pc-b", "hp_before": 12, "hp_after": 12, "revived": False}]}
        self.assertFalse(_cast_had_effect(heal_full, side, {}), "healing at full HP")
        revive = {**base, "events": [{"type": "healing_applied", "target_id": "pc-b", "hp_before": 0, "hp_after": 0, "revived": True}]}
        self.assertTrue(_cast_had_effect(revive, side, {}))
        temp = {**base, "events": [{"type": "temp_hp_applied", "target_id": "pc-a", "temp_hp_after": 7}]}
        self.assertTrue(_cast_had_effect(temp, side, {"temp_hp": {"pc-a": 0}}))
        self.assertFalse(_cast_had_effect(temp, side, {"temp_hp": {"pc-a": 7}}), "False Life re-cast, temp HP unchanged")
        tele = {"type": "teleported", "actor_id": "pc-a", "from_pos": {"x": 0, "y": 0}}
        self.assertTrue(_cast_had_effect({**base, "events": [{**tele, "to_pos": {"x": 30, "y": 0}}]}, side, {}))
        self.assertFalse(_cast_had_effect({**base, "events": [{**tele, "to_pos": {"x": 0, "y": 0}}]}, side, {}), "zero-distance teleport")


class TestTalkSpamRegression(unittest.TestCase):
    """CTO (Phase 3): talk-spam must stay at or under ~0.6x a random policy, and never win.

    Measured on the calibration line-ups: 17.24 vs 32.75 (0.526) on seeds 1-3, 16.20 vs 35.97 (0.450) on seeds 1-10.
    After #53 and #90 it runs on seeds 1-10 (`_ORDER_SEEDS`): 16.77 vs 36.19 (0.463). Seeds 1-3 alone measured
    0.632, too few games for a ratio bound.
    """

    def test_talk_spam_stays_under_six_tenths_of_random_with_zero_wins(self) -> None:
        from srd2024.arena.scenarios import _BUILTIN_SCENARIOS

        rules = RulesStore()

        def mean_and_wins(driver: Any) -> tuple[float, int]:
            scores: list[float] = []
            wins = 0
            for sid, enemies in _CALIBRATION_ENEMIES.items():
                raw = copy.deepcopy(_BUILTIN_SCENARIOS[sid])
                raw["id"] = f"{sid}-calibration"
                raw["enemies"] = copy.deepcopy(enemies)
                for seed in _ORDER_SEEDS:
                    if hasattr(driver, "reset"):
                        driver.reset(seed)
                    t = run_match(raw, party_driver=driver, enemy_driver=HeuristicTacticalDriver(rules), rules=rules, seed=seed)
                    scores.append(float(t["scorecard"]["composite_score"]))
                    wins += t["winner"] == "party"
            return sum(scores) / len(scores), wins

        talk_spam, talk_wins = mean_and_wins(_TalkSpam(rules))
        random_score, _ = mean_and_wins(RandomDriver(seed=1, rules=rules))
        self.assertEqual(talk_wins, 0, "talk-spam must never win")
        self.assertLessEqual(talk_spam, 0.6 * random_score, f"talk-spam {talk_spam:.2f} vs random {random_score:.2f}")


class TestScoreFloorAndOrdering(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()
        # The four real policies are ranked over seeds 1-10; the degenerate ones score ~0 on any seed set.
        cls.heuristic = _mean_composite(HeuristicTacticalDriver(cls.rules), cls.rules, seeds=_ORDER_SEEDS)
        cls.greedy = _mean_composite(GreedyDriver(cls.rules), cls.rules, seeds=_ORDER_SEEDS)
        cls.random = _mean_composite(RandomDriver(seed=1, rules=cls.rules), cls.rules, seeds=_ORDER_SEEDS)
        cls.noop = _mean_composite(_NoOp(), cls.rules)
        cls.talk_spam = _mean_composite(_TalkSpam(cls.rules), cls.rules, seeds=_ORDER_SEEDS)
        cls.garbage = _mean_composite(_Garbage(), cls.rules)
        cls.help_only = _mean_composite(_HelpOnly(), cls.rules)
        cls.help_talk = _mean_composite(_HelpOnly(talk=True), cls.rules)

    def test_noop_baseline_near_zero(self) -> None:
        # #22: a do-nothing policy must score near zero (was 7.52; ~1.25 is the legality floor).
        self.assertLess(self.noop, 2.0)

    def test_talk_spam_exploit_scores_near_noop(self) -> None:
        # #22: one attack plus idle chatter scored ~48-53. It now earns only its real damage and mastery.
        self.assertLess(self.talk_spam, 20.0)
        # It must stay below even a random policy. (A ratio bound, < 0.55 x random, held until the scenarios were
        # retuned for difficulty: random play now loses most matches, 32.8 -> 20.0, while talk-spam is 17.2 -> 15.8.)
        self.assertLess(self.talk_spam, self.random)
        self.assertLess(self.talk_spam, self.heuristic * 0.3)

    def test_help_only_scores_near_noop(self) -> None:
        # CTO M1: Help that never sets up a hit is not a contribution (was 45.18 / 48.18 with talk).
        self.assertLess(self.help_only, 20.0)
        self.assertLess(self.help_talk, 20.0)
        self.assertLess(self.help_only, self.random * 0.5)

    def test_reasonable_policies_score_clearly_above_noop(self) -> None:
        # Scenarios were retuned so heuristic-vs-heuristic is no longer a near-certain party win (94.7 / 84.9 / 32.8
        # before; 64.5 / 59.6 / 20.0 after). The ordering is what matters.
        # Seeds 1-10 (#90): heuristic 73.53, greedy 67.24, random 26.88.
        self.assertGreater(self.heuristic, 60.0)
        self.assertGreater(self.greedy, 55.0)
        self.assertGreater(self.heuristic, self.greedy)
        self.assertGreater(self.greedy, self.random)
        self.assertGreater(self.random, self.noop + 15.0)

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


class TestCommittedTracesReplay(unittest.TestCase):
    """Every committed example trace must replay to the same digest.

    The digest covers the scorecard, so a scorer change must refresh committed traces (#25 follow-up to #30).
    """

    def test_example_traces_replay_deterministically(self) -> None:
        from pathlib import Path

        rules = RulesStore()
        traces = sorted((Path(__file__).resolve().parents[1] / "examples" / "sessions").glob("*.trace.json"))
        self.assertTrue(traces, "no committed traces found")
        for path in traces:
            with self.subTest(trace=path.name):
                rep = replay_match(json.loads(path.read_text(encoding="utf-8")), rules)
                self.assertTrue(rep["deterministic_match"], rep.get("diffs"))


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
