"""Second-pass support checking can only weaken findings, never flip or upgrade them."""

from functools import partial

from beeja.extract import extract as _extract
from tests.test_extract import TABS_RESPONSE, alt

extract = partial(_extract, outcome_pass=False)


def verdicts(*vs):
    return [{"verdict": v, "stated_outcome": o, "explanation": "x"} if o else {"verdict": v, "explanation": "x"}
            for v, o in vs]


def _status(rec, title):
    return next(a for a in rec.alternatives if a.title == title)


def test_supported_claims_survive(tabs_discussion, scripted):
    # 3 terminal alternatives -> 3 status checks; withdrawn tabs has 1 verified reason -> 1 reason check;
    # deferred flag has no reasons.
    order = [("supports", "withdrawn"), ("supports", None), ("supports", "accepted"), ("supports", "deferred")]
    rec = extract(tabs_discussion, scripted([TABS_RESPONSE, *verdicts(*order)]))
    assert _status(rec, "Indent with tabs").status == "withdrawn"
    assert _status(rec, "Configurable --indent flag").status == "deferred"
    assert rec.metadata["support_checks"] == 4
    ev = rec.evidence_by_id()[_status(rec, "Indent with tabs").status_evidence[0]]
    assert ev.support == "supports"
    assert _status(rec, "Indent with tabs").status_support == "supports"


def test_shared_passage_keeps_weakest_verdict_but_per_claim_status(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [
        alt("Two-space indentation", "accepted", "m6", "Decision: we go with two spaces"),
        alt("Reformat existing files", "accepted", "m6", "Decision: we go with two spaces"),
    ]}
    rec = extract(tabs_discussion, scripted([resp, *verdicts(("supports", "accepted"), ("does_not_support", "none"))]))
    a, b = rec.alternatives
    assert a.status == "accepted" and a.status_support == "supports"
    assert b.status == "unresolved" and b.status_support == "does_not_support"
    assert rec.evidence_by_id()[a.status_evidence[0]].support == "does_not_support"


def test_unsupported_outcome_is_withheld(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [
        alt("Configurable --indent flag", "rejected", "m6", "The configurable --indent flag is deferred to a later release"),
    ]}
    rec = extract(tabs_discussion, scripted([resp, *verdicts(("does_not_support", "deferred"))]))
    a = rec.alternatives[0]
    assert a.status == "unresolved"  # the checker's 'deferred' reading is NOT adopted
    assert a.rejection_type is None and a.confidence == "uncertain"
    assert any("does not support 'rejected'" in n for n in a.notes)
    assert rec.status == "unresolved"
    assert rec.summary.endswith("1 unresolved.")


def test_weak_support_lowers_confidence(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [alt("Two-space indentation", "accepted", "m6", "we go with two spaces",
                                                  [("for", "matches", "m3", "It matches the upstream YAML style guide")])]}
    rec = extract(tabs_discussion, scripted([resp, *verdicts(("weak", "accepted"))]))
    a = rec.alternatives[0]
    assert a.status == "accepted" and a.confidence == "low"


def test_unsupported_reason_marked(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [
        alt("Indent with tabs", "withdrawn", "m5", "I withdraw the tabs idea",
            [("against", "tabs are slow", "m2", "Tabs render inconsistently across editors")]),
    ]}
    rec = extract(tabs_discussion, scripted([resp, *verdicts(("supports", "withdrawn"), ("does_not_support", None))]))
    r = rec.alternatives[0].reasons[0]
    assert r.supported is False
    assert rec.evidence_by_id()[r.evidence_ids[0]].support == "does_not_support"


def test_unverified_evidence_is_not_sent_to_checker(tabs_discussion, scripted):
    resp = {"question": "q", "alternatives": [alt("Indent with tabs", "rejected", "m6", "completely invented sentence here")]}
    prov = scripted([resp])  # no verdicts scripted: a checker call would raise
    rec = extract(tabs_discussion, prov)
    assert rec.alternatives[0].status == "unresolved" or rec.alternatives[0].status == "proposed"
    assert rec.metadata["support_checks"] == 0
