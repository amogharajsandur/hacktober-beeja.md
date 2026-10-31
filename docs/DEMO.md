# Demo script (about 6 minutes)

Everything below runs on the laptop. Steps 2–3 use the live model; if it is slow, use the
recorded real run instead (step 3b) and say so: it is a *previous real run*, not fresh inference.

1. **Problem.** Open <https://discuss.python.org/t/73969> (PEP 768, 59 posts). "Why does the
   debugger write a *file path* into the buffer instead of the code itself? The answer is in
   post 54 of 59."

2. **Model is local.** `ollama ps` / `ollama list` → `qwen3:4b`.

3. **Extract (live, ~2 min for the synthetic case, ~7 min for PEP 768).**
   ```bash
   beeja extract -i benchmarks/synthetic-config-format/input/discussion.txt -o out/demo.json --record out/demo.jsonl
   ```
   3b. Offline replay of the committed real run:
   ```bash
   beeja extract -i benchmarks/pep-0768/input/discourse_topic.json --replay examples/pep-0768/model_exchanges.jsonl -q | head -40
   ```

4. **Proof.** `beeja why-not "YAML config" --store decisions` → rejected/explicit, the verified
   implicit-typing reason, and the exact message it came from.

5. **Dossier.** `cd web && npm run dev` → open the quillfmt decision → click the YAML outcome chip
   → the sentence is highlighted inside maya's message q13 (verification `exact`, support `supports`).
   Show an alternative whose outcome was withheld by the support check (PEP 758 `as` clause).

6. **Future code changes.**
   ```bash
   beeja check-diff examples/check-diff/related_quillfmt_yaml.patch --store decisions
   beeja check-diff examples/check-diff/unrelated_docs.patch --store decisions   # nothing
   ```

7. **Agents.** `beeja-mcp --store decisions` (read-only MCP: search_decisions, get_decision,
   why_not, check_diff).

8. **Honest numbers.** `beeja benchmark` → 6/8 recovered, precision 0.833, 86% quotes verified;
   explain the misses (one PEP idea never appears in its thread; PEP 758's `as`-clause idea was
   named back to front by qwen3:4b and withheld by the support check rather than guessed).
