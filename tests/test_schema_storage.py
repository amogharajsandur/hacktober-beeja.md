import json

import pytest
from pydantic import ValidationError

from beeja import storage
from beeja.cli import main
from beeja.schema import (
    Alternative,
    DecisionRecord,
    Evidence,
    Reason,
    check_integrity,
)


def _record(**kw):
    ev = Evidence(evidence_id="ev-1", message_id="m1", quote="tabs render inconsistently")
    alt = Alternative(
        alternative_id="alt-1",
        title="Use tabs",
        status="rejected",
        rejection_type="explicit",
        reasons=[Reason(text="inconsistent rendering", evidence_ids=["ev-1"])],
    )
    base = dict(id="dec-test", question="Tabs or spaces?", alternatives=[alt], evidence=[ev])
    base.update(kw)
    return DecisionRecord(**base)


def test_roundtrip_is_deterministic(tmp_path):
    rec = _record()
    p = storage.save(rec, tmp_path)
    first = p.read_text()
    again = storage.dumps(storage.load_file(p)[0])
    assert first == again
    assert json.loads(first)["alternatives"][0]["status"] == "rejected"


def test_unknown_status_rejected():
    with pytest.raises(ValidationError):
        Alternative(alternative_id="a", title="x", status="maybe")


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        DecisionRecord(id="dec-x", question="q", invented_field=1)


def test_missing_metadata_stays_null():
    ev = Evidence(evidence_id="ev-1", message_id="m1", quote="q")
    assert ev.author is None and ev.source_url is None and ev.created_at is None
    assert ev.verification == "unchecked"


def test_integrity_flags_dangling_evidence():
    rec = _record(evidence=[])
    problems = check_integrity(rec)
    assert any("unknown evidence 'ev-1'" in p for p in problems)


def test_integrity_requires_rejection_type():
    rec = _record()
    rec.alternatives[0].rejection_type = None
    assert any("without a rejection_type" in p for p in check_integrity(rec))


def test_unsafe_ids_refused(tmp_path):
    with pytest.raises(storage.StorageError):
        storage.save(_record(id="../escape"), tmp_path)


def test_cli_validate(tmp_path, capsys):
    good = storage.save(_record(), tmp_path / "good")
    assert main(["validate", str(good), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "dec-x", "question": "q", "status": "nope"}))
    assert main(["validate", str(bad)]) == 1
    assert main(["validate", str(tmp_path / "missing.json")]) == 2


def test_cli_schema(capsys):
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "alternatives" in schema["properties"]
