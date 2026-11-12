"""LapForge doctor: health checks, a 0-100 score and a fix hint for every problem."""

from __future__ import annotations

from typing import Any

from .checks import Thresholds, run_checks
from .fix_hints import get_hint
from .score import compute_score, grade_for, top_problems


def run_doctor(thresholds: Thresholds | None = None, **collected: Any) -> dict[str, Any]:
    """Run all checks and score them: {"checks", "score", "problems"}.

    Optional keyword arguments (storage, memory, battery, startup, security, disk, temps)
    take data that was already collected, so it is not read again.
    """
    checks = run_checks(thresholds, **collected)
    return {"checks": checks, "score": compute_score(checks), "problems": top_problems(checks)}


__all__ = [
    "Thresholds",
    "compute_score",
    "get_hint",
    "grade_for",
    "run_checks",
    "run_doctor",
    "top_problems",
]
