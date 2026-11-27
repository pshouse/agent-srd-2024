"""Deterministic quantitative match scorer for Stage 3 5e 2024 (SRD 5.2) Arena.

Implements STAGE3-SPEC §6.1:
Computes reproducible 0–100 scores across four weighted dimensions:
- `outcome_score` (0.35)
- `rules_legality_score` (0.25)
- `tactical_score` (0.25)
- `coordination_score` (0.15)
"""

from __future__ import annotations

from typing import Any

from srd2024.engine.store import RulesStore, strip_prefix

SCORE_WEIGHTS: dict[str, float] = {
    "outcome": 0.35,
    "rules_legality": 0.25,
    "tactical": 0.25,
    "coordination": 0.15,
}

_PRODUCTIVE_INTENT_TYPES: frozenset[str] = frozenset({"attack", "cast_spell", "help"})

# Ratio terms (legality, immunity, friendly fire, focus fire) can be perfect
# from a tiny sample: one attack followed by idle turns used to collect almost
# all of them (#22). Each is now paid in proportion to `contribution`, the share
# of the side's active turns that attacked, cast, or helped. A fully idle side
# keeps a small legality floor so it still ranks above pure violations.
_LEGALITY_FLOOR = 0.05

_SETUP_MASTERIES: frozenset[str] = frozenset({"sap", "topple", "vex", "slow"})

_KNOWN_BUFF_SPELLS: frozenset[str] = frozenset(
    {
        "bless",
        "shield",
        "mage-armor",
        "shield-of-faith",
        "misty-step",
        "aid",
        "barkskin",
        "haste",
        "longstrider",
        "false-life",
        "spirit-guardians",
        "hold-person",
    }
)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _extract_intent_targets(intent: dict[str, Any]) -> list[str]:
    raw_ids = intent.get("target_ids")
    if isinstance(raw_ids, list) and raw_ids:
        return [str(t) for t in raw_ids if t is not None]
    tid = intent.get("target_id")
    if tid:
        return [str(tid)]
    return []


def _is_offensive_action(
    action_entry: dict[str, Any],
    opp_ids: set[str],
    rules: RulesStore | None,
) -> list[str]:
    """Return targeted opponent IDs if this action is an offensive attack or spell."""
    intent = action_entry.get("intent") or {}
    itype = str(intent.get("type") or "").strip().lower()
    if itype == "attack":
        return [t for t in _extract_intent_targets(intent) if t in opp_ids]
    if itype == "cast_spell":
        opp_targets = [t for t in _extract_intent_targets(intent) if t in opp_ids]
        if not opp_targets:
            return []
        spell_slug = strip_prefix(str(intent.get("spell") or ""))
        if rules is not None and spell_slug:
            sp = rules.get_spell(spell_slug)
            if sp is not None:
                res = str(sp.get("resolution") or "").lower()
                if sp.get("damage") or res in ("attack", "save", "auto_hit"):
                    return opp_targets
                return []
        # Fallback when rules is None or spell unknown: check events or assume harmful if targeting enemies.
        events = action_entry.get("events") or []
        if any(
            ev.get("type") in ("damage_applied", "spell_attack_roll", "saving_throw")
            for ev in events
        ):
            return opp_targets
        if spell_slug not in _KNOWN_BUFF_SPELLS:
            return opp_targets
    return []


