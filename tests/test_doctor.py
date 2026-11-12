"""Tests for the doctor engine: scoring, fix hints and the pure judge_* functions.

Nothing here reads the real machine, so the tests pass on any OS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Let a plain `pytest -q` find the package even before `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lapforge.doctor import checks as doctor_checks  # noqa: E402
from lapforge.doctor.checks import (  # noqa: E402
    GIB,
    WEIGHTS,
    Thresholds,
    judge_battery,
    judge_disk_health,
    judge_memory,
    judge_security,
    judge_startup,
    judge_storage,
    judge_temperature,
    make_check,
)
from lapforge.doctor.fix_hints import HINTS, get_hint  # noqa: E402
from lapforge.doctor.score import (  # noqa: E402
    CAP_ANY_BAD,
    CAP_CRITICAL,
    compute_score,
    grade_for,
    top_problems,
)

T = Thresholds()


def chk(level: str, weight: float, label: str = "x") -> dict:
    return {"id": f"t.{label}", "label": label, "level": level, "weight": weight}


# --------------------------------------------------------------------------
# Score
# --------------------------------------------------------------------------
def test_score_no_checks_is_perfect():
    result = compute_score([])
    assert result["score"] == 100
    assert result["grade"] == "Excellent"
    assert result["level"] == "good"
    assert result["capped"] is False


def test_score_all_good_is_perfect():
    result = compute_score([chk("good", 10), chk("good", 20)])
    assert result["score"] == 100
    assert result["counts"] == {"good": 2, "warn": 0, "bad": 0, "unknown": 0}


def test_score_warn_costs_half_weight():
    result = compute_score([chk("warn", 8)])
    assert result["penalty"] == 4.0
    assert result["score"] == 96


def test_score_bad_costs_full_weight_and_caps_below_good():
    result = compute_score([chk("bad", 8)])
    assert result["penalty"] == 8.0
    # 100 - 8 = 92 would be "Excellent", but any red check caps the score.
    assert result["score"] == CAP_ANY_BAD
    assert result["capped"] is True
    assert result["grade"] == "Good"


def test_score_critical_bad_caps_at_fair():
    result = compute_score([chk("bad", 20)])
    assert result["score"] == CAP_CRITICAL
    assert result["grade"] == "Fair"
    assert result["level"] == "warn"
    assert result["capped"] is True


def test_score_muted_costs_nothing():
    result = compute_score([chk("muted", 15), chk("muted", 20)])
    assert result["score"] == 100
    assert result["counts"]["unknown"] == 2
    assert result["capped"] is False


def test_score_missing_level_is_treated_as_unknown():
    result = compute_score([{"weight": 10}])
    assert result["score"] == 100
    assert result["counts"]["unknown"] == 1


def test_score_never_below_zero():
    result = compute_score([chk("bad", 80), chk("bad", 80)])
    assert result["score"] == 0
    assert result["grade"] == "Poor"
    assert result["level"] == "bad"


def test_score_mixed_counts():
    result = compute_score([chk("good", 5), chk("warn", 10), chk("bad", 10), chk("muted", 3)])
    assert result["counts"] == {"good": 1, "warn": 1, "bad": 1, "unknown": 1}
    assert result["penalty"] == 15.0
    assert result["score"] == 79  # 85 raw, capped by the red check


@pytest.mark.parametrize(
    "score, grade, level",
    [
        (100, "Excellent", "good"),
        (90, "Excellent", "good"),
        (89, "Good", "good"),
        (75, "Good", "good"),
        (74, "Fair", "warn"),
        (50, "Fair", "warn"),
        (49, "Poor", "bad"),
        (0, "Poor", "bad"),
        (-5, "Poor", "bad"),
    ],
)
def test_grade_boundaries(score, grade, level):
    assert grade_for(score) == (grade, level)


def test_top_problems_orders_worst_first():
    items = [
        chk("good", 50, "fine"),
        chk("warn", 20, "warn-heavy"),
        chk("bad", 5, "bad-light"),
        chk("bad", 15, "bad-heavy"),
        chk("muted", 30, "unknown"),
        chk("warn", 2, "warn-light"),
    ]
    labels = [c["label"] for c in top_problems(items)]
    assert labels == ["bad-heavy", "bad-light", "warn-heavy", "warn-light"]


def test_top_problems_limit_and_empty():
    items = [chk("bad", 1, "a"), chk("bad", 2, "b"), chk("warn", 3, "c")]
    assert len(top_problems(items, limit=2)) == 2
    assert top_problems([chk("good", 5)]) == []
    assert top_problems([]) == []


# --------------------------------------------------------------------------
# Fix hints
# --------------------------------------------------------------------------
def test_hint_empty_for_good_and_muted():
    assert get_hint("storage.space", "good") == ""
    assert get_hint("storage.space", "muted") == ""


def test_hint_empty_for_unknown_check():
    assert get_hint("no.such.check", "bad") == ""


def test_hint_fills_placeholders():
    text = get_hint("storage.space", "bad", mount="C:\\")
    assert "C:\\" in text
    assert "{mount}" not in text


def test_hint_missing_placeholder_is_left_alone():
    assert "{mount}" in get_hint("storage.space", "warn")


def test_hint_falls_back_to_warn_text(monkeypatch):
    monkeypatch.setitem(HINTS, "demo.check", {"warn": "only warn"})
    assert get_hint("demo.check", "bad") == "only warn"


def test_every_hint_has_text_for_warn_and_bad():
    for check_id, levels in HINTS.items():
        for level in ("warn", "bad"):
            assert levels.get(level, "").strip(), f"{check_id} has no {level} hint"


# --------------------------------------------------------------------------
# make_check
# --------------------------------------------------------------------------
def test_make_check_has_hint_only_for_problems():
    good = make_check("memory.usage", "Memory", "RAM in use", "good", "40% in use", 6)
    bad = make_check("memory.usage", "Memory", "RAM in use", "bad", "97% in use", 6)
    assert good["hint"] == ""
    assert bad["hint"] != ""
    assert set(bad) == {"id", "category", "label", "level", "value", "hint", "weight"}


# --------------------------------------------------------------------------
# judge_storage
# --------------------------------------------------------------------------
@pytest.fixture
def linux_like(monkeypatch):
    """Treat '/' as the system volume so the tests behave the same everywhere."""
    monkeypatch.setattr(doctor_checks, "IS_WINDOWS", False)


def volume(mount="/", total_gb=500, used_percent=50.0):
    total = int(total_gb * GIB)
    free = int(total * (100 - used_percent) / 100)
    return {"mount": mount, "total": total, "free": free, "percent": used_percent}


def test_storage_healthy(linux_like):
    (check,) = judge_storage([volume(used_percent=40)], T)
    assert check["level"] == "good"
    assert check["weight"] == WEIGHTS["storage.system"]
    assert check["hint"] == ""


def test_storage_warn_by_percent(linux_like):
    (check,) = judge_storage([volume(used_percent=88)], T)
    assert check["level"] == "warn"
    assert "/" in check["hint"]


def test_storage_bad_by_percent(linux_like):
    (check,) = judge_storage([volume(used_percent=96)], T)
    assert check["level"] == "bad"


def test_storage_low_free_gb_overrides_percent(linux_like):
    # 128 GB drive only 60% used, but just 4 GB... build it by hand.
    vol = {"mount": "/", "total": 128 * GIB, "free": 4 * GIB, "percent": 60.0}
    (check,) = judge_storage([vol], T)
    assert check["level"] == "bad"
    vol["free"] = 8 * GIB
    (check,) = judge_storage([vol], T)
    assert check["level"] == "warn"


def test_storage_non_system_volume_weighs_less(linux_like):
    (check,) = judge_storage([volume(mount="/data", used_percent=96)], T)
    assert check["weight"] == WEIGHTS["storage.other"]


def test_storage_ignores_tiny_volumes(linux_like):
    tiny = {"mount": "/boot/efi", "total": 100 * 1024**2, "free": 1, "percent": 99.0}
    (check,) = judge_storage([tiny], T)
    assert check["level"] == "muted"  # nothing real to judge


def test_storage_no_volumes_is_muted():
    (check,) = judge_storage([], T)
    assert check["level"] == "muted"
    assert check["weight"] == 0


def test_storage_windows_system_drive(monkeypatch):
    monkeypatch.setattr(doctor_checks, "IS_WINDOWS", True)
    monkeypatch.setenv("SystemDrive", "C:")
    c_check, d_check = judge_storage([volume("C:\\", used_percent=50), volume("D:\\", used_percent=50)], T)
    assert c_check["weight"] == WEIGHTS["storage.system"]
    assert d_check["weight"] == WEIGHTS["storage.other"]


# --------------------------------------------------------------------------
# judge_memory
# --------------------------------------------------------------------------
@pytest.mark.parametrize("percent, level", [(40, "good"), (85, "warn"), (94.9, "warn"), (95, "bad")])
def test_memory_usage_levels(percent, level):
    checks = judge_memory({"percent": percent, "total": 16 * GIB}, T)
    usage = next(c for c in checks if c["id"] == "memory.usage")
    assert usage["level"] == level


@pytest.mark.parametrize("gb, level", [(16, "good"), (7.5, "good"), (6, "warn"), (4, "warn"), (2, "bad")])
def test_memory_capacity_levels(gb, level):
    checks = judge_memory({"percent": 10, "total": int(gb * GIB)}, T)
    cap = next(c for c in checks if c["id"] == "memory.capacity")
    assert cap["level"] == level


def test_memory_missing_data():
    checks = judge_memory({}, T)
    assert [c["id"] for c in checks] == ["memory.usage"]
    assert checks[0]["level"] == "muted"


# --------------------------------------------------------------------------
# judge_battery
# --------------------------------------------------------------------------
def test_battery_absent_gives_no_check():
    assert judge_battery({"present": False}, T) == []
    assert judge_battery({}, T) == []


def test_battery_wear_unknown_is_muted():
    (check,) = judge_battery({"present": True, "wear_percent": None}, T)
    assert check["level"] == "muted"


@pytest.mark.parametrize("wear, level", [(5, "good"), (20, "warn"), (39, "warn"), (40, "bad"), (70, "bad")])
def test_battery_wear_levels(wear, level):
    (check,) = judge_battery({"present": True, "wear_percent": wear}, T)
    assert check["level"] == level


def test_battery_value_text_includes_health_and_cycles():
    data = {"present": True, "wear_percent": 12, "health_percent": 88, "cycles": 240}
    (check,) = judge_battery(data, T)
    assert "12% worn" in check["value"]
    assert "health 88%" in check["value"]
    assert "240 cycles" in check["value"]


# --------------------------------------------------------------------------
# judge_startup
# --------------------------------------------------------------------------
def test_startup_unavailable():
    (check,) = judge_startup({"available": False, "note": "Windows only"}, T)
    assert check["level"] == "muted"
    assert check["value"] == "Windows only"


@pytest.mark.parametrize("count, level", [(3, "good"), (12, "warn"), (19, "warn"), (20, "bad")])
def test_startup_count_levels(count, level):
    (check,) = judge_startup({"available": True, "enabled_count": count, "heavy_count": 0}, T)
    assert check["level"] == level


def test_startup_many_heavy_apps_raises_to_warn():
    (check,) = judge_startup({"available": True, "enabled_count": 4, "heavy_count": 3}, T)
    assert check["level"] == "warn"
    assert "3 using noticeable memory" in check["value"]


def test_startup_heavy_does_not_lower_bad():
    (check,) = judge_startup({"available": True, "enabled_count": 25, "heavy_count": 5}, T)
    assert check["level"] == "bad"


# --------------------------------------------------------------------------
# judge_security
# --------------------------------------------------------------------------
def test_security_unavailable():
    (check,) = judge_security({"available": False, "note": "Windows only"})
    assert check["level"] == "muted"
    assert check["weight"] == 0


def test_security_wraps_each_item_with_weight_and_hint():
    report = {
        "available": True,
        "checks": [
            {"key": "antivirus", "label": "Antivirus", "level": "bad", "value": "Off", "hint": "x"},
            {"key": "firewall", "label": "Firewall", "level": "good", "value": "On", "hint": ""},
            {"key": "mystery", "label": "Mystery", "level": "warn", "value": "?", "hint": "own hint"},
        ],
    }
    av, fw, other = judge_security(report)
    assert av["id"] == "security.antivirus"
    assert av["weight"] == WEIGHTS["security.antivirus"]
    assert av["hint"] == get_hint("security.antivirus", "bad")
    assert fw["hint"] == ""
    # A check with no built-in hint falls back to the hint the collector supplied.
    assert other["weight"] == 0
    assert other["hint"] == "own hint"


# --------------------------------------------------------------------------
# judge_disk_health
# --------------------------------------------------------------------------
def disk(level, status="Healthy", reasons=None, model="SSD-1"):
    return {"model": model, "level": level, "status": status, "reasons": reasons or []}


def test_disk_health_unavailable():
    (check,) = judge_disk_health({"available": False, "note": "no data"})
    assert check["level"] == "muted"


def test_disk_health_takes_worst_drive():
    report = {"available": True, "disks": [disk("good"), disk("bad", reasons=["Failing"], model="HDD-2")]}
    (check,) = judge_disk_health(report)
    assert check["level"] == "bad"
    assert check["weight"] == WEIGHTS["disk.health"]
    assert "HDD-2: Failing" in check["value"]


def test_disk_health_all_muted_is_muted():
    (check,) = judge_disk_health({"available": True, "disks": [disk("muted", status="Unknown")]})
    assert check["level"] == "muted"
    assert check["weight"] == 0


def test_disk_health_muted_drive_does_not_hide_good_one():
    (check,) = judge_disk_health({"available": True, "disks": [disk("muted"), disk("good")]})
    assert check["level"] == "good"


# --------------------------------------------------------------------------
# judge_temperature
# --------------------------------------------------------------------------
def test_temperature_no_sensors_is_muted():
    (check,) = judge_temperature({"sensors": []}, T)
    assert check["level"] == "muted"
    assert "Not available" in check["value"]


@pytest.mark.parametrize("temp, level", [(55, "good"), (80, "warn"), (89, "warn"), (90, "bad")])
def test_temperature_levels(temp, level):
    report = {"sensors": [{"current_c": 40}, {"current_c": temp}]}
    (check,) = judge_temperature(report, T)
    assert check["level"] == level
    assert f"{temp:.0f} C" in check["value"]


# --------------------------------------------------------------------------
# End to end: checks feed the score
# --------------------------------------------------------------------------
def test_run_checks_never_raises_when_every_collector_fails(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("collector exploded")

    for name in (
        "get_storage_info",
        "get_memory_info",
        "get_battery_health",
        "get_startup_apps",
        "get_security_report",
        "get_disk_health",
        "get_temperatures",
    ):
        monkeypatch.setattr(doctor_checks, name, boom)
    checks = doctor_checks.run_checks()
    assert checks, "fallback checks should still be returned"
    assert all(c["level"] == "muted" for c in checks)
    assert compute_score(checks)["score"] == 100


def test_realistic_laptop_scores_in_expected_band():
    checks = (
        judge_memory({"percent": 60, "total": 16 * GIB}, T)
        + judge_battery({"present": True, "wear_percent": 25}, T)  # warn, 8*0.5 = 4
        + judge_startup({"available": True, "enabled_count": 22, "heavy_count": 0}, T)  # bad, 8
    )
    result = compute_score(checks)
    assert result["penalty"] == 12.0
    assert result["score"] == CAP_ANY_BAD  # red check keeps it below the Excellent range
    assert [c["id"] for c in top_problems(checks)] == ["startup.count", "battery.wear"]


# --------------------------------------------------------------------------
# Phase 24: coverage and partial results
# --------------------------------------------------------------------------
from rich.console import Console  # noqa: E402

from lapforge.collectors import security  # noqa: E402
from lapforge.doctor.score import PARTIAL_BELOW, compute_coverage  # noqa: E402
from lapforge.ui import health_view  # noqa: E402
from lapforge.ui.theme import LAPFORGE_THEME  # noqa: E402


def test_coverage_all_readable_is_complete():
    result = compute_score([chk("good", 10), chk("warn", 5), chk("bad", 5)])
    assert result["coverage"] == 1.0
    assert result["partial"] is False


def test_coverage_no_checks_is_not_partial():
    result = compute_score([])
    assert result["coverage"] == 1.0
    assert result["partial"] is False


def test_coverage_all_unreadable_is_partial():
    result = compute_score([chk("muted", 15, "a"), chk("muted", 20, "b")])
    assert result["coverage"] == 0.0
    assert result["partial"] is True
    assert result["score"] == 100  # not punished


def test_coverage_is_by_weight_not_by_count():
    # One heavy readable check outweighs two light unreadable ones.
    result = compute_score([chk("good", 60, "big"), chk("muted", 5, "a"), chk("muted", 5, "b")])
    assert result["coverage"] == pytest.approx(60 / 70, abs=0.001)
    assert result["partial"] is False


def test_partial_starts_below_seventy_percent():
    assert PARTIAL_BELOW == 0.70
    exactly = compute_score([chk("good", 70, "a"), chk("muted", 30, "b")])
    assert exactly["coverage"] == 0.7 and exactly["partial"] is False
    below = compute_score([chk("good", 60, "a"), chk("muted", 40, "b")])
    assert below["coverage"] == 0.6 and below["partial"] is True


def test_unreadable_checks_with_weight_zero_use_their_normal_weight():
    checks = [
        {"id": "storage.space", "label": "s", "level": "good", "weight": WEIGHTS["storage.system"]},
        {"id": "disk.health", "label": "d", "level": "muted", "weight": 0},
    ]
    expected = WEIGHTS["storage.system"] / (WEIGHTS["storage.system"] + WEIGHTS["disk.health"])
    assert compute_coverage(checks) == pytest.approx(expected, abs=0.001)
    assert compute_score(checks)["partial"] is True


def test_coverage_counts_checks_when_no_weight_is_known():
    checks = [{"id": "x.a", "level": "good", "weight": 0}, {"id": "x.b", "level": "muted", "weight": 0}]
    assert compute_coverage(checks) == 0.5


def test_coverage_does_not_change_score_numbers():
    result = compute_score([chk("good", 5), chk("warn", 10), chk("bad", 10), chk("muted", 3)])
    assert result["score"] == 79 and result["penalty"] == 15.0 and result["capped"] is True
    assert result["counts"] == {"good": 1, "warn": 1, "bad": 1, "unknown": 1}


def test_realistic_run_with_unreadable_areas_is_partial():
    checks = [
        *judge_storage([{"mount": "/", "total": 500 * GIB, "free": 300 * GIB, "percent": 40.0}], T),
        *judge_memory({"percent": 40, "total": 16 * GIB}, T),
        *judge_security({"available": False, "note": "Windows only"}),
        *judge_disk_health({"available": False}),
        *judge_temperature({"sensors": []}, T),
        *judge_startup({"available": False}, T),
    ]
    result = compute_score(checks)
    assert result["partial"] is True
    assert result["score"] == 100


# --------------------------------------------------------------------------
# Phase 24: the health screen
# --------------------------------------------------------------------------
def _info(unknown: int, partial: bool) -> dict:
    return {
        "score": 90,
        "grade": "Excellent",
        "level": "good",
        "capped": False,
        "counts": {"good": 3, "warn": 0, "bad": 0, "unknown": unknown},
        "coverage": 0.4 if partial else 1.0,
        "partial": partial,
    }


def _panel_text(info: dict, admin: bool) -> str:
    console = Console(theme=LAPFORGE_THEME, record=True, width=100)
    console.print(health_view.score_panel(info, admin=admin))
    return console.export_text()


def test_partial_result_shows_notice_and_administrator_tip():
    text = _panel_text(_info(4, True), admin=False)
    assert "Partial result - 4 checks could not be read" in text
    assert "Run as Administrator for a complete check." in text


def test_partial_notice_uses_singular_for_one_check():
    assert "Partial result - 1 check could not be read" in _panel_text(_info(1, True), admin=False)


def test_administrator_tip_is_hidden_when_already_administrator():
    text = _panel_text(_info(4, True), admin=True)
    assert "Partial result" in text
    assert "Run as Administrator" not in text


def test_complete_result_shows_no_partial_notice():
    text = _panel_text(_info(1, False), admin=False)
    assert "Partial result" not in text
    assert "Run as Administrator" not in text


def test_render_health_prints_partial_notice(monkeypatch):
    monkeypatch.setattr(health_view, "is_admin", lambda: False)
    checks = [chk("muted", 15, "a"), chk("muted", 20, "b")]
    result = {"checks": [{**c, "category": "Security", "value": "?", "hint": ""} for c in checks],
              "score": compute_score(checks), "problems": []}
    console = Console(theme=LAPFORGE_THEME, record=True, width=110)
    health_view.render_health(console, result)
    text = console.export_text()
    assert "Partial result - 2 checks could not be read" in text
    assert "Run as Administrator for a complete check." in text


# --------------------------------------------------------------------------
# Phase 24: antivirus product state
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "state, status, enabled, up_to_date",
    [
        (0x061100, "on", True, True),  # 397568: on, definitions current
        (0x061110, "on", True, False),  # 397584: on, definitions out of date
        (0x060100, "off", False, True),  # 393472: switched off
        (0x062100, "snoozed", False, True),
        (0x063100, "expired", False, True),
        (0x001000, "on", True, True),
        ("397568", "on", True, True),  # numbers sent as text still work
    ],
)
def test_decode_product_state_known_values(state, status, enabled, up_to_date):
    result = security.decode_product_state(state)
    assert result == {"status": status, "enabled": enabled, "up_to_date": up_to_date}


@pytest.mark.parametrize("state", [None, "abc", "", -1, True, 0x1000000, 0x064100, 3.5e20, [1], {}])
def test_decode_product_state_unknown_values(state):
    assert security.decode_product_state(state) == {"status": "unknown", "enabled": None, "up_to_date": None}


def test_normalize_products_accepts_dicts_names_and_decoded_items():
    raw = [
        {"name": "Acme AV", "state": 0x061100},
        "Old Style AV",
        {"name": "  ", "state": 1},
        {"name": "Done AV", "status": "off", "enabled": False, "up_to_date": None},
        None,
    ]
    products = security.normalize_products(raw)
    assert [p["name"] for p in products] == ["Acme AV", "Old Style AV", "Done AV"]
    assert [p["status"] for p in products] == ["on", "unknown", "off"]
    assert security.normalize_products({"name": "Single", "state": 0x060100})[0]["status"] == "off"
    assert security.normalize_products(None) == []


DEFENDER_OFF = {"realtime": False, "signature_age": 1}
DEFENDER_ON = {"realtime": True, "signature_age": 1}


def test_defender_on_is_unchanged():
    check = security.assess_antivirus(DEFENDER_ON, [])
    assert check["level"] == "good"
    assert "Defender real-time protection is on" in check["value"]


def test_defender_on_with_old_definitions_still_warns():
    assert security.assess_antivirus({"realtime": True, "signature_age": 10}, [])["level"] == "warn"
    assert security.assess_antivirus({"realtime": True, "signature_age": 20}, [])["level"] == "bad"


def test_third_party_antivirus_that_is_on_counts_as_protection():
    check = security.assess_antivirus(DEFENDER_OFF, [{"name": "Acme AV", "state": 0x061100}])
    assert check["level"] == "good"
    assert "Acme AV is switched on" in check["value"]


@pytest.mark.parametrize(
    "state, phrase",
    [(0x060100, "switched off"), (0x062100, "paused (snoozed)"), (0x063100, "expired")],
)
def test_third_party_antivirus_that_is_not_on_gives_a_warning(state, phrase):
    check = security.assess_antivirus(DEFENDER_OFF, [{"name": "Acme AV", "state": state}])
    assert check["level"] == "warn"
    assert "Acme AV" in check["value"] and phrase in check["value"]
    assert "No antivirus is protecting this PC" in check["value"]
    assert "Acme AV" in check["hint"] and "Windows Defender" in check["hint"]


def test_one_active_product_is_enough_even_if_another_is_off():
    products = [{"name": "Off AV", "state": 0x060100}, {"name": "On AV", "state": 0x061100}]
    check = security.assess_antivirus(DEFENDER_OFF, products)
    assert check["level"] == "good"
    assert "On AV" in check["value"] and "Off AV" not in check["value"]


def test_unknown_product_state_is_never_counted_as_protection():
    for products in (["Mystery AV"], [{"name": "Mystery AV", "state": 12345678}]):
        check = security.assess_antivirus(DEFENDER_OFF, products)
        assert check["level"] == "muted"
        assert "Mystery AV" in check["value"]


def test_defender_off_and_no_other_antivirus_is_a_problem():
    assert security.assess_antivirus(DEFENDER_OFF, [])["level"] == "bad"
    assert security.assess_antivirus(DEFENDER_OFF, [{"name": "Microsoft Defender Antivirus", "state": 0x060100}])["level"] == "bad"


def test_defender_status_unreadable_uses_product_state_too():
    on = security.assess_antivirus(None, [{"name": "Acme AV", "state": 0x061100}])
    off = security.assess_antivirus(None, [{"name": "Acme AV", "state": 0x060100}])
    nothing = security.assess_antivirus(None, [])
    assert on["level"] == "good" and "switched on" in on["value"]
    assert off["level"] == "warn" and "switched off" in off["value"]
    assert nothing["level"] == "muted"


def test_security_report_uses_product_state(monkeypatch):
    def fake_json(script, timeout):
        if script is security._DEFENDER_SCRIPT:
            return {"realtime": False, "signature_age": 1}
        if script is security._AV_PRODUCTS_SCRIPT:
            return {"name": "Acme AV", "state": 0x060100}  # a single product arrives as one object
        return None

    monkeypatch.setattr(security, "IS_WINDOWS", True)
    monkeypatch.setattr(security, "_safe_json", fake_json)
    monkeypatch.setattr(security, "get_bios_info", lambda: {"secure_boot": "Enabled"})
    monkeypatch.setattr(security, "_read_uac", lambda: 1)
    monkeypatch.setattr(security, "is_admin", lambda: True)
    report = security.get_security_report()
    antivirus = next(c for c in report["checks"] if c["key"] == "antivirus")
    assert antivirus["level"] == "warn"
    assert "Acme AV" in antivirus["value"]
