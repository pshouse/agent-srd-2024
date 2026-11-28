/**
 * DOM for `#/replay`. Parsing, validation, reconstruction, and stepping live
 * in replay-model.js and replay-player.js so they can be tested in Node.
 *
 * Trace text is untrusted (it includes LLM-written table talk). It reaches
 * the page only through textContent, attribute values set with
 * setAttribute, and SVG geometry numbers, never as parsed HTML.
 */

import {
  MAX_LOCAL_BYTES,
  boardBounds,
  buildReplay,
  hpBand,
  occupancy,
  parseTraceText,
  validateReplayIndex,
} from "./replay-model.js";
import { PLAY_INTERVAL_MS, initialPlayer, keyCommand, reducePlayer, stepLabel, turnStarts } from "./replay-player.js";
import { replayHash } from "./router.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const REPO_BLOB = "https://github.com/pshouse/agent-srd-2024/blob/main/";
const SERIOUS = new Set(["paralyzed", "unconscious", "stunned", "petrified", "incapacitated", "dead"]);

/** @type {{ controller: AbortController, timer: number } | null} */
let session = null;

/** Stop playback and drop listeners. app.js calls this on every route change. */
export function disposeReplay() {
  if (!session) return;
  session.controller.abort();
  window.clearInterval(session.timer);
  session = null;
}

/**
 * @param {HTMLElement} container
 * @param {{ routeId: string, step?: number, indexUrl: URL, setHash: (hash: string) => void, replaceHash: (hash: string) => void }} ctx
 */
