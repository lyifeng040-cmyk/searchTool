"""原版头部：导入、全局常量、依赖探测。保持原样以保证等价。"""
"""
极速文件搜索 V42 增强版 - PySide6 UI
功能：MFT索引、FTS5全文搜索、实时监控、全局热键、系统托盘
"""

import os
import sys

os.environ["QT_LOGGING_RULES"] = "*.debug=false;*.warning=false"

import string
import platform
import threading
import time
import datetime
import struct
import subprocess
import queue
import concurrent.futures
from collections import deque
import re
from pathlib import Path
import shutil
import math
import json
import logging
import ctypes
import struct

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QLabel,
    QComboBox,
    QMenu,
    QStatusBar,
    QProgressBar,
    QDialog,
    QCheckBox,
    QListWidget,
    QMessageBox,
    QFileDialog,
    QFrame,
    QSystemTrayIcon,
    QHeaderView,
    QAbstractItemView,
    QGroupBox,
    QScrollArea,
    QTextEdit,
    QSpinBox,
    QRadioButton,
    QGridLayout,
    QInputDialog,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QEvent, QObject
from PySide6.QtGui import (
    QAction,
    QFont,
    QColor,
    QKeySequence,
    QShortcut,
    QPixmap,
    QPainter,
)
from PySide6.QtGui import (
    QAction, QFont, QColor, QKeySequence, QShortcut, QPixmap, QPainter, QIcon
)

# ==================== 日志配置 ====================
LOG_DIR = Path.home() / ".filesearch"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ==================== 系统常量 ====================
IS_WINDOWS = platform.system() == "Windows"
MFT_AVAILABLE = False

# ==================== Rust 核心引擎加载 ====================
HAS_RUST_ENGINE = False
RUST_ENGINE = None

if IS_WINDOWS:
    try:

        class ScanResult(ctypes.Structure):
            _fields_ = [
                ("data", ctypes.POINTER(ctypes.c_uint8)),
                ("data_len", ctypes.c_size_t),
                ("count", ctypes.c_size_t),
            ]

        # ★ FileInfo 移到这里，和 ScanResult 同级
        class FileInfo(ctypes.Structure):
            _fields_ = [
                ("size", ctypes.c_uint64),
                ("mtime", ctypes.c_double),
                ("exists", ctypes.c_uint8),
            ]

        possible_paths = [
            Path(__file__).parent / "file_scanner_engine.dll",
            Path.cwd() / "file_scanner_engine.dll",
        ]

        dll_path = None
        for p in possible_paths:
            if p.exists():
                dll_path = p
                break

        if dll_path:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(dll_path.parent.resolve()))

            RUST_ENGINE = ctypes.CDLL(str(dll_path))

            # ===== 扫描结果结构 =====
            RUST_ENGINE.scan_drive_packed.argtypes = [ctypes.c_uint16]
            RUST_ENGINE.scan_drive_packed.restype = ScanResult
            RUST_ENGINE.free_scan_result.argtypes = [ScanResult]
            RUST_ENGINE.free_scan_result.restype = None

            # ===== DIR_CACHE 持久化（V50）=====
            RUST_ENGINE.save_dir_cache.argtypes = [ctypes.c_uint16, ctypes.c_char_p, ctypes.c_size_t]
            RUST_ENGINE.save_dir_cache.restype = ctypes.c_int32

            RUST_ENGINE.load_dir_cache.argtypes = [ctypes.c_uint16, ctypes.c_char_p, ctypes.c_size_t]
            RUST_ENGINE.load_dir_cache.restype = ctypes.c_int32

            # ===== 版本信息 =====
            RUST_ENGINE.get_engine_version.argtypes = []
            RUST_ENGINE.get_engine_version.restype = ctypes.c_uint32

            # ===== 懒加载文件信息（FileInfo 已在上面定义）=====
            RUST_ENGINE.get_file_info.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
            RUST_ENGINE.get_file_info.restype = FileInfo

            RUST_ENGINE.get_file_info_batch.argtypes = [
                ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                ctypes.POINTER(FileInfo), ctypes.c_size_t
            ]
            RUST_ENGINE.get_file_info_batch.restype = ctypes.c_size_t

            HAS_RUST_ENGINE = True
            logger.info(f"✅ Rust 核心引擎加载成功: {dll_path}")
        else:
            logger.warning("⚠️ 未找到 file_scanner_engine.dll")

    except Exception as e:
        logger.warning(f"⚠️ Rust 引擎加载失败: {e}")
        HAS_RUST_ENGINE = False
# ==================== 依赖检查 ====================
try:
    import win32clipboard
    import win32con

    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    logger.warning("pywin32 未安装，部分功能不可用")

try:
    import send2trash

    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False
    logger.warning("send2trash 未安装，删除将直接删除而非进入回收站")

try:
    import apsw

    HAS_APSW = True
except ImportError:
    HAS_APSW = False
    import sqlite3

    logger.warning("apsw 未安装，使用 sqlite3")

# ==================== 过滤规则 ====================
CAD_PATTERN = re.compile(r"cad20(1[0-9]|2[0-4])", re.IGNORECASE)
AUTOCAD_PATTERN = re.compile(r"autocad_20(1[0-9]|2[0-5])", re.IGNORECASE)

SKIP_DIRS_LOWER = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "$recycle.bin",
    "system volume information",
    "appdata",
    "boot",
    "node_modules",
    ".git",
    "__pycache__",
    "site-packages",
    "sys",
    "recovery",
    "config.msi",
    "$windows.~bt",
    "$windows.~ws",
    "cache",
    "caches",
    "temp",
    "tmp",
    "logs",
    "log",
    ".vscode",
    ".idea",
    ".vs",
    "obj",
    "bin",
    "debug",
    "release",
    "packages",
    ".nuget",
    "bower_components",
}

SKIP_EXTS = {
    ".lsp",
    ".fas",
    ".lnk",
    ".html",
    ".htm",
    ".xml",
    ".ini",
    ".lsp_bak",
    ".cuix",
    ".arx",
    ".crx",
    ".fx",
    ".dbx",
    ".kid",
    ".ico",
    ".rz",
    ".dll",
    ".sys",
    ".tmp",
    ".log",
    ".dat",
    ".db",
    ".pdb",
    ".obj",
    ".pyc",
    ".class",
    ".cache",
    ".lock",
}

ARCHIVE_EXTS = {
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".iso",
    ".jar",
    ".cab",
    ".bz2",
    ".xz",
}


# ==================== 工具函数 ====================
def get_c_scan_dirs(config_mgr=None):
    """获取C盘扫描目录列表"""
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
    """检查路径是否在允许路径列表内"""
    if not allowed_paths_lower:
        return False
    for ap in allowed_paths_lower:
        if path_lower.startswith(ap + "\\") or path_lower == ap:
            return True
    return False


def should_skip_path(path_lower, allowed_paths_lower=None):
    """检查路径是否应该跳过"""
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
    """检查目录是否应该跳过"""
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
    """格式化文件大小"""
    if size <= 0:
        return "-"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_time(timestamp):
    """格式化时间戳"""
    if timestamp <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError) as e:
        logger.warning(f"时间戳格式化失败: {timestamp}, {e}")
        return "-"


def parse_search_scope(scope_str, get_drives_fn, config_mgr=None):
    """统一解析搜索范围"""
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
    """模糊匹配 - 返回匹配分数"""
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
    """应用主题到应用程序"""
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

# ==================== 配置管理器 ====================
