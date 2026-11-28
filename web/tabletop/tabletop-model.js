/**
 * Pure state, menu, and click logic for the local tabletop served by
 * `bin/srd-web`. No DOM; every function here runs in Node tests.
 *
 * The server is the authority: the browser only proposes an action, and
 * the engine accepts or rejects it. These helpers fail closed: a response
 * that does not match the expected shape renders no board and no actions.
 * All strings from the server are untrusted (they include LLM table talk)
 * and are only ever rendered with textContent.
 */

import { tokenLabels, validateBounds, validateTerrain } from "../lib/replay-model.js";

export const STATUSES = new Set(["ready", "running", "awaiting_input", "finished", "paused", "error"]);
export const CATEGORIES = ["attack", "spell", "move", "tactical", "turn"];
export const CATEGORY_TITLES = { attack: "Attacks", spell: "Spells", move: "Movement", tactical: "Actions", turn: "Turn" };
const TOKEN_RE = /^[A-Za-z0-9_-]{20,128}$/;
const MAX_ITEMS = 400;
const MAX_UNITS = 200;
const COORD = 100000;
/** Board padding around the furthest token, and the most extra board drawn for movement. */
const PAD_FT = 10;
const MOVE_VIEW_CAP_FT = 60;

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isInt = (v) => Number.isInteger(v);
const isStr = (v) => typeof v === "string";
const isPos = (p) => isObj(p) && isInt(p.x) && isInt(p.y) && Math.abs(p.x) <= COORD && Math.abs(p.y) <= COORD;
const strList = (v) => Array.isArray(v) && v.every(isStr);
const snap = (v) => Math.floor(v / 5) * 5;

/**
 * Read the launch token from `#token=...`. The fragment is never sent to
 * the server, and the page removes it from the address bar after reading.
 * @param {string} hash
 * @returns {string | null}
 */
export function readToken(hash) {
  const m = /^#token=([^&]*)$/.exec(String(hash || ""));
  return m && TOKEN_RE.test(m[1]) ? m[1] : null;
}

/**
 * Validate one `/api/state` payload. Anything unexpected is an error, and
 * the page then shows the errors instead of a board or menu.
 * @param {unknown} raw
 * @returns {{ ok: true, errors: [], state: object } | { ok: false, errors: string[], state: null }}
 */
export function validateState(raw) {
  const errors = [];
  const fail = (msg) => {
    errors.push(msg);
    return { ok: false, errors, state: null };
  };
  if (!isObj(raw)) return fail("The server reply is not a JSON object.");
  if (!STATUSES.has(raw.status)) return fail(`Unknown session status ${JSON.stringify(String(raw.status)).slice(0, 40)}.`);
  if (!isObj(raw.features) || !["nl", "save", "out_trace", "resume"].every((k) => typeof raw.features[k] === "boolean")) {
    errors.push("features is malformed.");
  }
  if (!Array.isArray(raw.log) || !raw.log.every((l) => isObj(l) && isInt(l.seq) && isStr(l.text))) errors.push("log is malformed.");
  if (!isInt(raw.log_next)) errors.push("log_next must be an integer.");
  if (raw.decision !== null && !isInt(raw.decision)) errors.push("decision must be an integer or null.");
  const menu = checkMenu(raw.menu);
  errors.push(...menu);
  if (raw.view !== null) errors.push(...checkView(raw.view));
  if (raw.status === "awaiting_input" && (raw.view === null || raw.decision === null || !raw.menu.length)) {
    errors.push("The server is waiting for input but sent no menu.");
  }
  for (const key of ["error", "notice", "pause_reason"]) {
    if (raw[key] !== null && raw[key] !== undefined && !isStr(raw[key])) errors.push(`${key} must be a string or null.`);
  }
  if (raw.result !== null && raw.result !== undefined && !isObj(raw.result)) errors.push("result must be an object or null.");
  if (errors.length) return { ok: false, errors: errors.slice(0, 12), state: null };
  return { ok: true, errors: [], state: raw };
}

