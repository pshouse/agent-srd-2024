/**
 * Browser port of the resolved-bundle packer (SPEC §8, runtime get_bundle).
 * Token counts use Math.ceil(text.length / 4). Attribution is not part of
 * this text and does not affect the footer; `srd get` does not append it.
 */

export const ANTI_HUB_TOKEN_THRESHOLD = 400;
export const INBOUND_LIMIT = 10;
export const HEADER_RESERVE = 40;

const TYPE_TIERS = {
  "ability-scores": 1,
  conditions: 1,
  "damage-types": 1,
  feats: 1,
  "magic-schools": 1,
  poisons: 1,
  skills: 1,
  spells: 1,
  traits: 1,
  "weapon-mastery-properties": 1,
  "weapon-properties": 1,
  alignments: 2,
  backgrounds: 2,
  classes: 2,
  equipment: 2,
  features: 2,
  languages: 2,
  "magic-items": 2,
  monsters: 2,
  species: 2,
  subclasses: 2,
  subspecies: 2,
  "equipment-categories": 3,
  levels: 3,
  proficiencies: 3,
};

/**
 * @param {string} text
 * @returns {number}
 */
export function estTokens(text) {
  if (!text) return 0;
  return Math.ceil(String(text).length / 4);
}

/**
 * @param {string} entityType
 * @returns {number}
 */
export function tierForType(entityType) {
  return TYPE_TIERS[entityType] ?? 3;
}

/**
 * @param {number} value
 * @param {number} min
 * @param {number} max
 */
function clamp(value, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return min;
  return Math.max(min, Math.min(max, Math.trunc(n)));
}

/**
 * @param {Array<[string, string, string, unknown]>} edges
 * @param {Map<string, { type?: string, name?: string, summary?: string }>} byId
 */
export function indexGraph(edges, byId) {
  const outgoing = new Map();
  const incoming = new Map();
  for (const edge of edges) {
    const src = edge[0];
    const dst = edge[1];
    const rel = edge[2];
    const meta = edge[3] ?? null;
    let out = outgoing.get(src);
    if (!out) {
      out = [];
      outgoing.set(src, out);
    }
    out.push({ dst, rel, meta });
    let inc = incoming.get(dst);
    if (!inc) {
      inc = [];
      incoming.set(dst, inc);
    }
    const ent = byId.get(src);
    inc.push({
      src,
      rel,
      meta,
      type: ent ? ent.type : "",
      name: ent ? ent.name : "",
      summary: ent ? ent.summary : "",
    });
  }
  for (const list of incoming.values()) {
    list.sort((a, b) => {
      if (a.rel < b.rel) return -1;
      if (a.rel > b.rel) return 1;
      if (a.src < b.src) return -1;
      if (a.src > b.src) return 1;
      return 0;
    });
  }
  return { outgoing, incoming };
}

/**
 * @param {string} rootId
 * @param {number} depth
 * @param {number} estimated
 * @param {number} maxTokens
 * @param {number} fullCount
 * @param {number} summaryCount
 * @param {number} omittedCount
 */
export function formatBundleFooter(rootId, depth, estimated, maxTokens, fullCount, summaryCount, omittedCount) {
  return (
    `<!-- bundle: id=${rootId} depth=${depth} ` +
    `tokens~=${estimated}/${maxTokens} ` +
    `full=${fullCount} summary=${summaryCount} omitted=${omittedCount} -->`
  );
}

/**
 * @param {object} payload
 * @returns {string}
 */
export function renderBundle(payload) {
  if (!payload || payload.status !== "ok") return renderUnresolved(payload || {});
  const root = payload.root;
  const dependencies = payload.dependencies || [];
  const inbound = payload.inbound || [];
  const stats = payload.stats;
  const body = buildBundleMarkdownParts(
    root.card_body_md,
    dependencies,
    inbound,
    Boolean(payload.include_inbound),
    Number(stats.inbound_total ?? inbound.length),
  );
  const footer = formatBundleFooter(
    root.id,
    Number(stats.depth),
    Number(stats.estimated_tokens),
    Number(stats.max_tokens),
    Number(stats.full_count),
    Number(stats.summary_count),
    (stats.omitted_ids || []).length,
  );
  return `${body}\n\n${footer}`;
}

