/** Typographic punctuation folded for search and exact name match (SPEC §4.4). */
const TYPOGRAPHIC = {
  "\u2019": "'",
  "\u2018": "'",
  "\u201c": '"',
  "\u201d": '"',
  "\u2013": "-",
  "\u2014": "-",
  "\u2212": "-",
};

/**
 * @param {unknown} text
 * @returns {string}
 */
export function normalizeSearchText(text) {
  const value = text == null ? "" : String(text);
  let out = "";
  for (const ch of value) out += TYPOGRAPHIC[ch] ?? ch;
  return out;
}

/**
 * Case-insensitive name key used by exact name resolution.
 * @param {unknown} text
 * @returns {string}
 */
export function normalizeNameKey(text) {
  return normalizeSearchText(text).trim().toLowerCase();
}
