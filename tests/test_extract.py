"""Pipeline tests with deterministic scripted model responses (no live model in CI)."""

import json

from beeja.cli import main
from beeja.extract import CHUNK_SCHEMA, chunk_discussion, extract, title_similarity
from beeja.model_adapter import RecordingProvider, ReplayProvider, exchange_key
from beeja.schema import check_integrity


def alt(title, status, smid="", squote="", args=(), same_as="", proposed_in=""):
    return {
        "title": title, "same_as": same_as, "description": title, "proposed_in": proposed_in,
        "proponents": [], "status": status, "status_message_id": smid, "status_quote": squote,
        "arguments": [{"stance": s, "text": t, "message_id": m, "quote": q} for s, t, m, q in args],
    }


TABS_RESPONSE = {
    "question": "How should generated config files be indented?",
    "alternatives": [
        alt("Indent with tabs", "withdrawn", "m5", "I withdraw the tabs idea",
            [("against", "tabs break YAML", "m2", "break our YAML output, which forbids tab indentation")],
            proposed_in="m1"),
        alt("Two-space indentation", "accepted", "m6", "Decision: we go with two spaces",
            [("for", "matches upstream style", "m3", "It matches the upstream YAML style guide")], proposed_in="m3"),
        alt("Configurable --indent flag", "deferred", "m6",
            "The configurable --indent flag is deferred to a later release", proposed_in="m4"),
        alt("Reformat existing files", "unresolved", "", "", proposed_in="m7"),
    ],
}


def test_full_pipeline_statuses(tabs_discussion, scripted):
    rec = extract(tabs_discussion, scripted([TABS_RESPONSE]))
    by = {a.title: a for a in rec.alternatives}
    assert by["Indent with tabs"].status == "withdrawn"
    assert by["Two-space indentation"].status == "accepted"
    assert by["Configurable --indent flag"].status == "deferred"
    assert by["Reformat existing files"].status == "unresolved"
    assert by["Reformat existing files"].confidence == "uncertain"
    assert rec.status == "partially_decided"
    assert check_integrity(rec) == []
    assert rec.metadata["evidence_verified"] == rec.metadata["evidence_total"]
    # reasons for the withdrawn idea are the objections, each tied to verified evidence
    r = by["Indent with tabs"].reasons[0]
    assert r.supported and rec.evidence_by_id()[r.evidence_ids[0]].message_id == "m2"


def test_objection_without_decision_is_not_rejection(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [
        alt("Indent with tabs", "unresolved",
            args=[("against", "breaks yaml", "m2", "Tabs render inconsistently across editors")]),
    ]}
    a = extract(tabs_discussion, scripted([resp])).alternatives[0]
    assert a.status == "unresolved" and a.rejection_type is None


def test_model_claims_rejection_with_fabricated_quote(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [
        alt("Indent with tabs", "rejected", "m6", "Tabs are hereby rejected by the steering council forever",
            [("against", "breaks yaml", "m2", "Tabs render inconsistently across editors")]),
    ]}
    rec = extract(tabs_discussion, scripted([resp]))
    a = rec.alternatives[0]
    assert a.status == "unresolved"  # an unverifiable outcome is never accepted
    assert any("could not be verified" in n for n in a.notes)
    assert any(e.verification == "not_found" for e in rec.evidence)


def test_wrong_message_id_is_flagged(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [alt("Indent with tabs", "withdrawn", "m99", "I withdraw the tabs idea")]}
    rec = extract(tabs_discussion, scripted([resp]))
    assert rec.alternatives[0].status == "proposed"
    assert rec.evidence[0].verification == "missing_message"


def test_rejection_without_decision_wording_is_inferred(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [
        alt("Indent with tabs", "rejected", "m2", "Tabs render inconsistently across editors")]}
    a = extract(tabs_discussion, scripted([resp])).alternatives[0]
    assert a.status == "rejected" and a.rejection_type == "inferred" and a.confidence == "low"


