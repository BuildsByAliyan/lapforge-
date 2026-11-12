"""Hardware and basic OS collectors.

Every public function returns plain dicts/lists and never raises. Anything that
cannot be read on the current machine is reported as "N/A".
"""

from __future__ import annotations

import platform
import re
import socket
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import psutil

from ..utils import (
    IS_WINDOWS,
    NA,
    as_list,
    clean_text,
    format_bytes,
    format_duration,
    ordinal,
    powershell_json,
    run_cmd,
)

# SMBIOS memory type codes -> readable names.
_MEMORY_TYPES = {
    20: "DDR",
    21: "DDR2",
    24: "DDR3",
    26: "DDR4",
    27: "LPDDR",
    28: "LPDDR2",
    29: "LPDDR3",
    30: "LPDDR4",
    34: "DDR5",
    35: "LPDDR5",
}

_WINDOWS_SNAPSHOT_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cs   = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model, SystemType
$bios = Get-CimInstance Win32_BIOS | Select-Object SerialNumber
$os   = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture
$cpu  = Get-CimInstance Win32_Processor | Select-Object Name, Manufacturer, MaxClockSpeed
$gpu  = Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM
$ram  = Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity, Speed, ConfiguredClockSpeed, SMBIOSMemoryType, Manufacturer
$disk = Get-PhysicalDisk | Select-Object FriendlyName, MediaType, BusType, Size, HealthStatus
[ordered]@{
    system = $cs; bios = $bios; os = $os
    cpu = @($cpu); gpu = @($gpu); ram = @($ram); disk = @($disk)
} | ConvertTo-Json -Depth 4 -Compress
"""


@lru_cache(maxsize=1)
def _windows_snapshot() -> dict[str, Any]:
    """One PowerShell call for all WMI data (PowerShell start-up is slow)."""
    if not IS_WINDOWS:
        return {}
    data = powershell_json(_WINDOWS_SNAPSHOT_SCRIPT, timeout=60)
    return data if isinstance(data, dict) else {}


def _as_dict(value: Any) -> dict[str, Any]:
    """Return `value` only if it really is a dict; anything else becomes {}."""
    return value if isinstance(value, dict) else {}


def _as_dicts(value: Any) -> list[dict[str, Any]]:
    """Return a list that holds only the real dicts found in `value`."""
    return [item for item in as_list(value) if isinstance(item, dict)]


def _snap_dict(key: str) -> dict[str, Any]:
    """One dict from the Windows snapshot (empty if missing or malformed)."""
    return _as_dict(_windows_snapshot().get(key))


def _snap_dicts(key: str) -> list[dict[str, Any]]:
    """A list of dicts from the Windows snapshot (empty if missing or malformed)."""
    return _as_dicts(_windows_snapshot().get(key))


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


# --------------------------------------------------------------------------
# System / OS
# --------------------------------------------------------------------------
def get_system_info() -> dict[str, Any]:
    """Brand, model, serial number and host name."""
    brand = model = serial = NA
    if IS_WINDOWS:
        system = _snap_dict("system")
        bios = _snap_dict("bios")
        brand = clean_text(system.get("Manufacturer"))
        model = clean_text(system.get("Model"))
        serial = clean_text(bios.get("SerialNumber"))
    elif platform.system() == "Linux":
        brand = clean_text(_read_text("/sys/class/dmi/id/sys_vendor"))
        model = clean_text(_read_text("/sys/class/dmi/id/product_name"))
        serial = clean_text(_read_text("/sys/class/dmi/id/product_serial"))
    return {
        "brand": brand,
        "model": model,
        "serial": serial,
        "hostname": socket.gethostname() or NA,
        "system_type": clean_text(_snap_dict("system").get("SystemType")) if IS_WINDOWS else NA,
    }


def get_os_info() -> dict[str, Any]:
    """Operating system name, version, build, architecture, uptime."""
    name = f"{platform.system()} {platform.release()}".strip() or NA
    version = platform.version() or NA
    build = NA
    arch = platform.machine() or NA
    if IS_WINDOWS:
        os_data = _snap_dict("os")
        name = clean_text(os_data.get("Caption"), name)
        version = clean_text(os_data.get("Version"), version)
        build = clean_text(os_data.get("BuildNumber"))
        arch = clean_text(os_data.get("OSArchitecture"), arch)
    try:
        uptime = format_duration(time.time() - psutil.boot_time())
    except Exception:
        uptime = NA
    return {
        "name": name,
        "version": version,
        "build": build,
        "architecture": arch,
        "uptime": uptime,
        "python": platform.python_version(),
    }


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------
def detect_cpu_generation(name: str) -> str:
    """Guess the CPU generation from its marketing name, e.g. 'i7-1165G7' -> '11th Gen'."""
    if not name or name == NA:
        return NA
    if re.search(r"Core\(TM\)\s*Ultra", name, re.IGNORECASE) or re.search(
        r"Core\s+Ultra", name, re.IGNORECASE
    ):
        return "Core Ultra"
    match = re.search(r"\bi[3579]-(\d{3,5})", name, re.IGNORECASE)
    if match:
        digits = match.group(1)
        if len(digits) == 5:
            gen = int(digits[:2])
        elif len(digits) == 4:
            # 10th-14th gen mobile parts look like 1165G7 / 1255U (start with "1").
            gen = int(digits[:2]) if digits[0] == "1" else int(digits[0])
        else:
            gen = 1
        return f"{ordinal(gen)} Gen Intel"
    match = re.search(r"Ryzen\s+(?:AI\s+)?(?:\d\s+)?(?:PRO\s+)?(\d{4})", name, re.IGNORECASE)
    if match and "AI" not in name.upper().split("RYZEN")[-1][:5]:
        series = int(match.group(1)[0]) * 1000
        return f"Ryzen {series} series"
    if re.search(r"Ryzen\s+AI", name, re.IGNORECASE):
        return "Ryzen AI"
    return NA


def _linux_cpu_name() -> str:
    for line in _read_text("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[-1].strip()
    return NA


def median_load(samples: list[Any]) -> float | None:
    """Median of the numeric samples (None when there is no usable sample)."""
    values = sorted(
        float(x) for x in samples if isinstance(x, (int, float)) and not isinstance(x, bool)
    )
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _sample_cpu_load(count: int = 3, interval: float = 0.2) -> float | None:
    """Median of a few short CPU load samples. Call this AFTER other work is done."""
    samples: list[float] = []
    for _ in range(count):
        try:
            samples.append(psutil.cpu_percent(interval=interval))
        except Exception:
            continue
    return median_load(samples)


def _cpu_count(logical: bool) -> Any:
    try:
        return psutil.cpu_count(logical=logical) or NA
    except Exception:
        return NA


def get_cpu_info() -> dict[str, Any]:
    """Processor name, generation, cores, threads, clock speed, current load."""
    name = NA
    if IS_WINDOWS:
        cpus = _snap_dicts("cpu")
        if cpus:
            name = clean_text(cpus[0].get("Name"))
    elif platform.system() == "Linux":
        name = _linux_cpu_name()
    if name == NA:
        name = clean_text(platform.processor())
    name = re.sub(r"\s+", " ", name).strip() if name != NA else name

    try:
        freq = psutil.cpu_freq()
        max_mhz = freq.max or freq.current if freq else None
    except Exception:
        max_mhz = None
    # The PowerShell snapshot is read above. Measure the load only now, so that
    # LapForge's own PowerShell work does not inflate the number.
    usage = _sample_cpu_load()

    return {
        "name": name,
        "generation": detect_cpu_generation(name),
        "cores": _cpu_count(False),
        "threads": _cpu_count(True),
        "max_mhz": round(max_mhz) if max_mhz else None,
        "max_speed": f"{max_mhz / 1000:.2f} GHz" if max_mhz else NA,
        "usage_percent": usage,
    }


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------
def _memory_type_name(code: Any) -> str:
    """SMBIOS memory type code -> 'DDR4' etc. Unknown or malformed codes give N/A."""
    try:
        return _MEMORY_TYPES.get(int(code), NA)
    except (TypeError, ValueError):
        return NA


def _speed_text(raw: Any) -> str:
    """Memory speed in MHz as text; N/A for missing, zero or malformed values."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return NA
    return f"{value} MHz" if value > 0 else NA