export async function renderReplay(container, ctx) {
  disposeReplay();
  const me = { controller: new AbortController(), timer: 0 };
  session = me;
  const signal = me.controller.signal;
  const stale = () => session !== me;
  let loadSerial = 0;

  const head = el("div", { class: "insp-head" });
  const titles = el("div");
  const crumbs = el("div", { class: "crumbs" });
  crumbs.append(el("a", { href: "#/", text: "Explorer" }), " › Replay");
  titles.append(
    crumbs,
    el("h1", { text: "Arena replay" }),
    el("p", { class: "g-status", text: "Step through a recorded arena combat. Everything runs in this page; local files never leave your browser." }),
  );
  head.append(titles);

  const loader = el("div", { class: "panel pad rp-load" });
  const pickLabel = el("label", { for: "rp-pick", text: "Built-in replay" });
  const pick = el("select", { id: "rp-pick", class: "sel" });
  pick.append(el("option", { value: "", text: "Loading…" }));
  pick.disabled = true;
  const file = el("input", { id: "rp-file", type: "file", accept: ".json,application/json", class: "sr-only" });
  const fileBtn = el("label", { for: "rp-file", class: "btn", text: "Open a trace file" });
  const drop = el("p", { class: "hint", text: "…or drop a .trace.json file here. It is read in this page and never uploaded." });
  loader.append(el("div", { class: "rp-row" }, [pickLabel, pick, fileBtn, file]), drop);

  const status = el("div", { class: "rp-status" });
  const stage = el("div", { class: "rp-stage" });
  const root = el("section", { class: "replay" });
  root.append(head, loader, status, stage);
  container.replaceChildren(root);

  /** @type {Map<string, object>} */
  const rows = new Map();
  let player = null;

  function stopPlayer() {
    window.clearInterval(me.timer);
    me.timer = 0;
    if (player) player.dispose();
    player = null;
  }

  function fail(lead, errors) {
    stopPlayer();
    stage.replaceChildren();
    status.replaceChildren(notice("err", lead, errors));
  }

  function info(lead, detail) {
    status.replaceChildren(notice("info", lead, detail ? [detail] : []));
  }

  function accept(result, source) {
    if (!result.ok) {
      fail("This trace can’t be replayed, so nothing is shown.", result.errors);
      return;
    }
    stopPlayer();
    status.replaceChildren();
    player = mountViewer(stage, result.replay, source, me, signal, source.kind === "built-in" ? ctx.step : 0);
    ctx.step = 0;
    document.title = `Replay · ${result.replay.meta.scenarioName} · 5e 2024 SRD Agent Pack`;
  }

  async function loadBuiltIn(row) {
    const serial = ++loadSerial;
    stopPlayer();
    stage.replaceChildren();
    info(`Loading ${row.id}…`);
    let result;
    try {
      const res = await fetch(new URL(row.file, ctx.indexUrl), { signal });
      if (!res.ok) throw new Error(`HTTP ${res.status} for ${row.file}`);
      const bytes = new Uint8Array(await res.arrayBuffer());
      if (bytes.byteLength !== row.bytes) throw new Error(`${row.file} is ${bytes.byteLength} bytes; the index says ${row.bytes}.`);
      const hash = await sha256Hex(bytes);
      if (hash !== null && hash !== row.sha256) throw new Error(`${row.file} does not match the SHA-256 in the index.`);
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      const parsed = parseTraceText(text);
      result = parsed.ok ? buildReplay(parsed.trace) : parsed;
      if (result.ok && result.replay.meta.digest !== row.digest) {
        result = { ok: false, errors: ["The trace digest does not match the replay index."], replay: null };
      }
    } catch (err) {
      if (signal.aborted) return;
      result = { ok: false, errors: [err && err.message ? err.message : "The trace could not be loaded."], replay: null };
    }
    if (stale() || serial !== loadSerial) return;
    accept(result, { kind: "built-in", row });
  }

  function loadLocal(blob) {
    if (!blob) return;
    const serial = ++loadSerial;
    if (ctx.routeId) {
      ctx.replaceHash(replayHash(""));
      ctx.routeId = "";
    }
    pick.value = "";
    if (blob.size > MAX_LOCAL_BYTES) {
      fail("This file is too large to replay.", [`${blob.size} bytes; the limit is ${MAX_LOCAL_BYTES} bytes.`]);
      return;
    }
    stopPlayer();
    stage.replaceChildren();
    info(`Reading ${blob.name}…`);
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      if (stale() || serial !== loadSerial) return;
      const parsed = parseTraceText(typeof reader.result === "string" ? reader.result : null);
      accept(parsed.ok ? buildReplay(parsed.trace) : parsed, { kind: "local", name: blob.name });
    });
    reader.addEventListener("error", () => {
      if (stale() || serial !== loadSerial) return;
      fail("This file could not be read.", []);
    });
    reader.readAsText(blob);
  }

  file.addEventListener("change", () => {
    loadLocal(file.files && file.files[0]);
    file.value = "";
  }, { signal });
  loader.addEventListener("dragover", (event) => {
    event.preventDefault();
    loader.classList.add("drag");
  }, { signal });
  loader.addEventListener("dragleave", () => loader.classList.remove("drag"), { signal });
  loader.addEventListener("drop", (event) => {
    event.preventDefault();
    loader.classList.remove("drag");
    const files = event.dataTransfer && event.dataTransfer.files;
    loadLocal(files && files[0]);
  }, { signal });
  pick.addEventListener("change", () => ctx.setHash(replayHash(pick.value)), { signal });

  let indexErrors = [];
  try {
    const res = await fetch(ctx.indexUrl, { signal });
    if (!res.ok) throw new Error(`replays/index.json HTTP ${res.status}`);
    const checked = validateReplayIndex(await res.json());
    if (checked.ok) for (const row of checked.rows) rows.set(row.id, row);
    else indexErrors = checked.errors;
  } catch (err) {
    if (signal.aborted) return;
    indexErrors = [err && err.message ? err.message : "The replay index could not be loaded."];
  }
  if (stale()) return;

  pick.replaceChildren(el("option", { value: "", text: rows.size ? "Choose a replay…" : "No built-in replays" }));
  for (const row of rows.values()) {
    const opt = el("option", { value: row.id, text: `${row.scenario_name} · seed ${row.seed} · ${row.winner} wins · ${row.id}` });
    if (row.id === ctx.routeId) opt.selected = true;
    pick.append(opt);
  }
  pick.disabled = rows.size === 0;

  if (indexErrors.length) {
    status.replaceChildren(notice("warn", "The built-in replay list isn’t available. You can still open a local trace file.", indexErrors));
  }
  if (ctx.routeId) {
    const row = rows.get(ctx.routeId);
    if (row) await loadBuiltIn(row);
    else if (!indexErrors.length) fail("There is no built-in replay with that id.", [ctx.routeId]);
  } else if (!indexErrors.length) {
    info("Choose a built-in replay or open a local trace file.", "Keys: ← → step, Shift+← → jump a turn, Home/End, Space to play or pause.");
  }
}

