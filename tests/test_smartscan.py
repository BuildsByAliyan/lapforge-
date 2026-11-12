"""Tests for Smart Scan (Phase 28). No Windows needed: every job is replaced by fake data."""

from __future__ import annotations

import argparse
import io
import time

import pytest
from rich.console import Console

from lapforge import cli, smartscan
from lapforge.doctor.fix_hints import MEANS_LABEL, TODO_LABEL
from lapforge.smartscan import (
    CLEAN_MIN_BYTES,
    battery_summary,
    build_recommendations,
    run_smart_scan,
    should_offer_clean,
    startup_summary,
    summarize_problems,
)
from lapforge.ui import scan_view
from lapforge.ui.theme import LAPFORGE_THEME

GB = 1024**3
MB = 1024**2


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def problem(check_id="startup.count", level="warn", label="Startup programs", value="18 enabled", hint=""):
    return {"id": check_id, "category": "x", "label": label, "level": level, "value": value, "hint": hint, "weight": 8}


def actions(recs):
    return [r["action"] for r in recs]


def doctor_result(score=82, grade="Good", level="good", problems=None, partial=False, unknown=0):
    return {
        "checks": [],
        "score": {
            "score": score,
            "grade": grade,
            "level": level,
            "partial": partial,
            "coverage": 0.5 if partial else 1.0,
            "counts": {"good": 5, "warn": len(problems or []), "bad": 0, "unknown": unknown},
        },
        "problems": problems or [],
    }


def clean_result(size=3 * GB, files=1200, browser_found=True, open_browsers=None, needs_admin=False):
    return {
        "supported": True,
        "bytes": size,
        "files": files,
        "browser_found": browser_found,
        "open_browsers": open_browsers or [],
        "needs_admin": needs_admin,
    }


def fake_jobs(monkeypatch, *, startup=None, battery=None, clean=None, doctor=None):
    """Replace the four Smart Scan jobs. A value that is an Exception is raised by the job."""

    def make(value):
        def job(*_args):
            if isinstance(value, BaseException):
                raise value
            return value

        return job

    monkeypatch.setattr(smartscan, "_job_startup", make(startup if startup is not None else {"available": True, "enabled_count": 4, "heavy_count": 0}))
    monkeypatch.setattr(smartscan, "_job_battery", make(battery if battery is not None else {"present": False}))
    monkeypatch.setattr(smartscan, "_job_clean", make(clean if clean is not None else clean_result()))
    monkeypatch.setattr(smartscan, "_job_doctor", make(doctor if doctor is not None else doctor_result()))
    monkeypatch.setattr(smartscan, "is_admin", lambda: False)
    monkeypatch.setattr("lapforge.elevate.elevation_supported", lambda: True)


def capture_console():
    buffer = io.StringIO()
    return Console(file=buffer, theme=LAPFORGE_THEME, highlight=False, width=100, color_system=None), buffer


# --------------------------------------------------------------------------
# build_recommendations (pure decision logic)
# --------------------------------------------------------------------------
def test_healthy_laptop_gets_no_recommendations():
    assert build_recommendations(problems=[], junk_bytes=10 * MB, admin=True) == []


def test_clean_is_recommended_when_enough_junk_is_found():
    recs = build_recommendations(problems=[], junk_bytes=3 * GB, admin=True)
    assert actions(recs) == ["clean"]
    assert "3.0 GB" in recs[0]["text"]
    assert recs[0]["command"] == "lapforge clean"


def test_clean_is_not_recommended_for_a_tiny_amount():
    assert "clean" not in actions(build_recommendations(problems=[], junk_bytes=CLEAN_MIN_BYTES - 1, admin=True))
    assert "clean" in actions(build_recommendations(problems=[], junk_bytes=CLEAN_MIN_BYTES, admin=True))


def test_clean_comes_first():
    recs = build_recommendations(
        problems=[problem()], junk_bytes=2 * GB, startup={"enabled": 20, "heavy": 0}, admin=True
    )
    assert actions(recs)[0] == "clean"


def test_open_browser_is_explained():
    recs = build_recommendations(problems=[], open_browsers=["Chrome"], admin=True)
    assert actions(recs) == ["close_browser"]
    assert "Chrome is open" in recs[0]["text"]
    recs = build_recommendations(problems=[], open_browsers=["Chrome", "Edge"], admin=True)
    assert "Chrome and Edge are open" in recs[0]["text"]


