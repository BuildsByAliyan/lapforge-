"""LapForge Smart Scan: one action that checks the whole laptop and recommends next steps.

Smart Scan only READS. It runs these jobs at the same time:

    startup  - how many programs start with Windows
    battery  - battery wear summary
    clean    - how much space the quick clean (and the browser cache) could free
    doctor   - the health checks and the 0-100 Health score

Every job is guarded: if one fails or runs too long, the others still count and the
result says which job could not be read. `run_smart_scan()` never raises.

The decision logic lives in the pure function `build_recommendations`, so it can be
tested without Windows.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .doctor import run_doctor
from .doctor.fix_hints import split_hint
from .utils import format_bytes, is_admin

# The scan stops waiting for a slow job after this many seconds (the others still count).
SCAN_TIMEOUT_SECONDS = 75.0

MAX_PROBLEMS = 5

# Below this much reclaimable space, a clean is not worth recommending.
CLEAN_MIN_BYTES = 100 * 1024 * 1024

# Same limits as the doctor (Thresholds): 12 or more enabled programs, or 3 heavy ones.
STARTUP_REVIEW_COUNT = 12
STARTUP_REVIEW_HEAVY = 3

BATTERY_WEAR_REVIEW = 20.0

JOB_NAMES = ("startup", "battery", "clean", "doctor")

# Plain names for the jobs, used when a job could not be read.
JOB_LABELS = {
    "startup": "Startup programs",
    "battery": "Battery",
    "clean": "Junk files",
    "doctor": "Health checks",
}

_FALLBACK_ACTION = "Run `lapforge health` for details."


# --------------------------------------------------------------------------
# The four jobs (each one is guarded by run_smart_scan)
# --------------------------------------------------------------------------
def _job_startup() -> dict[str, Any]:
    from .collectors.software import get_startup_apps

    return get_startup_apps()


def _job_battery() -> dict[str, Any]:
    from .collectors.battery_health import get_battery_health

    return get_battery_health()


def _clean_targets_for_scan() -> tuple[list[Any], bool, list[str]]:
    """(targets, browser cache found, names of open browsers that were skipped)."""
    from .cleaner import get_targets
    from .cleaner.browser_cache import browser_name, browser_targets, detect_browsers, detected_open_browsers

    targets = list(get_targets("quick"))
    browser_found = bool(detect_browsers())
    open_keys = detected_open_browsers() if browser_found else []
    if browser_found:
        targets += browser_targets(set(open_keys))  # browsers that are open are skipped
    return targets, browser_found, [browser_name(key) for key in open_keys]


def _job_clean() -> dict[str, Any]:
    """Quick-clean preview plus browser cache. Measures only; nothing is deleted."""
    from .cleaner import build_preview, cleaner_supported

    if not cleaner_supported():
        return {"supported": False, "bytes": 0, "files": 0, "browser_found": False, "open_browsers": [], "needs_admin": False}
    targets, browser_found, open_browsers = _clean_targets_for_scan()
    preview = build_preview("quick" + (" + browser" if browser_found else ""), targets)
    needs_admin = any(
        item.get("needs_admin") and item.get("available") and not item.get("refused") for item in preview["items"]
    )
    return {
        "supported": True,
        "bytes": int(preview["total_bytes"]),
        "files": int(preview["total_files"]),
        "browser_found": browser_found,
        "open_browsers": open_browsers,
        "needs_admin": needs_admin,
    }


def _wait_for(future: Future[Any], timeout: float) -> Any:
    """Result of another job, or None when it failed or took too long."""
    try:
        return future.result(timeout=timeout)
    except Exception:
        return None


def _job_doctor(startup: Any = None, battery: Any = None) -> dict[str, Any]:
    """Health checks. Startup and battery data that was already read is reused, not read again."""
    collected: dict[str, Any] = {}
    if isinstance(startup, dict):
        collected["startup"] = startup
    if isinstance(battery, dict):
        collected["battery"] = battery
    return run_doctor(**collected)


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def summarize_problems(problems: list[dict[str, Any]], limit: int = MAX_PROBLEMS) -> list[dict[str, Any]]:
    """Top problems (worst first, as given) with a plain meaning and an action each. Pure."""
    summary: list[dict[str, Any]] = []
    for problem in problems[:limit]:
        meaning, action = split_hint(str(problem.get("hint", "")))
        summary.append(
            {
                "id": str(problem.get("id", "")),
                "label": str(problem.get("label", "Unknown")),
                "level": str(problem.get("level", "warn")),
                "value": str(problem.get("value", "")),
                "meaning": meaning,
                "action": action or _FALLBACK_ACTION,
            }
        )
    return summary


def startup_summary(data: Any) -> dict[str, Any]:
    """Short startup facts: {"available", "enabled", "heavy"}. Pure."""
    if not isinstance(data, dict) or not data.get("available"):
        return {"available": False, "enabled": 0, "heavy": 0}
    return {"available": True, "enabled": int(data.get("enabled_count", 0) or 0), "heavy": int(data.get("heavy_count", 0) or 0)}


def battery_summary(data: Any) -> dict[str, Any]:
    """Short battery facts: {"present", "wear_percent", "level"}. Pure."""
    if not isinstance(data, dict) or not data.get("present"):
        return {"present": False, "wear_percent": None, "level": "muted"}
    wear = data.get("wear_percent")
    return {
        "present": True,
        "wear_percent": float(wear) if isinstance(wear, (int, float)) else None,
        "level": str(data.get("level", "muted")),
    }


def build_recommendations(
    *,
    problems: list[dict[str, Any]],
    junk_bytes: int = 0,
    startup: dict[str, Any] | None = None,
    battery: dict[str, Any] | None = None,
    open_browsers: list[str] | None = None,
    partial: bool = False,
    admin: bool = False,
    elevation_supported: bool = False,
    clean_needs_admin: bool = False,
) -> list[dict[str, str]]:
    """Decide what to recommend, most useful first. Pure: no reading, no Windows calls.

    Each recommendation is {"action", "text", "command"}. Actions: "clean", "close_browser",
    "free_space", "review_startup", "fix_security", "check_drive", "check_battery",
    "check_temperature", "run_as_admin".
    """
    startup = startup or {}
    battery = battery or {}
    open_browsers = open_browsers or []
    ids = {str(p.get("id", "")) for p in problems}
    recs: list[dict[str, str]] = []

    def add(action: str, text: str, command: str) -> None:
        recs.append({"action": action, "text": text, "command": command})

    if junk_bytes >= CLEAN_MIN_BYTES:
        add("clean", f"Remove junk files (about {format_bytes(junk_bytes)}).", "lapforge clean")
    if open_browsers:
        names = " and ".join(open_browsers)
        one = len(open_browsers) == 1
        verb, pronoun, owner = ("is", "it", "its") if one else ("are", "them", "their")
        add("close_browser", f"{names} {verb} open. Close {pronoun} and scan again to include {owner} cache.", "lapforge scan")
    if "disk.health" in ids:
        add("check_drive", "Back up your files now. The drive reports a problem.", "lapforge info")
    if "storage.space" in ids:
        add("free_space", "Free up space on the system drive. Remove apps you no longer use.", "lapforge apps --sort size")
    if "thermal.temperature" in ids:
        add("check_temperature", "The laptop is running hot. Close heavy apps and clean the vents.", "lapforge live")
    if any(i.startswith("security.") for i in ids):
        add("fix_security", "Review the security warnings.", "lapforge security")
    enabled = int(startup.get("enabled", 0) or 0)
    heavy = int(startup.get("heavy", 0) or 0)
    if "startup.count" in ids or enabled >= STARTUP_REVIEW_COUNT or heavy >= STARTUP_REVIEW_HEAVY:
        add("review_startup", f"Review Startup programs ({enabled} start with Windows).", "lapforge startup --manage")
    wear = battery.get("wear_percent")
    if "battery.wear" in ids or (isinstance(wear, (int, float)) and wear >= BATTERY_WEAR_REVIEW):
        add("check_battery", "The battery has lost some of its capacity. Check the details.", "lapforge info")
    if not admin and elevation_supported and (partial or clean_needs_admin):
        add("run_as_admin", "Run Smart Scan as Administrator for a complete check.", "lapforge --admin scan")
    return recs


def should_offer_clean(result: dict[str, Any]) -> bool:
    """True when the scan found enough junk files to offer a clean. Pure."""
    return bool(result.get("junk_available")) and int(result.get("junk_bytes", 0)) >= CLEAN_MIN_BYTES


# --------------------------------------------------------------------------
# The scan
# --------------------------------------------------------------------------
def _empty_result() -> dict[str, Any]:
    return {
        "score": None,
        "grade": "Unknown",
        "level": "muted",
        "partial": False,
        "coverage": None,
        "counts": {"good": 0, "warn": 0, "bad": 0, "unknown": 0},
        "problems": [],
        "problem_total": 0,
        "junk_available": False,
        "junk_bytes": 0,
        "junk_files": 0,
        "junk_text": "N/A",
        "browser_found": False,
        "open_browsers": [],
        "startup": startup_summary(None),
        "battery": battery_summary(None),
        "recommendations": [],
        "failed": [],
        "admin": False,
        "seconds": 0.0,
    }


def run_smart_scan(timeout: float = SCAN_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run every job at the same time and return one summary. Never raises, never deletes.

    Returns {"score", "grade", "level", "partial", "coverage", "counts", "problems",
    "problem_total", "junk_available", "junk_bytes", "junk_files", "junk_text", "browser_found",
    "open_browsers", "startup", "battery", "recommendations", "failed", "admin", "seconds"}.
    `score` is None when the health checks could not run. `failed` lists the plain names of
    jobs that could not be read.
    """
    started = time.monotonic()
    result = _empty_result()
    raw: dict[str, Any] = {}
    failed: list[str] = []
    pool = ThreadPoolExecutor(max_workers=len(JOB_NAMES))
    try:
        futures: dict[str, Future[Any]] = {
            "startup": pool.submit(_job_startup),
            "battery": pool.submit(_job_battery),
            "clean": pool.submit(_job_clean),
        }
        # The doctor reuses the startup and battery data instead of reading it a second time.
        # It waits only a short time for them; if they are slow it reads what it needs itself.
        patience = min(timeout / 3, 25.0)
        futures["doctor"] = pool.submit(
            lambda: _job_doctor(_wait_for(futures["startup"], patience), _wait_for(futures["battery"], patience))
        )
        for name in JOB_NAMES:
            remaining = max(0.5, timeout - (time.monotonic() - started))
            try:
                raw[name] = futures[name].result(timeout=remaining)
            except Exception:  # a failed or too slow job never stops the scan
                raw[name] = None
                failed.append(JOB_LABELS[name])
    except Exception:
        failed = [JOB_LABELS[name] for name in JOB_NAMES if raw.get(name) is None]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    admin = is_admin()
    result["admin"] = admin
    result["failed"] = failed

    doctor = raw.get("doctor")
    if isinstance(doctor, dict) and isinstance(doctor.get("score"), dict):
        info = doctor["score"]
        result.update(
            score=info.get("score"),
            grade=info.get("grade", "Unknown"),
            level=info.get("level", "muted"),
            partial=bool(info.get("partial")),
            coverage=info.get("coverage"),
            counts=dict(info.get("counts", result["counts"])),
        )
        problems = list(doctor.get("problems", []))
        result["problem_total"] = len(problems)
        result["problems"] = summarize_problems(problems)
    else:
        problems = []
        if JOB_LABELS["doctor"] not in failed:
            failed.append(JOB_LABELS["doctor"])

    clean = raw.get("clean")
    if isinstance(clean, dict) and clean.get("supported"):
        result.update(
            junk_available=True,
            junk_bytes=int(clean.get("bytes", 0) or 0),
            junk_files=int(clean.get("files", 0) or 0),
            browser_found=bool(clean.get("browser_found")),
            open_browsers=list(clean.get("open_browsers", [])),
        )
        result["junk_text"] = format_bytes(result["junk_bytes"])

    result["startup"] = startup_summary(raw.get("startup"))
    result["battery"] = battery_summary(raw.get("battery"))

    try:
        from .elevate import elevation_supported

        can_restart = bool(elevation_supported())
    except Exception:
        can_restart = False
    result["recommendations"] = build_recommendations(
        problems=problems,
        junk_bytes=result["junk_bytes"],
        startup=result["startup"],
        battery=result["battery"],
        open_browsers=result["open_browsers"],
        partial=result["partial"],
        admin=admin,
        elevation_supported=can_restart,
        clean_needs_admin=bool(isinstance(clean, dict) and clean.get("needs_admin")),
    )
    result["seconds"] = round(time.monotonic() - started, 1)
    return result
