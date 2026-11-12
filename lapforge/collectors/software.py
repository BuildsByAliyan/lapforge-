"""Installed apps, startup entries and driver ages (read-only)."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psutil

from ..utils import IS_WINDOWS, NA, as_list, clean_text, format_bytes, powershell_json

try:  # winreg only exists on Windows
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

_UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_RUN = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
_UPDATE_NAME = re.compile(r"(\bKB\d{6,}\b|^(Security |Hotfix |Cumulative )?Update for )", re.IGNORECASE)

# Driver age limits (days). Old is not always broken, so we only nudge.
DRIVER_WARN_DAYS = 3 * 365
DRIVER_BAD_DAYS = 5 * 365
# Memory used right now by a startup program (bytes).
STARTUP_WARN_BYTES = 100 * 1024 * 1024
STARTUP_BAD_BYTES = 300 * 1024 * 1024

_DRIVER_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_PnPSignedDriver |
    Where-Object { $_.DeviceName -and $_.Manufacturer -and $_.Manufacturer -notmatch '^(Microsoft|Windows|\()' } |
    ForEach-Object {
        $d = $null
        if ($_.DriverDate) { $d = $_.DriverDate.ToString('yyyy-MM-dd') }
        [ordered]@{
            name = $_.DeviceName
            cls = $_.DeviceClass
            vendor = $_.Manufacturer
            version = $_.DriverVersion
            date = $d
        }
    } | ConvertTo-Json -Compress
"""


# --------------------------------------------------------------------------
# Registry helpers
# --------------------------------------------------------------------------
def _read_values(key: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    index = 0
    while True:
        try:
            name, data, _ = winreg.EnumValue(key, index)
        except OSError:
            break
        values[name] = data
        index += 1
    return values


def _open(root: Any, path: str, view: int = 0) -> Any:
    try:
        return winreg.OpenKey(root, path, 0, winreg.KEY_READ | view)
    except OSError:
        return None


def _registry_views() -> list[tuple[Any, int]]:
    return [
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, winreg.KEY_WOW64_64KEY),
    ]


