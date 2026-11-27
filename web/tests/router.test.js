import assert from "node:assert/strict";
import test from "node:test";

import { cardAssetUrl, cardRelativePath, distAssetUrl } from "../lib/paths.js";
import { parseRoute } from "../lib/router.js";

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
