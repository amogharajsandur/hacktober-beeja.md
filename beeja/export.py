"""Exporters over the canonical record: MADR Markdown, Portable Text narrative, web viewer data.

All exports are derived deterministically from the stored facts; nothing new is generated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .evidence import is_verified
from .ingest import load_discussion
from .schema import DecisionRecord

NEGATIVE = ("rejected", "withdrawn", "deferred", "superseded")


def _key(*parts: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", "-".join(str(p) for p in parts).lower()).strip("-")[:60]


def to_madr(rec: DecisionRecord) -> str:
    """A basic MADR 3-style document. Unverified evidence is marked as such."""
    ev = rec.evidence_by_id()
    accepted = [a for a in rec.alternatives if a.status == "accepted"]
    lines = [
        f"# {rec.question}", "",
        f"* Status: {rec.status} (AI-extracted by {rec.metadata.get('model', 'unknown')}, not human-reviewed)",
        f"* Source: {', '.join(rec.source_threads)}", "",
        "## Context and Problem Statement", "", rec.topic or rec.question, "",
        "## Considered Options", "",
    ]
    lines += [f"* {a.title} — **{a.status}**" for a in rec.alternatives]
    lines += ["", "## Decision Outcome", ""]
    if accepted:
        lines += [f"Chosen option: \"{a.title}\"." for a in accepted]
    else:
        lines.append("No option was recorded as accepted in the discussion.")
    lines += ["", "## Pros and Cons of the Options", ""]
    for a in rec.alternatives:
        lines += [f"### {a.title}", "", f"Outcome: {a.status}" + (f" ({a.rejection_type})" if a.rejection_type else "")
                  + f", confidence {a.confidence}.", ""]
        for r in a.reasons:
            refs = []
            for i in r.evidence_ids:
                e = ev.get(i)
                if e:
                    tag = "" if is_verified(e) else " [unverified]"
                    refs.append(f"[{e.message_id}]({e.source_url})" if e.source_url else e.message_id)
                    refs[-1] += tag
            lines.append(f"* {'Bad' if a.status in NEGATIVE else 'Good'}, because {r.text} ({', '.join(refs)})")
        lines += [f"* Note: {n}" for n in a.notes]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_portable_text(rec: DecisionRecord) -> list[dict[str, Any]]:
    """A short narrative as Portable Text blocks, with links to the cited source messages."""
    ev = rec.evidence_by_id()
    blocks: list[dict[str, Any]] = []

    def block(children: list[dict[str, Any]], style: str = "normal", mark_defs=None, list_item=None):
        b = {"_type": "block", "_key": _key("b", len(blocks)), "style": style,
             "children": children, "markDefs": mark_defs or []}
        if list_item:
            b["listItem"], b["level"] = list_item, 1
        blocks.append(b)

    def span(text: str, marks=None, n=0):
        return {"_type": "span", "_key": _key("s", len(blocks), n), "text": text, "marks": marks or []}

    block([span("What happened")], style="h3")
    block([span(rec.summary)])
    for a in rec.alternatives:
        if a.status not in NEGATIVE and a.status != "accepted":
            continue
        children = [span(f"{a.title}: ", ["strong"]), span(f"{a.status}. ", n=1)]
        defs = []
        r = next((x for x in a.reasons if x.supported), None)
        if r:
            children.append(span(r.text + " ", n=2))
        sid = (a.status_evidence or [None])[0]
        e = ev.get(sid) if sid else None
        if e is not None and e.source_url and is_verified(e):
            k = _key("l", a.alternative_id)
            defs.append({"_type": "link", "_key": k, "href": e.source_url})
            children.append(span(f"source: {e.message_id}", [k], n=3))
        block(children, mark_defs=defs, list_item="bullet")
    return blocks


def export_web(examples: Path, out: Path) -> list[str]:
    """Write ``index.json`` and one ``<id>.json`` per example for the static viewer.

    Each decision file carries the record, a Portable Text narrative and the cited source
    messages (only those), so the viewer can highlight passages in their real context.
    """
    out.mkdir(parents=True, exist_ok=True)
    index, written = [], []
    for case in sorted(p for p in Path(examples).iterdir() if (p / "decision.json").is_file()):
        rec = DecisionRecord.model_validate_json((case / "decision.json").read_text(encoding="utf-8"))
        messages: dict[str, Any] = {}
        src = case / "input_path.txt"
        if src.is_file():
            root = Path(examples).resolve().parent
            d = load_discussion(root / src.read_text().strip())
            for mid in sorted({e.message_id for e in rec.evidence}):
                m = d.message(mid)
                if m:
                    messages[mid] = {"author": m.author_display_name or m.author_id, "role": m.author_role,
                                     "created_at": m.created_at, "source_url": m.source_url, "body": m.body,
                                     "reply_to": m.reply_to}
        payload = {"record": rec.model_dump(mode="json"), "narrative": rec.presentation or to_portable_text(rec),
                   "messages": messages, "narrative_generated": rec.presentation is None}
        (out / f"{rec.id}.json").write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        written.append(rec.id)
        statuses = [a.status for a in rec.alternatives]
        index.append({"id": rec.id, "question": rec.question, "topic": rec.topic, "status": rec.status,
                      "summary": rec.summary, "alternatives": len(rec.alternatives), "case": case.name,
                      "counts": {s: statuses.count(s) for s in sorted(set(statuses))},
                      "evidence_total": len(rec.evidence),
                      "evidence_verified": sum(1 for e in rec.evidence if is_verified(e)),
                      "source": next((t for t in rec.source_threads if t.startswith("http")), None)})
    (out / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return written
