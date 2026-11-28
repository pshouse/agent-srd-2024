import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DEFAULT_LIMITS,
  XP_TABLE_2024,
  addPartyMember,
  adjustRoomMonster,
  buildLaunchPayload,
  computeLocalBudget,
  createBuilderState,
  monsterQueryString,
  removePartyMember,
  roomCombatantCount,
  setBuilderMode,
  validateShortRestPlan,
} from "../tabletop/builder-model.js";
import { STATUSES, statusText, validateState } from "../tabletop/tabletop-model.js";

function sampleCatalog() {
  const mkPreset = (id, label, cls) => ({
    id,
    label,
    core: true,
    class: cls,
    default_spec: { name: label, class: cls, level: 5 },
    specs_by_level: Object.fromEntries(
      Array.from({ length: 10 }, (_, i) => [String(i + 1), { name: label, class: cls, level: i + 1 }]),
    ),
  });
  return {
    limits: { ...DEFAULT_LIMITS },
    xp_table: XP_TABLE_2024,
    presets: [
      mkPreset("fighter", "Aldric Vance", "fighter"),
      mkPreset("wizard-evoker", "Lyra Starweave", "wizard"),
      mkPreset("cleric-life", "Dorian Dawnshield", "cleric"),
    ],
  };
}

test("builder state enforces party size, room mode, and #21 combatant caps", () => {
  const cat = sampleCatalog();
  let st = createBuilderState(cat);
  assert.equal(st.party.length, 2);
  assert.equal(st.rooms.length, 1);

  // Add up to 6 PCs, then refuse 7th.
  for (let i = 0; i < 4; i++) {
    const r = addPartyMember(st, cat.presets[0], 5);
    assert.equal(r.ok, true);
    st = r.state;
  }
  assert.equal(st.party.length, 6);
  assert.equal(addPartyMember(st, cat.presets[0], 5).ok, false);

  // Switch to 3-room dungeon and back.
  st = setBuilderMode(st, "dungeon");
  assert.equal(st.rooms.length, 3);
  st = setBuilderMode(st, "encounter");
  assert.equal(st.rooms.length, 1);

  // Add 12 enemies -> 6 PCs + 12 enemies = 18 combatants (exact cap).
  const ghoul = { slug: "ghoul", name: "Ghoul", cr: 1, xp: 200, coverage: "full" };
  for (let i = 0; i < 12; i++) {
    const r = adjustRoomMonster(st, ghoul, 1);
    assert.equal(r.ok, true);
    st = r.state;
  }
  assert.equal(roomCombatantCount(st, 0), 18);
  // 13th enemy refused.
  assert.equal(adjustRoomMonster(st, ghoul, 1).ok, false);

  // Removing PCs stops at minimum 1.
  while (st.party.length > 1) {
    const r = removePartyMember(st, st.party.length - 1);
    assert.equal(r.ok, true);
    st = r.state;
  }
  assert.equal(removePartyMember(st, 0).ok, false);
});

test("2024 XP budget math and monster query string builder", () => {
  const b = computeLocalBudget([5, 5, 5, 5], [{ monster: "ghoul", count: 2, xp: 200 }, { monster: "ghast", count: 1, xp: 450 }]);
  assert.deepEqual(b.party_xp_thresholds, { low: 2000, moderate: 3000, high: 4400 });
  assert.equal(b.encounter_xp, 850);
  assert.equal(b.difficulty, "trivial");
  assert.equal(b.combatant_count, 7);

  const highB = computeLocalBudget([1], [{ monster: "goblin-warrior", count: 2, xp: 50 }]);
  assert.equal(highB.difficulty, "high");
  const deadlyB = computeLocalBudget([1], [{ monster: "ghoul", count: 1, xp: 200 }]);
  assert.equal(deadlyB.difficulty, "deadly");

  const qs = monsterQueryString({ q: "goblin", cr_min: "0.25", cr_max: "2", type: "humanoid", coverage: "full", limit: 25, offset: 50 });
  assert.match(qs, /q=goblin/);
  assert.match(qs, /cr_min=0\.25/);
  assert.match(qs, /coverage=full/);
  assert.match(qs, /limit=25/);
  assert.match(qs, /offset=50/);
});

test("buildLaunchPayload and validateShortRestPlan", () => {
  const cat = sampleCatalog();
  let st = createBuilderState(cat);
  assert.equal(buildLaunchPayload(st).ok, false); // 0 enemies
  st = adjustRoomMonster(st, { slug: "ghoul", name: "Ghoul", cr: 1, xp: 200, coverage: "full" }, 2).state;
  const built = buildLaunchPayload(st);
  assert.equal(built.ok, true);
  assert.equal(built.payload.mode, "encounter");
  assert.equal(built.payload.rooms[0].enemies[0].count, 2);

  const partySummary = {
    "pc-lyra": { name: "Lyra", level: 5, hit_dice: { die: 6, max: 5, remaining: 3 } },
  };
  assert.equal(validateShortRestPlan(partySummary, { "pc-lyra": { hit_dice_to_spend: 2, arcane_recovery_slots: { "2": 1, "1": 1 } } }).ok, true);
  assert.equal(validateShortRestPlan(partySummary, { "pc-lyra": { hit_dice_to_spend: 4 } }).ok, false);
  assert.equal(validateShortRestPlan(partySummary, { "pc-lyra": { hit_dice_to_spend: 0, arcane_recovery_slots: { "3": 2 } } }).ok, false);
});

test("tabletop-model accepts builder and short_rest lifecycle states", () => {
  assert.equal(STATUSES.has("builder"), true);
  assert.equal(STATUSES.has("short_rest"), true);
  const base = {
    status: "builder",
    scenario: { id: "builder", name: "Custom Builder" },
    seat: null,
    features: { nl: false, save: false, out_trace: false, resume: false },
    decision: null,
    view: null,
    menu: [],
    log: [],
    log_next: 0,
    result: null,
    trace_written: false,
    checkpoint_written: false,
    pause_reason: null,
    notice: null,
    error: null,
  };
  assert.equal(validateState(base).ok, true);
  assert.match(statusText(base), /Builder/);
  assert.equal(validateState({ ...base, status: "short_rest" }).ok, true);
  assert.match(statusText({ ...base, status: "short_rest" }), /Short Rest/);
});

test("builder modules obey strict ADR 0001 CSP and stay off the Pages allowlist", () => {
  for (const name of ["builder-model.js", "builder-view.js"]) {
    const src = readFileSync(new URL(`../tabletop/${name}`, import.meta.url), "utf8");
    assert.doesNotMatch(src, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/, name);
    assert.doesNotMatch(src, /\.style\b/, name);
    assert.doesNotMatch(src, /setAttribute\(\s*["']style["']/, name);
    assert.doesNotMatch(src, /localStorage|sessionStorage|document\.cookie/, name);
    assert.doesNotMatch(src, /https?:\/\//, name);
  }
  const sites = readFileSync(new URL("../../scripts/site_files.py", import.meta.url), "utf8");
  assert.doesNotMatch(sites, /tabletop|builder-model|builder-view/);
});
