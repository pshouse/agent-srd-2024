"""Local browser tabletop for 5e 2024 (SRD 5.2): the server behind `bin/srd-web`.

The browser is a thin client over the same contract as `bin/srd-arena play`.
A worker thread runs `run_interactive_session` unchanged, with a
`HumanDriver` whose `input_fn` blocks until the browser sends a command.
Every browser action is validated against the legal action menu and then
turned into the exact line a terminal player would type (`3`, `move 15 0`,
`r`, `e`, `say ...`, `save`, `quit`), so the engine stays the authority and a
trace played over the web has the same format as terminal play and replays
with `bin/srd-arena replay`.

Security model (local-only, single user):
- Binds only to 127.0.0.1. Any other host is refused.
- A per-launch random bearer token (`secrets`) gates every `/api/` call. The
  startup URL carries it in the fragment, which browsers never send.
- Host must be `127.0.0.1:<port>` or `localhost:<port>` (DNS-rebinding defense);
  a present Origin must match; cross-site fetch metadata is refused.
- POSTs need the token, a matching Origin, `Content-Type: application/json`,
  a Content-Length, and a small body. Unknown fields are refused.
- Static files come from a fixed in-memory allowlist read at launch, so no
  request path ever reaches the filesystem.
- `--save` and `--out-trace` paths are fixed at launch and keep the CLI's
  overwrite rule (`--force`).

Strictly Python 3.11+ stdlib.
"""

from __future__ import annotations

import argparse
import copy
import hmac
import json
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from srd2024.engine.store import RulesStore

from .agents import BaseLLMDriver, _available_slots_map, create_driver, scrub_secrets
from .builder import (
    MAX_BUILDER_BODY_BYTES,
    compile_launch_payload,
    compute_encounter_budget,
    get_builder_catalog,
    search_monsters,
    validate_pc_spec,
)
from .human import HumanDriver, _sanitize_hook_for_hud, build_human_action_menu
from .scenarios import load_scenario
from .session import load_session_checkpoint, run_interactive_session, save_session_checkpoint

BIND_HOST = "127.0.0.1"
MAX_BODY_BYTES = 8 * 1024
MAX_TEXT_CHARS = 300
MAX_COORD_FT = 1000
MAX_LOG_LINES = 20000
MAX_LOG_LINE_CHARS = 2000
MAX_LOG_PER_RESPONSE = 2000
DEFAULT_WAIT_S = 20.0

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = REPO_ROOT / "web"

# URL path -> file under web/. Nothing else is served.
STATIC_FILES: dict[str, str] = {
    "/": "tabletop/index.html",
    "/index.html": "tabletop/index.html",
    "/tabletop.js": "tabletop/tabletop.js",
    "/tabletop-model.js": "tabletop/tabletop-model.js",
    "/builder-model.js": "tabletop/builder-model.js",
    "/builder-view.js": "tabletop/builder-view.js",
    "/tabletop.css": "tabletop/tabletop.css",
    "/styles.css": "styles.css",
    "/lib/board-view.js": "lib/board-view.js",
    "/lib/replay-model.js": "lib/replay-model.js",
    "/lib/attribution.js": "lib/attribution.js",
}
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩﻿]")
_REJECTED_PREFIX = "! Rejected:"
# Lines the terminal loop would treat as a command rather than plain English.
_COMMAND_RE = re.compile(
    r"^(\d+|r|reroll|inspiration|heroic inspiration|heroic_inspiration|e|end|end_turn|end turn|pass|"
    r"stand|stand up|stand_up|status|map|hud|help|\?|h|save|quit|exit|q|say|move|mv|m)$"
    r"|^(say|move|mv|m)\s",
    re.IGNORECASE,
)
_STRUCTURED_KINDS = ("attack", "cast_spell", "class_feature", "dodge", "dash", "disengage", "help")
_INTENT_KEYS = {
    "type", "actor_id", "target_id", "target_ids", "weapon", "spell", "slot_level",
    "action", "ally_id", "offhand", "destination", "to", "stand_up", "as_reaction",
    "feature", "mode", "amount", "allocations", "move_to",
}


class ApiError(Exception):
    """A request the API refuses, with its HTTP status."""

    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = message
        self.extra = extra


