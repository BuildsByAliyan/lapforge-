"""Network collectors: interfaces, saved WiFi profiles, current WiFi, ping/latency.

Security notes
- WiFi passwords are only requested from Windows when `reveal=True`, and only for
  the computer LapForge is running on. They are never written to disk or logged.
- Nothing is sent anywhere except the ping / TCP probes of the `speed` command,
  which the user starts explicitly.
"""

from __future__ import annotations

import re
import socket
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psutil

from ..utils import IS_WINDOWS, NA, run_cmd

DEFAULT_HOSTS: list[tuple[str, str]] = [
    ("Cloudflare", "1.1.1.1"),
    ("Google DNS", "8.8.8.8"),
    ("Google", "google.com"),
]


# --------------------------------------------------------------------------
# Pure parsers (no Windows needed - these are what the unit tests exercise)
# --------------------------------------------------------------------------
def parse_key_values(text: str) -> list[tuple[str, str]]:
    """Split 'Key   : value' lines into ordered (key, value) pairs."""
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = re.match(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$", line)
        if match and match.group(1):
            pairs.append((match.group(1), match.group(2)))
    return pairs


def parse_wlan_profiles(text: str) -> list[str]:
    """Names of saved WiFi profiles from `netsh wlan show profiles`."""
    names: list[str] = []
    in_section = False
    for line in text.splitlines():
        if "profiles on interface" in line.lower():
            in_section = True
            continue
        if not in_section:
            continue
        match = re.match(r"^\s*[^:]+:\s*(.+?)\s*$", line)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def parse_wlan_profiles_raw(text: str) -> list[str]:
    """Like parse_wlan_profiles, but keeps spaces at the END of a name.

    A WiFi name can really end with a space. Windows then only finds the profile when asked with that space.
    """
    names: list[str] = []
    in_section = False
    for line in text.splitlines():
        if "profiles on interface" in line.lower():
            in_section = True
            continue
        if not in_section:
            continue
        match = re.match(r"^\s*[^:]+:\s*(\S.*)$", line)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def parse_profile_detail(text: str) -> dict[str, Any]:
    """Security details of one profile from `netsh wlan show profile name=...`."""
    detail: dict[str, Any] = {
        "authentication": NA,
        "cipher": NA,
        "security_key": None,
        "key_content": None,
    }
    for key, value in parse_key_values(text):
        low = key.lower()
        if low == "authentication" and detail["authentication"] == NA and value:
            detail["authentication"] = value
        elif low == "cipher" and detail["cipher"] == NA and value:
            detail["cipher"] = value
        elif low == "security key" and detail["security_key"] is None:
            detail["security_key"] = value
        elif low == "key content" and detail["key_content"] is None:
            detail["key_content"] = value
    return detail


def password_state(detail: dict[str, Any], reveal: bool) -> tuple[str, str | None]:
    """Decide what to show in the password column. Returns (state, text)."""
    key_type = (detail.get("security_key") or "").lower()
    if key_type == "absent":
        return "open", "(open network - no password)"
    if not reveal:
        return "hidden", None
    if detail.get("key_content"):
        return "revealed", detail["key_content"]
    return "needs_admin", None


def repair_mojibake(text: str) -> str:
    """Undo UTF-8 text that Windows showed as Windows-1252 letters ('ð™°' -> a bold letter).

    `netsh wlan show interfaces` prints the connected network's name this way when it is not plain English.
    Normal text is returned unchanged: it only changes when the whole text is exactly such a mix-up.
    """
    if not text or text.isascii():
        return text
    raw = bytearray()
    for ch in text:
        try:
            raw += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) < 256:  # the five bytes Windows-1252 leaves unused
                raw.append(ord(ch))
            else:
                return text
    try:
        fixed = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return text
    return fixed if fixed != text else text


def parse_wlan_interface(text: str) -> dict[str, Any]:
    """Current WiFi connection from `netsh wlan show interfaces`."""
    wanted = {
        "ssid": "ssid",
        "state": "state",
        "radio type": "radio",
        "authentication": "authentication",
        "channel": "channel",
        "signal": "signal",
        "receive rate (mbps)": "rx_mbps",
        "transmit rate (mbps)": "tx_mbps",
    }
    info: dict[str, Any] = {v: NA for v in wanted.values()}
    for key, value in parse_key_values(text):
        field = wanted.get(key.lower())
        if field and info[field] == NA and value:
            info[field] = value
    info["ssid"] = repair_mojibake(str(info["ssid"]))
    match = re.search(r"\d+", str(info["signal"]))
    info["signal_percent"] = int(match.group()) if match else None
    info["connected"] = str(info["state"]).lower() == "connected"
    return info


