"""Tests for the netsh / ping parsers in lapforge.collectors.network.

The parsers are pure text-in, data-out functions, so these run on any OS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Let a plain `pytest -q` find the package even before `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lapforge.collectors import network  # noqa: E402
from lapforge.collectors.network import (  # noqa: E402
    latency_level,
    parse_key_values,
    parse_ping_output,
    parse_profile_detail,
    parse_wlan_interface,
    parse_wlan_profiles,
    password_state,
)

NA = "N/A"

PROFILES_TEXT = """
Profiles on interface Wi-Fi:

Group policy profiles (read only)
---------------------------------
    <None>

User profiles
-------------
    All User Profile     : HomeNet
    All User Profile     : Office WiFi: 5G
    All User Profile     : HomeNet
    All User Profile     : Cafe_Guest
"""

PROFILE_DETAIL_WITH_KEY = """
Profile HomeNet on interface Wi-Fi:
=======================================================================

Connectivity settings
---------------------
    SSID name              : "HomeNet"

Security settings
-----------------
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Authentication         : WPA2-Personal
    Cipher                 : GCMP
    Security key           : Present
    Key Content            : s3cret pass:word
"""

PROFILE_DETAIL_NO_KEY = """
Security settings
-----------------
    Authentication         : WPA3-Personal
    Cipher                 : CCMP
    Security key           : Present
"""

PROFILE_DETAIL_OPEN = """
Security settings
-----------------
    Authentication         : Open
    Cipher                 : None
    Security key           : Absent
"""

INTERFACE_TEXT = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    State                  : connected
    SSID                   : HomeNet
    BSSID                  : aa:bb:cc:dd:ee:ff
    Network type           : Infrastructure
    Radio type             : 802.11ax
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Channel                : 36
    Receive rate (Mbps)    : 866.7
    Transmit rate (Mbps)   : 573.5
    Signal                 : 87%
    Profile                : HomeNet
"""

PING_WINDOWS = """
Pinging 1.1.1.1 with 32 bytes of data:
Reply from 1.1.1.1: bytes=32 time=12ms TTL=58
Reply from 1.1.1.1: bytes=32 time=14ms TTL=58
Reply from 1.1.1.1: bytes=32 time=11ms TTL=58
Request timed out.

Ping statistics for 1.1.1.1:
    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
Approximate round trip times in milli-seconds:
    Minimum = 11ms, Maximum = 14ms, Average = 12ms
"""

PING_UNIX = """
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=9.81 ms

--- 8.8.8.8 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3004ms
rtt min/avg/max/mdev = 9.123/10.456/12.789/1.200 ms
"""


# --------------------------------------------------------------------------
# parse_key_values
# --------------------------------------------------------------------------
def test_key_values_basic_and_order():
    pairs = parse_key_values("  Name  :  Wi-Fi  \nState : connected\n")
    assert pairs == [("Name", "Wi-Fi"), ("State", "connected")]


def test_key_values_splits_on_first_colon_only():
    assert parse_key_values("Key Content : ab:cd:ef") == [("Key Content", "ab:cd:ef")]


def test_key_values_ignores_lines_without_colon_and_empty_text():
    assert parse_key_values("no separator here\n\n---") == []
    assert parse_key_values("") == []


def test_key_values_keeps_empty_value():
    assert parse_key_values("SSID : ") == [("SSID", "")]


# --------------------------------------------------------------------------
# parse_wlan_profiles
# --------------------------------------------------------------------------
def test_profiles_extracts_unique_names_in_order():
    assert parse_wlan_profiles(PROFILES_TEXT) == ["HomeNet", "Office WiFi: 5G", "Cafe_Guest"]


def test_profiles_ignores_text_before_the_interface_header():
    text = "    All User Profile     : Ghost\n" + PROFILES_TEXT
    assert "Ghost" not in parse_wlan_profiles(text)


def test_profiles_empty_or_no_wifi():
    assert parse_wlan_profiles("") == []
    assert parse_wlan_profiles("The Wireless AutoConfig Service (wlansvc) is not running.") == []


# --------------------------------------------------------------------------
# parse_profile_detail
# --------------------------------------------------------------------------
def test_profile_detail_takes_first_value_of_each_field():
    detail = parse_profile_detail(PROFILE_DETAIL_WITH_KEY)
    assert detail["authentication"] == "WPA2-Personal"
    assert detail["cipher"] == "CCMP"  # not the later GCMP line
    assert detail["security_key"] == "Present"
    assert detail["key_content"] == "s3cret pass:word"


def test_profile_detail_without_key_content():
    detail = parse_profile_detail(PROFILE_DETAIL_NO_KEY)
    assert detail["authentication"] == "WPA3-Personal"
    assert detail["key_content"] is None


def test_profile_detail_empty_text_uses_defaults():
    assert parse_profile_detail("") == {
        "authentication": NA,
        "cipher": NA,
        "security_key": None,
        "key_content": None,
    }


# --------------------------------------------------------------------------
# password_state (the --reveal rules)
# --------------------------------------------------------------------------
def test_password_hidden_without_reveal_even_when_key_is_present():
    detail = parse_profile_detail(PROFILE_DETAIL_WITH_KEY)
    assert password_state(detail, reveal=False) == ("hidden", None)


def test_password_revealed_only_with_flag():
    detail = parse_profile_detail(PROFILE_DETAIL_WITH_KEY)
    assert password_state(detail, reveal=True) == ("revealed", "s3cret pass:word")


def test_password_needs_admin_when_key_missing_with_reveal():
    detail = parse_profile_detail(PROFILE_DETAIL_NO_KEY)
    assert password_state(detail, reveal=True) == ("needs_admin", None)


