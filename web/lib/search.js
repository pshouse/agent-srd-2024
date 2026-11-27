import { entityMatchesFacets } from "./facets.js";
import { normalizeSearchText } from "./normalize.js";

/**
 * @param {{ name?: string, slug?: string, id?: string, summary?: string }} entity
 */
function fieldsOf(entity) {
  return {
    name: normalizeSearchText(entity.name || "").toLowerCase(),
    slug: String(entity.slug || "").toLowerCase(),
    id: String(entity.id || "").toLowerCase(),
    summary: normalizeSearchText(entity.summary || "").toLowerCase(),
  };
}

/**
 * @param {{ name?: string, id: string }} a
 * @param {{ name?: string, id: string }} b
 */
function compareNameId(a, b) {
  const an = normalizeSearchText(a.name || "").toLowerCase();
  const bn = normalizeSearchText(b.name || "").toLowerCase();
  if (an < bn) return -1;
  if (an > bn) return 1;
  if (a.id < b.id) return -1;
  if (a.id > b.id) return 1;
  return 0;
}

/**
 * @param {{ name: string, slug: string, id: string }} fields
 * @param {string} qNorm
 */
function rank(fields, qNorm) {
  if (fields.id === qNorm) return 0;
  if (fields.slug === qNorm) return 1;
  if (fields.name === qNorm) return 2;
  if (fields.name.startsWith(qNorm) || fields.slug.startsWith(qNorm)) return 3;
  if (fields.name.includes(qNorm)) return 4;
  if (fields.slug.includes(qNorm) || fields.id.includes(qNorm)) return 5;
  return 6;
}

/**
 * Substring search across name, slug, id, and summary.
 * Typographic punctuation is normalized on the query, name, and summary
 * so a straight apostrophe matches a curly one. Every whitespace-separated
 * token must hit at least one of those four fields.
 *
 * @param {{ entities: object[] }} catalog
 * @param {string} query
 * @param {{ type?: string, facets?: Record<string, string[]> }} [options]
 * @returns {object[]}
 */
export function searchEntities(catalog, query, options = {}) {
  const type = options.type || "";
  const facets = options.facets || null;
  let pool = catalog.entities;
  if (type) pool = pool.filter((entity) => entity.type === type);
  if (facets) pool = pool.filter((entity) => entityMatchesFacets(entity, facets));

  const qNorm = normalizeSearchText(query ?? "").trim().toLowerCase();
  if (!qNorm) return pool.slice().sort(compareNameId);

  const tokens = qNorm.split(/\s+/).filter(Boolean);
  const hits = [];
  for (const entity of pool) {
    const fields = fieldsOf(entity);
    const values = [fields.name, fields.slug, fields.id, fields.summary];
    if (!tokens.every((token) => values.some((value) => value.includes(token)))) continue;
    hits.push({ entity, rank: rank(fields, qNorm) });
  }
  const byName = options.sort === "name";
  hits.sort((a, b) => (byName ? 0 : a.rank - b.rank) || compareNameId(a.entity, b.entity));
  return hits.map((hit) => hit.entity);
}

/**
 * Index ranges of `query` inside `text`. Typographic folds are one-to-one,
 * so the ranges refer to the original string. Overlapping hits merge.
 * @param {string} text
 * @param {string} query
 * @returns {[number, number][]}
 */
export function matchRanges(text, query) {
  const source = String(text ?? "");
  const tokens = normalizeSearchText(query ?? "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (!tokens.length || !source) return [];
  const norm = normalizeSearchText(source).toLowerCase();
  const ranges = [];
  for (const token of tokens) {
    let from = 0;
    while (from < norm.length) {
      const at = norm.indexOf(token, from);
      if (at < 0) break;
      ranges.push([at, at + token.length]);
      from = at + Math.max(token.length, 1);
    }
  }
  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  /** @type {[number, number][]} */
  const merged = [];
  for (const range of ranges) {
    const prev = merged[merged.length - 1];
    if (prev && range[0] <= prev[1]) prev[1] = Math.max(prev[1], range[1]);
    else merged.push([range[0], range[1]]);
  }
  return merged;
}
