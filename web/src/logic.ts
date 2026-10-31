// Small pure helpers, unit-tested in tests/logic.test.mjs.

const ORDER = ["accepted", "rejected", "withdrawn", "deferred", "superseded", "unresolved", "proposed"];

export function statusOrder(s: string): number {
  const i = ORDER.indexOf(s);
  return i < 0 ? ORDER.length : i;
}

export const DECIDED = new Set(["accepted", "rejected", "withdrawn", "deferred", "superseded"]);

export function highlight(
  body: string,
  start: number | null,
  end: number | null,
): { before: string; match: string; after: string } | null {
  if (start == null || end == null || start < 0 || end > body.length || start >= end) return null;
  return { before: body.slice(0, start), match: body.slice(start, end), after: body.slice(end) };
}

// ---------------------------------------------------------------- light markdown

export type Line = { s: number; e: number }; // content range inside the original body
export type Block = { kind: "p" | "code" | "quote" | "list"; lines: Line[] };

const CODE_HINT = /^(\s{2,}\S|try:|except\b|else:|finally:|def |class |import |from \S+ import|>>>|\.\.\.$|[A-Za-z_][\w.]*\s*=\s|@\w|#include|\}|\{)/;

/** Split a message body into blocks while keeping exact character offsets, so a quote
 *  located by start/end offsets can still be highlighted after rendering. */
export function parseBlocks(body: string): Block[] {
  const raw: Line[][] = [];
  let cur: Line[] = [];
  let pos = 0;
  for (const text of body.split("\n")) {
    const s = pos;
    const e = pos + text.length;
    pos = e + 1;
    if (text.trim() === "") {
      if (cur.length) raw.push(cur);
      cur = [];
    } else cur.push({ s, e });
  }
  if (cur.length) raw.push(cur);
  return raw.map((lines) => {
    const texts = lines.map((l) => body.slice(l.s, l.e));
    if (texts.every((t) => t.trimStart().startsWith(">"))) {
      return {
        kind: "quote",
        lines: lines.map((l, i) => {
          const off = texts[i].indexOf(">") + 1 + (texts[i][texts[i].indexOf(">") + 1] === " " ? 1 : 0);
          return { s: l.s + off, e: l.e };
        }),
      };
    }
    if (texts.every((t) => /^\s*[-*] /.test(t))) {
      return { kind: "list", lines: lines.map((l, i) => ({ s: l.s + texts[i].search(/[-*] /) + 2, e: l.e })) };
    }
    const codeish = texts.filter((t) => CODE_HINT.test(t)).length;
    if (codeish > 0 && codeish >= Math.ceil(texts.length / 2) && (texts.length > 1 || /:$|^\s{2,}/.test(texts[0]))) {
      return { kind: "code", lines };
    }
    return { kind: "p", lines };
  });
}

/** Text pieces of [s, e) with the part overlapping [hs, he) flagged for highlighting. */
export function segments(body: string, s: number, e: number, hs: number | null, he: number | null) {
  if (hs == null || he == null || he <= s || hs >= e) return [{ text: body.slice(s, e), mark: false }];
  const a = Math.max(s, hs);
  const b = Math.min(e, he);
  return [
    { text: body.slice(s, a), mark: false },
    { text: body.slice(a, b), mark: true },
    { text: body.slice(b, e), mark: false },
  ].filter((x) => x.text.length > 0);
}

// ---------------------------------------------------------------- display helpers

export function hostOf(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname;
  } catch {
    return null;
  }
}

export function initials(name: string | null | undefined): string {
  const parts = (name || "?").replace(/[._-]+/g, " ").trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : parts[0]?.[1] ?? "")).toUpperCase();
}

export function hue(name: string | null | undefined): number {
  let h = 0;
  for (const ch of name || "?") h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

export function countBy(statuses: string[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const s of statuses) out[s] = (out[s] ?? 0) + 1;
  return out;
}
