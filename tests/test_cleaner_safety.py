"""Safety tests for the cleaner (Phase 25). They run on any system, no Windows needed.

Windows is faked: IS_WINDOWS is switched on and a fake `winreg` module reports a
personal folder that the user moved to another place.
"""

from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

import pytest

from lapforge import cli
from lapforge.cleaner import browser_cache, temp_files
from lapforge.cleaner.browser_cache import (
    browser_name,
    browser_targets,
    clean_browser_cache,
    is_cache_folder,
    open_browser_keys,
    open_browser_notes,
)
from lapforge.cleaner.temp_files import CleanTarget, check_root, clean_target, recycle_clear_script, scan_target

PERSONAL = ("Documents", "Pictures", "Videos", "Desktop", "Downloads", "Music")
HOURS = 3600


# --------------------------------------------------------------------------
# Fake Windows
# --------------------------------------------------------------------------
class _FakeKey:
    def __init__(self, values: dict[str, str]):
        self._values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_pc(tmp_path, monkeypatch):
    """A pretend Windows PC inside tmp_path. Returns a namespace with the useful folders."""
    home = tmp_path / "Users" / "alice"
    for name in PERSONAL:
        (home / name).mkdir(parents=True)
    moved = tmp_path / "D_drive" / "MyDocs"  # a Documents folder moved to another drive
    moved.mkdir(parents=True)
    temp = home / "AppData" / "Local" / "Temp"
    temp.mkdir(parents=True)
    local = home / "AppData" / "Local"

    moved_values = {"Personal": str(moved)}

    def query(key, name):
        if name in key._values:
            return key._values[name], 1
        raise OSError("not found")

    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        OpenKey=lambda *a, **k: _FakeKey(moved_values),
        QueryValueEx=query,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(temp_files, "IS_WINDOWS", True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    for var in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemDrive",
                "OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LAPFORGE_CONFIG_DIR", str(tmp_path / "config"))
    temp_files.protected_dirs.cache_clear()
    yield types.SimpleNamespace(home=home, temp=temp, local=local, moved=moved, root=tmp_path)
    temp_files.protected_dirs.cache_clear()


def _make_file(path: Path, age_hours: float = 5.0, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    stamp = time.time() - age_hours * HOURS
    os.utime(path, (stamp, stamp))
    return path


def _temp_target(root: Path, min_age_hours: float = 1.0) -> CleanTarget:
    return CleanTarget("user_temp", "User temp files", "test", ("quick",), roots=(str(root),), min_age_hours=min_age_hours)


# --------------------------------------------------------------------------
# Dry run and age
# --------------------------------------------------------------------------
def test_dry_run_is_the_default_and_deletes_nothing(fake_pc):
    old = _make_file(fake_pc.temp / "old.tmp")
    result = clean_target(_temp_target(fake_pc.temp))  # no dry_run argument
    assert result["dry_run"] is True
    assert result["deleted"] == 0
    assert old.exists()


def test_explicit_dry_run_deletes_nothing(fake_pc):
    old = _make_file(fake_pc.temp / "sub" / "old.tmp")
    result = clean_target(_temp_target(fake_pc.temp), dry_run=True)
    assert result["files"] == 1  # it is counted, not removed
    assert old.exists()


def test_real_clean_removes_only_files_older_than_the_minimum_age(fake_pc):
    old = _make_file(fake_pc.temp / "old.tmp", age_hours=5)
    fresh = _make_file(fake_pc.temp / "fresh.tmp", age_hours=0.1)
    result = clean_target(_temp_target(fake_pc.temp, min_age_hours=1.0), dry_run=False)
    assert result["deleted"] == 1
    assert not old.exists()
    assert fresh.exists()


# --------------------------------------------------------------------------
# Personal folders
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", PERSONAL)
def test_personal_folders_are_never_touched(fake_pc, name):
    keep = _make_file(fake_pc.home / name / "keep.txt")
    nested = _make_file(fake_pc.home / name / "deep" / "keep.txt")
    result = clean_target(_temp_target(fake_pc.home / name, min_age_hours=0), dry_run=False)
    assert result["refused"] is True
    assert result["deleted"] == 0
    assert keep.exists() and nested.exists()


def test_a_folder_inside_a_personal_folder_is_refused(fake_pc):
    keep = _make_file(fake_pc.home / "Downloads" / "installers" / "setup.exe")
    result = clean_target(_temp_target(fake_pc.home / "Downloads" / "installers", min_age_hours=0), dry_run=False)
    assert result["refused"] is True
    assert keep.exists()


def test_a_personal_folder_moved_to_another_drive_is_protected(fake_pc):
    keep = _make_file(fake_pc.moved / "thesis.docx")
    assert temp_files.is_protected(str(fake_pc.moved))
    result = clean_target(_temp_target(fake_pc.moved, min_age_hours=0), dry_run=False)
    assert result["refused"] is True
    assert keep.exists()


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------
def test_symbolic_links_are_not_followed(fake_pc):
    outside = fake_pc.root / "outside"
    keep = _make_file(outside / "important.txt")
    link = fake_pc.temp / "link_to_outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot create symbolic links")
    file_link = fake_pc.temp / "file_link.tmp"
    try:
        file_link.symlink_to(keep)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot create symbolic links")
    result = clean_target(_temp_target(fake_pc.temp, min_age_hours=0), dry_run=False)
    assert result["deleted"] == 0
    assert keep.exists()
    assert (outside / "important.txt").read_text() == "data"


