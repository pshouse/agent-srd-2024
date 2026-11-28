/**
 * DOM for the local tabletop served by `bin/srd-web` (127.0.0.1 only).
 *
 * The launch token arrives in the URL fragment, is kept in memory, and is
 * removed from the address bar. Every API call sends it as a bearer token.
 * Server text (combat log, names, LLM table talk) is untrusted and is only
 * rendered with textContent through board-view.js helpers.
 */

import { attributionPieces } from "../lib/attribution.js";
import { badge, createBoard, el, notice, paintBoard, paintRoster, svgEl, terrainLegendItems } from "../lib/board-view.js";
import { mountBuilderView, renderShortRestView } from "./builder-view.js";
import {
  boardModel,
  clickAction,
  downloadName,
  groupMenu,
  itemsTargeting,
  logKind,
  menuBody,
  moveBodyFromInput,
  newLines,
  reachableSquares,
  readToken,
  resolveClickNote,
  squareAt,
  statusText,
  validateState,
} from "./tabletop-model.js";

const POLL_MS = 1000;
const main = document.getElementById("main");
const token = readToken(location.hash);
if (location.hash) history.replaceState(null, "", location.pathname);

fillAttribution();

let state = null;
let lastSeq = 0;
let busy = false;
let pollTimer = 0;
let picked = null;
let pendingMove = null;
let currentClickNote = "";
let builderMounted = false;

const ui = buildShell();

function setClickNote(text) {
  currentClickNote = text;
  ui.clickNote.textContent = text;
}

if (!token) {
  ui.status.replaceChildren(notice("err", "No session token.", ["Open the full URL that bin/srd-web printed when it started (it ends in #token=…)."]));
} else {
  boot();
}

async function boot() {
  const first = await api("GET", "/api/state");
  if (!first) return;
  if (first.body.status === "ready") {
    const started = await api("POST", "/api/start", {});
    if (started) accept(started.body.state, started.body.error);
    return;
  }
  accept(first.body, null);
}

/**
 * @returns {Promise<{ status: number, body: any } | null>} null after showing an error
 */
