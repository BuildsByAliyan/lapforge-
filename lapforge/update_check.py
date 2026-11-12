"""Update checker: is a newer LapForge release on GitHub?

What it does, exactly:
  * ONE read-only HTTPS GET to the public GitHub "latest release" page of LapForge.
  * It sends nothing about you or your PC. The only identifying text is the
    user-agent "LapForge/<your version>" that every web request needs.
  * It never downloads or installs anything. It only tells you a new version exists.
  * Automatic checks (when you run plain `lapforge`) happen at most once a day and
    never show an error. `lapforge update` checks right now.

Turn it off:  lapforge config set check_updates off
          or  set the environment variable LAPFORGE_NO_UPDATE_CHECK=1
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import __app_name__, __version__
from .config import config_dir, load_config, write_json_atomic

REPO = "BuildsByAliyan/lapforge"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ENV_DISABLE = "LAPFORGE_NO_UPDATE_CHECK"

TIMEOUT_S = 4
CHECK_INTERVAL_S = 24 * 3600  # after a good check
RETRY_INTERVAL_S = 3600  # after a failed check
_MAX_BYTES = 1_000_000
_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------
def parse_version(text: Any) -> tuple[int, ...] | None:
    """'v3.1.0' -> (3, 1, 0). Pre-release tails ('-beta') are ignored. None if there is no number."""
    match = _VERSION_RE.search(str(text or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def is_newer(latest: Any, current: Any) -> bool:
    """True if `latest` is a higher version than `current` ('3.1' vs '3.1.0' counts as equal)."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def parse_release(payload: Any) -> dict[str, str] | None:
    """Pick the useful fields out of GitHub's release JSON. None if it is not a usable release."""
    if not isinstance(payload, dict) or payload.get("draft"):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    if parse_version(tag) is None:
        return None
    url = str(payload.get("html_url") or "")
    if not url.startswith("https://github.com/"):  # never show a link that is not GitHub
        url = RELEASES_PAGE
    return {
        "tag": tag,
        "version": ".".join(str(n) for n in parse_version(tag) or ()),
        "name": str(payload.get("name") or tag),
        "url": url,
        "published": str(payload.get("published_at") or "")[:10],
    }


# --------------------------------------------------------------------------
# Settings and cache
# --------------------------------------------------------------------------
def updates_disabled(config: dict[str, Any] | None = None) -> bool:
    """True if automatic checks are switched off (setting or environment variable)."""
    if os.environ.get(ENV_DISABLE, "").strip().lower() not in ("", "0", "false", "no", "off"):
        return True
    return not (config if config is not None else load_config()).get("check_updates", True)


def _state_path():
    return config_dir() / "update_state.json"


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        write_json_atomic(_state_path(), state)
    except OSError:
        pass  # a cache that cannot be written only means we ask again next time


# --------------------------------------------------------------------------
# The one network request
# --------------------------------------------------------------------------
def fetch_latest_release(timeout: float = TIMEOUT_S) -> Any:
    """GET the latest-release JSON from GitHub. Raises urllib/OS/ValueError errors on failure."""
    request = urllib.request.Request(
        API_URL,
        method="GET",
        headers={"User-Agent": f"{__app_name__}/{__version__}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec: fixed https URL
        return json.loads(response.read(_MAX_BYTES).decode("utf-8", errors="replace"))


def _result(status: str, current: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": status,  # update | current | none | disabled | error
        "current": current,
        "latest": "",
        "url": RELEASES_PAGE,
        "published": "",
        "note": "",
        "from_cache": False,
        "update_available": False,
    }
    base.update(extra)
    return base


def _from_release(release: dict[str, str], current: str, from_cache: bool = False) -> dict[str, Any]:
    newer = is_newer(release["version"], current)
    return _result(
        "update" if newer else "current",
        current,
        latest=release["version"],
        url=release["url"],
        published=release.get("published", ""),
        from_cache=from_cache,
        update_available=newer,
    )


def check_for_update(
    force: bool = False,
    current: str | None = None,
    config: dict[str, Any] | None = None,
    fetch: Callable[[], Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Look for a newer release. Never raises.

    force=False  automatic mode: does nothing if checks are off, and uses the saved
                 answer if the last check was less than a day ago.
    force=True   the user asked: check now (even if automatic checks are off).
    `fetch` and `now` are for tests.
    """
    current = current or __version__
    clock = now if now is not None else time.time()
    if not force and updates_disabled(config):
        return _result("disabled", current, note="Update checks are turned off.")

    state = _load_state()
    if not force and clock < float(state.get("next_check", 0) or 0):
        cached = parse_release({"tag_name": state.get("tag", ""), "html_url": state.get("url", ""), "published_at": state.get("published", "")})
        if cached:
            return _from_release(cached, current, from_cache=True)
        return _result("none", current, from_cache=True, note="No release found yet.")

    try:
        payload = (fetch or fetch_latest_release)()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # no release published (yet)
            _save_state({"next_check": clock + CHECK_INTERVAL_S, "tag": "", "url": "", "published": ""})
            return _result("none", current, note="No release has been published yet.")
        _save_state({**state, "next_check": clock + RETRY_INTERVAL_S})
        return _result("error", current, note=f"GitHub answered with error {exc.code}.")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        _save_state({**state, "next_check": clock + RETRY_INTERVAL_S})
        reason = getattr(exc, "reason", None) or exc
        return _result("error", current, note=f"Could not reach GitHub ({reason}).")

    release = parse_release(payload)
    if release is None:
        _save_state({**state, "next_check": clock + RETRY_INTERVAL_S})
        return _result("error", current, note="GitHub's answer was not a usable release.")
    _save_state(
        {
            "next_check": clock + CHECK_INTERVAL_S,
            "tag": release["tag"],
            "url": release["url"],
            "published": release["published"],
        }
    )
    return _from_release(release, current)


def update_notice(result: dict[str, Any]) -> str:
    """One line for the screen (empty if there is nothing worth saying)."""
    if result.get("status") == "update":
        return f"A newer {__app_name__} is available: {result['latest']} (you have {result['current']}). {result['url']}"
    return ""
