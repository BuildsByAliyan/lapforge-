"""Plain-English explanations of technical terms.

    explain("TPM")          -> one sentence in everyday words ("" for an unknown term)
    find_terms(text)        -> the known terms that appear in a piece of text

Screens use this the first time a technical word appears, so nobody has to look it up.
"""

from __future__ import annotations

import re

# Canonical term -> one plain sentence.
TERMS: dict[str, str] = {
    "TPM": "A security chip inside the laptop that protects your sign-in and encryption keys.",
    "Secure Boot": "A startup safety check that stops harmful software from loading before Windows starts.",
    "BitLocker": "A Windows feature that locks the contents of your drive, so nobody can read your files if the laptop is lost or stolen.",
    "UAC": "User Account Control, the Windows question \"Do you want to allow this app to make changes?\" that stops programs changing your PC without asking.",
    "Firewall": "A guard that blocks unwanted connections between your laptop and the internet or other computers.",
    "Defender real-time protection": "Windows Security's constant scanning, which catches viruses as soon as they appear.",
    "SMART": "A health report that a drive makes about itself and that can warn you before the drive fails.",
    "SFC": "System File Checker, a Windows tool that finds and repairs damaged system files.",
    "DISM": "A Windows tool that repairs the stored copy of Windows that System File Checker (SFC) uses.",
    "Prefetch": "Small files Windows keeps so that apps open faster, which Windows rebuilds by itself.",
    "DNS cache": "A short-term list of website addresses that your laptop remembers, which is rebuilt automatically.",
    "Startup programs": "Programs that start by themselves when Windows starts and can make it slower.",
    "Restore point": "A saved snapshot of your Windows settings that lets you go back if a change causes trouble.",
    "Battery wear": "How much battery capacity has been lost with age, compared with when the battery was new.",
    "Boot mode": "How the laptop starts Windows: UEFI is the modern way and Legacy BIOS is the older way.",
}

# Other names that explain() understands (lower case).
_ALIASES: dict[str, str] = {
    "user account control": "UAC",
    "real-time protection": "Defender real-time protection",
    "realtime protection": "Defender real-time protection",
    "defender": "Defender real-time protection",
    "uefi": "Boot mode",
    "legacy bios": "Boot mode",
    "boot mode (uefi / legacy bios)": "Boot mode",
    "startup": "Startup programs",
    "startup apps": "Startup programs",
    "system file checker": "SFC",
    "system restore point": "Restore point",
}

# How find_terms() spots each term in a text. Short acronyms are matched in capital letters
# only, so the ordinary word "smart" is not mistaken for SMART.
_DETECT: dict[str, re.Pattern[str]] = {
    "TPM": re.compile(r"\bTPM\b"),
    "Secure Boot": re.compile(r"\bsecure boot\b", re.IGNORECASE),
    "BitLocker": re.compile(r"\bbitlocker\b", re.IGNORECASE),
    "UAC": re.compile(r"\bUAC\b|\buser account control\b", re.IGNORECASE),
    "Firewall": re.compile(r"\bfirewall\b", re.IGNORECASE),
    "Defender real-time protection": re.compile(r"\breal-?time protection\b", re.IGNORECASE),
    "SMART": re.compile(r"\bSMART\b"),
    "SFC": re.compile(r"\bSFC\b"),
    "DISM": re.compile(r"\bDISM\b"),
    "Prefetch": re.compile(r"\bprefetch\b", re.IGNORECASE),
    "DNS cache": re.compile(r"\bdns cache\b", re.IGNORECASE),
    "Startup programs": re.compile(r"\bstartup programs\b", re.IGNORECASE),
    "Restore point": re.compile(r"\brestore points?\b", re.IGNORECASE),
    "Battery wear": re.compile(r"\bbattery wear\b", re.IGNORECASE),
    "Boot mode": re.compile(r"\bboot mode\b", re.IGNORECASE),
}


def _canonical(term: str) -> str:
    key = " ".join((term or "").split()).lower()
    for name in TERMS:
        if name.lower() == key:
            return name
    return _ALIASES.get(key, "")


def explain(term: str) -> str:
    """One plain sentence for a technical term, or "" if the term is not known."""
    return TERMS.get(_canonical(term), "")


def find_terms(text: str) -> list[str]:
    """Known terms that appear in `text`, in the order they first appear (no repeats)."""
    found: list[tuple[int, str]] = []
    for name, pattern in _DETECT.items():
        match = pattern.search(text or "")
        if match:
            found.append((match.start(), name))
    return [name for _pos, name in sorted(found)]


__all__ = ["TERMS", "explain", "find_terms"]
