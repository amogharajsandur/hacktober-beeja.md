# `beeja check-diff` examples

The three patches here are **sample changes written by the team** to exercise the advisory; they
are not real upstream PRs. The decision store they are checked against (`decisions/`) is the
real `qwen3:4b` output committed under `examples/`.

| patch | expected | output |
|---|---|---|
| `related_debugger_shared_buffer.patch` — replaces the per-thread debugger buffer in `PyThreadState` with one shared buffer | PEP 768 advisory | `related_debugger_shared_buffer.advisory.txt` / `.json` |
| `related_quillfmt_yaml.patch` — adds YAML loading next to `pyproject.toml` | quillfmt advisory (YAML rejected) | `related_quillfmt_yaml.advisory.txt` / `.json` |
| `unrelated_docs.patch` — a docs typo fix that happens to mention "buffer", "thread" and "debugger" | no advisory | `unrelated_docs.advisory.txt` / `.json` |

Regenerate:

```bash
for p in examples/check-diff/*.patch; do
  beeja check-diff "$p" --store decisions > "${p%.patch}.advisory.txt"
  beeja check-diff "$p" --store decisions --json > "${p%.patch}.advisory.json"
done
```