# --------------------------------------------------------------------------
# A TEMP folder that points somewhere dangerous
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["Documents", "Downloads"])
def test_temp_pointing_to_a_personal_folder_is_refused(fake_pc, monkeypatch, name):
    keep = _make_file(fake_pc.home / name / "keep.txt")
    monkeypatch.setenv("TEMP", str(fake_pc.home / name))
    monkeypatch.setenv("TMP", str(fake_pc.home / name))
    target = next(t for t in temp_files.get_targets("quick") if t.key == "user_temp")
    result = clean_target(target, dry_run=False)
    assert result["refused"] is True
    assert keep.exists()


def test_temp_pointing_to_the_home_folder_is_refused(fake_pc, monkeypatch):
    keep = _make_file(fake_pc.home / "notes.txt")
    monkeypatch.setenv("TEMP", str(fake_pc.home))
    monkeypatch.setenv("TMP", str(fake_pc.home))
    target = next(t for t in temp_files.get_targets("quick") if t.key == "user_temp")
    result = clean_target(target, dry_run=False)
    assert result["refused"] is True
    assert "important" in result["note"].lower() or "personal" in result["note"].lower()
    assert keep.exists()


def test_temp_pointing_to_a_drive_root_is_refused(fake_pc, monkeypatch):
    root = Path(fake_pc.root.anchor)  # "/" here, "C:\\" on Windows
    assert check_root(str(root)) == "Refused: drive root"
    monkeypatch.setenv("TEMP", str(root))
    monkeypatch.setenv("TMP", str(root))
    target = next(t for t in temp_files.get_targets("quick") if t.key == "user_temp")
    result = scan_target(target)
    assert result["refused"] is True
    assert result["files"] == 0


def test_an_empty_folder_setting_is_refused():
    assert check_root("") == "No folder configured"


# --------------------------------------------------------------------------
# Browser cache allow-list
# --------------------------------------------------------------------------
USER_DATA = Path("Users", "alice", "AppData", "Local", "Google", "Chrome", "User Data")


@pytest.mark.parametrize(
    "tail",
    [
        ("Default", "Cache"),
        ("Default", "Code Cache"),
        ("Profile 1", "GPUCache"),
        ("Default", "Service Worker", "CacheStorage"),
    ],
)
def test_allowed_cache_folders_are_accepted(tail):
    assert is_cache_folder(str(USER_DATA.joinpath(*tail)))


@pytest.mark.parametrize(
    "tail",
    [
        ("Default", "Cookies"),
        ("Default", "Login Data"),
        ("Default", "History"),
        ("Default", "Network", "Cookies"),
        ("Default", "Local Storage"),
        ("Default", "Bookmarks"),
        ("Default", "Extensions"),
        ("Default", "Cache", "Login Data"),  # a personal name below a cache folder
        ("Cache",),  # not inside a profile
        ("Default",),  # a whole profile
    ],
)
def test_personal_data_paths_are_refused(tail):
    assert not is_cache_folder(str(USER_DATA.joinpath(*tail)))


def test_the_cleaner_refuses_a_target_that_is_not_on_the_allow_list(fake_pc):
    profile = fake_pc.local / "Google" / "Chrome" / "User Data" / "Default"
    cookies_dir = profile / "Network"
    keep = _make_file(cookies_dir / "Cookies")
    bad = CleanTarget("evil", "Evil", "test", ("browser",), roots=(str(cookies_dir),))
    result = clean_browser_cache(dry_run=False, targets=[bad])
    assert result["items"] == []
    assert keep.exists()


