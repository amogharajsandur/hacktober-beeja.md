"""Human-friendly terminal views for CLI results. Machine output stays in ``--json``."""

from __future__ import annotations

from typing import Any

from .term import Style


def _src(e: dict[str, Any]) -> str:
    who = e.get("author") or "unknown author"
    return f"{who} · {e.get('message_id')}" + (f" · {e['source_url']}" if e.get("source_url") else "")


def search(st: Style, query: str, hits) -> str:
    out = [st.header(f"🔎  {query}", f"{len(hits)} result(s) · offline search over the decision store")]
    if not hits:
        out.append("\n  " + st("No matching decisions.", "yellow"))
    for i, h in enumerate(hits, 1):
        ref = h.decision_id + (f"#{h.alternative_id}" if h.alternative_id else "")
        out.append(f"\n  {st(f'{i:>2}.', 'dim')} {st(h.title, 'bold')}")
        out.append(f"      {st.status(h.status)}   {st('score', 'dim')} {st(f'{h.score:.1f}', 'cyan')}   "
                   + st(ref, "dim"))
        if h.reason:
            out.append("      " + st("why  ", "dim") + h.reason)
        for e in h.evidence[:1]:
            out.append(st.quote(e["quote"], _src(e)))
    return "\n".join(out) + "\n"


def why_not(st: Style, idea: str, answers: list[dict[str, Any]]) -> str:
    out = [st.header(f"🤔  Why not “{idea}”?", "answers come only from rejected, withdrawn or deferred alternatives")]
    if not answers:
        out.append("\n  " + st("No rejected, withdrawn or deferred alternative matches that idea.", "yellow"))
    for o in answers:
        out.append(f"\n  {st.status(o['status'])}  {st(o['title'], 'bold')}")
        meta = [f"confidence {o['confidence']}"] + ([f"{o['rejection_type']} outcome"] if o["rejection_type"] else [])
        out.append("  " + st(" · ".join(meta), "dim"))
        for e in o["outcome_evidence"]:
            out.append(st.section("Outcome"))
            out.append(st.quote(e["quote"], _src(e)))
        if o["reasons"]:
            out.append(st.section("Reasons"))
        for r in o["reasons"]:
            mark = st("✔ verified", "green") if r["verified"] else st("⚠ unverified", "yellow")
            out.append(f"    • {r['reason']}  {mark}")
            for e in r["evidence"]:
                out.append(st.quote(e["quote"], _src(e), indent=8))
        for n in o["notes"]:
            out.append("    " + st("⚠ " + n, "yellow"))
    return "\n".join(out) + "\n"


def advisory(st: Style, report: dict[str, Any]) -> str:
    files = report["files_changed"]
    out = [st.header("🧭  Historical context for this change", f"{len(files)} file(s): " + ", ".join(files))]
    if not report["advisories"]:
        out.append("\n  " + st("✔ No stored decision is explicitly linked to these changes.", "green"))
    for adv in report["advisories"]:
        out.append(f"\n  {st.status(adv['status'])}  {st(adv['question'], 'bold')}")
        out.append("  " + st(f"{adv['decision_id']} · score {adv['score']}", "dim"))
        out.append(st.section("Matched because"))
        for m in adv["matched_because"]:
            out.append(f"    {st('◆', 'cyan')} {st(m['type'], 'cyan')}  {m['file']} ~ {st(m['anchor'], 'bold')}  "
                       + st(m["source"], "dim"))
        if adv["alternatives"]:
            out.append(st.section("What was decided before"))
        for a in adv["alternatives"]:
            hint = ("  " + st("← this change uses: " + ", ".join(a["touched_by_change"]), "byellow")
                    if a["touched_by_change"] else "")
            out.append(f"\n    {st.status(a['status'])}  {st(a['title'], 'bold')}{hint}")
            if a["reason"]:
                out.append("      " + st("why  ", "dim") + a["reason"])
            for e in a["evidence"][:1]:
                out.append(st.quote(e["quote"], _src(e), indent=8))
    out.append("\n  " + st("ℹ " + report["note"], "dim"))
    return "\n".join(out) + "\n"


def benchmark(st: Style, report: dict[str, Any]) -> str:
    s = report["summary"]
    if not s:
        return st.header("Benchmark", "no outputs found") + "\n"
    out = [st.header("📊  Benchmark: recovering rejected ideas without the final PEP",
                     f"{s['cases']} cases · {s['ground_truth_items']} ground-truth items · computed from committed real outputs")]
    out.append(st.section("Scores"))
    out.append(st.kv("recall", st.meter(s["recall"], f"({s['recovered']}/{s['ground_truth_items']} recovered)")))
    out.append(st.kv("recall (in-thread)", st.meter(s["recall_discussion_only"], "(ideas actually raised)")))
    out.append(st.kv("precision", st.meter(s["precision"], f"({s['unmatched_negative_claims']} unmatched claim)")))
    out.append(st.kv("F1", st.meter(s["f1"])))
    out.append(st.kv("quotes verified", st.meter(s["evidence_verification_rate"])))
    out.append(st.kv("reasons recovered", st(str(s["reasons_recovered"]), "bold")
                     + st(f"  ({s['reasons_supported_by_verified_evidence']} backed by verified quotes)", "dim")))
    out.append(st.kv("open-question checks", st(f"{s['status_checks_passed']}/{s['status_checks_total']}", "bold")))
    for c in report["cases"]:
        out.append(st.section(f"{c['case']}  ") + st(f"({c['alternatives_extracted']} alternatives, {c['model']})", "dim"))
        for i in c["items"]:
            mark = st("✔", "bgreen") if i["recovered"] else st("✘", "bred")
            via = st(f" via {i['via']}", "dim") if i["via"] == "complement" else ""
            status = st.status(i["extracted_status"]) if i["extracted_status"] else st("not found", "dim")
            reason = st("  reason ✔", "green") if i["reason_supported"] else ""
            note = "" if i["in_discussion"] else st("  (never raised in the thread)", "dim")
            out.append(f"    {mark} {i['title']}")
            out.append(f"        {status}{via}{reason}{note}")
        for u in c["unmatched_negative"]:
            out.append(f"    {st('?', 'byellow')} {u['title']} " + st(f"[{u['status']}] not in ground truth — review", "dim"))
    if report["missing_outputs"]:
        out.append("\n  " + st("missing outputs: " + ", ".join(report["missing_outputs"]), "yellow"))
    return "\n".join(out) + "\n"
