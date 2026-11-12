"""Duplicate file finder (read-only).

Finds files with identical content: first by size, then by a quick hash of the
start of the file, then by a full SHA-256. This module can only *report*. It has
no delete function on purpose: you decide what to remove yourself.

You must name the folders to scan. Drive roots and Windows / Program Files
folders are refused. Links and junctions are never followed.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


from ..ui.tables import grid_table
from ..utils import format_bytes

DEFAULT_MIN_SIZE = 1024 * 1024  # ignore files smaller than 1 MB
DEFAULT_MAX_FILES = 200_000
_QUICK_BYTES = 64 * 1024
_CHUNK = 1024 * 1024
_REPARSE_POINT = 0x400
_MAX_ERRORS_KEPT = 5

# Folders that are never worth scanning (system data, trash).
_SKIP_DIRS = frozenset({"$recycle.bin", "system volume information", "windows.old", "$windows.~bt", "$windows.~ws"})

ProgressFn = Callable[[str, int, int], None]  # (stage, done, total)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _system_roots() -> set[str]:
    roots: set[str] = set()
    for var in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(var)
        if value:
            roots.add(_norm(value))
    return roots


def refuse_reason(root: str) -> str:
    """'' if `root` may be scanned, otherwise why it is refused."""
    if not root:
        return "No folder given"
    path = Path(root)
    if path.parent == path:
        return "Refused: drive root (pick a folder)"
    target = _norm(root)
    for guard in _system_roots():
        if target == guard or target.startswith(guard + os.sep):
            return "Refused: Windows / program folder"
    return ""


def _is_link(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & _REPARSE_POINT)
    except OSError:
        return True


def _walk(root: str, errors: list[str]) -> Iterator[tuple[str, os.stat_result]]:
    """Yield (path, stat) for regular files under `root` without following links."""
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
                            if entry.name.lower() not in _SKIP_DIRS:
                                stack.append(entry.path)
                            continue
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        yield entry.path, info
        except OSError as exc:
            if len(errors) < _MAX_ERRORS_KEPT:
                errors.append(f"{folder}: {exc.strerror or exc}")


def _hash_file(path: str, limit: int | None = None) -> str | None:
    """SHA-256 of a file (or of its first `limit` bytes). None if it cannot be read."""
    digest = hashlib.sha256()
    remaining = limit
    try:
        with open(path, "rb") as handle:
            while True:
                size = _CHUNK if remaining is None else min(_CHUNK, remaining)
                if size <= 0:
                    break
                block = handle.read(size)
                if not block:
                    break
                digest.update(block)
                if remaining is not None:
                    remaining -= len(block)
    except OSError:
        return None
    return digest.hexdigest()


def _note(on_progress: ProgressFn | None, stage: str, done: int, total: int) -> None:
    if on_progress is not None:
        on_progress(stage, done, total)


# --------------------------------------------------------------------------
# Finder
# --------------------------------------------------------------------------
def find_duplicates(
    roots: Iterable[str],
    min_size: int = DEFAULT_MIN_SIZE,
    max_files: int = DEFAULT_MAX_FILES,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Find duplicate files under the given folders. Read-only: deletes nothing.

    Returns a dict with:
      groups          list of {hash, size, size_text, count, wasted_bytes, wasted_text, keep, files}
                      (files = [{path, modified}], oldest first; `keep` is only a suggestion)
      duplicate_files extra copies (files you could remove), wasted_bytes / wasted_text
      scanned_files, refused, unreadable, errors, truncated, seconds
    """
    started = time.time()
    min_size = max(1, int(min_size))
    errors: list[str] = []
    refused: list[dict[str, str]] = []
    folders: list[str] = []
    for raw in roots:
        reason = refuse_reason(raw)
        if reason:
            refused.append({"path": str(raw), "reason": reason})
        elif not os.path.isdir(raw):
            refused.append({"path": str(raw), "reason": "Folder not found"})
        else:
            folders.append(os.path.abspath(raw))

    # Stage 1: group by size.
    by_size: dict[int, list[tuple[str, float]]] = defaultdict(list)
    seen_paths: set[str] = set()
    seen_ids: set[tuple[int, int]] = set()  # hard links point at the same data: not duplicates
    scanned = 0
    truncated = False
    for root in folders:
        for path, info in _walk(root, errors):
            if scanned >= max_files:
                truncated = True
                break
            scanned += 1
            if scanned % 500 == 0:
                _note(on_progress, "Scanning files", scanned, 0)
            if info.st_size < min_size:
                continue
            key = _norm(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            if info.st_ino:
                file_id = (info.st_dev, info.st_ino)
                if file_id in seen_ids:
                    continue
                seen_ids.add(file_id)
            by_size[info.st_size].append((path, info.st_mtime))
        if truncated:
            break
    _note(on_progress, "Scanning files", scanned, scanned)

    same_size = {size: files for size, files in by_size.items() if len(files) > 1}
    candidates = sum(len(files) for files in same_size.values())

    # Stage 2: quick hash of the first 64 KB to drop most non-matches cheaply.
    unreadable = 0
    done = 0
    by_quick: dict[tuple[int, str], list[tuple[str, float]]] = defaultdict(list)
    for size, files in same_size.items():
        for path, mtime in files:
            done += 1
            if done % 25 == 0 or done == candidates:
                _note(on_progress, "Quick compare", done, candidates)
            quick = _hash_file(path, _QUICK_BYTES)
            if quick is None:
                unreadable += 1
                continue
            by_quick[(size, quick)].append((path, mtime))

    # Stage 3: full SHA-256 only for files that still look alike.
    survivors = [(size, files) for (size, _q), files in by_quick.items() if len(files) > 1]
    total_full = sum(len(files) for _s, files in survivors)
    done = 0
    by_full: dict[tuple[int, str], list[tuple[str, float]]] = defaultdict(list)
    for size, files in survivors:
        for path, mtime in files:
            done += 1
            _note(on_progress, "Full compare", done, total_full)
            digest = _hash_file(path)
            if digest is None:
                unreadable += 1
                continue
            by_full[(size, digest)].append((path, mtime))

    groups: list[dict[str, Any]] = []
    for (size, digest), files in by_full.items():
        if len(files) < 2:
            continue
        files.sort(key=lambda item: (item[1], item[0].lower()))
        wasted = size * (len(files) - 1)
        groups.append(
            {
                "hash": digest,
                "size": size,
                "size_text": format_bytes(size),
                "count": len(files),
                "wasted_bytes": wasted,
                "wasted_text": format_bytes(wasted),
                "keep": files[0][0],
                "files": [
                    {"path": p, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(m))} for p, m in files
                ],
            }
        )
    groups.sort(key=lambda g: g["wasted_bytes"], reverse=True)
    wasted_total = sum(g["wasted_bytes"] for g in groups)
    return {
        "roots": folders,
        "refused": refused,
        "min_size": min_size,
        "scanned_files": scanned,
        "groups": groups,
        "duplicate_files": sum(g["count"] - 1 for g in groups),
        "wasted_bytes": wasted_total,
        "wasted_text": format_bytes(wasted_total),
        "unreadable": unreadable,
        "errors": errors,
        "truncated": truncated,
        "seconds": round(time.time() - started, 1),
    }


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------
def _short_path(path: str, width: int = 70) -> str:
    return path if len(path) <= width else "..." + path[-(width - 3):]


def duplicates_table(result: dict[str, Any], limit: int = 15):
    """Rich table: biggest duplicate groups first (copies, size, wasted space, first file)."""
    table = grid_table("Duplicate files (report only)", ["#", "Copies", "Each", "Wasted", "Files"])
    for number, group in enumerate(result["groups"][:limit], start=1):
        lines = [_short_path(f["path"]) for f in group["files"][:3]]
        if group["count"] > 3:
            lines.append(f"... and {group['count'] - 3} more")
        table.add_row(str(number), str(group["count"]), group["size_text"], group["wasted_text"], "\n".join(lines))
    return table