function checkMenu(menu) {
  if (!Array.isArray(menu)) return ["menu must be a list."];
  if (menu.length > MAX_ITEMS) return ["menu is too long."];
  const out = [];
  menu.forEach((m, i) => {
    const ok =
      isObj(m) && m.index === i + 1 && CATEGORIES.includes(m.category) && isStr(m.label) && isStr(m.summary) &&
      isObj(m.intent) && isStr(m.intent.type) && isObj(m.meta);
    if (!ok) out.push(`menu item ${i + 1} is malformed.`);
  });
  return out.slice(0, 4);
}

function checkView(view) {
  if (!isObj(view)) return ["view must be an object."];
  const out = [];
  const me = view.self;
  if (!isObj(me) || !isStr(me.id) || !isStr(me.name) || !isPos(me.position) || !isInt(me.hp) || !isInt(me.hp_max) ||
    !isInt(me.ac) || !isInt(me.movement_remaining_ft) || !isInt(me.speed_ft) || !strList(me.conditions) || !isObj(me.resources)) {
    out.push("view.self is malformed.");
  }
  if (!isInt(view.round)) out.push("view.round must be an integer.");
  if (!Array.isArray(view.allies) || !Array.isArray(view.enemies)) return [...out, "view.allies and view.enemies must be lists."];
  if (view.allies.length + view.enemies.length > MAX_UNITS) out.push("Too many combatants.");
  for (const a of view.allies) {
    if (!isObj(a) || !isStr(a.id) || !isStr(a.name) || !isPos(a.position) || !isInt(a.hp) || !isInt(a.hp_max) || !isInt(a.ac) ||
      typeof a.alive !== "boolean" || !strList(a.conditions)) {
      out.push("an ally entry is malformed.");
      break;
    }
  }
  for (const e of view.enemies) {
    // Opponents carry a public HP band only. Exact HP or AC here means the payload is wrong, so refuse it.
    if (!isObj(e) || !isStr(e.id) || !isStr(e.name) || !isPos(e.position) || typeof e.alive !== "boolean" || !isStr(e.hp_band) ||
      !strList(e.conditions) || "hp" in e || "hp_max" in e || "ac" in e) {
      out.push("an opponent entry is malformed.");
      break;
    }
  }
  if (!Array.isArray(view.table_talk) || !Array.isArray(view.recent_events)) out.push("view talk and events must be lists.");
  if ("bounds" in view && view.bounds !== null) out.push(...validateBounds(view.bounds));
  if ("terrain" in view && view.terrain !== null) out.push(...validateTerrain(view.terrain));
  return out;
}

/**
 * Units and roster for board-view.js from the active seat's view.
 * Opponents get `hpMax: null` and `ac: null`, so the shared painter shows
 * their public band instead of numbers.
 * @param {object} view a validated view
 */
export function boardModel(view) {
  const me = view.self;
  const mySide = me.side === "enemy" ? "enemy" : "party";
  const theirSide = mySide === "party" ? "enemy" : "party";
  const rows = [
    { id: me.id, name: me.name, side: mySide, hpMax: me.hp_max, ac: me.ac, unit: unitOf(me, me.position, me.hp, true) },
    ...view.allies.map((a) => ({ id: a.id, name: a.name, side: mySide, hpMax: a.hp_max, ac: a.ac, unit: unitOf(a, a.position, a.hp, a.alive) })),
    ...view.enemies.map((e) => ({ id: e.id, name: e.name, side: theirSide, hpMax: null, ac: null, unit: { ...unitOf(e, e.position, 0, e.alive), hpBand: e.hp_band } })),
  ];
  const labels = tokenLabels(rows.map((r) => r.name));
  const roster = new Map(rows.map((r, i) => [r.id, { id: r.id, name: r.name, side: r.side, hpMax: r.hpMax, ac: r.ac, label: labels[i] }]));
  const frame = { activeId: me.id, units: rows.map((r) => r.unit) };
  return {
    roster,
    frame,
    bounds: boardBoundsFor(view),
    battlefieldBounds: view.bounds || null,
    terrain: view.terrain || null,
  };
}

function unitOf(row, pos, hp, alive) {
  const ds = isObj(row.death_saves) ? row.death_saves : {};
  return {
    id: row.id,
    x: pos.x,
    y: pos.y,
    hp,
    alive,
    conditions: [...row.conditions].sort(),
    concentration: isStr(row.concentration) ? row.concentration : null,
    effects: strList(row.effects) ? [...row.effects].sort() : [],
    deathSaves: { successes: isInt(ds.successes) ? ds.successes : 0, failures: isInt(ds.failures) ? ds.failures : 0 },
  };
}

