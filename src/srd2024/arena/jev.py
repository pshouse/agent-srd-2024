"""TypeSafe AI Jev as an Arena party driver (`typesafe:<model>`, e.g. `typesafe:jev-1.14.0`).

Jev is a calibrated decision model, not a chat model. It answers typed questions about a `state`. A Choice
question returns a probability for every named option (`POST /v1/systemone`, docs.typesafe.ai). Each decision
works like this:

1. `enumerate_candidates()` builds a list of concrete intents for the active PC from its own observation:
   weapon attacks and spells on targets in range, moves, Dodge/Dash/Disengage and `end_turn`.
2. Jev gets the same observation JSON the LLM drivers see, plus one Choice question over those candidates.
3. The driver plays the most probable candidate. If the engine rejects it, the retry takes the next most probable
   candidate from the same answer, with no second API call.

Jev writes no table talk, so it will score lower on coordination. Moves are limited to a few generated
destinations. On a monster seat the driver hands off to `HeuristicTacticalDriver`.
"""

from __future__ import annotations

import json
import os
from typing import Any

from srd2024.engine.combat import distance_ft, spell_target_allowed
from srd2024.engine.store import RulesStore

from .agents import (
    AgentDecision,
    BaseLLMDriver,
    HeuristicTacticalDriver,
    _alive_enemies,
    _aoe_targets_for_spell,
    _available_slots_map,
    _best_kite_position,
    _occupied_squares,
    _can_cast_spell_now,
    _conscious_allies,
    _downed_allies,
    _ensure_rules,
    _reject_control_chars_in_secret,
    _spell_aoe_spec,
    _spell_effective_range_ft,
    _step_toward,
    _weapon_max_range,
    scrub_secrets,
    strip_prefix,
)

MAX_CANDIDATES = 80  # Choice allows 255; keep requests small and the option list readable.
QUESTION_ID = "next_action"

INSTRUCTIONS = (
    "You choose the next action for the active player character (state.self) in a 5e 2024 (SRD 5.2) combat. "
    "The party wins when every enemy is defeated. Choose the option that most improves the party's chance to win "
    "this fight. Use damage types the target isn't immune to; enemy stat blocks are public SRD monsters "
    "(state.enemies[].source_id). Get 0-HP allies back up. Don't stand within one cone or burst of several allies "
    "against breath weapons. Use the Action before ending the turn. A turn can include several options in a row "
    "(move, then attack, then more Extra Attacks); you are choosing only the next one."
)


def _enemy_label(e: dict[str, Any], self_pos: dict[str, int]) -> str:
    band = e.get("hp_band") or ("bloodied" if e.get("bloodied") else "unharmed")
    return f"{e.get('name', e['id'])} ({e['id']}, {distance_ft(self_pos, e['position'])} ft away, {band})"


def _ally_label(a: dict[str, Any], self_pos: dict[str, int]) -> str:
    return (
        f"{a.get('name', a['id'])} ({a['id']}, {distance_ft(self_pos, a['position'])} ft away, "
        f"{a.get('hp', '?')}/{a.get('hp_max', '?')} HP)"
    )


def _conditions(me: dict[str, Any]) -> dict[str, set[str]]:
    """Map each of the actor's conditions to the ids of its sources (empty set when the source is unknown)."""
    out: dict[str, set[str]] = {}
    for c in me.get("conditions") or []:
        if isinstance(c, dict):
            out.setdefault(strip_prefix(str(c.get("condition") or "")), set()).update(
                {str(c["source_id"])} if c.get("source_id") else set()
            )
        else:
            out.setdefault(strip_prefix(str(c)), set())
    return out


