"""Tests for reports: redaction, default folder, single-read snapshot (Phase 23)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from lapforge import cli, export
from lapforge.collectors import hardware
from lapforge.doctor import checks as doctor_checks
from lapforge.doctor import run_doctor
from lapforge.export import (
    PRIVATE_KEYS,
    REDACTED,
    choose_report_dir,
    collect_snapshot,
    default_output_dir,
    redact_snapshot,
    write_exports,
)
from lapforge.export.json_export import validate_snapshot

GIB = 1024**3
SECRETS = ["SN-123456", "BOARD-9999", "AA:BB:CC:DD:EE:FF", "192.168.1.77", "MY-SECRET-PC"]


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Settings live in a temp folder so a real config file can never change a test."""
    monkeypatch.setenv("LAPFORGE_CONFIG_DIR", str(tmp_path / "config"))


# --------------------------------------------------------------------------
# Fake collector data
# --------------------------------------------------------------------------
def _fake_hardware() -> dict:
    data = {name: make() for name, make in hardware.SECTION_FALLBACKS.items()}
    data["system"] = {"brand": "Acme", "model": "Book", "serial": "SN-123456", "hostname": "MY-SECRET-PC", "system_type": "x64"}
    data["memory"] = {**data["memory"], "total": 16 * GIB, "total_text": "16.0 GB", "percent": 50.0}
    data["storage"] = {
        "drives": [],
        "volumes": [
            {
                "mount": "C:\\",
                "fstype": "NTFS",
                "total": 500 * GIB,
                "used": 200 * GIB,
                "free": 300 * GIB,
                "percent": 40.0,
                "total_text": "500.0 GB",
                "free_text": "300.0 GB",
            }
        ],
    }
    return data


@pytest.fixture
def fake_collectors(monkeypatch):
    """Replace every collector with fake data and count how often each one is read."""
    calls: dict[str, int] = {}

    def fake(name, value):
        def reader(*_a, **_k):
            calls[name] = calls.get(name, 0) + 1
            return value

        return reader

    startup = {
        "available": True,
        "items": [{"name": "Updater", "command": r"C:\Users\bob\secret.exe"}],
        "enabled_count": 1,
        "heavy_count": 0,
        "note": "",
    }
    values = {
        "collect_hardware": _fake_hardware(),
        "get_bios_info": {"vendor": "Acme", "board_serial": "BOARD-9999", "serial": "SN-123456", "mode": "UEFI"},
        "get_battery_health": {"present": False},
        "get_disk_health": {"available": False, "note": "Not available", "disks": []},
        "get_temperatures": {"available": False, "sensors": [], "note": "Not available"},
        "get_network_interfaces": [
            {"name": "Wi-Fi", "up": True, "speed_mbps": 100, "ipv4": "192.168.1.77", "mac": "AA:BB:CC:DD:EE:FF"}
        ],
        "get_security_report": {"available": False, "note": "Not available", "checks": []},
        "get_startup_apps": startup,
    }
    for name, value in values.items():
        monkeypatch.setattr(export, name, fake(name, value))
    # The doctor must not read anything by itself when data is handed to it.
    for name in ("get_storage_info", "get_memory_info", "get_battery_health", "get_startup_apps",
                 "get_security_report", "get_disk_health", "get_temperatures"):
        monkeypatch.setattr(doctor_checks, name, fake("doctor." + name, {}))
    return calls


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------
def _private_snapshot() -> dict:
    return {
        "system": {"serial": "SN-123456", "hostname": "MY-SECRET-PC", "host_name": "MY-SECRET-PC", "brand": "Acme"},
        "bios": {"board_serial": "BOARD-9999", "serial": "N/A"},
        "network": {"interfaces": [{"name": "Wi-Fi", "ipv4": "192.168.1.77", "mac": "AA:BB:CC:DD:EE:FF", "ip": ""}]},
    }


def test_host_name_keys_are_private():
    assert {"hostname", "host_name"} <= PRIVATE_KEYS


