"""Build .ipynb files from a list of cell sources.

Real notebook JSON, not a stand-in: the parser's job is to cope with what Jupyter actually
writes, including `source` as a list of lines with the newlines left on, which is the form
every real notebook uses and the one a hand-rolled fixture never has.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def make_notebook(
    path: Path,
    sources: list[str],
    counts: list[int | None] | None = None,
    language: str = "python",
    as_lines: bool = True,
    markdown: list[str] | None = None,
) -> Path:
    counts = counts or list(range(1, len(sources) + 1))
    cells = []
    for md in markdown or []:
        cells.append({"cell_type": "markdown", "source": md, "metadata": {}})
    for src, n in zip(sources, counts, strict=True):
        # Jupyter splits source into lines and keeps the trailing newline on all but the
        # last. A parser that only handles a plain string works on fixtures and on nothing.
        body = src.splitlines(keepends=True) if as_lines else src
        cells.append(
            {
                "cell_type": "code",
                "source": body,
                "execution_count": n,
                "metadata": {},
                "outputs": [],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cells": cells,
                "metadata": {"kernelspec": {"language": language, "name": language}},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def notebook_factory(tmp_path: Path):
    def build(sources, name="nb.ipynb", **kw):
        return make_notebook(tmp_path / name, sources, **kw)

    return build
