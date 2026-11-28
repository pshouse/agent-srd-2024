import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import { bandMeter } from "../lib/board-view.js";
import {
  boardModel,
  clickAction,
  downloadName,
  groupMenu,
  itemsTargeting,
  logKind,
  menuBody,
  moveBodyFromInput,
  newLines,
  reachableSquares,
  readToken,
  squareAt,
  statusText,
  validateState,
} from "../tabletop/tabletop-model.js";

const TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789-_ABCDE";
const clone = (v) => JSON.parse(JSON.stringify(v));

function view() {
  return {
    round: 1,
    scenario: { id: "goblin-ambush", name: "Goblin Ambush" },
    self: {
      id: "pc-bram", name: "Bram Ironhand", side: "party", kind: "pc", position: { x: 0, y: 0 }, hp: 12, hp_max: 12, temp_hp: 0, ac: 19,
      speed_ft: 30, movement_remaining_ft: 30, conditions: [], concentration: null, effects: [], heroic_inspiration: true,
      resources: { action: true, bonus_action: true, reaction: true, attacks_remaining: 0 }, slots: {}, death_saves: { successes: 0, failures: 0 },
    },
    allies: [{ id: "pc-talia", name: "Talia Spark", position: { x: 0, y: 15 }, hp: 8, hp_max: 8, ac: 12, alive: true, conditions: [], concentration: null, effects: [] }],
    enemies: [
      { id: "goblin-1", name: "Goblin Scout A", position: { x: 20, y: 0 }, alive: true, hp_band: "unharmed", conditions: [], effects: [] },
      { id: "goblin-2", name: "Goblin Scout B", position: { x: 25, y: 15 }, alive: true, hp_band: "bloodied", conditions: ["prone"], effects: [] },
    ],
    can_reroll: false,
    table_talk: [],
    recent_events: [],
    retry_feedback: null,
  };
}

function menu() {
  const items = [
    ["attack", "Attack Goblin Scout A with Shortbow (20 ft)", { type: "attack", actor_id: "pc-bram", target_id: "goblin-1", weapon: "shortbow" }],
    ["attack", "Attack Goblin Scout B with Shortbow (25 ft)", { type: "attack", actor_id: "pc-bram", target_id: "goblin-2", weapon: "shortbow" }],
    ["move", "Move to (15,0)", { type: "move", actor_id: "pc-bram", to: { x: 15, y: 0 } }],
    ["tactical", "Dodge", { type: "dodge", actor_id: "pc-bram" }],
    ["tactical", "Help for Talia Spark against Goblin Scout A", { type: "help", actor_id: "pc-bram", target_id: "goblin-1", ally_id: "pc-talia" }],
    ["turn", "End Turn", { type: "end_turn", actor_id: "pc-bram" }],
  ];
  return items.map(([category, label, intent], i) => ({ index: i + 1, category, label, summary: "", intent, meta: {} }));
}

function state(over = {}) {
  return {
    status: "awaiting_input",
    scenario: { id: "goblin-ambush", name: "Goblin Ambush" },
    seat: "pc-bram",
    features: { nl: false, save: true, out_trace: false, resume: false },
    decision: 3,
    view: view(),
    menu: menu(),
    log: [{ seq: 1, text: "=== start ===" }],
    log_next: 1,
    result: null,
    trace_written: false,
    checkpoint_written: false,
    pause_reason: null,
    notice: null,
    error: null,
    ...over,
  };
}

test("the launch token is read only from a well-formed fragment", () => {
  assert.equal(readToken(`#token=${TOKEN}`), TOKEN);
  for (const bad of ["", "#", "#token=", "#token=short", `#token=${TOKEN}&x=1`, `#tok=${TOKEN}`, `#token=${TOKEN}<script>`, `?token=${TOKEN}`]) {
    assert.equal(readToken(bad), null, bad);
  }
});

