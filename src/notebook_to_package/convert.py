"""Turn the cells into a module: imports at the top, definitions next, the rest in `main()`.

The rearranging is the whole point and also the whole danger. A notebook is a flat sequence
of top-level statements, so every name it binds is a module global and every function it
defines can see all of them. Move those statements into a function and they become locals,
and a function defined above no longer sees them:

    df = load()                 # cell 2, module level
    def summarise():            # cell 5, module level
        return df.describe()    # reads the global `df`

Put `df = load()` inside `main()` and `summarise` raises `NameError`. Nothing in the
conversion fails; the module imports cleanly; the break appears only when somebody calls the
function. So `main()` declares `global` for exactly the names it binds that something at
module level reads - no more, because a blanket `global` on everything would export loop
variables and scratch names as part of the package's surface.

Three things are deliberately not attempted:

**Magics are not translated.** `%matplotlib inline` has no equivalent and `!pip install` is
not this tool's business. They are removed and listed, so the output says what it dropped.

**Cells are not reordered.** A notebook whose names are used before they are defined does not
run top to bottom, and quietly sorting the cells would produce a package that works while the
notebook does not - which hides the defect rather than reporting it.

**Nothing is deleted as dead.** A cell binding a name nobody reads may still be the point of
the notebook: it wrote a file, trained a model, printed the answer.
"""

from __future__ import annotations

import ast
import keyword
import re
import sys
from dataclasses import dataclass, field

from notebook_to_package.notebook import Cell, Notebook
from notebook_to_package.notebook import parse as nb_parse

_IDENT = re.compile(r"[^0-9a-zA-Z_]+")


def module_name(stem: str) -> str:
    name = _IDENT.sub("_", stem).strip("_").lower()
    if not name or name[0].isdigit():
        name = f"nb_{name}"
    # `lambda.ipynb` would become `import lambda` - a SyntaxError - and `json.ipynb` a package
    # that shadows the standard library for everything installed beside it.
    if keyword.iskeyword(name) or name in sys.stdlib_module_names:
        name = f"{name}_nb"
    return name


#: Entry-point names tried in order; the first one the notebook does not bind is used. A
#: notebook with its own `def main()` would otherwise have it replaced by the generated one,
#: and the generated main() calling the user's main() recurses forever.
ENTRY_CANDIDATES = ("main", "run_notebook", "run_all_cells", "_ntp_main")


@dataclass
class Converted:
    module: str
    """The generated Python source."""

    name: str
    entry: str = "main"
    """The generated function that runs the notebook's statements."""

    imports: int = 0
    definitions: int = 0
    kept_in_main: list[str] = field(default_factory=list)
    """Definitions left in place because defining them reads a value main() computes."""

    statements: int = 0
    globals_declared: list[str] = field(default_factory=list)
    dropped_magics: list[str] = field(default_factory=list)
    skipped_cells: list[tuple[int, str]] = field(default_factory=list)
    """(cell index, why) - a syntax error, or a cell that is nothing but magics."""


def _free_names(node: ast.AST) -> set[str]:
    """Names a definition reads without binding them itself.

    Approximate on purpose, and approximate in the safe direction: a name bound locally
    inside the function is subtracted, anything else read is treated as a lookup that will
    land on the module. Over-reporting costs a `global` that was not needed; under-reporting
    produces a package that raises `NameError` at run time.
    """
    bound: set[str] = set()
    used: set[str] = set()

    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            (bound if isinstance(child.ctx, ast.Store | ast.Del) else used).add(child.id)
        elif isinstance(child, ast.arg):
            bound.add(child.arg)
        elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(child.name)
        elif isinstance(child, ast.Import | ast.ImportFrom):
            for a in child.names:
                if a.name != "*":
                    bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(child, ast.ExceptHandler) and child.name:
            bound.add(child.name)
    # `global n; n += 1` stores n, which the loop above records as a local binding. It is
    # the opposite: a write to the module global, which must therefore exist there.
    return (used - bound) | _declared_global(node)


def _is_main_guard(stmt: ast.stmt) -> bool:
    """`if __name__ == "__main__":` at the top of a cell (either operand order)."""
    if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
        return False
    t = stmt.test
    if len(t.ops) != 1 or not isinstance(t.ops[0], ast.Eq):
        return False
    sides = [t.left, t.comparators[0]]
    names = [s for s in sides if isinstance(s, ast.Name) and s.id == "__name__"]
    consts = [s for s in sides if isinstance(s, ast.Constant) and s.value == "__main__"]
    return len(names) == 1 and len(consts) == 1


def _declared_global(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Global):
            out.update(child.names)
    return out


def _eager_names(node: ast.AST) -> set[str]:
    """Names a definition reads *while being defined*, not when later called.

    Decorators, default values and base classes are evaluated by the `def`/`class`
    statement itself, and so is every statement of a class body. A definition reading
    such a name cannot be hoisted above main(): at import time main() has not run, and
    `class A: k = K` raises NameError where the notebook worked.
    """
    out: set[str] = set()

    def reads(expr: ast.AST | None) -> None:
        if expr is None:
            return
        for c in ast.walk(expr):
            if isinstance(c, ast.Name) and isinstance(c.ctx, ast.Load):
                out.add(c.id)

    def visit(n: ast.AST, local: set[str]) -> None:
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
            for d in n.decorator_list:
                reads(d)
            for d in n.args.defaults + [k for k in n.args.kw_defaults if k is not None]:
                reads(d)
            return
        if isinstance(n, ast.Lambda):
            for d in n.args.defaults + [k for k in n.args.kw_defaults if k is not None]:
                reads(d)
            return
        if isinstance(n, ast.ClassDef):
            for d in n.decorator_list + n.bases + [k.value for k in n.keywords]:
                reads(d)
            inner: set[str] = set()
            for stmt in n.body:
                visit(stmt, inner)
                inner |= _bound_at_top(stmt)
            return
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            # A class-body name assigned earlier in the same body is not a module read.
            if n.id not in local:
                out.add(n.id)
        for c in ast.iter_child_nodes(n):
            visit(c, local)

    visit(node, set())
    return out


