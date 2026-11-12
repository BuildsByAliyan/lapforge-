"""Dry-run preview: how many files and how much space a clean would free.

Nothing here deletes anything. The `lapforge clean` command (a later phase)
shows this preview first and only cleans after you confirm.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from rich.console import Console
from rich.text import Text

from ..ui.tables import grid_table, kv_table
from ..ui.theme import make_console, status_text
from ..utils import format_bytes, is_admin
from .temp_files import CleanTarget, cleaner_supported, get_targets, scan_target


def item_state(item: dict[str, Any], admin: bool) -> tuple[str, str]:
    """(level, label) shown next to one preview row."""
    if item["refused"]:
        return "bad", "Skipped for safety"
    if not item["available"]:
        return "muted", "Not available"
    if item["needs_admin"] and not admin:
        return "warn", "Needs Administrator" if item["files"] == 0 else "Partly (needs Administrator)"
    if item["files"] == 0:
        return "muted", "Nothing to clean"
    return "good", "Ready"


def build_preview(profile: str = "quick", targets: list[CleanTarget] | None = None) -> dict[str, Any]:
    """Scan every target of a profile (or the given targets) without deleting anything.

    Returns {"profile", "dry_run", "admin", "supported", "items", "total_files", "total_bytes", "total_text"}.
    """
    chosen = targets if targets is not None else get_targets(profile)
    admin = is_admin()
    items: list[dict[str, Any]] = []
    if chosen:
        with ThreadPoolExecutor(max_workers=min(4, len(chosen))) as pool:
            items = list(pool.map(scan_target, chosen))
    for item in items:
        level, label = item_state(item, admin)
        item["level"], item["state"] = level, label
    usable = [i for i in items if i["available"] and not i["refused"]]
    total_files = sum(i["files"] for i in usable if i["key"] != "dns_cache")  # DNS entries are not files
    total_bytes = sum(i["bytes"] for i in usable)
    return {
        "profile": profile,
        "dry_run": True,
        "admin": admin,
        "supported": targets is not None or cleaner_supported(),
        "items": items,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_text": format_bytes(total_bytes),
    }


def preview_table(preview: dict[str, Any]):
    """Rich table of the preview (item, files, size, status)."""
    title = f"Clean preview - {preview['profile']} (dry run)"
    table = grid_table(title, ["Item", "Files", "Size", "Status", "Note"])
    for item in preview["items"]:
        if item["key"] == "dns_cache" and item["available"]:
            files, size = f"{item['files']:,} entries", "-"
        else:
            files = f"{item['files']:,}" if item["available"] else "-"
            size = item["bytes_text"] if item["available"] else "-"
        note = item["note"] if item["key"] != "dns_cache" or not item["available"] else ""
        if item["warning"] and item["files"]:
            note = (note + " " if note else "") + item["warning"]
        table.add_row(
            item["label"],
            files,
            size,
            status_text(item["level"], item["state"]),
            Text(note, style="muted"),
        )
    return table


def preview_all(profile: str = "full", console: Console | None = None) -> dict[str, Any]:
    """Print the dry-run preview for a profile ('quick' or 'full') and return it.

    Default is 'full' so the test command shows every item. Nothing is deleted.
    """
    console = console or make_console()
    if not cleaner_supported():
        console.print(kv_table("Clean preview", [("Status", "The cleaner needs Windows (not available on this system).")]))
        return build_preview(profile)
    with console.status("[accent]Measuring what can be cleaned...[/accent]", spinner="dots"):
        preview = build_preview(profile)
    console.print(preview_table(preview))
    console.print(
        f"  [accent]Can free about {preview['total_text']}[/accent] "
        f"[muted]in {preview['total_files']:,} files.[/muted]  [good]DRY RUN - nothing was deleted.[/good]"
    )
    console.print("  [muted]Your Documents, Pictures, Videos, Desktop and Downloads are never touched.[/muted]")
    if not preview["admin"]:
        console.print("  [muted]Tip: run in an Administrator terminal to include Windows temp, Prefetch and the update cache fully.[/muted]")
    return preview
