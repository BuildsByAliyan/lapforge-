"""Help for people who type: a one-screen cheat sheet, "Did you mean ...?" and a gentle tip.

The cheat sheet is plain data (`CHEAT_SHEET`). A test compares it with the real list of
commands, so it cannot silently go out of date when a command is added.
"""

from __future__ import annotations

import difflib
from typing import Iterable

from rich.console import Console
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

# Group -> (command as typed after "lapforge", one plain sentence).
CHEAT_SHEET: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "LOOK",
        (
            ("scan", "Check everything at once and see what to do next (also: check)."),
            ("info", "Show every detail of this laptop."),
            ("health", "Show the Health score and how to fix each problem."),
        ),
    ),
    (
        "FIX",
        (
            ("clean", "Remove junk files. Shows a Preview and asks first."),
            ("startup", "Choose which programs start with Windows."),
            ("duplicates", "Find copies of the same file. Never deletes anything."),
            ("repair", "Check and repair Windows system files."),
        ),
    ),
    (
        "PROTECT",
        (
            ("security", "Show Windows Security, firewall and encryption status."),
            ("wifi", "List the Wi-Fi networks saved on this PC."),
        ),
    ),
    (
        "NETWORK",
        (("speed", "Test how fast and steady the internet connection is."),),
    ),
    (
        "EXPORT",
        (("report", "Save your laptop details as a file you can keep or share."),),
    ),
    (
        "TOOLS",
        (
            ("live", "Watch what is using the processor, memory and disk right now."),
            ("upgrade", "Find out if more memory (RAM) or a faster drive would help."),
            ("apps", "List the installed apps."),
            ("drivers", "List drivers, oldest first."),
            ("config", "Show or change LapForge settings."),
            ("update", "Check if a newer version of LapForge exists."),
            ("menu", "Open the menu. No commands to remember."),
            ("dashboard", "Show the one-screen summary of this laptop."),
            ("commands", "Show this list (also: help)."),
        ),
    ),
)

REMEMBER = ("scan", "info", "health", "clean", "report")

TIP_TEXT = "Tip: run 'lapforge scan' for a full check."
# Commands after which the tip makes sense (the single commands people try first).
TIP_COMMANDS = frozenset({"info", "health", "clean", "startup", "apps", "drivers", "security", "upgrade"})

# The tip is shown at most once in each run of LapForge.
_tip_state = {"shown": False}


def listed_commands() -> set[str]:
    """The names of all commands that appear on the cheat sheet."""
    return {command for _group, rows in CHEAT_SHEET for command, _sentence in rows}


# --------------------------------------------------------------------------
# Typos (pure)
# --------------------------------------------------------------------------
def suggest_command(word: str | None, commands: Iterable[str], cutoff: float = 0.6) -> str | None:
    """The command that looks most like `word`, or None when nothing is close. Pure.

    suggest_command("helth", ["health", "info"]) -> "health"
    """
    typed = (word or "").strip().lower()
    if not typed:
        return None
    matches = difflib.get_close_matches(typed, [c.lower() for c in commands], n=1, cutoff=cutoff)
    return matches[0] if matches else None


def unknown_command_message(word: str, suggestion: str | None) -> str:
    """'Unknown command 'helth'. Did you mean 'health'?' - or where to look when nothing is close."""
    if suggestion:
        return f"Unknown command '{word}'. Did you mean '{suggestion}'?"
    return f"Unknown command '{word}'."


# --------------------------------------------------------------------------
# The tip (pure decision + a tiny piece of state)
# --------------------------------------------------------------------------
def should_show_tip(command: str | None, enabled: bool = True, already_shown: bool = False) -> bool:
    """True for a common command, when tips are on and none was shown yet. Pure."""
    return bool(enabled) and not already_shown and command in TIP_COMMANDS


def take_tip(command: str | None, enabled: bool = True) -> str:
    """The tip text to print now, or '' (and remembers that it was shown, so it is never repeated)."""
    if should_show_tip(command, enabled, _tip_state["shown"]):
        _tip_state["shown"] = True
        return TIP_TEXT
    return ""


def reset_tip() -> None:
    """Forget that the tip was shown (used by tests)."""
    _tip_state["shown"] = False


# --------------------------------------------------------------------------
# The cheat sheet screen
# --------------------------------------------------------------------------
def cheat_sheet_table() -> Table:
    """Grouped table: command and one plain sentence per line."""
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column()
    for index, (group, rows) in enumerate(CHEAT_SHEET):
        if index:
            table.add_row("", "", "")
        for position, (command, sentence) in enumerate(rows):
            table.add_row(
                Text(group if position == 0 else "", style="title"),
                Text(f"lapforge {command}", style="accent"),
                Text(sentence, style="muted"),
            )
    return table


def render_commands(console: Console) -> None:
    """Print the cheat sheet: the 5 commands to remember, then every command by group."""
    console.print()
    console.print(Text("  LapForge commands", style="title"))
    line = Text("  The 5 commands to remember: ", style="label")
    for number, command in enumerate(REMEMBER):
        if number:
            line.append(", ", style="muted")
        line.append(command, style="accent")
    console.print(line)
    console.print()
    console.print(Padding(cheat_sheet_table(), (0, 0, 0, 2)))
    console.print()
    console.print("  [muted]More about one command, for example:[/muted] [accent]lapforge help clean[/accent]")
    console.print("  [muted]Not sure where to start? Run[/muted] [accent]lapforge[/accent] [muted]to open the menu.[/muted]")
    console.print()


__all__ = [
    "CHEAT_SHEET",
    "REMEMBER",
    "TIP_COMMANDS",
    "TIP_TEXT",
    "cheat_sheet_table",
    "listed_commands",
    "render_commands",
    "reset_tip",
    "should_show_tip",
    "suggest_command",
    "take_tip",
    "unknown_command_message",
]
