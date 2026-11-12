"""Security status: antivirus, firewall, disk encryption, TPM, Secure Boot, UAC.

Every check becomes one small dict:
    {"key", "label", "level", "value", "hint"}
where level is 'good' / 'warn' / 'bad' / 'muted' (muted = could not be read)
and hint is a short "how to fix it" line (empty when everything is fine).
Nothing here changes any setting - it only reads.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..utils import IS_WINDOWS, NA, as_list, is_admin, powershell_json
from .bios import get_bios_info

try:  # winreg only exists on Windows
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

_DEFENDER_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$s = Get-MpComputerStatus
if ($s) {
    [ordered]@{
        av = $s.AntivirusEnabled
        realtime = $s.RealTimeProtectionEnabled
        signature_age = $s.AntivirusSignatureAge
        tamper = $s.IsTamperProtected
    } | ConvertTo-Json -Compress
}
"""

_AV_PRODUCTS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct |
    ForEach-Object { [ordered]@{ name = "$($_.displayName)"; state = [int64]$_.productState } } |
    ConvertTo-Json -Compress
"""

_FIREWALL_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-NetFirewallProfile | ForEach-Object {
    [ordered]@{ name = "$($_.Name)"; enabled = "$($_.Enabled)" }
} | ConvertTo-Json -Compress
"""

_BITLOCKER_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-BitLockerVolume | ForEach-Object {
    [ordered]@{
        mount = "$($_.MountPoint)"
        protection = "$($_.ProtectionStatus)"
        volume_type = "$($_.VolumeType)"
    }
} | ConvertTo-Json -Compress
"""

_TPM_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$t = Get-Tpm
$w = Get-CimInstance -Namespace 'root\cimv2\Security\MicrosoftTpm' -ClassName Win32_Tpm | Select-Object -First 1
if ($t) {
    [ordered]@{
        present = $t.TpmPresent
        ready = $t.TpmReady
        spec = "$($w.SpecVersion)"
    } | ConvertTo-Json -Compress
}
"""

_STATUS_WORDS = {"good": "OK", "warn": "Warning", "bad": "At risk", "muted": "Unknown"}


def status_word(level: str) -> str:
    """'good' -> 'OK', 'bad' -> 'At risk' ..."""
    return _STATUS_WORDS.get(level, "Unknown")


def _check(key: str, label: str, level: str, value: str, hint: str = "") -> dict[str, str]:
    return {"key": key, "label": label, "level": level, "value": value, "hint": hint if level != "good" else ""}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Pure assessment functions (easy to test, no system access)
# --------------------------------------------------------------------------
# Windows Security Center "productState" is a number that reads as hex AABBCC:
#   AA = what the product is, BB = on / off / snoozed / expired (high digit), CC = definitions.
_PRODUCT_STATUS = {0: "off", 1: "on", 2: "snoozed", 3: "expired"}
_STATUS_PHRASE = {"off": "switched off", "snoozed": "paused (snoozed)", "expired": "expired"}


def decode_product_state(state: Any) -> dict[str, Any]:
    """Decode a Security Center productState number (pure, no system access).

    Returns {"status", "enabled", "up_to_date"}. status is 'on', 'off', 'snoozed',
    'expired' or 'unknown'. Only 'on' means the product is protecting the PC.
    enabled is True/False for a known status and None for 'unknown'.
    up_to_date is True/False when Windows reports it, otherwise None.
    """
    unknown: dict[str, Any] = {"status": "unknown", "enabled": None, "up_to_date": None}
    if isinstance(state, bool):
        return unknown
    try:
        number = int(state)
    except (TypeError, ValueError):
        return unknown
    if not 0 <= number <= 0xFFFFFF:
        return unknown
    status = _PRODUCT_STATUS.get((number >> 12) & 0xF)
    if status is None:
        return unknown
    signatures = (number >> 4) & 0xF
    return {
        "status": status,
        "enabled": status == "on",
        "up_to_date": True if signatures == 0 else False if signatures == 1 else None,
    }


def normalize_products(raw: Any) -> list[dict[str, Any]]:
    """Turn the Security Center answer into [{"name", "status", "enabled", "up_to_date"}].

    Accepts dicts with a productState ("state"), already decoded dicts, and plain names
    (a plain name has no state, so its status is 'unknown').
    """
    products: list[dict[str, Any]] = []
    for item in as_list(raw):
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if "status" in item:
                decoded = {key: item.get(key) for key in ("status", "enabled", "up_to_date")}
            else:
                decoded = decode_product_state(item.get("state"))
        else:
            name = str(item or "").strip()
            decoded = decode_product_state(None)
        if name:
            products.append({"name": name, **decoded})
    return products


