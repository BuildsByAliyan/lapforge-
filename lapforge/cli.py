"""LapForge command line interface (argparse)."""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.text import Text

from . import __app_name__, __version__
from .collectors import (
    collect_hardware,
    get_battery_health,
    get_bios_info,
    get_current_wifi,
    get_disk_health,
    get_saved_wifi,
    get_temperatures,
    redact,
    run_speed_test,
    temp_level,
)
from .collectors.security import get_security_report
from .cleaner import build_preview, clean_target, cleaner_supported, get_targets, preview_table
from .cleaner.browser_cache import browser_name, browser_targets, detected_open_browsers, open_browser_notes
from .cleaner.duplicates import DEFAULT_MAX_FILES, duplicates_table, find_duplicates
from .cleaner.logger import write_log, RunLog
from .cleaner.repair import guided_repair, repair_plan
from .cleaner.report import build_clean_report, disk_snapshot, render_clean_report, report_lines
from .config import config_path, describe_config, load_config, reset_config, set_setting
from .doctor import run_doctor
from . import elevate
from .export import collect_snapshot, default_output_dir, redact_snapshot, render_snapshot, write_exports
from .optimizer import change_startup_item, get_upgrade_report, list_changes, undo_change
from .optimizer.startup_manager import is_protected
from .smartscan import run_smart_scan, should_offer_clean
from .update_check import check_for_update, update_notice
from .collectors.software import get_drivers, get_installed_apps, get_startup_apps, sort_apps
from .ui.banner import print_banner
from .ui.dashboard import show_dashboard
from .ui.errors import show_error
from .ui.health_view import exit_code_for, render_health
from .ui.help_view import render_commands, suggest_command, take_tip, unknown_command_message
from .ui.menu import run_menu
from .ui.scan_view import render_scan
from .ui.live import run_live, show_snapshot
from .ui.tables import (
    apps_table,
    battery_text,
    current_wifi_table,
    drivers_table,
    grid_table,
    health_text,
    kv_table,
    level_summary,
    security_table,
    speed_table,
    startup_table,
    wifi_profiles_table,
)
from .ui.theme import LAPFORGE_THEME, status_text, usage_bar
from .utils import NA, format_bytes, is_admin


APPS_LIMIT = 40
DRIVERS_LIMIT = 25

# Short names for `lapforge startup --source ...` -> the exact source text used by the startup list.
_STARTUP_SOURCES = {
    "registry-user": "Registry (this user)",
    "registry-all": "Registry (all users)",
    "folder-user": "Startup folder (this user)",
    "folder-all": "Startup folder (all users)",
}

_REPORT_EXTENSIONS = {".html": "html", ".htm": "html", ".json": "json", ".md": "md", ".markdown": "md"}


HELP_EXAMPLES = """Examples:
  lapforge                    Open the menu (no commands to remember)
  lapforge scan               Check everything and see what to do next
  lapforge health             Health score with plain-English fixes
  lapforge clean --dry-run    Preview the junk files, delete nothing
  lapforge report             Save a report you can keep or share

New here? Run 'lapforge commands' for a short list of every command."""


def make_console() -> Console:
    """Console that follows the saved theme setting ('dark' by default, 'mono' = no colours)."""
    if load_config()["theme"] == "mono":
        return Console(theme=LAPFORGE_THEME, highlight=False, no_color=True)
    return Console(theme=LAPFORGE_THEME, highlight=False)