def parse_ping_output(text: str) -> dict[str, Any]:
    """Statistics from Windows (`ping -n`) or Unix (`ping -c`) output."""
    out: dict[str, Any] = {"sent": None, "received": None, "loss_percent": None,
                           "min_ms": None, "avg_ms": None, "max_ms": None}
    win = re.search(r"Sent = (\d+), Received = (\d+), Lost = (\d+)", text)
    unix = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", text)
    found = win or unix
    if found:
        out["sent"], out["received"] = int(found.group(1)), int(found.group(2))
        if out["sent"]:
            out["loss_percent"] = round(100 * (out["sent"] - out["received"]) / out["sent"], 1)
    win_t = re.search(r"Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms", text)
    unix_t = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)", text)
    if win_t:
        out["min_ms"], out["max_ms"], out["avg_ms"] = (float(win_t.group(i)) for i in (1, 2, 3))
    elif unix_t:
        out["min_ms"], out["avg_ms"], out["max_ms"] = (float(unix_t.group(i)) for i in (1, 2, 3))
    return out


def latency_level(avg_ms: float | None, loss_percent: float | None) -> str:
    """'good' / 'warn' / 'bad' for a connection to one host ('bad' when unreachable)."""
    if avg_ms is None:
        return "bad"
    if (loss_percent or 0) >= 25 or avg_ms >= 150:
        return "bad"
    if (loss_percent or 0) > 0 or avg_ms >= 50:
        return "warn"
    return "good"


# --------------------------------------------------------------------------
# netsh helper
# --------------------------------------------------------------------------
def _looks_garbled(text: str) -> bool:
    """True when netsh output may have lost letters: a '?' or odd characters (other alphabets in a WiFi name)."""
    return any(ch == "?" or ch == "\ufffd" or ord(ch) > 127 for ch in text)


