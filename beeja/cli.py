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
    import os

    from .extract import extract

    for flag, var in ((args.provider, "BEEJA_PROVIDER"), (args.model, "BEEJA_MODEL"), (args.base_url, "BEEJA_BASE_URL")):
        if flag:
            os.environ[var] = flag
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
    if any(provider.name.startswith(p + ":") for p in ("openai", "anthropic", "gemini", "xai")):
        print("[beeja] warning: this is a closed-model API. The hackathon rules score closed models 0 for "
              "open-source AI use; the declared model is qwen3:4b (local).", file=sys.stderr)
    try:
        record = extract(
            discussion, provider, max_chars=args.chunk_chars, support_check=not args.no_support_check,
            outcome_pass=not args.no_outcome_pass, progress=progress
        )
    except ModelError as exc:
        print(f"error: model failure: {exc}", file=sys.stderr)
        return EXIT_MODEL
    if hasattr(provider, "compact"):
        provider.compact()
        progress(f"recording: {provider.reused} exchanges reused, {len(provider.used) - provider.reused} new")
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


def cmd_benchmark(args: argparse.Namespace) -> int:
    from .benchmark import render_markdown, run

    if not Path(args.dataset).is_dir():
        print(f"error: dataset directory not found: {args.dataset}", file=sys.stderr)
        return EXIT_USAGE
    try:
        report = run(Path(args.dataset), Path(args.outputs))
    except storage.StorageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID
    md = render_markdown(report)
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    if args.json:
        _print_json(report)
    else:
        sys.stdout.write(md)
    return EXIT_OK if report["summary"] else EXIT_INVALID


def _load_store(path: str):
    try:
        return storage.load_dir(Path(path))
    except storage.StorageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def cmd_search(args: argparse.Namespace) -> int:
    from .retrieval import search

    recs = _load_store(args.store)
    if recs is None:
        return EXIT_USAGE
    hits = search(recs, args.query, limit=args.limit, negative_only=args.negative_only)
    if args.json:
        _print_json({"query": args.query, "results": [h.as_dict() for h in hits]})
        return EXIT_OK
    if not hits:
        print("no matching decisions")
    for h in hits:
        ref = h.decision_id + (f"#{h.alternative_id}" if h.alternative_id else "")
        print(f"{h.score:7.2f}  [{h.status}] {ref}  {h.title}")
        if h.reason:
            print(f"         why: {h.reason}")
        for e in h.evidence[:1]:
            print(f"         proof: \"{e['quote'][:150]}\" ({e['source_url'] or e['message_id']})")
    return EXIT_OK


def cmd_get(args: argparse.Namespace) -> int:
    from .retrieval import get

    recs = _load_store(args.store)
    if recs is None:
        return EXIT_USAGE
    found = get(recs, args.ref)
    if found is None:
        print(f"error: no decision {args.ref!r} in {args.store}", file=sys.stderr)
        return EXIT_INVALID
    rec, alt = found
    data = rec.model_dump(mode="json")
    if alt is not None:
        ev = rec.evidence_by_id()
        ids = set(alt.status_evidence + alt.supporting_evidence + alt.opposing_evidence)
        ids.update(i for r in alt.reasons for i in r.evidence_ids)
        data = {"decision_id": rec.id, "question": rec.question, "alternative": alt.model_dump(mode="json"),
                "evidence": [ev[i].model_dump(mode="json") for i in sorted(ids) if i in ev]}
    _print_json(data)
    return EXIT_OK


def cmd_why_not(args: argparse.Namespace) -> int:
    from .retrieval import why_not

    recs = _load_store(args.store)
    if recs is None:
        return EXIT_USAGE
    out = why_not(recs, args.idea)
    if args.json:
        _print_json({"idea": args.idea, "answers": out})
        return EXIT_OK
    if not out:
        print("No rejected, withdrawn or deferred alternative in the store matches that idea.")
    for o in out:
        print(f"[{o['status']}/{o['rejection_type'] or '-'}, confidence {o['confidence']}] {o['title']}")
        for e in o["outcome_evidence"]:
            print(f"  outcome: \"{e['quote'][:200]}\" - {e['author'] or 'unknown'} {e['source_url'] or ''}")
        for r in o["reasons"]:
            mark = "verified" if r["verified"] else "UNVERIFIED"
            print(f"  - {r['reason']} ({mark})")
            for e in r["evidence"]:
                print(f"      \"{e['quote'][:200]}\" {e['source_url'] or e['message_id']}")
        for n in o["notes"]:
            print(f"  note: {n}")
    return EXIT_OK


