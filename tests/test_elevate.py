"""Tests for Administrator awareness and the one-click restart (Phase 26). No Windows needed."""

from __future__ import annotations

import argparse
import io
import subprocess

import pytest

from lapforge import cli, elevate
from lapforge.elevate import (
    ASK,
    CANCELLED,
    FAILED,
    NONE,
    RELAUNCH,
    STARTED,
    UNSUPPORTED,
    admin_reason,
    build_relaunch_command,
    decide_elevation,
    interpret_shell_result,
    needs_admin,
    owns_console,
    parse_yes_no,
)


# --------------------------------------------------------------------------
# needs_admin / admin_reason
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "command, args",
    [
        ("wifi", ["--reveal"]),
        ("repair", ["--run"]),
        ("repair", ["--mode", "repair", "--run"]),
        ("startup", ["--disable", "OneDrive", "--apply"]),
        ("startup", ["--undo"]),
        ("startup", ["--manage"]),
        ("clean", ["--full"]),
        ("clean", ["--full", "--dry-run"]),
        ("info", []),
        ("health", []),
    ],
)
def test_commands_that_need_administrator(command, args):
    assert needs_admin(command, args)


@pytest.mark.parametrize(
    "command, args",
    [
        ("wifi", []),
        ("repair", []),
        ("repair", ["--mode", "check"]),
        ("startup", []),
        ("startup", ["--disable", "OneDrive"]),  # preview only
        ("startup", ["--history"]),
        ("clean", []),
        ("clean", ["--browser", "--dry-run"]),
        ("duplicates", ["--path", "."]),
        ("report", []),
        ("speed", []),
        ("live", ["--once"]),
        (None, []),
    ],
)
def test_commands_that_do_not_need_administrator(command, args):
    assert not needs_admin(command, args)


def test_args_can_be_a_namespace_a_dict_or_a_list():
    parser = cli.build_parser()
    assert needs_admin("wifi", parser.parse_args(["wifi", "--reveal"]))
    assert not needs_admin("wifi", parser.parse_args(["wifi"]))
    assert needs_admin("clean", {"full": True})
    assert not needs_admin("clean", {"full": False})
    assert needs_admin("clean", ["--full"])
    assert not needs_admin("clean", None)


def test_startup_undo_namespace_value_counts():
    ns = cli.build_parser().parse_args(["startup", "--undo", "abc123", "--apply"])
    assert needs_admin("startup", ns)


def test_levels_and_plain_messages():
    level, text = admin_reason("wifi", ["--reveal"])
    assert level == elevate.REQUIRED and text.endswith(".")
    level, text = admin_reason("info", [])
    assert level == elevate.RECOMMENDED and "temperature" in text
    assert admin_reason("speed", []) == ("", "")


def test_running_as_text():
    assert elevate.running_as_text(True) == "Administrator"
    assert elevate.running_as_text(False) == "Standard user"


@pytest.mark.parametrize("text, expected", [("y", True), ("Y", True), (" yes ", True), ("", False), ("n", False), (None, False), ("maybe", False)])
def test_parse_yes_no_defaults_to_no(text, expected):
    assert parse_yes_no(text) is expected


# --------------------------------------------------------------------------
# build_relaunch_command
# --------------------------------------------------------------------------
def test_frozen_exe_is_started_directly_with_the_marker():
    program, params = build_relaunch_command(["clean", "--full"], True, r"C:\Tools\LapForge.exe")
    assert program == r"C:\Tools\LapForge.exe"
    assert params == "clean --full --elevated"


def test_source_run_uses_python_dash_m_lapforge():
    program, params = build_relaunch_command(["wifi", "--reveal"], False, r"C:\Python312\python.exe")
    assert program == r"C:\Python312\python.exe"
    assert params.startswith("-m lapforge ")
    assert params == "-m lapforge wifi --reveal --elevated"


