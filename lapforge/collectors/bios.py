"""BIOS / firmware and motherboard collector."""

from __future__ import annotations

import platform
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..utils import IS_WINDOWS, NA, clean_text, powershell_json

_BIOS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$b = Get-CimInstance Win32_BIOS | Select-Object -First 1
$m = Get-CimInstance Win32_BaseBoard | Select-Object -First 1
$rd = $null
if ($b.ReleaseDate) { $rd = $b.ReleaseDate.ToString('yyyy-MM-dd') }
[ordered]@{
    vendor = $b.Manufacturer
    version = $b.SMBIOSBIOSVersion
    release_date = $rd
    serial = $b.SerialNumber
    smbios = "$($b.SMBIOSMajorVersion).$($b.SMBIOSMinorVersion)"
    board_vendor = $m.Manufacturer
    board_model = $m.Product
    board_serial = $m.SerialNumber
} | ConvertTo-Json -Compress
"""

HIDDEN_TEXT = "Hidden (use --show-serial)"


def tidy_version(text: str) -> str:
    """Remove stray spaces some firmware puts inside brackets: 'JBET51WW (1.16 )' -> 'JBET51WW (1.16)'."""
    if text == NA:
        return text
    text = re.sub(r"([(\[])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def redact(value: str, reveal: bool = False) -> str:
    """Hide serial numbers unless the user explicitly asked to see them."""
    if value == NA or reveal:
        return value
    return HIDDEN_TEXT


def _dmi(name: str) -> str:
    try:
        return Path(f"/sys/class/dmi/id/{name}").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _firmware_mode(code: Any) -> str:
    """1 = Legacy BIOS, 2 = UEFI. Anything else (including booleans) is unknown."""
    if isinstance(code, bool):
        return NA
    return {1: "Legacy BIOS", 2: "UEFI"}.get(code, NA)


def firmware_mode_from_sources(registry_code: Any, api_code: Any) -> str:
    """Pick the boot mode: the registry value first, the Windows API value as a fallback."""
    mode = _firmware_mode(registry_code)
    if mode != NA:
        return mode
    return _firmware_mode(api_code)


def _secure_boot_text(flag: Any, mode: str) -> str:
    """Secure Boot status. Legacy BIOS laptops cannot use Secure Boot at all."""
    if mode == "Legacy BIOS":
        return "Not supported (Legacy BIOS)"
    if isinstance(flag, bool):
        return NA
    if flag == 1:
        return "Enabled"
    if flag == 0:
        return "Disabled"
    return NA


_CONTROL_KEY = r"SYSTEM\CurrentControlSet\Control"
_SECURE_BOOT_KEY = r"SYSTEM\CurrentControlSet\Control\SecureBoot\State"


def _read_registry_dword(subkey: str, name: str) -> int | None:
    """Read one HKLM value (readable without Administrator rights). None if unavailable."""
    if not IS_WINDOWS:
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return value if isinstance(value, int) else None
    except Exception:
        return None


def _firmware_type_from_api() -> int | None:
    """Windows API GetFirmwareType: 1 = Legacy BIOS, 2 = UEFI. None if unavailable."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes

        result = ctypes.c_uint(0)
        ok = ctypes.windll.kernel32.GetFirmwareType(ctypes.byref(result))  # type: ignore[attr-defined]
        return int(result.value) if ok else None
    except Exception:
        return None


def detect_boot_mode() -> str:
    """'UEFI', 'Legacy BIOS' or N/A on Windows, without needing Administrator rights."""
    registry_code = _read_registry_dword(_CONTROL_KEY, "PEFirmwareType")
    api_code = None if _firmware_mode(registry_code) != NA else _firmware_type_from_api()
    return firmware_mode_from_sources(registry_code, api_code)


def detect_secure_boot(mode: str) -> str:
    """Secure Boot text for the given boot mode (registry value is read only on UEFI)."""
    flag = None if mode == "Legacy BIOS" else _read_registry_dword(_SECURE_BOOT_KEY, "UEFISecureBootEnabled")
    return _secure_boot_text(flag, mode)


@lru_cache(maxsize=1)
def get_bios_info() -> dict[str, Any]:
    """BIOS vendor/version/date, boot mode, Secure Boot, and motherboard details."""
    info: dict[str, Any] = {
        "vendor": NA,
        "version": NA,
        "release_date": NA,
        "smbios": NA,
        "mode": NA,
        "secure_boot": NA,
        "serial": NA,
        "board_vendor": NA,
        "board_model": NA,
        "board_serial": NA,
    }
    if IS_WINDOWS:
        data = powershell_json(_BIOS_SCRIPT, timeout=30)
        if isinstance(data, dict):
            info["vendor"] = clean_text(data.get("vendor"))
            info["version"] = tidy_version(clean_text(data.get("version")))
            info["release_date"] = clean_text(data.get("release_date"))
            info["serial"] = clean_text(data.get("serial"))
            smbios = clean_text(data.get("smbios"))
            info["smbios"] = NA if smbios in ("0.0", ".") else smbios
            info["board_vendor"] = clean_text(data.get("board_vendor"))
            info["board_model"] = clean_text(data.get("board_model"))
            info["board_serial"] = clean_text(data.get("board_serial"))
        # Boot mode and Secure Boot do not depend on PowerShell, so they are always tried.
        info["mode"] = detect_boot_mode()
        info["secure_boot"] = detect_secure_boot(info["mode"])
    elif platform.system() == "Linux":
        info["vendor"] = clean_text(_dmi("bios_vendor"))
        info["version"] = tidy_version(clean_text(_dmi("bios_version")))
        info["release_date"] = clean_text(_dmi("bios_date"))
        info["board_vendor"] = clean_text(_dmi("board_vendor"))
        info["board_model"] = clean_text(_dmi("board_name"))
        info["board_serial"] = clean_text(_dmi("board_serial"))
        info["serial"] = clean_text(_dmi("product_serial"))
        info["mode"] = "UEFI" if Path("/sys/firmware/efi").exists() else "Legacy BIOS"
        info["secure_boot"] = _secure_boot_text(None, info["mode"])
    return info
