import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildTerrainLayer, createBoard, terrainLegendItems } from "../lib/board-view.js";
import {
  boardBounds,
  buildReplay,
  describeEvent,
  validateBounds,
  validateTerrain,
} from "../lib/replay-model.js";
import {
  boardModel,
  clickAction,
  reachableSquares,
  validateState,
} from "../tabletop/tabletop-model.js";

/** Minimal pure-JS DOM stub for testing SVG/HTML builders in Node with zero external deps. */
class FakeNode {
  constructor(tag, ns = null) {
    this.tagName = tag;
    this.namespaceURI = ns;
    this.attrs = new Map();
    this.childNodes = [];
    this.textContent = "";
    this.className = "";
  }
  setAttribute(k, v) {
    if (k === "class") this.className = String(v);
    this.attrs.set(k, String(v));
  }
  getAttribute(k) {
    return this.attrs.get(k) ?? null;
  }
  append(...nodes) {
    for (const n of nodes) {
      this.childNodes.push(typeof n === "string" ? { nodeType: 3, textContent: n } : n);
    }
  }
  findAll(predicate, out = []) {
    if (predicate(this)) out.push(this);
    for (const c of this.childNodes) {
      if (c instanceof FakeNode) c.findAll(predicate, out);
    }
    return out;
  }
}

function withFakeDocument(fn) {
  const prevDoc = globalThis.document;
  const prevNode = globalThis.Node;
  globalThis.Node = FakeNode;
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createElementNS: (ns, tag) => new FakeNode(tag, ns),
    createTextNode: (text) => ({ nodeType: 3, textContent: String(text) }),
  };
  try {
    return fn();
  } finally {
    globalThis.document = prevDoc;
    globalThis.Node = prevNode;
  }
}

const SAMPLE_BOUNDS = { min_x: 0, min_y: 0, max_x: 40, max_y: 25 };
const SAMPLE_TERRAIN = {
  walls: [[15, 5], [15, 10]],
  difficult_terrain: [[10, 10], [20, 10]],
  cover: [
    { x: 10, y: 5, kind: "half" },
    { x: 25, y: 15, kind: "three_quarters" },
  ],
};

test("createBoard renders battlefield bounds, walls, difficult terrain, and half/3/4 cover with zero inline styles", () => {
  withFakeDocument(() => {
    const viewBox = { x: -5, y: -5, w: 55, h: 40 };
    const { svg, layer } = createBoard(viewBox, { bounds: SAMPLE_BOUNDS, terrain: SAMPLE_TERRAIN });
    assert.equal(svg.tagName, "svg");
    assert.ok(layer instanceof FakeNode);

    const boundsRects = svg.findAll((n) => n.getAttribute("class") === "rp-bounds");
    assert.equal(boundsRects.length, 1);
    assert.equal(boundsRects[0].getAttribute("x"), "0");
    assert.equal(boundsRects[0].getAttribute("y"), "0");
    assert.equal(boundsRects[0].getAttribute("width"), "45");
    assert.equal(boundsRects[0].getAttribute("height"), "30");

    const oob = svg.findAll((n) => n.getAttribute("class") === "rp-oob");
    assert.equal(oob.length, 1);

    const walls = svg.findAll((n) => n.getAttribute("class") === "rp-wall");
    assert.equal(walls.length, 2);
    assert.equal(walls[0].getAttribute("data-x"), "15");

    const diff = svg.findAll((n) => n.getAttribute("class") === "rp-diff");
    assert.equal(diff.length, 2);

    const half = svg.findAll((n) => n.getAttribute("class") === "rp-cov-half");
    assert.equal(half.length, 1);
    const tq = svg.findAll((n) => n.getAttribute("class") === "rp-cov-3q");
    assert.equal(tq.length, 1);

    // Strictly zero inline style attributes anywhere in the rendered SVG tree.
    const styled = svg.findAll((n) => n.attrs.has("style"));
    assert.deepEqual(styled, []);

    // Backward compatible call with no options renders clean grid with no terrain layer.
    const plain = createBoard({ x: 0, y: 0, w: 20, h: 20 });
    assert.equal(plain.svg.findAll((n) => n.getAttribute("class") === "rp-terrain").length, 0);
    assert.equal(plain.svg.findAll((n) => n.getAttribute("class") === "rp-bounds").length, 0);

    // Terrain legend items cover all present feature types.
    const items = terrainLegendItems(SAMPLE_BOUNDS, SAMPLE_TERRAIN);
    assert.equal(items.length, 5);
    const layerOnly = buildTerrainLayer(SAMPLE_TERRAIN);
    assert.equal(layerOnly.getAttribute("class"), "rp-terrain");
  });
});

