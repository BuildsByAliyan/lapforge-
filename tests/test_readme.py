"""The README is the front door of the project: its images, links and command list must stay correct."""

import argparse
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lapforge import cli

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
ASSETS = ROOT / ".readme"


def _github_anchor(heading: str) -> str:
    """How GitHub turns a heading into a link target."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def _anchors() -> set[str]:
    found = {m.group(1) for m in re.finditer(r'<a id="([^"]+)"', README)}
    in_code = False
    for line in README.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and re.match(r"#{1,6} ", line):
            found.add(_github_anchor(line.lstrip("#")))
    return found


def test_every_image_in_the_readme_exists():
    images = re.findall(r'src="([^"]+)"', README) + re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", README)
    local = [i for i in images if not i.startswith(("http://", "https://"))]
    assert local, "the README should show images"
    missing = [i for i in local if not (ROOT / i).is_file()]
    assert not missing, missing


def test_every_local_link_exists():
    links = re.findall(r"(?<!!)\[[^\]]+\]\(([^)\s]+)\)", README)
    local = [link for link in links if not link.startswith(("http://", "https://", "#", "mailto:"))]
    missing = [link for link in local if not (ROOT / link.split("#")[0]).exists()]
    assert not missing, missing


def test_every_in_page_link_points_to_a_real_heading():
    wanted = {m.group(1) for m in re.finditer(r"\]\(#([^)\s]+)\)", README)}
    missing = sorted(wanted - _anchors())
    assert not missing, missing


def test_every_asset_is_used_valid_and_small():
    files = sorted(ASSETS.glob("*"))
    assert files
    for path in files:
        assert path.suffix == ".svg", f"{path.name}: only SVG files belong here"
        ET.parse(path)  # must be valid XML
        assert path.stat().st_size < 40_000, f"{path.name} is too big for a fast README"
        assert f".readme/{path.name}" in README, f"{path.name} is not used in the README"


def test_images_have_alt_text():
    tags = re.findall(r"<img [^>]+>", README)
    assert tags
    for tag in tags:
        assert "alt=" in tag, tag


def _command_names() -> list[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    raise AssertionError("no commands found")


@pytest.mark.parametrize("name", _command_names())
def test_every_command_is_explained(name):
    assert f"lapforge {name}" in README, f"the README does not explain `lapforge {name}`"


def test_every_option_of_every_command_is_mentioned():
    parser = cli.build_parser()
    missing = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for cmd, sub in action.choices.items():
                for opt in sub._actions:
                    for flag in opt.option_strings:
                        if flag.startswith("--") and flag != "--help" and f"`{flag}" not in README and f"{flag}" not in README:
                            missing.append(f"{cmd} {flag}")
    assert not missing, missing


def test_settings_table_matches_the_real_settings():
    from lapforge.config import SETTINGS

    for key in SETTINGS:
        assert f"`{key}`" in README, key
