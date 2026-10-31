"""Canonical JSON storage: one deterministic file per decision."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from .schema import DecisionRecord, check_integrity

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class StorageError(Exception):
    pass


def dumps(record: DecisionRecord) -> str:
    """Deterministic serialization: sorted keys, 2-space indent, trailing newline."""
    data = record.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def safe_id(record_id: str) -> str:
    if not _ID_RE.match(record_id):
        raise StorageError(f"unsafe decision id {record_id!r}")
    return record_id


def save(record: DecisionRecord, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_id(record.id)}.json"
    path.write_text(dumps(record), encoding="utf-8")
    return path


def load_file(path: Path) -> list[DecisionRecord]:
    """Load a file holding either one decision object or a list of them."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"{path}: cannot read JSON: {exc}") from exc
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for i, item in enumerate(items):
        try:
            out.append(DecisionRecord.model_validate(item))
        except ValidationError as exc:
            raise StorageError(f"{path}[{i}]: schema validation failed:\n{exc}") from exc
    return out


def load_dir(directory: Path) -> list[DecisionRecord]:
    directory = Path(directory)
    if not directory.is_dir():
        raise StorageError(f"decision store {directory} does not exist")
    records: list[DecisionRecord] = []
    for p in sorted(directory.glob("*.json")):
        records.extend(load_file(p))
    return records


def validate_paths(paths: Iterable[Path]) -> dict[str, list[str]]:
    """Validate files or directories. Returns {path: [problems]} (empty list = valid)."""
    report: dict[str, list[str]] = {}
    for path in paths:
        path = Path(path)
        files = sorted(path.glob("*.json")) if path.is_dir() else [path]
        for f in files:
            try:
                recs = load_file(f)
            except StorageError as exc:
                report[str(f)] = [str(exc)]
                continue
            problems: list[str] = []
            for r in recs:
                problems.extend(f"{r.id}: {p}" for p in check_integrity(r))
            report[str(f)] = problems
    return report
