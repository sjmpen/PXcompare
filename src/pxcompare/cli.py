"""Command line: pxcompare BASELINE CANDIDATE.

Exit codes: 0 = identical or equivalent, 1 = differences found, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compare import DEFAULT_IGNORED_KEYWORDS, CompareOptions, compare_files
from .folder import compare_folders
from .parser import PxParseError
from .report import render_comparison, render_folder


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pxcompare",
        description="Compare two PX files, or two folders of PX files (matched by file name).",
        epilog="Exit codes: 0 = identical or equivalent, 1 = differences found, 2 = error.",
    )
    p.add_argument("baseline", type=Path, help="baseline PX file or folder (e.g. output of the old pipeline)")
    p.add_argument("candidate", type=Path, help="candidate PX file or folder (e.g. output of the new pipeline)")
    p.add_argument("--ignore", action="append", default=[], metavar="KEYWORD",
                   help="metadata keyword to ignore (repeatable). Ignored by default: "
                        + ", ".join(sorted(DEFAULT_IGNORED_KEYWORDS)))
    p.add_argument("--no-default-ignores", action="store_true",
                   help="also compare " + ", ".join(sorted(DEFAULT_IGNORED_KEYWORDS)))
    p.add_argument("--abs-tol", type=float, default=0.0, help="treat numbers within this absolute difference as equal")
    p.add_argument("--rel-tol", type=float, default=0.0, help="treat numbers within this relative difference as equal")
    p.add_argument("--max-rows", type=int, default=20, help="max differing cells / items to print (default 20)")
    p.add_argument("--lang", help="language for labels in the data table, e.g. en (default: the file's default)")
    p.add_argument("--json", type=Path, metavar="FILE", help="also write the full result as JSON")
    p.add_argument("--recursive", action="store_true", help="folder mode: include subfolders")
    p.add_argument("--summary", action="store_true", help="folder mode: print only the summary table")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ignored = set() if args.no_default_ignores else set(DEFAULT_IGNORED_KEYWORDS)
    ignored |= {k.upper() for k in args.ignore}
    options = CompareOptions(ignore_keywords=frozenset(ignored), abs_tol=args.abs_tol, rel_tol=args.rel_tol)
    color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    try:
        if args.baseline.is_dir() and args.candidate.is_dir():
            result = compare_folders(args.baseline, args.candidate, options, recursive=args.recursive)
            print(render_folder(result, args.max_rows, args.lang, color, details=not args.summary), end="")
            payload = {
                "baseline": str(result.baseline),
                "candidate": str(result.candidate),
                "ok": result.ok,
                "files": [
                    {"name": e.name, "verdict": e.verdict, "error": e.error,
                     "comparison": e.comparison.to_dict() if e.comparison else None}
                    for e in result.entries
                ],
            }
            ok = result.ok
        elif args.baseline.is_dir() or args.candidate.is_dir():
            print("pxcompare: give two files or two folders, not one of each", file=sys.stderr)
            return 2
        else:
            cmp = compare_files(args.baseline, args.candidate, options)
            print(render_comparison(cmp, args.max_rows, args.lang, color), end="")
            payload = cmp.to_dict()
            ok = cmp.ok
    except (PxParseError, OSError) as exc:
        print(f"pxcompare: {exc}", file=sys.stderr)
        return 2

    if args.json:
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
