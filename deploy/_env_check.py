"""Probe ONE Python interpreter for Auto_ext capability tiers.

Called by ``deploy/doctor.sh`` once per candidate interpreter; prints machine
readable ``KEY=VALUE`` lines on stdout.  Not meant to be run by hand (but
harmless if you do).

Deliberately written in the most conservative syntax available -- no f-strings,
no annotations, no dataclasses -- so that even an ancient interpreter can PARSE
it and cleanly report itself as unusable, instead of dying with a SyntaxError
that looks like a corrupt package.  This is not hypothetical here: on the
deployment target, bare ``python`` on PATH is Python 2.7 (it comes from an
OpenOffice install), and that interpreter WILL be probed.

Everything printed here is pure ASCII on purpose: the red zone's ``LANG`` is
often ``C``, and a non-ASCII byte in this stream would make the probe die with
UnicodeEncodeError -- i.e. a perfectly good box would look broken.

The strong check is the real ``import`` of the shipped package, not just "is
Python new enough": importing ``auto_ext.core.runner`` proves the package landed
intact AND that this interpreter can compile it.

Tiers:

    tier 1  render templates, dry-run, run the shipped test suite
            needs: Python >= 3.11 + the offline dependency wheels
    tier 2  really drive the EDA tools
            needs: + si / strmout / calibre / qrc on PATH
    tier 3  GUI
            needs: + an importable PyQt5 and a $DISPLAY

Tiers are cumulative (tier 3 implies tier 2 implies tier 1).  A missing tier-3
dependency is a DEGRADE, not a failure: a plain ssh session is supposed to have
no $DISPLAY.
"""

import os
import sys

# pyproject.toml says requires-python >= 3.11, and the package uses 3.11 syntax
# throughout.  The deployment target ships 3.11.4.
MIN_PY = (3, 11)

# Third-party runtime dependencies, installed from the offline wheel bundle by
# scripts/install_offline.sh.  Unlike the sibling project (pure stdlib), Auto_ext
# genuinely cannot reach tier 1 without these, so they gate it.
# Import name first, human name second -- ruamel.yaml differs from its wheel.
DEPS = (
    ("jinja2", "Jinja2"),
    ("ruamel.yaml", "ruamel.yaml"),
    ("pydantic", "pydantic"),
    ("typer", "typer"),
    ("rich", "rich"),
)

# tier 2 = "can really run the flow".  jivaro is deliberately NOT here: reduction
# is optional per recipe (``jivaro.enabled: false`` is a normal configuration),
# so a box without it can still do real extraction runs.  It is reported, never
# gated on.
TOOLS_FOR_RUN = ("si", "strmout", "calibre", "qrc")
TOOLS_OPTIONAL = ("jivaro",)

# Reported for orientation only.  Whether a given run has everything it needs is
# a question about the selected PdkProfile, and `auto-ext check-env` answers it
# properly -- duplicating that logic here would produce a second, subtly
# different answer, which is worse than no answer.
ENV_VARS = ("WORK_ROOT", "WORK_ROOT2", "VERIFY_ROOT", "SETUP_ROOT", "PDK_LAYER_MAP_FILE")


def emit(key, value):
    sys.stdout.write("%s=%s\n" % (key, value))


def try_import(name):
    """Return (ok, detail). detail is a version string or the error text."""
    try:
        __import__(name)
        mod = sys.modules.get(name)
    except Exception:
        exc = sys.exc_info()[1]
        return False, ("%s: %s" % (exc.__class__.__name__, exc)).replace("\n", " ")
    ver = getattr(mod, "__version__", "") if mod is not None else ""
    if not isinstance(ver, str):
        ver = str(ver)
    return True, ver


