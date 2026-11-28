/**
 * Shared DOM for the tactical grid, the status roster, and log lines.
 * Used by the static `#/replay` viewer (replay-view.js) and the local
 * tabletop served by `bin/srd-web` (web/tabletop/tabletop.js).
 *
 * All text is untrusted (traces and live play carry LLM-written table
 * talk). It reaches the page only through textContent, attribute values
 * set with setAttribute, and SVG geometry numbers, never as parsed HTML.
 *
 * Units come in two shapes. A full unit has `hp` and a roster entry with
 * `hpMax` and `ac`. A hidden unit (an opponent seen from a live seat) has
 * `hpBand` and a roster entry whose `hpMax` and `ac` are null, so exact
 * HP and AC are never drawn.
 */

import { hpBand, occupancy } from "./replay-model.js";

export const SVG_NS = "http://www.w3.org/2000/svg";
const SERIOUS = new Set(["paralyzed", "unconscious", "stunned", "petrified", "incapacitated", "dead"]);

/** Public HP bands from the engine's partial observation, as a meter value. */
const BAND_RATIO = { unharmed: 1, scratched: 0.75, bloodied: 0.4, critical: 0.15, defeated: 0 };

/**
 * Meter step for a public HP band. Unknown bands draw an empty meter.
 * @param {string} band
 * @returns {{ step: number, ratio: number, band: string }}
 */
export function bandMeter(band) {
  const ratio = Object.hasOwn(BAND_RATIO, band) ? BAND_RATIO[band] : 0;
  const step = ratio > 0 ? Math.max(1, Math.round(ratio * 20)) : 0;
  return { step, ratio, band: ratio > 0.5 ? "ok" : ratio > 0.25 ? "mid" : "low" };
}

function meterOf(u, info) {
  return info.hpMax == null ? bandMeter(u.hpBand) : hpBand(u.hp, info.hpMax);
}

function hpText(u, info) {
  return info.hpMax == null ? `Condition: ${u.hpBand || "unknown"}` : `HP ${u.hp}/${info.hpMax} · AC ${info.ac}`;
}

function isDown(u, info) {
  return info.hpMax == null ? u.hpBand === "defeated" : u.hp <= 0;
}

/**
 * An empty board: an SVG with grid lines and a layer for tokens.
 * @param {{ x: number, y: number, w: number, h: number }} b bounds in feet
 * @returns {{ svg: SVGSVGElement, layer: SVGGElement }}
 */
export function createBoard(b) {
  const svg = svgEl("svg", {
    viewBox: `${b.x} ${b.y} ${b.w} ${b.h}`,
    role: "img",
    "aria-label": `Tactical grid, ${b.w / 5} by ${b.h / 5} squares of 5 ft`,
    class: "rp-board",
  });
  const grid = svgEl("g", { class: "rp-grid" });
  for (let x = b.x; x <= b.x + b.w; x += 5) grid.append(svgEl("line", { x1: x, y1: b.y, x2: x, y2: b.y + b.h }));
  for (let y = b.y; y <= b.y + b.h; y += 5) grid.append(svgEl("line", { x1: b.x, y1: y, x2: b.x + b.w, y2: y }));
  const layer = svgEl("g");
  svg.append(grid, layer);
  return { svg, layer };
}

export function paintBoard(layer, frame, prevFrame, roster) {
  layer.replaceChildren();
  const byId = new Map(frame.units.map((u) => [u.id, u]));
  const centers = new Map();
  for (const sq of occupancy(frame.units)) {
    const n = sq.ids.length;
    if (sq.ids.includes(frame.activeId)) layer.append(svgEl("rect", { class: "rp-active", x: sq.x + 0.25, y: sq.y + 0.25, width: 4.5, height: 4.5, rx: 0.6 }));
    if (n > 1) layer.append(svgEl("rect", { class: "rp-shared", x: sq.x + 0.4, y: sq.y + 0.4, width: 4.2, height: 4.2, rx: 0.5 }));
    const cols = n === 1 ? 1 : 2;
    const rowsN = Math.ceil(n / cols);
    const cw = 5 / cols;
    const ch = 5 / rowsN;
    const r = n === 1 ? 1.8 : Math.min(cw, ch) * 0.36;
    sq.ids.forEach((id, j) => {
      const u = byId.get(id);
      const info = roster.get(id);
      const cx = sq.x + cw * (j % cols) + cw / 2;
      const cy = sq.y + ch * Math.floor(j / cols) + ch / 2 - (n === 1 ? 0.3 : 0.15);
      centers.set(id, [cx, cy]);
      layer.append(token(u, info, cx, cy, r, n === 1, id === frame.activeId));
    });
    if (n > 1) {
      const mark = svgEl("g", { class: "rp-stack" });
      mark.append(svgEl("circle", { cx: sq.x + 4.5, cy: sq.y + 0.5, r: 0.7 }), svgText(sq.x + 4.5, sq.y + 0.78, String(n)));
      const names = sq.ids.map((id) => roster.get(id).name).join(", ");
      mark.append(svgTitle(`Shared square (${n}): ${names}`));
      layer.append(mark);
    }
  }
  if (prevFrame) {
    const before = new Map(prevFrame.units.map((u) => [u.id, u]));
    for (const u of frame.units) {
      const old = before.get(u.id);
      if (!old || (old.x === u.x && old.y === u.y)) continue;
      const to = centers.get(u.id);
      layer.prepend(svgEl("line", { class: "rp-move", x1: old.x + 2.5, y1: old.y + 2.5, x2: to[0], y2: to[1] }));
    }
  }
}

