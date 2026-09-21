"""Writing the package, and the one judgement call in it: dependencies."""

from __future__ import annotations

from notebook_to_package import convert as convert_mod
from notebook_to_package import notebook as nb_mod
from notebook_to_package import package as package_mod


def build(factory, sources, into, **kw):
    nb = nb_mod.read(factory(sources, **kw))
    return nb, package_mod.write(nb, convert_mod.convert(nb), into)


def test_it_writes_the_three_files(notebook_factory, tmp_path):
    _, w = build(notebook_factory, ["x = 1\n"], tmp_path / "out")
    names = {p.name for p in w.files}
    assert names == {"pyproject.toml", "__init__.py", "nb.py"}


def test_the_standard_library_is_not_a_dependency(notebook_factory, tmp_path):
    _, w = build(notebook_factory, ["import json, os, pathlib\n"], tmp_path / "out")
    assert w.dependencies == []


def test_a_third_party_import_becomes_a_dependency(notebook_factory, tmp_path):
    _, w = build(notebook_factory, ["import requests\n"], tmp_path / "out")
    assert w.dependencies == ["requests"]


def test_import_names_that_differ_from_the_distribution(notebook_factory, tmp_path):
    _, w = build(
        notebook_factory,
        ["import sklearn\nimport cv2\nfrom PIL import Image\n"],
        tmp_path / "o",
    )
    assert w.dependencies == ["opencv-python", "pillow", "scikit-learn"]
    assert w.unknown_distribution == []


def test_an_unknown_import_is_written_through_and_flagged(notebook_factory, tmp_path):
    """Better to name something that fails to install than to guess and install the wrong
    package under a similar name."""
    _, w = build(notebook_factory, ["import some_obscure_lib\n"], tmp_path / "out")
    assert w.dependencies == ["some_obscure_lib"]
    assert w.unknown_distribution == ["some_obscure_lib"]


def test_a_relative_import_is_not_a_dependency():
    assert package_mod.dependencies("from . import sibling\n") == ([], [])


def test_versions_are_not_invented(notebook_factory, tmp_path):
    """The notebook records which packages it imports, not which versions it ran against."""
    _, _w = build(notebook_factory, ["import requests\n"], tmp_path / "out")
    text = (tmp_path / "out" / "pyproject.toml").read_text()
    assert '"requests",' in text
    assert ">=" not in text.split("dependencies = [")[1].split("]")[0]


def test_an_entry_point_is_declared(notebook_factory, tmp_path):
    _, _w = build(notebook_factory, ["x = 1\n"], tmp_path / "out")
    text = (tmp_path / "out" / "pyproject.toml").read_text()
    assert "[project.scripts]" in text and ":main" in text


def test_it_refuses_to_overwrite_by_default(notebook_factory, tmp_path):
    import pytest

    nb = nb_mod.read(notebook_factory(["x = 1\n"]))
    conv = convert_mod.convert(nb)
    package_mod.write(nb, conv, tmp_path / "out")
    with pytest.raises(FileExistsError):
        package_mod.write(nb, conv, tmp_path / "out")
    package_mod.write(nb, conv, tmp_path / "out", force=True)


def test_the_generated_package_imports(notebook_factory, tmp_path):
    """The paperwork is only worth anything if the result is importable."""
    import subprocess
    import sys

    _, _w = build(notebook_factory, ["import math\n", "x = math.pi\n"], tmp_path / "out")
    proc = subprocess.run(
        [sys.executable, "-c", "import nb; nb.main(); print('ok')"],
        cwd=tmp_path / "out" / "src",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
