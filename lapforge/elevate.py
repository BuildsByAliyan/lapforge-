"""Administrator awareness: which features need it, and how to restart with it.

LapForge never bypasses Windows security. To get Administrator rights it starts a new
copy of itself with the "runas" verb, which always shows the normal Windows permission
prompt. The person can press No, and LapForge then carries on with the limited version.

The decisions (which command needs Administrator, how the restart command looks, what to
do next) are plain functions that need no Windows. The few Windows calls sit in small
wrappers that are guarded and never raise.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from argparse import Namespace
from collections.abc import Mapping, Sequence
from typing import Any

from .utils import IS_WINDOWS, is_admin

# Internal marker added to the restarted copy so it never restarts itself again.
ELEVATED_FLAG = "--elevated"
ADMIN_FLAG = "--admin"

REQUIRED = "required"  # the feature cannot do its job without Administrator rights
RECOMMENDED = "recommended"  # works, but some details stay "Unknown" without them

_SW_SHOWNORMAL = 1
_SE_ERR_ACCESSDENIED = 5  # what ShellExecute returns when the permission prompt is declined
_ERROR_CANCELLED = 1223

STARTED = "started"
CANCELLED = "cancelled"
FAILED = "failed"


# --------------------------------------------------------------------------
# Which features need Administrator (pure)
# --------------------------------------------------------------------------
def _active_flags(args: Any) -> set[str]:
    """Names of the options that are switched on, such as {"reveal", "full"}.

    `args` can be an argparse Namespace, a dict, or a list of command-line words
    (["--full", "--dry-run"]). Names have no dashes and use underscores.
    """
    if args is None:
        return set()
    if isinstance(args, Namespace):
        args = vars(args)
    if isinstance(args, Mapping):
        return {str(key).lstrip("-").replace("-", "_") for key, value in args.items() if value}
    if isinstance(args, Sequence) and not isinstance(args, str):
        flags = set()
        for word in args:
            word = str(word)
            if word.startswith("-") and len(word) > 1:
                flags.add(word.lstrip("-").split("=", 1)[0].replace("-", "_"))
        return flags
    return set()


def admin_reason(command: str | None, args: Any = None) -> tuple[str, str]:
    """(level, what is missing without Administrator) for a command, or ("", "").

    level is REQUIRED or RECOMMENDED. The sentence says what the limited version cannot do.
    """
    flags = _active_flags(args)
    if command == "wifi" and "reveal" in flags:
        return REQUIRED, "Saved Wi-Fi passwords will not be shown."
    if command == "repair" and "run" in flags:
        return REQUIRED, "System file repair cannot run."
    if command == "startup" and flags & {"apply", "undo", "manage"}:
        return REQUIRED, "Changes to startup programs that start for all users cannot be made."
    if command == "clean" and "full" in flags:
        return REQUIRED, "Windows temp files, Prefetch and the update cache will be skipped or only partly cleaned."
    if command in ("info", "health"):
        return RECOMMENDED, "Drive wear and temperature details may show as Unknown."
    return "", ""


def needs_admin(command: str | None, args: Any = None) -> bool:
    """True if the command is better with Administrator rights (see admin_reason)."""
    return bool(admin_reason(command, args)[0])


def running_as_text(admin: bool) -> str:
    """'Administrator' or 'Standard user'."""
    return "Administrator" if admin else "Standard user"


def parse_yes_no(answer: str | None) -> bool:
    """True only for an explicit yes ('y' or 'yes'). Empty or anything else is No."""
    return (answer or "").strip().lower() in ("y", "yes")


# --------------------------------------------------------------------------
# What to do about it (pure)
# --------------------------------------------------------------------------
NONE = "none"  # carry on as normal
ASK = "ask"  # ask the person first
RELAUNCH = "relaunch"  # restart as Administrator now (the person used --admin)
UNSUPPORTED = "unsupported"  # --admin was used but this system cannot restart that way


def decide_elevation(
    command: str | None,
    args: Any,
    *,
    admin: bool,
    elevated: bool,
    supported: bool,
    forced: bool,
    interactive: bool,
) -> str:
    """Decide what LapForge does about Administrator rights. Returns NONE, ASK, RELAUNCH or UNSUPPORTED.

    elevated  - this copy was already started by LapForge's restart (never restart twice)
    forced    - the person used --admin
    interactive - someone can answer a question (the terminal is a real one)
    """
    if admin or elevated:
        return NONE
    if forced:
        return RELAUNCH if supported else UNSUPPORTED
    if supported and interactive and admin_reason(command, args)[0] == REQUIRED:
        return ASK
    return NONE


# --------------------------------------------------------------------------
# Restart command (pure)
# --------------------------------------------------------------------------
def build_relaunch_command(argv: Sequence[str], frozen: bool, executable: str) -> tuple[str, str]:
    """(program, parameter text) that starts LapForge again with the same command.

    argv        - the words after the program name, e.g. ["clean", "--full"]
    frozen      - True for the built LapForge.exe, False when run from Python source
    executable  - the exe (frozen) or the Python interpreter (source)

    --admin is removed and the internal --elevated marker is added, so the new copy
    does not restart itself again. Words are quoted with subprocess.list2cmdline.
    """
    words = [str(w) for w in argv if w not in (ADMIN_FLAG, ELEVATED_FLAG)]
    words.append(ELEVATED_FLAG)
    if not frozen:
        words = ["-m", "lapforge", *words]
    return executable, subprocess.list2cmdline(words)


def interpret_shell_result(code: int | None, last_error: int = 0) -> str:
    """Turn the ShellExecute result into STARTED, CANCELLED or FAILED.

    A value above 32 means success. Declining the Windows permission prompt gives
    "access denied" (5) or the error code 1223.
    """
    value = int(code or 0)
    if value > 32:
        return STARTED
    if value == _SE_ERR_ACCESSDENIED or last_error == _ERROR_CANCELLED:
        return CANCELLED
    return FAILED


def owns_console(
    pids: Sequence[int],
    my_pid: int,
    parent_pid: int | None = None,
    parent_name: str = "",
    my_name: str = "",
    frozen: bool = False,
) -> bool:
    """True when the console window belongs to LapForge alone (typical for a double-click).

    `pids` are all processes attached to the console. A built single-file exe starts a small
    copy of itself first, so that parent (same file name) is not counted as another program.
    """
    others = set(pids) - {my_pid}
    if not others:
        return True
    return bool(
        frozen
        and parent_pid is not None
        and others == {parent_pid}
        and parent_name
        and parent_name.lower() == my_name.lower()
    )


# --------------------------------------------------------------------------
# Windows calls (small, guarded, never raise)
# --------------------------------------------------------------------------
def elevation_supported() -> bool:
    """Restarting as Administrator is only possible on Windows."""
    return IS_WINDOWS


def is_frozen() -> bool:
    """True when running as the built LapForge.exe."""
    return bool(getattr(sys, "frozen", False))


def _shell_execute_runas(program: str, params: str, directory: str) -> tuple[int, int]:
    """Call ShellExecuteW with the 'runas' verb. Returns (result code, last error)."""
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    shell32.ShellExecuteW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    ctypes.windll.kernel32.SetLastError(0)  # type: ignore[attr-defined]
    result = shell32.ShellExecuteW(None, "runas", program, params, directory, _SW_SHOWNORMAL)
    return int(result or 0), int(ctypes.windll.kernel32.GetLastError())  # type: ignore[attr-defined]


def relaunch_as_admin(argv: Sequence[str] | None = None, directory: str | None = None) -> str:
    """Start LapForge again as Administrator (Windows shows its permission prompt).

    Returns "started", "cancelled" (the person pressed No) or "failed". After "started"
    the caller should end the current process. `argv` defaults to the current command line.
    """
    if not IS_WINDOWS:
        return FAILED
    try:
        words = list(sys.argv[1:] if argv is None else argv)
        program, params = build_relaunch_command(words, is_frozen(), sys.executable)
        # The new window must start in the same folder, or "python -m lapforge" would not
        # find LapForge when it is run from its source folder.
        code, error = _shell_execute_runas(program, params, directory or os.getcwd())
        return interpret_shell_result(code, error)
    except Exception:
        return FAILED


def _console_process_ids() -> list[int]:
    buffer = (ctypes.c_uint * 64)()
    count = ctypes.windll.kernel32.GetConsoleProcessList(buffer, 64)  # type: ignore[attr-defined]
    return [int(buffer[i]) for i in range(min(int(count), 64))]


def console_owned_by_us() -> bool:
    """True when LapForge created its own console window (a double-clicked exe).

    False when it was started from an existing terminal, and on any other system.
    """
    if not IS_WINDOWS:
        return False
    try:
        pids = _console_process_ids()
        if not pids:
            return False  # no console at all
        parent_pid: int | None = None
        parent_name = ""
        if is_frozen():
            try:
                import psutil

                parent_pid = os.getppid()
                parent_name = psutil.Process(parent_pid).name()
            except Exception:
                parent_pid = None
        return owns_console(
            pids,
            os.getpid(),
            parent_pid,
            parent_name,
            os.path.basename(sys.executable),
            is_frozen(),
        )
    except Exception:
        return False


def wait_for_enter_if_own_console(message: str = "Press Enter to close this window.") -> None:
    """Keep a window of our own open until Enter, so the result can be read. No-op in a terminal."""
    if not console_owned_by_us():
        return
    try:
        input(f"\n{message} ")
    except (EOFError, KeyboardInterrupt):
        pass


__all__ = [
    "ADMIN_FLAG",
    "ASK",
    "CANCELLED",
    "ELEVATED_FLAG",
    "FAILED",
    "NONE",
    "RECOMMENDED",
    "RELAUNCH",
    "REQUIRED",
    "STARTED",
    "UNSUPPORTED",
    "admin_reason",
    "build_relaunch_command",
    "console_owned_by_us",
    "decide_elevation",
    "elevation_supported",
    "interpret_shell_result",
    "is_admin",
    "is_frozen",
    "needs_admin",
    "owns_console",
    "parse_yes_no",
    "relaunch_as_admin",
    "running_as_text",
    "wait_for_enter_if_own_console",
]
