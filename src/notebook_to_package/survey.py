"""Audit a whole directory of notebooks and count what is wrong with them.

One notebook's audit is a fact about that notebook. A few hundred is a claim about how
notebooks are written, which is the only way to say whether "notebooks cannot be run from
clean" is a real problem or a repeated anecdote.

The counting is per notebook, not per occurrence. A single file with forty undefined names
is one broken notebook, and summing occurrences would let it outvote forty clean ones.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from notebook_to_package import audit as audit_mod
from notebook_to_package import notebook as nb_mod


@dataclass
class Survey:
    root: Path
    total: int = 0
    unreadable: int = 0
    not_python: int = 0
    empty: int = 0

    analysed: int = 0
    with_never_defined: int = 0
    with_used_before_defined: int = 0
    with_out_of_order: int = 0
    with_magics: int = 0
    with_star_imports: int = 0
    with_syntax_errors: int = 0
    with_unrun_cells: int = 0
    runs_top_to_bottom: int = 0
    undecidable: int = 0
    """Star-imports bind unknown names, so the question cannot be answered from syntax."""

    cells: int = 0
    magic_kinds: Counter = field(default_factory=Counter)
    worst: list[tuple[str, int, list[str]]] = field(default_factory=list)
    per_notebook: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        d = {
            "root": str(self.root),
            "notebooks_found": self.total,
            "unreadable": self.unreadable,
            "not_python": self.not_python,
            "empty": self.empty,
            "analysed": self.analysed,
            "code_cells": self.cells,
            "runs_top_to_bottom": self.runs_top_to_bottom,
            "undecidable_star_import": self.undecidable,
            "with_never_defined": self.with_never_defined,
            "with_used_before_defined": self.with_used_before_defined,
            "with_out_of_order_execution": self.with_out_of_order,
            "with_cells_never_run": self.with_unrun_cells,
            "with_magics": self.with_magics,
            "with_syntax_errors": self.with_syntax_errors,
            "top_magics": self.magic_kinds.most_common(10),
        }
        if self.analysed:
            d["share_running_top_to_bottom"] = round(self.runs_top_to_bottom / self.analysed, 4)
        return d


def _magic_kind(line: str) -> str:
    line = line.strip()
    if line.startswith("!"):
        return "!shell"
    head = line.lstrip("%").split()[0] if line.lstrip("%").split() else "?"
    return ("%%" if line.startswith("%%") else "%") + head


def survey(root: Path, progress=None) -> Survey:
    say = progress or (lambda *_: None)
    s = Survey(root=root)
    paths = nb_mod.find(root)
    s.total = len(paths)
    say(f"{s.total} notebooks under {root}")

    for i, p in enumerate(paths):
        if progress and i and i % 50 == 0:
            say(f"{i}/{s.total}")
        nb = nb_mod.read(p)
        if nb.unreadable:
            s.unreadable += 1
            continue
        if not nb.language.startswith("python"):
            s.not_python += 1
            continue
        if not nb.code:
            s.empty += 1
            continue

        a = audit_mod.audit(nb)
        s.analysed += 1
        s.cells += len(nb.code)

        for _, m in nb.magics():
            s.magic_kinds[_magic_kind(m)] += 1

        names = sorted({u.name for u in a.never_defined})
        if names:
            s.with_never_defined += 1
        if a.used_before_defined:
            s.with_used_before_defined += 1
        if a.out_of_order:
            s.with_out_of_order += 1
        if nb.magics():
            s.with_magics += 1
        if a.star_imports:
            s.with_star_imports += 1
            s.undecidable += 1
        if any(c.syntax_error for c in nb.cells):
            s.with_syntax_errors += 1
        if a.unrun:
            s.with_unrun_cells += 1
        if a.runs_top_to_bottom:
            s.runs_top_to_bottom += 1

        rel = p.relative_to(root).as_posix()
        s.per_notebook.append({"path": rel, **a.summary(), "never_defined_names": names[:20]})
        if names:
            s.worst.append((rel, len(names), names[:8]))

    s.worst.sort(key=lambda t: -t[1])
    return s


def text(s: Survey) -> str:
    out = ["=" * 74, f"NOTEBOOK SURVEY - {s.root}", "=" * 74]
    out.append(f"{s.total} notebooks found")
    for label, n in (
        ("unreadable JSON", s.unreadable),
        ("not Python", s.not_python),
        ("no code cells", s.empty),
    ):
        if n:
            out.append(f"  {n} {label}, excluded")
    out.append(f"{s.analysed} analysed, {s.cells} code cells")
    out.append("")

    if not s.analysed:
        out.append("Nothing to analyse. This is not a clean result - check the path.")
        return "\n".join(out)

    def row(label: str, n: int) -> str:
        return f"  {n:>5}  {n / s.analysed:>5.0%}  {label}"

    out.append("Of the notebooks analysed:")
    # "provably" is not hedging. A star-import binds names this cannot see, so those
    # notebooks are counted in neither direction, and the figure is a floor rather than
    # an estimate.
    out.append(row("provably run top to bottom in a fresh kernel", s.runs_top_to_bottom))
    out.append(row("use a name that is never defined anywhere", s.with_never_defined))
    out.append(row("use a name before the cell that defines it", s.with_used_before_defined))
    out.append(row("were last run out of document order", s.with_out_of_order))
    out.append(row("contain a cell that was never run", s.with_unrun_cells))
    out.append(row("contain IPython magics or shell escapes", s.with_magics))
    out.append(row("star-import, so the question is undecidable", s.with_star_imports))
    out.append(row("contain a cell that does not parse", s.with_syntax_errors))
    out.append("")

    if s.magic_kinds:
        out.append("Most common magics:")
        for kind, n in s.magic_kinds.most_common(8):
            out.append(f"  {n:>5}  {kind}")
        out.append("")

    if s.worst:
        out.append("Most undefined names:")
        for rel, n, names in s.worst[:8]:
            out.append(f"  {n:>3}  {rel}")
            out.append(f"       {', '.join(names)}")
    return "\n".join(out)


def write_json(s: Survey, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({**s.summary(), "notebooks": s.per_notebook}, indent=2),
        encoding="utf-8",
    )
