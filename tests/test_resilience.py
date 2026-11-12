"""Tests for collector resilience and accurate hardware info (Phase 21)."""

from __future__ import annotations

import pytest
from rich.console import Console

from lapforge import cli
from lapforge.collectors import bios, hardware
from lapforge.ui import dashboard
from lapforge.ui.theme import LAPFORGE_THEME
from lapforge.utils import NA

MALFORMED_SNAPSHOT = {
    "system": "oops",
    "bios": [1, 2],
    "os": 5,
    "cpu": [1, "x", None],
    "gpu": "bad",
    "ram": [{"SMBIOSMemoryType": [1], "Capacity": {"a": 1}, "Speed": "fast"}, 7],
    "disk": 42,
}


def _recorded_console(width: int = 120) -> Console:
    return Console(theme=LAPFORGE_THEME, record=True, width=width)


# --------------------------------------------------------------------------
# Type-safe helpers and malformed PowerShell output
# --------------------------------------------------------------------------
def test_as_dict_accepts_only_dicts():
    assert hardware._as_dict({"a": 1}) == {"a": 1}
    for bad in (None, "text", 5, [1], [{"a": 1}]):
        assert hardware._as_dict(bad) == {}


def test_as_dicts_keeps_only_real_dicts():
    assert hardware._as_dicts({"a": 1}) == [{"a": 1}]
    assert hardware._as_dicts([{"a": 1}, "x", 3, None, {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert hardware._as_dicts("text") == []
    assert hardware._as_dicts(None) == []


def test_malformed_windows_snapshot_never_raises(monkeypatch):
    monkeypatch.setattr(hardware, "IS_WINDOWS", True)
    monkeypatch.setattr(hardware, "_windows_snapshot", lambda: MALFORMED_SNAPSHOT)
    monkeypatch.setattr(hardware.platform, "processor", lambda: "")
    assert hardware.get_system_info()["brand"] == NA
    assert hardware.get_os_info()["build"] == NA
    assert hardware.get_cpu_info()["name"] == NA
    memory = hardware.get_memory_info()
    assert memory["type"] == NA and memory["speed"] == NA
    assert hardware.get_storage_info()["drives"] == []
    assert hardware.get_gpu_info() == []


@pytest.mark.parametrize("code,expected", [(26, "DDR4"), (34, "DDR5"), ("24", "DDR3"), ([1], NA), (None, NA), (999, NA)])
def test_memory_type_name(code, expected):
    assert hardware._memory_type_name(code) == expected


@pytest.mark.parametrize("raw,expected", [(2400, "2400 MHz"), ("3200", "3200 MHz"), (0, NA), (None, NA), ("fast", NA)])
def test_speed_text(raw, expected):
    assert hardware._speed_text(raw) == expected


# --------------------------------------------------------------------------
# CPU load
# --------------------------------------------------------------------------
def test_median_load():
    assert hardware.median_load([90, 10, 12]) == 12
    assert hardware.median_load([10, 20]) == 15
    assert hardware.median_load([]) is None
    assert hardware.median_load([None, "x", True]) is None


def test_cpu_load_uses_median_of_three_samples(monkeypatch):
    values = iter([95.0, 8.0, 10.0])
    monkeypatch.setattr(hardware.psutil, "cpu_percent", lambda interval=None: next(values))
    assert hardware._sample_cpu_load() == 10.0


def test_cpu_load_is_none_when_sampling_fails(monkeypatch):
    def fail(interval=None):
        raise RuntimeError("no counters")

    monkeypatch.setattr(hardware.psutil, "cpu_percent", fail)
    assert hardware._sample_cpu_load() is None


def test_cpu_load_is_sampled_after_powershell_data(monkeypatch):
    events: list[str] = []

    def snapshot():
        events.append("snapshot")
        return {"cpu": [{"Name": "Test CPU"}]}

    def sample(interval=None):
        events.append("sample")
        return 5.0

    monkeypatch.setattr(hardware, "IS_WINDOWS", True)
    monkeypatch.setattr(hardware, "_windows_snapshot", snapshot)
    monkeypatch.setattr(hardware.psutil, "cpu_percent", sample)
    info = hardware.get_cpu_info()
    assert info["usage_percent"] == 5.0
    assert events[0] == "snapshot"
    assert events[1:] == ["sample"] * 3


# --------------------------------------------------------------------------
# Safe collection
# --------------------------------------------------------------------------
def test_safe_collect_returns_fallback_on_error_or_wrong_type():
    def boom():
        raise RuntimeError("x")

    assert hardware.safe_collect("cpu", boom)["name"] == NA
    assert hardware.safe_collect("gpu", lambda: "not a list") == []
    assert hardware.safe_collect("storage", lambda: {"drives": [1], "volumes": []})["drives"] == [1]


def test_collect_hardware_one_failure_does_not_break_others(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(hardware, "get_cpu_info", boom)
    data = hardware.collect_hardware()
    assert data["cpu"]["name"] == NA
    assert data["memory"]["total_text"] != NA
    assert set(data) == set(hardware.SECTION_FALLBACKS)


# --------------------------------------------------------------------------
# Boot mode and Secure Boot
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "registry,api,expected",
    [
        (2, None, "UEFI"),
        (1, 2, "Legacy BIOS"),
        (None, 2, "UEFI"),
        (None, 1, "Legacy BIOS"),
        (None, None, NA),
        (0, 0, NA),
        (True, None, NA),
    ],
)
def test_firmware_mode_from_sources(registry, api, expected):
    assert bios.firmware_mode_from_sources(registry, api) == expected


@pytest.mark.parametrize(
    "flag,mode,expected",
    [
        (1, "UEFI", "Enabled"),
        (0, "UEFI", "Disabled"),
        (None, "UEFI", NA),
        (None, "Legacy BIOS", "Not supported (Legacy BIOS)"),
        (1, "Legacy BIOS", "Not supported (Legacy BIOS)"),
        (None, NA, NA),
    ],
)
def test_secure_boot_text(flag, mode, expected):
    assert bios._secure_boot_text(flag, mode) == expected


def test_boot_mode_prefers_registry_and_skips_api(monkeypatch):
    monkeypatch.setattr(bios, "_read_registry_dword", lambda key, name: 2)

    def api_must_not_run():
        raise AssertionError("API should not be used when the registry value exists")

    monkeypatch.setattr(bios, "_firmware_type_from_api", api_must_not_run)
    assert bios.detect_boot_mode() == "UEFI"


def test_boot_mode_falls_back_to_windows_api(monkeypatch):
    monkeypatch.setattr(bios, "_read_registry_dword", lambda key, name: None)
    monkeypatch.setattr(bios, "_firmware_type_from_api", lambda: 1)
    assert bios.detect_boot_mode() == "Legacy BIOS"


def test_boot_mode_unknown_when_nothing_is_available(monkeypatch):
    monkeypatch.setattr(bios, "_read_registry_dword", lambda key, name: None)
    monkeypatch.setattr(bios, "_firmware_type_from_api", lambda: None)
    assert bios.detect_boot_mode() == NA


def test_secure_boot_reads_registry_only_on_uefi(monkeypatch):
    calls: list[str] = []

    def reader(key, name):
        calls.append(name)
        return 1

    monkeypatch.setattr(bios, "_read_registry_dword", reader)
    assert bios.detect_secure_boot("UEFI") == "Enabled"
    assert bios.detect_secure_boot("Legacy BIOS") == "Not supported (Legacy BIOS)"
    assert calls == ["UEFISecureBootEnabled"]


def test_windows_registry_and_api_helpers_are_safe_off_windows(monkeypatch):
    monkeypatch.setattr(bios, "IS_WINDOWS", False)
    assert bios._read_registry_dword(r"SOFTWARE\Test", "Value") is None
    assert bios._firmware_type_from_api() is None


def test_boot_mode_works_even_if_powershell_fails(monkeypatch):
    bios.get_bios_info.cache_clear()
    monkeypatch.setattr(bios, "IS_WINDOWS", True)
    monkeypatch.setattr(bios, "powershell_json", lambda script, timeout=40: None)
    monkeypatch.setattr(bios, "detect_boot_mode", lambda: "Legacy BIOS")
    try:
        info = bios.get_bios_info()
    finally:
        bios.get_bios_info.cache_clear()
    assert info["mode"] == "Legacy BIOS"
    assert info["secure_boot"] == "Not supported (Legacy BIOS)"
    assert info["vendor"] == NA


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
def test_logo_has_no_extra_empty_box():
    assert len(dashboard._LOGO) == 7
    assert not any(line.strip().startswith("╭") for line in dashboard._LOGO[1:])
    assert len({len(line) for line in dashboard._LOGO}) == 1


def test_dashboard_item_failure_shows_na_and_rest_still_appears(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(dashboard, "get_cpu_info", boom)
    monkeypatch.setattr(dashboard, "get_gpu_info", boom)
    data = dashboard.collect_dashboard_data()
    assert data["cpu"]["name"] == NA
    assert data["gpu"] == []
    assert data["memory"]["total_text"] != NA
    console = _recorded_console()
    console.print(dashboard.build_dashboard(data, console.width))
    text = console.export_text()
    assert "CPU" in text and "RAM" in text and "N/A" in text


# --------------------------------------------------------------------------
# info command
# --------------------------------------------------------------------------
def test_info_failing_section_shows_na_only_for_that_section(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(cli, "get_bios_info", boom)
    monkeypatch.setattr(cli, "get_temperatures", boom)
    console = _recorded_console()
    cli.render_info(console, hardware.collect_hardware())
    text = console.export_text()
    assert "BIOS & Motherboard" in text and "Temperatures" in text
    assert "Processor" in text and "Memory" in text and "Battery" in text
    assert "RuntimeError" not in text


def test_info_command_runs_when_a_collector_fails(monkeypatch, capsys):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(hardware, "get_memory_info", boom)
    assert cli.main(["info"]) == 0
    out = capsys.readouterr().out
    assert "Processor" in out and "Something went wrong" not in out