def _names(products: list[dict[str, Any]]) -> str:
    return ", ".join(p["name"] for p in products)


def _third_party_result(defender_text: str, others: list[dict[str, Any]]) -> dict[str, str] | None:
    """Result when no Defender protection is confirmed but third-party products exist (None if there are none)."""
    if not others:
        return None
    active = [p for p in others if p["status"] == "on"]
    if active:
        if defender_text:
            return _check("antivirus", "Antivirus", "good", f"{defender_text}, but {_names(active)} is switched on and protecting this PC")
        return _check("antivirus", "Antivirus", "good", f"Third-party antivirus: {_names(active)} (switched on)")
    inactive = [p for p in others if p["status"] in _STATUS_PHRASE]
    if inactive:
        phrases = ", ".join(f"{p['name']} is {_STATUS_PHRASE[p['status']]}" for p in inactive)
        lead = f"{defender_text} and " if defender_text else ""
        return _check(
            "antivirus",
            "Antivirus",
            "warn",
            f"{lead}{phrases}. No antivirus is protecting this PC right now",
            f"Open {inactive[0]['name']} and switch its protection on. If you no longer use it, "
            "turn on Windows Defender in Windows Security > Virus & threat protection > Manage settings.",
        )
    return _check(
        "antivirus",
        "Antivirus",
        "muted",
        f"Could not tell whether {_names(others)} is switched on",
        f"Open {others[0]['name']} and check that its protection is on.",
    )


def assess_antivirus(defender: Any, products: Any) -> dict[str, str]:
    """Antivirus state. Only products that are switched ON count as protection.

    `products` is the Security Center list (see normalize_products). A third-party
    antivirus that is off, snoozed or expired is not protection and gives a warning.
    """
    others = [p for p in normalize_products(products) if "defender" not in p["name"].lower()]
    if not isinstance(defender, dict):
        result = _third_party_result("", others)
        if result is not None:
            return result
        return _check(
            "antivirus",
            "Antivirus",
            "muted",
            "Could not read Defender status",
            "Open Windows Security and check Virus & threat protection.",
        )
    if defender.get("realtime") is True:
        age = _num(defender.get("signature_age"))
        text = "Defender real-time protection is on"
        if age is None:
            return _check("antivirus", "Antivirus", "good", text)
        days = int(age)
        text += f", definitions {days} day(s) old"
        if days > 14:
            return _check("antivirus", "Antivirus", "bad", text, "Windows Security > Virus & threat protection > Check for updates.")
        if days > 7:
            return _check("antivirus", "Antivirus", "warn", text, "Windows Security > Virus & threat protection > Check for updates.")
        return _check("antivirus", "Antivirus", "good", text)
    result = _third_party_result("Defender is off", others)
    if result is not None:
        return result
    return _check(
        "antivirus",
        "Antivirus",
        "bad",
        "Real-time protection is OFF and no other antivirus found",
        "Windows Security > Virus & threat protection > Manage settings > turn on Real-time protection.",
    )


def assess_firewall(profiles: list[dict[str, Any]]) -> dict[str, str]:
    if not profiles:
        return _check("firewall", "Firewall", "muted", "Could not read firewall status", "Open Windows Security > Firewall & network protection.")
    parts: list[str] = []
    off: list[str] = []
    for p in profiles:
        name = str(p.get("name", "?"))
        on = str(p.get("enabled", "")).lower() == "true"
        parts.append(f"{name}: {'On' if on else 'Off'}")
        if not on:
            off.append(name)
    value = ", ".join(parts)
    if not off:
        return _check("firewall", "Firewall", "good", value)
    hint = "Windows Security > Firewall & network protection > turn the profile on."
    level = "bad" if "Public" in off or len(off) == len(profiles) else "warn"
    return _check("firewall", "Firewall", level, value, hint)


def assess_bitlocker(volumes: list[dict[str, Any]], admin: bool) -> dict[str, str]:
    label = "Disk encryption (BitLocker)"
    if not volumes:
        if not admin:
            return _check("bitlocker", label, "muted", "Needs an Administrator terminal to read", "Run LapForge as Administrator to check BitLocker.")
        return _check("bitlocker", label, "muted", "Not available (not supported on this Windows edition)")
    summary = ", ".join(f"{v.get('mount', '?')} {'On' if str(v.get('protection', '')).lower() == 'on' else 'Off'}" for v in volumes)
    os_vols = [v for v in volumes if "operating" in str(v.get("volume_type", "")).lower()] or volumes
    if all(str(v.get("protection", "")).lower() == "on" for v in os_vols):
        return _check("bitlocker", label, "good", summary)
    return _check(
        "bitlocker",
        label,
        "warn",
        f"{summary} (system drive is not encrypted)",
        "Settings > Privacy & security > Device encryption (or BitLocker on Pro editions). Save the recovery key first.",
    )


