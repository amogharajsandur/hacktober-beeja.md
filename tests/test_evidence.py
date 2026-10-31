from beeja.evidence import is_verified, locate, make_evidence
from beeja.schema import Verification

BODY = "Tabs render inconsistently across editors\nand break our YAML output, which forbids tab indentation."


def test_exact_match_across_line_breaks_and_case():
    m = locate("tabs render inconsistently across editors and break our YAML output", BODY)
    assert m.verification == Verification.exact
    assert BODY[m.start:m.end].startswith("Tabs render")


def test_smart_quotes_and_backticks_normalised():
    body = "I don’t think `except*` should change."
    assert locate("I don't think except* should change", body).verification == Verification.exact


def test_elided_quote():
    m = locate("Tabs render inconsistently ... forbids tab indentation", BODY)
    assert m.verification == Verification.exact


def test_small_typo_is_only_approximate():
    m = locate("Tabs render inconsistantly across editors and break our YAML output", BODY)
    assert m.verification == Verification.approximate


def test_fabricated_quote_not_found():
    m = locate("Spaces are slower to parse and waste disk space on every file", BODY)
    assert m.verification == Verification.not_found


def test_too_short_quote_is_not_evidence():
    assert locate("Tabs", BODY).verification == Verification.not_found


def test_make_evidence_missing_message(tabs_discussion):
    ev = make_evidence(tabs_discussion, "m99", "anything at all here")
    assert ev.verification == Verification.missing_message and not is_verified(ev)


def test_make_evidence_fills_metadata_from_source(tabs_discussion):
    ev = make_evidence(tabs_discussion, "m6", "we go with two spaces")
    assert ev.verification == Verification.exact
    assert ev.author == "carol"
    assert ev.source_hash == tabs_discussion.message("m6").source_snapshot_hash
    body = tabs_discussion.message("m6").body
    assert body[ev.start_offset:ev.end_offset].lower() == "we go with two spaces"


def test_approximate_match_stores_real_source_text(tabs_discussion):
    ev = make_evidence(tabs_discussion, "m2", "Tabs render inconsistantly across editors and break our YAML")
    assert ev.verification == Verification.approximate
    assert "inconsistently" in ev.quote  # the source wording, not the model's typo


def test_quote_inside_quoted_reply_has_no_author():
    from beeja.ingest import load_discussion
    from tests.conftest import FIXTURES

    d = load_discussion(FIXTURES / "discourse_small.json")
    ev = make_evidence(d, "post-2", "make foo optional")
    assert ev.verification == Verification.exact
    assert ev.author is None  # critic was quoting author1, so do not attribute it to critic
