import { validateGraph } from "./bundle.js";
import { indexAdjacency, layoutEgo, selectEgo } from "./ego.js";
import { familyOf, typeLabel } from "./facets.js";
import { loadGraph } from "./inspector-data.js";
import { claimInspector, inspectorStale } from "./inspector-token.js";
import { resolveIdentifier } from "./resolve.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * @param {HTMLElement} container
 * @param {{ catalog: object, manifest: object, routeId: string, hash: string, setHash?: (hash: string) => void, loadGraph?: () => Promise<unknown> }} ctx
 */
export async function renderGraph(container, ctx) {
  const token = claimInspector(container);
  const stale = () => inspectorStale(container, token);
  const fetchGraph = ctx.loadGraph || loadGraph;
  try {
    container.replaceChildren(el("p", { class: "loading", text: "Loading graph…" }));
    if (!ctx.routeId) {
      if (!stale()) container.replaceChildren(emptyGraph());
      return;
    }
    const graph = await fetchGraph();
    if (stale()) return;
    const errors = validateGraph(graph, ctx.manifest);
    if (errors.length) {
      container.replaceChildren(failClosed(errors[0]));
      return;
    }
    const resolved = resolveIdentifier(ctx.catalog, ctx.routeId);
    if (resolved.status !== "ok") {
      container.replaceChildren(unresolved(resolved));
      return;
    }
    const params = readGraphQuery(ctx.hash);
    const selection = selectEgo(indexAdjacency(graph.edges), ctx.catalog.byId, resolved.entity.id, params);
    const layout = layoutEgo(selection);
    if (stale()) return;
    paint(container, ctx, resolved.entity, selection, layout, params);
  } catch (err) {
    if (stale()) return;
    container.replaceChildren(failClosed(err && err.message ? err.message : "This graph cannot be shown."));
  }
}

function paint(container, ctx, entity, selection, layout, params) {
  const head = el("div", { class: "insp-head" });
  const titles = el("div");
  const crumbs = el("div", { class: "crumbs" });
  crumbs.append(el("a", { href: "#/", text: "Explorer" }), " › ", el("a", { href: `#/e/${entity.id}`, text: entity.name }), " › Graph");
  titles.append(crumbs, el("h1", { text: `References around ${entity.name}` }));
  const actions = el("div", { class: "hide-narrow" });
  actions.append(
    el("a", { class: "btn", href: `#/e/${entity.id}`, text: "Open card" }),
    el("a", { class: "btn primary", href: `#/bundle/${entity.id}`, text: "Open bundle" }),
  );
  head.append(titles, actions);

  const controls = el("div", { class: "g-controls" });
  controls.append(segment(ctx, entity.id, params, "hops", [1, 2], "Hops"));
  controls.append(segment(ctx, entity.id, params, "direction", ["both", "out", "in"], "Direction"));

  const status = el("p", { class: "g-status" });
  const hiddenText = selection.hidden > 0 ? ` ${selection.hidden} more not shown.` : "";
  status.textContent = `${selection.shown} of ${selection.total} neighbours shown · cap ${selection.cap} nodes.${hiddenText}`;

  const pieces = [head, controls, status];
  if (selection.hidden > 0) {
    pieces.push(el("div", {
      class: "notice warn",
      role: "status",
      text: `${entity.name} has ${selection.total} neighbours; showing ${selection.shown}. ${selection.hidden} more not shown.`,
    }));
  }
  if (!selection.neighbors.length) {
    pieces.push(el("p", { text: `${entity.name} has no cross-references in this direction, so there is nothing to draw.` }));
  } else {
    pieces.push(draw(entity, selection, layout));
    pieces.push(list(selection));
  }
  container.replaceChildren(...pieces);
}

function draw(entity, selection, layout) {
  const wrap = el("div", { class: "g-canvas" });
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", `Reference graph for ${entity.name}`);
  const byId = new Map(selection.neighbors.map((item) => [item.id, item]));
  for (const edge of layout.edges) {
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", String(edge.x1));
    line.setAttribute("y1", String(edge.y1));
    line.setAttribute("x2", String(edge.x2));
    line.setAttribute("y2", String(edge.y2));
    line.setAttribute("class", "g-edge");
    svg.append(line);
  }
  for (const node of layout.nodes) {
    const neighbor = byId.get(node.id);
    const href = node.side === "root" ? `#/e/${node.id}` : `#/graph/${node.id}`;
    const link = document.createElementNS(SVG_NS, "a");
    link.setAttribute("href", href);
    link.setAttribute("class", "g-node");
    const label = neighbor
      ? `${neighbor.name}, ${typeLabel(neighbor.type)}, ${neighbor.side}`
      : `${entity.name}, centre`;
    link.setAttribute("aria-label", label);
    const dot = document.createElementNS(SVG_NS, "circle");
    dot.setAttribute("cx", String(node.x));
    dot.setAttribute("cy", String(node.y));
    dot.setAttribute("r", node.side === "root" ? "16" : "9");
    const family = familyOf(neighbor ? neighbor.type : entity.type).id;
    dot.setAttribute("class", `g-dot fam-${family}${node.side === "root" ? " g-root" : ""}`);
    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("x", String(node.x));
    text.setAttribute("y", String(node.y + (node.side === "root" ? 28 : 20)));
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("class", "g-label");
    text.textContent = neighbor ? neighbor.name : entity.name;
    link.append(dot, text);
    svg.append(link);
  }
  wrap.append(svg);
  return wrap;
}

