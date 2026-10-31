"""Optional network ingestion of a public Discourse topic via its JSON endpoints.

Only used by ``beeja fetch-discourse``; extraction and benchmarks work fully offline on
the saved JSON.
"""

from __future__ import annotations

import time

import httpx

USER_AGENT = "beeja/0.1 (+https://github.com/amogharajsandur/hacktober-beeja.md)"


def fetch_topic(base_url: str, topic_id: int, *, delay: float = 1.0, timeout: float = 30.0) -> dict:
    """Return the topic JSON with *all* posts in ``post_stream.posts``.

    Discourse returns ~20 posts with the topic; the rest are fetched in batches through
    ``/t/<id>/posts.json?post_ids[]=...``. A short delay between requests respects rate limits.
    """
    base = base_url.rstrip("/")
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
        r = c.get(f"{base}/t/{topic_id}.json")
        r.raise_for_status()
        topic = r.json()
        stream = topic["post_stream"]["stream"]
        have = {p["id"] for p in topic["post_stream"]["posts"]}
        missing = [pid for pid in stream if pid not in have]
        for i in range(0, len(missing), 20):
            time.sleep(delay)
            batch = missing[i : i + 20]
            r = c.get(f"{base}/t/{topic_id}/posts.json", params=[("post_ids[]", p) for p in batch])
            r.raise_for_status()
            topic["post_stream"]["posts"].extend(r.json()["post_stream"]["posts"])
    topic["post_stream"]["posts"].sort(key=lambda p: p["post_number"])
    topic["_beeja_base_url"] = base
    return topic