test("state validation accepts a good payload and fails closed on anything else", () => {
  assert.equal(validateState(state()).ok, true);
  assert.equal(validateState(state({ status: "running", decision: null, menu: [] })).ok, true);
  const bad = [
    null,
    [],
    state({ status: "hacked" }),
    state({ menu: "1" }),
    state({ menu: [{ ...menu()[0], index: 2 }] }),
    state({ menu: [{ ...menu()[0], category: "nuke" }] }),
    state({ menu: [{ ...menu()[0], intent: null }] }),
    state({ decision: "3" }),
    state({ log: [{ seq: "1", text: "x" }] }),
    state({ features: { nl: "yes", save: true, out_trace: false, resume: false } }),
    state({ menu: [] }),
    state({ view: null }),
    state({ error: { html: "<b>" } }),
  ];
  for (const raw of bad) assert.equal(validateState(raw).ok, false, JSON.stringify(raw)?.slice(0, 80));
  // An opponent row carrying exact HP or AC is a leak, so the whole state is refused.
  for (const key of ["hp", "ac", "hp_max"]) {
    const s = state();
    s.view.enemies[0][key] = 7;
    const got = validateState(s);
    assert.equal(got.ok, false, key);
    assert.equal(got.state, null);
  }
  const s = state();
  s.view.self.position = { x: "0", y: 0 };
  assert.equal(validateState(s).ok, false);
});

test("menu rendering groups by category in a fixed order and keeps the server's indices", () => {
  const groups = groupMenu(menu());
  assert.deepEqual(groups.map((g) => g.category), ["attack", "move", "tactical", "turn"]);
  assert.deepEqual(groups.map((g) => g.title), ["Attacks", "Movement", "Actions", "Turn"]);
  assert.deepEqual(groups.flatMap((g) => g.items.map((i) => i.index)), [1, 2, 3, 4, 5, 6]);
  assert.deepEqual(groupMenu([]), []);
  assert.deepEqual(menuBody(menu()[3], 9), { kind: "menu", index: 4, decision: 9 });
});

test("the board model hides opponent HP and AC and labels tokens uniquely", () => {
  const m = boardModel(view());
  assert.equal(m.frame.activeId, "pc-bram");
  const gob = m.roster.get("goblin-1");
  assert.equal(gob.hpMax, null);
  assert.equal(gob.ac, null);
  assert.equal(gob.side, "enemy");
  assert.equal(m.roster.get("pc-talia").hpMax, 8);
  const unit = m.frame.units.find((u) => u.id === "goblin-2");
  assert.equal(unit.hpBand, "bloodied");
  assert.equal(unit.hp, 0);
  assert.equal(new Set([...m.roster.values()].map((r) => r.label)).size, 4);
  // Playing the DM seat flips the sides.
  const dm = view();
  dm.self.side = "enemy";
  assert.equal(boardModel(dm).roster.get("goblin-1").side, "party");
  assert.deepEqual(bandMeter("bloodied"), { step: 8, ratio: 0.4, band: "mid" });
  assert.equal(bandMeter("defeated").step, 0);
  assert.equal(bandMeter("whatever").step, 0);
});

test("reachable squares respect remaining movement, prone crawling, and occupied squares", () => {
  const v = view();
  const b = boardModel(v).bounds;
  const reach = reachableSquares(v, b);
  const has = (x, y) => reach.some((r) => r.x === x && r.y === y);
  assert.ok(has(30, 30));
  assert.ok(has(-30, 0));
  assert.equal(has(35, 0), false);
  assert.equal(has(0, 0), false, "own square");
  assert.equal(has(20, 0), false, "occupied by a goblin");
  assert.equal(has(0, 15), false, "occupied by an ally");
  v.self.conditions = ["prone"];
  const crawl = reachableSquares(v, b);
  assert.ok(crawl.some((r) => r.x === 15 && r.y === 0));
  assert.equal(crawl.some((r) => r.x === 20 && r.y === 5), false);
  v.self.movement_remaining_ft = 5;
  assert.deepEqual(reachableSquares(v, b), []);
  const still = view();
  still.self.speed_ft = 0;
  assert.deepEqual(reachableSquares(still, b), []);
});

