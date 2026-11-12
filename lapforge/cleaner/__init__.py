"""LapForge cleaner: preview first, clean only after you confirm (dry-run by default)."""

from __future__ import annotations

from .preview import build_preview, item_state, preview_all, preview_table
from .temp_files import (
    PROFILES,
    CleanTarget,
    check_root,
    clean_target,
    cleaner_supported,
    get_targets,
    is_protected,
    protected_dirs,
    scan_target,
)

__all__ = [
    "PROFILES",
    "CleanTarget",
    "build_preview",
    "check_root",
    "clean_target",
    "cleaner_supported",
    "get_targets",
    "is_protected",
    "item_state",
    "preview_all",
    "preview_table",
    "protected_dirs",
    "scan_target",
]