async function api(method, path, body) {
  const headers = { Authorization: `Bearer ${token}` };
  const init = { method, headers, cache: "no-store", credentials: "omit", redirect: "error" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  let data;
  try {
    res = await fetch(path, init);
    data = await res.json();
  } catch (err) {
    showError("The local server did not answer.", [err && err.message ? err.message : "Network error"]);
    return null;
  }
  if (res.status === 401 || res.status === 403) {
    showError("The server refused this page’s token.", [typeof data.error === "string" ? data.error : `HTTP ${res.status}`, "Reopen the URL bin/srd-web printed."]);
    return null;
  }
  if (res.status === 422 && data && data.state) return { status: res.status, body: data };
  if (!res.ok) {
    showError("That action was refused.", [typeof data.error === "string" ? data.error : `HTTP ${res.status}`]);
    if (res.status === 409) refresh();
    return null;
  }
  return { status: res.status, body: data };
}

async function refresh() {
  const got = await api("GET", `/api/state?since=${lastSeq}`);
  if (got) accept(got.body, null);
}

async function send(path, body) {
  if (busy || !state) return;
  busy = true;
  picked = null;
  paintControls();
  const got = await api("POST", path, body);
  busy = false;
  if (got) {
    accept(got.body.state, got.body.error, { afterAction: true, actionOutcome: got.body.outcome || null });
  } else {
    pendingMove = null;
    setClickNote("");
    paintControls();
  }
}

/** Validate and apply one state payload. A bad payload clears the board (fail closed). */
function accept(raw, actionError, opts = {}) {
  const checked = validateState(raw);
  window.clearTimeout(pollTimer);
  if (!checked.ok) {
    state = null;
    pendingMove = null;
    setClickNote("");
    ui.stage.hidden = true;
    showError("The server sent a state this page does not understand, so nothing is shown.", checked.errors);
    return;
  }
  const prevState = state;
  state = checked.state;
  ui.stage.hidden = false;
  const incoming = newLines(state.log, lastSeq);
  appendLog(incoming);
  const nextNote = resolveClickNote({
    prevState,
    nextState: state,
    currentNote: currentClickNote,
    pendingMove,
    incomingLog: incoming,
    actionError,
    actionOutcome: opts.actionOutcome || null,
    afterAction: Boolean(opts.afterAction),
  });
  if (opts.afterAction || actionError || state.status !== "running") pendingMove = null;
  setClickNote(nextNote);
  if (actionError) showError("The engine rejected that action.", [actionError]);
  else if (state.error) showError("The session stopped with an error.", [state.error]);
  else if (state.notice) ui.status.replaceChildren(notice("warn", state.notice, []));
  else ui.status.replaceChildren();
  paint();
  if (state.status === "running") pollTimer = window.setTimeout(refresh, POLL_MS);
}

/** Drop null placeholders before replaceChildren, which would print them as text. */
function present(...nodes) {
  return nodes.filter((n) => n != null);
}

function showError(lead, details) {
  ui.status.replaceChildren(notice("err", lead, details));
}

// ---- layout ----------------------------------------------------------------

function buildShell() {
  const title = el("h1", { text: "Local tabletop" });
  const pills = el("div", { class: "rp-badges tt-pills" });
  const saveBtn = el("button", { type: "button", class: "btn", text: "Save checkpoint" });
  const quitBtn = el("button", { type: "button", class: "btn", text: "Quit" });
  const resumeBtn = el("button", { type: "button", class: "btn primary", text: "Resume from checkpoint" });
  const exportBtn = el("button", { type: "button", class: "btn primary", text: "Export trace" });
  const tools = el("div", { class: "rp-row tt-tools" }, [saveBtn, quitBtn, resumeBtn, exportBtn]);
  const head = el("div", { class: "tt-head" }, [el("div", {}, [title, pills]), tools]);

  const status = el("div", { class: "rp-status", "aria-live": "polite" });

  const boardBox = el("div", { class: "g-canvas tt-board" });
  const legend = el("p", { class: "legend" });
  legend.append(
    el("span", {}, [el("i", { class: "rp-sw-party" }), " Party"]),
    el("span", {}, [el("i", { class: "rp-sw-enemy" }), " Enemies"]),
    el("span", {}, [el("i", { class: "tt-sw-reach" }), " Reachable this turn"]),
    el("span", { text: "Click a square to move, or a combatant to target it." }),
  );
  const clickNote = el("p", { class: "hint tt-click", "aria-live": "polite" });
  const targets = el("div", { class: "panel pad tt-targets" });
  targets.hidden = true;

  const moveX = el("input", { type: "number", step: "5", inputmode: "numeric", class: "tt-num", "aria-label": "Move to x (ft)" });
  const moveY = el("input", { type: "number", step: "5", inputmode: "numeric", class: "tt-num", "aria-label": "Move to y (ft)" });
  const moveBtn = el("button", { type: "button", class: "btn", text: "Move" });
  const moveRow = el("div", { class: "rp-row tt-move" }, [el("span", { text: "Move to x" }), moveX, el("span", { text: "y" }), moveY, moveBtn]);

  const reroll = el("div", { class: "notice info tt-reroll" });
  reroll.hidden = true;
  const menuBox = el("div", { class: "tt-menu" });

  const sayInput = el("input", { type: "text", maxlength: "300", class: "tt-text", "aria-label": "Say to your side", placeholder: "Say something to your side (sent with your next action)" });
  const sayBtn = el("button", { type: "button", class: "btn", text: "Say" });
  const sayRow = el("div", { class: "rp-row tt-say" }, [sayInput, sayBtn]);

  const nlInput = el("input", { type: "text", maxlength: "300", class: "tt-text", "aria-label": "Order in plain English", placeholder: "Plain-English order, e.g. “shoot the nearest goblin”" });
  const nlBtn = el("button", { type: "button", class: "btn", text: "Send order" });
  const nlRow = el("div", { class: "rp-row tt-nl" }, [nlInput, nlBtn]);
  nlRow.hidden = true;

  const left = el("div", { class: "rp-left" }, [boardBox, legend, clickNote, targets, reroll, el("h2", { class: "sec-h", text: "Legal actions" }), menuBox, moveRow, sayRow, nlRow]);

  const me = el("div", { class: "panel pad tt-me" });
  const roster = el("div", { class: "rp-roster" });
  const talk = el("ul", { class: "tt-talk" });
  const log = el("ol", { class: "rp-log panel tt-log", "aria-label": "Combat log" });
  const right = el("div", { class: "rp-right" }, [
    el("h2", { class: "sec-h", text: "Status" }),
    me,
    roster,
    el("h2", { class: "sec-h", text: "Table talk" }),
    talk,
    el("h2", { class: "sec-h", text: "Combat log" }),
    log,
  ]);
  const builderBox = el("div", { class: "bld-host" });
  builderBox.hidden = true;
  const restBox = el("div", { class: "bld-host" });
  restBox.hidden = true;
  const combatStage = el("div", { class: "rp-main" }, [left, right]);
  const stage = el("div", { class: "tt-stage" }, [builderBox, restBox, combatStage]);
  main.replaceChildren(head, status, stage);

  saveBtn.addEventListener("click", () => send("/api/save", { decision: state && state.decision }));
  quitBtn.addEventListener("click", () => send("/api/quit", { decision: state && state.decision }));
  resumeBtn.addEventListener("click", () => send("/api/start", {}));
  exportBtn.addEventListener("click", exportTrace);
  moveBtn.addEventListener("click", () => {
    const got = moveBodyFromInput(moveX.value, moveY.value, state && state.decision);
    if (!got.ok) {
      showError("Can’t move there.", [got.error]);
      return;
    }
    if (state && state.view && state.view.self) {
      pendingMove = {
        actorId: state.view.self.id,
        from: { x: state.view.self.position.x, y: state.view.self.position.y },
        to: { x: got.body.x, y: got.body.y },
      };
      setClickNote(`Moving to (${got.body.x}, ${got.body.y})…`);
    }
    send("/api/action", got.body);
  });
  sayBtn.addEventListener("click", sendSay);
  sayInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendSay();
  });
  nlBtn.addEventListener("click", sendNl);
  nlInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendNl();
  });

  function sendSay() {
    const text = sayInput.value.trim();
    if (!text) return;
    sayInput.value = "";
    send("/api/action", { kind: "say", text, decision: state && state.decision });
  }
  function sendNl() {
    const text = nlInput.value.trim();
    if (!text) return;
    nlInput.value = "";
    send("/api/nl", { text, decision: state && state.decision });
  }

  return { title, pills, saveBtn, quitBtn, resumeBtn, exportBtn, status, stage, builderBox, restBox, combatStage, boardBox, legend, clickNote, targets, reroll, menuBox, moveRow, moveBtn, moveX, moveY, sayRow, sayBtn, sayInput, nlRow, nlBtn, nlInput, me, roster, talk, log };
}

