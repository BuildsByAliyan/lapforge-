"""ASCII banner for LapForge."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from .. import __author__, __tagline__, __version__
from .theme import PALETTE

# 5-row block letters, hand-drawn for the word LAPFORGE.
_GLYPHS: dict[str, list[str]] = {
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "F": ["█████", "█    ", "████ ", "█    ", "█    "],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "G": [" ████", "█    ", "█  ██", "█   █", " ████"],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
}
_WORD = "LAPFORGE"


def banner_lines(word: str = _WORD) -> list[str]:
    """Join the glyphs of `word` into 5 plain-text rows."""
    rows = []
    for row in range(5):
        rows.append(" ".join(_GLYPHS[ch][row] for ch in word))
    return rows


BANNER_WIDTH = len(banner_lines()[0])


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _blend(start: str, end: str, ratio: float) -> str:
    """Colour at `ratio` (0..1) between two hex colours."""
    s, e = _hex_to_rgb(start), _hex_to_rgb(end)
    mixed = tuple(round(a + (b - a) * ratio) for a, b in zip(s, e))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def render_banner_text() -> Text:
    """Gradient banner (brand colour at the top fading to accent at the bottom)."""
    lines = banner_lines()
    text = Text()
    for index, line in enumerate(lines):
        ratio = index / (len(lines) - 1)
        text.append(line + "\n", style=f"bold {_blend(PALETTE['brand'], PALETTE['accent'], ratio)}")
    return text


def print_banner(console: Console) -> None:
    """Print the big banner, or a one-line title on very narrow terminals."""
    if console.width >= BANNER_WIDTH + 2:
        console.print(render_banner_text(), end="")
    else:
        console.print("[brand]LAPFORGE[/brand]")
    console.print(
        f"[muted]v{__version__}  |  by {__author__}[/muted]\n[accent]{__tagline__}[/accent]\n"
    )
