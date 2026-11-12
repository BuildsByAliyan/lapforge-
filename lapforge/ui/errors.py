"""Friendly error display.

`friendly_message` is pure (easy to test). `show_error` prints a calm panel
instead of a raw Python traceback. Technical details appear only with --debug.
"""

from __future__ import annotations

import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# (what happened, what the user can do)
_GENERIC = (
    "LapForge could not finish this task because of an unexpected problem.",
    "Try the command again. If it keeps failing, restart LapForge.",
)
_PERMISSION = (
    "LapForge does not have permission to read or change something it needed.",
    "Close other programs that may be using the item, or run LapForge as Administrator.",
)
_NOT_FOUND = (
    "LapForge could not find a file or folder it needed.",
    "Check that the item still exists and has not been moved or deleted, then try again.",
)
_TIMEOUT = (
    "A Windows tool took too long to answer.",
    "Wait a moment and try again. Closing heavy programs can help.",
)
_OS_ERROR = (
    "Windows reported a problem while LapForge was reading or writing data.",
    "Check that your drive is connected and not full, then try again.",
)


def _advice(exc: BaseException) -> tuple[str, str]:
    """Return (sentence, what_to_do) for an exception. Specific types come first."""
    if isinstance(exc, PermissionError):
        return _PERMISSION
    if isinstance(exc, FileNotFoundError):
        return _NOT_FOUND
    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
        return _TIMEOUT
    if isinstance(exc, OSError):
        return _OS_ERROR
    return _GENERIC


def friendly_message(exc: BaseException) -> str:
    """One plain sentence that describes the problem (no exception names)."""
    return _advice(exc)[0]


def friendly_advice(exc: BaseException) -> str:
    """One plain sentence that tells the user what to try next."""
    return _advice(exc)[1]


def show_error(console: Console, exc: BaseException, debug: bool = False) -> None:
    """Print a calm red panel. With debug=True also print the full traceback."""
    body = Text()
    body.append(friendly_message(exc) + "\n\n")
    body.append("What you can do\n", style="bold")
    body.append(friendly_advice(exc) + "\n\n")
    body.append("Run with --debug for technical details.", style="muted")
    console.print()
    console.print(Panel(body, title="[bad]Something went wrong[/bad]", border_style="bad", padding=(1, 2)))
    if debug:
        console.print_exception(show_locals=False)
