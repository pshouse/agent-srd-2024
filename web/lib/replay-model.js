/**
 * Pure trace parsing, validation, and state reconstruction for `#/replay`.
 * No DOM. Every function here runs in Node tests.
 *
 * A trace is untrusted input: it may be a local file, and it carries
 * LLM-written table talk. Nothing is rendered unless the whole trace
 * validates and stepping through every action reproduces final_state.
 */

export const TRACE_SCHEMA_VERSION = "4";
export const INDEX_SCHEMA_VERSION = "1";
export const DATASET_ID = "srd-2024-5.2";
export const MAX_LOCAL_BYTES = 20 * 1024 * 1024;

const LIMITS = { combatants: 200, actions: 20000, events: 2000, coord: 100000, errors: 12 };
const SIDES = new Set(["party", "enemy"]);
const BLOCKED = new Set(["__proto__", "constructor", "prototype"]);
const HEX64 = /^[0-9a-f]{64}$/;
const SAFE_ID = /^[a-z0-9][a-z0-9._-]{0,120}$/;
const SAFE_FILE = /^[a-z0-9][a-z0-9._-]{0,120}\.trace\.json$/;
const SAFE_WRITEUP = /^examples\/sessions\/[a-z0-9][a-z0-9._-]{0,120}\.md$/;

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isInt = (v) => Number.isInteger(v);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isStr = (v) => typeof v === "string";
const optStr = (v) => v == null || typeof v === "string";
const safeKey = (v) => isStr(v) && v.length > 0 && v.length <= 200 && !BLOCKED.has(v);

/**
 * @param {string} text
 * @returns {{ ok: true, trace: object } | { ok: false, errors: string[] }}
 */
export function parseTraceText(text) {
  if (typeof text !== "string") return { ok: false, errors: ["The file is not text."] };
  if (text.length > MAX_LOCAL_BYTES) return { ok: false, errors: ["The file is over 20 MB."] };
  let trace;
  try {
    trace = JSON.parse(text);
  } catch {
    return { ok: false, errors: ["The file is not valid JSON."] };
  }
  return { ok: true, trace };
}

/**
 * Report every shape problem (capped). An empty array means buildReplay may run.
 * @param {unknown} trace
 * @returns {string[]}
 */
