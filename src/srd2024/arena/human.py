"""Human-in-the-Loop Interactive Tabletop Play primitives for 5e 2024 (SRD 5.2).

Provides:
- `HumanSessionPause`: clean control-flow exception raised on `save` / `quit`.
- `build_human_action_menu(observation, rules)`: deterministic, numbered,
  JSON-serializable legal action menu suitable for both CLI and future web UIs.
- `render_tactical_hud(observation, menu)`: compact ASCII 5-ft battlefield map,
  actor/ally status, strict partial-observability enemy HP bands (never leaking
  hidden enemy exact HP or AC), party `table_talk`, and numbered actions.
- `HumanDriver`: `AgentDriver`-compatible interactive driver supporting menu
  numbers, tactical shortcuts (`e`, `move x y`, `stand`, `say <msg>`,
  `status`/`map`, `help`, `save`, `quit`), and optional Natural-Language-to-Intent
  translation via any `BaseLLMDriver`.

Strictly Python 3.11+ stdlib + `srd2024.engine` / `srd2024.arena.agents` only.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from srd2024.engine.combat import distance_ft
from srd2024.engine.store import RulesStore, strip_prefix

from .agents import (
    _FENCE_RE,
    _MAX_DECISION_TEXT,
    AgentDecision,
    BaseLLMDriver,
    _alive_enemies,
    _all_known_spells,
    _aoe_targets_for_spell,
    _available_monster_actions,
    _available_slots_map,
    _best_kite_position,
    _can_cast_spell_now,
    _condition_set,
    _conscious_allies,
    _downed_allies,
    _ensure_rules,
    _monster_action_range,
    _spell_aoe_spec,
    _spell_effective_range_ft,
    _step_toward,
    _weapon_max_range,
    _json_nesting_exceeds,
    _scan_balanced_json_objects,
    extract_decision_json,
)

_MOVE_CMD_RE = re.compile(r"^(?:move|mv|m)\s+(-?\d+)(?:\s*,\s*|\s+)(-?\d+)$", re.IGNORECASE)
_VS_AC_RE = re.compile(r"\((\d+)\s+vs\s+AC\s+\d+\)")

_SELF_ONLY_SPELLS: frozenset[str] = frozenset(
    {"shield", "mage-armor", "false-life", "mirror-image", "blur"}
)
_MULTI_ALLY_BUFF_SPELLS: frozenset[str] = frozenset({"bless", "aid"})
_SINGLE_ALLY_BUFF_SPELLS: frozenset[str] = frozenset(
    {"shield-of-faith", "barkskin", "haste", "longstrider", "guidance", "resistance"}
)


class HumanSessionPause(Exception):
    """Raised when a human player requests `save` or `quit` during an interactive session."""

    def __init__(self, reason: str = "quit", *, actor_id: str | None = None, message: str = "") -> None:
        self.reason: str = str(reason or "quit")
        self.actor_id: str | None = actor_id
        self.message: str = message or f"Interactive session paused ({self.reason})."
        super().__init__(self.message)


def _fmt_signed(val: int) -> str:
    return f"+{val}" if val >= 0 else str(val)


def _fmt_pos(pos: dict[str, Any] | None) -> str:
    if not isinstance(pos, dict):
        return "(0,0)"
    return f"({int(pos.get('x', 0))},{int(pos.get('y', 0))})"


def _retreat_step(
    origin: dict[str, int],
    enemies: list[dict[str, Any]],
    max_move_ft: int,
) -> dict[str, int] | None:
    """Compute a fallback retreat step away from the nearest enemy when not adjacent."""
    if max_move_ft < 5 or not enemies:
        return None
    nearest = min(enemies, key=lambda e: (distance_ft(origin, e["position"]), str(e.get("id") or "")))
    ox, oy = int(origin["x"]), int(origin["y"])
    ex, ey = int(nearest["position"]["x"]), int(nearest["position"]["y"])
    dx = ox - ex
    dy = oy - ey
    if dx == 0 and dy == 0:
        step_x, step_y = -5, 0
    else:
        step_x = 5 if dx > 0 else (-5 if dx < 0 else 0)
        step_y = 5 if dy > 0 else (-5 if dy < 0 else 0)
    steps = min(4, max_move_ft // 5)
    cand = {"x": ox + step_x * steps, "y": oy + step_y * steps}
    if cand == origin:
        return None
    if distance_ft(origin, cand) > max_move_ft:
        return None
    if distance_ft(cand, nearest["position"]) <= distance_ft(origin, nearest["position"]):
        return None
    return cand


def _sanitize_hook_for_hud(hook: str, enemy_names: list[str]) -> str:
    """Prevent opposing exact AC values in narrative hooks from leaking in the HUD."""
    if not enemy_names or "vs AC" not in hook:
        return hook
    # Only redact AC when an enemy is the target (appears after 'hits ' or 'misses ').
    for ename in enemy_names:
        if f"hits {ename}" in hook or f"misses {ename}" in hook:
            return _VS_AC_RE.sub(r"(roll \1)", hook)
    return hook


def build_human_action_menu(
    observation: dict[str, Any],
    rules: RulesStore,
) -> list[dict[str, Any]]:
    """Generate a deterministic, numbered, JSON-serializable legal action menu.

    Each returned item has:
    - `index`: 1-based integer choice index
    - `category`: `"attack"` | `"spell"` | `"move"` | `"tactical"` | `"turn"`
    - `label`: concise human-readable action description
    - `summary`: secondary tactical info (damage, mastery, slot, range, etc.)
    - `intent`: exact Stage 2 `combat.step()` intent dict
    - `meta`: structured metadata dict for frontend / web UI consumption
    """
    rules = _ensure_rules(rules)
    self_data = observation.get("self") or {}
    legal = observation.get("legal_actions") or {}
    actor_id = str(self_data.get("id") or legal.get("active_actor_id") or "")
    kind = str(self_data.get("kind") or "pc")
    self_pos = self_data.get("position") or {"x": 0, "y": 0}
    sx, sy = int(self_pos.get("x", 0)), int(self_pos.get("y", 0))
    res = self_data.get("turn_resources") or {}
    conds = _condition_set(self_data)
    rem_move = int(self_data.get("movement_remaining_ft", legal.get("movement_remaining_ft", 0)))
    speed = int(self_data.get("speed_ft", 30))

    enemies = sorted(
        _alive_enemies(observation),
        key=lambda e: (distance_ft(self_pos, e["position"]), str(e.get("id") or "")),
    )
    allies_all = [a for a in (observation.get("allies") or []) if a.get("alive", False)]
    conscious_allies = sorted(
        _conscious_allies(observation),
        key=lambda a: (distance_ft(self_pos, a["position"]), str(a.get("id") or "")),
    )
    downed_allies = sorted(
        _downed_allies(observation),
        key=lambda a: (distance_ft(self_pos, a["position"]), str(a.get("id") or "")),
    )

    items: list[dict[str, Any]] = []

    def _add(
        category: str,
        label: str,
        summary: str,
        intent: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> None:
        items.append(
            {
                "index": len(items) + 1,
                "category": category,
                "label": label,
                "summary": summary,
                "intent": intent,
                "meta": dict(meta or {}),
            }
        )

    # 0. Heroic Inspiration Reroll (option #1 whenever eligible)
    if legal.get("can_reroll"):
        hint = legal.get("reroll_hint") or {}
        r_label = str(hint.get("label") or "last d20 roll")
        r_target = hint.get("target_name") or hint.get("target_id")
        prev_total = hint.get("previous_total")
        prev_d20 = hint.get("previous_d20")
        enemy_ids = {str(e.get("id") or "") for e in enemies}
        target_id = str(hint.get("target_id") or "")
        target_ac = hint.get("target_ac")
        if target_id in enemy_ids:
            target_ac = None
        if r_target and prev_total is not None and target_ac is not None:
            menu_label = (
                f"Spend Heroic Inspiration — reroll {r_label} vs {r_target} "
                f"(was {prev_total} vs AC {target_ac})"
            )
        elif r_target and prev_total is not None:
            menu_label = f"Spend Heroic Inspiration — reroll {r_label} vs {r_target} (was {prev_total})"
        elif prev_total is not None:
            menu_label = f"Spend Heroic Inspiration — reroll {r_label} (was {prev_total})"
        else:
            menu_label = f"Spend Heroic Inspiration — reroll {r_label}"
        summary_bits = ["Free (consumes Heroic Inspiration)"]
        if prev_d20 is not None:
            summary_bits.append(f"previous d20: {prev_d20}")
        _add(
            "tactical",
            menu_label,
            "; ".join(summary_bits),
            {"type": "reroll", "actor_id": actor_id},
            {
                "action_kind": "reroll",
                "reroll_hint": {
                    k: v
                    for k, v in (hint.items() if isinstance(hint, dict) else [])
                    if not (k == "target_ac" and target_id in enemy_ids)
                },
            },
        )

    # 1. Stand up from Prone
    can_stand = bool(legal.get("can_stand_up", "prone" in conds and speed > 0 and rem_move >= speed // 2))
    if "prone" in conds and can_stand:
        stand_cost = speed // 2
        _add(
            "move",
            f"Stand up from Prone (costs {stand_cost} ft movement)",
            f"Removes Prone condition; {max(0, rem_move - stand_cost)} ft movement remaining after",
            {
                "type": "move",
                "actor_id": actor_id,
                "stand_up": True,
                "to": {"x": sx, "y": sy},
            },
            {"action_kind": "stand_up", "cost_ft": stand_cost},
        )

    # 2. Weapon Attacks (PC) or Monster Actions (Monster)
    can_attack = bool(res.get("action")) or int(res.get("attacks_remaining", 0)) > 0
    if kind != "monster" and self_data.get("weapons") and enemies:
        if can_attack:
            for wp in self_data.get("weapons") or []:
                slug = str(wp.get("slug") or "")
                wname = str(wp.get("name") or slug)
                normal_r, max_r, is_ranged = _weapon_max_range(wp)
                atk_bonus = int(wp.get("attack_bonus", 0))
                dmg_dice = str(wp.get("damage_dice") or "1")
                dmg_bonus = int(wp.get("damage_bonus", 0))
                dmg_type = strip_prefix(str(wp.get("damage_type") or "bludgeoning"))
                mastery = wp.get("mastery")
                mastery_active = bool(wp.get("mastery_active", False))
                mastery_txt = (
                    f"{mastery} [active]"
                    if (mastery and mastery_active)
                    else (str(mastery) if mastery else "none")
                )
                dmg_str = f"{dmg_dice}{_fmt_signed(dmg_bonus) if dmg_bonus else ''} {dmg_type}"

                for enemy in enemies:
                    dist = distance_ft(self_pos, enemy["position"])
                    if dist > max_r:
                        continue
                    long_tag = " [long range, disadv]" if dist > normal_r else ""
                    m_tag = f", Mastery: {mastery}" if (mastery and mastery_active) else ""
                    _add(
                        "attack",
                        f"Attack {enemy['name']} with {wname} ({dist} ft{m_tag}{long_tag})",
                        f"{_fmt_signed(atk_bonus)} to hit, {dmg_str}, mastery: {mastery_txt}",
                        {
                            "type": "attack",
                            "actor_id": actor_id,
                            "target_id": str(enemy["id"]),
                            "weapon": slug,
                        },
                        {
                            "target_id": str(enemy["id"]),
                            "target_name": str(enemy["name"]),
                            "weapon": slug,
                            "weapon_name": wname,
                            "mastery": mastery if mastery_active else None,
                            "distance_ft": dist,
                            "long_range": dist > normal_r,
                            "is_ranged": is_ranged,
                            "offhand": False,
                        },
                    )

        # Light off-hand / Nick follow-up when a Light weapon attack was already made this turn
        if res.get("light_attack_made") and (res.get("bonus_action") or not res.get("nick_used")):
            for wp in self_data.get("weapons") or []:
                props = {strip_prefix(str(p)) for p in (wp.get("properties") or [])}
                if "light" not in props:
                    continue
                is_nick = wp.get("mastery") == "nick" and bool(wp.get("mastery_active")) and not res.get("nick_used")
                if not is_nick and not res.get("bonus_action"):
                    continue
                slug = str(wp.get("slug") or "")
                wname = str(wp.get("name") or slug)
                normal_r, max_r, _ = _weapon_max_range(wp)
                atk_bonus = int(wp.get("attack_bonus", 0))
                dmg_dice = str(wp.get("damage_dice") or "1")
                dmg_type = strip_prefix(str(wp.get("damage_type") or "piercing"))
                tag = "Nick free off-hand" if is_nick else "Bonus Action off-hand"
                for enemy in enemies:
                    dist = distance_ft(self_pos, enemy["position"])
                    if dist > max_r:
                        continue
                    _add(
                        "attack",
                        f"Off-hand {wname} vs {enemy['name']} ({dist} ft, {tag})",
                        f"{_fmt_signed(atk_bonus)} to hit, {dmg_dice} {dmg_type} ({tag})",
                        {
                            "type": "attack",
                            "actor_id": actor_id,
                            "target_id": str(enemy["id"]),
                            "weapon": slug,
                            "offhand": True,
                        },
                        {
                            "target_id": str(enemy["id"]),
                            "target_name": str(enemy["name"]),
                            "weapon": slug,
                            "weapon_name": wname,
                            "distance_ft": dist,
                            "offhand": True,
                            "nick": is_nick,
                        },
                    )

    if kind == "monster" and res.get("action") and enemies:
        all_m_acts = list(self_data.get("monster_actions") or [])
        avail_m_acts = _available_monster_actions(observation)
        for m_act in avail_m_acts:
            aname = str(m_act.get("name") or "")
            if not aname:
                continue
            max_r, is_ranged, is_save_aoe = _monster_action_range(m_act, all_m_acts)
            in_r = [e for e in enemies if distance_ft(self_pos, e["position"]) <= max_r]
            if not in_r:
                continue
            if is_save_aoe:
                tids = [str(e["id"]) for e in in_r]
                tnames = ", ".join(str(e["name"]) for e in in_r)
                save_info = m_act.get("save") or {}
                s_ab = str(save_info.get("ability") or "dex").upper()
                s_dc = int(save_info.get("dc") or 10)
                _add(
                    "attack",
                    f"Use {aname} (AoE {max_r} ft) hitting {tnames}",
                    f"{s_ab} save DC {s_dc}, {len(tids)} target(s)",
                    {
                        "type": "attack",
                        "actor_id": actor_id,
                        "action": aname,
                        "target_ids": tids,
                    },
                    {
                        "action": aname,
                        "target_ids": tids,
                        "is_aoe": True,
                        "range_ft": max_r,
                    },
                )
            else:
                atk_b = m_act.get("attack_bonus")
                atk_txt = f"{_fmt_signed(int(atk_b))} to hit" if atk_b is not None else "monster action"
                for enemy in in_r:
                    dist = distance_ft(self_pos, enemy["position"])
                    _add(
                        "attack",
                        f"Use {aname} on {enemy['name']} ({dist} ft)",
                        f"{atk_txt}, {'ranged' if is_ranged else 'melee'}",
                        {
                            "type": "attack",
                            "actor_id": actor_id,
                            "action": aname,
                            "target_ids": [str(enemy["id"])],
                        },
                        {
                            "action": aname,
                            "target_id": str(enemy["id"]),
                            "target_name": str(enemy["name"]),
                            "distance_ft": dist,
                            "is_ranged": is_ranged,
                        },
                    )

    # 3. Castable Spells & Cantrips
    if isinstance(self_data.get("spellcasting"), dict):
        slots = _available_slots_map(self_data)
        known_spells = _all_known_spells(observation)
        friendlies_for_aoe = [{"id": actor_id, "position": self_pos}] + list(allies_all)

        for sp_slug in known_spells:
            sp_rec = rules.get_spell(sp_slug)
            if sp_rec is None:
                continue
            can_cast, action_type, slot_level = _can_cast_spell_now(
                sp_slug, sp_rec, self_data, slots, allow_reaction=True
            )
            if not can_cast:
                continue

            sp_name = str(sp_rec.get("name") or sp_slug)
            sp_lvl = int(sp_rec.get("level", 0))
            slot_tag = "Cantrip" if sp_lvl == 0 else f"L{slot_level} slot"
            act_tag = (
                "Reaction"
                if action_type == "reaction"
                else ("Bonus Action" if action_type == "bonus_action" else "Action")
            )
            conc_tag = ", Conc." if sp_rec.get("concentration") else ""
            base_summary = f"{slot_tag}, {act_tag}{conc_tag}"
            eff_range = _spell_effective_range_ft(sp_slug, sp_rec)
            buffs = list(sp_rec.get("buffs") or [])
            resolution = str(sp_rec.get("resolution") or "utility").lower()
            has_teleport = any(isinstance(b, dict) and b.get("kind") == "teleport" for b in buffs)
            has_healing = isinstance(sp_rec.get("healing"), dict)
            has_temp_hp = isinstance(sp_rec.get("temp_hp"), dict)
            has_damage = bool(sp_rec.get("damage"))
            aoe_spec = _spell_aoe_spec(sp_slug, sp_rec)

            # 3a. Teleport (Misty Step)
            if has_teleport:
                max_d = 30
                for b in buffs:
                    if isinstance(b, dict) and b.get("kind") == "teleport":
                        max_d = int(b.get("max_distance_ft", 30))
                dest = _best_kite_position(
                    self_pos, max_d, enemies, conscious_allies, preferred_range_ft=35
                ) or _retreat_step(self_pos, enemies, max_d)
                if dest is None:
                    dest = {"x": sx - 15, "y": sy}
                if dest != self_pos:
                    _add(
                        "spell",
                        f"Cast {sp_name} -> teleport to {_fmt_pos(dest)} ({slot_tag})",
                        f"{base_summary}, up to {max_d} ft",
                        {
                            "type": "cast_spell",
                            "actor_id": actor_id,
                            "spell": sp_slug,
                            "slot_level": slot_level,
                            "destination": dest,
                            "target_ids": [actor_id],
                        },
                        {
                            "spell": sp_slug,
                            "slot_level": slot_level,
                            "action_type": action_type,
                            "destination": dest,
                        },
                    )
                continue

            # 3b. Healing Spells
            if has_healing:
                if sp_slug == "mass-healing-word":
                    heal_targets = [
                        a for a in (downed_allies + conscious_allies)
                        if distance_ft(self_pos, a["position"]) <= eff_range
                        and int(a.get("hp", 0)) < int(a.get("hp_max", 1))
                    ]
                    tids = [str(a["id"]) for a in heal_targets]
                    if int(self_data.get("hp", 0)) < int(self_data.get("hp_max", 1)):
                        tids.append(actor_id)
                    if not tids:
                        tids = [actor_id]
                    _add(
                        "spell",
                        f"Cast {sp_name} on {len(tids)} ally/allies ({slot_tag})",
                        f"{base_summary}, range {eff_range} ft",
                        {
                            "type": "cast_spell",
                            "actor_id": actor_id,
                            "spell": sp_slug,
                            "slot_level": slot_level,
                            "target_ids": tids,
                        },
                        {"spell": sp_slug, "slot_level": slot_level, "target_ids": tids},
                    )
                else:
                    # Offer downed allies first, then wounded conscious allies, then self if wounded
                    heal_candidates: list[dict[str, Any]] = []
                    for a in downed_allies:
                        if distance_ft(self_pos, a["position"]) <= eff_range:
                            heal_candidates.append(a)
                    for a in conscious_allies:
                        if (
                            distance_ft(self_pos, a["position"]) <= eff_range
                            and int(a.get("hp", 0)) < int(a.get("hp_max", 1))
                        ):
                            heal_candidates.append(a)
                    if int(self_data.get("hp", 0)) < int(self_data.get("hp_max", 1)):
                        heal_candidates.append(
                            {
                                "id": actor_id,
                                "name": f"{self_data.get('name', actor_id)} (self)",
                                "position": self_pos,
                                "hp": self_data.get("hp", 0),
                                "hp_max": self_data.get("hp_max", 1),
                            }
                        )
                    if not heal_candidates:
                        # Still allow casting on self or closest ally if player wants
                        heal_candidates.append(
                            {
                                "id": actor_id,
                                "name": f"{self_data.get('name', actor_id)} (self)",
                                "position": self_pos,
                                "hp": self_data.get("hp", 0),
                                "hp_max": self_data.get("hp_max", 1),
                            }
                        )
                    for tgt in heal_candidates:
                        dist = distance_ft(self_pos, tgt["position"])
                        hp_txt = f"{tgt.get('hp', 0)}/{tgt.get('hp_max', 1)} HP"
                        _add(
                            "spell",
                            f"Cast {sp_name} on {tgt['name']} ({hp_txt}, {dist} ft, {slot_tag})",
                            base_summary,
                            {
                                "type": "cast_spell",
                                "actor_id": actor_id,
                                "spell": sp_slug,
                                "slot_level": slot_level,
                                "target_ids": [str(tgt["id"])],
                            },
                            {
                                "spell": sp_slug,
                                "slot_level": slot_level,
                                "target_id": str(tgt["id"]),
                                "distance_ft": dist,
                            },
                        )
                continue

            # 3c. Self-only or Temp-HP Buffs (Shield, Mage Armor, False Life)
            if sp_slug in _SELF_ONLY_SPELLS or has_temp_hp or (
                str(sp_rec.get("range_kind") or "").lower() == "self" and not aoe_spec and not has_damage
            ):
                intent_obj: dict[str, Any] = {
                    "type": "cast_spell",
                    "actor_id": actor_id,
                    "spell": sp_slug,
                    "slot_level": slot_level,
                    "target_ids": [actor_id],
                }
                if action_type == "reaction":
                    intent_obj["as_reaction"] = True
                _add(
                    "spell",
                    f"Cast {sp_name} on self ({slot_tag})",
                    base_summary,
                    intent_obj,
                    {
                        "spell": sp_slug,
                        "slot_level": slot_level,
                        "action_type": action_type,
                        "target_ids": [actor_id],
                    },
                )
                continue

            # 3d. Multi-ally Buffs (Bless, Aid)
            if sp_slug in _MULTI_ALLY_BUFF_SPELLS:
                in_r_allies = [a for a in conscious_allies if distance_ft(self_pos, a["position"]) <= eff_range]
                tids = [str(a["id"]) for a in in_r_allies[:2]] + [actor_id]
                if len(in_r_allies) >= 3:
                    tids = [str(a["id"]) for a in in_r_allies[:3]]
                _add(
                    "spell",
                    f"Cast {sp_name} on party ({len(tids)} targets, {slot_tag})",
                    f"{base_summary}, range {eff_range} ft",
                    {
                        "type": "cast_spell",
                        "actor_id": actor_id,
                        "spell": sp_slug,
                        "slot_level": slot_level,
                        "target_ids": tids,
                    },
                    {"spell": sp_slug, "slot_level": slot_level, "target_ids": tids},
                )
                continue

            # 3e. Single-ally Buffs (Shield of Faith, Haste, Guidance, etc.)
            if sp_slug in _SINGLE_ALLY_BUFF_SPELLS:
                buff_targets: list[tuple[str, str, int]] = [(actor_id, f"{self_data.get('name', actor_id)} (self)", 0)]
                for a in conscious_allies:
                    d = distance_ft(self_pos, a["position"])
                    if d <= eff_range:
                        buff_targets.append((str(a["id"]), str(a["name"]), d))
                for tid, tname, dist in buff_targets[:3]:
                    _add(
                        "spell",
                        f"Cast {sp_name} on {tname} ({dist} ft, {slot_tag})",
                        base_summary,
                        {
                            "type": "cast_spell",
                            "actor_id": actor_id,
                            "spell": sp_slug,
                            "slot_level": slot_level,
                            "target_ids": [tid],
                        },
                        {"spell": sp_slug, "slot_level": slot_level, "target_id": tid},
                    )
                continue

            # 3f. AoE Offensive Spells (Fireball, Shatter, Burning Hands, Lightning Bolt, Spirit Guardians)
            if aoe_spec is not None and enemies:
                safe_hits = _aoe_targets_for_spell(
                    sp_slug, sp_rec, self_pos, enemies, friendlies_for_aoe, avoid_friendlies=True
                )
                if safe_hits:
                    tids = [str(e["id"]) for e in safe_hits]
                    tnames = ", ".join(str(e["name"]) for e in safe_hits)
                    _add(
                        "spell",
                        f"Cast {sp_name} (AoE, safe) hitting {tnames} ({slot_tag})",
                        f"{base_summary}, {len(tids)} target(s), zero friendly fire",
                        {
                            "type": "cast_spell",
                            "actor_id": actor_id,
                            "spell": sp_slug,
                            "slot_level": slot_level,
                            "target_ids": tids,
                        },
                        {
                            "spell": sp_slug,
                            "slot_level": slot_level,
                            "target_ids": tids,
                            "is_aoe": True,
                        },
                    )
                else:
                    # Check if any enemies are in range individually (e.g., if emanation or tight quarters)
                    any_hits = _aoe_targets_for_spell(
                        sp_slug, sp_rec, self_pos, enemies, [], avoid_friendlies=False
                    )
                    if any_hits:
                        tids = [str(e["id"]) for e in any_hits]
                        tnames = ", ".join(str(e["name"]) for e in any_hits)
                        _add(
                            "spell",
                            f"Cast {sp_name} (AoE) hitting {tnames} ({slot_tag})",
                            f"{base_summary}, {len(tids)} target(s)",
                            {
                                "type": "cast_spell",
                                "actor_id": actor_id,
                                "spell": sp_slug,
                                "slot_level": slot_level,
                                "target_ids": tids,
                            },
                            {
                                "spell": sp_slug,
                                "slot_level": slot_level,
                                "target_ids": tids,
                                "is_aoe": True,
                            },
                        )
                continue

            # 3g. Single-Target Offensive / Control Spells (Fire Bolt, Magic Missile, Sacred Flame, Hold Person, etc.)
            if resolution in ("attack", "save", "auto_hit") or has_damage:
                if enemies:
                    for enemy in enemies:
                        dist = distance_ft(self_pos, enemy["position"])
                        if dist > eff_range:
                            continue
                        _add(
                            "spell",
                            f"Cast {sp_name} at {enemy['name']} ({dist} ft, {slot_tag})",
                            f"{base_summary}, {resolution}",
                            {
                                "type": "cast_spell",
                                "actor_id": actor_id,
                                "spell": sp_slug,
                                "slot_level": slot_level,
                                "target_ids": [str(enemy["id"])],
                            },
                            {
                                "spell": sp_slug,
                                "slot_level": slot_level,
                                "target_id": str(enemy["id"]),
                                "target_name": str(enemy["name"]),
                                "distance_ft": dist,
                            },
                        )
                continue

            # 3h. Narrative / Utility Spells
            _add(
                "spell",
                f"Cast {sp_name} ({slot_tag}, utility)",
                base_summary,
                {
                    "type": "cast_spell",
                    "actor_id": actor_id,
                    "spell": sp_slug,
                    "slot_level": slot_level,
                    "target_ids": [actor_id],
                },
                {"spell": sp_slug, "slot_level": slot_level, "utility": True},
            )

    # 4. Smart Tactical Movement Destinations
    crawl_mult = 2 if "prone" in conds else 1
    max_step = rem_move // crawl_mult
    if max_step >= 5 and speed > 0:
        seen_dests: set[tuple[int, int]] = {(sx, sy)}

        # 4a. Close into melee with up to 2 nearest enemies currently > 5 ft away
        if "frightened" not in conds:
            closed_count = 0
            for enemy in enemies:
                if closed_count >= 2:
                    break
                cur_d = distance_ft(self_pos, enemy["position"])
                if cur_d <= 5:
                    continue
                dest = _step_toward(self_pos, enemy["position"], max_step, desired_dist_ft=5)
                key = (int(dest["x"]), int(dest["y"]))
                if key in seen_dests:
                    continue
                seen_dests.add(key)
                new_d = distance_ft(dest, enemy["position"])
                move_cost = distance_ft(self_pos, dest) * crawl_mult
                _add(
                    "move",
                    f"Move to {_fmt_pos(dest)} — close toward {enemy['name']} (now {new_d} ft)",
                    f"Costs {move_cost} ft of {rem_move} ft movement",
                    {"type": "move", "actor_id": actor_id, "to": dest},
                    {
                        "action_kind": "close",
                        "to": dest,
                        "cost_ft": move_cost,
                        "target_id": str(enemy["id"]),
                    },
                )
                closed_count += 1

        # 4b. Kite / Fall Back
        if enemies:
            kite_dest = _best_kite_position(
                self_pos, max_step, enemies, conscious_allies, preferred_range_ft=30
            )
            if kite_dest is None:
                kite_dest = _retreat_step(self_pos, enemies, max_step)
            if kite_dest is not None:
                key = (int(kite_dest["x"]), int(kite_dest["y"]))
                if key not in seen_dests:
                    seen_dests.add(key)
                    min_e_dist = min(distance_ft(kite_dest, e["position"]) for e in enemies)
                    move_cost = distance_ft(self_pos, kite_dest) * crawl_mult
                    _add(
                        "move",
                        f"Move to {_fmt_pos(kite_dest)} — kite / fall back ({min_e_dist} ft from nearest foe)",
                        f"Costs {move_cost} ft of {rem_move} ft movement",
                        {"type": "move", "actor_id": actor_id, "to": kite_dest},
                        {"action_kind": "kite", "to": kite_dest, "cost_ft": move_cost},
                    )

    # 5. Standard Tactical Actions (Dodge, Dash, Disengage, Help)
    if res.get("action"):
        _add(
            "tactical",
            "Dodge (impose Disadvantage on attacks against you)",
            "Action; DEX saves with Advantage until your next turn",
            {"type": "dodge", "actor_id": actor_id},
            {"action_kind": "dodge"},
        )
        if speed > 0:
            _add(
                "tactical",
                f"Dash (+{speed} ft extra movement this turn)",
                "Action; doubles remaining movement budget",
                {"type": "dash", "actor_id": actor_id},
                {"action_kind": "dash", "added_ft": speed},
            )
        _add(
            "tactical",
            "Disengage (move without provoking Opportunity Attacks)",
            "Action; safe movement for rest of turn",
            {"type": "disengage", "actor_id": actor_id},
            {"action_kind": "disengage"},
        )
        if enemies:
            help_target = enemies[0]
            help_ally = conscious_allies[0] if conscious_allies else None
            help_intent: dict[str, Any] = {
                "type": "help",
                "actor_id": actor_id,
                "target_id": str(help_target["id"]),
            }
            if help_ally is not None:
                help_intent["ally_id"] = str(help_ally["id"])
                ally_note = f" for {help_ally['name']}"
            else:
                ally_note = ""
            _add(
                "tactical",
                f"Help{ally_note} against {help_target['name']} (grant Advantage)",
                "Action; next ally attack against target has Advantage",
                help_intent,
                {
                    "action_kind": "help",
                    "target_id": str(help_target["id"]),
                    "ally_id": str(help_ally["id"]) if help_ally else None,
                },
            )

    # 6. End Turn (always available as final option)
    _add(
        "turn",
        "End Turn",
        "Pass control to the next combatant in initiative order",
        {"type": "end_turn", "actor_id": actor_id},
        {"action_kind": "end_turn"},
    )

    return items


def _assign_map_tokens(entities: list[dict[str, Any]]) -> dict[str, str]:
    """Deterministically assign unique single-character ASCII map tokens."""
    used: set[str] = set()
    tokens: dict[str, str] = {}
    fallback_pool = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz")

    for ent in entities:
        cid = str(ent["id"])
        name = str(ent.get("name") or cid).strip()
        letters = [ch for ch in name if ch.isalpha()]
        first = letters[0].upper() if letters else "X"
        candidates: list[str] = [first]
        # If name ends with a single letter/digit like "Goblin Scout B" or "goblin-2", try it too
        parts = re.split(r"[\s_-]+", name)
        if len(parts) > 1 and len(parts[-1]) == 1 and parts[-1].isalnum():
            candidates.append(parts[-1].upper())
        candidates.append(first.lower())
        for ch in letters[1:]:
            candidates.append(ch.upper())
        candidates.extend(fallback_pool)

        chosen = "?"
        for cand in candidates:
            if cand not in used:
                chosen = cand
                used.add(cand)
                break
        tokens[cid] = chosen
    return tokens


def _render_ascii_battlefield(
    self_data: dict[str, Any],
    allies: list[dict[str, Any]],
    enemies: list[dict[str, Any]],
) -> list[str]:
    """Render a compact 5-ft grid ASCII battlefield map + coordinate legend."""
    actor_id = str(self_data.get("id") or "")
    displayed: list[dict[str, Any]] = [
        {
            "id": actor_id,
            "name": str(self_data.get("name") or actor_id),
            "position": self_data.get("position") or {"x": 0, "y": 0},
            "role": "YOU",
            "alive": True,
        }
    ]
    for a in allies:
        if a.get("alive", False):
            displayed.append(
                {
                    "id": str(a["id"]),
                    "name": str(a.get("name") or a["id"]),
                    "position": a.get("position") or {"x": 0, "y": 0},
                    "role": "DOWN" if a.get("unconscious") or int(a.get("hp", 1)) == 0 else "ALLY",
                    "alive": True,
                }
            )
    for e in enemies:
        if e.get("alive", False) and e.get("hp_band") != "defeated":
            displayed.append(
                {
                    "id": str(e["id"]),
                    "name": str(e.get("name") or e["id"]),
                    "position": e.get("position") or {"x": 0, "y": 0},
                    "role": f"FOE:{e.get('hp_band', 'unharmed')}",
                    "alive": True,
                }
            )

    tokens = _assign_map_tokens(displayed)

    # Snap coordinates to 5-ft cells
    xs = [int(ent["position"]["x"]) // 5 * 5 for ent in displayed]
    ys = [int(ent["position"]["y"]) // 5 * 5 for ent in displayed]
    min_x = min(xs) - 5
    max_x = max(xs) + 5
    min_y = min(ys) - 5
    max_y = max(ys) + 5

    # Cap grid dimensions so far-apart combatants stay compact in terminals
    cols = list(range(min_x, max_x + 1, 5))
    rows = list(range(min_y, max_y + 1, 5))
    if len(cols) > 18 or len(rows) > 12:
        # Show compact coordinate table only when span is huge
        lines = ["Battlefield (5-ft grid — wide span):"]
        for ent in displayed:
            tok = tokens[ent["id"]]
            lines.append(f"  [{tok}] {ent['name']} @ {_fmt_pos(ent['position'])} ({ent['role']})")
        return lines

    cell_map: dict[tuple[int, int], str] = {}
    for ent in displayed:
        cx = int(ent["position"]["x"]) // 5 * 5
        cy = int(ent["position"]["y"]) // 5 * 5
        tok = tokens[ent["id"]]
        if (cx, cy) in cell_map:
            cell_map[(cx, cy)] = "*"
        else:
            cell_map[(cx, cy)] = tok

    lines: list[str] = ["Battlefield Map (5-ft squares):"]
    x_hdr = "      " + "".join(f"{x:>3}" for x in cols)
    lines.append(x_hdr)
    lines.append("     +" + "---" * len(cols) + "+")
    for y in rows:
        row_cells = "".join(f" {cell_map.get((x, y), '.')} " for x in cols)
        lines.append(f"y={y:>3}|{row_cells}|")
    lines.append("     +" + "---" * len(cols) + "+")

    legend_bits = [
        f"[{tokens[ent['id']]}]={ent['name']} {_fmt_pos(ent['position'])} ({ent['role']})"
        for ent in displayed
    ]
    lines.append("Legend: " + " | ".join(legend_bits))
    return lines


def render_tactical_hud(
    observation: dict[str, Any],
    menu: list[dict[str, Any]],
    *,
    nl_enabled: bool = False,
) -> str:
    """Render a compact ASCII 5-ft battlefield HUD and numbered action menu.

    Strictly enforces partial observability: enemies show ONLY public fields
    (`hp_band`, `conditions`, `visible_effects`, `position`, `distance_ft`) —
    NEVER hidden exact enemy HP or AC.
    """
    rnd = int(observation.get("round", 1))
    sc = observation.get("scenario") or {}
    sc_name = str(sc.get("name") or sc.get("id") or "Arena Encounter")
    self_data = observation.get("self") or {}
    allies = list(observation.get("allies") or [])
    enemies = list(observation.get("enemies") or [])
    board = list(observation.get("party_blackboard") or [])
    recent_events = list(observation.get("recent_events") or [])
    retry_fb = observation.get("retry_feedback")

    actor_name = str(self_data.get("name") or self_data.get("id") or "Active Combatant")
    actor_id = str(self_data.get("id") or "")
    hp = int(self_data.get("hp", 0))
    hp_max = int(self_data.get("hp_max", 0))
    temp_hp = int(self_data.get("temp_hp", 0))
    eff_ac = int(self_data.get("effective_ac", self_data.get("ac", 10)))
    rem_move = int(self_data.get("movement_remaining_ft", 0))
    speed = int(self_data.get("speed_ft", 30))
    pos = self_data.get("position") or {"x": 0, "y": 0}
    res = self_data.get("turn_resources") or {}
    conds = list(self_data.get("conditions") or [])
    conc = self_data.get("concentration")
    sc_data = self_data.get("spellcasting")

    hp_str = f"{hp}/{hp_max}" + (f" (+{temp_hp} tmp)" if temp_hp > 0 else "")
    res_bits: list[str] = []
    if res.get("action"):
        res_bits.append("Action")
    elif int(res.get("attacks_remaining", 0)) > 0:
        res_bits.append(f"Attacks:{int(res['attacks_remaining'])}")
    if res.get("bonus_action"):
        res_bits.append("Bonus")
    if res.get("reaction"):
        res_bits.append("Reaction")
    res_str = ", ".join(res_bits) if res_bits else "none"

    slots_str = ""
    if isinstance(sc_data, dict):
        slots = _available_slots_map(self_data)
        if slots:
            slots_str = " | Slots: " + " ".join(f"L{k}:{v}" for k, v in sorted(slots.items()))
        else:
            slots_str = " | Slots: 0"

    cond_str = ", ".join(str(c) for c in conds) if conds else "none"
    conc_str = f" | Conc: {conc.get('spell')}" if isinstance(conc, dict) and conc.get("spell") else ""
    legal = observation.get("legal_actions") or {}
    has_heroic_insp = bool(self_data.get("heroic_inspiration", False) or legal.get("can_reroll", False))
    insp_str = " | Heroic Inspiration: Ready" if has_heroic_insp else ""

    lines: list[str] = [
        "=" * 72,
        f"5e 2024 (SRD 5.2) | {sc_name} | Round {rnd} | Active: {actor_name} ({actor_id})",
        "-" * 72,
    ]

    # Battlefield Map
    lines.extend(_render_ascii_battlefield(self_data, allies, enemies))
    lines.append("-" * 72)

    # Active Actor Status
    lines.append(
        f"YOU: {actor_name} | HP {hp_str} | AC {eff_ac} | Pos {_fmt_pos(pos)} | Move {rem_move}/{speed} ft"
    )
    lines.append(
        f"     Resources: [{res_str}] | Cond: {cond_str}{conc_str}{slots_str}{insp_str}"
    )

    # Allies
    if allies:
        lines.append("ALLIES:")
        for a in allies:
            a_status = "DEFEATED" if not a.get("alive") else ("DOWN" if a.get("unconscious") or int(a.get("hp", 0)) == 0 else "OK")
            a_conds = ", ".join(str(c) for c in (a.get("conditions") or []))
            c_bit = f" [{a_conds}]" if a_conds else ""
            lines.append(
                f"  - {a.get('name', a['id'])} ({a['id']}): HP {a.get('hp', 0)}/{a.get('hp_max', 0)} "
                f"AC {a.get('ac', '?')} @ {_fmt_pos(a.get('position'))} ({a.get('distance_ft', 0)} ft) [{a_status}]{c_bit}"
            )

    # Visible Enemies (STRICT PARTIAL OBSERVABILITY — HP bands only, NEVER exact HP or AC!)
    if enemies:
        lines.append("VISIBLE ENEMIES:")
        for e in enemies:
            band = str(e.get("hp_band") or "unharmed")
            e_conds = list(e.get("conditions") or [])
            e_effs = list(e.get("visible_effects") or [])
            extra_bits = [f"cond:{','.join(e_conds)}"] if e_conds else []
            if e_effs:
                extra_bits.append(f"fx:{','.join(e_effs)}")
            extra = f" ({'; '.join(extra_bits)})" if extra_bits else ""
            lines.append(
                f"  - {e.get('name', e['id'])} ({e['id']}): [{band}] "
                f"@ {_fmt_pos(e.get('position'))} ({e.get('distance_ft', 0)} ft){extra}"
            )

    # Recent Party Table Talk
    if board:
        lines.append("PARTY TABLE TALK:")
        for msg in board[-4:]:
            lines.append(
                f"  [R{msg.get('round', '?')}] {msg.get('actor_name', msg.get('actor_id', '?'))}: "
                f"\"{msg.get('message', '')}\""
            )

    # Recent Events (sanitized so hidden enemy AC never leaks)
    if recent_events:
        enemy_names = [str(e.get("name") or "") for e in enemies if e.get("name")]
        lines.append("RECENT EVENTS:")
        for ev in recent_events[-4:]:
            lines.append(f"  * {_sanitize_hook_for_hud(str(ev), enemy_names)}")

    # Retry Feedback
    if retry_fb:
        lines.append(f"! RETRY FEEDBACK: {retry_fb}")

    # Numbered Action Menu
    lines.append("-" * 72)
    lines.append("LEGAL ACTIONS:")
    cat_badges = {
        "attack": "ATK",
        "spell": "SPL",
        "move": "MOV",
        "tactical": "ACT",
        "turn": "END",
    }
    for item in menu:
        badge = cat_badges.get(str(item.get("category") or ""), "OPT")
        lines.append(f"  [{item['index']:>2}] [{badge}] {item['label']}")
        if item.get("summary"):
            lines.append(f"           {item['summary']}")

    nl_hint = " | or plain English" if nl_enabled else ""
    lines.append("-" * 72)
    lines.append(
        f"Commands: <1..{len(menu)}> | r(eroll) | e(nd) | move <x> <y> | stand | say <msg> | status | map | help | save | quit{nl_hint}"
    )
    return "\n".join(lines)


def _build_nl_system_prompt() -> str:
    return (
        "You translate one line typed by a human player into a 5e 2024 (SRD 5.2) combat intent for their "
        "character. The rules engine resolves everything; you NEVER roll dice or invent results.\n\n"
        "Only translate an explicit order the character can carry out this turn. Match one of the legal menu "
        "intents, or emit a valid Stage 2 intent (`move`, `attack`, `cast_spell`, `dodge`, `dash`, `disengage`, "
        "`help`, `reroll`, `end_turn`) with `actor_id` set to `self_id`.\n\n"
        "Do NOT take an action when the line:\n"
        "- is not an order (a remark, celebration, reaction, or question, e.g. \"Got him!\"),\n"
        "- is ambiguous between different actions or targets, or\n"
        "- asks for something the character cannot do now (no such action, out of range, resource spent).\n"
        "Never substitute a different action for the one the player asked for, even a reasonable one: the player "
        "decides. Instead reply with intent null and a short message to the player that says what you understood "
        "and, if the request isn't possible, names the closest legal options from the menu by number.\n\n"
        "Never speak for the player: do not write party chat or dialogue (the player uses `say <msg>` for that).\n\n"
        "Respond with ONLY one JSON object, either\n"
        '{"intent": {...}, "rationale": "<short translation note>"}\n'
        "or\n"
        '{"intent": null, "clarify": "<one or two sentences to the player>"}'
    )


def _build_nl_user_prompt(
    user_text: str,
    observation: dict[str, Any],
    menu: list[dict[str, Any]],
) -> str:
    compact_menu = [
        {"index": m["index"], "label": m["label"], "intent": m["intent"]}
        for m in menu
    ]
    payload = {
        "player_command": user_text,
        "self_id": (observation.get("self") or {}).get("id"),
        "self_position": (observation.get("self") or {}).get("position"),
        "movement_remaining_ft": (observation.get("self") or {}).get("movement_remaining_ft"),
        "enemies": [
            {"id": e["id"], "name": e["name"], "position": e["position"], "distance_ft": e["distance_ft"]}
            for e in (observation.get("enemies") or [])
            if e.get("alive")
        ],
        # Own side only (fully visible to the player), so orders like "move next to Talia" or "heal Lyra" resolve.
        "allies": [
            {"id": a["id"], "name": a["name"], "position": a["position"], "distance_ft": a.get("distance_ft"),
             "hp": a.get("hp"), "hp_max": a.get("hp_max"), "unconscious": bool(a.get("unconscious"))}
            for a in (observation.get("allies") or [])
            if a.get("alive")
        ],
        "menu": compact_menu,
    }
    return (
        "Translate the player's line into one JSON object: an intent, or intent null with a clarify message.\n"
        f"CONTEXT:\n{json.dumps(payload, separators=(',', ':'))}\n\n"
        "Respond with ONLY the JSON object."
    )


_NL_CLARIFY_MAX = 600
_NL_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩﻿]")


@dataclass
class NLClarification:
    """The translator declined to act: the line wasn't an order, was ambiguous, or asked for something impossible."""

    message: str
    raw_response: str
    usage: dict[str, int]


