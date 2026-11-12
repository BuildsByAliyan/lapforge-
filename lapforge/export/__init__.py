"""LapForge export: a full system snapshot as JSON, Markdown or HTML.

    snapshot = collect_snapshot()          # read the system (nothing is written)
    paths = write_exports(snapshot)        # default folder: see default_output_dir()
    paths = export_all("reports")          # both steps in one call

Serial numbers, MAC addresses, IP addresses and the computer (host) name are hidden
unless `show_private=True` is passed. Saved WiFi names and passwords are never included.
Files are written on this computer only; nothing is sent anywhere.
"""

from __future__ import annotations

import copy
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import __app_name__, __author__, __tagline__, __version__
from ..collectors import (
    collect_hardware,
    get_battery_health,
    get_bios_info,
    get_disk_health,
    get_network_interfaces,
    get_temperatures,
)
from ..collectors.security import get_security_report
from ..collectors.software import get_drivers, get_installed_apps, get_startup_apps
from ..config import load_config
from ..doctor import run_doctor
from ..utils import NA
from .html_export import to_html
from .json_export import SCHEMA_VERSION, make_json_safe, to_json, validate_snapshot
from .markdown_export import snapshot_sections, to_markdown

FORMATS = ("json", "md", "html")
EXTENSIONS = {"json": ".json", "md": ".md", "html": ".html"}
REDACTED = "Hidden (redacted)"

