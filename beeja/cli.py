"""Command-line interface. Every command can emit machine-readable JSON.

Exit codes: 0 success, 1 validation/content problem, 2 usage or input error, 3 model error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, storage
from .ingest import IngestError, load_discussion
from .model_adapter import ModelError, provider_from_env
from .schema import json_schema

EXIT_OK, EXIT_INVALID, EXIT_USAGE, EXIT_MODEL = 0, 1, 2, 3


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_validate(args: argparse.Namespace) -> int:
    missing = [p for p in args.paths if not Path(p).exists()]
    if missing:
        print(f"error: path not found: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    report = storage.validate_paths(Path(p) for p in args.paths)
    ok = all(not v for v in report.values()) and bool(report)
    if args.json:
        _print_json({"valid": ok, "files": report})
    else:
        for f, problems in report.items():
            print(f"{'OK  ' if not problems else 'FAIL'} {f}")
            for p in problems:
                print(f"     - {p}")
        if not report:
            print("no decision files found")
    return EXIT_OK if ok else EXIT_INVALID


def cmd_extract(args: argparse.Namespace) -> int:
    from .extract import extract

    try:
        discussion = load_discussion(args.input)
        provider = provider_from_env(
            record_to=Path(args.record) if args.record else None,
            replay_from=Path(args.replay) if args.replay else None,
        )
    except (IngestError, ModelError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    def progress(msg: str) -> None:
        if not args.quiet:
            print(f"[beeja] {msg}", file=sys.stderr)

    progress(f"{len(discussion.messages)} messages from {args.input}; model {provider.name}")
    try:
        record = extract(discussion, provider, max_chars=args.chunk_chars, progress=progress)
    except ModelError as exc:
        print(f"error: model failure: {exc}", file=sys.stderr)
        return EXIT_MODEL
    text = storage.dumps(record)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
        progress(f"wrote {args.output}")
    if args.store:
        progress(f"stored {storage.save(record, Path(args.store))}")
    if not args.output or args.json:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_fetch_discourse(args: argparse.Namespace) -> int:
    from .discourse import fetch_topic

    try:
        topic = fetch_topic(args.base_url, args.topic_id)
    except Exception as exc:  # network errors are reported, not raised
        print(f"error: fetch failed: {exc}", file=sys.stderr)
        return EXIT_USAGE
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(topic, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"saved {len(topic['post_stream']['posts'])} posts to {args.output}", file=sys.stderr)
    return EXIT_OK


def cmd_schema(args: argparse.Namespace) -> int:
    _print_json(json_schema())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="beeja",
        description="Beeja: recover evidence-backed engineering decisions from discussions.",
    )
    p.add_argument("--version", action="version", version=f"beeja {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate decision JSON files or directories")
    v.add_argument("paths", nargs="+")
    v.add_argument("--json", action="store_true", help="emit JSON report")
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser("extract", help="extract a decision record from a discussion file")
    e.add_argument("--input", "-i", required=True, help="discussion file (.json, .txt, .md)")
    e.add_argument("--output", "-o", help="write the decision record JSON here")
    e.add_argument("--store", help="also save into this decision store directory (e.g. decisions/)")
    e.add_argument("--record", help="append raw model exchanges to this JSONL file")
    e.add_argument("--replay", help="replay model exchanges from this JSONL file (no live model)")
    e.add_argument("--chunk-chars", type=int, default=7000, help="max characters per model chunk")
    e.add_argument("--json", action="store_true", help="print the record to stdout even with --output")
    e.add_argument("--quiet", "-q", action="store_true")
    e.set_defaults(func=cmd_extract)

    f = sub.add_parser("fetch-discourse", help="download a public Discourse topic as JSON (network)")
    f.add_argument("topic_id", type=int)
    f.add_argument("--base-url", default="https://discuss.python.org")
    f.add_argument("--output", "-o", required=True)
    f.set_defaults(func=cmd_fetch_discourse)

    s = sub.add_parser("schema", help="print the DecisionRecord JSON Schema")
    s.set_defaults(func=cmd_schema)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