def test_private_details_are_hidden_by_default():
    safe = redact_snapshot(_private_snapshot())
    text = json.dumps(safe)
    for secret in SECRETS:
        assert secret not in text
    assert safe["system"]["hostname"] == REDACTED
    assert safe["network"]["interfaces"][0]["mac"] == REDACTED
    assert safe["system"]["brand"] == "Acme"
    assert safe["privacy"]["private_details_hidden"] is True
    assert "computer name" in safe["privacy"]["note"]


def test_empty_and_unavailable_values_stay_as_they_are():
    safe = redact_snapshot(_private_snapshot())
    assert safe["bios"]["serial"] == "N/A"
    assert safe["network"]["interfaces"][0]["ip"] == ""


def test_private_details_are_visible_with_show_private():
    safe = redact_snapshot(_private_snapshot(), show_private=True)
    text = json.dumps(safe)
    for secret in SECRETS:
        assert secret in text
    assert safe["privacy"]["private_details_hidden"] is False
    assert safe["privacy"]["hidden_fields"] == []


def test_redaction_does_not_change_the_original():
    original = _private_snapshot()
    redact_snapshot(original)
    assert original["system"]["hostname"] == "MY-SECRET-PC"


# --------------------------------------------------------------------------
# Snapshot built from fake collector data
# --------------------------------------------------------------------------
def test_snapshot_from_fake_data_is_valid_and_hides_private_details(fake_collectors):
    snapshot = collect_snapshot(include_apps=False)
    assert validate_snapshot(snapshot) == []
    text = json.dumps(snapshot)
    for secret in SECRETS:
        assert secret not in text
    assert r"C:\Users\bob\secret.exe" not in text  # startup command lines are dropped
    assert snapshot["system"]["brand"] == "Acme"


def test_snapshot_with_show_private_keeps_details(fake_collectors):
    text = json.dumps(collect_snapshot(show_private=True, include_apps=False))
    for secret in SECRETS:
        assert secret in text


def test_every_collector_is_read_exactly_once(fake_collectors):
    collect_snapshot(include_apps=False)
    assert fake_collectors["collect_hardware"] == 1
    for name in ("get_battery_health", "get_disk_health", "get_temperatures", "get_security_report", "get_startup_apps"):
        assert fake_collectors[name] == 1, name
    assert not [key for key in fake_collectors if key.startswith("doctor.")]


def test_doctor_judges_the_data_it_was_given(fake_collectors):
    snapshot = collect_snapshot(include_apps=False)
    checks = {c["id"]: c for c in snapshot["health"]["checks"]}
    assert checks["storage.space"]["level"] == "good"
    assert "300.0 GB free" in checks["storage.space"]["value"]
    assert checks["memory.capacity"]["value"] == "16 GB"
    assert checks["startup.count"]["value"].startswith("1 enabled")
    assert checks["disk.health"]["level"] == "muted"


