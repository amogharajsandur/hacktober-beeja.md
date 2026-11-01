# Local Gemma 4 E2B benchmark

Model: `gemma4:e2b` through local Ollama at `127.0.0.1:11434`.
Context: 8192; adapter temperature 0, seed 7, thinking disabled.
Beeja commit: 72c8f2f, with local test-isolation changes (no extraction changes).

All three benchmark inputs went through the existing extraction, outcome, evidence,
and support pipeline. Ground truth was used only by the scorer. Qwen examples and
benchmarks/RESULTS.md were left unchanged. This is one run per case, not a repeated
statistical comparison. The historical Qwen run is the existing baseline.

PEP 758 took 137.89 seconds; PEP 768 took 239.96 seconds. Synthetic timing was not captured.
Raw model exchanges are saved beside each decision. All three decisions passed
`beeja validate`. Separate store/ and viewer-data/ contain the generated records.
All four MCP tools completed over stdio against this separate store; CLI search
also completed (cli-search.json). No default model or production store was changed.

## Score again

From the project root:

```bash
.venv/bin/beeja benchmark --dataset benchmarks --outputs benchmark-runs/gemma4-e2b --markdown benchmark-runs/gemma4-e2b/RESULTS.md
```

## Findings

Gemma recovered 1/8 negative items versus 6/8 for the historical Qwen run.
Precision 1.0 reflects only one matched negative claim and is not broad accuracy.
Synthetic evidence failed because IDs such as `message q13` do not identify the
source message `q13`; Beeja withheld the associated outcomes. No ID normalization
or prompt tuning was applied to improve the score after the run.