def enumerate_candidates(observation: dict[str, Any], rules: RulesStore) -> list[tuple[str, str, dict[str, Any]]]:
    """Return `(option_name, description, intent)` candidates for the active PC. Names are unique."""
    me = observation["self"]
    actor_id = str(me["id"])
    pos = me["position"]
    res = me.get("turn_resources") or {}
    legal = observation.get("legal_actions") or {}
    enemies = _alive_enemies(observation)
    allies = _conscious_allies(observation)
    downed = _downed_allies(observation)
    out: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[str] = set()

    def add(name: str, desc: str, intent: dict[str, Any]) -> None:
        if name not in seen:
            seen.add(name)
            out.append((name, desc, intent))

    # Weapon attacks (Attack action or remaining Extra Attacks), and the off-hand Light attack.
    can_attack = bool(res.get("action")) or int(res.get("attacks_remaining", 0) or 0) > 0
    can_offhand = (
        "offhand_attack" in (legal.get("bonus_actions") or [])
        and bool(res.get("light_attack_made"))
        and not res.get("nick_used")  # the extra Light attack is once per turn, with or without Nick
    )
    for w in me.get("weapons") or []:
        slug = str(w.get("slug") or w.get("id") or w.get("name") or "")
        if not slug:
            continue
        normal, long_r, _ = _weapon_max_range(w)
        dmg = f"{w.get('damage_dice', '?')}+{w.get('damage_bonus', 0)} {strip_prefix(str(w.get('damage_type') or ''))}"
        mastery = f", {w['mastery']} mastery" if w.get("mastery") and w.get("mastery_active") else ""
        for e in enemies:
            d = distance_ft(pos, e["position"])
            if d > long_r:
                continue
            far = " at long range (Disadvantage)" if d > normal else ""
            if can_attack:
                add(
                    f"attack {e['id']} with {slug}",
                    f"Weapon attack on {_enemy_label(e, pos)} with {w.get('name', slug)} ({dmg}{mastery}){far}.",
                    {"type": "attack", "actor_id": actor_id, "target_id": e["id"], "weapon": slug, "offhand": False},
                )
            if can_offhand and "light" in (w.get("properties") or []) and d <= normal:
                add(
                    f"off-hand attack {e['id']} with {slug}",
                    f"Extra Light-weapon (off-hand) attack on {_enemy_label(e, pos)} with {w.get('name', slug)} "
                    f"({dmg}{mastery}). Once per turn.",
                    {"type": "attack", "actor_id": actor_id, "target_id": e["id"], "weapon": slug, "offhand": True},
                )

    # Spells the actor can cast now (reaction spells are cast by the engine).
    sc = me.get("spellcasting") if isinstance(me.get("spellcasting"), dict) else {}
    slots = _available_slots_map(me)
    spell_slugs: list[str] = []
    for raw in list(sc.get("cantrips") or []) + list(sc.get("prepared_spells") or []):
        s = strip_prefix(str(raw))
        if s and s not in spell_slugs:
            spell_slugs.append(s)
    friendlies = allies + [{"id": actor_id, "position": pos}]
    for slug in spell_slugs:
        rec = rules.get_spell(slug)
        if not isinstance(rec, dict) or str(rec.get("coverage") or "narrative") == "narrative":
            continue
        ok, action_type, slot = _can_cast_spell_now(slug, rec, me, slots)
        if not ok or action_type == "reaction":
            continue
        name = rec.get("name", slug)
        base: dict[str, Any] = {"type": "cast_spell", "actor_id": actor_id, "spell": slug}
        slot_txt = ""
        if int(rec.get("level", 0)) >= 1:
            base["slot_level"] = slot
            slot_txt = f" with a level {slot} slot"
        econ = "Bonus Action" if action_type == "bonus_action" else "Action"
        conc = " Concentration (ends any current concentration)." if rec.get("concentration") else ""
        dmg_types = sorted({strip_prefix(str(p.get("type") or "")) for p in rec.get("damage") or []})
        dmg_txt = f" Deals {'/'.join(dmg_types)} damage." if dmg_types else ""
        rng = _spell_effective_range_ft(slug, rec)
        mode = str((rec.get("targeting") or {}).get("mode") or "single_target")
        max_t = int((rec.get("targeting") or {}).get("max_targets") or 1)
        heal = bool(rec.get("healing"))
        head = f"Cast {name}{slot_txt} ({econ})"

        if mode == "self":
            if slug == "misty-step":
                dest = _best_kite_position(pos, 30, enemies, allies, spread_from_allies=True, occupied=_occupied_squares(observation)) if enemies else None
                if dest:
                    add(
                        f"cast {slug} to ({dest['x']},{dest['y']})",
                        f"{head}: teleport to ({dest['x']},{dest['y']}), away from enemies.{conc}",
                        {**base, "destination": dest},
                    )
            else:
                add(f"cast {slug}", f"{head} on yourself.{conc}", {**base, "target_ids": [actor_id]})
            continue
        if _spell_aoe_spec(slug, rec) or mode == "aoe":
            hit = _aoe_targets_for_spell(slug, rec, pos, enemies, friendlies, avoid_friendlies=True)
            if hit:
                names = ", ".join(str(e["id"]) for e in hit)
                add(
                    f"cast {slug} on {'+'.join(str(e['id']) for e in hit)}",
                    f"{head}: area effect catching {names} and no allies.{dmg_txt}{conc}",
                    {**base, "target_ids": [str(e["id"]) for e in hit]},
                )
            continue
        if heal:
            pool = [a for a in downed + allies if distance_ft(pos, a["position"]) <= max(rng, 5)]
            pool.append({**me, "id": actor_id})
            hurt = [a for a in pool if int(a.get("hp", 0)) < int(a.get("hp_max", 0) or 0)]
            for a in hurt:
                tag = " (at 0 HP: gets them back up)" if int(a.get("hp", 1)) == 0 else ""
                who = "yourself" if a["id"] == actor_id else _ally_label(a, pos)
                add(f"cast {slug} on {a['id']}", f"{head}: heal {who}{tag}.", {**base, "target_ids": [a["id"]]})
            if max_t > 1 and len(hurt) > 1:
                group = [a["id"] for a in hurt[:max_t]]
                add(
                    f"cast {slug} on {'+'.join(group)}",
                    f"{head}: heal {', '.join(group)}.",
                    {**base, "target_ids": group},
                )
            continue
        if not dmg_types and not rec.get("save"):
            # Buffs such as Bless: up to max_targets of the party in range, nearest first.
            party = sorted(
                [a for a in allies if distance_ft(pos, a["position"]) <= rng] + [{**me, "id": actor_id}],
                key=lambda a: (a["id"] != actor_id, distance_ft(pos, a["position"])),
            )[:max_t]
            ids = [a["id"] for a in party]
            add(f"cast {slug} on {'+'.join(ids)}", f"{head} on {', '.join(ids)}.{conc}", {**base, "target_ids": ids})
            continue
        in_range = [e for e in enemies if distance_ft(pos, e["position"]) <= rng]
        # Hold Person (and any other type-restricted spell) is refused by the engine unless the
        # target's creature type is known and allowed. Keep those casts out of the Choice list.
        in_range = [e for e in in_range if spell_target_allowed(slug, rec, e, rules)]
        for e in in_range:
            add(
                f"cast {slug} on {e['id']}",
                f"{head} targeting {_enemy_label(e, pos)}.{dmg_txt}{conc}",
                {**base, "target_ids": [e["id"]]},
            )
        if max_t > 1 and len(in_range) > 1:
            group = [e["id"] for e in sorted(in_range, key=lambda e: distance_ft(pos, e["position"]))[:max_t]]
            add(
                f"cast {slug} on {'+'.join(group)}",
                f"{head} split across {', '.join(group)}.{dmg_txt}{conc}",
                {**base, "target_ids": group},
            )

    # Movement.
    move_ft = int(legal.get("movement_remaining_ft", me.get("movement_remaining_ft", 0)) or 0)
    stand = bool(legal.get("can_stand_up"))
    conds = _conditions(me)
    if stand:
        move_ft -= int(me.get("speed_ft", 30) or 30) // 2  # standing up costs half your speed
    elif "prone" in conds:
        move_ft //= 2  # crawling costs double
    fear_src = conds.get("frightened")
    # The observation names the condition but not its source, so when Frightened treat every enemy as one.
    fear_pos = [e["position"] for e in enemies if fear_src is not None and (not fear_src or e["id"] in fear_src)]

    def fear_ok(dest: dict[str, int]) -> bool:
        return all(distance_ft(dest, fp) >= distance_ft(pos, fp) for fp in fear_pos)

    if stand:
        add(
            "stand up",
            "Stand up from Prone (costs half your speed).",
            {"type": "move", "actor_id": actor_id, "to": dict(pos), "stand_up": True},
        )
    if move_ft >= 5:
        for e in enemies:
            dest = _step_toward(pos, e["position"], move_ft, desired_dist_ft=5, occupied=_occupied_squares(observation))
            if dest != pos and fear_ok(dest):
                close = distance_ft(dest, e["position"]) <= 5
                add(
                    f"move toward {e['id']} to ({dest['x']},{dest['y']})",
                    f"Move {distance_ft(pos, dest)} ft toward {_enemy_label(e, pos)}"
                    f"{', ending next to it' if close else ''}. Leaving an enemy's reach provokes an Opportunity Attack.",
                    {"type": "move", "actor_id": actor_id, "to": dest, "stand_up": stand},
                )
        for a in downed:
            dest = _step_toward(pos, a["position"], move_ft, desired_dist_ft=5, occupied=_occupied_squares(observation))
            if dest != pos and fear_ok(dest):
                add(
                    f"move toward {a['id']} to ({dest['x']},{dest['y']})",
                    f"Move {distance_ft(pos, dest)} ft toward downed ally {_ally_label(a, pos)}.",
                    {"type": "move", "actor_id": actor_id, "to": dest, "stand_up": stand},
                )
        if enemies:
            dest = _best_kite_position(pos, move_ft, enemies, allies, spread_from_allies=True, occupied=_occupied_squares(observation))
            if dest and dest != pos and fear_ok(dest):
                add(
                    f"move away to ({dest['x']},{dest['y']})",
                    f"Move {distance_ft(pos, dest)} ft to ({dest['x']},{dest['y']}): out of melee reach and at least "
                    "20 ft from allies where possible. Leaving an enemy's reach provokes an Opportunity Attack.",
                    {"type": "move", "actor_id": actor_id, "to": dest, "stand_up": stand},
                )

    if res.get("action"):
        add("dodge", "Take the Dodge action: attacks against you have Disadvantage until your next turn.",
            {"type": "dodge", "actor_id": actor_id})
        add("dash", "Take the Dash action: extra movement equal to your speed this turn.",
            {"type": "dash", "actor_id": actor_id})
        if any(distance_ft(pos, e["position"]) <= 5 for e in enemies):
            add("disengage", "Take the Disengage action: moving this turn doesn't provoke Opportunity Attacks.",
                {"type": "disengage", "actor_id": actor_id})

    out = out[: MAX_CANDIDATES - 1]
    add("end turn", "End your turn now.", {"type": "end_turn", "actor_id": actor_id})
    return out


