# Benchmark: recovering rejected ideas without the final PEP

The P6 evaluation gives a system historical PEP discussions **without** the final PEP and asks
whether it can recover the rejected ideas and why they were rejected. This directory reproduces
that setup on a small scale.

## Cases

| case | input (what the model sees) | ground truth (scoring only) |
|---|---|---|
| `pep-0758` | all 52 posts of [discuss.python.org/t/66453](https://discuss.python.org/t/66453) | final PEP 758 *Rejected Ideas* + *Deferred Ideas* |
| `pep-0768` | all 59 posts of [discuss.python.org/t/73969](https://discuss.python.org/t/73969) | final PEP 768 *Rejected Ideas* |
| `synthetic-config-format` | a 20-message discussion **written by the team** | known by construction |

Each case has `input/` and `ground_truth.json`. The extractor only ever reads `input/`.
`tests/test_benchmark.py` checks that distinctive sentences from the final PEP sections
(`leak_canaries`) do not occur in the inputs.

The synthetic case exists because a model may have memorised well-known PEP outcomes. Its
project ("quillfmt") is fictional, so outcomes can only come from the text. It also covers the
negative cases: a withdrawn idea, a deferred idea, two similar-but-distinct TOML options, an
open question with conflicting opinions (JSON), and a proposal nobody decided (env vars).

Not every PEP lists all rejected ideas, and not every listed idea was raised in the thread.
PEP 758's "mixed parenthesized and unparenthesized syntax" never appears in its 52 posts, so it
is marked `in_discussion: false` and reported separately rather than hidden.

## Matching rule (fixed before scoring)

An extracted alternative matches a ground-truth item when its title + description contains at
least one term from **every** concept group in `match_all` and none of `exclude_any`. Terms
match at a word start, case-insensitively. This demands the specific technical idea (e.g.
*single/shared* **and** *buffer*), not just a shared keyword.

* **Recovered** — a matching alternative has one of the item's `expected_status` values, or an
  *accepted* alternative matches the item's `complement` (the same decision stated from the
  other side, e.g. "always require parentheses with `as`" for the deferred "allow unparenthesized
  with `as`").
* **Wrong status** — matched, but the status is not acceptable (e.g. `unresolved`). Counts as missed.
* **Unmatched negative claim** — an alternative Beeja calls rejected/withdrawn/deferred/superseded
  that matches no ground-truth entry. Counted against precision, but listed for manual review:
  some are genuine ideas the PEP simply does not list.
* **Reason recovered** — one of the matched alternative's reasons (or its evidence quote)
  contains a `reason_any` term; **reason verified** additionally requires that the reason's
  evidence quote was found in the cited message.

Precision = matched negative claims / all negative claims. Recall = recovered items / items.

## Run

```bash
# score the committed real-model outputs in examples/
beeja benchmark --dataset benchmarks --outputs examples --markdown benchmarks/RESULTS.md

# regenerate outputs with the live model first (about 4-7 minutes per PEP on an M3 laptop)
beeja extract -i benchmarks/pep-0768/input/discourse_topic.json \
  -o examples/pep-0768/decision.json --record examples/pep-0768/model_exchanges.jsonl
```

Results from the committed run are in [RESULTS.md](RESULTS.md). The numbers are computed from
the actual outputs; with 3 cases and 8 ground-truth items they are indicative, not statistically
meaningful.

## Limitations

* Keyword concept groups are a transparent but imperfect proxy for semantic equivalence; each
  match is listed so a human can audit it.
* A verified quote proves the passage exists. The support pass (a second model call) checks
  whether it backs the claim; neither proves the model ignored memorised knowledge.
