import { searchEntities } from "./search.js";
import { normalizeNameKey } from "./normalize.js";

const LEVEL_URL = /^\/api\/2024\/(?:classes|subclasses)\/([a-z0-9-]+)\/levels\/(\d+)$/;
const SIMPLE_URL = /^\/api\/2024\/([a-z0-9-]+)\/([a-z0-9-]+)$/;

/**
 * Map an exact `/api/2024/...` URL onto a canonical id when the index can
 * prove the target exists. Level URLs use the URL segment plus the level
 * number (`levels:berserker-3`). Proficiency tool URLs prefix the slug with
 * `tool-` while the canonical id does not; that alias is accepted only when
 * the unprefixed id is present. Anything else returns null — never a guess.
 *
 * @param {string} url
 * @param {Map<string, object>} byId
 * @returns {string | null}
 */
export function idForApiUrl(url, byId) {
  const level = LEVEL_URL.exec(url);
  if (level) {
    const id = `levels:${level[1]}-${level[2]}`;
    return byId.has(id) ? id : null;
  }
  const simple = SIMPLE_URL.exec(url);
  if (!simple) return null;
  const direct = `${simple[1]}:${simple[2]}`;
  if (byId.has(direct)) return direct;
  if (simple[1] === "proficiencies" && simple[2].startsWith("tool-")) {
    const alt = `proficiencies:${simple[2].slice("tool-".length)}`;
    if (byId.has(alt)) return alt;
  }
  return null;
}

/**
 * @param {object[]} entities
 */
export function buildCatalog(entities) {
  const list = Array.isArray(entities) ? entities : [];
  const byId = new Map();
  const bySlug = new Map();
  const byName = new Map();
  for (const entity of list) {
    if (!entity || typeof entity.id !== "string") continue;
    byId.set(entity.id, entity);
    push(bySlug, String(entity.slug || "").toLowerCase(), entity);
    push(byName, normalizeNameKey(entity.name || ""), entity);
  }
  for (const group of bySlug.values()) group.sort(compareId);
  for (const group of byName.values()) group.sort(compareId);
  return { entities: list, byId, bySlug, byName };
}

/**
 * SPEC §4.2 resolution order:
 * canonical id, exact API URL, bare slug, normalized exact name, else not found.
 * More than one slug or name match is `ambiguous` with candidates sorted by id.
 *
 * @param {ReturnType<typeof buildCatalog>} catalog
 * @param {string} rawQuery
 */
export function resolveIdentifier(catalog, rawQuery) {
  const q = String(rawQuery ?? "").trim();
  if (!q) return { status: "not_found", query: q, suggestions: [] };

  const qLower = q.toLowerCase();
  if (q.includes(":")) {
    const entity = catalog.byId.get(qLower);
    if (entity) return { status: "ok", query: q, entity };
  }

  if (q.startsWith("/api/")) {
    const id = idForApiUrl(q, catalog.byId);
    if (id) return { status: "ok", query: q, entity: catalog.byId.get(id) };
  }

  const slugHits = catalog.bySlug.get(qLower) || [];
  if (slugHits.length === 1) return { status: "ok", query: q, entity: slugHits[0] };
  if (slugHits.length > 1) {
    return { status: "ambiguous", query: q, candidates: slugHits.map(stub) };
  }

  const nameHits = catalog.byName.get(normalizeNameKey(q)) || [];
  if (nameHits.length === 1) return { status: "ok", query: q, entity: nameHits[0] };
  if (nameHits.length > 1) {
    return { status: "ambiguous", query: q, candidates: nameHits.map(stub) };
  }

  const suggestions = searchEntities(catalog, q).slice(0, 5).map(stub);
  return { status: "not_found", query: q, suggestions };
}

/**
 * Unique resolutions open a card. Ambiguous queries return every candidate
 * and do not pick one.
 *
 * @param {ReturnType<typeof buildCatalog>} catalog
 * @param {string} rawQuery
 */
export function openTarget(catalog, rawQuery) {
  const resolved = resolveIdentifier(catalog, rawQuery);
  if (resolved.status === "ok") {
    return {
      action: "open",
      id: resolved.entity.id,
      hash: `#/e/${resolved.entity.id}`,
      entity: resolved.entity,
    };
  }
  if (resolved.status === "ambiguous") {
    return { action: "ambiguous", query: resolved.query, candidates: resolved.candidates };
  }
  return { action: "none", query: resolved.query, suggestions: resolved.suggestions };
}

/**
 * @param {Map<string, object[]>} map
 * @param {string} key
 * @param {object} entity
 */
function push(map, key, entity) {
  let group = map.get(key);
  if (!group) {
    group = [];
    map.set(key, group);
  }
  group.push(entity);
}

/**
 * @param {{ id: string }} a
 * @param {{ id: string }} b
 */
function compareId(a, b) {
  if (a.id < b.id) return -1;
  if (a.id > b.id) return 1;
  return 0;
}

/**
 * @param {{ id: string, type: string, name: string, summary?: string }} entity
 */
function stub(entity) {
  return {
    id: entity.id,
    type: entity.type,
    name: entity.name,
    summary: entity.summary,
  };
}
