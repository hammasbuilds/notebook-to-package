"""Running both sides. These execute real subprocesses, which is the point."""

from __future__ import annotations

from notebook_to_package import convert as convert_mod
from notebook_to_package import notebook as nb_mod
from notebook_to_package import verify as verify_mod


def check(factory, sources, **kw):
    nb = nb_mod.read(factory(sources, **kw))
    return nb, verify_mod.verify(nb, convert_mod.convert(nb), timeout=60)


def test_a_straightforward_notebook_converts_faithfully(notebook_factory):
    _, v = check(
        notebook_factory,
        ["import math\n", "x = 9\n", "root = math.sqrt(x)\n"],
    )
    assert v.notebook.ok and v.package.ok
    assert v.faithful is True
    assert "root" in v.same


def test_a_global_read_by_a_function_survives_the_move(notebook_factory):
    """The conversion's one genuinely dangerous transformation, executed.

    Without the `global` declaration this raises NameError inside `summarise()` - and the
    module would still have imported cleanly, so nothing short of running it would tell.
    """
    _, v = check(
        notebook_factory,
        [
            "total = 10\n",
            "def summarise():\n    return total * 2\n",
            "answer = summarise()\n",
        ],
    )
    assert v.package.ok, v.package.error
    assert v.faithful is True
    assert "answer" in v.same


def test_main_locals_are_compared_not_just_module_globals(notebook_factory):
    """A scratch variable becomes a local of main() by design.

    Comparing module namespaces alone would call that intended change a fidelity failure
    and report every notebook as broken.
    """
    _, v = check(notebook_factory, ["scratch = 41 + 1\n"])
    assert v.faithful is True
    assert "scratch" in v.same


def test_a_notebook_that_raises_yields_no_verdict(notebook_factory):
    """Not a conversion failure. Nothing can be concluded, and the tool says so."""
    _, v = check(notebook_factory, ["raise ValueError('boom')\n"])
    assert v.notebook.ok is False
    assert "ValueError" in (v.notebook.error or "")
    assert v.faithful is None


def test_a_notebook_using_an_undefined_name_does_not_run(notebook_factory):
    _, v = check(notebook_factory, ["print(never_defined_anywhere)\n"])
    assert v.notebook.ok is False
    assert "NameError" in (v.notebook.error or "")


def test_values_that_cannot_be_compared_are_excluded_from_both_sides(notebook_factory):
    """An object whose repr raises is not evidence either way."""
    _, v = check(
        notebook_factory,
        [
            "class Bad:\n    def __repr__(self):\n        raise RuntimeError('no')\n",
            "thing = Bad()\n",
        ],
    )
    assert v.notebook.ok and v.package.ok
    assert "thing" in v.skipped_opaque


def test_memory_addresses_do_not_count_as_a_difference(notebook_factory):
    """Two processes never share an id, so an unnormalised repr differs every time."""
    _, v = check(
        notebook_factory,
        ["class Thing:\n    pass\n", "obj = Thing()\n"],
    )
    assert v.faithful is True


def test_magics_do_not_stop_either_side_running(notebook_factory):
    _, v = check(notebook_factory, ["%matplotlib inline\nimport math\n", "x = math.pi\n"])
    assert v.notebook.ok and v.package.ok
    assert v.faithful is True


def test_a_timeout_is_recorded_as_such(notebook_factory):
    nb = nb_mod.read(notebook_factory(["import time\ntime.sleep(30)\n"]))
    v = verify_mod.verify(nb, convert_mod.convert(nb), timeout=2)
    assert v.notebook.timed_out
    assert v.faithful is None


def test_linearise_keeps_document_order(notebook_factory):
    nb = nb_mod.read(notebook_factory(["b = 2\n", "a = 1\n"], counts=[2, 1]))
    src = verify_mod.linearise(nb)
    assert src.index("b = 2") < src.index("a = 1")


def test_linearise_drops_magics(notebook_factory):
    nb = nb_mod.read(notebook_factory(["!pip install nothing\nx = 1\n"]))
    src = verify_mod.linearise(nb)
    assert "pip install" not in src and "x = 1" in src


def test_a_notebook_reading_stdin_does_not_hang(notebook_factory):
    """stdin is never inherited.

    Without `stdin=DEVNULL` the notebook reads *this* process's stdin and waits for a key
    nobody is going to press. Under pytest that is an immediate EOF, which hides it; run
    from a terminal it hangs the whole corpus.
    """
    nb = nb_mod.read(notebook_factory(["answer = input('give me something: ')\n"]))
    v = verify_mod.verify(nb, convert_mod.convert(nb), timeout=20)
    assert not v.notebook.timed_out
    assert v.notebook.ok is False
    assert "EOF" in (v.notebook.error or "")


def test_the_run_environment_forces_a_non_interactive_matplotlib(notebook_factory):
    """The hang that cost twenty-five minutes on a forty-five second timeout.

    matplotlib's default backend opens a window and `plt.show()` blocks until it is closed.
    Killing the interpreter does not help: the GUI thread keeps the output pipe open, so the
    parent waits in communicate() for an EOF that never arrives.
    """
    assert verify_mod.RUN_ENV["MPLBACKEND"] == "Agg"

    nb = nb_mod.read(notebook_factory(["import os\nbackend = os.environ['MPLBACKEND']\n"]))
    v = verify_mod.verify(nb, convert_mod.convert(nb), timeout=60)
    assert v.faithful is True
    assert v.notebook.names["backend"]["repr"] == "'Agg'"


def test_a_timeout_reports_how_long_it_waited(notebook_factory):
    nb = nb_mod.read(notebook_factory(["import time\ntime.sleep(60)\n"]))
    v = verify_mod.verify(nb, convert_mod.convert(nb), timeout=3)
    assert v.notebook.timed_out
    assert "3s" in (v.notebook.error or "")


def test_unseeded_randomness_is_not_a_conversion_failure(notebook_factory):
    """The false accusation that the first corpus run made four times out of four.

    A notebook calling `random.random()` with no seed produces different values every run.
    Comparing one execution against another finds a difference the conversion did not cause,
    and reporting it as a conversion bug sends somebody looking for a fault that is not there.
    """
    nb = nb_mod.read(
        notebook_factory(["import random\nnoise = [random.random() for _ in range(50)]\n"])
    )
    v = verify_mod.verify(nb, convert_mod.convert(nb), timeout=60)
    assert "noise" in v.nondeterministic
    assert v.differs == []
    assert v.faithful is True


def test_a_deterministic_value_is_still_compared(notebook_factory):
    """The control must not swallow everything - then nothing would ever be checked."""
    nb = nb_mod.read(
        notebook_factory(["import random\nnoise = random.random()\nfixed = sum(range(100))\n"])
    )
    v = verify_mod.verify(nb, convert_mod.convert(nb), timeout=60)
    assert "noise" in v.nondeterministic
    assert "fixed" in v.same


def test_the_control_can_be_turned_off(notebook_factory):
    nb = nb_mod.read(notebook_factory(["x = 1 + 1\n"]))
    v = verify_mod.verify(nb, convert_mod.convert(nb), timeout=60, control=False)
    assert v.nondeterministic == []
    assert v.faithful is True
