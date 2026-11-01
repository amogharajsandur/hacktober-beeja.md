"""PEP / Discourse imports through the same one-step workflow (network and model faked)."""

import json

import pytest

from beeja import pep, sources
from beeja.importer import ImportError_, import_url
from tests.conftest import FIXTURES


@pytest.mark.parametrize("url,kind,case", [
    ("https://github.com/o/r/issues/7", "github", "gh-o-r-issue-7"),
    ("https://discuss.python.org/t/pep-758-allow-except/66453", "discourse", "discourse-discuss-python-org-66453"),
    ("https://discuss.python.org/t/66453/12", "discourse", "discourse-discuss-python-org-66453"),
    ("https://peps.python.org/pep-0758/", "pep", None),
    ("PEP 758", "pep", None),
])
def test_resolve_classifies_without_network(url, kind, case):
    s = sources.resolve(url)
    assert s.kind == kind and s.case == case


@pytest.mark.parametrize("url", ["", "https://example.com/blog/post", "https://peps.python.org/"])
def test_resolve_rejects_unsupported(url):
    with pytest.raises(sources.SourceError) as e:
        sources.resolve(url)
    assert e.value.kind == "invalid_url"


def _topic():
    t = json.loads((FIXTURES / "discourse_small.json").read_text())
    t["id"] = 66453
    t["title"] = "PEP 758: Allow except without parentheses"
    return t


ANSWER = {"question": "Should foo be optional?", "alternatives": [{
    "title": "Make foo optional", "same_as": "", "description": "make foo optional", "proposed_in": "post-1",
    "proponents": ["author1"], "status": "rejected", "status_message_id": "post-3",
    "status_quote": "OK, we decided to drop it.",
    "arguments": [{"stance": "against", "text": "breaks users", "message_id": "post-2",
                   "quote": "That would break & confuse users."}]}]}


@pytest.fixture
def fake_pep(monkeypatch):
    calls = []

    def resolve_pep(n, client=None):
        calls.append(n)
        return "https://discuss.python.org", 66453, "Allow except without parentheses"

    monkeypatch.setattr(pep, "resolve_pep", resolve_pep)
    monkeypatch.setattr("beeja.discourse.fetch_topic", lambda base, tid, **kw: _topic())
    return calls


def _provider(scripted):
    verdict = {"explanation": "states it", "stated_outcome": "rejected", "verdict": "supports"}
    return scripted([ANSWER, verdict, verdict, verdict])


def test_pep_url_imports_its_discussion_not_the_pep(tmp_path, scripted, fake_pep):
    progress = []
    res = import_url("https://peps.python.org/pep-0758/", root=tmp_path, provider=_provider(scripted),
                     progress=progress.append)
    assert fake_pep == [758]
    assert res["case"] == "discourse-discuss-python-org-66453" and res["label"] == "PEP 758"
    assert any("not the PEP text" in p for p in progress)
    rec = json.loads((tmp_path / "decisions" / f"{res['decision_id']}.json").read_text())
    assert rec["alternatives"][0]["status"] == "rejected"
    assert rec["metadata"]["discourse"]["pep"] == 758
    saved = json.loads((tmp_path / "imports" / res["case"] / "input" / "discussion.json").read_text())
    assert "post_stream" in saved  # the thread, as fetched; no PEP body anywhere
    # the same thread pasted directly is recognised as already imported
    with pytest.raises(ImportError_) as e:
        import_url("https://discuss.python.org/t/pep-758/66453", root=tmp_path, provider=_provider(scripted))
    assert e.value.kind == "exists"


def test_import_never_overwrites_a_record_it_does_not_own(tmp_path, scripted, fake_pep):
    first = import_url("PEP 758", root=tmp_path, provider=_provider(scripted))
    example = tmp_path / "decisions" / f"{first['decision_id']}.json"
    # pretend that record belongs to someone else (e.g. a bundled example), then import again elsewhere
    other = tmp_path / "other"
    (other / "decisions").mkdir(parents=True)
    (other / "decisions" / example.name).write_text(example.read_text())
    res = import_url("PEP 758", root=other, provider=_provider(scripted))
    assert res["decision_id"].endswith("-import")
    assert (other / "decisions" / example.name).read_text() == example.read_text()


def test_pep_without_discourse_thread_is_explained(tmp_path, monkeypatch):
    def no_thread(n, client=None):
        raise pep.SourceError("PEP 8 was discussed on mailing lists only.", "no_discussion")

    monkeypatch.setattr(pep, "resolve_pep", no_thread)
    with pytest.raises(ImportError_) as e:
        import_url("PEP 8", root=tmp_path)
    assert e.value.kind == "no_discussion"


def test_discourse_thread_without_replies_is_refused(tmp_path, monkeypatch):
    t = _topic()
    t["post_stream"]["posts"] = t["post_stream"]["posts"][:1]
    monkeypatch.setattr("beeja.discourse.fetch_topic", lambda base, tid, **kw: t)
    with pytest.raises(ImportError_) as e:
        import_url("https://discuss.python.org/t/x/66453", root=tmp_path)
    assert e.value.kind == "empty"