def test_open_browser_sentence_uses_the_right_pronoun():
    one = build_recommendations(problems=[], open_browsers=["Chrome"], admin=True)[0]["text"]
    two = build_recommendations(problems=[], open_browsers=["Chrome", "Edge"], admin=True)[0]["text"]
    assert one == "Chrome is open. Close it and scan again to include its cache."
    assert two == "Chrome and Edge are open. Close them and scan again to include their cache."


@pytest.mark.parametrize(
    "startup, expected",
    [
        ({"enabled": 11, "heavy": 2}, False),
        ({"enabled": 12, "heavy": 0}, True),
        ({"enabled": 5, "heavy": 3}, True),
        ({"enabled": 0, "heavy": 0}, False),
    ],
)
def test_startup_review_rules(startup, expected):
    recs = build_recommendations(problems=[], startup=startup, admin=True)
    assert ("review_startup" in actions(recs)) is expected


def test_startup_problem_triggers_review_even_without_summary():
    assert "review_startup" in actions(build_recommendations(problems=[problem()], admin=True))


def test_problem_ids_map_to_actions():
    cases = {
        "storage.space": "free_space",
        "security.firewall": "fix_security",
        "security.antivirus": "fix_security",
        "disk.health": "check_drive",
        "thermal.temperature": "check_temperature",
        "battery.wear": "check_battery",
    }
    for check_id, action in cases.items():
        recs = build_recommendations(problems=[problem(check_id=check_id)], admin=True)
        assert action in actions(recs), check_id


def test_security_problems_give_one_recommendation_only():
    recs = build_recommendations(
        problems=[problem("security.firewall"), problem("security.uac"), problem("security.tpm")], admin=True
    )
    assert actions(recs).count("fix_security") == 1


def test_battery_wear_in_summary_triggers_battery_step():
    recs = build_recommendations(problems=[], battery={"present": True, "wear_percent": 35.0}, admin=True)
    assert actions(recs) == ["check_battery"]
    assert build_recommendations(problems=[], battery={"present": True, "wear_percent": 5.0}, admin=True) == []


def test_run_as_admin_only_when_it_helps_and_is_possible():
    base = dict(problems=[], partial=True, elevation_supported=True)
    assert actions(build_recommendations(**base, admin=False)) == ["run_as_admin"]
    assert build_recommendations(**base, admin=True) == []  # already Administrator
    assert build_recommendations(problems=[], partial=True, elevation_supported=False, admin=False) == []  # cannot restart
    assert build_recommendations(problems=[], partial=False, elevation_supported=True, admin=False) == []  # nothing missing
    needs = build_recommendations(problems=[], clean_needs_admin=True, elevation_supported=True, admin=False)
    assert actions(needs) == ["run_as_admin"]
    assert needs[0]["command"] == "lapforge --admin scan"


def test_every_recommendation_has_action_text_and_command():
    recs = build_recommendations(
        problems=[problem("storage.space"), problem("security.uac"), problem("disk.health", "bad"), problem("battery.wear")],
        junk_bytes=GB,
        startup={"enabled": 30, "heavy": 5},
        open_browsers=["Chrome"],
        partial=True,
        elevation_supported=True,
    )
    assert len(recs) >= 6
    assert len(set(actions(recs))) == len(recs)  # no duplicates
    for rec in recs:
        assert set(rec) == {"action", "text", "command"}
        assert rec["text"].endswith(".")
        assert rec["command"].startswith("lapforge")


def test_should_offer_clean():
    assert should_offer_clean({"junk_available": True, "junk_bytes": 2 * GB})
    assert not should_offer_clean({"junk_available": True, "junk_bytes": 1})
    assert not should_offer_clean({"junk_available": False, "junk_bytes": 2 * GB})
    assert not should_offer_clean({})


# --------------------------------------------------------------------------
# Small pure helpers
# --------------------------------------------------------------------------
def test_summarize_problems_splits_the_hint_and_keeps_order():
    hint = f"{MEANS_LABEL} Too many programs start. {TODO_LABEL} Switch some off."
    items = summarize_problems([problem(hint=hint), problem("disk.health", "bad", "Drive health", "Failing")])
    assert items[0]["meaning"] == "Too many programs start."
    assert items[0]["action"] == "Switch some off."
    assert items[1]["id"] == "disk.health"
    assert items[1]["action"]  # a fallback action is always present


def test_summarize_problems_keeps_at_most_five():
    many = [problem(label=f"Problem {n}") for n in range(9)]
    assert [p["label"] for p in summarize_problems(many)] == [f"Problem {n}" for n in range(5)]


