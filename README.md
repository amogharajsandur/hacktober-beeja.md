# beeja.md

Beeja recovers **what was decided, what was rejected, why, and where the proof is** from
scattered open-source engineering discussions (PEP threads, issues, PR reviews), and stores
the result as validated, evidence-linked JSON decision records.

Hacktoberfest HackDay Sonipat 2026 — Problem 06: Open Source Decision Intelligence and Knowledge Recovery.

## Why not just search, or ask an LLM for a summary?

* **Search finds messages, not decisions.** The outcome of "should we do X?" is usually spread
  over a proposal, a dozen objections and one sentence from the PEP author forty posts later.
* **A summary flattens alternatives** and silently turns "nobody answered the objection" into
  "rejected". Beeja keeps every alternative separate, with its own status, and treats silence
  as `unresolved`.
* **An LLM answer has no proof.** Every Beeja outcome and reason points at a passage that was
  found verbatim in the cited message and then checked to actually say that. What fails those
  checks is kept for review, flagged, and never shown as proof.

## Architecture

```
 discussion (.json Discourse / GitHub / native, .txt/.md)        beeja fetch-discourse (optional)
        │
        ▼
  ingest ──► chunk (message boundaries) ──► qwen3:4b extraction per chunk ──► cluster alternatives
                                                   (Ollama, JSON schema)          │
        ┌──────────────────────────────────────────────────────────────────────┘
        ▼
  reply-tree outcome pass (qwen3:4b, unresolved alternatives only)
        ▼
  evidence resolution (deterministic: exact / approximate / not_found / missing_message)
        ▼
  status resolution (deterministic: only verified claims count; silence ≠ rejection)
        ▼
  support check (qwen3:4b, can only withhold or downgrade) ──► DecisionRecord (Pydantic, JSON)
                                                                        │
                       decisions/  (one deterministic JSON file per decision)
            ┌────────────────┬───────────────┬──────────────┬─────────────┬──────────────┐
         CLI search/get   why-not      check-diff      MCP server      web viewer     benchmark
```

One engine, several interfaces: the CLI, MCP server, diff advisory and viewer all read the same
records through the same functions. Everything after extraction is offline and model-free.

## Results at a glance

From the committed real runs (`benchmarks/RESULTS.md`, 3 cases, 8 ground-truth items — small,
indicative only):