test("click-to-intent: empty reachable square moves, combatant opens target choices, else nothing is sent", () => {
  const v = view();
  const m = menu();
  const reach = reachableSquares(v, boardModel(v).bounds);
  assert.deepEqual(clickAction(v, m, squareAt(16.2, 3.9), reach), { type: "move", body: { kind: "move", x: 15, y: 0 } });
  const gob = clickAction(v, m, squareAt(21, 1), reach);
  assert.equal(gob.type, "targets");
  assert.equal(gob.id, "goblin-1");
  assert.deepEqual(gob.items.map((i) => i.index), [1, 5]);
  assert.equal(clickAction(v, m, { x: 0, y: 0 }, reach).type, "none");
  assert.equal(clickAction(v, m, { x: 0, y: 15 }, reach).type, "none");
  const far = clickAction(v, m, { x: 100, y: 100 }, reach);
  assert.equal(far.type, "none");
  assert.match(far.reason, /out of your movement/);
  assert.equal(clickAction(v, [], { x: 5, y: 0 }, reach).type, "none");
  // A defeated combatant's square is not a target.
  v.enemies[0].alive = false;
  assert.equal(clickAction(v, m, { x: 20, y: 0 }, reachableSquares(v, boardModel(v).bounds)).type, "move");
  assert.deepEqual(itemsTargeting(m, "pc-talia"), []);
  assert.deepEqual(squareAt(-0.1, 4.99), { x: -5, y: 0 });
});

test("typed moves are checked before anything is sent", () => {
  assert.deepEqual(moveBodyFromInput("15", " -5 ", 2), { ok: true, body: { kind: "move", x: 15, y: -5, decision: 2 } });
  for (const [x, y] of [["", "0"], ["7", "0"], ["1.5", "0"], ["abc", "0"], ["5", ""]]) {
    assert.equal(moveBodyFromInput(x, y, 1).ok, false, `${x},${y}`);
  }
});

test("log lines, download names, and status text", () => {
  assert.equal(logKind("  ! Rejected: Insufficient movement"), "violation");
  assert.equal(logKind('  [Talia Spark says]: "hi"'), "talk");
  assert.equal(logKind("  * Bram moves 15 ft."), "hook");
  assert.equal(logKind("--- Round 1 | Goblin ---"), "head");
  assert.equal(logKind("[Table Talk queued]"), "sys");
  assert.equal(logKind("Initiative: ..."), "plain");
  assert.deepEqual(newLines([{ seq: 3, text: "c" }, { seq: 1, text: "a" }, { seq: 2, text: "b" }], 1).map((l) => l.seq), [2, 3]);
  assert.equal(downloadName('attachment; filename="goblin-ambush-seed42.trace.json"'), "goblin-ambush-seed42.trace.json");
  assert.equal(downloadName('attachment; filename="../../x.sh"'), "srd-web.trace.json");
  assert.equal(downloadName(null), "srd-web.trace.json");
  assert.match(statusText(state()), /Your move: Bram/);
  assert.match(statusText(state({ status: "finished", result: { winner: "party" } })), /party wins/);
});

test("the tabletop is plain modules with no HTML injection or inline styles, and stays off the Pages allowlist", () => {
  const dir = new URL("../tabletop/", import.meta.url);
  const files = readdirSync(dir).filter((f) => f.endsWith(".js"));
  assert.deepEqual(files.sort(), ["tabletop-model.js", "tabletop.js"]);
  for (const f of [...files.map((f) => new URL(f, dir)), new URL("../lib/board-view.js", import.meta.url)]) {
    const src = readFileSync(f, "utf8");
    assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/, f.pathname);
    assert.doesNotMatch(src, /\.style\b/, f.pathname);
    assert.doesNotMatch(src, /setAttribute\(\s*["']style["']/, f.pathname);
    assert.doesNotMatch(src, /localStorage|sessionStorage|document\.cookie/, f.pathname);
    assert.doesNotMatch(src, /https?:\/\/(?!creativecommons|www\.dndbeyond|www\.w3\.org\/2000\/svg)/, f.pathname);
  }
  const ui = readFileSync(new URL("tabletop.js", dir), "utf8");
  assert.match(ui, /Authorization: `Bearer \$\{token\}`/);
  assert.match(ui, /history\.replaceState/);
  assert.match(ui, /from "\.\.\/lib\/board-view\.js"/);
  const sites = readFileSync(new URL("../../scripts/site_files.py", import.meta.url), "utf8");
  assert.doesNotMatch(sites, /tabletop/);
  assert.doesNotMatch(sites, /"web\/\*\*|"web\/\*"/);
});
