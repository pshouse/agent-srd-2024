import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { liveHtmlHazards, renderCardMarkdown, rewriteCardHref } from "../lib/markdown.js";

const HOSTILE = `# Hostile

<script>alert(1)</script>

<img src="x" onerror="alert(1)">

[click](javascript:alert(1))

[click](JavaScript:alert(1))

<a href="javascript:alert(1)">raw</a>

[Fire](../damage-types/fire.md)

| Slot Level | Damage |
|---|---|
| 3 | 8d6 |
`;

test("hostile Markdown does not execute and produces no live HTML", () => {
  const rendered = renderCardMarkdown(HOSTILE);
  assert.equal(rendered.ok, true);
  assert.deepEqual(rendered.hazards, []);
  assert.deepEqual(liveHtmlHazards(rendered.html), []);

  assert.equal(rendered.html.includes("<script"), false);
  assert.equal(rendered.html.includes("&lt;script&gt;"), true);
  assert.equal(/<[^>]+\son[a-z]+\s*=/i.test(rendered.html), false);
  assert.equal(rendered.html.toLowerCase().includes("onerror="), true);
  assert.equal(/<a\b[^>]*href\s*=\s*["']?\s*javascript:/i.test(rendered.html), false);
  assert.equal(rendered.html.includes("[click](javascript:alert(1))"), true);
  assert.equal(/<a\b[^>]*>raw<\/a>/i.test(rendered.html), false);

  const tags = rendered.html.match(/<\/?([a-zA-Z0-9]+)\b[^>]*>/g) || [];
  for (const tag of tags) {
    assert.doesNotMatch(tag, /^<\/?script\b/i);
    assert.doesNotMatch(tag, /\son[a-z]+\s*=/i);
    assert.doesNotMatch(tag, /javascript\s*:/i);
  }
});

test("markdown images are not rendered", () => {
  const rendered = renderCardMarkdown("![alt text](javascript:alert(1))\n\n![picture](https://example.com/x.png)\n");
  assert.equal(rendered.ok, true);
  assert.equal(rendered.html.includes("<img"), false);
  assert.match(rendered.html, /alt text/);
  assert.match(rendered.html, /picture/);
  assert.deepEqual(liveHtmlHazards(rendered.html), []);
});

test("relative card links are rewritten and pipe tables render", () => {
  assert.equal(rewriteCardHref("../damage-types/fire.md"), "#/e/damage-types:fire");
  assert.equal(
    rewriteCardHref("../equipment-categories/martial-melee-weapons.md"),
    "#/e/equipment-categories:martial-melee-weapons",
  );
  assert.equal(rewriteCardHref("https://example.com/x"), "https://example.com/x");

  const rendered = renderCardMarkdown(HOSTILE);
  assert.match(rendered.html, /href="#\/e\/damage-types:fire"/);
  assert.match(rendered.html, /<table>/);
  assert.match(rendered.html, /<td>8d6<\/td>/);
});

test("the fireball card renders its table and hash links with no live HTML", () => {
  const source = readFileSync(new URL("../../dist/cards/spells/fireball.md", import.meta.url), "utf8");
  const rendered = renderCardMarkdown(source);
  assert.equal(rendered.ok, true);
  assert.deepEqual(liveHtmlHazards(rendered.html), []);
  assert.match(rendered.html, /<h1>Fireball/);
  assert.match(rendered.html, /href="#\/e\/magic-schools:evocation"/);
  assert.match(rendered.html, /href="#\/e\/damage-types:fire"/);
  assert.match(rendered.html, /href="#\/e\/classes:wizard"/);
  assert.match(rendered.html, /<table>/);
  assert.equal(rendered.html.includes("../"), false);
  assert.equal(rendered.html.includes("id: spells:fireball"), false);
});