def which(name):
    """First match for `name` on PATH, or "" -- shutil.which is 3.3+ only."""
    path = os.environ.get("PATH", "")
    for part in path.split(os.pathsep):
        if not part:
            continue
        candidate = os.path.join(part, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def pyqt5_bundled_qt_dir():
    """Directory of the Qt5 libs bundled inside the PyQt5 wheel, or "".

    On the CentOS 7-class deployment target, /usr/lib64/libstdc++.so.6 tops out
    at GLIBCXX_3.4.19 and lacks the C++14 sized-delete symbol that a site-wide
    Qt5 needs, so importing PyQt5 fails until this directory is first on
    LD_LIBRARY_PATH.  run.sh does that prepend for the `gui` and `test`
    subcommands; doctor.sh uses this path to re-probe and tell the operator that
    the GUI is fine, the bare interpreter just is not the way in.
    """
    try:
        import PyQt5
    except Exception:
        return ""
    base = os.path.dirname(getattr(PyQt5, "__file__", "") or "")
    if not base:
        return ""
    lib = os.path.join(base, "Qt5", "lib")
    return lib if os.path.isdir(lib) else ""


# ---------------------------------------------------------------------------
# Tier decision -- pure functions, so tests/test_deploy.py can drive them with a
# hand-written truth table instead of needing a real box.  Keep them free of
# I/O: the moment they probe anything themselves, the test has to fake a whole
# environment and stops being a test of the DECISION.
# ---------------------------------------------------------------------------


def missing_deps(deps_found):
    """Which third-party dependencies are absent, in the declared order."""
    out = []
    for name, human in DEPS:
        if not deps_found.get(name):
            out.append(human)
    return out


def missing_tools(tools_found):
    """Which of TOOLS_FOR_RUN are absent, in the declared order."""
    out = []
    for name in TOOLS_FOR_RUN:
        if not tools_found.get(name):
            out.append(name)
    return out


def decide_tier(py_ok, deps_found, core_ok, tools_found, qt_ok, display_ok):
    """Capability facts -> tier number.

    0  unusable (interpreter too old, dependencies absent, or the package will
       not import)
    1  render / dry-run / unit tests
    2  + really drive the EDA tools
    3  + GUI
    """
    if not py_ok or missing_deps(deps_found) or not core_ok:
        return 0
    if missing_tools(tools_found):
        return 1
    if not (qt_ok and display_ok):
        return 2
    return 3


def tier_blocker(tier, py_ok, deps_found, core_ok, tools_found, qt_ok, display_ok):
    """One line naming what keeps this interpreter out of the next tier up."""
    if tier == 0:
        if not py_ok:
            return "python is older than %d.%d" % MIN_PY
        gone = missing_deps(deps_found)
        if gone:
            return (
                "offline dependencies not installed: "
                + ", ".join(gone)
                + " -- run: bash scripts/install_offline.sh"
            )
        return "the shipped package does not import -- incomplete install?"
    if tier == 1:
        return "not on PATH: " + ", ".join(missing_tools(tools_found))
    if tier == 2:
        if not qt_ok:
            return "PyQt5 does not import for this interpreter"
        return "PyQt5 is fine but $DISPLAY is unset (normal in a plain ssh session)"
    return ""


# ---------------------------------------------------------------------------


def probe_qt():
    """(ok, detail, bundled_lib_dir) for PyQt5 in THIS process."""
    ok, detail = try_import("PyQt5.QtCore")
    if ok:
        try:
            from PyQt5 import QtCore

            detail = QtCore.QT_VERSION_STR
        except Exception:
            pass
    return ok, detail, pyqt5_bundled_qt_dir()


def main():
    argv = sys.argv[1:]

    # Second pass: doctor.sh re-runs us with LD_LIBRARY_PATH pointing at the
    # wheel's bundled Qt5.  LD_LIBRARY_PATH is read by the dynamic loader at
    # process start, so this genuinely cannot be done in one process.
    if "--qt-only" in argv:
        ok, detail, _ = probe_qt()
        emit("MOD_PyQt5", "OK" if ok else "FAIL")
        emit("MOD_PyQt5_detail", detail)
        return 0

    emit("PY_EXEC", sys.executable or "?")
    emit("PY_VERSION", sys.version.split()[0])
    # The red-zone rule is "no venv" (nothing can be installed there anyway) --
    # flag one if we are somehow inside it, so doctor.sh can warn instead of
    # silently recommending a non-reproducible interpreter.
    base = getattr(sys, "base_prefix", getattr(sys, "real_prefix", sys.prefix))
    emit("PY_VENV", "YES" if base != sys.prefix else "NO")

    py_ok = sys.version_info[0] >= 3 and sys.version_info[:2] >= MIN_PY
    if not py_ok:
        emit("PY_OK", "NO")
        emit("PY_WHY", "need >= %d.%d" % MIN_PY)
        emit("TIER", "0")
        emit("TIER_WHY", tier_blocker(0, False, {}, False, {}, False, False))
        return 1
    emit("PY_OK", "YES")

    # --- third-party dependencies (tier 1 gate) ----------------------------
    deps_found = {}
    for name, human in DEPS:
        ok, detail = try_import(name)
        deps_found[name] = ok
        emit("DEP_" + name.replace(".", "_"), "OK" if ok else "MISSING")
        emit("DEP_" + name.replace(".", "_") + "_detail", detail)
        emit("DEP_" + name.replace(".", "_") + "_human", human)

    # --- the shipped package (the real test) -------------------------------
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    emit("INSTALL_ROOT", root)
    if root not in sys.path:
        sys.path.insert(0, root)

    runner_ok, runner_detail = try_import("auto_ext.core.runner")
    emit("IMP_core_runner", "OK" if runner_ok else "FAIL")
    emit("IMP_core_runner_detail", runner_detail)

    cli_ok, cli_detail = try_import("auto_ext.cli")
    emit("IMP_cli", "OK" if cli_ok else "FAIL")
    emit("IMP_cli_detail", cli_detail)

    render_ok, render_detail = try_import("auto_ext.core.render")
    emit("IMP_core_render", "OK" if render_ok else "FAIL")
    emit("IMP_core_render_detail", render_detail)

    core_ok = runner_ok and cli_ok and render_ok

    # --- external tools (tier 2) -------------------------------------------
    # Same lookup the tool itself uses: auto_ext.tools.base resolves argv[0] with
    # shutil.which against the inherited PATH, so a plain PATH scan reports
    # exactly what a real run would find.
    tools_found = {}
    for name in TOOLS_FOR_RUN + TOOLS_OPTIONAL:
        where = which(name)
        tools_found[name] = where
        emit("TOOL_" + name, "OK" if where else "MISSING")
        emit("TOOL_" + name + "_detail", where)

    # --- environment variables (informational) -----------------------------
    for var in ENV_VARS:
        emit("ENV_" + var, os.environ.get(var, ""))

    # --- Qt / GUI (tier 3) --------------------------------------------------
    qt_ok, qt_detail, qt_lib = probe_qt()
    emit("MOD_PyQt5", "OK" if qt_ok else "FAIL")
    emit("MOD_PyQt5_detail", qt_detail)
    emit("QT_BUNDLED_LIB", qt_lib)
    display = os.environ.get("DISPLAY", "")
    emit("ENV_DISPLAY", display)
    display_ok = bool(display)

    # --- capability tiers --------------------------------------------------
    tier = decide_tier(py_ok, deps_found, core_ok, tools_found, qt_ok, display_ok)
    emit("CAP_core", "YES" if tier >= 1 else "NO")
    emit("CAP_run", "YES" if tier >= 2 else "NO")
    emit("CAP_gui", "YES" if tier >= 3 else "NO")
    # "the GUI code is fine, this box just has no X11" -- worth saying out loud
    # so a headless ssh session is not mistaken for a broken install.
    emit("GUI_CODE", "OK" if (core_ok and qt_ok) else "NO")
    emit("TIER", "%d" % tier)
    emit("TIER_WHY", tier_blocker(tier, py_ok, deps_found, core_ok, tools_found, qt_ok, display_ok))
    return 0


if __name__ == "__main__":
    sys.exit(main())