def test_failed_collector_does_not_stop_the_snapshot(fake_collectors, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(export, "get_security_report", boom)
    snapshot = collect_snapshot(include_apps=False)
    assert validate_snapshot(snapshot) == []
    checks = {c["id"]: c for c in snapshot["health"]["checks"]}
    assert checks["security.antivirus"]["level"] == "muted"


# --------------------------------------------------------------------------
# Doctor accepts pre-collected data
# --------------------------------------------------------------------------
@pytest.fixture
def doctor_calls(monkeypatch):
    calls: list[str] = []
    for name in ("get_storage_info", "get_memory_info", "get_battery_health", "get_startup_apps",
                 "get_security_report", "get_disk_health", "get_temperatures"):
        def reader(*_a, _name=name, **_k):
            calls.append(_name)
            return {}

        monkeypatch.setattr(doctor_checks, name, reader)
    return calls


def test_run_checks_reads_nothing_when_all_data_is_given(doctor_calls):
    result = run_doctor(storage={}, memory={}, battery={}, startup={}, security={}, disk={}, temps={})
    assert doctor_calls == []
    assert result["checks"]


def test_run_checks_reads_only_missing_data(doctor_calls):
    doctor_checks.run_checks(
        storage={"volumes": []}, battery={}, startup={}, security={}, disk={}, temps={}
    )
    assert doctor_calls == ["get_memory_info"]


def test_run_checks_default_reads_everything(doctor_calls):
    doctor_checks.run_checks()
    assert sorted(doctor_calls) == sorted(
        ["get_storage_info", "get_memory_info", "get_battery_health", "get_startup_apps",
         "get_security_report", "get_disk_health", "get_temperatures"]
    )


# --------------------------------------------------------------------------
# Default report folder
# --------------------------------------------------------------------------
def test_default_folder_is_in_the_home_folder(tmp_path):
    home, cwd = tmp_path / "home", tmp_path / "work"
    assert choose_report_dir("", home, cwd) == home / "LapForge" / "Reports"


def test_configured_folder_wins(tmp_path):
    assert choose_report_dir(str(tmp_path / "mine"), tmp_path / "home", tmp_path) == tmp_path / "mine"


def test_current_folder_is_used_only_without_a_home_folder(tmp_path):
    assert choose_report_dir("", None, tmp_path / "work") == tmp_path / "work" / "reports"


def test_windows_system_folder_is_never_used(tmp_path, monkeypatch):
    windows = tmp_path / "FakeWindows"
    monkeypatch.setenv("SystemRoot", str(windows))
    system32 = windows / "System32"
    assert export.is_system_folder(system32)
    assert not export.is_system_folder(tmp_path / "Other")
    chosen = choose_report_dir("", None, system32)
    assert chosen == Path(tempfile.gettempdir()) / "LapForge" / "Reports"
    # A home folder inside a system folder is skipped as well.
    assert choose_report_dir("", system32, tmp_path / "work") == tmp_path / "work" / "reports"


def test_default_output_dir_uses_home_not_cwd(tmp_path, monkeypatch):
    home, cwd = tmp_path / "home", tmp_path / "work"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: cwd))
    assert default_output_dir() == home / "LapForge" / "Reports"


def test_default_output_dir_uses_configured_report_dir(tmp_path, monkeypatch):
    from lapforge.config import set_setting

    target = tmp_path / "my reports"
    assert set_setting("report_dir", str(target))["ok"]
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert default_output_dir() == target


def test_write_exports_creates_the_folder(fake_collectors, tmp_path):
    folder = tmp_path / "a" / "b" / "Reports"
    paths = write_exports(collect_snapshot(include_apps=False), folder, ["json"])
    assert paths["json"].parent == folder and paths["json"].exists()


# --------------------------------------------------------------------------
# Files written by `lapforge report`
# --------------------------------------------------------------------------
def _run_report(tmp_path, monkeypatch, *extra: str):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(hardware.socket, "gethostname", lambda: "TEST-HOST-XYZ")
    code = cli.main(["report", "--format", "all", "--no-apps", *extra])
    files = sorted((home / "LapForge" / "Reports").glob("lapforge-report-*"))
    return code, files


def test_report_saves_to_default_folder_without_host_name(tmp_path, monkeypatch, capsys):
    code, files = _run_report(tmp_path, monkeypatch)
    capsys.readouterr()
    assert code == 0
    assert {f.suffix for f in files} == {".json", ".md", ".html"}
    for file in files:
        assert "TEST-HOST-XYZ" not in file.read_text(encoding="utf-8")


def test_report_with_show_private_includes_host_name(tmp_path, monkeypatch, capsys):
    code, files = _run_report(tmp_path, monkeypatch, "--show-private")
    capsys.readouterr()
    assert code == 0
    json_file = next(f for f in files if f.suffix == ".json")
    assert "TEST-HOST-XYZ" in json_file.read_text(encoding="utf-8")
