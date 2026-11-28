/**
 * Pure state, XP budget, query-string, and validation logic for the
 * Custom Party & Encounter Builder in `bin/srd-web` (M6.4).
 *
 * Zero DOM; every function runs directly in Node tests (`web/tests/builder.test.js`).
 * The server remains authoritative on all rules, compilation, and `#21` caps.
 */

export const DEFAULT_LIMITS = {
  min_party_size: 1,
  max_party_size: 6,
  min_pc_level: 1,
  max_pc_level: 10,
  min_enemies_per_room: 1,
  max_enemies_per_room: 12,
  max_combatants_per_encounter: 18,
  max_dungeon_rooms: 3,
  min_map_ft: 20,
  max_map_ft: 120,
  max_walls: 200,
  max_difficult: 200,
  max_cover: 100,
};

/** Exact 5e 2024 (SRD 5.2) per-PC XP thresholds (levels 1-10, no 2014 multiplier). */
export const XP_TABLE_2024 = {
  1: { low: 50, moderate: 75, high: 100 },
  2: { low: 100, moderate: 150, high: 200 },
  3: { low: 150, moderate: 225, high: 400 },
  4: { low: 250, moderate: 375, high: 500 },
  5: { low: 500, moderate: 750, high: 1100 },
  6: { low: 600, moderate: 1000, high: 1400 },
  7: { low: 750, moderate: 1300, high: 1700 },
  8: { low: 1000, moderate: 1700, high: 2100 },
  9: { low: 1300, moderate: 2000, high: 2600 },
  10: { low: 1600, moderate: 2300, high: 3100 },
};

export const ALLOWED_DRIVERS = ["heuristic-tactical", "greedy", "random"];

const clone = (v) => JSON.parse(JSON.stringify(v));

function makeRoom(index) {
  return {
    id: `room-${index + 1}`,
    name: index === 0 ? "Room 1: Entrance" : index === 1 ? "Room 2: Guardroom" : "Room 3: Sanctum",
    map_preset: index === 0 ? "open-hall" : index === 1 ? "pillared-crypt" : "chokepoint-bridge",
    width_ft: 50,
    height_ft: 35,
    bounds: null,
    terrain: null,
    enemies: [],
    max_rounds: 12,
  };
}

/**
 * Create an initial builder state seeded from `/api/builder/catalog`.
 * @param {object | null} catalog
 */
export function createBuilderState(catalog = null) {
  const limits = { ...DEFAULT_LIMITS, ...(catalog && catalog.limits ? catalog.limits : {}) };
  const presets = Array.isArray(catalog?.presets) ? catalog.presets : [];
  const party = [];
  const defaultIds = ["fighter", "wizard-evoker"];
  for (const arch of defaultIds) {
    const found = presets.find((p) => p.id === arch);
    if (found && found.default_spec) {
      party.push({
        id: `pc-${arch}`,
        archetype: arch,
        spec: clone(found.default_spec),
        validation: null,
      });
    }
  }
  return {
    mode: "encounter",
    name: "Custom Encounter",
    activeRoom: 0,
    party,
    rooms: [makeRoom(0)],
    play_as: "party",
    party_agent: "heuristic-tactical",
    enemy_agent: "heuristic-tactical",
    seed: 42,
    allow_short_rest: true,
    limits,
    nextPcSeq: party.length + 1,
  };
}

/**
 * Switch between `"encounter"` (1 room) and `"dungeon"` (3 rooms).
 */
export function setBuilderMode(bstate, mode) {
  const next = clone(bstate);
  if (mode !== "encounter" && mode !== "dungeon") return next;
  next.mode = mode;
  if (mode === "encounter") {
    next.rooms = [next.rooms[0] || makeRoom(0)];
    next.activeRoom = 0;
    if (next.name === "Custom Dungeon") next.name = "Custom Encounter";
  } else {
    while (next.rooms.length < 3) next.rooms.push(makeRoom(next.rooms.length));
    next.rooms = next.rooms.slice(0, 3);
    if (next.activeRoom >= 3) next.activeRoom = 0;
    if (next.name === "Custom Encounter") next.name = "Custom Dungeon";
  }
  return next;
}

