"""Tests for the friendly error layer (Phase 20)."""

from __future__ import annotations

import subprocess

import pytest
from rich.console import Console

from lapforge import cli
from lapforge.ui.errors import friendly_advice, friendly_message, show_error
from lapforge.ui.theme import LAPFORGE_THEME


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError("denied"),
        FileNotFoundError("missing"),
        TimeoutError("slow"),
        subprocess.TimeoutExpired("cmd", 5),
        OSError("disk"),
        RuntimeError("boom"),
    ],
)
def test_messages_are_plain_sentences(exc):
    message = friendly_message(exc)
    advice = friendly_advice(exc)
    assert message.endswith(".") and advice.endswith(".")
    assert type(exc).__name__ not in message + advice


def test_specific_errors_are_not_hidden_by_oserror():
    assert friendly_message(PermissionError()) != friendly_message(OSError())
    assert friendly_message(FileNotFoundError()) != friendly_message(OSError())
    assert friendly_message(TimeoutError()) != friendly_message(OSError())


def _capture(debug: bool) -> str:
    console = Console(theme=LAPFORGE_THEME, record=True, width=100)
    try:
        raise RuntimeError("secret internal detail")
    except RuntimeError as exc:
        show_error(console, exc, debug=debug)
    return console.export_text()


def test_show_error_panel_without_debug():
    text = _capture(debug=False)
    assert "Something went wrong" in text
    assert "What you can do" in text
    assert "Run with --debug for technical details." in text
    assert "Traceback" not in text
    assert "secret internal detail" not in text


def test_show_error_with_debug_prints_traceback():
    text = _capture(debug=True)
    assert "Traceback" in text
    assert "secret internal detail" in text


def _boom(*_args, **_kwargs):
    raise RuntimeError("forced failure")


@pytest.mark.parametrize("argv", [["--debug", "info"], ["info", "--debug"], ["info"]])
def test_main_shows_friendly_panel_and_returns_1(monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "cmd_info", _boom)
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert "Something went wrong" in out
    assert ("Traceback" in out) == ("--debug" in argv)


def test_main_keyboard_interrupt_returns_130(monkeypatch, capsys):
    def interrupt(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_info", interrupt)
    assert cli.main(["info"]) == 130
    assert "Cancelled" in capsys.readouterr().out


def test_debug_flag_is_in_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "--debug" in capsys.readouterr().out