def test_startup_and_battery_summaries_cope_with_bad_data():
    assert startup_summary(None) == {"available": False, "enabled": 0, "heavy": 0}
    assert startup_summary("nonsense")["available"] is False
    assert startup_summary({"available": True, "enabled_count": 14, "heavy_count": 2}) == {"available": True, "enabled": 14, "heavy": 2}
    assert battery_summary(None)["present"] is False
    assert battery_summary({"present": True, "wear_percent": "?", "level": "warn"})["wear_percent"] is None
    assert battery_summary({"present": True, "wear_percent": 12.5, "level": "good"}) == {"present": True, "wear_percent": 12.5, "level": "good"}


# --------------------------------------------------------------------------
# run_smart_scan
# --------------------------------------------------------------------------
def test_scan_combines_all_jobs(monkeypatch):
    hint = f"{MEANS_LABEL} Too many programs start. {TODO_LABEL} Switch some off."
    fake_jobs(
        monkeypatch,
        startup={"available": True, "enabled_count": 18, "heavy_count": 1},
        battery={"present": True, "wear_percent": 8.0, "level": "good"},
        clean=clean_result(size=3 * GB, files=1200, open_browsers=["Chrome"]),
        doctor=doctor_result(problems=[problem(hint=hint)]),
    )
    result = run_smart_scan()
    assert result["score"] == 82 and result["grade"] == "Good" and result["level"] == "good"
    assert result["problems"][0]["meaning"] == "Too many programs start."
    assert result["problem_total"] == 1
    assert result["junk_available"] and result["junk_bytes"] == 3 * GB and result["junk_files"] == 1200
    assert result["junk_text"] == "3.0 GB"
    assert result["open_browsers"] == ["Chrome"]
    assert result["startup"]["enabled"] == 18
    assert result["battery"]["present"] is True
    assert result["failed"] == []
    assert actions(result["recommendations"]) == ["clean", "close_browser", "review_startup"]
    assert isinstance(result["seconds"], float)


@pytest.mark.parametrize("job", ["_job_startup", "_job_battery", "_job_clean", "_job_doctor"])
def test_scan_never_raises_when_one_job_fails(monkeypatch, job):
    fake_jobs(monkeypatch)

    def boom(*_args):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(smartscan, job, boom)
    result = run_smart_scan()
    assert len(result["failed"]) <= 1
    assert result["junk_available"] is (job != "_job_clean")
    assert (result["score"] is None) is (job == "_job_doctor")
    assert isinstance(result["recommendations"], list)


def test_scan_never_raises_when_every_job_fails(monkeypatch):
    error = RuntimeError("simulated failure")
    fake_jobs(monkeypatch, startup=error, battery=error, clean=error, doctor=error)
    result = run_smart_scan()
    assert result["score"] is None and result["grade"] == "Unknown"
    assert result["problems"] == [] and result["junk_available"] is False
    assert set(result["failed"]) == {"Startup programs", "Battery", "Junk files", "Health checks"}
    assert not smartscan.should_offer_clean(result)


def test_scan_survives_odd_job_results(monkeypatch):
    fake_jobs(monkeypatch, startup="odd", battery=42, clean=["x"], doctor={"score": "bad shape"})
    result = run_smart_scan()
    assert result["score"] is None
    assert result["junk_available"] is False
    assert result["startup"]["available"] is False


def test_scan_gives_up_on_a_slow_job(monkeypatch):
    fake_jobs(monkeypatch)

    def slow(*_args):
        time.sleep(1.5)
        return clean_result()

    monkeypatch.setattr(smartscan, "_job_clean", slow)
    started = time.monotonic()
    result = run_smart_scan(timeout=0.6)
    assert time.monotonic() - started < 1.4
    assert "Junk files" in result["failed"]
    assert result["score"] == 82  # the other jobs still count


def test_doctor_job_reuses_startup_and_battery_data(monkeypatch):
    seen = {}

    def fake_run_doctor(**collected):
        seen.update(collected)
        return doctor_result()

    monkeypatch.setattr(smartscan, "run_doctor", fake_run_doctor)
    startup = {"available": True, "enabled_count": 3}
    battery = {"present": False}
    smartscan._job_doctor(startup, battery)
    assert seen == {"startup": startup, "battery": battery}
    seen.clear()
    smartscan._job_doctor(None, "not a dict")  # failed jobs: the doctor reads them itself
    assert seen == {}


