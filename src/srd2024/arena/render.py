"""Markdown tabletop session transcript and benchmark leaderboard renderers.

Implements STAGE3-SPEC §6.2 using strictly Python stdlib and trademark-clean
5e 2024 (SRD 5.2) prose.
"""

from __future__ import annotations

from typing import Any


def _fmt_pos(pos: dict[str, Any] | None) -> str:
    if not isinstance(pos, dict):
        return "(?, ?)"
    return f"({pos.get('x', 0)}, {pos.get('y', 0)})"


def _fmt_conditions(raw_conds: list[Any] | None) -> str:
    if not raw_conds:
        return "—"
    names: list[str] = []
    for c in raw_conds:
        if isinstance(c, dict):
            val = str(c.get("condition") or "").strip()
        else:
            val = str(c).strip()
        if val and val not in names:
            names.append(val)
    return ", ".join(names) if names else "—"


def _fmt_intent(intent: dict[str, Any] | None) -> str:
    if not isinstance(intent, dict):
        return "`unknown`"
    itype = str(intent.get("type") or "unknown")
    parts: list[str] = [f"`{itype}`"]
    if itype == "move":
        if intent.get("stand_up"):
            parts.append("stand_up=True")
        if intent.get("to") is not None:
            parts.append(f"to {_fmt_pos(intent.get('to'))}")
    elif itype == "attack":
        if intent.get("weapon"):
            parts.append(f"weapon=`{intent['weapon']}`")
        if intent.get("action"):
            parts.append(f"action=`{intent['action']}`")
        if intent.get("offhand"):
            parts.append("(off-hand)")
        if intent.get("target_ids"):
            parts.append(f"targets={intent['target_ids']}")
        elif intent.get("target_id"):
            parts.append(f"target=`{intent['target_id']}`")
    elif itype == "cast_spell":
        if intent.get("spell"):
            parts.append(f"spell=`{intent['spell']}`")
        if intent.get("slot_level") is not None:
            parts.append(f"slot={intent['slot_level']}")
        if intent.get("as_reaction"):
            parts.append("(reaction)")
        if intent.get("target_ids"):
            parts.append(f"targets={intent['target_ids']}")
        elif intent.get("target_id"):
            parts.append(f"target=`{intent['target_id']}`")
        if intent.get("destination"):
            parts.append(f"dest={_fmt_pos(intent['destination'])}")
    elif itype == "help":
        if intent.get("target_id"):
            parts.append(f"target=`{intent['target_id']}`")
        if intent.get("ally_id"):
            parts.append(f"ally=`{intent['ally_id']}`")
    return " ".join(parts)


def render_scenarios_markdown(scenarios: list[dict[str, Any]]) -> str:
    """Render built-in 5e 2024 (SRD 5.2) arena scenarios as a Markdown catalog."""
    lines: list[str] = [
        "# 5e 2024 (SRD 5.2) Arena Scenarios",
        "",
        "| ID | Name | Tier | Level | Max Rounds | Tactical Themes |",
        "|---|---|---:|---:|---:|---|",
    ]
    for sc in scenarios:
        themes = ", ".join(str(t) for t in (sc.get("tactical_themes") or [])) or "—"
        lines.append(
            f"| `{sc.get('id', '')}` | {sc.get('name', '')} | {sc.get('tier', '')} "
            f"| {sc.get('party_level', '')} | {sc.get('max_rounds', '')} | {themes} |"
        )

    for sc in scenarios:
        lines.extend(
            [
                "",
                f"## {sc.get('name', sc.get('id', 'Scenario'))} (`{sc.get('id', '')}`)",
                "",
                str(sc.get("description") or ""),
                "",
            ]
        )
        party_sum = sc.get("party_summary")
        if isinstance(party_sum, list):
            party_txt = ", ".join(str(x) for x in party_sum)
        else:
            party_txt = str(party_sum or "—")
        enemy_sum = sc.get("enemy_summary")
        if isinstance(enemy_sum, list):
            enemy_txt = ", ".join(str(x) for x in enemy_sum)
        else:
            enemy_txt = str(enemy_sum or "—")
        lines.append(f"- **Party:** {party_txt}")
        lines.append(f"- **Enemies:** {enemy_txt}")
        themes = ", ".join(str(t) for t in (sc.get("tactical_themes") or []))
        if themes:
            lines.append(f"- **Tactical Themes:** {themes}")

    return "\n".join(lines).rstrip() + "\n"