# Any value stored under one of these keys is hidden unless show_private=True.
PRIVATE_KEYS = frozenset(
    {
        "serial",
        "board_serial",
        "serial_number",
        "mac",
        "mac_address",
        "ipv4",
        "ipv6",
        "ip",
        "ip_address",
        "uuid",
        "hostname",
        "host_name",
    }
)
# Startup entries keep their command line out of reports (it can contain user names and paths).
_STARTUP_DROP = ("command",)


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------
def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (REDACTED if k in PRIVATE_KEYS and v not in (None, "", NA) else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def redact_snapshot(snapshot: dict[str, Any], show_private: bool = False) -> dict[str, Any]:
    """Return a copy of the snapshot with serial / MAC / IP values hidden (unless show_private)."""
    result = copy.deepcopy(snapshot)
    if not show_private:
        result = _scrub(result)
    result["privacy"] = {
        "private_details_hidden": not show_private,
        "hidden_fields": [] if show_private else sorted(PRIVATE_KEYS),
        "note": (
            "Serial numbers, MAC addresses, IP addresses and the computer name are shown."
            if show_private
            else "Serial numbers, MAC addresses, IP addresses and the computer name are hidden."
        ),
    }
    return result


# --------------------------------------------------------------------------
# Collect
# --------------------------------------------------------------------------
def _safe(func: Callable[[], Any]) -> Any:
    try:
        return func()
    except Exception:
        return None


def collect_snapshot(
    show_private: bool = False,
    include_apps: bool = True,
    include_drivers: bool = False,
) -> dict[str, Any]:
    """Read the whole system into one snapshot dict. Writes nothing. Never raises.

    Drivers are off by default because reading them can take up to a minute.
    """
    jobs: dict[str, Callable[[], Any]] = {
        "hardware": collect_hardware,
        "bios": get_bios_info,
        "battery_health": get_battery_health,
        "disk_health": get_disk_health,
        "temperatures": get_temperatures,
        "interfaces": get_network_interfaces,
        "security": get_security_report,
        "startup": get_startup_apps,
    }
    if include_apps:
        jobs["apps"] = get_installed_apps
    if include_drivers:
        jobs["drivers"] = get_drivers
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {name: pool.submit(_safe, fn) for name, fn in jobs.items()}
        data = {name: fut.result() for name, fut in futures.items()}

    # Every collector was read once above. The doctor judges that same data (no second reading).
    # A collector that failed is passed as {} so the doctor shows "not available" for it.
    def _given(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    hardware_data = _given(data["hardware"])
    data["doctor"] = _safe(
        lambda: run_doctor(
            storage=_given(hardware_data.get("storage")),
            memory=_given(hardware_data.get("memory")),
            battery=_given(data["battery_health"]),
            startup=_given(data["startup"]),
            security=_given(data["security"]),
            disk=_given(data["disk_health"]),
            temps=_given(data["temperatures"]),
        )
    )

    hardware = data["hardware"] if isinstance(data["hardware"], dict) else {}
    doctor = data["doctor"] if isinstance(data["doctor"], dict) else {}
    startup = data["startup"] if isinstance(data["startup"], dict) else {"available": False, "items": [], "note": "Not available"}
    startup = {
        **startup,
        "items": [{k: v for k, v in item.items() if k not in _STARTUP_DROP} for item in startup.get("items", []) if isinstance(item, dict)],
    }
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": __app_name__, "version": __version__, "author": __author__, "tagline": __tagline__},
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "privacy": {},
        "system": hardware.get("system", {}),
        "os": hardware.get("os", {}),
        "bios": data["bios"] or {},
        "cpu": hardware.get("cpu", {}),
        "memory": hardware.get("memory", {}),
        "storage": hardware.get("storage", {}),
        "gpu": hardware.get("gpu", []),
        "battery": {"charge": hardware.get("battery", {}), "health": data["battery_health"] or {}},
        "disk_health": data["disk_health"] or {},
        "temperatures": data["temperatures"] or {},
        "network": {"interfaces": data["interfaces"] or []},
        "security": data["security"] or {},
        "startup": startup,
        "health": {
            "score": doctor.get("score", {}),
            "checks": doctor.get("checks", []),
            "problem_ids": [p.get("id") for p in doctor.get("problems", []) if isinstance(p, dict)],
        },
    }
    if include_apps:
        snapshot["apps"] = data["apps"] or {"available": False, "apps": [], "note": "Not available"}
    if include_drivers:
        snapshot["drivers"] = data["drivers"] or {"available": False, "drivers": [], "note": "Not available"}
    return redact_snapshot(snapshot, show_private)


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
def _system_folders() -> set[str]:
    """Windows system and program folders (empty set on other systems)."""
    folders: set[str] = set()
    for var in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(var)
        if value:
            folders.add(os.path.normcase(os.path.abspath(value)))
    return folders


def is_system_folder(path: str | Path) -> bool:
    """True if `path` is, or is inside, a Windows system or program folder."""
    target = os.path.normcase(os.path.abspath(str(path)))
    return any(target == guard or target.startswith(guard + os.sep) for guard in _system_folders())


def choose_report_dir(configured: str, home: Path | None, cwd: Path) -> Path:
    """Pick the folder for saved reports.

    1. The folder from the `report_dir` setting, if one is set.
    2. Otherwise "<home>/LapForge/Reports".
    3. If there is no usable home folder, a "reports" folder in the current folder,
       but never when the current folder is a Windows system folder.
    4. Last resort: a LapForge folder in the temporary folder.
    """
    if configured:
        return Path(configured)
    if home is not None and not is_system_folder(home):
        return home / "LapForge" / "Reports"
    if not is_system_folder(cwd):
        return cwd / "reports"
    return Path(tempfile.gettempdir()) / "LapForge" / "Reports"


def default_output_dir() -> Path:
    """Where reports are saved when no folder is given (the folder is created when writing)."""
    try:
        home: Path | None = Path.home()
    except (RuntimeError, KeyError, OSError):
        home = None
    try:
        cwd = Path.cwd()
    except OSError:
        cwd = Path(tempfile.gettempdir())
    return choose_report_dir(load_config()["report_dir"], home, cwd)


def render_snapshot(snapshot: dict[str, Any], fmt: str) -> str:
    """The snapshot as text in one format: 'json', 'md' or 'html'."""
    if fmt == "json":
        return to_json(snapshot)
    if fmt == "md":
        return to_markdown(snapshot)
    if fmt == "html":
        return to_html(snapshot)
    raise ValueError(f"format must be one of {FORMATS}, not {fmt!r}")


def _normalise_formats(formats: Iterable[str] | str) -> list[str]:
    wanted = [formats] if isinstance(formats, str) else list(formats)
    chosen: list[str] = []
    for fmt in wanted:
        fmt = "md" if fmt == "markdown" else fmt
        if fmt not in FORMATS:
            raise ValueError(f"format must be one of {FORMATS}, not {fmt!r}")
        if fmt not in chosen:
            chosen.append(fmt)
    return chosen


def write_exports(
    snapshot: dict[str, Any],
    directory: str | Path | None = None,
    formats: Iterable[str] | str = FORMATS,
    show_private: bool = False,
) -> dict[str, Path]:
    """Write a snapshot to files and return {format: path}.

    The snapshot is redacted again here (unless show_private), so private details
    cannot slip into a file even if the snapshot was built with them.
    """
    chosen = _normalise_formats(formats)
    folder = Path(directory) if directory else default_output_dir()
    folder.mkdir(parents=True, exist_ok=True)
    safe = redact_snapshot(snapshot, show_private)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    paths: dict[str, Path] = {}
    for fmt in chosen:
        target = folder / f"lapforge-report-{stamp}{EXTENSIONS[fmt]}"
        number = 1
        while target.exists():
            number += 1
            target = folder / f"lapforge-report-{stamp}-{number}{EXTENSIONS[fmt]}"
        target.write_text(render_snapshot(safe, fmt), encoding="utf-8", newline="\n")
        paths[fmt] = target
    return paths


def export_all(
    directory: str | Path | None = None,
    formats: Iterable[str] | str = FORMATS,
    show_private: bool = False,
    include_apps: bool = True,
    include_drivers: bool = False,
) -> dict[str, Path]:
    """Collect a fresh snapshot and write it as JSON, Markdown and HTML. Returns {format: path}."""
    _normalise_formats(formats)  # fail early on a bad format, before the slow scan
    snapshot = collect_snapshot(show_private, include_apps, include_drivers)
    return write_exports(snapshot, directory, formats, show_private)


__all__ = [
    "EXTENSIONS",
    "FORMATS",
    "PRIVATE_KEYS",
    "REDACTED",
    "SCHEMA_VERSION",
    "collect_snapshot",
    "choose_report_dir",
    "default_output_dir",
    "export_all",
    "is_system_folder",
    "make_json_safe",
    "redact_snapshot",
    "render_snapshot",
    "snapshot_sections",
    "to_html",
    "to_json",
    "to_markdown",
    "validate_snapshot",
    "write_exports",
]
