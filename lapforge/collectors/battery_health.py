"""Battery health: design vs. current full-charge capacity and wear percentage."""

from __future__ import annotations

import os
import re
import tempfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from typing import Any

import psutil

from ..utils import IS_WINDOWS, NA, clean_text, powershell_json, run_cmd

_CHEMISTRY = {
    1: "Other",
    2: "Unknown",
    3: "Lead Acid",
    4: "NiCd",
    5: "NiMH",
    6: "Li-ion",
    7: "Zinc Air",
    8: "Li-polymer",
}

_WMI_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$d = Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData | Select-Object -First 1
$f = Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity | Select-Object -First 1
$c = Get-CimInstance -Namespace root\wmi -ClassName BatteryCycleCount | Select-Object -First 1
$w = Get-CimInstance Win32_Battery | Select-Object -First 1
[ordered]@{
    design = $d.DesignedCapacity
    full = $f.FullChargedCapacity
    cycles = $c.CycleCount
    chemistry = $w.Chemistry
} | ConvertTo-Json -Compress
"""


def compute_wear(design: float | None, full: float | None) -> dict[str, Any]:
    """Wear and health percentages from design and current full-charge capacity.

    Returns {"wear_percent": None, "health_percent": None} when the numbers are
    missing or unusable. A full capacity above design counts as 0% wear.
    """
    try:
        d, f = float(design), float(full)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {"wear_percent": None, "health_percent": None}
    if d <= 0 or f <= 0:
        return {"wear_percent": None, "health_percent": None}
    health = min(100.0, f / d * 100)
    return {"wear_percent": round(100 - health, 1), "health_percent": round(health, 1)}


def wear_level(wear_percent: float | None) -> str:
    """'good' (<20% wear), 'warn' (<40%), 'bad' (40%+), 'muted' if unknown."""
    if wear_percent is None:
        return "muted"
    if wear_percent < 20:
        return "good"
    if wear_percent < 40:
        return "warn"
    return "bad"


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value).replace(",", ""))
    return int(match.group()) if match else None


def parse_battery_report_xml(raw: bytes) -> dict[str, int | None]:
    """Pull capacities and cycle count out of a `powercfg /batteryreport /xml` file."""
    result: dict[str, int | None] = {"design": None, "full": None, "cycles": None}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return result
    wanted = {"DesignCapacity": "design", "FullChargeCapacity": "full", "CycleCount": "cycles"}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in wanted and result[wanted[tag]] is None:
            result[wanted[tag]] = _to_int(element.text)
    return result


def _from_powercfg() -> dict[str, int | None]:
    """Fallback source for PCs where the WMI battery classes are blocked."""
    empty: dict[str, int | None] = {"design": None, "full": None, "cycles": None}
    if not IS_WINDOWS:
        return empty
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="lapforge_batt_")
    os.close(fd)
    try:
        run_cmd(["powercfg", "/batteryreport", "/xml", "/output", path, "/duration", "1"], timeout=40)
        with open(path, "rb") as handle:
            raw = handle.read()
        return parse_battery_report_xml(raw) if raw else empty
    except OSError:
        return empty
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@lru_cache(maxsize=1)
def get_battery_health() -> dict[str, Any]:
    """Battery wear report. `present` is False on desktops / when no battery exists."""
    result: dict[str, Any] = {
        "present": False,
        "design_mwh": None,
        "full_mwh": None,
        "wear_percent": None,
        "health_percent": None,
        "level": "muted",
        "cycles": NA,
        "chemistry": NA,
        "source": NA,
    }
    try:
        if psutil.sensors_battery() is None:
            return result
    except Exception:
        return result
    result["present"] = True
    if not IS_WINDOWS:
        return result

    design = full = cycles = None
    data = powershell_json(_WMI_SCRIPT, timeout=30)
    if isinstance(data, dict):
        design, full, cycles = _to_int(data.get("design")), _to_int(data.get("full")), _to_int(data.get("cycles"))
        result["chemistry"] = _CHEMISTRY.get(_to_int(data.get("chemistry")), NA)
        if design and full:
            result["source"] = "Windows WMI"
    if not (design and full):
        report = _from_powercfg()
        design, full = report["design"], report["full"]
        cycles = cycles or report["cycles"]
        if design and full:
            result["source"] = "powercfg battery report"

    result["design_mwh"], result["full_mwh"] = design or None, full or None
    result.update(compute_wear(design, full))
    result["level"] = wear_level(result["wear_percent"])
    # Many laptops report 0 cycles when they do not track them.
    result["cycles"] = str(cycles) if cycles else NA
    result["chemistry"] = clean_text(result["chemistry"])
    return result
