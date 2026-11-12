"""Tests for command discoverability (Phase 30): cheat sheet, typo help and the tip."""

from __future__ import annotations

import argparse
import io

import pytest
from rich.console import Console

from lapforge import cli, config
from lapforge.ui.help_view import (
    CHEAT_SHEET,
    REMEMBER,
    TIP_COMMANDS,
    TIP_TEXT,
    listed_commands,
    render_commands,
    reset_tip,
    should_show_tip,
    suggest_command,
    take_tip,
    unknown_command_message,
)
from lapforge.ui.theme import LAPFORGE_THEME


def parser_commands():
    """(all names including aliases, names that are real commands) from the real argparse parser."""
    parser = cli.build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    names = list(action.choices)
    primary = []
    seen = set()
    for name, sub in action.choices.items():
        if id(sub) not in seen:  # an alias points to the same parser as its main name
            seen.add(id(sub))
            primary.append(name)
    return names, primary


def screen_text(width=110):
    buffer = io.StringIO()
    console = Console(file=buffer, theme=LAPFORGE_THEME, highlight=False, width=width, color_system=None)
    render_commands(console)
    return buffer.getvalue()


class Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture(autouse=True)
def fresh_tip():
    reset_tip()
    yield
    reset_tip()


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPFORGE_CONFIG_DIR", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------
# suggest_command
# --------------------------------------------------------------------------
COMMANDS = ["info", "health", "clean", "scan", "report", "startup", "security", "speed"]


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("helth", "health"),
        ("heatlh", "health"),
        ("clen", "clean"),
        ("scna", "scan"),
        ("reprot", "report"),
        ("startp", "startup"),
        ("HEALTH", "health"),
        (" info ", "info"),
        ("secuirty", "security"),
    ],
)
def test_close_words_get_a_suggestion(typed, expected):
    assert suggest_command(typed, COMMANDS) == expected


@pytest.mark.parametrize("typed", ["xyzzy", "banana", "qqqqqq", "zzz"])
def test_far_words_get_no_suggestion(typed):
    assert suggest_command(typed, COMMANDS) is None


@pytest.mark.parametrize("typed", ["", "   ", None])
def test_empty_input_gets_no_suggestion(typed):
    assert suggest_command(typed, COMMANDS) is None


def test_no_commands_means_no_suggestion():
    assert suggest_command("health", []) is None


def test_suggestion_message_text():
    assert unknown_command_message("helth", "health") == "Unknown command 'helth'. Did you mean 'health'?"
    assert unknown_command_message("xyzzy", None) == "Unknown command 'xyzzy'."


def test_suggestions_work_with_the_real_command_list():
    names, _primary = parser_commands()
    assert suggest_command("helth", names) == "health"
    assert suggest_command("chek", names) == "check"
    assert suggest_command("comands", names) == "commands"


# --------------------------------------------------------------------------
# The cheat sheet
# --------------------------------------------------------------------------
def test_cheat_sheet_lists_every_real_command():
    _names, primary = parser_commands()
    missing = set(primary) - listed_commands()
    assert not missing, f"commands missing from the cheat sheet: {sorted(missing)}"


def test_cheat_sheet_lists_nothing_that_does_not_exist():
    names, _primary = parser_commands()
    unknown = listed_commands() - set(names)
    assert not unknown, f"cheat sheet lists commands that do not exist: {sorted(unknown)}"


def test_aliases_are_mentioned_on_the_sheet():
    text = screen_text()
    assert "(also: check)" in text and "(also: help)" in text


def test_groups_and_order_follow_the_design():
    groups = {name: [c for c, _s in rows] for name, rows in CHEAT_SHEET}
    assert list(groups) == ["LOOK", "FIX", "PROTECT", "NETWORK", "EXPORT", "TOOLS"]
    assert {"info", "health", "scan"} <= set(groups["LOOK"])
    assert {"clean", "startup", "duplicates", "repair"} <= set(groups["FIX"])
    assert set(groups["PROTECT"]) == {"security", "wifi"}
    assert groups["NETWORK"] == ["speed"]
    assert groups["EXPORT"] == ["report"]
    assert {"live", "upgrade", "apps", "drivers", "config", "update", "menu"} <= set(groups["TOOLS"])


