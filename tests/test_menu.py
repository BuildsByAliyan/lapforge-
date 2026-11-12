"""Tests for the interactive menu (Phase 29). No Windows and no real terminal needed."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from lapforge import cli, elevate
from lapforge.ui import menu
from lapforge.ui.menu import (
    ADMIN,
    BACK,
    BLANK,
    INVALID,
    ITEM,
    MAIN_MENU,
    QUIT,
    Choice,
    MenuItem,
    action_header_lines,
    admin_marker,
    command_text,
    find_item,
    footer_lines,
    invalid_hint,
    parse_choice,
    run_action,
    run_menu,
    summary_text,
    walk,
    windows_name,
)
from lapforge.ui.theme import LAPFORGE_THEME


def actions():
    return [item for item in walk() if not item.is_submenu]


def capture_console():
    buffer = io.StringIO()
    return Console(file=buffer, theme=LAPFORGE_THEME, highlight=False, width=110, color_system=None), buffer


class Script:
    """Feeds typed answers to the menu and records the prompts it showed."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def run(*answers, admin=False, can_restart=True, relaunch=None, runner=None):
    console, buffer = capture_console()
    ran = []
    script = Script(*answers)
    code = run_menu(
        console,
        runner=runner or (lambda argv: ran.append(argv) or 0),
        ask=script,
        admin=admin,
        can_restart=can_restart,
        relaunch=relaunch or (lambda argv: elevate.FAILED),
    )
    return code, ran, buffer.getvalue(), script


# --------------------------------------------------------------------------
# Menu tree integrity
# --------------------------------------------------------------------------
def test_keys_are_unique_and_not_empty():
    keys = [item.key for item in walk()]
    assert all(keys)
    assert len(keys) == len(set(keys))


def test_main_menu_matches_the_design():
    titles = [item.title for item in MAIN_MENU]
    assert titles == [
        "Smart Scan (recommended)",
        "My Laptop",
        "Clean and Speed Up",
        "Security and Network",
        "Repair Windows",
        "Reports and Settings",
    ]
    sub = {item.title: [c.title for c in item.children] for item in MAIN_MENU if item.is_submenu}
    assert sub["My Laptop"] == ["System details", "Battery and drives", "Upgrade advisor"]
    assert sub["Clean and Speed Up"] == ["Clean junk files", "Startup programs", "Find duplicate files", "Live monitor"]
    assert sub["Security and Network"] == ["Security status", "Internet speed test", "Saved Wi-Fi networks"]
    assert sub["Repair Windows"] == ["Check system files", "Repair system files"]
    assert sub["Reports and Settings"] == ["Save a report", "Settings", "Check for updates"]


def test_every_item_is_an_action_or_a_submenu_never_both():
    for item in walk():
        assert item.title and item.description
        assert bool(item.command) != bool(item.children), item.key
        if item.is_submenu:
            assert not item.needs_admin


def test_menus_are_at_most_two_levels_deep():
    for item in MAIN_MENU:
        for child in item.children:
            assert not child.is_submenu


def test_every_action_command_parses_with_the_real_parser():
    parser = cli.build_parser()
    for item in actions():
        args = parser.parse_args(list(item.command))
        assert args.command == item.command[0], item.key


def test_no_menu_command_deletes_or_reveals_without_asking():
    for item in actions():
        words = set(item.command)
        assert not words & {"--yes", "-y", "--reveal", "--apply", "--disable", "--enable"}, item.key


def test_admin_flags_agree_with_the_elevation_rules():
    parser = cli.build_parser()
    for item in actions():
        args = parser.parse_args(list(item.command))
        required = elevate.admin_reason(args.command, args)[0] == elevate.REQUIRED
        assert item.needs_admin is required, item.key


def test_every_action_is_reachable_and_findable():
    for item in walk():
        assert find_item(item.key) is item
    assert find_item("nope") is None
    assert len(actions()) == 16


def test_command_text_shows_the_typed_command():
    assert command_text(find_item("laptop.health")) == "lapforge health"
    assert command_text(find_item("repair.check")) == "lapforge repair --mode check --run"
    assert action_header_lines(find_item("scan")) == ["Smart Scan (recommended)", "Command: lapforge scan"]


