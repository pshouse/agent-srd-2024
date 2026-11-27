/**
 * Hash-only routes. The path before the hash is the Pages base
 * (`/agent-srd-2024/`) or the local `/web/` directory, and is ignored.
 *
 * - `#/` explorer
 * - `#/e/<type>:<slug>` card
 *
 * @param {string} hash
 * @returns {{ name: "explorer", unknown: boolean } | { name: "card", type: string, slug: string, id: string, unknown: false }}
 */
export function parseRoute(hash) {
  let raw = String(hash ?? "");
  if (raw.startsWith("#")) raw = raw.slice(1);
  try {
    raw = decodeURIComponent(raw);
  } catch {
    /* keep the raw hash when it is not valid percent-encoding */
  }
  const pathOnly = raw.split("?")[0].replace(/\/+$/, "") || "/";
  if (pathOnly === "/") return { name: "explorer", unknown: false };
  const lower = pathOnly.toLowerCase();
  const later = /^\/(bundle|graph)(?:\/([a-z0-9-]+:[a-z0-9-]+))?$/.exec(lower);
  if (later) return { name: "later", view: later[1], id: later[2] || "", unknown: false };
  const match = /^\/e\/([a-z0-9-]+):([a-z0-9-]+)$/.exec(lower);
  if (!match) return { name: "explorer", unknown: true };
  return {
    name: "card",
    type: match[1],
    slug: match[2],
    id: `${match[1]}:${match[2]}`,
    unknown: false,
  };
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
