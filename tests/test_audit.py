"""Could it run top to bottom - and the false positives that question invites."""

from __future__ import annotations

from notebook_to_package import audit as audit_mod
from notebook_to_package import notebook as nb_mod


def run(factory, sources, **kw):
    return audit_mod.audit(nb_mod.read(factory(sources, **kw)))


def test_a_clean_notebook(notebook_factory):
    a = run(notebook_factory, ["import math\n", "x = 2\n", "print(math.sqrt(x))\n"])
    assert a.runs_top_to_bottom is True
    assert a.never_defined == [] and a.used_before_defined == []


def test_a_name_that_is_never_defined(notebook_factory):
    """The unambiguous one: it came from a cell that has been deleted."""
    a = run(notebook_factory, ["print(df.shape)\n"])
    assert [u.name for u in a.never_defined] == ["df"]
    assert a.runs_top_to_bottom is False


def test_a_name_used_before_its_cell(notebook_factory):
    a = run(notebook_factory, ["print(total)\n", "total = 5\n"])
    assert a.used_before_defined == [("total", 0, 1)]
    assert a.runs_top_to_bottom is False


def test_a_function_reading_a_global_defined_later_is_fine(notebook_factory):
    """The false positive that mattered.

    A helper defined early that reads a constant set later is ordinary notebook style and
    runs correctly, because the body is not evaluated until the function is called. The
    first version flagged it, which would have put the pattern in the findings of most
    real notebooks.
    """
    a = run(
        notebook_factory,
        ["def scaled():\n    return total * 2\n", "total = 5\n", "print(scaled())\n"],
    )
    assert a.used_before_defined == []
    assert a.runs_top_to_bottom is True


def test_a_function_reading_a_name_defined_nowhere_is_still_reported(notebook_factory):
    """Deferred does not mean excused: it will raise whenever it is finally called."""
    a = run(notebook_factory, ["def f():\n    return missing_thing\n"])
    assert [u.name for u in a.never_defined] == ["missing_thing"]


def test_a_class_body_runs_at_definition_time(notebook_factory):
    """Unlike a function body, so its free names are immediate uses."""
    a = run(notebook_factory, ["class C:\n    size = LIMIT\n", "LIMIT = 3\n"])
    assert a.used_before_defined == [("LIMIT", 0, 1)]


def test_builtins_are_not_undefined(notebook_factory):
    a = run(notebook_factory, ["print(len(sorted([3, 1])))\n"])
    assert a.never_defined == []


def test_a_loop_variable_is_not_undefined(notebook_factory):
    a = run(notebook_factory, ["for i in range(3):\n    print(i)\n"])
    assert a.never_defined == []


def test_a_comprehension_variable_is_not_undefined(notebook_factory):
    a = run(notebook_factory, ["xs = [y * 2 for y in range(3)]\n"])
    assert a.never_defined == []


def test_a_function_argument_is_not_undefined(notebook_factory):
    a = run(notebook_factory, ["def f(a, b=1):\n    return a + b\n"])
    assert a.never_defined == []


def test_an_except_alias_is_not_undefined(notebook_factory):
    a = run(notebook_factory, ["try:\n    pass\nexcept ValueError as e:\n    print(e)\n"])
    assert a.never_defined == []


def test_an_import_inside_a_cell_with_a_magic_still_counts(notebook_factory):
    """The bug that would have wrecked the survey.

    `%matplotlib inline` sits in the first cell of a large share of real notebooks, right
    beside the imports. Parsing `source` instead of `code` makes that cell contribute no
    bindings at all, and every module it imports reads as never defined.
    """
    a = run(notebook_factory, ["%matplotlib inline\nimport math\n", "math.pi\n"])
    assert a.never_defined == []


def test_a_star_import_makes_the_question_undecidable(notebook_factory):
    a = run(notebook_factory, ["from os.path import *\n", "print(join('a', 'b'))\n"])
    assert a.star_imports == [0]
    assert a.runs_top_to_bottom is None


def test_out_of_order_execution_counts(notebook_factory):
    a = run(notebook_factory, ["a=1", "b=2", "c=3"], counts=[3, 1, 2])
    assert [i for i, _ in a.out_of_order] == [1, 2]


def test_in_order_execution_counts_are_not_flagged(notebook_factory):
    a = run(notebook_factory, ["a=1", "b=2", "c=3"], counts=[1, 2, 3])
    assert a.out_of_order == []


def test_counts_with_gaps_are_not_out_of_order(notebook_factory):
    """Re-running a cell bumps its count; ascending with gaps is still ascending."""
    a = run(notebook_factory, ["a=1", "b=2"], counts=[2, 9])
    assert a.out_of_order == []


def test_cells_never_run_are_counted(notebook_factory):
    a = run(notebook_factory, ["a=1", "b=2"], counts=[1, None])
    assert a.unrun == 1


def test_a_redefined_name_is_recorded(notebook_factory):
    a = run(notebook_factory, ["x = 1\n", "x = 2\n"])
    assert a.redefined["x"] == [0, 1]
    assert a.defined["x"] == 0


def test_a_name_is_reported_once_not_once_per_use(notebook_factory):
    a = run(notebook_factory, ["print(df)\n", "print(df)\n", "print(df)\n"])
    assert len(a.never_defined) == 1


def test_an_unparseable_cell_does_not_stop_the_audit(notebook_factory):
    a = run(notebook_factory, ["import math\n", "def oops(:\n", "math.pi\n"])
    assert a.never_defined == []
    assert a.summary()["syntax_errors"] == 1
