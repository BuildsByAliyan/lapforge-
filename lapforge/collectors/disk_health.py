"""Disk health: Windows storage health, SMART failure prediction, SSD wear, temperature."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from ..utils import IS_WINDOWS, as_list, clean_text, format_bytes, powershell_json

_DISK_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$rows = @()
foreach ($d in (Get-PhysicalDisk)) {
    $r = $d | Get-StorageReliabilityCounter
    $rows += [ordered]@{
        name = $d.FriendlyName
        media = "$($d.MediaType)"
        bus = "$($d.BusType)"
        size = $d.Size
        health = "$($d.HealthStatus)"
        operational = "$($d.OperationalStatus)"
        temp = $r.Temperature
        wear = $r.Wear
        hours = $r.PowerOnHours
        read_errors = $r.ReadErrorsTotal
        write_errors = $r.WriteErrorsTotal
    }
}
$pf = @(Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_FailurePredictStatus)
$predict = $null
if ($pf.Count -gt 0) { $predict = (@($pf | Where-Object { $_.PredictFailure }).Count -gt 0) }
[ordered]@{ disks = @($rows); predict_failure = $predict } | ConvertTo-Json -Depth 4 -Compress
"""

_RANK = {"good": 0, "muted": 0, "warn": 1, "bad": 2}
_STATUS_LABEL = {"good": "Healthy", "warn": "Warning", "bad": "Failing", "muted": "Unknown"}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def assess_disk(
    health: str,
    predict_failure: bool | None,
    wear: float | None,
    temp: float | None,
    read_errors: float | None = None,
    write_errors: float | None = None,
) -> tuple[str, list[str]]:
    """Turn raw disk readings into a level ('good'/'warn'/'bad'/'muted') plus reasons."""
    level = "good"
    reasons: list[str] = []

    def raise_to(new: str, reason: str) -> None:
        nonlocal level
        reasons.append(reason)
        if _RANK[new] > _RANK[level]:
            level = new

    if predict_failure:
        raise_to("bad", "SMART predicts this drive will fail - back up your data now")
    low = (health or "").lower()
    if low == "unhealthy":
        raise_to("bad", "Windows reports the drive as Unhealthy")
    elif low == "warning":
        raise_to("warn", "Windows reports a drive Warning")
    if wear is not None:
        if wear >= 90:
            raise_to("bad", f"SSD life almost used up ({wear:.0f}% worn)")
        elif wear >= 70:
            raise_to("warn", f"SSD is {wear:.0f}% worn")
    if temp is not None:
        if temp >= 70:
            raise_to("bad", f"Drive is very hot ({temp:.0f} C)")
        elif temp >= 60:
            raise_to("warn", f"Drive is running warm ({temp:.0f} C)")
    errors = (read_errors or 0) + (write_errors or 0)
    if errors >= 50:
        raise_to("warn", f"{errors:.0f} read/write errors logged")

    if level == "good" and low not in ("healthy", "warning", "unhealthy") and predict_failure is None:
        return "muted", reasons
    return level, reasons


@lru_cache(maxsize=1)
def get_disk_health() -> dict[str, Any]:
    """Per-drive health report.

    Returns {"available": bool, "disks": [...], "predict_failure": bool|None, "note": str}.
    Temperature, wear and SMART data may need an Administrator terminal; missing
    values are reported as None / "N/A".
    """
    result: dict[str, Any] = {"available": False, "disks": [], "predict_failure": None, "note": ""}
    if not IS_WINDOWS:
        result["note"] = "Detailed disk health is only available on Windows."
        return result

    data = powershell_json(_DISK_SCRIPT, timeout=60)
    if not isinstance(data, dict):
        result["note"] = "Could not read disk health from Windows."
        return result

    predict = data.get("predict_failure")
    result["predict_failure"] = predict if isinstance(predict, bool) else None
    for raw in as_list(data.get("disks")):
        wear, temp = _num(raw.get("wear")), _num(raw.get("temp"))
        hours = _num(raw.get("hours"))
        health = clean_text(raw.get("health"))
        level, reasons = assess_disk(
            health,
            result["predict_failure"],
            wear,
            temp,
            _num(raw.get("read_errors")),
            _num(raw.get("write_errors")),
        )
        result["disks"].append(
            {
                "model": clean_text(raw.get("name")),
                "type": clean_text(raw.get("media")),
                "bus": clean_text(raw.get("bus")),
                "size": format_bytes(raw.get("size"), 0),
                "health": health,
                "wear_percent": wear,
                "temperature_c": temp,
                "power_on_hours": int(hours) if hours is not None else None,
                "level": level,
                "status": _STATUS_LABEL[level],
                "reasons": reasons,
            }
        )
    result["available"] = bool(result["disks"])
    if result["available"] and all(
        d["temperature_c"] is None and d["wear_percent"] is None for d in result["disks"]
    ):
        result["note"] = "Temperature and wear need an Administrator terminal."
    if not result["available"]:
        result["note"] = "No drives were reported by Windows."
    return result