// ---- painting ----------------------------------------------------------------

function paint() {
  const s = state;
  ui.title.textContent = s.scenario && s.scenario.name ? s.scenario.name : "Local tabletop";
  document.title = `${ui.title.textContent} · Local tabletop · 5e 2024 SRD Agent Pack`;
  ui.pills.replaceChildren(
    ...present(
      el("span", { class: `pill tt-st-${s.status}`, text: statusText(s) }),
      s.view ? el("span", { class: "pill", text: `Round ${s.view.round}` }) : null,
      el("span", { class: "pill", text: `Seat: ${s.seat || "default"}` }),
    ),
  );
  if (s.result) {
    ui.pills.append(
      el("span", { class: "pill full", text: `Winner: ${String(s.result.winner)}` }),
      el("span", { class: "pill", text: `${String(s.result.rounds)} rounds · ${String(s.result.actions)} actions` }),
    );
    if (typeof s.result.composite_score === "number") ui.pills.append(el("span", { class: "pill", text: `Score ${s.result.composite_score}` }));
    if (s.trace_written) ui.pills.append(el("span", { class: "pill full", text: "Trace written (--out-trace)" }));
  }
  if (s.checkpoint_written) ui.pills.append(el("span", { class: "pill", text: "Checkpoint saved" }));
  const isBuilder = s.status === "builder";
  const isShortRest = s.status === "short_rest";
  ui.builderBox.hidden = !isBuilder;
  ui.restBox.hidden = !isShortRest;
  ui.combatStage.hidden = isBuilder || isShortRest;
  if (isBuilder) {
    if (!builderMounted) {
      builderMounted = true;
      mountBuilderView(ui.builderBox, {
        api,
        onLaunched: (nextState, err) => {
          builderMounted = false;
          accept(nextState, err, { afterAction: true });
        },
      });
    }
    paintControls();
    return;
  }
  builderMounted = false;
  if (isShortRest) {
    renderShortRestView(ui.restBox, s, {
      busy,
      onSubmit: (takeRest, plan) => send("/api/short-rest", { take_rest: takeRest, plan }),
    });
    paintControls();
    return;
  }
  paintBoardAndStatus();
  paintControls();
}

