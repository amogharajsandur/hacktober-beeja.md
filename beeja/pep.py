"""Resolve a Python PEP to its *discussion thread* on a Discourse forum.

Only the PEP's header block is read (Discussions-To / Post-History) to find the thread.
The PEP body is never returned or stored; the thread itself is what Beeja imports.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

USER_AGENT = "beeja"
RAW_URL = "https://raw.githubusercontent.com/python/peps/main/peps/pep-{number:04d}.{ext}"


class SourceError(Exception):
    """A user-facing error; ``kind`` is one of invalid_url, not_found, no_discussion, network."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def parse_discourse_url(url: str) -> tuple[str, int] | None:
    """``https://host/t/slug/123/4`` -> ``("https://host", 123)``; None if not a topic URL."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    parts = [s for s in p.path.split("/") if s]
    if not parts or parts[0] != "t" or len(parts) < 2:
        return None
    rest = parts[1:]
    if rest[0].isdigit():
        topic_id = int(rest[0])
    elif len(rest) >= 2 and rest[1].isdigit():
        topic_id = int(rest[1])
    else:
        return None
    return f"{p.scheme}://{p.netloc}", topic_id


_PEP_URL_RE = re.compile(
    r"^https?://(?:peps\.python\.org|(?:www\.)?python\.org/dev/peps)/pep-(\d+)(?:/|\.html|\.rst|\.txt)?(?:[?#].*)?$",
    re.I,
)
_PEP_BARE_RE = re.compile(r"^\s*pep[\s-]*(\d+)\s*$", re.I)


def parse_pep_url(url: str) -> int | None:
    """Return the PEP number for a PEP URL or a bare ``PEP 758`` / ``pep-758``; else None."""
    s = (url or "").strip()
    m = _PEP_URL_RE.match(s) or _PEP_BARE_RE.match(s)
    return int(m.group(1)) if m else None


def _headers(rst_text: str) -> dict[str, str]:
    """Parse the leading RFC-822 style header block (continuations start with whitespace)."""
    out: dict[str, str] = {}
    key: str | None = None
    for line in rst_text.splitlines():
        if not line.strip():
            if out:
                break
            continue
        if line[0] in " \t" and key:
            out[key] += " " + line.strip()
            continue
        k, sep, v = line.partition(":")
        if not sep or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", k):
            break
        key = k.lower()
        out[key] = v.strip()
    return out


_URL_RE = re.compile(r"https?://[^\s<>`\"')\]]+")


def pep_discussion_urls(rst_text: str) -> list[str]:
    """Discourse thread URLs from the header: Discussions-To first, then Post-History latest first."""
    h = _headers(rst_text)
    cands: list[str] = []
    cands += _URL_RE.findall(h.get("discussions-to", ""))
    cands += list(reversed(_URL_RE.findall(h.get("post-history", ""))))
    seen: set[str] = set()
    out: list[str] = []
    for u in cands:
        u = u.rstrip(".,;")
        if parse_discourse_url(u) is None or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def resolve_pep(number: int, *, client: httpx.Client | None = None) -> tuple[str, int, str]:
    """Return ``(base_url, topic_id, pep_title)`` of the PEP's discussion thread."""
    own = client is None
    c = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        text = None
        for ext in ("rst", "md"):
            try:
                r = c.get(RAW_URL.format(number=number, ext=ext), headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as exc:
                raise SourceError(f"Could not reach GitHub: {exc.__class__.__name__}", "network") from exc
            if r.status_code == 404:
                continue
            if r.status_code >= 400:
                raise SourceError(f"Could not fetch PEP {number}: HTTP {r.status_code}", "network")
            text = r.text
            break
    finally:
        if own:
            c.close()
    if text is None:
        raise SourceError(f"PEP {number} was not found.", "not_found")
    title = _headers(text).get("title", "") or f"PEP {number}"
    for u in pep_discussion_urls(text):
        parsed = parse_discourse_url(u)
        if parsed:
            return parsed[0], parsed[1], title
    raise SourceError(
        f"PEP {number} has no discuss.python.org thread; discussions on mailing lists are not supported. "
        "Paste a discuss.python.org thread URL instead.",
        "no_discussion",
    )