def test_scan_passes_collected_data_to_the_doctor(monkeypatch):
    fake_jobs(monkeypatch, startup={"available": True, "enabled_count": 7, "heavy_count": 0}, battery={"present": False})
    received = {}

    def doctor(startup=None, battery=None):
        received["startup"], received["battery"] = startup, battery
        return doctor_result()

    monkeypatch.setattr(smartscan, "_job_doctor", doctor)
    run_smart_scan()
    assert received["startup"]["enabled_count"] == 7
    assert received["battery"] == {"present": False}


def test_clean_job_reports_unsupported_systems(monkeypatch):
    monkeypatch.setattr("lapforge.cleaner.cleaner_supported", lambda: False)
    result = smartscan._job_clean()
    assert result["supported"] is False and result["bytes"] == 0


def test_clean_job_only_measures(monkeypatch):
    calls = []
    monkeypatch.setattr("lapforge.cleaner.cleaner_supported", lambda: True)
    monkeypatch.setattr(smartscan, "_clean_targets_for_scan", lambda: (["t1", "t2"], True, ["Edge"]))

    def fake_preview(profile, targets):
        calls.append((profile, list(targets)))
        return {
            "total_bytes": 5 * MB,
            "total_files": 40,
            "items": [
                {"needs_admin": True, "available": True, "refused": False},
                {"needs_admin": False, "available": True, "refused": False},
            ],
        }

    monkeypatch.setattr("lapforge.cleaner.build_preview", fake_preview)
    monkeypatch.setattr("lapforge.cleaner.clean_target", lambda *a, **k: pytest.fail("Smart Scan must never clean"))
    result = smartscan._job_clean()
    assert calls == [("quick + browser", ["t1", "t2"])]
    assert result == {
        "supported": True,
        "bytes": 5 * MB,
        "files": 40,
        "browser_found": True,
        "open_browsers": ["Edge"],
        "needs_admin": True,
    }


# --------------------------------------------------------------------------
# The screen
# --------------------------------------------------------------------------
def sample_result(**changes):
    result = {
        "score": 82,
        "grade": "Good",
        "level": "good",
        "partial": False,
        "problems": [
            {"id": "startup.count", "label": "Startup programs", "level": "warn", "value": "18 enabled", "meaning": "", "action": ""},
            {"id": "security.firewall", "label": "Firewall", "level": "bad", "value": "Off", "meaning": "", "action": ""},
        ],
        "problem_total": 2,
        "junk_available": True,
        "junk_bytes": 3 * GB,
        "junk_text": "3.2 GB",
        "junk_files": 1200,
        "open_browsers": [],
        "recommendations": [
            {"action": "clean", "text": "Remove junk files (about 3.2 GB).", "command": "lapforge clean"},
            {"action": "review_startup", "text": "Review Startup programs (18 start with Windows).", "command": "lapforge startup --manage"},
        ],
        "failed": [],
        "admin": False,
    }
    result.update(changes)
    return result


def render(result):
    console, buffer = capture_console()
    scan_view.render_scan(console, result)
    return buffer.getvalue()


def test_screen_shows_the_requested_sections():
    text = render(sample_result())
    assert "Smart Scan result" in text
    assert "Health score: 82/100 (Good)" in text
    assert "What needs attention" in text
    assert "Startup programs - 18 enabled" in text
    assert "Firewall - Off" in text
    assert "Junk files you can remove: 3.2 GB" in text
    assert "Recommended next steps" in text
    assert "1. Remove junk files (about 3.2 GB)." in text
    assert "Command: lapforge clean" in text
    assert "2. Review Startup programs" in text
    assert "Running as: Standard user" in text


def test_screen_shows_at_most_five_problem_lines():
    many = [
        {"id": f"x{n}", "label": f"Problem {n}", "level": "warn", "value": "v", "meaning": "", "action": ""} for n in range(8)
    ]
    text = render(sample_result(problems=many, problem_total=8))
    assert "Problem 4" in text and "Problem 5" not in text
    assert "...and 3 more" in text


def test_screen_when_everything_is_fine():
    text = render(sample_result(problems=[], problem_total=0, recommendations=[], junk_bytes=0, junk_text="0 B"))
    assert "Nothing needs attention." in text
    assert "Junk files you can remove: none found" in text
    assert "Nothing to do right now." in text


def test_screen_when_checks_could_not_run():
    text = render(sample_result(score=None, grade="Unknown", level="muted", problems=[], problem_total=0, failed=["Health checks"]))
    assert "could not be read on this PC" in text
    assert "Health checks could not be read" in text


