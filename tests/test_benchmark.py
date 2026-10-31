import json
from pathlib import Path

from beeja.benchmark import concept_match, run, score_case
from beeja.cli import main
from beeja.schema import Alternative, DecisionRecord, Evidence, Reason

ROOT = Path(__file__).resolve().parent.parent

GT = {
    "case": "t",
    "items": [
        {"id": "r1", "title": "YAML", "expected_status": ["rejected"], "match_all": [["yaml"]],
         "reason_any": ["typing"], "in_discussion": True},
        {"id": "r2", "title": "INI", "expected_status": ["rejected"], "match_all": [["ini"]], "in_discussion": True},
        {"id": "d1", "title": "as clause", "expected_status": ["deferred"], "match_all": [["unparenthesized"], ["as clause"]],
         "complement": {"match_all": [["require"], ["as clause"]], "status": ["accepted"]}, "in_discussion": False},
    ],
    "open": [{"id": "u1", "title": "JSON", "match_all": [["json"]], "expected_status": ["unresolved", "proposed"]}],
}


def rec(*alts, evidence=()):
    return DecisionRecord(id="dec-t", question="q", alternatives=list(alts), evidence=list(evidence))


def A(i, title, status, reasons=(), rt=None):
    return Alternative(alternative_id=i, title=title, status=status, reasons=list(reasons),
                       rejection_type=rt or ("explicit" if status == "rejected" else None))


def test_concept_match_word_start_and_groups():
    assert concept_match("Use a YAML file", [["yaml"]])
    assert not concept_match("Use a Yamlish file", [["yaml "]])
    assert not concept_match("mini config", [["ini"]])  # 'ini' must start a word
    assert concept_match("Allow X with an as clause", [["allow"], ["as clause"]])
    assert not concept_match("single shared buffer", [["buffer"]], exclude=["shared buffer"])


def test_scores_recovery_reasons_and_false_positives():
    ev = Evidence(evidence_id="ev-1", message_id="m1", quote="implicit typing is a trap", verification="exact")
    r = rec(
        A("a1", "Dedicated YAML file", "rejected", [Reason(text="implicit typing", evidence_ids=["ev-1"], supported=True)]),
        A("a2", "INI file", "unresolved"),  # found, but wrong status
        A("a3", "Always require parentheses with the as clause", "accepted"),  # complement of d1
        A("a4", "Use XML", "rejected"),  # not in ground truth -> unmatched negative
        A("a5", "JSON config", "rejected"),  # open question reported as decided
        evidence=[ev],
    )
    s = score_case(GT, r)
    items = {i["id"]: i for i in s["items"]}
    assert items["r1"]["recovered"] and items["r1"]["reason_recovered"] and items["r1"]["reason_supported"]
    assert not items["r2"]["recovered"] and items["r2"]["wrong_status_matches"] == ["a2:unresolved"]
    assert items["d1"]["recovered"] and items["d1"]["via"] == "complement"
    assert s["recovered"] == 2 and s["missed"] == 1 and s["recall"] == 0.667
    assert [u["alternative_id"] for u in s["unmatched_negative"]] == ["a4"]
    assert s["precision"] == 0.5  # a1 matched vs a4 unmatched (complement hits are not negative claims)
    assert s["status_checks"][0]["ok"] is False  # JSON was open but reported rejected
    assert s["evidence_verification_rate"] == 1.0


def test_empty_extraction_scores_zero():
    s = score_case(GT, rec())
    assert s["recovered"] == 0 and s["recall"] == 0.0 and s["precision"] is None


def test_ground_truth_never_reaches_extraction_inputs():
    """Ground truth lives beside, not inside, the inputs the extractor reads."""
    for gt_path in (ROOT / "benchmarks").glob("*/ground_truth.json"):
        gt = json.loads(gt_path.read_text())
        inp = (gt_path.parent / gt["input"]).read_text().lower()
        assert "ground_truth" not in inp
        # distinctive sentences from the final PEP's Rejected/Deferred Ideas must not be in the input
        for canary in gt["leak_canaries"]:
            assert canary.lower() not in inp


def test_cli_benchmark_on_committed_outputs(tmp_path, capsys):
    md = tmp_path / "r.md"
    code = main(["benchmark", "--dataset", str(ROOT / "benchmarks"), "--outputs", str(ROOT / "examples"),
                 "--markdown", str(md), "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["cases"] >= 1
    assert md.read_text().startswith("# Benchmark results")
    assert main(["benchmark", "--dataset", str(tmp_path / "nope")]) == 2


def test_run_reports_missing_outputs(tmp_path):
    case = tmp_path / "ds" / "c1"
    case.mkdir(parents=True)
    (case / "ground_truth.json").write_text(json.dumps({**GT, "case": "c1"}))
    rep = run(tmp_path / "ds", tmp_path / "outs")
    assert rep["missing_outputs"] == ["c1"] and rep["summary"] is None