def test_every_line_has_one_plain_sentence():
    seen = []
    for _group, rows in CHEAT_SHEET:
        for command, sentence in rows:
            seen.append(command)
            assert sentence.endswith("."), command
            assert len(sentence) > 15, command
    assert len(seen) == len(set(seen))  # no command is listed twice


def test_screen_shows_the_five_commands_to_remember_and_fits_one_screen():
    text = screen_text()
    assert "The 5 commands to remember: scan, info, health, clean, report" in text
    assert REMEMBER == ("scan", "info", "health", "clean", "report")
    assert set(REMEMBER) <= listed_commands()
    for _group, rows in CHEAT_SHEET:
        for command, _sentence in rows:
            assert f"lapforge {command}" in text
    assert len(text.splitlines()) <= 40


def test_screen_has_no_python_jargon():
    text = screen_text()
    for word in ("Traceback", "argparse", "Exception"):
        assert word not in text


# --------------------------------------------------------------------------
# Unknown commands (through the real main())
# --------------------------------------------------------------------------
def test_typo_prints_a_suggestion_and_exits_with_code_2(capsys):
    assert cli.main(["helth"]) == 2
    err = capsys.readouterr().err
    assert "Unknown command 'helth'. Did you mean 'health'?" in err
    assert "lapforge commands" in err


def test_far_word_prints_no_suggestion(capsys):
    assert cli.main(["xyzzy"]) == 2
    err = capsys.readouterr().err
    assert "Unknown command 'xyzzy'." in err and "Did you mean" not in err


def test_typo_is_found_even_with_global_flags_around_it(capsys):
    assert cli.main(["--debug", "helth", "--admin"]) == 2
    assert "Did you mean 'health'?" in capsys.readouterr().err


def test_a_real_command_is_not_reported_as_unknown(monkeypatch):
    monkeypatch.setattr(cli, "_dispatch", lambda args: 0)
    monkeypatch.setattr(cli, "_handle_elevation", lambda *a, **k: None)
    assert cli.main(["health"]) == 0


# --------------------------------------------------------------------------
# `lapforge commands` / `lapforge help`
# --------------------------------------------------------------------------
def test_parser_knows_commands_and_the_help_alias():
    parser = cli.build_parser()
    assert parser.parse_args(["commands"]).command == "commands"
    assert parser.parse_args(["help"]).command == "help"
    assert parser.parse_args(["help", "clean"]).topic == "clean"


@pytest.mark.parametrize("word", ["commands", "help"])
def test_dispatch_shows_the_cheat_sheet(monkeypatch, word):
    shown = []
    monkeypatch.setattr(cli, "render_commands", lambda console: shown.append("sheet"))
    assert cli._dispatch(cli.build_parser().parse_args([word])) == 0
    assert shown == ["sheet"]


def test_help_with_a_command_shows_that_commands_options(capsys):
    assert cli.main(["help", "clean"]) == 0
    out = capsys.readouterr().out
    assert "usage: lapforge clean" in out and "--dry-run" in out


def test_help_with_an_unknown_command_suggests(capsys):
    assert cli.main(["help", "helth"]) == 2
    assert "Did you mean 'health'?" in capsys.readouterr().err


def test_main_help_has_examples_and_the_pointer():
    text = cli.build_parser().format_help()
    assert "Examples:" in text and "lapforge scan" in text and "lapforge clean --dry-run" in text
    assert "lapforge commands" in text
    assert "open the menu" in text.lower()


# --------------------------------------------------------------------------
# The tip
# --------------------------------------------------------------------------
def test_tip_text():
    assert TIP_TEXT == "Tip: run 'lapforge scan' for a full check."


@pytest.mark.parametrize("command", sorted(TIP_COMMANDS))
def test_tip_is_for_common_commands(command):
    assert should_show_tip(command)


