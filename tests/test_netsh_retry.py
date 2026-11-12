"""netsh output with names in other alphabets gets one more try in UTF-8; plain English output does not."""

import pytest

from lapforge.collectors import network


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", True)
    calls = []
    return calls


def _fake(calls, plain, utf8_text):
    def run(args, utf8):
        calls.append(utf8)
        return utf8_text if utf8 else plain

    return run


@pytest.mark.parametrize("text, garbled", [("Wi-Fi OK\n", False), ("SSID : ??????\n", True), ("SSID : d\u00e9\u00fc\n", True), ("a\ufffdb", True)])
def test_looks_garbled(text, garbled):
    assert network._looks_garbled(text) is garbled


def test_plain_output_is_not_run_twice(monkeypatch, windows):
    monkeypatch.setattr(network, "_netsh_once", _fake(windows, "SSID : Home\n", "never"))
    assert network._netsh(["show", "interfaces"]) == "SSID : Home\n"
    assert windows == [False]


def test_garbled_output_is_replaced_by_the_utf8_run(monkeypatch, windows):
    monkeypatch.setattr(network, "_netsh_once", _fake(windows, "SSID : ??????\n", "SSID : \u0627\u0644\u0645\u0646\u0632\u0644\n"))
    assert "\u0627\u0644\u0645\u0646\u0632\u0644" in network._netsh(["show", "interfaces"])
    assert windows == [False, True]


def test_failing_utf8_run_keeps_the_first_answer(monkeypatch, windows):
    def run(args, utf8):
        if utf8:
            raise OSError("no console")
        return "SSID : ??\n"

    monkeypatch.setattr(network, "_netsh_once", run)
    assert network._netsh(["show", "interfaces"]) == "SSID : ??\n"


def test_empty_utf8_run_keeps_the_first_answer(monkeypatch, windows):
    monkeypatch.setattr(network, "_netsh_once", _fake(windows, "SSID : ??\n", "  "))
    assert network._netsh(["show", "interfaces"]) == "SSID : ??\n"


def test_failing_first_run_returns_nothing(monkeypatch, windows):
    def run(args, utf8):
        raise OSError("netsh missing")

    monkeypatch.setattr(network, "_netsh_once", run)
    assert network._netsh(["show", "interfaces"]) == ""


def test_not_windows_returns_nothing(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", False)
    assert network._netsh(["show", "interfaces"]) == ""


# --------------------------------------------------------------------------
# netsh shows the CONNECTED network's name as Windows-1252 letters when it is not plain English
# --------------------------------------------------------------------------
def _as_windows_1252(text):
    """How Windows shows UTF-8 bytes as Windows-1252 (the five unused bytes become control characters)."""
    unused = (0x81, 0x8D, 0x8F, 0x90, 0x9D)
    return "".join(chr(b) if b in unused else bytes([b]).decode("cp1252") for b in text.encode("utf-8"))


@pytest.mark.parametrize("name", ["\U0001d670\U0001d67b\U0001d678", "\u0639\u0627\u0644\u06cc\u0627\u0646", "\u65e5\u672c\u8a9e", "\U0001f600 Home"])
def test_mojibake_name_is_repaired(name):
    assert network.repair_mojibake(_as_windows_1252(name)) == name


@pytest.mark.parametrize("name", ["Home", "Caf\u00e9", "M\u00fcller WiFi", "Pixel_8129", "\u0639\u0627\u0644\u06cc\u0627\u0646", "N/A", ""])
def test_normal_names_are_left_alone(name):
    assert network.repair_mojibake(name) == name


def test_interface_ssid_is_repaired_in_the_parsed_result():
    ssid = "\U0001d670\U0001d67b"
    text = f"    Name                   : Wi-Fi\n    State                  : connected\n    SSID                   : {_as_windows_1252(ssid)}\n    Signal                 : 91%\n"
    info = network.parse_wlan_interface(text)
    assert info["ssid"] == ssid
    assert info["connected"] is True
    assert info["signal_percent"] == 91


# --------------------------------------------------------------------------
# A WiFi name that ends with a space: Windows only finds the profile with that exact spelling
# --------------------------------------------------------------------------
LISTING = (
    "Profiles on interface Wi-Fi:\n\n"
    "Group policy profiles (read only)\n---------------------------------\n    <None>\n\n"
    "User profiles\n-------------\n"
    "    All User Profile     : Home\n"
    "    All User Profile     : \u0639\u0627\u0644\u06cc\u0627\u0646 \n"
)


def test_raw_parser_keeps_the_space_at_the_end():
    assert network.parse_wlan_profiles_raw(LISTING) == ["Home", "\u0639\u0627\u0644\u06cc\u0627\u0646 "]
    assert network.parse_wlan_profiles(LISTING) == ["Home", "\u0639\u0627\u0644\u06cc\u0627\u0646"]


def test_name_with_a_space_at_the_end_is_found_on_the_second_try(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", True)
    asked = []

    def fake(args):
        if args == ["show", "profiles"]:
            return LISTING
        name = args[2][len("name="):]
        asked.append(name)
        if name in ("Home", "\u0639\u0627\u0644\u06cc\u0627\u0646 "):
            return "    Authentication         : WPA2-Personal\n    Cipher                 : CCMP\n"
        return f'Profile "{name}" is not found on the system.\n'

    monkeypatch.setattr(network, "_netsh", fake)
    result = network.get_saved_wifi()
    by_name = {p["ssid"]: p for p in result["profiles"]}
    assert by_name["\u0639\u0627\u0644\u06cc\u0627\u0646"]["authentication"] == "WPA2-Personal"
    assert by_name["Home"]["authentication"] == "WPA2-Personal"
    assert asked.count("Home") == 1  # normal names need only one question
    assert asked[-2:] == ["\u0639\u0627\u0644\u06cc\u0627\u0646", "\u0639\u0627\u0644\u06cc\u0627\u0646 "]


def test_truly_missing_profile_still_says_na(monkeypatch):
    monkeypatch.setattr(network, "IS_WINDOWS", True)

    def fake(args):
        return LISTING if args == ["show", "profiles"] else 'Profile "x" is not found on the system.\n'

    monkeypatch.setattr(network, "_netsh", fake)
    assert all(p["authentication"] == "N/A" for p in network.get_saved_wifi()["profiles"])
