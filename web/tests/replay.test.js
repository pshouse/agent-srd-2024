import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  boardBounds,
  buildReplay,
  describeEvent,
  hpBand,
  occupancy,
  parseTraceText,
  tokenLabel,
  tokenLabels,
  validateReplayIndex,
  validateTrace,
} from "../lib/replay-model.js";
import { clampStep, initialPlayer, keyCommand, reducePlayer, stepLabel, turnStarts } from "../lib/replay-player.js";
import { parseRoute, replayHash, replayStepFromHash } from "../lib/router.js";

const repo = new URL("../../", import.meta.url);
const indexRaw = JSON.parse(readFileSync(new URL("dist/replays/index.json", repo), "utf8"));
// The committed index lists only git-tracked traces, so local untracked traces never enter this test.
const committed = indexRaw.replays.map((row) => {
  const bytes = readFileSync(new URL(row.source, repo));
  return { row, bytes, trace: JSON.parse(bytes.toString("utf8")) };
});
const goblin = committed.find((c) => c.row.id === "2026-11-27-goblin-nl-1");
const clone = (value) => JSON.parse(JSON.stringify(value));

test("the replay index lists every committed trace and its copies match byte for byte", () => {
  assert.ok(committed.length >= 5);
  assert.ok(goblin, "goblin-nl-1 is committed");
  const checked = validateReplayIndex(indexRaw);
  assert.equal(checked.ok, true, checked.errors.join("; "));
  for (const { row, bytes, trace } of committed) {
    const copy = readFileSync(new URL(`dist/replays/${row.file}`, repo));
    assert.ok(copy.equals(bytes), row.file);
    assert.equal(createHash("sha256").update(bytes).digest("hex"), row.sha256);
    assert.equal(row.bytes, bytes.length);
    assert.ok(row.bytes < 5 * 1024 * 1024);
    assert.equal(row.digest, trace.digest);
    assert.equal(row.seed, trace.config.seed);
    assert.equal(row.winner, trace.winner);
    assert.equal(row.rounds, trace.rounds);
  }
});

test("stepping through every committed trace reproduces final_state", () => {
  for (const { row, trace } of committed) {
    const result = buildReplay(trace);
    assert.equal(result.ok, true, `${row.id}: ${result.errors.join("; ")}`);
    const { frames, steps } = result.replay;
    assert.equal(frames.length, trace.actions.length + 1, row.id);
    assert.equal(steps.length, trace.actions.length, row.id);
    // Independent comparison against final_state (buildReplay also checks, fail-closed).
    const last = new Map(frames.at(-1).units.map((u) => [u.id, u]));
    for (const [id, want] of Object.entries(trace.final_state.combatants)) {
      const got = last.get(id);
      const at = `${row.id} ${id}`;
      assert.deepEqual({ x: got.x, y: got.y }, want.position, `${at} position`);
      assert.equal(got.hp, want.hp, `${at} hp`);
      assert.equal(got.alive, want.alive, `${at} alive`);
      assert.deepEqual(got.conditions, want.conditions.map((c) => c.condition).sort(), `${at} conditions`);
      assert.equal(got.concentration, want.concentration ? want.concentration.spell : null, `${at} concentration`);
      assert.deepEqual(got.effects, (want.active_effects || []).map((e) => e.kind).sort(), `${at} effects`);
      assert.deepEqual(got.deathSaves, want.death_saves, `${at} death saves`);
    }
    // Frame 0 is initial_state untouched.
    for (const u of frames[0].units) {
      const init = trace.initial_state.combatants[u.id];
      assert.deepEqual({ x: u.x, y: u.y, hp: u.hp }, { ...init.position, hp: init.hp });
    }
  }
});

