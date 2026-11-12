"""Cleaner engine: what can be cleaned, how much it holds, and (safely) deleting it.

Safety rules built into this module:
  * Dry-run is the default. Nothing is deleted unless dry_run=False is passed.
  * Personal folders (Documents, Pictures, Videos, Desktop, Downloads, Music,
    OneDrive) are never scanned or touched, even if a target points at them.
  * Symbolic links / junctions are never followed.
  * Files in use are skipped and counted, never forced.
  * Recently changed temp files (younger than min_age_hours) are left alone.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import os
import stat
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import psutil

from ..utils import IS_WINDOWS, format_bytes, powershell_json, run_cmd, run_powershell

PROFILES = ("quick", "full")
_REPARSE_POINT = 0x400
_MAX_ERRORS_KEPT = 5

_DNS_COUNT_SCRIPT = "@(Get-DnsClientCache -ErrorAction SilentlyContinue).Count"


@dataclass(frozen=True)
class CleanTarget:
    """One thing the cleaner can clear."""

    key: str
    label: str
    description: str
    profiles: tuple[str, ...]
    kind: str = "folder"  # "folder" | "recycle" | "dns"
    roots: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()  # file-name patterns (empty = every file)
    min_age_hours: float = 0.0
    needs_admin: bool = False
    warning: str = ""


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------
def _env_path(*names: str, tail: str = "") -> str:
    for name in names:
        base = os.environ.get(name)
        if base:
            return str(Path(base) / tail) if tail else base
    return ""


def _all_targets() -> list[CleanTarget]:
    user_temp = _env_path("TEMP", "TMP")
    local = _env_path("LOCALAPPDATA")
    windir = _env_path("SystemRoot", "WINDIR")
    return [
        CleanTarget(
            "user_temp",
            "User temp files",
            "Leftovers apps wrote to your temp folder",
            ("quick", "full"),
            roots=(user_temp,) if user_temp else (),
            min_age_hours=1.0,
        ),
        CleanTarget(
            "windows_temp",
            "Windows temp files",
            "System-wide temp folder",
            ("quick", "full"),
            roots=(str(Path(windir) / "Temp"),) if windir else (),
            min_age_hours=1.0,
            needs_admin=True,
        ),
        CleanTarget(
            "dns_cache",
            "DNS cache",
            "Remembered website addresses (rebuilt automatically)",
            ("quick", "full"),
            kind="dns",
            needs_admin=True,
        ),
        CleanTarget(
            "prefetch",
            "Prefetch files",
            "Windows app-launch hints (rebuilt automatically)",
            ("full",),
            roots=(str(Path(windir) / "Prefetch"),) if windir else (),
            patterns=("*.pf",),
            needs_admin=True,
            warning="The first launch of each app afterwards may be a little slower.",
        ),
        CleanTarget(
            "update_cache",
            "Windows Update download cache",
            "Already-installed update downloads",
            ("full",),
            roots=(str(Path(windir) / "SoftwareDistribution" / "Download"),) if windir else (),
            needs_admin=True,
        ),
        CleanTarget(
            "thumbnails",
            "Thumbnail and icon cache",
            "Picture previews Explorer rebuilds when needed",
            ("full",),
            roots=(str(Path(local) / "Microsoft" / "Windows" / "Explorer"),) if local else (),
            patterns=("thumbcache_*.db", "iconcache_*.db"),
            warning="Some files are locked by Explorer and will be skipped.",
        ),
        CleanTarget(
            "recycle_bin",
            "Recycle Bin",
            "Files you already deleted (your account, all fixed drives)",
            ("full",),
            kind="recycle",
            warning="Permanently deletes the files in your Recycle Bin on all fixed drives. Other users' Recycle Bins are not touched.",
        ),
    ]


def cleaner_supported() -> bool:
    """The built-in targets are Windows locations."""
    return IS_WINDOWS


def get_targets(profile: str = "quick") -> list[CleanTarget]:
    """Targets for 'quick' (temp + DNS) or 'full' (quick + prefetch, update cache, thumbnails, Recycle Bin)."""
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}, not {profile!r}")
    if not cleaner_supported():
        return []
    return [t for t in _all_targets() if profile in t.profiles]


# --------------------------------------------------------------------------
# Protection
# --------------------------------------------------------------------------
def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


@lru_cache(maxsize=1)
def protected_dirs() -> tuple[str, ...]:
    """Personal folders that must never be cleaned (normalised, absolute)."""
    home = Path.home()
    found: list[str] = []
    for name in ("Documents", "Pictures", "Videos", "Desktop", "Downloads", "Music"):
        found.append(str(home / name))
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            found.append(os.environ[var])
    if IS_WINDOWS:  # folders the user moved to another drive
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            ) as key:
                for value_name in (
                    "Personal",
                    "My Pictures",
                    "My Video",
                    "My Music",
                    "Desktop",
                    "{374DE290-123F-4565-9164-39C4925E467B}",
                ):
                    try:
                        raw, _ = winreg.QueryValueEx(key, value_name)
                        found.append(os.path.expandvars(str(raw)))
                    except OSError:
                        continue
        except OSError:
            pass
    return tuple(sorted({_norm(p) for p in found if p}))


def is_protected(path: str) -> bool:
    """True if `path` is inside a personal folder, or contains one."""
    target = _norm(os.path.realpath(path))
    for guard in protected_dirs():
        if target == guard or target.startswith(guard + os.sep) or guard.startswith(target + os.sep):
            return True
    return False


def _dangerous_roots() -> set[str]:
    roots = {_norm(str(Path.home())), _norm(str(Path.home().parent))}
    for var in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemDrive"):
        value = os.environ.get(var)
        if value:
            roots.add(_norm(value + (os.sep if var == "SystemDrive" else "")))
    return roots


def check_root(root: str) -> str:
    """Return '' if `root` may be cleaned, otherwise the reason it must be refused."""
    if not root:
        return "No folder configured"
    path = Path(root)
    if path.parent == path:
        return "Refused: drive root"
    if _norm(root) in _dangerous_roots():
        return "Refused: important system or user folder"
    if is_protected(root):
        return "Refused: personal folder"
    return ""


# --------------------------------------------------------------------------
# File walking
# --------------------------------------------------------------------------
def _is_link(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & _REPARSE_POINT)
    except OSError:
        return True  # when unsure, do not touch it


def iter_files(
    root: str,
    patterns: tuple[str, ...] = (),
    min_age_hours: float = 0.0,
    errors: list[str] | None = None,
) -> Iterator[tuple[str, int]]:
    """Yield (path, size) for every cleanable file under `root`.

    Never follows links, never enters personal folders, skips files newer than
    `min_age_hours`, and (if `patterns` is given) only matches those file names.
    """
    cutoff = time.time() - min_age_hours * 3600
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if _is_link(entry):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not is_protected_fast(entry.path):
                                stack.append(entry.path)
                            continue
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if patterns and not any(fnmatch.fnmatch(entry.name.lower(), p.lower()) for p in patterns):
                        continue
                    if min_age_hours and info.st_mtime > cutoff:
                        continue
                    yield entry.path, info.st_size
        except OSError as exc:
            if errors is not None and len(errors) < _MAX_ERRORS_KEPT:
                errors.append(f"{folder}: {exc.strerror or exc}")


def is_protected_fast(path: str) -> bool:
    """Cheap per-folder guard used while walking (no symlink resolution needed)."""
    target = _norm(path)
    return any(target == g or target.startswith(g + os.sep) for g in protected_dirs())


# --------------------------------------------------------------------------
# Special targets (Recycle Bin, DNS)
# --------------------------------------------------------------------------
def _current_sid() -> str:
    out = run_cmd(["whoami", "/user", "/fo", "csv", "/nh"], timeout=15)
    try:
        row = next(csv.reader(io.StringIO(out)))
        return row[1].strip()
    except (StopIteration, IndexError):
        return ""


def _fixed_drives() -> list[str]:
    drives = []
    try:
        for part in psutil.disk_partitions(all=False):
            if "fixed" in part.opts.lower() or not part.opts:
                drives.append(part.mountpoint)
    except Exception:
        pass
    return drives


def recycle_clear_script(drives: list[str]) -> str:
    """PowerShell that empties the current user's Recycle Bin on exactly the given drives.

    `drives` are mount points such as "C:\\". The preview measures these same drives,
    so the action matches what the preview showed. Pure function (no system access).
    """
    letters = sorted({d[0].upper() for d in drives if len(d) >= 2 and d[0].isalpha() and d[1] == ":"})
    return "; ".join(f"Clear-RecycleBin -DriveLetter {letter} -Force -ErrorAction SilentlyContinue" for letter in letters)


def _recycle_folders() -> list[str]:
    sid = _current_sid()
    if not sid:
        return []
    return [str(Path(d) / "$Recycle.Bin" / sid) for d in _fixed_drives() if (Path(d) / "$Recycle.Bin" / sid).is_dir()]


def _measure_recycle() -> tuple[int, int, list[str]]:
    files = total = 0
    errors: list[str] = []
    for folder in _recycle_folders():
        for path, size in iter_files(folder, errors=errors):
            if os.path.basename(path).upper().startswith("$I"):
                total += size  # tiny index files: count the bytes, not as 'files'
                continue
            files += 1
            total += size
    return files, total, errors


def _measure_dns() -> int | None:
    data = powershell_json(_DNS_COUNT_SCRIPT, timeout=20)
    return int(data) if isinstance(data, (int, float)) else None


# --------------------------------------------------------------------------
# Scan (read-only) and clean
# --------------------------------------------------------------------------
def _result(target: CleanTarget) -> dict[str, Any]:
    return {
        "key": target.key,
        "label": target.label,
        "description": target.description,
        "available": False,
        "refused": False,
        "needs_admin": target.needs_admin,
        "warning": target.warning,
        "note": "",
        "files": 0,
        "bytes": 0,
        "bytes_text": format_bytes(0),
        "errors": [],
    }


def scan_target(target: CleanTarget) -> dict[str, Any]:
    """Measure what a target holds. Read-only - never deletes."""
    res = _result(target)
    if target.kind == "recycle":
        if not IS_WINDOWS:
            res["note"] = "Windows only"
            return res
        files, total, errors = _measure_recycle()
        res.update(available=True, files=files, bytes=total, errors=errors)
    elif target.kind == "dns":
        if not IS_WINDOWS:
            res["note"] = "Windows only"
            return res
        count = _measure_dns()
        if count is None:
            res["note"] = "Could not read the DNS cache"
            return res
        res.update(available=True, files=count)
        res["note"] = "entries (no disk space)"
    else:
        folders = [r for r in target.roots if r]
        if not folders:
            res["note"] = "Folder not found on this PC"
            return res
        for root in folders:
            reason = check_root(root)
            if reason:
                res.update(refused=True, note=reason)
                return res
            if not os.path.isdir(root):
                res["note"] = "Folder not found on this PC"
                continue
            res["available"] = True
            for _, size in iter_files(root, target.patterns, target.min_age_hours, res["errors"]):
                res["files"] += 1
                res["bytes"] += size
    res["bytes_text"] = format_bytes(res["bytes"])
    return res


def _delete_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except PermissionError:
        try:  # read-only files: clear the flag once and retry
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
            return True
        except OSError:
            return False
    except OSError:
        return False


def _old_dirs(root: str, min_age_hours: float) -> set[str]:
    """Sub-folders old enough to remove once empty (measured BEFORE deleting files,
    because deleting a file refreshes its folder's modified time)."""
    cutoff = time.time() - min_age_hours * 3600
    found: set[str] = set()
    for folder, dirs, _files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(folder, d)) and not is_protected_fast(os.path.join(folder, d))]
        for name in dirs:
            path = os.path.join(folder, name)
            try:
                if not min_age_hours or os.stat(path).st_mtime <= cutoff:
                    found.add(_norm(path))
            except OSError:
                continue
    return found


