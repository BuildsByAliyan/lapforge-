"""Checks for the release pipeline and the version (Phase 32).

The workflow files are read as data (they run on GitHub). Needs PyYAML; skipped without it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import lapforge

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(workflow):
    # YAML reads the key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def run_text(job):
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def step_named(job, fragment):
    for step in job["steps"]:
        if fragment.lower() in str(step.get("name", "")).lower():
            return step
    raise AssertionError(f"no step named like {fragment!r}")


# --------------------------------------------------------------------------
# Version and package metadata
# --------------------------------------------------------------------------
def test_version_is_4_0_0():
    assert lapforge.__version__ == "4.0.0"
    assert re.fullmatch(r"\d+\.\d+\.\d+", lapforge.__version__)


def test_pyproject_keeps_the_console_script_and_reads_the_version_from_the_code():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'lapforge = "lapforge.cli:main"' in text
    assert 'version = { attr = "lapforge.__version__" }' in text
    assert 'dynamic = ["version"]' in text
    assert 'requires-python = ">=3.10"' in text


# --------------------------------------------------------------------------
# release.yml
# --------------------------------------------------------------------------
def test_release_runs_on_version_tags_only():
    on = triggers(load("release.yml"))
    assert set(on) == {"push"}
    assert set(on["push"]) == {"tags"}
    patterns = on["push"]["tags"]
    assert patterns and all(p.startswith("v") for p in patterns)


def test_release_can_write_contents_and_nothing_more():
    assert load("release.yml")["permissions"] == {"contents": "write"}


def test_release_builds_on_windows_through_app_run():
    job = load("release.yml")["jobs"]["release"]
    assert job["runs-on"] == "windows-latest"
    commands = run_text(job)
    assert "PyInstaller" in commands and "--onefile --console" in commands
    assert "--name LapForge" in commands
    assert "from lapforge.app import run" in commands and "sys.exit(run())" in commands


def test_release_checks_the_tag_runs_tests_and_smoke_tests_the_exe():
    job = load("release.yml")["jobs"]["release"]
    check = step_named(job, "tag matches")["run"]
    assert "lapforge.__version__" in check and "GITHUB_REF_NAME" in check
    assert "pytest" in step_named(job, "run tests")["run"]
    assert step_named(job, "smoke test")["run"].strip() == "dist\\LapForge.exe --version"
    names = [str(step.get("name", "")) for step in job["steps"]]
    assert names.index("Run tests") < names.index("Build LapForge.exe") < names.index("Smoke test the exe")


def test_release_writes_a_checksum_file_for_the_exe():
    run = step_named(load("release.yml")["jobs"]["release"], "checksum")["run"]
    assert "sha256sum dist/LapForge.exe" in run
    assert "dist/LapForge.exe.sha256" in run


def test_release_attaches_both_files_with_the_built_in_token():
    job = load("release.yml")["jobs"]["release"]
    step = step_named(job, "Create the GitHub Release")
    assert step["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    run = step["run"]
    assert "gh release create" in run
    assert "dist/LapForge.exe " in run and "dist/LapForge.exe.sha256" in run
    assert "--notes-file release-notes.md" in run
    assert "--verify-tag" in run


def test_release_notes_explain_the_download_and_the_smartscreen_screen():
    notes = step_named(load("release.yml")["jobs"]["release"], "release notes")["run"]
    assert "Download LapForge.exe and double-click it" in notes
    assert "Windows protected your PC" in notes
    assert "not code-signed" in notes
    assert "More info" in notes and "Run anyway" in notes
    assert "SHA-256" in notes and "$FILE_HASH" in notes
    assert "Get-FileHash" in notes


def test_release_notes_are_plain_english():
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    text.encode("ascii")  # no special characters that could break on any system


# --------------------------------------------------------------------------
# ci.yml
# --------------------------------------------------------------------------
def test_ci_keeps_the_test_matrix():
    ci = load("ci.yml")
    matrix = ci["jobs"]["test"]["strategy"]["matrix"]
    assert set(matrix["os"]) == {"windows-latest", "ubuntu-latest"}
    assert [str(v) for v in matrix["python"]] == ["3.10", "3.12", "3.13", "3.14"]
    assert "pytest" in run_text(ci["jobs"]["test"])


def test_ci_runs_the_three_smoke_commands():
    commands = [line.strip() for line in run_text(load("ci.yml")["jobs"]["test"]).splitlines()]
    for command in ("lapforge --version", "lapforge commands", "lapforge dashboard"):
        assert command in commands


def test_ci_builds_the_same_exe_the_release_builds():
    ci = load("ci.yml")
    job = ci["jobs"]["build-exe"]
    commands = run_text(job)
    assert "--name LapForge" in commands and "lapforge.app import run" in commands
    assert "lapforge.cli import main" not in commands
    assert "dist\\LapForge.exe --version" in commands
    upload = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")][0]
    assert upload["with"]["path"] == "dist/LapForge.exe"
    assert ci["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_workflows_are_valid_and_every_job_has_steps(name):
    workflow = load(name)
    assert workflow["name"]
    for job in workflow["jobs"].values():
        assert job["runs-on"] and job["steps"]
