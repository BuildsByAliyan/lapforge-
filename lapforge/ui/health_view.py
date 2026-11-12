"""Screen for `lapforge health`: score bar, checks table and fix hints."""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..collectors.security import status_word
from ..doctor.fix_hints import MEANS_LABEL, TODO_LABEL, split_hint
from ..utils import is_admin
from .glossary import explain, find_terms
from .tables import grid_table, level_summary
from .theme import status_text

BAR_WIDTH = 30
CATEGORY_ORDER = ["Security", "Storage", "Memory", "Battery", "Startup", "Thermals"]
_SEVERITY = {"bad": 0, "warn": 1, "good": 2, "muted": 3}


def score_bar(score: int, level: str, width: int = BAR_WIDTH) -> Text:
    """Bar such as '████████████████░░░░░░░░░░░░░░' coloured by grade."""
    value = max(0, min(100, int(score)))
    filled = int(round(width * value / 100))
    bar = Text("█" * filled, style=level)
    bar.append("░" * (width - filled), style="muted")
    return bar


def partial_notice(info: dict[str, Any], admin: bool | None = None) -> list[Text]:
    """Lines shown under the score when too many checks could not be read (empty otherwise)."""
    if not info.get("partial"):
        return []
    unknown = info["counts"]["unknown"]
    noun = "check" if unknown == 1 else "checks"
    lines = [Text(f"  Partial result - {unknown} {noun} could not be read", style="warn")]
    if not (is_admin() if admin is None else admin):
        lines.append(Text("  Run as Administrator for a complete check.", style="muted"))
    return lines


def score_panel(info: dict[str, Any], admin: bool | None = None) -> Panel:
    """Big score number, grade, bar and a green/yellow/red count line."""
    level = info["level"]
    headline = Text()
    headline.append("Health score  ", style="label")
    headline.append(f"{info['score']}", style=f"bold {level}")
    headline.append(" / 100   ", style="muted")
    headline.append_text(status_text(level, info["grade"]))

    counts = info["counts"]
    lines: list[Any] = [
        headline,
        score_bar(info["score"], level),
        level_summary(counts["good"], counts["warn"], counts["bad"], counts["unknown"]),
    ]
    lines.extend(partial_notice(info, admin))
    if info.get("capped"):
        lines.append(Text("  The score is limited because at least one serious problem was found.", style="muted"))
    return Panel(Group(*lines), title="[title]PC Health[/title]", title_align="left", box=box.ROUNDED, border_style=level, expand=False, padding=(1, 2))


def _category_rank(name: str) -> int:
    return CATEGORY_ORDER.index(name) if name in CATEGORY_ORDER else len(CATEGORY_ORDER)


def checks_table(checks: list[dict[str, Any]]):
    """All checks grouped by area, each with a status icon."""
    table = grid_table("Checks", ["Area", "Check", "Status", "Details"])
    ordered = sorted(checks, key=lambda c: (_category_rank(c["category"]), c["category"]))
    previous = ""
    for check in ordered:
        area = check["category"] if check["category"] != previous else ""
        previous = check["category"]
        table.add_row(
            Text(area, style="label"),
            check["label"],
            status_text(check["level"], status_word(check["level"])),
            check["value"],
        )
    return table


def term_notes(problem: dict[str, Any], seen: set[str]) -> list[tuple[str, str]]:
    """(term, plain explanation) for technical terms in a problem, each explained only once.

    `seen` holds the terms already explained on this screen; it is updated here.
    """
    text = " ".join(str(problem.get(key, "")) for key in ("label", "value", "hint"))
    notes: list[tuple[str, str]] = []
    for term in find_terms(text):
        if term not in seen:
            seen.add(term)
            notes.append((term, explain(term)))
    return notes


def problem_body(problem: dict[str, Any], seen: set[str]) -> Text:
    """One problem: its name and result, then 'What this means', 'What to do' and any new terms."""
    means, todo = split_hint(problem.get("hint", ""))
    body = Text(problem["label"], style=f"bold {problem['level']}")
    body.append(f" - {problem['value']}\n", style="muted")
    if means:
        body.append(f"{MEANS_LABEL} ", style="label")
        body.append(f"{means}\n", style="value")
    body.append(f"{TODO_LABEL} ", style="label")
    body.append(todo or "No fix hint available.", style="value")
    for term, text in term_notes(problem, seen):
        body.append(f"\n{term}: ", style="label")
        body.append(text, style="muted")
    return body


def hints_panel(problems: list[dict[str, Any]]) -> Panel:
    """Numbered 'what this means / what to do' list (worst first), or a green all-clear."""
    if not problems:
        return Panel(
            status_text("good", "No problems found. Nothing to fix."),
            title="[title]How to fix[/title]",
            title_align="left",
            box=box.ROUNDED,
            border_style="good",
            expand=False,
            padding=(0, 2),
        )
    ordered = sorted(problems, key=lambda c: _SEVERITY.get(c["level"], 3))
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)  # "1. ●"
    grid.add_column()  # problem + hint (wraps inside its own column)
    seen: set[str] = set()
    for number, problem in enumerate(ordered, start=1):
        marker = Text(f"{number}. ", style="muted")
        marker.append_text(status_text(problem["level"]))
        grid.add_row(marker, problem_body(problem, seen))
        if number < len(ordered):
            grid.add_row("", "")
    worst = ordered[0]["level"]
    return Panel(
        grid,
        title="[title]How to fix[/title]",
        title_align="left",
        box=box.ROUNDED,
        border_style=worst,
        expand=False,
        padding=(1, 2),
    )


def render_health(console: Console, result: dict[str, Any]) -> None:
    """Print the whole health screen from run_doctor() output."""
    console.print(score_panel(result["score"]))
    console.print(checks_table(result["checks"]))
    console.print(hints_panel(result["problems"]))
    unknown = result["score"]["counts"]["unknown"]
    if unknown:
        note = f"  {unknown} check(s) could not be read on this PC and are shown as Unknown. They do not lower the score."
        if not is_admin():
            note += " Try an Administrator terminal for more detail."
        console.print(f"[muted]{note}[/muted]")


def exit_code_for(info: dict[str, Any], fail_on: str = "never") -> int:
    """0 normally; 1 when the chosen severity was found (handy for scripts)."""
    counts = info["counts"]
    if fail_on == "bad" and counts["bad"] > 0:
        return 1
    if fail_on == "warn" and (counts["bad"] > 0 or counts["warn"] > 0):
        return 1
    return 0