/**
 * Board extent: every token plus padding, plus the active combatant's
 * movement (capped) so reachable squares are visible and clickable.
 */
export function boardBoundsFor(view) {
  const pts = [view.self.position, ...view.allies.map((a) => a.position), ...view.enemies.map((e) => e.position)];
  const reach = Math.min(MOVE_VIEW_CAP_FT, Math.max(0, view.self.movement_remaining_ft));
  let minX = Math.min(...pts.map((p) => p.x)) - PAD_FT;
  let minY = Math.min(...pts.map((p) => p.y)) - PAD_FT;
  let maxX = Math.max(...pts.map((p) => p.x)) + PAD_FT;
  let maxY = Math.max(...pts.map((p) => p.y)) + PAD_FT;
  const room = view.bounds || null;
  if (room) {
    minX = Math.min(minX, room.min_x - 5);
    minY = Math.min(minY, room.min_y - 5);
    maxX = Math.max(maxX, room.max_x + 5);
    maxY = Math.max(maxY, room.max_y + 5);
  } else {
    minX = Math.min(minX, view.self.position.x - reach);
    minY = Math.min(minY, view.self.position.y - reach);
    maxX = Math.max(maxX, view.self.position.x + reach);
    maxY = Math.max(maxY, view.self.position.y + reach);
  }
  const terrain = view.terrain || null;
  if (terrain) {
    for (const pair of [...(terrain.walls || []), ...(terrain.difficult_terrain || [])]) {
      minX = Math.min(minX, pair[0] - 5);
      minY = Math.min(minY, pair[1] - 5);
      maxX = Math.max(maxX, pair[0] + 5);
      maxY = Math.max(maxY, pair[1] + 5);
    }
    for (const c of terrain.cover || []) {
      minX = Math.min(minX, c.x - 5);
      minY = Math.min(minY, c.y - 5);
      maxX = Math.max(maxX, c.x + 5);
      maxY = Math.max(maxY, c.y + 5);
    }
  }
  const x = snap(minX);
  const y = snap(minY);
  return { x, y, w: snap(maxX) + 5 - x, h: snap(maxY) + 5 - y };
}

/** 5e grid distance in feet (Chebyshev, 5 ft per square). */
export function distanceFt(a, b) {
  return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y));
}

function inRoomBounds(x, y, room) {
  if (!room) return true;
  return x >= room.min_x && x <= room.max_x && y >= room.min_y && y <= room.max_y;
}

/**
 * Squares the active combatant could walk to with its remaining movement,
 * inside the board (and battlefield bounds / around walls & difficult terrain),
 * not counting occupied squares. This is a hint for the highlight; the engine
 * still validates every move.
 */
