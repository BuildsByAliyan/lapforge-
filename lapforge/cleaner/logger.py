"""Run logs for the cleaner and repair commands.

Every real run writes one plain-text log file on THIS computer. Nothing is sent
anywhere. Logs only name cleaner targets and totals (never individual file
names), and the newest logs are kept while older ones are removed.

Folder: %LOCALAPPDATA%\\LapForge\\logs on Windows, ~/.lapforge/logs elsewhere.
Set LAPFORGE_LOG_DIR to use a different folder.
"""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from typing import Any

from .. import __app_name__, __version__
from ..utils import IS_WINDOWS, is_admin

KEEP_LOGS = 30
_KINDS = ("clean", "repair")


def log_dir() -> Path:
    """Folder where run logs are written (not created here)."""
    override = os.environ.get("LAPFORGE_LOG_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if IS_WINDOWS and local:
        return Path(local) / "LapForge" / "logs"
    return Path.home() / ".lapforge" / "logs"


def list_logs(kind: str | None = None, limit: int | None = None) -> list[Path]:
    """Log files, newest first. `kind` is 'clean' or 'repair' (None = all)."""
    folder = log_dir()
    try:
        files = [p for p in folder.glob("*.log") if kind is None or p.name.startswith(f"{kind}-")]
    except OSError:
        return []
    files.sort(key=lambda p: p.name, reverse=True)
    return files[:limit] if limit else files


def prune_logs(keep: int = KEEP_LOGS) -> int:
    """Remove the oldest LapForge log files so only `keep` remain. Returns how many were removed."""
    removed = 0
    for old in list_logs()[max(1, keep):]:
        if not old.name.startswith(tuple(f"{k}-" for k in _KINDS)):
            continue
        try:
            old.unlink()
            removed += 1
        except OSError:
            continue
    return removed


class RunLog:
    """A text log for one run. Writing never raises: if the log cannot be written,
    `path` is None and the command carries on."""

    def __init__(self, kind: str, directory: Path | None = None) -> None:
        if kind not in _KINDS:
            raise ValueError(f"kind must be one of {_KINDS}, not {kind!r}")
        self.kind = kind
        self.path: Path | None = None
        self._handle = None
        folder = directory or log_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            candidate = folder / f"{kind}-{stamp}.log"
            number = 1
            while candidate.exists():  # two runs in the same second
                number += 1
                candidate = folder / f"{kind}-{stamp}-{number}.log"
            self._handle = open(candidate, "w", encoding="utf-8", errors="replace")
            self.path = candidate
        except OSError:
            self._handle = None
        self.line(f"{__app_name__} {__version__} - {kind} run")
        self.line(f"Started : {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.line(f"System  : {platform.system()} {platform.release()} ({platform.version()})")
        self.line(f"Admin   : {'yes' if is_admin() else 'no'}")

    def line(self, text: str = "") -> None:
        if self._handle is None:
            return
        try:
            self._handle.write(text + "\n")
            self._handle.flush()
        except (OSError, ValueError):
            self._handle = None

    def section(self, title: str) -> None:
        self.line()
        self.line(f"== {title} ==")

    def lines(self, rows: list[str]) -> None:
        for row in rows:
            self.line(row)

    def close(self) -> Path | None:
        self.line()
        self.line(f"Finished: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        prune_logs()
        return self.path

    def __enter__(self) -> "RunLog":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def write_log(kind: str, title: str, lines: list[str]) -> Path | None:
    """Write a finished run to a new log file. Returns its path (None if it could not be written)."""
    log = RunLog(kind)
    log.section(title)
    log.lines(lines)
    return log.close()
