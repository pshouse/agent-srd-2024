import { ATTRIBUTION } from "./attribution.js";
import {
  copyAgentContext,
  estTokens,
  expandOutgoing,
  indexGraph,
  packBundle,
  renderBundle,
  validateGraph,
} from "./bundle.js";
import { familyOf, typeLabel } from "./facets.js";
import { loadGraph, loadShard } from "./inspector-data.js";
import { claimInspector, inspectorStale } from "./inspector-token.js";
import { resolveIdentifier } from "./resolve.js";

const LOG_MIN = Math.log(200);
const LOG_MAX = Math.log(16000);

/**
 * @param {HTMLElement} container
 * @param {{ catalog: object, manifest: object, routeId: string, hash: string, attribution: string }} ctx
 */
export async function renderInspector(container, ctx) {
  const token = claimInspector(container);
  const stale = () => inspectorStale(container, token);
  const fetchGraph = ctx.loadGraph || loadGraph;
  const fetchShard = ctx.loadShard || loadShard;
  const fail = (text) => {
    if (stale()) return;
    container.replaceChildren(problem(text, true));
  };
  try {
    container.replaceChildren(el("p", { class: "loading", text: "Loading bundle…" }));
    if (!ctx.routeId) {
      if (stale()) return;
      container.replaceChildren(emptyInspector());
      return;
    }
    let graph;
    try {
      graph = await fetchGraph();
    } catch {
      fail("The cross-reference graph could not be loaded.");
      return;
    }
    if (stale()) return;
    const errors = validateGraph(graph, ctx.manifest);
    if (errors.length) {
      fail(errors.join(" "));
      return;
    }
    const world = worldFrom(ctx.catalog, graph);
    const params = readInspectorQuery(ctx.hash);
    const resolved = resolveIdentifier(ctx.catalog, ctx.routeId);
    if (resolved.status !== "ok") {
      if (stale()) return;
      container.replaceChildren(unresolvedView(resolved));
      return;
    }
    const relFilter = params.rels;
    const { depIds } = expandOutgoing(world.outgoing, resolved.entity.id, params.depth, relFilter);
    const types = new Set([resolved.entity.type]);
    for (const id of depIds) {
      const entity = ctx.catalog.byId.get(id);
      if (entity) types.add(entity.type);
    }
    try {
      await Promise.all([...types].map((type) => fetchShard(type)));
    } catch {
      fail("A card shard could not be loaded.");
      return;
    }
    if (stale()) return;
    const bodies = new Map();
    for (const type of types) {
      const shard = await fetchShard(type);
      if (!shard || typeof shard !== "object") continue;
      for (const [id, record] of Object.entries(shard)) {
        if (record && typeof record.card_body_md === "string") bodies.set(id, record.card_body_md);
      }
    }
    world.bodyOf = (id) => {
      if (!bodies.has(id)) throw new Error(`missing body ${id}`);
      return bodies.get(id);
    };
    const payload = packBundle(world, ctx.routeId, {
      depth: params.depth,
      maxTokens: params.maxTokens,
      includeInbound: params.inbound,
      rels: relFilter,
    });
    if (stale()) return;
    if (!payload || payload.status === "error") {
      fail((payload && payload.error) || "This bundle cannot be shown.");
      return;
    }
    const attribution = ctx.attribution || ATTRIBUTION;
    paint(container, ctx, payload, params, world, attribution);
  } catch (err) {
    fail(err && err.message ? err.message : "This bundle cannot be shown.");
  }
}

function worldFrom(catalog, graph) {
  const indexed = indexGraph(graph.edges, catalog.byId);
  return {
    catalog,
    outgoing: indexed.outgoing,
    incoming: indexed.incoming,
    resolveIdentifier,
    bodyOf(id) {
      throw new Error(`missing body ${id}`);
    },
  };
}

