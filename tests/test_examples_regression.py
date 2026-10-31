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


def test_decision_store_matches_examples():
    """decisions/ (the store the CLI, MCP and diff advisories read) is exactly the example outputs."""
    store = {p.name: p.read_text() for p in (ROOT / "decisions").glob("*.json")}
    expected = {}
    for case in CASES:
        rec = storage.load_file(case / "decision.json")[0]
        expected[f"{rec.id}.json"] = (case / "decision.json").read_text()
    assert store == expected


@pytest.mark.parametrize("patch", sorted((ROOT / "examples" / "check-diff").glob("*.patch")), ids=lambda p: p.stem)
def test_committed_advisories_are_current(patch):
    from beeja.diff import check_diff, render_text

    report = check_diff(storage.load_dir(ROOT / "decisions"), patch.read_text())
    assert render_text(report) == patch.with_suffix(".advisory.txt").read_text()
    if patch.stem.startswith("unrelated"):
        assert report["advisories"] == []
    else:
        assert report["advisories"], f"{patch.name} should be linked to a stored decision"
