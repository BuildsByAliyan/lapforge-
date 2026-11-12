"""Entry point for LapForge.exe and `python -m lapforge`.

`run()` does three things around the normal `cli.main()`:

  1. It makes the output UTF-8, so the boxes and bars never break on old Windows code pages.
  2. It never lets a raw Python error reach the screen.
  3. When LapForge was started by a double-click (Windows opened a console window just for it),
     it waits for Enter before the window closes, so the last screen can always be read.
     When LapForge was started from an existing terminal it never waits.
"""

from __future__ import annotations

import sys
from typing import Any, Sequence

from . import elevate

CLOSE_MESSAGE = "Press Enter to close this window."


def exit_code_from(code: Any) -> int:
    """Turn the value of a SystemExit into a process exit code. Pure.

    None -> 0, a number -> that number, anything else (a message) -> 1.
    """
    if code is None:
        return 0
    if isinstance(code, bool):
        return int(code)
    if isinstance(code, int):
        return code
    return 1


def should_pause(own_console: bool, argv: Sequence[str]) -> bool:
    """True when the window must wait for Enter before it closes. Pure.

    Only for a console that LapForge owns (a double-click). A restarted Administrator window
    already waits by itself (see cli.main), so it must not wait a second time.
    """
    return bool(own_console) and elevate.ELEVATED_FLAG not in argv


def set_utf8_output() -> None:
    """Switch stdout and stderr to UTF-8. Streams that cannot be switched are left alone."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def _show_unexpected_error(exc: BaseException, debug: bool) -> None:
    """A calm message for a problem nobody planned for (never a raw traceback)."""
    try:
        from .ui.errors import show_error
        from .ui.theme import make_console

        show_error(make_console(), exc, debug=debug)
    except Exception:
        print("LapForge could not finish. Run with --debug for technical details.")


def _wait_before_closing(argv: Sequence[str]) -> None:
    """Wait for Enter when the console window belongs to LapForge alone."""
    try:
        own = elevate.console_owned_by_us()
    except Exception:
        own = False
    if not should_pause(own, argv):
        return
    try:
        input(f"\n{CLOSE_MESSAGE} ")
    except (EOFError, KeyboardInterrupt):
        pass


def run(argv: Sequence[str] | None = None) -> int:
    """Run LapForge and return the exit code. Never raises."""
    words = list(sys.argv[1:] if argv is None else argv)
    set_utf8_output()
    try:
        from .cli import main

        code = int(main(words) or 0)
    except SystemExit as exc:  # --version, --help and wrong command lines end this way
        code = exit_code_from(exc.code)
    except KeyboardInterrupt:
        print("\nCancelled.")
        code = 130
    except Exception as exc:
        _show_unexpected_error(exc, debug="--debug" in words)
        code = 1
    _wait_before_closing(words)
    return code


__all__ = ["CLOSE_MESSAGE", "exit_code_from", "run", "set_utf8_output", "should_pause"]
