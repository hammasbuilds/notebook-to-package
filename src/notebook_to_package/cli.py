"""Command line: audit one notebook, survey a directory, convert, or verify a conversion."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from notebook_to_package import audit as audit_mod
from notebook_to_package import bench as bench_mod
from notebook_to_package import convert as convert_mod
from notebook_to_package import notebook as nb_mod
from notebook_to_package import package as package_mod
from notebook_to_package import report as report_mod
from notebook_to_package import survey as survey_mod
from notebook_to_package import verify as verify_mod


def _say(msg: str) -> None:
    print(f"  .. {msg}", file=sys.stderr, flush=True)


def _load(path: Path):
    # `audit` takes one notebook and `survey` takes a directory, which is easy
    # to get the wrong way round. Handing a directory to open() raises
    # PermissionError on Windows and IsADirectoryError on POSIX, and the old
    # message passed either through verbatim - so the user was told they lacked
    # permission to read a directory they had just created.
    if path.is_dir():
        print(
            f"{path} is a directory. `audit` takes one notebook; "
            f"use `survey {path}` to look at a directory of them.",
            file=sys.stderr,
        )
        return None

    nb = nb_mod.read(path)
    if nb.unreadable:
        print(f"could not read {path}: {nb.unreadable}", file=sys.stderr)
        return None
    if not nb.code:
        print(f"{path} has no code cells", file=sys.stderr)
        return None
    return nb


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="notebook-to-package",
        description="Turn a notebook into a package, and prove the conversion kept its behaviour.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="could this notebook run top to bottom?")
    a.add_argument("notebook", type=Path)
    a.add_argument("--json", type=Path)

    s = sub.add_parser("survey", help="audit every notebook under a directory")
    s.add_argument("directory", type=Path)
    s.add_argument("--json", type=Path)
    s.add_argument("--quiet", action="store_true")

    c = sub.add_parser("convert", help="write an installable package")
    c.add_argument("notebook", type=Path)
    c.add_argument("into", type=Path)
    c.add_argument("--name", default="", help="package name (default: from the filename)")
    c.add_argument("--force", action="store_true", help="overwrite existing files")
    c.add_argument("--stdout", action="store_true", help="print the module instead of writing")

    v = sub.add_parser("verify", help="convert, run both, compare what each left behind")
    v.add_argument("notebook", type=Path)
    v.add_argument(
        "--python",
        default="",
        help="interpreter to run the notebook with (default: this one). A notebook needs "
        "its own dependencies; this tool needs none.",
    )
    v.add_argument("--timeout", type=float, default=300.0)
    v.add_argument("--json", type=Path)

    b = sub.add_parser("bench", help="convert and verify every notebook under a directory")
    b.add_argument("directory", type=Path)
    b.add_argument("--python", default="")
    b.add_argument("--timeout", type=float, default=120.0)
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--json", type=Path)
    b.add_argument("--quiet", action="store_true")

    args = p.parse_args(argv)

    if args.cmd in ("audit", "convert", "verify") and not args.notebook.exists():
        print(f"no such file: {args.notebook}", file=sys.stderr)
        return 2
    if args.cmd in ("survey", "bench"):
        target = args.directory
        if not target.exists():
            print(f"no such directory: {target}", file=sys.stderr)
            return 2

    if args.cmd == "audit":
        nb = _load(args.notebook)
        if nb is None:
            return 1
        result = audit_mod.audit(nb)
        print(report_mod.audit_text(result))
        if args.json:
            report_mod.write_json(report_mod.as_json(result), args.json)
            print(f"\nwrote {args.json}")
        return 0 if result.runs_top_to_bottom is not False else 1

    if args.cmd == "survey":
        s = survey_mod.survey(args.directory, progress=None if args.quiet else _say)
        print(survey_mod.text(s))
        if args.json:
            survey_mod.write_json(s, args.json)
            print(f"\nwrote {args.json}")
        return 0

    if args.cmd == "convert":
        nb = _load(args.notebook)
        if nb is None:
            return 1
        conv = convert_mod.convert(nb, args.name)
        if args.stdout:
            print(conv.module)
            return 0
        try:
            w = package_mod.write(nb, conv, args.into, force=args.force)
        except FileExistsError as e:
            print(f"{e}", file=sys.stderr)
            return 1
        print(f"wrote {len(w.files)} files to {w.root}")
        for f in w.files:
            print(f"  {f}")
        if w.dependencies:
            print(f"\ndependencies declared (NOT pinned): {', '.join(w.dependencies)}")
        if w.unknown_distribution:
            print(
                "  these were written under their import name because the distribution "
                "name is not known: " + ", ".join(w.unknown_distribution)
            )
        return 0

    if args.cmd == "verify":
        nb = _load(args.notebook)
        if nb is None:
            return 1
        result = audit_mod.audit(nb)
        conv = convert_mod.convert(nb)
        verdict = verify_mod.verify(nb, conv, python=args.python, timeout=args.timeout)
        print(report_mod.verify_text(conv, verdict))
        if args.json:
            report_mod.write_json(report_mod.as_json(result, conv, verdict), args.json)
            print(f"\nwrote {args.json}")
        return 0 if verdict.faithful is not False else 1

    res = bench_mod.run(
        args.directory,
        python=args.python,
        timeout=args.timeout,
        limit=args.limit,
        progress=None if args.quiet else _say,
    )
    print(bench_mod.text(res))
    if args.json:
        bench_mod.write_json(res, args.json)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
