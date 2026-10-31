"""Benchmark: can Beeja recover rejected / deferred / withdrawn ideas from a discussion alone?

Each case directory holds ``input/`` (what the model sees) and ``ground_truth.json`` (built from
the final PEP, used only here for scoring). Extraction never reads ground truth.

Matching rule (defined before scoring, documented in benchmarks/README.md): an extracted
alternative matches a ground-truth item when its title + description contains at least one
term from *every* concept group in ``match_all`` and no ``exclude_any`` term. Terms match at a
word start, case-insensitively. A ground-truth item may also be recovered by its
``complement``: an accepted alternative that states the same decision from the other side
("always require parentheses with as" == "unparenthesized with as" not adopted).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .evidence import is_verified
from .schema import Alternative, DecisionRecord

NEGATIVE = {"rejected", "withdrawn", "deferred", "superseded"}


def _has(text: str, term: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()), text) is not None


def concept_match(text: str, groups: list[list[str]], exclude: list[str] | None = None) -> bool:
    text = " " + text.lower() + " "
    if exclude and any(_has(text, t) for t in exclude):
        return False
    return all(any(_has(text, t) for t in g) for g in groups)


def _alt_text(a: Alternative) -> str:
    return f"{a.title} {a.description}"


@dataclass
class ItemResult:
    id: str
    title: str
    in_discussion: bool
    recovered: bool
    via: str | None  # "direct" | "complement"
    alternative_id: str | None
    extracted_status: str | None
    reason_recovered: bool
    reason_supported: bool
    wrong_status_matches: list[str]


def score_case(gt: dict[str, Any], rec: DecisionRecord) -> dict[str, Any]:
    ev = rec.evidence_by_id()
    used: set[str] = set()
    items: list[ItemResult] = []
    for it in gt["items"]:
        direct, comp, wrong = None, None, []
        for a in rec.alternatives:
            if concept_match(_alt_text(a), it["match_all"], it.get("exclude_any")):
                if a.status in it["expected_status"]:
                    direct = direct or a
                else:
                    wrong.append(f"{a.alternative_id}:{a.status}")
            c = it.get("complement")
            if c and a.status in c["status"] and concept_match(_alt_text(a), c["match_all"], c.get("exclude_any")):
                comp = comp or a
        hit = direct or comp
        r_rec = r_sup = False
        if hit is not None:
            used.add(hit.alternative_id)
            for r in hit.reasons:
                blob = r.text + " " + " ".join(ev[i].quote for i in r.evidence_ids if i in ev)
                if any(_has(" " + blob.lower(), t) for t in it.get("reason_any", [])):
                    r_rec = True
                    r_sup = r_sup or (r.supported and any(is_verified(ev[i]) for i in r.evidence_ids if i in ev))
        items.append(
            ItemResult(
                id=it["id"], title=it["title"], in_discussion=bool(it.get("in_discussion", True)),
                recovered=hit is not None, via=("direct" if direct else "complement") if hit else None,
                alternative_id=hit.alternative_id if hit else None, extracted_status=hit.status if hit else None,
                reason_recovered=r_rec, reason_supported=r_sup,
                wrong_status_matches=[] if hit else wrong,
            )
        )

    # alternatives the system calls rejected/withdrawn/deferred that match no ground-truth entry
    known = list(gt["items"]) + list(gt.get("accepted", [])) + list(gt.get("open", []))
    unmatched_negative = []
    for a in rec.alternatives:
        if a.status not in NEGATIVE or a.alternative_id in used:
            continue
        if any(concept_match(_alt_text(a), k["match_all"], k.get("exclude_any")) for k in known):
            continue
        unmatched_negative.append({"alternative_id": a.alternative_id, "title": a.title, "status": a.status})

    status_checks = []
    for k in gt.get("accepted", []):
        m = [a for a in rec.alternatives if concept_match(_alt_text(a), k["match_all"])]
        status_checks.append({"id": k["id"], "expected": ["accepted"], "found": [a.status for a in m],
                              "ok": any(a.status == "accepted" for a in m)})
    for k in gt.get("open", []):
        m = [a for a in rec.alternatives if concept_match(_alt_text(a), k["match_all"])]
        # an open question must never be reported as decided; not finding it at all is also OK
        status_checks.append({"id": k["id"], "expected": k["expected_status"], "found": [a.status for a in m],
                              "ok": all(a.status in k["expected_status"] for a in m)})

    tp = sum(1 for i in items if i.recovered)
    claimed_negative_matched = len({i.alternative_id for i in items if i.recovered and i.via == "direct"})
    fp = len(unmatched_negative)
    disc_items = [i for i in items if i.in_discussion]
    n_ev = len(rec.evidence)
    n_ver = sum(1 for e in rec.evidence if is_verified(e))
    return {
        "case": gt["case"],
        "ground_truth_items": len(items),
        "recovered": tp,
        "missed": len(items) - tp,
        "recall": _ratio(tp, len(items)),
        "recall_discussion_only": _ratio(sum(i.recovered for i in disc_items), len(disc_items)),
        "unmatched_negative_claims": fp,
        "precision": _ratio(claimed_negative_matched, claimed_negative_matched + fp),
        "reasons_recovered": sum(i.reason_recovered for i in items),
        "reasons_supported_by_verified_evidence": sum(i.reason_supported for i in items),
        "evidence_total": n_ev,
        "evidence_verified": n_ver,
        "evidence_verification_rate": _ratio(n_ver, n_ev),
        "items": [i.__dict__ for i in items],
        "unmatched_negative": unmatched_negative,
        "status_checks": status_checks,
        "alternatives_extracted": len(rec.alternatives),
        "model": rec.metadata.get("model"),
    }


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    gt = sum(c["ground_truth_items"] for c in cases)
    tp = sum(c["recovered"] for c in cases)
    disc = sum(1 for c in cases for i in c["items"] if i["in_discussion"])
    disc_tp = sum(1 for c in cases for i in c["items"] if i["in_discussion"] and i["recovered"])
    pm = sum(len({i["alternative_id"] for i in c["items"] if i["recovered"] and i["via"] == "direct"}) for c in cases)
    fp = sum(c["unmatched_negative_claims"] for c in cases)
    ev = sum(c["evidence_total"] for c in cases)
    ver = sum(c["evidence_verified"] for c in cases)
    p, r = _ratio(pm, pm + fp), _ratio(tp, gt)
    f1 = round(2 * p * r / (p + r), 3) if p and r else None
    return {
        "cases": len(cases),
        "ground_truth_items": gt,
        "recovered": tp,
        "missed": gt - tp,
        "recall": r,
        "recall_discussion_only": _ratio(disc_tp, disc),
        "precision": p,
        "f1": f1,
        "unmatched_negative_claims": fp,
        "reasons_recovered": sum(c["reasons_recovered"] for c in cases),
        "reasons_supported_by_verified_evidence": sum(c["reasons_supported_by_verified_evidence"] for c in cases),
        "evidence_verification_rate": _ratio(ver, ev),
        "status_checks_passed": sum(s["ok"] for c in cases for s in c["status_checks"]),
        "status_checks_total": sum(len(c["status_checks"]) for c in cases),
    }


def run(dataset: Path, outputs: Path) -> dict[str, Any]:
    """Score ``outputs/<case>/decision.json`` against ``dataset/<case>/ground_truth.json``."""
    from .storage import load_file

    results, missing = [], []
    for gt_path in sorted(Path(dataset).glob("*/ground_truth.json")):
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        out = Path(outputs) / gt["case"] / "decision.json"
        if not out.is_file():
            missing.append(gt["case"])
            continue
        results.append(score_case(gt, load_file(out)[0]))
    return {"summary": aggregate(results) if results else None, "cases": results, "missing_outputs": missing}


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = ["# Benchmark results", ""]
    if not s:
        return "\n".join(lines + ["No outputs found."]) + "\n"
    lines += [
        "| metric | value |", "|---|---|",
        f"| cases | {s['cases']} |",
        f"| ground-truth rejected/deferred items | {s['ground_truth_items']} |",
        f"| recovered | {s['recovered']} |",
        f"| missed | {s['missed']} |",
        f"| recall | {s['recall']} |",
        f"| recall (items actually raised in the discussion) | {s['recall_discussion_only']} |",
        f"| precision | {s['precision']} |",
        f"| F1 | {s['f1']} |",
        f"| negative claims matching no ground truth | {s['unmatched_negative_claims']} |",
        f"| correct rejection reasons recovered | {s['reasons_recovered']} |",
        f"| ...of which backed by verified evidence | {s['reasons_supported_by_verified_evidence']} |",
        f"| evidence verification rate | {s['evidence_verification_rate']} |",
        f"| accepted/open status checks passed | {s['status_checks_passed']}/{s['status_checks_total']} |",
        "",
    ]
    for c in report["cases"]:
        lines += [f"## {c['case']}", "", f"model: `{c['model']}`, alternatives extracted: {c['alternatives_extracted']}", "",
                  "| item | in discussion | recovered | via | status | reason | reason verified |",
                  "|---|---|---|---|---|---|---|"]
        for i in c["items"]:
            lines.append(
                f"| {i['id']}: {i['title']} | {'yes' if i['in_discussion'] else 'no'} | "
                f"{'yes' if i['recovered'] else 'no'} | {i['via'] or '-'} | {i['extracted_status'] or '-'} | "
                f"{'yes' if i['reason_recovered'] else 'no'} | {'yes' if i['reason_supported'] else 'no'} |"
            )
        if c["unmatched_negative"]:
            lines += ["", "Negative claims with no ground-truth match (review manually; may be real ideas the PEP omits):"]
            lines += [f"- {u['alternative_id']} [{u['status']}] {u['title']}" for u in c["unmatched_negative"]]
        if c["status_checks"]:
            lines += ["", "Status checks:"]
            lines += [f"- {s_['id']}: expected {s_['expected']}, found {s_['found']} -> {'ok' if s_['ok'] else 'FAIL'}"
                      for s_ in c["status_checks"]]
        lines.append("")
    if report["missing_outputs"]:
        lines.append(f"Missing outputs: {', '.join(report['missing_outputs'])}")
    return "\n".join(lines) + "\n"
