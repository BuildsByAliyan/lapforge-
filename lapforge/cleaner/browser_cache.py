"""Browser cache cleaner for Chrome and Edge.

Only *cache* folders are ever listed. The list of folders is a fixed allow-list,
so cookies, saved passwords, history, bookmarks, extensions and open tabs are
never scanned or deleted. Each target is a normal cleaner target, so it gets the
same safety rules as the rest of the cleaner: dry-run by default, links are not
followed, files in use are skipped and counted.

A browser that is open is skipped by default, and LapForge never closes it for you.
Nothing here deletes anything unless `dry_run=False` is passed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import psutil

from ..utils import format_bytes
from .preview import build_preview
from .temp_files import CleanTarget, clean_target

# (key, display name, path under %LOCALAPPDATA%, process names)
_BROWSERS: tuple[tuple[str, str, tuple[str, ...], frozenset[str]], ...] = (
    ("chrome", "Chrome", ("Google", "Chrome", "User Data"), frozenset({"chrome.exe", "chrome"})),
    ("edge", "Edge", ("Microsoft", "Edge", "User Data"), frozenset({"msedge.exe", "msedge"})),
)

# The ONLY folders inside a browser profile that may be cleaned.
CACHE_SUBDIRS: tuple[tuple[str, ...], ...] = (
    ("Cache",),
    ("Code Cache",),
    ("GPUCache",),
    ("DawnGraphiteCache",),
    ("DawnWebGPUCache",),
    ("Service Worker", "CacheStorage"),
    ("Service Worker", "ScriptCache"),
)

# Names that hold personal data. If one ever shows up in a path, it is refused.
NEVER_TOUCH: frozenset[str] = frozenset(
    {
        "cookies",
        "login data",
        "login data for account",
        "history",
        "web data",
        "network",
        "local storage",
        "session storage",
        "indexeddb",
        "bookmarks",
        "preferences",
        "secure preferences",
        "favicons",
        "top sites",
        "extensions",
        "sessions",
        "autofill",
        "passwords",
    }
)

_PROFILE_RE = re.compile(r"^(default|profile \d+)$", re.IGNORECASE)


# --------------------------------------------------------------------------
# Finding browsers and profiles
# --------------------------------------------------------------------------
def _user_data_dir(parts: tuple[str, ...]) -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    path = Path(local).joinpath(*parts)
    return path if path.is_dir() else None


def _profiles(user_data: Path) -> list[str]:
    """Profile folder names: 'Default', 'Profile 1', 'Profile 2', ..."""
    try:
        names = [e.name for e in os.scandir(user_data) if e.is_dir(follow_symlinks=False) and _PROFILE_RE.match(e.name)]
    except OSError:
        return []
    return sorted(names, key=lambda n: (n.lower() != "default", n.lower()))


def detect_browsers() -> list[dict[str, Any]]:
    """Installed Chromium browsers that have at least one profile on this PC."""
    found: list[dict[str, Any]] = []
    for key, name, parts, _procs in _BROWSERS:
        user_data = _user_data_dir(parts)
        if user_data is None:
            continue
        profiles = _profiles(user_data)
        if profiles:
            found.append({"key": key, "name": name, "user_data": str(user_data), "profiles": profiles})
    return found


def running_browsers() -> set[str]:
    """Keys ('chrome', 'edge') of browsers that are open right now."""
    wanted = {key: procs for key, _n, _p, procs in _BROWSERS}
    running: set[str] = set()
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            for key, procs in wanted.items():
                if name in procs:
                    running.add(key)
    except Exception:
        pass
    return running


def browser_name(key: str) -> str:
    """Display name for a browser key ('chrome' -> 'Chrome')."""
    for k, name, _parts, _procs in _BROWSERS:
        if k == key:
            return name
    return key.title()


def open_browser_keys(installed: list[str], running: set[str]) -> list[str]:
    """Browsers that are both installed (have a profile here) and open right now. Pure."""
    return [key for key in installed if key in running]


def open_browser_notes(keys: list[str]) -> list[str]:
    """One plain sentence per open browser, shown when its cache is skipped. Pure."""
    return [f"{browser_name(key)} is open. Close it and run Clean again to include its cache." for key in keys]


def detected_open_browsers(running: set[str] | None = None) -> list[str]:
    """Keys of installed browsers that are open now (their cache is skipped by default)."""
    now = running_browsers() if running is None else running
    return open_browser_keys([b["key"] for b in detect_browsers()], now)


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------
def is_cache_folder(path: str) -> bool:
    """True only if `path` ends in one of the allow-listed cache folders of a
    browser profile and contains no personal-data folder name."""
    parts = [p.lower() for p in Path(path).parts]
    if any(p in NEVER_TOUCH for p in parts):
        return False
    for sub in CACHE_SUBDIRS:
        tail = [s.lower() for s in sub]
        if parts[-len(tail):] == tail and len(parts) > len(tail) and _PROFILE_RE.match(parts[-len(tail) - 1]):
            return True
    return False


def _safe_target(target: CleanTarget) -> bool:
    return bool(target.roots) and all(is_cache_folder(root) for root in target.roots)


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------
def browser_targets(open_browsers: set[str] | None = None, skip_open: bool = True) -> list[CleanTarget]:
    """One cleaner target per browser profile (cache folders only).

    A browser that is open is SKIPPED by default (its cache files are in use, and
    cleaning them half-way is confusing). Pass skip_open=False to list it anyway,
    with a warning. No process is ever closed by LapForge.
    """
    open_now = running_browsers() if open_browsers is None else open_browsers
    targets: list[CleanTarget] = []
    for browser in detect_browsers():
        is_open = browser["key"] in open_now
        if is_open and skip_open:
            continue
        for profile in browser["profiles"]:
            base = Path(browser["user_data"]) / profile
            roots = tuple(str(base.joinpath(*sub)) for sub in CACHE_SUBDIRS if base.joinpath(*sub).is_dir())
            if not roots:
                continue
            warning = f"{browser['name']} is open - close it for a full clean (files in use are skipped)." if is_open else ""
            slug = profile.lower().replace(" ", "_")
            targets.append(
                CleanTarget(
                    key=f"{browser['key']}_{slug}_cache",
                    label=f"{browser['name']} cache ({profile})",
                    description="Saved web pages and images. Cookies, passwords and history are NOT touched.",
                    profiles=("browser",),
                    roots=roots,
                    warning=warning,
                )
            )
    return [t for t in targets if _safe_target(t)]


def browser_cache_available() -> bool:
    """True if Chrome or Edge profiles were found on this PC."""
    return bool(detect_browsers())


# --------------------------------------------------------------------------
# Preview and clean
# --------------------------------------------------------------------------
def preview_browser_cache(targets: list[CleanTarget] | None = None) -> dict[str, Any]:
    """Dry-run measurement of browser caches. Deletes nothing."""
    chosen = browser_targets() if targets is None else [t for t in targets if _safe_target(t)]
    preview = build_preview("browser", chosen)
    preview["supported"] = True
    preview["browsers"] = [b["name"] for b in detect_browsers()]
    return preview


def clean_browser_cache(dry_run: bool = True, targets: list[CleanTarget] | None = None) -> dict[str, Any]:
    """Clean browser caches. DRY-RUN BY DEFAULT: nothing is deleted unless dry_run=False.

    Returns {"dry_run", "items", "deleted", "freed_bytes", "freed_text", "skipped", "open_browsers",
    "skipped_browsers"}. Browsers that are open are skipped (see browser_targets).
    """
    chosen = browser_targets() if targets is None else targets  # open browsers are skipped
    chosen = [t for t in chosen if _safe_target(t)]  # last line of defence
    items = [clean_target(t, dry_run=dry_run) for t in chosen]
    freed = sum(i["freed_bytes"] for i in items)
    return {
        "dry_run": dry_run,
        "items": items,
        "deleted": sum(i["deleted"] for i in items),
        "freed_bytes": freed,
        "freed_text": format_bytes(freed),
        "skipped": sum(i["skipped"] for i in items),
        "open_browsers": sorted(running_browsers()),
        "skipped_browsers": detected_open_browsers(),
    }