def render_match_markdown(transcript: dict[str, Any]) -> str:
    """Render a MatchTranscript as a readable 5e 2024 (SRD 5.2) Tabletop Session Transcript."""
    scenario = transcript.get("scenario") or {}
    config = transcript.get("config") or {}
    init_state = transcript.get("initial_state") or {}
    final_state = transcript.get("final_state") or {}
    actions = list(transcript.get("actions") or [])
    violations = list(transcript.get("violations") or [])
    recharge_events = list(transcript.get("recharge_events") or [])
    scorecard = transcript.get("scorecard") or {}

    sc_name = str(scenario.get("name") or scenario.get("id") or "Arena Match")
    sc_id = str(scenario.get("id") or "")
    seed = config.get("seed", "")
    party_drv = config.get("party_driver", "unknown")
    enemy_drv = config.get("enemy_driver", "unknown")
    winner = str(transcript.get("winner") or "draw")
    rounds = transcript.get("rounds", 0)
    digest = str(transcript.get("digest") or "")

    lines: list[str] = [
        f"# 5e 2024 (SRD 5.2) Tabletop Session Transcript — {sc_name}",
        "",
        "## Match Summary",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Scenario | {sc_name} (`{sc_id}`) |",
        f"| Seed | `{seed}` |",
        f"| Party Driver | `{party_drv}` |",
        f"| Enemy Driver | `{enemy_drv}` |",
        f"| Winner | **{winner}** |",
        f"| Rounds | {rounds} |",
        f"| Digest | `{digest}` |",
        "",
    ]

    # Starting Roster & Initiative Order
    init_combatants = init_state.get("combatants") or {}
    turn_order = init_state.get("turn_order") or sorted(init_combatants.keys())
    lines.extend(
        [
            "## Starting Roster & Initiative Order",
            "",
            "| Init | ID | Name | Side | HP | AC | Position |",
            "|---:|---|---|---|---:|---:|---|",
        ]
    )
    for cid in turn_order:
        c = init_combatants.get(cid)
        if not isinstance(c, dict):
            continue
        lines.append(
            f"| {c.get('initiative', '—')} | `{cid}` | {c.get('name', cid)} | {c.get('side', '')} "
            f"| {c.get('hp', 0)}/{c.get('hp_max', 0)} | {c.get('ac', '?')} | {_fmt_pos(c.get('position'))} |"
        )

    # Index violations by (round, turn_index, actor_id) and recharge events by (round, actor_id)
    viol_by_turn: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    for v in violations:
        key = (int(v.get("round", 0)), int(v.get("turn_index", 0)), str(v.get("actor_id") or ""))
        viol_by_turn.setdefault(key, []).append(v)

    recharge_by_round_actor: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for r in recharge_events:
        key = (int(r.get("round", 0)), str(r.get("actor_id") or ""))
        recharge_by_round_actor.setdefault(key, []).append(r)

    # Track live HP & position per combatant for rich turn headers
    live_hp: dict[str, int] = {
        cid: int(c.get("hp", 0)) for cid, c in init_combatants.items() if isinstance(c, dict)
    }
    live_hp_max: dict[str, int] = {
        cid: int(c.get("hp_max", 0)) for cid, c in init_combatants.items() if isinstance(c, dict)
    }
    live_pos: dict[str, dict[str, Any]] = {
        cid: dict(c.get("position") or {"x": 0, "y": 0})
        for cid, c in init_combatants.items()
        if isinstance(c, dict)
    }

    current_round: int | None = None
    current_turn_key: tuple[int, int, str] | None = None
    shown_recharges: set[tuple[int, str]] = set()
    shown_viols: set[tuple[int, int, str]] = set()

    for act in actions:
        rnd = int(act.get("round", 0))
        t_idx = int(act.get("turn_index", 0))
        aid = str(act.get("actor_id") or "")
        aname = str(act.get("actor_name") or aid)
        aside = str(act.get("side") or "")

        if rnd != current_round:
            current_round = rnd
            lines.extend(["", f"## Round {rnd}"])

        t_key = (rnd, t_idx, aid)
        if t_key != current_turn_key:
            current_turn_key = t_key
            hp_txt = f"{live_hp.get(aid, '?')}/{live_hp_max.get(aid, '?')} HP"
            pos_txt = _fmt_pos(live_pos.get(aid))
            lines.extend(["", f"### {aname} (`{aid}`, {aside}) — {hp_txt} @ {pos_txt}"])

            r_key = (rnd, aid)
            if r_key in recharge_by_round_actor and r_key not in shown_recharges:
                shown_recharges.add(r_key)
                for rev in recharge_by_round_actor[r_key]:
                    status_str = "RECHARGED" if rev.get("recharged") else "not recharged"
                    lines.append(
                        f"- *Recharge Check:* `{rev.get('action')}` rolled `{rev.get('roll')}` "
                        f"(need {rev.get('min_value')}+) -> **{status_str}**"
                    )

            if t_key in viol_by_turn and t_key not in shown_viols:
                shown_viols.add(t_key)
                for v in viol_by_turn[t_key]:
                    lines.append(
                        f"- *Retry Violation (attempt {v.get('attempt', '?')}):* {v.get('error', '')}"
                    )

        talk = act.get("table_talk")
        if isinstance(talk, str) and talk.strip():
            lines.append(f"> **Table Talk:** \"{talk.strip()}\"")

        tags: list[str] = []
        if act.get("auto_skipped"):
            tags.append("auto-skipped")
        if act.get("auto_end_turn"):
            tags.append("auto-end-turn")
        if act.get("forced_fallback"):
            tags.append("forced-fallback")
        tag_str = f" *[{', '.join(tags)}]*" if tags else ""

        lines.append(f"- **Intent:** {_fmt_intent(act.get('intent'))}{tag_str}")
        rationale = str(act.get("rationale") or "").strip()
        if rationale:
            lines.append(f"  *Rationale:* {rationale}")

        for hook in act.get("narrative_hooks") or []:
            lines.append(f"  - {hook}")

        # Update live tracker from state_diff
        sdiff = act.get("state_diff") or {}
        for hc in sdiff.get("hp_changes") or []:
            cid = str(hc.get("id") or "")
            if cid and "after" in hc:
                live_hp[cid] = int(hc["after"])
        for pc in sdiff.get("position_changes") or []:
            cid = str(pc.get("id") or "")
            if cid and isinstance(pc.get("after"), dict):
                live_pos[cid] = dict(pc["after"])

    # Final Combatant Status
    final_combatants = final_state.get("combatants") or {}
    final_order = final_state.get("turn_order") or sorted(final_combatants.keys())
    lines.extend(
        [
            "",
            "## Final Combatant Status",
            "",
            "| ID | Name | Side | Status | HP | Conditions | Position |",
            "|---|---|---|---|---:|---|---|",
        ]
    )
    for cid in final_order:
        c = final_combatants.get(cid)
        if not isinstance(c, dict):
            continue
        hp = int(c.get("hp", 0))
        hp_max = int(c.get("hp_max", 0))
        if not c.get("alive"):
            status_lbl = "Defeated"
        elif hp <= 0:
            status_lbl = "Unconscious"
        else:
            status_lbl = "Active"
        lines.append(
            f"| `{cid}` | {c.get('name', cid)} | {c.get('side', '')} | {status_lbl} "
            f"| {hp}/{hp_max} | {_fmt_conditions(c.get('conditions'))} | {_fmt_pos(c.get('position'))} |"
        )

    # Final Quantitative Scorecard
    if scorecard:
        weights = scorecard.get("weights") or {}
        metrics = scorecard.get("metrics") or {}
        lines.extend(
            [
                "",
                "## Quantitative Scorecard",
                "",
                "| Dimension | Weight | Score (0–100) |",
                "|---|---:|---:|",
                f"| Outcome | {weights.get('outcome', 0.35):.2f} | {scorecard.get('outcome_score', 0.0):.2f} |",
                f"| Rules Legality | {weights.get('rules_legality', 0.25):.2f} | {scorecard.get('rules_legality_score', 0.0):.2f} |",
                f"| Tactical Execution | {weights.get('tactical', 0.25):.2f} | {scorecard.get('tactical_score', 0.0):.2f} |",
                f"| Coordination | {weights.get('coordination', 0.15):.2f} | {scorecard.get('coordination_score', 0.0):.2f} |",
                f"| **Composite Score** | **1.00** | **{scorecard.get('composite_score', 0.0):.2f}** |",
                "",
                "### Key Telemetry",
                "",
                f"- **Survivors:** {metrics.get('survivors', 0)}/{metrics.get('total_allies', 0)} "
                f"(HP ratio: {float(metrics.get('hp_ratio', 0.0)) * 100:.1f}%)",
                f"- **Effective Damage Dealt:** {metrics.get('effective_damage_dealt', 0)}",
                f"- **Wasted Immune Hits:** {metrics.get('wasted_immune_hits', 0)} "
                f"(raw damage wasted: {metrics.get('wasted_immune_raw_damage', 0)})",
                f"- **Mastery Triggers:** {metrics.get('mastery_triggers', 0)} | "
                f"**Buff Casts:** {metrics.get('effective_buff_casts', 0)} | "
                f"**Revivals:** {metrics.get('ally_revivals', 0)} | "
                f"**Heals:** {metrics.get('ally_heals', 0)}",
                f"- **Friendly Fire Incidents:** {metrics.get('friendly_fire_incidents', 0)} | "
                f"**Table Talk Messages:** {metrics.get('table_talk_messages', 0)}",
                f"- **Rules Violations:** {metrics.get('total_violations', 0)} | "
                f"**Forced Fallbacks:** {metrics.get('forced_fallbacks', 0)} | "
                f"**First-Attempt Legal:** {metrics.get('first_attempt_legal', 0)}/{metrics.get('total_decisions', 0)}",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def render_benchmark_markdown(report: dict[str, Any]) -> str:
    """Render a BenchmarkReport as a ranked Markdown leaderboard."""
    scenarios = list(report.get("scenarios") or [])
    seeds = list(report.get("seeds") or [])
    enemy_agent = str(report.get("enemy_agent") or "heuristic-tactical")
    leaderboard = list(report.get("leaderboard") or [])

    lines: list[str] = [
        "# 5e 2024 (SRD 5.2) Arena Benchmark Leaderboard",
        "",
        f"- **Scenarios:** {', '.join(f'`{s}`' for s in scenarios)}",
        f"- **Seeds:** `{seeds}`",
        f"- **Enemy Agent:** `{enemy_agent}`",
        "",
        "## Ranked Leaderboard",
        "",
        "| Rank | Contender | W-L-D | Win Rate | Composite | Outcome | Legality | Tactical | Coord | Avg Rnds | Violations | Wasted Immune |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in leaderboard:
        wld = f"{row.get('wins', 0)}-{row.get('losses', 0)}-{row.get('draws', 0)}"
        wr = f"{float(row.get('win_rate', 0.0)) * 100:.1f}%"
        lines.append(
            f"| {row.get('rank', '')} | `{row.get('label', '')}` | {wld} | {wr} "
            f"| **{float(row.get('mean_composite_score', 0.0)):.2f}** "
            f"| {float(row.get('mean_outcome_score', 0.0)):.2f} "
            f"| {float(row.get('mean_rules_legality_score', 0.0)):.2f} "
            f"| {float(row.get('mean_tactical_score', 0.0)):.2f} "
            f"| {float(row.get('mean_coordination_score', 0.0)):.2f} "
            f"| {float(row.get('mean_rounds', 0.0)):.2f} "
            f"| {row.get('total_violations', 0)} "
            f"| {row.get('wasted_immune_hits', 0)} |"
        )

    if scenarios and leaderboard:
        lines.extend(["", "## Per-Scenario Breakdown", ""])
        header = "| Contender | " + " | ".join(f"`{s}`" for s in scenarios) + " |"
        sep = "|---|" + "|".join("---:" for _ in scenarios) + "|"
        lines.extend([header, sep])
        for row in leaderboard:
            by_sc = row.get("by_scenario") or {}
            cells: list[str] = []
            for sc_id in scenarios:
                cell_data = by_sc.get(sc_id)
                if isinstance(cell_data, dict):
                    wr = float(cell_data.get("win_rate", 0.0)) * 100.0
                    comp = float(cell_data.get("mean_composite_score", 0.0))
                    cells.append(f"{wr:.0f}% ({comp:.1f})")
                else:
                    cells.append("—")
            lines.append(f"| `{row.get('label', '')}` | " + " | ".join(cells) + " |")

    return "\n".join(lines).rstrip() + "\n"


def render_replay_markdown(replay_result: dict[str, Any]) -> str:
    """Render a replay_match verification result as Markdown."""
    matched = bool(replay_result.get("deterministic_match", False))
    status_badge = "PASS (100% Deterministic)" if matched else "FAIL (Divergence Detected)"
    diffs = list(replay_result.get("diffs") or [])

    lines: list[str] = [
        "# 5e 2024 (SRD 5.2) Arena Replay Verification",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Scenario | `{replay_result.get('scenario_id', '')}` |",
        f"| Seed | `{replay_result.get('seed', '')}` |",
        f"| Deterministic Match | **{status_badge}** |",
        f"| Original Digest | `{replay_result.get('original_digest', '')}` |",
        f"| Replayed Digest | `{replay_result.get('replayed_digest', '')}` |",
        "",
        "## Diff Summary",
        "",
    ]
    if not diffs:
        lines.append("No differences detected — replayed state, events, and scorecard are byte-identical.")
    else:
        for d in diffs:
            lines.append(f"- {d}")

    return "\n".join(lines).rstrip() + "\n"