def assess_tpm(data: Any, admin: bool) -> dict[str, str]:
    if not isinstance(data, dict):
        if not admin:
            return _check("tpm", "TPM", "muted", "Needs an Administrator terminal to read", "Run LapForge as Administrator to check the TPM.")
        return _check("tpm", "TPM", "muted", "Not available")
    if data.get("present") is not True:
        if not admin:
            # Without Administrator rights Windows can answer "no TPM" for a laptop that has one.
            return _check(
                "tpm",
                "TPM",
                "muted",
                "Needs an Administrator terminal to confirm",
                "Run LapForge as Administrator to check the TPM. Without Administrator rights Windows can report a TPM as missing even when the laptop has one.",
            )
        return _check("tpm", "TPM", "warn", "No TPM detected", "Look for 'fTPM', 'PTT' or 'TPM' in the BIOS and enable it (Windows 11 needs TPM 2.0).")
    spec = str(data.get("spec") or "").split(",")[0].strip()
    name = f"TPM {spec}" if spec else "TPM present"
    if data.get("ready") is True:
        return _check("tpm", "TPM", "good", f"{name}, ready")
    return _check("tpm", "TPM", "warn", f"{name}, not ready", "Open tpm.msc and initialise the TPM, or check the BIOS TPM setting.")


def assess_secure_boot(text: str) -> dict[str, str]:
    if text == "Enabled":
        return _check("secure_boot", "Secure Boot", "good", "Enabled")
    if text == "Disabled":
        return _check("secure_boot", "Secure Boot", "warn", "Disabled", "Turn on Secure Boot in the BIOS/UEFI settings (Boot or Security tab).")
    return _check("secure_boot", "Secure Boot", "muted", text if text != NA else "Not available")


def assess_uac(value: int | None) -> dict[str, str]:
    if value == 1:
        return _check("uac", "User Account Control", "good", "Enabled")
    if value == 0:
        return _check("uac", "User Account Control", "bad", "Disabled", "Control Panel > Security and Maintenance > Change User Account Control settings.")
    return _check("uac", "User Account Control", "muted", "Not available")


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------
def _read_uac() -> int | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "EnableLUA")
            return int(value)
    except (OSError, ValueError):
        return None


def _safe_json(script: str, timeout: int) -> Any:
    try:
        return powershell_json(script, timeout=timeout)
    except Exception:
        return None


def get_security_report() -> dict[str, Any]:
    """Run every security check. Never raises."""
    report: dict[str, Any] = {"available": False, "checks": [], "good": 0, "warn": 0, "bad": 0, "unknown": 0, "note": ""}
    if not IS_WINDOWS:
        report["note"] = "Security checks need Windows (not available on this system)."
        return report
    try:
        admin = is_admin()
        with ThreadPoolExecutor(max_workers=5) as pool:
            f_def = pool.submit(_safe_json, _DEFENDER_SCRIPT, 30)
            f_av = pool.submit(_safe_json, _AV_PRODUCTS_SCRIPT, 30)
            f_fw = pool.submit(_safe_json, _FIREWALL_SCRIPT, 30)
            f_bl = pool.submit(_safe_json, _BITLOCKER_SCRIPT, 30)
            f_tpm = pool.submit(_safe_json, _TPM_SCRIPT, 30)
            defender, av_raw, fw_raw = f_def.result(), f_av.result(), f_fw.result()
            bl_raw, tpm = f_bl.result(), f_tpm.result()
        products = normalize_products(av_raw)
        checks = [
            assess_antivirus(defender, products),
            assess_firewall([p for p in as_list(fw_raw) if isinstance(p, dict)]),
            assess_bitlocker([v for v in as_list(bl_raw) if isinstance(v, dict)], admin),
            assess_tpm(tpm, admin),
            assess_secure_boot(get_bios_info()["secure_boot"]),
            assess_uac(_read_uac()),
        ]
    except Exception as exc:  # last-resort guard: collectors must never crash the CLI
        report["note"] = f"Security checks failed: {exc}"
        return report
    report["available"] = True
    report["checks"] = checks
    for c in checks:
        report[{"good": "good", "warn": "warn", "bad": "bad"}.get(c["level"], "unknown")] += 1
    return report