def _netsh_once(args: list[str], utf8: bool) -> str:
    """One `netsh wlan ...` run.

    Normal run: its own hidden console, decoded with the OEM code page (fine for English names).
    utf8 run: shares our console, switched to UTF-8 for the moment, so names in any alphabet come through.
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    command = ["netsh", "wlan", *args]
    previous = kernel32.GetConsoleOutputCP() if utf8 else 0
    if utf8 and previous:  # we have a console: use it
        switched = previous != 65001 and bool(kernel32.SetConsoleOutputCP(65001))
        try:
            proc = subprocess.run(command, capture_output=True, timeout=20)
        finally:
            if switched:
                kernel32.SetConsoleOutputCP(previous)
        try:
            return proc.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return proc.stdout.decode(f"cp{previous}", errors="replace")
    proc = subprocess.run(
        command,
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
    )
    return proc.stdout.decode(f"cp{kernel32.GetOEMCP()}", errors="replace")


def _netsh(args: list[str]) -> str:
    """Run `netsh wlan ...`. Names in other alphabets get one more try in UTF-8; never raises."""
    if not IS_WINDOWS:
        return ""
    try:
        text = _netsh_once(args, utf8=False)
    except (OSError, subprocess.SubprocessError, LookupError, AttributeError):
        return ""
    if _looks_garbled(text):
        try:
            better = _netsh_once(args, utf8=True)
        except (OSError, subprocess.SubprocessError, LookupError, AttributeError):
            better = ""
        if better.strip():
            return better
    return text


# --------------------------------------------------------------------------
# Interfaces
# --------------------------------------------------------------------------
def get_network_interfaces() -> list[dict[str, Any]]:
    """Active network adapters. MAC/IP are raw here; callers redact them for reports."""
    interfaces: list[dict[str, Any]] = []
    try:
        addrs, stats = psutil.net_if_addrs(), psutil.net_if_stats()
    except Exception:
        return interfaces
    for name, entries in addrs.items():
        ipv4 = mac = NA
        for entry in entries:
            if entry.family == socket.AF_INET:
                ipv4 = entry.address
            elif getattr(entry.family, "name", "") == "AF_LINK" or entry.family == getattr(psutil, "AF_LINK", -1):
                mac = entry.address
        if ipv4.startswith("127."):
            continue
        stat = stats.get(name)
        interfaces.append(
            {
                "name": name,
                "up": bool(stat and stat.isup),
                "speed_mbps": stat.speed if stat and stat.speed else None,
                "ipv4": ipv4,
                "mac": mac,
            }
        )
    return interfaces


# --------------------------------------------------------------------------
# WiFi
# --------------------------------------------------------------------------
def get_current_wifi() -> dict[str, Any]:
    """Current WiFi connection (SSID, signal, radio, channel, link rates)."""
    if not IS_WINDOWS:
        return {"available": False, "connected": False}
    text = _netsh(["show", "interfaces"])
    if not text.strip():
        return {"available": False, "connected": False}
    info = parse_wlan_interface(text)
    info["available"] = True
    return info


def _profile_args(name: str, reveal: bool) -> list[str]:
    return ["show", "profile", f"name={name}"] + (["key=clear"] if reveal else [])


def get_saved_wifi(reveal: bool = False) -> dict[str, Any]:
    """Saved WiFi networks on THIS computer.

    Passwords are only requested from Windows when reveal=True. Returns
    {"available": bool, "profiles": [...], "note": str}.
    """
    result: dict[str, Any] = {"available": False, "profiles": [], "note": ""}
    if not IS_WINDOWS:
        result["note"] = "Saved WiFi networks can only be read on Windows."
        return result
    listing = _netsh(["show", "profiles"])
    names = parse_wlan_profiles(listing)
    exact = {name.strip(): name for name in parse_wlan_profiles_raw(listing)}  # name as Windows spells it
    if not names:
        result["note"] = "No saved WiFi networks found (or this PC has no WiFi adapter)."
        return result
    result["available"] = True
    for ssid in sorted(names, key=str.lower):
        detail = parse_profile_detail(_netsh(_profile_args(ssid, reveal)))
        if detail["authentication"] == NA and exact.get(ssid, ssid) != ssid:
            # Windows said "not found": the name probably ends with a space. Ask with the exact spelling.
            detail = parse_profile_detail(_netsh(_profile_args(exact[ssid], reveal)))
        state, text = password_state(detail, reveal)
        result["profiles"].append(
            {
                "ssid": ssid,
                "authentication": detail["authentication"],
                "cipher": detail["cipher"],
                "state": state,
                "password": text,
            }
        )
    return result


# --------------------------------------------------------------------------
# Ping / latency
# --------------------------------------------------------------------------
def _tcp_probe(host: str, count: int = 4, port: int = 443, timeout: float = 2.0) -> dict[str, Any]:
    """Fallback when ICMP ping is blocked: time TCP connections to port 443."""
    samples: list[float] = []
    for _ in range(count):
        start = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                samples.append((time.perf_counter() - start) * 1000)
        except OSError:
            pass
    lost = count - len(samples)
    return {
        "sent": count,
        "received": len(samples),
        "loss_percent": round(100 * lost / count, 1),
        "min_ms": round(min(samples), 1) if samples else None,
        "avg_ms": round(statistics.mean(samples), 1) if samples else None,
        "max_ms": round(max(samples), 1) if samples else None,
    }


def ping_host(host: str, count: int = 4, timeout_s: int = 2) -> dict[str, Any]:
    """Ping one host (system `ping`), falling back to TCP port-443 timing if ping is unusable."""
    if IS_WINDOWS:
        cmd = ["ping", "-n", str(count), "-w", str(timeout_s * 1000), host]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout_s), host]
    stats = parse_ping_output(run_cmd(cmd, timeout=count * (timeout_s + 1) + 5))
    method = "ICMP"
    if stats["received"] is None or stats["received"] == 0:
        tcp = _tcp_probe(host, count, timeout=timeout_s)
        if tcp["received"]:
            stats, method = tcp, "TCP"
    stats["host"] = host
    stats["method"] = method
    stats["level"] = latency_level(stats["avg_ms"], stats["loss_percent"])
    return stats


def measure_dns(name: str = "google.com") -> float | None:
    """Milliseconds to resolve a host name (None if it fails)."""
    start = time.perf_counter()
    try:
        socket.getaddrinfo(name, 443)
    except OSError:
        return None
    return round((time.perf_counter() - start) * 1000, 1)


def run_speed_test(hosts: list[tuple[str, str]] | None = None, count: int = 4) -> dict[str, Any]:
    """Ping all hosts in parallel and time one DNS lookup."""
    targets = hosts or DEFAULT_HOSTS
    with ThreadPoolExecutor(max_workers=len(targets) + 1) as pool:
        dns_future = pool.submit(measure_dns)
        futures = [(label, pool.submit(ping_host, host, count)) for label, host in targets]
        rows = []
        for label, future in futures:
            row = future.result()
            row["label"] = label
            rows.append(row)
        dns_ms = dns_future.result()
    return {"hosts": rows, "dns_ms": dns_ms}
