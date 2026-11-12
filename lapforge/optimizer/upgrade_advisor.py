"""Upgrade advisor: can this laptop take more RAM, or a bigger / faster SSD?

It only READS hardware details and gives advice. Nothing on the computer is changed.

What it looks at
  * RAM     - installed size, type (DDR4/DDR5...), speed, memory slots (used / free),
              the maximum the firmware says it supports, and whether the RAM is soldered.
  * Storage - drive type (NVMe SSD / SATA SSD / HDD), size, free space on the system drive,
              and M.2 slots when the firmware reports them.

Honest limits
  * Windows cannot see EMPTY drive slots on most laptops. When it cannot tell, the advisor
    says so and points to the laptop's service manual instead of guessing.
  * The "maximum RAM" number comes from the firmware and is sometimes wrong (too low or
    absurdly high). Numbers that cannot be right are ignored and reported as unknown.

Every public function returns plain dicts/lists and never raises. Anything that cannot be
read on this machine is reported as "N/A" or "unknown".
"""

from __future__ import annotations

import os
import re
from typing import Any

from ..collectors.hardware import get_memory_info, get_storage_info, get_system_info
from ..utils import IS_WINDOWS, NA, as_list, clean_text, format_bytes, powershell_json

GIB = 1024**3
STANDARD_MODULE_GB = (4, 8, 16, 32, 64)
STANDARD_SSD_GB = (256, 512, 1000, 2000, 4000)
MAX_LAPTOP_RAM_GB = 1024  # a firmware claim above this is not trusted for a laptop

