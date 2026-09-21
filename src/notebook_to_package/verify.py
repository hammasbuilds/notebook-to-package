"""Run the notebook and the generated package, and compare what each left behind.

A conversion that imports cleanly has proved nothing. The rearranging happens at module
level, and the way it breaks is a `NameError` inside a function that nobody calls until
later. So both sides are executed and their resulting namespaces compared.

## Comparing namespaces is the hard part

A DataFrame is not `==` a DataFrame - it returns a frame of booleans, and `bool()` of that
raises. An array does the same. An object without `__eq__` compares by identity, and the two
runs are separate processes, so identity never matches. Sorting on any of those gives a
confident wrong answer.

So each value is reduced to a **digest**: a type name plus a normalised `repr`, with memory
addresses stripped. That is weaker than equality and it is weaker in a stated direction - two
different objects with the same repr compare equal here. It is also the only comparison that
works across a process boundary for arbitrary third-party types without importing them.

Values that cannot be digested at all - a repr that raises, an object whose repr is its
address - are counted as `opaque` and excluded from both sides, because a comparison that
always fails is not evidence either way.

## Both sides run in a subprocess

Executing a notebook in this interpreter would let it `sys.exit`, chdir, install a signal
handler or leak imports into the comparison. It also lets the two runs use a *different*
interpreter from this one, which matters: the notebook needs pandas and matplotlib, and this
tool needs nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from notebook_to_package.convert import Converted
from notebook_to_package.notebook import Notebook
from notebook_to_package.notebook import parse as nb_parse

_ADDR = re.compile(r"0x[0-9a-fA-F]{4,}")
_MAXLEN = 2000

#: Names every run defines that say nothing about the notebook.
IGNORED = {
    "__name__",
    "__doc__",
    "__package__",
    "__loader__",
    "__spec__",
    "__builtins__",
    "__file__",
    "__annotations__",
    "__cached__",
    "annotations",
    "main",
}

_HARNESS = r"""
import json, sys, types, re, importlib.util
_ADDR = re.compile(r"0x[0-9a-fA-F]{4,}")
IGNORED = set(json.loads(sys.argv[3]))

OWN = sys.argv[4]

def digest(v):
    try:
        r = repr(v)
    except Exception as e:
        return {"opaque": "repr raised " + type(e).__name__}
    r = _ADDR.sub("0xADDR", r)
    # A default repr is "<module.Class object at 0x...>", and the two sides necessarily
    # run under different module names - the notebook as __main__, the package as an
    # imported module. Normalising the address alone still left every user-defined object
    # differing, on the module name. Each side strips its own.
    r = r.replace(OWN + ".", "<mod>.")
    if len(r) > __MAXLEN__:
        r = r[:__MAXLEN__] + "...<truncated>"
    return {"type": type(v).__name__, "repr": r}

def namespace(ns):
    out = {}
    for k, v in ns.items():
        if k in IGNORED or k.startswith("__"):
            continue
        if isinstance(v, types.ModuleType | types.FunctionType | type):
            # Functions and classes differ by qualname and module between the two runs by
            # construction; comparing them would fail on every notebook for no reason.
            continue
        out[k] = digest(v)
    return out