export function reachableSquares(view, bounds) {
  const me = view.self;
  if (!me.resources || me.speed_ft <= 0) return [];
  const isProne = me.conditions.includes("prone");
  const baseCost = isProne ? 2 : 1;
  const budget = me.movement_remaining_ft;
  if (budget < 5 * baseCost) return [];
  const taken = occupiedSquares(view);
  const room = view.bounds || null;
  const terrain = view.terrain || null;
  const walls = new Set((terrain && terrain.walls ? terrain.walls : []).map(([wx, wy]) => `${wx},${wy}`));
  const diff = new Set((terrain && terrain.difficult_terrain ? terrain.difficult_terrain : []).map(([dx, dy]) => `${dx},${dy}`));

  if (walls.size > 0 || diff.size > 0) {
    const sx = snap(me.position.x);
    const sy = snap(me.position.y);
    const dist = new Map([[`${sx},${sy}`, 0]]);
    const queue = [{ x: sx, y: sy, cost: 0 }];
    while (queue.length) {
      queue.sort((a, b) => a.cost - b.cost);
      const cur = queue.shift();
      const key = `${cur.x},${cur.y}`;
      if (cur.cost > dist.get(key)) continue;
      for (const stepX of [-5, 0, 5]) {
        for (const stepY of [-5, 0, 5]) {
          if (stepX === 0 && stepY === 0) continue;
          const nx = cur.x + stepX;
          const ny = cur.y + stepY;
          if (nx < bounds.x || nx >= bounds.x + bounds.w || ny < bounds.y || ny >= bounds.y + bounds.h) continue;
          if (!inRoomBounds(nx, ny, room)) continue;
          const nKey = `${nx},${ny}`;
          if (walls.has(nKey)) continue;
          if (stepX !== 0 && stepY !== 0 && (walls.has(`${cur.x + stepX},${cur.y}`) || walls.has(`${cur.x},${cur.y + stepY}`))) {
            continue;
          }
          const stepMult = 1 + (isProne ? 1 : 0) + (diff.has(nKey) ? 1 : 0);
          const nextCost = cur.cost + 5 * stepMult;
          if (nextCost > budget) continue;
          if (nextCost < (dist.get(nKey) ?? Infinity)) {
            dist.set(nKey, nextCost);
            queue.push({ x: nx, y: ny, cost: nextCost });
          }
        }
      }
    }
    const out = [];
    for (const key of dist.keys()) {
      if (key === `${sx},${sy}` || taken.has(key)) continue;
      const [x, y] = key.split(",").map(Number);
      out.push({ x, y });
    }
    return out;
  }

  const out = [];
  for (let x = bounds.x; x < bounds.x + bounds.w; x += 5) {
    for (let y = bounds.y; y < bounds.y + bounds.h; y += 5) {
      if (!inRoomBounds(x, y, room)) continue;
      const d = distanceFt(me.position, { x, y });
      if (d === 0 || d * baseCost > budget || taken.has(`${x},${y}`)) continue;
      out.push({ x, y });
    }
  }
  return out;
}

function occupiedSquares(view) {
  const taken = new Set();
  for (const u of [view.self, ...view.allies, ...view.enemies]) {
    if (u.alive === false) continue;
    taken.add(`${snap(u.position.x)},${snap(u.position.y)}`);
  }
  return taken;
}

/** Snap a board point in feet to its 5-ft square. */
export function squareAt(x, y) {
  return { x: snap(x), y: snap(y) };
}

/** Menu items that act on one combatant (attack, spell, help, heal). */
export function itemsTargeting(menu, id) {
  return menu.filter((m) => {
    const it = m.intent;
    if (it.type === "end_turn" || it.type === "move" || it.type === "reroll") return false;
    if (it.target_id === id) return true;
    return Array.isArray(it.target_ids) && it.target_ids.includes(id);
  });
}

/**
 * What a click on a square means. Returns one of
 * - `{ type: "move", body }` for a reachable empty square,
 * - `{ type: "targets", id, name, items }` for a combatant with legal actions on it,
 * - `{ type: "none", reason }` otherwise (nothing is sent).
 * @param {object} view a validated view
 * @param {object[]} menu a validated menu
 * @param {{ x: number, y: number }} sq
 * @param {{ x: number, y: number }[]} reachable
 */
export function clickAction(view, menu, sq, reachable) {
  if (!menu.length) return { type: "none", reason: "It is not your turn." };
  const here = [view.self, ...view.allies, ...view.enemies].filter(
    (u) => u.alive !== false && snap(u.position.x) === sq.x && snap(u.position.y) === sq.y,
  );
  if (here.length) {
    for (const u of here) {
      const items = itemsTargeting(menu, u.id);
      if (items.length) return { type: "targets", id: u.id, name: u.name, items };
    }
    if (here.some((u) => u.id === view.self.id)) return { type: "none", reason: "That is your square." };
    return { type: "none", reason: `No legal action targets ${here[0].name} right now.` };
  }
  if (reachable.some((r) => r.x === sq.x && r.y === sq.y)) {
    return { type: "move", body: { kind: "move", x: sq.x, y: sq.y } };
  }
  if (view.bounds && !inRoomBounds(sq.x, sq.y, view.bounds)) {
    return { type: "none", reason: `(${sq.x}, ${sq.y}) is outside the battlefield bounds.` };
  }
  if (view.terrain && Array.isArray(view.terrain.walls) && view.terrain.walls.some(([wx, wy]) => wx === sq.x && wy === sq.y)) {
    return { type: "none", reason: `(${sq.x}, ${sq.y}) is blocked by a wall.` };
  }
  return { type: "none", reason: `(${sq.x}, ${sq.y}) is out of your movement this turn.` };
}

