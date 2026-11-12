"""Guided Windows repair: DISM + SFC.

Two modes:
  * "check"  - read-only health checks (DISM CheckHealth, DISM ScanHealth, SFC verify-only).
  * "repair" - DISM RestoreHealth, then SFC /scannow. Windows fixes its own system files.

Dry-run is the default: you see the plan and nothing is executed. A real run
needs an Administrator terminal and your confirmation. These tools only restore
Microsoft's own system files, so there is no setting of yours to undo.
"""

from __future__ import annotations

import codecs
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm
from rich.text import Text

from ..ui.tables import grid_table, kv_table
from ..ui.theme import make_console, status_text
from ..utils import IS_WINDOWS, is_admin

MODES = ("check", "repair")
_LEVEL_ORDER = {"good": 0, "muted": 1, "warn": 2, "bad": 3}
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_BAR_ONLY_RE = re.compile(r"^\[?[=\s]*\d{1,3}(?:\.\d+)?\s*%[=\s]*\]?$")
_DISM_ERROR_RE = re.compile(r"error:\s*(0x[0-9a-f]+|\d+)", re.IGNORECASE)
_MAX_LINES_KEPT = 500

ProgressFn = Callable[[float], None]
OutputFn = Callable[[str], None]


@dataclass(frozen=True)
class RepairStep:
    """One command of a repair plan."""

    key: str
    label: str
    tool: str  # "sfc" | "dism"
    args: tuple[str, ...]
    description: str
    duration: str
    modifies: bool = False
    timeout_s: int = 3600


_STEPS: dict[str, RepairStep] = {
    "dism_check": RepairStep(
        "dism_check", "DISM quick check", "dism",
        ("/Online", "/Cleanup-Image", "/CheckHealth"),
        "Asks Windows whether the component store was already flagged as damaged.", "under 1 minute", False, 600,
    ),
    "dism_scan": RepairStep(
        "dism_scan", "DISM full scan", "dism",
        ("/Online", "/Cleanup-Image", "/ScanHealth"),
        "Scans the component store for damage. Changes nothing.", "5-15 minutes", False, 3600,
    ),
    "sfc_verify": RepairStep(
        "sfc_verify", "SFC verify only", "sfc",
        ("/verifyonly",),
        "Checks protected system files. Changes nothing.", "5-15 minutes", False, 5400,
    ),
    "dism_restore": RepairStep(
        "dism_restore", "DISM repair", "dism",
        ("/Online", "/Cleanup-Image", "/RestoreHealth"),
        "Repairs the component store (may download clean files from Windows Update).", "10-30 minutes", True, 7200,
    ),
    "sfc_scannow": RepairStep(
        "sfc_scannow", "SFC repair", "sfc",
        ("/scannow",),
        "Replaces damaged protected system files with good copies.", "5-20 minutes", True, 5400,
    ),
}
_MODE_STEPS = {
    "check": ("dism_check", "dism_scan", "sfc_verify"),
    "repair": ("dism_restore", "sfc_scannow"),
}


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------
def repair_supported() -> bool:
    """DISM and SFC only exist on Windows."""
    return IS_WINDOWS


