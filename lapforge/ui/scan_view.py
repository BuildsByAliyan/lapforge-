"""The Smart Scan screen: a short, calm summary with clear next steps.

    Smart Scan result
    Health score: 82/100 (Good)
    What needs attention        (at most 5 lines, each with a coloured marker)
    Junk files you can remove: 3.2 GB
    Recommended next steps      (numbered)

This module only prints. The question "Remove the junk files now?" is asked by the
`lapforge scan` command, which then runs the normal clean flow.
"""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from ..elevate import running_as_text
from .theme import status_text

MAX_ATTENTION_LINES = 5


def score_line(result: dict[str, Any]) -> Text:
    """'Health score: 82/100 (Good)' - or a plain note when the checks could not run."""
    line = Text("Health score: ", style="label")
    if result.get("score") is None:
        line.append("could not be read on this PC", style="muted")
        return line
    level = result.get("level", "muted")
    line.append(f"{result['score']}/100", style=level)
    line.append(f" ({result.get('grade', 'Unknown')})", style=level)
    return line


def attention_lines(result: dict[str, Any]) -> list[Text]:
    """One line per problem (worst first, at most 5): a coloured marker, the name and the result."""
    lines: list[Text] = []
    for problem in result.get("problems", [])[:MAX_ATTENTION_LINES]:
        line = Text("  ")
        line.append_text(status_text(problem.get("level", "warn")))
        line.append(f" {problem.get('label', 'Unknown')}", style="value")
        if problem.get("value"):
            line.append(f" - {problem['value']}", style="muted")
        lines.append(line)
    return lines


def junk_line(result: dict[str, Any]) -> Text:
    """'Junk files you can remove: 3.2 GB', or why the number is not available."""
    line = Text("Junk files you can remove: ", style="label")
    if not result.get("junk_available"):
        line.append("not available on this PC", style="muted")
    elif int(result.get("junk_bytes", 0)) <= 0:
        line.append("none found", style="good")
    else:
        line.append(str(result.get("junk_text", "N/A")), style="accent")
        line.append(f"  ({int(result.get('junk_files', 0)):,} files)", style="muted")
    return line


def recommendation_lines(result: dict[str, Any]) -> list[Text]:
    """Numbered next steps, each followed by the command that does it."""
    lines: list[Text] = []
    for number, rec in enumerate(result.get("recommendations", []), start=1):
        step = Text(f"  {number}. ", style="muted")
        step.append(rec["text"], style="value")
        lines.append(step)
        if rec.get("command"):
            lines.append(Text(f"     Command: {rec['command']}", style="muted"))
    return lines


def render_scan(console: Console, result: dict[str, Any]) -> None:
    """Print the Smart Scan result."""
    level = result.get("level", "muted") if result.get("score") is not None else "muted"
    body: list[Any] = [score_line(result)]
    if result.get("partial"):
        body.append(Text("  Partial result - many checks could not be read on this PC.", style="warn"))
    body.append(Text(""))

    body.append(Text("What needs attention", style="title"))
    attention = attention_lines(result)
    if attention:
        body.extend(attention)
        hidden = int(result.get("problem_total", 0)) - len(attention)
        if hidden > 0:
            body.append(Text(f"  ...and {hidden} more. Run `lapforge health` to see all of them.", style="muted"))
    elif result.get("score") is None:
        body.append(Text("  The health checks could not run. Run `lapforge health` to try again.", style="muted"))
    else:
        body.append(Text("  Nothing needs attention.", style="good"))
    body.append(Text(""))

    body.append(junk_line(result))
    for name in result.get("open_browsers", []):
        body.append(Text(f"  {name} is open, so its cache was not counted.", style="muted"))
    body.append(Text(""))

    body.append(Text("Recommended next steps", style="title"))
    steps = recommendation_lines(result)
    body.extend(steps if steps else [Text("  Nothing to do right now.", style="good")])

    console.print(
        Panel(
            Group(*body),
            title="[title]Smart Scan result[/title]",
            title_align="left",
            box=box.ROUNDED,
            border_style=level,
            expand=False,
            padding=(1, 2),
        )
    )
    for name in result.get("failed", []):
        console.print(f"  [muted]{name} could not be read, so it is not part of this result.[/muted]")
    admin = bool(result.get("admin"))
    console.print(f"  [muted]Running as:[/muted] [{'good' if admin else 'value'}]{running_as_text(admin)}[/]")
    console.print("  [muted]Smart Scan itself only looks. Nothing is removed unless you confirm it.[/muted]")