export function validateTrace(trace) {
  const errors = [];
  const fail = (msg) => {
    if (errors.length < LIMITS.errors) errors.push(msg);
  };
  if (!isObj(trace)) return ["The trace is not a JSON object."];
  if (trace.schema_version !== TRACE_SCHEMA_VERSION) {
    return [`schema_version is ${show(trace.schema_version)} (expected "${TRACE_SCHEMA_VERSION}").`];
  }
  if (trace.dataset_id !== DATASET_ID) fail(`dataset_id is ${show(trace.dataset_id)} (expected "${DATASET_ID}").`);
  const sc = trace.scenario;
  if (!isObj(sc) || !isStr(sc.id) || !isStr(sc.name)) fail("scenario.id and scenario.name must be strings.");
  if (!isObj(trace.config) || !isInt(trace.config.seed)) fail("config.seed must be an integer.");
  if (!isStr(trace.winner)) fail("winner must be a string.");
  if (!isInt(trace.rounds) || trace.rounds < 0) fail("rounds must be a non-negative integer.");
  if (!isStr(trace.digest) || !HEX64.test(trace.digest)) fail("digest must be a 64-character hex string.");
  if (trace.scorecard != null && !isObj(trace.scorecard)) fail("scorecard must be an object.");
  if (!Array.isArray(trace.violations)) fail("violations must be a list.");
  if ("rejected_inputs" in trace && !Array.isArray(trace.rejected_inputs)) fail("rejected_inputs must be a list.");

  const ids = new Set();
  for (const key of ["initial_state", "final_state"]) {
    const state = trace[key];
    if (!isObj(state) || !isObj(state.combatants)) {
      fail(`${key}.combatants must be an object.`);
      continue;
    }
    const entries = Object.entries(state.combatants);
    if (!entries.length) fail(`${key} has no combatants.`);
    if (entries.length > LIMITS.combatants) fail(`${key} has more than ${LIMITS.combatants} combatants.`);
    for (const [id, row] of entries) {
      if (key === "initial_state") ids.add(id);
      else if (!ids.has(id)) fail(`final_state has combatant ${show(id)} that is not in initial_state.`);
      checkCombatant(id, row, `${key}.${id}`, fail);
    }
    if (key === "final_state" && entries.length !== ids.size) fail("final_state and initial_state list different combatants.");
  }
  const init = isObj(trace.initial_state) ? trace.initial_state : {};
  if (!Array.isArray(init.turn_order) || !init.turn_order.every((id) => ids.has(id))) {
    fail("initial_state.turn_order must list combatant ids.");
  }
  if (!isInt(init.active_index)) fail("initial_state.active_index must be an integer.");

  if (!Array.isArray(trace.actions)) {
    fail("actions must be a list.");
    return errors;
  }
  if (trace.actions.length > LIMITS.actions) fail(`actions has more than ${LIMITS.actions} entries.`);
  let lastSeq = -Infinity;
  trace.actions.forEach((action, i) => {
    const at = `actions[${i}]`;
    if (!isObj(action)) return fail(`${at} is not an object.`);
    if (!isInt(action.seq) || action.seq <= lastSeq) fail(`${at}.seq must be an increasing integer.`);
    lastSeq = isInt(action.seq) ? action.seq : lastSeq;
    if (!isInt(action.round) || !isInt(action.turn_index)) fail(`${at}.round and turn_index must be integers.`);
    if (!ids.has(action.actor_id)) fail(`${at}.actor_id is not a combatant.`);
    if (!optStr(action.actor_name) || !optStr(action.driver) || !optStr(action.side)) fail(`${at} has a non-string name, driver, or side.`);
    if (!optStr(action.table_talk) || !optStr(action.rationale)) fail(`${at}.table_talk and rationale must be strings or null.`);
    if (!isObj(action.intent) || !optStr(action.intent.type)) fail(`${at}.intent must be an object.`);
    if (action.narrative_hooks != null && !(Array.isArray(action.narrative_hooks) && action.narrative_hooks.every(isStr))) {
      fail(`${at}.narrative_hooks must be a list of strings.`);
    }
    if (!Array.isArray(action.events) || action.events.length > LIMITS.events || !action.events.every((e) => isObj(e) && isStr(e.type))) {
      fail(`${at}.events must be a list of typed events.`);
    }
    checkDiff(action.state_diff, ids, at, fail);
  });
  return errors;
}

function checkCombatant(id, row, at, fail) {
  if (!safeKey(id)) return fail(`${at}: unsafe combatant id.`);
  if (!isObj(row)) return fail(`${at} is not an object.`);
  if (row.id !== id) fail(`${at}.id does not match its key.`);
  if (!isStr(row.name)) fail(`${at}.name must be a string.`);
  if (!SIDES.has(row.side)) fail(`${at}.side is ${show(row.side)} (expected "party" or "enemy").`);
  if (!isPos(row.position)) fail(`${at}.position must be integer feet {x, y}.`);
  if (!isInt(row.hp) || !isInt(row.hp_max) || row.hp_max <= 0) fail(`${at}.hp and hp_max must be integers.`);
  if (!isNum(row.ac)) fail(`${at}.ac must be a number.`);
  if (typeof row.alive !== "boolean") fail(`${at}.alive must be true or false.`);
  if (!Array.isArray(row.conditions) || !row.conditions.every((c) => isObj(c) && isStr(c.condition))) {
    fail(`${at}.conditions must be a list of {condition}.`);
  }
  if (row.concentration != null && !(isObj(row.concentration) && isStr(row.concentration.spell))) {
    fail(`${at}.concentration must be null or {spell}.`);
  }
  if (row.active_effects != null && !(Array.isArray(row.active_effects) && row.active_effects.every((e) => isObj(e) && isStr(e.kind)))) {
    fail(`${at}.active_effects must be a list of {kind}.`);
  }
}

