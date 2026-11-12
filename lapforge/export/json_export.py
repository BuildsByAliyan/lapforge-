"""JSON export: the machine-readable system snapshot.

The snapshot is a plain dict (see `lapforge.export.collect_snapshot`). Every
snapshot carries a `schema_version` so other tools can tell which layout they
are reading. `validate_snapshot` checks that layout without extra libraries.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

# Top-level keys every snapshot must have, and the type each must be.
REQUIRED_KEYS: dict[str, type] = {
    "schema_version": str,
    "tool": dict,
    "generated_at": str,
    "privacy": dict,
    "system": dict,
    "os": dict,
    "bios": dict,
    "cpu": dict,
    "memory": dict,
    "storage": dict,
    "gpu": list,
    "battery": dict,
    "disk_health": dict,
    "temperatures": dict,
    "network": dict,
    "security": dict,
    "startup": dict,
    "health": dict,
}


def make_json_safe(value: Any) -> Any:
    """Convert any value into something json.dumps accepts (no NaN/Infinity, str keys)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((make_json_safe(v) for v in value), key=str)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def to_json(snapshot: dict[str, Any], indent: int = 2) -> str:
    """The snapshot as pretty JSON text (UTF-8 friendly, ends with a newline)."""
    return json.dumps(make_json_safe(snapshot), indent=indent, ensure_ascii=False) + "\n"


def validate_snapshot(snapshot: Any) -> list[str]:
    """Return a list of problems with a snapshot (empty list = valid)."""
    if not isinstance(snapshot, dict):
        return ["snapshot must be a JSON object"]
    problems: list[str] = []
    for key, expected in REQUIRED_KEYS.items():
        if key not in snapshot:
            problems.append(f"missing key: {key}")
        elif not isinstance(snapshot[key], expected):
            problems.append(f"{key} must be {expected.__name__}, got {type(snapshot[key]).__name__}")
    if snapshot.get("schema_version") not in (None, SCHEMA_VERSION) and isinstance(snapshot.get("schema_version"), str):
        problems.append(f"unknown schema_version {snapshot['schema_version']!r} (expected {SCHEMA_VERSION})")
    health = snapshot.get("health")
    if isinstance(health, dict):
        score = (health.get("score") or {}).get("score") if isinstance(health.get("score"), dict) else None
        if score is not None and not (isinstance(score, int) and 0 <= score <= 100):
            problems.append("health.score.score must be an integer from 0 to 100")
        if not isinstance(health.get("checks", []), list):
            problems.append("health.checks must be a list")
    return problems
