"""Live monitor: CPU, memory, disk and network updating on screen, plus the apps
that use the most and a one-line bottleneck verdict.

The screen has a fixed height (it depends only on how many apps are listed), so
it redraws in place without jumping. Everything is read-only and stays on this
computer.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

import psutil
from rich.console import Console, Group
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.text import Text

from ..collectors.bottleneck import (
    BottleneckLimits,
    disk_rate,
    group_processes,
    judge_bottleneck,
    measure_bottleneck,
    sample_processes,
    top_by,
)
from ..utils import NA, format_bytes
from .tables import grid_table, kv_table
from .theme import percent_level, status_text, usage_bar

SPARK_CHARS = "▁▂▃▄▅▆▇█"
HISTORY = 60  # readings kept for the little graphs
_SMOOTH_TICKS = 5  # the verdict looks at the last few readings, not a single spike


# --------------------------------------------------------------------------
# Small helpers (pure)
# --------------------------------------------------------------------------
def sparkline(values: list[float], width: int, maximum: float | None = None) -> str:
    """Tiny bar graph of the latest `width` values, e.g. '▁▂▄▇█▅'. Padded on the left to `width`."""
    shown = list(values)[-width:] if width > 0 else []
    if not shown:
        return " " * max(0, width)
    top = maximum if maximum else max(shown)
    if top <= 0:
        top = 1.0
    chars = []
    for value in shown:
        ratio = max(0.0, min(1.0, value / top))
        chars.append(SPARK_CHARS[min(len(SPARK_CHARS) - 1, int(ratio * (len(SPARK_CHARS) - 1) + 0.5))])
    return " " * (width - len(chars)) + "".join(chars)


def format_rate(bytes_per_second: float | None) -> str:
    """Bytes per second as text: 1536 -> '1.5 KB/s'. None -> N/A."""
    if bytes_per_second is None:
        return NA
    return f"{format_bytes(bytes_per_second)}/s"


def _spark_width(console_width: int) -> int:
    """Graph width that still fits next to the bars on narrow terminals."""
    return max(8, min(40, console_width - 62))


def _app_label(group: dict[str, Any], width: int = 22) -> str:
    text = group["name"] + (f" x{group['count']}" if group["count"] > 1 else "")
    return text if len(text) <= width else text[: width - 1] + "…"


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------
class LiveMonitor:
    """Takes a reading every time `tick()` is called and remembers the recent past."""

    def __init__(self, top: int = 5, limits: BottleneckLimits | None = None) -> None:
        self.top = top
        self.limits = limits
        self.cpu: deque[float] = deque(maxlen=HISTORY)
        self.ram: deque[float] = deque(maxlen=HISTORY)
        self.disk: deque[float] = deque(maxlen=HISTORY)  # MB/s read+write
        self.net: deque[float] = deque(maxlen=HISTORY)  # MB/s up+down
        self.peak = {"cpu": 0.0, "ram": 0.0}
        self.samples = 0
        self._app_cpu: dict[str, float] = {}
        self._disk_before: Any = None
        self._net_before: Any = None
        self._clock = time.monotonic()

    @staticmethod
    def _counters(getter: Any) -> Any:
        try:
            return getter()
        except Exception:
            return None

    def prime(self) -> None:
        """First readings are always zero, so take them before the screen starts."""
        try:
            psutil.cpu_percent(None)
        except Exception:
            pass
        sample_processes()
        self._disk_before = self._counters(psutil.disk_io_counters)
        self._net_before = self._counters(psutil.net_io_counters)
        self._clock = time.monotonic()

    def tick(self) -> dict[str, Any]:
        """Read everything once and return the state to draw."""
        now = time.monotonic()
        seconds = max(0.05, now - self._clock)
        self._clock = now
        try:
            cpu = psutil.cpu_percent(None)
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory().percent
        except Exception:
            cpu, vm, swap = 0.0, None, None
        ram_percent = vm.percent if vm is not None else 0.0

        disk_now = self._counters(psutil.disk_io_counters)
        read, write = disk_rate(self._disk_before, disk_now, seconds)
        self._disk_before = disk_now

        net_now = self._counters(psutil.net_io_counters)
        up = down = None
        if self._net_before is not None and net_now is not None:
            up = max(0, net_now.bytes_sent - self._net_before.bytes_sent) / seconds
            down = max(0, net_now.bytes_recv - self._net_before.bytes_recv) / seconds
        self._net_before = net_now

        groups = group_processes(sample_processes(), vm.total if vm is not None else 0)
        smoothed: list[dict[str, Any]] = []
        seen: dict[str, float] = {}
        for group in groups:
            key = group["name"].lower()
            value = 0.5 * self._app_cpu.get(key, group["cpu"]) + 0.5 * group["cpu"]
            seen[key] = value
            smoothed.append({**group, "cpu": round(value, 1)})
        self._app_cpu = seen

        self.cpu.append(cpu)
        self.ram.append(ram_percent)
        self.disk.append(((read or 0) + (write or 0)) / (1024 * 1024))
        self.net.append(((up or 0) + (down or 0)) / (1024 * 1024))
        self.samples += 1
        self.peak["cpu"] = max(self.peak["cpu"], cpu)
        self.peak["ram"] = max(self.peak["ram"], ram_percent)

        recent = list(self.cpu)[-_SMOOTH_TICKS:]
        recent_disk = list(self.disk)[-_SMOOTH_TICKS:]
        verdict = judge_bottleneck(
            {
                "cpu_percent": sum(recent) / len(recent),
                "ram_percent": ram_percent,
                "swap_percent": swap,
                "disk_mb_s": (sum(recent_disk) / len(recent_disk)) if disk_now is not None else None,
            },
            smoothed,
            self.limits,
        )
        return {
            "time": time.strftime("%H:%M:%S"),
            "cores": psutil.cpu_count(logical=True) or 1,
            "cpu": cpu,
            "ram_percent": ram_percent,
            "ram_used": vm.used if vm is not None else None,
            "ram_total": vm.total if vm is not None else None,
            "swap_percent": swap,
            "disk_read": read,
            "disk_write": write,
            "net_up": up,
            "net_down": down,
            "top_cpu": top_by(smoothed, "cpu", self.top),
            "top_ram": top_by(smoothed, "rss", self.top),
            "verdict": verdict,
            "battery": self._battery(),
        }

    @staticmethod
    def _battery() -> str:
        try:
            batt = psutil.sensors_battery()
        except Exception:
            batt = None
        if batt is None:
            return "No battery detected"
        return f"{batt.percent:.0f}% " + ("(plugged in)" if batt.power_plugged else "(on battery)")


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------
def _system_panel(state: dict[str, Any], monitor: LiveMonitor, spark: int) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="label", no_wrap=True, min_width=8)
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(style="muted", no_wrap=True)

    def graph(values: Any, maximum: float | None, level: str = "accent") -> Text:
        return Text(sparkline(list(values), spark, maximum), style=level)

    ram_text = (
        f"{format_bytes(state['ram_used'])} / {format_bytes(state['ram_total'])}"
        if state["ram_total"]
        else NA
    )
    grid.add_row("CPU", usage_bar(state["cpu"], 20), graph(monitor.cpu, 100), f"{state['cores']} threads")
    grid.add_row("Memory", usage_bar(state["ram_percent"], 20, warn=80, bad=92), graph(monitor.ram, 100), ram_text)
    disk_text = Text(f"▼ {format_rate(state['disk_read'])}  ▲ {format_rate(state['disk_write'])}", style="value")
    grid.add_row("Disk", disk_text, graph(monitor.disk, None), "read / write")
    net_text = Text(f"▼ {format_rate(state['net_down'])}  ▲ {format_rate(state['net_up'])}", style="value")
    grid.add_row("Network", net_text, graph(monitor.net, None), "down / up")
    grid.add_row("Battery", Text(state["battery"], style="value"), "", "")
    return Panel(grid, title="[title]System[/title]", title_align="left", border_style="border", expand=True)


def _apps_panel(title: str, groups: list[dict[str, Any]], top: int, by: str) -> Panel:
    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(no_wrap=True, ratio=1, overflow="ellipsis")
    table.add_column(justify="right", no_wrap=True)
    table.add_column(justify="right", no_wrap=True)
    table.add_row(Text("App", style="accent"), Text("CPU", style="accent"), Text("Memory", style="accent"))
    for index in range(top):
        if index < len(groups):
            g = groups[index]
            cpu_style = percent_level(g["cpu"], warn=20, bad=40)
            ram_style = percent_level(g["ram_percent"], warn=10, bad=20)
            table.add_row(
                Text(_app_label(g), style="value"),
                Text(f"{g['cpu']:.1f}%", style=cpu_style if by == "cpu" else "muted"),
                Text(g["rss_text"], style=ram_style if by == "rss" else "muted"),
            )
        else:
            table.add_row("", "", "")
    return Panel(table, title=f"[title]{title}[/title]", title_align="left", border_style="border", expand=True)


def _verdict_panel(verdict: dict[str, Any]) -> Panel:
    label = "All clear" if verdict["primary"] == "None" else f"Bottleneck: {verdict['primary']}"
    first = Text.assemble(status_text(verdict["level"], label), ("  ", ""), (verdict["summary"], "value"))
    first.no_wrap, first.overflow = True, "ellipsis"
    second = Text(verdict["hint"] or " ", style="muted")  # may wrap to two lines; the panel height is fixed
    return Panel(
        Group(first, second),
        title="[title]What is slowing it down?[/title]",
        title_align="left",
        border_style="border",
        expand=True,
        height=5,
    )


def render_live(state: dict[str, Any], monitor: LiveMonitor, interval: float, width: int = 100) -> Group:
    """The whole live screen (fixed height)."""
    top = monitor.top
    header = Text.assemble(
        ("LAPFORGE LIVE", "brand"),
        (f"   {state['time']}   refresh every {interval:g}s", "muted"),
    )
    apps = Table.grid(expand=True, padding=(0, 1))
    apps.add_column(ratio=1)
    apps.add_column(ratio=1)
    apps.add_row(
        _apps_panel("Top apps by CPU", state["top_cpu"], top, "cpu"),
        _apps_panel("Top apps by memory", state["top_ram"], top, "rss"),
    )
    footer = Text("Press Ctrl+C to stop. Read-only: nothing is closed, changed or sent anywhere.", style="muted")
    return Group(header, _system_panel(state, monitor, _spark_width(width)), apps, _verdict_panel(state["verdict"]), footer)


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------
def run_live(console: Console, interval: float = 1.0, duration: float | None = None, top: int = 5) -> dict[str, Any]:
    """Show the live monitor until Ctrl+C (or `duration` seconds). Returns a short summary.

    When the output is not a terminal (piped to a file), a single snapshot is printed instead.
    """
    if not console.is_terminal:
        return show_snapshot(console, seconds=min(duration or 3.0, 10.0), top=top)
    monitor = LiveMonitor(top=top)
    monitor.prime()
    time.sleep(min(0.6, max(0.2, interval)))
    started = time.monotonic()
    state = monitor.tick()
    try:
        with Live(
            render_live(state, monitor, interval, console.width),
            console=console,
            auto_refresh=False,
            vertical_overflow="crop",
            transient=False,
        ) as live:
            next_at = time.monotonic() + interval
            while duration is None or time.monotonic() - started < duration:
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                # Keep a steady rhythm even though reading the apps takes a moment.
                next_at = max(next_at + interval, time.monotonic() + 0.05)
                state = monitor.tick()
                live.update(render_live(state, monitor, interval, console.width), refresh=True)
    except KeyboardInterrupt:
        pass
    summary = {
        "samples": monitor.samples,
        "peak_cpu": round(monitor.peak["cpu"], 1),
        "peak_ram": round(monitor.peak["ram"], 1),
        "verdict": state["verdict"],
    }
    console.print(
        f"  [muted]Stopped after {monitor.samples} readings. Peak CPU {summary['peak_cpu']:.0f}%, "
        f"peak memory {summary['peak_ram']:.0f}%.[/muted]"
    )
    return summary


def bottleneck_tables(result: dict[str, Any], top: int = 5) -> list[Any]:
    """Tables for a one-off bottleneck report (used by `live --once`)."""
    metrics = result["metrics"]

    def pct(value: float | None, warn: float, bad: float) -> Text:
        return Text(f"{value:.0f}%" if value is not None else NA, style=percent_level(value, warn, bad))

    limits = BottleneckLimits()
    rows: list[tuple[str, Any]] = [
        ("CPU (average)", pct(metrics["cpu_percent"], limits.cpu_warn, limits.cpu_bad)),
        ("Memory in use", pct(metrics["ram_percent"], limits.ram_warn, limits.ram_bad)),
        ("Page file in use", pct(metrics["swap_percent"], limits.swap_warn, 90)),
        ("Disk activity", f"{metrics['disk_mb_s']:.1f} MB/s" if metrics["disk_mb_s"] is not None else NA),
        ("Measured for", f"{result.get('seconds', 0):g} seconds"),
    ]
    tables: list[Any] = [kv_table("System load", rows)]

    apps = grid_table("Biggest apps", ["Using most CPU", "CPU", "Using most memory", "Memory"])
    for index in range(top):
        c = result["apps_by_cpu"][index] if index < len(result["apps_by_cpu"]) else None
        r = result["apps_by_ram"][index] if index < len(result["apps_by_ram"]) else None
        apps.add_row(
            _app_label(c) if c else "",
            f"{c['cpu']:.1f}%" if c else "",
            _app_label(r) if r else "",
            r["rss_text"] if r else "",
        )
    tables.append(apps)

    if result["findings"]:
        findings = grid_table("Findings", ["Resource", "Status", "What is happening", "What to do"])
        for item in result["findings"]:
            findings.add_row(item["resource"], status_text(item["level"], item["value"]), item["summary"], item["hint"])
        tables.append(findings)
    return tables


def show_snapshot(console: Console, seconds: float = 3.0, top: int = 5) -> dict[str, Any]:
    """Measure for a few seconds, print the load, the biggest apps and the verdict. Returns the result."""
    with console.status(f"[accent]Watching your laptop for {seconds:g} seconds...[/accent]", spinner="dots"):
        result = measure_bottleneck(seconds=seconds, interval=1.0)
    for table in bottleneck_tables(result, top):
        console.print(table)
    console.print(_verdict_panel(result))
    return result