@pytest.mark.parametrize("command", ["scan", "check", "menu", "commands", "report", "config", "live", None])
def test_tip_is_not_for_other_commands(command):
    assert not should_show_tip(command)


def test_tip_decision_respects_the_switch_and_the_once_rule():
    assert not should_show_tip("info", enabled=False)
    assert not should_show_tip("info", already_shown=True)


def test_tip_is_shown_at_most_once_per_run():
    assert take_tip("info") == TIP_TEXT
    assert take_tip("health") == ""
    assert take_tip("clean") == ""
    reset_tip()
    assert take_tip("health") == TIP_TEXT


def test_a_switched_off_tip_is_never_taken_and_stays_available():
    assert take_tip("info", enabled=False) == ""
    assert take_tip("info", enabled=True) == TIP_TEXT


def tip_env(monkeypatch, tty=True):
    monkeypatch.setattr("sys.stdin", Stream(tty))
    monkeypatch.setattr("sys.stdout", Stream(tty))
    printed = []
    console = Console(file=io.StringIO(), theme=LAPFORGE_THEME, highlight=False)
    monkeypatch.setattr(cli, "make_console", lambda: console)
    return console.file, printed


def test_cli_shows_the_tip_once_after_a_common_command(monkeypatch, config_dir):
    out, _ = tip_env(monkeypatch)
    cli._maybe_show_tip("info", 0)
    cli._maybe_show_tip("health", 0)
    assert out.getvalue().count("Tip: run 'lapforge scan'") == 1


def test_cli_shows_no_tip_after_a_failure_or_when_output_is_redirected(monkeypatch, config_dir):
    out, _ = tip_env(monkeypatch, tty=True)
    cli._maybe_show_tip("info", 1)
    assert out.getvalue() == ""
    out, _ = tip_env(monkeypatch, tty=False)
    cli._maybe_show_tip("info", 0)
    assert out.getvalue() == ""


def test_cli_shows_no_tip_for_scan(monkeypatch, config_dir):
    out, _ = tip_env(monkeypatch)
    cli._maybe_show_tip("scan", 0)
    assert out.getvalue() == ""


def test_tip_can_be_turned_off_with_the_config_key(monkeypatch, config_dir):
    out, _ = tip_env(monkeypatch)
    assert config.set_setting("show_tips", "off")["ok"]
    cli._maybe_show_tip("info", 0)
    assert out.getvalue() == ""
    assert config.set_setting("show_tips", "on")["ok"]
    cli._maybe_show_tip("info", 0)
    assert "Tip: run 'lapforge scan'" in out.getvalue()


def test_main_shows_the_tip_after_a_successful_common_command(monkeypatch, config_dir):
    out, _ = tip_env(monkeypatch)
    monkeypatch.setattr(cli, "_dispatch", lambda args: 0)
    monkeypatch.setattr(cli, "_handle_elevation", lambda *a, **k: None)
    assert cli.main(["info"]) == 0
    assert "Tip: run 'lapforge scan'" in out.getvalue()


# --------------------------------------------------------------------------
# The show_tips setting
# --------------------------------------------------------------------------
def test_show_tips_setting_defaults_to_on_and_accepts_on_off(config_dir):
    assert config.DEFAULTS["show_tips"] is True
    assert config.load_config()["show_tips"] is True
    assert config.validate_value("show_tips", "off") == (False, "")
    assert config.validate_value("show_tips", "yes") == (True, "")
    value, error = config.validate_value("show_tips", "maybe")
    assert value is None and "show_tips" in error and "on or off" in error


def test_show_tips_is_saved_and_listed(config_dir):
    assert config.set_setting("show_tips", "off")["ok"]
    assert config.load_config()["show_tips"] is False
    rows = {row["key"]: row for row in config.describe_config()}
    assert rows["show_tips"]["value"] == "off" and rows["show_tips"]["default"] == "on"
    assert rows["show_tips"]["changed"] is True
