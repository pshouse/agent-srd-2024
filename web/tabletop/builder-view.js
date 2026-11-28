/**
 * Plain-DOM Custom Party & Encounter Builder and Short Rest Intermission UI
 * for `bin/srd-web` (M6.4, ADR 0001 strict CSP).
 *
 * Zero HTML parsing, zero inline CSS attributes, zero storage or external URLs.
 */

import { badge, createBoard, el, notice, svgEl, terrainLegendItems } from "../lib/board-view.js";
import {
  ALLOWED_DRIVERS,
  addPartyMember,
  adjustRoomMonster,
  buildLaunchPayload,
  computeLocalBudget,
  createBuilderState,
  monsterQueryString,
  removePartyMember,
  roomCombatantCount,
  roomEnemyCount,
  setBuilderMode,
  validateShortRestPlan,
} from "./builder-model.js";

const CR_OPTIONS = ["", "0", "0.125", "0.25", "0.5", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "12", "15", "20", "30"];

/**
 * Mount the 4-panel Custom Builder inside `container`.
 */
export async function mountBuilderView(container, { api, onLaunched, onError }) {
  container.replaceChildren(el("p", { class: "hint pad", text: "Loading 5e 2024 (SRD 5.2) builder catalog…" }));
  const catRes = await api("GET", "/api/builder/catalog");
  if (!catRes || !catRes.body) return;
  const catalog = catRes.body;

  let bstate = createBuilderState(catalog);
  let filters = { q: "", cr_min: "", cr_max: "", type: "", coverage: "all", limit: 20, offset: 0 };
  let monsterPage = { total: 0, offset: 0, limit: 20, items: [] };
  let expandedPc = -1;
  let launching = false;
  let localNotice = null;

  const root = el("div", { class: "bld-root" });
  const msgBox = el("div", { class: "bld-msg", "aria-live": "polite" });
  const partyPanel = el("section", { class: "panel pad bld-panel" });
  const mapPanel = el("section", { class: "panel pad bld-panel" });
  const monsterPanel = el("section", { class: "panel pad bld-panel" });
  const settingsPanel = el("section", { class: "panel pad bld-panel" });
  root.append(msgBox, el("div", { class: "bld-grid" }, [partyPanel, mapPanel, monsterPanel, settingsPanel]));
  container.replaceChildren(root);

  function setMsg(kind, title, lines = []) {
    localNotice = kind ? { kind, title, lines } : null;
    if (!localNotice) msgBox.replaceChildren();
    else msgBox.replaceChildren(notice(localNotice.kind, localNotice.title, localNotice.lines));
  }

  async function validatePcAt(idx) {
    const pc = bstate.party[idx];
    if (!pc) return;
    const res = await api("POST", "/api/builder/validate-pc", { spec: pc.spec });
    if (res && res.body && bstate.party[idx] && bstate.party[idx].id === pc.id) {
      bstate.party[idx].validation = res.body;
      paintParty();
    }
  }

  async function fetchMonsters() {
    const qs = monsterQueryString(filters);
    const res = await api("GET", `/api/builder/monsters?${qs}`);
    if (res && res.body) {
      monsterPage = res.body;
      paintMonsters();
    }
  }

  function findMapDef(presetId) {
    return (catalog.maps || []).find((m) => m.id === presetId) || null;
  }

  function paintAll() {
    paintParty();
    paintMap();
    paintMonsters();
    paintSettings();
  }

  // ---- Panel 1: Party Roster (1-6 PCs, L1-10) ------------------------------

  function paintParty() {
    const limits = bstate.limits;
    const head = el("div", { class: "bld-head" }, [
      el("h2", { class: "sec-h", text: `1. Party Roster (${bstate.party.length} / ${limits.max_party_size})` }),
    ]);

    const addBar = el("div", { class: "rp-row bld-addbar" });
    const corePresets = (catalog.presets || []).filter((p) => p.core);
    for (const p of corePresets) {
      const btn = el("button", { type: "button", class: "btn", text: `+ ${p.label.split(" (")[0]}` });
      btn.disabled = bstate.party.length >= limits.max_party_size;
      btn.addEventListener("click", () => {
        const res = addPartyMember(bstate, p, 5);
        if (!res.ok) {
          setMsg("warn", res.error);
          return;
        }
        setMsg(null);
        bstate = res.state;
        paintAll();
        validatePcAt(bstate.party.length - 1);
      });
      addBar.append(btn);
    }

    const otherPresets = (catalog.presets || []).filter((p) => !p.core);
    if (otherPresets.length) {
      const sel = el("select", { class: "bld-select", "aria-label": "Add another SRD class" }, [
        el("option", { value: "", text: "+ Other Class…" }),
        ...otherPresets.map((p) => el("option", { value: p.id, text: p.label })),
      ]);
      sel.disabled = bstate.party.length >= limits.max_party_size;
      sel.addEventListener("change", () => {
        const chosen = otherPresets.find((p) => p.id === sel.value);
        sel.value = "";
        if (!chosen) return;
        const res = addPartyMember(bstate, chosen, 5);
        if (!res.ok) {
          setMsg("warn", res.error);
          return;
        }
        setMsg(null);
        bstate = res.state;
        paintAll();
        validatePcAt(bstate.party.length - 1);
      });
      addBar.append(sel);
    }

    const list = el("div", { class: "bld-pc-list" });
    bstate.party.forEach((pc, idx) => {
      const v = pc.validation;
      const sb = v && v.statblock_summary;
      const lvlSel = el("select", { class: "bld-select bld-lvl", "aria-label": `${pc.spec.name} level` });
      for (let l = limits.min_pc_level; l <= limits.max_pc_level; l++) {
        const opt = el("option", { value: String(l), text: `L${l}` });
        if (Number(pc.spec.level) === l) opt.selected = true;
        lvlSel.append(opt);
      }
      lvlSel.addEventListener("change", () => {
        const lvl = Number(lvlSel.value);
        const preset = (catalog.presets || []).find((p) => p.id === pc.archetype);
        if (preset && preset.specs_by_level && preset.specs_by_level[String(lvl)]) {
          const prevName = pc.spec.name;
          pc.spec = JSON.parse(JSON.stringify(preset.specs_by_level[String(lvl)]));
          pc.spec.name = prevName;
        } else {
          pc.spec.level = lvl;
        }
        pc.validation = null;
        paintAll();
        validatePcAt(idx);
      });

      const editBtn = el("button", { type: "button", class: "btn ghost", text: expandedPc === idx ? "Hide" : "Customize" });
      editBtn.addEventListener("click", () => {
        expandedPc = expandedPc === idx ? -1 : idx;
        paintParty();
      });

      const dupBtn = el("button", { type: "button", class: "btn ghost", text: "Duplicate" });
      dupBtn.disabled = bstate.party.length >= limits.max_party_size;
      dupBtn.addEventListener("click", () => {
        const preset = (catalog.presets || []).find((p) => p.id === pc.archetype);
        const res = addPartyMember(bstate, preset, pc.spec.level);
        if (!res.ok) {
          setMsg("warn", res.error);
          return;
        }
        bstate = res.state;
        paintAll();
        validatePcAt(bstate.party.length - 1);
      });

      const remBtn = el("button", { type: "button", class: "btn ghost", text: "Remove" });
      remBtn.disabled = bstate.party.length <= limits.min_party_size;
      remBtn.addEventListener("click", () => {
        const res = removePartyMember(bstate, idx);
        if (!res.ok) {
          setMsg("warn", res.error);
          return;
        }
        if (expandedPc === idx) expandedPc = -1;
        bstate = res.state;
        paintAll();
      });

      const summaryNode = sb
        ? el("div", { class: "rp-tags" }, [
            badge(`HP ${sb.hp_max}`, "fx"),
            badge(`AC ${sb.ac}`, "on"),
            badge(`Init ${sb.initiative >= 0 ? "+" : ""}${sb.initiative}`, "fx"),
            ...(sb.spell_save_dc ? [badge(`DC ${sb.spell_save_dc}`, "conc")] : []),
          ])
        : v && !v.valid
          ? el("p", { class: "hint tt-retry", text: (v.errors && v.errors[0] ? v.errors[0].message : "Invalid PC spec") })
          : el("p", { class: "hint", text: "Validating…" });

      const subInfo = (catalog.character_options?.subclasses || []).find((s) => s.slug === pc.spec.subclass);
      const subBadge = subInfo ? badge(`${subInfo.name} (${subInfo.coverage})`, subInfo.coverage === "partial" ? "on" : "fx") : null;

      const card = el("div", { class: "bld-pc-card" }, [
        el("div", { class: "bld-pc-top" }, [
          el("div", {}, [
            el("b", { text: pc.spec.name || pc.id }),
            el("span", { class: "hint", text: ` · ${pc.spec.class} ` }),
            ...(subBadge ? [subBadge] : []),
          ]),
          el("div", { class: "rp-row" }, [lvlSel, editBtn, dupBtn, remBtn]),
        ]),
        summaryNode,
      ]);

      if (expandedPc === idx) {
        card.append(buildPcDrawer(pc, idx));
      }
      list.append(card);
    });

    partyPanel.replaceChildren(head, addBar, list);
  }

  function buildPcDrawer(pc, idx) {
    const nameIn = el("input", { type: "text", class: "tt-text", value: pc.spec.name || "", "aria-label": "PC Name" });
    nameIn.addEventListener("change", () => {
      pc.spec.name = nameIn.value.trim() || pc.id;
      paintSettings();
      validatePcAt(idx);
    });

    const armorSel = el("select", { class: "bld-select", "aria-label": "Armor" }, [
      el("option", { value: "", text: "No armor" }),
      ...(catalog.character_options?.armors || []).map((a) => {
        const opt = el("option", { value: a.slug, text: a.name });
        if (pc.spec.equipment && pc.spec.equipment.armor === a.slug) opt.selected = true;
        return opt;
      }),
    ]);
    armorSel.addEventListener("change", () => {
      pc.spec.equipment = pc.spec.equipment || { armor: null, shield: false, weapons: [] };
      pc.spec.equipment.armor = armorSel.value || null;
      validatePcAt(idx);
    });

    const shieldChk = el("input", { type: "checkbox" });
    shieldChk.checked = Boolean(pc.spec.equipment && pc.spec.equipment.shield);
    shieldChk.addEventListener("change", () => {
      pc.spec.equipment = pc.spec.equipment || { armor: null, shield: false, weapons: [] };
      pc.spec.equipment.shield = shieldChk.checked;
      validatePcAt(idx);
    });

    return el("div", { class: "bld-drawer" }, [
      el("label", { class: "bld-field" }, [el("span", { text: "Name" }), nameIn]),
      el("label", { class: "bld-field" }, [el("span", { text: "Armor" }), armorSel]),
      el("label", { class: "bld-check" }, [shieldChk, el("span", { text: "Equip Shield (+2 AC)" })]),
    ]);
  }

  // ---- Panel 2: Battlefield & Mode -----------------------------------------

  function paintMap() {
    const room = bstate.rooms[bstate.activeRoom] || bstate.rooms[0];
    const modeRow = el("div", { class: "rp-row bld-mode-row" });
    for (const [m, label] of [["encounter", "Single Encounter (1 Room)"], ["dungeon", "3-Room Dungeon"]]) {
      const btn = el("button", { type: "button", class: bstate.mode === m ? "btn primary" : "btn", text: label });
      btn.addEventListener("click", () => {
        bstate = setBuilderMode(bstate, m);
        paintAll();
      });
      modeRow.append(btn);
    }

    const tabs = el("div", { class: "rp-row bld-tabs" });
    if (bstate.mode === "dungeon") {
      bstate.rooms.forEach((r, idx) => {
        const btn = el("button", {
          type: "button",
          class: bstate.activeRoom === idx ? "btn primary" : "btn",
          text: `Room ${idx + 1} (${roomEnemyCount(r)})`,
        });
        btn.addEventListener("click", () => {
          bstate.activeRoom = idx;
          paintAll();
        });
        tabs.append(btn);
      });
    }

    const mapSel = el("select", { class: "bld-select", "aria-label": "Map preset" });
    for (const m of catalog.maps || []) {
      const opt = el("option", { value: m.id, text: `${m.name} (${m.kind})` });
      if (room.map_preset === m.id) opt.selected = true;
      mapSel.append(opt);
    }
    mapSel.addEventListener("change", () => {
      room.map_preset = mapSel.value;
      const def = findMapDef(room.map_preset);
      if (def && def.kind === "builtin") {
        room.bounds = JSON.parse(JSON.stringify(def.bounds));
        room.terrain = JSON.parse(JSON.stringify(def.terrain));
      } else {
        room.bounds = null;
        room.terrain = null;
      }
      paintMap();
    });

    const wIn = el("input", { type: "number", step: "5", min: "20", max: "120", value: String(room.width_ft), class: "tt-num", "aria-label": "Room width (ft)" });
    const hIn = el("input", { type: "number", step: "5", min: "20", max: "120", value: String(room.height_ft), class: "tt-num", "aria-label": "Room height (ft)" });
    const mapDef = findMapDef(room.map_preset);
    const isParametric = !mapDef || mapDef.kind === "parametric";
    wIn.disabled = !isParametric;
    hIn.disabled = !isParametric;
    const onDim = () => {
      const w = Math.max(20, Math.min(120, Math.round((Number(wIn.value) || 50) / 5) * 5));
      const h = Math.max(20, Math.min(120, Math.round((Number(hIn.value) || 35) / 5) * 5));
      room.width_ft = w;
      room.height_ft = h;
      room.bounds = null;
      room.terrain = null;
      paintMap();
    };
    wIn.addEventListener("change", onDim);
    hIn.addEventListener("change", onDim);

    const previewBounds = room.bounds || (mapDef && mapDef.kind === "builtin" ? mapDef.bounds : { min_x: 0, min_y: 0, max_x: room.width_ft - 5, max_y: room.height_ft - 5 });
    const previewTerrain = room.terrain || (mapDef ? mapDef.terrain : { walls: [], difficult_terrain: [], cover: [] });
    const viewBox = {
      x: previewBounds.min_x - 5,
      y: previewBounds.min_y - 5,
      w: previewBounds.max_x - previewBounds.min_x + 15,
      h: previewBounds.max_y - previewBounds.min_y + 15,
    };
    const { svg, layer } = createBoard(viewBox, { bounds: previewBounds, terrain: previewTerrain });
    // Draw West (party) and East (enemy) spawn zone indicators.
    const rw = previewBounds.max_x - previewBounds.min_x + 5;
    const rh = previewBounds.max_y - previewBounds.min_y + 5;
    layer.append(
      svgEl("rect", { class: "bld-spawn-party", x: previewBounds.min_x, y: previewBounds.min_y, width: Math.min(10, rw / 3), height: rh }),
      svgEl("rect", { class: "bld-spawn-enemy", x: previewBounds.max_x + 5 - Math.min(10, rw / 3), y: previewBounds.min_y, width: Math.min(10, rw / 3), height: rh }),
    );
    const previewBox = el("div", { class: "g-canvas bld-preview" }, [svg]);
    const legend = el("p", { class: "legend" }, [
      el("span", {}, [el("i", { class: "rp-sw-party" }), " Party Spawn"]),
      el("span", {}, [el("i", { class: "rp-sw-enemy" }), " Enemy Spawn"]),
      ...terrainLegendItems(previewBounds, previewTerrain),
    ]);

    mapPanel.replaceChildren(
      el("h2", { class: "sec-h", text: "2. Battlefield & Mode" }),
      modeRow,
      ...(bstate.mode === "dungeon" ? [tabs] : []),
      el("div", { class: "rp-row bld-map-ctrls" }, [
        mapSel,
        el("span", { text: "W (ft)" }),
        wIn,
        el("span", { text: "H (ft)" }),
        hIn,
      ]),
      previewBox,
      legend,
    );
  }

  // ---- Panel 3: Monster Picker (all 341 SRD) & XP Budget -------------------

  function paintMonsters() {
    const room = bstate.rooms[bstate.activeRoom] || bstate.rooms[0];
    const partyLevels = bstate.party.map((p) => Number(p.spec.level) || 1);
    const budget = computeLocalBudget(partyLevels, room.enemies, catalog.xp_table);
    const totalUnits = roomCombatantCount(bstate, bstate.activeRoom);
    const maxUnits = bstate.limits.max_combatants_per_encounter;

    const meter = el("div", { class: "bld-budget" }, [
      el("div", { class: "rp-tags" }, [
        badge(`Difficulty: ${budget.difficulty.toUpperCase()}`, budget.difficulty === "deadly" ? "bad" : budget.difficulty === "high" ? "conc" : "on"),
        badge(`Encounter XP: ${budget.encounter_xp}`, "fx"),
        badge(`Combatants: ${totalUnits} / ${maxUnits}`, totalUnits > maxUnits ? "bad" : "fx"),
      ]),
      el("p", {
        class: "hint",
        text: `2024 Thresholds · Low ${budget.party_xp_thresholds.low} · Moderate ${budget.party_xp_thresholds.moderate} · High ${budget.party_xp_thresholds.high} XP`,
      }),
    ]);

    const selectedList = el("div", { class: "bld-selected" });
    if (!room.enemies.length) {
      selectedList.append(el("p", { class: "hint", text: "No enemies added to this room yet." }));
    } else {
      for (const e of room.enemies) {
        const minus = el("button", { type: "button", class: "btn ghost", text: "−" });
        const plus = el("button", { type: "button", class: "btn ghost", text: "+" });
        minus.addEventListener("click", () => {
          bstate = adjustRoomMonster(bstate, { slug: e.monster, name: e.name, cr: e.cr, xp: e.xp, coverage: e.coverage }, -1).state;
          paintMonsters();
          paintMap();
          paintSettings();
        });
        plus.addEventListener("click", () => {
          const res = adjustRoomMonster(bstate, { slug: e.monster, name: e.name, cr: e.cr, xp: e.xp, coverage: e.coverage }, 1);
          if (!res.ok) setMsg("warn", res.error);
          else {
            setMsg(null);
            bstate = res.state;
            paintMonsters();
            paintMap();
            paintSettings();
          }
        });
        selectedList.append(
          el("div", { class: "bld-sel-row" }, [
            el("span", { text: `${e.count}× ${e.name} (CR ${e.cr} · ${e.xp * e.count} XP)` }),
            el("div", { class: "rp-row" }, [minus, plus]),
          ]),
        );
      }
    }

    const qIn = el("input", { type: "text", class: "tt-text", value: filters.q, placeholder: "Search all 341 SRD monsters…", "aria-label": "Search monsters" });
    const crMinSel = el("select", { class: "bld-select", "aria-label": "Min CR" }, CR_OPTIONS.map((v) => {
      const opt = el("option", { value: v, text: v ? `CR ≥ ${v}` : "Min CR" });
      if (filters.cr_min === v) opt.selected = true;
      return opt;
    }));
    const crMaxSel = el("select", { class: "bld-select", "aria-label": "Max CR" }, CR_OPTIONS.map((v) => {
      const opt = el("option", { value: v, text: v ? `CR ≤ ${v}` : "Max CR" });
      if (filters.cr_max === v) opt.selected = true;
      return opt;
    }));
    const typeSel = el("select", { class: "bld-select", "aria-label": "Creature type" }, [
      el("option", { value: "", text: "All Types" }),
      ...(catalog.character_options?.creature_types || []).map((t) => {
        const opt = el("option", { value: t, text: t });
        if (filters.type === t) opt.selected = true;
        return opt;
      }),
    ]);
    const covSel = el("select", { class: "bld-select", "aria-label": "Coverage filter" }, [
      ["all", "All Coverage"],
      ["full", "Full only"],
      ["partial", "Partial only"],
    ].map(([v, label]) => {
      const opt = el("option", { value: v, text: label });
      if (filters.coverage === v) opt.selected = true;
      return opt;
    }));
    const searchBtn = el("button", { type: "button", class: "btn", text: "Filter" });
    const applyFilters = () => {
      filters = {
        ...filters,
        q: qIn.value,
        cr_min: crMinSel.value,
        cr_max: crMaxSel.value,
        type: typeSel.value,
        coverage: covSel.value,
        offset: 0,
      };
      fetchMonsters();
    };
    searchBtn.addEventListener("click", applyFilters);
    qIn.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") applyFilters();
    });
    for (const sel of [crMinSel, crMaxSel, typeSel, covSel]) sel.addEventListener("change", applyFilters);

    const results = el("div", { class: "bld-mon-list" });
    for (const m of monsterPage.items || []) {
      const addBtn = el("button", { type: "button", class: "btn", text: "+ Add" });
      addBtn.addEventListener("click", () => {
        const res = adjustRoomMonster(bstate, m, 1);
        if (!res.ok) {
          setMsg("warn", res.error);
          return;
        }
        setMsg(null);
        bstate = res.state;
        paintMonsters();
        paintMap();
        paintSettings();
      });
      const covPill = badge(m.coverage, m.coverage === "full" ? "fx" : "conc");
      if (m.unmodeled_clauses && m.unmodeled_clauses.length) {
        covPill.setAttribute("title", `Unmodeled: ${m.unmodeled_clauses.join(", ")}`);
      }
      results.append(
        el("div", { class: "bld-mon-row" }, [
          el("div", {}, [
            el("b", { text: m.name }),
            el("span", { class: "hint", text: ` · CR ${m.cr} (${m.xp} XP) · AC ${m.ac} · HP ${m.hp_max} · ${m.creature_type} ` }),
            covPill,
          ]),
          addBtn,
        ]),
      );
    }

    const prevBtn = el("button", { type: "button", class: "btn ghost", text: "Prev" });
    const nextBtn = el("button", { type: "button", class: "btn ghost", text: "Next" });
    prevBtn.disabled = filters.offset <= 0;
    nextBtn.disabled = filters.offset + filters.limit >= monsterPage.total;
    prevBtn.addEventListener("click", () => {
      filters.offset = Math.max(0, filters.offset - filters.limit);
      fetchMonsters();
    });
    nextBtn.addEventListener("click", () => {
      filters.offset += filters.limit;
      fetchMonsters();
    });
    const pager = el("div", { class: "rp-row bld-pager" }, [
      prevBtn,
      el("span", { class: "hint", text: `Showing ${monsterPage.total ? filters.offset + 1 : 0}–${Math.min(monsterPage.total, filters.offset + filters.limit)} of ${monsterPage.total}` }),
      nextBtn,
    ]);

    monsterPanel.replaceChildren(
      el("h2", { class: "sec-h", text: `3. Monster Picker (${room.name})` }),
      meter,
      selectedList,
      el("div", { class: "rp-row bld-filters" }, [qIn, crMinSel, crMaxSel, typeSel, covSel, searchBtn]),
      results,
      pager,
    );
  }

  // ---- Panel 4: Table Settings & Launch ------------------------------------

  function paintSettings() {
    const nameIn = el("input", { type: "text", class: "tt-text", value: bstate.name, "aria-label": "Encounter or dungeon name" });
    nameIn.addEventListener("change", () => {
      bstate.name = nameIn.value.trim() || "Custom Encounter";
    });

    const seatSel = el("select", { class: "bld-select", "aria-label": "Play as seat" }, [
      el("option", { value: "party", text: "Entire Party" }),
      ...bstate.party.map((p) => el("option", { value: p.id, text: `${p.spec.name || p.id} (single PC)` })),
      el("option", { value: "dm", text: "DM (control enemies)" }),
    ]);
    seatSel.value = bstate.play_as;
    seatSel.addEventListener("change", () => {
      bstate.play_as = seatSel.value;
    });

    const partyAiSel = el("select", { class: "bld-select", "aria-label": "Party AI driver" }, ALLOWED_DRIVERS.map((d) => {
      const opt = el("option", { value: d, text: d });
      if (bstate.party_agent === d) opt.selected = true;
      return opt;
    }));
    partyAiSel.addEventListener("change", () => {
      bstate.party_agent = partyAiSel.value;
    });

    const enemyAiSel = el("select", { class: "bld-select", "aria-label": "Enemy AI driver" }, ALLOWED_DRIVERS.map((d) => {
      const opt = el("option", { value: d, text: d });
      if (bstate.enemy_agent === d) opt.selected = true;
      return opt;
    }));
    enemyAiSel.addEventListener("change", () => {
      bstate.enemy_agent = enemyAiSel.value;
    });

    const seedIn = el("input", { type: "number", class: "tt-num", value: String(bstate.seed), "aria-label": "Deterministic seed" });
    seedIn.addEventListener("change", () => {
      bstate.seed = Number.parseInt(seedIn.value, 10) || 42;
    });

    const restChk = el("input", { type: "checkbox" });
    restChk.checked = Boolean(bstate.allow_short_rest);
    restChk.addEventListener("change", () => {
      bstate.allow_short_rest = restChk.checked;
    });

    const check = buildLaunchPayload(bstate);
    const launchBtn = el("button", {
      type: "button",
      class: "btn primary bld-launch",
      text: launching ? "Launching…" : bstate.mode === "dungeon" ? "Launch 3-Room Dungeon" : "Launch Encounter",
    });
    launchBtn.disabled = launching || !check.ok;
    launchBtn.addEventListener("click", async () => {
      const built = buildLaunchPayload(bstate);
      if (!built.ok) {
        setMsg("err", "Fix builder issues before launching:", built.errors);
        return;
      }
      launching = true;
      setMsg(null);
      paintSettings();
      const res = await api("POST", "/api/builder/launch", built.payload);
      launching = false;
      if (res && res.body && res.body.state) {
        onLaunched(res.body.state, res.body.error || null);
      } else {
        paintSettings();
        if (onError) onError();
      }
    });

    settingsPanel.replaceChildren(
      el("h2", { class: "sec-h", text: "4. Table Settings & Launch" }),
      el("div", { class: "bld-settings-grid" }, [
        el("label", { class: "bld-field" }, [el("span", { text: "Title" }), nameIn]),
        el("label", { class: "bld-field" }, [el("span", { text: "Play as" }), seatSel]),
        el("label", { class: "bld-field" }, [el("span", { text: "Party AI" }), partyAiSel]),
        el("label", { class: "bld-field" }, [el("span", { text: "Enemy AI" }), enemyAiSel]),
        el("label", { class: "bld-field" }, [el("span", { text: "Seed" }), seedIn]),
      ]),
      ...(bstate.mode === "dungeon"
        ? [el("label", { class: "bld-check" }, [restChk, el("span", { text: "Allow Short Rest between rooms" })])]
        : []),
      ...(!check.ok ? [el("p", { class: "hint", text: check.errors[0] })] : []),
      launchBtn,
    );
  }

  paintAll();
  bstate.party.forEach((_, i) => validatePcAt(i));
  await fetchMonsters();
}

