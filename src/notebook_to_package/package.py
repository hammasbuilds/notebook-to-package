"""Write the installable package around the generated module.

A single `.py` file is not a package, and the gap is mostly paperwork: a `pyproject.toml`, a
directory with an `__init__.py`, an entry point. The one judgement call is dependencies.

**Third-party imports are declared, not pinned.** The notebook says `import pandas`; it does
not say which version, and this cannot know - the kernel that ran it is gone. Writing
`pandas>=2` would be an invention. The distribution name is guessed from the import name via
a small table of the ones that differ (`sklearn` is `scikit-learn`, `cv2` is `opencv-python`),
and anything not in that table is written through unchanged with a comment saying so, because
a wrong dependency that installs something is worse than one that fails loudly.

Standard-library imports are dropped from the dependency list by checking
`sys.stdlib_module_names`, which is exact rather than a list somebody maintained.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from notebook_to_package.convert import Converted
from notebook_to_package.notebook import Notebook

#: Import name -> distribution name, for the ones where they differ. Only entries that are
#: certain; a guess here installs the wrong package.
DISTRIBUTION = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "skimage": "scikit-image",
    "dateutil": "python-dateutil",
    "serial": "pyserial",
    "OpenSSL": "pyopenssl",
    "Crypto": "pycryptodome",
    "docx": "python-docx",
    "fitz": "pymupdf",
    "attr": "attrs",
    "IPython": "ipython",
}


@dataclass
class Written:
    root: Path
    files: list[Path] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    unknown_distribution: list[str] = field(default_factory=list)
    """Imports written through under their import name because the mapping is unknown."""


def top_level_imports(module_source: str) -> list[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # level > 0 is a relative import, which names nothing installable.
            names.add(node.module.split(".")[0])
    return sorted(names)


def dependencies(module_source: str) -> tuple[list[str], list[str]]:
    """(distribution names, import names whose distribution is unknown)."""
    deps: list[str] = []
    unknown: list[str] = []
    for name in top_level_imports(module_source):
        if name in sys.stdlib_module_names or name.startswith("_"):
            continue
        if name in DISTRIBUTION:
            deps.append(DISTRIBUTION[name])
        else:
            deps.append(name)
            unknown.append(name)
    return sorted(set(deps)), unknown


PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{dist}"
version = "0.1.0"
description = "Generated from {source} by notebook-to-package"
requires-python = ">={pyver}"
# Versions are NOT pinned. The notebook records which packages it imports and not which
# versions it ran against - that kernel is gone. Pin these before relying on the package.
dependencies = [
{deps}]

[project.scripts]
{dist} = "{pkg}.{module}:main"

[tool.hatch.build.targets.wheel]
packages = ["src/{pkg}"]
"""

INIT = '''\
"""{dist} - generated from {source}.

The notebook's code lives in `{module}`. Import what you need from there, or run
`{dist}` to execute it end to end.
"""

from {pkg}.{module} import main

__all__ = ["main"]
'''


def write(nb: Notebook, conv: Converted, into: Path, force: bool = False) -> Written:
    pkg = conv.name
    dist = pkg.replace("_", "-")
    deps, unknown = dependencies(conv.module)

    w = Written(root=into, dependencies=deps, unknown_distribution=unknown)
    src = into / "src" / pkg
    src.mkdir(parents=True, exist_ok=True)

    dep_lines = "".join(f'  "{d}",\n' for d in deps)
    files = {
        into / "pyproject.toml": PYPROJECT.format(
            dist=dist,
            pkg=pkg,
            module=conv.name,
            source=nb.path.name,
            deps=dep_lines,
            pyver=f"{sys.version_info.major}.{sys.version_info.minor}",
        ),
        src / "__init__.py": INIT.format(dist=dist, pkg=pkg, module=conv.name, source=nb.path.name),
        src / f"{conv.name}.py": conv.module,
    }

    for path, body in files.items():
        if path.exists() and not force:
            raise FileExistsError(f"{path} exists; pass force=True to overwrite")
        path.write_text(body, encoding="utf-8", newline="\n")
        w.files.append(path)
    return w
