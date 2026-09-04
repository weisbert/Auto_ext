"""The health report's "can this host open a file?" row.

Split out of ``tests/core/test_health.py`` because it is about the host rather
than about a PDK profile: it exists so the Setup drawer can say *this box
cannot open files* before the user presses a button that then does nothing at
all -- the symptom on the deployment target, where neither ``xdg-open`` nor
``gio`` is installed.
"""

from __future__ import annotations

import pytest

from auto_ext.core import health
from auto_ext.core.health import check_profile, default_checks
from auto_ext.model.pdk import CheckStatus, PdkCheckKind, PdkProfile


@pytest.fixture
def profile() -> PdkProfile:
    return PdkProfile(profile_id="cfxxx", display_name="CFXXX")


def _checks(platform: str):
    return health._file_opener_checks(platform)


def test_linux_gets_a_file_opener_check(profile: PdkProfile) -> None:
    (check,) = _checks("linux")
    assert check.check_id == "tool.file_opener"
    assert check.kind is PdkCheckKind.ON_PATH
    assert check.target == "xdg-open|gio"
    # A warning, never a blocker: the flow runs fine, you just cannot read the
    # report afterwards without leaving the app.
    assert check.required is False
    assert "xdg-utils" in check.fix_hint


def test_windows_and_macos_get_no_row(profile: PdkProfile) -> None:
    """ShellExecuteW and `open` are part of the OS: a row that can only say
    yes is noise in a list meant to be read."""

    assert _checks("win32") == []
    assert _checks("darwin") == []


def test_either_launcher_satisfies_the_check(profile: PdkProfile) -> None:
    (check,) = _checks("linux")
    report = check_profile(
        profile,
        checks=[check],
        which=lambda name: "/usr/bin/gio" if name == "gio" else None,
    )
    result = report.result("tool.file_opener")
    assert result.status is CheckStatus.OK
    assert result.observed == "/usr/bin/gio"


def test_a_host_with_neither_launcher_warns_and_names_both(
    profile: PdkProfile,
) -> None:
    (check,) = _checks("linux")
    report = check_profile(profile, checks=[check], which=lambda _name: None)
    result = report.result("tool.file_opener")

    # WARN, not FAIL: check_profile downgrades a non-required failure, so the
    # run is not blocked and the drawer still shows the row.
    assert result.status is CheckStatus.WARN
    assert report.can_run
    assert "xdg-open" in result.message and "gio" in result.message
    assert result.fix_hint


def test_the_row_reaches_the_setup_drawer(profile: PdkProfile) -> None:
    """It is only useful if it is listed, and it must not land in "Other"."""

    from auto_ext.ui.screens import setup_drawer as sd

    ids = [c.check_id for c in default_checks(profile)]
    if health.sys.platform in ("win32", "darwin"):
        assert "tool.file_opener" not in ids
        return
    assert "tool.file_opener" in ids
    assert sd.group_for("tool.file_opener") == "tools"


def test_a_single_binary_target_is_unaffected(profile: PdkProfile) -> None:
    """The alternatives separator must not change what every other tool check
    reports -- `si` has no `|` in it and is looked up exactly as before."""

    report = check_profile(
        profile, which=lambda name: "/opt/cad/bin/si" if name == "si" else None
    )
    assert report.result("tool.si").status is CheckStatus.OK
    assert report.result("tool.calibre").status is CheckStatus.FAIL
    assert "calibre was not found on PATH" in report.result("tool.calibre").message
