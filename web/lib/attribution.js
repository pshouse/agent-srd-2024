/**
 * Official SRD 5.2 attribution. Curly quotes and the comma before the
 * license URL are part of the required footer sentence.
 */
export const ATTRIBUTION =
  "This work includes material from the System Reference Document 5.2 (\u201cSRD 5.2\u201d) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.";

/**
 * Split the attribution into text runs and the two official URLs.
 * Joining every value reproduces ATTRIBUTION exactly. A trailing period
 * stays in the following text run so the URL itself has no sentence punctuation.
 * @param {string} [text]
 * @returns {{ type: "text" | "url", value: string }[]}
 */
export function attributionPieces(text = ATTRIBUTION) {
  const pieces = [];
  const pattern = /https:\/\/[^\s]+/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    let value = match[0];
    let end = match.index + value.length;
    if (/[.,)]$/.test(value)) {
      value = value.slice(0, -1);
      end -= 1;
    }
    if (match.index > last) pieces.push({ type: "text", value: text.slice(last, match.index) });
    pieces.push({ type: "url", value });
    last = end;
  }
  if (last < text.length) pieces.push({ type: "text", value: text.slice(last) });
  return pieces;
}
