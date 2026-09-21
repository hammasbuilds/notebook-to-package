"""The rearranging, and the scoping it breaks if done naively."""

from __future__ import annotations

import ast

from notebook_to_package import convert as convert_mod
from notebook_to_package import notebook as nb_mod


def conv(factory, sources, **kw):
    return convert_mod.convert(nb_mod.read(factory(sources, **kw)))


def test_the_module_parses(notebook_factory):
    c = conv(notebook_factory, ["import math\n", "x = math.pi\n"])
    ast.parse(c.module)


def test_imports_are_hoisted(notebook_factory):
    c = conv(notebook_factory, ["x = 1\n", "import math\n", "y = math.pi\n"])
    body = c.module.split("def main():")[0]
    assert "import math" in body
    assert c.imports == 1


def test_definitions_stay_at_module_level(notebook_factory):
    c = conv(notebook_factory, ["def f():\n    return 1\n", "print(f())\n"])
    body, inside = c.module.split("def main():")
    assert "def f():" in body
    assert "print(f())" in inside
    assert c.definitions == 1 and c.statements == 1


def test_a_global_read_by_a_definition_is_declared_global(notebook_factory):
    """The scoping trap, and the reason this is verified by running it.

    `total` is assigned at notebook top level and read by a function. Move the assignment
    into main() without a `global` and it becomes a local: the module imports cleanly, and
    `summarise()` raises NameError whenever somebody calls it.
    """
    c = conv(
        notebook_factory,
        [
            "total = 10\n",
            "def summarise():\n    return total * 2\n",
            "print(summarise())\n",
        ],
    )
    assert c.globals_declared == ["total"]
    assert "global total" in c.module


def test_a_name_no_definition_reads_is_not_declared_global(notebook_factory):
    """A blanket `global` would export every scratch variable as package surface."""
    c = conv(notebook_factory, ["scratch = 1\n", "def f():\n    return 2\n"])
    assert c.globals_declared == []


def test_magics_are_dropped_and_counted(notebook_factory):
    c = conv(notebook_factory, ["%matplotlib inline\nimport math\n"])
    assert c.dropped_magics == ["%matplotlib inline"]
    assert "matplotlib" not in c.module
    assert "import math" in c.module


def test_a_cell_that_is_only_a_magic_is_skipped_with_a_reason(notebook_factory):
    c = conv(notebook_factory, ["%load_ext autoreload\n", "x = 1\n"])
    assert c.skipped_cells == [(0, "nothing but magics")]


def test_an_unparseable_cell_is_skipped_with_a_reason(notebook_factory):
    c = conv(notebook_factory, ["def oops(:\n", "x = 1\n"])
    assert c.skipped_cells and c.skipped_cells[0][0] == 0
    assert "x = 1" in c.module


def test_cells_are_not_reordered(notebook_factory):
    """Sorting them would make the package work where the notebook does not, which hides
    the defect instead of reporting it."""
    c = conv(notebook_factory, ["print(later)\n", "later = 1\n"])
    inside = c.module.split("def main():")[1]
    assert inside.index("print(later)") < inside.index("later = 1")


def test_an_empty_notebook_still_produces_a_callable_main(notebook_factory):
    c = conv(notebook_factory, ["%matplotlib inline\n"])
    ns: dict = {}
    exec(compile(c.module, "<gen>", "exec"), ns)  # noqa: S102
    assert ns["main"]() is None


def test_the_module_name_is_a_valid_identifier(notebook_factory):
    c = conv(notebook_factory, ["x = 1\n"], name="02.03 Some-Notebook!.ipynb")
    assert c.name.isidentifier()


def test_a_name_starting_with_a_digit_gets_a_prefix():
    assert convert_mod.module_name("01-intro").isidentifier()


def test_an_entry_point_guard_is_written(notebook_factory):
    c = conv(notebook_factory, ["x = 1\n"])
    assert c.module.rstrip().endswith("main()")
    assert '__name__ == "__main__"' in c.module
