"""
Utility helpers extracted from legacy implementation.
"""

import datetime
import json
import logging
import os
import re
from pathlib import Path

from .constants import (
    CAD_PATTERN,
    AUTOCAD_PATTERN,
    SKIP_DIRS_LOWER,
)

logger = logging.getLogger(__name__)


def get_c_scan_dirs(config_mgr=None):
    """Get default/whitelisted C: scan directories."""
    if config_mgr:
        return config_mgr.get_enabled_c_paths()

    default_dirs = [
        os.path.expandvars(r"%TEMP%"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        os.path.expandvars(r"%USERPROFILE%\Documents"),
        os.path.expandvars(r"%USERPROFILE%\Downloads"),
    ]
    dirs = []
    for p in default_dirs:
        if p and os.path.isdir(p):
            p = os.path.normpath(p)
            if p not in dirs:
                dirs.append(p)
    return dirs


def is_in_allowed_paths(path_lower, allowed_paths_lower):
    """Check if a path is within allowed paths."""
    if not allowed_paths_lower:
        return False
    for ap in allowed_paths_lower:
        if path_lower.startswith(ap + "\\") or path_lower == ap:
            return True
    return False


def should_skip_path(path_lower, allowed_paths_lower=None):
    """Return True if path should be skipped."""
    if allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower):
        return False

    path_parts = path_lower.replace("/", "\\").split("\\")
    for part in path_parts:
        if part in SKIP_DIRS_LOWER:
            return True

    if "site-packages" in path_lower:
        return True
    if CAD_PATTERN.search(path_lower):
        return True
    if AUTOCAD_PATTERN.search(path_lower):
        return True
    if "tangent" in path_lower:
        return True

    return False


def should_skip_dir(name_lower, path_lower=None, allowed_paths_lower=None):
    """Return True if directory should be skipped."""
    if CAD_PATTERN.search(name_lower):
        return True
    if AUTOCAD_PATTERN.search(name_lower):
        return True
    if "tangent" in name_lower:
        return True

    if path_lower and allowed_paths_lower:
        if is_in_allowed_paths(path_lower, allowed_paths_lower):
            return False

    if name_lower in SKIP_DIRS_LOWER:
        return True

    return False


def format_size(size):
    """Format file size."""
    if size <= 0:
        return "-"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_time(timestamp):
    """Format epoch seconds to string."""
    if timestamp <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError) as e:
        logger.warning(f"时间戳格式化失败: {timestamp}, {e}")
        return "-"


def parse_search_scope(scope_str, get_drives_fn, config_mgr=None):
    """Parse search scope string into list of targets."""
    targets = []
    if "所有磁盘" in scope_str:
        for d in get_drives_fn():
            if d.upper().startswith("C:"):
                targets.extend(get_c_scan_dirs(config_mgr))
            else:
                norm = os.path.normpath(d).rstrip("\\/ ")
                targets.append(norm)
    else:
        s = scope_str.strip()
        if os.path.isdir(s):
            norm = os.path.normpath(s).rstrip("\\/ ")
            targets.append(norm)
        else:
            targets.append(s)
    return targets


def fuzzy_match(keyword, filename):
    """Fuzzy match score (higher is better)."""
    keyword = keyword.lower()
    filename_lower = filename.lower()

    if keyword in filename_lower:
        return 100

    ki = 0
    for char in filename_lower:
        if ki < len(keyword) and char == keyword[ki]:
            ki += 1
    if ki == len(keyword):
        return 60 + ki * 5

    words = re.split(r"[\s\-_.]", filename_lower)
    initials = "".join(w[0] for w in words if w)
    if keyword in initials:
        return 50

    return 0


def apply_theme(app, theme_name):
    """Apply light/dark theme to a Qt app."""
    if theme_name == "dark":
        app.setStyleSheet(
            """
            QMainWindow, QDialog { background-color: #2d2d2d; color: #ffffff; }
            QTreeWidget { background-color: #3d3d3d; color: #ffffff; alternate-background-color: #454545; }
            QTreeWidget::item:selected { background-color: #0078d4; }
            QLineEdit, QComboBox, QSpinBox { background-color: #3d3d3d; color: #ffffff; border: 1px solid #555; padding: 4px; }
            QPushButton { background-color: #4d4d4d; color: #ffffff; border: 1px solid #666; padding: 5px 10px; }
            QPushButton:hover { background-color: #5d5d5d; }
            QLabel { color: #ffffff; }
            QGroupBox { color: #ffffff; border: 1px solid #555; }
            QCheckBox, QRadioButton { color: #ffffff; }
            QMenu { background-color: #3d3d3d; color: #ffffff; }
            QMenu::item:selected { background-color: #0078d4; }
            QStatusBar { background-color: #2d2d2d; color: #aaaaaa; }
            QHeaderView::section { background-color: #3d3d3d; color: #ffffff; padding: 4px; border: 1px solid #555; }
            QScrollBar { background-color: #2d2d2d; }
        """
        )
    else:
        app.setStyleSheet(
            """
            QMainWindow, QDialog { background-color: #ffffff; }
            QTreeWidget { alternate-background-color: #f8f9fa; }
            QTreeWidget::item:selected { background-color: #0078d4; color: white; }
            QHeaderView::section { background-color: #f0f0f0; padding: 4px; border: 1px solid #dcdcdc; font-weight: bold; }
            QTreeWidget { border: 1px solid #dcdcdc; }
        """
        )


__all__ = [
    "get_c_scan_dirs",
    "is_in_allowed_paths",
    "should_skip_path",
    "should_skip_dir",
    "format_size",
    "format_time",
    "parse_search_scope",
    "fuzzy_match",
    "apply_theme",
]
