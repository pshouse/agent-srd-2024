/** Fail-closed invariants for the M1 catalog. Graph JSON is not an input. */

export const EXPECTED_SCHEMA_VERSION = "1";
export const EXPECTED_DATASET_ID = "srd-2024-5.2";
export const EXPECTED_ENTITY_COUNT = 2029;

/**
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Report every failed invariant. An empty array means the catalog may render.
 * The cross-reference graph is not an input and is not required to carry a dataset id.
 * @param {unknown} manifest
 * @param {unknown} index
 * @returns {string[]}
 */
export function validateBoot(manifest, index) {
  const errors = [];

  if (!isObject(manifest)) {
    errors.push("manifest is missing or not an object");
  } else {
    if (manifest.schema_version !== EXPECTED_SCHEMA_VERSION) {
      errors.push(
        `manifest.schema_version is ${formatValue(manifest.schema_version)} (expected "${EXPECTED_SCHEMA_VERSION}")`,
      );
    }
    if (manifest.dataset_id !== EXPECTED_DATASET_ID) {
      errors.push(
        `manifest.dataset_id is ${formatValue(manifest.dataset_id)} (expected "${EXPECTED_DATASET_ID}")`,
      );
    }
    const counts = isObject(manifest.counts) ? manifest.counts : null;
    const count = counts ? counts.entities : undefined;
    if (count !== EXPECTED_ENTITY_COUNT) {
      errors.push(
        `manifest.counts.entities is ${formatValue(count)} (expected ${EXPECTED_ENTITY_COUNT})`,
      );
    }
  }

  if (!isObject(index)) {
    errors.push("index is missing or not an object");
  } else {
    if (index.schema_version !== EXPECTED_SCHEMA_VERSION) {
      errors.push(
        `index.schema_version is ${formatValue(index.schema_version)} (expected "${EXPECTED_SCHEMA_VERSION}")`,
      );
    }
    if (index.dataset_id !== EXPECTED_DATASET_ID) {
      errors.push(
        `index.dataset_id is ${formatValue(index.dataset_id)} (expected "${EXPECTED_DATASET_ID}")`,
      );
    }
    if (!Array.isArray(index.entities) || index.entities.length !== EXPECTED_ENTITY_COUNT) {
      const length = Array.isArray(index.entities) ? index.entities.length : "unavailable";
      errors.push(
        `index.entities.length is ${formatValue(length)} (expected ${EXPECTED_ENTITY_COUNT})`,
      );
    }
    if (Array.isArray(index.entities)) {
      const ids = new Set();
      for (const entity of index.entities) {
        if (entity && typeof entity.id === "string" && entity.id) ids.add(entity.id);
      }
      if (ids.size !== EXPECTED_ENTITY_COUNT) {
        errors.push(
          `index entity id set size is ${ids.size} (expected ${EXPECTED_ENTITY_COUNT})`,
        );
      }
    }
  }

  return errors;
}

/**
 * @param {unknown} manifest
 * @param {unknown} index
 * @returns {{ ok: true, errors: string[], entities: object[] } | { ok: false, errors: string[], entities: null }}
 */
export function bootViewModel(manifest, index) {
  const errors = validateBoot(manifest, index);
  if (errors.length) return { ok: false, errors, entities: null };
  return { ok: true, errors, entities: /** @type {{ entities: object[] }} */ (index).entities };
}

/**
 * @param {unknown} value
 * @returns {string}
 */
function formatValue(value) {
  if (value === undefined) return "missing";
  if (typeof value === "string") return JSON.stringify(value);
  return String(value);
}
