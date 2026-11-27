import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'; upgrade-insecure-requests";

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    if (name === "vendor" || name === "node_modules") continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, acc);
    else acc.push(path);
  }
  return acc;
}

function staticImportSpecifiers(source) {
  const specifiers = [];
  const pattern = /^\s*import\s+(?:[^'"\n]+from\s+)?["'](\.[^"']+)["']/gm;
  for (const match of source.matchAll(pattern)) specifiers.push(match[1]);
  return specifiers;
}

function moduleGraph(entry) {
  const seen = new Set();
  const pending = [entry];
  while (pending.length) {
    const file = pending.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    const source = readFileSync(file, "utf8");
    const dir = join(file, "..");
    for (const spec of staticImportSpecifiers(source)) {
      pending.push(join(dir, spec));
    }
  }
  return [...seen];
}

test("every HTML file has the CSP and no inline scripts or styles", () => {
  const htmlFiles = walk(webRoot).filter((file) => file.endsWith(".html"));
  assert.ok(htmlFiles.length >= 1);
  for (const file of htmlFiles) {
    const html = readFileSync(file, "utf8");
    assert.ok(html.includes(CSP), file);
    assert.equal(html.includes("unsafe-inline"), false, file);
    assert.doesNotMatch(html, /<style\b/i);
    assert.doesNotMatch(html, /\sstyle\s*=/i);
    assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
    assert.doesNotMatch(html, /javascript\s*:/i);
    assert.doesNotMatch(html, /<form\b/i);

    const scripts = html.match(/<script\b[^>]*>[\s\S]*?<\/script>/gi) || [];
    assert.ok(scripts.length >= 1, file);
    for (const script of scripts) {
      assert.match(script, /\bsrc\s*=\s*["'][^"']+["']/);
      assert.doesNotMatch(script, /\bsrc\s*=\s*["']https?:/i);
      assert.match(script, /^<script\b[^>]*>\s*<\/script>$/i);
    }
    assert.equal((html.match(/<script\b/gi) || []).length, scripts.length);

    const header = html.slice(html.indexOf("<header"), html.indexOf("</header>"));
    const nav = html.slice(html.indexOf("<nav"), html.indexOf("</nav>"));
        for (const part of [header, nav]) {
          assert.doesNotMatch(part, /D&D|D&amp;D|WotC|Wizards|dndbeyond|ampersand/i);
          assert.doesNotMatch(part, /<img\b/i);
        }
    assert.match(header, />5e 2024 SRD Agent Pack</);
    assert.match(header, />5E compatible</);
  }
});

test("first paint does not load the graph or the type shards", () => {
  const appPath = join(webRoot, "app.js");
  const app = readFileSync(appPath, "utf8");
  assert.match(app, /import\(\s*["']\.\/lib\/markdown\.js["']\s*\)/);
  const graph = moduleGraph(appPath).map((file) => readFileSync(file, "utf8")).join("\n");
  assert.doesNotMatch(graph, /graph\.json/);
  assert.doesNotMatch(graph, /\/types\//);
  assert.doesNotMatch(graph, /markdown-it/);
  assert.match(app, /manifest\.json/);
  assert.match(app, /web\/index\.json/);
  assert.equal(app.includes("graph.json"), false);
  assert.equal(app.includes("/types/"), false);
  assert.match(app, /import\("\.\/lib\/inspector-view\.js"\)/);
  const inspectorData = readFileSync(join(webRoot, "lib", "inspector-data.js"), "utf8");
  assert.match(inspectorData, /graph\.json/);
  assert.match(inspectorData, /\/types\//);

  const pkg = JSON.parse(readFileSync(join(webRoot, "package.json"), "utf8"));
  assert.equal(pkg.dependencies, undefined);
  assert.equal(pkg.devDependencies, undefined);
  assert.equal(pkg.type, "module");
});

test("shipped web files use system fonts only", () => {
  const files = walk(webRoot);
  assert.ok(files.length > 0);
  for (const file of files) {
    const source = readFileSync(file, "utf8");
    const inter = "Int" + "er";
    const plex = "IBM" + " Plex";
    const face = "@font-" + "face";
    const mock = "mock-" + "fonts";
    assert.doesNotMatch(source, new RegExp(face, "i"), file);
    assert.equal(source.includes(mock), false, file);
    assert.doesNotMatch(source, new RegExp(`\\b${plex}\\b`), file);
    assert.doesNotMatch(source, new RegExp(`\\b${inter}\\b`), file);
  }
  const css = readFileSync(join(webRoot, "styles.css"));
  const gzipped = gzipSync(css).length;
  assert.ok(gzipped < 7 * 1024, `styles.css gzipped ${gzipped} bytes`);
  assert.match(css.toString("utf8"), /system-ui/);
});

test("app code does not inject inline styles", () => {
  const sources = [
    join(webRoot, "app.js"),
    ...walk(join(webRoot, "lib")).filter((file) => file.endsWith(".js")),
  ];
  for (const file of sources) {
    const source = readFileSync(file, "utf8");
    assert.doesNotMatch(source, /\.style\b/, file);
    assert.doesNotMatch(source, /setAttribute\(\s*["']style["']/, file);
  }
  const app = readFileSync(join(webRoot, "app.js"), "utf8");
  assert.equal((app.match(/innerHTML/g) || []).length, 1);
});
