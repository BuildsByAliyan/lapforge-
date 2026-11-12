"""Reusable table builders and small cell formatters."""

from __future__ import annotations

from typing import Any

from rich import box
from rich.table import Table
from rich.text import Text

from ..utils import NA
from .theme import percent_level, status_text


def kv_table(title: str, rows: list[tuple[str, Any]]) -> Table:
    """Two-column 'Field | Value' table. Values may be plain text or rich Text."""
    table = Table(
        title=title,
        title_style="title",
        title_justify="left",
        box=box.ROUNDED,
        border_style="border",
        show_header=False,
        pad_edge=True,
        expand=False,
    )
    table.add_column("Field", style="label", no_wrap=True)
    table.add_column("Value", style="value")
    for label, value in rows:
        table.add_row(label, value if isinstance(value, Text) else str(value))
    return table


def grid_table(title: str, columns: list[str]) -> Table:
    """Table with a header row; the caller adds rows with .add_row(...)."""
    table = Table(
        title=title,
        title_style="title",
        title_justify="left",
        box=box.ROUNDED,
        border_style="border",
        header_style="accent",
        expand=False,
    )
    for col in columns:
        table.add_column(col, style="value")
    return table


def health_text(value: str) -> Text:
    """Disk/drive health word -> coloured icon."""
    low = value.lower()
    if low == "healthy":
        return status_text("good", value)
    if low == "warning":
        return status_text("warn", value)
    if low == "unhealthy":
        return status_text("bad", value)
    return Text(value, style="muted" if value == NA else "value")


def battery_text(batt: dict[str, Any]) -> Text:
    """Battery charge cell. A LOW charge is the problem, so the scale is inverted."""
    if not batt["present"]:
        return Text("No battery detected", style="muted")
    percent = batt["percent"]
    level = percent_level(100 - percent, warn=80, bad=90) if percent is not None else "muted"
    text = Text(f"{percent:.0f}% " if percent is not None else "N/A ", style=level)
    text.append("(charging / plugged in)" if batt["plugged"] else "(on battery)", style="muted")
    return text


# --------------------------------------------------------------------------
# Network tables (wifi / speed commands)
# --------------------------------------------------------------------------
def signal_bar(percent: int | None, width: int = 16) -> Text:
    """WiFi signal bar: strong is green, weak is red."""
    if percent is None:
        return Text(NA, style="muted")
    level = percent_level(100 - percent, warn=50, bad=75)
    filled = int(round(width * max(0, min(100, percent)) / 100))
    bar = Text("█" * filled, style=level)
    bar.append("░" * (width - filled), style="muted")
    bar.append(f" {percent}%", style=level)
    return bar


def current_wifi_table(info: dict[str, Any]) -> Table:
    """Where this laptop is connected right now."""
    if not info.get("available"):
        return kv_table("Current WiFi", [("Status", "Not available (no WiFi adapter or not Windows)")])
    if not info.get("connected"):
        return kv_table("Current WiFi", [("Status", status_text("warn", "Not connected to WiFi"))])
    return kv_table(
        "Current WiFi",
        [
            ("Network (SSID)", info["ssid"]),
            ("Signal", signal_bar(info["signal_percent"])),
            ("Radio", f"{info['radio']}  (channel {info['channel']})"),
            ("Security", info["authentication"]),
            ("Link speed", f"{info['rx_mbps']} Mbps down / {info['tx_mbps']} Mbps up"),
        ],
    )


def wifi_profiles_table(profiles: list[dict[str, Any]], reveal: bool) -> Table:
    """Saved networks. Passwords are shown only when `reveal` was requested."""
    table = grid_table("Saved WiFi networks", ["Network", "Security", "Password"])
    for p in profiles:
        state = p["state"]
        if state == "revealed":
            password = Text(p["password"], style="warn")
        elif state == "open":
            password = Text(p["password"], style="muted")
        elif state == "needs_admin":
            password = Text("Not readable (run as Administrator)", style="bad")
        else:
            password = Text("Hidden (use --reveal)", style="muted")
        table.add_row(p["ssid"], p["authentication"], password)
    return table