def cmd_check_diff(args: argparse.Namespace) -> int:
    from .diff import check_diff, render_text

    recs = _load_store(args.store)
    if recs is None:
        return EXIT_USAGE
    try:
        text = sys.stdin.read() if args.patch == "-" else Path(args.patch).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read patch: {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = check_diff(recs, text)
    if args.json:
        _print_json(report)
    else:
        sys.stdout.write(render_text(report))
    return EXIT_OK


def cmd_export_web(args: argparse.Namespace) -> int:
    from .export import export_web

    if not Path(args.examples).is_dir():
        print(f"error: not a directory: {args.examples}", file=sys.stderr)
        return EXIT_USAGE
    ids = export_web(Path(args.examples), Path(args.out))
    print(f"exported {len(ids)} decisions to {args.out}", file=sys.stderr)
    return EXIT_OK if ids else EXIT_INVALID


def cmd_export_madr(args: argparse.Namespace) -> int:
    from .export import to_madr
    from .retrieval import get

    recs = _load_store(args.store)
    if recs is None:
        return EXIT_USAGE
    found = get(recs, args.ref)
    if found is None:
        print(f"error: no decision {args.ref!r}", file=sys.stderr)
        return EXIT_INVALID
    text = to_madr(found[0])
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_providers(args: argparse.Namespace) -> int:
    from .model_adapter import PRESETS, load_dotenv, resolve_provider_name

    load_dotenv()
    try:
        active = resolve_provider_name()
    except ModelError as exc:
        active = f"(error: {exc})"
    rows = []
    for name, (kind, base, keys, model, open_w) in PRESETS.items():
        rows.append({"provider": name, "api": kind, "base_url": base, "key_env": list(keys),
                     "default_model": model, "open_weight_default": open_w})
    if args.json:
        _print_json({"active": active, "providers": rows})
        return EXIT_OK
    print(f"active (from environment): {active}\n")
    print(f"{'provider':<18}{'default model':<52}key variable")
    for r in rows:
        tag = "" if r["open_weight_default"] else "  [closed model]"
        print(f"{r['provider']:<18}{(r['default_model'] or '(set BEEJA_MODEL)'):<52}"
              f"{' / '.join(r['key_env']) or '-'}{tag}")
    print("\nPut any key in BEEJA_API_KEY (or a .env file); the provider is detected from its prefix.\n"
          "Override with BEEJA_PROVIDER / BEEJA_MODEL / BEEJA_BASE_URL or --provider/--model/--base-url.")
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
    e.add_argument("--provider", help="ollama (default), any preset from `beeja providers`, or openai-compatible")
    e.add_argument("--model", help="model name for the provider (default: the preset's default)")
    e.add_argument("--base-url", help="custom endpoint, e.g. http://server:8000/v1")
    e.add_argument("--no-outcome-pass", action="store_true", help="skip the reply-tree outcome pass")
    e.add_argument("--no-support-check", action="store_true", help="skip the second model pass that checks support")
    e.add_argument("--json", action="store_true", help="print the record to stdout even with --output")
    e.add_argument("--quiet", "-q", action="store_true")
    e.set_defaults(func=cmd_extract)

    f = sub.add_parser("fetch-discourse", help="download a public Discourse topic as JSON (network)")
    f.add_argument("topic_id", type=int)
    f.add_argument("--base-url", default="https://discuss.python.org")
    f.add_argument("--output", "-o", required=True)
    f.set_defaults(func=cmd_fetch_discourse)

    b = sub.add_parser("benchmark", help="score extracted decisions against withheld ground truth")
    b.add_argument("--dataset", default="benchmarks", help="directory of cases with ground_truth.json")
    b.add_argument("--outputs", default="examples", help="directory holding <case>/decision.json")
    b.add_argument("--markdown", help="also write the Markdown report here")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_benchmark)

    se = sub.add_parser("search", help="search stored decisions (offline, no model)")
    se.add_argument("query")
    se.add_argument("--store", default="decisions")
    se.add_argument("--limit", type=int, default=10)
    se.add_argument("--negative-only", action="store_true", help="only rejected/withdrawn/deferred/superseded")
    se.add_argument("--json", action="store_true")
    se.set_defaults(func=cmd_search)

    g = sub.add_parser("get", help="print one decision (dec-id) or alternative (dec-id#alt-n) as JSON")
    g.add_argument("ref")
    g.add_argument("--store", default="decisions")
    g.add_argument("--json", action="store_true", help="accepted for symmetry; output is always JSON")
    g.set_defaults(func=cmd_get)

    w = sub.add_parser("why-not", help='source-grounded answer to "why not X?"')
    w.add_argument("idea")
    w.add_argument("--store", default="decisions")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_why_not)

    cd = sub.add_parser("check-diff", help="report stored decisions explicitly linked to a unified diff")
    cd.add_argument("patch", help="patch file, or - for stdin")
    cd.add_argument("--store", default="decisions")
    cd.add_argument("--json", action="store_true")
    cd.set_defaults(func=cmd_check_diff)

    xw = sub.add_parser("export-web", help="write viewer data (records + cited source messages) for web/")
    xw.add_argument("--examples", default="examples", help="directory of <case>/decision.json + input_path.txt")
    xw.add_argument("--out", default="web/public/data")
    xw.set_defaults(func=cmd_export_web)

    xm = sub.add_parser("export-madr", help="render one stored decision as a MADR Markdown document")
    xm.add_argument("ref")
    xm.add_argument("--store", default="decisions")
    xm.add_argument("--output", "-o")
    xm.set_defaults(func=cmd_export_madr)

    pv = sub.add_parser("providers", help="list supported model providers and the active one")
    pv.add_argument("--json", action="store_true")
    pv.set_defaults(func=cmd_providers)

    s = sub.add_parser("schema", help="print the DecisionRecord JSON Schema")
    s.set_defaults(func=cmd_schema)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
