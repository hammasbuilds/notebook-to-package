"""Could this notebook have run top to bottom, as written, in a fresh kernel?

A notebook is not a program. It is a transcript of an interactive session, and the file
records the order the cells are *printed* in, not the order they were *run* in. Those come
apart in three ways, and each has a different consequence for anyone converting it:

**Out of order.** `execution_count` is the `[n]` beside a cell. If the counts do not ascend
down the document, the author ran cells backwards at some point. That on its own is normal
and harmless - people re-run a cell after fixing it. It becomes a problem only when combined
with the next one.

**Used before defined.** A name read in cell 3 and assigned in cell 9. Reading top to bottom
this is a `NameError`; in the author's kernel it worked, because cell 9 had already run.

**Never defined at all.** A name used in the notebook, not assigned anywhere in it, not
imported, not a builtin. It came from a cell that has since been deleted, or from a kernel
the author had been typing into for an hour. **This notebook cannot run from clean, by
anyone, ever** - and it is the only one of the three that is unambiguous.

The analysis is deliberately syntactic and its false-positive direction is stated: a name
bound by `exec`, by `globals()[...]`, or by a star-import is invisible here and will be
reported as undefined. Star-imports are therefore detected and the notebook excused.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field

from notebook_to_package.notebook import Cell, Notebook
from notebook_to_package.notebook import parse as nb_parse

BUILTINS = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "_", "__"}


@dataclass
class Binding:
    name: str
    cell: int


@dataclass
class Use:
    name: str
    cell: int


@dataclass
class Audit:
    notebook: Notebook
    defined: dict[str, int] = field(default_factory=dict)
    """name -> the first cell (by document order) that binds it."""

    used_before_defined: list[tuple[str, int, int]] = field(default_factory=list)
    """(name, cell that uses it, cell that defines it)"""

    never_defined: list[Use] = field(default_factory=list)
    star_imports: list[int] = field(default_factory=list)
    """Cells doing `from x import *`. They bind unknown names, so `never_defined` is
    unreliable for the whole notebook and is reported as such rather than suppressed."""

    out_of_order: list[tuple[int, int]] = field(default_factory=list)
    """(cell index, execution_count) for cells whose count breaks the ascending run."""

    unrun: int = 0
    redefined: dict[str, list[int]] = field(default_factory=dict)

    @property
    def runs_top_to_bottom(self) -> bool | None:
        """None when a star-import makes the question unanswerable from syntax alone."""
        if self.star_imports:
            return None
        return not self.never_defined and not self.used_before_defined

    def summary(self) -> dict:
        return {
            "cells": len(self.notebook.code),
            "executed": len(self.notebook.executed),
            "never_run": self.unrun,
            "out_of_order": len(self.out_of_order),
            "used_before_defined": len(self.used_before_defined),
            "never_defined": sorted({u.name for u in self.never_defined}),
            "star_imports": self.star_imports,
            "magics": len(self.notebook.magics()),
            "syntax_errors": sum(1 for c in self.notebook.cells if c.syntax_error),
            "runs_top_to_bottom": self.runs_top_to_bottom,
        }


class _Scan(ast.NodeVisitor):
    """Top-level bindings and free names of one cell.

    Function and class bodies are walked for *uses* but their locals are not treated as
    notebook-level bindings - a name assigned inside a function is not available to the next
    cell, and counting it as defined would hide exactly the bug being looked for.
    """

    def __init__(self):
        self.binds: set[str] = set()
        self.uses: set[str] = set()
        """Names read when this cell runs."""

        self.deferred: set[str] = set()
        """Names read inside a function body, which is not evaluated at definition time.

    A helper defined in cell 2 that reads a global set in cell 5 is ordinary notebook style
    and perfectly correct, as long as nothing calls it before cell 5. Counting those as
    "used before defined" flags the pattern rather than the fault - the first version of
    this reported it on a six-cell demo that runs fine.

    They still count towards *never* defined: a free name that is nowhere in the notebook
    will raise whenever the function is finally called.
        """

        self.star = False
        self._deferred = False

        self._scopes: list[set[str]] = []
        """A set of local names per enclosing function or class, innermost last.

        Discarding a name from `uses` when it turns out to be bound locally is not enough,
        because it only works if the binding is visited first. `def f(a): return a` walks
        the arguments before the body and behaves; `lambda: [x for x in xs]` and a name
        bound after its first read do not. A stack is checked at the point of use, so the
        order stops mattering.
        """

    # -- scopes -----------------------------------------------------------------
    def _walk_scoped(self, node, deferred: bool):
        self._scopes.append(set())
        self._prebind(node)
        was, self._deferred = self._deferred, self._deferred or deferred
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._deferred = was
        self._scopes.pop()

    def _prebind(self, node) -> None:
        """Collect a scope's local names before reading any of its body.

        Walking in order is not enough, because a name can be read above the line that
        binds it: `[y * 2 for y in xs]` visits `y * 2` first, and `y` then looks like a
        free name that the notebook never defines. Python resolves a scope's locals before
        executing it, and so does this.

        Nested function and class bodies are skipped - their locals are theirs, and folding
        them in here would hide a genuine free name in the outer body.
        """
        stack = list(ast.iter_child_nodes(node))
        while stack:
            child = stack.pop()
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self._bind(child.name)
                continue
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store | ast.Del):
                self._bind(child.id)
            elif isinstance(child, ast.arg):
                self._bind(child.arg)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                self._bind(child.name)
            elif isinstance(child, ast.Import | ast.ImportFrom):
                for a in child.names:
                    if a.name != "*":
                        self._bind(a.asname or a.name.split(".")[0])
            stack.extend(ast.iter_child_nodes(child))

    def _is_local(self, name: str) -> bool:
        return any(name in scope for scope in self._scopes)

    def _bind(self, name: str):
        if self._scopes:
            self._scopes[-1].add(name)
        else:
            self.binds.add(name)

    # -- bindings ---------------------------------------------------------------
    def visit_Import(self, node: ast.Import):
        for a in node.names:
            self._bind((a.asname or a.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for a in node.names:
            if a.name == "*":
                self.star = True
            else:
                self._bind(a.asname or a.name)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._bind(node.name)
        # Decorators and default arguments *are* evaluated now; the body is not. A default
        # of `df.shape` reads `df` at definition time, and treating it as deferred would
        # miss a genuine use-before-definition.
        for d in node.decorator_list:
            self.visit(d)
        for d in [*node.args.defaults, *[k for k in node.args.kw_defaults if k]]:
            self.visit(d)
        self._walk_scoped(node, deferred=True)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef):
        self._bind(node.name)
        # A class body runs at definition time, so its free names are immediate uses.
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases:
            self.visit(b)
        self._walk_scoped(node, deferred=False)

    def visit_Lambda(self, node: ast.Lambda):
        self._walk_scoped(node, deferred=True)

    def visit_ListComp(self, node):
        """A comprehension has its own scope in Python 3, so its target is not a notebook
        name. Left unscoped, `[y * 2 for y in xs]` would register `y` as defined by the
        notebook, and a later cell reading `y` would wrongly look fine."""
        self._walk_scoped(node, deferred=False)

    visit_SetComp = visit_ListComp  # type: ignore[assignment]
    visit_DictComp = visit_ListComp  # type: ignore[assignment]
    visit_GeneratorExp = visit_ListComp  # type: ignore[assignment]

    def visit_arg(self, node: ast.arg):
        """Parameters are local names. Without this, every function body reads as undefined."""
        self._bind(node.arg)
        if node.annotation:
            self.visit(node.annotation)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global):
        for n in node.names:
            self.binds.add(n)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Store | ast.Del):
            self._bind(node.id)
        elif self._is_local(node.id):
            return
        elif self._deferred:
            self.deferred.add(node.id)
        else:
            self.uses.add(node.id)


def _scan(cell: Cell) -> _Scan:
    s = _Scan()
    try:
        s.visit(nb_parse(cell.code))
    except SyntaxError:
        pass
    return s


def audit(nb: Notebook) -> Audit:
    a = Audit(notebook=nb)

    scans: list[tuple[Cell, _Scan]] = []
    for cell in nb.cells:
        if not cell.is_runnable:
            continue
        s = _scan(cell)
        scans.append((cell, s))
        if s.star:
            a.star_imports.append(cell.index)
        for name in s.binds:
            a.redefined.setdefault(name, []).append(cell.index)
            a.defined.setdefault(name, cell.index)

    reported: set[str] = set()
    for cell, s in scans:
        # Immediate uses can be both undefined and out of order.
        for name in sorted(s.uses):
            if name in reported or name in BUILTINS:
                continue
            where = a.defined.get(name)
            if where is None:
                a.never_defined.append(Use(name, cell.index))
                reported.add(name)
            elif where > cell.index:
                a.used_before_defined.append((name, cell.index, where))
                reported.add(name)
        # Deferred uses can only be undefined. Whether the function is *called* before the
        # name exists is a question about call order, which this does not attempt.
        for name in sorted(s.deferred):
            if name in reported or name in BUILTINS or name in a.defined:
                continue
            a.never_defined.append(Use(name, cell.index))
            reported.add(name)

    a.redefined = {k: v for k, v in a.redefined.items() if len(v) > 1}
    a.unrun = sum(1 for c in nb.code if c.execution_count is None)

    last = 0
    for cell in nb.executed:
        n = cell.execution_count or 0
        if n < last:
            a.out_of_order.append((cell.index, n))
        last = max(last, n)

    return a