test("intermediate frames follow each action's state_diff", () => {
  const { replay } = buildReplay(goblin.trace);
  goblin.trace.actions.forEach((action, i) => {
    const frame = new Map(replay.frames[i + 1].units.map((u) => [u.id, u]));
    for (const c of action.state_diff.hp_changes) assert.equal(frame.get(c.id).hp, c.after);
    for (const c of action.state_diff.position_changes) assert.deepEqual({ x: frame.get(c.id).x, y: frame.get(c.id).y }, c.after);
  });
  // Bram's javelin reroll (seq 2) takes Goblin Scout A from 10 to 6.
  assert.equal(replay.frames[1].units.find((u) => u.id === "goblin-1").hp, 10);
  assert.equal(replay.frames[2].units.find((u) => u.id === "goblin-1").hp, 6);
  // The end_turn action hands the active highlight to the next combatant.
  assert.equal(replay.frames[0].activeId, "pc-bram");
  assert.equal(replay.frames[4].activeId, "pc-talia");
});

test("shared squares in real traces are grouped, not dropped", () => {
  let shared = 0;
  for (const { trace } of committed) {
    const { replay } = buildReplay(trace);
    for (const frame of replay.frames) {
      const squares = occupancy(frame.units);
      assert.equal(squares.reduce((n, sq) => n + sq.ids.length, 0), frame.units.length);
      shared += squares.filter((sq) => sq.ids.length > 1).length;
    }
  }
  assert.ok(shared > 0, "expected at least one shared square across committed traces");
});

test("violations land on the action they rejected", () => {
  const wyrm = committed.find((c) => c.trace.violations.length > 0);
  assert.ok(wyrm, "a committed trace has violations");
  const { replay } = buildReplay(wyrm.trace);
  assert.deepEqual(replay.unattached, []);
  const lines = replay.steps.flatMap((s) => s.lines).filter((l) => l.kind === "violation");
  assert.equal(lines.length, wyrm.trace.violations.length);
  assert.match(lines[0].text, /^Rejected attempt 1 \(attack\): Nick mastery/);
});

test("table talk, hooks, dice, damage, and healing reach the log as plain text", () => {
  const { replay } = buildReplay(goblin.trace);
  const step = replay.steps[1];
  const kinds = step.lines.map((l) => l.kind);
  assert.deepEqual(kinds.slice(0, 3), ["hook", "hook", "hook"]);
  assert.ok(kinds.includes("dice") && kinds.includes("damage"));
  assert.equal(replay.steps[0].lines[0].kind, "talk");
  const names = (id) => ({ a: "Alpha", b: "Beta" })[id] || "?";
  assert.equal(
    describeEvent({ type: "attack_roll", actor_id: "a", target_id: "b", weapon: "javelin", mode: "advantage", rolls: [3, 17], attack_bonus: 5, total: 22, target_ac: 15, hit: true }, names).text,
    "Alpha → Beta, javelin: d20 [3, 17] (advantage) + 5 = 22 vs AC 15 · hit",
  );
  assert.equal(
    describeEvent({ type: "saving_throw", target_id: "b", ability: "con", rolls: [4], modifier: -1, total: 3, dc: 10, success: false }, names).text,
    "Beta CON save: d20 [4] - 1 = 3 vs DC 10 · fail",
  );
  assert.equal(
    describeEvent({ type: "damage_applied", target_id: "b", total_damage: 0, breakdown: [{ type: "fire", effective: 0, modifier: "immune" }], hp_before: 9, hp_after: 9 }, names).text,
    "Beta takes 0 damage (0 fire (immune)) · HP 9 → 9",
  );
  assert.equal(
    describeEvent({ type: "healing_applied", target_id: "a", amount: 10, hp_before: 0, hp_after: 10, revived: true }, names).text,
    "Alpha heals 10 · HP 0 → 10 · revived",
  );
  assert.equal(describeEvent({ type: "turn_ended", actor_id: "a" }, names), null);
});

test("older traces without rejected_inputs still replay", () => {
  const trace = clone(goblin.trace);
  delete trace.rejected_inputs;
  assert.equal(buildReplay(trace).ok, true);
});

