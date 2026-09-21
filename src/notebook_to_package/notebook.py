"""Read a .ipynb without nbformat, because a notebook is already JSON.

The format is a dict with a `cells` list; each cell has a `cell_type`, a `source` that is
either a string or a list of lines, and - for code cells that have been run - an
`execution_count`. Nothing here needs a library.

Two things in a notebook are not Python and must be recognised rather than parsed:

    %magic  / %%cell magic     IPython, not Python
    !shell                     a shell command

`ast.parse` rejects both. A converter that silently dropped them would produce a package
that looks equivalent and is not - `%matplotlib inline` is harmless to lose, `!pip install`
and `%run other.ipynb` are not. They are recorded per cell so the report can say which kind.
"""

from __future__ import annotations

import ast
import json
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

#: A line that is IPython rather than Python. `%%` must be the first line of its cell;
#: `%` and `!` may be anywhere, including as `x = !ls`.
_LINE_MAGIC = re.compile(r"^\s*[%!]{1,2}\w")
_ASSIGN_MAGIC = re.compile(r"^\s*\w+\s*=\s*[%!]\w")
_HELP = re.compile(r"^\s*\w[\w.]*\?\??\s*$")


@dataclass
class Cell:
    index: int
    """Position in the document, from 0. Not the order it was run in."""

    source: str
    execution_count: int | None = None
    """The `[n]` beside the cell. None means it was never run, or was cleared."""

    magics: list[str] = field(default_factory=list)
    syntax_error: str | None = None

    @property
    def code(self) -> str:
        """The source with magic lines removed - the part that is Python.

        Everything that parses this cell must use it rather than `source`. A single
        `%matplotlib inline` makes `ast.parse` reject the whole cell, and that cell is
        usually the one holding the imports: the first version of the audit reported `math`
        as never defined in a notebook that imports it on line 2, because line 1 was a magic.
        Across a real corpus that turns "cannot run from clean" into a meaningless number.
        """
        if not self.magics:
            return self.source
        drop = set(self.magics)
        return "\n".join(ln for ln in self.source.splitlines() if ln.strip() not in drop)

    @property
    def is_runnable(self) -> bool:
        return bool(self.code.strip()) and self.syntax_error is None


@dataclass
class Notebook:
    path: Path
    cells: list[Cell] = field(default_factory=list)
    language: str = "python"
    unreadable: str | None = None

    @property
    def code(self) -> list[Cell]:
        return [c for c in self.cells if c.source.strip()]

    @property
    def executed(self) -> list[Cell]:
        return [c for c in self.code if c.execution_count]

    def magics(self) -> list[tuple[int, str]]:
        return [(c.index, m) for c in self.cells for m in c.magics]


def parse(source: str) -> ast.Module:
    r"""`ast.parse`, with the compiler's warnings kept to itself.

    Real notebooks are full of `"\d+"` written without a raw prefix, and every one of them
    makes the parser emit a SyntaxWarning on stderr. Surveying a few hundred files then
    buries the actual report under warnings about somebody else's regexes - which are being
    reported as a *finding* here anyway, if they matter.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(source)


def _text(source) -> str:
    if isinstance(source, list):
        return "".join(source)
    return source or ""


def _find_magics(src: str) -> list[str]:
    out = []
    for i, line in enumerate(src.splitlines()):
        if _LINE_MAGIC.match(line) or _ASSIGN_MAGIC.match(line) or _HELP.match(line):
            # `%%` only counts on the first line; anywhere else it is a modulo or a comment.
            if line.lstrip().startswith("%%") and i != 0:
                continue
            out.append(line.strip())
    return out


def read(path: Path) -> Notebook:
    nb = Notebook(path=path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as e:
        # An unreadable notebook is recorded, not raised: a corpus always has a few, and
        # stopping on the first one turns a survey into a single error message.
        nb.unreadable = f"{type(e).__name__}: {e}"
        return nb

    meta = raw.get("metadata") or {}
    lang = (meta.get("kernelspec") or {}).get("language") or (
        (meta.get("language_info") or {}).get("name")
    )
    nb.language = (lang or "python").lower()

    for i, raw_cell in enumerate(raw.get("cells") or []):
        if raw_cell.get("cell_type") != "code":
            continue
        src = _text(raw_cell.get("source"))
        cell = Cell(
            index=i,
            source=src,
            execution_count=raw_cell.get("execution_count"),
            magics=_find_magics(src),
        )
        if cell.code.strip():
            try:
                parse(cell.code)
            except SyntaxError as e:
                cell.syntax_error = f"line {e.lineno}: {e.msg}"
        nb.cells.append(cell)
    return nb


def find(root: Path, skip_checkpoints: bool = True) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*.ipynb")):
        parts = set(p.parts)
        if skip_checkpoints and (".ipynb_checkpoints" in parts or ".git" in parts):
            continue
        out.append(p)
    return out
