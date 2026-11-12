"""Tests for the plain-English text layer: glossary, two-part hints, health screen (Phase 27)."""

from __future__ import annotations

import pytest
from rich.console import Console

from lapforge.doctor.fix_hints import HINTS, MEANS_LABEL, TODO_LABEL, get_hint, split_hint
from lapforge.ui import health_view
from lapforge.ui.glossary import TERMS, explain, find_terms
from lapforge.ui.theme import LAPFORGE_THEME

REQUIRED_TERMS = [
    "TPM", "Secure Boot", "BitLocker", "UAC", "Firewall", "Defender real-time protection", "SMART",
    "SFC", "DISM", "Prefetch", "DNS cache", "Startup programs", "Restore point", "Battery wear", "Boot mode",
]


@pytest.mark.parametrize("term", REQUIRED_TERMS)
def test_every_required_term_has_one_plain_sentence(term):
    text = explain(term)
    assert text and text.endswith(".")
    assert text.count(". ") == 0  # one sentence


def test_explain_ignores_case_and_accepts_other_names():
    assert explain("tpm") == explain("TPM")
    assert explain("  secure   boot ") == explain("Secure Boot")
    assert explain("UEFI") == explain("Legacy BIOS") == explain("Boot mode")
    assert explain("User Account Control") == explain("UAC")


@pytest.mark.parametrize("term", ["", "nonsense", "RAM disk", None])
def test_unknown_term_gives_an_empty_string(term):
    assert explain(term) == ""


def test_find_terms_keeps_order_and_ignores_plain_words():
    assert find_terms("Secure Boot is off and the TPM is missing") == ["Secure Boot", "TPM"]
    assert find_terms("a smart idea") == []  # the ordinary word is not the drive report
    assert find_terms("SMART warning") == ["SMART"]
    assert find_terms("") == []
    assert find_terms("Turn on Real-time protection") == ["Defender real-time protection"]


def test_every_hint_has_both_parts():
    for check_id, levels in HINTS.items():
        for level, text in levels.items():
            means, todo = split_hint(text)
            assert means and todo, f"{check_id}/{level} must say what it means and what to do"
            assert text.startswith(MEANS_LABEL) and TODO_LABEL in text


def test_hints_keep_their_placeholders():
    assert "{mount}" in get_hint("storage.space", "warn")
    assert "C:\\" in get_hint("storage.space", "bad", mount="C:\\")


def test_split_hint_handles_other_text():
    assert split_hint("Turn it on.") == ("", "Turn it on.")
    assert split_hint("") == ("", "")
    assert split_hint(f"{MEANS_LABEL} A. {TODO_LABEL} B.") == ("A.", "B.")


def _problem(check_id, level, label, value, hint=None):
    return {"id": check_id, "level": level, "label": label, "value": value,
            "hint": get_hint(check_id, level) if hint is None else hint}


def _screen(problems) -> str:
    console = Console(theme=LAPFORGE_THEME, record=True, width=110)
    console.print(health_view.hints_panel(problems))
    return console.export_text()


def test_health_screen_shows_both_parts_and_explains_terms():
    text = _screen([_problem("security.tpm", "bad", "TPM", "Off")])
    assert "What this means:" in text and "What to do:" in text
    assert "TPM: A security chip" in text


def test_a_term_is_explained_only_once():
    text = _screen([
        _problem("security.tpm", "bad", "TPM", "Off"),
        _problem("security.tpm", "warn", "TPM", "Not ready"),
    ])
    assert text.count("A security chip") == 1


def test_a_hint_from_a_collector_still_shows_what_to_do():
    text = _screen([_problem("security.firewall", "warn", "Firewall", "Off", hint="Turn the profile on.")])
    assert "What to do: Turn the profile on." in text
    assert "What this means:" not in text


def test_missing_hint_has_a_fallback():
    text = _screen([_problem("x.y", "warn", "Thing", "Odd", hint="")])
    assert "No fix hint available." in text


def test_no_problems_message_is_unchanged():
    assert "No problems found. Nothing to fix." in _screen([])


def test_terms_dict_matches_the_required_list():
    assert set(REQUIRED_TERMS) <= set(TERMS)