function checkDiff(diff, ids, at, fail) {
  if (!isObj(diff)) return fail(`${at}.state_diff must be an object.`);
  for (const key of ["hp_changes", "position_changes", "condition_changes"]) {
    if (!Array.isArray(diff[key])) return fail(`${at}.state_diff.${key} must be a list.`);
  }
  if (diff.effect_changes != null && !Array.isArray(diff.effect_changes)) return fail(`${at}.state_diff.effect_changes must be a list.`);
  for (const c of diff.hp_changes) {
    if (!isObj(c) || !ids.has(c.id) || !isInt(c.before) || !isInt(c.after)) return fail(`${at}.state_diff.hp_changes has a bad row.`);
  }
  for (const c of diff.position_changes) {
    if (!isObj(c) || !ids.has(c.id) || !isPos(c.after)) return fail(`${at}.state_diff.position_changes has a bad row.`);
  }
  for (const key of ["condition_changes", "effect_changes"]) {
    for (const c of diff[key] || []) {
      if (!isObj(c) || !ids.has(c.id) || !strList(c.added) || !strList(c.removed)) return fail(`${at}.state_diff.${key} has a bad row.`);
    }
  }
}

const strList = (v) => Array.isArray(v) && v.every(isStr);
const isPos = (p) => isObj(p) && isInt(p.x) && isInt(p.y) && Math.abs(p.x) <= LIMITS.coord && Math.abs(p.y) <= LIMITS.coord;

/**
 * Snapshot of one combatant at a step. Arrays are sorted copies.
 * @typedef {{ id: string, x: number, y: number, hp: number, alive: boolean, conditions: string[], concentration: string | null, effects: string[], deathSaves: { successes: number, failures: number } }} Unit
 */

function unitFrom(row) {
  return {
    id: row.id,
    x: row.position.x,
    y: row.position.y,
    hp: row.hp,
    alive: row.alive,
    conditions: row.conditions.map((c) => c.condition).sort(),
    concentration: row.concentration ? row.concentration.spell : null,
    effects: (row.active_effects || []).map((e) => e.kind).sort(),
    deathSaves: {
      successes: isObj(row.death_saves) && isInt(row.death_saves.successes) ? row.death_saves.successes : 0,
      failures: isObj(row.death_saves) && isInt(row.death_saves.failures) ? row.death_saves.failures : 0,
    },
  };
}

function cloneUnit(u) {
  return { ...u, conditions: [...u.conditions], effects: [...u.effects], deathSaves: { ...u.deathSaves } };
}

/**
 * Apply one action to a unit map. state_diff carries HP, position, conditions,
 * and effects. Concentration, defeat, and death saves come from events,
 * because state_diff does not record them.
 * @param {Map<string, Unit>} units
 * @param {object} action
 * @returns {string[]} problems
 */
export function applyAction(units, action) {
  const problems = [];
  const d = action.state_diff;
  for (const c of d.hp_changes) {
    const u = units.get(c.id);
    if (u.hp !== c.before) problems.push(`seq ${action.seq}: ${c.id} HP is ${u.hp} but the diff starts from ${c.before}.`);
    u.hp = c.after;
  }
  for (const c of d.position_changes) {
    const u = units.get(c.id);
    u.x = c.after.x;
    u.y = c.after.y;
  }
  for (const c of d.condition_changes) applyNames(units.get(c.id), "conditions", c);
  for (const c of d.effect_changes || []) applyNames(units.get(c.id), "effects", c);
  for (const e of action.events) {
    const who = units.get(e.actor_id);
    if (e.type === "concentration_started" && who && isStr(e.spell)) who.concentration = e.spell;
    else if (e.type === "concentration_ended" && who) who.concentration = null;
    // Monsters are defeated at 0 HP; PCs die from three failed death saves or massive damage (combatant_died).
    else if ((e.type === "combatant_defeated" || e.type === "combatant_died") && units.has(e.combatant_id)) {
      units.get(e.combatant_id).alive = false;
    }
    else if (e.type === "death_save" && who && isInt(e.successes) && isInt(e.failures)) {
      who.deathSaves = { successes: e.successes, failures: e.failures };
    } else if (e.type === "death_save_damage_failure" && units.has(e.target_id) && isInt(e.failures_total)) {
      units.get(e.target_id).deathSaves.failures = e.failures_total;
    } else if (e.type === "healing_applied" && units.has(e.target_id) && e.hp_before === 0 && isInt(e.hp_after) && e.hp_after > 0) {
      units.get(e.target_id).deathSaves = { successes: 0, failures: 0 };
    }
  }
  return problems;
}

