import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ATTRIBUTION, attributionPieces } from "../lib/attribution.js";

const manifest = JSON.parse(readFileSync(new URL("../../dist/manifest.json", import.meta.url), "utf8"));
const official = manifest.license.attribution;

const EXPECTED =
  "This work includes material from the System Reference Document 5.2 (\u201cSRD 5.2\u201d) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.";

test("the footer attribution equals dist/manifest.json license.attribution byte for byte", () => {
  assert.equal(typeof official, "string");
  assert.ok(official.length > 0);
  const footerBytes = Buffer.from(ATTRIBUTION, "utf8");
  const manifestBytes = Buffer.from(official, "utf8");
  assert.equal(footerBytes.equals(manifestBytes), true);
  assert.equal(ATTRIBUTION, official);
  const rendered = attributionPieces(official).map((piece) => piece.value).join("");
  assert.equal(Buffer.from(rendered, "utf8").equals(manifestBytes), true);
});

test("the attribution string matches the official sentence exactly", () => {
  assert.equal(ATTRIBUTION, EXPECTED);
  assert.equal(ATTRIBUTION, official);
  assert.equal(ATTRIBUTION.includes("\u201cSRD 5.2\u201d"), true);
  assert.equal(ATTRIBUTION.includes('"SRD 5.2"'), false);
  assert.equal(
    ATTRIBUTION.includes("License, available at https://creativecommons.org/licenses/by/4.0/legalcode."),
    true,
  );
});

test("the attribution pieces join back to the official sentence", () => {
  const pieces = attributionPieces();
  assert.equal(pieces.map((piece) => piece.value).join(""), ATTRIBUTION);
  assert.deepEqual(
    pieces.filter((piece) => piece.type === "url").map((piece) => piece.value),
    ["https://www.dndbeyond.com/srd", "https://creativecommons.org/licenses/by/4.0/legalcode"],
  );
});

test("the page assigns that exact string to the footer", () => {
  const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(app, /placeAttribution\(quoteEl\)/);
  const gate = app.slice(app.indexOf("const decision = bootViewModel"));
  assert.match(gate, /if \(!decision\.ok\) \{[\s\S]*?return;\s*\}\s*const official = trustedAttribution\(manifest\);\s*if \(quoteEl && official\) placeAttribution\(quoteEl, official\)/);
  assert.equal(app.includes("manifest.license.attribution"), false);
  assert.match(app, /attributionPieces\(text\)/);
  assert.match(app, /text = ATTRIBUTION/);
  assert.match(html, /id="attribution"/);
  assert.match(html, /Independent project, not an official product/);
  assert.match(html, /Mischief Dev LLC/);
  assert.doesNotMatch(html, /not affiliated with/i);
  assert.match(html, /5e-bits\/5e-srd-api/);
  assert.match(html, /\(MIT\)/);
  assert.match(html, /href="\.\.\/bin\/srd"/);
  assert.match(html, /href="\.\.\/bin\/srd-mcp"/);
  assert.match(html, /href="\.\.\/skill\/SKILL\.md"/);
});
