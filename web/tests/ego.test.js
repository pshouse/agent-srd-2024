import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { duplicateNodeIds, validateGraph } from "../lib/bundle.js";
import { GRAPH_NODE_CAP, indexAdjacency, layoutEgo, selectEgo } from "../lib/ego.js";
import { buildCatalog } from "../lib/resolve.js";
import { renderGraph } from "../lib/graph-view.js";
import { invalidateInspector } from "../lib/inspector-token.js";

const root = new URL("../../", import.meta.url);

function loadJson(path) {
  return JSON.parse(readFileSync(new URL(path, root), "utf8"));
}

const graph = loadJson("dist/web/graph.json");
const index = loadJson("dist/web/index.json");
const manifest = loadJson("dist/manifest.json");
const catalog = buildCatalog(index.entities);
const adj = indexAdjacency(graph.edges);

test("the shipped graph passes the stricter checks", () => {
  assert.deepEqual(validateGraph(graph, manifest), []);
});

test("validation rejects a missing endpoint, an unknown kind, and a duplicate node", () => {
  const ids = Object.keys(graph.nodes);
  const nodes = {};
  for (const id of ids) nodes[id] = graph.nodes[id];
  const dangling = {
    schema_version: "1",
    nodes,
    edges: [["missing:nope", ids[0], "class", null]],
  };
  assert.match(validateGraph(dangling, { counts: { edges: 1 } }).join("\n"), /endpoint is not a node/);

  const unknown = {
    schema_version: "1",
    nodes,
    edges: [[ids[0], ids[1], "not-a-kind", null]],
  };
  assert.match(validateGraph(unknown, { counts: { edges: 1 } }).join("\n"), /unknown edge kind/);

  const duplicate = {
    schema_version: "1",
    nodes: { "spells:a": { id: "spells:same" }, "spells:b": { id: "spells:same" } },
    edges: [],
  };
  assert.match(validateGraph(duplicate, { counts: { edges: 0 } }).join("\n"), /duplicate nodes/);
  assert.deepEqual(duplicateNodeIds(["spells:a", "spells:a"]), ["spells:a"]);
});

test("the cap keeps the root and reports how many neighbours were left out", () => {
  const wizard = selectEgo(adj, catalog.byId, "classes:wizard", { hops: 1, direction: "both" });
  assert.equal(wizard.root.id, "classes:wizard");
  assert.ok(wizard.neighbors.length <= GRAPH_NODE_CAP - 1);
  assert.equal(wizard.neighbors.length + 1 <= GRAPH_NODE_CAP, true);
  assert.equal(wizard.shown + wizard.hidden, wizard.total);
  assert.ok(wizard.hidden > 0);
  assert.equal(wizard.neighbors.some((item) => item.id === "classes:wizard"), false);
  const types = new Set(wizard.neighbors.map((item) => item.type));
  assert.ok(types.size > 1);

  const sword = selectEgo(adj, catalog.byId, "equipment:longsword", { hops: 1, direction: "both" });
  assert.equal(sword.hidden, 0);
  assert.ok(sword.neighbors.some((item) => item.id === "weapon-mastery-properties:sap"));
});

test("the same id lays out the same coordinates twice", () => {
  const first = selectEgo(adj, catalog.byId, "classes:wizard", { hops: 1, direction: "both" });
  const second = selectEgo(adj, catalog.byId, "classes:wizard", { hops: 1, direction: "both" });
  assert.deepEqual(second.neighbors.map((item) => item.id), first.neighbors.map((item) => item.id));
  assert.deepEqual(layoutEgo(second), layoutEgo(first));
  const layout = layoutEgo(first);
  assert.equal(layout.nodes[0].id, "classes:wizard");
  assert.equal(layout.nodes.length, first.neighbors.length + 1);
});

class FakeNode {
  constructor(tag) {
    this.nodeName = tag;
    this.className = "";
    this.dataset = {};
    this.children = [];
    this.attrs = {};
    this._text = "";
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
}

globalThis.Node = FakeNode;
globalThis.document = {
  createElement(tag) {
    return new FakeNode(tag);
  },
  createElementNS(_ns, tag) {
    return new FakeNode(tag);
  },
  createTextNode(text) {
    const node = new FakeNode("#text");
    node.textContent = text;
    return node;
  },
};

test("a route change while the graph file is loading keeps the explorer", async () => {
  const container = new FakeNode("div");
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const task = renderGraph(container, {
    routeId: "spells:fireball",
    catalog,
    manifest,
    hash: "#/graph/spells:fireball",
    loadGraph: () => pending,
  });
  assert.match(container.textContent, /Loading graph/);
  invalidateInspector(container);
  const explorer = new FakeNode("section");
  explorer.textContent = "Fireball the spell card";
  container.replaceChildren(explorer);
  release(graph);
  await task;
  assert.equal(container.textContent, "Fireball the spell card");
  assert.equal(container.textContent.includes("References around"), false);
});

test("corrupt graph data shows an error and no drawing", async () => {
  const container = new FakeNode("div");
  await renderGraph(container, {
    routeId: "spells:fireball",
    catalog,
    manifest,
    hash: "#/graph/spells:fireball",
    loadGraph: async () => ({ schema_version: "1", nodes: { "spells:fireball": { name: "Fireball" } }, edges: [] }),
  });
  assert.match(container.textContent, /This graph cannot be shown/);
  assert.equal(container.textContent.includes("<svg"), false);
  assert.equal(container.children.some((child) => child.nodeName === "svg"), false);
});