function applyNames(unit, key, change) {
  const next = [...unit[key]];
  for (const name of change.removed) {
    const at = next.indexOf(name);
    if (at !== -1) next.splice(at, 1);
  }
  for (const name of change.added) if (key === "effects" || !next.includes(name)) next.push(name);
  unit[key] = next.sort();
}

/**
 * Validate, then rebuild every step. frames[0] is initial_state and
 * frames[i] is the state after actions[i - 1].
 * @param {unknown} trace
 * @returns {{ ok: true, errors: [], replay: Replay } | { ok: false, errors: string[], replay: null }}
 */
export function buildReplay(trace) {
  const errors = validateTrace(trace);
  if (errors.length) return { ok: false, errors, replay: null };
  const init = trace.initial_state;
  const roster = Object.values(init.combatants).map((row) => ({
    id: row.id,
    name: row.name,
    side: row.side,
    kind: isStr(row.kind) ? row.kind : "",
    hpMax: row.hp_max,
    ac: row.ac,
    label: "",
  }));
  tokenLabels(roster.map((r) => r.name)).forEach((label, i) => {
    roster[i].label = label;
  });
  const units = new Map(Object.values(init.combatants).map((row) => [row.id, unitFrom(row)]));
  const order = roster.map((r) => r.id);
  const snapshot = () => order.map((id) => cloneUnit(units.get(id)));
  const firstActive = init.turn_order[init.active_index] || trace.actions[0]?.actor_id || order[0];
  const frames = [{ index: 0, round: isInt(init.round) ? init.round : 1, activeId: firstActive, units: snapshot() }];
  const problems = [];
  trace.actions.forEach((action, i) => {
    problems.push(...applyAction(units, action));
    let activeId = action.actor_id;
    let round = action.round;
    for (const e of action.events) {
      if (e.type === "turn_started" && units.has(e.actor_id)) {
        activeId = e.actor_id;
        if (isInt(e.round)) round = e.round;
      }
    }
    frames.push({ index: i + 1, round, activeId, units: snapshot() });
  });
  if (problems.length) return { ok: false, errors: problems.slice(0, LIMITS.errors), replay: null };
  const replay = {
    meta: metaOf(trace),
    roster,
    frames,
    steps: trace.actions.map((action, i) => stepOf(action, i, roster)),
    unattached: [],
  };
  attachNotes(replay, trace);
  const mismatch = compareWithFinal(replay, trace);
  if (mismatch.length) {
    return {
      ok: false,
      errors: ["Stepping through the actions does not reproduce final_state.", ...mismatch.slice(0, LIMITS.errors - 1)],
      replay: null,
    };
  }
  return { ok: true, errors: [], replay };
}

/**
 * Compare the last frame with final_state. An empty array means they agree
 * on position, HP, conditions, concentration, and alive.
 * @param {{ frames: Array<{ units: Unit[] }> }} replay
 * @param {object} trace
 * @returns {string[]}
 */
export function compareWithFinal(replay, trace) {
  const out = [];
  const last = replay.frames[replay.frames.length - 1];
  for (const u of last.units) {
    const want = unitFrom(trace.final_state.combatants[u.id]);
    for (const key of ["x", "y", "hp", "alive", "concentration"]) {
      if (u[key] !== want[key]) out.push(`${u.id}.${key} is ${show(u[key])}, final_state has ${show(want[key])}.`);
    }
    if (u.conditions.join() !== want.conditions.join()) {
      out.push(`${u.id}.conditions are [${u.conditions}], final_state has [${want.conditions}].`);
    }
  }
  return out;
}