function paint(container, ctx, payload, params, world, attribution) {
  if (payload.status !== "ok") {
    container.replaceChildren(unresolvedView(payload));
    return;
  }
  const markdown = renderBundle(payload);
  const copied = copyAgentContext(markdown, attribution);
  const root = payload.root;
  const direct = world.outgoing.get(root.id) || [];
  const rels = [...new Set(direct.map((edge) => edge.rel))].sort();
  const activeRels = params.rels || rels;

  const head = el("div", { class: "insp-head" });
  const titles = el("div");
  const crumbs = el("div", { class: "crumbs" });
  crumbs.append(
    el("a", { href: "#/", text: "Explorer" }),
    " › ",
    el("a", { href: `#/e/${root.id}`, text: root.name }),
    " › Bundle",
  );
  titles.append(crumbs, el("h1", { text: `Bundle for ${root.name}` }));
  const actions = el("div", { class: "hide-narrow acts" });
  actions.append(el("a", { class: "btn", href: `#/graph/${root.id}`, text: "View graph" }));
  const copyBtn = el("button", { type: "button", class: "btn primary", text: "Copy Agent Context" });
  actions.append(copyBtn);
  head.append(titles, actions);

  const layout = el("div", { class: "insp" });
  layout.append(
    settings(ctx, root, params, rels, activeRels),
    meterColumn(payload, attribution),
    previewColumn(markdown, attribution),
  );

  const fallback = el("div", { class: "fallback panel", hidden: true });
  const note = el("p", { class: "hint", text: "Your browser blocked copying. Select the text below and copy it yourself." });
  const area = el("textarea", { readonly: "readonly" });
  area.value = copied;
  const selectBtn = el("button", { type: "button", class: "btn", text: "Select all" });
  selectBtn.addEventListener("click", () => {
    area.focus();
    area.select();
  });
  fallback.append(note, area, selectBtn);

  const bar = el("div", { class: "stickbar" });
  const stickyCopy = el("button", { type: "button", class: "btn primary", text: "Copy Agent Context" });
  bar.append(stickyCopy);

  container.replaceChildren(head, layout, fallback, bar);

  const buttons = [copyBtn, stickyCopy];
  const onCopy = () => doCopy(copied, buttons, fallback, area);
  copyBtn.addEventListener("click", onCopy);
  stickyCopy.addEventListener("click", onCopy);
}

