import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ATTRIBUTION } from "../lib/attribution.js";
import {
  copyAgentContext,
  indexGraph,
  packBundle,
  renderBundle,
  validateGraph,
} from "../lib/bundle.js";
import { trustedAttribution } from "../lib/boot.js";
import { buildCatalog, resolveIdentifier } from "../lib/resolve.js";

const root = new URL("../../", import.meta.url);

function loadJson(path) {
  return JSON.parse(readFileSync(new URL(path, root), "utf8"));
}

function loadWorld() {
  const index = loadJson("dist/web/index.json");
  const graph = loadJson("dist/web/graph.json");
  const manifest = loadJson("dist/manifest.json");
  const catalog = buildCatalog(index.entities);
  const { outgoing, incoming } = indexGraph(graph.edges, catalog.byId);
  const shards = new Map();
  function bodyOf(id) {
    const entity = catalog.byId.get(id);
    if (!shards.has(entity.type)) {
      shards.set(entity.type, loadJson(`dist/web/types/${entity.type}.json`));
    }
    const record = shards.get(entity.type)[id];
    if (!record || typeof record.card_body_md !== "string") {
      throw new Error(`missing card body for ${id}`);
    }
    return record.card_body_md;
  }
  return { catalog, outgoing, incoming, bodyOf, resolveIdentifier, graph, manifest };
}

const world = loadWorld();

function cliText(name) {
  return readFileSync(new URL(`fixtures/${name}`, import.meta.url));
}

function assertParity(name, identifier, options) {
  const payload = packBundle(world, identifier, options);
  const rendered = renderBundle(payload);
  const golden = cliText(name);
  assert.equal(Buffer.from(`${rendered}\n`, "utf8").equals(golden), true, name);
  return payload;
}

test("equipment:longsword depth 1 includes sap, versatile, and slashing", () => {
  const payload = assertParity("equipment-longsword-d1-t2000.md", "equipment:longsword", {
    depth: 1,
    maxTokens: 2000,
  });
  const ids = payload.dependencies.filter((dep) => dep.detail === "full").map((dep) => dep.id);
  assert.ok(ids.includes("weapon-mastery-properties:sap"));
  assert.ok(ids.includes("weapon-properties:versatile"));
  assert.ok(ids.includes("damage-types:slashing"));
});

test("spells:fireball depth 1 expands evocation and fire and stubs the class hubs", () => {
  const payload = assertParity("spells-fireball-d1-t2000.md", "spells:fireball", {
    depth: 1,
    maxTokens: 2000,
  });
  const full = payload.dependencies.filter((dep) => dep.detail === "full").map((dep) => dep.id);
  const summary = payload.dependencies.filter((dep) => dep.detail === "summary").map((dep) => dep.id);
  assert.deepEqual(full.sort(), ["damage-types:fire", "magic-schools:evocation"]);
  assert.ok(summary.includes("classes:wizard"));
  assert.ok(summary.includes("classes:sorcerer"));
});

test("classes:wizard at a tight budget keeps the root and omits dependencies", () => {
  const payload = assertParity("classes-wizard-d1-t200.md", "classes:wizard", {
    depth: 1,
    maxTokens: 200,
  });
  assert.equal(payload.stats.budget_exceeded, true);
  assert.equal(payload.stats.full_count, 0);
  assert.equal(payload.stats.summary_count, 0);
  assert.ok(payload.stats.omitted_ids.length > 0);
  assert.equal(payload.root.card_body_md.length > 0, true);
});

test("an ambiguous slug matches the CLI and does not guess", () => {
  const payload = packBundle(world, "shield", { depth: 1, maxTokens: 2000 });
  assert.equal(payload.status, "ambiguous");
  assert.equal(payload.candidates.length, 3);
  const rendered = renderBundle(payload);
  assert.equal(Buffer.from(`${rendered}\n`, "utf8").equals(cliText("shield-ambiguous.md")), true);
});

test("longsword at 800 tokens matches the CLI", () => {
  assertParity("equipment-longsword-d1-t800.md", "equipment:longsword", {
    depth: 1,
    maxTokens: 800,
  });
});

test("the copy payload is the parity bundle plus the attribution", () => {
  const payload = packBundle(world, "equipment:longsword", { depth: 1, maxTokens: 2000 });
  const markdown = renderBundle(payload);
  const copied = copyAgentContext(markdown, ATTRIBUTION);
  assert.equal(copied.startsWith(markdown), true);
  assert.equal(copied.endsWith(ATTRIBUTION), true);
  assert.match(copied, /<!-- bundle: id=equipment:longsword /);
  assert.equal(markdown.includes(ATTRIBUTION), false);
  const official = world.manifest.license.attribution;
  assert.equal(Buffer.from(ATTRIBUTION, "utf8").equals(Buffer.from(official, "utf8")), true);
  assert.equal(copied.includes(official), true);
});

test("the inspector graph fails closed unless schema, nodes, and edges agree", () => {
  assert.deepEqual(validateGraph(world.graph, world.manifest), []);
  const badVersion = { ...world.graph, schema_version: "2" };
  assert.match(validateGraph(badVersion, world.manifest).join("\n"), /schema_version/);
  const badNodes = { ...world.graph, nodes: { only: {} } };
  assert.match(validateGraph(badNodes, world.manifest).join("\n"), /node count/);
  const badEdges = { ...world.graph, edges: world.graph.edges.slice(0, 3) };
  assert.match(validateGraph(badEdges, world.manifest).join("\n"), /edges\.length/);
  const poisoned = {
    schema_version: "9",
    dataset_id: "srd-2024-5.2",
    counts: { entities: 2029 },
    license: { attribution: "POISON ATTRIBUTION" },
  };
  assert.equal(trustedAttribution(poisoned), null);
  assert.equal(trustedAttribution(world.manifest), world.manifest.license.attribution);
});

test("a hostile card body stays literal text in the bundle", () => {
  const hostile = "<script>alert(1)</script>\n<img src=x onerror=alert(1)>";
  const custom = {
    ...world,
    bodyOf(id) {
      if (id === "conditions:blinded") return hostile;
      return world.bodyOf(id);
    },
  };
  const payload = packBundle(custom, "conditions:blinded", { depth: 0, maxTokens: 2000 });
  const markdown = renderBundle(payload);
  assert.equal(markdown.includes(hostile), true);
  assert.equal(markdown.includes("<img"), true);
  assert.equal(markdown.startsWith(hostile.trimEnd()), true);
});
