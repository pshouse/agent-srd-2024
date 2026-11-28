"""Multi-agent 5e 2024 (SRD 5.2) arena match runner, replay verifier, and benchmark suite.

Implements STAGE3-SPEC §4.2 and §5:
- `build_observation`: strict partial observability & same-side blackboard isolation.
- `run_match`: pure `srd2024.engine.combat.step()` adjudication, non-invasive monster
  recharge sidecar, incapacitated/0-HP auto-skip, retry feedback + forced `end_turn` fallback.
- `compute_transcript_digest` & `replay_match`: 100% deterministic trace verification.
- `run_benchmark`: multi-seed, multi-scenario leaderboard runner.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from srd2024.engine.combat import (
    _effective_ac,
    _effective_conditions,
    _effective_speed,
    _has_modifier_flag,
    compute_legal_actions,
    distance_ft,
    step,
)
from srd2024.engine.dice import DeterministicRNG
from srd2024.engine.store import RulesStore, strip_prefix

from .agents import AgentDriver, ProviderUnavailable, ReplayDriver, create_driver, scrub_secrets
from .budget import LedgerError, MatchBudgetExceeded, MatchMeter
from .scenarios import BUILTIN_SCENARIO_IDS, load_scenario
from .scoring import score_match

_INCAPACITATING_CONDITIONS: frozenset[str] = frozenset(
    {"unconscious", "paralyzed", "stunned", "petrified", "incapacitated"}
)

_VISIBLE_EFFECT_KINDS: frozenset[str] = frozenset(
    {
        "dodge",
        "shield",
        "shield_of_faith",
        "mage_armor",
        "barkskin",
        "bless",
        "bane",
        "haste",
        "slow",
        "sap",
        "help",
        "guiding_bolt_advantage",
        "next_attack_advantage",
        "longstrider",
    }
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")


def _condition_slugs(combatant: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in combatant.get("conditions") or []:
        if isinstance(entry, dict):
            sl = strip_prefix(str(entry.get("condition") or ""))
        else:
            sl = strip_prefix(str(entry))
        if sl and sl not in out:
            out.append(sl)
    if int(combatant.get("exhaustion_level", 0)) > 0 and "exhaustion" not in out:
        out.append("exhaustion")
    return out


def _effect_kinds(combatant: dict[str, Any], *, visible_only: bool = False) -> list[str]:
    out: list[str] = []
    for eff in combatant.get("active_effects") or []:
        if not isinstance(eff, dict):
            continue
        kind = str(eff.get("kind") or "").strip()
        if not kind:
            continue
        if visible_only and kind not in _VISIBLE_EFFECT_KINDS:
            continue
        if kind not in out:
            out.append(kind)
    return out


def _hp_band(combatant: dict[str, Any]) -> tuple[str, bool]:
    """Map enemy HP to public band without leaking exact HP or max HP."""
    hp = int(combatant.get("hp", 0))
    hp_max = max(1, int(combatant.get("hp_max", 1)))
    if not combatant.get("alive", False) or hp <= 0:
        return "defeated", False
    if hp >= hp_max:
        return "unharmed", False
    if hp > hp_max // 2:
        return "scratched", False
    if hp > hp_max // 4:
        return "bloodied", True
    return "critical", True


def _find_monster_action(actor: dict[str, Any], action_name: str) -> dict[str, Any] | None:
    needle = action_name.strip().lower()
    needle_slug = _slugify(action_name)
    for act in actor.get("monster_actions") or []:
        aname = str(act.get("name") or "").strip()
        if aname.lower() == needle or _slugify(aname) == needle_slug:
            return act
    return None


def _is_recharge_action(action: dict[str, Any]) -> bool:
    usage = action.get("usage")
    return isinstance(usage, dict) and str(usage.get("type") or "").strip().lower() == "recharge on roll"


def _is_action_on_cooldown(
    cooldowns: dict[str, dict[str, bool]],
    actor_id: str,
    action_name: str,
) -> bool:
    actor_cd = cooldowns.get(actor_id) or {}
    if actor_cd.get(action_name, False):
        return True
    slug = _slugify(action_name)
    for k, on_cd in actor_cd.items():
        if on_cd and _slugify(k) == slug:
            return True
    return False


def _available_monster_action_names(
    actor: dict[str, Any],
    cooldowns: dict[str, dict[str, bool]],
) -> list[str]:
    if actor.get("kind") != "monster":
        return []
    actor_id = str(actor.get("id") or "")
    out: list[str] = []
    for act in actor.get("monster_actions") or []:
        aname = str(act.get("name") or "").strip()
        if aname and not _is_action_on_cooldown(cooldowns, actor_id, aname):
            out.append(aname)
    return out


def _reaction_spells_for(actor: dict[str, Any], rules: RulesStore) -> list[str]:
    res = actor.get("turn_resources") or {}
    if not res.get("reaction", False):
        return []
    sc = actor.get("spellcasting")
    if not isinstance(sc, dict):
        return []
    avail_slots = {int(k) for k, v in (sc.get("slots") or {}).items() if int(v) > 0}
    out: list[str] = []
    all_sp = list(sc.get("cantrips") or []) + list(sc.get("prepared_spells") or [])
    for sp_slug in all_sp:
        sp_rec = rules.get_spell(sp_slug)
        if sp_rec is None:
            continue
        if str(sp_rec.get("action_type") or "").lower() != "reaction":
            continue
        sp_lvl = int(sp_rec.get("level", 0))
        if sp_lvl > 0 and not any(s >= sp_lvl for s in avail_slots):
            continue
        if sp_slug not in out:
            out.append(sp_slug)
    return out


def build_observation(
    state: dict[str, Any],
    actor_id: str,
    scenario: dict[str, Any],
    blackboard: dict[str, list[dict[str, Any]]],
    recent_hooks: list[str],
    cooldowns: dict[str, dict[str, bool]],
    rules: RulesStore,
    retry_feedback: str | None = None,
) -> dict[str, Any]:
    """Build a strict partial-observability observation for `actor_id` (STAGE3-SPEC §4.2).

    Information-hiding invariant: opposing-side entries NEVER contain exact `hp`,
    `hp_max`, `ac`, private `spellcasting`, or opposing-side `table_talk`.
    """
    combatants = state["combatants"]
    actor = combatants[actor_id]
    side = str(actor["side"])
    actor_pos = actor["position"]

    avail_monster_actions = _available_monster_action_names(actor, cooldowns)
    self_conds = _condition_slugs(actor)
    eff_speed = _effective_speed(actor, rules)
    rem_move = int(actor.get("movement_remaining_ft", 0))

    self_view: dict[str, Any] = {
        "id": actor["id"],
        "name": actor["name"],
        "side": side,
        "kind": actor.get("kind", "pc"),
        "source_id": actor.get("source_id", ""),
        "level": int(actor.get("level", 1)),
        "size": actor.get("size", "Medium"),
        "position": copy.deepcopy(actor_pos),
        "hp": int(actor["hp"]),
        "hp_max": int(actor["hp_max"]),
        "temp_hp": int(actor.get("temp_hp", 0)),
        "ac": int(actor["ac"]),
        "effective_ac": _effective_ac(actor),
        "wearing_armor": bool(actor.get("wearing_armor", False)),
        "ability_mods": copy.deepcopy(actor.get("ability_mods", {})),
        "speed_ft": int(actor.get("speed_ft", 30)),
        "movement_remaining_ft": rem_move,
        "conditions": self_conds,
        "concentration": copy.deepcopy(actor.get("concentration")),
        "active_effects": _effect_kinds(actor, visible_only=False),
        "heroic_inspiration": bool(actor.get("heroic_inspiration", False)),
        "turn_resources": copy.deepcopy(actor.get("turn_resources", {})),
        "weapons": copy.deepcopy(actor.get("weapons", [])),
        "monster_actions": copy.deepcopy(actor.get("monster_actions", [])),
        "available_monster_actions": list(avail_monster_actions),
        "spellcasting": copy.deepcopy(actor.get("spellcasting")),
        "damage_resistances": list(actor.get("damage_resistances") or []),
        "damage_immunities": list(actor.get("damage_immunities") or []),
        "death_saves": copy.deepcopy(actor.get("death_saves", {"successes": 0, "failures": 0})),
    }

    # Determine actor melee reach for `in_reach_enemies`
    max_reach = 5
    for w in actor.get("weapons") or []:
        if str(w.get("weapon_range") or "melee").lower() == "melee":
            max_reach = max(max_reach, int(w.get("reach_ft") or 5))
    for ma in actor.get("monster_actions") or []:
        if ma.get("reach_ft"):
            max_reach = max(max_reach, int(ma["reach_ft"]))

    allies: list[dict[str, Any]] = []
    enemies: list[dict[str, Any]] = []
    in_reach_enemies: list[str] = []
    downed_allies: list[str] = []

    ordered_ids = list(state.get("turn_order") or sorted(combatants.keys()))
    for cid in ordered_ids:
        if cid == actor_id or cid not in combatants:
            continue
        other = combatants[cid]
        dist = distance_ft(actor_pos, other["position"])
        other_conds = _condition_slugs(other)
        if str(other["side"]) == side:
            is_alive = bool(other.get("alive", False))
            other_hp = int(other.get("hp", 0))
            unconscious = bool(is_alive and (other_hp == 0 or "unconscious" in other_conds))
            if is_alive and other_hp == 0:
                downed_allies.append(cid)
            allies.append(
                {
                    "id": other["id"],
                    "name": other["name"],
                    "kind": other.get("kind", "pc"),
                    "source_id": other.get("source_id", ""),
                    "position": copy.deepcopy(other["position"]),
                    "distance_ft": dist,
                    "hp": other_hp,
                    "hp_max": int(other.get("hp_max", 1)),
                    "ac": int(other.get("ac", 10)),
                    "alive": is_alive,
                    "unconscious": unconscious,
                    "conditions": other_conds,
                    "concentration": copy.deepcopy(other.get("concentration")),
                    "active_effects": _effect_kinds(other, visible_only=False),
                }
            )
        else:
            band, bloodied = _hp_band(other)
            is_enemy_alive = bool(
                other.get("alive", False)
                and (other.get("kind") == "pc" or int(other.get("hp", 0)) > 0)
            )
            if is_enemy_alive and int(other.get("hp", 0)) > 0 and dist <= max_reach:
                in_reach_enemies.append(cid)
            # STRICT PARTIAL OBSERVABILITY: never expose exact hp, hp_max, ac, or slots!
            enemies.append(
                {
                    "id": other["id"],
                    "name": other["name"],
                    "source_id": other.get("source_id", ""),
                    "size": other.get("size", "Medium"),
                    "position": copy.deepcopy(other["position"]),
                    "distance_ft": dist,
                    "alive": is_enemy_alive,
                    "bloodied": bloodied,
                    "hp_band": band,
                    "conditions": other_conds,
                    "visible_effects": _effect_kinds(other, visible_only=True),
                }
            )

    legal = compute_legal_actions(state, rules)
    hint = legal.get("reroll_hint")
    if isinstance(hint, dict):
        target = combatants.get(str(hint.get("target_id") or ""))
        if isinstance(target, dict) and str(target.get("side")) != side:
            hint.pop("target_ac", None)
    if actor.get("kind") == "monster":
        legal["weapons"] = list(avail_monster_actions) if (actor.get("turn_resources") or {}).get("action") else []
    reaction_spells = _reaction_spells_for(actor, rules)
    can_stand_up = bool(
        "prone" in self_conds and eff_speed > 0 and rem_move >= (eff_speed // 2)
    )
    legal["reaction_spells"] = reaction_spells
    legal["available_monster_actions"] = (
        list(avail_monster_actions)
        if (actor.get("kind") == "monster" and (actor.get("turn_resources") or {}).get("action"))
        else []
    )
    legal["can_stand_up"] = can_stand_up
    legal["in_reach_enemies"] = in_reach_enemies
    legal["downed_allies"] = downed_allies

    side_board = list(blackboard.get(side) or [])[-12:]
    hooks_window = [_observer_event_text(h) for h in list(recent_hooks or [])[-12:]]

    return {
        "round": int(state.get("round", 1)),
        "scenario": {
            "id": str(scenario.get("id") or state.get("encounter_id") or ""),
            "name": str(scenario.get("name") or ""),
            "description": str(scenario.get("description") or ""),
        },
        "self": self_view,
        "allies": allies,
        "enemies": enemies,
        "legal_actions": legal,
        "party_blackboard": copy.deepcopy(side_board),
        "recent_events": list(hooks_window),
        "retry_feedback": retry_feedback,
    }


def _resolve_winner(state: dict[str, Any]) -> str:
    combatants = (state.get("combatants") or {}).values()
    party = [c for c in combatants if str(c.get("side")) == "party"]
    enemies = [c for c in combatants if str(c.get("side")) != "party"]

    party_alive = sum(1 for c in party if c.get("alive"))
    party_conscious = sum(1 for c in party if c.get("alive") and int(c.get("hp", 0)) > 0)
    enemies_alive = sum(1 for c in enemies if c.get("alive") and int(c.get("hp", 0)) > 0)

    if enemies_alive == 0 and party_alive > 0:
        return "party"
    if party_alive == 0 or (enemies_alive > 0 and party_conscious == 0):
        return "enemy"
    return "draw"


def _is_match_over(state: dict[str, Any]) -> bool:
    if state.get("status") == "ended":
        return True
    winner = _resolve_winner(state)
    return winner in ("party", "enemy")


def _select_driver(
    driver_or_map: AgentDriver | dict[str, AgentDriver],
    actor_id: str,
) -> AgentDriver:
    if isinstance(driver_or_map, dict):
        if actor_id in driver_or_map:
            return driver_or_map[actor_id]
        if "*" in driver_or_map:
            return driver_or_map["*"]
        if "default" in driver_or_map:
            return driver_or_map["default"]
        raise ValueError(f"No driver configured for combatant '{actor_id}'.")
    return driver_or_map


def _describe_driver_config(driver_or_map: AgentDriver | dict[str, AgentDriver]) -> str | dict[str, str]:
    if isinstance(driver_or_map, dict):
        return {k: str(getattr(v, "name", type(v).__name__)) for k, v in sorted(driver_or_map.items())}
    return str(getattr(driver_or_map, "name", type(driver_or_map).__name__))


_ARENA_INTENT_TYPES = frozenset(
    {
        "move",
        "attack",
        "cast_spell",
        "dodge",
        "dash",
        "disengage",
        "help",
        "end_turn",
        "reroll",
    }
)
_PRIVATE_RESOURCE_RE = re.compile(
    r"spell slots?|slots remaining|no Reaction|Reaction remaining",
    re.IGNORECASE,
)
_NAMED_RESOURCE_RE = re.compile(r"^(\S+) has no\b")
# Narration a player sees. The referee log in the transcript keeps the original
# hooks. These patterns also cover forms Stage 4 adds, such as "(18/32 HP)",
# "(failed CON DC 13)", and "(AC 17, ...)".
_HIDDEN_NARRATION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*\([^)]*\b(?:AC|DC)\b[^)]*\)", re.IGNORECASE),
    re.compile(r"\bvs\s+AC\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bAC\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bDC\s+\d+\b", re.IGNORECASE),
    re.compile(r"\s*\(\s*\d+\s*/\s*\d+\s*HP[^)]*\)", re.IGNORECASE),
    re.compile(r"\b\d+\s*/\s*\d+\s*HP\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+temp(?:orary)?\s+HP\b", re.IGNORECASE),
    # Stage 4 writes "(Temp HP: 15)" (any spacing, colon optional).
    re.compile(r"\(\s*temp(?:orary)?\s+HP\s*:?\s*\d+[^)]*\)", re.IGNORECASE),
    re.compile(r"\blevel-\d+\s+spell\s+slots?\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+spell\s+slots?\b", re.IGNORECASE),
    # Death saves (#73 MF1): the d20 roll and the running N/3 tally belong to the dying PC's sheet only. Every
    # seat still sees that the save was made, succeeded or failed.
    re.compile(r"(?<=Death Save)\s*\(\s*(?:\d+|two failures)\s*\)", re.IGNORECASE),
    re.compile(r"\s*:\s*\d+\s*/\s*3\s+successes\s*,\s*\d+\s*/\s*3\s+failures", re.IGNORECASE),
)


def _observer_event_text(text: str) -> str:
    """Drop hidden AC, DC, exact HP, temp HP, slot counts and death-save rolls/tallies from player narration."""
    cleaned = str(text)
    for pattern in _HIDDEN_NARRATION_RES:
        cleaned = pattern.sub("", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


_MAX_RETRY_HISTORY = 8


def _retry_feedback_with_history(
    retry_feedback: str | None,
    violations: list[dict[str, Any]],
    turn_index: int,
    actor_id: str,
) -> str | None:
    """Add every earlier rejected intent from this turn to `retry_feedback` (#55).

    The latest rejection is already described by `retry_feedback`. Without the earlier ones, a driver
    bounces between intents it already tried until the retries run out. Returns None or the unchanged
    message when there is nothing to add.
    """
    if not retry_feedback:
        return retry_feedback
    this_turn = [v for v in violations if v.get("turn_index") == turn_index and v.get("actor_id") == actor_id]
    earlier = this_turn[:-1][-_MAX_RETRY_HISTORY:]
    if not earlier:
        return retry_feedback
    lines = []
    for v in earlier:
        intent = v.get("intent") or {}
        shown = json.dumps(intent, sort_keys=True) if intent else "(no valid intent)"
        lines.append(f"- {shown}: {v.get('error')}")
    return scrub_secrets(
        f"{retry_feedback}\nAlso rejected earlier this turn (do not repeat any of these):\n" + "\n".join(lines)
    )


def _arena_intent_rejection(intent: dict[str, Any], active_id: str) -> str | None:
    """Reject intents the arena must not pass to the engine.

    Allowed types are the turn actions a combatant can choose. `actor_id` must
    be present and equal the active combatant, so a driver cannot rebuild the
    encounter or spend another creature's action.
    """
    raw_type = intent.get("type")
    itype = str(raw_type).strip().lower() if isinstance(raw_type, str) else ""
    if itype not in _ARENA_INTENT_TYPES:
        shown = itype[:64]
        return f"Intent type {shown!r} is not allowed."
    actor = intent.get("actor_id")
    if not isinstance(actor, str) or actor != active_id:
        return "Intent rejected: actor_id must be the active combatant."
    return None


def _driver_visible_error(message: str, active_id: str) -> str:
    """Hide another combatant's slots and reaction from driver-facing text."""
    text = str(message or "Invalid intent.")
    named = _NAMED_RESOURCE_RE.match(text)
    if _PRIVATE_RESOURCE_RE.search(text) and (named is None or named.group(1) != active_id):
        return "Intent rejected."
    return text


def _cannot_act(actor: dict[str, Any], rules: RulesStore) -> bool:
    if not actor.get("alive", False) or int(actor.get("hp", 0)) <= 0:
        return True
    if _has_modifier_flag(actor, "cannot_act", rules):
        return True
    if _effective_conditions(actor, rules) & _INCAPACITATING_CONDITIONS:
        return True
    return False


def compute_transcript_digest(transcript: dict[str, Any]) -> str:
    """SHA-256 hex digest of canonical JSON over final_state, all action events, winner, and scorecard."""
    payload = {
        "winner": transcript.get("winner"),
        "final_state": transcript.get("final_state"),
        "events": [a.get("events", []) for a in (transcript.get("actions") or [])],
        "scorecard": transcript.get("scorecard"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_match(
    scenario: str | Path | dict[str, Any],
    *,
    party_driver: AgentDriver | dict[str, AgentDriver],
    enemy_driver: AgentDriver | dict[str, AgentDriver],
    rules: RulesStore,
    seed: int = 42,
    max_rounds: int | None = None,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
) -> dict[str, Any]:
    """Run a complete multi-turn 5e 2024 (SRD 5.2) arena match and return a MatchTranscript."""
    compiled = load_scenario(scenario, rules)
    effective_max_rounds = int(max_rounds) if max_rounds is not None else int(compiled["max_rounds"])
    if effective_max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {effective_max_rounds}.")

    start_res = step(
        None,
        {
            "type": "start_encounter",
            "encounter_id": compiled["id"],
            "seed": int(seed),
            "combatants": copy.deepcopy(compiled["combatants"]),
        },
        rules,
    )
    if start_res.get("status") != "ok":
        raise ValueError(f"Failed to start encounter '{compiled['id']}': {start_res.get('error')}")

    state: dict[str, Any] = start_res["state"]
    initial_state: dict[str, Any] = copy.deepcopy(state)
    recent_hooks: list[str] = list(start_res.get("narrative_hooks") or [])

    recharge_rng = DeterministicRNG(seed=int(seed) ^ 0x5A5A5A5A)
    cooldowns: dict[str, dict[str, bool]] = {}
    recharge_events: list[dict[str, Any]] = []
    blackboard: dict[str, list[dict[str, Any]]] = {"party": [], "enemy": []}
    actions: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    turn_counter = 0
    action_seq = 0
    max_total_turns = effective_max_rounds * max(1, len(state["turn_order"])) + 4

    while not _is_match_over(state) and int(state["round"]) <= effective_max_rounds:
        if turn_counter >= max_total_turns:
            break

        cur_round = int(state["round"])
        turn_order = state["turn_order"]
        active_id = turn_order[int(state["active_index"]) % len(turn_order)]
        actor = state["combatants"][active_id]
        side = str(actor["side"])
        driver_source = party_driver if side == "party" else enemy_driver
        driver = _select_driver(driver_source, active_id)
        driver_name = str(getattr(driver, "name", type(driver).__name__))

        # 1. Monster recharge sidecar check at start of monster's turn.
        if actor.get("kind") == "monster" and actor.get("alive") and int(actor.get("hp", 0)) > 0:
            for m_act in actor.get("monster_actions") or []:
                if not _is_recharge_action(m_act):
                    continue
                act_name = str(m_act.get("name") or "")
                if not act_name or not _is_action_on_cooldown(cooldowns, active_id, act_name):
                    continue
                usage = m_act.get("usage") or {}
                dice_expr = str(usage.get("dice") or "1d6")
                min_val = int(usage.get("min_value") or 5)
                roll_val = int(recharge_rng.roll_dice_expr(dice_expr, crit=False)["total"])
                recharged = roll_val >= min_val
                if recharged:
                    cooldowns.setdefault(active_id, {})[act_name] = False
                recharge_events.append(
                    {
                        "round": cur_round,
                        "actor_id": active_id,
                        "action": act_name,
                        "roll": roll_val,
                        "min_value": min_val,
                        "recharged": recharged,
                    }
                )

        # 2. Auto-skip incapacitated / 0-HP / dead turns cleanly.
        if _cannot_act(actor, rules):
            skip_intent = {"type": "end_turn", "actor_id": active_id}
            skip_res = step(state, skip_intent, rules)
            action_seq += 1
            actions.append(
                {
                    "seq": action_seq,
                    "round": cur_round,
                    "turn_index": turn_counter,
                    "action_index": 0,
                    "actor_id": active_id,
                    "actor_name": actor["name"],
                    "side": side,
                    "driver": driver_name,
                    "attempts": 0,
                    "first_attempt_legal": True,
                    "auto_skipped": True,
                    "auto_end_turn": False,
                    "forced_fallback": False,
                    "intent": skip_intent,
                    "rationale": "Auto-skipped (incapacitated or 0 HP).",
                    "table_talk": None,
                    "events": skip_res.get("events") or [],
                    "state_diff": skip_res.get("state_diff") or {},
                    "narrative_hooks": skip_res.get("narrative_hooks") or [],
                    "usage": {},
                }
            )
            recent_hooks.extend(skip_res.get("narrative_hooks") or [])
            state = skip_res["state"]
            turn_counter += 1
            continue

        # 3. Active turn action slots with retry feedback & forced end_turn fallback.
        turn_ended = False
        for action_index in range(max(1, int(max_actions_per_turn))):
            if _is_match_over(state):
                turn_ended = True
                break
            now_active = state["turn_order"][int(state["active_index"]) % len(state["turn_order"])]
            if now_active != active_id or int(state["round"]) != cur_round:
                turn_ended = True
                break
            # Dropped mid-turn (e.g. by an Opportunity Attack while moving away, #38): end the turn cleanly
            # instead of asking the driver for an action it can no longer take.
            if action_index > 0 and _cannot_act(state["combatants"][active_id], rules):
                drop_intent = {"type": "end_turn", "actor_id": active_id}
                drop_res = step(state, drop_intent, rules)
                action_seq += 1
                actions.append(
                    {
                        "seq": action_seq,
                        "round": cur_round,
                        "turn_index": turn_counter,
                        "action_index": action_index,
                        "actor_id": active_id,
                        "actor_name": actor["name"],
                        "side": side,
                        "driver": driver_name,
                        "attempts": 0,
                        "first_attempt_legal": True,
                        "auto_skipped": True,
                        "auto_end_turn": False,
                        "forced_fallback": False,
                        "intent": drop_intent,
                        "rationale": "Auto-ended: incapacitated or at 0 HP mid-turn.",
                        "table_talk": None,
                        "events": drop_res.get("events") or [],
                        "state_diff": drop_res.get("state_diff") or {},
                        "narrative_hooks": drop_res.get("narrative_hooks") or [],
                        "usage": {},
                    }
                )
                recent_hooks.extend(drop_res.get("narrative_hooks") or [])
                state = drop_res["state"]
                turn_ended = True
                break

            retry_feedback: str | None = None
            accepted = False

            for attempt in range(1, max(0, int(max_retries_per_action)) + 2):
                observation = build_observation(
                    state,
                    active_id,
                    compiled,
                    blackboard,
                    recent_hooks,
                    cooldowns,
                    rules,
                    retry_feedback=_retry_feedback_with_history(retry_feedback, violations, turn_counter, active_id),
                )
                try:
                    decision = driver.decide(observation)
                    raw_intent = getattr(decision, "intent", None)
                    if not isinstance(raw_intent, dict) or not raw_intent.get("type"):
                        raise ValueError("Driver decision intent must be a dict with a non-empty 'type'.")
                    intent = copy.deepcopy(raw_intent)
                except (ProviderUnavailable, MatchBudgetExceeded):
                    # Out of credits, failing persistently, or out of this match's budget: the match can't be played
                    # fairly. Let the bench stop this contender instead of recording violations and forcing end_turn.
                    raise
                except Exception as exc:
                    err_msg = scrub_secrets(str(exc) or type(exc).__name__)
                    violations.append(
                        {
                            "round": cur_round,
                            "turn_index": turn_counter,
                            "actor_id": active_id,
                            "side": side,
                            "action_index": action_index,
                            "attempt": attempt,
                            "intent": {},
                            "error": err_msg,
                        }
                    )
                    retry_feedback = scrub_secrets(
                        f"Attempt {attempt} decision error: {err_msg}. "
                        "Emit a single valid JSON object with a legal 'intent'."
                    )
                    continue

                itype = str(intent["type"]).strip().lower()
                intent["type"] = itype
                rejection = _arena_intent_rejection(intent, active_id)
                if rejection is not None:
                    violations.append(
                        {
                            "round": cur_round,
                            "turn_index": turn_counter,
                            "actor_id": active_id,
                            "side": side,
                            "action_index": action_index,
                            "attempt": attempt,
                            "intent": intent,
                            "error": rejection,
                        }
                    )
                    retry_feedback = (
                        f"{rejection} Emit one allowed intent for the active combatant."
                    )
                    continue

                # Recharge sidecar validation before calling combat.step().
                recharging_action_name: str | None = None
                cur_actor = state["combatants"][active_id]
                if itype == "attack" and cur_actor.get("kind") == "monster":
                    req_act = str(intent.get("action") or "")
                    if not req_act and cur_actor.get("monster_actions"):
                        req_act = str(cur_actor["monster_actions"][0].get("name") or "")
                    m_act_obj = _find_monster_action(cur_actor, req_act) if req_act else None
                    if m_act_obj is not None:
                        canon_name = str(m_act_obj.get("name") or req_act)
                        if _is_action_on_cooldown(cooldowns, active_id, canon_name):
                            err_msg = f"Action '{canon_name}' is currently recharging."
                            violations.append(
                                {
                                    "round": cur_round,
                                    "turn_index": turn_counter,
                                    "actor_id": active_id,
                                    "side": side,
                                    "action_index": action_index,
                                    "attempt": attempt,
                                    "intent": intent,
                                    "error": err_msg,
                                }
                            )
                            retry_feedback = (
                                f"Previous intent rejected: {err_msg} "
                                "Choose an available action from legal_actions.available_monster_actions."
                            )
                            continue
                        if _is_recharge_action(m_act_obj):
                            recharging_action_name = canon_name

                res = step(state, intent, rules)
                if res.get("status") == "ok":
                    if recharging_action_name is not None:
                        cooldowns.setdefault(active_id, {})[recharging_action_name] = True

                    talk_raw = getattr(decision, "table_talk", None)
                    talk_clean = talk_raw.strip() if isinstance(talk_raw, str) and talk_raw.strip() else None
                    if talk_clean is not None:
                        talk_clean = scrub_secrets(talk_clean) or None
                    if talk_clean is not None:
                        blackboard.setdefault(side, []).append(
                            {
                                "round": cur_round,
                                "actor_id": active_id,
                                "actor_name": cur_actor["name"],
                                "message": talk_clean,
                            }
                        )

                    usage_raw = getattr(decision, "usage", None)
                    usage_dict = copy.deepcopy(usage_raw) if isinstance(usage_raw, dict) else {}

                    action_seq += 1
                    actions.append(
                        {
                            "seq": action_seq,
                            "round": cur_round,
                            "turn_index": turn_counter,
                            "action_index": action_index,
                            "actor_id": active_id,
                            "actor_name": cur_actor["name"],
                            "side": side,
                            "driver": driver_name,
                            "attempts": attempt,
                            "first_attempt_legal": attempt == 1,
                            "auto_skipped": False,
                            "auto_end_turn": False,
                            "forced_fallback": False,
                            "intent": intent,
                            "rationale": scrub_secrets(str(getattr(decision, "rationale", "") or "")),
                            "table_talk": talk_clean,
                            "events": res.get("events") or [],
                            "state_diff": res.get("state_diff") or {},
                            "narrative_hooks": res.get("narrative_hooks") or [],
                            "usage": usage_dict,
                        }
                    )
                    recent_hooks.extend(res.get("narrative_hooks") or [])
                    state = res["state"]
                    accepted = True
                    if itype == "end_turn":
                        turn_ended = True
                    break

                err_msg = scrub_secrets(
                    _driver_visible_error(str(res.get("error") or "Invalid intent."), active_id)
                )
                violations.append(
                    {
                        "round": cur_round,
                        "turn_index": turn_counter,
                        "actor_id": active_id,
                        "side": side,
                        "action_index": action_index,
                        "attempt": attempt,
                        "intent": intent,
                        "error": err_msg,
                    }
                )
                retry_feedback = scrub_secrets(
                    f"Previous intent {json.dumps(intent, sort_keys=True)} was rejected: {err_msg}. "
                    "Choose a legal action from legal_actions."
                )

            if not accepted:
                fb_intent = {"type": "end_turn", "actor_id": active_id}
                fb_res = step(state, fb_intent, rules)
                action_seq += 1
                actions.append(
                    {
                        "seq": action_seq,
                        "round": cur_round,
                        "turn_index": turn_counter,
                        "action_index": action_index,
                        "actor_id": active_id,
                        "actor_name": state["combatants"][active_id]["name"],
                        "side": side,
                        "driver": driver_name,
                        "attempts": max(0, int(max_retries_per_action)) + 1,
                        "first_attempt_legal": False,
                        "auto_skipped": False,
                        "auto_end_turn": False,
                        "forced_fallback": True,
                        "intent": fb_intent,
                        "rationale": "Forced end_turn fallback after exhausting retries.",
                        "table_talk": None,
                        "events": fb_res.get("events") or [],
                        "state_diff": fb_res.get("state_diff") or {},
                        "narrative_hooks": fb_res.get("narrative_hooks") or [],
                        "usage": {},
                    }
                )
                recent_hooks.extend(fb_res.get("narrative_hooks") or [])
                state = fb_res["state"]
                turn_ended = True
                break

            if turn_ended or _is_match_over(state):
                break

        # 4. Auto end_turn if action slots exhausted while turn still active.
        if not turn_ended and not _is_match_over(state):
            now_active = state["turn_order"][int(state["active_index"]) % len(state["turn_order"])]
            if now_active == active_id and int(state["round"]) == cur_round:
                end_intent = {"type": "end_turn", "actor_id": active_id}
                end_res = step(state, end_intent, rules)
                action_seq += 1
                actions.append(
                    {
                        "seq": action_seq,
                        "round": cur_round,
                        "turn_index": turn_counter,
                        "action_index": int(max_actions_per_turn),
                        "actor_id": active_id,
                        "actor_name": state["combatants"][active_id]["name"],
                        "side": side,
                        "driver": driver_name,
                        "attempts": 0,
                        "first_attempt_legal": True,
                        "auto_skipped": False,
                        "auto_end_turn": True,
                        "forced_fallback": False,
                        "intent": end_intent,
                        "rationale": "Automatic end_turn after reaching max_actions_per_turn.",
                        "table_talk": None,
                        "events": end_res.get("events") or [],
                        "state_diff": end_res.get("state_diff") or {},
                        "narrative_hooks": end_res.get("narrative_hooks") or [],
                        "usage": {},
                    }
                )
                recent_hooks.extend(end_res.get("narrative_hooks") or [])
                state = end_res["state"]

        turn_counter += 1

    winner = _resolve_winner(state)
    rounds_played = max((int(a["round"]) for a in actions), default=min(int(state["round"]), effective_max_rounds))

    transcript: dict[str, Any] = {
        "schema_version": "4",
        "dataset_id": rules.dataset_id,
        "scenario": {
            "id": compiled["id"],
            "name": compiled["name"],
            "tier": compiled["tier"],
            "party_level": compiled["party_level"],
            "max_rounds": compiled["max_rounds"],
            "description": compiled["description"],
            "party_ids": list(compiled["party_ids"]),
            "enemy_ids": list(compiled["enemy_ids"]),
        },
        "config": {
            "seed": int(seed),
            "max_rounds": effective_max_rounds,
            "max_actions_per_turn": int(max_actions_per_turn),
            "max_retries_per_action": int(max_retries_per_action),
            "party_driver": _describe_driver_config(party_driver),
            "enemy_driver": _describe_driver_config(enemy_driver),
        },
        "winner": winner,
        "rounds": rounds_played,
        "initial_state": initial_state,
        "final_state": copy.deepcopy(state),
        "actions": actions,
        "violations": violations,
        "recharge_events": recharge_events,
        "blackboard": blackboard,
    }

    transcript["scorecard"] = score_match(transcript, rules, side="party")
    transcript["digest"] = compute_transcript_digest(transcript)
    return transcript


def _combatant_to_start_entry(comb: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a `start_encounter` combatant entry from an initial EncounterState combatant."""
    if comb.get("kind") == "monster":
        return {
            "id": comb["id"],
            "name": comb["name"],
            "side": comb["side"],
            "monster": strip_prefix(str(comb.get("source_id") or "")),
            "position": copy.deepcopy(comb["position"]),
            "hp": int(comb["hp"]),
            "hp_max": int(comb["hp_max"]),
            "temp_hp": int(comb.get("temp_hp", 0)),
            "exhaustion_level": int(comb.get("exhaustion_level", 0)),
            "conditions": copy.deepcopy(comb.get("conditions", [])),
            "heroic_inspiration": bool(comb.get("heroic_inspiration", False)),
        }

    ac_breakdown = "Armored" if comb.get("wearing_armor") else "Unarmored"
    statblock = {
        "name": comb["name"],
        "class": comb.get("source_id", "classes:fighter"),
        "level": int(comb.get("level", 1)),
        "size": comb.get("size", "Medium"),
        "hp_max": int(comb["hp_max"]),
        "ac": int(comb["ac"]),
        "ac_breakdown": ac_breakdown,
        "speed_ft": int(comb.get("speed_ft", 30)),
        "initiative": int(comb.get("initiative_bonus", comb["ability_mods"]["dex"])),
        "abilities": copy.deepcopy(comb["abilities"]),
        "ability_mods": copy.deepcopy(comb["ability_mods"]),
        "proficiency_bonus": int(comb.get("proficiency_bonus", 2)),
        "saving_throws": copy.deepcopy(comb["saves"]),
        "damage_resistances": list(comb.get("damage_resistances", [])),
        "damage_immunities": list(comb.get("damage_immunities", [])),
        "damage_vulnerabilities": list(comb.get("damage_vulnerabilities", [])),
        "condition_immunities": list(comb.get("condition_immunities", [])),
        "extra_attacks": int(comb.get("extra_attacks", 1)),
        "feats": list(comb.get("feats", [])),
        "weapon_profiles": copy.deepcopy(comb.get("weapons", [])),
        "spellcasting": copy.deepcopy(comb.get("spellcasting")),
    }
    return {
        "id": comb["id"],
        "name": comb["name"],
        "side": comb["side"],
        "statblock": statblock,
        "position": copy.deepcopy(comb["position"]),
        "hp": int(comb["hp"]),
        "temp_hp": int(comb.get("temp_hp", 0)),
        "exhaustion_level": int(comb.get("exhaustion_level", 0)),
        "conditions": copy.deepcopy(comb.get("conditions", [])),
        "heroic_inspiration": bool(comb.get("heroic_inspiration", False)),
    }


def _builtin_lineup_matches(scenario_id: str, transcript: dict[str, Any]) -> bool:
    """True while the built-in scenario still has the line-up (ids and start positions) the transcript recorded.

    Built-in scenarios get retuned; a trace recorded before that replays from its own initial_state instead.
    """
    from .scenarios import _BUILTIN_SCENARIOS

    raw = _BUILTIN_SCENARIOS.get(scenario_id) or {}
    def xy(pos: Any) -> tuple[Any, Any]:
        return ((pos or {}).get("x"), (pos or {}).get("y"))

    builtin = {e["id"]: xy(e.get("position")) for e in list(raw.get("party") or []) + list(raw.get("enemies") or [])}
    recorded = {cid: xy(c.get("position")) for cid, c in ((transcript.get("initial_state") or {}).get("combatants") or {}).items()}
    return builtin == recorded


def _reconstruct_scenario_from_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    sc_meta = transcript.get("scenario") or {}
    init_state = transcript.get("initial_state") or {}
    init_combatants = init_state.get("combatants") or {}
    if not init_combatants:
        raise ValueError("Transcript is missing 'initial_state.combatants' and cannot be replayed.")

    party_ids = list(sc_meta.get("party_ids") or [])
    enemy_ids = list(sc_meta.get("enemy_ids") or [])
    if not party_ids or not enemy_ids:
        party_ids = [cid for cid, c in init_combatants.items() if str(c.get("side")) == "party"]
        enemy_ids = [cid for cid, c in init_combatants.items() if str(c.get("side")) != "party"]

    ordered_ids = [cid for cid in (party_ids + enemy_ids) if cid in init_combatants]
    for cid in init_combatants:
        if cid not in ordered_ids:
            ordered_ids.append(cid)

    combatants = [_combatant_to_start_entry(init_combatants[cid]) for cid in ordered_ids]
    return {
        "id": str(sc_meta.get("id") or init_state.get("encounter_id") or "replayed-scenario"),
        "name": str(sc_meta.get("name") or sc_meta.get("id") or "Replayed Scenario"),
        "description": str(sc_meta.get("description") or ""),
        "tier": int(sc_meta.get("tier") or 1),
        "party_level": int(sc_meta.get("party_level") or 1),
        "max_rounds": int(sc_meta.get("max_rounds") or (transcript.get("config") or {}).get("max_rounds") or 10),
        "tactical_themes": list(sc_meta.get("tactical_themes") or []),
        "combatants": combatants,
        "party_ids": party_ids,
        "enemy_ids": enemy_ids,
        "source": "inline",
    }


def replay_match(
    transcript: dict[str, Any],
    rules: RulesStore,
    *,
    scenario_override: str | Path | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay a recorded MatchTranscript through `ReplayDriver` + `srd2024.engine.combat.step()`."""
    schema = str(transcript.get("schema_version") or "")
    if schema == "3":
        message = (
            "unsupported schema_version '3'. v0.3 traces cannot be replayed; expected schema_version 4."
        )
        return {
            "status": "error",
            "error": message,
            "scenario_id": str((transcript.get("scenario") or {}).get("id") or ""),
            "seed": int((transcript.get("config") or {}).get("seed", 0) or 0),
            "deterministic_match": False,
            "original_digest": str(transcript.get("digest") or ""),
            "replayed_digest": "",
            "diffs": [message],
            "replayed_transcript": None,
        }
    if schema not in ("4", ""):
        message = f"unsupported schema_version {schema!r}. Expected schema_version 4."
        return {
            "status": "error",
            "error": message,
            "scenario_id": str((transcript.get("scenario") or {}).get("id") or ""),
            "seed": int((transcript.get("config") or {}).get("seed", 0) or 0),
            "deterministic_match": False,
            "original_digest": str(transcript.get("digest") or ""),
            "replayed_digest": "",
            "diffs": [message],
            "replayed_transcript": None,
        }
    sc_meta = transcript.get("scenario") or {}
    cfg = transcript.get("config") or {}
    scenario_id = str(sc_meta.get("id") or (transcript.get("initial_state") or {}).get("encounter_id") or "")
    seed = int(cfg.get("seed", 42))
    max_rounds = int(cfg.get("max_rounds") or sc_meta.get("max_rounds") or 10)
    max_actions = int(cfg.get("max_actions_per_turn", 6))
    max_retries = int(cfg.get("max_retries_per_action", 2))
    peak_attempt = 1
    for entry in list(transcript.get("violations") or []) + list(transcript.get("actions") or []):
        if not isinstance(entry, dict):
            continue
        peak_attempt = max(peak_attempt, int(entry.get("attempt") or entry.get("attempts") or 1))
    max_retries = max(max_retries, peak_attempt - 1)

    if scenario_override is not None:
        scenario_input: str | Path | dict[str, Any] = scenario_override
    elif scenario_id in BUILTIN_SCENARIO_IDS and _builtin_lineup_matches(scenario_id, transcript):
        scenario_input = scenario_id
    else:
        scenario_input = _reconstruct_scenario_from_transcript(transcript)

    party_replay = ReplayDriver(transcript, side="party")
    enemy_replay = ReplayDriver(transcript, side="enemy")

    replayed = run_match(
        scenario_input,
        party_driver=party_replay,
        enemy_driver=enemy_replay,
        rules=rules,
        seed=seed,
        max_rounds=max_rounds,
        max_actions_per_turn=max_actions,
        max_retries_per_action=max_retries,
    )

    original_digest = str(transcript.get("digest") or compute_transcript_digest(transcript))
    replayed_digest = str(replayed["digest"])

    diffs: list[str] = []
    if replayed.get("winner") != transcript.get("winner"):
        diffs.append(f"winner mismatch: original={transcript.get('winner')!r}, replayed={replayed.get('winner')!r}")
    if replayed.get("final_state") != transcript.get("final_state"):
        diffs.append("final_state differs between original and replayed transcripts.")

    orig_actions = transcript.get("actions") or []
    rep_actions = replayed.get("actions") or []
    if len(orig_actions) != len(rep_actions):
        diffs.append(f"action count mismatch: original={len(orig_actions)}, replayed={len(rep_actions)}")
    else:
        for idx, (oa, ra) in enumerate(zip(orig_actions, rep_actions)):
            if oa.get("events") != ra.get("events"):
                diffs.append(f"action[{idx}] (seq={oa.get('seq')}) events differ.")
                break

    if replayed.get("scorecard") != transcript.get("scorecard"):
        diffs.append("scorecard differs between original and replayed transcripts.")

    if original_digest != replayed_digest and not diffs:
        diffs.append(f"digest mismatch: original={original_digest}, replayed={replayed_digest}")

    return {
        "status": "ok",
        "scenario_id": scenario_id,
        "seed": seed,
        "deterministic_match": (original_digest == replayed_digest) and (len(diffs) == 0),
        "original_digest": original_digest,
        "replayed_digest": replayed_digest,
        "diffs": diffs,
        "replayed_transcript": replayed,
    }


def _contender_label_and_driver(spec: str | dict[str, Any]) -> tuple[str, str]:
    if isinstance(spec, str):
        s = spec.strip()
        return s, s
    if isinstance(spec, dict):
        driver_str = str(spec.get("driver") or spec.get("spec") or spec.get("type") or "").strip()
        if not driver_str and spec.get("model"):
            driver_str = str(spec["model"])
        label = str(spec.get("label") or spec.get("name") or driver_str or "contender").strip()
        return label, driver_str or label
    raise ValueError(f"Unsupported contender spec type: {type(spec).__name__}.")


def run_benchmark(
    *,
    scenarios: list[str | Path] | None = None,
    contenders: list[str | dict[str, Any]] | None = None,
    enemy_agent: str | dict[str, Any] = "heuristic-tactical",
    seeds: list[int] | None = None,
    rules: RulesStore,
    max_rounds: int | None = None,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
    include_traces: bool = False,
    on_match: Callable[[dict[str, Any], int, int], None] | None = None,
    budget: Any = None,
    on_abort: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run all (contender x scenario x seed) matches and return a deterministic BenchmarkReport.

    `on_match(summary, done, total)` is called after each match finishes, with that match's summary row (the same
    dict that lands in `report["matches"]`). The CLI uses it for progress lines and incremental JSONL output, so
    a long LLM bench shows where it is and keeps finished matches if it's interrupted. It doesn't affect results.

    A contender stops cleanly, keeping its finished matches and listed under `aborted_contenders`, when:
    - its provider reports no credits or quota, or keeps failing after retries (`ProviderUnavailable`);
    - `budget` (a `BudgetLedger`) is set and the contender is paid, and the ledger can't reserve the next match's
      limit under the cap (`budget_cap`), or a match would go over its own limit (`match_budget`), or the ledger is
      missing or corrupt (`ledger_error`, which also stops every later paid contender).
    An unfinished match is dropped, not scored. Every paid match reserves its limit before it starts and settles to
    its metered cost when it ends, finished or aborted (see budget.py). Other contenders carry on. With `budget`,
    each match summary gets `cost_usd`.
    """
    scenario_list = list(BUILTIN_SCENARIO_IDS) if scenarios is None else list(scenarios)
    contender_list = (
        ["heuristic-tactical", "greedy", "random"] if contenders is None else list(contenders)
    )
    seed_list = [1, 2, 3] if seeds is None else [int(s) for s in seeds]

    compiled_scenarios = [load_scenario(sc, rules) for sc in scenario_list]
    scenario_ids = [sc["id"] for sc in compiled_scenarios]
    enemy_label, _ = _contender_label_and_driver(enemy_agent)

    all_match_summaries: list[dict[str, Any]] = []
    leaderboard_rows: list[dict[str, Any]] = []
    total_matches = len(contender_list) * len(compiled_scenarios) * len(seed_list)
    aborted_contenders: list[dict[str, Any]] = []
    ledger_broken: str | None = None
    prices: dict[str, tuple[float, float]] = {}
    limits: dict[str, float] = {}
    if budget is not None:
        for contender in contender_list:
            spec = contender if isinstance(contender, str) else str(_contender_label_and_driver(contender)[1])
            price = budget.price_for(spec)
            if price is None:
                raise ValueError(
                    f"No price for paid contender {spec!r}; pass --price MODEL=IN/OUT (USD per million tokens)."
                )
            c_label = _contender_label_and_driver(contender)[0]
            prices[c_label] = price
            if price[0] > 0 or price[1] > 0:
                limit = budget.limit_for(spec)
                if limit is None:
                    raise ValueError(f"No per-match limit for paid contender {spec!r}; pass --budget-match-usd.")
                limits[c_label] = limit

    for contender in contender_list:
        label, driver_str = _contender_label_and_driver(contender)
        c_matches = 0
        c_wins = 0
        c_losses = 0
        c_draws = 0
        sum_comp = 0.0
        sum_out = 0.0
        sum_leg = 0.0
        sum_tac = 0.0
        sum_coord = 0.0
        sum_rounds = 0.0
        total_viols = 0
        total_wasted_immune = 0
        by_scenario: dict[str, dict[str, Any]] = {}
        stop: tuple[str, str] | None = None
        price = prices.get(label, (0.0, 0.0))
        paid = budget is not None and (price[0] > 0 or price[1] > 0)

        for compiled_sc in compiled_scenarios:
            if stop is not None:
                break
            sc_id = compiled_sc["id"]
            sc_wins = 0
            sc_matches = 0
            sc_sum_comp = 0.0
            sc_wasted_immune = 0

            for seed in seed_list:
                meta = {"contender": label, "scenario_id": sc_id, "seed": int(seed)}
                rid: str | None = None
                meter: MatchMeter | None = None
                if paid:
                    if ledger_broken is not None:
                        stop = ("ledger_error", ledger_broken)
                        break
                    try:
                        rid = budget.reserve(meta, limits[label])
                    except LedgerError as exc:
                        ledger_broken = str(exc)
                        stop = ("ledger_error", ledger_broken)
                        break
                    if rid is None:
                        stop = ("budget_cap", f"${budget.committed():.2f} of the ${budget.cap_usd:.2f} cap is spent or "
                                              f"reserved; the next match needs ${limits[label]:.2f}")
                        break
                    meter = MatchMeter(price, limits[label])
                p_drv = create_driver(contender, rules=rules, seed=seed)
                if meter is not None:
                    p_drv.meter = meter
                e_drv = create_driver(enemy_agent, rules=rules, seed=seed + 10000)
                outcome = "finished"
                try:
                    transcript = run_match(
                        compiled_sc,
                        party_driver=p_drv,
                        enemy_driver=e_drv,
                        rules=rules,
                        seed=seed,
                        max_rounds=max_rounds,
                        max_actions_per_turn=max_actions_per_turn,
                        max_retries_per_action=max_retries_per_action,
                    )
                except ProviderUnavailable as exc:
                    outcome = f"aborted:{exc.reason}"
                    stop = (exc.reason, scrub_secrets(str(exc)))
                except MatchBudgetExceeded as exc:
                    outcome = f"aborted:{exc.reason}"
                    stop = (exc.reason, str(exc))
                finally:
                    if rid is not None and meter is not None:
                        try:
                            budget.settle(rid, meter.spent_usd, {**meta, "outcome": outcome, "calls": meter.calls,
                                                                 "worst_case_charges": meter.worst_case_charges,
                                                                 "clamped_charges": meter.clamped_charges})
                        except LedgerError as exc:
                            ledger_broken = str(exc)
                            stop = stop or ("ledger_error", ledger_broken)
                if stop is not None:
                    break
                sc_card = transcript["scorecard"]
                metrics = sc_card["metrics"]
                w = transcript["winner"]

                c_matches += 1
                sc_matches += 1
                if w == "party":
                    c_wins += 1
                    sc_wins += 1
                elif w == "enemy":
                    c_losses += 1
                else:
                    c_draws += 1

                comp = float(sc_card["composite_score"])
                out_s = float(sc_card["outcome_score"])
                leg_s = float(sc_card["rules_legality_score"])
                tac_s = float(sc_card["tactical_score"])
                coo_s = float(sc_card["coordination_score"])
                viols = int(metrics["total_violations"])
                wasted = int(metrics["wasted_immune_hits"])

                sum_comp += comp
                sum_out += out_s
                sum_leg += leg_s
                sum_tac += tac_s
                sum_coord += coo_s
                sum_rounds += float(transcript["rounds"])
                total_viols += viols
                total_wasted_immune += wasted

                sc_sum_comp += comp
                sc_wasted_immune += wasted

                match_summary: dict[str, Any] = {
                    "contender": label,
                    "scenario_id": sc_id,
                    "seed": int(seed),
                    "winner": w,
                    "rounds": int(transcript["rounds"]),
                    "composite_score": comp,
                    "outcome_score": out_s,
                    "rules_legality_score": leg_s,
                    "tactical_score": tac_s,
                    "coordination_score": coo_s,
                    "violations": viols,
                    "wasted_immune_hits": wasted,
                    "digest": transcript["digest"],
                }
                if budget is not None:
                    match_summary["cost_usd"] = (
                        round(min(meter.spent_usd, limits[label]), 6) if meter is not None else 0.0
                    )
                if include_traces:
                    match_summary["transcript"] = transcript
                all_match_summaries.append(match_summary)
                if on_match is not None:
                    on_match(match_summary, len(all_match_summaries), total_matches)

            if sc_matches == 0:
                continue
            by_scenario[sc_id] = {
                "wins": sc_wins,
                "matches": sc_matches,
                "win_rate": round(sc_wins / max(1, sc_matches), 4),
                "mean_composite_score": round(sc_sum_comp / max(1, sc_matches), 2),
                "wasted_immune_hits": sc_wasted_immune,
            }

        denom = max(1, c_matches)
        leaderboard_rows.append(
            {
                "rank": 0,
                "label": label,
                "driver": driver_str,
                "matches": c_matches,
                "wins": c_wins,
                "losses": c_losses,
                "draws": c_draws,
                "win_rate": round(c_wins / denom, 4),
                "mean_composite_score": round(sum_comp / denom, 2),
                "mean_outcome_score": round(sum_out / denom, 2),
                "mean_rules_legality_score": round(sum_leg / denom, 2),
                "mean_tactical_score": round(sum_tac / denom, 2),
                "mean_coordination_score": round(sum_coord / denom, 2),
                "mean_rounds": round(sum_rounds / denom, 2),
                "total_violations": total_viols,
                "wasted_immune_hits": total_wasted_immune,
                "by_scenario": by_scenario,
            }
        )
        if stop is not None:
            leaderboard_rows[-1]["aborted"] = stop[0]
            abort_row = {"label": label, "driver": driver_str, "reason": stop[0], "message": stop[1],
                         "matches_completed": c_matches,
                         "matches_planned": len(compiled_scenarios) * len(seed_list)}
            aborted_contenders.append(abort_row)
            if on_abort is not None:
                on_abort(abort_row)

    leaderboard_rows.sort(
        key=lambda r: (
            -r["mean_composite_score"],
            -r["win_rate"],
            -r["mean_rules_legality_score"],
            r["label"],
        )
    )
    for rank_idx, row in enumerate(leaderboard_rows, start=1):
        row["rank"] = rank_idx

    report: dict[str, Any] = {
        "schema_version": "4",
        "status": "ok",
        "scenarios": scenario_ids,
        "seeds": seed_list,
        "enemy_agent": enemy_label,
        "leaderboard": leaderboard_rows,
        "matches": all_match_summaries,
    }
    if aborted_contenders:
        report["aborted_contenders"] = aborted_contenders
    if budget is not None:
        try:
            spent, committed = budget.spent(), budget.committed()
        except LedgerError as exc:
            spent = committed = float("nan")
            ledger_broken = ledger_broken or str(exc)
        report["budget"] = {"cap_usd": budget.cap_usd, "match_limit_usd": dict(limits),
                            "spent_usd_ledger_total": spent, "committed_usd_ledger_total": committed}
        if ledger_broken is not None:
            report["budget"]["ledger_error"] = ledger_broken
    return report