# SMBIOS memory type codes -> readable names.
MEMORY_TYPES = {
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

# Win32_PhysicalMemory.FormFactor codes.
FORM_FACTORS = {
    7: "SIMM",
    8: "DIMM",
    9: "TSOP",
    11: "RIMM",
    12: "SODIMM",
    13: "SRIMM",
    14: "SMD",
    21: "BGA",
    22: "FPBGA",
    23: "LGA",
}
SOCKET_FORM_FACTORS = {7, 8, 11, 12, 13}
SOLDERED_FORM_FACTORS = {5, 9, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}

# Drives on these buses are not the laptop's internal storage (SD cards, USB sticks, virtual disks).
_EXTERNAL_BUSES = {"usb", "sd", "1394", "virtual", "file backed virtual", "spaces", "iscsi"}

LEVEL_ORDER = {"bad": 0, "warn": 1, "good": 2, "muted": 3}

_RAW_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$arr  = Get-CimInstance Win32_PhysicalMemoryArray | Select-Object MaxCapacity, MaxCapacityEx, MemoryDevices
$ram  = Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator, Capacity, SMBIOSMemoryType, FormFactor, Speed, ConfiguredClockSpeed, Manufacturer, PartNumber
$disk = Get-PhysicalDisk | Select-Object FriendlyName, MediaType, BusType, Size
$slot = Get-CimInstance Win32_SystemSlot | Select-Object SlotDesignation, CurrentUsage
[ordered]@{ array = @($arr); ram = @($ram); disk = @($disk); slot = @($slot) } | ConvertTo-Json -Depth 4 -Compress
"""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _gb_text(num_bytes: float | int | None) -> str:
    """8589934592 -> '8 GB'. N/A when unknown."""
    if not num_bytes or num_bytes <= 0:
        return NA
    return f"{num_bytes / GIB:.0f} GB"


def _rec(key: str, level: str, title: str, detail: str) -> dict[str, str]:
    return {"key": key, "level": level, "title": title, "detail": detail}


def _collect_raw() -> dict[str, Any]:
    """One PowerShell call for everything the advisor needs (PowerShell start-up is slow)."""
    data = powershell_json(_RAW_SCRIPT, timeout=60)
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# RAM: parsing
# --------------------------------------------------------------------------
def module_is_soldered(type_name: str, form_code: int | None) -> bool | None:
    """True = soldered to the board, False = a removable stick, None = cannot tell."""
    if type_name.startswith("LPDDR"):
        return True
    if form_code in SOLDERED_FORM_FACTORS:
        return True
    if form_code in SOCKET_FORM_FACTORS:
        return False
    return None


def parse_ram_modules(raw_modules: Any) -> list[dict[str, Any]]:
    """Windows memory rows -> one dict per installed module."""
    modules: list[dict[str, Any]] = []
    for row in as_list(raw_modules):
        if not isinstance(row, dict):
            continue
        capacity = _int(row.get("Capacity"))
        if not capacity or capacity <= 0:
            continue
        type_name = MEMORY_TYPES.get(_int(row.get("SMBIOSMemoryType")) or -1, NA)
        form_code = _int(row.get("FormFactor"))
        speed = _int(row.get("ConfiguredClockSpeed")) or _int(row.get("Speed"))
        modules.append(
            {
                "slot": clean_text(row.get("DeviceLocator")),
                "capacity_bytes": capacity,
                "capacity_gb": round(capacity / GIB),
                "capacity": format_bytes(capacity, 0),
                "type": type_name,
                "form": FORM_FACTORS.get(form_code or -1, NA),
                "speed_mhz": speed,
                "speed": f"{speed} MHz" if speed else NA,
                "maker": clean_text(row.get("Manufacturer")),
                "part": clean_text(row.get("PartNumber")),
                "soldered": module_is_soldered(type_name, form_code),
            }
        )
    return modules


def parse_ram_array(raw_array: Any) -> tuple[int | None, int | None]:
    """Memory array rows -> (total slots, max supported bytes). None when unknown."""
    slots = 0
    max_kb = 0
    for row in as_list(raw_array):
        if not isinstance(row, dict):
            continue
        slots += _int(row.get("MemoryDevices")) or 0
        # Both fields are in kilobytes; the "Ex" one is used when the plain one overflows.
        max_kb += max(_int(row.get("MaxCapacity")) or 0, _int(row.get("MaxCapacityEx")) or 0)
    return (slots or None), (max_kb * 1024 or None)


def classify_soldering(modules: list[dict[str, Any]]) -> str:
    """'all' | 'partial' | 'none' | 'unknown' for the installed modules."""
    if not modules:
        return "unknown"
    soldered = sum(1 for m in modules if m["soldered"] is True)
    socketed = sum(1 for m in modules if m["soldered"] is False)
    if soldered == len(modules):
        return "all"
    if soldered and socketed:
        return "partial"
    if socketed:
        return "none"
    return "unknown"


# --------------------------------------------------------------------------
# RAM: advice
# --------------------------------------------------------------------------
def ram_need(nominal_gb: float, percent_used: float | None) -> dict[str, Any]:
    """How much RAM this laptop should have: {"level", "target_gb", "reason"}."""
    if nominal_gb < 6:
        return {"level": "bad", "target_gb": 8, "reason": f"{nominal_gb:.0f} GB is too little for Windows 10/11 with a browser open."}
    if nominal_gb < 12:
        return {"level": "warn", "target_gb": 16, "reason": f"{nominal_gb:.0f} GB works, but many browser tabs plus other apps will make it slow."}
    if nominal_gb < 24:
        if percent_used is not None and percent_used >= 85:
            return {"level": "warn", "target_gb": 32, "reason": f"{nominal_gb:.0f} GB is normally plenty, but {percent_used:.0f}% is in use right now."}
        return {"level": "good", "target_gb": None, "reason": f"{nominal_gb:.0f} GB is enough for everyday work, study and light editing."}
    return {"level": "good", "target_gb": None, "reason": f"{nominal_gb:.0f} GB is more than enough for almost everything."}


def can_upgrade_ram(
    soldering: str,
    slots_total: int | None,
    modules: list[dict[str, Any]],
    installed_bytes: int,
    max_bytes: int | None,
) -> dict[str, Any]:
    """Decide if RAM can be upgraded. Returns {"verdict", "free_slots", "max_ok", "headroom_gb"}.

    verdict: 'yes' (add a module), 'replace' (all slots full: swap for bigger sticks),
             'no' (soldered, or already at the maximum) or 'unknown'.
    """
    max_ok = bool(max_bytes) and installed_bytes <= (max_bytes or 0) <= MAX_LAPTOP_RAM_GB * GIB
    headroom_gb = round(((max_bytes or 0) - installed_bytes) / GIB) if max_ok else None
    free = max(0, slots_total - len(modules)) if slots_total else None
    base = {"free_slots": free, "max_ok": max_ok, "headroom_gb": headroom_gb}

    if soldering == "all":
        return {**base, "verdict": "no", "free_slots": 0}
    if free is None or soldering == "unknown":
        return {**base, "verdict": "unknown"}
    if headroom_gb is not None and headroom_gb <= 0:
        return {**base, "verdict": "no"}
    if free > 0:
        return {**base, "verdict": "yes"}
    return {**base, "verdict": "replace"}


def plan_ram_upgrade(
    modules: list[dict[str, Any]],
    verdict: str,
    installed_gb: float,
    target_gb: int | None,
    max_gb: float | None,
    mem_type: str,
) -> str:
    """One sentence on what to buy. Pure text logic."""
    kind = f" {mem_type}" if mem_type != NA else ""
    wanted_total = float(target_gb or installed_gb * 2)
    if max_gb is not None:
        wanted_total = min(wanted_total, max_gb)

    if verdict == "yes":
        needed = max(0.0, wanted_total - installed_gb)
        if needed <= 0:
            return "No extra RAM is needed right now."
        existing = {m["capacity_gb"] for m in modules}
        if len(existing) == 1 and next(iter(existing)) >= needed:
            size = next(iter(existing))
            return f"Add one {size} GB{kind} module with the same size and speed as your current one (this keeps dual-channel speed)."
        size = next((s for s in STANDARD_MODULE_GB if s >= needed), STANDARD_MODULE_GB[-1])
        return (
            f"Add one {size} GB{kind} module (total {installed_gb + size:.0f} GB). "
            "Matching the size of the stick you already have keeps dual-channel speed."
        )
    if verdict == "replace":
        if len(modules) >= 2:
            each = next((s for s in STANDARD_MODULE_GB if s * len(modules) >= wanted_total), STANDARD_MODULE_GB[-1])
            return f"All slots are full. Replace the sticks with {len(modules)} x {each} GB{kind} (total {each * len(modules)} GB)."
        size = next((s for s in STANDARD_MODULE_GB if s >= wanted_total), STANDARD_MODULE_GB[-1])
        return f"The only slot is in use. Replace the stick with one {size} GB{kind} module."
    return ""


def assess_ram(raw: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    """Everything about RAM upgrades. `raw` is the Windows data, `memory` is get_memory_info()."""
    modules = parse_ram_modules(raw.get("ram"))
    slots_total, max_bytes = parse_ram_array(raw.get("array"))
    total_seen = _int(memory.get("total")) or 0
    installed_bytes = sum(m["capacity_bytes"] for m in modules) or (round(total_seen / GIB) * GIB)
    installed_gb = installed_bytes / GIB
    percent = memory.get("percent")
    mem_type = next((m["type"] for m in modules if m["type"] != NA), NA)
    mem_speed = next((m["speed"] for m in modules if m["speed"] != NA), NA)

    soldering = classify_soldering(modules)
    upgrade = can_upgrade_ram(soldering, slots_total, modules, installed_bytes, max_bytes)
    need = ram_need(installed_gb, percent if isinstance(percent, (int, float)) else None)

    max_gb = (max_bytes / GIB) if upgrade["max_ok"] and max_bytes else None
    sizes = {m["capacity_gb"] for m in modules}
    if len(modules) >= 2 and len(sizes) == 1:
        channel = "Dual-channel (matching sticks)"
    elif len(modules) >= 2:
        channel = "Mixed stick sizes (partly dual-channel)"
    elif len(modules) == 1:
        channel = "Single-channel (a second stick would speed up graphics)"
    else:
        channel = NA

    plan = plan_ram_upgrade(modules, upgrade["verdict"], installed_gb, need["target_gb"], max_gb, mem_type)
    return {
        "installed_bytes": installed_bytes,
        "installed_gb": installed_gb,
        "installed_text": _gb_text(installed_bytes),
        "type": mem_type,
        "speed": mem_speed,
        "modules": modules,
        "slots_total": slots_total,
        "slots_used": len(modules),
        "slots_free": upgrade["free_slots"],
        "max_bytes": max_bytes if upgrade["max_ok"] else None,
        "max_text": _gb_text(max_bytes) if upgrade["max_ok"] else NA,
        "headroom_gb": upgrade["headroom_gb"],
        "soldered": soldering,
        "channel": channel,
        "verdict": upgrade["verdict"],
        "percent_used": percent,
        "need": need,
        "plan": plan,
    }


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
def classify_drive(media: str, bus: str) -> str:
    """'nvme' | 'ssd' | 'hdd' | 'emmc' | 'unknown'."""
    media_l, bus_l = media.lower(), bus.lower()
    if bus_l == "mmc":
        return "emmc"
    if bus_l == "nvme":
        return "nvme"
    if media_l == "hdd":
        return "hdd"
    if media_l == "ssd":
        return "ssd"
    return "unknown"


_DRIVE_LABELS = {
    "nvme": "NVMe SSD",
    "ssd": "SATA SSD",
    "hdd": "Hard disk (HDD)",
    "emmc": "eMMC (soldered)",
    "unknown": NA,
}


def parse_drives(raw_disks: Any) -> list[dict[str, Any]]:
    """Internal drives only (USB sticks, SD cards and virtual disks are left out)."""
    drives: list[dict[str, Any]] = []
    for row in as_list(raw_disks):
        if not isinstance(row, dict):
            continue
        bus = clean_text(row.get("BusType"))
        if bus.lower() in _EXTERNAL_BUSES:
            continue
        media = clean_text(row.get("MediaType"))
        size = _int(row.get("Size"))
        kind = classify_drive(media, bus)
        drives.append(
            {
                "model": clean_text(row.get("FriendlyName")),
                "kind": kind,
                "type": _DRIVE_LABELS[kind],
                "bus": bus,
                "size_bytes": size,
                "size": format_bytes(size, 0) if size else NA,
            }
        )
    return drives


def parse_m2_slots(raw_slots: Any) -> dict[str, Any]:
    """M.2 slots as reported by the firmware. Many laptops report none: then known=False."""
    total = free = 0
    for row in as_list(raw_slots):
        if not isinstance(row, dict):
            continue
        name = str(row.get("SlotDesignation") or "")
        if not re.search(r"\bM\.?2\b", name, re.IGNORECASE):
            continue
        total += 1
        if _int(row.get("CurrentUsage")) == 3:  # 3 = available
            free += 1
    return {"known": total > 0, "total": total, "free": free}


def next_ssd_size_gb(current_gb: float) -> int:
    """The next sensible SSD size: roughly double what you have now (256 -> 512, 512 -> 1000)."""
    for size in STANDARD_SSD_GB:
        if size >= current_gb * 1.8:
            return size
    return STANDARD_SSD_GB[-1]


def ssd_size_label(size_gb: int) -> str:
    """256 -> '256 GB', 1000 -> '1 TB'."""
    return f"{size_gb // 1000} TB" if size_gb >= 1000 else f"{size_gb} GB"


def system_volume(volumes: list[dict[str, Any]], drive: str | None = None) -> dict[str, Any] | None:
    """The volume Windows is installed on (C: normally). Falls back to the first volume."""
    letter = (drive or os.environ.get("SystemDrive") or "C:").upper().rstrip("\\/")
    for vol in volumes:
        if str(vol.get("mount", "")).upper().rstrip("\\/") == letter:
            return vol
    return volumes[0] if volumes else None


def assess_storage(raw: dict[str, Any], volumes: list[dict[str, Any]], system_drive: str | None = None) -> dict[str, Any]:
    """Drive types, free space and slots, plus a verdict: add / replace / no / unknown."""
    drives = parse_drives(raw.get("disk"))
    m2 = parse_m2_slots(raw.get("slot"))
    kinds = {d["kind"] for d in drives}
    vol = system_volume(volumes, system_drive)
    free_percent = (100 - float(vol["percent"])) if vol and vol.get("percent") is not None else None
    free_bytes = vol.get("free") if vol else None

    if kinds == {"emmc"}:
        verdict = "no"
    elif m2["known"] and m2["free"] > 0:
        verdict = "add"
    elif drives:
        verdict = "replace"
    else:
        verdict = "unknown"

    largest = max((d["size_bytes"] or 0 for d in drives), default=0)
    return {
        "drives": drives,
        "has_hdd": "hdd" in kinds,
        "has_ssd": bool(kinds & {"ssd", "nvme"}),
        "has_nvme": "nvme" in kinds,
        "m2": m2,
        "system_volume": vol,
        "free_percent": free_percent,
        "free_bytes": free_bytes,
        "free_text": format_bytes(free_bytes) if free_bytes is not None else NA,
        "largest_gb": largest / GIB if largest else None,
        "verdict": verdict,
    }


# --------------------------------------------------------------------------
# Advice list
# --------------------------------------------------------------------------
def ram_recommendations(ram: dict[str, Any]) -> list[dict[str, str]]:
    """Advice lines about RAM: [{"key", "level", "title", "detail"}]."""
    need, verdict = ram["need"], ram["verdict"]

    if ram["soldered"] == "all":
        lpddr = any(m["type"].startswith("LPDDR") for m in ram["modules"])
        extra = " (A few newer laptops use a removable LPCAMM2 module: check your model's manual.)" if lpddr else ""
        if need["target_gb"]:
            return [_rec("ram", need["level"], f"RAM: {need['reason']}", f"The memory is soldered to the motherboard, so it cannot be upgraded.{extra} Close heavy apps, or buy a laptop with more RAM next time.")]
        return [_rec("ram", "good", f"RAM: {need['reason']}", f"The memory is soldered, so it cannot be upgraded - but you do not need more.{extra}")]

    title = f"RAM: {need['reason']}"
    if need["target_gb"] is None:
        detail = {
            "yes": f"If you ever want more: {ram['slots_free']} free slot(s), up to {ram['max_text']} supported.",
            "replace": f"If you ever want more: swap the sticks for bigger ones (up to {ram['max_text']} supported).",
            "no": "This is already the most the laptop supports.",
        }.get(verdict, "Nothing to do.")
        return [_rec("ram", "good", title, detail)]

    if verdict in ("yes", "replace"):
        limit = f" The firmware says the maximum is {ram['max_text']}." if ram["max_text"] != NA else ""
        return [_rec("ram", need["level"], title, f"Upgrading to {need['target_gb']} GB is possible. {ram['plan']}{limit}")]
    if verdict == "no":
        return [_rec("ram", need["level"], title, f"The laptop is already at its supported maximum ({ram['max_text']}), so RAM cannot go higher.")]
    return [
        _rec(
            "ram",
            need["level"],
            title,
            f"LapForge could not read the memory slots, so it cannot say for sure. Check the service manual for your model before buying ({need['target_gb']} GB is the goal).",
        )
    ]


def storage_recommendations(storage: dict[str, Any], system: dict[str, Any]) -> list[dict[str, str]]:
    """Advice lines about the drives: [{"key", "level", "title", "detail"}]."""
    recs: list[dict[str, str]] = []
    drives = storage["drives"]
    hint = system.get("search_hint") or ""
    hint_text = f' Search for: "{hint}".' if hint else ""

    if not drives:
        recs.append(_rec("drive-type", "muted", "Storage: drive type could not be read", "Windows did not report the drive type. Try again in an Administrator terminal."))
    elif storage["verdict"] == "no":
        recs.append(_rec("drive-type", "warn", "Storage: eMMC drive (soldered)", "eMMC storage is part of the motherboard and cannot be replaced. Use an external SSD or a large microSD card for extra space."))
    elif storage["has_hdd"] and not storage["has_ssd"]:
        recs.append(_rec("drive-type", "bad", "Storage: you have a hard disk (HDD) - an SSD is the biggest speed-up", "Replacing it with an SSD makes start-up and apps several times faster. Most laptops with an HDD take a 2.5\" SATA SSD in the same bay. Clone the old drive or reinstall Windows, and back up your files first."))
    elif storage["has_hdd"]:
        recs.append(_rec("drive-type", "good", "Storage: SSD + HDD", "You already have an SSD. Keep Windows and your apps on the SSD and use the HDD for large files."))
    elif storage["has_nvme"]:
        recs.append(_rec("drive-type", "good", "Storage: NVMe SSD (the fastest kind)", "Nothing to gain in speed. Only upgrade it if you need more space."))
    else:
        recs.append(_rec("drive-type", "good", "Storage: SATA SSD (good)", "An NVMe SSD is faster, but it only fits if your laptop has an M.2 NVMe slot. Check the service manual before buying."))

    vol, free_pct = storage["system_volume"], storage["free_percent"]
    if vol is not None and free_pct is not None:
        free_bytes = storage["free_bytes"] or 0
        reference_gb = storage["largest_gb"] or (vol.get("total") or 0) / GIB
        can_swap = storage["verdict"] in ("replace", "add") and reference_gb
        goal = f" If it stays tight, move to a {ssd_size_label(next_ssd_size_gb(reference_gb))} drive." if can_swap else ""
        if free_bytes < 15 * GIB or (free_pct < 10 and free_bytes < 50 * GIB):
            recs.append(_rec("space", "bad", f"Storage: {vol['mount']} is almost full ({storage['free_text']} free)", f"Free space first with 'lapforge clean'. Windows slows down when the system drive is nearly full.{goal}"))
        elif free_pct < 20 and free_bytes < 60 * GIB:
            recs.append(_rec("space", "warn", f"Storage: {vol['mount']} is getting full ({storage['free_text']} free)", f"Run 'lapforge clean' to free space.{goal}"))
        else:
            recs.append(_rec("space", "good", f"Storage: {vol['mount']} has enough free space ({storage['free_text']} free)", "No storage upgrade is needed for space."))

    m2 = storage["m2"]
    if storage["verdict"] == "no" or not drives:
        return recs
    if m2["known"] and m2["free"] > 0:
        recs.append(_rec("slot", "good", f"Storage: the firmware lists {m2['free']} free M.2 slot(s)", "You can ADD a second SSD without replacing the current one. Confirm the slot type (NVMe or SATA) in the manual."))
    elif m2["known"]:
        recs.append(_rec("slot", "muted", "Storage: M.2 slot(s) are all in use", "To get more space, replace the current SSD with a bigger one."))
    else:
        recs.append(_rec("slot", "muted", "Storage: free drive slots cannot be detected by software", f"Most laptops do not tell Windows about empty slots. Look in the service manual for a second M.2 slot or a 2.5\" bay.{hint_text}"))
    return recs


def worst_level(recs: list[dict[str, str]]) -> str:
    """The most serious level in the list ('bad' > 'warn' > 'good'); 'muted' when nothing is rated."""
    levels = [r["level"] for r in recs if r["level"] in LEVEL_ORDER and r["level"] != "muted"]
    return min(levels, key=lambda lv: LEVEL_ORDER[lv]) if levels else "muted"


def build_headline(ram: dict[str, Any], storage: dict[str, Any]) -> str:
    """One line for the top of the screen."""
    ram_part = {
        "yes": f"RAM: can be upgraded (up to {ram['max_text']})" if ram["max_text"] != NA else "RAM: can be upgraded",
        "replace": "RAM: can be upgraded by replacing the sticks",
        "no": "RAM: cannot be upgraded" + (" (soldered)" if ram["soldered"] == "all" else ""),
        "unknown": "RAM: upgrade not confirmed",
    }[ram["verdict"]]
    storage_part = {
        "add": "SSD: a free M.2 slot exists",
        "replace": "SSD: can be replaced with a bigger/faster one",
        "no": "SSD: cannot be replaced (eMMC)",
        "unknown": "SSD: unknown",
    }[storage["verdict"]]
    return f"{ram_part}  |  {storage_part}"


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def _unavailable(note: str) -> dict[str, Any]:
    return {
        "available": False,
        "note": note,
        "system": {"brand": NA, "model": NA, "search_hint": ""},
        "ram": None,
        "storage": None,
        "recommendations": [],
        "level": "muted",
        "headline": "",
    }


def get_upgrade_report(
    raw: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    storage_info: dict[str, Any] | None = None,
    system: dict[str, Any] | None = None,
    system_drive: str | None = None,
) -> dict[str, Any]:
    """Full upgrade report for `lapforge upgrade`. Read-only; never raises.

    Returns {"available", "note", "system", "ram", "storage", "recommendations", "level", "headline"}.
    The arguments exist so tests (and other systems) can supply the data; normally call it with none.
    """
    try:
        if raw is None:
            if not IS_WINDOWS:
                return _unavailable("The upgrade advisor needs Windows to read memory slots and drives (not available on this system).")
            raw = _collect_raw()
            if not raw:
                return _unavailable("Windows did not return the hardware details. Try again, or run in an Administrator terminal.")
        mem = memory if memory is not None else get_memory_info()
        stor = storage_info if storage_info is not None else get_storage_info()
        sysinfo = system if system is not None else get_system_info()

        brand, model = clean_text(sysinfo.get("brand")), clean_text(sysinfo.get("model"))
        words = [w for w in (brand, model) if w != NA]
        system_out = {
            "brand": brand,
            "model": model,
            "search_hint": f"{' '.join(words)} RAM SSD upgrade service manual" if words else "",
        }

        ram = assess_ram(raw, mem)
        storage = assess_storage(raw, stor.get("volumes") or [], system_drive)
        recs = ram_recommendations(ram) + storage_recommendations(storage, system_out)
        recs.sort(key=lambda r: LEVEL_ORDER.get(r["level"], 3))
        return {
            "available": True,
            "note": "",
            "system": system_out,
            "ram": ram,
            "storage": storage,
            "recommendations": recs,
            "level": worst_level(recs),
            "headline": build_headline(ram, storage),
        }
    except Exception as exc:  # an advisor must never crash the tool
        return _unavailable(f"Could not build the upgrade report: {exc}")


__all__ = [
    "assess_ram",
    "assess_storage",
    "build_headline",
    "can_upgrade_ram",
    "classify_drive",
    "classify_soldering",
    "get_upgrade_report",
    "next_ssd_size_gb",
    "parse_drives",
    "parse_m2_slots",
    "parse_ram_array",
    "parse_ram_modules",
    "plan_ram_upgrade",
    "ram_need",
    "ram_recommendations",
    "ssd_size_label",
    "storage_recommendations",
    "system_volume",
]
