# Model comparison

Three-case Beeja benchmark: two real Python discussions (PEP 758 and PEP 768),
plus one synthetic configuration discussion. Eight ground-truth rejected/deferred
items; one was never raised in the supplied discussion. Ground truth is used by
the scorer, not supplied to extraction.

| Metric | Qwen 3 4B (historical) | Gemma 4 E2B (local) | Gemma 4 31B Cloud |
|---|---:|---:|---:|
| Recovered items | 6/8 | 1/8 | 7/8 |
| Recall | 75.0% | 12.5% | 87.5% |
| Recall, items raised in source | 85.7% | 14.3% | 100% |
| Precision | 83.3% | 100% | 83.3% |
| F1 | 78.9% | 22.2% | 85.3% |
| Evidence verification | 85.6% | 58.6% | 98.8% |
| Correct rejection reasons | 4 | 0 | 5 |
| Reasons backed by verified evidence | 4 | 0 | 5 |
| Accepted/open checks passed | 3/3 | 2/3 | 3/3 |

These are single runs on a small dataset, not general model rankings. Qwen uses
the previously committed examples; Gemma runs use commit 72c8f2f's extraction
pipeline. No prompt tuning or evidence-ID repair was applied. E2B's precision
comes from only one matched negative claim. Cloud precision includes one negative
claim unmatched by the benchmark ground truth; it requires manual review.

## Runtime and findings

Gemma 4 31B Cloud: synthetic 18.28 seconds, PEP 758 18.02 seconds, PEP 768
36.65 seconds; total 72.95 seconds. E2B: PEP 758 137.89 seconds, PEP 768
239.96 seconds; synthetic timing was not captured. Historical Qwen runtime is
not measured here. Cloud versus local timing is not a hardware-controlled comparison.

E2B returned synthetic evidence IDs such as `message q13` rather than `q13`.
All 15 synthetic references failed verification; Beeja withheld the outcomes.
Cloud recovered every ground-truth item actually raised in the supplied threads.
All six new decision records passed schema/integrity validation.

## Reproduce

Keep existing Qwen artifacts in examples/ and benchmarks/RESULTS.md unchanged.
Configure Ollama locally and authenticate its cloud model if required. From the
project root, choose a fresh output directory and run (model calls may incur cost):

```bash
MODEL=gemma4:31b-cloud
RUN=benchmark-runs/new-run
for CASE in synthetic-config-format pep-0758 pep-0768; do
  INPUT="benchmarks/$CASE/input/discourse_topic.json"
  if [ "$CASE" = synthetic-config-format ]; then
    INPUT="benchmarks/$CASE/input/discussion.txt"
  fi
  BEEJA_PROVIDER=ollama BEEJA_MODEL="$MODEL" \
    BEEJA_BASE_URL=http://127.0.0.1:11434 BEEJA_NUM_CTX=8192 \
    .venv/bin/beeja extract -i "$INPUT" \
      -o "$RUN/$CASE/decision.json" --record "$RUN/$CASE/model_exchanges.jsonl"
done
.venv/bin/beeja benchmark --dataset benchmarks --outputs "$RUN" --markdown "$RUN/RESULTS.md"
```

Use `MODEL=gemma4:e2b` for the local model. The adapter used temperature 0,
seed 7, thinking disabled and context 8192. Each saved case includes the raw
exchanges, output record and input path. JSON and Markdown reports are supplied
for both Gemma runs. These records are separate from the application's default store.
