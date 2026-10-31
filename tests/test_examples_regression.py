"""Regression: replaying the recorded *real* qwen3:4b exchanges must reproduce the committed
example outputs exactly. This keeps CI deterministic and offline while proving the committed
examples came from the current pipeline."""

from pathlib import Path

import pytest

from beeja import storage
from beeja.extract import extract
from beeja.ingest import load_discussion
from beeja.model_adapter import ReplayProvider
from beeja.schema import check_integrity

ROOT = Path(__file__).resolve().parent.parent
CASES = sorted(p.parent for p in (ROOT / "examples").glob("*/model_exchanges.jsonl"))


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_example_replays_to_committed_output(case):
    src = (case / "input_path.txt").read_text().strip()
    d = load_discussion(ROOT / src)
    rec = extract(d, ReplayProvider(case / "model_exchanges.jsonl", model="qwen3:4b"))
    rec.metadata["model"] = "ollama:qwen3:4b"  # recorded from live Ollama; replay reports itself
    committed = (case / "decision.json").read_text()
    assert storage.dumps(rec) == committed
    assert check_integrity(rec) == []


def test_examples_exist():
    assert CASES, "examples/ must contain at least one real model run"
