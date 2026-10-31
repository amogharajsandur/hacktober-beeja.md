# Hackathon

## Team: beeja.md

- Amogha Raj Sandur
- Mohan Kumar C R
- Mohammed Yaseen

## Problem 6: Open Source Decision Intelligence and Knowledge Recovery

The project aims to recover knowledge from open-source project information and make it useful for understanding decisions.

## Selected model

**Qwen3 4B (`qwen3:4b`, Q4_K_M) via Ollama, running locally on the team laptop** (Apple M3, 8 GB).

- Used for: extracting alternatives, outcomes, arguments and verbatim evidence quotes from
  discussion chunks (`beeja extract`). Every quote it returns is checked against the source text
  by deterministic code before it is allowed into a decision record.
- Why: open weights (Apache-2.0), runs fully on the laptop as the participant guidelines require,
  supports JSON-schema constrained output through Ollama, and fits in 8 GB RAM with an 8k context.
  Measured on the PEP 758 thread: ~2,100-token prompts, ~45 s per chunk, 5 chunks.

Earlier plan (not used): Gemma 4 31B via Ollama Cloud. It was dropped because hosted third-party
inference is not one of the permitted environments (laptop or organiser model server) and the
31B model does not fit on the laptop. The model is configurable (`BEEJA_MODEL`), but `qwen3:4b`
is the only model the project has been run and tested with.