function token(u, info, cx, cy, r, roomy, active) {
  const classes = ["rp-tok", `side-${info.side}`];
  if (!u.alive) classes.push("dead");
  else if (isDown(u, info)) classes.push("down");
  if (active) classes.push("on");
  if (!roomy) classes.push("sm");
  if (info.label.length > 2) classes.push("l3");
  const g = svgEl("g", { class: classes.join(" ") });
  g.append(svgEl("circle", { cx, cy, r }));
  g.append(svgText(cx, cy + r * 0.32, info.label));
  const hp = meterOf(u, info);
  const w = r * 2;
  const y = cy + r + 0.2;
  g.append(svgEl("rect", { class: "rp-hpb", x: cx - r, y, width: w, height: 0.5 }));
  if (hp.step) g.append(svgEl("rect", { class: `rp-hpf hp-${hp.band}`, x: cx - r, y, width: w * hp.ratio, height: 0.5 }));
  const tags = [...u.conditions];
  if (u.concentration) tags.push("concentrating");
  if (roomy && tags.length) {
    const t = svgText(cx, cy - r - 0.25, tags.map(abbrev).join(" "));
    t.setAttribute("class", "rp-cnd");
    g.append(t);
  }
  const bits = info.hpMax == null ? [`${info.name}`, `Condition: ${u.hpBand || "unknown"}`] : [`${info.name}`, `HP ${u.hp}/${info.hpMax}`, `AC ${info.ac}`];
  if (tags.length) bits.push(tags.join(", "));
  if (!u.alive) bits.push("defeated");
  g.append(svgTitle(bits.join(" · ")));
  return g;
}

const ABBREV = { prone: "PRN", unconscious: "UNC", paralyzed: "PAR", poisoned: "PSN", grappled: "GRP", restrained: "RST", frightened: "FRT", stunned: "STN", incapacitated: "INC", invisible: "INV", blinded: "BLD", charmed: "CHM", deafened: "DEF", petrified: "PET", exhaustion: "EXH", concentrating: "CONC" };

export function abbrev(name) {
  return Object.hasOwn(ABBREV, name) ? ABBREV[name] : String(name).slice(0, 3).toUpperCase();
}

export function paintRoster(box, frame, roster) {
  const sections = [];
  for (const [side, title] of [["party", "Party"], ["enemy", "Enemies"]]) {
    const list = el("ul", { class: "rp-units" });
    for (const u of frame.units) {
      const info = roster.get(u.id);
      if (info.side !== side) continue;
      const li = el("li", { class: `rp-unit side-${side}${u.alive ? "" : " dead"}${u.id === frame.activeId ? " on" : ""}` });
      const top = el("div", { class: "rp-ut" });
      top.append(el("b", { text: info.name }), el("span", { class: "idc", text: hpText(u, info) }));
      const hp = meterOf(u, info);
      const label = info.hpMax == null ? `Condition ${u.hpBand || "unknown"}` : `${u.hp} of ${info.hpMax} hit points`;
      const meter = el("div", { class: "meter rp-hp", role: "img", "aria-label": label });
      meter.append(el("i", { class: `w${hp.step} hp-${hp.band}` }));
      const tags = el("div", { class: "rp-tags" });
      if (u.id === frame.activeId) tags.append(badge("Active turn", "on"));
      if (!u.alive) tags.append(badge("Defeated", "bad"));
      else if (info.hpMax != null && u.hp <= 0) tags.append(badge("0 HP", "bad"));
      for (const c of u.conditions) tags.append(badge(titleCase(c), SERIOUS.has(c) ? "bad" : ""));
      if (u.concentration) tags.append(badge(`Concentrating: ${u.concentration}`, "conc"));
      for (const fx of u.effects) tags.append(badge(fx.replace(/_/g, " "), "fx"));
      if (u.alive && info.hpMax != null && u.hp <= 0 && (u.deathSaves.successes || u.deathSaves.failures)) {
        tags.append(badge(`Death saves ${u.deathSaves.successes}✓ ${u.deathSaves.failures}✗`, "bad"));
      }
      li.append(top, meter, tags);
      list.append(li);
    }
    sections.push(el("section", {}, [el("h3", { text: title }), list]));
  }
  box.replaceChildren(...sections);
}

export function badge(text, kind) {
  return el("span", { class: kind ? `cond ${kind}` : "cond", text });
}

export function titleCase(text) {
  const s = String(text).replace(/[_-]/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function logLine(line) {
  const p = el("p", { class: `rp-l k-${line.kind}` });
  if (line.kind === "talk") p.append(el("q", { text: line.text }));
  else p.textContent = line.text;
  return p;
}

export function notice(kind, lead, details) {
  const box = el("div", { class: `notice ${kind}`, role: kind === "err" ? "alert" : "status" });
  const mark = kind === "err" ? "×" : kind === "warn" ? "!" : "i";
  box.append(el("span", { class: "ic", "aria-hidden": "true" }, [el("span", { text: mark })]));
  const copy = el("div");
  copy.append(el("b", { text: lead }));
  if (details.length) {
    const list = el("ul", { class: "detail" });
    for (const d of details) list.append(el("li", { text: d }));
    copy.append(list);
  }
  box.append(copy);
  return box;
}

export function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    node.setAttribute(key, typeof value === "number" ? String(Math.round(value * 100) / 100) : String(value));
  }
  return node;
}

export function svgText(x, y, text) {
  const node = svgEl("text", { x, y, "text-anchor": "middle" });
  node.textContent = text;
  return node;
}

export function svgTitle(text) {
  const node = document.createElementNS(SVG_NS, "title");
  node.textContent = text;
  return node;
}

/**
 * @param {string} tag
 * @param {Record<string, unknown>} [attrs]
 * @param {Array<Node | string | null | undefined>} [children]
 */
export function el(tag, attrs = {}, children = []) {
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
