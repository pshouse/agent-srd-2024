const SAFE_SEGMENT = /^[a-z0-9-]+$/;

/**
 * Relative card path under dist/, or null when the segments are not safe.
 * @param {string} type
 * @param {string} slug
 * @returns {string | null}
 */
export function cardRelativePath(type, slug) {
  if (!SAFE_SEGMENT.test(type) || !SAFE_SEGMENT.test(slug)) return null;
  return `cards/${type}/${slug}.md`;
}

/**
 * Resolve a dist-relative asset against the app module URL.
 * `web/app.js` plus `../dist/...` lands on `/agent-srd-2024/dist/...`
 * under Pages and on `/dist/...` when the repo root is the server root.
 * @param {string | URL} moduleUrl
 * @param {string} distRelativePath
 * @returns {URL}
 */
export function distAssetUrl(moduleUrl, distRelativePath) {
  const path = distRelativePath.replace(/^\/+/, "");
  return new URL(`../dist/${path}`, moduleUrl);
}

/**
 * @param {string | URL} moduleUrl
 * @param {string} type
 * @param {string} slug
 * @returns {URL | null}
 */
export function cardAssetUrl(moduleUrl, type, slug) {
  const rel = cardRelativePath(type, slug);
  if (!rel) return null;
  return distAssetUrl(moduleUrl, rel);
}
