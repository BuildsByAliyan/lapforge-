"""TPM check (Phase 33c): never say 'No TPM detected' unless Windows could really tell."""

from __future__ import annotations

import pytest

from lapforge.collectors.security import assess_tpm


def test_standard_user_cannot_confirm_a_missing_tpm():
    check = assess_tpm({"present": False, "ready": False, "spec": ""}, admin=False)
    assert check["level"] == "muted"
    assert "Administrator" in check["value"]
    assert "Run LapForge as Administrator" in check["hint"]
    assert "No TPM detected" not in check["value"]


def test_administrator_with_no_tpm_gets_the_warning():
    check = assess_tpm({"present": False, "ready": False, "spec": ""}, admin=True)
    assert check["level"] == "warn"
    assert check["value"] == "No TPM detected"
    assert "BIOS" in check["hint"]


@pytest.mark.parametrize("admin", [True, False])
def test_a_present_ready_tpm_is_good_for_everyone(admin):
    check = assess_tpm({"present": True, "ready": True, "spec": "2.0, 0, 1.38"}, admin=admin)
    assert check["level"] == "good"
    assert check["value"] == "TPM 2.0, ready"
    assert check["hint"] == ""


def test_a_present_tpm_that_is_not_ready_warns():
    check = assess_tpm({"present": True, "ready": False, "spec": "1.2"}, admin=False)
    assert check["level"] == "warn" and "not ready" in check["value"]


def test_no_answer_at_all_is_unknown_never_a_warning():
    assert assess_tpm(None, admin=False)["level"] == "muted"
    assert assess_tpm(None, admin=True)["level"] == "muted"
    assert assess_tpm("odd", admin=True)["value"] == "Not available"
