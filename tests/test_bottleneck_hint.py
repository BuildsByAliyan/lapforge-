"""A busy normal Windows task must never be called suspicious; unknown programs still get the careful advice."""

import pytest

from lapforge.collectors.bottleneck import is_windows_background_task, judge_bottleneck


def _group(name, cpu, rss=50 * 1024 * 1024):
    return {"name": name, "count": 1, "cpu": cpu, "rss": rss, "rss_text": "50.0 MB", "ram_percent": 1.0}


def _cpu_hint(name):
    metrics = {"cpu_percent": 100.0, "ram_percent": 30.0, "swap_percent": 1.0, "disk_mb_s": 0.1}
    result = judge_bottleneck(metrics, [_group(name, 90.0), _group("other", 5.0)])
    return next(f["hint"] for f in result["findings"] if f["resource"] == "CPU")


@pytest.mark.parametrize("name", ["CompatTelRunner", "MsMpEng", "TiWorker", "SearchIndexer", "svchost", "compattelrunner.exe"])
def test_normal_windows_tasks_are_not_called_malware(name):
    hint = _cpu_hint(name)
    assert "malware" not in hint
    assert "normal Windows background task" in hint
    assert "wait a few minutes" in hint


def test_unknown_programs_still_get_the_careful_advice():
    hint = _cpu_hint("mystery_tool")
    assert "mystery_tool is using the most" in hint
    assert "malware" in hint


def test_no_single_culprit_message_is_kept():
    metrics = {"cpu_percent": 100.0, "ram_percent": 30.0, "swap_percent": 1.0, "disk_mb_s": 0.1}
    result = judge_bottleneck(metrics, [])
    hint = next(f["hint"] for f in result["findings"] if f["resource"] == "CPU")
    assert "No single program stands out" in hint


def test_is_windows_background_task():
    assert is_windows_background_task("CompatTelRunner")
    assert is_windows_background_task("WmiPrvSE.exe")
    assert not is_windows_background_task("chrome")
