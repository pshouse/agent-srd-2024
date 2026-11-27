"""Interactive Per-Seat Play Session Controller for 5e 2024 (SRD 5.2).

Supports mixed human + AI tables across any scenario:
- `--as <pc_id>`: control 1 adventurer alongside AI companions + AI DM
- `--as party`: control the entire adventuring party against an AI DM
- `--as dm`: control all enemies against an AI party
- `--seat <combatant_id>=<driver_spec>`: arbitrary per-seat driver mixing

Reuses Stage 2 `srd2024.engine.combat.step()` and Stage 3 `build_observation`,
monster recharge sidecar, `score_match`, and `compute_transcript_digest` so
completed interactive sessions emit 100% deterministic, `bin/srd-arena replay`
verifiable `MatchTranscript` traces and support lossless `--save` / `--resume`
checkpoints.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Callable

from srd2024.engine.combat import step
from srd2024.engine.dice import DeterministicRNG
from srd2024.engine.store import RulesStore

from .agents import AgentDriver, BaseLLMDriver, create_driver, scrub_secrets
from .human import HumanDriver, HumanSessionPause
from .runner import (
    _arena_intent_rejection,
    _cannot_act,
    _describe_driver_config,
    _driver_visible_error,
    _find_monster_action,
    _is_action_on_cooldown,
    _is_match_over,
    _is_recharge_action,
    _resolve_winner,
    _select_driver,
    build_observation,
    compute_transcript_digest,
)
from .scenarios import load_scenario
from .scoring import score_match

_VS_AC_RE = re.compile(r"\((\d+)\s+vs\s+AC\s+\d+\)")


def _parse_seat_overrides(
    seat_overrides: dict[str, Any] | list[str] | None,
) -> dict[str, Any]:
    """Normalize seat overrides from either a dict or `['id=spec', ...]` CLI list."""
    if not seat_overrides:
        return {}
    if isinstance(seat_overrides, dict):
        return {str(k).strip(): v for k, v in seat_overrides.items() if str(k).strip()}
    parsed: dict[str, Any] = {}
    for item in seat_overrides:
        raw = str(item).strip()
        if not raw:
            continue
        if "=" not in raw:
            raise ValueError(f"Invalid --seat '{raw}': expected '<combatant_id>=<driver_spec>' format.")
        cid, spec = raw.split("=", 1)
        cid = cid.strip()
        spec = spec.strip()
        if not cid or not spec:
            raise ValueError(f"Invalid --seat '{raw}': both combatant_id and driver_spec must be non-empty.")
        parsed[cid] = spec
    return parsed


def _is_driver_instance(obj: Any) -> bool:
    return callable(getattr(obj, "decide", None)) and hasattr(obj, "name")


def _serialize_driver_spec(spec: Any) -> Any:
    if isinstance(spec, (str, dict)):
        return copy.deepcopy(spec)
    if _is_driver_instance(spec):
        return str(getattr(spec, "name", "heuristic-tactical"))
    return str(spec)


def _sanitize_stream_hook(hook: str, hidden_names: list[str]) -> str:
    if not hidden_names or "vs AC" not in hook:
        return hook
    for name in hidden_names:
        if f"hits {name}" in hook or f"misses {name}" in hook:
            return _VS_AC_RE.sub(r"(roll \1)", hook)
    return hook


def resolve_seat_drivers(
    scenario: str | Path | dict[str, Any],
    *,
    play_as: str | None = None,
    seat_overrides: dict[str, Any] | list[str] | None = None,
    party_agent: str | dict[str, Any] = "heuristic-tactical",
    enemy_agent: str | dict[str, Any] = "heuristic-tactical",
    human_driver: HumanDriver | None = None,
    rules: RulesStore | None = None,
    seed: int = 42,
    api_base: str | None = None,
    api_key_env: str | None = None,
    temperature: float = 0.0,
) -> tuple[dict[str, AgentDriver], dict[str, AgentDriver]]:
    """Resolve per-combatant `(party_drivers, enemy_drivers)` maps for an interactive table.

    Modes:
    - `play_as="<combatant_id>"`: human controls that combatant; AI controls the rest.
    - `play_as="party"`: human controls all party PCs; AI controls enemies.
    - `play_as="dm"`: human controls all enemies; AI controls the party.
    - `seat_overrides`: explicit per-combatant `<id>=<driver_spec>` overrides applied
      on top of `play_as` / defaults.
    - If both `play_as is None` and `seat_overrides` is empty, defaults to playing
      the first party member (`party_ids[0]`).
    """
    rules_store = rules if rules is not None else RulesStore()
    compiled = load_scenario(scenario, rules_store)
    party_ids = list(compiled["party_ids"])
    enemy_ids = list(compiled["enemy_ids"])
    all_ids = set(party_ids) | set(enemy_ids)

    overrides = _parse_seat_overrides(seat_overrides)
    for cid in overrides:
        if cid not in all_ids:
            valid_list = ", ".join(party_ids + enemy_ids)
            raise ValueError(
                f"Unknown combatant ID '{cid}' in seat override for scenario '{compiled['id']}'. "
                f"Valid IDs: {valid_list}"
            )

    human_drv = human_driver if human_driver is not None else HumanDriver(rules=rules_store)

    # Determine which seats default to human via play_as
    human_seats: set[str] = set()
    if play_as is not None and str(play_as).strip():
        role = str(play_as).strip()
        role_low = role.lower()
        if role_low == "party":
            human_seats.update(party_ids)
        elif role_low == "dm":
            human_seats.update(enemy_ids)
        elif role in all_ids:
            human_seats.add(role)
        else:
            valid_opts = ", ".join(["party", "dm"] + party_ids + enemy_ids)
            raise ValueError(
                f"Invalid --as target '{play_as}' for scenario '{compiled['id']}'. "
                f"Expected one of: {valid_opts}"
            )
    elif not overrides:
        if party_ids:
            human_seats.add(party_ids[0])

    def _build_seat_driver(cid: str, default_spec: str | dict[str, Any], seat_seed: int) -> AgentDriver:
        if cid in overrides:
            ov = overrides[cid]
            if _is_driver_instance(ov):
                return ov
            if isinstance(ov, str) and ov.strip().lower() == "human":
                return human_drv
            return create_driver(
                ov,
                rules=rules_store,
                seed=seat_seed,
                api_base=api_base,
                api_key_env=api_key_env,
                temperature=temperature,
            )
        if cid in human_seats:
            return human_drv
        if _is_driver_instance(default_spec):
            return default_spec
        if isinstance(default_spec, str) and default_spec.strip().lower() == "human":
            return human_drv
        return create_driver(
            default_spec,
            rules=rules_store,
            seed=seat_seed,
            api_base=api_base,
            api_key_env=api_key_env,
            temperature=temperature,
        )

    party_map: dict[str, AgentDriver] = {}
    for idx, pid in enumerate(party_ids):
        party_map[pid] = _build_seat_driver(pid, party_agent, int(seed) + idx)

    enemy_map: dict[str, AgentDriver] = {}
    for idx, eid in enumerate(enemy_ids):
        enemy_map[eid] = _build_seat_driver(eid, enemy_agent, int(seed) + 100 + idx)

    return party_map, enemy_map


def save_session_checkpoint(
    path: str | Path,
    checkpoint: dict[str, Any],
    *,
    force: bool = False,
) -> Path:
    """Persist an interactive session checkpoint JSON cleanly to disk."""
    out_path = Path(path).expanduser()
    if out_path.exists() and not force:
        raise FileExistsError(
            f"Refusing to overwrite '{out_path}'. Pass --force to replace it."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def load_session_checkpoint(path: str | Path) -> dict[str, Any]:
    """Load and validate a saved interactive session checkpoint JSON."""
    ckpt_path = Path(path).expanduser()
    if not ckpt_path.is_file():
        raise FileNotFoundError(f"Checkpoint file not found: '{ckpt_path}'")
    try:
        data = json.loads(ckpt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in checkpoint '{ckpt_path}': {exc.msg}") from exc
    if not isinstance(data, dict) or data.get("kind") != "arena_session_checkpoint":
        raise ValueError(f"File '{ckpt_path}' is not a valid arena session checkpoint.")
    for req_key in ("scenario", "config", "initial_state", "state"):
        if req_key not in data or not isinstance(data[req_key], dict):
            raise ValueError(f"Checkpoint '{ckpt_path}' is missing required object '{req_key}'.")
    return data


def _build_checkpoint_payload(
    *,
    compiled: dict[str, Any],
    config: dict[str, Any],
    initial_state: dict[str, Any],
    state: dict[str, Any],
    recharge_rng: DeterministicRNG,
    cooldowns: dict[str, dict[str, bool]],
    recharge_events: list[dict[str, Any]],
    blackboard: dict[str, list[dict[str, Any]]],
    recent_hooks: list[str],
    actions: list[dict[str, Any]],
    violations: list[dict[str, Any]],
    rejected_inputs: list[dict[str, Any]] | None = None,
    turn_counter: int,
    action_seq: int,
    turn_action_index: int,
    turn_recharge_checked: bool,
    pause_reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "4",
        "kind": "arena_session_checkpoint",
        "scenario": copy.deepcopy(compiled),
        "config": copy.deepcopy(config),
        "initial_state": copy.deepcopy(initial_state),
        "state": copy.deepcopy(state),
        "recharge_rng": recharge_rng.to_dict(),
        "cooldowns": copy.deepcopy(cooldowns),
        "recharge_events": copy.deepcopy(recharge_events),
        "blackboard": copy.deepcopy(blackboard),
        "recent_hooks": list(recent_hooks),
        "actions": copy.deepcopy(actions),
        "violations": copy.deepcopy(violations),
        "rejected_inputs": copy.deepcopy(list(rejected_inputs or [])),
        "turn_counter": int(turn_counter),
        "action_seq": int(action_seq),
        "turn_action_index": int(turn_action_index),
        "turn_recharge_checked": bool(turn_recharge_checked),
        "pause_reason": str(pause_reason),
    }


def run_interactive_session(
    scenario: str | Path | dict[str, Any] | None = None,
    *,
    rules: RulesStore,
    play_as: str | None = None,
    seat_overrides: dict[str, Any] | list[str] | None = None,
    party_agent: str | dict[str, Any] = "heuristic-tactical",
    enemy_agent: str | dict[str, Any] = "heuristic-tactical",
    human_driver: HumanDriver | None = None,
    nl_driver: BaseLLMDriver | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], Any] = print,
    seed: int = 42,
    max_rounds: int | None = None,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
    save_path: str | Path | None = None,
    resume_path: str | Path | None = None,
    out_trace: str | Path | None = None,
    force: bool = False,
    api_base: str | None = None,
    api_key_env: str | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Run an interactive human-in-the-loop 5e 2024 (SRD 5.2) tabletop session.

    Returns either:
    - A complete, replayable Stage 3 `MatchTranscript` dict (when combat concludes), OR
    - A paused checkpoint report dict (`{"status": "paused", ...}`) when the human
      player triggers `save` or `quit`.
    """
    if resume_path is not None:
        ckpt = load_session_checkpoint(resume_path)
        compiled = load_scenario(ckpt["scenario"], rules)
        saved_cfg = ckpt.get("config") or {}
        eff_seed = int(saved_cfg.get("seed", seed))
        eff_max_rounds = int(
            max_rounds if max_rounds is not None else saved_cfg.get("max_rounds", compiled["max_rounds"])
        )
        eff_max_actions = int(saved_cfg.get("max_actions_per_turn", max_actions_per_turn))
        eff_max_retries = int(saved_cfg.get("max_retries_per_action", max_retries_per_action))
        eff_play_as = play_as
        eff_seat_overrides = _parse_seat_overrides(seat_overrides) if seat_overrides is not None else {}
        eff_party_agent = party_agent
        eff_enemy_agent = enemy_agent
        saved_party = saved_cfg.get("party_agent")
        saved_enemy = saved_cfg.get("enemy_agent")
        if saved_party != party_agent or saved_enemy != enemy_agent or saved_cfg.get("seat_overrides"):
            output_fn(
                "Warning: checkpoint driver settings (drivers, seats, base URLs, "
                "api_key_env) were ignored. Only the current command line is used. "
                "Checkpoints contain RNG state and exact HP and are for the player's own use."
            )

        initial_state: dict[str, Any] = copy.deepcopy(ckpt["initial_state"])
        state: dict[str, Any] = copy.deepcopy(ckpt["state"])
        recharge_rng = DeterministicRNG.from_dict(ckpt.get("recharge_rng"))
        cooldowns: dict[str, dict[str, bool]] = copy.deepcopy(ckpt.get("cooldowns") or {})
        recharge_events: list[dict[str, Any]] = copy.deepcopy(ckpt.get("recharge_events") or [])
        blackboard: dict[str, list[dict[str, Any]]] = copy.deepcopy(
            ckpt.get("blackboard") or {"party": [], "enemy": []}
        )
        recent_hooks: list[str] = list(ckpt.get("recent_hooks") or [])
        actions: list[dict[str, Any]] = copy.deepcopy(ckpt.get("actions") or [])
        violations: list[dict[str, Any]] = copy.deepcopy(ckpt.get("violations") or [])
        rejected_inputs: list[dict[str, Any]] = copy.deepcopy(ckpt.get("rejected_inputs") or [])
        turn_counter = int(ckpt.get("turn_counter", 0))
        action_seq = int(ckpt.get("action_seq", len(actions)))
        resume_action_index = int(ckpt.get("turn_action_index", 0))
        resume_recharge_checked = bool(ckpt.get("turn_recharge_checked", False))
        resumed = True
    else:
        if scenario is None:
            raise ValueError("Provide a scenario ID/path or --resume <checkpoint.json>.")
        compiled = load_scenario(scenario, rules)
        eff_seed = int(seed)
        eff_max_rounds = int(max_rounds) if max_rounds is not None else int(compiled["max_rounds"])
        if eff_max_rounds < 1:
            raise ValueError(f"max_rounds must be >= 1, got {eff_max_rounds}.")
        eff_max_actions = int(max_actions_per_turn)
        eff_max_retries = int(max_retries_per_action)
        eff_play_as = play_as
        eff_seat_overrides = _parse_seat_overrides(seat_overrides)
        eff_party_agent = party_agent
        eff_enemy_agent = enemy_agent

        start_res = step(
            None,
            {
                "type": "start_encounter",
                "encounter_id": compiled["id"],
                "seed": eff_seed,
                "combatants": copy.deepcopy(compiled["combatants"]),
            },
            rules,
        )
        if start_res.get("status") != "ok":
            raise ValueError(f"Failed to start encounter '{compiled['id']}': {start_res.get('error')}")

        state = start_res["state"]
        initial_state = copy.deepcopy(state)
        recent_hooks = list(start_res.get("narrative_hooks") or [])
        recharge_rng = DeterministicRNG(seed=eff_seed ^ 0x5A5A5A5A)
        cooldowns = {}
        recharge_events = []
        blackboard = {"party": [], "enemy": []}
        actions = []
        violations = []
        rejected_inputs = []
        turn_counter = 0
        action_seq = 0
        resume_action_index = 0
        resume_recharge_checked = False
        resumed = False

    if human_driver is None:
        human_drv = HumanDriver(
            rules=rules,
            input_fn=input_fn,
            output_fn=output_fn,
            nl_driver=nl_driver,
        )
    else:
        human_drv = human_driver

    party_driver, enemy_driver = resolve_seat_drivers(
        compiled,
        play_as=eff_play_as,
        seat_overrides=eff_seat_overrides,
        party_agent=eff_party_agent,
        enemy_agent=eff_enemy_agent,
        human_driver=human_drv,
        rules=rules,
        seed=eff_seed,
        api_base=api_base,
        api_key_env=api_key_env,
        temperature=temperature,
    )

    # Identify which sides have human seats so stream output preserves partial observability
    human_sides: set[str] = set()
    for pid, drv in party_driver.items():
        if isinstance(drv, HumanDriver) or str(getattr(drv, "name", "")).lower() == "human":
            human_sides.add("party")
    for eid, drv in enemy_driver.items():
        if isinstance(drv, HumanDriver) or str(getattr(drv, "name", "")).lower() == "human":
            human_sides.add("enemy")

    hidden_target_names: list[str] = []
    if human_sides == {"party"}:
        hidden_target_names = [
            str(c["name"])
            for cid, c in state["combatants"].items()
            if str(c.get("side")) != "party"
        ]
    elif human_sides == {"enemy"}:
        hidden_target_names = [
            str(c["name"])
            for cid, c in state["combatants"].items()
            if str(c.get("side")) == "party"
        ]

    session_config: dict[str, Any] = {
        "seed": eff_seed,
        "max_rounds": eff_max_rounds,
        "max_actions_per_turn": eff_max_actions,
        "max_retries_per_action": eff_max_retries,
        "play_as": eff_play_as,
        "seat_overrides": {k: _serialize_driver_spec(v) for k, v in eff_seat_overrides.items()},
        "party_agent": _serialize_driver_spec(eff_party_agent),
        "enemy_agent": _serialize_driver_spec(eff_enemy_agent),
    }

    # Session header banner
    if resumed:
        cur_active = state["turn_order"][int(state["active_index"]) % len(state["turn_order"])]
        output_fn(
            f"=== Resumed 5e 2024 (SRD 5.2) Session: {compiled['name']} "
            f"(Round {state['round']}, Active: {state['combatants'][cur_active]['name']}) ==="
        )
    else:
        init_names = " -> ".join(
            f"{state['combatants'][cid]['name']} ({state['combatants'][cid].get('initiative', '?')})"
            for cid in state["turn_order"]
        )
        human_roster = [
            cid for cid, drv in {**party_driver, **enemy_driver}.items()
            if isinstance(drv, HumanDriver) or str(getattr(drv, "name", "")).lower() == "human"
        ]
        output_fn(f"=== 5e 2024 (SRD 5.2) Interactive Play: {compiled['name']} (seed {eff_seed}) ===")
        output_fn(f"Initiative: {init_names}")
        output_fn(f"Human seat(s): {', '.join(human_roster) if human_roster else 'none (spectator)'}")

    max_total_turns = eff_max_rounds * max(1, len(state["turn_order"])) + 4

    while not _is_match_over(state) and int(state["round"]) <= eff_max_rounds:
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
        is_human_turn = isinstance(driver, HumanDriver) or driver_name.lower() == "human"

        # 1. Monster recharge sidecar check at start of monster's turn (skip if already checked before mid-turn pause).
        if not resume_recharge_checked:
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
                    if recharged:
                        output_fn(f"[Round {cur_round}] {actor['name']}'s {act_name} recharged!")
        turn_recharge_checked = True

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
            for h in skip_res.get("narrative_hooks") or []:
                output_fn(f"  * {_sanitize_stream_hook(str(h), hidden_target_names)}")
            recent_hooks.extend(skip_res.get("narrative_hooks") or [])
            state = skip_res["state"]
            turn_counter += 1
            resume_action_index = 0
            resume_recharge_checked = False
            continue

        if not is_human_turn and resume_action_index == 0:
            output_fn(f"\n--- Round {cur_round} | {actor['name']} ({driver_name}) ---")

        # 3. Active turn action slots
        turn_ended = False
        start_slot = max(0, resume_action_index)
        resume_action_index = 0
        resume_recharge_checked = False

        for action_index in range(start_slot, max(1, eff_max_actions)):
            if _is_match_over(state):
                turn_ended = True
                break
            now_active = state["turn_order"][int(state["active_index"]) % len(state["turn_order"])]
            if now_active != active_id or int(state["round"]) != cur_round:
                turn_ended = True
                break

            retry_feedback: str | None = None
            accepted = False
            max_attempts = (max(eff_max_retries, 5) if is_human_turn else max(0, eff_max_retries)) + 1

            for attempt in range(1, max_attempts + 1):
                observation = build_observation(
                    state,
                    active_id,
                    compiled,
                    blackboard,
                    recent_hooks,
                    cooldowns,
                    rules,
                    retry_feedback=retry_feedback,
                )
                observation["self"]["heroic_inspiration"] = bool(
                    state["combatants"][active_id].get("heroic_inspiration", False)
                )
                try:
                    try:
                        decision = driver.decide(observation)
                    finally:
                        drain_fn = getattr(driver, "drain_rejected_inputs", None)
                        if callable(drain_fn):
                            rejected_inputs.extend(drain_fn())
                    raw_intent = getattr(decision, "intent", None)
                    if not isinstance(raw_intent, dict) or not raw_intent.get("type"):
                        raise ValueError("Driver decision intent must be a dict with a non-empty 'type'.")
                    intent = copy.deepcopy(raw_intent)
                except HumanSessionPause as pause:
                    ckpt_payload = _build_checkpoint_payload(
                        compiled=compiled,
                        config=session_config,
                        initial_state=initial_state,
                        state=state,
                        recharge_rng=recharge_rng,
                        cooldowns=cooldowns,
                        recharge_events=recharge_events,
                        blackboard=blackboard,
                        recent_hooks=recent_hooks,
                        actions=actions,
                        violations=violations,
                        rejected_inputs=rejected_inputs,
                        turn_counter=turn_counter,
                        action_seq=action_seq,
                        turn_action_index=action_index,
                        turn_recharge_checked=turn_recharge_checked,
                        pause_reason=pause.reason,
                    )
                    saved_str: str | None = None
                    if save_path is not None:
                        saved_p = save_session_checkpoint(save_path, ckpt_payload, force=force)
                        saved_str = str(saved_p)
                        output_fn(f"[Session paused ({pause.reason}) — checkpoint saved to {saved_str}]")
                    else:
                        output_fn(f"[Session paused ({pause.reason}) — no --save path specified]")
                    return {
                        "status": "paused",
                        "reason": pause.reason,
                        "checkpoint_path": saved_str,
                        "checkpoint": ckpt_payload,
                        "scenario": {
                            "id": compiled["id"],
                            "name": compiled["name"],
                        },
                        "round": cur_round,
                        "actions_taken": len(actions),
                    }
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
                        "Emit a valid legal intent."
                    )
                    continue

                itype = str(intent["type"]).strip().lower()
                intent["type"] = itype
                rejection = _arena_intent_rejection(intent, active_id)
                if rejection is not None:
                    err_msg = scrub_secrets(rejection)
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
                        f"{rejection} Emit one allowed intent for the active combatant."
                    )
                    if is_human_turn:
                        output_fn(f"  ! Rejected: {err_msg}")
                    continue

                # Recharge sidecar validation before calling combat.step()
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
                                "Choose an available action."
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
                        blackboard.setdefault(side, []).append(
                            {
                                "round": cur_round,
                                "actor_id": active_id,
                                "actor_name": cur_actor["name"],
                                "message": talk_clean,
                            }
                        )
                        if not human_sides or side in human_sides:
                            output_fn(f'  [{cur_actor["name"]} says]: "{talk_clean}"')

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
                            "rationale": str(getattr(decision, "rationale", "") or ""),
                            "table_talk": talk_clean,
                            "events": res.get("events") or [],
                            "state_diff": res.get("state_diff") or {},
                            "narrative_hooks": res.get("narrative_hooks") or [],
                            "usage": usage_dict,
                        }
                    )
                    for h in res.get("narrative_hooks") or []:
                        output_fn(f"  * {_sanitize_stream_hook(str(h), hidden_target_names)}")
                    recent_hooks.extend(res.get("narrative_hooks") or [])
                    state = res["state"]
                    accepted = True
                    if itype == "end_turn":
                        turn_ended = True
                    break

                err_msg = scrub_secrets(
                    _driver_visible_error(str(res.get("error") or "Invalid intent."), active_id)
                )
                if is_human_turn:
                    output_fn(f"  ! Rejected: {err_msg}")
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
                    "Choose a legal action."
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
                        "attempts": max_attempts,
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
                for h in fb_res.get("narrative_hooks") or []:
                    output_fn(f"  * {_sanitize_stream_hook(str(h), hidden_target_names)}")
                recent_hooks.extend(fb_res.get("narrative_hooks") or [])
                state = fb_res["state"]
                turn_ended = True
                break

            if turn_ended or _is_match_over(state):
                break

        # 4. Auto end_turn if action slots exhausted while turn still active
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
                        "action_index": eff_max_actions,
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
                for h in end_res.get("narrative_hooks") or []:
                    output_fn(f"  * {_sanitize_stream_hook(str(h), hidden_target_names)}")
                recent_hooks.extend(end_res.get("narrative_hooks") or [])
                state = end_res["state"]

        turn_counter += 1

    winner = _resolve_winner(state)
    rounds_played = max(
        (int(a["round"]) for a in actions),
        default=min(int(state["round"]), eff_max_rounds),
    )

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
            "seed": eff_seed,
            "max_rounds": eff_max_rounds,
            "max_actions_per_turn": eff_max_actions,
            "max_retries_per_action": eff_max_retries,
            "party_driver": _describe_driver_config(party_driver),
            "enemy_driver": _describe_driver_config(enemy_driver),
        },
        "winner": winner,
        "rounds": rounds_played,
        "initial_state": initial_state,
        "final_state": copy.deepcopy(state),
        "actions": actions,
        "violations": violations,
        "rejected_inputs": rejected_inputs,
        "recharge_events": recharge_events,
        "blackboard": blackboard,
    }

    transcript["scorecard"] = score_match(transcript, rules, side="party")
    transcript["digest"] = compute_transcript_digest(transcript)

    if out_trace is not None:
        trace_path = Path(out_trace).expanduser()
        if trace_path.exists() and not force:
            raise FileExistsError(
                f"Refusing to overwrite '{trace_path}'. Pass --force to replace it."
            )
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    sc_card = transcript["scorecard"]
    output_fn(
        f"\n=== Match Complete | Winner: {winner.upper()} | Rounds: {rounds_played} "
        f"| Composite Score: {sc_card['composite_score']:.2f}/100 ==="
    )
    return transcript


__all__ = [
    "load_session_checkpoint",
    "resolve_seat_drivers",
    "run_interactive_session",
    "save_session_checkpoint",
]
