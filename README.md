# beeja.md

Beeja recovers **what was decided, what was rejected, why, and where the proof is** from
scattered open-source engineering discussions (PEP threads, issues, PR reviews), and stores
the result as validated, evidence-linked JSON decision records.

Hacktoberfest HackDay Sonipat 2026 — Problem 06: Open Source Decision Intelligence and Knowledge Recovery.

> Status: Phase 1 (foundation). The schema, storage and `validate` command work today.
> Extraction, evidence verification, benchmark and retrieval land in later phases and this
> README is updated as each one is merged.

## Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Use

```bash
beeja --help
beeja schema                     # print the DecisionRecord JSON Schema
beeja validate decisions/        # validate one file or a directory of decision records
```

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
3.12 for every pull request to `main` and every push to `main`. CI never calls a live model.

## License

MIT — see [LICENSE](LICENSE).
