"""Command-line interface. Every command can emit machine-readable JSON.

Exit codes: 0 success, 1 validation/content problem, 2 usage or input error, 3 model error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, storage
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

    s = sub.add_parser("schema", help="print the DecisionRecord JSON Schema")
    s.set_defaults(func=cmd_schema)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
