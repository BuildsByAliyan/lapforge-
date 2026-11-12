"""Doctor checks: read the system, judge each area, attach a fix hint.

Every check is a plain dict:
    {"id", "category", "label", "level", "value", "hint", "weight"}
level is 'good' / 'warn' / 'bad' / 'muted' (muted = could not be read).
The judge_* functions are pure (data in, checks out), so they are easy to test.
This module only reads - it never changes anything on the PC.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from ..collectors import get_battery_health, get_disk_health, get_memory_info, get_storage_info, get_temperatures
from ..collectors.security import get_security_report
from ..collectors.software import get_startup_apps
from ..collectors.temperature import hottest
from ..utils import IS_WINDOWS, NA
from .fix_hints import get_hint

GIB = 1024**3


@dataclass(frozen=True)
class Thresholds:
    """Limits that decide green / yellow / red. Change them in one place."""

    disk_used_warn: float = 85.0  # percent used
    disk_used_bad: float = 95.0
    disk_free_warn_gb: float = 10.0
    disk_free_bad_gb: float = 5.0
    ram_used_warn: float = 85.0
    ram_used_bad: float = 95.0
    ram_total_ok_gb: float = 7.0  # an 8 GB laptop reports about 7.4-7.9 GiB
    ram_total_low_gb: float = 3.5
    battery_wear_warn: float = 20.0
    battery_wear_bad: float = 40.0
    startup_warn: int = 12
    startup_bad: int = 20
    startup_heavy_warn: int = 3
    temp_warn: float = 80.0
    temp_bad: float = 90.0


# Weight = points lost when the check is red (yellow loses half).
WEIGHTS = {
    "storage.system": 15,
    "storage.other": 5,
    "memory.usage": 6,
    "memory.capacity": 5,
    "battery.wear": 8,
    "startup.count": 8,
    "security.antivirus": 15,
    "security.firewall": 10,
    "security.uac": 5,
    "security.bitlocker": 4,
    "security.secure_boot": 3,
    "security.tpm": 2,
    "disk.health": 20,
    "thermal.temperature": 8,
}

_RANK = {"good": 0, "muted": 0, "warn": 1, "bad": 2}


def make_check(
    check_id: str,
    category: str,
    label: str,
    level: str,
    value: str,
    weight: float,
    **context: Any,
) -> dict[str, Any]:
    """Build one check dict; the fix hint is filled in for yellow/red only."""
    return {
        "id": check_id,
        "category": category,
        "label": label,
        "level": level,
        "value": value,
        "hint": get_hint(check_id, level, **context),
        "weight": weight,
    }


def _unavailable(check_id: str, category: str, label: str, value: str = "Not available") -> dict[str, Any]:
    return make_check(check_id, category, label, "muted", value, 0)


def _worst(*levels: str) -> str:
    return max(levels, key=lambda lv: _RANK[lv])


def _level_by_percent(value: float, warn: float, bad: float) -> str:
    if value >= bad:
        return "bad"
    if value >= warn:
        return "warn"
    return "good"


# --------------------------------------------------------------------------
# Judges (pure functions)
# --------------------------------------------------------------------------
def _is_system_volume(mount: str) -> bool:
    if IS_WINDOWS:
        return mount.upper().startswith(os.environ.get("SystemDrive", "C:").upper())
    return mount == "/"


def judge_storage(volumes: list[dict[str, Any]], t: Thresholds) -> list[dict[str, Any]]:
    """Free space on every real drive (tiny volumes are ignored)."""
    checks: list[dict[str, Any]] = []
    for vol in volumes:
        total, free = vol.get("total") or 0, vol.get("free") or 0
        if total < GIB:
            continue
        mount = str(vol.get("mount", "?"))
        used = float(vol.get("percent") or 0)
        free_gb = free / GIB
        level = _level_by_percent(used, t.disk_used_warn, t.disk_used_bad)
        if free_gb < t.disk_free_bad_gb:
            level = "bad"
        elif free_gb < t.disk_free_warn_gb:
            level = _worst(level, "warn")
        system = _is_system_volume(mount)
        checks.append(
            make_check(
                "storage.space",
                "Storage",
                f"Free space on {mount}",
                level,
                f"{used:.0f}% used, {free_gb:.1f} GB free",
                WEIGHTS["storage.system" if system else "storage.other"],
                mount=mount,
            )
        )
    if not checks:
        checks.append(_unavailable("storage.space", "Storage", "Free space"))
    return checks


def judge_memory(mem: dict[str, Any], t: Thresholds) -> list[dict[str, Any]]:
    """RAM in use right now, and how much RAM is installed."""
    percent, total = mem.get("percent"), mem.get("total")
    checks: list[dict[str, Any]] = []
    if percent is None:
        checks.append(_unavailable("memory.usage", "Memory", "RAM in use"))
    else:
        level = _level_by_percent(float(percent), t.ram_used_warn, t.ram_used_bad)
        checks.append(make_check("memory.usage", "Memory", "RAM in use", level, f"{float(percent):.0f}% in use", WEIGHTS["memory.usage"]))
    if total:
        gb = total / GIB
        level = "bad" if gb < t.ram_total_low_gb else "warn" if gb < t.ram_total_ok_gb else "good"
        checks.append(make_check("memory.capacity", "Memory", "Installed RAM", level, f"{round(gb)} GB", WEIGHTS["memory.capacity"]))
    return checks


def judge_battery(health: dict[str, Any], t: Thresholds) -> list[dict[str, Any]]:
    """Battery wear. Desktops (no battery) get no check at all."""
    if not health.get("present"):
        return []
    wear = health.get("wear_percent")
    if wear is None:
        return [_unavailable("battery.wear", "Battery", "Battery wear", "Wear data not available (try Administrator)")]
    level = _level_by_percent(float(wear), t.battery_wear_warn, t.battery_wear_bad)
    value = f"{float(wear):.0f}% worn"
    if health.get("health_percent") is not None:
        value += f" (health {float(health['health_percent']):.0f}%)"
    if health.get("cycles") not in (None, NA):
        value += f", {health['cycles']} cycles"
    return [make_check("battery.wear", "Battery", "Battery wear", level, value, WEIGHTS["battery.wear"])]


def judge_startup(data: dict[str, Any], t: Thresholds) -> list[dict[str, Any]]:
    """Number of programs that start with Windows."""
    if not data.get("available"):
        return [_unavailable("startup.count", "Startup", "Startup programs", data.get("note") or "Not available")]
    enabled, heavy = int(data.get("enabled_count", 0)), int(data.get("heavy_count", 0))
    level = "bad" if enabled >= t.startup_bad else "warn" if enabled >= t.startup_warn else "good"
    if heavy >= t.startup_heavy_warn:
        level = _worst(level, "warn")
    value = f"{enabled} enabled"
    if heavy:
        value += f", {heavy} using noticeable memory"
    return [make_check("startup.count", "Startup", "Startup programs", level, value, WEIGHTS["startup.count"])]


def judge_security(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrap the security collector's checks as doctor checks."""
    if not report.get("available"):
        return [_unavailable("security.antivirus", "Security", "Security", report.get("note") or "Not available")]
    checks: list[dict[str, Any]] = []
    for item in report["checks"]:
        check_id = f"security.{item['key']}"
        level = item["level"]
        check = make_check(check_id, "Security", item["label"], level, item["value"], WEIGHTS.get(check_id, 0))
        if level in ("warn", "bad") and not check["hint"]:
            check["hint"] = item.get("hint", "")
        checks.append(check)
    return checks


