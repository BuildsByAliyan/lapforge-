"""Before/after report for a clean run, shown on screen and written to the log.

`disk_snapshot()` is taken before and after cleaning; `build_clean_report()` puts
both together with what each cleaner target did.
"""

from __future__ import annotations

import os
import time
from typing import Any

import psutil
from rich.console import Console
from rich.text import Text

from ..ui.tables import grid_table, kv_table
from ..ui.theme import status_text, usage_bar
from ..utils import IS_WINDOWS, NA, format_bytes


def disk_snapshot(path: str | None = None) -> dict[str, Any]:
    """Space on the system drive (or on `path`) right now."""
    if path is None:
        path = (os.environ.get("SystemDrive", "C:") + "\\") if IS_WINDOWS else os.path.abspath(os.sep)
    snap: dict[str, Any] = {
        "available": False,
        "mount": path,
        "total": 0,
        "used": 0,
        "free": 0,
        "percent": None,
        "total_text": NA,
        "used_text": NA,
        "free_text": NA,
        "time": time.strftime("%H:%M:%S"),
    }
    try:
        usage = psutil.disk_usage(path)
    except (OSError, ValueError):
        return snap
    snap.update(
        available=True,
        total=usage.total,
        used=usage.used,
        free=usage.free,
        percent=usage.percent,
        total_text=format_bytes(usage.total),
        used_text=format_bytes(usage.used),
        free_text=format_bytes(usage.free),
    )
    return snap


def _item_level(item: dict[str, Any]) -> tuple[str, str]:
    """(level, label) for one cleaned target."""
    if item.get("refused"):
        return "bad", "Skipped for safety"
    if not item.get("available"):
        return "muted", "Not available"
    if item["key"] == "dns_cache":
        return ("good", "Flushed") if item["deleted"] else ("muted", "Nothing to clean")
    if item["deleted"] == 0 and item["files"] == 0:
        return "muted", "Nothing to clean"
    if item["deleted"] == 0:
        return "warn", "Nothing could be removed"
    if item["skipped"]:
        return "warn", "Partly cleaned"
    return "good", "Cleaned"


def _signed_bytes(value: int | None) -> str:
    """1536 -> '+1.5 KB', -1536 -> '-1.5 KB', None -> N/A."""
    if value is None:
        return NA
    return ("+" if value > 0 else "-" if value < 0 else "") + format_bytes(abs(value))


def build_clean_report(
    profile: str,
    results: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    started: float,
    finished: float | None = None,
) -> dict[str, Any]:
    """Combine the per-target results and the two disk snapshots into one report."""
    finished = finished if finished is not None else time.time()
    items = []
    for res in results:
        level, label = _item_level(res)
        items.append({**res, "level": level, "state": label})
    freed = sum(i["freed_bytes"] for i in items)
    deleted = sum(i["deleted"] for i in items if i["key"] != "dns_cache")
    skipped = sum(i["skipped"] for i in items if i["key"] != "dns_cache")
    delta = (after["free"] - before["free"]) if before["available"] and after["available"] else None
    errors = [f"{i['label']}: {e}" for i in items for e in i["errors"]]
    return {
        "profile": profile,
        "items": items,
        "before": before,
        "after": after,
        "freed_bytes": freed,
        "freed_text": format_bytes(freed),
        "deleted_files": deleted,
        "skipped_files": skipped,
        "free_change": delta,
        "free_change_text": _signed_bytes(delta),
        "errors": errors,
        "seconds": round(finished - started, 1),
    }


# --------------------------------------------------------------------------
# On screen
# --------------------------------------------------------------------------
def before_after_table(report: dict[str, Any]):
    """Disk space before and after the clean."""
    before, after = report["before"], report["after"]
    table = grid_table(f"Before / After - drive {before['mount']}", ["", "Before", "After", "Change"])
    if not (before["available"] and after["available"]):
        table = kv_table("Before / After", [("Disk space", "Not available on this system")])
        return table
    table.add_row("Free space", before["free_text"], after["free_text"], Text(report["free_change_text"], style="good" if (report["free_change"] or 0) > 0 else "muted"))
    table.add_row("Used space", before["used_text"], after["used_text"], "")
    table.add_row("Used", usage_bar(before["percent"], 14), usage_bar(after["percent"], 14), "")
    return table


def results_table(report: dict[str, Any]):
    """What each cleaner target removed."""
    table = grid_table("Clean results", ["Item", "Removed", "Freed", "Skipped", "Status"])
    for item in report["items"]:
        if item["key"] == "dns_cache" and item["available"]:
            removed, freed = f"{item['deleted']:,} entries", "-"
            skipped = "-"
        else:
            removed = f"{item['deleted']:,}" if item["available"] else "-"
            freed = item["freed_text"] if item["available"] else "-"
            skipped = f"{item['skipped']:,}" if item["available"] else "-"
        table.add_row(item["label"], removed, freed, skipped, status_text(item["level"], item["state"]))
    return table


def render_clean_report(console: Console, report: dict[str, Any]) -> None:
    """Print the full report: results, before/after and a one-line summary."""
    console.print(results_table(report))
    console.print(before_after_table(report))
    console.print(
        f"  [good]Freed about {report['freed_text']}[/good] "
        f"[muted]({report['deleted_files']:,} files removed in {report['seconds']}s).[/muted]"
    )
    if report["skipped_files"]:
        console.print(
            f"  [muted]{report['skipped_files']:,} files were in use or protected and were left alone. "
            "Closing apps and running as Administrator removes more.[/muted]"
        )
    for error in report["errors"][:5]:
        console.print(f"  [warn]- {error}[/warn]")


# --------------------------------------------------------------------------
# For the log file
# --------------------------------------------------------------------------
def report_lines(report: dict[str, Any]) -> list[str]:
    """Plain-text lines describing the report (no individual file names)."""
    before, after = report["before"], report["after"]
    lines = [f"Profile : {report['profile']}", f"Duration: {report['seconds']}s", ""]
    for item in report["items"]:
        if item["key"] == "dns_cache":
            detail = f"{item['deleted']} entries flushed"
        else:
            detail = f"removed {item['deleted']} files, freed {item['freed_text']}, skipped {item['skipped']}"
        note = f" ({item['note']})" if item.get("note") else ""
        lines.append(f"- {item['label']}: {item['state']} - {detail}{note}")
    lines.append("")
    if before["available"] and after["available"]:
        lines.append(f"Drive {before['mount']} free space: {before['free_text']} -> {after['free_text']} (change {report['free_change_text']})")
    lines.append(f"Total freed (reported): {report['freed_text']} in {report['deleted_files']} files; skipped {report['skipped_files']}")
    for error in report["errors"]:
        lines.append(f"! {error}")
    return lines
