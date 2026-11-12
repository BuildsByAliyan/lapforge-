"""LapForge settings: a small JSON file on THIS computer.

Settings
  theme          "dark" (default) or "mono" (no colours)
  language       "en" (the only language right now; the setting is kept for later)
  check_updates  true/false - ask GitHub once a day if a newer LapForge exists
  show_tips      true/false - show a short tip after common commands (at most once per run)
  report_dir     folder for `lapforge report` ("" = the LapForge/Reports folder in your user folder)

File: %APPDATA%\\LapForge\\config.json on Windows, ~/.config/lapforge/config.json elsewhere.
Set LAPFORGE_CONFIG_DIR to use another folder. A broken or hand-edited file never
crashes LapForge: bad values fall back to the defaults.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .utils import IS_WINDOWS

THEME_CHOICES = ("dark", "mono")
LANGUAGE_CHOICES = ("en",)

SETTINGS: dict[str, dict[str, Any]] = {
    "theme": {"default": "dark", "choices": THEME_CHOICES, "help": "Colour theme: dark or mono (no colours)"},
    "language": {"default": "en", "choices": LANGUAGE_CHOICES, "help": "Display language (only 'en' for now)"},
    "check_updates": {"default": True, "choices": ("on", "off"), "help": "Check GitHub once a day for a newer version"},
    "report_dir": {"default": "", "choices": (), "help": "Folder for reports (empty = <your user folder>/LapForge/Reports)"},
    "show_tips": {"default": True, "choices": ("on", "off"), "help": "Show a short tip after common commands"},
}
DEFAULTS: dict[str, Any] = {key: meta["default"] for key, meta in SETTINGS.items()}

_TRUE = {"1", "true", "yes", "on", "y", "enable", "enabled"}
_FALSE = {"0", "false", "no", "off", "n", "disable", "disabled"}


# --------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------
def config_dir() -> Path:
    """Folder that holds LapForge's settings (not created here)."""
    override = os.environ.get("LAPFORGE_CONFIG_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if IS_WINDOWS and appdata:
        return Path(appdata) / "LapForge"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "lapforge"


def config_path() -> Path:
    return config_dir() / "config.json"


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON so a crash can never leave a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def parse_bool(raw: Any) -> bool | None:
    """'on' / 'yes' / 'true' / 1 -> True, 'off' / 'no' / 'false' / 0 -> False, anything else -> None."""
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def validate_value(key: str, raw: Any) -> tuple[Any, str]:
    """Check one setting. Returns (clean value, "") or (None, reason it was refused)."""
    if key not in SETTINGS:
        return None, f"Unknown setting '{key}'. Valid settings: {', '.join(SETTINGS)}"
    if key in ("theme", "language"):
        value = str(raw).strip().lower()
        choices = SETTINGS[key]["choices"]
        if value not in choices:
            return None, f"'{raw}' is not valid for {key}. Choose one of: {', '.join(choices)}"
        return value, ""
    if key in ("check_updates", "show_tips"):
        flag = parse_bool(raw)
        if flag is None:
            return None, f"'{raw}' is not valid for {key}. Use on or off"
        return flag, ""
    if key == "report_dir":
        text = str(raw).strip().strip('"')
        if not text:
            return "", ""
        path = Path(os.path.expandvars(os.path.expanduser(text)))
        if path.exists() and not path.is_dir():
            return None, f"'{text}' is a file, not a folder"
        return str(path if path.is_absolute() else path.resolve()), ""
    return None, f"Unknown setting '{key}'"


# --------------------------------------------------------------------------
# Load / save
# --------------------------------------------------------------------------
def load_config() -> dict[str, Any]:
    """All settings, with defaults for anything missing or invalid. Never raises."""
    settings = dict(DEFAULTS)
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(data, dict):
        return settings
    for key in DEFAULTS:
        if key in data:
            value, error = validate_value(key, data[key])
            if not error:
                settings[key] = value
    return settings


def save_config(settings: dict[str, Any]) -> Path:
    """Write the settings file (only known keys). Raises OSError if the folder cannot be written."""
    clean = {key: settings.get(key, DEFAULTS[key]) for key in DEFAULTS}
    path = config_path()
    write_json_atomic(path, clean)
    return path


def set_setting(key: str, raw: Any) -> dict[str, Any]:
    """Validate and save one setting.

    Returns {"ok", "key", "old", "new", "error", "path"}. `old` lets the caller show how to undo.
    """
    result: dict[str, Any] = {"ok": False, "key": key, "old": None, "new": None, "error": "", "path": str(config_path())}
    value, error = validate_value(key, raw)
    if error:
        result["error"] = error
        return result
    current = load_config()
    result["old"], result["new"] = current[key], value
    current[key] = value
    try:
        save_config(current)
    except OSError as exc:
        result["error"] = f"Could not save settings: {exc.strerror or exc}"
        return result
    result["ok"] = True
    return result


def reset_config() -> bool:
    """Go back to the defaults by removing LapForge's own settings file.

    Returns True if a file was removed, False if there was nothing to remove.
    """
    try:
        config_path().unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def display_value(value: Any) -> str:
    """Setting value as shown to people (True -> 'on', '' -> 'default')."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value) if value != "" else "(default: your user folder/LapForge/Reports)"


def describe_config() -> list[dict[str, Any]]:
    """Rows for the `config` command: key, current value, default, help."""
    current = load_config()
    return [
        {
            "key": key,
            "value": display_value(current[key]),
            "default": display_value(meta["default"]),
            "changed": current[key] != meta["default"],
            "help": meta["help"],
        }
        for key, meta in SETTINGS.items()
    ]
