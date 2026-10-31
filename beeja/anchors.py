"""Deterministic code anchors: explicit file paths, C/Python symbols and PR/issue/commit
references that the discussion itself mentions. Nothing is inferred from outside the text."""

from __future__ import annotations

import re
from collections import defaultdict

from .schema import CodeAnchor, Discussion

_PATH = re.compile(r"(?<![\w/.-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_-]+\.(?:c|h|py|pyi|rs|go|ts|js|toml|cfg|rst))\b")
_FILE = re.compile(r"(?<![\w/.-])([A-Za-z0-9_-]+\.(?:c|h|py|pyi|rs|go|toml))\b")
_CSYM = re.compile(r"\b(_?Py[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*)\b")
_PYSYM = re.compile(r"\b((?:sys|os|typing|asyncio|importlib|tomllib|configparser|ast|dis|inspect)\.[a-z_][a-z0-9_]*)\b")
_GH = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/(pull|issues)/(\d+)")
_GHSHORT = re.compile(r"\bgh-(\d{3,6})\b")
_BPO = re.compile(r"\bbpo-(\d{3,6})\b")

# words that look like symbols but name projects or products
_NOT_SYMBOLS = {"PyPy", "PyTorch", "PyPI", "PyCon", "PyCharm", "PyYAML", "PyQt", "PySide", "PyO3"}
_NOT_FILES = {"setup.py"}
# real symbols, but so common that matching them would flag almost any change
_TOO_GENERIC = {"os.path", "sys.path", "sys.argv", "sys.exit", "sys.modules", "sys.stdout", "sys.stderr", "os.environ"}


def find_anchors(d: Discussion, max_anchors: int = 40) -> list[CodeAnchor]:
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    repo: dict[tuple[str, str], str | None] = {}

    def add(kind: str, value: str, mid: str, repository: str | None = None) -> None:
        key = (kind, value)
        if mid not in seen[key]:
            seen[key].append(mid)
        repo.setdefault(key, repository)

    for m in d.messages:
        body = m.body
        text = re.sub(r"https?://\S+", " ", body)  # paths inside URLs belong to other projects
        paths = set()
        for p in _PATH.findall(text):
            if re.search(r"/(blob|tree|raw|commit)/", p) or p.split("/")[0] in {"github.com", "gitlab.com", "www"}:
                continue  # a link into some other repository, not this project's code
            paths.add(p)
            add("path", p, m.message_id)
        for f in _FILE.findall(text):
            if f not in _NOT_FILES and not any(p.endswith("/" + f) for p in paths):
                add("path", f, m.message_id)
        for s in _CSYM.findall(body):
            if s not in _NOT_SYMBOLS:
                add("symbol", s, m.message_id)
        for s in _PYSYM.findall(body):
            if s not in _TOO_GENERIC:
                add("symbol", s, m.message_id)
        for r, kind, n in _GH.findall(body):
            add("pr" if kind == "pull" else "issue", f"{r}#{n}", m.message_id, r)
        for n in _GHSHORT.findall(body):
            add("issue", f"gh-{n}", m.message_id)
        for n in _BPO.findall(body):
            add("issue", f"bpo-{n}", m.message_id)

    order = {m.message_id: m.position for m in d.messages}
    ranked = sorted(seen.items(), key=lambda kv: (-len(kv[1]), order.get(kv[1][0], 0), kv[0]))
    return [
        CodeAnchor(kind=k, value=v, repository=repo[(k, v)], note="mentioned in " + ", ".join(mids[:6]))
        for (k, v), mids in ranked[:max_anchors]
    ]
