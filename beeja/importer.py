"""One-step import: GitHub issue/PR, Discourse thread or Python PEP URL -> discussion -> extraction -> store -> viewer data.

Layout (all relative to the project root):

    imports/<case>/input/discussion.json   fetched thread (Beeja-native JSON, public data only)
    imports/<case>/input_path.txt          path of the input, as for examples/
    imports/<case>/model_exchanges.jsonl   recorded real model exchanges (replayable)
    imports/<case>/decision.json           the extracted record
    imports/<case>/history/<UTC time>/     previous versions kept on re-import
    decisions/<decision id>.json           the same record in the CLI / MCP store
    web/public/data/                       viewer data, regenerated from examples/ AND imports/

A repeated import of the same URL is refused unless ``reimport=True``; re-importing moves the
previous record and its source data into ``history/`` instead of overwriting them. Nothing is
reported as done until the record validates and the viewer data containing it is written.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import storage
from .export import export_web
from .github import GitHubClient
from .ingest import load_discussion
from .model_adapter import ModelError, ModelProvider, RecordingProvider, load_dotenv, provider_from_env
from .schema import check_integrity

WRITE_LOCK = threading.Lock()  # serialises writes to the store, case folders and viewer data


class ImportError_(Exception):  # noqa: N801 - avoid shadowing the builtin ImportError
    def __init__(self, message: str, kind: str, **extra: Any) -> None:
        super().__init__(message)
        self.kind = kind
        self.extra = extra


def roots(root: Path) -> list[Path]:
    return [p for p in (root / "examples", root / "imports") if p.is_dir()]


def refresh_viewer(root: Path) -> list[str]:
    """Regenerate viewer data from every source of records. Never drops imported decisions."""
    with WRITE_LOCK:
        return export_web(roots(root), root / "web" / "public" / "data")


def existing(root: Path, case: str) -> str | None:
    p = root / "imports" / case / "decision.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))["id"]
    return None


def import_github(url: str, **kw: Any) -> dict[str, Any]:
    """Backwards-compatible name; any supported URL works."""
    return import_url(url, **kw)


def import_url(
    url: str,
    *,
    root: Path = Path("."),
    reimport: bool = False,
    discover_related: bool = True,
    provider: ModelProvider | None = None,
    gh: GitHubClient | None = None,
    progress: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    from .extract import extract

    root = Path(root)
    load_dotenv(root / ".env")
    from . import sources

    try:
        src = sources.resolve(url)
        src = sources.locate(src, progress)
    except sources.SourceError as exc:
        raise ImportError_(str(exc), exc.kind) from exc
    case_dir = root / "imports" / src.case
    prev = existing(root, src.case)
    if prev and not reimport:
        raise ImportError_(f"{src.url} was already imported as {prev}. Open it, or re-import to "
                           "replace it (the current version is kept in history).", "exists", decision_id=prev)

    progress(f"Fetching {src.url}…")
    try:
        data = sources.fetch(src, gh=gh, progress=progress)
    except sources.SourceError as exc:
        raise ImportError_(str(exc), exc.kind) from exc
    if src.kind == "github":
        gh_meta = data["metadata"]["github"]
        source_meta = {"github": {k: gh_meta[k] for k in ("owner", "repo", "kind", "number", "state", "omitted")}}
    else:
        source_meta = {"discourse": {"url": src.url, **{k: v for k, v in src.detail.items() if k in ("pep", "pep_title")}}}

    staging = case_dir / ".staging"
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "input").mkdir(parents=True)
    rel_input = Path("imports") / src.case / "input" / "discussion.json"
    (staging / "input" / "discussion.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    try:
        discussion = load_discussion(staging / "input" / "discussion.json")
        if provider is None:
            provider = provider_from_env(record_to=staging / "model_exchanges.jsonl")
        progress(f"Extracting with {provider.name} ({len(discussion.messages)} messages)…")
        record = extract(discussion, provider, progress=progress)
        if isinstance(provider, RecordingProvider):
            provider.compact()
    except ModelError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        msg = str(exc)
        kind = "model_unreachable" if any(s in msg.lower() for s in ("connect", "refused", "resolve", "timed out")) else "model"
        hint = (" Is the model server running? Check BEEJA_PROVIDER / BEEJA_MODEL / BEEJA_BASE_URL in .env."
                if kind == "model_unreachable" else "")
        raise ImportError_(f"Model error: {msg}.{hint}", kind) from exc
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    problems = check_integrity(record)
    if problems:
        shutil.rmtree(staging, ignore_errors=True)
        raise ImportError_("Extracted record failed validation: " + "; ".join(problems), "invalid")
    record.metadata.update(source_meta)
    # never replace a record this import does not own (e.g. a bundled example of the same thread)
    owned = {prev} if prev else set()
    if (root / "decisions" / f"{record.id}.json").is_file() and record.id not in owned:
        record.id = f"{record.id[:100]}-import"
    record.metadata["imported_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (staging / "decision.json").write_text(storage.dumps(record), encoding="utf-8")
    (staging / "input_path.txt").write_text(rel_input.as_posix() + "\n")

    with WRITE_LOCK:
        archived = None
        if (case_dir / "decision.json").is_file():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archived = case_dir / "history" / stamp
            archived.mkdir(parents=True)
            old_id = json.loads((case_dir / "decision.json").read_text())["id"]
            for name in ("decision.json", "input", "input_path.txt", "model_exchanges.jsonl"):
                if (case_dir / name).exists():
                    shutil.move(str(case_dir / name), str(archived / name))
            for stale in (root / "decisions" / f"{old_id}.json", root / "web" / "public" / "data" / f"{old_id}.json"):
                stale.unlink(missing_ok=True)
        for item in staging.iterdir():
            shutil.move(str(item), str(case_dir / item.name))
        staging.rmdir()
        storage.save(record, root / "decisions")
        written = export_web(roots(root), root / "web" / "public" / "data")
    if record.id not in written or not (root / "web" / "public" / "data" / f"{record.id}.json").is_file():
        raise ImportError_("Record was saved but the viewer data could not be written.", "export")
    counts: dict[str, int] = {}
    for a in record.alternatives:
        counts[a.status] = counts.get(a.status, 0) + 1
    if src.kind == "github" and discover_related:
        from .discovery import discover
        from .github import token_from_env

        def import_related(link):
            return import_url(link, root=root, gh=gh, discover_related=False, progress=progress)

        record.metadata["related_history"] = discover(
            data, root, gh or GitHubClient(token_from_env()), import_related, progress)
        with WRITE_LOCK:
            (case_dir / "decision.json").write_text(storage.dumps(record), encoding="utf-8")
            storage.save(record, root / "decisions")
            export_web(roots(root), root / "web" / "public" / "data")
    progress(f"Saved {record.id}: {record.summary}")
    return {
        "decision_id": record.id, "case": src.case, "url": src.url, "label": src.label, "summary": record.summary,
        "status": record.status, "counts": counts, "messages": len(discussion.messages),
        "evidence_verified": record.metadata.get("evidence_verified"),
        "evidence_total": record.metadata.get("evidence_total"),
        "archived_previous": str(archived.relative_to(root)) if archived else None,
        "model": provider.name,
    }
