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
import re
from dataclasses import dataclass, field

from notebook_to_package.notebook import Cell, Notebook
from notebook_to_package.notebook import parse as nb_parse

_IDENT = re.compile(r"[^0-9a-zA-Z_]+")


def module_name(stem: str) -> str:
    name = _IDENT.sub("_", stem).strip("_").lower()
    if not name or name[0].isdigit():
        name = f"nb_{name}"
    return name


@dataclass
class Converted:
    module: str
    """The generated Python source."""

    name: str
    imports: int = 0
    definitions: int = 0
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
    return used - bound


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
    definitions: list[ast.stmt] = []
    body: list[ast.stmt] = []

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
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                definitions.append(stmt)
            else:
                body.append(stmt)

    # Names the module-level definitions read but do not bind. Anything main() binds that
    # appears here has to stay a module global or those definitions break.
    read_by_definitions: set[str] = set()
    for d in definitions:
        read_by_definitions |= _free_names(d)

    bound_in_main: set[str] = set()
    for stmt in body:
        bound_in_main |= _bound_at_top(stmt)

    needs_global = sorted(bound_in_main & read_by_definitions)
    out.globals_declared = needs_global
    out.imports = len(imports)
    out.definitions = len(definitions)
    out.statements = len(body)

    out.module = _render(nb, name, imports, definitions, body, needs_global)
    return out


def _render(
    nb: Notebook,
    name: str,
    imports: list[ast.stmt],
    definitions: list[ast.stmt],
    body: list[ast.stmt],
    needs_global: list[str],
) -> str:
    lines = [
        '"""Generated from ' + nb.path.name + " by notebook-to-package.",
        "",
        "Cells are kept in document order. Imports are hoisted, definitions are module",
        "level, and the remaining statements run in main().",
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

    lines.append("def main():")
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
    lines.append("    main()")
    return "\n".join(lines) + "\n"