/**
 * Add a PC from a catalog preset at the requested level (enforcing max_party_size).
 */
export function addPartyMember(bstate, preset, level = 5) {
  if (!preset) return { ok: false, error: "Unknown preset.", state: bstate };
  const maxParty = bstate.limits?.max_party_size || DEFAULT_LIMITS.max_party_size;
  if (bstate.party.length >= maxParty) {
    return { ok: false, error: `Party is capped at ${maxParty} PCs.`, state: bstate };
  }
  const lvl = Math.max(DEFAULT_LIMITS.min_pc_level, Math.min(DEFAULT_LIMITS.max_pc_level, Number(level) || 5));
  const spec = clone((preset.specs_by_level && preset.specs_by_level[String(lvl)]) || preset.default_spec);
  const next = clone(bstate);
  const seq = next.nextPcSeq++;
  const id = `pc-${preset.id}-${seq}`;
  if (next.party.some((p) => p.spec && p.spec.name === spec.name)) {
    spec.name = `${spec.name} ${seq}`;
  }
  next.party.push({ id, archetype: preset.id, spec, validation: null });
  return { ok: true, error: null, state: next };
}

/**
 * Remove a PC by index (keeping at least 1 PC if enforceMin is true).
 */
export function removePartyMember(bstate, index) {
  const minParty = bstate.limits?.min_party_size || DEFAULT_LIMITS.min_party_size;
  if (bstate.party.length <= minParty) {
    return { ok: false, error: `At least ${minParty} PC is required.`, state: bstate };
  }
  if (index < 0 || index >= bstate.party.length) {
    return { ok: false, error: "Invalid PC index.", state: bstate };
  }
  const next = clone(bstate);
  const removed = next.party.splice(index, 1)[0];
  if (next.play_as === removed.id) next.play_as = "party";
  return { ok: true, error: null, state: next };
}

/**
 * Count total enemies in one room.
 */
export function roomEnemyCount(room) {
  if (!room || !Array.isArray(room.enemies)) return 0;
  return room.enemies.reduce((acc, e) => acc + Math.max(0, Number(e.count) || 0), 0);
}

/**
 * Total combatants (PCs + room enemies) for one room.
 */
export function roomCombatantCount(bstate, roomIndex = bstate.activeRoom) {
  const room = bstate.rooms[roomIndex] || bstate.rooms[0];
  return (bstate.party ? bstate.party.length : 0) + roomEnemyCount(room);
}

/**
 * Add `delta` (+1 or -1) of a monster to the active room, enforcing `#21` caps.
 */
export function adjustRoomMonster(bstate, monster, delta, roomIndex = bstate.activeRoom) {
  if (!monster || !monster.slug) return { ok: false, error: "Invalid monster.", state: bstate };
  const next = clone(bstate);
  const room = next.rooms[roomIndex];
  if (!room) return { ok: false, error: "Invalid room.", state: bstate };
  const maxEnemies = next.limits?.max_enemies_per_room || DEFAULT_LIMITS.max_enemies_per_room;
  const maxCombatants = next.limits?.max_combatants_per_encounter || DEFAULT_LIMITS.max_combatants_per_encounter;

  const existing = room.enemies.find((e) => e.monster === monster.slug);
  if (delta > 0) {
    const curEnemies = roomEnemyCount(room);
    if (curEnemies + delta > maxEnemies) {
      return { ok: false, error: `At most ${maxEnemies} enemies per room.`, state: bstate };
    }
    if (next.party.length + curEnemies + delta > maxCombatants) {
      return { ok: false, error: `Room cannot exceed ${maxCombatants} total combatants.`, state: bstate };
    }
    if (existing) {
      existing.count += delta;
    } else {
      room.enemies.push({
        monster: monster.slug,
        name: monster.name || monster.slug,
        cr: monster.cr ?? 0,
        xp: Number(monster.xp) || 0,
        coverage: monster.coverage || "partial",
        count: delta,
      });
    }
  } else if (delta < 0 && existing) {
    existing.count += delta;
    if (existing.count <= 0) {
      room.enemies = room.enemies.filter((e) => e.monster !== monster.slug);
    }
  }
  return { ok: true, error: null, state: next };
}