test("replay-model validates bounds/terrain, expands boardBounds, and annotates cover in logs", () => {
  assert.deepEqual(validateBounds(SAMPLE_BOUNDS), []);
  assert.deepEqual(validateTerrain(SAMPLE_TERRAIN), []);
  assert.ok(validateBounds({ min_x: 0, min_y: 0, max_x: 7, max_y: 10 }).length > 0);
  assert.ok(validateBounds({ min_x: 20, min_y: 0, max_x: 10, max_y: 10 }).length > 0);
  assert.ok(validateTerrain({ cover: [{ x: 5, y: 5, kind: "full" }] }).length > 0);
  assert.ok(validateTerrain({ walls: [[3, 0]] }).length > 0);

  const b = boardBounds([{ units: [{ x: 5, y: 5 }] }], SAMPLE_BOUNDS, SAMPLE_TERRAIN);
  assert.deepEqual(b, { x: -5, y: -5, w: 55, h: 40 });

  const names = (id) => ({ a: "Aldric", b: "Goblin" })[id] || "?";
  const atkLine = describeEvent(
    {
      type: "attack_roll",
      actor_id: "a",
      target_id: "b",
      weapon: "shortbow",
      mode: "normal",
      rolls: [12],
      attack_bonus: 5,
      total: 17,
      target_ac: 17,
      cover: "half",
      cover_ac_bonus: 2,
      hit: true,
    },
    names,
  );
  assert.match(atkLine.text, /half cover \+2 AC/);

  const saveLine = describeEvent(
    {
      type: "saving_throw",
      target_id: "b",
      ability: "dex",
      rolls: [8],
      modifier: 2,
      cover: "three_quarters",
      cover_save_bonus: 5,
      total: 15,
      dc: 15,
      success: true,
    },
    names,
  );
  assert.match(saveLine.text, /three-quarters cover \+5/);

  // Trace with bounds & terrain builds cleanly; malformed terrain fails closed.
  const baseTrace = {
    schema_version: "4",
    dataset_id: "srd-2024-5.2",
    scenario: { id: "ruined-keep", name: "Ruined Keep" },
    config: { seed: 1, party_driver: "heuristic-tactical", enemy_driver: "heuristic-tactical" },
    winner: "party",
    rounds: 1,
    digest: "0".repeat(64),
    violations: [],
    initial_state: {
      round: 1,
      turn_order: ["pc-1", "en-1"],
      active_index: 0,
      bounds: SAMPLE_BOUNDS,
      terrain: SAMPLE_TERRAIN,
      combatants: {
        "pc-1": { id: "pc-1", name: "Aldric", side: "party", position: { x: 5, y: 5 }, hp: 44, hp_max: 44, ac: 19, alive: true, conditions: [] },
        "en-1": { id: "en-1", name: "Guard", side: "enemy", position: { x: 30, y: 5 }, hp: 20, hp_max: 20, ac: 15, alive: true, conditions: [] },
      },
    },
    final_state: {
      round: 1,
      turn_order: ["pc-1", "en-1"],
      active_index: 0,
      bounds: SAMPLE_BOUNDS,
      terrain: SAMPLE_TERRAIN,
      combatants: {
        "pc-1": { id: "pc-1", name: "Aldric", side: "party", position: { x: 5, y: 5 }, hp: 44, hp_max: 44, ac: 19, alive: true, conditions: [] },
        "en-1": { id: "en-1", name: "Guard", side: "enemy", position: { x: 30, y: 5 }, hp: 20, hp_max: 20, ac: 15, alive: true, conditions: [] },
      },
    },
    actions: [],
  };
  const ok = buildReplay(baseTrace);
  assert.equal(ok.ok, true, ok.errors.join("; "));
  assert.deepEqual(ok.replay.bounds, SAMPLE_BOUNDS);
  assert.deepEqual(ok.replay.terrain, SAMPLE_TERRAIN);

  const badTrace = JSON.parse(JSON.stringify(baseTrace));
  badTrace.initial_state.terrain.cover = [{ x: 5, y: 5, kind: "bogus" }];
  assert.equal(buildReplay(badTrace).ok, false);
});

