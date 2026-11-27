/**
 * Outgoing ids listed in a card's YAML frontmatter, in file order.
 * @param {unknown} markdown
 * @returns {string[]}
 */
export function parseCardRefs(markdown) {
  const text = String(markdown ?? "");
  if (!text.startsWith("---")) return [];
  const end = text.indexOf("\n---", 3);
  if (end < 0) return [];
  const refs = [];
  for (const line of text.slice(0, end).split("\n")) {
    const match = /^-\s+([a-z0-9-]+:[a-z0-9-]+)\s*$/.exec(line.trim());
    if (match) refs.push(match[1]);
  }
  return refs;
}