def get_steps(mode: str = "check") -> list[RepairStep]:
    """Steps for 'check' (read-only) or 'repair' (DISM RestoreHealth then SFC /scannow)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    return [_STEPS[key] for key in _MODE_STEPS[mode]]


def _system_tool(name: str) -> str:
    """Full path of sfc/dism so a look-alike earlier in PATH is never run."""
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    base = Path(root)
    sysnative = base / "Sysnative"  # 32-bit Python on 64-bit Windows
    folder = sysnative if sysnative.is_dir() else base / "System32"
    return str(folder / f"{name}.exe")


def command_line(step: RepairStep) -> list[str]:
    """The exact command that would run for a step."""
    return [_system_tool(step.tool), *step.args]


def repair_plan(mode: str = "check") -> dict[str, Any]:
    """Describe what a run would do. Executes nothing."""
    steps = get_steps(mode)
    return {
        "mode": mode,
        "supported": repair_supported(),
        "admin": is_admin(),
        "modifies": any(s.modifies for s in steps),
        "steps": steps,
    }


# --------------------------------------------------------------------------
# Reading the result
# --------------------------------------------------------------------------
def interpret_output(tool: str, text: str, returncode: int | None) -> tuple[str, str]:
    """Turn the tool's final text into (level, plain-English summary).

    Level is 'good', 'warn' or 'bad'. The text matching is for English Windows;
    anything else falls back to the exit code.
    """
    low = text.lower()
    if tool == "sfc":
        if "did not find any integrity violations" in low:
            return "good", "No integrity violations found"
        if "unable to fix" in low:
            return "bad", "Damaged files found that SFC could not fix - run the DISM repair, restart, then SFC again"
        if "successfully repaired" in low:
            return "good", "Damaged files were found and repaired"
        if "repair pending" in low:
            return "warn", "A repair is already pending - restart Windows, then run this again"
        if "found integrity violations" in low or "found corrupt files" in low:
            return "warn", "Integrity violations found - run 'repair' mode to fix them"
        if "could not perform the requested operation" in low:
            return "bad", "SFC could not run (needs an Administrator terminal, or Windows is busy)"
        return "warn", "Finished, but the result was not recognised - see C:\\Windows\\Logs\\CBS\\CBS.log"

    if "error: 740" in low or "elevated permissions" in low:
        return "bad", "Needs an Administrator terminal"
    if "component store is repairable" in low:
        return "warn", "Component store is damaged but repairable - run 'repair' mode"
    if "no component store corruption detected" in low:
        return "good", "No component store corruption detected"
    if "restore operation completed successfully" in low:
        return "good", "Component store repaired"
    match = _DISM_ERROR_RE.search(text)
    if match or (returncode not in (0, None)):
        code = match.group(1) if match else str(returncode)
        return "bad", f"DISM reported error {code} - see C:\\Windows\\Logs\\DISM\\dism.log"
    if returncode == 0 or "operation completed successfully" in low:
        return "good", "Completed successfully"
    return "warn", "Finished, but the result was not recognised - see C:\\Windows\\Logs\\DISM\\dism.log"


def _worst(levels: list[str]) -> str:
    return max(levels or ["muted"], key=lambda lv: _LEVEL_ORDER.get(lv, 1))


# --------------------------------------------------------------------------
# Running one step
# --------------------------------------------------------------------------
def _decoder(tool: str) -> codecs.IncrementalDecoder:
    """SFC prints UTF-16LE when piped; DISM prints in a normal 8-bit encoding."""
    return codecs.getincrementaldecoder("utf-16-le" if tool == "sfc" else "utf-8")(errors="ignore")


def run_step(
    step: RepairStep,
    on_progress: ProgressFn | None = None,
    on_output: OutputFn | None = None,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Run one command and stream its progress. Never raises for tool failures.

    `command` overrides the command line (used by tests with a harmless stand-in).
    """
    cmd = command or command_line(step)
    result: dict[str, Any] = {
        "key": step.key,
        "label": step.label,
        "command": " ".join(cmd),
        "ran": False,
        "returncode": None,
        "level": "bad",
        "summary": "",
        "percent": 0.0,
        "seconds": 0.0,
        "tail": [],
    }
    kwargs: dict[str, Any] = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    started = time.time()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, **kwargs)
    except (OSError, ValueError) as exc:
        result["summary"] = f"Could not start: {exc}"
        return result

    timed_out = threading.Event()

    def stop() -> None:
        timed_out.set()
        try:
            proc.kill()
        except OSError:
            pass

    watchdog = threading.Timer(step.timeout_s, stop)
    watchdog.daemon = True
    watchdog.start()

    decoder = _decoder(step.tool)
    lines: list[str] = []
    buffer = ""
    percent = 0.0

    def handle(chunk: str) -> None:
        nonlocal percent
        for part in re.split(r"[\r\n]+", chunk):
            part = part.replace("\x00", "").strip()
            if not part:
                continue
            found = _PERCENT_RE.search(part)
            if found:
                value = min(100.0, float(found.group(1)))
                if value >= percent:
                    percent = value
                    if on_progress is not None:
                        on_progress(percent)
                if _BAR_ONLY_RE.match(part):
                    continue
            if len(lines) < _MAX_LINES_KEPT:
                lines.append(part)
            if on_output is not None:
                on_output(part)

    try:
        assert proc.stdout is not None
        while True:
            data = proc.stdout.read(4096)
            if not data:
                break
            buffer += decoder.decode(data)
            cut = max(buffer.rfind("\n"), buffer.rfind("\r"))
            if cut >= 0:
                handle(buffer[: cut + 1])
                buffer = buffer[cut + 1:]
        buffer += decoder.decode(b"", final=True)
        if buffer:
            handle(buffer)
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        proc.wait()
        result.update(ran=True, summary="Stopped by you (Ctrl+C)", seconds=round(time.time() - started, 1))
        raise
    finally:
        watchdog.cancel()

    result.update(ran=True, returncode=proc.returncode, percent=percent, seconds=round(time.time() - started, 1))
    result["tail"] = lines[-6:]
    if timed_out.is_set():
        result.update(level="bad", summary=f"Stopped: took longer than {step.timeout_s // 60} minutes")
        return result
    level, summary = interpret_output(step.tool, "\n".join(lines), proc.returncode)
    result.update(level=level, summary=summary)
    return result


