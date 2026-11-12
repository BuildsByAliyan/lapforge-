"""Tests for the launchers and double-click behaviour (Phase 31). No Windows needed."""

from __future__ import annotations

import io
import runpy
from pathlib import Path

import pytest

from lapforge import app, cli, elevate

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def window(monkeypatch):
    """Control whether the console belongs to LapForge, and record the 'Press Enter' question."""
    state = {"own": False, "asked": []}
    monkeypatch.setattr(elevate, "console_owned_by_us", lambda: state["own"])

    def fake_input(prompt=""):
        state["asked"].append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    return state


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code, expected",
    [(None, 0), (0, 0), (2, 2), (130, 130), (True, 1), (False, 0), ("message", 1), (3.5, 1)],
)
def test_exit_code_from(code, expected):
    assert app.exit_code_from(code) == expected


def test_should_pause_only_for_an_own_console():
    assert app.should_pause(True, ["info"]) is True
    assert app.should_pause(True, []) is True
    assert app.should_pause(False, ["info"]) is False
    assert app.should_pause(False, []) is False


def test_should_not_pause_twice_in_a_restarted_administrator_window():
    assert app.should_pause(True, ["info", elevate.ELEVATED_FLAG]) is False


# --------------------------------------------------------------------------
# run()
# --------------------------------------------------------------------------
def test_run_passes_the_arguments_and_returns_the_exit_code(monkeypatch, window):
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv: seen.append(argv) or 7)
    assert app.run(["health", "--fail-on", "bad"]) == 7
    assert seen == [["health", "--fail-on", "bad"]]


def test_run_uses_the_command_line_by_default(monkeypatch, window):
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr("sys.argv", ["LapForge.exe", "scan"])
    assert app.run() == 0
    assert seen == [["scan"]]


def test_double_click_waits_for_enter(monkeypatch, window):
    window["own"] = True
    monkeypatch.setattr(cli, "main", lambda argv: 0)
    assert app.run([]) == 0
    assert len(window["asked"]) == 1
    assert "Press Enter to close this window." in window["asked"][0]


def test_started_from_a_terminal_never_waits(monkeypatch, window):
    window["own"] = False
    monkeypatch.setattr(cli, "main", lambda argv: 0)
    assert app.run(["info"]) == 0
    assert window["asked"] == []


def test_a_restarted_administrator_window_does_not_wait_a_second_time(monkeypatch, window):
    window["own"] = True
    monkeypatch.setattr(cli, "main", lambda argv: 0)
    app.run(["menu", elevate.ELEVATED_FLAG])
    assert window["asked"] == []


def test_waiting_survives_closed_input_and_ctrl_c(monkeypatch, window):
    window["own"] = True
    monkeypatch.setattr(cli, "main", lambda argv: 0)
    for error in (EOFError, KeyboardInterrupt):
        def broken(prompt="", error=error):
            raise error

        monkeypatch.setattr("builtins.input", broken)
        assert app.run([]) == 0


def test_a_failing_console_check_means_no_waiting(monkeypatch, window):
    def broken():
        raise OSError("no console")

    monkeypatch.setattr(elevate, "console_owned_by_us", broken)
    monkeypatch.setattr(cli, "main", lambda argv: 0)
    assert app.run([]) == 0
    assert window["asked"] == []


@pytest.mark.parametrize("raised, expected", [(0, 0), (2, 2), (None, 0), ("bad", 1)])
def test_version_help_and_usage_errors_end_with_the_right_code(monkeypatch, window, raised, expected):
    def exits(argv):
        raise SystemExit(raised)

    monkeypatch.setattr(cli, "main", exits)
    assert app.run(["--version"]) == expected


def test_the_window_still_waits_after_version_output(monkeypatch, window):
    window["own"] = True

    def exits(argv):
        raise SystemExit(0)

    monkeypatch.setattr(cli, "main", exits)
    assert app.run(["--version"]) == 0
    assert len(window["asked"]) == 1