function expectFail(mutate, pattern) {
  const trace = clone(goblin.trace);
  const replaced = mutate(trace);
  const result = buildReplay(replaced === undefined ? trace : replaced);
  assert.equal(result.ok, false, `expected failure for ${pattern}`);
  assert.equal(result.replay, null);
  assert.ok(result.errors.length > 0);
  assert.match(result.errors.join(" | "), pattern);
}

test("unknown schema versions and wrong datasets fail closed", () => {
  expectFail((t) => { t.schema_version = "3"; }, /schema_version is "3" \(expected "4"\)/);
  expectFail((t) => { t.schema_version = 4; }, /schema_version is 4/);
  expectFail((t) => { delete t.schema_version; }, /schema_version is missing/);
  expectFail((t) => { t.dataset_id = "other"; }, /dataset_id/);
  expectFail(() => [], /not a JSON object/);
  expectFail(() => null, /not a JSON object/);
});

test("bad shapes fail closed with a reason", () => {
  expectFail((t) => { t.actions = {}; }, /actions must be a list/);
  expectFail((t) => { t.actions[0].actor_id = "nobody"; }, /actor_id is not a combatant/);
  expectFail((t) => { t.actions[3].seq = 1; }, /seq must be an increasing integer/);
  expectFail((t) => { t.actions[0].events = "x"; }, /events must be a list/);
  expectFail((t) => { t.actions[0].table_talk = { html: "<b>" }; }, /table_talk/);
  expectFail((t) => { t.actions[0].narrative_hooks = [1]; }, /narrative_hooks/);
  expectFail((t) => { t.actions[2].state_diff.position_changes[0].after = { x: 2.5, y: 0 }; }, /position_changes/);
  expectFail((t) => { t.actions[1].state_diff.hp_changes[0].id = "ghost"; }, /hp_changes/);
  expectFail((t) => { delete t.actions[0].state_diff; }, /state_diff must be an object/);
  expectFail((t) => { t.initial_state.combatants["pc-bram"].position = { x: "0", y: 0 }; }, /position must be integer feet/);
  expectFail((t) => { t.initial_state.combatants["pc-bram"].side = "neutral"; }, /side is "neutral"/);
  expectFail((t) => { t.initial_state.combatants["pc-bram"].name = 7; }, /name must be a string/);
  expectFail((t) => { t.config.seed = "0"; }, /config.seed/);
  expectFail((t) => { t.digest = "abc"; }, /digest/);
  expectFail((t) => { t.violations = null; }, /violations must be a list/);
  expectFail((t) => { t.rejected_inputs = "no"; }, /rejected_inputs must be a list/);
  expectFail((t) => { t.final_state.combatants.extra = clone(t.final_state.combatants["pc-bram"]); }, /not in initial_state/);
  expectFail((t) => {
    const row = t.initial_state.combatants["pc-bram"];
    t.initial_state.combatants = JSON.parse(`{"__proto__": ${JSON.stringify({ ...row, id: "__proto__" })}}`);
  }, /unsafe combatant id/);
});

test("a trace whose steps do not add up to final_state is refused", () => {
  expectFail((t) => { t.final_state.combatants["goblin-2"].hp = 3; }, /does not reproduce final_state[\s\S]*goblin-2\.hp/);
  expectFail((t) => { t.final_state.combatants["pc-bram"].position = { x: 50, y: 50 }; }, /pc-bram\.x/);
  expectFail((t) => { t.final_state.combatants["pc-talia"].conditions = [{ condition: "prone" }]; }, /pc-talia\.conditions/);
  expectFail((t) => { t.actions[1].state_diff.hp_changes[0].before = 99; }, /diff starts from 99/);
});

test("parseTraceText refuses non-JSON and oversize text", () => {
  assert.deepEqual(parseTraceText("{nope").errors, ["The file is not valid JSON."]);
  assert.equal(parseTraceText(null).ok, false);
  assert.equal(parseTraceText("x".repeat(20 * 1024 * 1024 + 1)).ok, false);
  const ok = parseTraceText(goblin.bytes.toString("utf8"));
  assert.equal(ok.ok, true);
  assert.deepEqual(validateTrace(ok.trace), []);
});

