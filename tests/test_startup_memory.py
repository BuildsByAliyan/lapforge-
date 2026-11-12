"""Several startup entries that start the same program must not repeat (or inflate) its memory."""

from lapforge.collectors.software import SHARED_MEMORY_TEXT, build_startup_items

MB = 1024 * 1024
EDGE = r"c:\program files (x86)\microsoft\edge\application\msedge.exe"


def _entry(name, command, enabled=True):
    return {"name": name, "command": command, "source": "Registry (this user)", "enabled": enabled}


def test_same_program_is_counted_once():
    raw = [_entry(f"MicrosoftEdgeAutoLaunch_{i}", f'"{EDGE}" --no-startup-window') for i in range(5)]
    items = build_startup_items(raw, {EDGE: 310 * MB}, {"msedge.exe": 310 * MB})
    with_memory = [i for i in items if i["memory"] is not None]
    assert len(with_memory) == 1
    assert with_memory[0]["memory"] == 310 * MB
    assert [i["memory_text"] for i in items].count(SHARED_MEMORY_TEXT) == 4
    assert sum(1 for i in items if i["level"] in ("bad", "warn")) == 1


def test_disabled_entry_does_not_claim_memory():
    raw = [_entry("Edge old", f'"{EDGE}"', enabled=False), _entry("Edge new", f'"{EDGE}"')]
    items = {i["name"]: i for i in build_startup_items(raw, {EDGE: 300 * MB}, {})}
    assert items["Edge old"]["memory"] is None
    assert items["Edge old"]["level"] == "muted"
    assert items["Edge new"]["memory"] == 300 * MB


def test_not_running_and_duplicates_of_the_same_entry():
    raw = [_entry("Tool", r'"C:\x\tool.exe"'), _entry("Tool", r'"C:\x\tool.exe"')]
    items = build_startup_items(raw, {}, {})
    assert len(items) == 1
    assert items[0]["memory_text"] == "Not running"
    assert items[0]["level"] == "good"


def test_different_programs_keep_their_own_memory():
    raw = [_entry("A", r'"C:\x\a.exe"'), _entry("B", "b.exe")]
    items = {i["name"]: i for i in build_startup_items(raw, {r"c:\x\a.exe": 5 * MB}, {"b.exe": 120 * MB})}
    assert items["A"]["memory"] == 5 * MB
    assert items["B"]["memory"] == 120 * MB