# --------------------------------------------------------------------------
# Open browsers are skipped (never closed)
# --------------------------------------------------------------------------
@pytest.fixture
def chrome_profile(fake_pc):
    cache = fake_pc.local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    old = _make_file(cache / "f_000001")
    cookies = _make_file(cache.parent / "Cookies")
    return types.SimpleNamespace(cache=cache, old=old, cookies=cookies)


def test_a_closed_browser_gets_a_cache_target(chrome_profile):
    targets = browser_targets(open_browsers=set())
    assert [t.label for t in targets] == ["Chrome cache (Default)"]
    assert targets[0].warning == ""


def test_an_open_browser_is_skipped_by_default(chrome_profile):
    assert browser_targets(open_browsers={"chrome"}) == []


def test_skip_open_false_lists_the_browser_with_a_warning(chrome_profile):
    targets = browser_targets(open_browsers={"chrome"}, skip_open=False)
    assert len(targets) == 1
    assert "is open" in targets[0].warning


def test_cleaning_with_an_open_browser_leaves_its_cache_alone(chrome_profile, monkeypatch):
    monkeypatch.setattr(browser_cache, "running_browsers", lambda: {"chrome"})
    result = clean_browser_cache(dry_run=False)
    assert result["items"] == []
    assert result["skipped_browsers"] == ["chrome"]
    assert chrome_profile.old.exists()


def test_cleaning_with_a_closed_browser_removes_only_cache_files(chrome_profile, monkeypatch):
    monkeypatch.setattr(browser_cache, "running_browsers", lambda: set())
    result = clean_browser_cache(dry_run=False)
    assert result["deleted"] == 1
    assert not chrome_profile.old.exists()
    assert chrome_profile.cookies.exists()


def test_open_browser_keys_needs_both_installed_and_running():
    assert open_browser_keys(["chrome", "edge"], {"edge", "firefox"}) == ["edge"]
    assert open_browser_keys([], {"chrome"}) == []
    assert open_browser_keys(["chrome"], set()) == []


def test_open_browser_note_uses_the_plain_sentence():
    assert open_browser_notes(["chrome"]) == ["Chrome is open. Close it and run Clean again to include its cache."]
    assert open_browser_notes(["edge"])[0].startswith("Edge is open.")
    assert open_browser_notes([]) == []
    assert browser_name("chrome") == "Chrome"


def _quiet_clean_run(monkeypatch, open_keys):
    monkeypatch.setattr(temp_files, "_measure_dns", lambda: None)
    monkeypatch.setattr(temp_files, "_measure_recycle", lambda: (0, 0, []))
    monkeypatch.setattr(cli, "detected_open_browsers", lambda: list(open_keys))
    monkeypatch.setattr(cli, "get_targets", temp_files.get_targets)


def test_clean_browser_dry_run_explains_why_a_browser_is_skipped(chrome_profile, fake_pc, monkeypatch, capsys):
    _make_file(fake_pc.temp / "junk.tmp")
    _quiet_clean_run(monkeypatch, ["chrome"])
    monkeypatch.setattr(browser_cache, "running_browsers", lambda: {"chrome"})
    code = cli.main(["clean", "--browser", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Chrome is open. Close it and run Clean again to include its cache." in out
    assert "Chrome cache" not in out  # no preview row for the skipped browser
    assert chrome_profile.old.exists()


def test_clean_browser_without_an_open_browser_shows_no_skip_note(chrome_profile, fake_pc, monkeypatch, capsys):
    _make_file(fake_pc.temp / "junk.tmp")
    _quiet_clean_run(monkeypatch, [])
    monkeypatch.setattr(browser_cache, "running_browsers", lambda: set())
    code = cli.main(["clean", "--browser", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "is open" not in out
    assert "Chrome cache" in out
    assert chrome_profile.old.exists()  # dry run


# --------------------------------------------------------------------------
# Recycle Bin: the action matches the preview
# --------------------------------------------------------------------------
def test_recycle_script_clears_exactly_the_given_drives():
    script = recycle_clear_script(["C:\\", "d:\\", "C:\\"])
    assert script.count("Clear-RecycleBin") == 2
    assert "-DriveLetter C " in script and "-DriveLetter D " in script


def test_recycle_script_is_empty_without_drive_letters():
    assert recycle_clear_script([]) == ""
    assert recycle_clear_script(["/", "/mnt/data"]) == ""


def test_recycle_warning_is_honest_about_what_is_removed():
    target = next(t for t in temp_files._all_targets() if t.key == "recycle_bin")
    assert "all fixed drives" in target.warning
    assert "Other users" in target.warning