test("the replay index rejects unsafe rows", () => {
  const bad = (mutate) => {
    const raw = clone(indexRaw);
    mutate(raw);
    return validateReplayIndex(raw);
  };
  assert.equal(bad((r) => { r.schema_version = "2"; }).ok, false);
  assert.equal(bad((r) => { r.trace_schema_version = "3"; }).ok, false);
  assert.equal(bad((r) => { r.replays[0].file = "../secret.trace.json"; }).ok, false);
  assert.equal(bad((r) => { r.replays[0].file = "https://evil.example/x.trace.json"; }).ok, false);
  assert.equal(bad((r) => { r.replays[0].id = "__proto__"; }).ok, false);
  assert.equal(bad((r) => { r.replays[1].id = r.replays[0].id; r.replays[1].file = r.replays[0].file; }).ok, false);
  assert.equal(bad((r) => { r.replays[0].writeup = "javascript:alert(1)"; }).ok, false);
  assert.equal(bad((r) => { r.replays[0].sha256 = "x"; }).ok, false);
  assert.equal(validateReplayIndex([]).ok, false);
});

test("player steps, clamps, plays to the end, and jumps by turn", () => {
  const { replay } = buildReplay(goblin.trace);
  const turns = turnStarts(replay.steps);
  assert.deepEqual(turns.slice(0, 3), [0, 4, 6]);
  let s = initialPlayer(replay.frames.length);
  assert.deepEqual(s, { index: 0, total: 15, playing: false });
  s = reducePlayer(s, { type: "prev" });
  assert.equal(s.index, 0);
  s = reducePlayer(s, { type: "next" });
  s = reducePlayer(s, { type: "next" });
  assert.equal(s.index, 2);
  s = reducePlayer(s, { type: "nextTurn" }, turns);
  assert.equal(s.index, 4);
  s = reducePlayer(s, { type: "prevTurn" }, turns);
  assert.equal(s.index, 0);
  s = reducePlayer(s, { type: "seek", value: 999 });
  assert.equal(s.index, 14);
  s = reducePlayer(s, { type: "seek", value: "abc" });
  assert.equal(s.index, 0);
  s = reducePlayer(s, { type: "last" });
  s = reducePlayer(s, { type: "toggle" });
  assert.deepEqual([s.index, s.playing], [0, true], "play at the end restarts from the beginning");
  for (let i = 0; i < 20; i += 1) s = reducePlayer(s, { type: "tick" });
  assert.deepEqual([s.index, s.playing], [14, false], "playback stops on the last step");
  s = reducePlayer(s, { type: "first" });
  s = reducePlayer(s, { type: "toggle" });
  s = reducePlayer(s, { type: "next" });
  assert.equal(s.playing, false, "a manual step pauses playback");
  assert.equal(reducePlayer(s, { type: "bogus" }), s);
  assert.equal(clampStep(-3, 10), 0);
  assert.equal(clampStep(4.9, 10), 4);
  assert.equal(reducePlayer(initialPlayer(1), { type: "toggle" }).playing, false);
});

test("keyboard shortcuts and scrubber labels", () => {
  assert.deepEqual(keyCommand("ArrowLeft"), { type: "prev" });
  assert.deepEqual(keyCommand("ArrowRight"), { type: "next" });
  assert.deepEqual(keyCommand("ArrowRight", { shiftKey: true }), { type: "nextTurn" });
  assert.deepEqual(keyCommand("ArrowLeft", { shiftKey: true }), { type: "prevTurn" });
  assert.deepEqual(keyCommand("Home"), { type: "first" });
  assert.deepEqual(keyCommand("End"), { type: "last" });
  assert.deepEqual(keyCommand(" "), { type: "toggle" });
  assert.equal(keyCommand("a"), null);
  const { replay } = buildReplay(goblin.trace);
  assert.equal(stepLabel(replay, 0), "Start · round 1 · step 0 of 14");
  assert.equal(stepLabel(replay, 14), "Round 2 · turn 6 · step 14 of 14");
});

