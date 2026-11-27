import { ATTRIBUTION, attributionPieces } from "./lib/attribution.js";
import { bootViewModel, trustedAttribution } from "./lib/boot.js";
import { parseCardRefs } from "./lib/cardmeta.js";
import {
  FAMILIES,
  facetKeyLabel,
  facetValueLabel,
  familyOf,
  orderFacetKeys,
  resourceTypesFromManifest,
  typeLabel,
  typePlural,
  valuesForFacet,
} from "./lib/facets.js";
import { cardAssetUrl } from "./lib/paths.js";
import { buildCatalog, openTarget, resolveIdentifier } from "./lib/resolve.js";
import { readHashState } from "./lib/router.js";
import { matchRanges, searchEntities } from "./lib/search.js";

const PAGE = 10;
const PAGE_STEP = 25;
const COLLAPSE_AT = 12;
const SVG_NS = "http://www.w3.org/2000/svg";

const quoteEl = document.getElementById("attribution");
if (quoteEl) placeAttribution(quoteEl);

const mainEl = document.getElementById("main");
const narrow = window.matchMedia("(max-width: 759px)");

start().catch(() => {
  renderHardError(["The explorer stopped before any entities were shown."]);
});

async function start() {
  let manifest;
  let index;
  try {
    const [manifestRes, indexRes] = await Promise.all([
      fetch(new URL("../dist/manifest.json", import.meta.url)),
      fetch(new URL("../dist/web/index.json", import.meta.url)),
    ]);
    if (!manifestRes.ok || !indexRes.ok) {
      const errors = [];
      if (!manifestRes.ok) errors.push(`manifest.json HTTP ${manifestRes.status}`);
      if (!indexRes.ok) errors.push(`web/index.json HTTP ${indexRes.status}`);
      renderHardError(errors);
      return;
    }
    manifest = await manifestRes.json();
    index = await indexRes.json();
  } catch {
    renderHardError(["The manifest and index could not be loaded."]);
    return;
  }

  const decision = bootViewModel(manifest, index);
  if (!decision.ok) {
    renderHardError(decision.errors);
    return;
  }
  const official = trustedAttribution(manifest);
  if (quoteEl && official) placeAttribution(quoteEl, official);

  const line = document.getElementById("dataset-line");
  if (line) {
    const edges = manifest.counts && manifest.counts.edges;
    line.textContent = `Dataset ${manifest.dataset_id} · schema ${manifest.schema_version} · ${group(decision.entities.length)} entries · ${group(edges)} cross-references`;
  }
  mount(manifest, buildCatalog(decision.entities));
}

/**
 * @param {object} manifest
 * @param {ReturnType<typeof buildCatalog>} catalog
 */