function settings(ctx, root, params, rels, activeRels) {
  const aside = el("aside", { class: "panel pad", "aria-label": "Bundle settings" });
  const entry = el("div", { class: "ctl" });
  entry.append(el("span", { class: "lbl", text: "Entry" }));
  const line = el("div", { class: "gcenter" });
  line.append(el("b", { text: root.name }));
  const change = el("a", { class: "btn ghost", href: "#/bundle/", text: "Change" });
  line.append(change);
  entry.append(line, el("p", { class: "hint" }, [el("span", { class: "idc", text: root.id })]));

  const depthCtl = el("div", { class: "ctl" });
  const depthLabel = el("span", { class: "lbl" });
  depthLabel.append("Depth ", el("small", { text: "hops of references" }));
  const seg = el("div", { class: "seg", role: "group", "aria-label": "Depth" });
  for (const depth of [0, 1, 2]) {
    const button = el("button", {
      type: "button",
      text: String(depth),
      "aria-pressed": depth === params.depth ? "true" : "false",
    });
    button.addEventListener("click", () => ctx.setHash(hashFor(root.id, { ...params, depth, rels: params.rels })));
    seg.append(button);
  }
  depthCtl.append(depthLabel, seg);

  const budget = el("div", { class: "ctl" });
  const budgetLabel = el("span", { class: "lbl" });
  budgetLabel.append("Token budget ", el("small", { text: "est. tokens (chars/4)" }));
  const slider = el("input", {
    type: "range",
    min: "0",
    max: "1000",
    value: String(sliderFromTokens(params.maxTokens)),
    "aria-label": "Token budget",
  });
  const sliderStart = String(sliderFromTokens(params.maxTokens));
  const number = el("input", { type: "number", min: "200", max: "16000", value: String(params.maxTokens), "aria-label": "Max tokens" });
  const ends = el("div", { class: "legend" });
  ends.append(el("span", { text: "200" }), el("span", { text: "2,000 default" }), el("span", { text: "16,000" }));
  const reset = el("button", { type: "button", class: "btn ghost", text: "Reset" });
  reset.addEventListener("click", () => ctx.setHash(hashFor(root.id, { ...params, maxTokens: 2000 })));
  slider.addEventListener("change", () => {
    if (slider.value === sliderStart) return;
    const maxTokens = tokensFromSlider(Number(slider.value));
    ctx.setHash(hashFor(root.id, { ...params, maxTokens }));
  });
  number.addEventListener("change", () => {
    const maxTokens = clamp(number.value, 200, 16000);
    ctx.setHash(hashFor(root.id, { ...params, maxTokens }));
  });
  const numfield = el("div", { class: "numfield" });
  numfield.append(number, el("span", { class: "hint", text: "tokens" }), reset);
  budget.append(budgetLabel, slider, ends, numfield);

  const inboundCtl = el("div", { class: "ctl" });
  const inbound = el("label", { class: "switch" });
  const box = el("input", { type: "checkbox" });
  box.checked = params.inbound;
  box.addEventListener("change", () => ctx.setHash(hashFor(root.id, { ...params, inbound: box.checked })));
  inbound.append(box, "Include inbound references");
  inboundCtl.append(inbound, el("p", { class: "hint", text: "Adds up to 10 entries that point to this one." }));

  const relCtl = el("div", { class: "ctl" });
  const relLabel = el("span", { class: "lbl" });
  relLabel.append("Follow relations ", el("small", { text: `${activeRels.length} of ${rels.length}` }));
  relCtl.append(relLabel);
  for (const rel of rels) {
    const label = el("label", { class: "chk" });
    const input = el("input", { type: "checkbox" });
    input.checked = activeRels.includes(rel);
    input.addEventListener("change", () => {
      const next = new Set(activeRels);
      if (input.checked) next.add(rel);
      else next.delete(rel);
      const list = [...next].sort();
      const allOn = list.length === rels.length;
      ctx.setHash(hashFor(root.id, { ...params, rels: allOn ? null : list }));
    });
    label.append(input, rel);
    relCtl.append(label);
  }

  aside.append(entry, depthCtl, budget, inboundCtl, relCtl);
  return aside;
}

function meterColumn(payload, attribution) {
  const wrap = el("div");
  const stats = payload.stats;
  const section = el("section", { class: "panel pad" });
  const top = el("div", { class: "meter-top" });
  const big = el("div", { class: "big" });
  big.append(String(stats.estimated_tokens), el("small", { text: ` / ${stats.max_tokens.toLocaleString("en-US")}` }));
  const counts = el("div", { class: "unit" });
  counts.append(`${stats.full_count} full · ${stats.summary_count} summarized · ${stats.omitted_ids.length} omitted`);
  top.append(el("div", {}, [big, el("div", { class: "unit", text: "est. tokens (chars/4)" })]), counts);
  const meter = el("div", { class: "meter", role: "img", "aria-label": `${stats.estimated_tokens} of ${stats.max_tokens} estimated tokens` });
  if (stats.estimated_tokens >= stats.max_tokens) {
    meter.append(el("i", { class: "m-over" }));
  } else {
    const max = stats.max_tokens;
    const full = payload.dependencies.filter((dep) => dep.detail === "full").reduce((sum, dep) => sum + dep.tokens_est, 0);
    const summary = payload.dependencies.filter((dep) => dep.detail === "summary").reduce((sum, dep) => sum + dep.tokens_est, 0);
    meter.append(
      el("i", { class: `m-root ${widthClass(payload.root.tokens_est, max)}` }),
      el("i", { class: `m-full ${widthClass(full, max)}` }),
      el("i", { class: `m-sum ${widthClass(summary, max)}` }),
    );
  }
  const extra = estTokens(`\n\n${attribution}`);
  const caveat = el("p", {
    class: "hint",
    text: `Estimate only: characters ÷ 4, the same rule the CLI and MCP use. Your model's real token count will differ. Copying also adds ${extra} tokens of attribution.`,
  });
  section.append(top, meter, caveat);
  wrap.append(section);
  if (stats.summary_count > 0) {
    wrap.append(problem(`${stats.summary_count} references summarized to fit. They are listed as one-line stubs. The root card is never cut.`, false));
  }
  if (stats.budget_exceeded) {
    wrap.append(problem("The root card is larger than the budget. It is included in full anyway. Dependencies were left out.", false));
  }
  wrap.append(entryTable(payload));
  return wrap;
}