function paintBoardAndStatus() {
  const view = state.view;
  if (!view) {
    ui.boardBox.replaceChildren(el("p", { class: "hint pad", text: "The board appears when a seat you control is up." }));
    ui.me.replaceChildren(el("p", { class: "hint", text: "No seat to show yet." }));
    ui.roster.replaceChildren();
    ui.talk.replaceChildren();
    return;
  }
  const model = boardModel(view);
  const live = state.status === "awaiting_input";
  const reach = live ? reachableSquares(view, model.bounds) : [];
  const { svg, layer } = createBoard(model.bounds, { bounds: model.battlefieldBounds, terrain: model.terrain });
  svg.setAttribute("class", "rp-board tt-svg");
  svg.setAttribute("aria-label", `Tactical grid around ${view.self.name}. Use the action list or the Move form below to act with the keyboard.`);
  const reachLayer = svgEl("g", { class: "tt-reach" });
  for (const sq of reach) reachLayer.append(svgEl("rect", { x: sq.x + 0.2, y: sq.y + 0.2, width: 4.6, height: 4.6, rx: 0.4 }));
  svg.insertBefore(reachLayer, layer);
  paintBoard(layer, model.frame, null, model.roster);
  ui.legend.replaceChildren(
    el("span", {}, [el("i", { class: "rp-sw-party" }), " Party"]),
    el("span", {}, [el("i", { class: "rp-sw-enemy" }), " Enemies"]),
    el("span", {}, [el("i", { class: "tt-sw-reach" }), " Reachable this turn"]),
    ...terrainLegendItems(model.battlefieldBounds, model.terrain),
    el("span", { text: "Click a square to move, or a combatant to target it." }),
  );
  if (picked) {
    const u = model.frame.units.find((x) => x.id === picked);
    if (u) layer.append(svgEl("rect", { class: "tt-pick", x: Math.floor(u.x / 5) * 5 + 0.1, y: Math.floor(u.y / 5) * 5 + 0.1, width: 4.8, height: 4.8, rx: 0.6 }));
  }
  svg.addEventListener("click", (event) => onBoardClick(svg, event, reach));
  ui.boardBox.replaceChildren(svg);

  paintMe(view);
  paintRoster(ui.roster, model.frame, model.roster);
  ui.talk.replaceChildren(
    ...(view.table_talk.length
      ? view.table_talk.slice(-8).map((m) => el("li", {}, [el("b", { text: `R${String(m.round)} ${m.actor_name}: ` }), el("q", { text: m.message })]))
      : [el("li", { class: "hint", text: "Nothing said yet." })]),
  );
}

