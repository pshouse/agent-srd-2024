/**
 * Hash-only routes. The path before the hash is the Pages base
 * (`/agent-srd-2024/`) or the local `/web/` directory, and is ignored.
 *
 * - `#/` explorer
 * - `#/e/<type>:<slug>` card
 * - `#/replay` and `#/replay/<id>` arena replay viewer
 *
 * @param {string} hash
 * @returns {{ name: "explorer", unknown: boolean } | { name: "card", type: string, slug: string, id: string, unknown: false }}
 */
export function parseRoute(hash) {
  let raw = String(hash ?? "");
  if (raw.startsWith("#")) raw = raw.slice(1);
  const queryAt = raw.indexOf("?");
  const pathOnly = (queryAt === -1 ? raw : raw.slice(0, queryAt)).replace(/\/+$/, "") || "/";
  if (pathOnly === "/") return { name: "explorer", unknown: false };
  const parts = pathOnly.split("/");
  const head = decodePart(parts[1] || "").toLowerCase();
  if ((head === "bundle" || head === "graph") && parts.length <= 3) {
    if (parts.length === 2) return { name: "later", view: head, id: "", unknown: false };
    const id = safeEntityId(decodePart(parts[2]).toLowerCase());
    if (!id) return { name: "explorer", unknown: true };
    return { name: "later", view: head, id, unknown: false };
  }
  if (head === "replay" && parts.length <= 3) {
    if (parts.length === 2) return { name: "replay", id: "", unknown: false };
    const id = decodePart(parts[2]).toLowerCase();
    if (!REPLAY_ID.test(id)) return { name: "explorer", unknown: true };
    return { name: "replay", id, unknown: false };
  }
  if (head === "e" && parts.length === 3) {
    const decoded = decodePart(parts[2]).toLowerCase();
    const match = /^([a-z0-9-]+):([a-z0-9-]+)$/.exec(decoded);
    if (!match) return { name: "explorer", unknown: true };
    const id = safeEntityId(decoded);
    if (!id) return { name: "explorer", unknown: true };
    return { name: "card", type: match[1], slug: match[2], id, unknown: false };
  }
  return { name: "explorer", unknown: true };
}

/** Built-in replay ids are trace file stems, such as `2026-11-27-goblin-nl-1`. */
const REPLAY_ID = /^[a-z0-9][a-z0-9._-]{0,120}$/;

/**
 * @param {string} id built-in replay id, or "" for the loader
 * @returns {string}
 */
export function replayHash(id) {
  return id ? `#/replay/${id}` : "#/replay";
}

/**
 * Optional `?step=N` on a replay route, so a moment in a combat can be linked.
 * @param {string} hash
 * @returns {number} 0 when absent or not a non-negative integer
 */
export function replayStepFromHash(hash) {
  const raw = String(hash ?? "");
  const at = raw.indexOf("?");
  if (at === -1) return 0;
  const value = new URLSearchParams(raw.slice(at + 1)).get("step") || "";
  return /^\d{1,6}$/.test(value) ? Number(value) : 0;
}

const BLOCKED_IDS = new Set(["__proto__", "constructor", "prototype"]);

/**
 * Accept a canonical id, a bare slug, or a real entry name.
 * Spaces, apostrophes, dots, hyphens, and the other characters that appear
 * in the catalog are kept. Prototype keys, control characters, and
 * overlong strings are rejected.
 * @param {string} raw
 * @returns {string}
 */
export function safeEntityId(raw) {
  const id = String(raw || "");
  if (!id || id.length > 200) return "";
  for (const ch of id) {
    const code = ch.codePointAt(0);
    if (code <= 0x1f || code === 0x7f) return "";
  }
  if (id.includes("\\")) return "";
  if (BLOCKED_IDS.has(id)) return "";
  for (const part of id.split(":")) {
    if (BLOCKED_IDS.has(part)) return "";
  }
  return id;
}

/**
 * Decode one path segment. A `%2F` stays inside that segment, so a name
 * such as Antipathy/Sympathy is one id rather than an extra path piece.
 * @param {string} part
 * @returns {string}
 */
function decodePart(part) {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/**
 * @param {string} id
 * @returns {string}
 */
export function cardHash(id) {
  return `#/e/${id}`;
}

const QUERY_RESERVED = new Set(["q", "type", "sort"]);

/**
 * Read a location hash the same way the first paint and `hashchange` do.
 * Facet bags are null-prototype objects. Keys other than `q`, `type`, `sort`,
 * and the allowlist are ignored, including `__proto__`, `constructor`, and `prototype`.
 * @param {string} hash
 * @param {Iterable<string>} [allowedFacetKeys]
 */
export function readHashState(hash, allowedFacetKeys) {
  const allowed = new Set(allowedFacetKeys || []);
  const route = parseRoute(hash);
  const raw = String(hash ?? "");
  const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
  const params = new URLSearchParams(query);
  const facets = Object.create(null);
  for (const [key, value] of params) {
    if (value === "" || QUERY_RESERVED.has(key) || !allowed.has(key)) continue;
    if (!Object.hasOwn(facets, key)) facets[key] = [];
    facets[key].push(value);
  }
  return {
    route,
    q: params.get("q") || "",
    type: params.get("type") || "",
    sort: params.get("sort") || "",
    facets,
  };
}
