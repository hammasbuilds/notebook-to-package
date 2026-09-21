"""Reading the file, and telling Python from IPython."""

from __future__ import annotations

from notebook_to_package import notebook as nb_mod


def test_source_as_a_list_of_lines(notebook_factory):
    """How Jupyter actually writes it."""
    nb = nb_mod.read(notebook_factory(["a = 1\nb = 2\n"], as_lines=True))
    assert nb.cells[0].source == "a = 1\nb = 2\n"


def test_source_as_a_plain_string(notebook_factory):
    nb = nb_mod.read(notebook_factory(["a = 1\n"], as_lines=False))
    assert nb.cells[0].source == "a = 1\n"


def test_markdown_cells_are_not_code(notebook_factory):
    nb = nb_mod.read(notebook_factory(["a = 1\n"], markdown=["# heading"]))
    assert len(nb.cells) == 1


def test_execution_counts_are_kept(notebook_factory):
    nb = nb_mod.read(notebook_factory(["a=1", "b=2"], counts=[7, 3]))
    assert [c.execution_count for c in nb.cells] == [7, 3]


def test_a_cell_that_was_never_run(notebook_factory):
    nb = nb_mod.read(notebook_factory(["a=1", "b=2"], counts=[1, None]))
    assert len(nb.executed) == 1


def test_line_magics_are_found(notebook_factory):
    nb = nb_mod.read(notebook_factory(["%matplotlib inline\nimport os\n"]))
    assert nb.cells[0].magics == ["%matplotlib inline"]


def test_shell_escapes_are_found(notebook_factory):
    nb = nb_mod.read(notebook_factory(["!pip install requests\n"]))
    assert nb.cells[0].magics == ["!pip install requests"]


def test_assignment_from_a_shell_escape(notebook_factory):
    nb = nb_mod.read(notebook_factory(["files = !ls\n"]))
    assert nb.cells[0].magics == ["files = !ls"]


def test_cell_magic_only_counts_on_the_first_line(notebook_factory):
    """`%%` elsewhere is a modulo, not a magic."""
    nb = nb_mod.read(notebook_factory(["x = 5\ny = x %%2 if False else 1\n"]))
    assert nb.cells[0].magics == []


def test_code_strips_the_magic_lines(notebook_factory):
    """The property everything else parses.

    A single `%matplotlib inline` makes `ast.parse` reject the entire cell - and that is
    usually the cell holding the imports, so every name it binds goes missing.
    """
    nb = nb_mod.read(notebook_factory(["%matplotlib inline\nimport math\n"]))
    assert nb.cells[0].code.strip() == "import math"
    assert nb.cells[0].syntax_error is None


def test_a_cell_that_is_only_a_magic_has_no_code(notebook_factory):
    nb = nb_mod.read(notebook_factory(["%load_ext autoreload\n"]))
    assert nb.cells[0].code.strip() == ""
    assert not nb.cells[0].is_runnable


def test_a_syntax_error_is_recorded_not_raised(notebook_factory):
    nb = nb_mod.read(notebook_factory(["def oops(:\n"]))
    assert nb.cells[0].syntax_error
    assert not nb.cells[0].is_runnable


def test_unreadable_json_is_recorded_not_raised(tmp_path):
    p = tmp_path / "broken.ipynb"
    p.write_text("{not json", encoding="utf-8")
    nb = nb_mod.read(p)
    assert nb.unreadable and nb.cells == []


def test_language_is_read_from_the_kernelspec(notebook_factory):
    nb = nb_mod.read(notebook_factory(["1 + 1"], language="r"))
    assert nb.language == "r"


def test_find_skips_checkpoints(tmp_path, notebook_factory):
    notebook_factory(["a=1"], name="real.ipynb")
    ckpt = tmp_path / ".ipynb_checkpoints"
    ckpt.mkdir()
    (ckpt / "real-checkpoint.ipynb").write_text("{}", encoding="utf-8")
    found = nb_mod.find(tmp_path)
    assert [p.name for p in found] == ["real.ipynb"]
