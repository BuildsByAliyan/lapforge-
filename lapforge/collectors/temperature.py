"""Temperature sensors. Returns an empty list (never raises) when none are exposed."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import psutil

from ..utils import IS_WINDOWS, as_list, powershell_json

_THERMAL_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$zones = @(Get-CimInstance Win32_PerfFormattedData_Counters_ThermalZoneInformation |
    Select-Object Name, Temperature)
$acpi = @(Get-CimInstance -Namespace root\wmi -ClassName MSAcpi_ThermalZoneTemperature |
    Select-Object InstanceName, CurrentTemperature)
[ordered]@{ zones = @($zones); acpi = @($acpi) } | ConvertTo-Json -Depth 4 -Compress
"""


def kelvin_to_c(kelvin: float) -> float:
    return round(kelvin - 273.15, 1)


def decikelvin_to_c(value: float) -> float:
    """ACPI thermal zones report tenths of a Kelvin."""
    return kelvin_to_c(value / 10)


def temp_level(celsius: float | None) -> str:
    """'good' below 70 C, 'warn' 70-84 C, 'bad' from 85 C."""
    if celsius is None:
        return "muted"
    if celsius >= 85:
        return "bad"
    if celsius >= 70:
        return "warn"
    return "good"


def _plausible(celsius: float | None) -> bool:
    return celsius is not None and 1 < celsius < 130


def _sensor(label: str, current: float, source: str, high: float | None = None, critical: float | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "current_c": current,
        "high_c": high,
        "critical_c": critical,
        "level": temp_level(current),
        "source": source,
    }


def _windows_sensors() -> list[dict[str, Any]]:
    data = powershell_json(_THERMAL_SCRIPT, timeout=30)
    if not isinstance(data, dict):
        return []
    sensors: list[dict[str, Any]] = []
    for zone in as_list(data.get("zones")):
        try:
            celsius = kelvin_to_c(float(zone.get("Temperature")))
        except (TypeError, ValueError):
            continue
        if _plausible(celsius):
            name = str(zone.get("Name") or "").replace("\\_TZ.", "").replace("\\", "") or "Thermal zone"
            sensors.append(_sensor(f"Thermal zone {name}", celsius, "Windows thermal zone"))
    if sensors:
        return sensors
    for index, zone in enumerate(as_list(data.get("acpi")), start=1):
        try:
            celsius = decikelvin_to_c(float(zone.get("CurrentTemperature")))
        except (TypeError, ValueError):
            continue
        if _plausible(celsius):
            sensors.append(_sensor(f"ACPI zone {index}", celsius, "ACPI"))
    return sensors


def _psutil_sensors() -> list[dict[str, Any]]:
    reader = getattr(psutil, "sensors_temperatures", None)
    if reader is None:
        return []
    try:
        groups = reader()
    except Exception:
        return []
    sensors: list[dict[str, Any]] = []
    for group, entries in groups.items():
        for entry in entries:
            if not _plausible(entry.current):
                continue
            label = f"{group} {entry.label}".strip() if entry.label else group
            sensors.append(_sensor(label, round(entry.current, 1), "psutil", entry.high, entry.critical))
    return sensors


@lru_cache(maxsize=1)
def get_temperatures() -> dict[str, Any]:
    """System temperature sensors: {"available": bool, "sensors": [...], "note": str}."""
    sensors = _windows_sensors() if IS_WINDOWS else _psutil_sensors()
    note = ""
    if not sensors:
        note = "Not available - this laptop does not expose temperature sensors to the OS."
    return {"available": bool(sensors), "sensors": sensors, "note": note}


def hottest(report: dict[str, Any]) -> float | None:
    temps = [s["current_c"] for s in report.get("sensors", [])]
    return max(temps) if temps else None