function entryTable(payload) {
  const section = el("section", { class: "panel" });
  const top = el("div", { class: "code-top" });
  const count = 1 + payload.dependencies.length;
  top.append(el("h2", { text: `Included entries · ${count}` }), el("span", { class: "hint", text: "Sorted by hop, tier, id" }));
  const table = el("table", { class: "deps" });
  const head = el("thead");
  const hr = el("tr");
  hr.append(el("th", { class: "num", text: "Hop" }), el("th", { text: "Entry" }), el("th", { class: "hide-m", text: "Tier" }), el("th", { text: "Detail" }), el("th", { class: "num", text: "Tokens" }));
  head.append(hr);
  const body = el("tbody");
  body.append(entryRow({
    hop: 0,
    name: payload.root.name,
    id: payload.root.id,
    type: payload.root.type,
    rels: [],
    tier: payload.root.tier,
    detail: "full",
    tokens: payload.root.card_tokens_est,
    root: true,
  }));
  for (const dep of payload.dependencies) {
    body.append(entryRow({
      hop: dep.hop,
      name: dep.name,
      id: dep.id,
      type: dep.type,
      rels: dep.rels,
      tier: dep.tier,
      detail: dep.detail,
      tokens: dep.detail === "full" ? dep.card_tokens_est : dep.tokens_est,
      root: false,
    }));
  }
  table.append(head, body);
  section.append(top, table);
  return section;
}

function entryRow(row) {
  const tr = el("tr", { class: row.root ? "root" : "" });
  const entry = el("td");
  entry.append(el("a", { href: `#/e/${row.id}`, text: row.name }));
  const via = row.root ? `${row.type} · root, always in full` : `${row.type} · via ${row.rels.join(", ")}`;
  entry.append(el("span", { class: "via", text: via }));
  const detail = row.detail === "full" ? "Full" : "Summary";
  tr.append(
    el("td", { class: "num", text: String(row.hop) }),
    entry,
    el("td", { class: "hide-m" }, [el("span", { class: "pill", text: `T${row.tier}` })]),
    el("td", {}, [el("span", { class: `pill ${row.detail === "full" ? "full" : "summary"}`, text: detail })]),
    el("td", { class: "num", text: String(row.tokens) }),
  );
  return tr;
}

function previewColumn(markdown, attribution) {
  const section = el("section", { class: "panel col3" });
  const top = el("div", { class: "code-top" });
  top.append(el("h2", { text: "What gets copied" }), el("span", { class: "pill", text: "Markdown" }));
  const pre = el("pre", { class: "code" });
  pre.textContent = markdown;
  const added = el("p", { class: "appended" });
  added.append(el("b", { text: "Added on copy: " }), attribution);
  section.append(top, pre, added);
  return section;
}

