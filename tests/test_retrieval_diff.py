from beeja.anchors import find_anchors
from beeja.diff import check_diff, parse_unified_diff, render_text
from beeja.ingest import from_text
from beeja.retrieval import get, search, why_not
from beeja.schema import Alternative, CodeAnchor, DecisionRecord, Evidence, Reason


def _rec():
    ev = [
        Evidence(evidence_id="ev-1", message_id="m2", quote="a single shared buffer would force serialization",
                 author="ana", source_url="https://x/t/1/2", verification="exact"),
        Evidence(evidence_id="ev-2", message_id="m3", quote="we went with per-thread buffers", verification="exact"),
        Evidence(evidence_id="ev-3", message_id="m4", quote="totally invented", verification="not_found"),
    ]
    alts = [
        Alternative(alternative_id="alt-1", title="Single shared runtime buffer", status="rejected", rejection_type="explicit",
                    reasons=[Reason(text="forces serialization of debugger operations", evidence_ids=["ev-1"], supported=True)],
                    status_evidence=["ev-2"], confidence="high"),
        Alternative(alternative_id="alt-2", title="Per-thread debugger buffers", status="accepted", status_evidence=["ev-2"]),
        Alternative(alternative_id="alt-3", title="Use signals to trigger attach", status="unresolved",
                    reasons=[Reason(text="signals are unsafe", evidence_ids=["ev-3"], supported=False)]),
    ]
    return DecisionRecord(
        id="dec-768", question="How should external debuggers attach?", topic="Safe external debugger interface",
        alternatives=alts, evidence=ev,
        code_anchors=[CodeAnchor(kind="symbol", value="PyThreadState", note="mentioned in m1"),
                      CodeAnchor(kind="path", value="Python/remote_debugging.c", note="mentioned in m5"),
                      CodeAnchor(kind="symbol", value="sys.remote_exec", note="mentioned in m1")],
    )


OTHER = DecisionRecord(id="dec-tabs", question="Tabs or spaces?", topic="indentation",
                       alternatives=[Alternative(alternative_id="alt-1", title="Indent with tabs", status="withdrawn")])


def test_search_ranks_relevant_alternative_with_evidence():
    hits = search([_rec(), OTHER], "why not a shared buffer?")
    assert hits[0].decision_id == "dec-768" and hits[0].alternative_id == "alt-1"
    assert hits[0].status == "rejected" and "buffer" in hits[0].matched_terms
    assert hits[0].evidence[0]["source_url"] == "https://x/t/1/2"


def test_search_never_returns_unverified_evidence():
    hits = search([_rec()], "signals attach")
    h = next(h for h in hits if h.alternative_id == "alt-3")
    assert h.evidence == []


def test_search_no_match_and_empty_query():
    assert search([_rec(), OTHER], "kubernetes helm chart") == []
    assert search([_rec()], "why not the") == []


def test_get_decision_and_alternative():
    recs = [_rec(), OTHER]
    assert get(recs, "dec-768")[0].id == "dec-768"
    assert get(recs, "dec-768#alt-2")[1].status == "accepted"
    assert get(recs, "dec-768#alt-9") is None and get(recs, "dec-nope") is None


def test_why_not_only_negative_outcomes():
    out = why_not([_rec(), OTHER], "per-thread or shared buffer")
    assert [o["alternative_id"] for o in out] == ["alt-1"]
    assert out[0]["reasons"][0]["verified"] is True
    assert out[0]["reasons"][0]["evidence"][0]["quote"].startswith("a single shared buffer")


DIFF_RELATED = """diff --git a/Python/remote_debugging.c b/Python/remote_debugging.c
--- a/Python/remote_debugging.c
+++ b/Python/remote_debugging.c
@@ -10,3 +10,4 @@
+static char shared_buffer[4096];  /* one buffer for all threads */
 int x;
diff --git a/Lib/test/test_sys.py b/Lib/test/test_sys.py
--- a/Lib/test/test_sys.py
+++ b/Lib/test/test_sys.py
@@ -1,2 +1,3 @@
+    sys.remote_exec(pid, script)
"""

