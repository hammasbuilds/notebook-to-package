"""End to end through the command line, including what each exit code means."""

from __future__ import annotations

import json

from notebook_to_package.cli import main


def test_audit_reports_a_clean_notebook(notebook_factory, capsys):
    p = notebook_factory(["import math\n", "x = math.pi\n"])
    assert main(["audit", str(p)]) == 0
    assert "Runs top to bottom" in capsys.readouterr().out


def test_audit_exits_nonzero_on_a_broken_notebook(notebook_factory, capsys):
    """So it can gate a commit hook."""
    p = notebook_factory(["print(df)\n"])
    assert main(["audit", str(p)]) == 1
    out = capsys.readouterr().out
    assert "NEVER DEFINED ANYWHERE" in out and "df" in out


def test_audit_writes_json(notebook_factory, tmp_path, capsys):
    p = notebook_factory(["print(df)\n"])
    dest = tmp_path / "o" / "a.json"
    main(["audit", str(p), "--json", str(dest)])
    data = json.loads(dest.read_text())
    assert data["never_defined"] == ["df"]
    assert data["runs_top_to_bottom"] is False


def test_survey_counts_a_directory(notebook_factory, tmp_path, capsys):
    notebook_factory(["import math\nx = math.pi\n"], name="good.ipynb")
    notebook_factory(["print(missing)\n"], name="bad.ipynb")
    assert main(["survey", str(tmp_path), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "2 analysed" in out
    assert "run top to bottom" in out


def test_survey_says_when_it_found_nothing(tmp_path, capsys):
    """ "No notebooks here" and "every notebook is clean" must not look alike."""
    assert main(["survey", str(tmp_path), "--quiet"]) == 0
    assert "Nothing to analyse" in capsys.readouterr().out


def test_convert_writes_a_package(notebook_factory, tmp_path, capsys):
    p = notebook_factory(["import math\n", "x = math.pi\n"])
    assert main(["convert", str(p), str(tmp_path / "pkg")]) == 0
    assert (tmp_path / "pkg" / "pyproject.toml").exists()
    assert "wrote 3 files" in capsys.readouterr().out


def test_convert_to_stdout(notebook_factory, capsys):
    p = notebook_factory(["x = 1\n"])
    assert main(["convert", str(p), "unused", "--stdout"]) == 0
    assert "def main():" in capsys.readouterr().out


def test_convert_refuses_to_overwrite(notebook_factory, tmp_path, capsys):
    p = notebook_factory(["x = 1\n"])
    main(["convert", str(p), str(tmp_path / "pkg")])
    assert main(["convert", str(p), str(tmp_path / "pkg")]) == 1
    assert main(["convert", str(p), str(tmp_path / "pkg"), "--force"]) == 0


def test_verify_runs_both_sides(notebook_factory, capsys):
    p = notebook_factory(["import math\n", "root = math.sqrt(16)\n"])
    assert main(["verify", str(p), "--timeout", "60"]) == 0
    out = capsys.readouterr().out
    assert "FAITHFUL" in out
    assert "notebook  ran" in out and "package   ran" in out


def test_verify_gives_no_verdict_when_the_notebook_fails(notebook_factory, capsys):
    """Exit 0: the conversion was not shown to be wrong, so this must not fail a build."""
    p = notebook_factory(["raise SystemError('nope')\n"])
    assert main(["verify", str(p), "--timeout", "60"]) == 0
    assert "No verdict" in capsys.readouterr().out


def test_bench_runs_over_a_directory(notebook_factory, tmp_path, capsys):
    notebook_factory(["x = 1 + 1\n"], name="a.ipynb")
    notebook_factory(["raise ValueError('x')\n"], name="b.ipynb")
    assert main(["bench", str(tmp_path), "--quiet", "--timeout", "60"]) == 0
    out = capsys.readouterr().out
    assert "CONVERSION FIDELITY" in out
    assert "did not run here" in out


def test_a_missing_file_is_refused(tmp_path, capsys):
    assert main(["audit", str(tmp_path / "nope.ipynb")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_a_missing_directory_is_refused(tmp_path, capsys):
    assert main(["survey", str(tmp_path / "nope")]) == 2
    assert "no such directory" in capsys.readouterr().err


def test_an_unreadable_notebook_is_refused(tmp_path, capsys):
    p = tmp_path / "broken.ipynb"
    p.write_text("{not json", encoding="utf-8")
    assert main(["audit", str(p)]) == 1
    assert "could not read" in capsys.readouterr().err
