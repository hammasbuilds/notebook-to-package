"""Show what notebook-to-package does, in one command, with nothing to set up.

    python demo.py

The tool turns a Jupyter notebook into an installable package, and the hard
part is not the conversion - it is telling which cells can be converted at all.
A notebook is not a program: cells run in whatever order somebody clicked, so a
cell can depend on a name defined in a cell *below* it, or on a variable that
only exists because an earlier version of a cell was run and then edited away.
Converting that faithfully produces a module that raises NameError on import.

So the demo writes a notebook with those faults planted - out-of-order
execution, a name used before it is defined, a top-level side effect - and runs
the audit over it. The answer is known in advance, which is the only way to
tell a correct report from a confident one.

Nothing is installed and nothing is written outside a temporary directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Cells in the order Jupyter stored them, with execution counts that say they
# were NOT run in that order - exactly what a real, edited-in-place notebook
# looks like by the time anybody wants it packaged.
CELLS = [
    ("import json\nimport pandas as pd\n", 1),
    # Uses `frame`, which is created two cells below. Run top to bottom this
    # raises NameError; the notebook "works" only because it was run out of
    # order.
    ("summary = frame.describe()\n", 4),
    ("print('loading...')\n", 2),  # a top-level side effect
    ("frame = pd.DataFrame({'a': [1, 2, 3]})\n", 3),
    ("def clean(df):\n    return df.dropna()\n", 5),
]


def write_notebook(path: Path) -> None:
    cells = [
        {
            "cell_type": "code",
            # Jupyter stores source as a list of lines with newlines kept on.
            "source": source.splitlines(keepends=True),
            "execution_count": count,
            "metadata": {},
            "outputs": [],
        }
        for source, count in CELLS
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cells": cells,
                "metadata": {"kernelspec": {"language": "python", "name": "python3"}},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="n2p-demo-"))
    try:
        notebook = work / "analysis.ipynb"
        write_notebook(notebook)

        print("A notebook with three faults planted in it:", flush=True)
        print("  * cells stored in an order they were never run in", flush=True)
        print("  * `summary` uses `frame`, which is defined two cells later", flush=True)
        print("  * a bare print() at top level, which becomes an import-time effect", flush=True)
        print(flush=True)
        print(f"  written to {notebook}", flush=True)
        print(flush=True)

        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, "-m", "notebook_to_package.cli", "audit", str(notebook)],
            cwd=ROOT,
            env=env,
            check=False,
        )
        if result.returncode not in (0, 1):
            return result.returncode

        print(flush=True)
        print("Each finding above corresponds to a fault written into the notebook", flush=True)
        print("on purpose, so the report can be read as right or wrong rather than", flush=True)
        print("merely plausible.", flush=True)
        print(flush=True)
        print("Point it at your own notebooks with:", flush=True)
        print("    notebook-to-package survey <dir>    # what is there", flush=True)
        print("    notebook-to-package audit <notebook> # what would break", flush=True)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