function paintMe(view) {
  const me = view.self;
  const r = me.resources;
  const tags = el("div", { class: "rp-tags" });
  for (const [on, label] of [[r.action, "Action"], [r.bonus_action, "Bonus action"], [r.reaction, "Reaction"]]) tags.append(badge(label, on ? "fx" : "bad"));
  if (r.surge_action) tags.append(badge("Action Surge Ready", "on"));
  if (r.attacks_remaining > 0) tags.append(badge(`Attacks left: ${r.attacks_remaining}`, "on"));
  if (me.heroic_inspiration) tags.append(badge("Heroic Inspiration", "on"));
  const classRes = me.class_resources && typeof me.class_resources === "object" ? me.class_resources : {};
  const RES_LABELS = {
    second_wind: "Second Wind",
    action_surge: "Action Surge",
    lay_on_hands: "Lay on Hands",
    channel_divinity: "Channel Divinity",
    arcane_recovery: "Arcane Recovery",
  };
  for (const [k, info] of Object.entries(classRes)) {
    if (!info || typeof info !== "object") continue;
    const label = RES_LABELS[k] || k.replaceAll("_", " ");
    const unit = k === "lay_on_hands" ? " HP" : "";
    tags.append(badge(`${label} ${info.remaining}/${info.max}${unit}`, info.remaining > 0 ? "fx" : "bad"));
  }
  if (me.hit_dice && typeof me.hit_dice === "object") {
    tags.append(badge(`Hit Dice ${me.hit_dice.remaining}/${me.hit_dice.max} d${me.hit_dice.die}`, "fx"));
  }
  for (const c of me.conditions) tags.append(badge(c, "bad"));
  if (me.concentration) tags.append(badge(`Concentrating: ${me.concentration}`, "conc"));
  const slots = Object.entries(me.slots || {}).map(([lvl, n]) => `L${lvl}: ${n}`).join(" · ");
  ui.me.replaceChildren(
    ...present(
    el("div", { class: "rp-ut" }, [el("b", { text: me.name }), el("span", { class: "idc", text: `(${me.position.x}, ${me.position.y})` })]),
    el("p", { class: "tt-stats", text: `HP ${me.hp}/${me.hp_max}${me.temp_hp ? ` (+${me.temp_hp} temp)` : ""} · AC ${me.ac} · Move ${me.movement_remaining_ft}/${me.speed_ft} ft` }),
    slots ? el("p", { class: "hint", text: `Spell slots ${slots}` }) : null,
    tags,
    view.retry_feedback ? el("p", { class: "hint tt-retry", text: `Retry: ${view.retry_feedback}` }) : null,
    ),
  );
}

function paintControls() {
  const s = state;
  const live = !!s && s.status === "awaiting_input" && !busy;
  const f = s ? s.features : { nl: false, save: false, resume: false };
  ui.saveBtn.hidden = !f.save;
  ui.saveBtn.disabled = !live;
  ui.quitBtn.disabled = !live;
  ui.resumeBtn.hidden = !f.resume;
  ui.resumeBtn.disabled = busy;
  ui.exportBtn.disabled = !s || s.status !== "finished";
  ui.nlRow.hidden = !f.nl;
  for (const c of [ui.moveBtn, ui.moveX, ui.moveY, ui.sayBtn, ui.sayInput, ui.nlBtn, ui.nlInput]) c.disabled = !live;

  const menu = live ? s.menu : [];
  ui.menuBox.replaceChildren();
  if (!menu.length) {
    ui.menuBox.append(el("p", { class: "hint", text: busy ? "Waiting for the engine…" : s && s.status === "running" ? "AI seats are acting…" : "No actions right now." }));
  }
  for (const group of groupMenu(menu)) {
    const list = el("div", { class: "tt-group" }, [el("h3", { text: group.title })]);
    for (const item of group.items) list.append(menuButton(item));
    ui.menuBox.append(list);
  }
  const rerollItem = menu.find((m) => m.intent.type === "reroll");
  ui.reroll.hidden = !rerollItem;
  if (rerollItem) {
    const btn = el("button", { type: "button", class: "btn primary", text: "Spend Heroic Inspiration" });
    btn.addEventListener("click", () => send("/api/action", { kind: "reroll", decision: s.decision }));
    ui.reroll.replaceChildren(el("div", {}, [el("b", { text: rerollItem.label }), el("p", { class: "hint", text: rerollItem.summary })]), btn);
  }
  paintTargets();
}

