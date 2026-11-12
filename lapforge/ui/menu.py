"""The LapForge menu: press a number, get an answer. No commands to remember.

The menu is plain data (`MAIN_MENU`). Every action is a normal LapForge command that is run
in this same window through `main([...])`, so the menu and the typed commands always behave
the same way. Each action screen shows the equivalent command ("Command: lapforge health"),
so people slowly learn the commands if they want to.

Pure parts (easy to test without a terminal): `parse_choice`, `admin_marker`, `command_text`,
`footer_lines`, `summary_text`, `windows_name`, `find_item`, `walk`.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from typing import Callable, Iterator, NamedTuple, Sequence

from rich.console import Console
from rich.padding import Padding
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .. import elevate
from ..utils import format_bytes, is_admin
from .banner import print_banner
from .errors import show_error
from .theme import make_console


# --------------------------------------------------------------------------
# The menu as data
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class MenuItem:
    """One line of the menu. It is either an action (`command`) or a submenu (`children`).

    `command` is the list of words after "lapforge", for example ("health",).
    `needs_admin` marks features that cannot do their job without Administrator rights.
    """

    key: str
    title: str
    description: str
    command: tuple[str, ...] = ()
    needs_admin: bool = False
    children: tuple["MenuItem", ...] = ()

    @property
    def is_submenu(self) -> bool:
        return bool(self.children)


MAIN_MENU: tuple[MenuItem, ...] = (
    MenuItem("scan", "Smart Scan (recommended)", "Checks everything and tells you what to do next.", ("scan",)),
    MenuItem(
        "laptop",
        "My Laptop",
        "Details, battery, drives and upgrade options.",
        children=(
            MenuItem("laptop.info", "System details", "Model, processor, memory, drives and Windows.", ("info",)),
            MenuItem(
                "laptop.health",
                "Battery and drives",
                "Health score, battery wear, drive health and temperature.",
                ("health",),
            ),
            MenuItem(
                "laptop.upgrade",
                "Upgrade advisor",
                "Can this laptop take more memory (RAM) or a faster drive?",
                ("upgrade",),
            ),
        ),
    ),
    MenuItem(
        "speedup",
        "Clean and Speed Up",
        "Remove junk files and slow-starting programs.",
        children=(
            MenuItem(
                "speedup.clean",
                "Clean junk files",
                "Shows a Preview first and asks before removing anything.",
                ("clean",),
            ),
            MenuItem(
                "speedup.startup",
                "Startup programs",
                "Choose which programs start with Windows. Every change can be undone.",
                ("startup", "--manage"),
                needs_admin=True,
            ),
            MenuItem(
                "speedup.duplicates",
                "Find duplicate files",
                "Looks for copies of the same file in Downloads. Never deletes anything.",
                ("duplicates",),
            ),
            MenuItem("speedup.live", "Live monitor", "Watch what is using the processor, memory and disk.", ("live",)),
        ),
    ),
    MenuItem(
        "security",
        "Security and Network",
        "Windows Security, internet quality and Wi-Fi.",
        children=(
            MenuItem("security.status", "Security status", "Antivirus, firewall, encryption and sign-in protection.", ("security",)),
            MenuItem("security.speed", "Internet speed test", "Checks how fast and steady the connection is.", ("speed",)),
            MenuItem("security.wifi", "Saved Wi-Fi networks", "Lists the networks this PC knows. Passwords stay hidden.", ("wifi",)),
        ),
    ),
    MenuItem(
        "repair",
        "Repair Windows",
        "Check and repair Windows system files.",
        children=(
            MenuItem(
                "repair.check",
                "Check system files",
                "Looks for damaged Windows files. Changes nothing.",
                ("repair", "--mode", "check", "--run"),
                needs_admin=True,
            ),
            MenuItem(
                "repair.fix",
                "Repair system files",
                "Repairs damaged Windows files. Can take 20 minutes or more.",
                ("repair", "--mode", "repair", "--run"),
                needs_admin=True,
            ),
        ),
    ),
    MenuItem(
        "reports",
        "Reports and Settings",
        "Save a report, change settings, look for updates.",
        children=(
            MenuItem("reports.save", "Save a report", "Saves your laptop details as a file you can keep or share.", ("report",)),
            MenuItem("reports.settings", "Settings", "Shows the LapForge settings.", ("config",)),
            MenuItem("reports.update", "Check for updates", "Looks for a newer version of LapForge.", ("update",)),
        ),
    ),
)


def walk(items: Sequence[MenuItem] = MAIN_MENU) -> Iterator[MenuItem]:
    """Every item of the tree, parents before their children."""
    for item in items:
        yield item
        yield from walk(item.children)


def find_item(key: str, items: Sequence[MenuItem] = MAIN_MENU) -> MenuItem | None:
    """The item with this key, or None."""
    for item in walk(items):
        if item.key == key:
            return item
    return None


def command_text(item: MenuItem) -> str:
    """'lapforge health' - the typed command that does the same thing."""
    return " ".join(("lapforge",) + tuple(item.command))


def admin_marker(item: MenuItem) -> str:
    """'[Admin]' for items that need Administrator rights, otherwise ''."""
    return "[Admin]" if item.needs_admin else ""


# --------------------------------------------------------------------------
# Choosing (pure)
# --------------------------------------------------------------------------
ITEM = "item"  # a numbered item was chosen (`index` is its position, starting at 0)
ADMIN = "admin"  # restart as Administrator
BACK = "back"  # one step back (leave the menu at the top level)
QUIT = "quit"  # leave LapForge from anywhere
BLANK = "blank"  # nothing was typed
INVALID = "invalid"  # something that is not on the screen


class Choice(NamedTuple):
    kind: str
    index: int | None = None


def parse_choice(raw: str | None, count: int, admin_offered: bool = False) -> Choice:
    """Turn what the person typed into a Choice. Pure.

    `count` is how many numbered items are on the screen. Numbers 1..count pick an item,
    "0" (or "b") goes back, "q" quits, "a" restarts as Administrator when it is offered.
    Anything else is INVALID and an empty answer is BLANK.
    """
    text = (raw or "").strip().lower()
    if not text:
        return Choice(BLANK)
    if text in ("0", "b", "back"):
        return Choice(BACK)
    if text in ("q", "quit", "exit"):
        return Choice(QUIT)
    if text == "a":
        return Choice(ADMIN) if admin_offered else Choice(INVALID)
    if text.isdigit():
        number = int(text)
        if 1 <= number <= count:
            return Choice(ITEM, number - 1)
    return Choice(INVALID)


def invalid_hint(count: int, admin_offered: bool = False, at_root: bool = True) -> str:
    """A short sentence that says what can be typed."""
    exit_word = "exit" if at_root else "go back"
    hint = f"Please type a number from 1 to {count}, or 0 to {exit_word}"
    if admin_offered:
        hint += ", or A to restart as Administrator"
    return hint + "."


# --------------------------------------------------------------------------
# Text on the screen (pure)
# --------------------------------------------------------------------------
def footer_lines(admin: bool, can_restart: bool, at_root: bool) -> list[str]:
    """Lines shown under every menu, in this order: who is running, [A], [0]."""
    lines = [f"Running as: {elevate.running_as_text(admin)}"]
    if not admin and can_restart:
        lines.append("[A] Restart as Administrator")
    lines.append("[0] Exit" if at_root else "[0] Back    [Q] Quit")
    return lines


def windows_name(release: str, version: str) -> str:
    """'Windows 11' or 'Windows 10' from platform.release() and platform.version(). Pure.

    Windows 11 still reports release '10'; its build number (the last part of the version) is 22000 or more.
    """
    try:
        build = int(str(version).split(".")[-1])
    except ValueError:
        build = 0
    if str(release) in ("10", "11"):
        return "Windows 11" if build >= 22000 else "Windows 10"
    return f"Windows {release}".strip()


def summary_text(os_name: str, cores: int | None, ram_bytes: int | None) -> str:
    """One line such as 'Windows 11  |  4 processor cores  |  8.0 GB memory'. Unknown parts are left out."""
    parts = [os_name] if os_name else []
    if cores:
        parts.append(f"{cores} processor core" + ("" if cores == 1 else "s"))
    if ram_bytes:
        parts.append(f"{format_bytes(ram_bytes)} memory")
    return "  |  ".join(parts)


def system_summary() -> str:
    """One-line summary of this PC. Quick (no slow Windows queries); '' if nothing can be read."""
    try:
        system = platform.system()
        name = windows_name(platform.release(), platform.version()) if system == "Windows" else (system or "")
    except Exception:
        name = ""
    cores = ram = None
    try:
        import psutil

        cores = psutil.cpu_count(logical=True)
        ram = int(psutil.virtual_memory().total)
    except Exception:
        pass
    return summary_text(name, cores, ram)


def action_header_lines(item: MenuItem) -> list[str]:
    """What the screen of an action shows before it starts: its title and the typed command."""
    return [item.title, f"Command: {command_text(item)}"]


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------
def menu_table(items: Sequence[MenuItem]) -> Table:
    """Numbered list: number, title (with the [Admin] mark) and a short description."""
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", no_wrap=True)
    table.add_column()
    table.add_column()
    for number, item in enumerate(items, start=1):
        title = Text(item.title, style="accent" if item.key == "scan" else "value")
        marker = admin_marker(item)
        if marker:
            title.append(f" {marker}", style="warn")
        if item.is_submenu:
            title.append("  >", style="muted")
        table.add_row(Text(str(number), style="brand"), title, Text(item.description, style="muted"))
    return table


def _is_windows() -> bool:
    return sys.platform == "win32"


def clear_screen(console: Console) -> None:
    """Wipe the terminal so a new menu is not drawn on top of old text.

    On Windows the `cls` command is used: it empties the whole window in every kind of console,
    while the ANSI clear code only moved the cursor to the top in the classic Command Prompt.
    Output that goes to a file or a pipe is never cleared.
    """
    if not console.is_terminal:
        return
    if _is_windows():
        try:
            console.file.flush()
            os.system("cls")
            return
        except Exception:
            pass
    console.clear()


def render_menu(
    console: Console,
    title: str,
    items: Sequence[MenuItem],
    admin: bool,
    can_restart: bool,
    at_root: bool,
    summary: str = "",
) -> None:
    """Draw one menu screen: banner (or title), summary line, numbered items and footer."""
    clear_screen(console)
    if at_root:
        print_banner(console)
        if summary:
            console.print(Text(f"  {summary}", style="muted"))
        console.print(Text("  MAIN MENU", style="title"))
    else:
        console.print(Text("  LAPFORGE", style="brand"))
        console.print(Text(f"  {title.upper()}", style="title"))
    console.print()
    console.print(Padding(menu_table(items), (0, 0, 0, 2)))
    console.print()
    footer = footer_lines(admin, can_restart, at_root)
    console.print(Text("  " + footer[0], style="good" if admin else "muted"))
    for line in footer[1:]:
        console.print(Text("  " + line, style="accent"))
    console.print()


# --------------------------------------------------------------------------
# Running an action
# --------------------------------------------------------------------------
Runner = Callable[[list[str]], int]


def default_runner(argv: list[str]) -> int:
    """Run a normal LapForge command in this window (imported here to avoid a circular import)."""
    from ..cli import main

    return int(main(argv) or 0)


def run_action(console: Console, item: MenuItem, runner: Runner) -> int | None:
    """Show the action header, run the command, and never let a failure close the menu.

    Returns the command's exit code, or None when it could not be started.
    """
    title, command = action_header_lines(item)
    console.print()
    console.print(Rule(Text(title, style="title"), style="border"))
    console.print(Text(f"  {command}", style="muted"))
    console.print()
    try:
        return runner(list(item.command))
    except SystemExit as exc:  # argparse stops this way when a command line is wrong
        if exc.code not in (0, None):
            console.print("  [muted]That command could not be started.[/muted]")
        return None
    except KeyboardInterrupt:
        console.print("\n  [muted]Cancelled.[/muted]")
        return None
    except Exception as exc:
        show_error(console, exc)
        return None


def _read(console: Console, ask: Callable[[str], str] | None, prompt: str) -> str | None:
    """One line of input. None when input is closed or the person pressed Ctrl+C."""
    try:
        if ask is not None:
            return ask(prompt)
        return console.input(Text(prompt, style="label"))
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None


def run_menu(
    console: Console | None = None,
    runner: Runner | None = None,
    ask: Callable[[str], str] | None = None,
    admin: bool | None = None,
    can_restart: bool | None = None,
    relaunch: Callable[[Sequence[str]], str] | None = None,
    items: Sequence[MenuItem] = MAIN_MENU,
) -> int:
    """Show the menu until the person leaves. Returns 0.

    Ctrl+C and closed input leave cleanly. `runner`, `ask`, `admin`, `can_restart` and `relaunch`
    can be replaced (tests do this); the defaults are the real ones.
    """
    console = console or make_console()
    runner = runner or default_runner
    relaunch = relaunch or elevate.relaunch_as_admin
    is_admin_now = is_admin() if admin is None else admin
    restart_possible = elevate.elevation_supported() if can_restart is None else can_restart
    summary = system_summary()

    stack: list[tuple[str, Sequence[MenuItem]]] = [("Main menu", items)]
    hint = ""
    while True:
        title, current = stack[-1]
        at_root = len(stack) == 1
        offered = (not is_admin_now) and restart_possible
        render_menu(console, title, current, is_admin_now, restart_possible, at_root, summary)
        if hint:
            console.print(Text(f"  {hint}", style="warn"))
            hint = ""
        raw = _read(console, ask, "  Your choice: ")
        if raw is None:
            return 0
        choice = parse_choice(raw, len(current), offered)

        if choice.kind == QUIT:
            return 0
        if choice.kind == BACK:
            if at_root:
                return 0
            stack.pop()
        elif choice.kind in (BLANK, INVALID):
            hint = invalid_hint(len(current), offered, at_root)
        elif choice.kind == ADMIN:
            console.print("  [muted]Windows will now ask for permission...[/muted]")
            result = relaunch(["menu"])
            if result == elevate.STARTED:
                console.print("  [good]LapForge is opening in a new Administrator window. You can close this one.[/good]")
                return 0
            if result == elevate.CANCELLED:
                hint = "Administrator permission was not given. LapForge continues as Standard user."
            else:
                hint = "LapForge could not restart as Administrator. Open an Administrator terminal and try again."
        elif choice.kind == ITEM and choice.index is not None:
            item = current[choice.index]
            if item.is_submenu:
                stack.append((item.title, item.children))
                continue
            run_action(console, item, runner)
            console.print()
            if _read(console, ask, "  Press Enter to return to the menu.") is None:
                return 0


__all__ = [
    "ADMIN",
    "BACK",
    "BLANK",
    "INVALID",
    "ITEM",
    "MAIN_MENU",
    "QUIT",
    "Choice",
    "MenuItem",
    "action_header_lines",
    "clear_screen",
    "admin_marker",
    "command_text",
    "default_runner",
    "find_item",
    "footer_lines",
    "invalid_hint",
    "parse_choice",
    "render_menu",
    "run_action",
    "run_menu",
    "summary_text",
    "system_summary",
    "walk",
    "windows_name",
]
