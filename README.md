# beeja.md

Beeja recovers **what was decided, what was rejected, why, and where the proof is** from
scattered open-source engineering discussions (PEP threads, issues, PR reviews), and stores
the result as validated, evidence-linked JSON decision records.

Hacktoberfest HackDay Sonipat 2026 — Problem 06: Open Source Decision Intelligence and Knowledge Recovery.

> Status: Phase 6. Ingestion, chunked extraction with a real local open-weight model, quote
> verification, a support-checking second pass, a PEP benchmark with withheld ground truth,
> offline search / get / "why not X?", PR-diff advisories, a read-only decision viewer and a read-only MCP server work today.

## Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Model runtime

Beeja uses **Qwen3 4B (`qwen3:4b`) through a local [Ollama](https://ollama.com) server**. No API
keys and no hosted service are needed.

```bash
ollama pull qwen3:4b          # ~2.5 GB, fits an 8 GB laptop
ollama serve                  # if it is not already running
```

| Variable | Default | Meaning |
|---|---|---|
| `BEEJA_MODEL` | `qwen3:4b` | Ollama model tag |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server (laptop or organiser server) |
| `BEEJA_PROVIDER` | `ollama` | `ollama` for live inference, `replay` to replay a recording |
| `BEEJA_NUM_CTX` | `8192` | context window requested from Ollama |
| `BEEJA_TIMEOUT` | `600` | seconds per model call |

## Use

```bash
beeja --help

# fetch a public discussion once (optional; everything after this is offline)
beeja fetch-discourse 66453 -o benchmarks/pep-0758/input/discourse_topic.json

# extract a decision record with the live model, recording every model exchange
beeja extract -i benchmarks/pep-0758/input/discourse_topic.json \
  -o out/pep-0758.json --record out/pep-0758.jsonl --store decisions/

# re-run the same extraction offline from the recording (deterministic)
beeja extract -i benchmarks/pep-0758/input/discourse_topic.json --replay out/pep-0758.jsonl

beeja validate decisions/        # schema + integrity checks
beeja schema                     # print the DecisionRecord JSON Schema
```

Inputs: Discourse topic JSON, GitHub comment lists, Beeja-native JSON, or `.txt`/`.md` files
whose messages start with `[id] author (role): text`.

## How extraction works

1. **Ingest** — normalise messages, keep IDs, authors, timestamps, URLs and reply links
   (`reply_to_post_number` for Discourse). Missing metadata stays `null`. Each body is hashed.
2. **Chunk** — split on message boundaries (~7,000 characters per chunk) so long threads never
   get collapsed into one lossy summary.
3. **Model extraction** — for each chunk the model returns alternatives, a status claim with a
   verbatim quote, and for/against arguments with quotes, using a JSON schema enforced by Ollama.
   The alternatives found so far are passed forward so repeated mentions are merged.
4. **Evidence resolution** — every quote is located in the cited message (normalising whitespace,
   smart quotes, backticks, elisions). Results: `exact`, `approximate` (the stored quote is then
   replaced by the real source span), `not_found`, `missing_message`.
5. **Status resolution** (deterministic) — only status claims whose quote verifies count; the
   latest verified claim by message position wins; criticism without a stated decision is
   `unresolved`, never `rejected`. A rejection is `explicit` only if the verified passage itself
   states an outcome, otherwise `inferred` with `low` confidence.
6. **Support check** (second model pass, `beeja/support.py`) — for every accepted / rejected /
   withdrawn / deferred outcome, the model sees only the claim and the surrounding source text
   and must say whether the passage actually states that outcome for that alternative. It can
   only weaken a finding: `does_not_support` sends the outcome back to `unresolved` with a note,
   `weak` lowers confidence. It never flips or upgrades an outcome. Up to two reasons per
   negative outcome are checked the same way. Disable with `--no-support-check`.

Three separate questions are kept apart in every `Evidence` item: does the cited message exist,
does the passage occur in it (`verification`), and does it support the claim (`support`).

## Retrieval and diff advisories (offline, no model)

`decisions/` is the canonical store (one JSON file per decision). It currently holds the three
real `qwen3:4b` records from `examples/`.

```bash
beeja search "why not a single shared buffer" --store decisions          # ranked alternatives
beeja search "yaml" --store decisions --negative-only --json
beeja get dec-73969-pep-768-safe-external-debugger-interface-for-cpy#alt-8 --store decisions
beeja why-not "YAML config" --store decisions       # rejected/withdrawn/deferred + verified reasons
beeja check-diff examples/check-diff/related_quillfmt_yaml.patch --store decisions
git diff main | beeja check-diff - --store decisions --json
```

**Code anchors** are extracted deterministically from the discussion text: file paths, C/Python
symbols (`PyThreadState`, `sys.remote_exec`), GitHub PR/issue refs (`gh-12345`). Each records
which messages mention it. Links into other repositories and very generic names (`sys.path`)
are skipped.

**`check-diff`** reports a decision only when the change has an *explicit* link to it: a changed
file matches an anchored path, a changed line uses an anchored symbol, or a changed line refers to
an anchored file. Topic keywords alone never trigger an advisory. Every advisory says what matched
and from which message, lists the accepted / rejected alternatives with a verified quote and
link, and marks the alternatives whose wording the change itself uses. It is informational only:
Beeja never blocks, rejects or comments on a PR. See `examples/check-diff/` for a related C
change, a related Python change and an unrelated docs change (no advisory).

## Decision dossier (web viewer)

A read-only React + TypeScript viewer in `web/` renders the real records: decision list with
filter, one page per decision with status, alternatives, reasons, confidence, notes, code
anchors and sources. Clicking any evidence chip shows the quote **highlighted inside the original
source message**, with its verification and support verdicts; unverified quotes are struck
through and never presented as proof. Every page says the content is AI-extracted and not
human-reviewed. The short narrative is Portable Text rendered with `@portabletext/react`; it is
generated deterministically from the structured record (facts stay in the JSON, not in rich text).

```bash
beeja export-web --examples examples --out web/public/data   # records + only the cited messages
cd web && npm ci && npm run dev                               # http://localhost:5173
npm run build && npm test                                     # what CI runs
```

The viewer is optional: every record is equally available through `beeja search` / `beeja get`.

Exports: `beeja export-madr <dec-id> --store decisions` renders a basic MADR document
(unverified evidence is labelled).

## MCP server for coding agents

`beeja-mcp` is a local, read-only [Model Context Protocol](https://modelcontextprotocol.io)
server over stdio, built on the official Python SDK (`mcp` 2.x, `MCPServer`). It calls the same
functions as the CLI.

| tool | returns |
|---|---|
| `search_decisions(query, limit=5, negative_only=false)` | compact hits: IDs, title, status, one reason, verified evidence refs |
| `get_decision(ref)` | one decision (`dec-id`) or one alternative (`dec-id#alt-2`) with its evidence |
| `why_not(idea)` | rejected / withdrawn / deferred alternatives with their verified reasons |
| `check_diff(diff)` | advisories for a unified diff (same rules as `beeja check-diff`) |

All tools are annotated read-only; nothing writes to disk, calls a model or uses the network.
Responses label discussion-derived text as untrusted data so agents treat it as quotes, not
instructions.

```bash
pip install -e ".[mcp]"
beeja-mcp --store decisions        # stdio; BEEJA_STORE also works
```

Example client configuration (Claude Code / Claude Desktop style `mcpServers` entry):

```json
{
  "mcpServers": {
    "beeja": {
      "command": "beeja-mcp",
      "args": ["--store", "/absolute/path/to/hacktober-beeja.md/decisions"]
    }
  }
}
```

`tests/test_mcp.py` exercises every tool through the SDK client both in-process and by
launching `python -m beeja.mcp_server` as a real stdio subprocess.

## Benchmark

```bash
beeja benchmark --dataset benchmarks --outputs examples
```

Scores the committed real-model outputs against ground truth taken from the final PEPs, which
the model never sees. See [benchmarks/README.md](benchmarks/README.md) for the cases, the
matching rule (fixed before scoring) and limitations, and
[benchmarks/RESULTS.md](benchmarks/RESULTS.md) for the actual numbers.

`python -m beeja ...` is equivalent to `beeja ...`.

Exit codes: `0` success, `1` invalid content, `2` usage/input error, `3` model error.

## Data model

Facts live in typed Pydantic models (`beeja/schema.py`): `SourceMessage`, `Discussion`,
`DecisionRecord`, `Alternative`, `Reason`, `Evidence`, `CodeAnchor`. Alternative statuses are
`proposed`, `accepted`, `rejected`, `withdrawn`, `deferred`, `unresolved` and `superseded`, and are
not interchangeable. Missing source metadata stays `null`; it is never invented.

Each decision is stored as one deterministic JSON file (sorted keys, stable IDs).

## Tests and CI

```bash
ruff check .
pytest -q
```

`.github/workflows/ci.yml` runs lint, a CLI smoke test and the test suite on Python 3.11 and
3.12 for every pull request to `main` and every push to `main`. CI never calls a live model:
unit tests use scripted responses, and `tests/test_examples_regression.py` replays the recorded
real `qwen3:4b` exchanges in `examples/` and requires the committed outputs to be reproduced
byte-for-byte.

## Examples

`examples/<case>/` holds the input path, the raw recorded model exchanges
(`model_exchanges.jsonl`, real `qwen3:4b` output with timing stats) and the resulting
`decision.json`. `examples/check-diff/` holds sample patches (written by the team) with the
actual advisories produced against `decisions/`; tests require both to stay current.

## License

MIT — see [LICENSE](LICENSE).
