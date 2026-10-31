"""Terminal views: readable with colour, plain (no ANSI) when not a terminal."""

from beeja import pretty, storage
from beeja.diff import check_diff
from beeja.retrieval import search, why_not
from beeja.term import Style, enabled, plain
from tests.test_examples_regression import ROOT

RECS = storage.load_dir(ROOT / "decisions")


def test_no_colour_when_not_a_tty(monkeypatch):
    import io

    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert enabled(io.StringIO()) is False
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert enabled(io.StringIO()) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert enabled(io.StringIO()) is False


def test_views_render_and_colour_is_only_decoration():
    for st in (Style(False), Style(True)):
        out = pretty.why_not(st, "YAML config", why_not(RECS, "YAML config"))
        assert "YAML" in plain(out) and "rejected" in plain(out)
        out = pretty.search(st, "shared buffer", search(RECS, "shared buffer", limit=3))
        assert "shared buffer" in plain(out)
        diff = (ROOT / "examples/check-diff/related_quillfmt_yaml.patch").read_text()
        out = pretty.advisory(st, check_diff(RECS, diff))
        assert "Matched because" in plain(out) and "this change uses: yaml" in plain(out)
    assert "\x1b[" not in pretty.why_not(Style(False), "YAML config", why_not(RECS, "YAML config"))
    assert "\x1b[" in pretty.why_not(Style(True), "YAML config", why_not(RECS, "YAML config"))


def test_empty_results_are_explained():
    st = Style(False)
    assert "No matching decisions" in pretty.search(st, "kubernetes helm", [])
    assert "No rejected" in pretty.why_not(st, "kubernetes", [])