/**
 * Clipboard text: the CLI markdown, a blank line, then the attribution.
 * The attribution is not part of `srd get` and is not inside the token footer.
 * @param {string} bundleMarkdown
 * @param {string} attribution
 */
export function copyAgentContext(bundleMarkdown, attribution) {
  return `${bundleMarkdown}\n\n${attribution}`;
}

/**
 * @param {object} payload
 */
function renderUnresolved(payload) {
  const status = payload.status;
  const query = payload.query || "";
  if (status === "ambiguous") {
    const lines = [
      `# Ambiguous Identifier: \`${query}\``,
      "",
      "Multiple SRD entities match this identifier. Re-query using one of these canonical IDs:",
      "",
    ];
    for (const cand of payload.candidates || []) {
      lines.push(`- **${cand.name}** (\`${cand.id}\`, ${cand.type}): ${cand.summary}`);
    }
    return lines.join("\n");
  }
  if (status === "not_found") {
    const lines = [`# Entity Not Found: \`${query}\``];
    const suggestions = payload.suggestions || [];
    if (suggestions.length) {
      lines.push("", "Did you mean one of these?", "");
      for (const sug of suggestions) {
        lines.push(`- **${sug.name}** (\`${sug.id}\`, ${sug.type}): ${sug.summary}`);
      }
    } else {
      lines.push("", "No matching entities found.");
    }
    return lines.join("\n");
  }
  return "";
}

function buildBundleMarkdownParts(rootBody, dependencies, inbound, includeInbound, inboundTotal) {
  const sections = [String(rootBody || "").trimEnd()];
  const fullDeps = dependencies.filter((dep) => dep.detail === "full");
  const summaryDeps = dependencies.filter((dep) => dep.detail === "summary");
  if (fullDeps.length || summaryDeps.length) {
    const refParts = ["## Referenced Rules"];
    for (const dep of fullDeps) {
      const rels = dep.rels.join(", ");
      const heading = `### ${dep.name} (\`${dep.id}\`) — via ${rels}`;
      const body = String(dep.card_body_md || "").trimEnd();
      refParts.push(`${heading}\n\n${body}`);
    }
    if (summaryDeps.length) {
      const sumLines = ["### Summarized References"];
      for (const dep of summaryDeps) {
        const rels = dep.rels.join(", ");
        sumLines.push(`- **${dep.name}** (\`${dep.id}\`, via ${rels}): ${dep.summary}`);
      }
      refParts.push(sumLines.join("\n"));
    }
    sections.push(refParts.join("\n\n"));
  }
  if (includeInbound) {
    if (inbound.length) {
      const lines = [`## Referenced By (${inbound.length} of ${inboundTotal})`];
      for (const item of inbound) {
        lines.push(`- **${item.name}** (\`${item.id}\`, via ${item.rel}): ${item.summary}`);
      }
      sections.push(lines.join("\n"));
    } else {
      sections.push("## Referenced By\n\n*(none)*");
    }
  }
  return sections.join("\n\n");
}

/**
 * Ids whose card bodies the packer will measure: the root and every dependency.
 * @param {Map<string, {dst: string, rel: string}[]>} outgoing
 * @param {string} rootId
 * @param {number} depth
 * @param {string[] | null} relFilter
 */
