"""Turn a pasted URL into a discussion Beeja can extract from.

Supported:
  * GitHub issue / pull request URLs                      -> beeja.github
  * Discourse topic URLs (discuss.python.org or any other) -> beeja.discourse
  * Python PEPs: https://peps.python.org/pep-0758/, "PEP 758" -> the PEP's discuss.python.org
    thread, found from its Discussions-To / Post-History headers (beeja.pep).

For a PEP only the *discussion* is imported, never the PEP text itself: that would hand the
model the final answer (the P6 evaluation gives discussions without the PEP).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .github import GitHubClient, GitHubError, fetch_discussion, parse_url, token_from_env


class SourceError(Exception):
    def __init__(self, message: str, kind: str, **extra: Any) -> None:
        super().__init__(message)
        self.kind = kind
        self.extra = extra


@dataclass
class Source:
    kind: str  # "github" | "discourse" | "pep"
    url: str
    case: str | None  # known without network for github/discourse; resolved later for a PEP
    label: str
    detail: dict[str, Any] = field(default_factory=dict)


def _host_slug(base: str) -> str:
    host = re.sub(r"^https?://", "", base).split("/")[0]
    return re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")


def discourse_case(base: str, topic_id: int) -> str:
    return f"discourse-{_host_slug(base)}-{topic_id}"


def resolve(url: str) -> Source:
    """Classify a URL without touching the network."""
    from . import pep

    text = (url or "").strip()
    if "github.com/" in text:
        try:
            ref = parse_url(text)
        except GitHubError as exc:
            raise SourceError(str(exc), exc.kind) from exc
        return Source("github", ref.html_url, ref.case, f"{ref.owner}/{ref.repo}#{ref.number}", {"ref": ref})
    n = pep.parse_pep_url(text)
    if n is not None:
        return Source("pep", f"https://peps.python.org/pep-{n:04d}/", None, f"PEP {n}", {"number": n})
    d = pep.parse_discourse_url(text)
    if d is not None:
        base, topic = d
        return Source("discourse", f"{base}/t/{topic}", discourse_case(base, topic), f"{_host_slug(base)} topic {topic}",
                      {"base": base, "topic": topic})
    raise SourceError(
        "Unsupported URL. Paste a GitHub issue/PR URL, a Discourse thread URL (e.g. "
        "https://discuss.python.org/t/<slug>/<id>) or a PEP (https://peps.python.org/pep-0758/ or 'PEP 758').",
        "invalid_url")


def locate(src: Source, progress: Callable[[str], None] = lambda _m: None) -> Source:
    """Resolve a PEP to its discussion thread (network). Other sources are returned unchanged."""
    from . import pep

    if src.kind != "pep":
        return src
    try:
        base, topic, title = pep.resolve_pep(src.detail["number"])
    except pep.SourceError as exc:
        raise SourceError(str(exc), exc.kind) from exc
    progress(f"{src.label} ({title}) is discussed at {base}/t/{topic} — importing that thread, not the PEP text.")
    return Source("discourse", f"{base}/t/{topic}", discourse_case(base, topic), src.label,
                  {"base": base, "topic": topic, "pep": src.detail["number"], "pep_title": title})


def fetch(src: Source, *, gh: GitHubClient | None = None, progress: Callable[[str], None] = lambda _m: None) -> dict:
    """Return discussion JSON that ``ingest.load_discussion`` understands."""
    from .discourse import FetchError, fetch_topic

    if src.kind == "github":
        gh = gh or GitHubClient(token_from_env())
        try:
            data = fetch_discussion(src.detail["ref"], gh)
        except GitHubError as exc:
            raise SourceError(str(exc), exc.kind) from exc
        progress(f"Fetched {len(data['messages'])} messages ({gh.requests} GitHub requests, "
                 f"{'authenticated' if gh.authenticated else 'anonymous'}).")
        return data
    if src.kind == "discourse":
        try:
            topic = fetch_topic(src.detail["base"], src.detail["topic"])
        except FetchError as exc:
            msg = str(exc)
            kind = "not_found" if ("404" in msg or "403" in msg or "not a Discourse" in msg) else "network"
            raise SourceError(f"Could not fetch the thread: {msg}", kind) from exc
        posts = topic.get("post_stream", {}).get("posts", [])
        if len(posts) < 2:
            raise SourceError("This thread has no replies yet, so there is no decision history to recover.", "empty")
        topic["_beeja_meta"] = {k: v for k, v in src.detail.items() if k in ("pep", "pep_title")}
        progress(f"Fetched {len(posts)} posts from {src.url}.")
        return topic
    raise SourceError(f"Cannot fetch a source of kind {src.kind!r}.", "internal")
