"""Bottleneck finder: what is slowing this laptop down, and which apps cause it.

Reads CPU, memory and disk activity plus the running programs. It only reads:
nothing is closed, changed or sent anywhere.

Programs are grouped by name, because one app (a browser, for example) runs as
many processes. `judge_bottleneck` is a pure function (numbers in, verdict out)
so it can be tested without a real computer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import psutil

from ..utils import format_bytes

# Programs that only show "idle" time and would look like the biggest CPU user.
_IGNORED_NAMES = frozenset({"system idle process", "idle", "[system process]"})
_LEVEL_ORDER = {"good": 0, "muted": 0, "warn": 1, "bad": 2}
_RESOURCE_PRIORITY = {"RAM": 0, "CPU": 1, "Disk": 2}  # on a tie, memory pressure is the root cause


@dataclass(frozen=True)
class BottleneckLimits:
    """Where 'busy' turns yellow / red. Change them in one place."""

    cpu_warn: float = 70.0  # percent of the whole CPU
    cpu_bad: float = 90.0
    ram_warn: float = 80.0  # percent of installed RAM in use
    ram_bad: float = 92.0
    swap_warn: float = 40.0  # percent of the page file in use
    disk_warn_mb: float = 100.0  # MB/s read+write, sustained
    app_min_cpu: float = 3.0  # an app must use at least this much CPU to be named
    app_min_ram: float = 3.0  # ... or this much of the RAM


# --------------------------------------------------------------------------
# Reading the running programs
# --------------------------------------------------------------------------
def clean_name(name: str | None) -> str:
    """'chrome.exe' -> 'chrome' for display. Empty names become 'unknown'."""
    text = (name or "").strip()
    if text.lower().endswith(".exe"):
        text = text[:-4]
    return text or "unknown"


def cpu_count() -> int:
    return psutil.cpu_count(logical=True) or 1


def sample_processes() -> list[dict[str, Any]]:
    """One reading of every running program: pid, name, cpu, rss.

    `cpu` is the share of the WHOLE CPU (0-100), measured since the previous call,
    so the first call after start returns zeros: call it once to prime it.
    Programs Windows does not let us read are skipped, never reported as errors.
    """
    cores = cpu_count()
    found: list[dict[str, Any]] = []
    try:
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            info = proc.info
            name = info.get("name")
            if not info.get("pid") or (name or "").lower() in _IGNORED_NAMES:
                continue
            mem = info.get("memory_info")
            found.append(
                {
                    "pid": info["pid"],
                    "name": clean_name(name),
                    "cpu": max(0.0, min(100.0, (info.get("cpu_percent") or 0.0) / cores)),
                    "rss": int(mem.rss) if mem is not None else 0,
                }
            )
    except Exception:
        pass
    return found


def group_processes(processes: list[dict[str, Any]], total_ram: int | None = None) -> list[dict[str, Any]]:
    """Add up processes that share a name. Returns groups sorted by CPU, then memory.

    Each group: {"name", "count", "cpu", "rss", "rss_text", "ram_percent"}.
    """
    if total_ram is None:
        try:
            total_ram = psutil.virtual_memory().total
        except Exception:
            total_ram = 0
    groups: dict[str, dict[str, Any]] = {}
    for proc in processes:
        name = clean_name(proc.get("name"))
        key = name.lower()
        group = groups.setdefault(key, {"name": name, "count": 0, "cpu": 0.0, "rss": 0})
        group["count"] += 1
        group["cpu"] += proc["cpu"]
        group["rss"] += proc["rss"]
    result = []
    for group in groups.values():
        group["cpu"] = round(min(100.0, group["cpu"]), 1)
        group["rss_text"] = format_bytes(group["rss"])
        group["ram_percent"] = round(100.0 * group["rss"] / total_ram, 1) if total_ram else 0.0
        result.append(group)
    result.sort(key=lambda g: (-g["cpu"], -g["rss"], g["name"].lower()))
    return result


def top_by(groups: list[dict[str, Any]], key: str, count: int) -> list[dict[str, Any]]:
    """The `count` biggest groups by 'cpu' or 'rss' (biggest first)."""
    if key not in ("cpu", "rss"):
        raise ValueError("key must be 'cpu' or 'rss'")
    return sorted(groups, key=lambda g: (-g[key], g["name"].lower()))[: max(0, count)]


# --------------------------------------------------------------------------
# Judging
# --------------------------------------------------------------------------
def level_for(value: float | None, warn: float, bad: float) -> str:
    """'good' / 'warn' / 'bad' ('muted' if unknown)."""
    if value is None:
        return "muted"
    return "bad" if value >= bad else "warn" if value >= warn else "good"


def _culprit(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": group["name"],
        "count": group["count"],
        "cpu": group["cpu"],
        "rss": group["rss"],
        "rss_text": group["rss_text"],
        "ram_percent": group["ram_percent"],
    }


def _names(culprits: list[dict[str, Any]]) -> str:
    return ", ".join(c["name"] for c in culprits[:3])


# Normal Windows background tasks. They are busy now and then (updates, scanning, indexing) and stop by themselves.
WINDOWS_BACKGROUND_TASKS = frozenset(
    {
        "compattelrunner", "msmpeng", "tiworker", "trustedinstaller", "wmiprvse", "searchindexer", "searchprotocolhost",
        "searchfilterhost", "musnotification", "musnotificationux", "mousocoreworker", "usoclient", "wuauclt", "dismhost",
        "svchost", "wsappx", "microsoftedgeupdate", "taskhostw", "dllhost",
    }
)


def is_windows_background_task(name: str) -> bool:
    return name.lower().removesuffix(".exe").strip() in WINDOWS_BACKGROUND_TASKS


def _cpu_hint(top_cpu: list[dict[str, Any]]) -> str:
    """Advice for a busy processor. A normal Windows task is never called suspicious."""
    if not top_cpu:
        return "No single program stands out: the load is spread over many small tasks. A restart clears stuck background tasks."
    name = top_cpu[0]["name"]
    if is_windows_background_task(name):
        return (
            f"{name} is a normal Windows background task (updates, scanning or indexing). "
            "It is busy for a while and then stops by itself, so wait a few minutes. "
            "If the processor stays at 100% for hours, restart the laptop."
        )
    return (
        f"{name} is using the most. Close or restart it; if it keeps happening, update it or check it for malware. "
        "A restart clears stuck background tasks."
    )


def judge_bottleneck(
    metrics: dict[str, Any],
    groups: list[dict[str, Any]],
    limits: BottleneckLimits | None = None,
) -> dict[str, Any]:
    """Decide what, if anything, is the bottleneck.

    metrics: {"cpu_percent", "ram_percent", "swap_percent", "disk_mb_s"} (any may be None).
    Returns {"level", "primary", "summary", "hint", "findings", "top_cpu", "top_ram",
    "apps_by_cpu", "apps_by_ram", "metrics"}.
    `primary` is 'RAM', 'CPU', 'Disk' or 'None'.
    """
    lim = limits or BottleneckLimits()
    cpu, ram = metrics.get("cpu_percent"), metrics.get("ram_percent")
    swap, disk = metrics.get("swap_percent"), metrics.get("disk_mb_s")
    top_cpu = [_culprit(g) for g in top_by(groups, "cpu", 5) if g["cpu"] >= lim.app_min_cpu]
    top_ram = [_culprit(g) for g in top_by(groups, "rss", 5) if g["ram_percent"] >= lim.app_min_ram]

    findings: list[dict[str, Any]] = []

    ram_level = level_for(ram, lim.ram_warn, lim.ram_bad)
    if ram_level in ("warn", "bad"):
        swap_note = f" and the page file is {swap:.0f}% full, which slows everything down" if swap is not None and swap >= lim.swap_warn else ""
        findings.append(
            {
                "resource": "RAM",
                "level": ram_level,
                "value": f"{ram:.0f}% in use",
                "summary": f"Memory is {ram:.0f}% full{swap_note}.",
                "culprits": top_ram[:3],
                "hint": (f"Close or restart {_names(top_ram)}. " if top_ram else "")
                + "If memory is often this full, more RAM will make the biggest difference.",
            }
        )

    cpu_level = level_for(cpu, lim.cpu_warn, lim.cpu_bad)
    if cpu_level in ("warn", "bad"):
        findings.append(
            {
                "resource": "CPU",
                "level": cpu_level,
                "value": f"{cpu:.0f}% busy",
                "summary": f"The processor is {cpu:.0f}% busy.",
                "culprits": top_cpu[:3],
                "hint": _cpu_hint(top_cpu),
            }
        )

    if disk is not None and disk >= lim.disk_warn_mb:
        findings.append(
            {
                "resource": "Disk",
                "level": "warn",
                "value": f"{disk:.0f} MB/s",
                "summary": f"The drive is busy at {disk:.0f} MB/s.",
                "culprits": [],
                "hint": "Something is reading or writing a lot (an update, a backup, a download or an antivirus scan). Wait for it to finish, or check Windows Update.",
            }
        )

    findings.sort(key=lambda f: (-_LEVEL_ORDER[f["level"]], _RESOURCE_PRIORITY[f["resource"]]))
    if findings:
        first = findings[0]
        primary, level, summary, hint = first["resource"], first["level"], first["summary"], first["hint"]
    else:
        primary, level = "None", "good"
        summary = "No bottleneck: your laptop has plenty of headroom right now."
        hint = ""
    return {
        "level": level,
        "primary": primary,
        "summary": summary,
        "hint": hint,
        "findings": findings,
        "top_cpu": top_cpu,  # only apps busy enough to blame
        "top_ram": top_ram,
        "apps_by_cpu": [_culprit(g) for g in top_by(groups, "cpu", 10)],  # the plain top 10, for tables
        "apps_by_ram": [_culprit(g) for g in top_by(groups, "rss", 10)],
        "metrics": {
            "cpu_percent": cpu,
            "ram_percent": ram,
            "swap_percent": swap,
            "disk_mb_s": disk,
        },
    }


# --------------------------------------------------------------------------
# Measuring
# --------------------------------------------------------------------------
def disk_rate(previous: Any, current: Any, seconds: float) -> tuple[float | None, float | None]:
    """(read, write) in bytes per second between two psutil disk counters (None if unknown)."""
    if previous is None or current is None or seconds <= 0:
        return None, None
    read = max(0, current.read_bytes - previous.read_bytes) / seconds
    write = max(0, current.write_bytes - previous.write_bytes) / seconds
    return read, write


def _disk_counters() -> Any:
    try:
        return psutil.disk_io_counters()
    except Exception:
        return None


def measure_bottleneck(
    seconds: float = 3.0,
    interval: float = 1.0,
    limits: BottleneckLimits | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Watch the computer for a few seconds and judge what slows it down. Never raises.

    Readings are averaged, so a one-second spike is not reported as a problem.
    """
    interval = max(0.2, float(interval))
    rounds = max(1, int(round(max(seconds, interval) / interval)))
    try:
        psutil.cpu_percent(None)
        sample_processes()  # priming: the first per-process CPU reading is always zero
        total_ram = psutil.virtual_memory().total
    except Exception:
        return judge_bottleneck({}, [], limits)

    cpu_readings: list[float] = []
    ram_readings: list[float] = []
    swap_readings: list[float] = []
    disk_readings: list[float] = []
    cpu_sums: dict[str, float] = {}
    last_groups: dict[str, dict[str, Any]] = {}
    disk_before, clock = _disk_counters(), time.monotonic()
    for _ in range(rounds):
        sleep(interval)
        try:
            cpu_readings.append(psutil.cpu_percent(None))
            ram_readings.append(psutil.virtual_memory().percent)
            swap_readings.append(psutil.swap_memory().percent)
        except Exception:
            pass
        now = time.monotonic()
        disk_now = _disk_counters()
        read, write = disk_rate(disk_before, disk_now, now - clock)
        if read is not None and write is not None:
            disk_readings.append((read + write) / (1024 * 1024))
        disk_before, clock = disk_now, now
        for group in group_processes(sample_processes(), total_ram):
            key = group["name"].lower()
            cpu_sums[key] = cpu_sums.get(key, 0.0) + group["cpu"]
            last_groups[key] = group

    averaged = []
    for key, group in last_groups.items():
        averaged.append({**group, "cpu": round(cpu_sums.get(key, 0.0) / rounds, 1)})
    averaged.sort(key=lambda g: (-g["cpu"], -g["rss"], g["name"].lower()))

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    metrics = {
        "cpu_percent": mean(cpu_readings),
        "ram_percent": mean(ram_readings),
        "swap_percent": mean(swap_readings),
        "disk_mb_s": mean(disk_readings),
    }
    result = judge_bottleneck(metrics, averaged, limits)
    result["seconds"] = round(rounds * interval, 1)
    result["cores"] = cpu_count()
    return result


def get_bottleneck(seconds: float = 3.0) -> dict[str, Any]:
    """Convenience wrapper: measure for `seconds` seconds with the default limits."""
    return measure_bottleneck(seconds=seconds)


__all__ = [
    "BottleneckLimits",
    "clean_name",
    "disk_rate",
    "get_bottleneck",
    "group_processes",
    "judge_bottleneck",
    "level_for",
    "measure_bottleneck",
    "sample_processes",
    "top_by",
]