export function expandOutgoing(outgoing, rootId, depth, relFilter) {
  const minHop = new Map([[rootId, 0]]);
  const depRels = new Map();
  let frontier = [rootId];
  for (let hop = 0; hop < depth; hop += 1) {
    if (!frontier.length) break;
    const nextHop = hop + 1;
    const nextSet = new Set();
    const edges = [];
    for (const src of frontier) {
      const list = outgoing.get(src) || [];
      for (const edge of list) {
        if (relFilter && !relFilter.includes(edge.rel)) continue;
        edges.push(edge);
      }
    }
    for (const edge of edges) {
      const dst = edge.dst;
      if (dst === rootId) continue;
      const existing = minHop.has(dst) ? minHop.get(dst) : null;
      if (existing !== null && existing < nextHop) continue;
      if (!minHop.has(dst)) {
        minHop.set(dst, nextHop);
        nextSet.add(dst);
      }
      let rels = depRels.get(dst);
      if (!rels) {
        rels = new Set();
        depRels.set(dst, rels);
      }
      rels.add(edge.rel);
    }
    frontier = [...nextSet].sort();
  }
  const depIds = [...minHop.keys()].filter((id) => id !== rootId);
  return { minHop, depRels, depIds };
}

function computeEstimated(body, meta) {
  let est = estTokens(body);
  for (let pass = 0; pass < 2; pass += 1) {
    const footer = formatBundleFooter(
      meta.rootId,
      meta.depth,
      est,
      meta.maxTokens,
      meta.fullCount,
      meta.summaryCount,
      meta.omittedCount,
    );
    est = estTokens(`${body}\n\n${footer}`);
  }
  return est;
}

/**
 * @param {{ catalog: import("./resolve.js").ReturnType extends never ? any : any, outgoing: Map<string, {dst: string, rel: string, meta: unknown}[]>, incoming: Map<string, object[]>, bodyOf: (id: string) => string }} world
 * @param {string} identifier
 * @param {{ depth?: number, maxTokens?: number, includeInbound?: boolean, rels?: string[] | null }} [options]
 */
