import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { facetValueLabel, familyOf, orderFacetKeys } from "../lib/facets.js";
import { buildCatalog, openTarget, resolveIdentifier } from "../lib/resolve.js";
import { matchRanges, searchEntities } from "../lib/search.js";

const root = new URL("../../", import.meta.url);
const index = JSON.parse(readFileSync(new URL("dist/web/index.json", root), "utf8"));
const catalog = buildCatalog(index.entities);

function fixtureCatalog() {
  return buildCatalog([
    {
      id: "spells:dragons-breath",
      type: "spells",
      slug: "dragons-breath",
      name: "Dragon\u2019s Breath",
      summary: "A cone of fire.",
      facets: { level: ["2"] },
    },
    {
      id: "spells:fireball",
      type: "spells",
      slug: "fireball",
      name: "Fireball",
      summary: "Level 3 Evocation",
      facets: { level: ["3"], school: ["evocation"], class: ["wizard"] },
    },
    {
      id: "equipment:shield",
      type: "equipment",
      slug: "shield",
      name: "Shield",
      summary: "Armor.",
      facets: {},
    },
    {
      id: "magic-items:shield",
      type: "magic-items",
      slug: "shield",
      name: "Shield",
      summary: "Magic armor.",
      facets: {},
    },
    {
      id: "spells:shield",
      type: "spells",
      slug: "shield",
      name: "Shield",
      summary: "A ward.",
      facets: { level: ["1"] },
    },
  ]);
}

test("facet labels and families follow the design", () => {
  assert.equal(facetValueLabel("level", "0"), "Cantrip");
  assert.equal(facetValueLabel("concentration", "true"), "Yes");
  assert.equal(facetValueLabel("cr", "0.125"), "1/8");
  assert.equal(familyOf("spells").id, "spell");
  assert.equal(familyOf("monsters").id, "mon");
  assert.deepEqual(orderFacetKeys(["ritual", "school", "level", "class"]).slice(0, 3), ["level", "class", "school"]);
});

test("typographic ranges line up with the original characters", () => {
  assert.deepEqual(matchRanges("Dragon\u2019s Breath", "Dragon's"), [[0, 8]]);
});

test("Dragon's matches a curly apostrophe in the name", () => {
  const mini = fixtureCatalog();
  for (const query of ["Dragon's", "DRAGON'S", "Dragon\u2019s"]) {
    const hits = searchEntities(mini, query);
    assert.deepEqual(
      hits.map((entity) => entity.id),
      ["spells:dragons-breath"],
      query,
    );
  }
});

test("spells:fireball opens and a colliding slug does not", () => {
  const mini = fixtureCatalog();
  const opened = openTarget(mini, "spells:fireball");
  assert.equal(opened.action, "open");
  assert.equal(opened.id, "spells:fireball");
  assert.equal(opened.hash, "#/e/spells:fireball");

  const shield = openTarget(mini, "shield");
  assert.equal(shield.action, "ambiguous");
  assert.deepEqual(
    shield.candidates.map((candidate) => candidate.id),
    ["equipment:shield", "magic-items:shield", "spells:shield"],
  );
});

test("the shipped catalog resolves fireball, typographic names, and all 53 collisions", () => {
  const opened = openTarget(catalog, "spells:fireball");
  assert.equal(opened.action, "open");
  assert.equal(opened.hash, "#/e/spells:fireball");
  assert.equal(opened.entity.name, "Fireball");

  const wisp = searchEntities(catalog, "Will-o'-Wisp");
  assert.equal(wisp[0].id, "monsters:will-o-wisp");
  assert.equal(wisp[0].name, "Will-o\u2019-Wisp");

  const mood = searchEntities(catalog, "person's");
  assert.ok(mood.some((entity) => entity.id === "skills:insight"));

  const idHits = searchEntities(catalog, "spells:fireball");
  assert.equal(idHits[0].id, "spells:fireball");

  const groups = new Map();
  for (const entity of index.entities) {
    const group = groups.get(entity.slug) || [];
    group.push(entity);
    groups.set(entity.slug, group);
  }
  const collisions = [...groups.values()].filter((group) => group.length > 1);
  assert.equal(collisions.length, 53);
  for (const group of collisions) {
    const resolved = resolveIdentifier(catalog, group[0].slug);
    assert.equal(resolved.status, "ambiguous", group[0].slug);
    assert.deepEqual(
      resolved.candidates.map((candidate) => candidate.id),
      group.map((entity) => entity.id).sort(),
    );
    assert.equal(openTarget(catalog, group[0].slug).action, "ambiguous");
  }
});

test("facet filters combine with AND across keys", () => {
  const hits = searchEntities(catalog, "", {
    type: "spells",
    facets: { level: ["3"], school: ["evocation"] },
  });
  assert.ok(hits.some((entity) => entity.id === "spells:fireball"));
  assert.ok(hits.every((entity) => entity.type === "spells"));
  assert.ok(hits.every((entity) => entity.facets.level.includes("3")));
  assert.ok(hits.every((entity) => entity.facets.school.includes("evocation")));
});

test("API URLs resolve exactly, including level and tool-proficiency aliases", () => {
  assert.equal(
    resolveIdentifier(catalog, "/api/2024/spells/fireball").entity.id,
    "spells:fireball",
  );
  assert.equal(
    resolveIdentifier(catalog, "/api/2024/classes/barbarian/levels/1").entity.id,
    "levels:barbarian-1",
  );
  assert.equal(
    resolveIdentifier(catalog, "/api/2024/subclasses/berserker/levels/3").entity.id,
    "levels:berserker-3",
  );
  assert.equal(
    resolveIdentifier(catalog, "/api/2024/proficiencies/tool-smiths-tools").entity.id,
    "proficiencies:smiths-tools",
  );
  assert.equal(resolveIdentifier(catalog, "/api/2024/spells/not-a-spell").status, "not_found");
});

test("every stored API URL resolves to its own id", () => {
  const db = new DatabaseSync(fileURLToPath(new URL("dist/srd2024.sqlite", root)), {
    readOnly: true,
  });
  try {
    const rows = db.prepare("SELECT id, api_url FROM entities").all();
    assert.equal(rows.length, 2029);
    for (const row of rows) {
      const resolved = resolveIdentifier(catalog, row.api_url);
      assert.equal(resolved.status, "ok", row.api_url);
      assert.equal(resolved.entity.id, row.id, row.api_url);
    }
  } finally {
    db.close();
  }
});
