"""Fetch a GitHub issue or pull request discussion into Beeja's native discussion format.

Public repositories work without authentication (60 requests/hour per IP). For a higher limit
or private repositories set ``GITHUB_TOKEN`` (or ``GH_TOKEN``) in the environment or ``.env``;
if neither is set and the GitHub CLI is logged in, ``gh auth token`` is used. The token is only
ever sent to api.github.com and never written anywhere.

Included: the issue/PR description, every issue comment (all pages), and for pull requests the
review submissions that have a body plus inline review comments (with their reply links).
Not included: reactions, edit history, linked commits, cross-referenced issues.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx

API = "https://api.github.com"
_URL = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/(issues|pull)/(\d+)(?:[/?#].*)?$")


class GitHubError(Exception):
    """A user-facing error; ``kind`` lets the UI show a specific hint."""

    def __init__(self, message: str, kind: str = "github") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Ref:
    owner: str
    repo: str
    kind: str  # "issue" | "pull"
    number: int

    @property
    def case(self) -> str:
        return f"gh-{self.owner}-{self.repo}-{'pr' if self.kind == 'pull' else 'issue'}-{self.number}".lower()

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/{'pull' if self.kind == 'pull' else 'issues'}/{self.number}"


def parse_url(url: str) -> Ref:
    m = _URL.match((url or "").strip())
    if not m:
        raise GitHubError(
            "Not a GitHub issue or pull request URL. Expected https://github.com/<owner>/<repo>/issues/<n> "
            "or .../pull/<n>.", "invalid_url")
    owner, repo, kind, n = m.groups()
    return Ref(owner, repo, "pull" if kind == "pull" else "issue", int(n))


def token_from_env() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


class GitHubClient:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "beeja"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.authenticated = bool(token)
        self._c = client or httpx.Client(timeout=timeout, headers=headers, base_url=API)
        if client is not None:
            self._c.headers.update(headers)
        self.requests = 0

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        try:
            r = self._c.get(path if path.startswith("http") else API + path, params=params)
        except httpx.HTTPError as exc:
            raise GitHubError(f"Could not reach GitHub: {exc.__class__.__name__}", "network") from exc
        self.requests += 1
        if r.status_code == 404:
            raise GitHubError("Issue or pull request not found, or the repository is private "
                              "(set GITHUB_TOKEN for private repositories).", "not_found")
        if r.status_code in (403, 429) and (r.headers.get("x-ratelimit-remaining") == "0" or r.status_code == 429):
            hint = "" if self.authenticated else " Set GITHUB_TOKEN (or log in with `gh auth login`) for 5,000 requests/hour."
            raise GitHubError(f"GitHub rate limit reached (resets at epoch {r.headers.get('x-ratelimit-reset', '?')}).{hint}",
                              "rate_limited")
        if r.status_code in (401, 403):
            raise GitHubError(f"GitHub refused access (HTTP {r.status_code}). Check GITHUB_TOKEN.", "forbidden")
        if r.status_code >= 400:
            raise GitHubError(f"GitHub API error HTTP {r.status_code}.", "github")
        return r

    def get_json(self, path: str) -> Any:
        return self._get(path).json()

    def get_all(self, path: str) -> list[dict[str, Any]]:
        """Follow ``Link: rel="next"`` pagination."""
        items: list[dict[str, Any]] = []
        url: str | None = path
        params: dict[str, Any] | None = {"per_page": 100}
        while url:
            r = self._get(url, params)
            page = r.json()
            if not isinstance(page, list):
                raise GitHubError("Unexpected response from GitHub (expected a list).", "github")
            items.extend(page)
            url = r.links.get("next", {}).get("url")
            params = None  # the next link already carries its query string
        return items


def _msg(mid: str, user: dict | None, body: str | None, created: str | None, url: str | None,
         role: str | None = None, reply_to: str | None = None) -> dict[str, Any]:
    login = (user or {}).get("login")
    return {"message_id": mid, "author_id": login, "author_display_name": login, "author_role": role or None,
            "created_at": created, "source_url": url, "body": (body or "").strip(), "reply_to": reply_to,
            "parent_message_id": reply_to, "platform": "github"}


def fetch_discussion(ref: Ref, gh: GitHubClient) -> dict[str, Any]:
    """Return a Beeja-native discussion dict (see ``ingest.from_native``)."""
    base = f"/repos/{ref.owner}/{ref.repo}"
    issue = gh.get_json(f"{base}/issues/{ref.number}")
    is_pr = "pull_request" in issue
    if ref.kind == "issue" and is_pr:
        ref = Ref(ref.owner, ref.repo, "pull", ref.number)
    if ref.kind == "pull" and not is_pr:
        raise GitHubError("That number is an issue, not a pull request.", "not_found")
    messages = [_msg("description", issue.get("user"), issue.get("body"), issue.get("created_at"),
                     issue.get("html_url"), issue.get("author_association"))]
    for c in gh.get_all(f"{base}/issues/{ref.number}/comments"):
        messages.append(_msg(f"comment-{c['id']}", c.get("user"), c.get("body"), c.get("created_at"),
                             c.get("html_url"), c.get("author_association")))
    omitted: list[str] = ["reactions", "edit history"]
    if ref.kind == "pull":
        for rv in gh.get_all(f"{base}/pulls/{ref.number}/reviews"):
            if (rv.get("body") or "").strip():
                state = (rv.get("state") or "").lower().replace("_", " ")
                messages.append(_msg(f"review-{rv['id']}", rv.get("user"), f"[review: {state}] {rv['body']}",
                                     rv.get("submitted_at"), rv.get("html_url"), rv.get("author_association")))
        for c in gh.get_all(f"{base}/pulls/{ref.number}/comments"):
            reply = f"review-comment-{c['in_reply_to_id']}" if c.get("in_reply_to_id") else None
            where = f"[on {c.get('path')}] " if c.get("path") else ""
            messages.append(_msg(f"review-comment-{c['id']}", c.get("user"), where + (c.get("body") or ""),
                                 c.get("created_at"), c.get("html_url"), c.get("author_association"), reply))
        omitted.append("commits and code diffs")
    messages = [m for m in messages if m["body"]]
    messages.sort(key=lambda m: (m["message_id"] != "description", m["created_at"] or ""))
    if len(messages) < 2:
        raise GitHubError("This thread has no discussion yet (only a description, or nothing). There is no "
                          "decision history to recover.", "empty")
    return {
        "thread_id": ref.case,
        "title": f"{ref.owner}/{ref.repo}#{ref.number}: {issue.get('title') or ''}".strip(),
        "platform": "github",
        "source_url": ref.html_url,
        "messages": messages,
        "metadata": {"github": {"owner": ref.owner, "repo": ref.repo, "kind": ref.kind, "number": ref.number,
                                "state": issue.get("state"), "fetched_messages": len(messages),
                                "omitted": omitted}},
    }