def test_chunking_and_cross_chunk_clustering(tabs_discussion, scripted):
    chunks = chunk_discussion(tabs_discussion, max_chars=250)
    assert len(chunks) > 2
    ids = [m.message_id for c in chunks for m in c.messages]
    assert sorted(set(ids), key=ids.index) == [m.message_id for m in tabs_discussion.messages]
    first = {"question": "q", "alternatives": [alt("Indent with tabs", "proposed", proposed_in="m1")]}
    later = {"question": "", "alternatives": [
        alt("tabs", "withdrawn", "m5", "I withdraw the tabs idea", same_as="alt-1"),
        alt("Two-space indentation", "accepted", "m6", "we go with two spaces"),
    ]}
    rest = [{"question": "", "alternatives": []}] * (len(chunks) - 2)
    prov = scripted([first, later, *rest])
    rec = extract(tabs_discussion, prov, max_chars=250)
    assert [a.title for a in rec.alternatives] == ["Indent with tabs", "Two-space indentation"]
    assert rec.alternatives[0].status == "withdrawn"
    assert "alt-1: Indent with tabs" in prov.prompts[1]  # known alternatives passed forward


def test_latest_verified_claim_wins_by_message_position(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [
        alt("Configurable --indent flag", "deferred", "m6", "deferred to a later release"),
        alt("Configurable --indent flag", "proposed", "m4", "make the indentation width configurable"),
    ]}
    a = extract(tabs_discussion, scripted([resp])).alternatives
    assert len(a) == 1 and a[0].status == "deferred"


def test_similar_but_distinct_titles_not_merged():
    assert title_similarity("Allow unparenthesized except with as", "Allow mixed parenthesized and unparenthesized") < 0.6
    assert title_similarity("Indent with tabs", "Indent using tabs") >= 0.6


def test_prompt_marks_discussion_as_untrusted(tabs_discussion, scripted):
    from beeja.extract import SYSTEM

    assert "untrusted" in SYSTEM
    prov = scripted([{"question": "q", "alternatives": []}])
    extract(tabs_discussion, prov)
    assert "<<<" in prov.prompts[0] and ">>>" in prov.prompts[0]


def test_record_then_replay_is_identical(tmp_path, tabs_discussion, scripted):
    class FakeOllama:
        model = "qwen3:4b"
        name = "ollama:qwen3:4b"
        calls = []

        def generate_json(self, s, p, sc):
            return TABS_RESPONSE

    path = tmp_path / "rec.jsonl"
    live = extract(tabs_discussion, RecordingProvider(FakeOllama(), path))
    replayed = extract(tabs_discussion, ReplayProvider(path, model="qwen3:4b"))
    assert live.model_dump() | {"metadata": None} == replayed.model_dump() | {"metadata": None}
    line = json.loads(path.read_text().splitlines()[0])
    assert len(line["key"]) == 64 and line["model"] == "qwen3:4b"
    assert exchange_key("qwen3:4b", "a", "b", CHUNK_SCHEMA) != exchange_key("qwen3:4b", "a", "c", CHUNK_SCHEMA)


def test_replay_unknown_prompt_errors(tmp_path, tabs_discussion):
    import pytest

    from beeja.model_adapter import ModelError

    p = tmp_path / "empty.jsonl"
    p.write_text("")
    with pytest.raises(ModelError):
        extract(tabs_discussion, ReplayProvider(p))


def test_cli_extract_replay(tmp_path, tabs_discussion, monkeypatch, capsys):
    class FakeOllama:
        model = "qwen3:4b"
        name = "ollama:qwen3:4b"
        calls = []

        def generate_json(self, s, p, sc):
            return TABS_RESPONSE

    from tests.conftest import FIXTURES

    rec = tmp_path / "rec.jsonl"
    extract(tabs_discussion, RecordingProvider(FakeOllama(), rec))
    out = tmp_path / "out.json"
    code = main(["extract", "-i", str(FIXTURES / "tabs_vs_spaces.txt"), "-o", str(out), "--replay", str(rec), "-q"])
    assert code == 0
    assert main(["validate", str(out)]) == 0
    assert main(["extract", "-i", str(tmp_path / "missing.txt"), "--replay", str(rec)]) == 2