# --------------------------------------------------------------------------
# Choice parsing
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1", Choice(ITEM, 0)),
        ("6", Choice(ITEM, 5)),
        (" 3 ", Choice(ITEM, 2)),
        ("03", Choice(ITEM, 2)),
        ("0", Choice(BACK)),
        ("b", Choice(BACK)),
        ("q", Choice(QUIT)),
        ("Q", Choice(QUIT)),
        ("exit", Choice(QUIT)),
        ("", Choice(BLANK)),
        ("   ", Choice(BLANK)),
        (None, Choice(BLANK)),
        ("7", Choice(INVALID)),
        ("-1", Choice(INVALID)),
        ("1.5", Choice(INVALID)),
        ("hello", Choice(INVALID)),
        ("99999999999999999999", Choice(INVALID)),
    ],
)
def test_parse_choice(raw, expected):
    assert parse_choice(raw, 6) == expected


def test_admin_choice_only_when_offered():
    assert parse_choice("a", 6, admin_offered=True) == Choice(ADMIN)
    assert parse_choice("A", 6, admin_offered=True) == Choice(ADMIN)
    assert parse_choice("a", 6, admin_offered=False) == Choice(INVALID)


def test_invalid_hint_is_short_and_plain():
    assert invalid_hint(6) == "Please type a number from 1 to 6, or 0 to exit."
    assert invalid_hint(3, at_root=False) == "Please type a number from 1 to 3, or 0 to go back."
    assert "A to restart as Administrator" in invalid_hint(6, admin_offered=True)


# --------------------------------------------------------------------------
# Admin marker and footer
# --------------------------------------------------------------------------
def test_admin_marker():
    assert admin_marker(find_item("repair.fix")) == "[Admin]"
    assert admin_marker(find_item("speedup.startup")) == "[Admin]"
    assert admin_marker(find_item("laptop.info")) == ""
    assert admin_marker(MenuItem("x", "X", "x", ("info",))) == ""


def test_footer_for_a_standard_user():
    assert footer_lines(False, True, True) == ["Running as: Standard user", "[A] Restart as Administrator", "[0] Exit"]
    assert footer_lines(False, True, False)[-1] == "[0] Back    [Q] Quit"


def test_footer_hides_restart_when_not_useful():
    assert footer_lines(True, True, True) == ["Running as: Administrator", "[0] Exit"]
    assert footer_lines(False, False, True) == ["Running as: Standard user", "[0] Exit"]


def test_summary_line():
    assert summary_text("Windows 11", 4, 8 * 1024**3) == "Windows 11  |  4 processor cores  |  8.0 GB memory"
    assert summary_text("Windows 10", 1, None) == "Windows 10  |  1 processor core"
    assert summary_text("", None, None) == ""


@pytest.mark.parametrize(
    "release, version, expected",
    [("10", "10.0.19045", "Windows 10"), ("10", "10.0.22631", "Windows 11"), ("11", "10.0.26100", "Windows 11"), ("8.1", "6.3.9600", "Windows 8.1"), ("10", "odd", "Windows 10")],
)
def test_windows_name(release, version, expected):
    assert windows_name(release, version) == expected


# --------------------------------------------------------------------------
# Running the menu
# --------------------------------------------------------------------------
def test_main_screen_shows_numbers_footer_and_admin_marks():
    code, ran, text, _ = run("0")
    assert code == 0 and ran == []
    for number, title in enumerate([i.title for i in MAIN_MENU], start=1):
        assert f"{number}  {title}" in text
    assert "MAIN MENU" in text
    assert "Running as: Standard user" in text
    assert "[A] Restart as Administrator" in text
    assert "[0] Exit" in text


def test_submenu_shows_admin_mark_and_back_footer():
    _, _, text, _ = run("5", "0", "0")
    assert "REPAIR WINDOWS" in text
    assert "Repair system files [Admin]" in text
    assert "[0] Back" in text


def test_choosing_an_action_runs_the_command_and_waits_for_enter():
    code, ran, text, script = run("2", "2", "", "0", "0")
    assert code == 0
    assert ran == [["health"]]
    assert "Command: lapforge health" in text
    assert any("Press Enter to return to the menu." in p for p in script.prompts)


def test_top_level_action_runs_directly():
    _, ran, text, _ = run("1", "", "0")
    assert ran == [["scan"]]
    assert "Command: lapforge scan" in text


