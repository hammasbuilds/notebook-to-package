"""Print one notebook's audit, and the verdict on its conversion.

The three findings are printed in order of how certain they are, not how alarming they
sound. A name that is never defined anywhere is a fact: this file cannot run from clean.
A name used before its defining cell is also a fact, but only about document order. Cells
last run out of order are merely a trace of how somebody worked, and on their own mean
nothing at all - so they come last, and say so.
"""

from __future__ import annotations

import json
from pathlib import Path

from notebook_to_package.audit import Audit
from notebook_to_package.convert import Converted
from notebook_to_package.verify import Verdict


def audit_text(a: Audit) -> str:
    nb = a.notebook
    out = ["=" * 74, f"NOTEBOOK AUDIT - {nb.path.name}", "=" * 74]
    out.append(
        f"{len(nb.code)} code cells, {len(nb.executed)} run, {a.unrun} never run, "
        f"{len(nb.magics())} magics"
    )
    out.append("")

    verdict = a.runs_top_to_bottom
    if verdict is True:
        out.append("Runs top to bottom in a fresh kernel: nothing is read before it exists.")
    elif verdict is None:
        out.append(
            "Cannot be decided: a star-import binds names this cannot see, so anything "
            "below may be a false alarm."
        )
    else:
        out.append("Does NOT run top to bottom as written.")
    out.append("")

    if a.never_defined:
        names = sorted({u.name for u in a.never_defined})
        out.append("-" * 74)
        out.append(f"NEVER DEFINED ANYWHERE ({len(names)})")
        out.append("Used, but assigned in no cell and imported in none. It came from a cell")
        out.append("that has been deleted. Nobody can run this notebook from clean.")
        out.append("-" * 74)
        for u in a.never_defined[:20]:
            out.append(f"  cell {u.cell:>3}  {u.name}")
        if len(a.never_defined) > 20:
            out.append(f"  ... and {len(a.never_defined) - 20} more")
        out.append("")

    if a.used_before_defined:
        out.append("-" * 74)
        out.append(f"USED BEFORE DEFINED ({len(a.used_before_defined)})")
        out.append("Read in an earlier cell than the one that assigns it. It worked in the")
        out.append("author's kernel because that cell had already been run.")
        out.append("-" * 74)
        for name, used, defined in a.used_before_defined[:20]:
            out.append(f"  {name:<24} read in cell {used}, defined in cell {defined}")
        out.append("")

    if a.out_of_order:
        out.append("-" * 74)
        out.append(f"LAST RUN OUT OF ORDER ({len(a.out_of_order)} cells)")
        out.append("On its own this means nothing - re-running a cell after fixing it is")
        out.append("normal. It matters only alongside one of the findings above.")
        out.append("-" * 74)
        counts = ", ".join(f"cell {i}=[{n}]" for i, n in a.out_of_order[:12])
        out.append(f"  {counts}")
        out.append("")

    errs = [(c.index, c.syntax_error) for c in nb.cells if c.syntax_error]
    if errs:
        out.append(f"{len(errs)} cells do not parse and were excluded:")
        for i, e in errs[:5]:
            out.append(f"  cell {i}: {e}")
        out.append("")

    if nb.magics():
        kinds: dict[str, int] = {}
        for _, m in nb.magics():
            head = m.strip().split()[0] if m.strip().split() else m
            kinds[head] = kinds.get(head, 0) + 1
        out.append("Magics and shell escapes (removed by conversion, not translated):")
        for k, n in sorted(kinds.items(), key=lambda t: -t[1])[:8]:
            out.append(f"  {n:>3}  {k}")
    return "\n".join(out)


def verify_text(conv: Converted, v: Verdict) -> str:
    out = ["=" * 74, "CONVERSION", "=" * 74]
    out.append(
        f"{conv.imports} imports hoisted, {conv.definitions} definitions kept at module "
        f"level, {conv.statements} statements moved into main()"
    )
    if conv.globals_declared:
        out.append(
            f"{len(conv.globals_declared)} names declared global in main() because a "
            f"module-level definition reads them: " + ", ".join(conv.globals_declared[:10])
        )
    if conv.dropped_magics:
        out.append(f"{len(conv.dropped_magics)} magic lines dropped")
    for i, why in conv.skipped_cells:
        out.append(f"  cell {i} skipped: {why}")
    out.append("")

    out.append("-" * 74)
    out.append("VERIFICATION - both sides run, resulting values compared")
    out.append("-" * 74)
    out.append(
        f"  notebook  {'ran' if v.notebook.ok else 'FAILED'}"
        + (f": {v.notebook.error}" if v.notebook.error else "")
    )
    out.append(
        f"  package   {'ran' if v.package.ok else 'FAILED'}"
        + (f": {v.package.error}" if v.package.error else "")
    )
    out.append("")

    if v.faithful is None:
        out.append("No verdict: at least one side did not run, so there is nothing to")
        out.append("compare. This is not evidence that the conversion is wrong.")
        return "\n".join(out)

    if v.faithful:
        out.append(f"FAITHFUL - {len(v.same)} values match, none differ.")
    else:
        out.append("NOT FAITHFUL.")
    if v.differs:
        out.append("")
        out.append(f"  {len(v.differs)} values differ:")
        for name, a_repr, b_repr in v.differs[:10]:
            out.append(f"    {name}")
            out.append(f"      notebook: {a_repr}")
            out.append(f"      package : {b_repr}")
    if v.only_notebook:
        out.append(f"  only in the notebook: {', '.join(v.only_notebook[:15])}")
    if v.only_package:
        out.append(f"  only in the package : {', '.join(v.only_package[:15])}")
    if v.skipped_opaque:
        out.append(
            f"  {len(v.skipped_opaque)} values could not be compared and were excluded "
            f"from both sides"
        )
    return "\n".join(out)


def as_json(a: Audit, conv: Converted | None = None, v: Verdict | None = None) -> dict:
    d: dict = {"notebook": str(a.notebook.path), **a.summary()}
    if conv is not None:
        d["conversion"] = {
            "module": conv.name,
            "imports": conv.imports,
            "definitions": conv.definitions,
            "statements": conv.statements,
            "globals_declared": conv.globals_declared,
            "dropped_magics": len(conv.dropped_magics),
            "skipped_cells": conv.skipped_cells,
        }
    if v is not None:
        d["verification"] = {
            "notebook_ran": v.notebook.ok,
            "notebook_error": v.notebook.error,
            "package_ran": v.package.ok,
            "package_error": v.package.error,
            "faithful": v.faithful,
            "values_compared": v.comparable,
            "same": len(v.same),
            "differs": [{"name": n, "notebook": x, "package": y} for n, x, y in v.differs],
            "only_notebook": v.only_notebook,
            "only_package": v.only_package,
            "skipped_opaque": v.skipped_opaque,
        }
    return d


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
