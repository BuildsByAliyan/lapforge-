"""Shared helpers for LapForge: safe command running, formatting, admin check."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
from typing import Any

NA = "N/A"
IS_WINDOWS = os.name == "nt"

# Placeholder strings that OEMs leave in firmware; they carry no information.
_JUNK_VALUES = {
    "",
    "none",
    "null",
    "unknown",
    "default string",
    "to be filled by o.e.m.",
    "to be filled by oem",
    "system product name",
    "system manufacturer",
    "system serial number",
    "not specified",
    "not applicable",
    "o.e.m.",
    "oem",
    "0",
}


def run_cmd(args: list[str], timeout: int = 20) -> str:
    """Run a command and return stdout as text. Returns "" on any failure."""
    kwargs: dict[str, Any] = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return (proc.stdout or "").strip()


def run_powershell(script: str, timeout: int = 40) -> str:
    """Run a PowerShell script (Windows only). Returns "" elsewhere or on failure."""
    if not IS_WINDOWS:
        return ""
    prefix = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    return run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            prefix + script,
        ],
        timeout=timeout,
    )


def powershell_json(script: str, timeout: int = 40) -> Any:
    """Run PowerShell that prints JSON and return the parsed value (None on failure)."""
    out = run_powershell(script, timeout=timeout)
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def as_list(value: Any) -> list[Any]:
    """PowerShell's ConvertTo-Json turns 1-item arrays into objects; undo that."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def clean_text(value: Any, default: str = NA) -> str:
    """Return a stripped string, or `default` for empty/placeholder firmware values."""
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() in _JUNK_VALUES:
        return default
    return text


def format_bytes(num: float | int | None, digits: int = 1) -> str:
    """1536 -> '1.5 KB'. Returns N/A for None or negative values."""
    if num is None:
        return NA
    try:
        size = float(num)
    except (TypeError, ValueError):
        return NA
    if size < 0:
        return NA
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.{digits}f} {unit}"
        size /= 1024
    return NA


def format_percent(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return NA
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return NA


def format_duration(seconds: float | int | None) -> str:
    """93784 -> '1d 2h 3m'."""
    if seconds is None:
        return NA
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return NA
    if total < 0:
        return NA
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def ordinal(n: int) -> str:
    """1 -> '1st', 11 -> '11th', 22 -> '22nd'."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def is_admin() -> bool:
    """True if running as Administrator (Windows) or root (elsewhere)."""
    try:
        if IS_WINDOWS:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except Exception:
        return False