def test_back_returns_to_the_main_menu_and_zero_there_exits():
    code, ran, text, _ = run("3", "0", "0")
    assert code == 0 and ran == []
    assert text.count("MAIN MENU") == 2


def test_q_quits_from_inside_a_submenu():
    code, ran, _, script = run("3", "q")
    assert code == 0 and ran == [] and script.answers == []


def test_invalid_and_blank_input_show_a_hint_and_ask_again():
    code, ran, text, script = run("9", "", "abc", "0")
    assert code == 0 and ran == []
    assert text.count("Please type a number from 1 to 6, or 0 to exit") >= 3
    assert len(script.prompts) == 4


def test_a_in_a_submenu_is_checked_against_that_menu():
    _, _, text, _ = run("5", "3", "0", "0")  # only two items in Repair Windows
    assert "Please type a number from 1 to 2" in text


def test_closed_input_and_ctrl_c_leave_cleanly():
    assert run()[0] == 0  # input closed at once

    def interrupt(prompt):
        raise KeyboardInterrupt

    console, _ = capture_console()
    assert run_menu(console, runner=lambda argv: 0, ask=interrupt, admin=False, can_restart=False) == 0


def test_closed_input_after_an_action_leaves_cleanly():
    code, ran, _, _ = run("1")  # runs Smart Scan, then input ends at "Press Enter"
    assert code == 0 and ran == [["scan"]]


def test_a_failing_action_shows_a_calm_error_and_the_menu_continues():
    def broken(argv):
        raise RuntimeError("internal detail")

    code, _, text, _ = run("1", "", "0", runner=broken)
    assert code == 0
    assert "Something went wrong" in text
    assert "RuntimeError" not in text
    assert "MAIN MENU" in text


def test_argparse_exit_does_not_close_the_menu():
    def bad_command(argv):
        raise SystemExit(2)

    code, _, text, _ = run("1", "", "0", runner=bad_command)
    assert code == 0
    assert "could not be started" in text


def test_a_command_that_ends_with_exit_zero_is_not_an_error():
    def help_like(argv):
        raise SystemExit(0)

    _, _, text, _ = run("1", "", "0", runner=help_like)
    assert "could not be started" not in text


def test_ctrl_c_inside_an_action_returns_to_the_menu():
    def interrupted(argv):
        raise KeyboardInterrupt

    code, _, text, _ = run("1", "", "0", runner=interrupted)
    assert code == 0 and "Cancelled." in text


def test_run_action_returns_the_exit_code():
    console, _ = capture_console()
    assert run_action(console, find_item("scan"), lambda argv: 7) == 7
    assert run_action(console, find_item("scan"), lambda argv: (_ for _ in ()).throw(ValueError("x"))) is None


# --------------------------------------------------------------------------
# Restart as Administrator
# --------------------------------------------------------------------------
def test_a_restarts_as_administrator_with_the_menu_command():
    seen = []

    def relaunch(argv):
        seen.append(list(argv))
        return elevate.STARTED

    code, ran, text, script = run("a", relaunch=relaunch)
    assert code == 0 and ran == []
    assert seen == [["menu"]]
    assert "new Administrator window" in text
    assert script.answers == []


def test_declined_or_failed_restart_keeps_the_menu_open():
    code, _, text, _ = run("a", "0", relaunch=lambda argv: elevate.CANCELLED)
    assert code == 0 and "permission was not given" in text
    code, _, text, _ = run("a", "0", relaunch=lambda argv: elevate.FAILED)
    assert code == 0 and "could not restart as Administrator" in text


def test_a_is_not_offered_to_an_administrator_or_where_restart_is_impossible():
    def never(argv):
        raise AssertionError("must not restart")

    _, _, text, _ = run("a", "0", admin=True, relaunch=never)
    assert "[A] Restart" not in text and "Running as: Administrator" in text
    assert "Please type a number" in text
    _, _, text, _ = run("a", "0", can_restart=False, relaunch=never)
    assert "[A] Restart" not in text


# --------------------------------------------------------------------------
# `lapforge`, `lapforge menu`, `lapforge dashboard`
# --------------------------------------------------------------------------
class Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def set_terminal(monkeypatch, interactive):
    monkeypatch.setattr("sys.stdin", Stream(interactive))
    monkeypatch.setattr("sys.stdout", Stream(interactive))