function mount(manifest, catalog) {
  const types = resourceTypesFromManifest(manifest);
  const typeByName = new Map(types.map((row) => [row.type, row]));
  const allowedFacetKeys = types.flatMap((row) => row.facetKeys);
  const famOpen = new Set();
  const expanded = new Set();
  let moreOpen = false;
  let seenType = "";
  let visibleCount = PAGE;
  let pageKey = "";
  let cardState = null;
  let cardGen = 0;
  let viewGen = 0;

  mainEl.replaceChildren();
  const hero = el("div", { class: "wrap hero" });
  hero.append(
    el("p", { class: "eyebrow", text: "5E compatible · SRD 5.2 · runs in your browser" }),
    el("h1", { text: "The 5e 2024 SRD, resolved for agents." }),
    el("p", {
      text: "Search the 5e 2024 SRD, follow cross-references from card to card, then copy a token-budgeted bundle for your agent.",
    }),
    statRow(manifest),
  );
  const searchWrap = el("div", { class: "wrap search-wrap" });
  const field = el("div", { class: "field", role: "search" });
  field.append(searchIcon());
  const input = el("input", {
    id: "q",
    type: "search",
    autocomplete: "off",
    autocapitalize: "off",
    spellcheck: "false",
  });
  field.append(el("label", { class: "sr-only", for: "q", text: "Search the SRD" }), input, el("span", { class: "kbd", text: "/" }));
  const typo = el("p", { class: "typo-note", hidden: true });
  searchWrap.append(field, typo);

  const explorer = el("div", { class: "wrap explorer" });
  const side = el("details", { class: "side" });
  side.open = true;
  const resultsCol = el("section", { class: "results-col", "aria-label": "Results" });
  const cardSlot = el("div", { class: "card-slot" });
  explorer.append(side, resultsCol, cardSlot);
  mainEl.append(hero, searchWrap, explorer);

  function readState() {
    return readHashState(location.hash || "#/", allowedFacetKeys);
  }

  input.addEventListener("input", () => {
    const state = readState();
    state.q = input.value;
    const hash = hashFor(state);
    if ((location.hash || "#/") === hash) return;
    const url = new URL(location.href);
    url.hash = hash;
    history.replaceState(null, "", url);
    render();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const opened = openTarget(catalog, input.value);
    if (opened.action === "ambiguous" && readState().route.view === "bundle") {
      location.hash = `#/bundle/${encodeURIComponent(input.value.trim())}`;
      return;
    }
    if (opened.action !== "open") return;
    const state = readState();
    if (state.route.view === "bundle") {
      location.hash = `#/bundle/${opened.id}`;
      return;
    }
    state.q = input.value;
    state.route = { name: "card", id: opened.id, unknown: false };
    location.hash = hashFor(state);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    event.preventDefault();
    input.focus();
  });
  narrow.addEventListener("change", syncPlaceholder);
  window.addEventListener("hashchange", render);
  render();

  async function showBundle(state, gen) {
    const { renderInspector } = await import("./lib/inspector-view.js");
    if (gen !== viewGen) return;
    const attribution = trustedAttribution(manifest) || ATTRIBUTION;
    await renderInspector(resultsCol, {
      catalog,
      manifest,
      routeId: state.route.id,
      hash: location.hash,
      attribution,
      setHash(next) {
        if (location.hash !== next) location.hash = next;
      },
    });
  }

  function render() {
    const gen = ++viewGen;
    const state = readState();
    syncNav(state.route);
    syncPlaceholder();
    const bundleRoute = state.route.name === "later" && state.route.view === "bundle";
    mainEl.classList.toggle("mode-card", state.route.name === "card");
    mainEl.classList.toggle("mode-bundle", bundleRoute);
    const openEntity = state.route.id ? catalog.byId.get(state.route.id) : null;
    document.title = openEntity
      ? `${bundleRoute ? "Bundle for " : ""}${openEntity.name} · 5e 2024 SRD Agent Pack`
      : "5e 2024 SRD Agent Pack";
    if (input.value !== state.q) input.value = state.q;
    const showTypo = /['"\u2018\u2019\u201C\u201D]/.test(state.q);
    typo.hidden = !showTypo;
    typo.textContent = showTypo
      ? "Matching \u201cDragon\u2019s\u201d and \u201cDragon's\u201d alike. Apostrophes and quotes are normalized."
      : "";
    explorer.classList.toggle("solo", state.route.name === "later");
    if (bundleRoute) {
      explorer.classList.remove("has-card");
      side.hidden = true;
      cardSlot.hidden = true;
      cardSlot.replaceChildren();
      void showBundle(state, gen);
      return;
    }
    if (state.route.name === "later") {
      explorer.classList.remove("has-card");
      side.hidden = true;
      cardSlot.hidden = true;
      cardSlot.replaceChildren();
      resultsCol.replaceChildren(laterView(state.route));
      return;
    }
    side.hidden = false;
    cardSlot.hidden = false;
    if (state.type && state.type !== seenType) famOpen.add(familyOf(state.type).id);
    seenType = state.type;
    const found = collect(state);
    paintSide(state, found.length);
    paintResults(state, found);
    paintCard(state);
  }

  function collect(state) {
    const resolved = state.q.trim() ? resolveIdentifier(catalog, state.q) : null;
    if (resolved && resolved.status === "ambiguous") {
      return resolved.candidates
        .map((candidate) => catalog.byId.get(candidate.id))
        .filter(Boolean);
    }
    const sort = state.sort || (state.q.trim() ? "rank" : "name");
    return searchEntities(catalog, state.q, { type: state.type, facets: state.facets, sort });
  }

  function paintSide(state, count) {
    const active = (state.type ? 1 : 0) + Object.values(state.facets).reduce((sum, values) => sum + values.length, 0);
    const summary = el("summary", { class: "filters-toggle" });
    summary.append("Filters");
    if (active) summary.append(el("span", { class: "pill", text: `${active} active` }));
    const body = el("div", { class: "side-body" });
    body.append(typeFilters(state), ...facetFilters(state));
    const apply = el("div", { class: "fs apply-row" });
    const show = el("button", { type: "button", class: "btn primary grow", text: `Show ${group(count)} results` });
    show.addEventListener("click", () => {
      side.open = false;
    });
    const clear = el("button", { type: "button", class: "btn", text: "Clear all" });
    clear.addEventListener("click", () => {
      const next = readState();
      next.type = "";
      next.facets = {};
      location.hash = hashFor(next);
    });
    apply.append(show, clear);
    body.append(apply);
    side.replaceChildren(summary, body);
  }

  function typeFilters(state) {
    const block = el("div", { class: "fs" });
    const heading = el("h2", { text: "Type" });
    const all = el("button", { type: "button", text: "All types" });
    all.addEventListener("click", () => {
      const next = readState();
      next.type = "";
      next.facets = {};
      location.hash = hashFor(next);
    });
    heading.append(all);
    block.append(heading);
    for (const family of FAMILIES) {
      const rows = family.types.map((type) => typeByName.get(type)).filter(Boolean);
      const total = rows.reduce((sum, row) => sum + row.count, 0);
      const details = el("details", { class: "fam" });
      if (famOpen.has(family.id)) details.open = true;
      details.addEventListener("toggle", () => {
        if (details.open) famOpen.add(family.id);
        else famOpen.delete(family.id);
      });
      const sum = el("summary");
      sum.append(el("span", { class: "car" }), glyph(family.id), family.label, el("span", { class: "n", text: group(total) }));
      const list = el("ul");
      for (const row of rows) {
        const link = el("a", {
          href: hashFor({ ...state, type: row.type, facets: {}, route: { name: "explorer", unknown: false } }),
        });
        if (state.type === row.type) link.setAttribute("aria-current", "true");
        link.append(el("span", { text: typePlural(row.type) }), el("span", { class: "n", text: group(row.count) }));
        list.append(el("li", {}, [link]));
      }
      details.append(sum, list);
      block.append(details);
    }
    return block;
  }

  function facetFilters(state) {
    if (!state.type) {
      return [el("div", { class: "fs" }, [el("p", { class: "hint", text: "Choose a type to use its facet filters." })])];
    }
    const spec = typeByName.get(state.type);
    const keys = orderFacetKeys(spec ? spec.facetKeys : []);
    if (!keys.length) {
      return [el("div", { class: "fs" }, [el("p", { class: "hint", text: "This type has no facet filters." })])];
    }
    const primary = keys.slice(0, 3);
    const moreKeys = keys.slice(3);
    const blocks = primary.map((key, index) => facetBlock(state, key, index === 0));
    if (moreKeys.length) blocks.push(moreBlock(state, moreKeys));
    return blocks;
  }

  function facetBlock(state, key, withHint) {
    const block = el("div", { class: "fs" });
    const heading = el("h2", { text: facetKeyLabel(key) });
    if ((state.facets[key] || []).length) {
      const clear = el("button", { type: "button", text: "Clear" });
      clear.addEventListener("click", () => setFacet(state, key, []));
      heading.append(clear);
    }
    block.append(heading, facetOptions(state, key));
    if (withHint) {
      block.append(el("p", { class: "hint", text: "Pick several to match any of them. Different filters combine with AND." }));
    }
    return block;
  }

  function moreBlock(state, keys) {
    const block = el("div", { class: "fs" });
    block.append(el("h2", { text: "More filters" }));
    const details = el("details");
    const selected = keys.some((key) => (state.facets[key] || []).length);
    if (moreOpen || selected) details.open = true;
    details.addEventListener("toggle", () => {
      moreOpen = details.open;
    });
    details.append(el("summary", { class: "hint", text: keys.map(facetKeyLabel).join(" · ") }));
    for (const key of keys) details.append(facetBlock(state, key, false));
    block.append(details);
    return block;
  }

  function facetOptions(state, key) {
    const typeEntities = catalog.entities.filter((entity) => entity.type === state.type);
    const universe = valuesForFacet(typeEntities, key);
    const counted = valuesForFacet(
      searchEntities(catalog, state.q, {
        type: state.type,
        facets: facetsExcept(state.facets, key),
        sort: "name",
      }),
      key,
    );
    const counts = new Map(counted.map((item) => [item.value, item.count]));
    const selected = new Set(state.facets[key] || []);
    let rows = universe.map((item) => ({ ...item, count: counts.get(item.value) || 0 }));
    const box = el("div", { class: "opts", role: "group", "aria-label": facetKeyLabel(key) });
    const collapsed = rows.length > COLLAPSE_AT && !expanded.has(key);
    if (collapsed) {
      const preview = rows.slice(0, 8);
      const seen = new Set(preview.map((item) => item.value));
      for (const item of rows) {
        if (selected.has(item.value) && !seen.has(item.value)) preview.push(item);
      }
      rows = preview;
    }
    for (const item of rows) {
      const pressed = selected.has(item.value);
      const button = el("button", {
        type: "button",
        class: item.count === 0 && !pressed ? "opt zero" : "opt",
        "aria-pressed": pressed ? "true" : "false",
      });
      button.append(facetValueLabel(key, item.value), el("span", { class: "n", text: group(item.count) }));
      button.addEventListener("click", () => {
        const next = new Set(state.facets[key] || []);
        if (next.has(item.value)) next.delete(item.value);
        else next.add(item.value);
        setFacet(state, key, [...next]);
      });
      box.append(button);
    }
    if (collapsed) {
      const all = el("button", { type: "button", class: "btn", text: "Show all" });
      all.addEventListener("click", () => {
        expanded.add(key);
        render();
      });
      box.append(all);
    }
    return box;
  }

  function setFacet(state, key, values) {
    const next = readState();
    next.facets = { ...next.facets };
    if (values.length) next.facets[key] = values;
    else delete next.facets[key];
    location.hash = hashFor(next);
  }

  function paintResults(state, found) {
    const key = JSON.stringify([state.q, state.type, state.facets, state.sort, state.route.name === "card" ? "" : "list"]);
    if (key !== pageKey) {
      pageKey = key;
      visibleCount = PAGE;
    }
    resultsCol.replaceChildren();
    const bare = !state.q.trim() && !state.type && !Object.keys(state.facets).length;
    const resolved = state.q.trim() ? resolveIdentifier(catalog, state.q) : null;
    if (resolved && resolved.status === "ambiguous") {
      resultsCol.append(ambiguousNotice(resolved));
    }
    if (state.route.name !== "card" && bare) {
      resultsCol.append(startState());
      return;
    }
    if (!found.length) {
      resultsCol.append(noResults(state));
      return;
    }
    resultsCol.append(resultBar(state, found.length));
    const slice = found.slice(0, visibleCount);
    const list = el("ul", { class: "results" });
    for (const entity of slice) list.append(resultRow(entity, state));
    resultsCol.append(list);
    if (found.length > slice.length) {
      const more = el("div", { class: "more" });
      const button = el("button", { type: "button", class: "btn" });
      const next = Math.min(PAGE_STEP, found.length - slice.length);
      button.append(`Show ${next} more`, el("span", { class: "quiet", text: ` (${slice.length} of ${found.length})` }));
      button.addEventListener("click", () => {
        visibleCount += PAGE_STEP;
        render();
      });
      more.append(button);
      resultsCol.append(more);
    }
  }

  function startState() {
    const box = el("div", { class: "empty" });
    box.append(
      el("h3", { text: "Start with a word or an id" }),
      el("p", { text: `Search names, ids and summaries across all ${group(catalog.entities.length)} entries.` }),
    );
    const opts = el("div", { class: "opts" });
    for (const sample of ["Dragon\u2019s", "fireball", "grappled", "equipment:longsword"]) {
      const button = el("button", { type: "button", class: "opt", text: sample });
      button.addEventListener("click", () => {
        const next = readState();
        next.q = sample === "Dragon\u2019s" ? "Dragon's" : sample;
        next.route = { name: "explorer", unknown: false };
        location.hash = hashFor(next);
      });
      opts.append(button);
    }
    box.append(opts);
    return box;
  }

  function noResults(state) {
    const box = el("div", { class: "empty" });
    const title = state.q.trim() ? `No entries match \u201c${state.q.trim()}\u201d` : "No entries match these filters";
    box.append(el("h3", { text: title }), el("p", { text: `Nothing in ${scopePhrase(state)}. Try fewer words or remove a filter.` }));
    const row = el("div", { class: "acts" });
    if (state.type || Object.keys(state.facets).length) {
      const remove = el("button", { type: "button", class: "btn", text: "Remove filters" });
      remove.addEventListener("click", () => {
        const next = readState();
        next.type = "";
        next.facets = {};
        location.hash = hashFor(next);
      });
      row.append(remove);
    }
    if (state.type) {
      const all = el("button", { type: "button", class: "btn ghost", text: "Search all types" });
      all.addEventListener("click", () => {
        const next = readState();
        next.type = "";
        next.facets = {};
        location.hash = hashFor(next);
      });
      row.append(all);
    }
    box.append(row);
    return box;
  }

  function resultBar(state, count) {
    const bar = el("div", { class: "bar", role: "status", "aria-live": "polite" });
    bar.append(el("span", { class: "count", text: `${group(count)} ${count === 1 ? "result" : "results"}` }));
    if (state.type) bar.append(chip(typeLabel(state.type), "Remove type filter", () => {
      const next = readState();
      next.type = "";
      next.facets = {};
      location.hash = hashFor(next);
    }));
    for (const [key, values] of Object.entries(state.facets)) {
      for (const value of values) {
        const label = key === "level" ? `Level ${facetValueLabel(key, value)}` : `${facetKeyLabel(key)}: ${facetValueLabel(key, value)}`;
        bar.append(chip(label, `Remove ${label}`, () => setFacet(state, key, values.filter((item) => item !== value))));
      }
    }
    const sort = el("span", { class: "sort" });
    const label = el("label", { for: "sort", text: "Sort" });
    const select = el("select", { id: "sort", class: "sel" });
    const effective = state.sort || (state.q.trim() ? "rank" : "name");
    select.append(option("name", "Name A\u2013Z", effective === "name"), option("rank", "Best match", effective === "rank"));
    select.addEventListener("change", () => {
      const next = readState();
      next.sort = select.value;
      location.hash = hashFor(next);
    });
    sort.append(label, select);
    bar.append(sort);
    return bar;
  }

  function resultRow(entity, state) {
    const href = hashFor({ ...state, route: { name: "card", id: entity.id, unknown: false } });
    const link = el("a", { class: "res", href });
    if (state.route.name === "card" && state.route.id === entity.id) link.setAttribute("aria-current", "true");
    const family = familyOf(entity.type);
    const top = el("div", { class: "top" });
    top.append(chipSpan(family, typeLabel(entity.type)), el("span", { class: "idc", text: entity.id }));
    const tok = el("span", { class: "tok", title: "est. tokens (chars/4)", text: `~${entity.card_tokens_est || 0} tok` });
    top.append(tok);
    const name = el("h3");
    appendHighlighted(name, entity.name, state.q);
    const summary = el("p");
    appendHighlighted(summary, entity.summary || "", state.q);
    link.append(top, name, summary);
    return el("li", {}, [link]);
  }

  function paintCard(state) {
    cardSlot.replaceChildren();
    if (state.route.name !== "card") {
      explorer.classList.remove("has-card");
      return;
    }
    explorer.classList.add("has-card");
    const id = state.route.id;
    const entity = catalog.byId.get(id);
    cardSlot.append(backLink(state));
    if (!entity) {
      cardSlot.append(notFound(id));
      return;
    }
    if (!cardState || cardState.id !== id) {
      cardState = { id, status: "loading" };
      cardSlot.append(skeleton());
      void loadCard(id);
      return;
    }
    if (cardState.status === "loading") {
      cardSlot.append(skeleton());
      return;
    }
    if (cardState.status === "error") {
      cardSlot.append(cardError(() => {
        cardGen += 1;
        cardState = null;
        paintCard(readState());
      }));
      return;
    }
    cardSlot.append(cardArticle(entity, cardState));
  }

  async function loadCard(id) {
    const gen = ++cardGen;
    const entity = catalog.byId.get(id);
    const url = entity ? cardAssetUrl(import.meta.url, entity.type, entity.slug) : null;
    try {
      if (!url) throw new Error("path");
      const res = await fetch(url);
      if (!res.ok) throw new Error("http");
      const markdown = await res.text();
      const { renderCardMarkdown } = await import("./lib/markdown.js");
      if (gen !== cardGen) return;
      const rendered = renderCardMarkdown(markdown, (linkId) => {
        const target = catalog.byId.get(linkId);
        if (!target) return null;
        const summary = target.summary ? ` \u2014 ${target.summary}` : "";
        return { family: familyOf(target.type).id, title: `${target.name}${summary}` };
      });
      if (!rendered.ok) throw new Error("unsafe");
      cardState = { id, status: "ready", html: rendered.html, refs: parseCardRefs(markdown) };
    } catch {
      if (gen !== cardGen) return;
      cardState = { id, status: "error" };
    }
    const current = readState();
    if (current.route.name === "card" && current.route.id === id) paintCard(current);
  }

  function cardArticle(entity, loaded) {
    const family = familyOf(entity.type);
    const article = el("article", { class: "cardpane", "aria-labelledby": "card-h" });
    const header = el("header");
    const row = el("div", { class: "row" });
    row.append(chipSpan(family, typeLabel(entity.type)), el("span", { class: "idc", text: entity.id }));
    const acts = el("div", { class: "acts" });
    acts.append(
      el("a", { class: "btn primary", href: `#/bundle/${entity.id}`, text: "Open bundle" }),
      el("a", { class: "btn", href: `#/graph/${entity.id}`, text: "View graph" }),
    );
    const copy = el("button", { type: "button", class: "btn ghost", text: "Copy ID" });
    copy.addEventListener("click", () => copyId(entity.id));
    acts.append(copy);
    header.append(row, el("h2", { id: "card-h", text: entity.name }), el("p", { class: "sum", text: entity.summary || "" }), acts);
    const body = el("div", { class: "body" });
    const prose = el("div", { class: "prose" });
    prose.innerHTML = loaded.html;
    body.append(prose);
    const refs = (loaded.refs || []).map((id) => catalog.byId.get(id)).filter(Boolean);
    if (refs.length) {
      body.append(el("div", { class: "sec-h", text: `References \u00b7 ${refs.length}` }));
      const wrap = el("div", { class: "refs" });
      for (const ref of refs) wrap.append(refCard(ref));
      body.append(wrap);
    }
    const inbound = entity.in_deg || 0;
    if (inbound > 0) {
      body.append(el("div", { class: "sec-h", text: `Referenced by \u00b7 ${inbound}` }));
      const note = el("p", { class: "ref-note" });
      note.append(`${countPhrase(inbound, "entry points", "entries point")} to ${entity.name}. `);
      note.append(el("a", { href: `#/bundle/${entity.id}`, text: "See them in the bundle" }));
      note.append(" (turn on inbound) or ");
      note.append(el("a", { href: `#/graph/${entity.id}`, text: "in the graph" }));
      note.append(".");
      body.append(note);
    }
    const foot = el("footer", { class: "card-foot" });
    const path = el("span");
    path.append("Card ", el("code", { text: entity.card_path || "" }));
    const tier = el("span", { text: `Tier ${entity.tier}`, title: tierTitle(entity.tier) });
    foot.append(path, el("span", { text: `~${entity.card_tokens_est || 0} est. tokens (chars/4)` }), tier);
    article.append(header, body, foot);
    return article;
  }

  function backLink(state) {
    const listState = { ...state, route: { name: "explorer", unknown: false } };
    const count = searchEntities(catalog, state.q, { type: state.type, facets: state.facets }).length;
    return el("a", { class: "back-link btn ghost", href: hashFor(listState), text: `\u2190 Back to ${group(count)} results` });
  }

  function syncPlaceholder() {
    const count = group(catalog.entities.length);
    input.placeholder = narrow.matches ? `Search ${count} entries` : `Search ${count} entries: name, id or summary`;
  }
}

function laterView(route) {
  const box = el("section", { class: "later" });
  const title = route.view === "graph" ? "Reference graph" : "Bundle inspector";
  const when = route.view === "graph" ? "a later milestone" : "the next milestone";
  box.append(el("h1", { text: title }));
  box.append(
    noticeEl(
      "info",
      `${title} is not in this build.`,
      `It ships in ${when}. Cards and the explorer are ready now.`,
    ),
  );
  const acts = el("div", { class: "acts" });
  if (route.id) acts.append(el("a", { class: "btn", href: `#/e/${route.id}`, text: "Open card" }));
  acts.append(el("a", { class: "btn ghost", href: "#/", text: "Back to Explorer" }));
  box.append(acts);
  return box;
}

function ambiguousNotice(resolved) {
  return noticeEl(
    "warn",
    `\u201c${resolved.query}\u201d matches ${resolved.candidates.length} entries.`,
    "Pick the one you mean. The address will use its full id.",
  );
}

function notFound(id) {
  const box = el("div", { class: "empty" });
  box.append(
    noticeEl("info", "No entry with that id.", "Check the spelling, or search by name."),
    el("p", { class: "hint", text: id }),
  );
  return box;
}

function cardError(onRetry) {
  const box = el("div", {});
  box.append(noticeEl("err", "Couldn\u2019t load this card.", "The file may be missing or blocked."));
  const again = el("button", { type: "button", class: "btn", text: "Try again" });
  again.addEventListener("click", onRetry);
  box.append(again);
  return box;
}

function skeleton() {
  const box = el("div", { class: "cardpane" });
  const body = el("div", { class: "body" });
  body.append(el("div", { class: "skel title" }), el("div", { class: "skel" }), el("div", { class: "skel short" }), el("div", { class: "skel" }));
  box.append(body);
  return box;
}

function refCard(entity) {
  const family = familyOf(entity.type);
  const link = el("a", { class: "refcard", href: `#/e/${entity.id}`, title: entity.summary || "" });
  link.append(chipSpan(family, typeLabel(entity.type)), el("b", { text: entity.name }), el("span", { class: "idc", text: entity.id }));
  return link;
}

function chipSpan(family, label) {
  const chip = el("span", { class: `tchip fam-${family.id}` });
  chip.append(glyph(family.id), label);
  return chip;
}

function glyph(familyId) {
  return el("i", { class: `gl ${familyId}`, "aria-hidden": "true" });
}

function chip(label, aria, onClick) {
  const node = el("span", { class: "removable" });
  node.append(label);
  const button = el("button", { type: "button", text: "\u00d7", "aria-label": aria });
  button.addEventListener("click", onClick);
  node.append(button);
  return node;
}

function noticeEl(kind, lead, rest) {
  const box = el("div", { class: `notice ${kind}`, role: kind === "err" ? "alert" : "status" });
  const mark = kind === "err" ? "\u00d7" : kind === "warn" ? "!" : "i";
  box.append(el("span", { class: "ic", "aria-hidden": "true" }, [el("span", { text: mark })]));
  const copy = el("div");
  copy.append(el("b", { text: lead }));
  if (rest) copy.append(el("div", { class: "detail", text: rest }));
  box.append(copy);
  return box;
}

function scopePhrase(state) {
  const bits = [];
  if (state.type) bits.push(typePlural(state.type));
  for (const [key, values] of Object.entries(state.facets)) {
    bits.push(`${facetKeyLabel(key).toLowerCase()} ${values.map((value) => facetValueLabel(key, value)).join(" or ")}`);
  }
  return bits.length ? bits.join(", ") : "the catalog";
}

function facetsExcept(facets, key) {
  const next = { ...facets };
  delete next[key];
  return next;
}

function option(value, label, selected) {
  const node = el("option", { value, text: label });
  if (selected) node.selected = true;
  return node;
}

function appendHighlighted(parent, text, query) {
  const source = String(text ?? "");
  const ranges = matchRanges(source, query);
  if (!ranges.length) {
    parent.append(source);
    return;
  }
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start > cursor) parent.append(source.slice(cursor, start));
    parent.append(el("mark", { text: source.slice(start, end) }));
    cursor = end;
  }
  if (cursor < source.length) parent.append(source.slice(cursor));
}