| metric | value |
|---|---|
| rejected/deferred/withdrawn ideas recovered | 6 / 8 (recall 0.75; 0.857 on ideas actually raised in the threads; F1 0.789) |
| precision of negative claims | 0.833 (1 unmatched: PEP 758 "backport to 3.12/3.13", a real idea the PEP doesn't list) |
| correct reasons recovered, all backed by verified quotes | 4 |
| quotes found verbatim in the cited message | 86% (the rest are flagged, never used as proof) |
| open questions correctly *not* reported as decided (synthetic) | 3 / 3 |

## Quick start

```bash
make setup      # venv + pip install -e ".[dev]" + npm ci for the viewer
make demo       # offline: why-not, diff advisories, benchmark on the committed real outputs
make viewer     # decision dossier at http://localhost:5173
make test       # lint + pytest + viewer build/tests (what CI runs)
```

Manual install: Python 3.11+, `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`.
Search, `get`, `why-not`, `check-diff`, the benchmark, the viewer and the MCP server need **no
model**; only `beeja extract` calls one.

## Model runtime

The declared and tested model is **Qwen3 4B (`qwen3:4b`) on a local [Ollama](https://ollama.com)
server** (see `HACKATHON.md`). Every committed example was produced by it.

```bash
ollama pull qwen3:4b && ollama serve
beeja extract -i benchmarks/synthetic-config-format/input/discussion.txt -o out/demo.json
```

On an 8 GB laptop the default 8k context can cause swapping; use a smaller context and chunks:
`BEEJA_NUM_CTX=4096 beeja extract --chunk-chars 4000 ...`.

### Any provider: just add an API key

No key means local Ollama. Put **any** provider's key in `BEEJA_API_KEY` (in your shell or a `.env`
file in the working directory) and Beeja detects the provider from the key prefix and uses that
provider's default model:

```bash
echo 'BEEJA_API_KEY=gsk_...' > .env          # Groq key -> llama-3.3-70b-versatile
beeja extract -i benchmarks/synthetic-config-format/input/discussion.txt -o out/demo.json

beeja providers                               # all presets, their default models, and the active one
beeja extract --provider openrouter --model qwen/qwen3-32b -i ... -o ...
beeja extract --provider custom --base-url http://server:8000/v1 --model qwen3-4b -i ... -o ...
```

| provider (`--provider` / `BEEJA_PROVIDER`) | detected from key | default model | open weights |
|---|---|---|---|
| `ollama` (default, local) | – (no key) | `qwen3:4b` | yes |
| `ollama-cloud` | – (`OLLAMA_API_KEY`) | `gemma4:31b` | yes |
| `groq` | `gsk_` | `llama-3.3-70b-versatile` | yes |
| `openrouter` | `sk-or-` | `meta-llama/llama-3.3-70b-instruct` | yes (by default) |
| `huggingface` | `hf_` | `Qwen/Qwen3-32B` | yes |
| `together`, `fireworks`, `cerebras`, `deepseek`, `mistral` | `fw_`, `csk-` (others: set `--provider`) | Llama 3.3 70B / DeepSeek / Mistral Small | yes |
| `openai` | `sk-`, `sk-proj-` | `gpt-4o-mini` | **no** |
| `anthropic` | `sk-ant-` | `claude-sonnet-5-5` | **no** |
| `gemini` | `AIza` | `gemini-2.5-flash` | **no** |
| `xai` | `xai-` | `grok-3-mini` | **no** |
| `custom` / `openai-compatible` | – | set `--model` | any OpenAI-compatible server (vLLM, llama.cpp, LM Studio, LiteLLM, organiser server) |

Each preset also reads its usual key variable (`GROQ_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, ...). `--model` / `BEEJA_MODEL` and `--base-url` / `BEEJA_BASE_URL` override any
preset. Keys are only read from the environment or `.env` (git-ignored), never passed on the
command line, and never sent to local servers.

Hosted endpoints often ignore Ollama's JSON-schema `format`, so for every non-local provider
Beeja states the schema in the prompt, asks for structured output where the API supports it
(falling back automatically when it does not), parses replies leniently (code fences, `<think>`
blocks, surrounding prose) and makes one repair attempt. Every result still goes through the
same quote verification and support check, whichever model produced it.

> **Hackathon rules:** closed-model providers (marked **no** above) work, but Beeja prints a
> warning: the guidelines score closed models 0 for open-source AI use. The guidelines allow
> open-weight models "on your laptop or on
> our model server", and closed-model APIs score 0. Ollama Cloud hosts open-weight models but is
> a third-party service that is not explicitly approved. Get organiser approval before using
> it for the submission, and update `HACKATHON.md` and `examples/` if the declared model changes.

| Variable | Default | Meaning |
|---|---|---|
| `BEEJA_PROVIDER` | `auto` | `auto` (local Ollama, or detected from the key), a preset name, or `replay` |
| `BEEJA_MODEL` | `qwen3:4b` | model tag / name |
| `BEEJA_BASE_URL` / `OLLAMA_HOST` | `http://localhost:11434` | server URL (a bare host means port 11434) |
| `BEEJA_API_KEY` / `OLLAMA_API_KEY` | – | bearer token for remote servers |
| `BEEJA_SCHEMA_IN_PROMPT` | `auto` | on for `*-cloud` models, non-local hosts and `openai` |
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
6. **Reply-tree outcome pass** (`beeja/outcome.py`) — chunking can separate a proposal from the
   reply that settles it. For each alternative still without a verified outcome, Beeja rebuilds
   that alternative's own conversation from the reply structure (messages that mention or argue
   about it, two levels of replies, and later messages by the thread's original author that use
   its wording) and asks the model what happened to it. The answer is only a *claim*: it goes
   through the same quote verification, status resolution and support check. Disable with
   `--no-outcome-pass`.
7. **Support check** (second model pass, `beeja/support.py`) — for every accepted / rejected /
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

`.github/workflows/ci.yml` runs on every pull request to `main` and every push to `main`:
lint, CLI and `beeja-mcp` smoke tests and the full test suite on Python 3.11 and 3.12, plus a
`viewer-build` job that regenerates the viewer data from the committed records (must be
unchanged), then type-checks, builds and tests the React viewer. CI never calls a live model:
unit tests use scripted responses, and `tests/test_examples_regression.py` replays the recorded
real `qwen3:4b` exchanges in `examples/` and requires the committed outputs to be reproduced
byte-for-byte.

## Examples

`examples/<case>/` holds the input path, the raw recorded model exchanges
(`model_exchanges.jsonl`, real `qwen3:4b` output with timing stats) and the resulting
`decision.json`. `examples/check-diff/` holds sample patches (written by the team) with the
actual advisories produced against `decisions/`; tests require both to stay current.

## Known limitations

* **Small model, small benchmark.** `qwen3:4b` sometimes names an idea from the opposite side
  (PEP 758's `as`-clause idea) or attaches a general "PEP accepted" sentence to sub-features; the
  support check withholds what it cannot confirm, which costs recall. Three cases and eight
  ground-truth items cannot support strong claims.
* **Memorisation.** Verified quotes show an outcome is *in the text*; they do not prove the
  model did not also remember the PEP. The synthetic case is the control.
* **Matching is keyword-based.** Benchmark matching uses transparent concept groups; clustering
  of repeated mentions is title-based; search ranking is lexical.
* **One decision record per discussion**, with several alternatives inside it.
* **No decision authority.** `decision_authority` stays `null`: forum `admin` flags are not
  treated as proof of who decided.
* **Code anchors come only from the discussion text.** No repository checkout, symbol lineage or
  rename tracking.
* The viewer and MCP server are read-only by design; nothing is posted to real projects.

## Roadmap (not implemented)

GitHub App PR comments, tree-sitter symbol lineage across renames, closed-PR / reverted-commit
ingestion with diff similarity, executable premises ("rejected because Python 3.9 is supported"
→ revisit when that changes), cross-platform contributor identity, supersession timelines,
human review workflow for records, larger multi-PEP benchmark.

## License

MIT — see [LICENSE](LICENSE).