/**
 * @param {Uint8Array} bytes
 * @returns {Promise<string | null>} null when Web Crypto is unavailable (insecure context)
 */
async function sha256Hex(bytes) {
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (!subtle) return null;
  const digest = new Uint8Array(await subtle.digest("SHA-256", bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Build the viewer for one validated replay.
 * @returns {{ dispose: () => void }}
 */
function mountViewer(stage, replay, source, me, signal, startAt) {
  const roster = new Map(replay.roster.map((r) => [r.id, r]));
  const turns = turnStarts(replay.steps);
  let state = reducePlayer(initialPlayer(replay.frames.length), { type: "seek", value: startAt || 0 });
  const local = new AbortController();
  const both = { signal: anySignal(signal, local.signal) };

  const badges = summaryBadges(replay, source);

  const controls = el("div", { class: "rp-ctl", role: "group", "aria-label": "Playback" });
  const btn = (label, aria, cmd) => {
    const b = el("button", { type: "button", class: "btn", text: label, "aria-label": aria });
    b.addEventListener("click", () => dispatch({ type: cmd }), both);
    return b;
  };
  const first = btn("« First", "First step", "first");
  const prev = btn("‹ Prev", "Previous step", "prev");
  const play = btn("Play", "Play", "toggle");
  play.classList.add("primary");
  play.setAttribute("aria-pressed", "false");
  const next = btn("Next ›", "Next step", "next");
  const last = btn("Last »", "Last step", "last");
  const slider = el("input", { type: "range", min: "0", max: String(replay.frames.length - 1), step: "1", value: "0", "aria-label": "Step" });
  slider.addEventListener("input", () => dispatch({ type: "seek", value: Number(slider.value) }), both);
  const pos = el("span", { class: "rp-pos" });
  controls.append(first, prev, play, next, last, el("div", { class: "rp-scrub" }, [slider, pos]));

  const b = boardBounds(replay.frames);
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
  const board = el("div", { class: "g-canvas" }, [svg]);
  const legend = el("p", { class: "legend" });
  legend.append(
    el("span", {}, [el("i", { class: "rp-sw-party" }), " Party"]),
    el("span", {}, [el("i", { class: "rp-sw-enemy" }), " Enemies"]),
    el("span", { text: "Dashed square: shared square (count shown)" }),
    el("span", { text: "Gold outline: active turn" }),
  );
  const rosterBox = el("div", { class: "rp-roster" });
  const left = el("div", { class: "rp-left" }, [controls, board, legend, rosterBox]);

  const log = el("ol", { class: "rp-log panel", "aria-label": "Combat log" });
  const items = [];
  const start = el("li", { class: "rp-step" });
  start.append(el("div", { class: "rp-sh", text: `Start · initiative order: ${replay.meta.turnOrder.map((id) => roster.get(id).name).join(", ")}` }));
  for (const note of replay.unattached) start.append(logLine(note));
  log.append(start);
  items.push(start);
  for (const step of replay.steps) {
    const li = el("li", { class: "rp-step" });
    const jump = el("button", { type: "button", class: "rp-sh" });
    jump.append(
      el("b", { text: `#${step.index}` }),
      ` R${step.round} · `,
      el("span", { class: `rp-who side-${step.side === "enemy" ? "enemy" : "party"}`, text: step.actorName }),
      ` · ${step.intent || "action"}`,
    );
    if (step.driver) jump.append(el("span", { class: "idc", text: ` ${step.driver}` }));
    jump.addEventListener("click", () => dispatch({ type: "seek", value: step.index }), both);
    li.append(jump);
    for (const line of step.lines) li.append(logLine(line));
    if (step.rationale) {
      const why = el("details", { class: "rp-why" });
      why.append(el("summary", { text: "Rationale" }), el("p", { text: step.rationale }));
      li.append(why);
    }
    log.append(li);
    items.push(li);
  }
  const right = el("div", { class: "rp-right" }, [el("h2", { class: "sec-h", text: "Combat log" }), log]);
  stage.replaceChildren(badges, el("div", { class: "rp-main" }, [left, right]));

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    const tag = target && target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || (target && target.isContentEditable)) return;
    if (event.key === " " && (tag === "BUTTON" || tag === "SUMMARY" || tag === "A")) return;
    const cmd = keyCommand(event.key, { shiftKey: event.shiftKey });
    if (!cmd) return;
    event.preventDefault();
    dispatch(cmd);
  }, both);

  function dispatch(cmd) {
    const was = state.playing;
    state = reducePlayer(state, cmd, turns);
    if (state.playing && !was) {
      window.clearInterval(me.timer);
      me.timer = window.setInterval(() => dispatch({ type: "tick" }), PLAY_INTERVAL_MS);
    } else if (!state.playing && me.timer) {
      window.clearInterval(me.timer);
      me.timer = 0;
    }
    paint();
  }

  function paint() {
    const i = state.index;
    const frame = replay.frames[i];
    const label = stepLabel(replay, i);
    slider.value = String(i);
    slider.setAttribute("aria-valuetext", label);
    pos.textContent = label;
    play.textContent = state.playing ? "Pause" : "Play";
    play.setAttribute("aria-label", state.playing ? "Pause" : "Play");
    play.setAttribute("aria-pressed", state.playing ? "true" : "false");
    first.disabled = prev.disabled = i === 0;
    next.disabled = last.disabled = i === replay.frames.length - 1;
    paintBoard(layer, frame, i > 0 ? replay.frames[i - 1] : null, roster);
    paintRoster(rosterBox, frame, roster);
    items.forEach((li, n) => {
      li.hidden = n > i;
      li.classList.toggle("on", n === i);
      if (n === i) li.setAttribute("aria-current", "step");
      else li.removeAttribute("aria-current");
    });
    const current = items[i];
    const top = current.offsetTop;
    if (top < log.scrollTop || top + current.offsetHeight > log.scrollTop + log.clientHeight) {
      log.scrollTop = Math.max(0, top + current.offsetHeight - log.clientHeight);
    }
  }

  paint();
  return {
    dispose() {
      local.abort();
    },
  };
}