mode, target = sys.argv[1], sys.argv[2]
result = {"ok": False, "error": None, "names": {}}
try:
    if mode == "script":
        g = {"__name__": "__main__", "__file__": target}
        with open(target, encoding="utf-8") as fh:
            code = fh.read()
        exec(compile(code, target, "exec"), g)
        result["names"] = namespace(g)
    else:
        # Imported, not runpy'd. `runpy.run_path` hands back a *copy* of the globals taken
        # when the module finished executing - before main() is called - so every value the
        # conversion computes lands in the real module dict and none of it in that copy.
        # The first version compared against the copy and reported every notebook as
        # unfaithful, with every name "only in the notebook".
        spec = importlib.util.spec_from_file_location("ntp_generated", target)
        module = importlib.util.module_from_spec(spec)
        sys.modules["ntp_generated"] = module
        spec.loader.exec_module(module)
        fn = getattr(module, "main", None)
        if fn is None:
            raise RuntimeError("generated module has no main()")

        # main()'s locals are the other half of the answer. The notebook leaves every
        # scratch variable a module global; the package deliberately does not, so comparing
        # module namespaces alone would call the intended change a fidelity failure. What
        # has to match is the values computed, wherever they ended up living.
        captured = {}

        def _outer(frame, event, arg):
            if event == "call" and frame.f_code.co_name == "main":
                def _inner(f, ev, a):
                    if ev == "return":
                        captured.update(f.f_locals)
                    return _inner
                return _inner
            return None

        sys.settrace(_outer)
        try:
            fn()
        finally:
            sys.settrace(None)

        merged = dict(vars(module))
        merged.update(captured)
        result["names"] = namespace(merged)
    result["ok"] = True
except BaseException as e:
    result["error"] = type(e).__name__ + ": " + str(e)[:300]