def _interval(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not 0.5 <= number <= 10:
        raise argparse.ArgumentTypeError("must be between 0.5 and 10 seconds")
    return number


def _top_count(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if not 3 <= number <= 10:
        raise argparse.ArgumentTypeError("must be between 3 and 10")
    return number


def _min_size_mb(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not 0 < number <= 1_000_000:
        raise argparse.ArgumentTypeError("must be greater than 0 MB")
    return number


def _max_files(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if not 1 <= number <= 10_000_000:
        raise argparse.ArgumentTypeError("must be between 1 and 10,000,000")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lapforge",
        description="LapForge - One command. Every detail of your laptop. "
        "Run without a command to open the menu.",
        epilog=HELP_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"{__app_name__} {__version__}"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show technical details if something goes wrong (can be placed before or after the command)",
    )
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Run this command as Administrator (Windows shows its permission prompt). Can be placed before or after the command",
    )
    parser.add_argument(elevate.ELEVATED_FLAG, action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    info = sub.add_parser("info", help="Show full laptop details (hardware + OS)")
    info.add_argument(
        "--show-serial",
        action="store_true",
        help="Show the device serial number (hidden by default)",
    )

    wifi = sub.add_parser("wifi", help="Show saved WiFi networks of this PC (password only with --reveal)")
    wifi.add_argument(
        "--reveal",
        action="store_true",
        help="Show saved WiFi passwords (this PC only; needs an Administrator terminal)",
    )

    sub.add_parser("speed", help="Ping and latency test (internet connection quality)")

    commands = sub.add_parser("commands", aliases=["help"], help="Show a short list of every command, grouped by what it does")
    commands.add_argument("topic", nargs="?", metavar="command", help="Show the options of one command, for example: lapforge help clean")

    sub.add_parser("menu", help="Open the menu: press a number, no commands to remember (opens by itself when you run lapforge in a terminal)")
    sub.add_parser("dashboard", help="Show the dashboard (a one-screen summary of this laptop)")

    sub.add_parser(
        "scan",
        aliases=["check"],
        help="Smart Scan (recommended): checks everything and tells you what to do next. Only looks, never changes anything",
    )

    health = sub.add_parser("health", help="Health score (0-100), checks table and fix hints")
    health.add_argument(
        "--fail-on",
        choices=("never", "warn", "bad"),
        default="never",
        help="Exit with code 1 if a warning (warn) or a serious problem (bad) is found. Default: never",
    )

    clean = sub.add_parser("clean", help="Clean junk files: preview -> confirm -> clean -> before/after report")
    clean.add_argument("--full", action="store_true", help="Full clean (adds Prefetch, update cache, thumbnails, Recycle Bin)")
    clean.add_argument("--browser", action="store_true", help="Also clear Chrome/Edge cache (never cookies, passwords or history)")
    clean.add_argument("--dry-run", action="store_true", help="Only show the preview, delete nothing")
    clean.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation (the preview is still shown)")

    duplicates = sub.add_parser(
        "duplicates",
        help="Find duplicate files (report only, nothing is ever deleted)",
    )
    duplicates.add_argument(
        "--path",
        action="append",
        metavar="FOLDER",
        help="Folder to scan. Can be used more than once. Default: your Downloads folder",
    )
    duplicates.add_argument(
        "--min-size",
        type=_min_size_mb,
        default=1.0,
        metavar="MB",
        help="Ignore files smaller than MB megabytes (default 1)",
    )
    duplicates.add_argument(
        "--max-files",
        type=_max_files,
        default=DEFAULT_MAX_FILES,
        metavar="N",
        help=f"Stop after looking at N files (default {DEFAULT_MAX_FILES})",
    )

    repair = sub.add_parser("repair", help="Guided SFC + DISM system file repair (shows the plan unless --run)")
    repair.add_argument(
        "--mode",
        choices=("check", "repair"),
        default="check",
        help="check = read-only scan (default); repair = DISM RestoreHealth then SFC /scannow",
    )
    repair.add_argument("--run", action="store_true", help="Actually run it (Administrator terminal needed). Default: only show the plan")
    repair.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation when using --run")

    live = sub.add_parser("live", help="Live monitor: CPU, memory, disk, network and the apps using the most")
    live.add_argument("--interval", type=_interval, default=1.0, metavar="SECONDS", help="Refresh every SECONDS (0.5 to 10, default 1)")
    live.add_argument("--top", type=_top_count, default=5, metavar="N", help="Apps to list in each table (3 to 10, default 5)")
    live.add_argument("--duration", type=float, metavar="SECONDS", help="Stop by itself after SECONDS (default: run until Ctrl+C)")
    live.add_argument("--once", action="store_true", help="No live screen: watch for a few seconds, print what is slowing the laptop down, then exit")

    report = sub.add_parser("report", help="Save a full system report as HTML, JSON or Markdown")
    report.add_argument(
        "--format",
        choices=("html", "json", "md", "all"),
        default=None,
        help="Report format (default: html, or the one matching the --output file name)",
    )
    report.add_argument("-o", "--output", metavar="PATH", help="A folder, or a file ending in .html / .json / .md")
    report.add_argument("--show-private", action="store_true", help="Include serial numbers, MAC and IP addresses (hidden by default)")
    report.add_argument("--no-apps", action="store_true", help="Leave the installed apps list out")
    report.add_argument("--drivers", action="store_true", help="Include the driver list (can take up to a minute)")
    report.add_argument("--open", action="store_true", help="Open the HTML report in your browser when done")
    report.add_argument("--force", action="store_true", help="Overwrite the --output file if it already exists")

    config = sub.add_parser("config", help="Show or change LapForge settings (theme, language, update checks, report folder)")
    config_sub = config.add_subparsers(dest="config_action", metavar="<action>")
    config_sub.add_parser("show", help="Show all settings (default)")
    config_set = config_sub.add_parser("set", help="Change one setting")
    config_set.add_argument("key", help="theme | language | check_updates | report_dir | show_tips")
    config_set.add_argument("value", help="New value (for example: lapforge config set theme mono)")
    config_sub.add_parser("reset", help="Go back to the default settings")
    config_sub.add_parser("path", help="Show where the settings file is")

    sub.add_parser("update", help="Check GitHub now for a newer LapForge (one read-only request, nothing is sent about you)")

    sub.add_parser("security", help="Defender, firewall, BitLocker, TPM, Secure Boot, UAC")

    apps = sub.add_parser("apps", help="List installed apps")
    apps.add_argument("--sort", choices=("name", "size", "date"), default="name", help="Sort order (default: name)")
    apps.add_argument("--search", metavar="TEXT", help="Only show apps whose name or publisher contains TEXT")
    apps.add_argument("--all", action="store_true", help=f"Show every app (default: first {APPS_LIMIT})")

    startup = sub.add_parser(
        "startup",
        help="Programs that start with Windows (heaviest first); --manage switches them on/off safely",
    )
    startup.add_argument("--manage", action="store_true", help="Interactive: switch programs on/off with preview, backup, restore point and Undo")
    startup.add_argument("--disable", metavar="NAME", help="Turn the startup program NAME off (preview only unless --apply)")
    startup.add_argument("--enable", metavar="NAME", help="Turn the startup program NAME back on (preview only unless --apply)")
    startup.add_argument(
        "--source",
        choices=tuple(_STARTUP_SOURCES),
        help="Which entry, if the same name exists in more than one place (registry-user, registry-all, folder-user, folder-all)",
    )
    startup.add_argument("--undo", nargs="?", const="latest", metavar="CHANGE_ID", help="Undo a change (default: the newest one). Preview only unless --apply")
    startup.add_argument("--history", action="store_true", help="List the changes LapForge made (each one can be undone)")
    startup.add_argument("--apply", action="store_true", help="Really do the --disable / --enable / --undo (default: only show what would happen)")
    startup.add_argument("--no-restore-point", action="store_true", help="Do not make a System Restore point before the change (the backup is still saved)")
    startup.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation (the preview is still shown)")

    drivers = sub.add_parser("drivers", help="Driver ages (oldest first)")
    drivers.add_argument("--all", action="store_true", help=f"Show every driver (default: oldest {DRIVERS_LIMIT})")

    sub.add_parser("upgrade", help="Upgrade advisor: can this laptop take more RAM or a bigger / faster SSD? (read-only)")
    return parser


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _mwh(value: int | None) -> str:
    return f"{value:,} mWh" if value else NA


def _disk_health_table(report: dict[str, Any]):
    table = grid_table("Physical drives", ["Model", "Type", "Size", "Health", "Temp", "Life used", "Power-on"])
    for d in report["disks"]:
        temp = d["temperature_c"]
        wear = d["wear_percent"]
        hours = d["power_on_hours"]
        table.add_row(
            d["model"],
            d["type"],
            d["size"],
            status_text(d["level"], d["status"]),
            Text(f"{temp:.0f} C", style=temp_level(temp)) if temp is not None else Text(NA, style="muted"),
            f"{wear:.0f}%" if wear is not None else NA,
            f"{hours:,} h" if hours is not None else NA,
        )
    return table


def _temperature_table(temps: dict[str, Any], disks: dict[str, Any]):
    rows: list[tuple[str, Any]] = []
    for sensor in temps["sensors"]:
        rows.append((sensor["label"], Text(f"{sensor['current_c']:.1f} C", style=sensor["level"])))
    for d in disks["disks"]:
        if d["temperature_c"] is not None:
            rows.append((f"Drive: {d['model']}", Text(f"{d['temperature_c']:.0f} C", style=temp_level(d["temperature_c"]))))
    if not rows:
        rows.append(("Sensors", "Not available (this laptop does not expose temperatures to the OS)"))
    return kv_table("Temperatures", rows)


def _safe_value(func):
    """Run an optional collector; None if it fails (its section then shows N/A)."""
    try:
        return func()
    except Exception:
        return None


def _print_section(console, title: str, build) -> None:
    """Print one info section. If it cannot be built, show N/A for this section only."""
    try:
        build()
    except Exception:
        console.print(kv_table(title, [("Details", f"{NA} (could not be read on this PC)")]))


def render_info(console, data: dict[str, Any], show_serial: bool = False) -> None:
    system, os_info = data["system"], data["os"]
    cpu, mem = data["cpu"], data["memory"]
    storage, gpus, batt = data["storage"], data["gpu"], data["battery"]

    # Each extra collector is read on its own; a failure gives None and N/A in its section.
    bios = _safe_value(get_bios_info)
    health = _safe_value(get_battery_health)
    disks = _safe_value(get_disk_health)
    temps = _safe_value(get_temperatures)

    def system_section() -> None:
        console.print(
            kv_table(
                "System",
                [
                    ("Brand", system["brand"]),
                    ("Model", system["model"]),
                    ("Serial number", redact(system["serial"], show_serial)),
                    ("Host name", system["hostname"]),
                    ("Operating system", os_info["name"]),
                    ("OS version", f"{os_info['version']} (build {os_info['build']})"),
                    ("Architecture", os_info["architecture"]),
                    ("Uptime", os_info["uptime"]),
                ],
            )
        )

    def bios_section() -> None:
        console.print(
            kv_table(
                "BIOS & Motherboard",
                [
                    ("BIOS vendor", bios["vendor"]),
                    ("BIOS version", bios["version"]),
                    ("BIOS date", bios["release_date"]),
                    ("SMBIOS", bios["smbios"]),
                    ("Boot mode", bios["mode"]),
                    ("Secure Boot", bios["secure_boot"]),
                    ("Motherboard", " ".join(p for p in (bios["board_vendor"], bios["board_model"]) if p != NA) or NA),
                    ("Board serial", redact(bios["board_serial"], show_serial)),
                ],
            )
        )

    def processor_section() -> None:
        console.print(
            kv_table(
                "Processor",
                [
                    ("Name", cpu["name"]),
                    ("Generation", cpu["generation"]),
                    ("Cores / Threads", f"{cpu['cores']} / {cpu['threads']}"),
                    ("Rated speed", cpu["max_speed"]),
                    ("Current load", usage_bar(cpu["usage_percent"])),
                ],
            )
        )

    def memory_section() -> None:
        ram_rows: list[tuple[str, Any]] = [
            ("Total", mem["total_text"]),
            ("Type / Speed", f"{mem['type']} / {mem['speed']}"),
            ("In use", usage_bar(mem["percent"])),
        ]
        if mem["modules"]:
            sticks = ", ".join(f"{m['capacity']} {m['type']}" for m in mem["modules"])
            ram_rows.append((f"Modules ({len(mem['modules'])})", sticks))
        console.print(kv_table("Memory", ram_rows))

    def drives_section() -> None:
        if disks and disks["available"]:
            console.print(_disk_health_table(disks))
            notes = [f"{d['model']}: {r}" for d in disks["disks"] for r in d["reasons"]]
            if disks["predict_failure"] is None:
                notes.append("SMART failure prediction: not available on this PC")
            elif not disks["predict_failure"]:
                notes.append("SMART failure prediction: no failure predicted")
            if disks["note"] and not is_admin():
                notes.append(disks["note"])
            for note in notes:
                console.print(f"  [muted]- {note}[/muted]")
        elif storage["drives"]:
            drives = grid_table("Physical drives", ["Model", "Type", "Bus", "Size", "Health"])
            for d in storage["drives"]:
                drives.add_row(d["model"], d["type"], d["bus"], d["size"], health_text(d["health"]))
            console.print(drives)
        elif disks is None:
            raise ValueError("drive health could not be read")

    def volumes_section() -> None:
        if storage["volumes"]:
            vols = grid_table("Volumes", ["Mount", "FS", "Total", "Free", "Used"])
            for v in storage["volumes"]:
                vols.add_row(v["mount"], v["fstype"], v["total_text"], v["free_text"], usage_bar(v["percent"], 12))
            console.print(vols)
        else:
            console.print(kv_table("Volumes", [("Volumes", "Not available")]))

    def graphics_section() -> None:
        if gpus:
            gpu_table = grid_table("Graphics", ["Adapter", "Driver", "Video memory"])
            for g in gpus:
                gpu_table.add_row(g["name"], g["driver"], g["vram"])
            console.print(gpu_table)
        else:
            console.print(kv_table("Graphics", [("Adapter", "Not available")]))

    def battery_section() -> None:
        battery_rows: list[tuple[str, Any]] = [("Charge", battery_text(batt)), ("Time left", batt["time_left"])]
        if health and health["present"]:
            battery_rows += [
                ("Design capacity", _mwh(health["design_mwh"])),
                ("Full-charge capacity", _mwh(health["full_mwh"])),
                ("Battery wear", usage_bar(health["wear_percent"], 20, warn=20, bad=40)),
                ("Battery health", f"{health['health_percent']:.1f}%" if health["health_percent"] is not None else NA),
                ("Charge cycles", health["cycles"]),
                ("Chemistry", health["chemistry"]),
            ]
            if health["wear_percent"] is None:
                battery_rows.append(("Note", "Wear data not available on this laptop (try running as Administrator)"))
        console.print(kv_table("Battery", battery_rows))

    def temperature_section() -> None:
        console.print(_temperature_table(temps, disks or {"disks": []}))

    _print_section(console, "System", system_section)
    _print_section(console, "BIOS & Motherboard", bios_section)
    _print_section(console, "Processor", processor_section)
    _print_section(console, "Memory", memory_section)
    _print_section(console, "Physical drives", drives_section)
    _print_section(console, "Volumes", volumes_section)
    _print_section(console, "Graphics", graphics_section)
    _print_section(console, "Battery", battery_section)
    _print_section(console, "Temperatures", temperature_section)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_info(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)
    with console.status("[accent]Collecting hardware details...[/accent]", spinner="dots"):
        data = collect_hardware()
    render_info(console, data, show_serial=args.show_serial)
    return 0


def cmd_wifi(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)
    console.print(current_wifi_table(get_current_wifi()))
    if args.reveal:
        console.print(
            Panel(
                "Passwords below belong to THIS computer only. Do not share screenshots or copy them to "
                "other people's devices without permission.",
                title="[warn]Passwords will be shown[/warn]",
                border_style="warn",
                expand=False,
            )
        )
        if not is_admin():
            console.print(
                "[warn]Not running as Administrator:[/warn] Windows may refuse to show some passwords.\n"
                "[muted]Right-click Command Prompt -> Run as administrator, then run this command again.[/muted]\n"
            )
    with console.status("[accent]Reading saved networks...[/accent]", spinner="dots"):
        saved = get_saved_wifi(reveal=args.reveal)
    if not saved["available"]:
        console.print(kv_table("Saved WiFi networks", [("Status", saved["note"])]))
        return 0
    console.print(wifi_profiles_table(saved["profiles"], args.reveal))
    if not args.reveal:
        console.print("  [muted]Passwords are hidden. Use[/muted] [accent]lapforge wifi --reveal[/accent] [muted]to show them.[/muted]")
    return 0


def cmd_speed() -> int:
    console = make_console()
    print_banner(console)
    with console.status("[accent]Testing connection (ping + DNS)...[/accent]", spinner="dots"):
        report = run_speed_test()
    console.print(speed_table(report))
    dns = report["dns_ms"]
    console.print(f"  [muted]DNS lookup:[/muted] {f'{dns:.0f} ms' if dns is not None else 'failed'}")
    if all(r["avg_ms"] is None for r in report["hosts"]):
        console.print("  [bad]No internet connection detected.[/bad]")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)
    with console.status("[accent]Running health checks (this can take 10-20 seconds)...[/accent]", spinner="dots"):
        result = run_doctor()
    render_health(console, result)
    return exit_code_for(result["score"], args.fail_on)


def cmd_scan() -> int:
    """Smart Scan: one check of the whole laptop. Offers the normal clean flow at the end."""
    console = make_console()
    print_banner(console)
    with console.status("[accent]Smart Scan is checking your laptop (this can take up to a minute)...[/accent]", spinner="dots"):
        result = run_smart_scan()
    render_scan(console, result)
    if not should_offer_clean(result):
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        console.print("  [muted]To remove the junk files, run:[/muted] [accent]lapforge clean[/accent]")
        return 0
    # The normal clean flow still shows its own preview and asks for confirmation before deleting.
    if _ask_yes(console, "  Remove the junk files now?"):
        clean_args = argparse.Namespace(full=False, browser=bool(result.get("browser_found")), dry_run=False, yes=False)
        return cmd_clean(clean_args)
    console.print("  [muted]Nothing was changed.[/muted]")
    return 0


def cmd_security() -> int:
    console = make_console()
    print_banner(console)
    with console.status("[accent]Checking security settings...[/accent]", spinner="dots"):
        report = get_security_report()
    if not report["available"]:
        console.print(kv_table("Security status", [("Status", report["note"] or "Not available")]))
        return 0
    console.print(security_table(report))
    console.print(level_summary(report["good"], report["warn"], report["bad"], report["unknown"]))
    if not is_admin():
        console.print("  [muted]Tip: run in an Administrator terminal to also read BitLocker and TPM details.[/muted]")
    return 0


def cmd_apps(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)
    with console.status("[accent]Reading installed apps...[/accent]", spinner="dots"):
        data = get_installed_apps()
    if not data["available"]:
        console.print(kv_table("Installed apps", [("Status", data["note"] or "Not available")]))
        return 0
    apps = data["apps"]
    if args.search:
        needle = args.search.lower()
        apps = [a for a in apps if needle in a["name"].lower() or needle in a["publisher"].lower()]
        if not apps:
            console.print(f"  [warn]No installed app matches[/warn] '{args.search}'.")
            return 0
    apps = sort_apps(apps, args.sort)
    limit = None if args.all else APPS_LIMIT
    console.print(apps_table(apps, limit))
    total = format_bytes(data["total_bytes"]) if data["total_bytes"] else NA
    console.print(f"  [muted]{len(apps)} app(s) listed, {data['count']} installed in total. Size reported by apps: {total}.[/muted]")
    if limit is not None and len(apps) > limit:
        console.print(f"  [muted]Showing {limit}. Use[/muted] [accent]lapforge apps --all[/accent] [muted]to see every app.[/muted]")
    return 0


def _startup_list(console) -> int:
    with console.status("[accent]Reading startup programs...[/accent]", spinner="dots"):
        data = get_startup_apps()
    if not data["available"]:
        console.print(kv_table("Startup programs", [("Status", data["note"] or "Not available")]))
        return 0
    if not data["items"]:
        console.print(kv_table("Startup programs", [("Status", status_text("good", "No startup programs found"))]))
        return 0
    console.print(startup_table(data["items"]))
    console.print(
        f"  [muted]{data['count']} entries, {data['enabled_count']} enabled, "
        f"{data['heavy_count']} using noticeable memory right now.[/muted]"
    )
    console.print(
        "  [muted]Impact is judged by memory used right now. To switch programs off safely (preview, backup, Undo):[/muted] "
        "[accent]lapforge startup --manage[/accent]"
    )
    return 0


def _ask_yes(console, question: str) -> bool:
    """Yes/no question that defaults to No and never crashes on Ctrl+C or a closed input."""
    try:
        return Confirm.ask(question, console=console, default=False)
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False


def _startup_history(console) -> int:
    changes = list_changes(20)
    if not changes:
        console.print(kv_table("Startup changes", [("Status", "LapForge has not changed any startup program yet.")]))
        return 0
    table = grid_table("Startup changes made by LapForge (newest first)", ["Change ID", "Action", "Program", "When", "Status"])
    for c in changes:
        if c["undone"]:
            state = status_text("muted", "Undone")
        elif c["applied"]:
            state = status_text("good", "Active")
        else:
            state = status_text("muted", "Not applied")
        table.add_row(c["id"], c["action"], c["item"], c["created"].replace("T", " "), state)
    console.print(table)
    console.print("  [muted]Undo the newest active change:[/muted] [accent]lapforge startup --undo --apply[/accent]")
    return 0


def _show_change_result(console, result: dict[str, Any]) -> None:
    """Print the outcome of change_startup_item(): message or error, restore point, backup, how to undo."""
    if result["error"]:
        console.print(f"  [bad]{result['error']}[/bad]")
    elif result["message"]:
        style = "accent" if result["dry_run"] or result["already"] else "good"
        console.print(f"  [{style}]{result['message']}[/{style}]")
    rp = result.get("restore_point")
    if rp:
        style = "muted" if rp.get("protected") else "warn"
        console.print(f"  [{style}]Restore point: {rp.get('reason') or 'no details'}[/{style}]")
    if result.get("backup"):
        console.print(f"  [muted]Backup saved: {result['backup']}[/muted]")
    if result.get("change_id") and not result["dry_run"] and result["ok"]:
        console.print(f"  [muted]To undo:[/muted] [accent]lapforge startup --undo {result['change_id']} --apply[/accent]")


def _preview_startup_change(
    console, name: str, enable: bool, source: str | None, use_restore_point: bool, items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Dry run + a plan table. Nothing is changed here."""
    preview = change_startup_item(name, enable, dry_run=True, source=source, items=items)
    if preview["error"] or preview["already"]:
        _show_change_result(console, preview)
        return preview
    item = preview["item"] or {}
    admin_text = "No"
    if preview["needs_admin"]:
        admin_text = "Yes (you are Administrator)" if is_admin() else "Yes - this terminal is NOT Administrator"
    if not use_restore_point:
        restore_text = "Skipped (--no-restore-point)"
    elif is_admin():
        restore_text = "Will be created (Windows allows one per 24 hours)"
    else:
        restore_text = "Cannot be made without Administrator - only the backup is saved"
    console.print(
        kv_table(
            "Planned change",
            [
                ("Program", item.get("name", name)),
                ("Where", item.get("source", NA)),
                ("Action", "Switch OFF at startup" if not enable else "Switch ON at startup"),
                ("Needs Administrator", admin_text),
                ("Backup", "The exact previous setting is saved first"),
                ("Restore point", restore_text),
                ("Nothing is deleted", "Same on/off switch as Task Manager > Startup apps"),
            ],
        )
    )
    return preview


def _startup_change(console, args: argparse.Namespace) -> int:
    enable = bool(args.enable)
    name = args.enable or args.disable
    source = _STARTUP_SOURCES.get(args.source) if args.source else None
    preview = _preview_startup_change(console, name, enable, source, not args.no_restore_point)
    if preview["error"]:
        return 1
    if preview["already"]:
        return 0
    if not args.apply:
        flag = "--enable" if enable else "--disable"
        extra = f" --source {args.source}" if args.source else ""
        console.print("  [good]PREVIEW ONLY - nothing was changed.[/good]")
        console.print(f'  [muted]To do it, run:[/muted] [accent]lapforge startup {flag} "{name}"{extra} --apply[/accent]')
        return 0
    if not args.yes and not _ask_yes(console, "  Make this change now?"):
        console.print("  [muted]Cancelled - nothing was changed.[/muted]")
        return 0
    result = change_startup_item(name, enable, dry_run=False, restore_point=not args.no_restore_point, source=source)
    _show_change_result(console, result)
    return 0 if result["ok"] else 1


def _startup_undo(console, args: argparse.Namespace) -> int:
    change_id = None if args.undo == "latest" else args.undo
    plan = undo_change(change_id, dry_run=True)
    if not plan["ok"]:
        console.print(f"  [bad]{plan['error']}[/bad]")
        return 1
    console.print(kv_table("Undo plan", [("Change", plan["change_id"]), ("Program", plan["item"]), ("What happens", plan["message"])]))
    if not args.apply:
        console.print("  [good]PREVIEW ONLY - nothing was changed.[/good]")
        console.print(f"  [muted]To do it, run:[/muted] [accent]lapforge startup --undo {plan['change_id']} --apply[/accent]")
        return 0
    if not args.yes and not _ask_yes(console, "  Undo this change now?"):
        console.print("  [muted]Cancelled - nothing was changed.[/muted]")
        return 0
    result = undo_change(plan["change_id"], dry_run=False)
    if result["ok"]:
        console.print(f"  [good]{result['message']}[/good]")
        return 0
    console.print(f"  [bad]{result['error']}[/bad]")
    return 1


def _manage_table(items: list[dict[str, Any]]):
    table = grid_table("Startup programs - manage", ["#", "Program", "Starts with Windows", "Memory now", "Impact", "Where"])
    for number, item in enumerate(items, 1):
        name = Text(item["name"])
        if is_protected(item):
            name.append(" (protected)", style="muted")
        table.add_row(
            str(number),
            name,
            status_text("good", "On") if item["enabled"] else status_text("muted", "Off"),
            item["memory_text"],
            status_text(item["level"], item["impact"]),
            Text(item["source"], style="muted"),
        )
    return table


def _startup_manage(console, args: argparse.Namespace) -> int:
    """Interactive screen: pick a number to switch a program on/off, u = undo, h = history, q = quit."""
    use_restore_point = not args.no_restore_point
    admin = is_admin()
    while True:
        with console.status("[accent]Reading startup programs...[/accent]", spinner="dots"):
            data = get_startup_apps()
        if not data["available"]:
            console.print(kv_table("Startup programs", [("Status", data["note"] or "Not available")]))
            return 0
        items = data["items"]
        if not items:
            console.print(kv_table("Startup programs", [("Status", status_text("good", "No startup programs found"))]))
            return 0
        console.print(_manage_table(items))
        console.print("  [muted]Type a number to switch that program on or off.  u = undo the last change, h = history, q = quit.[/muted]")
        if not admin:
            console.print("  [muted]Not an Administrator terminal: items that start for all users cannot be changed.[/muted]")
        try:
            choice = Prompt.ask("  Your choice", console=console, default="q").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if choice in ("", "q", "quit", "exit"):
            return 0
        if choice in ("h", "history"):
            _startup_history(console)
            continue
        if choice in ("u", "undo"):
            plan = undo_change(None, dry_run=True)
            if not plan["ok"]:
                console.print(f"  [warn]{plan['error']}[/warn]")
                continue
            console.print(kv_table("Undo plan", [("Change", plan["change_id"]), ("Program", plan["item"]), ("What happens", plan["message"])]))
            if args.yes or _ask_yes(console, "  Undo this change now?"):
                result = undo_change(plan["change_id"], dry_run=False)
                console.print(f"  [good]{result['message']}[/good]" if result["ok"] else f"  [bad]{result['error']}[/bad]")
            else:
                console.print("  [muted]Cancelled - nothing was changed.[/muted]")
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(items):
            console.print("  [warn]Type a number from the list, or u, h, q.[/warn]")
            continue

        item = items[int(choice) - 1]
        enable = not item["enabled"]
        preview = _preview_startup_change(console, item["name"], enable, item["source"], use_restore_point, items=items)
        if preview["error"] or preview["already"]:
            continue
        if not args.yes and not _ask_yes(console, f"  Switch '{item['name']}' {'on' if enable else 'off'} now?"):
            console.print("  [muted]Cancelled - nothing was changed.[/muted]")
            continue
        result = change_startup_item(item["name"], enable, dry_run=False, restore_point=use_restore_point, source=item["source"], items=items)
        _show_change_result(console, result)


def cmd_startup(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)
    chosen = [bool(args.manage), bool(args.disable), bool(args.enable), args.undo is not None, bool(args.history)]
    if sum(chosen) > 1:
        console.print("  [bad]Use only one of --manage, --disable, --enable, --undo or --history at a time.[/bad]")
        return 2
    if args.history:
        return _startup_history(console)
    if args.undo is not None:
        return _startup_undo(console, args)
    if args.disable or args.enable:
        return _startup_change(console, args)
    if args.manage:
        return _startup_manage(console, args)
    return _startup_list(console)


def cmd_drivers(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)
    with console.status("[accent]Reading drivers (can take up to a minute)...[/accent]", spinner="dots"):
        data = get_drivers()
    if not data["available"]:
        console.print(kv_table("Drivers", [("Status", data["note"] or "Not available")]))
        return 0
    limit = None if args.all else DRIVERS_LIMIT
    console.print(drivers_table(data["drivers"], limit))
    console.print(
        level_summary(
            data["count"] - data["old_count"] - data["very_old_count"],
            data["old_count"],
            data["very_old_count"],
            words=("recent", "aging (3+ yrs)", "old (5+ yrs)"),
        )
    )
    console.print(
        "  [muted]An old driver is not always a problem. If something misbehaves, update graphics, network "
        "and chipset drivers from your laptop maker's website.[/muted]"
    )
    if limit is not None and data["count"] > limit:
        console.print(f"  [muted]Showing the oldest {limit}. Use[/muted] [accent]lapforge drivers --all[/accent] [muted]for the full list.[/muted]")
    return 0


_RAM_VERDICTS = {
    "yes": ("good", "Yes - add a module"),
    "replace": ("warn", "Yes - by replacing the sticks"),
    "no": ("warn", "No"),
    "unknown": ("muted", "Cannot tell (check the service manual)"),
}
_SOLDERED_TEXT = {
    "all": "Yes - soldered to the motherboard",
    "partial": "Partly (some soldered, some removable)",
    "none": "No - removable sticks",
    "unknown": NA,
}
_STORAGE_VERDICTS = {
    "add": ("good", "Yes - a free slot exists, you can add a drive"),
    "replace": ("good", "Yes - replace it with a bigger / faster one"),
    "no": ("warn", "No (soldered eMMC)"),
    "unknown": ("muted", "Cannot tell"),
}


def render_upgrade(console, report: dict[str, Any]) -> None:
    ram, storage, system = report["ram"], report["storage"], report["system"]
    console.print(Text("  " + report["headline"], style=report["level"]))
    console.print()

    ram_level, ram_label = _RAM_VERDICTS[ram["verdict"]]
    slots = f"{ram['slots_used']} used of {ram['slots_total']}" if ram["slots_total"] else NA
    free = ram["slots_free"] if ram["slots_free"] is not None else NA
    max_text = f"{ram['max_text']} (as reported by the firmware)" if ram["max_text"] != NA else "Not reliable (the firmware gave no usable value)"
    console.print(
        kv_table(
            "RAM upgrade",
            [
                ("Laptop", " ".join(p for p in (system["brand"], system["model"]) if p != NA) or NA),
                ("Installed", f"{ram['installed_text']}  ({ram['type']}, {ram['speed']})"),
                ("In use now", usage_bar(ram["percent_used"])),
                ("Memory slots", slots),
                ("Free slots", free),
                ("Maximum supported", max_text),
                ("Soldered", _SOLDERED_TEXT[ram["soldered"]]),
                ("Channel mode", ram["channel"]),
                ("Can RAM be upgraded?", status_text(ram_level, ram_label)),
            ],
        )
    )
    if ram["modules"]:
        modules = grid_table("Installed memory sticks", ["Slot", "Size", "Type", "Speed", "Form", "Maker", "Part number"])
        for m in ram["modules"]:
            modules.add_row(m["slot"], m["capacity"], m["type"], m["speed"], m["form"], m["maker"], m["part"])
        console.print(modules)

    if storage["drives"]:
        drives = grid_table("Internal drives", ["Model", "Type", "Bus", "Size"])
        for d in storage["drives"]:
            drives.add_row(d["model"], d["type"], d["bus"], d["size"])
        console.print(drives)
    vol, m2 = storage["system_volume"], storage["m2"]
    storage_level, storage_label = _STORAGE_VERDICTS[storage["verdict"]]
    rows: list[tuple[str, Any]] = []
    if vol is not None:
        rows.append(("System drive", f"{vol['mount']}  {storage['free_text']} free of {vol['total_text']}"))
        rows.append(("Space used", usage_bar(vol["percent"], 20, warn=80, bad=90)))
    rows.append(("M.2 slots", f"{m2['total']} found, {m2['free']} free (as reported by the firmware)" if m2["known"] else "Not reported by this laptop (most laptops do not)"))
    rows.append(("Can storage be upgraded?", status_text(storage_level, storage_label)))
    console.print(kv_table("Storage upgrade", rows))

    advice = grid_table("Upgrade advice (most important first)", ["", "Finding", "What to do"])
    for rec in report["recommendations"]:
        advice.add_row(status_text(rec["level"]), rec["title"], rec["detail"])
    console.print(advice)
    console.print("  [muted]Read-only: LapForge changed nothing on this computer.[/muted]")
    console.print(
        "  [muted]Before you buy parts, check the service manual for your exact model and your warranty "
        "(opening the laptop can void it on some models).[/muted]"
    )
    if system["search_hint"]:
        console.print(f"  [muted]Search for:[/muted] [accent]{system['search_hint']}[/accent]")


def cmd_upgrade() -> int:
    console = make_console()
    print_banner(console)
    with console.status("[accent]Checking RAM slots and drives (10-30 seconds)...[/accent]", spinner="dots"):
        report = get_upgrade_report()
    if not report["available"]:
        console.print(kv_table("Upgrade advisor", [("Status", report["note"] or "Not available")]))
        return 0
    render_upgrade(console, report)
    return 0


def _clean_targets(full: bool, with_browser: bool, open_browsers: set[str] | None = None):
    """Targets for a clean. Browsers that are open are left out (see browser_targets)."""
    targets = list(get_targets("full" if full else "quick"))
    if with_browser:
        targets += browser_targets(open_browsers)
    return targets


def _open_browsers_for_clean(console: Console, args: argparse.Namespace) -> list[str]:
    """Browsers whose cache is skipped because they are open.

    When someone can answer, offer ONE retry: they close the browser themselves and say
    yes. LapForge never closes a browser itself.
    """
    open_keys = detected_open_browsers()
    if not open_keys or args.yes or args.dry_run or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return open_keys
    names = " and ".join(browser_name(k) for k in open_keys)
    console.print(f"  [warn]{names} is open, so its cache would be skipped.[/warn]")
    try:
        retry = Confirm.ask(
            f"  Close {names} yourself now, then answer Yes to include its cache. Check again?",
            console=console,
            default=False,
        )
    except (EOFError, KeyboardInterrupt):
        retry = False
    return detected_open_browsers() if retry else open_keys


def _not_cleaned(item: dict[str, Any]) -> dict[str, Any]:
    """Result row for a target that had nothing to clean (so it is not touched)."""
    return {**item, "dry_run": False, "deleted": 0, "freed_bytes": 0, "freed_text": format_bytes(0), "skipped": 0}


def cmd_clean(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)
    profile = "full" if args.full else "quick"
    skipped_browsers = _open_browsers_for_clean(console, args) if args.browser else []
    targets = _clean_targets(args.full, args.browser, set(skipped_browsers))
    if not targets:
        reason = (
            "The cleaner needs Windows (not available on this system)."
            if not cleaner_supported()
            else "Nothing to clean here."
        )
        console.print(kv_table("Clean", [("Status", reason)]))
        return 0

    # 1. Preview (nothing is deleted yet)
    with console.status("[accent]Measuring what can be cleaned...[/accent]", spinner="dots"):
        preview = build_preview(profile + (" + browser" if args.browser else ""), targets)
    console.print(preview_table(preview))
    for note in open_browser_notes(skipped_browsers):
        console.print(f"  [warn]{note}[/warn]")
    ready = {i["key"] for i in preview["items"] if i["available"] and not i["refused"] and i["files"] > 0}
    if not ready:
        console.print("  [good]Nothing to clean right now.[/good]")
        return 0
    console.print(
        f"  [accent]Can free about {preview['total_text']}[/accent] [muted]in {preview['total_files']:,} files.[/muted]"
    )
    console.print("  [muted]Your Documents, Pictures, Videos, Desktop and Downloads are never touched.[/muted]")
    if not preview["admin"]:
        console.print("  [muted]Tip: run in an Administrator terminal to include Windows temp, Prefetch and the update cache fully.[/muted]")
    if args.dry_run:
        console.print("  [good]DRY RUN - nothing was deleted.[/good]")
        return 0

    # 2. Confirm
    if not args.yes:
        try:
            go = Confirm.ask("  Clean these now?", console=console, default=False)
        except (EOFError, KeyboardInterrupt):
            go = False
        if not go:
            console.print("  [muted]Cancelled - nothing was deleted.[/muted]")
            return 0

    # 3. Clean, with before/after disk space
    log = RunLog("clean")
    log.line(f"Profile : {profile}{' + browser cache' if args.browser else ''}")
    log.line(f"Preview : {preview['total_files']} files, {preview['total_text']}")
    before = disk_snapshot()
    started = time.time()
    previews = {i["key"]: i for i in preview["items"]}
    results: list[dict[str, Any]] = []
    interrupted = False
    progress = Progress(
        SpinnerColumn(style="accent"),
        TextColumn("[accent]{task.description}[/accent]"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    )
    try:
        with progress:
            task = progress.add_task("Starting...", total=len(targets))
            for target in targets:
                progress.update(task, description=f"Cleaning {target.label}")
                if target.key in ready:
                    results.append(clean_target(target, dry_run=False))
                else:
                    results.append(_not_cleaned(previews[target.key]))
                progress.advance(task)
            progress.update(task, description="Done")
    except KeyboardInterrupt:
        interrupted = True
        done = {r["key"] for r in results}
        results += [_not_cleaned(previews[t.key]) for t in targets if t.key not in done]
        console.print("\n  [warn]Stopped by you. Files already removed stay removed.[/warn]")
    after = disk_snapshot()
    report = build_clean_report(profile, results, before, after, started)
    render_clean_report(console, report)

    log.section("Result" + (" (stopped by user)" if interrupted else ""))
    log.lines(report_lines(report))
    path = log.close()
    if path:
        console.print(f"  [muted]Log saved:[/muted] {path}")
    return 130 if interrupted else 0


def cmd_duplicates(args: argparse.Namespace) -> int:
    """Find duplicate files. Read-only: this command never deletes anything."""
    console = make_console()
    print_banner(console)
    # Full paths are needed: a relative path such as "." would look like a drive root to the finder.
    folders = [os.path.abspath(os.path.expanduser(p)) for p in (args.path or [str(Path.home() / "Downloads")])]
    min_bytes = max(1, int(args.min_size * 1024 * 1024))
    with console.status("[accent]Looking for duplicate files...[/accent]", spinner="dots") as status:

        def update(stage: str, done: int, total: int) -> None:
            suffix = f" {done:,}/{total:,}" if total else f" {done:,}"
            status.update(f"[accent]{stage}{suffix}[/accent]")

        result = find_duplicates(folders, min_size=min_bytes, max_files=args.max_files, on_progress=update)

    for item in result["refused"]:
        console.print(f"  [warn]Cannot scan '{item['path']}': {item['reason']}[/warn]")
    if not result["roots"]:
        console.print(
            "  [muted]What to do: choose a normal folder, for example your Downloads folder:[/muted] "
            "[accent]lapforge duplicates --path <folder>[/accent]"
        )
        return 1

    if not result["groups"]:
        console.print(
            kv_table(
                "Duplicate files",
                [
                    ("Scanned files", f"{result['scanned_files']:,}"),
                    ("Result", "No duplicates found"),
                    ("Smallest file checked", format_bytes(result["min_size"])),
                ],
            )
        )
    else:
        console.print(duplicates_table(result))
        extra = result["duplicate_files"]
        console.print(
            f"  [accent]{extra:,} extra {'copy wastes' if extra == 1 else 'copies waste'} {result['wasted_text']}.[/accent] "
            f"[muted]Scanned {result['scanned_files']:,} files in {result['seconds']}s.[/muted]"
        )
        console.print("  [good]REPORT ONLY - nothing was deleted.[/good]")
        console.print(
            "  [muted]To remove copies: open the folder in Windows Explorer, compare the files listed above, "
            "and delete the ones you do not need yourself.[/muted]"
        )
    if result["truncated"]:
        console.print(
            "  [warn]Stopped early because the folder has too many files. "
            "Scan a smaller folder, or raise --max-files, for a full result.[/warn]"
        )
    if result["unreadable"]:
        console.print(f"  [muted]{result['unreadable']} files could not be read and were skipped.[/muted]")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)
    if not args.run:
        plan = repair_plan(args.mode)
        if not plan["supported"]:
            console.print(kv_table("Repair", [("Status", "DISM and SFC need Windows (not available on this system).")]))
            return 0
        table = grid_table(f"Repair plan - {args.mode}", ["Step", "What it does", "Time", "Changes system"])
        for step in plan["steps"]:
            table.add_row(step.label, step.description, step.duration, "Yes" if step.modifies else "No")
        console.print(table)
        console.print("  [good]PLAN ONLY - nothing was executed.[/good]")
        console.print(
            f"  [muted]To run it, open an Administrator terminal and use[/muted] "
            f"[accent]lapforge repair --mode {args.mode} --run[/accent]"
        )
        return 0

    result = guided_repair(args.mode, dry_run=False, assume_yes=args.yes, console=console)
    ran = [s for s in result["steps"] if s.get("ran")]
    if ran:
        lines = [f"Mode: {args.mode}", f"Overall: {result['level']}", ""]
        for step in ran:
            lines.append(f"- {step['label']}: {step['level']} - {step['summary']} (exit code {step['returncode']}, {step['seconds']}s)")
            lines.append(f"    command: {step['command']}")
            lines += [f"    | {row}" for row in step.get("tail", [])]
        path = write_log("repair", f"Repair - {args.mode}", lines)
        if path:
            console.print(f"  [muted]Log saved:[/muted] {path}")
    return 1 if (result.get("refused") or result["level"] == "bad") else 0


def cmd_live(args: argparse.Namespace) -> int:
    console = make_console()
    if args.once:
        print_banner(console)
        show_snapshot(console, seconds=min(args.duration or 3.0, 30.0), top=args.top)
        return 0
    run_live(console, interval=args.interval, duration=args.duration, top=args.top)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    console = make_console()
    print_banner(console)

    out = Path(os.path.expandvars(os.path.expanduser(args.output))) if args.output else None
    file_target = out is not None and out.suffix.lower() in _REPORT_EXTENSIONS
    fmt = args.format
    if file_target:
        implied = _REPORT_EXTENSIONS[out.suffix.lower()]
        if fmt not in (None, implied):
            console.print(f"  [bad]{out.name} ends in a {implied} extension but --format {fmt} was given.[/bad]")
            return 2
        formats = [implied]
        if out.exists() and not args.force:
            console.print(f"  [bad]{out} already exists.[/bad] [muted]Use --force to overwrite it, or pick another name.[/muted]")
            return 2
    else:
        if out is not None and out.exists() and not out.is_dir():
            console.print(f"  [bad]{out} is a file, not a folder.[/bad] [muted]Give a folder, or a name ending in .html / .json / .md.[/muted]")
            return 2
        fmt = fmt or "html"
        formats = ["json", "md", "html"] if fmt == "all" else [fmt]
        if out is None and load_config()["report_dir"]:
            out = Path(load_config()["report_dir"])

    if args.show_private:
        console.print(
            Panel(
                "This report will include serial numbers, MAC and IP addresses of THIS computer. "
                "Do not post it publicly or send it to people you do not trust.",
                title="[warn]Private details will be included[/warn]",
                border_style="warn",
                expand=False,
            )
        )
    with console.status("[accent]Collecting system details (10-30 seconds)...[/accent]", spinner="dots"):
        snapshot = collect_snapshot(
            show_private=args.show_private,
            include_apps=not args.no_apps,
            include_drivers=args.drivers,
        )
    try:
        if file_target:
            safe = redact_snapshot(snapshot, args.show_private)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_snapshot(safe, formats[0]), encoding="utf-8", newline="\n")
            paths = {formats[0]: out}
        else:
            paths = write_exports(snapshot, out or default_output_dir(), formats, args.show_private)
    except OSError as exc:
        console.print(f"  [bad]Could not save the report:[/bad] {exc.strerror or exc}")
        console.print("  [muted]Pick a folder you can write to with --output.[/muted]")
        return 1

    labels = {"html": "HTML", "json": "JSON", "md": "Markdown"}
    rows = [(labels[f], f"{p.resolve()}  ({format_bytes(p.stat().st_size)})") for f, p in paths.items()]
    console.print(kv_table("Report saved", rows))
    score = snapshot["health"].get("score", {})
    if score:
        console.print(
            Text("  Health score: ", style="muted")
            + Text(f"{score.get('score', NA)}/100 ({score.get('grade', NA)})", style=score.get("level", "muted"))
        )
    if not args.show_private:
        console.print("  [muted]Serial numbers, MAC and IP addresses are hidden. Use --show-private to include them.[/muted]")
    if args.open and "html" in paths:
        try:
            webbrowser.open(paths["html"].resolve().as_uri())
        except Exception:
            console.print("  [muted]Could not open the browser. Open the HTML file yourself.[/muted]")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    console = make_console()
    action = args.config_action or "show"
    if action == "path":
        console.print(f"  {config_path()}")
        return 0
    if action == "reset":
        if reset_config():
            console.print("  [good]Settings reset to the defaults.[/good]")
        else:
            console.print("  [muted]Nothing to reset: you are already using the defaults.[/muted]")
        return 0
    if action == "set":
        result = set_setting(args.key, args.value)
        if not result["ok"]:
            console.print(f"  [bad]{result['error']}[/bad]")
            return 1
        console.print(f"  [good]Saved.[/good] {args.key}: {result['old']!s} -> {result['new']!s}")
        if result["old"] != result["new"]:
            console.print(f"  [muted]To undo:[/muted] [accent]lapforge config set {args.key} {result['old']!s}[/accent]")
        return 0
    table = grid_table("LapForge settings", ["Setting", "Value", "Default", "Meaning"])
    for row in describe_config():
        table.add_row(row["key"], Text(row["value"], style="accent" if row["changed"] else "value"), row["default"], row["help"])
    console.print(table)
    console.print(f"  [muted]Settings file: {config_path()}[/muted]")
    console.print("  [muted]Change one with:[/muted] [accent]lapforge config set <setting> <value>[/accent]")
    return 0


def cmd_update() -> int:
    console = make_console()
    print_banner(console)
    with console.status("[accent]Asking GitHub for the latest release...[/accent]", spinner="dots"):
        result = check_for_update(force=True)
    verdict = {
        "update": status_text("warn", "A newer version is available"),
        "current": status_text("good", "You are up to date"),
        "none": status_text("muted", "No release has been published yet"),
        "error": status_text("warn", "Could not check"),
    }[result["status"]]
    rows: list[tuple[str, Any]] = [("Installed version", result["current"]), ("Latest release", result["latest"] or NA), ("Status", verdict)]
    if result["published"]:
        rows.append(("Published", result["published"]))
    if result["note"]:
        rows.append(("Note", result["note"]))
    if result["status"] == "update":
        rows.append(("Download", result["url"]))
    console.print(kv_table("Update check", rows))
    console.print(
        "  [muted]This sends one read-only request to GitHub and nothing about you or your PC. "
        "Turn off automatic checks:[/muted] [accent]lapforge config set check_updates off[/accent]"
    )
    return 0


def _is_interactive() -> bool:
    """True when a person is typing in a real terminal (both input and output are a terminal)."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _command_names(parser: argparse.ArgumentParser) -> list[str]:
    """Every command word the parser accepts (including aliases such as 'check' and 'help')."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    return []


def _unknown_command(parser: argparse.ArgumentParser, word: str) -> int:
    """Say that a command does not exist and suggest the closest one. Returns exit code 2."""
    err = Console(theme=LAPFORGE_THEME, highlight=False, stderr=True)
    err.print(f"[bad]{unknown_command_message(word, suggest_command(word, _command_names(parser)))}[/bad]")
    err.print("[muted]Run 'lapforge commands' to see every command.[/muted]")
    return 2


def cmd_commands(args: argparse.Namespace) -> int:
    """The cheat sheet, or the options of one command (`lapforge help clean`)."""
    topic = getattr(args, "topic", None)
    if topic:
        parser = build_parser()
        if topic not in _command_names(parser):
            return _unknown_command(parser, topic)
        try:
            parser.parse_args([topic, "--help"])
        except SystemExit:
            pass
        return 0
    render_commands(make_console())
    return 0


def _maybe_show_tip(command: str | None, code: int) -> None:
    """After a common command that worked, print one short tip (at most once per run; `show_tips` off hides it)."""
    if code != 0 or not _is_interactive():
        return
    tip = take_tip(command, bool(load_config().get("show_tips", True)))
    if tip:
        make_console().print(f"  [muted]{tip}[/muted]")


def cmd_menu() -> int:
    """The menu. Every item runs a normal command in this same window."""
    return run_menu(make_console())


def cmd_dashboard() -> int:
    console = make_console()
    show_dashboard(console)
    admin = is_admin()
    console.print(f"  [muted]Running as:[/muted] [{'good' if admin else 'value'}]{elevate.running_as_text(admin)}[/]")
    if not admin and elevate.elevation_supported():
        console.print("  [muted]Some features need Administrator. Use[/muted] [accent]lapforge --admin[/accent] [muted]to restart with the Windows permission prompt.[/muted]")
    # Automatic check: off-switch respected, at most once a day, silent on any failure.
    notice = update_notice(check_for_update())
    if notice:
        console.print(f"  [warn]{notice}[/warn]")
    return 0


def _ask_restart_as_admin(console: Console) -> bool:
    """Ask once. The default answer is No."""
    console.print("  [warn]This feature needs Administrator rights.[/warn]")
    try:
        return elevate.parse_yes_no(console.input("  Restart LapForge as Administrator now? \\[y/N] "))
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False


def _handle_elevation(
    console: Console,
    args: argparse.Namespace,
    original_args: list[str],
    forced: bool,
    elevated: bool,
) -> int | None:
    """Restart as Administrator when needed. Returns an exit code to stop, or None to carry on.

    The Windows permission prompt is always shown. If it is declined, LapForge carries on
    with the limited version and says what is missing.
    """
    decision = elevate.decide_elevation(
        args.command,
        args,
        admin=is_admin(),
        elevated=elevated,
        supported=elevate.elevation_supported(),
        forced=forced,
        interactive=sys.stdin.isatty() and sys.stdout.isatty(),
    )
    if decision == elevate.NONE:
        return None
    if decision == elevate.UNSUPPORTED:
        console.print("  [muted]Restarting as Administrator is only available on Windows. Continuing as usual.[/muted]")
        return None
    _level, missing = elevate.admin_reason(args.command, args)
    if decision == elevate.ASK and not _ask_restart_as_admin(console):
        if missing:
            console.print(f"  [muted]Continuing with the limited version. {missing}[/muted]")
        return None

    console.print("  [muted]Windows will now ask for permission...[/muted]")
    result = elevate.relaunch_as_admin(original_args)
    if result == elevate.STARTED:
        console.print("  [good]LapForge is opening in a new Administrator window. You can close this one.[/good]")
        return 0
    if result == elevate.CANCELLED:
        console.print("  [muted]Administrator permission was not given. Continuing with the limited version.[/muted]")
    else:
        console.print("  [warn]LapForge could not restart as Administrator. Continuing with the limited version.[/warn]")
        console.print("  [muted]What you can do: open an Administrator terminal yourself and run the command again.[/muted]")
    if missing:
        console.print(f"  [muted]{missing}[/muted]")
    return None


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "info":
        return cmd_info(args)
    if args.command == "wifi":
        return cmd_wifi(args)
    if args.command == "speed":
        return cmd_speed()
    if args.command in ("commands", "help"):
        return cmd_commands(args)
    if args.command in ("scan", "check"):
        return cmd_scan()
    if args.command == "health":
        return cmd_health(args)
    if args.command == "clean":
        return cmd_clean(args)
    if args.command == "duplicates":
        return cmd_duplicates(args)
    if args.command == "repair":
        return cmd_repair(args)
    if args.command == "live":
        return cmd_live(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "config":
        return cmd_config(args)
    if args.command == "update":
        return cmd_update()
    if args.command == "security":
        return cmd_security()
    if args.command == "apps":
        return cmd_apps(args)
    if args.command == "startup":
        return cmd_startup(args)
    if args.command == "drivers":
        return cmd_drivers(args)
    if args.command == "upgrade":
        return cmd_upgrade()
    if args.command == "menu":
        return cmd_menu()
    if args.command == "dashboard":
        return cmd_dashboard()
    # No command: the menu for a person in a terminal, the dashboard for scripts and pipes.
    return cmd_menu() if _is_interactive() else cmd_dashboard()


def main(argv: list[str] | None = None) -> int:
    # Make sure box/bar characters never crash on legacy Windows code pages.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    # --debug, --admin and the internal --elevated marker are accepted before or after the
    # command, so they are removed here first. The words that remain are the real command.
    debug = "--debug" in raw_args
    forced = elevate.ADMIN_FLAG in raw_args
    elevated = elevate.ELEVATED_FLAG in raw_args
    global_flags = ("--debug", elevate.ADMIN_FLAG, elevate.ELEVATED_FLAG)
    command_args = [item for item in raw_args if item not in global_flags]
    words = [item for item in command_args if not item.startswith("-")]
    if words and words[0] not in _command_names(parser):
        return _unknown_command(parser, words[0])
    args = parser.parse_args(command_args)
    try:
        code = _handle_elevation(make_console(), args, [i for i in raw_args if i != elevate.ADMIN_FLAG], forced, elevated)
        if code is not None:
            return code
        code = _dispatch(args)
        _maybe_show_tip(args.command, code)
        return code
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception as exc:  # any unexpected error is shown calmly, never as a raw traceback
        show_error(make_console(), exc, debug=debug)
        return 1
    finally:
        if elevated:  # a restarted window closes when done: keep it open so the result can be read
            elevate.wait_for_enter_if_own_console()


if __name__ == "__main__":
    sys.exit(main())