function anySignal(a, b) {
  const c = new AbortController();
  const stop = () => c.abort();
  if (a.aborted || b.aborted) c.abort();
  a.addEventListener("abort", stop, { once: true });
  b.addEventListener("abort", stop, { once: true });
  return c.signal;
}

function summaryBadges(replay, source) {
  const m = replay.meta;
  const row = el("div", { class: "rp-badges" });
  row.append(el("h2", { text: m.scenarioName }));
  const win = m.winner === "party" || m.winner === "enemy" ? m.winner : "other";
  row.append(
    el("span", { class: `pill rp-win-${win}`, text: `Winner: ${m.winner}` }),
    el("span", { class: "pill", text: `Seed ${m.seed}` }),
    el("span", { class: "pill", text: `${m.rounds} ${m.rounds === 1 ? "round" : "rounds"} · ${replay.steps.length} actions` }),
  );
  const s = m.score;
  if (s.composite !== null) {
    const parts = [
      ["outcome", s.outcome],
      ["legality", s.legality],
      ["tactical", s.tactical],
      ["coordination", s.coordination],
    ].filter(([, v]) => v !== null);
    row.append(el("span", {
      class: "pill full",
      text: `Score ${s.composite}${s.side ? ` (${s.side})` : ""}`,
      title: parts.map(([k, v]) => `${k} ${v}`).join(" · "),
    }));
  }
  if (m.violations) row.append(el("span", { class: "pill omit", text: `${m.violations} rejected ${m.violations === 1 ? "attempt" : "attempts"}` }));
  const digest = el("code", { class: "idc", title: m.digest, text: `digest ${m.digest.slice(0, 12)}…` });
  row.append(digest);
  const facts = el("p", { class: "g-status" });
  facts.append(`Party: ${m.partyDriver} · Enemies: ${m.enemyDriver} · `);
  if (source.kind === "built-in") {
    facts.append(`Built-in: ${source.row.id}`);
    if (source.row.writeup) {
      facts.append(" · ");
      facts.append(el("a", { href: `${REPO_BLOB}${source.row.writeup}`, text: "Session writeup" }));
    }
  } else {
    facts.append(`Local file: ${source.name}`);
  }
  return el("div", {}, [row, facts]);
}