def _extract_nl_clarification(raw_text: str) -> str | None:
    """Return the `clarify` message from a `{"intent": null, "clarify": "..."}` reply, else None."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    text = raw_text[:_MAX_DECISION_TEXT]
    if _json_nesting_exceeds(text.strip()):
        return None
    candidates = [text.strip()]
    candidates += [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    candidates += _scan_balanced_json_objects(text)
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and "intent" in obj and obj["intent"] is None:
            msg = obj.get("clarify")
            if isinstance(msg, str):
                msg = " ".join(_NL_CONTROL_RE.sub(" ", msg).split())
                if msg:
                    return msg[: _NL_CLARIFY_MAX - 1] + "…" if len(msg) > _NL_CLARIFY_MAX else msg
    return None


def _translate_nl_to_decision(
    user_text: str,
    observation: dict[str, Any],
    menu: list[dict[str, Any]],
    nl_driver: BaseLLMDriver,
) -> AgentDecision | NLClarification:
    """Translate plain English via `nl_driver` into a validated `AgentDecision`, or an `NLClarification` (no action).

    The translator never writes party chat for the player: any `table_talk` in the reply is ignored, so only the
    player's own `say` lines reach allies (and the coordination score).
    """
    sys_prompt = _build_nl_system_prompt()
    usr_prompt = _build_nl_user_prompt(user_text, observation, menu)
    url, headers, payload = nl_driver._build_request(sys_prompt, usr_prompt)
    resp_json = nl_driver._transport(url, headers, payload, nl_driver.timeout_s)
    raw_text, usage = nl_driver._parse_response(resp_json)
    try:
        parsed = extract_decision_json(raw_text)
    except ValueError:
        clarify = _extract_nl_clarification(raw_text)
        if clarify is not None:
            return NLClarification(message=clarify, raw_response=raw_text, usage=usage)
        raise

    intent = copy.deepcopy(parsed["intent"])
    actor_id = str((observation.get("self") or {}).get("id") or "")
    if actor_id and not intent.get("actor_id"):
        intent["actor_id"] = actor_id

    rationale = str(parsed.get("rationale") or f"NL translation of: {user_text}")
    return AgentDecision(
        intent=intent,
        rationale=rationale,
        table_talk=None,
        raw_response=raw_text,
        usage=usage,
    )


def _help_text(nl_enabled: bool) -> str:
    lines = [
        "Interactive Tabletop Commands:",
        "  <number>     Choose a numbered legal action from the menu (1..N)",
        "  r | reroll   Spend Heroic Inspiration to reroll your last missed d20",
        "  e | end      End your current turn",
        "  move <x> <y> Move to a specific 5-ft grid coordinate (e.g. 'move 15 0')",
        "  stand        Stand up from Prone (costs half movement)",
        "  say <msg>    Queue party table_talk to broadcast with your next action",
        "  status | map Re-display the ASCII tactical HUD and action menu",
        "  help | ?     Show this command reference",
        "  save         Pause and save a resumable checkpoint JSON",
        "  quit         Pause and exit (saves checkpoint if --save was provided)",
    ]
    if nl_enabled:
        lines.append("  <English>    Natural-language mode active: type an order in plain English to translate it to an intent")
        lines.append("               (remarks, ambiguous or impossible orders take no action; use 'say' to talk to allies)")
    return "\n".join(lines)


class HumanDriver:
    """Interactive `AgentDriver` for Human-in-the-Loop 5e 2024 tabletop play.

    Supports injectable `input_fn` and `output_fn` for scripted non-TTY testing
    and future frontend adapters, plus optional Natural-Language-to-Intent
    translation via any `BaseLLMDriver`.
    """

    name: str = "human"

    def __init__(
        self,
        *,
        rules: RulesStore | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], Any] = print,
        nl_driver: BaseLLMDriver | None = None,
        show_hud: bool = True,
        pause_on_eof: bool = False,
    ) -> None:
        self._rules: RulesStore = _ensure_rules(rules)
        self._input_fn: Callable[[str], str] = input_fn
        self._output_fn: Callable[[str], Any] = output_fn
        self._nl_driver: BaseLLMDriver | None = nl_driver
        self._show_hud: bool = bool(show_hud)
        self._pause_on_eof: bool = bool(pause_on_eof)
        self._pending_talk: list[str] = []
        self._rejected_inputs: list[dict[str, Any]] = []

    def reset(self, seed: int | None = None) -> None:
        self._pending_talk.clear()
        self._rejected_inputs.clear()
        if self._nl_driver is not None:
            self._nl_driver.reset(seed=seed)

    def drain_rejected_inputs(self) -> list[dict[str, Any]]:
        """Return and clear all rejected human CLI inputs recorded since the last drain."""
        drained = list(self._rejected_inputs)
        self._rejected_inputs.clear()
        return drained

    def _record_rejected(
        self,
        observation: dict[str, Any],
        actor_id: str,
        text: str,
        reason: str,
    ) -> None:
        self._rejected_inputs.append(
            {
                "round": observation.get("round"),
                "actor_id": actor_id,
                "input": text,
                "reason": reason,
            }
        )

    def _consume_talk(self, extra_talk: str | None = None) -> str | None:
        parts = [p for p in self._pending_talk if p]
        self._pending_talk.clear()
        if extra_talk and extra_talk.strip():
            parts.append(extra_talk.strip())
        return " ".join(parts) if parts else None

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        self_data = observation.get("self") or {}
        actor_id = str(self_data.get("id") or (observation.get("legal_actions") or {}).get("active_actor_id") or "")
        actor_name = str(self_data.get("name") or actor_id)
        self_pos = self_data.get("position") or {"x": 0, "y": 0}
        sx, sy = int(self_pos.get("x", 0)), int(self_pos.get("y", 0))

        menu = build_human_action_menu(observation, self._rules)
        if self._show_hud:
            hud = render_tactical_hud(observation, menu, nl_enabled=self._nl_driver is not None)
            self._output_fn(hud)

        prompt = f"[{actor_name}] Choose action (1-{len(menu)} or command): "
        while True:
            try:
                raw_line = self._input_fn(prompt)
            except KeyboardInterrupt as exc:
                raise HumanSessionPause("quit", actor_id=actor_id, message="Interrupted by player.") from exc
            except EOFError as exc:
                if self._pause_on_eof:
                    raise HumanSessionPause("eof", actor_id=actor_id, message="Input EOF reached.") from exc
                self._output_fn(f"[{actor_name}] Input EOF reached — ending turn.")
                return AgentDecision(
                    intent={"type": "end_turn", "actor_id": actor_id},
                    rationale="Input EOF reached; ending turn cleanly.",
                    table_talk=self._consume_talk(),
                )

            text = str(raw_line or "").strip()
            if not text:
                self._output_fn(f"Please enter a number (1-{len(menu)}) or 'help'.")
                continue

            low = text.lower()

            # 1. Exact numeric selection
            if re.fullmatch(r"\d+", text):
                idx = int(text)
                if 1 <= idx <= len(menu):
                    choice = menu[idx - 1]
                    return AgentDecision(
                        intent=copy.deepcopy(choice["intent"]),
                        rationale=f"Human chose [{idx}]: {choice['label']}",
                        table_talk=self._consume_talk(),
                    )
                self._record_rejected(
                    observation,
                    actor_id,
                    text,
                    f"Out-of-range menu selection '{idx}' (valid: 1..{len(menu)}).",
                )
                self._output_fn(f"Invalid selection '{idx}'. Enter a number between 1 and {len(menu)}.")
                continue

            # 2. Heroic Inspiration reroll shortcut
            if low in ("r", "reroll", "inspiration", "heroic inspiration", "heroic_inspiration"):
                return AgentDecision(
                    intent={"type": "reroll", "actor_id": actor_id},
                    rationale="Human spent Heroic Inspiration to reroll.",
                    table_talk=self._consume_talk(),
                )

            # 3. End turn shortcut
            if low in ("e", "end", "end_turn", "end turn", "pass"):
                return AgentDecision(
                    intent={"type": "end_turn", "actor_id": actor_id},
                    rationale="Human ended turn.",
                    table_talk=self._consume_talk(),
                )

            # 4. Stand up shortcut
            if low in ("stand", "stand up", "stand_up"):
                return AgentDecision(
                    intent={
                        "type": "move",
                        "actor_id": actor_id,
                        "stand_up": True,
                        "to": {"x": sx, "y": sy},
                    },
                    rationale="Human stood up from Prone.",
                    table_talk=self._consume_talk(),
                )

            # 5. Custom coordinate move
            mv_match = _MOVE_CMD_RE.match(text)
            if mv_match:
                tx, ty = int(mv_match.group(1)), int(mv_match.group(2))
                return AgentDecision(
                    intent={"type": "move", "actor_id": actor_id, "to": {"x": tx, "y": ty}},
                    rationale=f"Human moved to ({tx}, {ty}).",
                    table_talk=self._consume_talk(),
                )
            if low.startswith(("move ", "mv ", "m ")) or low in ("move", "mv", "m"):
                self._record_rejected(
                    observation,
                    actor_id,
                    text,
                    "Invalid move syntax; expected 'move <x> <y>'.",
                )
                self._output_fn("Usage: move <x> <y> (integer 5-ft coordinates, e.g. 'move 15 0').")
                continue

            # 6. Queue party table_talk before acting
            if low.startswith("say ") or low == "say":
                msg = text[3:].strip()
                if not msg:
                    self._output_fn("Usage: say <message to your side>")
                    continue
                self._pending_talk.append(msg)
                self._output_fn(f'[Table Talk queued]: "{msg}" (now choose your action)')
                continue

            # 7. Status / Map redraw
            if low in ("status", "map", "hud"):
                self._output_fn(render_tactical_hud(observation, menu, nl_enabled=self._nl_driver is not None))
                continue

            # 8. Help
            if low in ("help", "?", "h"):
                self._output_fn(_help_text(self._nl_driver is not None))
                continue

            # 9. Save / Quit session pause
            if low in ("save", "quit", "exit", "q"):
                reason = "save" if low == "save" else "quit"
                raise HumanSessionPause(
                    reason,
                    actor_id=actor_id,
                    message=f"Session paused by player command '{reason}'.",
                )

            # 10. Natural-Language-to-Intent translation (optional LLM driver)
            if self._nl_driver is not None:
                try:
                    nl_dec = _translate_nl_to_decision(text, observation, menu, self._nl_driver)
                except Exception as exc:
                    self._record_rejected(
                        observation,
                        actor_id,
                        text,
                        f"Natural-language translation failed: {exc}",
                    )
                    self._output_fn(
                        f"Natural-language translation failed: {exc}. "
                        f"Try rephrasing or pick a menu option (1-{len(menu)})."
                    )
                    continue
                if isinstance(nl_dec, NLClarification):
                    # No action: the line wasn't an order, was ambiguous, or asked for something impossible.
                    self._record_rejected(observation, actor_id, text, f"No action taken: {nl_dec.message}")
                    self._output_fn(f"[NL] No action taken. {nl_dec.message}")
                    continue
                self._output_fn(
                    f"[NL -> Intent] {json.dumps(nl_dec.intent, separators=(',', ':'))}"
                )
                return AgentDecision(
                    intent=nl_dec.intent,
                    rationale=nl_dec.rationale,
                    table_talk=self._consume_talk(),  # only the player's own `say` lines
                    raw_response=nl_dec.raw_response,
                    usage=nl_dec.usage,
                )

            # 11. Clean re-prompt on unrecognized input
            self._record_rejected(
                observation,
                actor_id,
                text,
                "Unrecognized CLI command.",
            )
            self._output_fn(
                f"Unrecognized input {text!r}. Enter 1-{len(menu)}, 'r', 'e', 'move <x> <y>', "
                "'say <msg>', 'status', 'help', 'save', or 'quit'."
            )


__all__ = [
    "HumanDriver",
    "HumanSessionPause",
    "build_human_action_menu",
    "render_tactical_hud",
]