sys.stdout.write("\x01NTP\x01" + json.dumps(result))
""".replace("__MAXLEN__", str(_MAXLEN))


@dataclass
class Run:
    ok: bool = False
    error: str | None = None
    names: dict[str, dict] = field(default_factory=dict)
    timed_out: bool = False

    @property
    def opaque(self) -> set[str]:
        return {k for k, v in self.names.items() if "opaque" in v}


@dataclass
class Verdict:
    notebook: Run
    package: Run
    same: list[str] = field(default_factory=list)
    differs: list[tuple[str, str, str]] = field(default_factory=list)
    only_notebook: list[str] = field(default_factory=list)
    only_package: list[str] = field(default_factory=list)
    skipped_opaque: list[str] = field(default_factory=list)
    nondeterministic: list[str] = field(default_factory=list)
    """Names that differed between two runs of the **notebook itself**.

    Nothing can be said about these. A notebook calling `np.random.random(100)` with no
    seed produces different values every run, and comparing one run against another - by
    any route - finds a difference that the conversion did not cause.

    This was not a hypothetical. The first corpus run reported four conversion failures;
    every one was a notebook using unseeded `np.random`, and the names it named
    (`xdata, ydata, zdata`) were arrays of random numbers. Four false accusations out of
    four - the entire failure list.
    """

    @property
    def comparable(self) -> int:
        return len(self.same) + len(self.differs)

    @property
    def faithful(self) -> bool | None:
        """None when neither side ran, so there is nothing to conclude."""
        if not (self.notebook.ok and self.package.ok):
            return None
        return not self.differs and not self.only_notebook and not self.only_package


def linearise(nb: Notebook) -> str:
    """The notebook's code cells as one script, in document order, magics removed.

    Document order, not execution order. It is the order a reader sees and the order any
    conversion must preserve; running it in `execution_count` order would test a program
    nobody has.
    """
    parts = []
    for cell in nb.cells:
        src = cell.source
        if cell.magics:
            drop = set(cell.magics)
            src = "\n".join(ln for ln in src.splitlines() if ln.strip() not in drop)
        if not src.strip():
            continue
        try:
            nb_parse(src)
        except SyntaxError:
            continue
        parts.append(f"# --- cell {cell.index} ---\n{src}")
    return "\n\n".join(parts) + "\n"


#: Environment every run gets, on both sides equally.
#:
#: `MPLBACKEND=Agg` is the one that matters. matplotlib's default backend on a desktop
#: opens a window, and `plt.show()` then blocks until somebody closes it - forever, on a
#: machine nobody is watching. The timeout does not save you: it kills the interpreter it
#: started, and the window belongs to a GUI thread that keeps the pipe open, so the parent
#: sits in communicate() waiting for an EOF that never comes. One notebook hung a corpus
#: run for twenty-five minutes on a forty-five second timeout.
RUN_ENV = {
    "MPLBACKEND": "Agg",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and anything it started.

    `Popen.kill()` reaches the child and nothing below it. A notebook that shells out, or
    that matplotlib has handed a GUI thread, leaves a grandchild holding the output pipe
    open - and the read that follows blocks forever, which is the failure being avoided.
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        else:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _run(mode: str, target: Path, python: str, timeout: float, cwd: Path) -> Run:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(_HARNESS)
        harness = Path(fh.name)
    env = dict(os.environ)
    env.update(RUN_ENV)
    cmd = [
        python,
        str(harness),
        mode,
        str(target),
        json.dumps(sorted(IGNORED)),
        "__main__" if mode == "script" else "ntp_generated",
    ]
    kwargs = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Never inherited. A notebook calling input() would otherwise read this
            # process's stdin and wait for a key that is never pressed.
            stdin=subprocess.DEVNULL,
            text=True,
            cwd=cwd,
            env=env,
            errors="replace",
            **kwargs,
        )
    except OSError as e:
        harness.unlink(missing_ok=True)
        return Run(ok=False, error=f"could not start interpreter: {e}")

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        harness.unlink(missing_ok=True)
        return Run(ok=False, error=f"timed out after {timeout:g}s", timed_out=True)
    finally:
        harness.unlink(missing_ok=True)

    out = stdout or ""
    marker = out.rfind("\x01NTP\x01")
    if marker < 0:
        # The process died before it could report - a segfault, an os._exit, a sys.exit in
        # the notebook. Whatever it printed is the only evidence there is.
        tail = ((stderr or "") + out).strip().splitlines()
        return Run(ok=False, error="no result: " + (tail[-1][:200] if tail else "no output"))
    try:
        data = json.loads(out[marker + 5 :])
    except json.JSONDecodeError as e:
        return Run(ok=False, error=f"unparseable result: {e}")
    return Run(ok=data["ok"], error=data["error"], names=data["names"])


def verify(
    nb: Notebook,
    conv: Converted,
    python: str = "",
    timeout: float = 300.0,
    workdir: Path | None = None,
    control: bool = True,
) -> Verdict:
    """Run both in the notebook's own directory, so relative data paths resolve.

    `control` runs the **notebook** a second time and throws away every name that differs
    between its own two runs. Without it, a notebook with unseeded randomness is reported
    as a conversion failure - and the conversion had nothing to do with it. It costs a third
    execution, which is the price of the difference between a measurement and an accusation.
    """
    python = python or sys.executable
    cwd = workdir or nb.path.parent

    with tempfile.TemporaryDirectory(prefix="ntp-") as tmp:
        script = Path(tmp) / "linear.py"
        script.write_text(linearise(nb), encoding="utf-8")
        module = Path(tmp) / f"{conv.name}.py"
        module.write_text(conv.module, encoding="utf-8")

        a = _run("script", script, python, timeout, cwd)
        control_run = _run("script", script, python, timeout, cwd) if control and a.ok else None
        b = _run("module", module, python, timeout, cwd)

    v = Verdict(notebook=a, package=b)
    if not (a.ok and b.ok):
        return v

    unstable: set[str] = set()
    if control_run is not None and control_run.ok:
        for name, digest in a.names.items():
            if control_run.names.get(name) != digest:
                unstable.add(name)
        unstable |= set(control_run.names) - set(a.names)
        v.nondeterministic = sorted(unstable)

    opaque = a.opaque | b.opaque
    v.skipped_opaque = sorted(opaque)

    for name in sorted(set(a.names) | set(b.names)):
        if name in opaque or name in unstable:
            continue
        in_a, in_b = name in a.names, name in b.names
        if in_a and not in_b:
            v.only_notebook.append(name)
        elif in_b and not in_a:
            v.only_package.append(name)
        elif a.names[name] == b.names[name]:
            v.same.append(name)
        else:
            v.differs.append((name, a.names[name]["repr"][:120], b.names[name]["repr"][:120]))
    return v
