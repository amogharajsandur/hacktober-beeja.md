// Small pure helpers, unit-tested in tests/logic.test.mjs.

const ORDER = ["accepted", "rejected", "withdrawn", "deferred", "superseded", "unresolved", "proposed"];

export function statusOrder(s: string): number {
  const i = ORDER.indexOf(s);
  return i < 0 ? ORDER.length : i;
}

export function highlight(
  body: string,
  start: number | null,
  end: number | null,
): { before: string; match: string; after: string } | null {
  if (start == null || end == null || start < 0 || end > body.length || start >= end) return null;
  return { before: body.slice(0, start), match: body.slice(start, end), after: body.slice(end) };
}
