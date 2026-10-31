"""Evidence resolution: does a cited passage really occur in the cited source message?

Three separate questions are kept apart (see README):
  1. Does the cited message exist?            -> ``missing_message``
  2. Does the passage occur in it?            -> ``exact`` / ``approximate`` / ``not_found``
  3. Does the passage support the claim?      -> ``Support`` (see ``support.py``)

This module answers 1 and 2 deterministically against the stored source text.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from .schema import Discussion, Evidence, Verification

_TRANSLATE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        " ": " ",
        "`": "",
    }
)

APPROX_THRESHOLD = 0.9
MIN_QUOTE_CHARS = 12


def _norm_char_map(text: str) -> tuple[str, list[int]]:
    """Normalise text for matching and keep a map from normalised index -> original index."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        ch = unicodedata.normalize("NFKC", ch).translate(_TRANSLATE).lower()
        for c in ch:
            if c.isspace() or c == ">":  # quote markers and line breaks are layout, not content
                if prev_space:
                    continue
                c = " "
                prev_space = True
            else:
                prev_space = False
            out.append(c)
            idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalise(text: str) -> str:
    return _norm_char_map(text)[0].strip(" .,;:")


@dataclass
class Match:
    verification: Verification
    start: int | None = None
    end: int | None = None
    ratio: float = 0.0
    in_quoted_reply: bool = False


def locate(quote: str, body: str) -> Match:
    q = normalise(quote).strip("\"' ")
    q = re.sub(r"\s*\.\.\.\s*", " ... ", q).strip()
    if len(q) < MIN_QUOTE_CHARS:
        return Match(Verification.not_found)
    nb, idx = _norm_char_map(body)
    pieces = [p.strip() for p in q.split(" ... ") if p.strip()]
    # elided quotes ("a ... b"): every piece must appear, in order
    if len(pieces) > 1:
        pos, start = 0, None
        for p in pieces:
            k = nb.find(p, pos)
            if k < 0:
                break
            start = k if start is None else start
            pos = k + len(p)
        else:
            return _mk(Verification.exact, start, pos, idx, body, 1.0)
    k = nb.find(q)
    if k >= 0:
        return _mk(Verification.exact, k, k + len(q), idx, body, 1.0)
    # fuzzy: best window via longest matching blocks
    sm = SequenceMatcher(None, nb, q, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return Match(Verification.not_found)
    matched = sum(b.size for b in blocks)
    span_start = blocks[0].a
    span_end = blocks[-1].a + blocks[-1].size
    span = max(span_end - span_start, 1)
    ratio = matched / max(len(q), 1)
    compact = matched / span  # penalise matches scattered across the whole message
    if ratio >= APPROX_THRESHOLD and compact >= 0.8:
        return _mk(Verification.approximate, span_start, span_end, idx, body, round(ratio, 3))
    return Match(Verification.not_found, ratio=round(ratio, 3))


def _mk(v: Verification, s: int, e: int, idx: list[int], body: str, ratio: float) -> Match:
    o_start = idx[s]
    o_end = idx[min(e, len(idx)) - 1] + 1
    line_start = body.rfind("\n", 0, o_start) + 1
    quoted = body[line_start:o_start].lstrip().startswith(">")
    return Match(v, o_start, o_end, ratio, quoted)


def evidence_id(message_id: str, quote: str) -> str:
    h = hashlib.sha1(f"{message_id}\x00{normalise(quote)}".encode("utf-8")).hexdigest()[:10]
    return f"ev-{h}"


def make_evidence(discussion: Discussion, message_id: str, quote: str) -> Evidence:
    """Build an Evidence item and verify it against the stored discussion."""
    quote = (quote or "").strip()
    msg = discussion.message(message_id)
    ev = Evidence(evidence_id=evidence_id(message_id, quote), message_id=message_id, quote=quote)
    if msg is None:
        ev.verification = Verification.missing_message
        return ev
    ev.source_url = msg.source_url
    ev.author = msg.author_display_name or msg.author_id
    ev.created_at = msg.created_at
    ev.source_hash = msg.source_snapshot_hash
    m = locate(quote, msg.body)
    ev.verification = m.verification
    if m.start is not None:
        ev.start_offset, ev.end_offset = m.start, m.end
        if m.verification == Verification.approximate:
            # keep the model's wording out of the record: store the real source span
            ev.quote = msg.body[m.start : m.end]
    if m.in_quoted_reply:
        # the passage sits inside a "> quoted" reply: the author is quoting someone else
        ev.author = None
    return ev


def is_verified(ev: Evidence) -> bool:
    return ev.verification in (Verification.exact.value, Verification.approximate.value)
