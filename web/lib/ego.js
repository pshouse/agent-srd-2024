/** Deterministic ego-graph selection and layout. No rendering, no I/O. */

export const GRAPH_NODE_CAP = 80;

/**
 * @param {Array<[string, string, string, unknown]>} edges
 */
export function indexAdjacency(edges) {
  const outgoing = new Map();
  const incoming = new Map();
  const degree = new Map();
  for (const edge of edges) {
    const src = edge[0];
    const dst = edge[1];
    const rel = edge[2];
    if (src === dst) continue;
    push(outgoing, src, { id: dst, rel });
    push(incoming, dst, { id: src, rel });
    degree.set(src, (degree.get(src) || 0) + 1);
    degree.set(dst, (degree.get(dst) || 0) + 1);
  }
  return { outgoing, incoming, degree };
}

function push(map, key, value) {
  let list = map.get(key);
  if (!list) {
    list = [];
    map.set(key, list);
  }
  list.push(value);
}

/**
 * Root plus at most 79 neighbours. Outgoing and mutual links come first,
 * ordered by hop, edge kind, then degree. Incoming links fill the rest
 * round-robin by type so one kind cannot take the whole cap.
 * @param {ReturnType<typeof indexAdjacency>} adj
 * @param {Map<string, { id: string, type?: string, name?: string, tier?: number }>} byId
 * @param {string} rootId
 * @param {{ hops?: number, direction?: string }} [options]
 */
export function selectEgo(adj, byId, rootId, options = {}) {
  const hops = options.hops === 2 ? 2 : 1;
  const direction = options.direction === "out" || options.direction === "in" ? options.direction : "both";
  const root = byId.get(rootId) || { id: rootId, type: "", name: rootId, tier: 3 };
  const directOut = collect(adj.outgoing, rootId);
  const directIn = collect(adj.incoming, rootId);
  const discovered = new Map();

  if (direction !== "in") addSide(discovered, directOut, 1, "out", byId, adj.degree);
  if (direction !== "out") addSide(discovered, directIn, 1, "in", byId, adj.degree);
  for (const item of discovered.values()) {
    if (item.out && item.in) item.side = "mutual";
  }

  if (hops === 2) {
    const hop1 = [...discovered.values()];
    for (const item of hop1) {
      if (item.side !== "in") addSide(discovered, collect(adj.outgoing, item.id), 2, "out", byId, adj.degree, rootId);
      if (item.side !== "out") addSide(discovered, collect(adj.incoming, item.id), 2, "in", byId, adj.degree, rootId);
    }
    for (const item of discovered.values()) {
      if (item.out && item.in) item.side = "mutual";
    }
  }

  const pool = [...discovered.values()].filter((item) => item.id !== rootId);
  const first = pool.filter((item) => item.side !== "in");
  const inbound = pool.filter((item) => item.side === "in");
  first.sort(compareNeighbor);
  const picked = first.slice(0, GRAPH_NODE_CAP - 1);
  if (picked.length < GRAPH_NODE_CAP - 1) {
    picked.push(...roundRobin(inbound, GRAPH_NODE_CAP - 1 - picked.length));
  }
  const pickedIds = new Set(picked.map((item) => item.id));
  const hidden = pool.filter((item) => !pickedIds.has(item.id)).length;
  return {
    root: { id: root.id, name: root.name || root.id, type: root.type || "", tier: root.tier || 3 },
    neighbors: picked,
    total: pool.length,
    shown: picked.length,
    hidden,
    cap: GRAPH_NODE_CAP,
  };
}

function collect(map, id) {
  const byTarget = new Map();
  for (const edge of map.get(id) || []) {
    let rels = byTarget.get(edge.id);
    if (!rels) {
      rels = [];
      byTarget.set(edge.id, rels);
    }
    rels.push(edge.rel);
  }
  const rows = [];
  for (const [target, rels] of byTarget) {
    rels.sort();
    rows.push({ id: target, rel: rels[0] });
  }
  rows.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return rows;
}

