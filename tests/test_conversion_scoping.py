"""Conversion cases that once produced a package which raised where the notebook ran.

Each one is verified end to end: both sides run, and the verdict must be FAITHFUL.
"""

from __future__ import annotations

import json

from notebook_to_package import audit as audit_mod
from notebook_to_package import convert as convert_mod
from notebook_to_package import notebook as nb_mod
from notebook_to_package import package as package_mod
from notebook_to_package import verify as verify_mod
from notebook_to_package.cli import main as cli_main


def _verify(path):
    nb = nb_mod.read(path)
    conv = convert_mod.convert(nb)
    return conv, verify_mod.verify(nb, conv, timeout=60)


def test_a_function_writing_a_global_keeps_it_a_module_global(notebook_factory):
    p = notebook_factory(["n = 0", "def inc():\n    global n\n    n += 1", "inc()\ninc()"])
    conv, v = _verify(p)
    assert "n" in conv.globals_declared
    assert v.faithful is True, v.package.error


def test_a_class_body_reading_a_cell_value_is_not_hoisted(notebook_factory):
    p = notebook_factory(
        ["K = 2", "class A:\n    k = K\n    def m(self):\n        return self.k", "a = A().m()"]
    )
    conv, v = _verify(p)
    assert conv.kept_in_main == ["A"]
    assert v.faithful is True, v.package.error


def test_a_default_argument_reading_a_cell_value_is_not_hoisted(notebook_factory):
    p = notebook_factory(
        ["LIMIT = 5", "def cap(x, hi=LIMIT):\n    return min(x, hi)", "r = cap(9)"]
    )
    conv, v = _verify(p)
    assert conv.kept_in_main == ["cap"]
    assert v.faithful is True, v.package.error


def test_a_hoistable_class_is_still_hoisted(notebook_factory):
    p = notebook_factory(["class A:\n    k = 1\n    j = k + 1", "a = A.j"])
    conv, v = _verify(p)
    assert conv.kept_in_main == []
    assert v.faithful is True


def test_a_notebook_defining_its_own_main_gets_another_entry(notebook_factory, tmp_path):
    p = notebook_factory(["def main():\n    return 41", "r = main() + 1"])
    conv, v = _verify(p)
    assert conv.entry == "run_notebook"
    assert v.faithful is True, v.package.error
    w = package_mod.write(nb_mod.read(p), conv, tmp_path / "pkg")
    text = (w.root / "pyproject.toml").read_text(encoding="utf-8")
    assert ":run_notebook" in text


def test_a_main_guard_block_still_runs_in_the_package(notebook_factory):
    p = notebook_factory(["if __name__ == '__main__':\n    z = 1", "w = 2"])
    _, v = _verify(p)
    assert v.faithful is True
    assert "z" in v.same


def test_unicode_output_does_not_break_either_side(notebook_factory):
    p = notebook_factory(["s = '\u65e5\u672c \u2713'\nprint(s)"])
    _, v = _verify(p)
    assert v.notebook.ok and v.package.ok, (v.notebook.error, v.package.error)
    assert v.faithful is True


def test_package_raising_where_the_notebook_ran_is_not_faithful():
    ok = verify_mod.Run(ok=True)
    bad = verify_mod.Run(ok=False, error="NameError: x")
    assert verify_mod.Verdict(notebook=ok, package=bad).faithful is False
    slow = verify_mod.Run(ok=False, error="timed out", timed_out=True)
    assert verify_mod.Verdict(notebook=ok, package=slow).faithful is None
    assert verify_mod.Verdict(notebook=bad, package=bad).faithful is None
    flaky = verify_mod.Verdict(notebook=ok, package=bad, control_failed=True)
    assert flaky.faithful is None


def test_keyword_and_stdlib_filenames_give_importable_names():
    assert convert_mod.module_name("lambda") == "lambda_nb"
    assert convert_mod.module_name("json") == "json_nb"
    assert convert_mod.module_name("analysis") == "analysis"


def test_requires_python_follows_the_notebook_kernel(notebook_factory, tmp_path):
    p = notebook_factory(["x = 1"])
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["metadata"]["language_info"] = {"name": "python", "version": "3.9.7"}
    p.write_text(json.dumps(raw), encoding="utf-8")
    nb = nb_mod.read(p)
    w = package_mod.write(nb, convert_mod.convert(nb), tmp_path / "pkg")
    assert 'requires-python = ">=3.9"' in (w.root / "pyproject.toml").read_text(encoding="utf-8")


def test_requires_python_defaults_to_the_supported_floor(notebook_factory, tmp_path):
    nb = nb_mod.read(notebook_factory(["x = 1"]))
    w = package_mod.write(nb, convert_mod.convert(nb), tmp_path / "pkg")
    assert 'requires-python = ">=3.11"' in (w.root / "pyproject.toml").read_text(encoding="utf-8")


def test_an_unparseable_cell_makes_the_audit_undecidable(notebook_factory, capsys):
    p = notebook_factory(["x = (", "y = 2"])
    assert audit_mod.audit(nb_mod.read(p)).runs_top_to_bottom is None
    assert cli_main(["audit", str(p)]) == 0
    assert "not valid Python" in capsys.readouterr().out
    assert cli_main(["audit", str(p), "--strict"]) == 1


def test_overwrite_refusal_names_the_cli_flag(notebook_factory, tmp_path, capsys):
    p = notebook_factory(["x = 1"])
    assert cli_main(["convert", str(p), str(tmp_path / "o")]) == 0
    assert cli_main(["convert", str(p), str(tmp_path / "o")]) == 1
    assert "--force" in capsys.readouterr().err