class JevDriver(BaseLLMDriver):
    """Party driver backed by TypeSafe AI's Jev (`POST /v1/systemone`, Choice question over candidate intents)."""

    DEFAULT_API_BASE = "https://api.typesafe.ai"
    DEFAULT_API_KEY_ENV = "TYPESAFE_API_KEY"
    PROVIDER_PREFIX = "typesafe"

    def __init__(self, model: str, *, rules: RulesStore | None = None, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self._rules = _ensure_rules(rules)
        self._monster_fallback = HeuristicTacticalDriver(rules=self._rules)
        self._ranked: list[tuple[str, float, dict[str, Any]]] = []
        self._tried: set[str] = set()
        self._usage: dict[str, int] = {}

    def reset(self, seed: int | None = None) -> None:
        self._ranked = []
        self._tried = set()
        self._monster_fallback.reset(seed)

    def _request(self, observation: dict[str, Any], criteria: dict[str, str]) -> tuple[str, dict[str, str], dict[str, Any]]:
        key = self._resolve_api_key()
        headers: dict[str, str] = {}
        if key:
            _reject_control_chars_in_secret(key)
            headers["Authorization"] = f"Bearer {key}"
        state = {k: v for k, v in observation.items() if k != "retry_feedback"}
        payload = {
            "model": self.model,
            "state": json.dumps(state, separators=(",", ":")),
            "questions": {QUESTION_ID: {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}},
        }
        return f"{self.api_base}/v1/systemone", headers, payload

    def _pick(self, retry: bool) -> AgentDecision:
        for name, p, intent in self._ranked:
            if name in self._tried:
                continue
            self._tried.add(name)
            top = [f"{n} ({q:.2f})" for n, q, _ in self._ranked[:3]]
            why = f"Jev: '{name}' p={p:.2f}. Top options: {'; '.join(top)}."
            if retry:
                why = f"Retry with the next-ranked option. {why}"
            return AgentDecision(intent=intent, rationale=why, table_talk=None, usage=dict(self._usage))
        raise RuntimeError("Every Jev candidate for this decision was rejected.")

    def decide(self, observation: dict[str, Any]) -> AgentDecision:
        me = observation.get("self") or {}
        if me.get("kind") == "monster":
            return self._monster_fallback.decide(observation)
        retry = bool(observation.get("retry_feedback"))
        if retry and self._ranked:
            return self._pick(retry=True)

        candidates = enumerate_candidates(observation, self._rules)
        if len(candidates) == 1:  # only end_turn: no API call needed
            self._ranked, self._tried, self._usage = [(candidates[0][0], 1.0, candidates[0][2])], set(), {}
            return self._pick(retry=False)
        criteria = {name: desc for name, desc, _ in candidates}
        url, headers, payload = self._request(observation, criteria)
        secret_values = [self._explicit_api_key or "", os.environ.get(self.api_key_env, "")]
        try:
            resp = self._transport(url, headers, payload, self.timeout_s)
        except Exception as exc:
            raise RuntimeError(scrub_secrets(str(exc) or "Jev request failed.", secret_values) or "Jev request failed.") from None
        answer = (resp.get("answers") or {}).get(QUESTION_ID)
        if not isinstance(answer, dict) or not isinstance(answer.get("probabilities"), dict):
            raise ValueError("Jev response missing answers.next_action.probabilities.")
        probs = answer["probabilities"]
        intents = {name: intent for name, _, intent in candidates}
        order = {name: i for i, (name, _, _) in enumerate(candidates)}
        self._ranked = sorted(
            ((n, float(probs.get(n, 0.0) or 0.0), intents[n]) for n in intents),
            key=lambda t: (-t[1], order[t[0]]),
        )
        self._tried = set()
        u = resp.get("usage") or {}
        inp = int(u.get("input_tokens", 0) or 0)
        out = int(u.get("output_tokens", 0) or 0)
        self._usage = {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}
        return self._pick(retry=False)