function addSide(discovered, rows, hop, side, byId, degree, rootId) {
  for (const row of rows) {
    if (row.id === rootId) continue;
    let item = discovered.get(row.id);
    if (!item) {
      const entity = byId.get(row.id);
      item = {
        id: row.id,
        name: entity && entity.name ? entity.name : row.id,
        type: entity && entity.type ? entity.type : row.id.split(":")[0],
        tier: entity && entity.tier ? entity.tier : 3,
        hop,
        rel: row.rel,
        degree: degree.get(row.id) || 0,
        side,
        out: side !== "in",
        in: side === "in",
      };
      discovered.set(row.id, item);
    } else if (hop < item.hop) {
      item.hop = hop;
      item.rel = row.rel;
      item.side = side;
    }
    if (side === "in") item.in = true;
    else item.out = true;
  }
}

function compareNeighbor(a, b) {
  if (a.hop !== b.hop) return a.hop - b.hop;
  if (a.rel < b.rel) return -1;
  if (a.rel > b.rel) return 1;
  if (a.degree !== b.degree) return a.degree - b.degree;
  if (a.id < b.id) return -1;
  if (a.id > b.id) return 1;
  return 0;
}

function roundRobin(items, limit) {
  const groups = new Map();
  for (const item of items) {
    const key = item.type || "";
    let list = groups.get(key);
    if (!list) {
      list = [];
      groups.set(key, list);
    }
    list.push(item);
  }
  for (const list of groups.values()) list.sort(compareNeighbor);
  const keys = [...groups.keys()].sort();
  const picked = [];
  let index = 0;
  while (picked.length < limit) {
    let progressed = false;
    for (const key of keys) {
      const list = groups.get(key);
      if (index < list.length) {
        picked.push(list[index]);
        progressed = true;
        if (picked.length >= limit) break;
      }
    }
    if (!progressed) break;
    index += 1;
  }
  return picked;
}

/**
 * Positions are a pure function of the ordered neighbour list.
 * @param {{ root: { id: string }, neighbors: { id: string, side: string }[] }} selection
 */
export function layoutEgo(selection) {
  const cx = 470;
  const cy = 300;
  const nodes = [{ id: selection.root.id, x: cx, y: cy, side: "root" }];
  const right = selection.neighbors.filter((item) => item.side !== "in");
  const left = selection.neighbors.filter((item) => item.side === "in");
  nodes.push(...placeArc(right, cx, cy, 0));
  nodes.push(...placeArc(left, cx, cy, Math.PI));
  const edges = selection.neighbors.map((item) => {
    const placed = nodes.find((node) => node.id === item.id);
    return { source: selection.root.id, target: item.id, rel: item.rel, side: item.side, x1: cx, y1: cy, x2: placed.x, y2: placed.y };
  });
  return { width: 940, height: 600, nodes, edges };
}

function placeArc(items, cx, cy, base) {
  const count = items.length;
  if (!count) return [];
  if (count <= 24) {
    const side = Math.cos(base) >= 0 ? 1 : -1;
    const x = cx + side * 240;
    const gap = Math.min(40, 480 / count);
    const start = cy - ((count - 1) * gap) / 2;
    return items.map((item, index) => ({
      id: item.id,
      x,
      y: Math.round((start + index * gap) * 100) / 100,
      side: item.side,
    }));
  }
  const spread = Math.min(2.4, Math.max(0.4, count * 0.08));
  return items.map((item, index) => {
    const t = count === 1 ? 0.5 : index / (count - 1);
    const angle = base - spread / 2 + t * spread;
    const radius = index % 2 === 0 ? 168 : 248;
    return {
      id: item.id,
      x: Math.round((cx + Math.cos(angle) * radius) * 100) / 100,
      y: Math.round((cy + Math.sin(angle) * radius) * 100) / 100,
      side: item.side,
    };
  });
}
