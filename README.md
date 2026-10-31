# beeja.md

Beeja recovers **what was decided, what was rejected, why, and where the proof is** from
scattered open-source engineering discussions (PEP threads, issues, PR reviews), and stores
the result as validated, evidence-linked JSON decision records.

Hacktoberfest HackDay Sonipat 2026 — Problem 06: Open Source Decision Intelligence and Knowledge Recovery.

> Status: Phase 2. Ingestion, chunked extraction with a real local open-weight model, quote
> verification and `validate` work today. Benchmark, retrieval and diff advisories land in later
> phases; this README is updated as each one is merged.

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
`decision.json`.

## License

MIT — see [LICENSE](LICENSE).
