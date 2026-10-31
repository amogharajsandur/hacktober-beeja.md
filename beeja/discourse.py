"""Optional network ingestion of a public Discourse topic via its JSON endpoints.

Only used by ``beeja fetch-discourse``; extraction and benchmarks work fully offline on
the saved JSON.
"""

from __future__ import annotations

import time

import httpx

USER_AGENT = "beeja/0.1 (+https://github.com/amogharajsandur/hacktober-beeja.md)"


class FetchError(Exception):
    pass


def _get_json(c: httpx.Client, url: str, params=None, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = c.get(url, params=params)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            if r.status_code in (403, 404):
                raise FetchError(f"{url}: HTTP {r.status_code} (private, deleted or not a Discourse topic?)")
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or "post_stream" not in data:
                raise FetchError(f"{url}: response is not a Discourse topic (no post_stream)")
            return data
        except (httpx.HTTPError, ValueError) as exc:  # network error, 429/5xx, invalid JSON
            last = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise FetchError(f"{url}: failed after {retries + 1} attempts: {last}")


def fetch_topic(base_url: str, topic_id: int, *, delay: float = 1.0, timeout: float = 30.0) -> dict:
    """Return the topic JSON with *all* posts in ``post_stream.posts``.

    Discourse returns ~20 posts with the topic; the rest are fetched in batches through
    ``/t/<id>/posts.json?post_ids[]=...``. A short delay between requests respects rate limits.
    """
    base = base_url.rstrip("/")
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
        topic = _get_json(c, f"{base}/t/{topic_id}.json")
        stream = topic["post_stream"].get("stream") or [p["id"] for p in topic["post_stream"].get("posts", [])]
        have = {p["id"] for p in topic["post_stream"]["posts"]}
        missing = [pid for pid in stream if pid not in have]
        for i in range(0, len(missing), 20):
            time.sleep(delay)
            batch = missing[i : i + 20]
            page = _get_json(c, f"{base}/t/{topic_id}/posts.json", params=[("post_ids[]", p) for p in batch])
            topic["post_stream"]["posts"].extend(page["post_stream"].get("posts", []))
    topic["post_stream"]["posts"].sort(key=lambda p: p["post_number"])
    topic["_beeja_base_url"] = base
    return topic