@pytest.fixture
def screens(monkeypatch):
    shown = []
    monkeypatch.setattr(cli, "cmd_menu", lambda: shown.append("menu") or 0)
    monkeypatch.setattr(cli, "cmd_dashboard", lambda: shown.append("dashboard") or 0)
    return shown


def test_parser_knows_menu_and_dashboard():
    parser = cli.build_parser()
    assert parser.parse_args(["menu"]).command == "menu"
    assert parser.parse_args(["dashboard"]).command == "dashboard"
    assert parser.parse_args([]).command is None


def test_plain_lapforge_opens_the_menu_in_a_terminal(monkeypatch, screens):
    set_terminal(monkeypatch, True)
    cli._dispatch(cli.build_parser().parse_args([]))
    assert screens == ["menu"]


@pytest.mark.parametrize("stdin_tty, stdout_tty", [(False, False), (True, False), (False, True)])
def test_plain_lapforge_shows_the_dashboard_when_not_interactive(monkeypatch, screens, stdin_tty, stdout_tty):
    monkeypatch.setattr("sys.stdin", Stream(stdin_tty))
    monkeypatch.setattr("sys.stdout", Stream(stdout_tty))
    cli._dispatch(cli.build_parser().parse_args([]))
    assert screens == ["dashboard"]


@pytest.mark.parametrize("interactive", [True, False])
def test_menu_and_dashboard_commands_always_show_their_own_screen(monkeypatch, screens, interactive):
    set_terminal(monkeypatch, interactive)
    cli._dispatch(cli.build_parser().parse_args(["menu"]))
    cli._dispatch(cli.build_parser().parse_args(["dashboard"]))
    assert screens == ["menu", "dashboard"]


def test_cmd_menu_runs_the_menu_with_the_cli_console(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "run_menu", lambda console: seen.append(console) or 0)
    assert cli.cmd_menu() == 0 and len(seen) == 1


def test_default_runner_uses_the_real_main(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "main", lambda argv: calls.append(argv) or 3)
    assert menu.default_runner(["health"]) == 3
    assert calls == [["health"]]


# --------------------------------------------------------------------------
# Clearing the screen (Phase 33b: the classic Windows console kept old text on screen)
# --------------------------------------------------------------------------
def terminal_console():
    buffer = io.StringIO()
    console = Console(file=buffer, theme=LAPFORGE_THEME, highlight=False, width=100, force_terminal=True)
    return console, buffer


def test_windows_clears_the_screen_with_cls(monkeypatch):
    commands = []
    monkeypatch.setattr(menu, "_is_windows", lambda: True)
    monkeypatch.setattr(menu.os, "system", lambda command: commands.append(command) or 0)
    console, buffer = terminal_console()
    menu.clear_screen(console)
    assert commands == ["cls"]
    assert "\x1b[2J" not in buffer.getvalue()


def test_other_systems_use_the_terminal_clear_code(monkeypatch):
    commands = []
    monkeypatch.setattr(menu, "_is_windows", lambda: False)
    monkeypatch.setattr(menu.os, "system", lambda command: commands.append(command) or 0)
    console, buffer = terminal_console()
    menu.clear_screen(console)
    assert commands == []
    assert "\x1b[2J" in buffer.getvalue()


def test_output_that_is_not_a_terminal_is_never_cleared(monkeypatch):
    commands = []
    monkeypatch.setattr(menu, "_is_windows", lambda: True)
    monkeypatch.setattr(menu.os, "system", lambda command: commands.append(command) or 0)
    console, buffer = capture_console()
    menu.clear_screen(console)
    assert commands == [] and buffer.getvalue() == ""


def test_a_failing_cls_falls_back_to_the_terminal_clear_code(monkeypatch):
    def broken(command):
        raise OSError("no shell")

    monkeypatch.setattr(menu, "_is_windows", lambda: True)
    monkeypatch.setattr(menu.os, "system", broken)
    console, buffer = terminal_console()
    menu.clear_screen(console)
    assert "\x1b[2J" in buffer.getvalue()


def test_every_menu_screen_clears_first(monkeypatch):
    cleared = []
    monkeypatch.setattr(menu, "clear_screen", lambda console: cleared.append(1))
    run("3", "0", "0")
    assert len(cleared) == 3  # main menu, submenu, main menu again
