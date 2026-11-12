"""Startup manager: turn programs that start with Windows off or on, with Undo.

How it stays safe
  * It uses Windows' own on/off switch (the same one Task Manager > Startup uses:
    the `StartupApproved` registry flag). No program, shortcut or registry entry
    is ever deleted or edited.
  * DRY-RUN BY DEFAULT: nothing changes unless dry_run=False.
  * Before every real change a backup file is written (the exact previous
    registry value) and, if asked, a System Restore point is made.
  * `undo_change()` puts the previous value back exactly. Every change can be undone.
  * Items for all users need an Administrator terminal. Windows Security items are refused.

Backups live in the LapForge settings folder, under `backups`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Protocol

from .. import __app_name__, __version__
from ..collectors.software import get_startup_apps
from ..config import config_dir, write_json_atomic
from ..utils import IS_WINDOWS, is_admin
from .restore_point import create_restore_point

try:  # winreg only exists on Windows
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

APPROVED_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
HIVES = ("HKCU", "HKLM")
_ENABLED_HEAD = b"\x02\x00\x00\x00"
_DISABLED_HEAD = b"\x03\x00\x00\x00"
_EPOCH_DIFF_100NS = 116444736000000000  # 1601-01-01 to 1970-01-01 in 100-nanosecond steps

# Programs that keep Windows safe. LapForge will not switch these off.
PROTECTED_HINTS = ("securityhealth", "windows defender", "windowsdefender", "windows security", "msascui", "sechealthui")


# --------------------------------------------------------------------------
# Registry access (a small interface, so the logic can be tested without Windows)
# --------------------------------------------------------------------------
class Registry(Protocol):
    def get(self, hive: str, subkey: str, value: str) -> bytes | None: ...
    def set(self, hive: str, subkey: str, value: str, data: bytes) -> None: ...
    def delete(self, hive: str, subkey: str, value: str) -> None: ...
    def has_run_value(self, hive: str, view: str, name: str) -> bool: ...


class WinRegistry:
    """The real Windows registry (StartupApproved flags only)."""

    @staticmethod
    def _root(hive: str) -> Any:
        return winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE

    def get(self, hive: str, subkey: str, value: str) -> bytes | None:
        try:
            with winreg.OpenKey(self._root(hive), f"{APPROVED_ROOT}\\{subkey}", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                data, kind = winreg.QueryValueEx(key, value)
        except OSError:
            return None
        return bytes(data) if kind == winreg.REG_BINARY and isinstance(data, (bytes, bytearray)) else None

    def set(self, hive: str, subkey: str, value: str, data: bytes) -> None:
        with winreg.CreateKeyEx(self._root(hive), f"{APPROVED_ROOT}\\{subkey}", 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, value, 0, winreg.REG_BINARY, data)

    def delete(self, hive: str, subkey: str, value: str) -> None:
        try:
            with winreg.OpenKey(self._root(hive), f"{APPROVED_ROOT}\\{subkey}", 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                winreg.DeleteValue(key, value)
        except FileNotFoundError:
            pass

    def has_run_value(self, hive: str, view: str, name: str) -> bool:
        flag = winreg.KEY_WOW64_64KEY if view == "64" else winreg.KEY_WOW64_32KEY
        try:
            with winreg.OpenKey(self._root(hive), RUN_KEY, 0, winreg.KEY_READ | flag) as key:
                winreg.QueryValueEx(key, name)
            return True
        except OSError:
            return False


class MemoryRegistry:
    """A fake registry kept in memory, for tests and dry runs on non-Windows systems."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], bytes] = {}
        self.run_values: set[tuple[str, str, str]] = set()

    def get(self, hive: str, subkey: str, value: str) -> bytes | None:
        return self.values.get((hive, subkey, value.lower()))

    def set(self, hive: str, subkey: str, value: str, data: bytes) -> None:
        self.values[(hive, subkey, value.lower())] = bytes(data)

    def delete(self, hive: str, subkey: str, value: str) -> None:
        self.values.pop((hive, subkey, value.lower()), None)

    def has_run_value(self, hive: str, view: str, name: str) -> bool:
        return (hive, view, name.lower()) in self.run_values