# --------------------------------------------------------------------------
# Installed apps
# --------------------------------------------------------------------------
def _pretty_date(raw: Any) -> str:
    text = str(raw or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return ""


def parse_app_entry(values: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one Uninstall registry entry into an app dict (None = skip it)."""
    name = str(values.get("DisplayName") or "").strip()
    if not name or _UPDATE_NAME.search(name):
        return None
    if values.get("SystemComponent") == 1 or values.get("ParentKeyName"):
        return None
    if str(values.get("ReleaseType") or "").lower() in {"update", "hotfix", "security update", "update rollup"}:
        return None
    try:
        size_bytes = int(values.get("EstimatedSize") or 0) * 1024
    except (TypeError, ValueError):
        size_bytes = 0
    return {
        "name": name,
        "version": clean_text(values.get("DisplayVersion")),
        "publisher": clean_text(values.get("Publisher")),
        "install_date": _pretty_date(values.get("InstallDate")),
        "size_bytes": size_bytes,
        "size": format_bytes(size_bytes) if size_bytes else NA,
    }


def sort_apps(apps: list[dict[str, Any]], by: str = "name") -> list[dict[str, Any]]:
    """Sort apps by 'name' (A-Z), 'size' (biggest first) or 'date' (newest first)."""
    if by == "size":
        return sorted(apps, key=lambda a: (-a["size_bytes"], a["name"].lower()))
    if by == "date":
        return sorted(apps, key=lambda a: (a["install_date"] == "", _invert(a["install_date"]), a["name"].lower()))
    return sorted(apps, key=lambda a: a["name"].lower())


def _invert(text: str) -> tuple[int, ...]:
    return tuple(-ord(ch) for ch in text)


def get_installed_apps() -> dict[str, Any]:
    """All installed desktop programs (no Windows updates / system components)."""
    result: dict[str, Any] = {"available": False, "apps": [], "count": 0, "total_bytes": 0, "note": ""}
    if not IS_WINDOWS or winreg is None:
        result["note"] = "Installed apps list needs Windows (not available on this system)."
        return result
    seen: set[tuple[str, str]] = set()
    apps: list[dict[str, Any]] = []
    try:
        for root, view in _registry_views():
            base = _open(root, _UNINSTALL, view)
            if base is None:
                continue
            with base:
                for i in range(winreg.QueryInfoKey(base)[0]):
                    try:
                        sub = winreg.EnumKey(base, i)
                        with winreg.OpenKey(base, sub) as key:
                            app = parse_app_entry(_read_values(key))
                    except OSError:
                        continue
                    if app is None:
                        continue
                    ident = (app["name"].lower(), app["version"])
                    if ident in seen:
                        continue
                    seen.add(ident)
                    apps.append(app)
    except Exception as exc:
        result["note"] = f"Could not read installed apps: {exc}"
        return result
    result["available"] = True
    result["apps"] = sort_apps(apps, "name")
    result["count"] = len(apps)
    result["total_bytes"] = sum(a["size_bytes"] for a in apps)
    return result


# --------------------------------------------------------------------------
# Startup programs
# --------------------------------------------------------------------------
def parse_command_path(command: str) -> str:
    """'"C:\\A B\\x.exe" --flag' -> 'C:\\A B\\x.exe' (best effort)."""
    cmd = os.path.expandvars((command or "").strip())
    if not cmd:
        return ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 0 else cmd[1:]
    match = re.match(r"(?i)^(.+?\.(?:exe|bat|cmd|com|lnk|vbs|msc))(?:\s|$)", cmd)
    return match.group(1) if match else cmd.split(" ")[0]


def startup_impact(enabled: bool, memory: int | None) -> tuple[str, str]:
    """(level, label) for a startup entry, judged by memory it uses right now."""
    if not enabled:
        return "muted", "Disabled"
    if memory is None:
        return "good", "Light"
    if memory >= STARTUP_BAD_BYTES:
        return "bad", "Heavy"
    if memory >= STARTUP_WARN_BYTES:
        return "warn", "Medium"
    return "good", "Light"


def _approval_map() -> dict[tuple[str, str, str], bool]:
    """(scope, kind, name) -> enabled, from Task Manager's StartupApproved keys."""
    approved: dict[tuple[str, str, str], bool] = {}
    for scope, root in (("user", winreg.HKEY_CURRENT_USER), ("machine", winreg.HKEY_LOCAL_MACHINE)):
        for kind, sub in (("run", "Run"), ("run", "Run32"), ("folder", "StartupFolder")):
            key = _open(root, f"{_APPROVED}\\{sub}")
            if key is None:
                continue
            with key:
                for name, data in _read_values(key).items():
                    if isinstance(data, (bytes, bytearray)) and data:
                        approved[(scope, kind, name.lower())] = data[0] % 2 == 0
    return approved


def _memory_maps() -> tuple[dict[str, int], dict[str, int]]:
    """Memory (bytes) used by running programs, keyed by exe path and by process name."""
    by_path: dict[str, int] = {}
    by_name: dict[str, int] = {}
    try:
        for proc in psutil.process_iter(["name", "exe", "memory_info"]):
            info = proc.info
            mem = info.get("memory_info")
            if mem is None:
                continue
            exe = (info.get("exe") or "").lower()
            name = (info.get("name") or "").lower()
            if exe:
                by_path[exe] = by_path.get(exe, 0) + mem.rss
            if name:
                by_name[name] = by_name.get(name, 0) + mem.rss
    except Exception:
        pass
    return by_path, by_name


def _lookup_memory_key(command_path: str, by_path: dict[str, int], by_name: dict[str, int]) -> tuple[str, int] | None:
    """(key, bytes) of the running program a startup command belongs to, or None if it is not running.

    The key tells which running program was matched, so several startup entries that start the same
    program (for example five Edge auto-launch entries) can be recognised as one.
    """
    path = command_path.lower()
    if path in by_path:
        return "path:" + path, by_path[path]
    base = os.path.basename(path)
    if base.endswith(".lnk"):
        base = base[:-4] + ".exe"
    if base in by_name:
        return "name:" + base, by_name[base]
    return None


def _lookup_memory(command_path: str, by_path: dict[str, int], by_name: dict[str, int]) -> int | None:
    found = _lookup_memory_key(command_path, by_path, by_name)
    return found[1] if found else None


SHARED_MEMORY_TEXT = "Shared (counted once)"


def build_startup_items(raw: list[dict[str, Any]], by_path: dict[str, int], by_name: dict[str, int]) -> list[dict[str, Any]]:
    """Add memory and impact to each startup entry. Pure.

    Several entries can start the same program. Its memory is counted ONCE (for the first enabled
    entry); the others say "Shared" instead of repeating the same number, so the totals and the
    "noticeable memory" count are not inflated.
    """
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    claimed: set[str] = set()
    for r in raw:
        ident = (r["name"].lower(), r["command"].lower())
        if ident in seen:
            continue
        seen.add(ident)
        found = _lookup_memory_key(parse_command_path(r["command"]), by_path, by_name)
        memory: int | None = None
        text = "Not running"
        if found is not None:
            key, used = found
            if not r["enabled"]:
                text = "-"  # a disabled entry does not own the memory of a program that is running anyway
            elif key in claimed:
                text = SHARED_MEMORY_TEXT
            else:
                claimed.add(key)
                memory, text = used, format_bytes(used)
        level, impact = startup_impact(r["enabled"], memory)
        items.append({**r, "memory": memory, "memory_text": text, "level": level, "impact": impact})
    return items


def _startup_folders() -> list[tuple[str, Path]]:
    folders: list[tuple[str, Path]] = []
    appdata, programdata = os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")
    tail = Path("Microsoft") / "Windows" / "Start Menu" / "Programs" / "Startup"
    if appdata:
        folders.append(("user", Path(appdata) / tail))
    if programdata:
        folders.append(("machine", Path(programdata) / tail))
    return folders


def get_startup_apps() -> dict[str, Any]:
    """Programs that start with Windows, with their current memory use."""
    result: dict[str, Any] = {
        "available": False,
        "items": [],
        "count": 0,
        "enabled_count": 0,
        "heavy_count": 0,
        "note": "",
    }
    if not IS_WINDOWS or winreg is None:
        result["note"] = "Startup list needs Windows (not available on this system)."
        return result
    raw: list[dict[str, Any]] = []
    try:
        approved = _approval_map()
        by_path, by_name = _memory_maps()
        for scope, root, view in (
            ("user", winreg.HKEY_CURRENT_USER, winreg.KEY_WOW64_64KEY),
            ("machine", winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
            ("machine", winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        ):
            key = _open(root, _RUN, view)
            if key is None:
                continue
            with key:
                for name, data in _read_values(key).items():
                    if isinstance(data, str) and data.strip():
                        raw.append(
                            {
                                "name": name,
                                "command": data.strip(),
                                "source": "Registry (this user)" if scope == "user" else "Registry (all users)",
                                "enabled": approved.get((scope, "run", name.lower()), True),
                            }
                        )
        for scope, folder in _startup_folders():
            if not folder.is_dir():
                continue
            for entry in sorted(folder.iterdir()):
                if entry.is_dir() or entry.name.lower() == "desktop.ini":
                    continue
                raw.append(
                    {
                        "name": entry.stem,
                        "command": str(entry),
                        "source": "Startup folder (this user)" if scope == "user" else "Startup folder (all users)",
                        "enabled": approved.get((scope, "folder", entry.name.lower()), True),
                    }
                )
        items = build_startup_items(raw, by_path, by_name)
    except Exception as exc:
        result["note"] = f"Could not read startup programs: {exc}"
        return result
    order = {"bad": 0, "warn": 1, "good": 2, "muted": 3}
    items.sort(key=lambda i: (order.get(i["level"], 3), -(i["memory"] or 0), i["name"].lower()))
    result["available"] = True
    result["items"] = items
    result["count"] = len(items)
    result["enabled_count"] = sum(1 for i in items if i["enabled"])
    result["heavy_count"] = sum(1 for i in items if i["level"] in ("bad", "warn"))
    return result


# --------------------------------------------------------------------------
# Drivers
# --------------------------------------------------------------------------
def driver_age_level(age_days: int | None) -> str:
    if age_days is None:
        return "muted"
    if age_days >= DRIVER_BAD_DAYS:
        return "bad"
    if age_days >= DRIVER_WARN_DAYS:
        return "warn"
    return "good"


def _age_text(age_days: int | None) -> str:
    if age_days is None:
        return NA
    if age_days < 60:
        return f"{age_days} days"
    if age_days < 730:
        return f"{age_days // 30} months"
    return f"{age_days / 365:.1f} years"


def parse_driver(row: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Raw PowerShell row -> driver dict with age and level."""
    today = today or date.today()
    raw_date = str(row.get("date") or "").strip()
    age_days: int | None = None
    try:
        age_days = max(0, (today - datetime.strptime(raw_date, "%Y-%m-%d").date()).days)
    except ValueError:
        raw_date = ""
    return {
        "name": clean_text(row.get("name")),
        "cls": clean_text(row.get("cls")),
        "vendor": clean_text(row.get("vendor")),
        "version": clean_text(row.get("version")),
        "date": raw_date or NA,
        "age_days": age_days,
        "age_text": _age_text(age_days),
        "level": driver_age_level(age_days),
    }


def get_drivers() -> dict[str, Any]:
    """Third-party drivers, oldest first. Microsoft in-box drivers are left out."""
    result: dict[str, Any] = {"available": False, "drivers": [], "count": 0, "old_count": 0, "very_old_count": 0, "note": ""}
    if not IS_WINDOWS:
        result["note"] = "Driver check needs Windows (not available on this system)."
        return result
    data = powershell_json(_DRIVER_SCRIPT, timeout=90)
    rows = [r for r in as_list(data) if isinstance(r, dict)]
    if not rows:
        result["note"] = "Could not read the driver list (try an Administrator terminal)."
        return result
    drivers: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        drv = parse_driver(row)
        ident = (drv["name"], drv["version"], drv["date"])
        if ident in seen:
            continue
        seen.add(ident)
        drivers.append(drv)
    drivers.sort(key=lambda d: (d["age_days"] is None, -(d["age_days"] or 0), d["name"].lower()))
    result["available"] = True
    result["drivers"] = drivers
    result["count"] = len(drivers)
    result["old_count"] = sum(1 for d in drivers if d["level"] == "warn")
    result["very_old_count"] = sum(1 for d in drivers if d["level"] == "bad")
    return result
