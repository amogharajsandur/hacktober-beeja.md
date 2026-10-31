"""Deterministic search over stored decision records. No model and no network.

Results are compact (IDs, titles, statuses, one reason, evidence references) so coding agents
can pull detail with ``get`` only when they need it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .evidence import is_verified
from .schema import Alternative, DecisionRecord

_STOP = set(
    "a an the of to in on for and or with without by as is are be it its this that why not what was were "
    "did do does we us our should would could can how when which who use using about over vs versus "
    "instead rather than allow allowing".split()
)
NEGATIVE = {"rejected", "withdrawn", "deferred", "superseded"}


def tokens(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9_*.]+", text.lower()):
        t = t.strip(".")
        if len(t) > 1 and t not in _STOP:
            out.append(t)
    return out


def _stem(t: str) -> str:
    for suf in ("ization", "ations", "ation", "ings", "ing", "ies", "es", "s", "ed"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


@dataclass
class Hit:
    decision_id: str
    alternative_id: str | None
    title: str
    status: str
    score: float
    matched_terms: list[str] = field(default_factory=list)
    reason: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def evidence_refs(rec: DecisionRecord, ids: list[str], limit: int = 2) -> list[dict[str, Any]]:
    ev = rec.evidence_by_id()
    out = []
    for i in ids:
        e = ev.get(i)
        if e is None or not is_verified(e):
            continue
        out.append({"evidence_id": e.evidence_id, "message_id": e.message_id, "author": e.author,
                    "source_url": e.source_url, "quote": e.quote[:240]})
        if len(out) >= limit:
            break
    return out


def _alt_fields(a: Alternative) -> list[tuple[str, float]]:
    return [(a.title, 3.0), (a.description, 2.0)] + [(r.text, 1.5) for r in a.reasons]


def _best_reason(a: Alternative) -> tuple[str | None, list[str]]:
    for r in a.reasons:
        if r.supported:
            return r.text, r.evidence_ids
    return (a.reasons[0].text, a.reasons[0].evidence_ids) if a.reasons else (None, [])


def search(records: list[DecisionRecord], query: str, *, limit: int = 10, negative_only: bool = False) -> list[Hit]:
    q = {_stem(t) for t in tokens(query)}
    if not q:
        return []
    # inverse document frequency over alternatives, so common words weigh less
    docs: list[tuple[DecisionRecord, Alternative | None, list[tuple[str, float]]]] = []
    for r in records:
        base = [(r.question, 1.0), (r.topic, 1.0)] + [(a.value, 2.0) for a in r.code_anchors]
        docs.append((r, None, base))
        for a in r.alternatives:
            docs.append((r, a, _alt_fields(a) + [(r.topic, 0.5)]))
    df: dict[str, int] = {}
    for _, _, fields in docs:
        for t in {_stem(t) for text, _ in fields for t in tokens(text)}:
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    hits: list[Hit] = []
    for r, a, fields in docs:
        if negative_only and (a is None or a.status not in NEGATIVE):
            continue
        score, matched = 0.0, set()
        for text, w in fields:
            ts = {_stem(t) for t in tokens(text)}
            for t in q & ts:
                score += w * math.log(1 + n / df.get(t, 1))
                matched.add(t)
        if not matched:
            continue
        coverage = len(matched) / len(q)
        score *= 0.5 + coverage
        if a is None:
            hits.append(Hit(r.id, None, r.question, r.status, round(score, 3), sorted(matched)))
        else:
            reason, ev_ids = _best_reason(a)
            hits.append(Hit(r.id, a.alternative_id, a.title, a.status, round(score, 3), sorted(matched), reason,
                            evidence_refs(r, ev_ids + a.status_evidence)))
    hits.sort(key=lambda h: (-h.score, h.decision_id, h.alternative_id or ""))
    return hits[:limit]


def get(records: list[DecisionRecord], ref: str) -> tuple[DecisionRecord, Alternative | None] | None:
    """``dec-id`` or ``dec-id#alt-2``."""
    dec_id, _, alt_id = ref.partition("#")
    for r in records:
        if r.id == dec_id:
            if not alt_id:
                return r, None
            a = r.alternative(alt_id)
            return (r, a) if a else None
    return None


def why_not(records: list[DecisionRecord], idea: str, limit: int = 3) -> list[dict[str, Any]]:
    """Source-grounded answer to "why not X?": negative-outcome alternatives with their reasons."""
    out = []
    for h in search(records, idea, limit=limit, negative_only=True):
        rec, alt = get(records, f"{h.decision_id}#{h.alternative_id}")  # type: ignore[misc]
        reasons = [
            {"reason": r.text, "verified": r.supported, "evidence": evidence_refs(rec, r.evidence_ids, 1)}
            for r in alt.reasons
        ]
        out.append({
            "decision_id": rec.id, "alternative_id": alt.alternative_id, "title": alt.title, "status": alt.status,
            "rejection_type": alt.rejection_type, "confidence": alt.confidence, "score": h.score,
            "outcome_evidence": evidence_refs(rec, alt.status_evidence, 1), "reasons": reasons,
            "notes": alt.notes,
        })
    return out