def _ms(value: float | None) -> str:
    if value is None:
        return NA
    return f"{value:.1f} ms" if value < 10 else f"{value:.0f} ms"


def speed_table(report: dict[str, Any]) -> Table:
    """Ping / latency results per host, with a status icon."""
    table = grid_table("Ping & latency", ["Host", "Address", "Via", "Min", "Avg", "Max", "Loss", "Status"])
    labels = {"good": "Good", "warn": "Slow", "bad": "Poor"}
    for row in report["hosts"]:
        if row["avg_ms"] is None:
            status = status_text("bad", "Down")
            loss = "100%"
        else:
            status = status_text(row["level"], labels[row["level"]])
            loss = f"{row['loss_percent']:.0f}%"
        table.add_row(
            row["label"], row["host"], row["method"], _ms(row["min_ms"]), _ms(row["avg_ms"]), _ms(row["max_ms"]), loss, status
        )
    return table


# --------------------------------------------------------------------------
# Security / apps / startup / drivers tables
# --------------------------------------------------------------------------
_STATUS_WORDS = {"good": "OK", "warn": "Warning", "bad": "At risk", "muted": "Unknown"}


def level_summary(good: int, warn: int, bad: int, unknown: int = 0, words: tuple[str, str, str] = ("good", "warning", "problem")) -> Text:
    """One-line count such as '● 4 good   ▲ 1 warning   ✖ 0 problem'."""
    text = Text("  ")
    text.append_text(status_text("good", f"{good} {words[0]}"))
    text.append("   ")
    text.append_text(status_text("warn", f"{warn} {words[1]}"))
    text.append("   ")
    text.append_text(status_text("bad", f"{bad} {words[2]}"))
    if unknown:
        text.append("   ")
        text.append_text(status_text("muted", f"{unknown} unknown"))
    return text


def security_table(report: dict[str, Any]) -> Table:
    """Security checks with status icon and a fix hint for anything not green."""
    table = grid_table("Security status", ["Check", "Status", "Details", "How to fix"])
    for c in report["checks"]:
        hint = Text(c["hint"], style="muted") if c["hint"] else Text("")
        table.add_row(c["label"], status_text(c["level"], _STATUS_WORDS.get(c["level"], "Unknown")), c["value"], hint)
    return table


def apps_table(apps: list[dict[str, Any]], limit: int | None = None) -> Table:
    """Installed programs. `limit` shows only the first N rows."""
    shown = apps if limit is None else apps[:limit]
    table = grid_table("Installed apps", ["#", "App", "Version", "Publisher", "Installed", "Size"])
    for number, a in enumerate(shown, start=1):
        table.add_row(
            str(number),
            a["name"],
            a["version"],
            a["publisher"],
            a["install_date"] or NA,
            a["size"],
        )
    return table


def _short(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def startup_table(items: list[dict[str, Any]]) -> Table:
    """Startup programs: heaviest first, with an impact icon."""
    table = grid_table("Startup programs", ["Program", "Source", "Memory now", "Impact", "Command"])
    for i in items:
        mem_style = {"bad": "bad", "warn": "warn"}.get(i["level"], "value")
        table.add_row(
            i["name"],
            i["source"],
            Text(i["memory_text"], style=mem_style if i["memory"] is not None else "muted"),
            status_text(i["level"], i["impact"]),
            Text(_short(i["command"], 44), style="muted"),
        )
    return table


def drivers_table(drivers: list[dict[str, Any]], limit: int | None = None) -> Table:
    """Drivers, oldest first, with an age icon."""
    shown = drivers if limit is None else drivers[:limit]
    table = grid_table("Drivers (oldest first)", ["Device", "Class", "Vendor", "Version", "Date", "Age", "Status"])
    labels = {"good": "Recent", "warn": "Aging", "bad": "Old", "muted": NA}
    for d in shown:
        table.add_row(
            _short(d["name"], 38),
            d["cls"],
            _short(d["vendor"], 22),
            d["version"],
            d["date"],
            d["age_text"],
            status_text(d["level"], labels.get(d["level"], NA)),
        )
    return table
