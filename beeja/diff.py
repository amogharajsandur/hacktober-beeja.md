"""PR-diff relevance: which stored decisions does a code change touch, and why?

Purely deterministic. A decision is reported only with at least one *explicit* link: a changed
file matching a path the discussion mentioned, or a changed line using a symbol the discussion
mentioned. Topic keywords alone never trigger an advisory (they only add to the score).
The output is informational; Beeja never blocks, rejects or comments on a PR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .retrieval import NEGATIVE, evidence_refs, tokens
from .schema import DecisionRecord

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


@dataclass
class FileChange:
    path: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def parse_unified_diff(text: str) -> list[FileChange]:
    files: list[FileChange] = []
    cur: FileChange | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(\S+) b/(\S+)", line)
            cur = FileChange(m.group(2) if m else line.split()[-1])
            files.append(cur)
        elif line.startswith("+++ "):
            p = line[4:].strip()
            if p != "/dev/null":
                p = p[2:] if p.startswith("b/") else p
                if cur is None or cur.path != p:
                    if cur is None or cur.added or cur.removed:
                        cur = FileChange(p)
                        files.append(cur)
                    else:
                        cur.path = p
        elif line.startswith("--- ") or line.startswith("@@") or line.startswith("index "):
            continue
        elif cur is not None and line.startswith("+"):
            cur.added.append(line[1:])
        elif cur is not None and line.startswith("-"):
            cur.removed.append(line[1:])
    return files


def _path_match(changed: str, anchor: str) -> str | None:
    c, a = PurePosixPath(changed), PurePosixPath(anchor)
    if changed == anchor or changed.endswith("/" + anchor):
        return "path"
    if "/" not in anchor and c.name == a.name:
        return "filename"
    return None


def check_diff(records: list[DecisionRecord], diff_text: str, *, min_keyword_overlap: int = 2) -> dict[str, Any]:
    files = parse_unified_diff(diff_text)
    changed_idents: dict[str, set[str]] = {}
    for f in files:
        ids: set[str] = set()
        for ln in f.added + f.removed:
            for tok in _IDENT.findall(ln):
                ids.add(tok)
                ids.update(tok.split("."))
        changed_idents[f.path] = ids
    diff_words = {t for f in files for ln in f.added + f.removed for t in tokens(ln)}
    added_words = {t for f in files for ln in f.added for t in tokens(ln.replace("_", " "))}

    advisories = []
    for r in records:
        reasons: list[dict[str, str]] = []
        score = 0.0
        for anc in r.code_anchors:
            for f in files:
                if anc.kind == "path":
                    kind = _path_match(f.path, anc.value)
                    if kind:
                        score += 3 if kind == "path" else 2
                        reasons.append({"type": kind, "file": f.path, "anchor": anc.value, "source": anc.note or ""})
                    elif any(PurePosixPath(anc.value).name in ln for ln in f.added + f.removed):
                        score += 1.5  # the changed code refers to the file the discussion is about
                        reasons.append({"type": "reference", "file": f.path, "anchor": anc.value, "source": anc.note or ""})
                elif anc.kind == "symbol" and anc.value in changed_idents[f.path]:
                    score += 2
                    reasons.append({"type": "symbol", "file": f.path, "anchor": anc.value, "source": anc.note or ""})
        if not reasons:
            continue  # no explicit link -> no advisory, whatever the keyword overlap
        topic_words = {t for a in r.alternatives for t in tokens(a.title)} | set(tokens(r.topic))
        overlap = sorted(topic_words & diff_words)
        if len(overlap) >= min_keyword_overlap:
            score += 0.5 * len(overlap)
            reasons.append({"type": "keywords", "file": "*", "anchor": ", ".join(overlap[:8]), "source": "topic words"})
        alts = []
        for a in r.alternatives:
            touched = sorted({t for t in tokens(a.title) if len(t) > 2} & added_words)
            decided = a.status in NEGATIVE or a.status == "accepted"
            # open alternatives are only shown when the change itself uses their wording
            if not decided and len(touched) < 2:
                continue
            reason = next((x for x in a.reasons if x.supported), None)
            alts.append({
                "alternative_id": a.alternative_id, "title": a.title, "status": a.status,
                "confidence": a.confidence, "reason": reason.text if reason else None,
                "touched_by_change": touched,
                "evidence": evidence_refs(r, (reason.evidence_ids if reason else []) + a.status_evidence, 2),
            })
        # alternatives the change seems to (re)introduce first, then rejected ones, then the rest
        alts.sort(key=lambda x: (not x["touched_by_change"], x["status"] not in NEGATIVE, x["status"] != "accepted"))
        advisories.append({
            "decision_id": r.id, "question": r.question, "status": r.status, "score": round(score, 2),
            "matched_because": reasons, "alternatives": alts, "source_threads": r.source_threads,
        })
    advisories.sort(key=lambda x: -x["score"])
    return {
        "files_changed": [f.path for f in files],
        "advisories": advisories,
        "note": "Informational historical context only. Beeja does not approve, block or comment on changes.",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [f"Changed files: {', '.join(report['files_changed']) or '(none)'}"]
    if not report["advisories"]:
        lines.append("No stored decision is explicitly linked to these changes.")
        return "\n".join(lines) + "\n"
    for adv in report["advisories"]:
        lines += ["", f"== {adv['decision_id']} (score {adv['score']})", f"   {adv['question']}", "   matched because:"]
        lines += [f"   - {m['type']}: {m['file']} ~ {m['anchor']} ({m['source']})" for m in adv["matched_because"]]
        for a in adv["alternatives"]:
            hint = f"  <- this change uses: {', '.join(a['touched_by_change'])}" if a["touched_by_change"] else ""
            lines.append(f"   [{a['status']}] {a['title']} (confidence {a['confidence']}){hint}")
            if a["reason"]:
                lines.append(f"       why: {a['reason']}")
            for e in a["evidence"][:1]:
                where = e["source_url"] or e["message_id"]
                lines.append(f"       proof: \"{e['quote'][:160]}\" - {e['author'] or 'unknown'} {where}")
    lines += ["", report["note"]]
    return "\n".join(lines) + "\n"