def test_words_with_spaces_are_quoted():
    words = ["duplicates", "--path", r"C:\My Files\Old Photos"]
    _program, params = build_relaunch_command(words, True, "LapForge.exe")
    assert params == subprocess.list2cmdline(words + ["--elevated"])
    assert '"C:\\My Files\\Old Photos"' in params


def test_admin_flag_is_dropped_and_marker_is_not_doubled():
    _program, params = build_relaunch_command(["--admin", "info", "--elevated"], False, "python")
    assert params == "-m lapforge info --elevated"
    assert params.count("--elevated") == 1
    assert "--admin" not in params


def test_empty_command_still_gets_the_marker():
    assert build_relaunch_command([], True, "LapForge.exe") == ("LapForge.exe", "--elevated")


# --------------------------------------------------------------------------
# ShellExecute result and console ownership
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code, error, expected",
    [
        (42, 0, STARTED),
        (33, 0, STARTED),
        (5, 0, CANCELLED),
        (0, 1223, CANCELLED),
        (2, 0, FAILED),  # file not found
        (0, 0, FAILED),
        (None, 0, FAILED),
    ],
)
def test_interpret_shell_result(code, error, expected):
    assert interpret_shell_result(code, error) == expected


def test_console_is_ours_when_we_are_alone():
    assert owns_console([100], 100)
    assert owns_console([], 100)


def test_console_is_not_ours_when_a_shell_shares_it():
    assert not owns_console([100, 200], 100)
    assert not owns_console([100, 200], 100, parent_pid=200, parent_name="cmd.exe", my_name="LapForge.exe", frozen=True)


def test_single_file_exe_ignores_its_own_launcher_copy():
    assert owns_console([100, 99], 100, parent_pid=99, parent_name="LapForge.exe", my_name="lapforge.exe", frozen=True)
    assert not owns_console([100, 99], 100, parent_pid=99, parent_name="LapForge.exe", my_name="LapForge.exe", frozen=False)


def test_windows_calls_are_safe_elsewhere(monkeypatch):
    monkeypatch.setattr(elevate, "IS_WINDOWS", False)
    assert elevate.console_owned_by_us() is False
    assert elevate.relaunch_as_admin(["info"]) == FAILED
    assert elevate.elevation_supported() is False


def test_console_owned_by_us_uses_the_process_list(monkeypatch):
    monkeypatch.setattr(elevate, "IS_WINDOWS", True)
    monkeypatch.setattr(elevate.os, "getpid", lambda: 100)
    monkeypatch.setattr(elevate, "is_frozen", lambda: False)
    monkeypatch.setattr(elevate, "_console_process_ids", lambda: [100])
    assert elevate.console_owned_by_us() is True
    monkeypatch.setattr(elevate, "_console_process_ids", lambda: [100, 7])
    assert elevate.console_owned_by_us() is False
    monkeypatch.setattr(elevate, "_console_process_ids", lambda: [])
    assert elevate.console_owned_by_us() is False


