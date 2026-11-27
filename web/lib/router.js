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