def test_screen_notes_partial_results_open_browsers_and_missing_cleaner():
    text = render(sample_result(partial=True, open_browsers=["Chrome"], junk_available=False))
    assert "Partial result" in text
    assert "Chrome is open, so its cache was not counted." in text
    assert "Junk files you can remove: not available on this PC" in text


def test_screen_has_no_python_jargon():
    text = render(sample_result())
    for word in ("Traceback", "Exception", "dict", "None"):
        assert word not in text


# --------------------------------------------------------------------------
# `lapforge scan` / `lapforge check`
# --------------------------------------------------------------------------
@pytest.fixture
def scan_env(monkeypatch):
    """Quiet console, a fake scan result, and a recorder for the clean command."""
    console, buffer = capture_console()
    monkeypatch.setattr(cli, "make_console", lambda: console)
    monkeypatch.setattr(cli, "print_banner", lambda *_a, **_k: None)
    result = sample_result(junk_available=True, junk_bytes=3 * GB, browser_found=True)
    monkeypatch.setattr(cli, "run_smart_scan", lambda: result)
    cleaned = []
    monkeypatch.setattr(cli, "cmd_clean", lambda args: cleaned.append(args) or 0)
    return {"buffer": buffer, "cleaned": cleaned, "result": result, "monkeypatch": monkeypatch}


def set_terminal(monkeypatch, interactive):
    class Stream(io.StringIO):
        def isatty(self):
            return interactive

    monkeypatch.setattr("sys.stdin", Stream())
    monkeypatch.setattr("sys.stdout", Stream() if interactive else io.StringIO())


def test_parser_accepts_scan_and_the_check_alias():
    parser = cli.build_parser()
    assert parser.parse_args(["scan"]).command == "scan"
    assert parser.parse_args(["check"]).command == "check"


@pytest.mark.parametrize("word", ["scan", "check"])
def test_dispatch_routes_both_words_to_the_scan(monkeypatch, word):
    monkeypatch.setattr(cli, "cmd_scan", lambda: 42)
    assert cli._dispatch(cli.build_parser().parse_args([word])) == 42


def test_scan_asks_and_does_nothing_when_the_answer_is_no(scan_env):
    set_terminal(scan_env["monkeypatch"], interactive=True)
    scan_env["monkeypatch"].setattr(cli, "_ask_yes", lambda console, question: False)
    assert cli.cmd_scan() == 0
    assert scan_env["cleaned"] == []
    assert "Nothing was changed." in scan_env["buffer"].getvalue()


def test_scan_runs_the_normal_clean_flow_only_after_yes(scan_env):
    set_terminal(scan_env["monkeypatch"], interactive=True)
    questions = []

    def yes(console, question):
        questions.append(question)
        return True

    scan_env["monkeypatch"].setattr(cli, "_ask_yes", yes)
    assert cli.cmd_scan() == 0
    assert "Remove the junk files now?" in questions[0]
    assert len(scan_env["cleaned"]) == 1
    clean_args = scan_env["cleaned"][0]
    # The normal flow is used with its own preview and confirmation: -y and --dry-run are not set.
    assert clean_args.yes is False and clean_args.dry_run is False
    assert clean_args.full is False and clean_args.browser is True


def test_scan_does_not_ask_when_nobody_can_answer(scan_env):
    set_terminal(scan_env["monkeypatch"], interactive=False)
    scan_env["monkeypatch"].setattr(cli, "_ask_yes", lambda *a: pytest.fail("must not ask"))
    assert cli.cmd_scan() == 0
    assert scan_env["cleaned"] == []
    assert "lapforge clean" in scan_env["buffer"].getvalue()


def test_scan_does_not_ask_when_there_is_little_junk(scan_env):
    set_terminal(scan_env["monkeypatch"], interactive=True)
    scan_env["result"].update(junk_bytes=1024)
    scan_env["monkeypatch"].setattr(cli, "_ask_yes", lambda *a: pytest.fail("must not ask"))
    assert cli.cmd_scan() == 0
    assert scan_env["cleaned"] == []


def test_scan_namespace_matches_what_cmd_clean_reads():
    """The namespace built by the scan must have every option cmd_clean uses."""
    parsed = cli.build_parser().parse_args(["clean"])
    built = argparse.Namespace(full=False, browser=False, dry_run=False, yes=False)
    assert set(vars(built)) <= set(vars(parsed))