# --------------------------------------------------------------------------
# Running a plan
# --------------------------------------------------------------------------
def run_repair(
    mode: str = "check",
    dry_run: bool = True,
    on_step_start: Callable[[RepairStep], None] | None = None,
    on_progress: ProgressFn | None = None,
    on_output: OutputFn | None = None,
) -> dict[str, Any]:
    """Run a repair plan. DRY-RUN BY DEFAULT: with dry_run=True nothing is executed.

    Returns {"mode", "dry_run", "supported", "admin", "refused", "note", "steps", "level"}.
    """
    plan = repair_plan(mode)
    out: dict[str, Any] = {
        "mode": mode,
        "dry_run": dry_run,
        "supported": plan["supported"],
        "admin": plan["admin"],
        "refused": False,
        "note": "",
        "steps": [],
        "level": "muted",
    }
    if not plan["supported"]:
        out["note"] = "DISM and SFC need Windows (not available on this system)"
        return out
    if dry_run:
        out["note"] = "Dry run - nothing was executed"
        out["steps"] = [
            {"key": s.key, "label": s.label, "command": " ".join(command_line(s)), "ran": False,
             "level": "muted", "summary": f"Would run ({s.duration})", "seconds": 0.0}
            for s in plan["steps"]
        ]
        return out
    if not plan["admin"]:
        out.update(refused=True, level="bad", note="Needs an Administrator terminal (right-click the terminal > Run as administrator)")
        return out

    for step in plan["steps"]:
        if on_step_start is not None:
            on_step_start(step)
        out["steps"].append(run_step(step, on_progress=on_progress, on_output=on_output))
    out["level"] = _worst([s["level"] for s in out["steps"]])
    return out


# --------------------------------------------------------------------------
# Guided (interactive) flow
# --------------------------------------------------------------------------
def _plan_table(plan: dict[str, Any]):
    table = grid_table(f"Repair plan - {plan['mode']}", ["Step", "What it does", "Time", "Changes system"])
    for step in plan["steps"]:
        table.add_row(step.label, step.description, step.duration, "Yes" if step.modifies else "No")
    return table


def _result_table(result: dict[str, Any]):
    table = grid_table(f"Repair result - {result['mode']}", ["Step", "Status", "Result", "Time"])
    for step in result["steps"]:
        seconds = f"{int(step['seconds'] // 60)}m {int(step['seconds'] % 60)}s" if step.get("ran") else "-"
        label = {"good": "OK", "warn": "Attention", "bad": "Problem", "muted": "Not run"}.get(step["level"], "")
        table.add_row(step["label"], status_text(step["level"], label), Text(step["summary"]), seconds)
    return table


def guided_repair(
    mode: str = "check",
    dry_run: bool = True,
    assume_yes: bool = False,
    console: Console | None = None,
) -> dict[str, Any]:
    """Show the plan, ask for confirmation, run it with a progress bar, show the result.

    With the default dry_run=True it only shows the plan. Use dry_run=False for a real run.
    """
    console = console or make_console()
    plan = repair_plan(mode)
    if not plan["supported"]:
        console.print(kv_table("Repair", [("Status", "DISM and SFC need Windows (not available on this system).")]))
        return run_repair(mode, dry_run=True)

    console.print(_plan_table(plan))
    if dry_run:
        console.print("  [good]DRY RUN - nothing was executed.[/good]")
        console.print("  [muted]Run it for real with an Administrator terminal and dry_run=False.[/muted]")
        return run_repair(mode, dry_run=True)
    if not plan["admin"]:
        console.print("  [bad]Needs an Administrator terminal.[/bad] [muted]Right-click the terminal > Run as administrator.[/muted]")
        return run_repair(mode, dry_run=False)

    if plan["modifies"]:
        console.print("  [warn]This repairs Windows system files and can take 15-60 minutes. Keep the laptop plugged in.[/warn]")
    if not assume_yes:
        try:
            go = Confirm.ask("  Start now?", console=console, default=False)
        except (EOFError, KeyboardInterrupt):
            go = False
        if not go:
            console.print("  [muted]Cancelled - nothing was executed.[/muted]")
            out = run_repair(mode, dry_run=True)
            out.update(dry_run=False, note="Cancelled by user")
            return out

    progress = Progress(
        TextColumn("[accent]{task.description}[/accent]"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    state: dict[str, Any] = {"task": None}

    def start(step: RepairStep) -> None:
        if state["task"] is not None:
            progress.update(state["task"], completed=100)
        state["task"] = progress.add_task(step.label, total=100)

    def move(percent: float) -> None:
        if state["task"] is not None:
            progress.update(state["task"], completed=percent)

    try:
        with progress:
            result = run_repair(mode, dry_run=False, on_step_start=start, on_progress=move)
            if state["task"] is not None:
                progress.update(state["task"], completed=100)
    except KeyboardInterrupt:
        console.print("\n  [warn]Stopped. Windows may finish or roll back the current step on its own.[/warn]")
        return {"mode": mode, "dry_run": False, "supported": True, "admin": True, "refused": False,
                "note": "Stopped by user", "steps": [], "level": "warn"}

    console.print(_result_table(result))
    if result["level"] == "good":
        console.print("  [good]All steps finished without problems.[/good]")
    elif result["level"] == "warn":
        console.print("  [warn]Finished with warnings. Read the table above.[/warn]")
    else:
        console.print("  [bad]A step reported a problem. Read the table above.[/bad]")
    if mode == "repair":
        console.print("  [muted]Restart Windows to finish any pending repairs.[/muted]")
    return result