@dataclass
class TabletopConfig:
    """Everything fixed at launch. The browser can change none of it."""

    scenario: str | dict[str, Any] | None = None
    play_as: str | None = None
    seat_overrides: list[str] | None = None
    party_agent: str | dict[str, Any] = "heuristic-tactical"
    enemy_agent: str | dict[str, Any] = "heuristic-tactical"
    nl_driver_spec: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.0
    seed: int = 42
    max_rounds: int | None = None
    max_actions_per_turn: int = 6
    max_retries_per_action: int = 2
    save_path: Path | None = None
    resume_path: Path | None = None
    out_trace: Path | None = None
    force: bool = False
    builder_mode: bool = False
    dungeon: str | dict[str, Any] | None = None
    allow_short_rest: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _clean_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ApiError(400, f"'{label}' must be a string.")
    text = " ".join(_CONTROL_RE.sub(" ", value).split())
    if not text:
        raise ApiError(400, f"'{label}' must not be empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise ApiError(400, f"'{label}' is longer than {MAX_TEXT_CHARS} characters.")
    return text


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _hud_line(text: str) -> str:
    text = str(text)
    return text if len(text) <= MAX_LOG_LINE_CHARS else text[: MAX_LOG_LINE_CHARS - 1] + "…"


def client_view(observation: dict[str, Any]) -> dict[str, Any]:
    """Whitelist the active seat's partial observation for the browser.

    The observation already hides opposing exact HP, AC, and slots; this copies
    only the fields the UI draws so nothing new can leak by accident.
    """
    me = observation.get("self") or {}
    res = me.get("turn_resources") or {}
    legal = observation.get("legal_actions") or {}
    enemies = list(observation.get("enemies") or [])
    enemy_names = [str(e.get("name") or "") for e in enemies if e.get("name")]
    conc = me.get("concentration")

    def _pos(p: Any) -> dict[str, int]:
        p = p if isinstance(p, dict) else {}
        return {"x": int(p.get("x", 0)), "y": int(p.get("y", 0))}

    def _conc(c: Any) -> str | None:
        return str(c.get("spell")) if isinstance(c, dict) and c.get("spell") else None

    slots = _available_slots_map(me) if isinstance(me.get("spellcasting"), dict) else {}
    view: dict[str, Any] = {
        "round": int(observation.get("round", 1)),
        "scenario": {
            "id": str((observation.get("scenario") or {}).get("id") or ""),
            "name": str((observation.get("scenario") or {}).get("name") or ""),
        },
        "self": {
            "id": str(me.get("id") or ""),
            "name": str(me.get("name") or ""),
            "side": str(me.get("side") or ""),
            "kind": str(me.get("kind") or ""),
            "position": _pos(me.get("position")),
            "hp": int(me.get("hp", 0)),
            "hp_max": int(me.get("hp_max", 0)),
            "temp_hp": int(me.get("temp_hp", 0)),
            "ac": int(me.get("effective_ac", me.get("ac", 10))),
            "speed_ft": int(me.get("speed_ft", 0)),
            "movement_remaining_ft": int(me.get("movement_remaining_ft", 0)),
            "conditions": [str(c) for c in me.get("conditions") or []],
            "concentration": _conc(conc),
            "effects": [str(e) for e in me.get("active_effects") or []],
            "heroic_inspiration": bool(me.get("heroic_inspiration", False)),
            "resources": {
                "action": bool(res.get("action")),
                "surge_action": bool(res.get("surge_action", False)),
                "bonus_action": bool(res.get("bonus_action")),
                "reaction": bool(res.get("reaction")),
                "attacks_remaining": int(res.get("attacks_remaining", 0) or 0),
            },
            "slots": {str(k): int(v) for k, v in sorted(slots.items())},
            "class_resources": copy.deepcopy(me.get("class_resources") or {}),
            "hit_dice": copy.deepcopy(me.get("hit_dice")),
            "death_saves": {
                "successes": int((me.get("death_saves") or {}).get("successes", 0)),
                "failures": int((me.get("death_saves") or {}).get("failures", 0)),
            },
        },
        "allies": [
            {
                "id": str(a.get("id") or ""),
                "name": str(a.get("name") or ""),
                "position": _pos(a.get("position")),
                "hp": int(a.get("hp", 0)),
                "hp_max": int(a.get("hp_max", 1)),
                "ac": int(a.get("ac", 10)),
                "alive": bool(a.get("alive", False)),
                "conditions": [str(c) for c in a.get("conditions") or []],
                "concentration": _conc(a.get("concentration")),
                "effects": [str(e) for e in a.get("active_effects") or []],
            }
            for a in observation.get("allies") or []
        ],
        "enemies": [
            {
                "id": str(e.get("id") or ""),
                "name": str(e.get("name") or ""),
                "position": _pos(e.get("position")),
                "alive": bool(e.get("alive", False)),
                "hp_band": str(e.get("hp_band") or "unharmed"),
                "conditions": [str(c) for c in e.get("conditions") or []],
                "effects": [str(x) for x in e.get("visible_effects") or []],
            }
            for e in enemies
        ],
        "can_reroll": bool(legal.get("can_reroll", False)),
        "table_talk": [
            {
                "round": m.get("round"),
                "actor_name": str(m.get("actor_name") or m.get("actor_id") or ""),
                "message": str(m.get("message") or ""),
            }
            for m in observation.get("party_blackboard") or []
        ],
        "recent_events": [_sanitize_hook_for_hud(str(h), enemy_names) for h in observation.get("recent_events") or []],
        "retry_feedback": str(observation["retry_feedback"]) if observation.get("retry_feedback") else None,
    }
    raw_bounds = observation.get("bounds")
    if isinstance(raw_bounds, dict) and all(k in raw_bounds for k in ("min_x", "min_y", "max_x", "max_y")):
        view["bounds"] = {
            "min_x": int(raw_bounds["min_x"]),
            "min_y": int(raw_bounds["min_y"]),
            "max_x": int(raw_bounds["max_x"]),
            "max_y": int(raw_bounds["max_y"]),
        }
    raw_terrain = observation.get("terrain")
    if isinstance(raw_terrain, dict):
        view["terrain"] = {
            "walls": [[int(p[0]), int(p[1])] for p in (raw_terrain.get("walls") or []) if isinstance(p, (list, tuple)) and len(p) == 2],
            "difficult_terrain": [
                [int(p[0]), int(p[1])] for p in (raw_terrain.get("difficult_terrain") or []) if isinstance(p, (list, tuple)) and len(p) == 2
            ],
            "cover": [
                {"x": int(c["x"]), "y": int(c["y"]), "kind": str(c["kind"])}
                for c in (raw_terrain.get("cover") or [])
                if isinstance(c, dict) and "x" in c and "y" in c and str(c.get("kind")) in ("half", "three_quarters")
            ],
        }
    return view


def _menu_for_client(menu: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "index": int(m["index"]),
            "category": str(m.get("category") or ""),
            "label": str(m.get("label") or ""),
            "summary": str(m.get("summary") or ""),
            "intent": copy.deepcopy(m.get("intent") or {}),
            "meta": copy.deepcopy(m.get("meta") or {}),
        }
        for m in menu
    ]


