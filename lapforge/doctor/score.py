"""Turn a list of doctor checks into a 0-100 health score."""

from __future__ import annotations

from typing import Any

from .checks import WEIGHTS

# How much of a check's weight is lost at each level. 'muted' (unknown) costs nothing:
# a laptop is not punished for something LapForge could not read.
LEVEL_FACTOR = {"bad": 1.0, "warn": 0.5}

# Any red check keeps the score out of the 'Good' range; a red check on something
# critical (weight >= CRITICAL_WEIGHT) keeps it in 'Poor/Fair'.
CAP_ANY_BAD = 79
CAP_CRITICAL = 59
CRITICAL_WEIGHT = 15

GRADES: list[tuple[int, str, str]] = [
    (90, "Excellent", "good"),
    (75, "Good", "good"),
    (50, "Fair", "warn"),
    (0, "Poor", "bad"),
]

# Below this share of readable checks (by weight) the result is marked as partial.
PARTIAL_BELOW = 0.70

# Check id -> key in checks.WEIGHTS, where the two differ.
_WEIGHT_KEY_FOR_ID = {"storage.space": "storage.system"}

_READABLE_LEVELS = ("good", "warn", "bad")

_SEVERITY = {"bad": 0, "warn": 1}


def grade_for(score: int) -> tuple[str, str]:
    """Score -> (grade name, level colour)."""
    for floor, name, level in GRADES:
        if score >= floor:
            return name, level
    return "Poor", "bad"


def _expected_weight(check: dict[str, Any]) -> float:
    """Weight of a check. Unreadable checks often carry weight 0, so use the normal weight for their id."""
    weight = float(check.get("weight", 0) or 0)
    if weight > 0:
        return weight
    check_id = str(check.get("id", ""))
    return float(WEIGHTS.get(_WEIGHT_KEY_FOR_ID.get(check_id, check_id), 0))


def compute_coverage(checks: list[dict[str, Any]]) -> float:
    """Share (0.0 to 1.0) of the checks, by weight, that could be read on this PC."""
    if not checks:
        return 1.0
    total = readable = 0.0
    for check in checks:
        weight = _expected_weight(check)
        total += weight
        if check.get("level", "muted") in _READABLE_LEVELS:
            readable += weight
    if total <= 0:  # no weights known: fall back to counting checks
        return round(sum(1 for c in checks if c.get("level", "muted") in _READABLE_LEVELS) / len(checks), 3)
    return round(readable / total, 3)


def compute_score(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Score a list of checks.

    Returns {"score", "grade", "level", "penalty", "capped", "counts", "coverage", "partial"}.
    `coverage` is the share of checks (by weight) that could be read; `partial` is True when
    it is below 70%. Coverage never changes the score itself.
    """
    counts = {"good": 0, "warn": 0, "bad": 0, "unknown": 0}
    penalty = 0.0
    cap = 100
    for check in checks:
        level = check.get("level", "muted")
        weight = float(check.get("weight", 0))
        if level in LEVEL_FACTOR:
            penalty += weight * LEVEL_FACTOR[level]
            counts[level] += 1
        elif level == "good":
            counts["good"] += 1
        else:
            counts["unknown"] += 1
        if level == "bad":
            cap = min(cap, CAP_CRITICAL if weight >= CRITICAL_WEIGHT else CAP_ANY_BAD)
    raw = max(0, min(100, int(round(100 - penalty))))
    score = min(raw, cap)
    grade, level = grade_for(score)
    coverage = compute_coverage(checks)
    return {
        "score": score,
        "grade": grade,
        "level": level,
        "penalty": round(penalty, 1),
        "capped": score < raw,
        "counts": counts,
        "coverage": coverage,
        "partial": coverage < PARTIAL_BELOW,
    }


def top_problems(checks: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    """Checks that need attention, worst first (red before yellow, then by weight)."""
    problems = [c for c in checks if c.get("level") in _SEVERITY]
    problems.sort(key=lambda c: (_SEVERITY[c["level"]], -float(c.get("weight", 0)), c.get("label", "")))
    return problems if limit is None else problems[:limit]
