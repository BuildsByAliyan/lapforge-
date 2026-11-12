"""LapForge optimizer: safe, undoable tweaks and read-only upgrade advice.

Every change is dry-run by default, writes a backup first and can be undone.
The upgrade advisor only reads hardware details and never changes anything.
"""

from __future__ import annotations

from . import restore_point, startup_manager, upgrade_advisor
from .restore_point import create_restore_point, list_restore_points, restore_status
from .startup_manager import (
    change_startup_item,
    disable_startup_item,
    enable_startup_item,
    find_item,
    list_changes,
    undo_change,
)
from .upgrade_advisor import get_upgrade_report

__all__ = [
    "change_startup_item",
    "create_restore_point",
    "disable_startup_item",
    "enable_startup_item",
    "find_item",
    "get_upgrade_report",
    "list_changes",
    "list_restore_points",
    "restore_point",
    "restore_status",
    "startup_manager",
    "undo_change",
    "upgrade_advisor",
]