class WebHumanDriver(HumanDriver):
    """`HumanDriver` that publishes each decision point to the web session first.

    `decide()` is the terminal driver's own loop; this only records the
    observation and menu the player is choosing from, and reports parse-level
    rejections, so web and terminal play take identical decisions.
    """

    def __init__(self, session: "TabletopSession", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session = session

    def decide(self, observation: dict[str, Any]):  # type: ignore[override]
        menu = build_human_action_menu(observation, self._rules)
        self._session._publish_decision(observation, menu)
        return super().decide(observation)

    def _record_rejected(self, observation: dict[str, Any], actor_id: str, text: str, reason: str) -> None:
        super()._record_rejected(observation, actor_id, text, reason)
        self._session._note_rejection(reason)


class TabletopSession:
    """One interactive table, run on a worker thread, driven by HTTP requests."""

    def __init__(
        self,
        config: TabletopConfig,
        *,
        rules: RulesStore,
        nl_driver: BaseLLMDriver | None = None,
    ) -> None:
        self.config = config
        self.rules = rules
        self.nl_driver = nl_driver
        self._cv = threading.Condition()
        self.status = "builder" if config.builder_mode else "ready"  # builder | ready | running | awaiting_input | short_rest | finished | paused | error
        self._pending: str | None = None
        self._pending_rest: dict[str, Any] | None = None
        self._last_rest_record: dict[str, Any] | None = None
        self.dungeon_progress: dict[str, Any] | None = None
        self._abort = False
        self._decision_id = 0
        self._view: dict[str, Any] | None = None
        self._menu: list[dict[str, Any]] = []
        self._log: list[dict[str, Any]] = []
        self._log_seq = 0
        self._rejections: list[tuple[int, str]] = []
        self._thread: threading.Thread | None = None
        self.transcript: dict[str, Any] | None = None
        self.error: str | None = None
        self.notice: str | None = None
        self.pause_reason: str | None = None
        self.trace_written = False
        self.checkpoint_written = False
        self._resume_from: Path | None = config.resume_path
        self._scenario_name = "Custom Builder" if config.builder_mode else ""
        self._scenario_id = "builder" if config.builder_mode else ""
        if config.scenario is not None:
            try:
                compiled = load_scenario(config.scenario, rules)
                self._scenario_name, self._scenario_id = str(compiled["name"]), str(compiled["id"])
            except Exception:  # validated again when the session starts
                pass
        elif config.dungeon is not None:
            if isinstance(config.dungeon, dict):
                self._scenario_name = str(config.dungeon.get("name") or "Custom Dungeon")
                self._scenario_id = str(config.dungeon.get("id") or "custom-dungeon")
            else:
                self._scenario_name = str(config.dungeon)
                self._scenario_id = str(config.dungeon)
        elif config.resume_path is not None:
            ckpt = load_session_checkpoint(config.resume_path)
            self._scenario_name = str(ckpt["scenario"].get("name") or "")
            self._scenario_id = str(ckpt["scenario"].get("id") or "")

    # ---- worker-thread hooks -------------------------------------------------

    def _output(self, text: Any) -> None:
        with self._cv:
            for line in str(text).split("\n"):
                self._log_seq += 1
                self._log.append({"seq": self._log_seq, "text": _hud_line(line)})
            if len(self._log) > MAX_LOG_LINES:
                del self._log[: len(self._log) - MAX_LOG_LINES]

    def _input(self, _prompt: str) -> str:
        with self._cv:
            self.status = "awaiting_input"
            self._cv.notify_all()
            while self._pending is None and not self._abort:
                self._cv.wait()
            if self._abort:
                self.status = "running"
                # The terminal driver turns Ctrl-C into a `quit` pause (and saves when --save is set).
                raise KeyboardInterrupt
            text, self._pending = self._pending, None
            self.status = "running"
            return text

    def _publish_decision(self, observation: dict[str, Any], menu: list[dict[str, Any]]) -> None:
        with self._cv:
            self._decision_id += 1
            self._view = client_view(observation)
            self._menu = _menu_for_client(menu)

    def _note_rejection(self, reason: str) -> None:
        with self._cv:
            self._rejections.append((self._log_seq, scrub_secrets(str(reason))))

    def _new_driver(self) -> WebHumanDriver:
        return WebHumanDriver(
            self,
            rules=self.rules,
            input_fn=self._input,
            output_fn=self._output,
            nl_driver=self.nl_driver,
            show_hud=False,
        )

    def _run(self) -> None:
        cfg = self.config
        if cfg.dungeon is not None:
            self._run_dungeon()
            return
        resume = self._resume_from
        while True:
            try:
                result = run_interactive_session(
                    None if resume is not None else cfg.scenario,
                    rules=self.rules,
                    play_as=cfg.play_as,
                    seat_overrides=cfg.seat_overrides,
                    party_agent=cfg.party_agent,
                    enemy_agent=cfg.enemy_agent,
                    human_driver=self._new_driver(),
                    output_fn=self._output,
                    seed=cfg.seed,
                    max_rounds=cfg.max_rounds,
                    max_actions_per_turn=cfg.max_actions_per_turn,
                    max_retries_per_action=cfg.max_retries_per_action,
                    resume_path=resume,
                    api_base=cfg.api_base,
                    api_key_env=cfg.api_key_env,
                    temperature=cfg.temperature,
                )
            except Exception as exc:  # surfaced to the browser, never a traceback
                with self._cv:
                    self.status = "error"
                    self.error = scrub_secrets(str(exc) or type(exc).__name__)
                    self._cv.notify_all()
                return

            if result.get("status") == "paused":
                reason = str(result.get("reason") or "quit")
                saved = self._write_checkpoint(result["checkpoint"])
                if reason == "save" and saved and not self._abort:
                    self._output(f"[Checkpoint saved to {cfg.save_path}; play continues]")
                    resume = cfg.save_path
                    continue
                with self._cv:
                    self.status = "paused"
                    self.pause_reason = reason
                    if saved:
                        self._resume_from = cfg.save_path
                    self._cv.notify_all()
                return

            trace_note = self._write_trace(result)
            with self._cv:
                self.transcript = result
                self.status = "finished"
                if trace_note:
                    self.notice = trace_note
                self._cv.notify_all()
            return

    def _wait_short_rest_choice(self, progress: dict[str, Any]) -> dict[str, Any]:
        with self._cv:
            self.dungeon_progress = copy.deepcopy(progress)
            self._pending_rest = None
            self.status = "short_rest"
            self._cv.notify_all()
            while self._pending_rest is None and not self._abort:
                self._cv.wait()
            if self._abort:
                self.status = "running"
                raise KeyboardInterrupt
            choice, self._pending_rest = self._pending_rest, None
            self.status = "running"
            return choice

    def _run_dungeon(self) -> None:
        cfg = self.config
        from srd2024.arena import dungeon as dungeon_mod

        try:
            compiled = dungeon_mod.load_dungeon(cfg.dungeon, self.rules)
            party_carryover = dungeon_mod.init_party_carryover(compiled["party"], self.rules)
            rooms = list(compiled["rooms"])
            total_rooms = len(rooms)
            effective_allow_rest = bool(cfg.allow_short_rest) and bool(
                compiled.get("allow_short_rest_between_rooms", True)
            )
            room_traces: list[dict[str, Any]] = []
            rests: list[dict[str, Any]] = []
            rooms_cleared = 0
            last_winner = "enemy"

            self._output(
                f"=== Starting Dungeon: {compiled['name']} ({total_rooms} rooms, Tier {compiled['tier']}) ==="
            )

            for r_idx, room in enumerate(rooms):
                if not dungeon_mod._has_conscious_living_pc(party_carryover):
                    break

                with self._cv:
                    self.dungeon_progress = {
                        "room_index": r_idx,
                        "total_rooms": total_rooms,
                        "room_name": str(room.get("name") or f"Room {r_idx + 1}"),
                        "next_room_name": (
                            str(rooms[r_idx + 1].get("name") or f"Room {r_idx + 2}")
                            if r_idx + 1 < total_rooms
                            else None
                        ),
                        "party_summary": copy.deepcopy(party_carryover),
                    }

                self._output(f"\n--- Entering {room['name']} ({r_idx + 1}/{total_rooms}) ---")
                room_scenario = dungeon_mod.build_room_scenario(compiled, r_idx, party_carryover)
                room_seed = int(cfg.seed) + r_idx * 1000
                room_res = run_interactive_session(
                    room_scenario,
                    rules=self.rules,
                    play_as=cfg.play_as,
                    seat_overrides=cfg.seat_overrides,
                    party_agent=cfg.party_agent,
                    enemy_agent=cfg.enemy_agent,
                    human_driver=self._new_driver(),
                    output_fn=self._output,
                    seed=room_seed,
                    max_rounds=cfg.max_rounds,
                    max_actions_per_turn=cfg.max_actions_per_turn,
                    max_retries_per_action=cfg.max_retries_per_action,
                    api_base=cfg.api_base,
                    api_key_env=cfg.api_key_env,
                    temperature=cfg.temperature,
                )
                if room_res.get("status") == "paused":
                    with self._cv:
                        self.status = "paused"
                        self.pause_reason = str(room_res.get("reason") or "quit")
                        self._cv.notify_all()
                    return

                room_traces.append(room_res)
                last_winner = str(room_res.get("winner") or "draw")
                room_won = last_winner == "party"
                party_carryover = dungeon_mod.extract_party_carryover(
                    room_res["final_state"],
                    party_carryover,
                    room_won=room_won,
                )
                if not room_won:
                    break

                rooms_cleared += 1
                if r_idx + 1 < total_rooms:
                    if not dungeon_mod._has_any_living_pc(party_carryover):
                        break
                    next_room = rooms[r_idx + 1]
                    if effective_allow_rest:
                        choice = self._wait_short_rest_choice(
                            {
                                "room_index": r_idx,
                                "total_rooms": total_rooms,
                                "room_name": str(room.get("name") or f"Room {r_idx + 1}"),
                                "next_room_name": str(next_room.get("name") or f"Room {r_idx + 2}"),
                                "party_summary": party_carryover,
                            }
                        )
                        if choice.get("take_rest", True):
                            rest_rng = dungeon_mod.DeterministicRNG(seed=(int(cfg.seed) ^ 0x52455354) + r_idx)
                            party_carryover, rest_rec = dungeon_mod.resolve_short_rest(
                                party_carryover,
                                rng=rest_rng,
                                plan=choice.get("plan"),
                            )
                            rest_entry = {
                                "after_room_index": r_idx,
                                "after_room_id": str(room.get("id") or f"room-{r_idx + 1}"),
                                **rest_rec,
                            }
                        else:
                            rest_entry = {
                                "after_room_index": r_idx,
                                "after_room_id": str(room.get("id") or f"room-{r_idx + 1}"),
                                "taken": False,
                                "per_pc": {},
                            }
                    else:
                        rest_entry = {
                            "after_room_index": r_idx,
                            "after_room_id": str(room.get("id") or f"room-{r_idx + 1}"),
                            "taken": False,
                            "per_pc": {},
                        }
                    rests.append(rest_entry)
                    with self._cv:
                        self._last_rest_record = copy.deepcopy(rest_entry)
                    if not dungeon_mod._has_conscious_living_pc(party_carryover):
                        break

            winner = "party" if rooms_cleared == total_rooms else last_winner
            final_party_state = dungeon_mod._format_final_party_state(party_carryover)
            result: dict[str, Any] = {
                "schema_version": dungeon_mod.DUNGEON_SCHEMA_VERSION,
                "kind": "dungeon_trace",
                "dataset_id": self.rules.dataset_id,
                "dungeon": {
                    "id": compiled["id"],
                    "name": compiled["name"],
                    "tier": int(compiled["tier"]),
                    "party_level": int(compiled["party_level"]),
                    "total_rooms": total_rooms,
                },
                "config": {
                    "seed": int(cfg.seed),
                    "party_driver": str(cfg.party_agent),
                    "enemy_driver": str(cfg.enemy_agent),
                    "allow_short_rest": effective_allow_rest,
                    "max_actions_per_turn": int(cfg.max_actions_per_turn),
                    "max_retries_per_action": int(cfg.max_retries_per_action),
                },
                "winner": winner,
                "rooms_cleared": rooms_cleared,
                "total_rooms": total_rooms,
                "rooms": room_traces,
                "rests": rests,
                "final_party_state": final_party_state,
            }
            result["scorecard"] = dungeon_mod.score_dungeon(result, self.rules)
            result["digest"] = dungeon_mod.compute_dungeon_digest(result)
        except KeyboardInterrupt:
            with self._cv:
                self.status = "paused"
                self.pause_reason = "quit"
                self._cv.notify_all()
            return
        except Exception as exc:
            with self._cv:
                self.status = "error"
                self.error = scrub_secrets(str(exc) or type(exc).__name__)
                self._cv.notify_all()
            return

        trace_note = self._write_trace(result)
        with self._cv:
            self.transcript = result
            self.dungeon_progress = None
            self.status = "finished"
            if trace_note:
                self.notice = trace_note
            self._cv.notify_all()

    def _write_checkpoint(self, payload: dict[str, Any]) -> bool:
        path = self.config.save_path
        if path is None:
            self._output("[Session paused: no --save path was given at launch, so no checkpoint was written]")
            return False
        try:
            # Overwrite rule as the CLI: refuse an existing file unless --force, except
            # one this server wrote itself earlier in the same launch.
            save_session_checkpoint(path, payload, force=self.config.force or self.checkpoint_written)
        except (OSError, ValueError) as exc:
            with self._cv:
                self.notice = scrub_secrets(f"Checkpoint not saved: {exc}")
            self._output(f"[Checkpoint not saved: {exc}]")
            return False
        with self._cv:
            self.checkpoint_written = True
        self._output(f"[Session paused — checkpoint saved to {path}]")
        return True

    def _write_trace(self, transcript: dict[str, Any]) -> str | None:
        path = self.config.out_trace
        if path is None:
            return None
        try:
            if path.exists() and not self.config.force:
                raise FileExistsError(f"Refusing to overwrite '{path}'. Pass --force to replace it.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            return f"Trace not written: {exc}. Use Export to download it."
        self.trace_written = True
        self._output(f"[Trace written to {path}]")
        return None

    # ---- request-thread API --------------------------------------------------

    def start(self, wait_s: float = DEFAULT_WAIT_S) -> dict[str, Any]:
        with self._cv:
            if self.status == "paused" and self._resume_from is not None and self.checkpoint_written:
                pass
            elif self.status != "ready":
                raise ApiError(409, f"The session is {self.status}; it can't be started again.")
            mark = self._log_seq
            self.status = "running"
            self.pause_reason = None
            self.notice = None
            self._thread = threading.Thread(target=self._run, name="srd-web-session", daemon=True)
            self._thread.start()
            self._wait_quiet(wait_s)
            return self.snapshot(since=mark)

    def _wait_quiet(self, wait_s: float) -> None:
        deadline = time.monotonic() + max(0.0, wait_s)
        while self.status == "running":
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self._cv.wait(left)

    def submit(self, line: str, *, decision: int | None, wait_s: float = DEFAULT_WAIT_S) -> tuple[dict[str, Any], str | None]:
        """Send one terminal-equivalent line and wait until the table is quiet again."""
        with self._cv:
            if self.status != "awaiting_input":
                raise ApiError(409, f"Not waiting for your input (session is {self.status}).")
            if decision is not None and decision != self._decision_id:
                raise ApiError(409, "That menu is out of date. Refresh and choose again.", decision=self._decision_id)
            mark = self._log_seq
            rej_mark = len(self._rejections)
            self._pending = line
            self.status = "running"
            self._cv.notify_all()
            self._wait_quiet(wait_s)
            error = None
            for _, reason in self._rejections[rej_mark:]:
                error = reason
            for entry in self._log:
                if entry["seq"] > mark and entry["text"].strip().startswith(_REJECTED_PREFIX):
                    error = entry["text"].strip()[len(_REJECTED_PREFIX):].strip()
            return self.snapshot(since=mark), error

    def launch_custom(self, launch_spec: dict[str, Any], wait_s: float = DEFAULT_WAIT_S) -> dict[str, Any]:
        """Start a custom single encounter or 3-room dungeon from the builder lobby."""
        mode = launch_spec["mode"]
        if mode == "dungeon":
            try:
                from srd2024.arena import dungeon as _dungeon_mod  # noqa: F401
            except ImportError as exc:
                raise ApiError(
                    501,
                    "Multi-room dungeon engine (srd2024.arena.dungeon) is not available yet.",
                ) from exc
        with self._cv:
            if self.status in ("running", "awaiting_input", "short_rest"):
                raise ApiError(409, f"A session is already active ({self.status}); finish or quit it first.")
            self.config.builder_mode = False
            self.config.play_as = launch_spec["play_as"]
            self.config.party_agent = launch_spec["party_agent"]
            self.config.enemy_agent = launch_spec["enemy_agent"]
            self.config.seed = int(launch_spec["seed"])
            self.config.allow_short_rest = bool(launch_spec.get("allow_short_rest", True))
            if mode == "dungeon":
                self.config.dungeon = launch_spec["dungeon"]
                self.config.scenario = None
                self._scenario_name = str(launch_spec["dungeon"]["name"])
                self._scenario_id = str(launch_spec["dungeon"]["id"])
            else:
                self.config.scenario = launch_spec["scenario"]
                self.config.dungeon = None
                self._scenario_name = str(launch_spec["scenario"]["name"])
                self._scenario_id = str(launch_spec["scenario"]["id"])
            self._resume_from = None
            self._abort = False
            self._pending = None
            self._pending_rest = None
            self._last_rest_record = None
            self.dungeon_progress = None
            self.transcript = None
            self.error = None
            self.notice = None
            self.pause_reason = None
            self._view = None
            self._menu = []
            mark = self._log_seq
            self.status = "running"
            self._thread = threading.Thread(target=self._run, name="srd-web-session", daemon=True)
            self._thread.start()
            self._wait_quiet(wait_s)
            return self.snapshot(since=mark)

    def submit_short_rest(
        self,
        *,
        take_rest: bool,
        plan: dict[str, Any] | None,
        wait_s: float = DEFAULT_WAIT_S,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Resolve an inter-room Short Rest in dungeon play."""
        with self._cv:
            if self.status != "short_rest":
                raise ApiError(409, f"Not waiting for a Short Rest (session is {self.status}).")
            mark = self._log_seq
            self._last_rest_record = None
            self._pending_rest = {"take_rest": bool(take_rest), "plan": copy.deepcopy(plan) if plan is not None else None}
            self.status = "running"
            self._cv.notify_all()
            self._wait_quiet(wait_s)
            return self.snapshot(since=mark), copy.deepcopy(self._last_rest_record)

    def current_menu(self) -> tuple[int, list[dict[str, Any]], dict[str, Any] | None]:
        with self._cv:
            if self.status != "awaiting_input":
                raise ApiError(409, f"Not waiting for your input (session is {self.status}).")
            return self._decision_id, copy.deepcopy(self._menu), copy.deepcopy(self._view)

    def snapshot(self, *, since: int = 0) -> dict[str, Any]:
        with self._cv:
            lines = [e for e in self._log if e["seq"] > since][:MAX_LOG_PER_RESPONSE]
            result = None
            if self.transcript is not None:
                sc = self.transcript.get("scorecard") or {}
                if self.transcript.get("kind") == "dungeon_trace":
                    rooms_list = self.transcript.get("rooms") or []
                    result = {
                        "winner": self.transcript.get("winner"),
                        "rounds": sum(int(r.get("rounds") or 0) for r in rooms_list),
                        "actions": sum(len(r.get("actions") or []) for r in rooms_list),
                        "rooms_cleared": self.transcript.get("rooms_cleared"),
                        "total_rooms": self.transcript.get("total_rooms"),
                        "composite_score": sc.get("composite_score"),
                        "digest": self.transcript.get("digest"),
                    }
                else:
                    result = {
                        "winner": self.transcript.get("winner"),
                        "rounds": self.transcript.get("rounds"),
                        "actions": len(self.transcript.get("actions") or []),
                        "composite_score": sc.get("composite_score"),
                        "digest": self.transcript.get("digest"),
                    }
            live = self.status == "awaiting_input"
            return {
                "status": self.status,
                "scenario": {"id": self._scenario_id, "name": self._scenario_name},
                "seat": self.config.play_as,
                "features": {
                    "nl": self.nl_driver is not None,
                    "save": self.config.save_path is not None,
                    "out_trace": self.config.out_trace is not None,
                    "resume": self.status == "paused" and self.checkpoint_written,
                },
                "decision": self._decision_id if live else None,
                "view": copy.deepcopy(self._view),
                "menu": copy.deepcopy(self._menu) if live else [],
                "dungeon_progress": copy.deepcopy(self.dungeon_progress),
                "log": lines,
                "log_next": lines[-1]["seq"] if lines else since,
                "result": result,
                "trace_written": self.trace_written,
                "checkpoint_written": self.checkpoint_written,
                "pause_reason": self.pause_reason,
                "notice": self.notice,
                "error": self.error,
            }

    def trace(self) -> dict[str, Any]:
        with self._cv:
            if self.transcript is None:
                raise ApiError(409, "The match is not finished yet, so there is no trace.")
            return self.transcript

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._cv:
            self._abort = True
            self._cv.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)


# ---- action validation ---------------------------------------------------------


def _menu_index_for(menu: list[dict[str, Any]], want: dict[str, Any]) -> int:
    """Return the one menu index whose intent matches every field in `want`."""
    matches = [m for m in menu if all(m["intent"].get(k) == v for k, v in want.items())]
    if not matches:
        raise ApiError(422, f"No legal menu action matches that {want.get('type', 'intent')}.")
    if len(matches) > 1:
        raise ApiError(422, f"{len(matches)} legal actions match; add fields to pick one (or send its menu index).")
    return int(matches[0]["index"])


def action_to_line(body: dict[str, Any], menu: list[dict[str, Any]], view: dict[str, Any] | None) -> str:
    """Validate one browser action against the legal menu and return the terminal line for it."""
    kind = body.get("kind")
    if not isinstance(kind, str):
        raise ApiError(400, "'kind' must be a string.")
    allowed = {"kind", "decision"}
    actor_id = str(((view or {}).get("self") or {}).get("id") or "")

    def _only(*keys: str) -> None:
        extra = set(body) - allowed - set(keys)
        if extra:
            raise ApiError(400, f"Unknown field(s) for '{kind}': {', '.join(sorted(extra))}.")

    if kind == "menu":
        _only("index")
        idx = body.get("index")
        if not _is_int(idx) or not 1 <= idx <= len(menu):
            raise ApiError(422, f"'index' must be a menu number from 1 to {len(menu)}.")
        return str(idx)
    if kind == "move":
        _only("x", "y")
        x, y = body.get("x"), body.get("y")
        if not (_is_int(x) and _is_int(y)):
            raise ApiError(400, "'x' and 'y' must be integers (feet).")
        if x % 5 or y % 5 or abs(x) > MAX_COORD_FT or abs(y) > MAX_COORD_FT:
            raise ApiError(422, "Move to a 5-ft grid square: x and y must be multiples of 5 within the board.")
        return f"move {x} {y}"
    if kind == "stand":
        _only()
        if not any(m["intent"].get("type") == "move" and m["intent"].get("stand_up") for m in menu):
            raise ApiError(422, "Standing up is not on the legal action menu right now.")
        return "stand"
    if kind == "reroll":
        _only()
        if not any(m["intent"].get("type") == "reroll" for m in menu):
            raise ApiError(422, "There is no roll to reroll with Heroic Inspiration right now.")
        return "r"
    if kind == "end_turn":
        _only()
        return "e"
    if kind == "say":
        _only("text")
        return f"say {_clean_text(body.get('text'), label='text')}"
    if kind == "intent" or kind in _STRUCTURED_KINDS:
        if kind == "intent":
            _only("intent")
            want = body.get("intent")
            if not isinstance(want, dict) or not isinstance(want.get("type"), str):
                raise ApiError(400, "'intent' must be an object with a string 'type'.")
            want = dict(want)
        else:
            _only(
                "target_id", "target_ids", "weapon", "spell", "slot_level", "action", "ally_id", "offhand",
                "feature", "mode", "amount", "allocations", "move_to",
            )
            want = {k: v for k, v in body.items() if k not in allowed}
            want["type"] = kind
        unknown = set(want) - _INTENT_KEYS
        if unknown:
            raise ApiError(400, f"Unknown intent field(s): {', '.join(sorted(unknown))}.")
        if want.get("actor_id", actor_id) != actor_id:
            raise ApiError(422, "You can only act for the active combatant.")
        want.setdefault("actor_id", actor_id)
        if want["type"] in ("end_turn", "reroll") and set(want) <= {"type", "actor_id"}:
            return "e" if want["type"] == "end_turn" else action_to_line({"kind": "reroll"}, menu, view)
        if want["type"] == "move" and set(want) <= {"type", "actor_id", "to"} and isinstance(want.get("to"), dict):
            to = want["to"]
            if set(to) != {"x", "y"}:
                raise ApiError(400, "'to' must be {x, y}.")
            return action_to_line({"kind": "move", "x": to.get("x"), "y": to.get("y")}, menu, view)
        return str(_menu_index_for(menu, want))
    raise ApiError(400, f"Unknown action kind '{kind}'.")


def nl_to_line(body: dict[str, Any]) -> str:
    extra = set(body) - {"text", "decision"}
    if extra:
        raise ApiError(400, f"Unknown field(s): {', '.join(sorted(extra))}.")
    text = _clean_text(body.get("text"), label="text")
    if _COMMAND_RE.match(text):
        raise ApiError(422, "That looks like a terminal command. Use the action buttons for menu choices, moves, say, save and quit.")
    return text


# ---- HTTP ----------------------------------------------------------------------


def load_static_files(web_root: Path = WEB_ROOT) -> dict[str, tuple[bytes, str]]:
    out: dict[str, tuple[bytes, str]] = {}
    for url_path, rel in STATIC_FILES.items():
        path = web_root / rel
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"srd-web static file missing: web/{rel}")
        out[url_path] = (path.read_bytes(), _MIME[path.suffix])
    return out


class TabletopServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, session: TabletopSession, *, host: str = BIND_HOST, port: int = 0, token: str | None = None) -> None:
        if host != BIND_HOST:
            raise ValueError(f"srd-web only binds {BIND_HOST}; refusing host {host!r}.")
        self.session = session
        self.token = token or secrets.token_urlsafe(32)
        self.static = load_static_files()
        super().__init__((BIND_HOST, int(port)), _Handler)
        if self.server_address[0] != BIND_HOST:  # pragma: no cover - defensive
            raise RuntimeError("srd-web bound to an unexpected address.")
        bound = int(self.server_address[1])
        self.port = bound
        self.allowed_hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}
        self.allowed_origins = {f"http://{h}" for h in self.allowed_hosts}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#token={self.token}"


class _Handler(BaseHTTPRequestHandler):
    server: TabletopServer
    server_version = "srd-web"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        # Quiet by default: the token never appears in a request line, but keep logs empty anyway.
        return

    # ---- responses

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _error(self, err: ApiError) -> None:
        extra = {"WWW-Authenticate": "Bearer"} if err.status == 401 else None
        self._json(err.status, {"error": err.message, **err.extra}, extra)

    # ---- checks

    def _check_origin(self, *, require: bool) -> None:
        host = self.headers.get("Host", "")
        if host not in self.server.allowed_hosts:
            raise ApiError(403, "Host not allowed.")
        origin = self.headers.get("Origin")
        if origin is None:
            if require:
                raise ApiError(403, "Origin header required.")
        elif origin not in self.server.allowed_origins:
            raise ApiError(403, "Origin not allowed.")
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            raise ApiError(403, "Cross-site request refused.")

    def _check_token(self) -> None:
        auth = self.headers.get("Authorization")
        if not auth:
            raise ApiError(401, "Missing bearer token.")
        scheme, _, value = auth.partition(" ")
        if scheme.lower() != "bearer" or not value or not hmac.compare_digest(value.strip().encode(), self.server.token.encode()):
            raise ApiError(403, "Invalid token.")

    def _read_json(self, max_bytes: int = MAX_BODY_BYTES) -> dict[str, Any]:
        ctype = self.headers.get("Content-Type", "")
        if ctype.split(";", 1)[0].strip().lower() != "application/json":
            raise ApiError(415, "Content-Type must be application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(411, "Chunked bodies are not accepted; send Content-Length.")
        raw_len = self.headers.get("Content-Length")
        if raw_len is None or not raw_len.isdigit():
            raise ApiError(411, "Content-Length required.")
        length = int(raw_len)
        if length > max_bytes:
            self.close_connection = True
            raise ApiError(413, f"Body larger than {max_bytes} bytes.")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Body is not valid JSON.") from None
        if not isinstance(data, dict):
            raise ApiError(400, "Body must be a JSON object.")
        return data

    # ---- routing

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch("OPTIONS")

    def _dispatch(self, method: str) -> None:
        try:
            parts = urlsplit(self.path)
            path = parts.path
            if method in ("GET", "HEAD") and not path.startswith("/api/"):
                self._check_origin(require=False)
                if path not in self.server.static:
                    raise ApiError(404, "Not found.")
                body, ctype = self.server.static[path]
                self._send(200, body, ctype)
                return
            if not path.startswith("/api/"):
                raise ApiError(405, "Method not allowed.")
            self._check_origin(require=method == "POST")
            self._check_token()
            self._api(method, path, parse_qs(parts.query))
        except ApiError as err:
            self._error(err)
        except Exception as exc:  # pragma: no cover - last-resort guard, no traceback to the client
            self._json(500, {"error": scrub_secrets(f"Internal error: {type(exc).__name__}")})

    def _api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        session = self.server.session
        if method == "GET" and path == "/api/state":
            since = 0
            if "since" in query:
                raw = query["since"][0]
                if not raw.isdigit():
                    raise ApiError(400, "'since' must be a non-negative integer.")
                since = int(raw)
            self._json(200, session.snapshot(since=since))
            return
        if method == "GET" and path == "/api/trace":
            trace = session.trace()
            trace_id = (trace.get("scenario") or trace.get("dungeon") or {}).get("id", "match")
            name = f"{trace_id}-seed{trace.get('config', {}).get('seed', 0)}.trace.json"
            name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
            body = (json.dumps(trace, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}"'})
            return
        if method == "GET" and path == "/api/builder/catalog":
            self._json(200, get_builder_catalog(session.rules))
            return
        if method == "GET" and path == "/api/builder/monsters":
            allowed_q = {"cr_min", "cr_max", "type", "coverage", "q", "limit", "offset"}
            unknown_q = set(query) - allowed_q
            if unknown_q:
                raise ApiError(400, f"Unknown query parameter(s): {', '.join(sorted(unknown_q))}.")

            def _opt_float(key: str) -> float | None:
                if key not in query or not query[key][0].strip():
                    return None
                raw_v = query[key][0].strip()
                if "/" in raw_v:
                    num, _, den = raw_v.partition("/")
                    try:
                        val = float(num) / float(den)
                    except (ValueError, ZeroDivisionError):
                        raise ApiError(400, f"'{key}' must be a valid CR number.") from None
                else:
                    try:
                        val = float(raw_v)
                    except ValueError:
                        raise ApiError(400, f"'{key}' must be a valid CR number.") from None
                if val < 0 or val > 30:
                    raise ApiError(400, f"'{key}' must be between 0 and 30.")
                return val

            def _opt_int(key: str, default: int) -> int:
                if key not in query or not query[key][0].strip():
                    return default
                raw_v = query[key][0].strip()
                if not raw_v.isdigit():
                    raise ApiError(400, f"'{key}' must be a non-negative integer.")
                return int(raw_v)

            try:
                res = search_monsters(
                    session.rules,
                    cr_min=_opt_float("cr_min"),
                    cr_max=_opt_float("cr_max"),
                    creature_type=query.get("type", [""])[0],
                    coverage=query.get("coverage", [""])[0],
                    q=query.get("q", [""])[0],
                    limit=_opt_int("limit", 50),
                    offset=_opt_int("offset", 0),
                )
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
            self._json(200, res)
            return
        if method != "POST":
            raise ApiError(405 if path in _ALL_API_PATHS else 404, "Method not allowed." if path in _ALL_API_PATHS else "Not found.")
        if path in _GET_API_PATHS:
            raise ApiError(405, "Method not allowed.")
        if path not in _API_PATHS:
            raise ApiError(404, "Not found.")
        max_bytes = MAX_BUILDER_BODY_BYTES if path in _BUILDER_POST_PATHS else MAX_BODY_BYTES
        body = self._read_json(max_bytes=max_bytes)

        if path == "/api/builder/validate-pc":
            extra = set(body) - {"spec"}
            if extra or "spec" not in body:
                raise ApiError(400, "validate-pc requires only the 'spec' object.")
            self._json(200, validate_pc_spec(body["spec"], session.rules))
            return
        if path == "/api/builder/budget":
            extra = set(body) - {"party_levels", "monsters"}
            if extra or "party_levels" not in body or "monsters" not in body:
                raise ApiError(400, "budget requires only 'party_levels' and 'monsters'.")
            try:
                res = compute_encounter_budget(body["party_levels"], body["monsters"], session.rules)
            except ValueError as exc:
                raise ApiError(422, str(exc)) from None
            self._json(200, res)
            return
        if path == "/api/builder/launch":
            try:
                compiled = compile_launch_payload(body, session.rules)
            except KeyError as exc:
                raise ApiError(400, exc.args[0] if exc.args else str(exc)) from None
            except ValueError as exc:
                raise ApiError(422, str(exc)) from None
            state = session.launch_custom(compiled)
            self._json(200, {"ok": True, "error": None, "state": state})
            return
        if path == "/api/short-rest":
            extra = set(body) - {"take_rest", "plan"}
            if extra:
                raise ApiError(400, f"Unknown field(s) for short-rest: {', '.join(sorted(extra))}.")
            take_rest = body.get("take_rest", True)
            if not isinstance(take_rest, bool):
                raise ApiError(400, "'take_rest' must be a boolean.")
            plan = body.get("plan")
            if plan is not None and not isinstance(plan, dict):
                raise ApiError(400, "'plan' must be an object or null.")
            try:
                from srd2024.arena import dungeon as _dungeon_mod  # noqa: F401
            except ImportError as exc:
                raise ApiError(
                    501,
                    "Multi-room dungeon engine (srd2024.arena.dungeon) is not available yet.",
                ) from exc
            state, rest = session.submit_short_rest(take_rest=take_rest, plan=plan)
            self._json(200, {"ok": True, "error": None, "rest": rest, "state": state})
            return

        decision = body.get("decision")
        if decision is not None and not _is_int(decision):
            raise ApiError(400, "'decision' must be an integer.")

        if path == "/api/start":
            if set(body) - {"decision"}:
                raise ApiError(400, "start takes no fields; scenario, seat, seed and agents come from the command line.")
            self._json(200, {"ok": True, "error": None, "state": session.start()})
            return
        if path == "/api/action":
            decision_id, menu, view = session.current_menu()
            line = action_to_line(body, menu, view)
            state, error = session.submit(line, decision=decision if decision is not None else decision_id)
            self._json(422 if error else 200, {"ok": error is None, "error": error, "state": state})
            return
        if path == "/api/nl":
            if session.nl_driver is None:
                raise ApiError(404, "Natural-language mode is off. Launch with --nl-driver to enable it.")
            line = nl_to_line(body)
            state, error = session.submit(line, decision=decision)
            self._json(422 if error else 200, {"ok": error is None, "error": error, "state": state})
            return
        if path == "/api/save":
            if set(body) - {"decision"}:
                raise ApiError(400, "save takes no fields; the checkpoint path is fixed at launch.")
            if session.config.save_path is None:
                raise ApiError(409, "Saving is off. Launch with --save <path> to enable it.")
            state, error = session.submit("save", decision=decision)
            self._json(200, {"ok": error is None, "error": error, "state": state})
            return
        if path == "/api/quit":
            if set(body) - {"decision"}:
                raise ApiError(400, "quit takes no fields.")
            state, error = session.submit("quit", decision=decision)
            self._json(200, {"ok": error is None, "error": error, "state": state})
            return
        raise ApiError(404, "Not found.")  # pragma: no cover


_GET_API_PATHS = {"/api/state", "/api/trace", "/api/builder/catalog", "/api/builder/monsters"}
_BUILDER_POST_PATHS = {"/api/builder/validate-pc", "/api/builder/budget", "/api/builder/launch"}
_API_PATHS = {"/api/start", "/api/action", "/api/nl", "/api/save", "/api/quit", "/api/short-rest", *_BUILDER_POST_PATHS}
_ALL_API_PATHS = _GET_API_PATHS | _API_PATHS


# ---- CLI -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="srd-web",
        description=(
            "Local browser tabletop for 5e 2024 (SRD 5.2). Same seats, drivers, checkpoints and traces as "
            "`srd-arena play`, served on 127.0.0.1 only."
        ),
    )
    p.add_argument("scenario", nargs="?", default=None, help="Built-in scenario ID or path to scenario JSON (omit to open the Custom Builder lobby).")
    p.add_argument("--builder", action="store_true", default=False, help="Open the Custom Party & Encounter Builder lobby.")
    p.add_argument("--dungeon", default=None, help="Built-in dungeon ID or path to dungeon JSON for multi-room play.")
    p.add_argument("--rules", default=None, help="Path to rules.json (overrides SRD2024_RULES and dist/rules.json).")
    p.add_argument("--as", dest="play_as", default=None, help="Seat to play: a combatant ID (e.g. pc-bram), 'party', or 'dm'.")
    p.add_argument("--seat", dest="seats", action="append", default=None, help="Per-seat driver override '<combatant_id>=<driver_spec>' (repeatable).")
    p.add_argument("--party-agent", default="heuristic-tactical", help="AI driver for non-human party seats.")
    p.add_argument("--enemy-agent", default="heuristic-tactical", help="AI driver for non-human enemy seats.")
    p.add_argument("--nl-driver", default=None, help="Optional LLM driver spec for natural-language orders.")
    p.add_argument("--api-base", default=None, help="Optional API base URL for live LLM seat or NL driver.")
    p.add_argument("--api-key-env", default=None, help="Environment variable holding the LLM API key.")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--max-actions-per-turn", type=int, default=6)
    p.add_argument("--max-retries", type=int, default=2)
    p.add_argument("--save", dest="save_path", default=None, help="Checkpoint path for the Save and Quit buttons (fixed at launch).")
    p.add_argument("--resume", dest="resume_path", default=None, help="Checkpoint JSON to resume.")
    p.add_argument("--out-trace", default=None, help="Write the final MatchTranscript JSON here (fixed at launch).")
    p.add_argument("--force", action="store_true", default=False, help="Allow overwriting an existing --save or --out-trace file.")
    p.add_argument("--host", default=BIND_HOST, help="Only 127.0.0.1 is accepted.")
    p.add_argument("--port", type=int, default=0, help="Port on 127.0.0.1 (default 0: pick a free port).")
    return p


def config_from_args(args: argparse.Namespace) -> TabletopConfig:
    from srd2024.runtime.arena_bridge import _parse_scenario_arg

    if args.host != BIND_HOST:
        raise ValueError(f"srd-web only binds {BIND_HOST}; refusing --host {args.host!r}.")
    if not 0 <= int(args.port) <= 65535:
        raise ValueError("--port must be between 0 and 65535.")
    builder_mode = bool(getattr(args, "builder", False)) or (
        args.scenario is None and args.resume_path is None and getattr(args, "dungeon", None) is None
    )
    if args.max_rounds is not None and args.max_rounds < 1:
        raise ValueError("--max-rounds must be >= 1.")
    if args.max_actions_per_turn < 1:
        raise ValueError("--max-actions-per-turn must be >= 1.")
    if args.max_retries < 0:
        raise ValueError("--max-retries must be >= 0.")
    save_path = Path(args.save_path).expanduser() if args.save_path else None
    out_trace = Path(args.out_trace).expanduser() if args.out_trace else None
    resume_path = Path(args.resume_path).expanduser() if args.resume_path else None
    for label, path in (("--save", save_path), ("--out-trace", out_trace)):
        if path is not None and path.exists() and not args.force:
            raise FileExistsError(f"Refusing to overwrite '{path}' ({label}). Pass --force to replace it.")
    if save_path is not None and out_trace is not None and save_path.resolve() == out_trace.resolve():
        raise ValueError("--save and --out-trace must be different files.")
    if resume_path is not None:
        load_session_checkpoint(resume_path)
    return TabletopConfig(
        scenario=_parse_scenario_arg(args.scenario) if args.scenario is not None and not builder_mode else None,
        play_as=args.play_as,
        seat_overrides=args.seats,
        party_agent=args.party_agent,
        enemy_agent=args.enemy_agent,
        nl_driver_spec=args.nl_driver,
        api_base=args.api_base,
        api_key_env=args.api_key_env,
        temperature=args.temperature,
        seed=args.seed,
        max_rounds=args.max_rounds,
        max_actions_per_turn=args.max_actions_per_turn,
        max_retries_per_action=args.max_retries,
        save_path=save_path,
        resume_path=resume_path,
        out_trace=out_trace,
        force=bool(args.force),
        builder_mode=builder_mode,
        dungeon=getattr(args, "dungeon", None) if not builder_mode else None,
    )


def build_nl_driver(config: TabletopConfig, rules: RulesStore) -> BaseLLMDriver | None:
    if config.nl_driver_spec is None:
        return None
    drv = create_driver(
        config.nl_driver_spec,
        rules=rules,
        seed=config.seed,
        api_base=config.api_base,
        api_key_env=config.api_key_env,
        temperature=config.temperature,
    )
    if not isinstance(drv, BaseLLMDriver):
        raise ValueError(
            f"--nl-driver must be a live LLM driver spec (openai-compat:/anthropic:/gemini:), got {config.nl_driver_spec!r}."
        )
    return drv


def main(argv: list[str] | None = None) -> int:
    from srd2024.runtime.rules_bridge import load_rules_store

    args = build_parser().parse_args(argv)
    try:
        config = config_from_args(args)
        rules = load_rules_store(args.rules)
        session = TabletopSession(config, rules=rules, nl_driver=build_nl_driver(config, rules))
        server = TabletopServer(session, port=args.port)
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print("srd-web: local 5e 2024 (SRD 5.2) tabletop on 127.0.0.1 only. Open this URL (it carries this launch's token):")
    print(server.url, flush=True)
    print("Press Ctrl-C to stop. A running session pauses (and saves when --save was given).", file=sys.stderr, flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        session.shutdown()
        server.server_close()
    return 0


__all__ = [
    "ApiError",
    "TabletopConfig",
    "TabletopServer",
    "TabletopSession",
    "WebHumanDriver",
    "action_to_line",
    "build_parser",
    "client_view",
    "config_from_args",
    "main",
    "nl_to_line",
]