function metaOf(trace) {
  const sc = trace.scorecard || {};
  const num = (v) => (isNum(v) ? v : null);
  return {
    scenarioId: trace.scenario.id,
    scenarioName: trace.scenario.name,
    seed: trace.config.seed,
    winner: trace.winner,
    rounds: trace.rounds,
    digest: trace.digest,
    partyDriver: driverLabel(trace.config.party_driver),
    enemyDriver: driverLabel(trace.config.enemy_driver),
    score: {
      side: isStr(sc.side) ? sc.side : null,
      composite: num(sc.composite_score),
      outcome: num(sc.outcome_score),
      legality: num(sc.rules_legality_score),
      tactical: num(sc.tactical_score),
      coordination: num(sc.coordination_score),
    },
    violations: trace.violations.length,
    rejectedInputs: Array.isArray(trace.rejected_inputs) ? trace.rejected_inputs.length : 0,
    turnOrder: [...trace.initial_state.turn_order],
  };
}

/**
 * @param {unknown} value
 * @returns {string}
 */
export function driverLabel(value) {
  if (isStr(value)) return value;
  if (isObj(value)) return [...new Set(Object.values(value).map(String))].sort().join(" + ");
  return "unknown";
}

/**
 * Short token text from word initials: "Goblin Scout A" -> "GSA",
 * "Bram Ironhand" -> "BI", "Ghoul 3" -> "G3", "Mummy" -> "MU".
 * @param {string} name
 * @returns {string}
 */
export function tokenLabel(name) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  const last = words[words.length - 1];
  if (/^\d+$/.test(last)) return `${words.slice(0, -1).map((w) => w[0]).join("").slice(0, 2)}${last.slice(0, 2)}`.toUpperCase();
  return words.map((w) => w[0]).join("").slice(0, 3).toUpperCase();
}

/**
 * Token labels that are unique within one roster. A clash keeps the first
 * two characters and adds a number: "CG", "CG2".
 * @param {string[]} names
 * @returns {string[]}
 */
export function tokenLabels(names) {
  const seen = new Set();
  return names.map((name) => {
    const base = tokenLabel(name);
    let label = base;
    for (let n = 2; seen.has(label); n += 1) label = `${base.slice(0, 2)}${n}`;
    seen.add(label);
    return label;
  });
}

function stepOf(action, i, roster) {
  const names = new Map(roster.map((r) => [r.id, r.name]));
  const nameOf = (id) => names.get(id) || String(id ?? "?");
  const lines = [];
  if (action.table_talk) lines.push({ kind: "talk", text: action.table_talk });
  for (const hook of action.narrative_hooks || []) lines.push({ kind: "hook", text: hook });
  for (const e of action.events) {
    const line = describeEvent(e, nameOf);
    if (line) lines.push(line);
  }
  return {
    index: i + 1,
    seq: action.seq,
    round: action.round,
    turnIndex: action.turn_index,
    actionIndex: isInt(action.action_index) ? action.action_index : null,
    actorId: action.actor_id,
    actorName: isStr(action.actor_name) ? action.actor_name : nameOf(action.actor_id),
    side: isStr(action.side) ? action.side : "",
    driver: isStr(action.driver) ? action.driver : "",
    intent: isStr(action.intent.type) ? action.intent.type : "",
    rationale: isStr(action.rationale) ? action.rationale : "",
    lines,
  };
}

/**
 * One log line for dice, damage, and healing events, or null for events
 * the narrative hooks already cover.
 * @param {object} e
 * @param {(id: unknown) => string} nameOf
 * @returns {{ kind: string, text: string } | null}
 */
