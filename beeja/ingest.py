"""Normalise local discussion files (.txt, .md, .json) into a ``Discussion``.

Supported JSON shapes:
  * Beeja native: ``{"thread_id": ..., "messages": [{"message_id": ..., "body": ...}, ...]}``
  * Discourse topic export (``/t/<id>.json`` with all posts in ``post_stream.posts``)
  * GitHub issue/PR comments: a list of comment objects (``id``, ``user.login``, ``body``)

Plain text / Markdown: a message starts with a header line ``[id] author (role): text``
(role optional). Files with no header lines are split on lines containing only ``---``.
"""

from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .schema import Discussion, SourceMessage


class IngestError(Exception):
    pass


def body_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _finalise(d: Discussion) -> Discussion:
    seen = set()
    for i, m in enumerate(d.messages):
        if m.message_id in seen:
            raise IngestError(f"duplicate message_id {m.message_id!r}")
        seen.add(m.message_id)
        m.position = i
        m.source_snapshot_hash = body_hash(m.body)
    return d


# ------------------------------------------------------------------ HTML


class _HTMLText(HTMLParser):
    """Discourse 'cooked' HTML -> readable text. Quoted replies are kept, prefixed with '> '."""

    BLOCK = {"p", "div", "li", "pre", "blockquote", "aside", "h1", "h2", "h3", "h4", "tr", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.quote_depth = 0
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if tag == "aside" and "quote" in cls:
            self.quote_depth += 1
        if tag == "div" and "title" in cls.split() and self.quote_depth:
            self.skip += 1  # "username said:" header of a quote
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "aside" and self.quote_depth:
            self.quote_depth -= 1
            self.parts.append("\n")
        if tag == "div" and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self.quote_depth:
            data = "\n".join("> " + ln if ln.strip() else ln for ln in data.split("\n"))
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [ln.rstrip() for ln in raw.split("\n")]
        out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
        return out.strip()


def html_to_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html or "")
    p.close()
    return p.text()


# ------------------------------------------------------------------ JSON shapes


def from_discourse(data: dict[str, Any], base_url: str | None = None) -> Discussion:
    topic_id = str(data.get("id"))
    slug = data.get("slug") or "topic"
    base = (base_url or data.get("_beeja_base_url") or "").rstrip("/")
    messages = []
    for post in data["post_stream"]["posts"]:
        n = post["post_number"]
        role = None
        if post.get("admin"):
            role = "admin"
        elif post.get("moderator"):
            role = "moderator"
        elif post.get("staff"):
            role = "staff"
        reply = post.get("reply_to_post_number")
        messages.append(
            SourceMessage(
                message_id=f"post-{n}",
                thread_id=topic_id,
                platform="discourse",
                source_id=str(post.get("id")),
                parent_message_id=f"post-{reply}" if reply else None,
                reply_to=f"post-{reply}" if reply else None,
                author_id=post.get("username"),
                author_display_name=post.get("name") or post.get("username"),
                author_role=role,
                created_at=post.get("created_at"),
                source_url=f"{base}/t/{slug}/{topic_id}/{n}" if base else None,
                body=html_to_text(post.get("cooked", "")),
            )
        )
    return Discussion(
        thread_id=topic_id,
        title=data.get("title"),
        platform="discourse",
        source_url=f"{base}/t/{slug}/{topic_id}" if base else None,
        messages=messages,
        metadata={"posts_count": data.get("posts_count"), "created_at": data.get("created_at")},
    )


def from_github_comments(items: list[dict[str, Any]], thread_id: str) -> Discussion:
    messages = []
    for c in items:
        user = c.get("user") or {}
        reply = c.get("in_reply_to_id")
        messages.append(
            SourceMessage(
                message_id=f"comment-{c['id']}",
                thread_id=thread_id,
                platform="github",
                source_id=str(c["id"]),
                parent_message_id=f"comment-{reply}" if reply else None,
                reply_to=f"comment-{reply}" if reply else None,
                author_id=user.get("login"),
                author_display_name=user.get("login"),
                author_role=(c.get("author_association") or None),
                created_at=c.get("created_at"),
                source_url=c.get("html_url"),
                body=c.get("body") or "",
            )
        )
    return Discussion(thread_id=thread_id, platform="github", messages=messages)


def from_native(data: dict[str, Any]) -> Discussion:
    thread_id = str(data.get("thread_id") or "thread")
    msgs = []
    for i, m in enumerate(data.get("messages", [])):
        m = dict(m)
        m.setdefault("thread_id", thread_id)
        m.setdefault("message_id", f"m{i + 1}")
        m.setdefault("platform", data.get("platform", "unknown"))
        msgs.append(SourceMessage.model_validate(m))
    rest = {k: v for k, v in data.items() if k not in {"messages", "thread_id"}}
    return Discussion(thread_id=thread_id, messages=msgs, **rest)


# ------------------------------------------------------------------ text shapes

_HEADER = re.compile(r"^\[(?P<id>[A-Za-z0-9_.:-]+)\]\s*(?P<author>[^:()\n]+?)(?:\s*\((?P<role>[^)]+)\))?\s*:\s?(?P<rest>.*)$")


def from_text(text: str, thread_id: str) -> Discussion:
    lines = text.splitlines()
    messages: list[SourceMessage] = []
    title = None
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        lines = lines[1:]
    if any(_HEADER.match(ln) for ln in lines):
        cur = None
        buf: list[str] = []

        def flush():
            if cur is not None:
                messages.append(cur.model_copy(update={"body": "\n".join(buf).strip()}))

        for ln in lines:
            m = _HEADER.match(ln)
            if m:
                flush()
                cur = SourceMessage(
                    message_id=m["id"],
                    thread_id=thread_id,
                    platform="text",
                    author_id=m["author"].strip(),
                    author_display_name=m["author"].strip(),
                    author_role=(m["role"] or None),
                    body="",
                )
                buf = [m["rest"]]
            elif cur is not None:
                buf.append(ln)
        flush()
    else:
        blocks = re.split(r"^\s*---+\s*$", "\n".join(lines), flags=re.M)
        for i, b in enumerate(x for x in blocks if x.strip()):
            messages.append(
                SourceMessage(message_id=f"m{i + 1}", thread_id=thread_id, platform="text", body=b.strip())
            )
    return Discussion(thread_id=thread_id, title=title, platform="text", messages=messages)


# ------------------------------------------------------------------ entry point


def load_discussion(path: str | Path) -> Discussion:
    path = Path(path)
    if not path.is_file():
        raise IngestError(f"input file not found: {path}")
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    stem = re.sub(r"[^a-z0-9_-]+", "-", path.stem.lower()).strip("-") or "thread"
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IngestError(f"{path}: invalid JSON: {exc}") from exc
        if isinstance(data, dict) and "post_stream" in data:
            d = from_discourse(data)
        elif isinstance(data, dict) and "messages" in data:
            d = from_native(data)
        elif isinstance(data, list) and all(isinstance(c, dict) and "body" in c for c in data):
            d = from_github_comments(data, thread_id=stem)
        else:
            raise IngestError(f"{path}: unrecognised JSON discussion format")
    elif suffix in {".txt", ".md", ".markdown"}:
        d = from_text(text, thread_id=stem)
    else:
        raise IngestError(f"{path}: unsupported file type {suffix!r} (use .json, .txt or .md)")
    if not d.messages:
        raise IngestError(f"{path}: no messages found")
    return _finalise(d)