def get_memory_info() -> dict[str, Any]:
    """RAM totals plus module details (type, speed) where the OS exposes them."""
    vm = psutil.virtual_memory()
    try:
        swap_total = psutil.swap_memory().total
    except Exception:
        swap_total = None

    modules: list[dict[str, Any]] = []
    if IS_WINDOWS:
        for stick in _snap_dicts("ram"):
            speed = stick.get("ConfiguredClockSpeed") or stick.get("Speed")
            modules.append(
                {
                    "capacity": format_bytes(stick.get("Capacity"), 0),
                    "type": _memory_type_name(stick.get("SMBIOSMemoryType")),
                    "speed": _speed_text(speed),
                    "manufacturer": clean_text(stick.get("Manufacturer")),
                }
            )
    mem_type = next((m["type"] for m in modules if m["type"] != NA), NA)
    mem_speed = next((m["speed"] for m in modules if m["speed"] != NA), NA)
    return {
        "total": vm.total,
        "total_text": format_bytes(vm.total),
        "used": vm.used,
        "available": vm.available,
        "percent": vm.percent,
        "type": mem_type,
        "speed": mem_speed,
        "modules": modules,
        "swap_total_text": format_bytes(swap_total),
    }


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
def get_storage_info() -> dict[str, Any]:
    """Physical drives (model, SSD/HDD, size) and mounted volumes with usage."""
    drives: list[dict[str, Any]] = []
    if IS_WINDOWS:
        for disk in _snap_dicts("disk"):
            media = clean_text(disk.get("MediaType"))
            drives.append(
                {
                    "model": clean_text(disk.get("FriendlyName")),
                    "type": media,
                    "bus": clean_text(disk.get("BusType")),
                    "size": format_bytes(disk.get("Size"), 0),
                    "health": clean_text(disk.get("HealthStatus")),
                }
            )

    volumes: list[dict[str, Any]] = []
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        partitions = []
    for part in partitions:
        if "cdrom" in part.opts.lower() or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        volumes.append(
            {
                "mount": part.mountpoint,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": usage.percent,
                "total_text": format_bytes(usage.total),
                "free_text": format_bytes(usage.free),
            }
        )
    return {"drives": drives, "volumes": volumes}


