"""Tests for lapforge.utils (formatting helpers, text cleaning, safe command running).

Everything here is pure or monkeypatched, so it passes on any OS (no Windows needed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Let a plain `pytest -q` find the package even before `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lapforge import utils  # noqa: E402
from lapforge.utils import (  # noqa: E402
    NA,
    as_list,
    clean_text,
    format_bytes,
    format_duration,
    format_percent,
    is_admin,
    ordinal,
    powershell_json,
    run_cmd,
    run_powershell,
)


# --------------------------------------------------------------------------
# format_bytes
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024**2, "1.0 MB"),
        (5 * 1024**3, "5.0 GB"),
        (1024**4, "1.0 TB"),
        (3 * 1024**5, "3072.0 TB"),  # anything above TB stays in TB
    ],
)
def test_format_bytes_units(value, expected):
    assert format_bytes(value) == expected


def test_format_bytes_digits():
    assert format_bytes(1536, digits=0) == "2 KB"
    assert format_bytes(1536, digits=2) == "1.50 KB"


@pytest.mark.parametrize("bad", [None, -1, -1024, "abc", [], object()])
def test_format_bytes_invalid_is_na(bad):
    assert format_bytes(bad) == NA


def test_format_bytes_accepts_numeric_string():
    assert format_bytes("2048") == "2.0 KB"


# --------------------------------------------------------------------------
# format_percent
# --------------------------------------------------------------------------
def test_format_percent_basic():
    assert format_percent(45.678) == "45.7%"
    assert format_percent(45.678, digits=0) == "46%"
    assert format_percent(0) == "0.0%"
    assert format_percent(100) == "100.0%"


@pytest.mark.parametrize("bad", [None, "x", [], {}])
def test_format_percent_invalid_is_na(bad):
    assert format_percent(bad) == NA


# --------------------------------------------------------------------------
# format_duration
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0m"),
        (59, "0m"),
        (60, "1m"),
        (3600, "1h 0m"),
        (3 * 3600 + 5 * 60, "3h 5m"),
        (86400, "1d 0h 0m"),  # hours are kept once days are shown
        (93784, "1d 2h 3m"),
        (90061.9, "1d 1h 1m"),  # floats are truncated
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize("bad", [None, -5, "abc", []])
def test_format_duration_invalid_is_na(bad):
    assert format_duration(bad) == NA


# --------------------------------------------------------------------------
# ordinal
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "n, expected",
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (10, "10th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (101, "101st"),
        (111, "111th"),
        (112, "112th"),
    ],
)
def test_ordinal(n, expected):
    assert ordinal(n) == expected


# --------------------------------------------------------------------------
# clean_text / as_list
# --------------------------------------------------------------------------
def test_clean_text_strips_whitespace():
    assert clean_text("  Dell Inc.  ") == "Dell Inc."


@pytest.mark.parametrize(
    "junk",
    [
        None,
        "",
        "   ",
        "None",
        "NULL",
        "Unknown",
        "Default string",
        "To Be Filled By O.E.M.",
        "System Serial Number",
        "System Product Name",
        "Not Specified",
        "0",
    ],
)
def test_clean_text_junk_becomes_default(junk):
    assert clean_text(junk) == NA


def test_clean_text_custom_default():
    assert clean_text("unknown", default="-") == "-"
    assert clean_text(None, default="") == ""


def test_clean_text_keeps_real_values_and_converts_numbers():
    assert clean_text("ThinkPad X1") == "ThinkPad X1"
    assert clean_text(16) == "16"


def test_as_list():
    assert as_list(None) == []
    assert as_list([1, 2]) == [1, 2]
    assert as_list({"a": 1}) == [{"a": 1}]  # one-item PowerShell arrays arrive as objects
    assert as_list("x") == ["x"]


# --------------------------------------------------------------------------
# run_cmd / PowerShell helpers (never raise)
# --------------------------------------------------------------------------
def test_run_cmd_returns_stdout():
    out = run_cmd([sys.executable, "-c", "print('hello')"])
    assert out == "hello"


def test_run_cmd_missing_program_returns_empty():
    assert run_cmd(["definitely-not-a-real-program-lapforge"]) == ""


def test_run_cmd_timeout_returns_empty():
    assert run_cmd([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1) == ""


def test_run_cmd_bad_args_returns_empty():
    assert run_cmd([""]) == ""


def test_run_powershell_is_empty_off_windows(monkeypatch):
    monkeypatch.setattr(utils, "IS_WINDOWS", False)
    assert run_powershell("Get-Date") == ""


def test_powershell_json_none_when_no_output(monkeypatch):
    monkeypatch.setattr(utils, "run_powershell", lambda script, timeout=40: "")
    assert powershell_json("anything") is None


def test_powershell_json_parses_valid_json(monkeypatch):
    monkeypatch.setattr(utils, "run_powershell", lambda script, timeout=40: '{"a": [1, 2]}')
    assert powershell_json("anything") == {"a": [1, 2]}


def test_powershell_json_none_on_invalid_json(monkeypatch):
    monkeypatch.setattr(utils, "run_powershell", lambda script, timeout=40: "not json")
    assert powershell_json("anything") is None


# --------------------------------------------------------------------------
# is_admin
# --------------------------------------------------------------------------
def test_is_admin_returns_bool():
    assert isinstance(is_admin(), bool)


def test_is_admin_false_when_check_fails(monkeypatch):
    monkeypatch.setattr(utils, "IS_WINDOWS", False)

    def boom():
        raise OSError("no euid")

    monkeypatch.setattr(utils.os, "geteuid", boom, raising=False)
    assert is_admin() is False