/**
 * Build the query string for `GET /api/builder/monsters`.
 */
export function monsterQueryString(filters = {}) {
  const params = new URLSearchParams();
  if (filters.q && String(filters.q).trim()) params.set("q", String(filters.q).trim());
  if (filters.cr_min !== undefined && filters.cr_min !== null && String(filters.cr_min).trim() !== "") {
    params.set("cr_min", String(filters.cr_min).trim());
  }
  if (filters.cr_max !== undefined && filters.cr_max !== null && String(filters.cr_max).trim() !== "") {
    params.set("cr_max", String(filters.cr_max).trim());
  }
  if (filters.type && String(filters.type).trim()) params.set("type", String(filters.type).trim());
  if (filters.coverage && String(filters.coverage).trim() && filters.coverage !== "all") {
    params.set("coverage", String(filters.coverage).trim());
  }
  const limit = Math.max(1, Math.min(100, Number(filters.limit) || 25));
  const offset = Math.max(0, Number(filters.offset) || 0);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return params.toString();
}

/**
 * Local 5e 2024 (SRD 5.2) XP budget preview matching `builder.py:compute_encounter_budget`.
 */
export function computeLocalBudget(partyLevels, roomEnemies, xpTable = XP_TABLE_2024) {
  const levels = Array.isArray(partyLevels) ? partyLevels : [];
  let low = 0;
  let moderate = 0;
  let high = 0;
  for (const rawLvl of levels) {
    const lvl = Math.max(1, Math.min(10, Number(rawLvl) || 1));
    const row = xpTable[lvl] || xpTable[String(lvl)] || XP_TABLE_2024[1];
    low += Number(row.low) || 0;
    moderate += Number(row.moderate) || 0;
    high += Number(row.high) || 0;
  }
  let encounterXp = 0;
  let enemyCount = 0;
  for (const e of roomEnemies || []) {
    const cnt = Math.max(0, Number(e.count) || 0);
    enemyCount += cnt;
    encounterXp += cnt * (Number(e.xp) || 0);
  }
  let difficulty = "trivial";
  if (encounterXp < low) difficulty = "trivial";
  else if (encounterXp < moderate) difficulty = "low";
  else if (encounterXp < high) difficulty = "moderate";
  else if (encounterXp <= high) difficulty = "high";
  else difficulty = "deadly";

  return {
    party_xp_thresholds: { low, moderate, high },
    encounter_xp: encounterXp,
    difficulty,
    combatant_count: levels.length + enemyCount,
  };
}

/**
 * Validate builder state locally and construct the exact `POST /api/builder/launch` payload.
 */
