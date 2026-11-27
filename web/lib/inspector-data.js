/** Lazy inspector fetches. Not imported by the explorer boot path. */

const GRAPH_URL = new URL("../../dist/web/graph.json", import.meta.url);

/**
 * @param {string} type
 * @returns {URL | null}
 */
export function shardUrl(type) {
  if (!/^[a-z0-9-]+$/.test(type)) return null;
  return new URL(`../../dist/web/types/${type}.json`, import.meta.url);
}

let graphPromise = null;
const shardPromises = new Map();

export function loadGraph() {
  if (!graphPromise) {
    graphPromise = fetch(GRAPH_URL).then((res) => {
      if (!res.ok) throw new Error(`graph HTTP ${res.status}`);
      return res.json();
    });
  }
  return graphPromise;
}

/**
 * @param {string} type
 */
export function loadShard(type) {
  if (shardPromises.has(type)) return shardPromises.get(type);
  const url = shardUrl(type);
  if (!url) return Promise.reject(new Error("bad type"));
  const promise = fetch(url).then((res) => {
    if (!res.ok) throw new Error(`shard HTTP ${res.status}`);
    return res.json();
  });
  shardPromises.set(type, promise);
  return promise;
}
