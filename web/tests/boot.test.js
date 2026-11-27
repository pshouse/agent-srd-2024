import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { bootViewModel, validateBoot } from "../lib/boot.js";

const root = new URL("../../", import.meta.url);

function entities(count, name = "Sample") {
  return Array.from({ length: count }, (_, index) => ({
    id: `spells:s${index}`,
    type: "spells",
    slug: `s${index}`,
    name: index === 0 ? name : `Sample ${index}`,
    summary: "",
    facets: {},
  }));
}

function pair({
  schema = "1",
  indexSchema = "1",
  manifestDataset = "srd-2024-5.2",
  indexDataset = "srd-2024-5.2",
  count = 2029,
  length = 2029,
  name = "Sample",
} = {}) {
  return {
    manifest: {
      schema_version: schema,
      dataset_id: manifestDataset,
      counts: { entities: count },
    },
    index: {
      schema_version: indexSchema,
      dataset_id: indexDataset,
      entities: entities(length, name),
    },
  };
}

test("a valid manifest and index pass and expose entities", () => {
  const { manifest, index } = pair();
  const decision = bootViewModel(manifest, index);
  assert.equal(decision.ok, true);
  assert.equal(decision.entities.length, 2029);
  assert.deepEqual(validateBoot(manifest, index), []);
});

test("a bad schema version is a hard error and renders no entities", () => {
  for (const schema of ["2", 1, ""]) {
    const { manifest, index } = pair({ schema, name: "Fireball" });
    const decision = bootViewModel(manifest, index);
    assert.equal(decision.ok, false, `schema ${String(schema)}`);
    assert.equal(decision.entities, null);
    assert.match(decision.errors.join("\n"), /schema_version/);
    assert.equal(decision.errors.join("\n").includes("Fireball"), false);
  }
  const missing = pair({ name: "Fireball" });
  delete missing.manifest.schema_version;
  const decision = bootViewModel(missing.manifest, missing.index);
  assert.equal(decision.ok, false);
  assert.equal(decision.entities, null);
  assert.match(decision.errors.join("\n"), /schema_version/);
});

test("a bad dataset_id on the manifest or the index is a hard error", () => {
  const manifestBad = pair({ manifestDataset: "srd-2014", name: "Fireball" });
  const manifestDecision = bootViewModel(manifestBad.manifest, manifestBad.index);
  assert.equal(manifestDecision.ok, false);
  assert.equal(manifestDecision.entities, null);
  assert.match(manifestDecision.errors.join("\n"), /manifest\.dataset_id/);

  const indexBad = pair({ indexDataset: "other", name: "Fireball" });
  const indexDecision = bootViewModel(indexBad.manifest, indexBad.index);
  assert.equal(indexDecision.ok, false);
  assert.equal(indexDecision.entities, null);
  assert.match(indexDecision.errors.join("\n"), /index\.dataset_id/);
  assert.equal(indexDecision.errors.join("\n").includes("Fireball"), false);
});

test("a bad entity count on the manifest or the index is a hard error", () => {
  const shortCount = pair({ count: 2028, name: "Fireball" });
  const countDecision = bootViewModel(shortCount.manifest, shortCount.index);
  assert.equal(countDecision.ok, false);
  assert.equal(countDecision.entities, null);
  assert.match(countDecision.errors.join("\n"), /manifest\.counts\.entities/);

  const stringCount = pair({ count: "2029" });
  assert.equal(bootViewModel(stringCount.manifest, stringCount.index).ok, false);

  const shortList = pair({ length: 2028, name: "Fireball" });
  const listDecision = bootViewModel(shortList.manifest, shortList.index);
  assert.equal(listDecision.ok, false);
  assert.equal(listDecision.entities, null);
  assert.match(listDecision.errors.join("\n"), /index\.entities\.length/);
  assert.equal(listDecision.errors.join("\n").includes("Fireball"), false);
});

test("mismatched version, dataset_id, and count are all reported together", () => {
  const { manifest, index } = pair({
    schema: "9",
    manifestDataset: "nope",
    indexDataset: "also-nope",
    count: 1,
    length: 1,
  });
  const errors = validateBoot(manifest, index);
  assert.equal(errors.length, 6);
});

test("a bad index.schema_version is a hard error and renders no entities", () => {
  for (const indexSchema of ["2", 1, ""]) {
    const { manifest, index } = pair({ indexSchema, name: "Fireball" });
    const decision = bootViewModel(manifest, index);
    assert.equal(decision.ok, false, `index schema ${String(indexSchema)}`);
    assert.equal(decision.entities, null);
    assert.match(decision.errors.join("\n"), /index\.schema_version/);
    assert.equal(decision.errors.join("\n").includes("Fireball"), false);
  }
  const missing = pair({ name: "Fireball" });
  delete missing.index.schema_version;
  const decision = bootViewModel(missing.manifest, missing.index);
  assert.equal(decision.ok, false);
  assert.equal(decision.entities, null);
  assert.match(decision.errors.join("\n"), /index\.schema_version/);
});

test("duplicate entity ids fail closed", () => {
  const { manifest, index } = pair({ name: "Fireball" });
  index.entities[1].id = index.entities[0].id;
  const decision = bootViewModel(manifest, index);
  assert.equal(decision.ok, false);
  assert.equal(decision.entities, null);
  assert.match(decision.errors.join("\n"), /id set size is 2028/);
  assert.equal(decision.errors.join("\n").includes("Fireball"), false);
  const ids = new Set(index.entities.map((entity) => entity.id));
  assert.equal(ids.size, 2028);
});

test("the shipped manifest and index pass without consulting the graph", () => {
  const manifest = JSON.parse(readFileSync(new URL("dist/manifest.json", root), "utf8"));
  const index = JSON.parse(readFileSync(new URL("dist/web/index.json", root), "utf8"));
  const decision = bootViewModel(manifest, index);
  assert.equal(decision.ok, true);
  assert.equal(decision.entities.length, 2029);
  assert.equal(validateBoot.length, 2);
});