function menuButton(item) {
  const b = el("button", { type: "button", class: `btn tt-item cat-${item.category}`, title: item.summary });
  b.append(el("span", { class: "tt-n", text: String(item.index) }), el("span", { text: item.label }));
  b.addEventListener("click", () => send("/api/action", menuBody(item, state.decision)));
  return b;
}

function paintTargets() {
  if (!picked || !state || state.status !== "awaiting_input" || busy) {
    ui.targets.hidden = true;
    return;
  }
  const view = state.view;
  const all = [view.self, ...view.allies, ...view.enemies];
  const who = all.find((u) => u.id === picked);
  const items = itemsTargeting(state.menu, picked);
  if (!who || !items.length) {
    ui.targets.hidden = true;
    return;
  }
  const close = el("button", { type: "button", class: "btn ghost", text: "Cancel" });
  close.addEventListener("click", () => {
    picked = null;
    paint();
  });
  ui.targets.replaceChildren(el("div", { class: "rp-ut" }, [el("b", { text: `Act on ${who.name}` }), close]), ...items.map(menuButton));
  ui.targets.hidden = false;
}

function onBoardClick(svg, event, reach) {
  if (!state || state.status !== "awaiting_input" || busy) return;
  const ctm = svg.getScreenCTM();
  if (!ctm) return;
  const pt = new DOMPoint(event.clientX, event.clientY).matrixTransform(ctm.inverse());
  const sq = squareAt(pt.x, pt.y);
  const act = clickAction(state.view, state.menu, sq, reach);
  if (act.type === "move") {
    pendingMove = {
      actorId: state.view.self.id,
      from: { x: state.view.self.position.x, y: state.view.self.position.y },
      to: { x: sq.x, y: sq.y },
    };
    setClickNote(`Moving to (${sq.x}, ${sq.y})…`);
    send("/api/action", { ...act.body, decision: state.decision });
  } else if (act.type === "targets") {
    picked = act.id;
    setClickNote(`${act.items.length} legal ${act.items.length === 1 ? "action" : "actions"} on ${act.name}.`);
    paint();
  } else {
    picked = null;
    setClickNote(act.reason);
    paint();
  }
}

function appendLog(lines) {
  if (!lines.length) return;
  for (const line of lines) {
    if (!line.text.trim()) continue;
    const kind = logKind(line.text);
    const li = el("li", { class: `tt-l k-${kind}` });
    if (kind === "talk") li.append(el("q", { text: line.text.trim() }));
    else li.textContent = line.text.trim();
    ui.log.append(li);
  }
  lastSeq = lines[lines.length - 1].seq;
  while (ui.log.childElementCount > 1500) ui.log.firstElementChild.remove();
  ui.log.scrollTop = ui.log.scrollHeight;
}

async function exportTrace() {
  let res;
  try {
    res = await fetch("/api/trace", { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", credentials: "omit" });
  } catch {
    showError("The local server did not answer.", []);
    return;
  }
  if (!res.ok) {
    showError("No trace to export yet.", [`HTTP ${res.status}`]);
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(new Blob([blob], { type: "application/json" }));
  const a = el("a", { href: url, download: downloadName(res.headers.get("Content-Disposition")) });
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function fillAttribution() {
  const box = document.getElementById("attribution");
  if (!box) return;
  box.replaceChildren();
  for (const piece of attributionPieces()) {
    if (piece.type === "url") box.append(el("a", { href: piece.value, rel: "noopener noreferrer", text: piece.value }));
    else box.append(document.createTextNode(piece.value));
  }
}