def test_ctrl_c_gives_exit_code_130(monkeypatch, window, capsys):
    def interrupted(argv):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "main", interrupted)
    assert app.run([]) == 130
    assert "Cancelled." in capsys.readouterr().out


def test_an_unexpected_error_is_calm_and_the_window_still_waits(monkeypatch, window, capsys):
    window["own"] = True

    def broken(argv):
        raise RuntimeError("internal detail")

    monkeypatch.setattr(cli, "main", broken)
    assert app.run([]) == 1
    out = capsys.readouterr().out
    assert "Something went wrong" in out
    assert "Traceback" not in out and "RuntimeError" not in out
    assert len(window["asked"]) == 1


def test_run_with_the_real_main_shows_the_version(window, capsys):
    assert app.run(["--version"]) == 0
    assert "LapForge" in capsys.readouterr().out


# --------------------------------------------------------------------------
# UTF-8 output
# --------------------------------------------------------------------------
def test_utf8_output_is_requested_for_both_streams(monkeypatch):
    calls = []

    class Stream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("sys.stdout", Stream())
    monkeypatch.setattr("sys.stderr", Stream())
    app.set_utf8_output()
    assert calls == [{"encoding": "utf-8", "errors": "replace"}] * 2


def test_utf8_output_never_fails_on_odd_streams(monkeypatch):
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", io.StringIO())  # StringIO.reconfigure does not exist
    app.set_utf8_output()


# --------------------------------------------------------------------------
# python -m lapforge
# --------------------------------------------------------------------------
def test_python_dash_m_calls_app_run(monkeypatch):
    monkeypatch.setattr(app, "run", lambda: 5)
    with pytest.raises(SystemExit) as info:
        runpy.run_module("lapforge", run_name="__main__")
    assert info.value.code == 5


# --------------------------------------------------------------------------
# The two .bat files (checked as text; they run on Windows)
# --------------------------------------------------------------------------
def read_bat(name):
    data = (ROOT / name).read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), f"{name} must use Windows line endings"
    text = data.decode("ascii")  # plain ASCII so no code page can garble it
    return text, text.lower()


def test_start_launcher_checks_python_installs_quietly_and_runs_lapforge():
    text, low = read_bat("Start-LapForge.bat")
    assert "sys.version_info >= (3, 10)" in text
    assert "https://www.python.org/downloads/" in text
    assert "-m pip install --quiet" in text and '"psutil>=5.9"' in text and '"rich>=13.0"' in text
    assert "-m lapforge %*" in text
    assert "pause" in low
    assert "import psutil, rich" in text  # the install only happens the first time


def test_start_launcher_keeps_the_window_open_after_a_double_click():
    text, _ = read_bat("Start-LapForge.bat")
    assert "%cmdcmdline%" in text and '"%~nx0"' in text


def test_start_launcher_messages_follow_the_plain_english_style():
    text, _ = read_bat("Start-LapForge.bat")
    assert "What to do" in text
    for word in ("Traceback", "ERRORLEVEL", "pip install -r"):
        assert word not in text


def test_build_script_builds_one_exe_through_app_run():
    text, low = read_bat("build_exe.bat")
    assert "--onefile --console" in text
    assert "--name LapForge" in text
    assert "from lapforge.app import run" in text and "sys.exit(run())" in text
    assert "dist\\LapForge.exe" in text
    assert "certutil -hashfile" in low and "sha256" in low
    assert '"dist\\LapForge.exe" --version' in text


def test_build_script_leaves_no_temporary_files():
    text, _ = read_bat("build_exe.bat")
    assert "del /q _entry.py" in text
    assert "rmdir /s /q build" in text
    assert "del /q LapForge.spec" in text
    assert text.count("call :cleanup") >= 3  # before, after the build and on failure


def test_the_old_exe_name_and_entry_are_gone():
    text, _ = read_bat("build_exe.bat")
    assert "from lapforge.cli import main" not in text
    assert "--name lapforge" not in text