@pytest.mark.parametrize("reveal", [True, False])
def test_open_network_has_no_password(reveal):
    detail = parse_profile_detail(PROFILE_DETAIL_OPEN)
    state, text = password_state(detail, reveal)
    assert state == "open"
    assert "no password" in text


def test_saved_wifi_not_available_off_windows(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", False)
    result = network.get_saved_wifi(reveal=True)
    assert result["available"] is False
    assert result["profiles"] == []
    assert result["note"]


def test_saved_wifi_only_asks_for_key_clear_when_revealing(monkeypatch):
    calls: list[list[str]] = []

    def fake_netsh(args):
        calls.append(args)
        if args == ["show", "profiles"]:
            return PROFILES_TEXT
        return PROFILE_DETAIL_WITH_KEY

    monkeypatch.setattr(network, "IS_WINDOWS", True)
    monkeypatch.setattr(network, "_netsh", fake_netsh)

    hidden = network.get_saved_wifi(reveal=False)
    assert not any("key=clear" in a for call in calls for a in call)
    assert all(p["password"] is None for p in hidden["profiles"])
    assert [p["ssid"] for p in hidden["profiles"]] == ["Cafe_Guest", "HomeNet", "Office WiFi: 5G"]

    shown = network.get_saved_wifi(reveal=True)
    assert any("key=clear" in a for call in calls for a in call)
    assert all(p["password"] == "s3cret pass:word" for p in shown["profiles"])


# --------------------------------------------------------------------------
# parse_wlan_interface
# --------------------------------------------------------------------------
def test_interface_connected():
    info = parse_wlan_interface(INTERFACE_TEXT)
    assert info["ssid"] == "HomeNet"
    assert info["state"] == "connected"
    assert info["connected"] is True
    assert info["radio"] == "802.11ax"
    assert info["authentication"] == "WPA2-Personal"
    assert info["channel"] == "36"
    assert info["rx_mbps"] == "866.7"
    assert info["tx_mbps"] == "573.5"
    assert info["signal"] == "87%"
    assert info["signal_percent"] == 87


def test_interface_disconnected():
    text = "    Name  : Wi-Fi\n    State : disconnected\n"
    info = parse_wlan_interface(text)
    assert info["connected"] is False
    assert info["ssid"] == NA
    assert info["signal_percent"] is None


def test_interface_empty_text():
    info = parse_wlan_interface("")
    assert info["connected"] is False
    assert info["signal_percent"] is None
    assert info["ssid"] == NA


def test_interface_does_not_match_bssid_as_ssid():
    info = parse_wlan_interface("    BSSID : aa:bb:cc:dd:ee:ff\n")
    assert info["ssid"] == NA


def test_current_wifi_not_available_off_windows(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", False)
    assert network.get_current_wifi() == {"available": False, "connected": False}


# --------------------------------------------------------------------------
# parse_ping_output / latency_level
# --------------------------------------------------------------------------
def test_ping_windows_output():
    out = parse_ping_output(PING_WINDOWS)
    assert (out["sent"], out["received"]) == (4, 3)
    assert out["loss_percent"] == 25.0
    assert (out["min_ms"], out["avg_ms"], out["max_ms"]) == (11.0, 12.0, 14.0)


def test_ping_unix_output():
    out = parse_ping_output(PING_UNIX)
    assert (out["sent"], out["received"]) == (4, 4)
    assert out["loss_percent"] == 0.0
    assert (out["min_ms"], out["avg_ms"], out["max_ms"]) == (9.123, 10.456, 12.789)


def test_ping_garbage_gives_all_none():
    out = parse_ping_output("Ping request could not find host nope. Please check the name.")
    assert all(value is None for value in out.values())


def test_ping_all_lost_has_loss_but_no_times():
    text = "    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),"
    out = parse_ping_output(text)
    assert out["received"] == 0
    assert out["loss_percent"] == 100.0
    assert out["avg_ms"] is None


@pytest.mark.parametrize(
    "avg, loss, level",
    [
        (None, None, "bad"),  # unreachable
        (None, 100.0, "bad"),
        (20, 0, "good"),
        (49.9, 0, "good"),
        (50, 0, "warn"),
        (20, 10, "warn"),
        (149, 0, "warn"),
        (150, 0, "bad"),
        (20, 25, "bad"),
        (20, None, "good"),
    ],
)
def test_latency_level(avg, loss, level):
    assert latency_level(avg, loss) == level


def test_ping_host_falls_back_to_tcp_when_icmp_fails(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", False)
    monkeypatch.setattr(network, "run_cmd", lambda *a, **k: "")
    fake = {"sent": 2, "received": 2, "loss_percent": 0.0, "min_ms": 5.0, "avg_ms": 6.0, "max_ms": 7.0}
    monkeypatch.setattr(network, "_tcp_probe", lambda *a, **k: dict(fake))
    result = network.ping_host("example.com", count=2)
    assert result["method"] == "TCP"
    assert result["host"] == "example.com"
    assert result["level"] == "good"


def test_ping_host_keeps_icmp_stats_when_they_work(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", False)
    monkeypatch.setattr(network, "run_cmd", lambda *a, **k: PING_UNIX)

    def no_tcp(*_a, **_k):
        raise AssertionError("TCP fallback should not run when ping works")

    monkeypatch.setattr(network, "_tcp_probe", no_tcp)
    result = network.ping_host("8.8.8.8")
    assert result["method"] == "ICMP"
    assert result["avg_ms"] == 10.456
    assert result["level"] == "good"
