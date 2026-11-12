"""Windows System Restore points: a safety net before LapForge changes anything.

  list_restore_points()       what restore points exist now
  create_restore_point(...)   make one (dry-run by default; needs Administrator)

LapForge never deletes or restores a restore point. To go back to one, use
Windows: press Win+R, type `rstrui`, press Enter.

Windows normally allows ONE new restore point per 24 hours. If a recent one
already exists it is reported as protection ("recent"), not as a failure.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Callable

from ..utils import IS_WINDOWS, NA, as_list, is_admin, powershell_json, run_powershell

RECENT_HOURS = 24
MAX_DESCRIPTION = 64
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9 ._()\-:]")

_LIST_SCRIPT = (
    "Get-ComputerRestorePoint -ErrorAction SilentlyContinue | "
    "Select-Object SequenceNumber, Description, "
    "@{n='Created';e={$_.ConvertToDateTime($_.CreationTime).ToString('s')}} | "
    "ConvertTo-Json -Compress"
)

ListFn = Callable[[], list[dict[str, Any]]]


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def clean_description(text: str) -> str:
    """Make a description safe to put inside a PowerShell string (letters, digits, a few symbols)."""
    cleaned = _SAFE_CHARS.sub("", str(text or "")).strip()
    return (cleaned or "LapForge restore point")[:MAX_DESCRIPTION]


def parse_restore_points(raw: Any) -> list[dict[str, Any]]:
    """PowerShell's JSON -> list of {"sequence", "description", "created", "age_hours"}, newest first."""
    points: list[dict[str, Any]] = []
    now = datetime.now()
    for row in as_list(raw):
        if not isinstance(row, dict):
            continue
        try:
            sequence = int(row.get("SequenceNumber"))
        except (TypeError, ValueError):
            continue
        created_text = str(row.get("Created") or "")
        try:
            created = datetime.fromisoformat(created_text)
            age = max(0.0, (now - created).total_seconds() / 3600)
        except ValueError:
            created, age = None, None
        points.append(
            {
                "sequence": sequence,
                "description": str(row.get("Description") or NA),
                "created": created.strftime("%Y-%m-%d %H:%M") if created else NA,
                "age_hours": round(age, 1) if age is not None else None,
            }
        )
    points.sort(key=lambda p: p["sequence"], reverse=True)
    return points


def recent_point(points: list[dict[str, Any]], hours: float = RECENT_HOURS) -> dict[str, Any] | None:
    """Newest restore point younger than `hours`, or None."""
    for point in points:
        if point["age_hours"] is not None and point["age_hours"] <= hours:
            return point
    return None


def interpret_checkpoint(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the restore points before and after a create attempt.

    Returns {"created", "protected", "point", "reason"}. `protected` is True when a restore point
    made now, or within the last 24 hours, exists.
    """
    known = {p["sequence"] for p in before}
    fresh = [p for p in after if p["sequence"] not in known]
    if fresh:
        return {"created": True, "protected": True, "point": fresh[0], "reason": "A new restore point was created."}
    recent = recent_point(after)
    if recent:
        return {
            "created": False,
            "protected": True,
            "point": recent,
            "reason": (
                f"No new restore point (Windows allows one every {RECENT_HOURS} hours), "
                f"but one from {recent['created']} already covers this change."
            ),
        }
    return {
        "created": False,
        "protected": False,
        "point": None,
        "reason": "Windows did not create a restore point. System Protection is probably turned off for the C: drive "
        "(Settings > System > About > System protection).",
    }


# --------------------------------------------------------------------------
# Reading and creating
# --------------------------------------------------------------------------
def list_restore_points(limit: int = 10) -> list[dict[str, Any]]:
    """Existing restore points, newest first. Empty on other systems or when Windows will not say."""
    if not IS_WINDOWS:
        return []
    try:
        return parse_restore_points(powershell_json(_LIST_SCRIPT, timeout=60))[: max(1, limit)]
    except Exception:
        return []


def _result(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": False,
        "dry_run": True,
        "supported": IS_WINDOWS,
        "created": False,
        "protected": False,
        "point": None,
        "description": "",
        "reason": "",
    }
    base.update(extra)
    return base


def create_restore_point(
    description: str = "LapForge restore point",
    dry_run: bool = True,
    list_points: ListFn | None = None,
    run_script: Callable[[str, int], str] | None = None,
    is_admin_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Create a System Restore point. DRY-RUN BY DEFAULT: nothing is created unless dry_run=False.

    Returns {"ok", "dry_run", "supported", "created", "protected", "point", "description", "reason"}.
    `ok` means a restore point now exists that covers a change (new, or one from the last 24 hours).
    The last three arguments are for tests.
    """
    text = clean_description(description)
    if not IS_WINDOWS and run_script is None:
        return _result(description=text, reason="System Restore needs Windows (not available on this system).")
    if not (is_admin_fn or is_admin)():
        return _result(description=text, dry_run=dry_run, reason="Creating a restore point needs an Administrator terminal.")
    lister = list_points or (lambda: list_restore_points(50))
    if dry_run:
        return _result(
            ok=True,
            dry_run=True,
            description=text,
            point=recent_point(lister()),
            reason=f'Dry run: would create the restore point "{text}".',
        )

    before = lister()
    script = f"Checkpoint-Computer -Description '{text}' -RestorePointType MODIFY_SETTINGS -ErrorAction Stop"
    try:
        (run_script or run_powershell)(script, 180)
    except Exception as exc:  # the runner should not raise, but a restore point must never crash a caller
        return _result(dry_run=False, description=text, reason=f"Could not run Windows' restore point command: {exc}")
    time.sleep(0 if run_script else 1)  # Windows needs a moment to list the new point
    verdict = interpret_checkpoint(before, lister())
    return _result(
        ok=verdict["protected"],
        dry_run=False,
        description=text,
        created=verdict["created"],
        protected=verdict["protected"],
        point=verdict["point"],
        reason=verdict["reason"],
    )


def restore_status() -> dict[str, Any]:
    """Short summary for screens: {"supported", "count", "latest", "recent"}."""
    points = list_restore_points(20)
    return {
        "supported": IS_WINDOWS,
        "count": len(points),
        "latest": points[0] if points else None,
        "recent": recent_point(points),
        "note": "" if IS_WINDOWS else "System Restore needs Windows (not available on this system).",
    }


__all__ = [
    "RECENT_HOURS",
    "clean_description",
    "create_restore_point",
    "interpret_checkpoint",
    "list_restore_points",
    "parse_restore_points",
    "recent_point",
    "restore_status",
]