def default_registry() -> Registry | None:
    """The real registry on Windows, None elsewhere."""
    return WinRegistry() if IS_WINDOWS and winreg is not None else None


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def approval_blob(enabled: bool, when: datetime | None = None) -> bytes:
    """The 12-byte value Windows stores: 02 = on, 03 = off (plus the time it was switched off)."""
    if enabled:
        return _ENABLED_HEAD + b"\x00" * 8
    stamp = (when or datetime.now()).timestamp()
    filetime = int(stamp * 10_000_000) + _EPOCH_DIFF_100NS
    return _DISABLED_HEAD + filetime.to_bytes(8, "little", signed=False)


def decode_state(blob: bytes | None) -> bool:
    """True = the program starts with Windows. No flag at all means it is on."""
    if not blob:
        return True
    return blob[0] % 2 == 0


def is_protected(item: dict[str, Any]) -> bool:
    """Windows Security items are never switched off by LapForge."""
    text = f"{item.get('name', '')} {item.get('command', '')}".lower()
    return any(hint in text for hint in PROTECTED_HINTS)


def resolve_targets(item: dict[str, Any], registry: Registry) -> list[dict[str, str]]:
    """Which StartupApproved values control this item: [{"hive", "subkey", "value"}, ...]."""
    source = str(item.get("source", ""))
    name = str(item.get("name", ""))
    if source == "Registry (this user)":
        return [{"hive": "HKCU", "subkey": "Run", "value": name}]
    if source == "Registry (all users)":
        found = []
        if registry.has_run_value("HKLM", "64", name):
            found.append({"hive": "HKLM", "subkey": "Run", "value": name})
        if registry.has_run_value("HKLM", "32", name):
            found.append({"hive": "HKLM", "subkey": "Run32", "value": name})
        return found or [{"hive": "HKLM", "subkey": "Run", "value": name}]
    if source.startswith("Startup folder"):
        filename = PureWindowsPath(str(item.get("command", ""))).name
        if not filename:
            return []
        hive = "HKCU" if "this user" in source else "HKLM"
        return [{"hive": hive, "subkey": "StartupFolder", "value": filename}]
    return []


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32] or "item"


# --------------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------------
def backup_dir() -> Path:
    """Folder that holds the change backups (not created here)."""
    return config_dir() / "backups"


def _backup_path(change_id: str) -> Path:
    return backup_dir() / f"startup-{change_id}.json"


def list_changes(limit: int = 20) -> list[dict[str, Any]]:
    """Recorded changes, newest first: {"id", "action", "item", "created", "undone", "applied", "path"}."""
    folder = backup_dir()
    try:
        files = sorted(folder.glob("startup-*.json"), reverse=True)
    except OSError:
        return []
    changes: list[dict[str, Any]] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "id" not in data:
            continue
        changes.append(
            {
                "id": data["id"],
                "action": data.get("action", ""),
                "item": data.get("item", {}).get("name", ""),
                "source": data.get("item", {}).get("source", ""),
                "created": data.get("created", ""),
                "applied": bool(data.get("applied")),
                "undone": bool(data.get("undone_at")),
                "path": str(path),
            }
        )
    return changes[: max(1, limit)]


def _load_change(change_id: str | None) -> tuple[dict[str, Any] | None, Path | None, str]:
    """Find a backup. change_id=None means the newest change that can still be undone."""
    changes = list_changes(1000)
    if change_id is None:
        for change in changes:
            if change["applied"] and not change["undone"]:
                change_id = change["id"]
                break
        else:
            return None, None, "There is no change to undo."
    path = _backup_path(str(change_id))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, f"No backup found for change '{change_id}'."
    return data, path, ""


