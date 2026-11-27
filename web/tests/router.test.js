import assert from "node:assert/strict";
import test from "node:test";

import { cardAssetUrl, cardRelativePath, distAssetUrl } from "../lib/paths.js";
import { readFileSync } from "node:fs";

import { parseRoute, readHashState } from "../lib/router.js";

test("hash routes ignore the Pages base path and the local directory", () => {
  assert.deepEqual(parseRoute(""), { name: "explorer", unknown: false });
  assert.deepEqual(parseRoute("#"), { name: "explorer", unknown: false });
  assert.deepEqual(parseRoute("#/"), { name: "explorer", unknown: false });
  assert.deepEqual(parseRoute("#/e/spells:fireball"), {
    name: "card",
    type: "spells",
    slug: "fireball",
    id: "spells:fireball",
    unknown: false,
  });
  assert.equal(parseRoute("#/e/Spells:Fireball").id, "spells:fireball");
  assert.equal(parseRoute("#/e/spells%3Afireball").id, "spells:fireball");
  assert.equal(parseRoute("#/missing").unknown, true);
  assert.equal(parseRoute("#/e/shield").name, "explorer");
});

const ALLOWED = ["level", "class", "school"];

test("boot hash ignores __proto__, constructor, and prototype", () => {
  for (const key of ["__proto__", "constructor", "prototype"]) {
    const state = readHashState(`#/?${key}=x&type=spells&level=3`, ALLOWED);
    assert.equal(state.route.name, "explorer");
    assert.equal(state.route.unknown, false);
    assert.equal(state.type, "spells");
    assert.deepEqual(state.facets.level, ["3"]);
    assert.equal(Object.getPrototypeOf(state.facets), null);
    assert.equal(Object.hasOwn(state.facets, key), false);
    assert.equal(state.facets[key], undefined);
    assert.doesNotThrow(() => state.facets.level.push("4"));
  }
});

test("hashchange ignores __proto__, constructor, and prototype", () => {
  for (const key of ["__proto__", "constructor", "prototype"]) {
    const state = readHashState(`#/e/spells:fireball?${key}=x&q=fire&level=3`, ALLOWED);
    assert.equal(state.route.name, "card");
    assert.equal(state.route.id, "spells:fireball");
    assert.equal(state.q, "fire");
    assert.deepEqual(state.facets.level, ["3"]);
    assert.equal(Object.getPrototypeOf(state.facets), null);
    assert.equal(Object.hasOwn(state.facets, key), false);
    assert.equal(state.facets[key], undefined);
  }
});

test("boot and hashchange both read the hash through readHashState", () => {
  const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  assert.match(app, /addEventListener\("hashchange", render\)/);
  assert.match(app, /function readState\(\) \{\s*return readHashState\(/);
  assert.match(app, /\n {2}render\(\);\n/);
});

test("hostile graph and card ids are not routes", () => {
  for (const id of ["__proto__", "constructor", "prototype"]) {
    const graph = parseRoute(`#/graph/${id}`);
    assert.equal(graph.name, "explorer");
    assert.equal(graph.unknown, true);
    assert.equal(graph.id, undefined);
    const card = parseRoute(`#/e/${id}:x`);
    assert.equal(card.name, "explorer");
    assert.equal(card.unknown, true);
  }
  const split = parseRoute("#/graph/spells/fireball");
  assert.equal(split.name, "explorer");
  assert.equal(split.unknown, true);
  for (const enc of ["%00", "%0a", "%1f", "%7f"]) {
    const route = parseRoute(`#/bundle/chain${enc}mail`);
    assert.equal(route.name, "explorer");
    assert.equal(route.unknown, true);
  }
  const longId = `spells:${"a".repeat(500)}`;
  const longRoute = parseRoute(`#/graph/${longId}`);
  assert.equal(longRoute.name, "explorer");
  assert.equal(longRoute.unknown, true);
  assert.equal(parseRoute("#/graph/spells:fireball").id, "spells:fireball");
  assert.equal(parseRoute("#/graph/").id, "");
});

test("every catalog id and name round-trips through the router", () => {
  const index = JSON.parse(readFileSync(new URL("../../dist/web/index.json", import.meta.url), "utf8"));
  assert.equal(index.entities.length, 2029);
  for (const entity of index.entities) {
    for (const view of ["bundle", "graph"]) {
      const byId = parseRoute(`#/${view}/${encodeURIComponent(entity.id)}`);
      assert.equal(byId.name, "later", entity.id);
      assert.equal(byId.view, view);
      assert.equal(byId.id, entity.id);
      const byName = parseRoute(`#/${view}/${encodeURIComponent(entity.name)}`);
      assert.equal(byName.name, "later", entity.name);
      assert.equal(byName.view, view);
      assert.equal(byName.id, entity.name.toLowerCase());
    }
    const card = parseRoute(`#/e/${encodeURIComponent(entity.id)}`);
    assert.equal(card.name, "card", entity.id);
    assert.equal(card.id, entity.id);
  }
  assert.equal(parseRoute("#/bundle/chain%20mail").id, "chain mail");
  assert.equal(parseRoute("#/bundle/wand%20of%20wonder").id, "wand of wonder");
  assert.equal(parseRoute("#/bundle/Dragon%27s%20Breath").id, "dragon's breath");
});

test("asset URLs stay relative to the app under both base paths", () => {
  const pages = distAssetUrl(
    "https://example.github.io/agent-srd-2024/web/app.js",
    "manifest.json",
  );
  assert.equal(pages.href, "https://example.github.io/agent-srd-2024/dist/manifest.json");

  const localIndex = distAssetUrl("http://127.0.0.1:8000/web/app.js", "web/index.json");
  assert.equal(localIndex.href, "http://127.0.0.1:8000/dist/web/index.json");

  const card = cardAssetUrl("http://127.0.0.1:8000/web/app.js", "spells", "fireball");
  assert.equal(card.href, "http://127.0.0.1:8000/dist/cards/spells/fireball.md");

  assert.equal(cardRelativePath("spells", "fireball"), "cards/spells/fireball.md");
  assert.equal(cardRelativePath("../spells", "fireball"), null);
  assert.equal(cardRelativePath("spells", "fire/ball"), null);
  assert.equal(cardAssetUrl("http://127.0.0.1:8000/web/app.js", "spells", ".."), null);
});
