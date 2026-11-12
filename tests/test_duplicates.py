"""Tests for the Duplicate Finder and `lapforge duplicates` (Phase 22). No Windows needed."""

from __future__ import annotations

from pathlib import Path

import pytest

from lapforge import cli
from lapforge.cleaner import duplicates


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def tree(tmp_path):
    """Three copies of X (3000 B), two copies of Y (2000 B), one unique file the same size as X."""
    x, y = b"X" * 3000, b"Y" * 2000
    files = {
        "x1": _write(tmp_path / "one" / "x1.bin", x),
        "x2": _write(tmp_path / "two" / "x2.bin", x),
        "x3": _write(tmp_path / "two" / "deep" / "x3.bin", x),
        "y1": _write(tmp_path / "one" / "y1.bin", y),
        "y2": _write(tmp_path / "y2.bin", y),
        "z": _write(tmp_path / "z.bin", b"Z" * 3000),
        "small1": _write(tmp_path / "small1.txt", b"s" * 500),
        "small2": _write(tmp_path / "small2.txt", b"s" * 500),
    }
    return tmp_path, files


def _snapshot(files: dict[str, Path]) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in files.values()}


# --------------------------------------------------------------------------
# Finder
# --------------------------------------------------------------------------
def test_groups_and_wasted_bytes_are_correct(tree):
    root, _files = tree
    result = duplicates.find_duplicates([str(root)], min_size=1000)
    sizes = sorted((g["size"], g["count"]) for g in result["groups"])
    assert sizes == [(2000, 2), (3000, 3)]
    assert result["wasted_bytes"] == 3000 * 2 + 2000 * 1
    assert result["duplicate_files"] == 3
    # The biggest waste comes first.
    assert result["groups"][0]["size"] == 3000


def test_same_size_but_different_content_is_not_a_duplicate(tree):
    root, files = tree
    result = duplicates.find_duplicates([str(root)], min_size=1000)
    listed = {f["path"] for g in result["groups"] for f in g["files"]}
    assert str(files["z"]) not in listed
    assert str(files["x1"]) in listed


def test_nothing_is_deleted_or_changed(tree):
    root, files = tree
    before = _snapshot(files)
    duplicates.find_duplicates([str(root)], min_size=1)
    assert _snapshot(files) == before


def test_module_has_no_delete_function():
    names = [n for n in dir(duplicates) if not n.startswith("__")]
    assert not [n for n in names if "delete" in n.lower() or "remove" in n.lower()]


def test_small_files_are_ignored_by_min_size(tree):
    root, files = tree
    ignored = duplicates.find_duplicates([str(root)], min_size=1000)
    listed = {f["path"] for g in ignored["groups"] for f in g["files"]}
    assert str(files["small1"]) not in listed
    found = duplicates.find_duplicates([str(root)], min_size=1)
    listed = {f["path"] for g in found["groups"] for f in g["files"]}
    assert str(files["small1"]) in listed and str(files["small2"]) in listed


def test_drive_root_is_refused(tmp_path):
    root = Path(tmp_path).anchor
    assert "drive root" in duplicates.refuse_reason(root)
    result = duplicates.find_duplicates([root])
    assert result["roots"] == [] and result["groups"] == []
    assert result["refused"][0]["path"] == root


def test_windows_folder_is_refused(tmp_path, monkeypatch):
    windows = tmp_path / "FakeWindows"
    (windows / "System32").mkdir(parents=True)
    monkeypatch.setenv("SystemRoot", str(windows))
    assert "Windows / program folder" in duplicates.refuse_reason(str(windows / "System32"))
    assert duplicates.refuse_reason(str(tmp_path / "Other")) == ""


def test_missing_folder_is_reported(tmp_path):
    result = duplicates.find_duplicates([str(tmp_path / "nope")])
    assert result["refused"][0]["reason"] == "Folder not found"


def test_max_files_stops_the_scan(tree):
    root, _files = tree
    result = duplicates.find_duplicates([str(root)], min_size=1, max_files=2)
    assert result["truncated"] is True
    assert result["scanned_files"] == 2


# --------------------------------------------------------------------------
# Command
# --------------------------------------------------------------------------
def test_command_lists_groups_and_keeps_every_file(tree, capsys):
    root, files = tree
    before = _snapshot(files)
    assert cli.main(["duplicates", "--path", str(root), "--min-size", "0.001"]) == 0
    out = capsys.readouterr().out
    assert "REPORT ONLY" in out
    assert "Windows Explorer" in out
    assert "waste" in out
    assert _snapshot(files) == before


def test_command_min_size_is_in_megabytes(tree, capsys):
    root, _files = tree
    assert cli.main(["duplicates", "--path", str(root)]) == 0  # default 1 MB: files are smaller
    assert "No duplicates found" in capsys.readouterr().out


def test_command_path_can_be_repeated(tree, capsys):
    root, _files = tree
    args = ["duplicates", "--path", str(root / "one"), "--path", str(root / "two"), "--min-size", "0.001"]
    assert cli.main(args) == 0
    assert "REPORT ONLY" in capsys.readouterr().out


def test_command_refuses_drive_root_with_clear_reason(tmp_path, capsys):
    assert cli.main(["duplicates", "--path", Path(tmp_path).anchor]) == 1
    out = capsys.readouterr().out
    assert "drive root" in out
    assert "What to do" in out


def test_command_defaults_to_downloads_folder(tmp_path, monkeypatch, capsys):
    _write(tmp_path / "Downloads" / "a.bin", b"A" * 4000)
    _write(tmp_path / "Downloads" / "b.bin", b"A" * 4000)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert cli.main(["duplicates", "--min-size", "0.001"]) == 0
    assert "REPORT ONLY" in capsys.readouterr().out
    assert (tmp_path / "Downloads" / "a.bin").exists() and (tmp_path / "Downloads" / "b.bin").exists()


def test_command_accepts_relative_folder_names(tree, monkeypatch, capsys):
    root, _files = tree
    monkeypatch.chdir(root)
    assert cli.main(["duplicates", "--path", ".", "--min-size", "0.001"]) == 0
    out = capsys.readouterr().out
    assert "drive root" not in out
    assert "REPORT ONLY" in out


def test_command_reports_truncated_scan(tree, capsys):
    root, _files = tree
    assert cli.main(["duplicates", "--path", str(root), "--min-size", "0.001", "--max-files", "2"]) == 0
    assert "Stopped early" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [["--min-size", "0"], ["--min-size", "abc"], ["--max-files", "0"], ["--max-files", "x"]])
def test_command_rejects_bad_numbers(bad):
    with pytest.raises(SystemExit):
        cli.main(["duplicates", *bad])
