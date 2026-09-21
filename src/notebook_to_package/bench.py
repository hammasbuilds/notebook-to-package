"""Convert every notebook in a corpus and check whether the conversion kept its behaviour.

The number that matters is a ratio with an honest denominator. Most notebooks in the wild
will not run here at all - they want a CSV that is not in the repository, a GPU, a network,
a package this environment does not have. None of that says anything about the conversion,
so those are counted separately and excluded.

    unrunnable    the notebook itself failed - nothing can be concluded
    faithful      both ran, every comparable value matched
    broken        both ran, something differed

**`broken` is the number worth staring at.** Every one of those is a conversion bug, not a
property of the notebook: both sides ran, so the difference is something the rearranging did.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from notebook_to_package import convert as convert_mod
from notebook_to_package import notebook as nb_mod
from notebook_to_package import verify as verify_mod


@dataclass
class Bench:
    root: Path
    attempted: int = 0
    unrunnable: int = 0
    timed_out: int = 0
    """One side ran out of time. A verdict was not reached, and that is all it means."""

    package_only_failed: int = 0
    """The notebook ran and the generated package *raised*. Always a conversion bug."""

    faithful: int = 0
    broken: int = 0
    nondeterministic_values: int = 0
    notebooks_with_nondeterminism: int = 0
    seconds: float = 0.0
    notebook_errors: Counter = field(default_factory=Counter)
    failures: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)

    @property
    def decided(self) -> int:
        return self.faithful + self.broken


def _error_kind(msg: str | None) -> str:
    if not msg:
        return "unknown"
    return msg.split(":", 1)[0].strip() or "unknown"


def run(
    root: Path,
    python: str = "",
    timeout: float = 120.0,
    limit: int = 0,
    progress=None,
) -> dict:
    say = progress or (lambda *_: None)
    started = time.time()
    b = Bench(root=root)

    paths = nb_mod.find(root)
    if limit:
        paths = paths[:limit]
    say(f"{len(paths)} notebooks")

    for i, path in enumerate(paths):
        if i and i % 20 == 0:
            say(
                f"{i}/{len(paths)} - {b.faithful} faithful, {b.broken} broken, "
                f"{b.unrunnable} unrunnable"
            )
        nb = nb_mod.read(path)
        if nb.unreadable or not nb.code or not nb.language.startswith("python"):
            continue

        b.attempted += 1
        conv = convert_mod.convert(nb)
        v = verify_mod.verify(nb, conv, python=python, timeout=timeout)
        rel = path.relative_to(root).as_posix()

        row = {
            "path": rel,
            "cells": len(nb.code),
            "notebook_ran": v.notebook.ok,
            "package_ran": v.package.ok,
            "faithful": v.faithful,
            "compared": v.comparable,
            "nondeterministic": len(v.nondeterministic),
        }
        if v.nondeterministic:
            b.nondeterministic_values += len(v.nondeterministic)
            b.notebooks_with_nondeterminism += 1

        if not v.notebook.ok:
            b.unrunnable += 1
            b.notebook_errors[_error_kind(v.notebook.error)] += 1
            row["notebook_error"] = v.notebook.error
        elif v.package.timed_out:
            # Not a conversion failure. The package ran out of the time budget, which says
            # something about the budget. Counting it as broken is an accusation the
            # evidence does not support - the one notebook this happened to came back
            # faithful when the budget was raised from 25s to 180s.
            b.timed_out += 1
            row["package_error"] = v.package.error
        elif not v.package.ok:
            # The notebook ran and the package *raised*. There is no excuse available: the
            # conversion broke it.
            b.package_only_failed += 1
            b.broken += 1
            row["package_error"] = v.package.error
            b.failures.append(
                {
                    "path": rel,
                    "kind": "package failed to run",
                    "detail": v.package.error,
                }
            )
        elif v.faithful:
            b.faithful += 1
        else:
            b.broken += 1
            b.failures.append(
                {
                    "path": rel,
                    "kind": "values differ",
                    "differs": [n for n, _, _ in v.differs][:10],
                    "only_notebook": v.only_notebook[:10],
                    "only_package": v.only_package[:10],
                }
            )
        b.rows.append(row)

    b.seconds = time.time() - started
    return {
        "root": str(root),
        "seconds": round(b.seconds, 1),
        "attempted": b.attempted,
        "unrunnable": b.unrunnable,
        "decided": b.decided,
        "faithful": b.faithful,
        "broken": b.broken,
        "package_only_failed": b.package_only_failed,
        "package_timed_out": b.timed_out,
        "notebooks_with_nondeterminism": b.notebooks_with_nondeterminism,
        "nondeterministic_values_excluded": b.nondeterministic_values,
        "fidelity": round(b.faithful / b.decided, 4) if b.decided else None,
        "notebook_error_kinds": b.notebook_errors.most_common(),
        "failures": b.failures,
        "notebooks": b.rows,
    }


def text(res: dict) -> str:
    out = ["=" * 74, f"CONVERSION FIDELITY - {res['root']}", "=" * 74]
    out.append(f"{res['attempted']} notebooks attempted, {res['seconds']}s")
    out.append("")
    out.append(
        f"  {res['unrunnable']:>5}  the notebook itself did not run here - excluded, "
        f"nothing can be concluded"
    )
    if res.get("package_timed_out"):
        out.append(
            f"  {res['package_timed_out']:>5}  the package ran out of the time budget - "
            f"no verdict, not a failure"
        )
    out.append(f"  {res['decided']:>5}  both sides ran, so the conversion can be judged")
    if res["decided"]:
        out.append(f"  {res['faithful']:>5}  faithful ({res['fidelity']:.1%})")
        out.append(f"  {res['broken']:>5}  broken")
        if res["package_only_failed"]:
            out.append(
                f"  {res['package_only_failed']:>5}  of which the package raised while the "
                f"notebook did not"
            )
    out.append("")
    if res.get("notebooks_with_nondeterminism"):
        out.append(
            f"  {res['notebooks_with_nondeterminism']} notebooks produced "
            f"{res['nondeterministic_values_excluded']} values that differ between two runs "
            f"of the notebook itself"
        )
        out.append(
            "  (excluded - the conversion cannot be blamed for a value the notebook does "
            "not reproduce)"
        )
        out.append("")

    if res["notebook_error_kinds"]:
        out.append("Why the excluded notebooks did not run:")
        for kind, n in res["notebook_error_kinds"][:10]:
            out.append(f"  {n:>5}  {kind}")
        out.append("")

    if res["failures"]:
        out.append("-" * 74)
        out.append("CONVERSION FAILURES")
        out.append("-" * 74)
        for f in res["failures"][:12]:
            out.append(f"  {f['path']}")
            if f["kind"] == "package failed to run":
                out.append(f"      package raised: {f['detail']}")
            else:
                if f.get("differs"):
                    out.append(f"      differs: {', '.join(f['differs'])}")
                if f.get("only_notebook"):
                    out.append(f"      lost: {', '.join(f['only_notebook'])}")
                if f.get("only_package"):
                    out.append(f"      gained: {', '.join(f['only_package'])}")
        if len(res["failures"]) > 12:
            out.append(f"  ... and {len(res['failures']) - 12} more")
    return "\n".join(out)


def write_json(res: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=2), encoding="utf-8")