def _successful_help_turns(side_actions: list[dict[str, Any]]) -> set[tuple[int, int, str]]:
    """Return turn keys of `help` actions whose Advantage was spent on an ally attack that hit.

    A Help action only contributes if the helped attack happens and lands (CTO M1 on #22).
    Help that expires unused, or that is spent on a miss, earns nothing. Each hit
    redeems at most one pending Help on that target (oldest first). A helper's pending
    Help expires when that helper starts their next turn.
    """
    pending: dict[str, list[tuple[tuple[int, int, str], str]]] = {}
    credited: set[tuple[int, int, str]] = set()
    for a in side_actions:
        if a.get("auto_skipped", False):
            continue
        actor_id = str(a.get("actor_id") or "")
        turn_key = (int(a.get("round", 0)), int(a.get("turn_index", 0)), actor_id)
        # Expire this actor's Help from earlier turns (engine: start_of_source_next_turn).
        for tgt in list(pending):
            pending[tgt] = [(k, h) for (k, h) in pending[tgt] if h != actor_id or k == turn_key]
        for ev in a.get("events") or []:
            etype = ev.get("type")
            if etype == "action_help" and ev.get("target_id"):
                pending.setdefault(str(ev["target_id"]), []).append((turn_key, actor_id))
            elif etype in ("attack_roll", "spell_attack_roll") and "help" in (ev.get("advantage_sources") or []):
                tgt = str(ev.get("target_id") or "")
                queue = pending.get(tgt) or []
                if queue:
                    help_key, _helper = queue.pop(0)
                    if ev.get("hit"):
                        credited.add(help_key)
    return credited