def judge_disk_health(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Worst state across all physical drives (SMART, wear, Windows health)."""
    if not report.get("available"):
        return [_unavailable("disk.health", "Storage", "Drive health", report.get("note") or "Not available")]
    level = "good"
    parts: list[str] = []
    for disk in report["disks"]:
        level = _worst(level, disk["level"]) if disk["level"] != "muted" else level
        note = disk["reasons"][0] if disk["reasons"] else disk["status"]
        parts.append(f"{disk['model']}: {note}")
    if all(d["level"] == "muted" for d in report["disks"]):
        level = "muted"
    weight = 0 if level == "muted" else WEIGHTS["disk.health"]
    return [make_check("disk.health", "Storage", "Drive health", level, "; ".join(parts) or NA, weight)]


def judge_temperature(report: dict[str, Any], t: Thresholds) -> list[dict[str, Any]]:
    """Hottest sensor on the laptop."""
    top = hottest(report)
    if top is None:
        return [_unavailable("thermal.temperature", "Thermals", "Temperature", "Not available (sensors not exposed by this laptop)")]
    level = _level_by_percent(top, t.temp_warn, t.temp_bad)
    return [make_check("thermal.temperature", "Thermals", "Temperature", level, f"Hottest sensor {top:.0f} C", WEIGHTS["thermal.temperature"])]


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
def _safe(fn: Callable[[], Any]) -> Any:
    """Run a collector; None means it failed (never let one collector stop the doctor)."""
    try:
        return fn()
    except Exception:
        return None


def run_checks(
    thresholds: Thresholds | None = None,
    *,
    storage: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    battery: dict[str, Any] | None = None,
    startup: dict[str, Any] | None = None,
    security: dict[str, Any] | None = None,
    disk: dict[str, Any] | None = None,
    temps: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run every doctor check and return them (list of dicts). Never raises.

    Pass data that was already collected (for example by `lapforge report`) to avoid
    reading it a second time. Anything left as None is read here, as before.
    """
    t = thresholds or Thresholds()
    # The collectors are looked up here (not at import time) so tests can replace them.
    readers: dict[str, Callable[[], Any]] = {
        "storage": get_storage_info,
        "memory": get_memory_info,
        "battery": get_battery_health,
        "startup": get_startup_apps,
        "security": get_security_report,
        "disk": get_disk_health,
        "temps": get_temperatures,
    }
    given: dict[str, Any] = {
        "storage": storage,
        "memory": memory,
        "battery": battery,
        "startup": startup,
        "security": security,
        "disk": disk,
        "temps": temps,
    }
    data: dict[str, Any] = {name: value for name, value in given.items() if value is not None}
    missing = [name for name in readers if name not in data]
    if missing:
        with ThreadPoolExecutor(max_workers=len(missing)) as pool:
            futures = {name: pool.submit(_safe, readers[name]) for name in missing}
            data.update({name: fut.result() for name, fut in futures.items()})

    checks: list[dict[str, Any]] = []
    steps: list[tuple[str, Callable[[Any], list[dict[str, Any]]], Callable[[], list[dict[str, Any]]]]] = [
        ("storage", lambda d: judge_storage(d["volumes"], t), lambda: [_unavailable("storage.space", "Storage", "Free space")]),
        ("memory", lambda d: judge_memory(d, t), lambda: [_unavailable("memory.usage", "Memory", "RAM in use")]),
        ("battery", lambda d: judge_battery(d, t), lambda: []),
        ("startup", lambda d: judge_startup(d, t), lambda: [_unavailable("startup.count", "Startup", "Startup programs")]),
        ("security", judge_security, lambda: [_unavailable("security.antivirus", "Security", "Security")]),
        ("disk", judge_disk_health, lambda: [_unavailable("disk.health", "Storage", "Drive health")]),
        ("temps", lambda d: judge_temperature(d, t), lambda: [_unavailable("thermal.temperature", "Thermals", "Temperature")]),
    ]
    for name, judge, fallback in steps:
        try:
            checks.extend(judge(data[name]) if data[name] is not None else fallback())
        except Exception:
            checks.extend(fallback())
    return checks