function statRow(manifest) {
  const row = el("div", { class: "stats" });
  const entries = manifest.counts && manifest.counts.entities;
  const edges = manifest.counts && manifest.counts.edges;
  const types = Array.isArray(manifest.resource_types) ? manifest.resource_types.length : 0;
  for (const [value, label] of [
    [group(entries), "entries"],
    [group(types), "types"],
    [group(edges), "cross-references"],
  ]) {
    const span = el("span");
    span.append(el("b", { text: value }), ` ${label}`);
    row.append(span);
  }
  return row;
}

function hashFor(state) {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.type) params.set("type", state.type);
  if (state.sort) params.set("sort", state.sort);
  for (const key of Object.keys(state.facets || {}).sort()) {
    for (const value of state.facets[key]) params.append(key, value);
  }
  const qs = params.toString();
  let path = "/";
  if (state.route && state.route.name === "card") path = `/e/${state.route.id}`;
  else if (state.route && state.route.name === "later") {
    path = `/${state.route.view}/${state.route.id || ""}`;
  }
  return `#${path}${qs ? `?${qs}` : ""}`;
}

function syncNav(route) {
  for (const link of document.querySelectorAll(".nav a")) {
    const view = link.getAttribute("data-view");
    const on = view === "explorer" ? route.name !== "later" : route.name === "later" && route.view === view;
    if (on) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function placeAttribution(node, text = ATTRIBUTION) {
  node.replaceChildren();
  for (const piece of attributionPieces(text)) {
    if (piece.type === "text") {
      node.append(piece.value);
      continue;
    }
    const link = document.createElement("a");
    link.href = piece.value;
    const parts = piece.value.split("/");
    parts.forEach((part, index) => {
      if (index) link.append("/");
      link.append(part);
      if (index > 1 && index < parts.length - 1) link.append(document.createElement("wbr"));
    });
    node.append(link);
  }
}

function renderHardError(errors) {
  const section = el("section", { class: "wrap later" });
  section.append(
    noticeEl(
      "err",
      "This data isn\u2019t the SRD build I expect, so I stopped.",
      `Expected schema_version \u201c1\u201d, dataset_id \u201csrd-2024-5.2\u201d, 2,029 entries. Found ${errors.join("; ")}.`,
    ),
  );
  const follow = el("p", { class: "hard-copy" });
  follow.append("No cards are shown and nothing can be copied until the data matches. ");
  follow.append(el("a", { href: "https://github.com/pshouse/agent-srd-2024", text: "Report this on GitHub" }));
  follow.append(".");
  section.append(follow);
  mainEl.replaceChildren(section);
  document.title = "Dataset check failed · 5e 2024 SRD Agent Pack";
}

async function copyId(id) {
  try {
    await navigator.clipboard.writeText(id);
    showToast("Copied id", id, false);
  } catch {
    showToast("Couldn\u2019t copy", "Select the id and copy it yourself.", true);
  }
}

let toastTimer = 0;
function showToast(title, detail, bad) {
  const toast = document.getElementById("toast");
  toast.classList.toggle("bad", bad);
  toast.replaceChildren(el("span", { class: "tick", text: bad ? "!" : "\u2713" }), el("div", {}, [title, el("small", { text: detail })]));
  toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 4000);
}

function countPhrase(count, singular, plural) {
  const words = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
  const lead = count <= 10 ? words[count] : group(count);
  return `${lead} ${count === 1 ? singular : plural}`;
}

function tierTitle(tier) {
  if (tier === 1) return "Tier 1 = core rule, always included in full in bundles.";
  if (tier === 2) return "Tier 2 = character or gameplay entry.";
  return "Tier 3 = large container, summarized in bundles when over 400 est. tokens.";
}

function group(value) {
  return Number(value || 0).toLocaleString("en-US");
}

function searchIcon() {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("width", "20");
  svg.setAttribute("height", "20");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");
  const circle = document.createElementNS(SVG_NS, "circle");
  circle.setAttribute("class", "search-icon");
  circle.setAttribute("cx", "8.5");
  circle.setAttribute("cy", "8.5");
  circle.setAttribute("r", "5.5");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("class", "search-icon");
  path.setAttribute("d", "m13 13 4.5 4.5");
  path.setAttribute("stroke-linecap", "round");
  svg.append(circle, path);
  return svg;
}

/**
 * @param {string} tag
 * @param {Record<string, unknown>} [attrs]
 * @param {Array<Node | string | null | undefined>} [children]
 */
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else if (key === "hidden") node.hidden = true;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
