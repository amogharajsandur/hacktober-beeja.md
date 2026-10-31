// Transpile src/logic.ts with the project's own TypeScript compiler and import the result.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync, readdirSync } from "node:fs";
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
  assert.ok(readdirSync(dir).length === index.length + 1);
});