/** Menu grouped by category, in a fixed order. */
export function groupMenu(menu) {
  return CATEGORIES.map((category) => ({ category, title: CATEGORY_TITLES[category], items: menu.filter((m) => m.category === category) })).filter(
    (g) => g.items.length,
  );
}

/** Request body for a menu item. */
export function menuBody(item, decision) {
  return { kind: "menu", index: item.index, decision };
}

/**
 * Move body from the typed coordinates of the keyboard form, or an error.
 * @returns {{ ok: true, body: object } | { ok: false, error: string }}
 */
export function moveBodyFromInput(xText, yText, decision) {
  const x = Number(String(xText).trim());
  const y = Number(String(yText).trim());
  if (!Number.isInteger(x) || !Number.isInteger(y) || String(xText).trim() === "" || String(yText).trim() === "") {
    return { ok: false, error: "Enter whole numbers of feet for x and y." };
  }
  if (x % 5 || y % 5) return { ok: false, error: "x and y must be multiples of 5 (one square is 5 ft)." };
  return { ok: true, body: { kind: "move", x, y, decision } };
}

/** CSS kind for one line of the session's text stream (the same lines the terminal prints). */
export function logKind(text) {
  const t = String(text).trim();
  if (t.startsWith("! Rejected")) return "violation";
  if (/^\[[^\]]+ says\]:/.test(t)) return "talk";
  if (t.startsWith("* ")) return "hook";
  if (t.startsWith("---") || t.startsWith("===")) return "head";
  if (t.startsWith("[")) return "sys";
  return "plain";
}

/** Lines in `incoming` that are newer than `lastSeq`, so a poll and a POST reply never duplicate. */
export function newLines(incoming, lastSeq) {
  return incoming.filter((l) => l.seq > lastSeq).sort((a, b) => a.seq - b.seq);
}

/** Safe download name from a Content-Disposition header. */
export function downloadName(header) {
  const m = /filename="([A-Za-z0-9._-]{1,120})"/.exec(String(header || ""));
  return m ? m[1] : "srd-web.trace.json";
}

/** Short human status line. */
export function statusText(state) {
  switch (state.status) {
    case "ready":
      return "Starting…";
    case "running":
      return "AI seats are acting…";
    case "awaiting_input":
      return `Your move: ${state.view ? state.view.self.name : ""}`;
    case "finished":
      return state.result ? `Match over · ${String(state.result.winner)} wins` : "Match over";
    case "paused":
      return state.pause_reason === "save" ? "Paused after save" : "Session ended";
    default:
      return "Session error";
  }
}

/** Stable identifier for the active human turn (`"<round>:<actor_id>"`), or null if no view. */
export function turnKey(stateOrView) {
  const view = isObj(stateOrView) && "view" in stateOrView ? stateOrView.view : stateOrView;
  if (!isObj(view) || !isObj(view.self) || !isStr(view.self.id) || !isInt(view.round)) return null;
  return `${view.round}:${view.self.id}`;
}

const STOP_HOOK_RE = /dropped|move ends|stopped|opportunity attack|incapacitated|blocked|occupied|bounds/i;

function moveStopReason(actionOutcome, incomingLog) {
  if (isObj(actionOutcome) && isStr(actionOutcome.reason) && actionOutcome.reason.trim()) {
    return actionOutcome.reason.trim();
  }
  const hooks = [];
  if (isObj(actionOutcome) && Array.isArray(actionOutcome.narrative_hooks)) {
    for (const h of actionOutcome.narrative_hooks) {
      if (isStr(h) && h.trim()) hooks.push(h.trim());
    }
  }
  if (Array.isArray(incomingLog)) {
    for (const line of incomingLog) {
      const text = isObj(line) && isStr(line.text) ? line.text.trim() : isStr(line) ? line.trim() : "";
      if (!text) continue;
      if (text.startsWith("* ")) hooks.push(text.slice(2).trim());
    }
  }
  const matched = hooks.find((h) => STOP_HOOK_RE.test(h));
  if (matched) return matched;
  if (isObj(actionOutcome) && Array.isArray(actionOutcome.events)) {
    const oa = actionOutcome.events.find((e) => isObj(e) && e.type === "opportunity_attack");
    if (oa) return "stopped by an Opportunity Attack.";
  }
  return "";
}