function emptyInspector() {
  const box = el("section", { class: "empty" });
  box.append(
    el("h1", { text: "Pick an entry to bundle" }),
    el("p", { text: "Search for a rule, spell, item, or monster, then open its bundle." }),
    el("p", {}, [el("a", { href: "#/", text: "Back to Explorer" })]),
  );
  return box;
}

function unresolvedView(payload) {
  const box = el("section", { class: "empty" });
  if (payload.status === "ambiguous") {
    box.append(el("h1", { text: `\u201c${payload.query}\u201d matches ${payload.candidates.length} entries` }));
    box.append(el("p", { text: "Pick the one you mean. Nothing is chosen automatically." }));
    const list = el("ul", { class: "results" });
    for (const cand of payload.candidates) {
      const link = el("a", { class: "res", href: `#/bundle/${cand.id}` });
      const family = familyOf(cand.type);
      link.append(el("span", { class: `tchip fam-${family.id}`, text: typeLabel(cand.type) }), el("h3", { text: cand.name }), el("p", { text: cand.summary || "" }));
      list.append(el("li", {}, [link]));
    }
    box.append(list);
    return box;
  }
  box.append(el("h1", { text: "No entry with that id" }), el("p", { text: "Check the spelling, or search by name." }));
  return box;
}

function problem(text, hard) {
  const box = el("div", { class: hard ? "notice err" : "notice warn", role: hard ? "alert" : "status" });
  box.append(el("span", { class: "ic", "aria-hidden": "true" }, [el("span", { text: hard ? "\u00d7" : "!" })]));
  const copy = el("div");
  if (hard) {
    copy.append(el("b", { text: "This bundle cannot be shown." }));
    copy.append(el("div", { class: "detail", text }));
  } else {
    copy.append(el("b", { text }));
  }
  box.append(copy);
  return box;
}

let copyTimer = 0;

async function doCopy(text, buttons, fallback, area) {
  try {
    await navigator.clipboard.writeText(text);
    fallback.hidden = true;
    for (const button of buttons) {
      button.textContent = "\u2713 Copied";
      button.classList.add("done");
    }
    window.clearTimeout(copyTimer);
    copyTimer = window.setTimeout(() => {
      for (const button of buttons) {
        button.textContent = "Copy Agent Context";
        button.classList.remove("done");
      }
    }, 2000);
  } catch {
    fallback.hidden = false;
    area.value = text;
    area.focus();
    area.select();
  }
}

export function readInspectorQuery(hash) {
  const raw = String(hash || "");
  const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
  const params = new URLSearchParams(query);
  return {
    depth: clamp(params.get("depth") ?? "1", 0, 2),
    maxTokens: clamp(params.get("max") ?? "2000", 200, 16000),
    inbound: params.get("inbound") === "1",
    rels: params.has("rels") ? params.get("rels").split(",").filter(Boolean) : null,
  };
}

export function hashFor(id, params) {
  const search = new URLSearchParams();
  search.set("depth", String(params.depth));
  search.set("max", String(params.maxTokens));
  if (params.inbound) search.set("inbound", "1");
  if (params.rels != null) search.set("rels", params.rels.join(","));
  return `#/bundle/${id}?${search}`;
}

function tokensFromSlider(value) {
  const t = clamp(value, 0, 1000) / 1000;
  return clamp(Math.round(Math.exp(LOG_MIN + t * (LOG_MAX - LOG_MIN))), 200, 16000);
}

function sliderFromTokens(tokens) {
  const t = (Math.log(clamp(tokens, 200, 16000)) - LOG_MIN) / (LOG_MAX - LOG_MIN);
  return Math.round(t * 1000);
}

function widthClass(part, max) {
  const steps = Math.max(0, Math.min(20, Math.round((part / max) * 20)));
  return `w${steps}`;
}

function clamp(value, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return min;
  return Math.max(min, Math.min(max, Math.trunc(n)));
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else if (key === "hidden") node.hidden = Boolean(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) {
    if (child == null || child === "") continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