DIFF_UNRELATED = """diff --git a/Doc/whatsnew/3.14.rst b/Doc/whatsnew/3.14.rst
--- a/Doc/whatsnew/3.14.rst
+++ b/Doc/whatsnew/3.14.rst
@@ -1,2 +1,3 @@
+The debugger buffer thread shared runtime notes for this release.
"""


def test_parse_unified_diff():
    files = parse_unified_diff(DIFF_RELATED)
    assert [f.path for f in files] == ["Python/remote_debugging.c", "Lib/test/test_sys.py"]
    assert files[1].added == ["    sys.remote_exec(pid, script)"]


def test_related_diff_produces_grounded_advisory():
    rep = check_diff([_rec(), OTHER], DIFF_RELATED)
    assert [a["decision_id"] for a in rep["advisories"]] == ["dec-768"]
    adv = rep["advisories"][0]
    kinds = {(m["type"], m["anchor"]) for m in adv["matched_because"]}
    assert ("path", "Python/remote_debugging.c") in kinds and ("symbol", "sys.remote_exec") in kinds
    rejected = adv["alternatives"][0]
    assert rejected["status"] == "rejected" and rejected["evidence"][0]["message_id"] == "m2"
    text = render_text(rep)
    assert "matched because" in text and "proof:" in text and "does not approve, block" in text


def test_reference_to_anchored_file_and_touched_alternative():
    rec = DecisionRecord(
        id="dec-cfg", question="Config format?", topic="config",
        alternatives=[Alternative(alternative_id="alt-1", title="YAML file", status="rejected", rejection_type="explicit")],
        code_anchors=[CodeAnchor(kind="path", value="pyproject.toml", note="mentioned in q1")],
    )
    diff = "+++ b/src/pkg/config.py\n+import yaml\n+data = load(root / 'pyproject.toml')\n"
    adv = check_diff([rec], diff)["advisories"][0]
    assert adv["matched_because"][0]["type"] == "reference"
    assert adv["alternatives"][0]["touched_by_change"] == ["yaml"]
    assert "this change uses: yaml" in render_text({"files_changed": ["x"], "advisories": [adv], "note": ""})


def test_open_alternative_shown_only_when_change_uses_its_wording():
    rep = check_diff([_rec()], "+++ b/Python/remote_debugging.c\n+int x;\n")
    assert [a["alternative_id"] for a in rep["advisories"][0]["alternatives"]] == ["alt-1", "alt-2"]
    rep = check_diff([_rec()], "+++ b/Python/remote_debugging.c\n+/* use signals to trigger attach */\n")
    assert rep["advisories"][0]["alternatives"][0]["alternative_id"] == "alt-3"


def test_unrelated_diff_gives_no_advisory_even_with_topic_words():
    rep = check_diff([_rec(), OTHER], DIFF_UNRELATED)
    assert rep["advisories"] == []  # many topic keywords, but no explicit path/symbol link
    assert "No stored decision" in render_text(rep)


def test_symbol_must_match_whole_identifier():
    diff = "+++ b/x.c\n+PyThreadStateExtra *p;\n"
    assert check_diff([_rec()], diff)["advisories"] == []


def test_find_anchors_from_text():
    d = from_text(
        "[m1] a: Add a field to PyThreadState and expose sys.remote_exec; see gh-12345.\n"
        "[m2] b: Touch Python/ceval_gil.c and pyproject.toml, not https://github.com/x/y/blob/main/z/injector.py\n"
        "[m3] c: PyPy already does this, PyThreadState again. See lmacken/pyrasite/blob/abc/pyrasite/injector.py"
        " and sys.path.\n",
        "t",
    )
    got = {(a.kind, a.value): a.note for a in find_anchors(d)}
    assert got[("symbol", "PyThreadState")] == "mentioned in m1, m3"
    assert ("symbol", "sys.remote_exec") in got and ("issue", "gh-12345") in got
    assert ("path", "Python/ceval_gil.c") in got and ("path", "pyproject.toml") in got
    assert ("symbol", "PyPy") not in got
    assert not any("injector" in v for _, v in got)
    assert ("symbol", "sys.path") not in got
