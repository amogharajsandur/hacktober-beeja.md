// Transpile src/logic.ts with the project's own TypeScript compiler and import the result.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import ts from "typescript";

const src = readFileSync(new URL("../src/logic.ts", import.meta.url), "utf8");
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } });
const mod = await import("data:text/javascript," + encodeURIComponent(js.outputText));

test("highlight splits around offsets", () => {
  assert.deepEqual(mod.highlight("abcdef", 2, 4), { before: "ab", match: "cd", after: "ef" });
});

test("highlight refuses missing or invalid offsets", () => {
  assert.equal(mod.highlight("abc", null, 2), null);
  assert.equal(mod.highlight("abc", 2, 9), null);
  assert.equal(mod.highlight("abc", 2, 2), null);
});

test("status order puts decided outcomes first", () => {
  assert.ok(mod.statusOrder("accepted") < mod.statusOrder("rejected"));
  assert.ok(mod.statusOrder("rejected") < mod.statusOrder("unresolved"));
});

test("exported viewer data references real records", () => {
  const dir = new URL("../public/data/", import.meta.url);
  assert.ok(existsSync(dir), "run `beeja export-web` first");
  const index = JSON.parse(readFileSync(new URL("index.json", dir), "utf8"));
  assert.ok(index.length >= 1);
  for (const e of index) {
    const p = JSON.parse(readFileSync(new URL(`${e.id}.json`, dir), "utf8"));
    assert.equal(p.record.id, e.id);
    for (const ev of p.record.evidence) {
      if (ev.verification === "exact" && ev.start_offset != null) {
        const body = p.messages[ev.message_id].body;
        assert.ok(body.slice(ev.start_offset, ev.end_offset).length > 0);
      }
    }
  }
  assert.equal(new Set(index.map((entry) => entry.id)).size, index.length);
});

test("parseBlocks keeps exact offsets for paragraphs, code, quotes and lists", () => {
  const body = "Intro line\nsecond line\n\ntry:\n    ...\nexcept A, B as e:\n    ...\n\n> quoted text\n\n- one\n- two";
  const blocks = mod.parseBlocks(body);
  assert.deepEqual(blocks.map((b) => b.kind), ["p", "code", "quote", "list"]);
  const q = blocks[2].lines[0];
  assert.equal(body.slice(q.s, q.e), "quoted text");
  assert.equal(body.slice(blocks[3].lines[1].s, blocks[3].lines[1].e), "two");
  for (const b of blocks) for (const l of b.lines) assert.ok(l.s >= 0 && l.e <= body.length && l.s <= l.e);
});

test("segments marks only the overlap with the highlight", () => {
  const body = "abcdefghij";
  assert.deepEqual(mod.segments(body, 2, 8, 4, 6), [
    { text: "cd", mark: false }, { text: "ef", mark: true }, { text: "gh", mark: false },
  ]);
  assert.deepEqual(mod.segments(body, 0, 3, 5, 9), [{ text: "abc", mark: false }]);
  assert.deepEqual(mod.segments(body, 0, 3, null, null), [{ text: "abc", mark: false }]);
});

test("display helpers", () => {
  assert.equal(mod.hostOf("https://discuss.python.org/t/x/1"), "discuss.python.org");
  assert.equal(mod.hostOf("not a url"), null);
  assert.equal(mod.initials("pablogsal"), "PA");
  assert.equal(mod.initials("Gregory P. Smith"), "GS");
  assert.deepEqual(mod.countBy(["a", "b", "a"]), { a: 2, b: 1 });
});