export function describeEvent(e, nameOf) {
  const signed = (n) => (n < 0 ? ` - ${-n}` : ` + ${n}`);
  const rolls = (r) => (Array.isArray(r) ? r.filter(isNum).join(", ") : "");
  if ((e.type === "attack_roll" || e.type === "spell_attack_roll") && isNum(e.total)) {
    const mode = isStr(e.mode) && e.mode !== "normal" ? ` (${e.mode})` : "";
    const what = isStr(e.weapon) ? e.weapon : isStr(e.spell) ? e.spell : isStr(e.action) ? e.action : "attack";
    const result = e.crit ? "critical hit" : e.hit ? "hit" : "miss";
    const bonus = isNum(e.attack_bonus) ? signed(e.attack_bonus) : "";
    return { kind: "dice", text: `${nameOf(e.actor_id)} → ${nameOf(e.target_id)}, ${what}: d20 [${rolls(e.rolls)}]${mode}${bonus} = ${e.total} vs AC ${show(e.target_ac)} · ${result}` };
  }
  if (e.type === "saving_throw" && isNum(e.total)) {
    const ability = isStr(e.ability) ? e.ability.toUpperCase() : "";
    const result = e.success ? "success" : "fail";
    const mod = isNum(e.modifier) ? signed(e.modifier) : "";
    const auto = e.auto_fail ? " (auto-fail)" : "";
    return { kind: "dice", text: `${nameOf(e.target_id)} ${ability} save: d20 [${rolls(e.rolls)}]${mod} = ${e.total} vs DC ${show(e.dc)} · ${result}${auto}` };
  }
  if (e.type === "death_save" && isNum(e.roll)) {
    return { kind: "dice", text: `${nameOf(e.actor_id)} death save: d20 [${e.roll}] · ${isStr(e.outcome) ? e.outcome : "?"} (${show(e.successes)} successes, ${show(e.failures)} failures)` };
  }
  if (e.type === "damage_applied" && isNum(e.total_damage)) {
    const parts = Array.isArray(e.breakdown)
      ? e.breakdown.filter((b) => isObj(b) && isNum(b.effective)).map((b) => `${b.effective} ${isStr(b.type) ? b.type : ""}${isStr(b.modifier) && b.modifier !== "normal" ? ` (${b.modifier})` : ""}`.trim())
      : [];
    const detail = parts.length ? ` (${parts.join(", ")})` : "";
    return { kind: "damage", text: `${nameOf(e.target_id)} takes ${e.total_damage} damage${detail} · HP ${show(e.hp_before)} → ${show(e.hp_after)}` };
  }
  if (e.type === "healing_applied" && isNum(e.amount)) {
    const revived = e.revived ? " · revived" : "";
    return { kind: "heal", text: `${nameOf(e.target_id)} heals ${e.amount} · HP ${show(e.hp_before)} → ${show(e.hp_after)}${revived}` };
  }
  if (e.type === "death_burst" && isNum(e.damage)) {
    return { kind: "damage", text: `${nameOf(e.source_id)} death burst: ${isStr(e.dice) ? e.dice : "dice"} = ${e.damage} ${isStr(e.damage_type) ? e.damage_type : ""}`.trim() };
  }
  return null;
}

/** Put violations and rejected inputs on the step they belong to. */
function attachNotes(replay, trace) {
  for (const v of trace.violations) {
    if (!isObj(v)) continue;
    const text = `Rejected${isInt(v.attempt) ? ` attempt ${v.attempt}` : ""}${isObj(v.intent) && isStr(v.intent.type) ? ` (${v.intent.type})` : ""}: ${isStr(v.error) ? v.error : "no reason given"}`;
    const step = replay.steps.find(
      (s) => s.round === v.round && s.turnIndex === v.turn_index && s.actorId === v.actor_id && (v.action_index == null || s.actionIndex === v.action_index),
    );
    if (step) step.lines.push({ kind: "violation", text });
    else replay.unattached.push({ kind: "violation", text: `Round ${show(v.round)}, ${String(v.actor_id ?? "?")}: ${text}` });
  }
  for (const r of trace.rejected_inputs || []) {
    if (!isObj(r)) continue;
    const text = `Input not understood${isStr(r.input) ? `: “${r.input}”` : ""}${isStr(r.reason) ? ` (${r.reason})` : ""}`;
    const step = replay.steps.find((s) => s.round === r.round && s.actorId === r.actor_id);
    if (step) step.lines.unshift({ kind: "violation", text });
    else replay.unattached.push({ kind: "violation", text });
  }
}

/**
 * Validate dist/replays/index.json. Rows that would build an unsafe URL are rejected.
 * @param {unknown} raw
 * @returns {{ ok: true, errors: [], rows: object[] } | { ok: false, errors: string[], rows: null }}
 */