function list(selection) {
  const section = el("section", { class: "panel" });
  const top = el("div", { class: "code-top" });
  top.append(el("h2", { text: "List · same data" }), el("span", { class: "hint", text: "Sorted by edge kind, then degree" }));
  const table = el("table", { class: "deps" });
  const head = el("thead");
  const row = el("tr");
  row.append(el("th", { text: "Direction" }), el("th", { text: "Entry" }), el("th", { text: "Relation" }), el("th", { class: "num", text: "Degree" }));
  head.append(row);
  const body = el("tbody");
  for (const item of selection.neighbors) {
    const tr = el("tr");
    const entry = el("td");
    entry.append(el("a", { href: `#/graph/${item.id}`, text: item.name }));
    entry.append(el("span", { class: "via", text: item.id }));
    tr.append(
      el("td", { text: item.side === "in" ? "← in" : item.side === "mutual" ? "↔ both" : "→ out" }),
      entry,
      el("td", { text: item.rel }),
      el("td", { class: "num", text: String(item.degree) }),
    );
    body.append(tr);
  }
  table.append(head, body);
  section.append(top, table);
  return section;
}

function segment(ctx, id, params, key, values, label) {
  const group = el("div", { class: "ctl" });
  group.append(el("span", { class: "lbl", text: label }));
  const seg = el("div", { class: "seg", role: "group", "aria-label": label });
  for (const value of values) {
    const current = key === "hops" ? params.hops : params.direction;
    const button = el("button", {
      type: "button",
      text: String(value),
      "aria-pressed": current === value ? "true" : "false",
    });
    button.addEventListener("click", () => {
      const next = { ...params, [key]: value };
      const hash = graphHash(id, next.hops, next.direction);
      if (ctx.setHash) ctx.setHash(hash);
    });
    seg.append(button);
  }
  group.append(seg);
  return group;
}

function emptyGraph() {
  const box = el("section", { class: "empty" });
  box.append(
    el("h1", { text: "Pick an entry to graph" }),
    el("p", { text: "Open a card or a bundle, then choose View graph." }),
    el("p", {}, [el("a", { href: "#/", text: "Back to Explorer" })]),
  );
  return box;
}

function unresolved(payload) {
  const box = el("section", { class: "empty" });
  if (payload.status === "ambiguous") {
    box.append(el("h1", { text: `\u201c${payload.query}\u201d matches ${payload.candidates.length} entries` }));
    box.append(el("p", { text: "Pick the one you mean. Nothing is chosen automatically." }));
    const items = el("ul", { class: "results" });
    for (const cand of payload.candidates) {
      const link = el("a", { class: "res", href: `#/graph/${cand.id}` });
      link.append(el("h3", { text: cand.name }), el("p", { text: cand.summary || cand.id }));
      items.append(el("li", {}, [link]));
    }
    box.append(items);
    return box;
  }
  box.append(el("h1", { text: "No entry with that id" }), el("p", { text: "Check the spelling, or search by name." }));
  return box;
}

function failClosed(text) {
  const box = el("div", { class: "notice err", role: "alert" });
  box.append(el("p", {}, [el("b", { text: "This graph cannot be shown." }), el("span", { text: ` ${text}` })]));
  return box;
}

export function readGraphQuery(hash) {
  const raw = String(hash || "");
  const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
  const params = new URLSearchParams(query);
  const hops = params.get("hops") === "2" ? 2 : 1;
  const direction = params.get("dir");
  return { hops, direction: direction === "out" || direction === "in" ? direction : "both" };
}

export function graphHash(id, hops, direction) {
  const search = new URLSearchParams();
  search.set("hops", String(hops === 2 ? 2 : 1));
  search.set("dir", direction === "out" || direction === "in" ? direction : "both");
  return `#/graph/${id}?${search}`;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) {
    if (child == null || child === "") continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