def _bound_at_top(node: ast.stmt) -> set[str]:
    """Names a top-level statement binds, not descending into nested scopes."""
    out: set[str] = set()
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {node.name}
    if isinstance(node, ast.Import | ast.ImportFrom):
        for a in node.names:
            if a.name != "*":
                out.add(a.asname or a.name.split(".")[0])
        return out

    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            out.add(child.id)
        elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.add(child.name)
    return out


def _strip_magics(cell: Cell) -> str:
    if not cell.magics:
        return cell.source
    keep = [ln for ln in cell.source.splitlines() if ln.strip() not in set(cell.magics)]
    return "\n".join(keep)


def convert(nb: Notebook, name: str = "") -> Converted:
    name = name or module_name(nb.path.stem)
    out = Converted(module="", name=name)

    imports: list[ast.stmt] = []
    ordered: list[ast.stmt] = []

    for cell in nb.cells:
        src = _strip_magics(cell)
        out.dropped_magics += cell.magics
        if not src.strip():
            if cell.magics:
                out.skipped_cells.append((cell.index, "nothing but magics"))
            continue
        try:
            tree = nb_parse(src)
        except SyntaxError as e:
            out.skipped_cells.append((cell.index, f"syntax error: {e.msg}"))
            continue

        for stmt in tree.body:
            if isinstance(stmt, ast.Import | ast.ImportFrom):
                imports.append(stmt)
            elif _is_main_guard(stmt):
                # In a notebook __name__ is "__main__", so the guarded block always runs.
                # Inside the package it would never run: main() executes under the module's
                # name. The block is what the cell did, so keep exactly that.
                ordered.extend(stmt.body)
            else:
                ordered.append(stmt)

    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    bound_in_main: set[str] = set()
    for stmt in ordered:
        if not isinstance(stmt, defs):
            bound_in_main |= _bound_at_top(stmt)

    # A definition that reads, while being defined, something main() computes stays in
    # main() at its own place. That makes its name a main() binding, which can pin further
    # definitions, so repeat until nothing moves.
    kept: set[int] = set()
    changed = True
    while changed:
        changed = False
        for i, stmt in enumerate(ordered):
            if i in kept or not isinstance(stmt, defs):
                continue
            if _eager_names(stmt) & bound_in_main:
                kept.add(i)
                bound_in_main.add(stmt.name)
                changed = True

    definitions = [s for i, s in enumerate(ordered) if isinstance(s, defs) and i not in kept]
    body = [s for i, s in enumerate(ordered) if not isinstance(s, defs) or i in kept]
    out.kept_in_main = [ordered[i].name for i in sorted(kept)]

    taken = set(bound_in_main)
    for st in imports + definitions:
        taken |= _bound_at_top(st)
    out.entry = next(c for c in ENTRY_CANDIDATES if c not in taken)

    # Names the module-level definitions read but do not bind. Anything main() binds that
    # appears here has to stay a module global or those definitions break.
    read_by_definitions: set[str] = set()
    for d in definitions:
        read_by_definitions |= _free_names(d)

    # A function kept inside main() that says `global n` writes the module global too.
    for stmt in body:
        read_by_definitions |= _declared_global(stmt)

    needs_global = sorted(bound_in_main & read_by_definitions)
    out.globals_declared = needs_global
    out.imports = len(imports)
    out.definitions = len(definitions)
    out.statements = len(body)

    out.module = _render(nb, name, imports, definitions, body, needs_global, out.entry)
    return out


def _render(
    nb: Notebook,
    name: str,
    imports: list[ast.stmt],
    definitions: list[ast.stmt],
    body: list[ast.stmt],
    needs_global: list[str],
    entry: str = "main",
) -> str:
    lines = [
        '"""Generated from ' + nb.path.name + " by notebook-to-package.",
        "",
        "Cells are kept in document order. Imports are hoisted, definitions are module",
        f"level, and the remaining statements run in {entry}().",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]
    lines += [ast.unparse(i) for i in imports]
    lines.append("")
    lines.append("")

    for d in definitions:
        lines.append(ast.unparse(d))
        lines.append("")
        lines.append("")

    lines.append(f"def {entry}():")
    if needs_global:
        lines.append("    # Read by the module-level definitions above, so these must stay")
        lines.append("    # module globals rather than becoming locals of main().")
        for n in needs_global:
            lines.append(f"    global {n}")
        lines.append("")
    if not body:
        lines.append("    return None")
    else:
        for stmt in body:
            for ln in ast.unparse(stmt).splitlines():
                lines.append("    " + ln if ln.strip() else "")
    lines.append("")
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append(f"    {entry}()")
    return "\n".join(lines) + "\n"