export function validateReplayIndex(raw) {
  if (!isObj(raw)) return { ok: false, errors: ["The replay index is not a JSON object."], rows: null };
  const errors = [];
  if (raw.schema_version !== INDEX_SCHEMA_VERSION) errors.push(`replay index schema_version is ${show(raw.schema_version)} (expected "${INDEX_SCHEMA_VERSION}").`);
  if (raw.dataset_id !== DATASET_ID) errors.push(`replay index dataset_id is ${show(raw.dataset_id)}.`);
  if (raw.trace_schema_version !== TRACE_SCHEMA_VERSION) errors.push(`replay index trace_schema_version is ${show(raw.trace_schema_version)}.`);
  if (!Array.isArray(raw.replays)) errors.push("replay index has no replays list.");
  if (errors.length) return { ok: false, errors, rows: null };
  const seen = new Set();
  raw.replays.forEach((row, i) => {
    const ok =
      isObj(row) &&
      isStr(row.id) && SAFE_ID.test(row.id) && !seen.has(row.id) &&
      isStr(row.file) && SAFE_FILE.test(row.file) && row.file === `${row.id}.trace.json` &&
      isStr(row.digest) && HEX64.test(row.digest) &&
      isStr(row.sha256) && HEX64.test(row.sha256) &&
      isInt(row.bytes) && row.bytes > 0 &&
      isStr(row.scenario_name) && isInt(row.seed) && isStr(row.winner) && isInt(row.rounds) &&
      (row.writeup == null || (isStr(row.writeup) && SAFE_WRITEUP.test(row.writeup)));
    if (!ok) errors.push(`replay index row ${i} is malformed.`);
    else seen.add(row.id);
  });
  if (errors.length) return { ok: false, errors: errors.slice(0, LIMITS.errors), rows: null };
  return { ok: true, errors: [], rows: raw.replays };
}

/**
 * Board extent in feet over every frame, padded by one 5-ft square.
 * @param {Array<{ units: Unit[] }>} frames
 * @returns {{ x: number, y: number, w: number, h: number }}
 */
export function boardBounds(frames) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const frame of frames) {
    for (const u of frame.units) {
      minX = Math.min(minX, u.x);
      minY = Math.min(minY, u.y);
      maxX = Math.max(maxX, u.x);
      maxY = Math.max(maxY, u.y);
    }
  }
  if (!Number.isFinite(minX)) return { x: -5, y: -5, w: 15, h: 15 };
  const x = snap(minX) - 5;
  const y = snap(minY) - 5;
  return { x, y, w: snap(maxX) + 10 - x, h: snap(maxY) + 10 - y };
}

const snap = (v) => Math.floor(v / 5) * 5;

/**
 * Group units by 5-ft square, in roster order. A group of two or more is a
 * shared square, which real traces contain (for example a creature standing
 * on a fallen one).
 * @param {Unit[]} units
 * @returns {Array<{ x: number, y: number, ids: string[] }>}
 */
export function occupancy(units) {
  const squares = new Map();
  for (const u of units) {
    const key = `${snap(u.x)},${snap(u.y)}`;
    if (!squares.has(key)) squares.set(key, { x: snap(u.x), y: snap(u.y), ids: [] });
    squares.get(key).ids.push(u.id);
  }
  return [...squares.values()];
}

/**
 * HP as a 0..20 step for the `.wN` width classes, and a severity band.
 * @param {number} hp
 * @param {number} max
 */
export function hpBand(hp, max) {
  const ratio = max > 0 ? Math.min(1, Math.max(0, hp / max)) : 0;
  const step = hp > 0 ? Math.max(1, Math.round(ratio * 20)) : 0;
  return { step, ratio, band: ratio > 0.5 ? "ok" : ratio > 0.25 ? "mid" : "low" };
}

function show(value) {
  if (value === undefined) return "missing";
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value.length > 60 ? `${value.slice(0, 60)}…` : value);
  if (typeof value === "object") return Array.isArray(value) ? "a list" : "an object";
  return String(value);
}
