import markdownit from "../vendor/markdown-it/markdown-it.esm.min.mjs";

const CARD_HREF = /^(?:(?:\.\.\/)+|\.\/)?([a-z0-9-]+)\/([a-z0-9-]+)\.md(#[^?\s]*)?$/i;

const DISALLOWED_TAG =
  /<\s*(script|iframe|object|embed|form|base|meta|link|style|svg|math|frame|frameset|applet)\b/i;
const EVENT_ATTR = /<[^>]+\s(on[a-z]+)\s*=/i;
const BAD_URL =
  /\s(?:href|src|xlink:href|action|formaction)\s*=\s*(?:"[^"]*(?:javascript|vbscript|data)\s*:|'[^']*(?:javascript|vbscript|data)\s*:|(?:javascript|vbscript|data)\s*:)/i;

/**
 * Rewrite a card-relative Markdown link to a hash route.
 * `../damage-types/fire.md` becomes `#/e/damage-types:fire`.
 * @param {string} href
 * @returns {string}
 */
export function rewriteCardHref(href) {
  if (typeof href !== "string") return href;
  const match = CARD_HREF.exec(href.trim());
  if (!match) return href;
  return `#/e/${match[1].toLowerCase()}:${match[2].toLowerCase()}`;
}

/**
 * @param {string} html
 * @returns {string[]}
 */
export function liveHtmlHazards(html) {
  const hazards = [];
  if (DISALLOWED_TAG.test(html)) hazards.push("disallowed-tag");
  if (EVENT_ATTR.test(html)) hazards.push("event-handler");
  if (BAD_URL.test(html)) hazards.push("dangerous-url");
  return hazards;
}

/**
 * Drop a leading YAML frontmatter block. The visible card is the Markdown body.
 * @param {unknown} markdown
 * @returns {string}
 */
export function stripFrontmatter(markdown) {
  const text = String(markdown ?? "").replace(/^\uFEFF/, "");
  if (!text.startsWith("---")) return text;
  const end = text.indexOf("\n---", 3);
  if (end === -1) return text;
  return text.slice(end + 4).replace(/^[ \t]*\r?\n/, "");
}

let renderer;

function createRenderer() {
  const md = markdownit({
    html: false,
    xhtmlOut: false,
    breaks: true,
    linkify: false,
    typographer: false,
  });
  md.enable("table");
  const allow = md.validateLink.bind(md);
  md.validateLink = (url) => {
    const stripped = String(url)
      .trim()
      .toLowerCase()
      .replace(/[\u0000-\u001f\s]+/g, "");
    if (
      stripped.startsWith("javascript:") ||
      stripped.startsWith("vbscript:") ||
      stripped.startsWith("data:")
    ) {
      return false;
    }
    return allow(url);
  };
  const fallback =
    md.renderer.rules.link_open ||
    function renderLinkOpen(tokens, idx, options, env, self) {
      return self.renderToken(tokens, idx, options);
    };
  md.renderer.rules.link_open = function linkOpen(tokens, idx, options, env, self) {
    const token = tokens[idx];
    const href = token.attrGet("href");
    if (href != null) {
      const rewritten = rewriteCardHref(href);
      token.attrSet("href", rewritten);
      const id = rewritten.startsWith("#/e/") ? decodeHashId(rewritten.slice(4)) : "";
      const info = id && env && typeof env.lookup === "function" ? env.lookup(id) : null;
      if (info) {
        token.attrSet("class", info.family ? `xref ${info.family}` : "xref");
        if (info.title) token.attrSet("title", info.title);
      }
    }
    return fallback(tokens, idx, options, env, self);
  };
  return md;
}

function getRenderer() {
  if (!renderer) renderer = createRenderer();
  return renderer;
}

export { parseCardRefs } from "./cardmeta.js";

/**
 * @param {string} id
 */
function decodeHashId(id) {
  try {
    return decodeURIComponent(id);
  } catch {
    return id;
  }
}

/**
 * Render card Markdown. HTML in the source is escaped (`html: false`).
 * Pipe tables stay enabled. Unsafe renderer output is refused.
 * `lookup(id)` may add a family class and a title tooltip on rewritten links.
 * @param {string} source
 * @param {(id: string) => { family?: string, title?: string } | null} [lookup]
 * @returns {{ ok: boolean, html: string, text: string, hazards: string[] }}
 */
export function renderCardMarkdown(source, lookup) {
  const text = stripFrontmatter(source);
  const html = getRenderer().render(text, { lookup: lookup || null });
  const hazards = liveHtmlHazards(html);
  if (hazards.length) return { ok: false, html: "", text, hazards };
  return { ok: true, html, text: "", hazards };
}