/**
 * Compute the next clickNote text after a state refresh (#83).
 *
 * - Clears on new turn (round or active seat changed), terminal state, or action error.
 * - Replaces "Moving to (x, y)…" with the resolved move outcome ("Moved to (x, y)." or
 *   "Move stopped at (x, y): ...") based on `actionOutcome.state_diff.position_changes`,
 *   `actionOutcome.events`, or the updated `nextState.view.self.position`.
 * - Clears any stale note after any other action completes.
 *
 * @param {{
 *   prevState?: object | null,
 *   nextState?: object | null,
 *   currentNote?: string,
 *   pendingMove?: { actorId?: string, from?: { x: number, y: number }, to: { x: number, y: number } } | null,
 *   incomingLog?: Array<{ seq?: number, text: string } | string>,
 *   actionError?: string | null,
 *   actionOutcome?: { events?: object[], state_diff?: { position_changes?: object[] }, narrative_hooks?: string[], reason?: string } | null,
 *   afterAction?: boolean,
 * }} opts
 * @returns {string}
 */
export function resolveClickNote({
  prevState = null,
  nextState = null,
  currentNote = "",
  pendingMove = null,
  incomingLog = [],
  actionError = null,
  actionOutcome = null,
  afterAction = false,
} = {}) {
  if (!isObj(nextState)) return "";
  if (nextState.status === "finished" || nextState.status === "paused" || nextState.status === "error") {
    return "";
  }
  const prevTurn = turnKey(prevState);
  const nextTurn = turnKey(nextState);
  if (prevTurn && nextTurn && prevTurn !== nextTurn) return "";
  if (actionError) return "";

  const decisionChanged = isObj(prevState) && prevState.decision !== nextState.decision;
  const actionResolved = Boolean(afterAction || actionOutcome || decisionChanged || nextState.status !== "running");

  if (isObj(pendingMove) && isPos(pendingMove.to) && actionResolved) {
    let resolvedPos = null;
    if (isObj(actionOutcome?.state_diff) && Array.isArray(actionOutcome.state_diff.position_changes)) {
      const changes = actionOutcome.state_diff.position_changes;
      const match = changes.find(
        (c) => isObj(c) && (!pendingMove.actorId || c.id === pendingMove.actorId) && isPos(c.after),
      );
      if (match) resolvedPos = match.after;
      else if (isPos(pendingMove.from)) resolvedPos = pendingMove.from;
      else if (isPos(nextState.view?.self?.position)) resolvedPos = nextState.view.self.position;
    } else if (Array.isArray(actionOutcome?.events)) {
      const movedEv = actionOutcome.events.find(
        (e) => isObj(e) && e.type === "moved" && (!pendingMove.actorId || e.actor_id === pendingMove.actorId) && isPos(e.to_pos),
      );
      if (movedEv) resolvedPos = movedEv.to_pos;
      else if (isPos(pendingMove.from)) resolvedPos = pendingMove.from;
      else if (isPos(nextState.view?.self?.position)) resolvedPos = nextState.view.self.position;
    } else if (isPos(nextState.view?.self?.position)) {
      resolvedPos = nextState.view.self.position;
    } else if (isPos(pendingMove.from)) {
      resolvedPos = pendingMove.from;
    }

    if (!resolvedPos) return "";
    if (resolvedPos.x === pendingMove.to.x && resolvedPos.y === pendingMove.to.y) {
      return `Moved to (${resolvedPos.x}, ${resolvedPos.y}).`;
    }
    const reason = moveStopReason(actionOutcome, incomingLog);
    return reason
      ? `Move stopped at (${resolvedPos.x}, ${resolvedPos.y}): ${reason}`
      : `Move stopped at (${resolvedPos.x}, ${resolvedPos.y}).`;
  }

  if (afterAction || decisionChanged || nextState.status === "running") return "";
  return isStr(currentNote) ? currentNote : "";
}