function paintBoard(layer, frame, prevFrame, roster) {
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
  else if (u.hp <= 0) classes.push("down");
  if (active) classes.push("on");
  if (!roomy) classes.push("sm");
  if (info.label.length > 2) classes.push("l3");
  const g = svgEl("g", { class: classes.join(" ") });
  g.append(svgEl("circle", { cx, cy, r }));
  g.append(svgText(cx, cy + r * 0.32, info.label));
  const hp = hpBand(u.hp, info.hpMax);
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
  const bits = [`${info.name}`, `HP ${u.hp}/${info.hpMax}`, `AC ${info.ac}`];
  if (tags.length) bits.push(tags.join(", "));
  if (!u.alive) bits.push("defeated");
  g.append(svgTitle(bits.join(" · ")));
  return g;
}

const ABBREV = { prone: "PRN", unconscious: "UNC", paralyzed: "PAR", poisoned: "PSN", grappled: "GRP", restrained: "RST", frightened: "FRT", stunned: "STN", incapacitated: "INC", invisible: "INV", blinded: "BLD", charmed: "CHM", deafened: "DEF", petrified: "PET", exhaustion: "EXH", concentrating: "CONC" };

function abbrev(name) {
  return Object.hasOwn(ABBREV, name) ? ABBREV[name] : String(name).slice(0, 3).toUpperCase();
}

function paintRoster(box, frame, roster) {
  const sections = [];
  for (const [side, title] of [["party", "Party"], ["enemy", "Enemies"]]) {
    const list = el("ul", { class: "rp-units" });
    for (const u of frame.units) {
      const info = roster.get(u.id);
      if (info.side !== side) continue;
      const li = el("li", { class: `rp-unit side-${side}${u.alive ? "" : " dead"}${u.id === frame.activeId ? " on" : ""}` });
      const top = el("div", { class: "rp-ut" });
      top.append(el("b", { text: info.name }), el("span", { class: "idc", text: `HP ${u.hp}/${info.hpMax} · AC ${info.ac}` }));
      const hp = hpBand(u.hp, info.hpMax);
      const meter = el("div", { class: "meter rp-hp", role: "img", "aria-label": `${u.hp} of ${info.hpMax} hit points` });
      meter.append(el("i", { class: `w${hp.step} hp-${hp.band}` }));
      const tags = el("div", { class: "rp-tags" });
      if (u.id === frame.activeId) tags.append(badge("Active turn", "on"));
      if (!u.alive) tags.append(badge("Defeated", "bad"));
      else if (u.hp <= 0) tags.append(badge("0 HP", "bad"));
      for (const c of u.conditions) tags.append(badge(titleCase(c), SERIOUS.has(c) ? "bad" : ""));
      if (u.concentration) tags.append(badge(`Concentrating: ${u.concentration}`, "conc"));
      for (const fx of u.effects) tags.append(badge(fx.replace(/_/g, " "), "fx"));
      if (u.alive && u.hp <= 0 && (u.deathSaves.successes || u.deathSaves.failures)) {
        tags.append(badge(`Death saves ${u.deathSaves.successes}✓ ${u.deathSaves.failures}✗`, "bad"));
      }
      li.append(top, meter, tags);
      list.append(li);
    }
    sections.push(el("section", {}, [el("h3", { text: title }), list]));
  }
  box.replaceChildren(...sections);
}

function badge(text, kind) {
  return el("span", { class: kind ? `cond ${kind}` : "cond", text });
}

function titleCase(text) {
  const s = String(text).replace(/[_-]/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function logLine(line) {
  const p = el("p", { class: `rp-l k-${line.kind}` });
  if (line.kind === "talk") p.append(el("q", { text: line.text }));
  else p.textContent = line.text;
  return p;
}

function notice(kind, lead, details) {
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

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    node.setAttribute(key, typeof value === "number" ? String(Math.round(value * 100) / 100) : String(value));
  }
  return node;
}

function svgText(x, y, text) {
  const node = svgEl("text", { x, y, "text-anchor": "middle" });
  node.textContent = text;
  return node;
}

function svgTitle(text) {
  const node = document.createElementNS(SVG_NS, "title");
  node.textContent = text;
  return node;
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
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) {
    if (child == null || child === "") continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
