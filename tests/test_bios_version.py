"""Firmware sometimes puts stray spaces inside brackets: 'JBET51WW (1.16 )'."""

import pytest

from lapforge.collectors.bios import tidy_version


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("JBET51WW (1.16 )", "JBET51WW (1.16)"),
        ("V1.0  (  x  )", "V1.0 (x)"),
        ("[ a ]", "[a]"),
        ("F.20", "F.20"),
        ("N/A", "N/A"),
        ("  padded  ", "padded"),
    ],
)
def test_tidy_version(raw, expected):
    assert tidy_version(raw) == expected