test("tabletop-model respects battlefield bounds, walls, and difficult terrain in reachableSquares and clickAction", () => {
  const view = {
    round: 1,
    scenario: { id: "ruined-keep", name: "Ruined Keep" },
    bounds: { min_x: 0, min_y: 0, max_x: 20, max_y: 10 },
    terrain: {
      walls: [[10, 0], [10, 5], [10, 10]],
      difficult_terrain: [[5, 0]],
      cover: [{ x: 5, y: 5, kind: "half" }],
    },
    self: {
      id: "pc-1", name: "Aldric", side: "party", kind: "pc", position: { x: 0, y: 0 }, hp: 44, hp_max: 44, temp_hp: 0, ac: 19,
      speed_ft: 10, movement_remaining_ft: 10, conditions: [], concentration: null, effects: [], heroic_inspiration: false,
      resources: { action: true, bonus_action: true, reaction: true, attacks_remaining: 0 }, slots: {}, death_saves: { successes: 0, failures: 0 },
    },
    allies: [],
    enemies: [{ id: "en-1", name: "Guard", position: { x: 20, y: 0 }, alive: true, hp_band: "unharmed", conditions: [], effects: [] }],
    can_reroll: false,
    table_talk: [],
    recent_events: [],
    retry_feedback: null,
  };
  const payload = {
    status: "awaiting_input",
    scenario: { id: "ruined-keep", name: "Ruined Keep" },
    seat: "pc-1",
    features: { nl: false, save: false, out_trace: false, resume: false },
    decision: 1,
    view,
    menu: [{ index: 1, category: "turn", label: "End Turn", summary: "", intent: { type: "end_turn", actor_id: "pc-1" }, meta: {} }],
    log: [],
    log_next: 0,
    result: null,
    trace_written: false,
    checkpoint_written: false,
    pause_reason: null,
    notice: null,
    error: null,
  };
  assert.equal(validateState(payload).ok, true);

  const model = boardModel(view);
  assert.deepEqual(model.battlefieldBounds, view.bounds);
  assert.deepEqual(model.terrain, view.terrain);

  const reach = reachableSquares(view, model.bounds);
  const has = (x, y) => reach.some((sq) => sq.x === x && sq.y === y);
  assert.equal(has(-5, 0), false, "out of bounds excluded");
  assert.equal(has(10, 0), false, "wall excluded");
  assert.equal(has(15, 0), false, "behind sealed wall excluded");
  assert.equal(has(5, 0), true, "difficult terrain square reachable for 10 ft");
  assert.equal(has(0, 10), true, "normal 10 ft square reachable");

  const clickOob = clickAction(view, payload.menu, { x: -5, y: 0 }, reach);
  assert.equal(clickOob.type, "none");
  assert.match(clickOob.reason, /outside the battlefield bounds/);

  const clickWall = clickAction(view, payload.menu, { x: 10, y: 0 }, reach);
  assert.equal(clickWall.type, "none");
  assert.match(clickWall.reason, /blocked by a wall/);
});

test("CSP compliance: board-view, replay, and tabletop modules contain no inline styles or HTML injection", () => {
  const files = [
    "../lib/board-view.js",
    "../lib/replay-model.js",
    "../lib/replay-view.js",
    "../tabletop/tabletop-model.js",
    "../tabletop/tabletop.js",
  ];
  for (const rel of files) {
    const src = readFileSync(new URL(rel, import.meta.url), "utf8");
    assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/, rel);
    assert.doesNotMatch(src, /\.style\b/, rel);
    assert.doesNotMatch(src, /setAttribute\(\s*["']style["']/, rel);
  }
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  for (const cls of [".rp-bounds", ".rp-wall", ".rp-diff", ".rp-cov-half", ".rp-cov-3q"]) {
    assert.ok(css.includes(cls), `styles.css missing ${cls}`);
  }
});