# --------------------------------------------------------------------------
# Finding the item
# --------------------------------------------------------------------------
def find_item(name: str, items: list[dict[str, Any]] | None = None, source: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """Pick one startup item by name (case-insensitive). Returns (item, "") or (None, reason)."""
    if items is None:
        report = get_startup_apps()
        if not report.get("available"):
            return None, report.get("note") or "Startup programs could not be read."
        items = report["items"]
    wanted = name.strip().lower()
    matches = [i for i in items if str(i.get("name", "")).lower() == wanted and (source is None or i.get("source") == source)]
    if not matches:
        close = sorted({str(i["name"]) for i in items if wanted and wanted in str(i.get("name", "")).lower()})[:5]
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        return None, f"No startup program named '{name}'.{hint}"
    if len(matches) > 1:
        sources = ", ".join(sorted({str(m.get("source")) for m in matches}))
        return None, f"'{name}' exists in more than one place ({sources}). Say which one you mean."
    return matches[0], ""


# --------------------------------------------------------------------------
# Changing
# --------------------------------------------------------------------------
def _result(action: str, dry_run: bool, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": False,
        "dry_run": dry_run,
        "action": action,
        "item": None,
        "changes": [],
        "needs_admin": False,
        "already": False,
        "backup": None,
        "change_id": None,
        "restore_point": None,
        "message": "",
        "error": "",
    }
    base.update(extra)
    return base


def change_startup_item(
    name: str,
    enable: bool,
    dry_run: bool = True,
    restore_point: bool = True,
    require_restore_point: bool = False,
    source: str | None = None,
    items: list[dict[str, Any]] | None = None,
    registry: Registry | None = None,
    restore_func: Callable[..., dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Switch one startup program on or off. DRY-RUN BY DEFAULT.

    A real run (dry_run=False): refuses protected items, checks Administrator rights when needed,
    makes a restore point (if restore_point; aborts if require_restore_point and it failed), writes the backup,
    applies the change, checks it, and rolls back if anything went wrong.

    Returns {"ok", "dry_run", "action", "item", "changes", "needs_admin", "already", "backup",
             "change_id", "restore_point", "message", "error"}.
    """
    action = "enable" if enable else "disable"
    reg = registry if registry is not None else default_registry()
    if reg is None:
        return _result(action, dry_run, error="Managing startup programs needs Windows (not available on this system).")

    item, problem = find_item(name, items, source)
    if item is None:
        return _result(action, dry_run, error=problem)
    summary = {k: item.get(k, "") for k in ("name", "source", "command")}
    if not enable and is_protected(item):
        return _result(action, dry_run, item=summary, error=f"'{item['name']}' is part of Windows Security. LapForge will not turn it off.")

    targets = resolve_targets(item, reg)
    if not targets:
        return _result(action, dry_run, item=summary, error=f"LapForge does not know how to switch '{item['name']}' ({item.get('source')}).")
    needs_admin = any(t["hive"] == "HKLM" for t in targets)
    current = [decode_state(reg.get(t["hive"], t["subkey"], t["value"])) for t in targets]
    if all(state == enable for state in current):
        word = "on" if enable else "off"
        return _result(action, dry_run, ok=True, item=summary, already=True, needs_admin=needs_admin, message=f"'{item['name']}' is already switched {word}. Nothing to change.")

    changes = [
        {**t, "previous": (reg.get(t["hive"], t["subkey"], t["value"]) or b"").hex() or None, "new": approval_blob(enable, now).hex()}
        for t in targets
    ]
    plan_text = f"Would switch '{item['name']}' {'on' if enable else 'off'} at startup."
    if dry_run:
        return _result(action, True, ok=True, item=summary, changes=changes, needs_admin=needs_admin, message="Dry run: " + plan_text[0].lower() + plan_text[1:])
    if needs_admin and not is_admin():
        return _result(action, False, item=summary, changes=changes, needs_admin=True, error="This item starts for all users, so it needs an Administrator terminal (right-click the terminal > Run as administrator).")

    # 1. Restore point (the safety net) -----------------------------------------------------------
    rp: dict[str, Any] | None = None
    if restore_point:
        maker = restore_func or create_restore_point
        rp = maker(f"LapForge: {action} startup item {item['name']}", dry_run=False)
        if require_restore_point and not rp.get("protected"):
            return _result(action, False, item=summary, changes=changes, needs_admin=needs_admin, restore_point=rp, error="No restore point could be made, so nothing was changed. " + str(rp.get("reason", "")))

    # 2. Backup, written BEFORE touching the registry -------------------------------------------
    stamp = (now or datetime.now())
    change_id = f"{stamp.strftime('%Y%m%d-%H%M%S')}-{slugify(item['name'])}"
    record = {
        "id": change_id,
        "tool": f"{__app_name__} {__version__}",
        "created": stamp.isoformat(timespec="seconds"),
        "action": action,
        "item": summary,
        "entries": changes,
        "restore_point": rp,
        "applied": False,
        "undone_at": None,
    }
    path = _backup_path(change_id)
    try:
        write_json_atomic(path, record)
    except OSError as exc:
        return _result(action, False, item=summary, changes=changes, needs_admin=needs_admin, restore_point=rp, error=f"Could not write the backup ({exc.strerror or exc}), so nothing was changed.")

    # 3. Apply, verify, roll back on any problem -----------------------------------------------
    done: list[dict[str, Any]] = []
    try:
        for entry in changes:
            reg.set(entry["hive"], entry["subkey"], entry["value"], bytes.fromhex(entry["new"]))
            done.append(entry)
            if decode_state(reg.get(entry["hive"], entry["subkey"], entry["value"])) != enable:
                raise OSError("Windows did not keep the new value")
    except Exception as exc:
        _restore_entries(reg, done)
        return _result(action, False, item=summary, changes=changes, needs_admin=needs_admin, backup=str(path), change_id=change_id, restore_point=rp, error=f"The change failed and was rolled back ({exc}). Nothing was changed.")

    record["applied"] = True
    try:
        write_json_atomic(path, record)
    except OSError:
        pass  # the change is live and the first backup already holds the previous values
    return _result(
        action,
        False,
        ok=True,
        item=summary,
        changes=changes,
        needs_admin=needs_admin,
        backup=str(path),
        change_id=change_id,
        restore_point=rp,
        message=f"'{item['name']}' is now {'on' if enable else 'off'} at startup. A backup was saved (change {change_id}), so this can be undone.",
    )


def _restore_entries(reg: Registry, entries: list[dict[str, Any]]) -> None:
    """Put the previous values back (delete the value if there was none before)."""
    for entry in entries:
        try:
            if entry.get("previous"):
                reg.set(entry["hive"], entry["subkey"], entry["value"], bytes.fromhex(entry["previous"]))
            else:
                reg.delete(entry["hive"], entry["subkey"], entry["value"])
        except Exception:
            continue


def disable_startup_item(name: str, dry_run: bool = True, **kwargs: Any) -> dict[str, Any]:
    """Turn a startup program off (see change_startup_item). Dry-run by default."""
    return change_startup_item(name, enable=False, dry_run=dry_run, **kwargs)


def enable_startup_item(name: str, dry_run: bool = True, **kwargs: Any) -> dict[str, Any]:
    """Turn a startup program back on (see change_startup_item). Dry-run by default."""
    return change_startup_item(name, enable=True, dry_run=dry_run, **kwargs)


def undo_change(change_id: str | None = None, dry_run: bool = False, registry: Registry | None = None) -> dict[str, Any]:
    """Undo a recorded change. change_id=None undoes the newest change that is still active.

    Returns {"ok", "dry_run", "change_id", "item", "action", "message", "error"}.
    """
    out: dict[str, Any] = {"ok": False, "dry_run": dry_run, "change_id": change_id, "item": "", "action": "undo", "message": "", "error": ""}
    reg = registry if registry is not None else default_registry()
    if reg is None:
        out["error"] = "Undo needs Windows (not available on this system)."
        return out
    data, path, problem = _load_change(change_id)
    if data is None or path is None:
        out["error"] = problem
        return out
    out["change_id"] = data["id"]
    out["item"] = data.get("item", {}).get("name", "")
    if data.get("undone_at"):
        out["error"] = f"Change '{data['id']}' was already undone on {data['undone_at']}."
        return out
    entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
    if not entries:
        out["error"] = "This backup has nothing to restore."
        return out
    if any(e.get("hive") == "HKLM" for e in entries) and not dry_run and not is_admin():
        out["error"] = "This change affects all users, so undoing it needs an Administrator terminal."
        return out
    if dry_run:
        out.update(ok=True, message=f"Dry run: would put '{out['item']}' back to how it was before {data.get('created', '')}.")
        return out

    _restore_entries(reg, entries)
    wrong = []
    for entry in entries:
        now_value = reg.get(entry["hive"], entry["subkey"], entry["value"])
        before = bytes.fromhex(entry["previous"]) if entry.get("previous") else None
        if now_value != before:
            wrong.append(entry["value"])
    if wrong:
        out["error"] = f"Could not fully restore: {', '.join(wrong)}. Open Task Manager > Startup to fix it by hand."
        return out
    data["undone_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        write_json_atomic(path, data)
    except OSError:
        pass
    out.update(ok=True, message=f"Undone: '{out['item']}' is back to how it was.")
    return out


__all__ = [
    "MemoryRegistry",
    "WinRegistry",
    "approval_blob",
    "backup_dir",
    "change_startup_item",
    "decode_state",
    "default_registry",
    "disable_startup_item",
    "enable_startup_item",
    "find_item",
    "is_protected",
    "list_changes",
    "resolve_targets",
    "undo_change",
]
