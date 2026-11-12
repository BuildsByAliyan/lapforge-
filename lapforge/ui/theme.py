"""LapForge colour theme (dark by default) and small styling helpers.

Green = good, Yellow = warning, Red = problem.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

PALETTE = {
    "brand": "#00d7af",
    "accent": "#5fafff",
    "good": "#5fd75f",
    "warn": "#ffd75f",
    "bad": "#ff5f5f",
    "muted": "#8a8a8a",
    "label": "#87afd7",
    "value": "#e4e4e4",
    "border": "#444c56",
}

LAPFORGE_THEME = Theme(
    {
        "brand": f"bold {PALETTE['brand']}",
        "accent": PALETTE["accent"],
        "good": f"bold {PALETTE['good']}",
        "warn": f"bold {PALETTE['warn']}",
        "bad": f"bold {PALETTE['bad']}",
        "muted": PALETTE["muted"],
        "label": PALETTE["label"],
        "value": PALETTE["value"],
        "border": PALETTE["border"],
        "title": f"bold {PALETTE['brand']}",
    }
)

STATUS_ICONS = {"good": "●", "warn": "▲", "bad": "✖", "muted": "○"}


def make_console() -> Console:
    """Console with the LapForge theme applied."""
    return Console(theme=LAPFORGE_THEME, highlight=False)


def percent_level(percent: float | None, warn: float = 70, bad: float = 90) -> str:
    """Map a usage percentage to 'good' / 'warn' / 'bad' ('muted' if unknown)."""
    if percent is None:
        return "muted"
    if percent >= bad:
        return "bad"
    if percent >= warn:
        return "warn"
    return "good"


def status_text(level: str, label: str = "") -> Text:
    """Coloured icon (plus optional label), e.g. status_text('good', 'Healthy')."""
    icon = STATUS_ICONS.get(level, STATUS_ICONS["muted"])
    text = Text(icon, style=level if level in STATUS_ICONS else "muted")
    if label:
        text.append(f" {label}", style=level if level in STATUS_ICONS else "muted")
    return text


def usage_bar(percent: float | None, width: int = 20, warn: float = 70, bad: float = 90) -> Text:
    """Coloured progress bar such as '████████░░░░ 45.0%'."""
    if percent is None:
        return Text("N/A", style="muted")
    value = max(0.0, min(100.0, float(percent)))
    filled = int(round(width * value / 100))
    style = percent_level(value, warn, bad)
    bar = Text("█" * filled, style=style)
    bar.append("░" * (width - filled), style="muted")
    bar.append(f" {value:.1f}%", style=style)
    return bar