export function packBundle(world, identifier, options = {}) {
  const depth = clamp(options.depth ?? 1, 0, 2);
  const maxTokens = clamp(options.maxTokens ?? 2000, 200, 16000);
  const includeInbound = Boolean(options.includeInbound);
  const relFilter = Array.isArray(options.rels)
    ? [...new Set(options.rels.map((rel) => String(rel).trim()).filter(Boolean))].sort()
    : null;

  const { resolveIdentifier } = world;
  const resolved = resolveIdentifier(world.catalog, identifier);
  if (resolved.status !== "ok") {
    return {
      status: resolved.status,
      query: resolved.query,
      candidates: resolved.candidates,
      suggestions: resolved.suggestions,
    };
  }

  const root = resolved.entity;
  const rootBody = world.bodyOf(root.id);
  const { minHop, depRels, depIds } = expandOutgoing(world.outgoing, root.id, depth, relFilter);
  depIds.sort((a, b) => {
    const hopDiff = minHop.get(a) - minHop.get(b);
    if (hopDiff) return hopDiff;
    const tierDiff = tierForType(world.catalog.byId.get(a).type) - tierForType(world.catalog.byId.get(b).type);
    if (tierDiff) return tierDiff;
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
  });

  const rootTokens = estTokens(rootBody);
  const budgetExceeded = rootTokens > maxTokens;
  let budgetLeft = Math.max(0, maxTokens - rootTokens - HEADER_RESERVE);
  const packed = [];
  const omitted = [];
  let truncated = false;
  let fullCount = 0;
  let summaryCount = 0;

  for (const depId of depIds) {
    const dep = world.catalog.byId.get(depId);
    const depTier = tierForType(dep.type);
    const rels = [...(depRels.get(depId) || [])].sort();
    const relsStr = rels.join(", ");
    const body = world.bodyOf(depId);
    const fullBlock = `### ${dep.name} (\`${dep.id}\`) — via ${relsStr}\n\n${body}`;
    const stubBlock = `- **${dep.name}** (\`${dep.id}\`, via ${relsStr}): ${dep.summary}`;
    const fullTokens = estTokens(fullBlock);
    const stubTokens = estTokens(stubBlock);
    const eligible = fullTokens <= budgetLeft && (depTier === 1 || dep.card_tokens_est <= ANTI_HUB_TOKEN_THRESHOLD);
    if (eligible) {
      budgetLeft -= fullTokens;
      fullCount += 1;
      packed.push({
        id: dep.id,
        type: dep.type,
        name: dep.name,
        hop: minHop.get(depId),
        rels,
        tier: depTier,
        detail: "full",
        summary: dep.summary,
        card_body_md: body,
        card_tokens_est: dep.card_tokens_est,
        tokens_est: fullTokens,
      });
    } else if (stubTokens <= budgetLeft) {
      budgetLeft -= stubTokens;
      summaryCount += 1;
      truncated = true;
      packed.push({
        id: dep.id,
        type: dep.type,
        name: dep.name,
        hop: minHop.get(depId),
        rels,
        tier: depTier,
        detail: "summary",
        summary: dep.summary,
        card_body_md: null,
        card_tokens_est: dep.card_tokens_est,
        tokens_est: stubTokens,
      });
    } else {
      omitted.push(dep.id);
      truncated = true;
    }
  }

  const inboundItems = [];
  let inboundTotal = 0;
  if (includeInbound) {
    const rows = world.incoming.get(root.id) || [];
    inboundTotal = rows.length;
    if (budgetLeft > 0) {
      for (const row of rows.slice(0, INBOUND_LIMIT)) {
        const stub = `- **${row.name}** (\`${row.src}\`, via ${row.rel}): ${row.summary}`;
        const tokens = estTokens(stub);
        if (tokens <= budgetLeft) {
          budgetLeft -= tokens;
          inboundItems.push({
            id: row.src,
            type: row.type,
            name: row.name,
            rel: row.rel,
            summary: row.summary,
          });
        } else {
          break;
        }
      }
    }
  }

  const statsDraft = {
    rootId: root.id,
    depth,
    maxTokens,
    fullCount,
    summaryCount,
    omittedCount: omitted.length,
  };
  const body = buildBundleMarkdownParts(rootBody, packed, inboundItems, includeInbound, inboundTotal);
  const estimated = computeEstimated(body, statsDraft);

  return {
    status: "ok",
    include_inbound: includeInbound,
    root: {
      id: root.id,
      type: root.type,
      slug: root.slug,
      name: root.name,
      summary: root.summary,
      card_body_md: rootBody,
      tokens_est: rootTokens,
      tier: tierForType(root.type),
      card_tokens_est: root.card_tokens_est,
    },
    dependencies: packed,
    inbound: inboundItems,
    stats: {
      depth,
      max_tokens: maxTokens,
      estimated_tokens: estimated,
      budget_exceeded: budgetExceeded,
      truncated,
      full_count: fullCount,
      summary_count: summaryCount,
      omitted_ids: omitted,
      inbound_total: inboundTotal,
    },
  };
}

/**
 * @param {unknown} graph
 * @param {{ counts?: { edges?: number } }} manifest
 * @returns {string[]}
 */
export function validateGraph(graph, manifest) {
  const errors = [];
  if (!graph || typeof graph !== "object" || Array.isArray(graph)) {
    errors.push("graph is missing or not an object");
    return errors;
  }
  if (graph.schema_version !== "1") {
    errors.push(`graph.schema_version is ${graph.schema_version === undefined ? "missing" : JSON.stringify(graph.schema_version)} (expected "1")`);
  }
  const nodes = graph.nodes && typeof graph.nodes === "object" && !Array.isArray(graph.nodes) ? graph.nodes : null;
  const nodeCount = nodes ? Object.keys(nodes).length : "unavailable";
  if (nodeCount !== 2029) errors.push(`graph node count is ${nodeCount} (expected 2029)`);
  const edgeCount = Array.isArray(graph.edges) ? graph.edges.length : "unavailable";
  const expectedEdges = manifest && manifest.counts ? manifest.counts.edges : undefined;
  if (edgeCount !== expectedEdges) {
    errors.push(`graph.edges.length is ${edgeCount} (expected ${expectedEdges})`);
  }
  return errors;
}
