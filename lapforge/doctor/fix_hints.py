"""Plain-language "how to fix it" text for every doctor check.

Every hint has two parts, written for people who are not technical:

    What this means: why the result matters.
    What to do: the next step.

Hints are only returned for problems ('warn' / 'bad'). A check that is fine, or
one LapForge simply could not read ('muted'), gets an empty hint.
"""

from __future__ import annotations

from typing import Any

MEANS_LABEL = "What this means:"
TODO_LABEL = "What to do:"


def _hint(means: str, todo: str) -> str:
    """Build a two-part hint. The text may contain {placeholders} (they are filled later)."""
    return f"{MEANS_LABEL} {means} {TODO_LABEL} {todo}"


def split_hint(text: str) -> tuple[str, str]:
    """Split a hint into (what this means, what to do).

    A hint that does not follow the two-part pattern (for example one written by a
    collector) is returned as ("", text).
    """
    text = (text or "").strip()
    if text.startswith(MEANS_LABEL) and TODO_LABEL in text:
        means, _sep, todo = text[len(MEANS_LABEL):].partition(TODO_LABEL)
        return means.strip(), todo.strip()
    return "", text


# check id -> {level: hint}. Use {name} placeholders for context values.
HINTS: dict[str, dict[str, str]] = {
    "storage.space": {
        "warn": _hint(
            "Drive {mount} is getting full. Windows needs free space to install updates and run smoothly.",
            "Run `lapforge clean` to remove Junk files, then uninstall apps you no longer use (`lapforge apps --sort size`).",
        ),
        "bad": _hint(
            "Drive {mount} is almost full. Windows may slow down or fail to install updates.",
            "Run `lapforge clean`, empty the Recycle Bin and move large videos or photos to an external drive.",
        ),
    },
    "memory.usage": {
        "warn": _hint(
            "The memory (RAM) is busy, so apps may respond slowly.",
            "Close browser tabs and apps you are not using. Run `lapforge startup` to see which Startup programs use memory.",
        ),
        "bad": _hint(
            "The memory (RAM) is almost full, so the laptop will feel slow and apps may freeze.",
            "Close large apps now. Restart the laptop if the memory stays full.",
        ),
    },
    "memory.capacity": {
        "warn": _hint(
            "This laptop has less memory (RAM) than modern Windows works best with.",
            "Keep fewer apps open at once. Run `lapforge upgrade` to see whether the memory can be increased.",
        ),
        "bad": _hint(
            "This laptop has very little memory (RAM), which slows down almost everything.",
            "Adding memory is the biggest speed-up you can give it. Run `lapforge upgrade` to see whether it can be upgraded.",
        ),
    },
    "battery.wear": {
        "warn": _hint(
            "The battery has lost part of its original capacity (Battery wear), so each charge lasts a shorter time.",
            "Avoid leaving the laptop at 100% on the charger for days, and keep it away from heat.",
        ),
        "bad": _hint(
            "The battery is badly worn and will not last long on a charge.",
            "Plan a battery replacement, and keep the charger with you until then.",
        ),
    },
    "startup.count": {
        "warn": _hint(
            "Many Startup programs start with Windows, which makes starting the laptop slower.",
            "Run `lapforge startup` and turn off the programs you do not need. You can also use Task Manager and its Startup apps tab.",
        ),
        "bad": _hint(
            "Far too many Startup programs start with Windows, which makes starting the laptop much slower.",
            "Turn off the heavy ones with `lapforge startup --manage`. Every change can be undone.",
        ),
    },
    "security.antivirus": {
        "warn": _hint(
            "The antivirus protection is not fully up to date or not fully switched on, so some threats may be missed.",
            "Open Windows Security, choose Virus & threat protection, then select Check for updates. If you use another antivirus, open it and switch its protection on.",
        ),
        "bad": _hint(
            "No antivirus seems to be protecting this PC, so a virus could infect it.",
            "Open Windows Security, choose Virus & threat protection, and turn on Real-time protection.",
        ),
    },
    "security.firewall": {
        "warn": _hint(
            "The Firewall is off for one type of network, so unwanted connections may get through.",
            "Open Windows Security, choose Firewall & network protection, and turn the Firewall on.",
        ),
        "bad": _hint(
            "The Firewall is off for an important type of network, so other computers can try to connect to yours.",
            "Open Windows Security, choose Firewall & network protection, and turn the Firewall on.",
        ),
    },
    "security.bitlocker": {
        "warn": _hint(
            "The system drive is not locked with BitLocker, so anyone who finds a lost or stolen laptop could read your files.",
            "Open Settings, choose Privacy & security, then Device encryption (or BitLocker). Save the recovery key in a safe place first.",
        ),
        "bad": _hint(
            "The system drive is not locked with BitLocker, so anyone who finds a lost or stolen laptop could read your files.",
            "Open Settings, choose Privacy & security, then Device encryption (or BitLocker). Save the recovery key in a safe place first.",
        ),
    },
    "security.tpm": {
        "warn": _hint(
            "The TPM is turned off or missing, and Windows 11 needs TPM 2.0.",
            "Open the BIOS settings and turn on the TPM (it may be called fTPM or PTT). Your laptop maker's website explains how.",
        ),
        "bad": _hint(
            "The TPM is turned off or missing, and Windows 11 needs TPM 2.0.",
            "Open the BIOS settings and turn on the TPM (it may be called fTPM or PTT). Your laptop maker's website explains how.",
        ),
    },
    "security.secure_boot": {
        "warn": _hint(
            "Secure Boot is off, so harmful software could load before Windows starts.",
            "Open the BIOS or UEFI settings and turn on Secure Boot (look on the Boot or Security tab). Laptops that use Legacy BIOS do not support it.",
        ),
        "bad": _hint(
            "Secure Boot is off, so harmful software could load before Windows starts.",
            "Open the BIOS or UEFI settings and turn on Secure Boot (look on the Boot or Security tab). Laptops that use Legacy BIOS do not support it.",
        ),
    },
    "security.uac": {
        "warn": _hint(
            "User Account Control is set low, so programs can change your PC with fewer questions.",
            "Open Control Panel, choose Security and Maintenance, and raise User Account Control to its default level.",
        ),
        "bad": _hint(
            "User Account Control is off, so programs can change your PC without asking you.",
            "Open Control Panel, choose Security and Maintenance, and turn User Account Control back on.",
        ),
    },
    "disk.health": {
        "warn": _hint(
            "A drive is reporting a warning in its SMART health report.",
            "Back up your important files soon, and run the check again in a few weeks.",
        ),
        "bad": _hint(
            "A drive is failing or nearly worn out, and you could lose files.",
            "Back up your files now and plan to replace the drive.",
        ),
    },
    "thermal.temperature": {
        "warn": _hint(
            "The laptop is running warm, which can slow it down and wear its parts faster.",
            "Clean the air vents, use the laptop on a hard, flat surface and close heavy apps.",
        ),
        "bad": _hint(
            "The laptop is running very hot, which can cause slowdowns, sudden shutdowns and damage.",
            "Close heavy apps now, clean the vents and fan, and avoid soft surfaces such as beds.",
        ),
    },
}


class _SafeDict(dict):
    """format_map helper that leaves unknown {placeholders} untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def get_hint(check_id: str, level: str, **context: Any) -> str:
    """Return the fix hint for a check at a given level ('' when nothing to fix)."""
    if level not in ("warn", "bad"):
        return ""
    levels = HINTS.get(check_id)
    if not levels:
        return ""
    text = levels.get(level) or levels.get("warn", "")
    return text.format_map(_SafeDict(context))
