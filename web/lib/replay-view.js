/**
 * DOM for `#/replay`. Parsing, validation, reconstruction, and stepping live
 * in replay-model.js and replay-player.js so they can be tested in Node.
 *
 * Trace text is untrusted (it includes LLM-written table talk). It reaches
 * the page only through textContent, attribute values set with
 * setAttribute, and SVG geometry numbers, never as parsed HTML.
 */

import { createBoard, el, logLine, notice, paintBoard, paintRoster, terrainLegendItems } from "./board-view.js";
import { MAX_LOCAL_BYTES, boardBounds, buildReplay, parseTraceText, validateReplayIndex } from "./replay-model.js";
import { PLAY_INTERVAL_MS, initialPlayer, keyCommand, reducePlayer, stepLabel, turnStarts } from "./replay-player.js";
import { replayHash } from "./router.js";

const REPO_BLOB = "https://github.com/pshouse/agent-srd-2024/blob/main/";

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

  const b = boardBounds(replay.frames, replay.bounds, replay.terrain);
  const { svg, layer } = createBoard(b, { bounds: replay.bounds, terrain: replay.terrain });
  const board = el("div", { class: "g-canvas" }, [svg]);
  const legend = el("p", { class: "legend" });
  legend.append(
    el("span", {}, [el("i", { class: "rp-sw-party" }), " Party"]),
    el("span", {}, [el("i", { class: "rp-sw-enemy" }), " Enemies"]),
    ...terrainLegendItems(replay.bounds, replay.terrain),
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