def _prune_empty_dirs(root: str, allowed: set[str]) -> None:
    """Remove empty sub-folders from `allowed` (never the root itself, never links)."""
    for folder, _dirs, _files in os.walk(root, topdown=False, followlinks=False):
        if _norm(folder) == _norm(root) or _norm(folder) not in allowed or os.path.islink(folder):
            continue
        try:
            os.rmdir(folder)  # only succeeds when the folder is empty
        except OSError:
            continue


def clean_target(target: CleanTarget, dry_run: bool = True) -> dict[str, Any]:
    """Clean one target. DRY-RUN BY DEFAULT: with dry_run=True nothing is deleted.

    Returns the scan result plus: dry_run, deleted, freed_bytes, skipped.
    """
    res = scan_target(target)
    res.update(dry_run=dry_run, deleted=0, freed_bytes=0, freed_text=format_bytes(0), skipped=0)
    if dry_run or not res["available"] or res["refused"]:
        return res

    if target.kind == "recycle":
        script = recycle_clear_script(_fixed_drives())
        if res["files"] > 0 and script:
            run_powershell(script, timeout=120)
        files_after, bytes_after, _ = _measure_recycle()
        res["deleted"] = max(0, res["files"] - files_after)
        res["freed_bytes"] = max(0, res["bytes"] - bytes_after)
    elif target.kind == "dns":
        before = res["files"]
        if before > 0:
            run_cmd(["ipconfig", "/flushdns"], timeout=20)
        after = _measure_dns()
        res["deleted"] = max(0, before - after) if after is not None else 0
        if before > 0 and res["deleted"] == 0:
            res["errors"].append("DNS cache was not cleared (needs an Administrator terminal)")
    else:
        for root in target.roots:
            if not root or check_root(root) or not os.path.isdir(root):
                continue
            prune = not target.patterns
            allowed = _old_dirs(root, target.min_age_hours) if prune else set()
            for path, size in list(iter_files(root, target.patterns, target.min_age_hours)):
                if _delete_file(path):
                    res["deleted"] += 1
                    res["freed_bytes"] += size
                else:
                    res["skipped"] += 1
            if prune:
                _prune_empty_dirs(root, allowed)
    res["skipped"] = res["skipped"] if target.kind == "folder" else max(0, res["files"] - res["deleted"])
    res["freed_text"] = format_bytes(res["freed_bytes"])
    return res
