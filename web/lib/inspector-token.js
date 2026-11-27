/** Shared so a route change can cancel an inspector that is still loading. */

let serial = 0;

/**
 * @param {HTMLElement} container
 * @returns {string}
 */
export function claimInspector(container) {
  serial += 1;
  const token = `insp-${serial}`;
  container.dataset.inspToken = token;
  return token;
}

/**
 * @param {HTMLElement} container
 * @param {string} token
 */
export function inspectorStale(container, token) {
  return container.dataset.inspToken !== token;
}

/**
 * Drop any in-flight inspector render. Called from render() on every route change.
 * @param {HTMLElement} container
 */
export function invalidateInspector(container) {
  if (!container) return;
  serial += 1;
  container.dataset.inspToken = `cancel-${serial}`;
}
