"""Dashboard shown by plain `lapforge`: logo on the left, live summary on the right."""

from __future__ import annotations

import os
import re
from typing import Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..collectors import (
    get_battery_info,
    get_cpu_info,
    get_gpu_info,
    get_memory_info,
    get_os_info,
    get_storage_info,
    get_system_info,
)
from ..collectors.hardware import safe_collect
from ..utils import NA
from .banner import print_banner
from .theme import PALETTE, percent_level, usage_bar

# Small laptop drawing used as the dashboard logo.
_LOGO = [
    "  ╭──────────────────╮  ",
    "  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │  ",
    "  │ ▓▓  L A P  ▓▓▓▓▓ │  ",
    "  │ ▓▓▓▓ FORGE ▓▓▓▓▓ │  ",
    "  │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │  ",
    "  ╰──────────────────╯  ",
    " " + "▀" * 22 + " ",
]
_LOGO_WIDTH = len(_LOGO[0])
SIDE_BY_SIDE_MIN_WIDTH = 92


def _short_cpu_name(name: str) -> str:
    """'Intel(R) Core(TM) i5-5300U CPU @ 2.30GHz' -> 'Intel Core i5-5300U'."""
    if name == NA:
        return name
    name = re.sub(r"\((?:R|TM|tm|r)\)", "", name)
    name = re.sub(r"\s+@.*$", "", name)
    name = re.sub(r"\s+CPU\b", "", name)
    return re.sub(r"\s+", " ", name).strip()


def _system_volume(volumes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The volume Windows is installed on (or '/' elsewhere), else the first one."""
    wanted = (os.environ.get("SystemDrive", "C:") + "\\").lower()
    for vol in volumes:
        if vol["mount"].lower() in (wanted, "/"):
            return vol
    return volumes[0] if volumes else None


def _logo_panel() -> Text:
    logo = Text()
    for index, line in enumerate(_LOGO):
        style = PALETTE["brand"] if index < 6 else PALETTE["accent"]
        logo.append(line if index == len(_LOGO) - 1 else line + "\n", style=style)
    return logo


def _palette_strip() -> Text:
    strip = Text()
    for key in ("good", "warn", "bad", "accent", "brand", "muted"):
        strip.append("███ ", style=PALETTE[key])
    return strip


def _charge_bar(percent: float | None, width: int = 20) -> Text:
    """Battery bar: low charge is red, high is green (opposite of usage bars)."""
    if percent is None:
        return Text("N/A", style="muted")
    level = percent_level(100 - percent, warn=80, bad=90)
    filled = int(round(width * max(0.0, min(100.0, percent)) / 100))
    bar = Text("█" * filled, style=level)
    bar.append("░" * (width - filled), style="muted")
    bar.append(f" {percent:.0f}%", style=level)
    return bar


def _summary(data: dict[str, Any]) -> Table:
    system, os_info = data["system"], data["os"]
    cpu, mem, storage = data["cpu"], data["memory"], data["storage"]
    gpus, batt = data["gpu"], data["battery"]

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="label", no_wrap=True)
    grid.add_column(style="value")

    def row(label: str, value: Any) -> None:
        grid.add_row(label, value)

    host = Text(f"{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}", style="brand")
    host.append(f"@{system['hostname']}", style="accent")
    grid.add_row("", host)
    grid.add_row("", Text("─" * 34, style="border"))

    model = " ".join(p for p in (system["brand"], system["model"]) if p != NA) or NA
    row("Model", model)
    row("OS", f"{os_info['name']} ({os_info['architecture']})")
    cpu_text = _short_cpu_name(cpu["name"])
    if cpu["generation"] != NA:
        cpu_text += f" - {cpu['generation']}"
    row("CPU", cpu_text)
    row("Cores", f"{cpu['cores']} cores / {cpu['threads']} threads")
    ram_kind = " ".join(p for p in (mem["type"], mem["speed"]) if p != NA)
    row("RAM", f"{mem['total_text']} {ram_kind}".strip())
    row("GPU", gpus[0]["name"] if gpus else "Not available")
    row("Uptime", os_info["uptime"])

    vol = _system_volume(storage["volumes"])
    row("Disk", f"{vol['mount']} {vol['free_text']} free of {vol['total_text']}" if vol else "Not available")

    grid.add_row("", "")
    row("CPU load", usage_bar(cpu["usage_percent"]))
    row("RAM used", usage_bar(mem["percent"]))
    row("Disk used", usage_bar(vol["percent"]) if vol else Text("N/A", style="muted"))
    if batt["present"]:
        state = " (charging)" if batt["plugged"] else ""
        battery = _charge_bar(batt["percent"])
        battery.append(state, style="muted")
        row("Battery", battery)
    grid.add_row("", "")
    grid.add_row("", _palette_strip())
    return grid


def collect_dashboard_data() -> dict[str, Any]:
    """Collect every dashboard item on its own. A failing item shows N/A; the rest still appears."""
    return {
        "system": safe_collect("system", get_system_info),
        "os": safe_collect("os", get_os_info),
        "cpu": safe_collect("cpu", get_cpu_info),
        "memory": safe_collect("memory", get_memory_info),
        "storage": safe_collect("storage", get_storage_info),
        "gpu": safe_collect("gpu", get_gpu_info),
        "battery": safe_collect("battery", get_battery_info),
    }


def build_dashboard(data: dict[str, Any], width: int) -> RenderableType:
    summary = _summary(data)
    if width >= SIDE_BY_SIDE_MIN_WIDTH:
        layout = Table.grid(padding=(0, 3))
        layout.add_column(no_wrap=True, width=_LOGO_WIDTH)
        layout.add_column()
        layout.add_row(_logo_panel(), summary)
        body: RenderableType = layout
    else:
        body = Group(summary)
    return Panel(body, box=box.ROUNDED, border_style="border", padding=(1, 2), expand=False)


def show_dashboard(console: Console) -> None:
    """Banner + side-by-side summary + command hints."""
    print_banner(console)
    with console.status("[accent]Reading your laptop...[/accent]", spinner="dots"):
        data = collect_dashboard_data()
    console.print(build_dashboard(data, console.width))
    console.print(
        "\n[muted]Try:[/muted] [accent]lapforge info[/accent]  [muted]for every detail  |[/muted]  "
        "[accent]lapforge --help[/accent]  [muted]for all commands[/muted]"
    )