def test_console_check_never_raises(monkeypatch):
    def boom():
        raise OSError("no console")

    monkeypatch.setattr(elevate, "IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "_console_process_ids", boom)
    assert elevate.console_owned_by_us() is False


@pytest.mark.parametrize(
    "result, expected",
    [((42, 0), STARTED), ((5, 0), CANCELLED), ((0, 1223), CANCELLED), ((2, 0), FAILED)],
)
def test_relaunch_as_admin_reports_the_outcome(monkeypatch, result, expected):
    seen = {}

    def fake(program, params, directory):
        seen.update(program=program, params=params, directory=directory)
        return result

    monkeypatch.setattr(elevate, "IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "is_frozen", lambda: False)
    monkeypatch.setattr(elevate, "_shell_execute_runas", fake)
    assert elevate.relaunch_as_admin(["clean", "--full"], directory="D:\\proj") == expected
    assert seen["params"] == "-m lapforge clean --full --elevated"
    assert seen["directory"] == "D:\\proj"


def test_relaunch_as_admin_never_raises(monkeypatch):
    def boom(*_a):
        raise OSError("no shell")

    monkeypatch.setattr(elevate, "IS_WINDOWS", True)
    monkeypatch.setattr(elevate, "_shell_execute_runas", boom)
    assert elevate.relaunch_as_admin(["info"]) == FAILED


def test_wait_for_enter_does_nothing_in_an_existing_terminal(monkeypatch):
    monkeypatch.setattr(elevate, "console_owned_by_us", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("must not wait")))
    elevate.wait_for_enter_if_own_console()


# --------------------------------------------------------------------------
# decide_elevation
# --------------------------------------------------------------------------
def _decide(command="wifi", args=("--reveal",), **kw):
    base = dict(admin=False, elevated=False, supported=True, forced=False, interactive=True)
    base.update(kw)
    return decide_elevation(command, list(args), **base)


def test_a_required_feature_asks_first():
    assert _decide() == ASK


def test_already_administrator_does_nothing():
    assert _decide(admin=True) == NONE
    assert _decide(admin=True, forced=True) == NONE


def test_restarted_copy_never_restarts_again():
    assert _decide(elevated=True) == NONE
    assert _decide(elevated=True, forced=True) == NONE


def test_admin_flag_restarts_without_asking():
    assert _decide(command="info", args=(), forced=True) == RELAUNCH
    assert _decide(command="speed", args=(), forced=True) == RELAUNCH


def test_admin_flag_on_a_system_that_cannot_restart():
    assert _decide(forced=True, supported=False) == UNSUPPORTED


def test_no_question_without_a_person_or_without_windows():
    assert _decide(interactive=False) == NONE
    assert _decide(supported=False) == NONE


def test_recommended_features_do_not_nag():
    assert _decide(command="info", args=()) == NONE
    assert _decide(command="health", args=()) == NONE
    assert _decide(command="clean", args=()) == NONE


# --------------------------------------------------------------------------
# In the command line tool
# --------------------------------------------------------------------------
class _Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def windows_user(monkeypatch, tmp_path):
    """A pretend Windows standard user in a real terminal. Records restarts and commands run."""
    monkeypatch.setenv("LAPFORGE_CONFIG_DIR", str(tmp_path / "config"))
    calls = {"relaunch": [], "ran": []}
    monkeypatch.setattr(cli, "is_admin", lambda: False)
    monkeypatch.setattr(cli.elevate, "elevation_supported", lambda: True)
    monkeypatch.setattr(cli.elevate, "relaunch_as_admin", lambda argv: calls["relaunch"].append(list(argv)) or STARTED)
    monkeypatch.setattr(cli, "_dispatch", lambda args: calls["ran"].append(args.command) or 0)
    monkeypatch.setattr(cli.elevate, "wait_for_enter_if_own_console", lambda *a, **k: calls.setdefault("waited", True))

    def answer(text: str):
        out = _Terminal()
        monkeypatch.setattr("sys.stdin", _Terminal(text))
        monkeypatch.setattr("sys.stdout", out)
        return out

    calls["answer"] = answer
    return calls


def test_saying_yes_restarts_with_the_same_command(windows_user):
    out = windows_user["answer"]("y\n")
    assert cli.main(["wifi", "--reveal"]) == 0
    text = out.getvalue()
    assert "This feature needs Administrator rights." in text
    assert "Restart LapForge as Administrator now?" in text
    assert "[y/N]" in text
    assert windows_user["relaunch"] == [["wifi", "--reveal"]]
    assert windows_user["ran"] == []


def test_the_default_answer_is_no_and_the_limited_version_runs(windows_user):
    out = windows_user["answer"]("\n")
    assert cli.main(["wifi", "--reveal"]) == 0
    assert windows_user["relaunch"] == []
    assert windows_user["ran"] == ["wifi"]
    assert "Saved Wi-Fi passwords will not be shown." in out.getvalue()


def test_end_of_input_counts_as_no(windows_user):
    windows_user["answer"]("")
    assert cli.main(["clean", "--full", "--dry-run"]) == 0
    assert windows_user["relaunch"] == []
    assert windows_user["ran"] == ["clean"]


def test_admin_flag_works_before_or_after_the_command(windows_user):
    windows_user["answer"]("")
    assert cli.main(["--admin", "info"]) == 0
    assert cli.main(["info", "--admin"]) == 0
    assert windows_user["relaunch"] == [["info"], ["info"]]
    assert windows_user["ran"] == []


def test_the_debug_flag_is_kept_for_the_restart(windows_user):
    windows_user["answer"]("")
    cli.main(["--debug", "--admin", "info"])
    assert windows_user["relaunch"] == [["--debug", "info"]]


def test_a_restarted_copy_does_not_restart_again(windows_user):
    windows_user["answer"]("")
    assert cli.main(["wifi", "--reveal", "--elevated"]) == 0
    assert windows_user["relaunch"] == []
    assert windows_user["ran"] == ["wifi"]
    assert windows_user.get("waited") is True  # the new window stays open until Enter


def test_a_declined_permission_prompt_continues_with_the_limited_version(windows_user, monkeypatch):
    out = windows_user["answer"]("")
    monkeypatch.setattr(cli.elevate, "relaunch_as_admin", lambda argv: CANCELLED)
    assert cli.main(["--admin", "info"]) == 0
    assert windows_user["ran"] == ["info"]
    assert "Administrator permission was not given" in out.getvalue()


def test_a_failed_restart_explains_what_to_do(windows_user, monkeypatch):
    out = windows_user["answer"]("")
    monkeypatch.setattr(cli.elevate, "relaunch_as_admin", lambda argv: FAILED)
    assert cli.main(["--admin", "info"]) == 0
    assert windows_user["ran"] == ["info"]
    assert "could not restart as Administrator" in out.getvalue()
    assert "What you can do" in out.getvalue()


def test_no_question_when_nobody_can_answer(windows_user, monkeypatch):
    out = io.StringIO()  # not a terminal
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdout", out)
    assert cli.main(["wifi", "--reveal"]) == 0
    assert "Restart LapForge" not in out.getvalue()
    assert windows_user["ran"] == ["wifi"]


def test_a_command_that_does_not_need_it_is_never_questioned(windows_user):
    out = windows_user["answer"]("y\n")
    assert cli.main(["speed"]) == 0
    assert "Administrator" not in out.getvalue()
    assert windows_user["relaunch"] == []


def test_admin_flag_off_windows_says_so_and_carries_on(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LAPFORGE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(cli, "is_admin", lambda: False)
    monkeypatch.setattr(cli.elevate, "elevation_supported", lambda: False)
    ran = []
    monkeypatch.setattr(cli, "_dispatch", lambda args: ran.append(args.command) or 0)
    assert cli.main(["--admin", "info"]) == 0
    assert "only available on Windows" in capsys.readouterr().out
    assert ran == ["info"]


def test_admin_flags_are_in_the_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "--admin" in out
    assert "--elevated" not in out  # internal marker stays hidden


def test_parser_accepts_the_new_flags():
    args = cli.build_parser().parse_args(["--admin", "info"])
    assert args.admin is True and args.command == "info"
    assert isinstance(args, argparse.Namespace)


def test_dashboard_shows_how_we_are_running(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LAPFORGE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(cli, "show_dashboard", lambda console: None)
    monkeypatch.setattr(cli, "check_for_update", lambda: None)
    monkeypatch.setattr(cli, "update_notice", lambda result: "")
    monkeypatch.setattr(cli, "is_admin", lambda: False)
    assert cli.main([]) == 0
    assert "Running as: Standard user" in capsys.readouterr().out
    monkeypatch.setattr(cli, "is_admin", lambda: True)
    assert cli.main([]) == 0
    assert "Running as: Administrator" in capsys.readouterr().out