# --------------------------------------------------------------------------
# GPU
# --------------------------------------------------------------------------
def _gpu_vram_text(raw: Any) -> str:
    """Win32_VideoController.AdapterRAM is a 32-bit field, so 4 GB is its ceiling."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return NA
    if value <= 0:
        return NA
    if value >= 4294967295:
        return "4 GB+"
    return format_bytes(value, 0)


def get_gpu_info() -> list[dict[str, Any]]:
    """List of graphics adapters (name, driver version, video memory)."""
    gpus: list[dict[str, Any]] = []
    if IS_WINDOWS:
        for gpu in _snap_dicts("gpu"):
            gpus.append(
                {
                    "name": clean_text(gpu.get("Name")),
                    "driver": clean_text(gpu.get("DriverVersion")),
                    "vram": _gpu_vram_text(gpu.get("AdapterRAM")),
                }
            )
    elif platform.system() == "Linux":
        for line in run_cmd(["lspci"]).splitlines():
            if re.search(r"VGA|3D controller|Display controller", line):
                gpus.append({"name": line.split(": ", 1)[-1].strip(), "driver": NA, "vram": NA})
    return gpus


# --------------------------------------------------------------------------
# Battery (basic level; wear/health arrives with the battery_health collector)
# --------------------------------------------------------------------------
def get_battery_info() -> dict[str, Any]:
    """Current charge level and power state. present=False on desktops."""
    try:
        batt = psutil.sensors_battery()
    except Exception:
        batt = None
    if batt is None:
        return {"present": False, "percent": None, "plugged": None, "time_left": NA}
    secs = batt.secsleft
    if secs in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) or secs is None:
        time_left = NA
    else:
        time_left = format_duration(secs)
    return {
        "present": True,
        "percent": batt.percent,
        "plugged": batt.power_plugged,
        "time_left": time_left,
    }


# --------------------------------------------------------------------------
# Safe collection (one failing collector never breaks the others)
# --------------------------------------------------------------------------
def _fallback_system() -> dict[str, Any]:
    return {"brand": NA, "model": NA, "serial": NA, "hostname": NA, "system_type": NA}


def _fallback_os() -> dict[str, Any]:
    return {
        "name": NA,
        "version": NA,
        "build": NA,
        "architecture": NA,
        "uptime": NA,
        "python": platform.python_version(),
    }


def _fallback_cpu() -> dict[str, Any]:
    return {
        "name": NA,
        "generation": NA,
        "cores": NA,
        "threads": NA,
        "max_mhz": None,
        "max_speed": NA,
        "usage_percent": None,
    }


def _fallback_memory() -> dict[str, Any]:
    return {
        "total": None,
        "total_text": NA,
        "used": None,
        "available": None,
        "percent": None,
        "type": NA,
        "speed": NA,
        "modules": [],
        "swap_total_text": NA,
    }


def _fallback_storage() -> dict[str, Any]:
    return {"drives": [], "volumes": []}


def _fallback_gpu() -> list[dict[str, Any]]:
    return []


def _fallback_battery() -> dict[str, Any]:
    return {"present": False, "percent": None, "plugged": None, "time_left": NA}


# Section name -> function that builds a complete "N/A" version of that section.
SECTION_FALLBACKS: dict[str, Callable[[], Any]] = {
    "system": _fallback_system,
    "os": _fallback_os,
    "cpu": _fallback_cpu,
    "memory": _fallback_memory,
    "storage": _fallback_storage,
    "gpu": _fallback_gpu,
    "battery": _fallback_battery,
}


def safe_collect(section: str, func: Callable[[], Any]) -> Any:
    """Run one collector. On any error (or a result of the wrong type) return its N/A version."""
    fallback = SECTION_FALLBACKS[section]()
    try:
        result = func()
    except Exception:
        return fallback
    return result if isinstance(result, type(fallback)) else fallback


# --------------------------------------------------------------------------
# Everything at once
# --------------------------------------------------------------------------
def collect_hardware() -> dict[str, Any]:
    """Full hardware snapshot used by `lapforge info`. A failing section shows N/A only."""
    collectors: dict[str, Callable[[], Any]] = {
        "system": get_system_info,
        "os": get_os_info,
        "cpu": get_cpu_info,
        "memory": get_memory_info,
        "storage": get_storage_info,
        "gpu": get_gpu_info,
        "battery": get_battery_info,
    }
    return {name: safe_collect(name, func) for name, func in collectors.items()}