/**
 * Render the inter-room Short Rest intermission panel (`state.status === "short_rest"`).
 */
export function renderShortRestView(container, state, { onSubmit, busy }) {
  const prog = state.dungeon_progress || {};
  const party = prog.party_summary || {};
  const plan = {};

  const cards = el("div", { class: "bld-pc-list" });
  const errBox = el("div", { class: "bld-msg", "aria-live": "polite" });

  for (const [pcId, pc] of Object.entries(party)) {
    const hd = pc.hit_dice || { die: 8, max: 1, remaining: 0 };
    plan[pcId] = { hit_dice_to_spend: 0 };
    const hdIn = el("input", {
      type: "number",
      min: "0",
      max: String(hd.remaining || 0),
      value: "0",
      class: "tt-num",
      "aria-label": `Hit Dice to spend for ${pc.name || pcId}`,
    });
    hdIn.disabled = busy || !pc.alive || (hd.remaining || 0) <= 0;
    hdIn.addEventListener("change", () => {
      plan[pcId].hit_dice_to_spend = Math.max(0, Math.min(Number(hd.remaining) || 0, Number.parseInt(hdIn.value, 10) || 0));
      hdIn.value = String(plan[pcId].hit_dice_to_spend);
    });

    const recoverTags = el("div", { class: "rp-tags" });
    const cres = pc.class_resources || {};
    if (cres.second_wind && cres.second_wind.remaining < cres.second_wind.max) recoverTags.append(badge("+1 Second Wind", "fx"));
    if (cres.action_surge && cres.action_surge.remaining < cres.action_surge.max) recoverTags.append(badge("Action Surge", "fx"));
    if (cres.channel_divinity && cres.channel_divinity.remaining < cres.channel_divinity.max) recoverTags.append(badge("+1 Channel Divinity", "fx"));
    if (pc.is_pact_magic) recoverTags.append(badge("Pact Magic Slots", "fx"));

    cards.append(
      el("div", { class: "bld-pc-card" }, [
        el("div", { class: "bld-pc-top" }, [
          el("b", { text: pc.name || pcId }),
          el("span", { class: "tt-stats", text: `HP ${pc.hp}/${pc.hp_max} · Hit Dice ${hd.remaining}/${hd.max} d${hd.die}` }),
        ]),
        el("div", { class: "rp-row" }, [el("span", { text: "Spend Hit Dice:" }), hdIn]),
        recoverTags,
      ]),
    );
  }

  const takeBtn = el("button", { type: "button", class: "btn primary", text: "Take Short Rest & Enter Next Room" });
  const skipBtn = el("button", { type: "button", class: "btn", text: "Skip Rest & Enter Next Room" });
  takeBtn.disabled = busy;
  skipBtn.disabled = busy;

  takeBtn.addEventListener("click", () => {
    const v = validateShortRestPlan(party, plan);
    if (!v.ok) {
      errBox.replaceChildren(notice("err", "Invalid Short Rest plan.", v.errors));
      return;
    }
    onSubmit(true, v.plan);
  });
  skipBtn.addEventListener("click", () => onSubmit(false, null));

  container.replaceChildren(
    el("section", { class: "panel pad bld-panel" }, [
      el("h2", { class: "sec-h", text: `Short Rest · Room ${(prog.room_index ?? 0) + 1} of ${prog.total_rooms ?? 3} Cleared` }),
      el("p", { class: "hint", text: prog.next_room_name ? `Up next: ${prog.next_room_name}` : "Prepare for the next room." }),
      errBox,
      cards,
      el("div", { class: "rp-row bld-rest-actions" }, [takeBtn, skipBtn]),
    ]),
  );
}
