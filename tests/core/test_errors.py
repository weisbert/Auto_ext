"""Tests for the :mod:`auto_ext.core.errors` hierarchy."""

from __future__ import annotations

import pytest

from auto_ext.core.errors import (
    AutoExtError,
    CheckError,
    ConfigError,
    EnvResolutionError,
    TemplateError,
    WorkdirError,
)


@pytest.mark.parametrize(
    "subclass",
    [ConfigError, EnvResolutionError, TemplateError, WorkdirError, CheckError],
)
def test_subclasses_inherit_autoexterror(subclass: type[Exception]) -> None:
    assert issubclass(subclass, AutoExtError)
    assert issubclass(subclass, Exception)


def test_autoexterror_is_raiseable() -> None:
    with pytest.raises(AutoExtError):
        raise ConfigError("bad yaml")


def test_error_str_preserves_message() -> None:
    err = TemplateError("render failed: /tmp/foo.j2")
    assert str(err) == "render failed: /tmp/foo.j2"


def test_errors_are_distinct_types() -> None:
    assert ConfigError is not EnvResolutionError
    assert TemplateError is not WorkdirError
    assert WorkdirError is not CheckError