def score_match(
    transcript: dict[str, Any],
    rules: RulesStore | None = None,
    *,
    side: str = "party",
) -> dict[str, Any]:
    """Compute purely mechanical, deterministic 0–100 eval scores from a MatchTranscript."""
    winner = str(transcript.get("winner") or "draw")
    rounds = int(transcript.get("rounds") or 0)
    config = transcript.get("config") or {}
    scenario_meta = transcript.get("scenario") or {}
    max_rounds = int(
        config.get("max_rounds")
        or scenario_meta.get("max_rounds")
        or max(1, rounds)
    )

    init_combatants: dict[str, dict[str, Any]] = (
        (transcript.get("initial_state") or {}).get("combatants") or {}
    )
    final_combatants: dict[str, dict[str, Any]] = (
        (transcript.get("final_state") or {}).get("combatants") or {}
    )

    base_combatants = init_combatants if init_combatants else final_combatants
    side_ids = {cid for cid, c in base_combatants.items() if str(c.get("side")) == side}
    opp_ids = {cid for cid, c in base_combatants.items() if str(c.get("side")) != side}

    # Include any combatants present only in final_state.
    for cid, c in final_combatants.items():
        if str(c.get("side")) == side:
            side_ids.add(cid)
        else:
            opp_ids.add(cid)

    actions: list[dict[str, Any]] = list(transcript.get("actions") or [])
    violations: list[dict[str, Any]] = list(transcript.get("violations") or [])
    blackboard_side: list[dict[str, Any]] = list(
        (transcript.get("blackboard") or {}).get(side) or []
    )

    # ------------------------------------------------------------------
    # 1. Outcome Score (0..100, weight 0.35)
    # ------------------------------------------------------------------
    if winner == side:
        win_pts = 50.0
    elif winner == "draw":
        win_pts = 20.0
    else:
        win_pts = 0.0

    side_final = [final_combatants[cid] for cid in sorted(side_ids) if cid in final_combatants]
    side_init = [init_combatants[cid] for cid in sorted(side_ids) if cid in init_combatants]
    opp_init = [init_combatants[cid] for cid in sorted(opp_ids) if cid in init_combatants]
    if not side_init:
        side_init = side_final
    if not opp_init:
        opp_init = [final_combatants[cid] for cid in sorted(opp_ids) if cid in final_combatants]

    total_allies = len(side_ids)
    survival_sum = 0.0
    survivors = 0
    total_final_hp = 0
    for c in side_final:
        alive = bool(c.get("alive", False))
        hp = max(0, int(c.get("hp", 0)))
        total_final_hp += hp
        if alive:
            survivors += 1
            # A downed-but-alive ally counts half only on a win (they get stabilized).
            # On a loss or draw it counts nothing, so losing without fighting earns no survival credit (#22).
            if hp > 0:
                survival_sum += 1.0
            elif winner == side:
                survival_sum += 0.5

    survival_pts = 25.0 * (survival_sum / max(1, total_allies))

    total_init_hp_max = sum(
        max(1, int(c.get("hp_max", c.get("hp", 1)))) for c in side_init
    )
    hp_ratio_raw = total_final_hp / max(1, total_init_hp_max)
    hp_ratio = round(_clamp(hp_ratio_raw, 0.0, 1.0), 4)
    hp_pts = 15.0 * _clamp(hp_ratio_raw, 0.0, 1.0)

    if winner == side:
        rounds_pts = 10.0 * max(0.0, 1.0 - (max(1, rounds) - 1) / max(1, max_rounds))
    else:
        rounds_pts = 0.0

    outcome_score = round(_clamp(win_pts + survival_pts + hp_pts + rounds_pts), 2)

    # ------------------------------------------------------------------
    # 2. Rules Legality Score (0..100, weight 0.25)
    # ------------------------------------------------------------------
    side_actions = [a for a in actions if str(a.get("side")) == side]
    decision_actions = [
        a
        for a in side_actions
        if not a.get("auto_skipped", False) and not a.get("auto_end_turn", False)
    ]
    total_decisions = len(decision_actions)
    first_attempt_legal = sum(
        1
        for a in decision_actions
        if bool(a.get("first_attempt_legal", not a.get("forced_fallback", False)))
        and not bool(a.get("forced_fallback", False))
    )
    forced_fallbacks = sum(1 for a in decision_actions if bool(a.get("forced_fallback", False)))
    side_violations = sum(1 for v in violations if str(v.get("side")) == side)

    # Turn productivity: a turn is productive if any action in it attacked or cast,
    # or used Help that set up an ally's hit.
    successful_help_turns = _successful_help_turns(side_actions)
    turn_productivity: dict[tuple[int, int, str], bool] = {}
    turn_talk: dict[tuple[int, int, str], int] = {}
    for a in side_actions:
        if a.get("auto_skipped", False):
            continue
        turn_key = (int(a.get("round", 0)), int(a.get("turn_index", 0)), str(a.get("actor_id") or ""))
        a_type = str((a.get("intent") or {}).get("type") or "").strip().lower()
        is_prod = a_type in _PRODUCTIVE_INTENT_TYPES and (a_type != "help" or turn_key in successful_help_turns)
        turn_productivity[turn_key] = turn_productivity.get(turn_key, False) or is_prod
        talk = a.get("table_talk")
        if isinstance(talk, str) and talk.strip():
            turn_talk[turn_key] = turn_talk.get(turn_key, 0) + 1
    active_turns = len(turn_productivity)
    productive_turns = sum(1 for prod in turn_productivity.values() if prod)
    contribution = productive_turns / active_turns if active_turns else 0.0

    if total_decisions == 0:
        rules_legality_score = 100.0
    else:
        first_attempt_rate = first_attempt_legal / total_decisions
        fallback_penalty = 15.0 * (forced_fallbacks / total_decisions)
        extra_retry_penalty = min(
            20.0,
            2.0 * max(0, side_violations - (total_decisions - first_attempt_legal)),
        )
        raw_legality = _clamp(100.0 * first_attempt_rate - fallback_penalty - extra_retry_penalty)
        rules_legality_score = round(raw_legality * max(_LEGALITY_FLOOR, contribution), 2)

    # ------------------------------------------------------------------
    # 3 & 4. Event Telemetry, Tactical & Coordination Metrics
    # ------------------------------------------------------------------
    effective_damage_dealt = 0
    wasted_immune_hits = 0
    wasted_immune_raw_damage = 0
    mastery_triggers = 0
    mastery_setup_triggers = 0
    effective_buff_casts = 0
    ally_revivals = 0
    ally_heals = 0
    ally_buff_targets = 0
    help_actions = 0
    friendly_fire_incidents = 0
    friendly_fire_damage = 0

    usage_prompt = 0
    usage_completion = 0
    usage_total = 0

    # Dynamic opponent HP tracking for focus-fire coordination.
    opp_hp: dict[str, int] = {}
    opp_hp_max: dict[str, int] = {}
    opp_alive: dict[str, bool] = {}
    opp_threat: dict[str, tuple[int, int]] = {}
    for cid in opp_ids:
        c = init_combatants.get(cid) or final_combatants.get(cid) or {}
        hp_max_v = max(1, int(c.get("hp_max", c.get("hp", 1))))
        hp_v = max(0, int(c.get("hp", hp_max_v)))
        opp_hp[cid] = hp_v
        opp_hp_max[cid] = hp_max_v
        opp_alive[cid] = bool(c.get("alive", hp_v > 0))
        opp_threat[cid] = (int(c.get("level", 1)), hp_max_v)

    offensive_multi_enemy_actions = 0
    focused_actions = 0

    for action in actions:
        a_side = str(action.get("side") or "")
        actor_id = str(action.get("actor_id") or "")
        intent = action.get("intent") or {}
        itype = str(intent.get("type") or "").strip().lower()
        events: list[dict[str, Any]] = list(action.get("events") or [])
        state_diff: dict[str, Any] = action.get("state_diff") or {}

        # Focus-fire evaluation BEFORE applying this action's damage.
        if a_side == side and not action.get("auto_skipped", False):
            opp_targets = _is_offensive_action(action, opp_ids, rules)
            if opp_targets:
                alive_opps = [
                    cid for cid in opp_ids if opp_alive.get(cid, False) and opp_hp.get(cid, 0) > 0
                ]
                if len(alive_opps) > 1:
                    offensive_multi_enemy_actions += 1
                    damaged_opps = {
                        cid for cid in alive_opps if opp_hp.get(cid, 0) < opp_hp_max.get(cid, 1)
                    }
                    if damaged_opps:
                        if any(t in damaged_opps for t in opp_targets):
                            focused_actions += 1
                    else:
                        max_threat = max(opp_threat[cid] for cid in alive_opps)
                        top_threats = {
                            cid for cid in alive_opps if opp_threat[cid] == max_threat
                        }
                        if any(t in top_threats for t in opp_targets) or len(set(opp_targets)) >= 2:
                            focused_actions += 1

        # Track side-specific telemetry.
        if a_side == side:
            usage = action.get("usage")
            if isinstance(usage, dict):
                pt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
                ct = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
                tt = int(usage.get("total_tokens", pt + ct) or 0)
                usage_prompt += pt
                usage_completion += ct
                usage_total += tt

            if itype == "help" or any(ev.get("type") == "action_help" for ev in events):
                help_actions += 1

            # Buff cast detection for cast_spell actions.
            if itype == "cast_spell":
                spell_slug = strip_prefix(str(intent.get("spell") or ""))
                effect_changes = state_diff.get("effect_changes") or []
                buffed_side_ids: set[str] = set()
                for ec in effect_changes:
                    cid = str(ec.get("id") or "")
                    if cid in side_ids and ec.get("added"):
                        buffed_side_ids.add(cid)
                has_buff_event = any(
                    ev.get("type") in ("temp_hp_applied", "teleported")
                    for ev in events
                )
                for ev in events:
                    if ev.get("type") == "temp_hp_applied":
                        tid = str(ev.get("target_id") or "")
                        if tid in side_ids:
                            buffed_side_ids.add(tid)
                # Fallback for synthetic transcripts without state_diff.
                if not buffed_side_ids and not has_buff_event and spell_slug in _KNOWN_BUFF_SPELLS:
                    has_buff_event = True
                    for tid in _extract_intent_targets(intent):
                        if tid in side_ids:
                            buffed_side_ids.add(tid)
                    if not buffed_side_ids and actor_id in side_ids:
                        buffed_side_ids.add(actor_id)
                if buffed_side_ids or has_buff_event:
                    effective_buff_casts += 1
                    ally_buff_targets += sum(1 for tid in buffed_side_ids if tid != actor_id)

        # Process events for damage, immune hits, masteries, heals, revivals, and opponent HP state.
        for ev in events:
            etype = str(ev.get("type") or "")
            # Engine-made reactions (Opportunity Attacks, Shield: #33/#38) aren't the driver's decisions: they still
            # move opponent HP and the outcome, but earn no damage-dealt or mastery credit.
            engine_reaction = bool(ev.get("reaction"))

            if etype == "damage_applied":
                src_id = str(ev.get("source_id") or actor_id)
                tgt_id = str(ev.get("target_id") or "")
                total_dmg = max(0, int(ev.get("total_damage", 0)))
                caused_by_side = (src_id in side_ids) or (a_side == side and not ev.get("source_id"))

                if caused_by_side and not engine_reaction:
                    if tgt_id in opp_ids:
                        effective_damage_dealt += total_dmg
                        breakdown = ev.get("breakdown") or []
                        immune_raw = sum(
                            max(0, int(part.get("raw", 0)))
                            for part in breakdown
                            if isinstance(part, dict)
                            and str(part.get("modifier") or "").lower() == "immune"
                            and int(part.get("raw", 0)) > 0
                        )
                        if immune_raw > 0:
                            wasted_immune_hits += 1
                            wasted_immune_raw_damage += immune_raw
                    elif tgt_id in side_ids and total_dmg > 0:
                        friendly_fire_incidents += 1
                        friendly_fire_damage += total_dmg

                if tgt_id in opp_ids:
                    if "hp_after" in ev and ev["hp_after"] is not None:
                        opp_hp[tgt_id] = max(0, int(ev["hp_after"]))
                    else:
                        opp_hp[tgt_id] = max(0, opp_hp.get(tgt_id, 0) - total_dmg)
                    if opp_hp[tgt_id] <= 0:
                        opp_alive[tgt_id] = False

            elif etype == "healing_applied":
                src_id = str(ev.get("source_id") or actor_id)
                tgt_id = str(ev.get("target_id") or "")
                caused_by_side = (src_id in side_ids) or (a_side == side and not ev.get("source_id"))
                hp_before = ev.get("hp_before")
                hp_after = ev.get("hp_after")
                amt = int(ev.get("amount", 0))
                revived = bool(ev.get("revived", False)) or (
                    hp_before is not None and int(hp_before) == 0 and hp_after is not None and int(hp_after) > 0
                )
                healed = (
                    (hp_before is not None and hp_after is not None and int(hp_after) > int(hp_before))
                    or (hp_before is None and amt > 0)
                )
                if caused_by_side and tgt_id in side_ids:
                    if revived:
                        ally_revivals += 1
                    if healed:
                        ally_heals += 1
                if tgt_id in opp_ids and hp_after is not None:
                    opp_hp[tgt_id] = max(0, int(hp_after))
                    if opp_hp[tgt_id] > 0:
                        opp_alive[tgt_id] = True

            elif etype == "mastery_triggered":
                m_actor = str(ev.get("actor_id") or actor_id)
                if not engine_reaction and (m_actor in side_ids or (a_side == side and not ev.get("actor_id"))):
                    mastery_triggers += 1
                    m_slug = strip_prefix(str(ev.get("mastery") or ""))
                    if m_slug in _SETUP_MASTERIES:
                        mastery_setup_triggers += 1

            elif etype in ("combatant_defeated", "combatant_died"):
                cid = str(ev.get("combatant_id") or "")
                if cid in opp_ids:
                    opp_alive[cid] = False
                    opp_hp[cid] = 0

        # Also sync opponent HP from state_diff if events omitted hp_after.
        for hc in state_diff.get("hp_changes") or []:
            cid = str(hc.get("id") or "")
            if cid in opp_ids and "after" in hc:
                opp_hp[cid] = max(0, int(hc["after"]))
                if opp_hp[cid] <= 0:
                    opp_alive[cid] = False

    # ------------------------------------------------------------------
    # 3. Tactical Score (0..100, weight 0.25)
    # ------------------------------------------------------------------
    total_opponent_initial_hp = sum(
        max(1, int(c.get("hp_max", c.get("hp", 1)))) for c in opp_init
    )
    dmg_eff_pts = 40.0 * min(1.0, effective_damage_dealt / max(1, total_opponent_initial_hp))
    immunity_pts = max(0.0, 25.0 - 12.5 * wasted_immune_hits) * contribution
    action_econ_pts = 20.0 * contribution

    feat_pts = min(
        15.0,
        mastery_triggers * 3.0
        + effective_buff_casts * 3.0
        + ally_revivals * 5.0
        + ally_heals * 2.0,
    )
    tactical_score = round(_clamp(dmg_eff_pts + immunity_pts + action_econ_pts + feat_pts), 2)

    # ------------------------------------------------------------------
    # 4. Coordination Score (0..100, weight 0.15)
    # ------------------------------------------------------------------
    ff_pts = max(0.0, 40.0 - 20.0 * friendly_fire_incidents) * contribution

    table_talk_messages = max(len(blackboard_side), sum(turn_talk.values()))
    # Only talk on productive turns scores: chatter while idling earns nothing.
    # The full message count stays in metrics.
    productive_talk_turns = sum(1 for k in turn_talk if turn_productivity.get(k, False))
    comm_pts = 20.0 * min(1.0, productive_talk_turns / max(1.0, active_turns * 0.5))

    if offensive_multi_enemy_actions == 0:
        focus_fire_ratio = 1.0 if (len(opp_ids) <= 1 or winner == side) else 0.0
    else:
        focus_fire_ratio = focused_actions / offensive_multi_enemy_actions
    focus_pts = 20.0 * focus_fire_ratio * contribution

    effective_help_actions = len(successful_help_turns)
    teamwork_pts = min(
        20.0,
        ally_revivals * 12.0
        + ally_heals * 6.0
        + ally_buff_targets * 4.0
        + effective_help_actions * 8.0
        + mastery_setup_triggers * 4.0,
    )
    coordination_score = round(_clamp(ff_pts + comm_pts + focus_pts + teamwork_pts), 2)

    # ------------------------------------------------------------------
    # 5. Composite Score
    # ------------------------------------------------------------------
    composite_score = round(
        _clamp(
            SCORE_WEIGHTS["outcome"] * outcome_score
            + SCORE_WEIGHTS["rules_legality"] * rules_legality_score
            + SCORE_WEIGHTS["tactical"] * tactical_score
            + SCORE_WEIGHTS["coordination"] * coordination_score
        ),
        2,
    )

    return {
        "side": side,
        "outcome_score": outcome_score,
        "rules_legality_score": rules_legality_score,
        "tactical_score": tactical_score,
        "coordination_score": coordination_score,
        "composite_score": composite_score,
        "weights": dict(SCORE_WEIGHTS),
        "metrics": {
            "winner": winner,
            "rounds": rounds,
            "max_rounds": max_rounds,
            "survivors": survivors,
            "total_allies": total_allies,
            "hp_ratio": hp_ratio,
            "effective_damage_dealt": effective_damage_dealt,
            "wasted_immune_hits": wasted_immune_hits,
            "wasted_immune_raw_damage": wasted_immune_raw_damage,
            "mastery_triggers": mastery_triggers,
            "mastery_setup_triggers": mastery_setup_triggers,
            "effective_buff_casts": effective_buff_casts,
            "ally_revivals": ally_revivals,
            "ally_heals": ally_heals,
            "ally_buff_targets": ally_buff_targets,
            "help_actions": help_actions,
            "effective_help_actions": effective_help_actions,
            "friendly_fire_incidents": friendly_fire_incidents,
            "friendly_fire_damage": friendly_fire_damage,
            "table_talk_messages": table_talk_messages,
            "focus_fire_ratio": round(focus_fire_ratio, 4),
            "active_turns": active_turns,
            "productive_turns": productive_turns,
            "contribution": round(contribution, 4),
            "productive_talk_turns": productive_talk_turns,
            "total_decisions": total_decisions,
            "first_attempt_legal": first_attempt_legal,
            "total_violations": side_violations,
            "forced_fallbacks": forced_fallbacks,
            "llm_usage_totals": {
                "prompt_tokens": usage_prompt,
                "completion_tokens": usage_completion,
                "total_tokens": usage_total,
            },
        },
    }