export function buildLaunchPayload(bstate) {
  const errors = [];
  const limits = bstate.limits || DEFAULT_LIMITS;
  if (!Array.isArray(bstate.party) || bstate.party.length < limits.min_party_size || bstate.party.length > limits.max_party_size) {
    errors.push(`Party must have ${limits.min_party_size}–${limits.max_party_size} PCs.`);
  }
  if (!ALLOWED_DRIVERS.includes(bstate.party_agent)) {
    errors.push(`Unsupported party driver '${bstate.party_agent}'.`);
  }
  if (!ALLOWED_DRIVERS.includes(bstate.enemy_agent)) {
    errors.push(`Unsupported enemy driver '${bstate.enemy_agent}'.`);
  }
  const expectedRooms = bstate.mode === "dungeon" ? 3 : 1;
  if (!Array.isArray(bstate.rooms) || bstate.rooms.length !== expectedRooms) {
    errors.push(`Mode '${bstate.mode}' requires ${expectedRooms} room(s).`);
  } else {
    bstate.rooms.forEach((room, idx) => {
      const eCnt = roomEnemyCount(room);
      if (eCnt < limits.min_enemies_per_room || eCnt > limits.max_enemies_per_room) {
        errors.push(`Room ${idx + 1} needs ${limits.min_enemies_per_room}–${limits.max_enemies_per_room} enemies.`);
      }
      if (bstate.party.length + eCnt > limits.max_combatants_per_encounter) {
        errors.push(`Room ${idx + 1} exceeds ${limits.max_combatants_per_encounter} total combatants.`);
      }
    });
  }
  if (!Number.isInteger(Number(bstate.seed))) {
    errors.push("Seed must be an integer.");
  }
  if (errors.length) return { ok: false, errors, payload: null };

  const payload = {
    mode: bstate.mode,
    name: String(bstate.name || "Custom Encounter").trim() || "Custom Encounter",
    party: bstate.party.map((p) => ({ id: p.id, spec: clone(p.spec) })),
    rooms: bstate.rooms.map((r) => {
      const entry = {
        id: r.id,
        name: r.name,
        map_preset: r.map_preset,
        width_ft: Number(r.width_ft) || 50,
        height_ft: Number(r.height_ft) || 35,
        max_rounds: Number(r.max_rounds) || 12,
        enemies: r.enemies.map((e) => ({ monster: e.monster, count: Number(e.count) || 1 })),
      };
      if (r.bounds) entry.bounds = clone(r.bounds);
      if (r.terrain) entry.terrain = clone(r.terrain);
      return entry;
    }),
    play_as: bstate.play_as || "party",
    party_agent: bstate.party_agent,
    enemy_agent: bstate.enemy_agent,
    seed: Number(bstate.seed),
    allow_short_rest: Boolean(bstate.allow_short_rest),
  };
  return { ok: true, errors: [], payload };
}

/**
 * Validate a Short Rest plan for `POST /api/short-rest`.
 */
export function validateShortRestPlan(partySummary, plan) {
  const errors = [];
  if (!plan || typeof plan !== "object") return { ok: true, errors: [], plan: null };
  const clean = {};
  for (const [pcId, choice] of Object.entries(plan)) {
    const pc = partySummary && partySummary[pcId];
    if (!pc) continue;
    const hdRem = Number(pc.hit_dice?.remaining ?? 0);
    const spend = Number(choice.hit_dice_to_spend ?? 0);
    if (!Number.isInteger(spend) || spend < 0 || spend > hdRem) {
      errors.push(`${pc.name || pcId}: Hit Dice to spend must be 0–${hdRem}.`);
    }
    const entry = { hit_dice_to_spend: Math.max(0, Math.min(hdRem, Math.trunc(spend) || 0)) };
    if (choice.arcane_recovery_slots && typeof choice.arcane_recovery_slots === "object") {
      const budget = Math.ceil((Number(pc.level) || 1) / 2);
      let used = 0;
      const recSlots = {};
      for (const [lvlStr, cntRaw] of Object.entries(choice.arcane_recovery_slots)) {
        const lvl = Number(lvlStr);
        const cnt = Number(cntRaw) || 0;
        if (cnt <= 0) continue;
        if (!Number.isInteger(lvl) || lvl < 1 || lvl > 5) {
          errors.push(`${pc.name || pcId}: Arcane Recovery only restores L1–L5 slots.`);
        }
        used += lvl * cnt;
        recSlots[String(lvl)] = cnt;
      }
      if (used > budget) {
        errors.push(`${pc.name || pcId}: Arcane Recovery total levels (${used}) exceeds budget (${budget}).`);
      }
      if (Object.keys(recSlots).length) entry.arcane_recovery_slots = recSlots;
    }
    clean[pcId] = entry;
  }
  return { ok: errors.length === 0, errors, plan: clean };
}