test("token labels are short and unique within each roster", () => {
  assert.equal(tokenLabel("Goblin Scout A"), "GSA");
  assert.equal(tokenLabel("Bram Ironhand"), "BI");
  assert.equal(tokenLabel("Ghoul 3"), "G3");
  assert.equal(tokenLabel("Mummy"), "MU");
  assert.equal(tokenLabel(""), "?");
  assert.deepEqual(tokenLabels(["Crypt Ghast", "Crypt Ghast", "Crypt Ghast"]), ["CG", "CG2", "CG3"]);
  for (const { row, trace } of committed) {
    const labels = buildReplay(trace).replay.roster.map((r) => r.label);
    assert.equal(new Set(labels).size, labels.length, row.id);
    assert.ok(labels.every((l) => l.length <= 3), row.id);
  }
});

test("board bounds cover every frame on the 5-ft grid", () => {
  for (const { trace } of committed) {
    const { replay } = buildReplay(trace);
    const b = boardBounds(replay.frames);
    for (const v of [b.x, b.y, b.w, b.h]) assert.ok(v % 5 === 0, String(v));
    for (const frame of replay.frames) {
      for (const u of frame.units) {
        // One padding square on every side of the outermost occupied squares.
        assert.ok(u.x >= b.x + 5 && u.x + 5 <= b.x + b.w - 5);
        assert.ok(u.y >= b.y + 5 && u.y + 5 <= b.y + b.h - 5);
      }
    }
  }
  assert.deepEqual(hpBand(0, 10), { step: 0, ratio: 0, band: "low" });
  assert.equal(hpBand(10, 10).step, 20);
  assert.equal(hpBand(1, 100).step, 1);
  assert.equal(hpBand(5, 10).band, "mid");
  assert.equal(hpBand(-4, 10).ratio, 0);
});

test("replay routes, deep links, and hostile ids", () => {
  assert.deepEqual(parseRoute("#/replay"), { name: "replay", id: "", unknown: false });
  assert.deepEqual(parseRoute("#/replay/"), { name: "replay", id: "", unknown: false });
  assert.deepEqual(parseRoute("#/replay/2026-11-27-goblin-nl-1?step=3"), {
    name: "replay",
    id: "2026-11-27-goblin-nl-1",
    unknown: false,
  });
  for (const hostile of ["#/replay/..%2F..%2Fetc", "#/replay/%3Cscript%3E", "#/replay/a/b", "#/replay/__proto__x%00"]) {
    assert.equal(parseRoute(hostile).name, "explorer", hostile);
  }
  assert.equal(replayHash(""), "#/replay");
  assert.equal(replayHash("x-1"), "#/replay/x-1");
  assert.equal(replayStepFromHash("#/replay/x?step=12"), 12);
  assert.equal(replayStepFromHash("#/replay/x?step=-1"), 0);
  assert.equal(replayStepFromHash("#/replay/x?step=1e3"), 0);
  assert.equal(replayStepFromHash("#/replay/x"), 0);
});

test("the replay view renders trace text without innerHTML and loads lazily", () => {
  const view = readFileSync(new URL("../lib/replay-view.js", import.meta.url), "utf8");
  const model = readFileSync(new URL("../lib/replay-model.js", import.meta.url), "utf8");
  for (const source of [view, model]) {
    assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/);
  }
  assert.match(view, /createElementNS\(SVG_NS/);
  assert.match(view, /new FileReader\(\)/);
  const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  assert.match(app, /import\("\.\/lib\/replay-view\.js"\)/);
  assert.doesNotMatch(app, /^import[^\n]*replay-view/m);
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /<a href="#\/replay" data-view="replay">/);
});
