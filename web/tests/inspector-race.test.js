import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { invalidateInspector } from "../lib/inspector-token.js";
import { renderInspector } from "../lib/inspector-view.js";
import { buildCatalog } from "../lib/resolve.js";

class FakeNode {
  constructor(tag) {
    this.nodeName = tag;
    this.className = "";
    this.hidden = false;
    this.dataset = {};
    this.children = [];
    this.attrs = {};
    this._text = "";
    this.classList = { add() {}, remove() {} };
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((child) => child.textContent || "").join("");
  }

  setAttribute(name, value) {
    this.attrs[name] = value;
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = nodes;
    this._text = "";
  }

  addEventListener() {}

  focus() {}

  select() {}
}

globalThis.Node = FakeNode;
globalThis.document = {
  createElement(tag) {
    return new FakeNode(tag);
  },
  createTextNode(text) {
    const node = new FakeNode("#text");
    node.textContent = text;
    return node;
  },
};

function countValidGraph(edges) {
  const nodes = {};
  for (const edge of edges) {
    nodes[edge[0]] = { name: edge[0] };
    nodes[edge[1]] = { name: edge[1] };
  }
  let i = 0;
  while (Object.keys(nodes).length < 2029) {
    nodes[`pad:n${i}`] = 1;
    i += 1;
  }
  return { schema_version: "1", nodes, edges };
}

const fireball = {
  id: "spells:fireball",
  type: "spells",
  slug: "fireball",
  name: "Fireball",
  summary: "Boom",
  card_tokens_est: 20,
};
const fire = {
  id: "damage-types:fire",
  type: "damage-types",
  slug: "fire",
  name: "Fire",
  summary: "Hot",
  card_tokens_est: 10,
};

test("a route change while the graph is loading keeps the explorer", async () => {
  const container = new FakeNode("div");
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const task = renderInspector(container, {
    routeId: "spells:fireball",
    catalog: buildCatalog([fireball]),
    manifest: { counts: { edges: 0 } },
    hash: "#/bundle/spells:fireball?depth=1&max=2000",
    attribution: "attr",
    setHash() {},
    loadGraph: () => pending,
  });
  assert.match(container.textContent, /Loading bundle/);
  invalidateInspector(container);
  const explorer = new FakeNode("section");
  explorer.textContent = "Fireball the spell card";
  container.replaceChildren(explorer);
  release(countValidGraph([]));
  await task;
  assert.equal(container.textContent, "Fireball the spell card");
  assert.equal(container.textContent.includes("Bundle for"), false);
  assert.equal(container.textContent.includes("What gets copied"), false);
});

test("render() invalidates the inspector token before choosing a route", () => {
  const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const renderAt = app.indexOf("function render() {");
  const nextFn = app.indexOf("\n  function ", renderAt + 20);
  const body = app.slice(renderAt, nextFn === -1 ? app.length : nextFn);
  const invalidateAt = body.indexOf("invalidateInspector(resultsCol)");
  const bundleAt = body.indexOf("if (bundleRoute)");
  assert.ok(invalidateAt > -1);
  assert.ok(bundleAt > invalidateAt);
});

test("a count-valid graph with an unknown edge shows a fail-closed error and no preview", async () => {
  const container = new FakeNode("div");
  const catalog = buildCatalog([fireball]);
  await renderInspector(container, {
    routeId: "spells:fireball",
    catalog,
    manifest: { counts: { edges: 1 } },
    hash: "#/bundle/spells:fireball?depth=1&max=2000",
    attribution: "attr",
    setHash() {},
    loadGraph: async () => countValidGraph([["spells:fireball", "missing:nope", "damage_type", null]]),
    loadShard: async () => ({ "spells:fireball": { card_body_md: "# Fireball\n\nBoom" } }),
  });
  assert.match(container.textContent, /This bundle cannot be shown/);
  assert.match(container.textContent, /unknown entry: missing:nope/);
  assert.equal(container.textContent.includes("What gets copied"), false);
  assert.equal(container.textContent.includes("Loading bundle"), false);
});

test("a count-valid graph with a missing card body shows a fail-closed error and no preview", async () => {
  const container = new FakeNode("div");
  const catalog = buildCatalog([fireball, fire]);
  await renderInspector(container, {
    routeId: "spells:fireball",
    catalog,
    manifest: { counts: { edges: 1 } },
    hash: "#/bundle/spells:fireball?depth=1&max=2000",
    attribution: "attr",
    setHash() {},
    loadGraph: async () => countValidGraph([["spells:fireball", "damage-types:fire", "damage_type", null]]),
    loadShard: async (type) => {
      if (type === "spells") return { "spells:fireball": { card_body_md: "# Fireball\n\nBoom" } };
      return {};
    },
  });
  assert.match(container.textContent, /This bundle cannot be shown/);
  assert.match(container.textContent, /card body for damage-types:fire is missing/);
  assert.equal(container.textContent.includes("What gets copied"), false);
  assert.equal(container.textContent.includes("# Fireball"), false);
});
