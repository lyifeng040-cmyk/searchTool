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
class ConfigManager:
    """配置管理器 - 处理应用程序配置的保存和加载"""

    def __init__(self):
        self.config_dir = LOG_DIR
        self.config_file = self.config_dir / "config.json"
        self.config = self._load()

    def _load(self):
        """加载配置文件"""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"配置加载失败: {e}")
        return self._get_default_config()

    def _get_default_config(self):
        """获取默认配置"""
        return {
            "search_history": [],
            "favorites": [],
            "theme": "light",
            "c_scan_paths": {"paths": [], "initialized": False},
            "enable_global_hotkey": True,
            "minimize_to_tray": True,
        }

    def save(self):
        """保存配置到文件"""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"配置保存失败: {e}")

    def add_history(self, keyword):
        """添加搜索历史"""
        if not keyword:
            return
        history = self.config.get("search_history", [])
        if keyword in history:
            history.remove(keyword)
        history.insert(0, keyword)
        self.config["search_history"] = history[:20]
        self.save()

    def get_history(self):
        """获取搜索历史"""
        return self.config.get("search_history", [])

    def add_favorite(self, path, name=None):
        """添加收藏"""
        if not path:
            return
        favs = self.config.get("favorites", [])
        for f in favs:
            if f.get("path", "").lower() == path.lower():
                return
        name = name or os.path.basename(path) or path
        favs.append({"name": name, "path": path})
        self.config["favorites"] = favs
        self.save()

    def remove_favorite(self, path):
        """移除收藏"""
        favs = self.config.get("favorites", [])
        self.config["favorites"] = [
            f for f in favs if f.get("path", "").lower() != path.lower()
        ]
        self.save()

    def get_favorites(self):
        """获取收藏列表"""
        return self.config.get("favorites", [])

    def set_theme(self, theme):
        """设置主题"""
        self.config["theme"] = theme
        self.save()

    def get_theme(self):
        """获取主题"""
        return self.config.get("theme", "light")

    def get_c_scan_paths(self):
        """获取C盘扫描路径列表"""
        config = self.config.get("c_scan_paths", {})
        if not config.get("initialized", False):
            return self._get_default_c_paths()
        return config.get("paths", [])

    def _get_default_c_paths(self):
        """获取默认的C盘路径配置"""
        default_dirs = [
            os.path.expandvars(r"%TEMP%"),
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
            os.path.expandvars(r"%USERPROFILE%\Desktop"),
            os.path.expandvars(r"%USERPROFILE%\Documents"),
            os.path.expandvars(r"%USERPROFILE%\Downloads"),
        ]
        paths = []
        for p in default_dirs:
            if p and os.path.isdir(p):
                p = os.path.normpath(p)
                paths.append({"path": p, "enabled": True})
        return paths

    def set_c_scan_paths(self, paths):
        """设置C盘扫描路径列表"""
        self.config["c_scan_paths"] = {"paths": paths, "initialized": True}
        self.save()

    def reset_c_scan_paths(self):
        """重置为默认C盘路径"""
        default_paths = self._get_default_c_paths()
        self.set_c_scan_paths(default_paths)
        return default_paths

    def get_enabled_c_paths(self):
        """获取启用的C盘路径列表"""
        paths = self.get_c_scan_paths()
        return [
            p["path"]
            for p in paths
            if p.get("enabled", True) and os.path.isdir(p["path"])
        ]

    def get_hotkey_enabled(self):
        """获取热键启用状态"""
        return self.config.get("enable_global_hotkey", True)

    def set_hotkey_enabled(self, enabled):
        """设置热键启用状态"""
        self.config["enable_global_hotkey"] = enabled
        self.save()

    def get_tray_enabled(self):
        """获取托盘启用状态"""
        return self.config.get("minimize_to_tray", True)

    def set_tray_enabled(self, enabled):
        """设置托盘启用状态"""
        self.config["minimize_to_tray"] = enabled
        self.save()

# ==================== MFT/USN 模块 ====================


if IS_WINDOWS:
    import ctypes.wintypes as wintypes

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FSCTL_ENUM_USN_DATA = 0x000900B3
    FSCTL_QUERY_USN_JOURNAL = 0x000900F4
    FILE_ATTRIBUTE_DIRECTORY = 0x10

    class USN_JOURNAL_DATA_V0(ctypes.Structure):
        _fields_ = [
            ("UsnJournalID", ctypes.c_uint64),
            ("FirstUsn", ctypes.c_int64),
            ("NextUsn", ctypes.c_int64),
            ("LowestValidUsn", ctypes.c_int64),
            ("MaxUsn", ctypes.c_int64),
            ("MaximumSize", ctypes.c_uint64),
            ("AllocationDelta", ctypes.c_uint64),
        ]

    class USN_RECORD_V2(ctypes.Structure):
        _fields_ = [
            ("RecordLength", ctypes.c_uint32),
            ("MajorVersion", ctypes.c_uint16),
            ("MinorVersion", ctypes.c_uint16),
            ("FileReferenceNumber", ctypes.c_uint64),
            ("ParentFileReferenceNumber", ctypes.c_uint64),
            ("Usn", ctypes.c_int64),
            ("TimeStamp", ctypes.c_int64),
            ("Reason", ctypes.c_uint32),
            ("SourceInfo", ctypes.c_uint32),
            ("SecurityId", ctypes.c_uint32),
            ("FileAttributes", ctypes.c_uint32),
            ("FileNameLength", ctypes.c_uint16),
            ("FileNameOffset", ctypes.c_uint16),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    CreateFileW.restype = wintypes.HANDLE

    DeviceIoControl = kernel32.DeviceIoControl
    DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    DeviceIoControl.restype = wintypes.BOOL

    CloseHandle = kernel32.CloseHandle
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
        """MFT枚举文件"""
        global MFT_AVAILABLE

        if HAS_RUST_ENGINE:
            logger.info(f"🚀 使用 Rust 核心引擎扫描驱动器 {drive_letter}...")
            result = None
            try:
                result = RUST_ENGINE.scan_drive_packed(ord(drive_letter.upper()[0]))

                if not result.data or result.count == 0:
                    raise Exception("空数据")

                raw_data = ctypes.string_at(result.data, result.data_len)
                py_list = []
                off = 0
                n = len(raw_data)

                allowed_paths_lower = None
                if allowed_paths:
                    allowed_paths_lower = [p.lower().rstrip("\\") for p in allowed_paths]

                skipped_count = 0

                while off < n:
                    # 新格式（更快）：不再传 name_lower
                    # [is_dir:1][name_len:2][path_len:2][parent_len:2][ext_len:1][size:8][mtime:8][data...]
                    if off + 24 > n:
                        break

                    is_dir = raw_data[off]
                    name_len = int.from_bytes(raw_data[off + 1:off + 3], "little")
                    path_len = int.from_bytes(raw_data[off + 3:off + 5], "little")
                    parent_len = int.from_bytes(raw_data[off + 5:off + 7], "little")
                    ext_len = raw_data[off + 7]
                    size = int.from_bytes(raw_data[off + 8:off + 16], "little")
                    mtime = struct.unpack("<d", raw_data[off + 16:off + 24])[0]
                    off += 24

                    total_len = name_len + path_len + parent_len + ext_len
                    if off + total_len > n:
                        break

                    name = raw_data[off:off + name_len].decode("utf-8", "replace")
                    off += name_len

                    path = raw_data[off:off + path_len].decode("utf-8", "replace")
                    off += path_len

                    parent = raw_data[off:off + parent_len].decode("utf-8", "replace")
                    off += parent_len

                    ext = raw_data[off:off + ext_len].decode("utf-8", "replace") if ext_len else ""
                    off += ext_len

                    name_lower = name.lower()
                    path_lower = path.lower()

                    # 过滤逻辑
                    if allowed_paths_lower:
                        in_allowed = False
                        for ap in allowed_paths_lower:
                            if path_lower.startswith(ap + "\\") or path_lower == ap:
                                in_allowed = True
                                break
                        if not in_allowed:
                            skipped_count += 1
                            continue
                    else:
                        if should_skip_path(path_lower, None):
                            skipped_count += 1
                            continue
                        if is_dir:
                            if should_skip_dir(name_lower, path_lower, None):
                                skipped_count += 1
                                continue
                        else:
                            if ext in skip_exts:
                                skipped_count += 1
                                continue

                    # ★ 现在 size 和 mtime 已经从 Rust 获取，无需后续处理
                    py_list.append((name, name_lower, path, parent, ext, size, mtime, is_dir))

                logger.info(f"✅ Rust返回={result.count}, 跳过={skipped_count}, 保留={len(py_list)}")

                MFT_AVAILABLE = True
                return py_list

            except Exception as e:
                logger.error(f"Rust 引擎错误: {e}，回退到 Python")
                import traceback
                traceback.print_exc()
            finally:
                if result and result.data:
                    try:
                        RUST_ENGINE.free_scan_result(result)
                    except:
                        pass

        # Python MFT 实现
        return _enum_volume_files_mft_python(drive_letter, skip_dirs, skip_exts, allowed_paths)


    # ==================== 新的优化版函数（替换这里）====================
    def _batch_stat_files(
        py_list,
        only_missing=True,
        write_back_db=False,
        db_conn=None,
        db_lock=None,
    ):
        """
        批量获取文件大小和修改时间（增强版）
        - only_missing=True: 只补齐 size/mtime 为 0 的项（懒加载推荐）
        - write_back_db=True: 可选写回数据库（需要传 db_conn + db_lock）
        py_list item 格式要求: [name, name_lower, path, parent, ext, size, mtime, is_dir]
        """
        if not py_list:
            return

        files_to_stat = []
        for item in py_list:
            try:
                # item[7] == 0 表示文件，1 表示目录
                if item[7] != 0:
                    continue

                # 懒加载：只处理缺失的
                if only_missing and (item[5] != 0 or item[6] != 0):
                    continue

                files_to_stat.append(item)
            except Exception:
                continue

        if not files_to_stat:
            return

        total_files = len(files_to_stat)
        start_time = time.time()

        # Windows API
        GetFileAttributesExW = kernel32.GetFileAttributesExW
        GetFileAttributesExW.restype = wintypes.BOOL
        GetFileAttributesExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p]

        class WIN32_FILE_ATTRIBUTE_DATA(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
            ]

        EPOCH_DIFF = 116444736000000000

        def stat_worker(batch):
            data = WIN32_FILE_ATTRIBUTE_DATA()
            updates = []  # (size, mtime, full_path)

            for item in batch:
                try:
                    path = item[2]
                    if GetFileAttributesExW(path, 0, ctypes.byref(data)):
                        size = (data.nFileSizeHigh << 32) + data.nFileSizeLow
                        mtime_ft = (data.ftLastWriteTime.dwHighDateTime << 32) + data.ftLastWriteTime.dwLowDateTime
                        if mtime_ft > EPOCH_DIFF:
                            mtime = (mtime_ft - EPOCH_DIFF) / 10000000.0
                        else:
                            mtime = 0.0

                        item[5] = int(size)
                        item[6] = float(mtime)

                        if write_back_db:
                            updates.append((int(size), float(mtime), path))
                except Exception:
                    pass

            return updates

        # 线程数：不要太夸张，慢盘会被打爆
        if total_files < 200:
            num_workers = 4
        elif total_files < 2000:
            num_workers = 8
        else:
            num_workers = 16

        batch_size = max(50, (total_files + num_workers - 1) // num_workers)
        batches = [
            files_to_stat[i:i + batch_size]
            for i in range(0, total_files, batch_size)
        ]

        all_updates = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as ex:
            for ups in ex.map(stat_worker, batches):
                if ups:
                    all_updates.extend(ups)

        # 可选写回 DB
        if write_back_db and all_updates and db_conn is not None:
            try:
                if db_lock is not None:
                    with db_lock:
                        cur = db_conn.cursor()
                        cur.executemany(
                            "UPDATE files SET size=?, mtime=? WHERE full_path=?",
                            all_updates,
                        )
                        if not HAS_APSW:
                            db_conn.commit()
                else:
                    cur = db_conn.cursor()
                    cur.executemany(
                        "UPDATE files SET size=?, mtime=? WHERE full_path=?",
                        all_updates,
                    )
                    if not HAS_APSW:
                        db_conn.commit()
            except Exception as e:
                logger.debug(f"[stat回写] 写回数据库失败: {e}")

        elapsed = time.time() - start_time
        speed = total_files / elapsed if elapsed > 0 else 0
        logger.info(f"补齐完成: {total_files} 个文件, 耗时 {elapsed:.2f}s, 速度 {speed:.0f}/s")


    def _enum_volume_files_mft_python(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
        """Python MFT 实现"""
        global MFT_AVAILABLE
        
        logger.info(f"使用 Python MFT 实现扫描驱动器 {drive_letter}...")
        drive = drive_letter.rstrip(":").upper()
        root_path = f"{drive}:\\"

        volume_path = f"\\\\.\\{drive}:"
        h = CreateFileW(
            volume_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if h == INVALID_HANDLE_VALUE:
            error_code = ctypes.get_last_error()
            logger.error(f"打开卷失败 {drive}: 错误代码 {error_code}")
            raise OSError(f"打开卷失败: {error_code}")

        try:
            jd = USN_JOURNAL_DATA_V0()
            br = wintypes.DWORD()
            if not DeviceIoControl(
                h,
                FSCTL_QUERY_USN_JOURNAL,
                None,
                0,
                ctypes.byref(jd),
                ctypes.sizeof(jd),
                ctypes.byref(br),
                None,
            ):
                error_code = ctypes.get_last_error()
                logger.error(f"查询USN失败 {drive}: 错误代码 {error_code}")
                raise OSError(f"查询USN失败: {error_code}")

            MFT_AVAILABLE = True
            records = {}
            BUFFER_SIZE = 1024 * 1024
            buf = (ctypes.c_ubyte * BUFFER_SIZE)()

            class MFT_ENUM_DATA(ctypes.Structure):
                _pack_ = 8
                _fields_ = [
                    ("StartFileReferenceNumber", ctypes.c_uint64),
                    ("LowUsn", ctypes.c_int64),
                    ("HighUsn", ctypes.c_int64),
                ]

            med = MFT_ENUM_DATA()
            med.StartFileReferenceNumber = 0
            med.LowUsn = 0
            med.HighUsn = jd.NextUsn

            allowed_paths_lower = (
                [p.lower().rstrip("\\") for p in allowed_paths]
                if allowed_paths
                else None
            )

            total = 0
            start_time = time.time()

            while True:
                ctypes.set_last_error(0)
                ok = DeviceIoControl(
                    h,
                    FSCTL_ENUM_USN_DATA,
                    ctypes.byref(med),
                    ctypes.sizeof(med),
                    ctypes.byref(buf),
                    BUFFER_SIZE,
                    ctypes.byref(br),
                    None,
                )
                err = ctypes.get_last_error()
                returned = br.value

                if not ok:
                    if err == 38:
                        break
                    if err != 0:
                        logger.error(f"MFT枚举失败 {drive}: 错误代码 {err}")
                        raise OSError(f"枚举失败: {err}")
                    if returned <= 8:
                        break
                if returned <= 8:
                    break

                next_frn = ctypes.cast(
                    ctypes.byref(buf), ctypes.POINTER(ctypes.c_uint64)
                )[0]
                offset = 8
                batch_count = 0

                while offset < returned:
                    if offset + 4 > returned:
                        break
                    rec_len = ctypes.cast(
                        ctypes.byref(buf, offset), ctypes.POINTER(ctypes.c_uint32)
                    )[0]
                    if rec_len == 0 or offset + rec_len > returned:
                        break

                    if rec_len >= ctypes.sizeof(USN_RECORD_V2):
                        rec = ctypes.cast(
                            ctypes.byref(buf, offset), ctypes.POINTER(USN_RECORD_V2)
                        ).contents
                        name_off, name_len = rec.FileNameOffset, rec.FileNameLength
                        if name_len > 0 and offset + name_off + name_len <= returned:
                            filename = bytes(
                                buf[offset + name_off : offset + name_off + name_len]
                            ).decode("utf-16le", errors="replace")
                            if filename and filename[0] not in ("$", "."):
                                file_ref = rec.FileReferenceNumber & 0x0000FFFFFFFFFFFF
                                parent_ref = (
                                    rec.ParentFileReferenceNumber & 0x0000FFFFFFFFFFFF
                                )
                                is_dir = bool(
                                    rec.FileAttributes & FILE_ATTRIBUTE_DIRECTORY
                                )
                                records[file_ref] = (filename, parent_ref, is_dir)
                                batch_count += 1
                    offset += rec_len

                total += batch_count
                if total and total % 100000 < batch_count:
                    logger.info(
                        f"[MFT] {drive}: 已枚举 {total:,} 条, 用时 {time.time()-start_time:.1f}s"
                    )

                med.StartFileReferenceNumber = next_frn
                if batch_count == 0:
                    break

            logger.info(f"[MFT] {drive}: 枚举完成 {len(records):,} 条")

            # 构建路径
            result = _build_paths_from_records(
                records, root_path, drive, skip_exts, allowed_paths_lower
            )

            return result
        finally:
            CloseHandle(h)

    def _build_paths_from_records(
        records, root_path, drive, skip_exts, allowed_paths_lower
    ):
        """从MFT记录构建完整路径"""
        logger.info(f"[MFT] {drive}: 开始构建路径...")

        dirs = {}
        files = {}
        parent_to_children = {}

        for ref, (name, parent_ref, is_dir) in records.items():
            if is_dir:
                dirs[ref] = (name, parent_ref)
                if parent_ref not in parent_to_children:
                    parent_to_children[parent_ref] = []
                parent_to_children[parent_ref].append(ref)
            else:
                files[ref] = (name, parent_ref)

        path_cache = {5: root_path}
        q = deque([5])

        while q:
            parent_ref = q.popleft()
            parent_path = path_cache.get(parent_ref)
            if not parent_path:
                continue

            parent_path_lower = parent_path.lower()
            if should_skip_path(
                parent_path_lower, allowed_paths_lower
            ) or should_skip_dir(
                os.path.basename(parent_path_lower),
                parent_path_lower,
                allowed_paths_lower,
            ):
                continue

            if parent_ref in parent_to_children:
                for child_ref in parent_to_children[parent_ref]:
                    child_name, _ = dirs[child_ref]
                    child_path = os.path.join(parent_path, child_name)
                    path_cache[child_ref] = child_path
                    q.append(child_ref)

        logger.info(
            f"[MFT] {drive}: 目录路径构建完成，缓存了 {len(path_cache):,} 个有效目录。"
        )

        result = []

        # 添加目录
        for ref, (name, parent_ref) in dirs.items():
            full_path = path_cache.get(ref)
            if not full_path or full_path == root_path:
                continue
            parent_dir = path_cache.get(parent_ref, root_path)
            result.append([name, name.lower(), full_path, parent_dir, "", 0, 0, 1])

        # 添加文件
        for ref, (name, parent_ref) in files.items():
            parent_path = path_cache.get(parent_ref)
            if not parent_path:
                continue

            full_path = os.path.join(parent_path, name)

            if should_skip_path(full_path.lower(), allowed_paths_lower):
                continue

            ext = os.path.splitext(name)[1].lower()
            if ext in skip_exts:
                continue

            if allowed_paths_lower and not is_in_allowed_paths(
                full_path.lower(), allowed_paths_lower
            ):
                continue

            result.append([name, name.lower(), full_path, parent_path, ext, 0, 0, 0])

        logger.info(f"[MFT] {drive}: 路径拼接与过滤完成，总计 {len(result):,} 条。")

        # 批量获取文件信息
        _batch_stat_files(result)

        logger.info(f"[MFT] {drive}: 过滤后 {len(result):,} 条")
        return [tuple(item) for item in result]

else:

    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
        raise OSError("MFT仅Windows可用")

        # ==================== 索引管理器 ====================

class IndexManager(QObject):
    """索引管理器 - 管理文件索引数据库"""

    progress_signal = Signal(int, str)
    build_finished_signal = Signal()
    fts_finished_signal = Signal()

    def __init__(self, db_path=None, config_mgr=None):
        super().__init__()
        self.config_mgr = config_mgr
        if db_path is None:
            idx_dir = LOG_DIR
            idx_dir.mkdir(exist_ok=True)
            self.db_path = str(idx_dir / "index.db")
        else:
            self.db_path = db_path

        self.conn = None
        self.lock = threading.RLock()
        self.is_ready = False
        self.is_building = False
        self.file_count = 0
        self.last_build_time = None
        self.has_fts = False
        self.used_mft = False

        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        try:
            if HAS_APSW:
                self.conn = apsw.Connection(self.db_path)
            else:
                self.conn = sqlite3.connect(self.db_path, check_same_thread=False)

            cursor = self.conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-2000000")
            cursor.execute("PRAGMA temp_store=MEMORY")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL,
                    filename_lower TEXT NOT NULL,
                    full_path TEXT UNIQUE NOT NULL,
                    parent_dir TEXT NOT NULL,
                    extension TEXT,
                    size INTEGER DEFAULT 0,
                    mtime REAL DEFAULT 0,
                    is_dir INTEGER DEFAULT 0
                )
            """
            )

            # 创建FTS5表
            try:
                fts_exists = False
                for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'"
                ):
                    fts_exists = True
                    break
                if not fts_exists:
                    cursor.execute(
                        "CREATE VIRTUAL TABLE files_fts USING fts5(filename, content=files, content_rowid=id)"
                    )
                    cursor.execute(
                        """
                        CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                            INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename);
                        END
                    """
                    )
                    cursor.execute(
                        """
                        CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                            INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename);
                        END
                    """
                    )
                self.has_fts = True
                logger.info("✅ FTS5 已启用")
            except Exception as e:
                self.has_fts = False
                logger.warning(f"⚠️ FTS5 不可用: {e}")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_parent ON files(parent_dir)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ext ON files(extension)")
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )

            if not HAS_APSW:
                self.conn.commit()

            self._load_stats()
        except Exception as e:
            logger.error(f"❌ 数据库初始化错误: {e}")
            self.conn = None

    def _load_stats(self, preserve_mft=False):
        """加载统计信息"""
        if not self.conn:
            return
        try:
            with self.lock:
                cursor = self.conn.cursor()

                count_result = list(cursor.execute("SELECT COUNT(*) FROM files"))
                self.file_count = count_result[0][0] if count_result else 0

                time_row = list(
                    cursor.execute("SELECT value FROM meta WHERE key='build_time'")
                )
                if time_row and time_row[0][0]:
                    try:
                        self.last_build_time = float(time_row[0][0])
                    except (ValueError, TypeError):
                        self.last_build_time = None
                else:
                    self.last_build_time = None

                if not preserve_mft:
                    mft_row = list(
                        cursor.execute("SELECT value FROM meta WHERE key='used_mft'")
                    )
                    self.used_mft = bool(mft_row and mft_row[0][0] == "1")

            self.is_ready = self.file_count > 0
        except Exception as e:
            logger.error(f"加载统计信息失败: {e}")
            self.file_count = 0
            self.is_ready = False

    def reload_stats(self):
        """重新加载统计信息"""
        if not self.is_building:
            self._load_stats(preserve_mft=True)

    def force_reload_stats(self):
        """强制重新加载统计信息"""
        self._load_stats(preserve_mft=True)

    def close(self):
        """关闭数据库连接"""
        with self.lock:
            if self.conn:
                try:
                    self.conn.close()
                    logger.info("数据库连接已关闭")
                except Exception as e:
                    logger.warning(f"关闭数据库连接失败: {e}")
                finally:
                    self.conn = None

    def search(self, keywords, scope_targets, limit=50000):
        """搜索文件（修复 scope 匹配：盘符/路径统一标准化）"""
        if not self.conn or not self.is_ready:
            return None
        try:
            with self.lock:
                cursor = self.conn.cursor()

                # 1) 数据库先做 LIKE 过滤（小写列）
                wheres = " AND ".join(["filename_lower LIKE ?"] * len(keywords))
                sql = f"""
                    SELECT filename, full_path, size, mtime, is_dir
                    FROM files
                    WHERE {wheres}
                    LIMIT ?
                """
                params = tuple([f"%{kw}%" for kw in keywords] + [limit])
                raw_results = list(cursor.execute(sql, params))

                # 2) scope 标准化：拆成 drives + paths
                scope_drives = set()
                scope_paths = []

                if scope_targets:
                    for t in scope_targets:
                        t_norm = os.path.normpath(t).lower().rstrip("\\")
                        drv = Path(t_norm).drive.lower()
                        if drv and (t_norm == drv or t_norm == drv + "\\"):
                            scope_drives.add(drv)
                        else:
                            scope_paths.append(t_norm)

                filtered = []
                for fn, fp, sz, mt, is_dir in raw_results:
                    path_norm = os.path.normpath(fp)
                    path_lower = path_norm.lower()
                    path_drive = Path(path_lower).drive.lower()

                    # 3) scope 过滤
                    if scope_targets:
                        ok = False

                        if scope_drives and path_drive in scope_drives:
                            ok = True

                        if not ok and scope_paths:
                            for p in scope_paths:
                                if path_lower == p or path_lower.startswith(p + "\\"):
                                    ok = True
                                    break

                        if not ok:
                            continue

                    # 4) 全局 skip 过滤
                    if should_skip_path(path_lower):
                        continue

                    name_lower = fn.lower()
                    if is_dir:
                        if should_skip_dir(name_lower, path_lower):
                            continue
                    else:
                        if os.path.splitext(name_lower)[1] in SKIP_EXTS:
                            continue

                    filtered.append((fn, fp, sz, mt, is_dir))

                return filtered

        except Exception as e:
            logger.error(f"搜索错误: {e}")
            return None

    def _search_like(self, cursor, keywords, limit):
        """LIKE 查询（回退方案）"""
        wheres = " AND ".join(["filename_lower LIKE ?"] * len(keywords))
        sql = f"""
            SELECT filename, full_path, size, mtime, is_dir
            FROM files
            WHERE {wheres}
            LIMIT ?
        """
        params = tuple([f"%{kw}%" for kw in keywords] + [limit])
        return list(cursor.execute(sql, params))

    def get_stats(self):
        """获取索引统计信息"""
        self._load_stats(preserve_mft=True)
        return {
            "count": self.file_count,
            "ready": self.is_ready,
            "building": self.is_building,
            "time": self.last_build_time,
            "path": self.db_path,
            "has_fts": self.has_fts,
            "used_mft": self.used_mft,
        }

    def build_index(self, drives, stop_fn=None):
        """构建索引"""
        global MFT_AVAILABLE
        if not self.conn or self.is_building:
            return

        self.is_building = True
        self.is_ready = False
        self.used_mft = False
        MFT_AVAILABLE = False
        build_start = time.time()

        try:
            logger.info("🚀 开始构建索引...")
            self.progress_signal.emit(0, "阶段1/5: 清理旧数据...")

            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("DROP TRIGGER IF EXISTS files_ai")
                cursor.execute("DROP TRIGGER IF EXISTS files_ad")
                cursor.execute("DROP TABLE IF EXISTS files_fts")
                cursor.execute("DROP TABLE IF EXISTS files")
                cursor.execute(
                    """
                    CREATE TABLE files (
                        id INTEGER PRIMARY KEY,
                        filename TEXT NOT NULL,
                        filename_lower TEXT NOT NULL,
                        full_path TEXT UNIQUE NOT NULL,
                        parent_dir TEXT NOT NULL,
                        extension TEXT,
                        size INTEGER DEFAULT 0,
                        mtime REAL DEFAULT 0,
                        is_dir INTEGER DEFAULT 0
                    )
                """
                )
                if not HAS_APSW:
                    self.conn.commit()
                self.has_fts = False
                self.file_count = 0

            logger.info(f"✅ 阶段1完成: {time.time() - build_start:.2f}s")

            # 阶段2: MFT扫描
            self.progress_signal.emit(0, "阶段2/5: MFT扫描...")
            all_drives = [d.upper().rstrip(":\\") for d in drives if os.path.exists(d)]
            c_allowed_paths = get_c_scan_dirs(self.config_mgr)
            all_data = []
            failed_drives = []

            if all_drives and IS_WINDOWS:
                data_lock = threading.Lock()

                def scan_one(drv):
                    try:
                        allowed = c_allowed_paths if drv == "C" else None
                        data = enum_volume_files_mft(
                            drv, SKIP_DIRS_LOWER, SKIP_EXTS, allowed_paths=allowed
                        )
                        with data_lock:
                            all_data.extend(data)
                        return drv, len(data)
                    except Exception as e:
                        logger.error(f"扫描驱动器 {drv} 失败: {e}")
                        return drv, -1

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(len(all_drives), 4)
                ) as ex:
                    futures = [ex.submit(scan_one, d) for d in all_drives]
                    for future in concurrent.futures.as_completed(futures):
                        if stop_fn and stop_fn():
                            break
                        drv, result = future.result()
                        if result < 0:
                            failed_drives.append(drv)
                        self.progress_signal.emit(
                            len(all_data),
                            f"MFT {drv}: {result if result >= 0 else '失败'}",
                        )

                if all_data:
                    self.used_mft = True

            logger.info(
                f"✅ 阶段2完成: {time.time() - build_start:.2f}s, 扫描到 {len(all_data):,} 条"
            )

            # 阶段3: 写入数据库（Rust 已获取文件大小，无需额外处理）
            if all_data:
                self.progress_signal.emit(len(all_data), "阶段3/5: 写入数据库...")
                write_start = time.time()

                with self.lock:
                    cursor = self.conn.cursor()

                    # 极限优化配置
                    cursor.execute("PRAGMA synchronous=OFF")
                    cursor.execute("PRAGMA journal_mode=MEMORY")
                    cursor.execute("PRAGMA locking_mode=EXCLUSIVE")
                    cursor.execute("PRAGMA temp_store=MEMORY")
                    cursor.execute("PRAGMA cache_size=-500000")
                    cursor.execute("PRAGMA mmap_size=268435456")

                    # 单事务批量写入
                    if HAS_APSW:
                        with self.conn:
                            cursor.executemany(
                                "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                                all_data
                            )
                    else:
                        cursor.executemany(
                            "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                            all_data
                        )
                        self.conn.commit()

                self.file_count = len(all_data)
                write_time = time.time() - write_start
                logger.info(f"✅ 阶段3完成: {write_time:.2f}s, 写入 {len(all_data):,} 条")

            # 阶段4: 创建索引
            self.progress_signal.emit(self.file_count, "阶段4/5: 创建索引...")
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_parent ON files(parent_dir)"
                )
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA journal_mode=WAL")

                # 保存元数据
                cursor.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_time', ?)",
                    (str(time.time()),),
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('used_mft', ?)",
                    ("1" if self.used_mft else "0",),
                )

                if not HAS_APSW:
                    self.conn.commit()

            logger.info(f"✅ 阶段4完成: {time.time() - build_start:.2f}s")

            # 阶段5: 后台构建FTS
            self.progress_signal.emit(self.file_count, "阶段5/5: 构建全文索引(后台)...")

            def build_fts_async():
                try:
                    logger.info("📝 后台构建 FTS5...")
                    fts_start = time.time()
                    with self.lock:
                        cursor = self.conn.cursor()
                        cursor.execute(
                            "CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(filename, content=files, content_rowid=id)"
                        )
                        cursor.execute(
                            "INSERT INTO files_fts(files_fts) VALUES('rebuild')"
                        )
                        cursor.execute(
                            """
                            CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                                INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename);
                            END
                        """
                        )
                        cursor.execute(
                            """
                            CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                                INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename);
                            END
                        """
                        )
                        if not HAS_APSW:
                            self.conn.commit()
                        self.has_fts = True
                    logger.info(f"✅ FTS5 构建完成: {time.time() - fts_start:.2f}s")
                except Exception as e:
                    logger.warning(f"⚠️ FTS5 构建失败: {e}")
                    self.has_fts = False
                self.fts_finished_signal.emit()

            threading.Thread(target=build_fts_async, daemon=True).start()

            # 处理失败的驱动器（回退到传统扫描）
            for drv in failed_drives:
                if stop_fn and stop_fn():
                    break
                paths_to_scan = c_allowed_paths if drv == "C" else [f"{drv}:\\"]
                for path in paths_to_scan:
                    logger.info(f"[传统扫描] {path}")
                    self._scan_dir(
                        path, c_allowed_paths if drv == "C" else None, stop_fn
                    )

            # 更新最终计数
            try:
                with self.lock:
                    cursor = self.conn.cursor()
                    final_count = list(cursor.execute("SELECT COUNT(*) FROM files"))[0][0]
                    self.file_count = final_count
            except:
                pass

            total_time = time.time() - build_start
            logger.info(
                f"✅ 索引构建完成: {self.file_count:,} 条, 总耗时 {total_time:.2f}s"
            )
            self.is_ready = self.file_count > 0
            self.build_finished_signal.emit()

        except Exception as e:
            import traceback
            logger.error(f"❌ 构建错误: {e}")
            traceback.print_exc()
        finally:
            self.is_building = False

    def _scan_dir(self, target, allowed_paths=None, stop_fn=None):
        """传统目录扫描"""
        try:
            if not os.path.exists(target):
                return
        except (OSError, PermissionError):
            logger.warning(f"无法访问目录: {target}")
            return

        allowed_paths_lower = (
            [p.lower().rstrip("\\") for p in allowed_paths] if allowed_paths else None
        )
        batch = []
        stack = deque([target])

        while stack:
            if stop_fn and stop_fn():
                break
            cur = stack.pop()
            if should_skip_path(cur.lower(), allowed_paths_lower):
                continue
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        if stop_fn and stop_fn():
                            break
                        if not e.name or e.name.startswith((".", "$")):
                            continue
                        try:
                            is_dir = e.is_dir()
                            st = e.stat(follow_symlinks=False)
                        except (OSError, PermissionError):
                            continue

                        path_lower = e.path.lower()
                        if is_dir:
                            if should_skip_dir(
                                e.name.lower(), path_lower, allowed_paths_lower
                            ):
                                continue
                            stack.append(e.path)
                            batch.append(
                                (e.name, e.name.lower(), e.path, cur, "", 0, 0, 1)
                            )
                        else:
                            ext = os.path.splitext(e.name)[1].lower()
                            if ext in SKIP_EXTS:
                                continue
                            batch.append(
                                (
                                    e.name,
                                    e.name.lower(),
                                    e.path,
                                    cur,
                                    ext,
                                    st.st_size,
                                    st.st_mtime,
                                    0,
                                )
                            )

                        if len(batch) >= 20000:
                            with self.lock:
                                cursor = self.conn.cursor()
                                cursor.executemany(
                                    "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                                    batch,
                                )
                                if not HAS_APSW:
                                    self.conn.commit()
                            self.file_count += len(batch)
                            self.progress_signal.emit(self.file_count, cur)
                            batch = []
            except (PermissionError, OSError):
                continue

        if batch:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.executemany(
                    "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", batch
                )
                if not HAS_APSW:
                    self.conn.commit()
            self.file_count += len(batch)

    def rebuild_drive(self, drive_letter, progress_callback=None, stop_fn=None):
        """重建单个驱动器的索引 - 高速版"""
        if not self.conn:
            return
        
        if self.is_building:
            logger.warning("索引正在构建中，跳过")
            return
        
        self.is_building = True
        drive = drive_letter.upper().rstrip(":\\")
        
        try:
            logger.info(f"开始重建 {drive}: 盘索引...")
            
            # 删除该驱动器的现有记录
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("DELETE FROM files WHERE full_path LIKE ?", (f"{drive}:%",))
                if not HAS_APSW:
                    self.conn.commit()
            
            # 重新扫描该驱动器
            c_allowed_paths = get_c_scan_dirs(self.config_mgr)
            allowed_paths = c_allowed_paths if drive == 'C' else None
            
            try:
                data = enum_volume_files_mft(drive, SKIP_DIRS_LOWER, SKIP_EXTS, allowed_paths)
                
                if data:
                    logger.info(f"开始写入 {len(data)} 条记录...")
                    write_start = time.time()
                    
                    with self.lock:
                        cursor = self.conn.cursor()
                        
                        # ★ 极限优化：完全关闭安全机制
                        cursor.execute("PRAGMA synchronous=OFF")
                        cursor.execute("PRAGMA journal_mode=OFF")
                        cursor.execute("PRAGMA locking_mode=EXCLUSIVE")
                        cursor.execute("PRAGMA temp_store=MEMORY")
                        cursor.execute("PRAGMA cache_size=-500000")
                        
                        # ★ 使用单个大事务
                        if HAS_APSW:
                            # APSW: 使用 with 语句自动管理事务
                            with self.conn:
                                cursor.executemany(
                                    "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                                    data
                                )
                                cursor.execute(
                                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_time', ?)",
                                    (str(time.time()),)
                                )
                                cursor.execute(
                                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('used_mft', '1')"
                                )
                        else:
                            # sqlite3: 手动管理事务
                            cursor.execute("BEGIN TRANSACTION")
                            cursor.executemany(
                                "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                                data
                            )
                            cursor.execute(
                                "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_time', ?)",
                                (str(time.time()),)
                            )
                            cursor.execute(
                                "INSERT OR REPLACE INTO meta (key, value) VALUES ('used_mft', '1')"
                            )
                            cursor.execute("COMMIT")
                        
                        # ★ 恢复正常模式
                        cursor.execute("PRAGMA synchronous=NORMAL")
                        cursor.execute("PRAGMA journal_mode=WAL")
                        cursor.execute("PRAGMA locking_mode=NORMAL")
                    
                    write_time = time.time() - write_start
                    logger.info(f"✅ {drive}: 盘索引重建完成，写入 {len(data)} 条记录，耗时 {write_time:.2f}s")
                    
            except Exception as e:
                logger.error(f"MFT扫描失败: {e}")
                import traceback
                traceback.print_exc()
            
            self._load_stats(preserve_mft=True)
            self.is_ready = self.file_count > 0
            
        except Exception as e:
            logger.error(f"重建驱动器 {drive} 失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.is_building = False
            logger.info(f"{drive}: 盘索引重建流程结束")
            self.build_finished_signal.emit()

# ==================== USN 文件监控 ====================

def _norm_path(p: str) -> str:
    """规范化路径，尽量保证和数据库 full_path 的格式一致"""
    p = os.path.normpath(p)
    # 去掉末尾反斜杠（根目录如 C:\ 不处理）
    if len(p) > 3 and p.endswith(os.sep):
        p = p.rstrip(os.sep)
    return p

# ==================== 持久化文件 ====================
def _dir_cache_file(drive_letter: str) -> str:
    """DIR_CACHE 持久化文件路径（按盘）"""
    base = Path(os.getenv("LOCALAPPDATA", ".")) / "SearchTool" / "dir_cache"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"dir_cache_{drive_letter.upper()}.bin")

class UsnFileWatcher(QObject):
    """USN Journal 文件监控器 - 高性能 Windows 原生方案"""
    
    # ★ 添加信号
    files_changed = Signal(int, int, list)

    def __init__(self, index_mgr, config_mgr=None):
        super().__init__()  # ★ 添加这行
        self.index_mgr = index_mgr
        self.config_mgr = config_mgr
        self.running = False
        self.stop_flag = False
        self.thread = None
        self.usn_positions = {}
        self.drives = []
        self._setup_ffi()

    def _setup_ffi(self):
        """设置 FFI 函数签名"""
        if not HAS_RUST_ENGINE:
            return

        class FileChange(ctypes.Structure):
            _fields_ = [
                ("action", ctypes.c_uint8),
                ("is_dir", ctypes.c_uint8),
                ("path_ptr", ctypes.POINTER(ctypes.c_uint8)),
                ("path_len", ctypes.c_size_t),
            ]

        class ChangeList(ctypes.Structure):
            _fields_ = [
                ("changes", ctypes.POINTER(FileChange)),
                ("count", ctypes.c_size_t),
            ]

        self.FileChange = FileChange
        self.ChangeList = ChangeList

        RUST_ENGINE.get_current_usn.argtypes = [ctypes.c_uint16]
        RUST_ENGINE.get_current_usn.restype = ctypes.c_int64

        RUST_ENGINE.get_usn_changes.argtypes = [ctypes.c_uint16, ctypes.c_int64]
        RUST_ENGINE.get_usn_changes.restype = ChangeList

        RUST_ENGINE.free_change_list.argtypes = [ChangeList]
        RUST_ENGINE.free_change_list.restype = None

    def start(self, drives):
        """启动监控"""
        if not HAS_RUST_ENGINE:
            logger.warning("[USN监控] Rust 引擎不可用")
            return

        if self.running:
            return

        self.drives = []
        for d in drives:
            drive_letter = d.upper().rstrip(":\\/")
            if len(drive_letter) == 1 and drive_letter.isalpha():
                self.drives.append(drive_letter)

        if not self.drives:
            logger.warning("[USN监控] 没有有效的驱动器")
            return

        for drive in self.drives:
            try:
                usn = RUST_ENGINE.get_current_usn(ord(drive))
                if usn >= 0:
                    self.usn_positions[drive] = usn
                    logger.info(f"[USN监控] {drive}: 初始 USN = {usn}")
                else:
                    logger.warning(f"[USN监控] {drive}: 获取 USN 失败")
            except Exception as e:
                logger.error(f"[USN监控] 获取 {drive} USN 失败: {e}")

        if not self.usn_positions:
            logger.warning("[USN监控] 没有可监控的驱动器")
            return

        self.running = True
        self.stop_flag = False
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logger.info(f"[USN监控] 已启动，监控: {list(self.usn_positions.keys())}")

    def poll_once(self):
        """立即检查一次所有驱动器（不给你等轮询间隔）"""
        if not self.running or self.stop_flag:
            return
        if self.index_mgr.is_building:
            return

        for drive in list(self.usn_positions.keys()):
            if self.stop_flag:
                break
            self._check_drive(drive)

    def _poll_loop(self):
        """轮询 USN 变更（自适应间隔）"""
        idle_count = 0  # 连续无变化次数
        
        while not self.stop_flag:
            try:
                if self.index_mgr.is_building:
                    idle_count = 0
                    time.sleep(1)
                    continue

                has_changes = False
                for drive in list(self.usn_positions.keys()):
                    if self.stop_flag:
                        break
                    if self._check_drive(drive):
                        has_changes = True

                # ★ 自适应间隔
                if has_changes:
                    idle_count = 0
                    sleep_time = 0.1  # 有变化时快速响应
                else:
                    idle_count += 1
                    # 逐渐放慢：0.2 -> 0.3 -> 0.45 -> ... -> 最长 2.0 秒
                    sleep_time = min(2.0, 0.2 * (1.3 ** min(idle_count, 10)))

            except Exception as e:
                logger.error(f"[USN监控] 轮询错误: {e}")
                sleep_time = 1.0

            # 分段 sleep 便于快速退出
            steps = max(1, int(sleep_time / 0.1))
            for _ in range(steps):
                if self.stop_flag:
                    break
                time.sleep(0.1)

    def _check_drive(self, drive):
        """检查单个驱动器的变更，返回是否有变化"""
        last_usn = self.usn_positions.get(drive, 0)

        try:
            current_usn = RUST_ENGINE.get_current_usn(ord(drive))
            if current_usn <= last_usn:
                return False

            result = RUST_ENGINE.get_usn_changes(ord(drive), last_usn)

            has_changes = False
            if result.count > 0 and result.changes:
                changes = []
                for i in range(result.count):
                    c = result.changes[i]
                    if c.path_ptr and c.path_len > 0:
                        try:
                            path_bytes = ctypes.string_at(c.path_ptr, c.path_len)
                            path = path_bytes.decode("utf-8", errors="replace")
                            action = int(c.action)
                            is_dir = bool(c.is_dir == 1)
                            changes.append((action, path, is_dir))
                        except Exception as e:
                            logger.debug(f"[USN] 解析失败: {e}")

                if changes:
                    self._apply_changes(changes)
                    has_changes = True

                RUST_ENGINE.free_change_list(result)

            # 更新 USN 位置
            self.usn_positions[drive] = current_usn
            return has_changes

        except Exception as e:
            logger.error(f"[USN监控] {drive} 失败: {e}")
            return False

    def _apply_changes(self, changes):
        """应用变更到数据库"""
        if not changes or not self.index_mgr.conn:
            return

        if self.index_mgr.is_building:
            return

        inserts = []
        deletes = []

        c_allowed = get_c_scan_dirs(self.config_mgr)
        c_allowed_lower = [p.lower() for p in c_allowed] if c_allowed else []

        for action, path, is_dir in changes:
            # 统一路径格式，避免删不掉
            path = _norm_path(path)

            # C 盘路径过滤（只允许白名单目录）
            if path.upper().startswith("C:"):
                path_lower = path.lower()
                in_allowed = any(path_lower.startswith(ap.lower()) for ap in c_allowed_lower)
                if not in_allowed:
                    continue

            name = os.path.basename(path)
            if not name or name.startswith((".", "$")):
                continue

            # 删除（包含：永久删除、移入回收站等你映射成删除的事件）
            if action in (0, 4):
                deletes.append(path)
                continue

            # 创建/修改/重命名
            if action in (1, 2, 3):
                if should_skip_path(path.lower()):
                    continue

                try:
                    if os.path.exists(path):
                        if is_dir:
                            # 目录：还原/创建时，USN 往往只给目录事件，不给全量子文件
                            if not should_skip_dir(name.lower(), path.lower()):
                                # 先插入目录本身
                                inserts.append(
                                    (
                                        name,
                                        name.lower(),
                                        path,
                                        os.path.dirname(path),
                                        "",
                                        0,
                                        0,
                                        1,
                                    )
                                )

                                # ★ 关键：补扫目录内容，确保索引能搜到子文件
                                extra = self._scan_dir_records(path)
                                if extra:
                                    inserts.extend(extra)
                        else:
                            # 文件
                            ext = os.path.splitext(name)[1].lower()
                            if ext not in SKIP_EXTS:
                                st = os.stat(path)
                                inserts.append(
                                    (
                                        name,
                                        name.lower(),
                                        path,
                                        os.path.dirname(path),
                                        ext,
                                        st.st_size,
                                        st.st_mtime,
                                        0,
                                    )
                                )
                except (OSError, PermissionError):
                    pass

        if not inserts and not deletes:
            return

        try:
            with self.index_mgr.lock:
                cursor = self.index_mgr.conn.cursor()

                # ====== 关键修复：强力删除（兼容 末尾\ / 不带\）======
                if deletes:
                    for d in deletes:
                        nd = _norm_path(d)
                        nd_slash = nd + os.sep  # 用于 LIKE 子项

                        # 1) 删除自身记录（文件/目录都可能）
                        cursor.execute(
                            "DELETE FROM files WHERE full_path = ? OR full_path = ?",
                            (nd, nd_slash),
                        )

                        # 2) 如果是目录，删除其子项
                        cursor.execute(
                            "DELETE FROM files WHERE full_path LIKE ?",
                            (nd_slash + "%",),
                        )

                # 写入新增/修改
                if inserts:
                    cursor.executemany(
                        "INSERT OR REPLACE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                        inserts,
                    )

                if not HAS_APSW:
                    self.index_mgr.conn.commit()

            # 立刻刷新计数（让 UI 看到变化）
            self.index_mgr.force_reload_stats()

            logger.info(f"[USN监控] 更新完成: +{len(inserts)} -{len(deletes)}")
            self.files_changed.emit(len(inserts), len(deletes), list(deletes))

        except Exception as e:
            logger.error(f"[USN监控] 数据库更新失败: {e}")
            
    def _scan_dir_records(self, root_path, max_items=200000, max_depth=15, max_seconds=0.5):
        """
        扫描目录生成 records 列表，格式与 files 表一致:
        (filename, filename_lower, full_path, parent_dir, extension, size, mtime, is_dir)
        
        限制条件（防止卡死）：
        - max_items: 最多扫描条数
        - max_depth: 最大目录深度
        - max_seconds: 最大耗时（秒）
        """
        records = []
        stack = [(root_path, 0)]  # (path, depth)
        start_time = time.time()

        while stack and len(records) < max_items:
            # ★ 时间限制：超时就退出
            if time.time() - start_time > max_seconds:
                logger.debug(f"[补扫] 超时退出: {root_path}, 已扫描 {len(records)} 条")
                break

            cur, depth = stack.pop()

            # 深度限制
            if depth > max_depth:
                continue

            cur_lower = cur.lower()
            if should_skip_path(cur_lower):
                continue

            try:
                with os.scandir(cur) as it:
                    for e in it:
                        # 再次检查时间（目录内文件很多时）
                        if time.time() - start_time > max_seconds:
                            break

                        if not e.name or e.name.startswith((".", "$")):
                            continue

                        full_path = _norm_path(e.path)
                        name = e.name
                        name_lower = name.lower()
                        parent_dir = _norm_path(cur)

                        try:
                            is_dir = e.is_dir(follow_symlinks=False)
                        except (OSError, PermissionError):
                            continue

                        if is_dir:
                            if should_skip_dir(name_lower, full_path.lower()):
                                continue
                            records.append((name, name_lower, full_path, parent_dir, "", 0, 0, 1))
                            stack.append((full_path, depth + 1))
                        else:
                            ext = os.path.splitext(name)[1].lower()
                            if ext in SKIP_EXTS:
                                continue
                            try:
                                st = e.stat(follow_symlinks=False)
                                size = st.st_size
                                mtime = st.st_mtime
                            except (OSError, PermissionError):
                                size = 0
                                mtime = 0
                            records.append((name, name_lower, full_path, parent_dir, ext, size, mtime, 0))

                        if len(records) >= max_items:
                            break
            except (OSError, PermissionError):
                continue

        if len(records) > 0:
            logger.debug(f"[补扫] {root_path}: {len(records)} 条, 耗时 {time.time() - start_time:.2f}s")

        return records

    def stop(self):
        """停止监控"""
        self.stop_flag = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.running = False
        self.usn_positions.clear()
        logger.info("[USN监控] 已停止")

            # ==================== 系统托盘管理 ====================


class TrayManager:
    """系统托盘管理器"""

    def __init__(self, app):
        self.app = app
        self.tray_icon = None
        self.running = False

    def _create_icon_image(self):
        """创建托盘图标"""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#4CAF50"))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(8, 8, 32, 32)
        painter.drawLine(36, 36, 54, 54)
        painter.end()
        return QIcon(pixmap)

    def _create_menu(self):
        """创建托盘菜单"""
        menu = QMenu()

        show_action = QAction("显示主窗口", self.app)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        menu.addSeparator()

        rebuild_action = QAction("重建索引", self.app)
        rebuild_action.triggered.connect(self._rebuild_index)
        menu.addAction(rebuild_action)

        refresh_action = QAction("刷新状态", self.app)
        refresh_action.triggered.connect(self._refresh_status)
        menu.addAction(refresh_action)

        menu.addSeparator()

        quit_action = QAction("退出", self.app)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        return menu

    def _show_window(self):
        self.app.show()
        self.app.showNormal()
        self.app.raise_()
        self.app.activateWindow()
        self.app.entry_kw.setFocus()

    def _rebuild_index(self):
        QTimer.singleShot(0, self.app._build_index)

    def _refresh_status(self):
        QTimer.singleShot(0, self.app.sync_now)

    def _quit(self):
        self.stop()
        QTimer.singleShot(0, self.app._do_quit)

    def start(self):
        """启动托盘"""
        if self.running:
            return True

        try:
            self.tray_icon = QSystemTrayIcon(self.app)
            self.tray_icon.setIcon(self._create_icon_image())
            self.tray_icon.setToolTip("极速文件搜索")
            self.tray_icon.setContextMenu(self._create_menu())
            self.tray_icon.activated.connect(self._on_activated)
            self.tray_icon.show()
            self.running = True
            logger.info("🔔 托盘已启动")
            return True
        except Exception as e:
            logger.error(f"启动托盘失败: {e}")
            return False

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def stop(self):
        """停止托盘"""
        if self.tray_icon and self.running:
            try:
                self.tray_icon.hide()
                self.tray_icon = None
                self.running = False
                logger.info("🔔 托盘已停止")
            except Exception as e:
                logger.error(f"停止托盘失败: {e}")

    def show_notification(self, title, message):
        """显示通知"""
        if self.tray_icon and self.running:
            try:
                self.tray_icon.showMessage(
                    title, message, QSystemTrayIcon.Information, 3000
                )
            except Exception as e:
                logger.debug(f"显示通知失败: {e}")


# ==================== 全局热键管理 ====================
class HotkeyManager(QObject):
    """全局热键管理器"""

    show_mini_signal = Signal()
    show_main_signal = Signal()

    HOTKEY_MINI = 1
    HOTKEY_MAIN = 2

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.registered = False
        self.thread = None
        self.stop_flag = False

        self.show_mini_signal.connect(self._on_hotkey_mini)
        self.show_main_signal.connect(self._on_hotkey_main)

    def start(self):
        """启动热键监听"""
        if not IS_WINDOWS:
            logger.warning("全局热键仅支持Windows系统")
            return False

        if self.registered:
            return True

        self.stop_flag = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def _run(self):
        """热键监听线程"""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)

            RegisterHotKey = user32.RegisterHotKey
            RegisterHotKey.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                wintypes.UINT,
                wintypes.UINT,
            ]
            RegisterHotKey.restype = wintypes.BOOL

            UnregisterHotKey = user32.UnregisterHotKey
            UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            UnregisterHotKey.restype = wintypes.BOOL

            PeekMessageW = user32.PeekMessageW
            PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            PeekMessageW.restype = wintypes.BOOL

            MOD_CONTROL = 0x0002
            MOD_SHIFT = 0x0004
            VK_SPACE = 0x20
            VK_TAB = 0x09
            WM_HOTKEY = 0x0312
            PM_REMOVE = 0x0001

            if not RegisterHotKey(
                None, self.HOTKEY_MINI, MOD_CONTROL | MOD_SHIFT, VK_SPACE
            ):
                logger.error("注册迷你窗口热键失败")
            else:
                logger.info("⌨️ 热键已注册: Ctrl+Shift+Space → 迷你窗口")

            if not RegisterHotKey(
                None, self.HOTKEY_MAIN, MOD_CONTROL | MOD_SHIFT, VK_TAB
            ):
                logger.error("注册主窗口热键失败")
            else:
                logger.info("⌨️ 热键已注册: Ctrl+Shift+Tab → 主窗口")

            self.registered = True

            msg = wintypes.MSG()
            while not self.stop_flag:
                if PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_HOTKEY:
                        if msg.wParam == self.HOTKEY_MINI:
                            self.show_mini_signal.emit()
                        elif msg.wParam == self.HOTKEY_MAIN:
                            self.show_main_signal.emit()
                else:
                    for _ in range(5):
                        if self.stop_flag:
                            break
                        time.sleep(0.02)

            UnregisterHotKey(None, self.HOTKEY_MINI)
            UnregisterHotKey(None, self.HOTKEY_MAIN)
            self.registered = False
            logger.info("⌨️ 全局热键已注销")

        except Exception as e:
            logger.error(f"热键监听错误: {e}")
            self.registered = False

    def _on_hotkey_mini(self):
        """处理迷你窗口热键"""
        logger.info("⌨️ 热键触发: 迷你窗口")
        if hasattr(self.app, "mini_search") and self.app.mini_search:
            self.app.mini_search.show()

    def _on_hotkey_main(self):
        """处理主窗口热键"""
        logger.info("⌨️ 热键触发: 主窗口")
        try:
            self.app.show()
            self.app.showNormal()
            self.app.raise_()
            self.app.activateWindow()
            self.app.entry_kw.setFocus()
            self.app.entry_kw.selectAll()
        except Exception as e:
            logger.error(f"显示主窗口失败: {e}")

    def stop(self):
        """停止热键监听"""
        self.stop_flag = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.registered = False


# ==================== 迷你搜索窗口 ====================
class MiniSearchWindow(QObject):
    """迷你搜索窗口"""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.window = None
        self.search_mode = "index"
        self.results = []
        self.result_listbox = None
        self.mode_label = None
        self.search_entry = None
        self.tip_label = None
        self.result_frame = None
        self.tip_frame = None
        self.button_frame = None
        self.ctx_menu = None

    def show(self):
        """显示迷你窗口"""
        if self.window is not None:
            try:
                self.window.activateWindow()
                self.window.raise_()
                self.search_entry.setFocus()
                self.search_entry.selectAll()
                return
            except:
                self.window = None

        self._create_window()

    def _create_window(self):
        """创建窗口"""
        self.window = QDialog(None)
        self.window.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.window.setAttribute(Qt.WA_TranslucentBackground, False)
        self.window.setFixedSize(720, 70)
        self.window.setStyleSheet(
            """
            QDialog { background-color: #b8e0f0; border: 3px solid #006699; }
            QLineEdit { padding: 8px; font-size: 14px; border: 2px solid #88c0d8; border-radius: 4px; background: white; }
            QLineEdit:focus { border-color: #006699; }
            QListWidget { background: white; border: 1px solid #88c0d8; font-size: 11px; }
            QListWidget::item { padding: 4px; }
            QListWidget::item:selected { background-color: #006699; color: white; }
            QListWidget::item:hover { background-color: #e0f0f8; }
            QPushButton { padding: 5px 10px; background: white; border: 1px groove #ccc; border-radius: 3px; font-size: 9px; color: #004466; }
            QPushButton:hover { background: #e8f4f8; }
            QLabel { color: #004466; }
        """
        )

        # 居中显示
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - 720) // 2
        y = int(screen.height() * 0.20)
        self.window.move(x, y)

        # 主布局
        main_layout = QVBoxLayout(self.window)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.setSpacing(12)

        # 搜索图标
        self.search_icon = QLabel("🔍")
        self.search_icon.setFont(QFont("Segoe UI Emoji", 18))
        self.search_icon.setStyleSheet("color: #004466;")
        self.search_icon.setCursor(Qt.PointingHandCursor)
        self.search_icon.mousePressEvent = lambda e: self._on_search()
        search_layout.addWidget(self.search_icon)

        # 搜索框
        self.search_entry = QLineEdit()
        self.search_entry.setFont(QFont("微软雅黑", 14))
        self.search_entry.setPlaceholderText("输入关键词搜索...")
        search_layout.addWidget(self.search_entry, 1)

        # 模式切换
        mode_frame = QHBoxLayout()
        mode_frame.setSpacing(3)

        self.left_arrow = QLabel("◀")
        self.left_arrow.setFont(QFont("Arial", 12, QFont.Bold))
        self.left_arrow.setStyleSheet("color: #004466;")
        self.left_arrow.setCursor(Qt.PointingHandCursor)
        self.left_arrow.mousePressEvent = lambda e: self._on_mode_switch()
        mode_frame.addWidget(self.left_arrow)

        self.mode_label = QLabel("索引搜索")
        self.mode_label.setFont(QFont("微软雅黑", 10, QFont.Bold))
        self.mode_label.setFixedWidth(70)
        self.mode_label.setAlignment(Qt.AlignCenter)
        self.mode_label.setStyleSheet("color: #004466;")
        mode_frame.addWidget(self.mode_label)

        self.right_arrow = QLabel("▶")
        self.right_arrow.setFont(QFont("Arial", 12, QFont.Bold))
        self.right_arrow.setStyleSheet("color: #004466;")
        self.right_arrow.setCursor(Qt.PointingHandCursor)
        self.right_arrow.mousePressEvent = lambda e: self._on_mode_switch()
        mode_frame.addWidget(self.right_arrow)

        search_layout.addLayout(mode_frame)

        # 关闭按钮
        self.close_btn = QLabel("✕")
        self.close_btn.setFont(QFont("Arial", 14, QFont.Bold))
        self.close_btn.setStyleSheet("color: #666666;")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.mousePressEvent = lambda e: self._on_close()
        self.close_btn.enterEvent = lambda e: self.close_btn.setStyleSheet(
            "color: #cc0000;"
        )
        self.close_btn.leaveEvent = lambda e: self.close_btn.setStyleSheet(
            "color: #666666;"
        )
        search_layout.addWidget(self.close_btn)

        main_layout.addLayout(search_layout)

        # 结果列表（初始隐藏）
        self.result_frame = QWidget()
        self.result_frame.setVisible(False)
        result_layout = QHBoxLayout(self.result_frame)
        result_layout.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        self.result_listbox = QListWidget()
        self.result_listbox.setFont(QFont("微软雅黑", 11))
        self.result_listbox.setMinimumHeight(280)
        self.result_listbox.setAlternatingRowColors(False)
        self.result_listbox.itemDoubleClicked.connect(self._on_open)
        self.result_listbox.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_listbox.customContextMenuRequested.connect(self._on_right_click)
        result_layout.addWidget(self.result_listbox)

        main_layout.addWidget(self.result_frame)

        # 按钮栏（初始隐藏）
        self.button_frame = QWidget()
        self.button_frame.setVisible(False)
        btn_layout = QHBoxLayout(self.button_frame)
        btn_layout.setContentsMargins(0, 6, 0, 0)
        btn_layout.setSpacing(4)

        self.btn_open = QPushButton("打开")
        self.btn_open.clicked.connect(self._btn_open)
        btn_layout.addWidget(self.btn_open)

        self.btn_locate = QPushButton("定位")
        self.btn_locate.clicked.connect(self._btn_locate)
        btn_layout.addWidget(self.btn_locate)

        self.btn_copy = QPushButton("复制")
        self.btn_copy.clicked.connect(self._btn_copy)
        btn_layout.addWidget(self.btn_copy)

        self.btn_delete = QPushButton("删除")
        self.btn_delete.setStyleSheet("color: #aa0000;")
        self.btn_delete.clicked.connect(self._btn_delete)
        btn_layout.addWidget(self.btn_delete)

        self.btn_to_main = QPushButton("主页面查看")
        self.btn_to_main.clicked.connect(self._btn_to_main)
        btn_layout.addWidget(self.btn_to_main)

        btn_layout.addStretch()
        main_layout.addWidget(self.button_frame)

        # 提示栏（初始隐藏）
        self.tip_frame = QWidget()
        self.tip_frame.setVisible(False)
        tip_layout = QHBoxLayout(self.tip_frame)
        tip_layout.setContentsMargins(0, 5, 0, 0)

        self.tip_label = QLabel(
            "Enter=打开  Ctrl+Enter=定位  Ctrl+C=复制  Delete=删除  Tab=主页面  Esc=关闭"
        )
        self.tip_label.setFont(QFont("微软雅黑", 9))
        self.tip_label.setStyleSheet("color: #004466;")
        tip_layout.addWidget(self.tip_label)

        main_layout.addWidget(self.tip_frame)

        # 创建右键菜单
        self._create_context_menu()

        # 安装事件过滤器
        self.window.installEventFilter(self)
        self.search_entry.installEventFilter(self)
        self.result_listbox.installEventFilter(self)

        # 显示窗口
        self.window.show()
        self.window.activateWindow()
        self.search_entry.setFocus()

    def eventFilter(self, obj, event):
        """事件过滤器"""
        if event.type() == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            if key == Qt.Key_Escape:
                self._on_close()
                return True

            if key == Qt.Key_Tab:
                self._on_switch_to_main()
                return True

            if key in (Qt.Key_Return, Qt.Key_Enter):
                if modifiers & Qt.ControlModifier:
                    self._on_locate()
                else:
                    self._on_search()
                return True

            if key == Qt.Key_C and modifiers & Qt.ControlModifier:
                self._on_copy_shortcut()
                return True

            if key == Qt.Key_Delete:
                self._on_delete_shortcut()
                return True

            if key == Qt.Key_Up:
                self._on_up()
                return True
            if key == Qt.Key_Down:
                self._on_down()
                return True

            if obj == self.search_entry:
                text = self.search_entry.text()
                cursor = self.search_entry.cursorPosition()
                if key == Qt.Key_Left and cursor == 0:
                    self._on_mode_switch()
                    return True
                if key == Qt.Key_Right and cursor == len(text):
                    self._on_mode_switch()
                    return True

        return super().eventFilter(obj, event)

    def _create_context_menu(self):
        """创建右键菜单"""
        self.ctx_menu = QMenu(self.window)
        self.ctx_menu.addAction("打开", self._btn_open)
        self.ctx_menu.addAction("定位", self._btn_locate)
        self.ctx_menu.addSeparator()
        self.ctx_menu.addAction("复制", self._btn_copy)
        self.ctx_menu.addSeparator()
        self.ctx_menu.addAction("删除", self._btn_delete)
        self.ctx_menu.addAction("主页面查看", self._btn_to_main)

    def _on_mode_switch(self, event=None):
        """切换搜索模式"""
        if self.search_mode == "index":
            self.search_mode = "realtime"
            self.mode_label.setText("实时搜索")
        else:
            self.search_mode = "index"
            self.mode_label.setText("索引搜索")

    def _on_search(self, event=None):
        """执行搜索"""
        keyword = self.search_entry.text().strip()
        if not keyword:
            return

        self.results.clear()
        self.result_listbox.clear()
        self._show_results_area()

        if self.search_mode == "index":
            self._search_index(keyword)
        else:
            self._search_realtime(keyword)

    def _search_index(self, keyword):
        """索引搜索"""
        if not self.app.index_mgr.is_ready:
            from PySide6.QtWidgets import QListWidgetItem

            self.result_listbox.addItem(
                QListWidgetItem("   ⚠️ 索引未就绪，请先构建索引")
            )
            return

        keywords = keyword.lower().split()
        scope_targets = self.app._get_search_scope_targets()
        results = self.app.index_mgr.search(keywords, scope_targets, limit=200)

        if results is None:
            from PySide6.QtWidgets import QListWidgetItem

            self.result_listbox.addItem(QListWidgetItem("   ⚠️ 搜索失败"))
            return

        self._display_results(results)

    def _search_realtime(self, keyword):
        """实时搜索"""
        from PySide6.QtWidgets import QListWidgetItem

        self.result_listbox.addItem(QListWidgetItem("   🔍 正在搜索..."))
        QApplication.processEvents()

        keywords = keyword.lower().split()
        scope_targets = self.app._get_search_scope_targets()
        results = []
        count = 0

        for target in scope_targets:
            if count >= 200 or not os.path.isdir(target):
                continue
            try:
                for root, dirs, files in os.walk(target):
                    dirs[:] = [
                        d
                        for d in dirs
                        if d.lower() not in SKIP_DIRS_LOWER and not d.startswith(".")
                    ]
                    for name in files + dirs:
                        if count >= 200:
                            break
                        if all(kw in name.lower() for kw in keywords):
                            fp = os.path.join(root, name)
                            is_dir = os.path.isdir(fp)
                            try:
                                st = os.stat(fp)
                                sz, mt = (
                                    (0, st.st_mtime)
                                    if is_dir
                                    else (st.st_size, st.st_mtime)
                                )
                            except:
                                sz, mt = 0, 0
                            results.append((name, fp, sz, mt, 1 if is_dir else 0))
                            count += 1
            except:
                continue

        self.result_listbox.clear()
        self._display_results(results)

    def _display_results(self, results):
        """显示搜索结果"""
        from PySide6.QtWidgets import QListWidgetItem

        if not results:
            self.result_listbox.addItem(QListWidgetItem("   😔 未找到匹配的文件"))
            return

        self.results = []
        for i, (fn, fp, sz, mt, is_dir) in enumerate(results):
            ext = os.path.splitext(fn)[1].lower()
            if is_dir:
                icon = "📁"
            elif ext in ARCHIVE_EXTS:
                icon = "📦"
            else:
                icon = "📄"

            item = QListWidgetItem(f"   {icon}  {fn}")
            if i % 2 == 0:
                item.setBackground(QColor("#ffffff"))
            else:
                item.setBackground(QColor("#e8f4f8"))

            self.result_listbox.addItem(item)
            self.results.append(
                {
                    "filename": fn,
                    "fullpath": fp,
                    "size": sz,
                    "mtime": mt,
                    "is_dir": is_dir,
                }
            )

        if self.results:
            self.result_listbox.setCurrentRow(0)

        self.tip_label.setText(
            f"找到 {len(self.results)} 个  │  Enter=打开  Ctrl+Enter=定位  Delete=删除  Tab=主页面  Esc=关闭"
        )

    def _show_results_area(self):
        """显示结果区域"""
        self.result_frame.setVisible(True)
        self.button_frame.setVisible(True)
        self.tip_frame.setVisible(True)

        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - 720) // 2
        y = int(screen.height() * 0.15)
        self.window.setFixedSize(720, 480)
        self.window.move(x, y)

    def _get_current_item(self):
        """获取当前选中项"""
        if not self.results:
            return None
        row = self.result_listbox.currentRow()
        if row < 0 or row >= len(self.results):
            return None
        return self.results[row]

    def _btn_open(self):
        self._on_open()

    def _btn_locate(self):
        self._on_locate()

    def _btn_copy(self):
        self._on_copy_shortcut()

    def _btn_delete(self):
        self._on_delete_shortcut()

    def _btn_to_main(self):
        self._on_switch_to_main()

    def _on_copy_shortcut(self, event=None):
        """复制路径"""
        item = self._get_current_item()
        if not item:
            return
        try:
            QApplication.clipboard().setText(item["fullpath"])
        except Exception as e:
            logger.error(f"复制路径失败: {e}")

    def _on_delete_shortcut(self, event=None):
        """删除文件"""
        item = self._get_current_item()
        if not item:
            return
        path = item["fullpath"]
        name = item["filename"]

        if HAS_SEND2TRASH:
            msg = f"确定删除？\n{name}\n\n将移动到回收站。"
        else:
            msg = f"确定永久删除？\n{name}\n\n⚠ 此操作不可恢复。"

        if QMessageBox.question(self.window, "确认删除", msg) != QMessageBox.Yes:
            return

        try:
            if HAS_SEND2TRASH:
                send2trash.send2trash(path)
            else:
                if item["is_dir"]:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        except Exception as e:
            logger.error(f"删除失败: {path} - {e}")
            QMessageBox.warning(self.window, "删除失败", f"无法删除：\n{path}\n\n{e}")
            return

        row = self.result_listbox.currentRow()
        self.result_listbox.takeItem(row)
        del self.results[row]

        if self.results:
            new_row = min(row, len(self.results) - 1)
            self.result_listbox.setCurrentRow(new_row)

    def _on_open(self, item=None):
        """打开文件"""
        item = self._get_current_item()
        if not item:
            return
        try:
            if item["is_dir"]:
                subprocess.Popen(f'explorer "{item["fullpath"]}"')
            else:
                os.startfile(item["fullpath"])
            self.close()
        except Exception as e:
            logger.error(f"打开失败: {e}")

    def _on_locate(self, event=None):
        """定位文件"""
        item = self._get_current_item()
        if not item:
            return
        try:
            subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
            self.close()
        except Exception as e:
            logger.error(f"定位失败: {e}")

    def _on_switch_to_main(self, event=None):
        """切换到主窗口"""
        keyword = self.search_entry.text().strip()
        results_copy = list(self.results)

        self.close()

        self.app.show()
        self.app.showNormal()
        self.app.raise_()
        self.app.activateWindow()

        if keyword:
            self.app.entry_kw.setText(keyword)

            if results_copy:
                with self.app.results_lock:
                    self.app.all_results.clear()
                    self.app.filtered_results.clear()
                    self.app.shown_paths.clear()

                    for item in results_copy:
                        ext = os.path.splitext(item["filename"])[1].lower()
                        if item["is_dir"]:
                            tc, ss = 0, "📂 文件夹"
                        elif ext in ARCHIVE_EXTS:
                            tc, ss = 1, "📦 压缩包"
                        else:
                            tc, ss = 2, format_size(item["size"])

                        self.app.all_results.append(
                            {
                                "filename": item["filename"],
                                "fullpath": item["fullpath"],
                                "dir_path": os.path.dirname(item["fullpath"]),
                                "size": item["size"],
                                "mtime": item["mtime"],
                                "type_code": tc,
                                "size_str": ss,
                                "mtime_str": format_time(item["mtime"]),
                            }
                        )
                        self.app.shown_paths.add(item["fullpath"])

                    self.app.filtered_results = list(self.app.all_results)
                    self.app.total_found = len(self.app.all_results)

                self.app.current_page = 1
                self.app._update_ext_combo()
                self.app._render_page()
                self.app.status.setText(f"✅ 从迷你窗口导入 {len(results_copy)} 个结果")
                self.app.btn_refresh.setEnabled(True)

        self.app.entry_kw.setFocus()

    def _on_up(self, event=None):
        """向上选择"""
        if not self.results:
            return
        row = self.result_listbox.currentRow()
        if row > 0:
            self.result_listbox.setCurrentRow(row - 1)

    def _on_down(self, event=None):
        """向下选择"""
        if not self.results:
            return
        row = self.result_listbox.currentRow()
        if row < len(self.results) - 1:
            self.result_listbox.setCurrentRow(row + 1)

    def _on_right_click(self, pos):
        """右键菜单"""
        if not self.results:
            return
        item = self.result_listbox.itemAt(pos)
        if item:
            row = self.result_listbox.row(item)
            self.result_listbox.setCurrentRow(row)
            self.ctx_menu.exec_(self.result_listbox.viewport().mapToGlobal(pos))

    def _on_close(self, event=None):
        """关闭窗口"""
        self.close()

    def close(self):
        """关闭窗口"""
        if self.window:
            try:
                self.window.close()
            except:
                pass
            self.window = None
        self.results.clear()

        # ==================== C盘目录设置对话框 ====================


class CDriveSettingsDialog:
    """C盘目录设置对话框"""

    def __init__(self, parent, config_mgr, index_mgr=None, on_rebuild_callback=None):
        self.parent = parent
        self.config_mgr = config_mgr
        self.index_mgr = index_mgr
        self.on_rebuild_callback = on_rebuild_callback
        self.dialog = None
        self.path_vars = {}
        self.paths_frame = None
        self.scroll_area = None
        self.stat_label = None
        self.original_paths = []

    def show(self):
        """显示对话框"""
        self.dialog = QDialog(self.parent)
        self.dialog.setWindowTitle("⚙️ C盘扫描目录设置")
        self.dialog.setMinimumSize(650, 500)
        self.dialog.setModal(True)

        self.original_paths = [p.copy() for p in self.config_mgr.get_c_scan_paths()]
        self._build_ui()
        self.dialog.exec_()

    def _build_ui(self):
        """构建UI"""
        main_layout = QVBoxLayout(self.dialog)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # 说明文字
        desc_label = QLabel(
            "设置C盘索引扫描的目录范围，勾选启用，取消勾选禁用，点击 ✕ 删除"
        )
        desc_label.setFont(QFont("微软雅黑", 9))
        desc_label.setStyleSheet("color: #666;")
        main_layout.addWidget(desc_label)

        # 按钮行
        btn_row = QHBoxLayout()

        title_label = QLabel("扫描目录列表:")
        title_label.setFont(QFont("微软雅黑", 10, QFont.Bold))
        btn_row.addWidget(title_label)
        btn_row.addStretch()

        browse_btn = QPushButton("+ 浏览添加")
        browse_btn.clicked.connect(self._browse_add)
        btn_row.addWidget(browse_btn)

        manual_btn = QPushButton("+ 手动输入")
        manual_btn.clicked.connect(self._manual_add)
        btn_row.addWidget(manual_btn)

        main_layout.addLayout(btn_row)

        # 快捷操作行
        quick_row = QHBoxLayout()

        select_all_btn = QPushButton("✓ 全选")
        select_all_btn.clicked.connect(self._select_all)
        quick_row.addWidget(select_all_btn)

        select_none_btn = QPushButton("✗ 全不选")
        select_none_btn.clicked.connect(self._select_none)
        quick_row.addWidget(select_none_btn)

        select_invert_btn = QPushButton("↻ 反选")
        select_invert_btn.clicked.connect(self._select_invert)
        quick_row.addWidget(select_invert_btn)

        quick_row.addStretch()

        self.stat_label = QLabel("")
        self.stat_label.setFont(QFont("微软雅黑", 9))
        self.stat_label.setStyleSheet("color: #666;")
        quick_row.addWidget(self.stat_label)

        main_layout.addLayout(quick_row)

        # 滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet(
            "QScrollArea { background-color: #fafafa; border: 1px solid #ddd; }"
        )

        self.paths_frame = QWidget()
        self.paths_layout = QVBoxLayout(self.paths_frame)
        self.paths_layout.setContentsMargins(5, 5, 5, 5)
        self.paths_layout.setSpacing(2)
        self.paths_layout.addStretch()

        self.scroll_area.setWidget(self.paths_frame)
        main_layout.addWidget(self.scroll_area, 1)

        self._refresh_paths_list()

        # 底部按钮
        bottom_layout = QHBoxLayout()

        reset_btn = QPushButton("恢复系统默认")
        reset_btn.clicked.connect(self._reset_default)
        bottom_layout.addWidget(reset_btn)

        bottom_layout.addStretch()

        rebuild_btn = QPushButton("🔄 立即重建C盘")
        rebuild_btn.clicked.connect(self._rebuild_c_drive)
        bottom_layout.addWidget(rebuild_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.dialog.reject)
        bottom_layout.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save)
        bottom_layout.addWidget(save_btn)

        main_layout.addLayout(bottom_layout)

    def _refresh_paths_list(self):
        """刷新路径列表"""
        while self.paths_layout.count() > 1:
            item = self.paths_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.path_vars.clear()
        paths = self.config_mgr.get_c_scan_paths()

        if not paths:
            empty_label = QLabel("（暂无目录，请点击上方按钮添加）")
            empty_label.setFont(QFont("微软雅黑", 9))
            empty_label.setStyleSheet("color: gray;")
            self.paths_layout.insertWidget(0, empty_label)
            self._update_stats()
            return

        for i, item in enumerate(paths):
            path = item.get("path", "")
            enabled = item.get("enabled", True)

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(5, 2, 5, 2)
            row_layout.setSpacing(8)

            cb = QCheckBox()
            cb.setChecked(enabled)
            cb.stateChanged.connect(self._update_stats)
            self.path_vars[path] = cb
            row_layout.addWidget(cb)

            path_exists = os.path.isdir(path)
            max_len = 55
            if len(path) > max_len:
                display_path = path[:20] + "..." + path[-(max_len - 23) :]
            else:
                display_path = path

            if not path_exists:
                display_path = f"{display_path}  (不存在)"

            path_label = QLabel(display_path)
            path_label.setFont(QFont("Consolas", 9))
            path_label.setStyleSheet(f"color: {'#333' if path_exists else 'red'};")
            path_label.setToolTip(path)
            path_label.setCursor(Qt.PointingHandCursor)
            row_layout.addWidget(path_label, 1)

            del_btn = QPushButton("✕")
            del_btn.setFixedWidth(30)
            del_btn.setStyleSheet("color: red;")
            del_btn.clicked.connect(lambda checked, p=path: self._delete_path(p))
            row_layout.addWidget(del_btn)

            self.paths_layout.insertWidget(i, row_widget)

        self._update_stats()

    def _select_all(self):
        for cb in self.path_vars.values():
            cb.setChecked(True)
        self._update_stats()

    def _select_none(self):
        for cb in self.path_vars.values():
            cb.setChecked(False)
        self._update_stats()

    def _select_invert(self):
        for cb in self.path_vars.values():
            cb.setChecked(not cb.isChecked())
        self._update_stats()

    def _update_stats(self):
        total = len(self.path_vars)
        enabled = sum(1 for cb in self.path_vars.values() if cb.isChecked())
        self.stat_label.setText(f"共 {total} 个目录，已启用 {enabled} 个")

    def _browse_add(self):
        path = QFileDialog.getExistingDirectory(self.dialog, "选择C盘目录", "C:\\")
        if path:
            self._add_path(path)

    def _manual_add(self):
        text, ok = QInputDialog.getText(
            self.dialog, "手动输入C盘目录路径", "路径:", QLineEdit.Normal, ""
        )
        if ok and text:
            self._add_path(text.strip())

    def _add_path(self, path):
        path = os.path.normpath(path)

        if not path.upper().startswith("C:"):
            QMessageBox.warning(self.dialog, "错误", "只能添加C盘路径")
            return False

        if not os.path.isdir(path):
            QMessageBox.warning(self.dialog, "错误", "路径不存在")
            return False

        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            if os.path.normpath(p["path"]).lower() == path.lower():
                QMessageBox.warning(self.dialog, "提示", "路径已存在")
                return False

        paths.append({"path": path, "enabled": True})
        self.config_mgr.set_c_scan_paths(paths)
        self._refresh_paths_list()
        return True

    def _delete_path(self, path):
        if (
            QMessageBox.question(self.dialog, "确认", f"确定删除此目录？\n{path}")
            != QMessageBox.Yes
        ):
            return

        paths = self.config_mgr.get_c_scan_paths()
        paths = [
            p
            for p in paths
            if os.path.normpath(p["path"]).lower() != os.path.normpath(path).lower()
        ]
        self.config_mgr.set_c_scan_paths(paths)
        self._refresh_paths_list()

    def _reset_default(self):
        if (
            QMessageBox.question(
                self.dialog, "确认", "确定恢复系统默认目录？\n这将清空当前列表。"
            )
            == QMessageBox.Yes
        ):
            self.config_mgr.reset_c_scan_paths()
            self._refresh_paths_list()

    def _save(self):
        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            path = p["path"]
            if path in self.path_vars:
                p["enabled"] = self.path_vars[path].isChecked()

        self.config_mgr.set_c_scan_paths(paths)

        current_paths = self.config_mgr.get_c_scan_paths()
        has_changes = self._detect_changes(current_paths)

        if has_changes:
            result = QMessageBox.question(
                self.dialog,
                "设置已保存",
                "C盘目录配置已更改。\n\n是否立即重建C盘索引？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )

            if result == QMessageBox.Yes:
                self.dialog.accept()
                self._do_rebuild_c_drive()
            elif result == QMessageBox.No:
                QMessageBox.information(
                    self.dialog, "提示", "设置已保存，稍后可手动重建C盘索引"
                )
                self.dialog.accept()
        else:
            QMessageBox.information(self.dialog, "成功", "设置已保存")
            self.dialog.accept()

    def _detect_changes(self, current_paths):
        if len(current_paths) != len(self.original_paths):
            return True

        for curr, orig in zip(current_paths, self.original_paths):
            if curr.get("path") != orig.get("path"):
                return True
            if curr.get("enabled") != orig.get("enabled"):
                return True

        return False

    def _rebuild_c_drive(self):
        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            path = p["path"]
            if path in self.path_vars:
                p["enabled"] = self.path_vars[path].isChecked()
        self.config_mgr.set_c_scan_paths(paths)

        if (
            QMessageBox.question(self.dialog, "确认", "确定立即重建C盘索引?")
            == QMessageBox.Yes
        ):
            self.dialog.accept()
            self._do_rebuild_c_drive()

    def _do_rebuild_c_drive(self):
        if self.on_rebuild_callback:
            self.on_rebuild_callback("C")


# ==================== 批量重命名对话框 ====================
class BatchRenameDialog:
    """批量重命名对话框"""

    def __init__(self, parent, targets, app):
        self.parent = parent
        self.targets = targets
        self.app = app
        self.dialog = None
        self.mode_var = "prefix"
        self.preview_lines = []

    def show(self, scope_text=""):
        """显示对话框"""
        self.dialog = QDialog(self.parent)
        self.dialog.setWindowTitle("✏ 批量重命名")
        self.dialog.setMinimumSize(780, 650)
        self.dialog.setModal(True)

        main_layout = QVBoxLayout(self.dialog)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # 标题
        title_label = QLabel("批量重命名")
        title_label.setFont(QFont("微软雅黑", 12, QFont.Bold))
        main_layout.addWidget(title_label)

        scope_label = QLabel(scope_text)
        scope_label.setFont(QFont("微软雅黑", 9))
        scope_label.setStyleSheet("color: #555;")
        main_layout.addWidget(scope_label)

        # 规则设置区域
        rule_group = QGroupBox("重命名规则")
        rule_layout = QVBoxLayout(rule_group)

        # 模式选择
        mode_layout = QHBoxLayout()

        self.mode_prefix_radio = QRadioButton("前缀 + 序号")
        self.mode_prefix_radio.setChecked(True)
        self.mode_prefix_radio.toggled.connect(self._on_mode_change)
        mode_layout.addWidget(self.mode_prefix_radio)

        self.mode_replace_radio = QRadioButton("替换文本")
        self.mode_replace_radio.toggled.connect(self._on_mode_change)
        mode_layout.addWidget(self.mode_replace_radio)

        mode_layout.addStretch()
        rule_layout.addLayout(mode_layout)

        # 前缀模式参数
        prefix_layout = QHBoxLayout()

        prefix_layout.addWidget(QLabel("新前缀:"))
        self.prefix_input = QLineEdit()
        self.prefix_input.setMaximumWidth(150)
        self.prefix_input.textChanged.connect(self._update_preview)
        prefix_layout.addWidget(self.prefix_input)

        prefix_layout.addWidget(QLabel("起始序号:"))
        self.start_num_input = QSpinBox()
        self.start_num_input.setRange(1, 99999)
        self.start_num_input.setValue(1)
        self.start_num_input.valueChanged.connect(self._update_preview)
        prefix_layout.addWidget(self.start_num_input)

        prefix_layout.addWidget(QLabel("序号位数:"))
        self.width_input = QSpinBox()
        self.width_input.setRange(1, 10)
        self.width_input.setValue(3)
        self.width_input.valueChanged.connect(self._update_preview)
        prefix_layout.addWidget(self.width_input)

        prefix_layout.addStretch()
        rule_layout.addLayout(prefix_layout)

        # 替换模式参数
        replace_layout = QHBoxLayout()

        replace_layout.addWidget(QLabel("查找文本:"))
        self.find_input = QLineEdit()
        self.find_input.setMaximumWidth(150)
        self.find_input.textChanged.connect(self._update_preview)
        replace_layout.addWidget(self.find_input)

        replace_layout.addWidget(QLabel("替换为:"))
        self.replace_input = QLineEdit()
        self.replace_input.setMaximumWidth(150)
        self.replace_input.textChanged.connect(self._update_preview)
        replace_layout.addWidget(self.replace_input)

        replace_layout.addStretch()
        rule_layout.addLayout(replace_layout)

        main_layout.addWidget(rule_group)

        # 预览区域
        preview_group = QGroupBox("预览")
        preview_layout = QVBoxLayout(preview_group)

        self.preview_text = QTextEdit()
        self.preview_text.setFont(QFont("Consolas", 9))
        self.preview_text.setReadOnly(True)
        self.preview_text.setMinimumHeight(250)
        preview_layout.addWidget(self.preview_text)

        main_layout.addWidget(preview_group, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()

        preview_btn = QPushButton("预览效果")
        preview_btn.clicked.connect(self._update_preview)
        btn_layout.addWidget(preview_btn)

        execute_btn = QPushButton("执行重命名")
        execute_btn.clicked.connect(self._do_rename)
        btn_layout.addWidget(execute_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.dialog.reject)
        btn_layout.addWidget(close_btn)

        main_layout.addLayout(btn_layout)

        self._update_preview()
        self.dialog.exec_()

    def _on_mode_change(self):
        self._update_preview()

    def _update_preview(self):
        """更新预览"""
        self.preview_text.clear()
        self.preview_lines = []

        if not self.targets:
            self.preview_text.setPlainText("（没有可重命名的项目）")
            return

        mode = "prefix" if self.mode_prefix_radio.isChecked() else "replace"

        if mode == "prefix":
            prefix = self.prefix_input.text()
            start = self.start_num_input.value()
            width = self.width_input.value()

            num = start
            for item in self.targets:
                old_full = item["fullpath"]
                old_name = item["filename"]
                name, ext = os.path.splitext(old_name)
                new_name = f"{prefix}{str(num).zfill(width)}{ext}"
                num += 1
                new_full = os.path.join(os.path.dirname(old_full), new_name)
                self.preview_lines.append((old_full, new_full))
        else:
            find = self.find_input.text()
            replace = self.replace_input.text()
            for item in self.targets:
                old_full = item["fullpath"]
                old_name = item["filename"]
                name, ext = os.path.splitext(old_name)
                if find:
                    new_name = name.replace(find, replace) + ext
                else:
                    new_name = old_name
                new_full = os.path.join(os.path.dirname(old_full), new_name)
                self.preview_lines.append((old_full, new_full))

        lines = []
        for old_full, new_full in self.preview_lines:
            old_name = os.path.basename(old_full)
            new_name = os.path.basename(new_full)
            mark = ""
            if old_full == new_full:
                mark = "  (未变化)"
            else:
                if (
                    os.path.exists(new_full)
                    and os.path.normpath(old_full).lower()
                    != os.path.normpath(new_full).lower()
                ):
                    mark = "  (⚠ 目标已存在)"
            lines.append(f"{old_name}  →  {new_name}{mark}")

        self.preview_text.setPlainText("\n".join(lines))

    def _do_rename(self):
        """执行重命名"""
        if not self.preview_lines:
            QMessageBox.warning(self.dialog, "提示", "没有可执行的重命名记录")
            return

        if (
            QMessageBox.question(
                self.dialog, "确认", "确定执行重命名？\n请先确认预览无误。"
            )
            != QMessageBox.Yes
        ):
            return

        success = 0
        skipped = 0
        failed = 0
        renamed_pairs = []

        for old_full, new_full in self.preview_lines:
            if old_full == new_full:
                skipped += 1
                continue
            try:
                if (
                    os.path.exists(new_full)
                    and os.path.normpath(old_full).lower()
                    != os.path.normpath(new_full).lower()
                ):
                    skipped += 1
                    continue
                os.rename(old_full, new_full)
                success += 1
                renamed_pairs.append((old_full, new_full))
            except Exception as e:
                failed += 1
                logger.error(f"[重命名失败] {old_full} -> {new_full} - {e}")

        if renamed_pairs:
            with self.app.results_lock:
                for old_full, new_full in renamed_pairs:
                    old_norm = os.path.normpath(old_full)
                    new_norm = os.path.normpath(new_full)
                    new_name = os.path.basename(new_norm)
                    new_dir = os.path.dirname(new_norm)

                    for item in self.app.all_results:
                        if os.path.normpath(item.get("fullpath", "")) == old_norm:
                            item["fullpath"] = new_norm
                            item["filename"] = new_name
                            item["dir_path"] = new_dir
                            break

                    for item in self.app.filtered_results:
                        if os.path.normpath(item.get("fullpath", "")) == old_norm:
                            item["fullpath"] = new_norm
                            item["filename"] = new_name
                            item["dir_path"] = new_dir
                            break

                    if hasattr(self.app, "shown_paths"):
                        self.app.shown_paths.discard(old_norm)
                        self.app.shown_paths.add(new_norm)

                self.app.current_page = 1

        try:
            self.app._render_page()
        except Exception as e:
            logger.error(f"[同步] 刷新界面失败: {e}")

        self.app.status.setText(
            f"批量重命名完成：成功 {success}，跳过 {skipped}，失败 {failed}"
        )
        QMessageBox.information(
            self.dialog,
            "完成",
            f"重命名完成：成功 {success}，跳过 {skipped}，失败 {failed}",
        )
        self.dialog.accept()

    # ==================== 搜索工作线程 ====================


class IndexSearchWorker(QThread):
    """索引搜索工作线程"""

    batch_ready = Signal(list)
    finished = Signal(float)
    error = Signal(str)

    def __init__(self, index_mgr, keyword, scope_targets, regex_mode, fuzzy_mode):
        super().__init__()
        self.index_mgr = index_mgr
        self.keyword_str = keyword
        self.keywords = keyword.lower().split()
        self.scope_targets = scope_targets
        self.regex_mode = regex_mode
        self.fuzzy_mode = fuzzy_mode
        self.stopped = False

    def stop(self):
        self.stopped = True

    def _match(self, filename):
        """匹配文件名"""
        if self.regex_mode:
            try:
                return re.search(self.keyword_str, filename, re.IGNORECASE)
            except re.error:
                return False
        if self.fuzzy_mode:
            return all(fuzzy_match(kw, filename) >= 50 for kw in self.keywords)
        return all(kw in filename.lower() for kw in self.keywords)

    def run(self):
        """运行搜索"""
        start_time = time.time()
        try:
            results = self.index_mgr.search(self.keywords, self.scope_targets)
            if results is None:
                self.error.emit("索引不可用或搜索失败")
                return

            batch = []
            for fn, fp, sz, mt, is_dir in results:
                if self.stopped:
                    return

                if not self._match(fn):
                    continue

                ext = os.path.splitext(fn)[1].lower()
                tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
                batch.append(
                    {
                        "filename": fn,
                        "fullpath": fp,
                        "dir_path": os.path.dirname(fp),
                        "size": sz,
                        "mtime": mt,
                        "type_code": tc,
                        "size_str": (
                            "📂 文件夹"
                            if tc == 0
                            else ("📦 压缩包" if tc == 1 else format_size(sz))
                        ),
                        "mtime_str": format_time(mt),
                    }
                )
                if len(batch) >= 200:
                    self.batch_ready.emit(list(batch))
                    batch.clear()

            if batch:
                self.batch_ready.emit(batch)
            self.finished.emit(time.time() - start_time)
        except Exception as e:
            logger.error(f"索引搜索线程错误: {e}")
            self.error.emit(str(e))


class RealtimeSearchWorker(QThread):
    """实时搜索工作线程"""

    batch_ready = Signal(list)
    progress = Signal(int, float)
    finished = Signal(float)
    error = Signal(str)

    def __init__(self, keyword, scope_targets, regex_mode, fuzzy_mode):
        super().__init__()
        self.keyword_str = keyword
        self.keywords = keyword.lower().split()
        self.scope_targets = scope_targets
        self.regex_mode = regex_mode
        self.fuzzy_mode = fuzzy_mode
        self.stopped = False
        self.is_paused = False

    def stop(self):
        self.stopped = True

    def toggle_pause(self, paused):
        self.is_paused = paused

    def _match(self, filename):
        """匹配文件名"""
        if self.regex_mode:
            try:
                return re.search(self.keyword_str, filename, re.IGNORECASE)
            except re.error:
                return False
        if self.fuzzy_mode:
            return all(fuzzy_match(kw, filename) >= 50 for kw in self.keywords)
        return all(kw in filename.lower() for kw in self.keywords)

    def run(self):
        """运行搜索"""
        start_time = time.time()
        try:
            task_queue = queue.Queue()
            for t in self.scope_targets:
                if os.path.isdir(t):
                    task_queue.put(t)

            active_threads = [0]
            lock = threading.Lock()
            scanned_dirs = [0]

            def worker():
                local_batch = []
                while not self.stopped:
                    while self.is_paused:
                        if self.stopped:
                            return
                        time.sleep(0.1)
                    try:
                        cur = task_queue.get(timeout=0.1)
                    except queue.Empty:
                        with lock:
                            if task_queue.empty() and active_threads[0] <= 1:
                                break
                        continue

                    with lock:
                        active_threads[0] += 1
                        scanned_dirs[0] += 1

                    if should_skip_path(cur.lower()):
                        with lock:
                            active_threads[0] -= 1
                        continue

                    try:
                        with os.scandir(cur) as it:
                            for e in it:
                                if self.stopped:
                                    return
                                if not e.name or e.name.startswith((".", "$")):
                                    continue
                                try:
                                    is_dir = e.is_dir()
                                    st = e.stat(follow_symlinks=False)
                                except (OSError, PermissionError):
                                    continue

                                if self._match(e.name):
                                    ext = os.path.splitext(e.name)[1].lower()
                                    tc = (
                                        0
                                        if is_dir
                                        else (1 if ext in ARCHIVE_EXTS else 2)
                                    )
                                    local_batch.append(
                                        {
                                            "filename": e.name,
                                            "fullpath": e.path,
                                            "dir_path": cur,
                                            "size": st.st_size,
                                            "mtime": st.st_mtime,
                                            "type_code": tc,
                                            "size_str": (
                                                "📂 文件夹"
                                                if tc == 0
                                                else (
                                                    "📦 压缩包"
                                                    if tc == 1
                                                    else format_size(st.st_size)
                                                )
                                            ),
                                            "mtime_str": format_time(st.st_mtime),
                                        }
                                    )

                                if is_dir and not should_skip_dir(e.name.lower()):
                                    task_queue.put(e.path)

                                if len(local_batch) >= 50:
                                    self.batch_ready.emit(list(local_batch))
                                    local_batch.clear()
                                    elapsed = time.time() - start_time
                                    speed = (
                                        scanned_dirs[0] / elapsed if elapsed > 0 else 0
                                    )
                                    self.progress.emit(scanned_dirs[0], speed)
                    except (PermissionError, OSError):
                        pass
                    with lock:
                        active_threads[0] -= 1
                if local_batch:
                    self.batch_ready.emit(local_batch)

            threads = [threading.Thread(target=worker, daemon=True) for _ in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            if not self.stopped:
                self.finished.emit(time.time() - start_time)
        except Exception as e:
            logger.error(f"实时搜索线程错误: {e}")
            self.error.emit(str(e))

    # ==================== 主程序 ====================


class SearchApp(QMainWindow):
    """主应用程序窗口"""

    def __init__(self, db_path=None):
        super().__init__()

        self.config_mgr = ConfigManager()
        self.setWindowTitle("🚀 极速文件搜索 V42 增强版")
        self.resize(1400, 900)

        # 初始化变量
        self.results_lock = threading.Lock()
        self.is_searching = False
        self.is_paused = False
        self.stop_event = False
        self.total_found = 0
        self.current_search_id = 0
        self.all_results = []
        self.filtered_results = []
        self.page_size = 1000
        self.current_page = 1
        self.total_pages = 1
        self.item_meta = {}
        self.start_time = 0.0
        self.last_search_params = None
        self.force_realtime = False
        self.fuzzy_var = True
        self.regex_var = False
        self.shown_paths = set()
        self.last_render_time = 0
        self.render_interval = 0.15
        self.last_search_scope = None
        self.full_search_results = []
        self.worker = None

        # 排序状态
        self.sort_column_index = -1
        self.sort_order = Qt.AscendingOrder

        # 索引管理器
        self.index_mgr = IndexManager(db_path=db_path, config_mgr=self.config_mgr)
        self.file_watcher = UsnFileWatcher(self.index_mgr, config_mgr=self.config_mgr)
        self.index_build_stop = False
        
        # ★ 连接文件变更信号
        self.file_watcher.files_changed.connect(self._on_files_changed)

        # ★ 添加自动刷新定时器
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._auto_refresh_status)
        self.status_timer.start(5000)  # 每5秒刷新一次

        # 托盘和热键管理器
        self.tray_mgr = TrayManager(self)
        self.hotkey_mgr = HotkeyManager(self)
        self.mini_search = MiniSearchWindow(self)

        # 连接信号
        self.index_mgr.progress_signal.connect(self.on_build_progress)
        self.index_mgr.build_finished_signal.connect(self.on_build_finished)
        self.index_mgr.fts_finished_signal.connect(self.on_fts_finished)

        # 构建UI
        self._build_menubar()
        self._build_ui()
        self._bind_shortcuts()

        # 初始化托盘和热键
        self._init_tray_and_hotkey() 

        # ★ 启动时加载 DIR_CACHE（加速首次变化检测）
        QTimer.singleShot(100, self._load_dir_cache_all)

        # 启动时检查索引
        QTimer.singleShot(500, self._check_index)



    def on_build_progress(self, count, message):
        """处理构建进度"""
        self.status.setText(f"🔄 构建中... ({count:,})")
        self.status_path.setText(message)

    def on_build_finished(self):
        """处理构建完成"""
        self.index_mgr.force_reload_stats()
        self._check_index()
        self.status_path.setText("")
        self.status.setText(f"✅ 索引完成 ({self.index_mgr.file_count:,})")
        self.file_watcher.stop()
        self.file_watcher.start(self._get_drives())
        logger.info("👁️ 文件监控已启动")

    def _on_files_changed(self, added, deleted, deleted_paths):
        """处理文件变更信号：同步更新索引状态 + 联动移除当前结果"""
        # 1) 刷新索引状态显示
        self.index_mgr.force_reload_stats()
        self._check_index()

        # 2) 联动：把当前结果集中已删除的项目移除（含目录子项）
        if deleted_paths:
            # 做成前缀列表：目录删除要连带子项
            prefixes = []
            exact = set()

            for p in deleted_paths:
                p = os.path.normpath(p)
                exact.add(p)
                # 目录的子项前缀（不确定是文件还是目录，做前缀兜底不会错）
                prefixes.append(p.rstrip("\\/") + os.sep)

            with self.results_lock:
                def keep_item(x):
                    fp = os.path.normpath(x.get("fullpath", ""))
                    if fp in exact:
                        return False
                    for pref in prefixes:
                        if fp.startswith(pref):
                            return False
                    return True

                before = len(self.filtered_results)
                self.all_results = [x for x in self.all_results if keep_item(x)]
                self.filtered_results = [x for x in self.filtered_results if keep_item(x)]
                self.total_found = len(self.filtered_results)

            # 如果当前就在看结果页，重绘一次
            if self.is_searching is False:
                self._render_page()

        if added > 0 or deleted > 0:
            self.status.setText(f"📁 文件变更: +{added} -{deleted}")

    def _auto_refresh_status(self):
        """自动刷新状态"""
        if not self.index_mgr.is_building:
            self.index_mgr.reload_stats()  
            self._check_index()  

    def on_fts_finished(self):
        """处理FTS构建完成"""
        logger.info("接收到 FTS_DONE 信号")
        self.index_mgr.force_reload_stats()
        self._check_index()

    def _init_tray_and_hotkey(self):
        """初始化托盘和热键"""
        if self.config_mgr.get_tray_enabled():
            self.tray_mgr.start()

        if self.config_mgr.get_hotkey_enabled() and HAS_WIN32:
            self.hotkey_mgr.start()

    def _build_menubar(self):
        """构建菜单栏"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction("📤 导出结果", self.export_results, QKeySequence("Ctrl+E"))
        file_menu.addSeparator()
        file_menu.addAction("📂 打开文件", self.open_file, QKeySequence("Return"))
        file_menu.addAction("🎯 定位文件", self.open_folder, QKeySequence("Ctrl+L"))
        file_menu.addSeparator()
        file_menu.addAction("🚪 退出", self._do_quit, QKeySequence("Alt+F4"))

        # 编辑菜单
        edit_menu = menubar.addMenu("编辑(&E)")
        edit_menu.addAction("✅ 全选", self.select_all, QKeySequence("Ctrl+A"))
        edit_menu.addSeparator()
        edit_menu.addAction("📋 复制路径", self.copy_path, QKeySequence("Ctrl+C"))
        edit_menu.addAction("📄 复制文件", self.copy_file, QKeySequence("Ctrl+Shift+C"))
        edit_menu.addSeparator()
        edit_menu.addAction("🗑️ 删除", self.delete_file, QKeySequence("Delete"))

        # 搜索菜单
        search_menu = menubar.addMenu("搜索(&S)")
        search_menu.addAction("🔍 开始搜索", self.start_search, QKeySequence("Return"))
        search_menu.addAction("🔄 刷新搜索", self.refresh_search, QKeySequence("F5"))
        search_menu.addAction("⏹ 停止搜索", self.stop_search, QKeySequence("Escape"))

        # 工具菜单
        tool_menu = menubar.addMenu("工具(&T)")
        tool_menu.addAction(
            "📊 大文件扫描", self.scan_large_files, QKeySequence("Ctrl+G")
        )
        tool_menu.addAction("✏ 批量重命名", self._show_batch_rename)
        tool_menu.addSeparator()
        tool_menu.addAction("🔧 索引管理", self._show_index_mgr)
        tool_menu.addAction("🔄 重建索引", self._build_index)
        tool_menu.addSeparator()
        tool_menu.addAction("⚙️ 设置", self._show_settings)

        # 收藏菜单
        self.fav_menu = menubar.addMenu("收藏(&B)")
        self._update_favorites_menu()

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("⌨️ 快捷键列表", self._show_shortcuts)
        help_menu.addSeparator()
        help_menu.addAction("ℹ️ 关于", self._show_about)

    def _build_ui(self):
        """构建UI"""
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        # ========== 头部区域 ==========
        header = QFrame()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        # Row0: 标题、状态、工具按钮
        row0 = QHBoxLayout()
        title_lbl = QLabel("⚡ 极速搜 V42")
        title_lbl.setFont(QFont("微软雅黑", 18, QFont.Bold))
        title_lbl.setStyleSheet("color: #4CAF50;")
        row0.addWidget(title_lbl)

        sub_lbl = QLabel("🎯 增强版")
        sub_lbl.setFont(QFont("微软雅黑", 10))
        sub_lbl.setStyleSheet("color: #FF9800;")
        row0.addWidget(sub_lbl)

        self.idx_lbl = QLabel("检查中...")
        self.idx_lbl.setFont(QFont("微软雅黑", 9))
        row0.addWidget(self.idx_lbl)
        row0.addStretch()

        btn_index_mgr = QPushButton("🔧 索引管理")
        btn_index_mgr.setFixedWidth(100)
        btn_index_mgr.clicked.connect(self._show_index_mgr)
        row0.addWidget(btn_index_mgr)

        btn_export = QPushButton("📤 导出")
        btn_export.setFixedWidth(70)
        btn_export.clicked.connect(self.export_results)
        row0.addWidget(btn_export)

        btn_big = QPushButton("📊 大文件")
        btn_big.setFixedWidth(80)
        btn_big.clicked.connect(self.scan_large_files)
        row0.addWidget(btn_big)

        theme_label = QLabel("主题:")
        theme_label.setFont(QFont("微软雅黑", 9))
        row0.addWidget(theme_label)

        self.combo_theme = QComboBox()
        self.combo_theme.addItems(["light", "dark"])
        self.combo_theme.setCurrentText(self.config_mgr.get_theme())
        self.combo_theme.currentTextChanged.connect(self._on_theme_change)
        self.combo_theme.setFixedWidth(80)
        row0.addWidget(self.combo_theme)

        btn_c_drive = QPushButton("📂 C盘目录")
        btn_c_drive.setFixedWidth(90)
        btn_c_drive.clicked.connect(self._show_c_drive_settings)
        row0.addWidget(btn_c_drive)

        btn_batch = QPushButton("✏ 批量重命名")
        btn_batch.setFixedWidth(100)
        btn_batch.clicked.connect(self._show_batch_rename)
        row0.addWidget(btn_batch)

        btn_refresh_idx = QPushButton("🔄 立即同步")
        btn_refresh_idx.setFixedWidth(90)
        btn_refresh_idx.clicked.connect(self.sync_now)
        row0.addWidget(btn_refresh_idx)

        header_layout.addLayout(row0)

        # Row1: 搜索栏
        row1 = QHBoxLayout()

        self.combo_fav = QComboBox()
        self._update_fav_combo()
        self.combo_fav.setFixedWidth(110)
        self.combo_fav.currentIndexChanged.connect(self._on_fav_combo_select)
        row1.addWidget(self.combo_fav)

        self.combo_scope = QComboBox()
        self._update_drives()
        self.combo_scope.setFixedWidth(180)
        self.combo_scope.currentIndexChanged.connect(self._on_scope_change)
        row1.addWidget(self.combo_scope)

        btn_browse = QPushButton("📂 选择目录")
        btn_browse.setFixedWidth(90)
        btn_browse.clicked.connect(self._browse)
        row1.addWidget(btn_browse)

        self.entry_kw = QLineEdit()
        self.entry_kw.setFont(QFont("微软雅黑", 12))
        self.entry_kw.setPlaceholderText("请输入搜索关键词...")
        self.entry_kw.returnPressed.connect(self.start_search)
        row1.addWidget(self.entry_kw, 1)

        self.chk_fuzzy = QCheckBox("模糊")
        self.chk_fuzzy.setChecked(self.fuzzy_var)
        self.chk_fuzzy.stateChanged.connect(
            lambda s: setattr(self, "fuzzy_var", bool(s))
        )
        row1.addWidget(self.chk_fuzzy)

        self.chk_regex = QCheckBox("正则")
        self.chk_regex.setChecked(self.regex_var)
        self.chk_regex.stateChanged.connect(
            lambda s: setattr(self, "regex_var", bool(s))
        )
        row1.addWidget(self.chk_regex)

        self.chk_realtime = QCheckBox("实时")
        self.chk_realtime.setChecked(self.force_realtime)
        self.chk_realtime.stateChanged.connect(
            lambda s: setattr(self, "force_realtime", bool(s))
        )
        row1.addWidget(self.chk_realtime)

        self.btn_search = QPushButton("🚀 搜索")
        self.btn_search.setFixedWidth(90)
        self.btn_search.clicked.connect(self.start_search)
        row1.addWidget(self.btn_search)

        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.setFixedWidth(80)
        self.btn_refresh.clicked.connect(self.refresh_search)
        self.btn_refresh.setEnabled(False)
        row1.addWidget(self.btn_refresh)

        self.btn_pause = QPushButton("⏸ 暂停")
        self.btn_pause.setFixedWidth(80)
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_pause.setEnabled(False)
        row1.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setFixedWidth(80)
        self.btn_stop.clicked.connect(self.stop_search)
        self.btn_stop.setEnabled(False)
        row1.addWidget(self.btn_stop)

        header_layout.addLayout(row1)

        # Row2: 筛选栏
        row2 = QHBoxLayout()

        row2.addWidget(QLabel("筛选:"))

        row2.addWidget(QLabel("格式"))
        self.ext_var = QComboBox()
        self.ext_var.addItem("全部")
        self.ext_var.currentIndexChanged.connect(lambda i: self._apply_filter())
        self.ext_var.setFixedWidth(150)
        row2.addWidget(self.ext_var)

        row2.addWidget(QLabel("大小"))
        self.size_var = QComboBox()
        self.size_var.addItems(["不限", ">1MB", ">10MB", ">100MB", ">500MB", ">1GB"])
        self.size_var.currentIndexChanged.connect(lambda i: self._apply_filter())
        self.size_var.setFixedWidth(100)
        row2.addWidget(self.size_var)

        row2.addWidget(QLabel("时间"))
        self.date_var = QComboBox()
        self.date_var.addItems(["不限", "今天", "3天内", "7天内", "30天内", "今年"])
        self.date_var.currentIndexChanged.connect(lambda i: self._apply_filter())
        self.date_var.setFixedWidth(100)
        row2.addWidget(self.date_var)

        btn_clear_filter = QPushButton("清除")
        btn_clear_filter.setFixedWidth(60)
        btn_clear_filter.clicked.connect(self._clear_filter)
        row2.addWidget(btn_clear_filter)

        row2.addStretch()
        self.lbl_filter = QLabel("")
        self.lbl_filter.setFont(QFont("微软雅黑", 9))
        self.lbl_filter.setStyleSheet("color: #666;")
        row2.addWidget(self.lbl_filter)

        header_layout.addLayout(row2)
        root_layout.addWidget(header)

        # ========== 结果区域 ==========
        body = QFrame()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            ["📄 文件名", "📂 所在目录", "📊 大小/类型", "🕒 修改时间"]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self.on_dblclick)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_menu)

        # ★ 用样式表设置交替背景色
        self.tree.setStyleSheet("""
            QTreeWidget {
                alternate-background-color: #f8f9fa;
                background-color: #ffffff;
            }
            QTreeWidget::item {
                padding: 2px;
            }
            QTreeWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """)

        header_view = self.tree.header()
        header_view.setSortIndicatorShown(True)
        header_view.setSectionsClickable(True)
        header_view.sectionClicked.connect(self.sort_column)

        header_view.setSectionResizeMode(0, QHeaderView.Interactive)
        header_view.setSectionResizeMode(1, QHeaderView.Interactive)
        header_view.setSectionResizeMode(2, QHeaderView.Interactive)
        header_view.setSectionResizeMode(3, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 400)
        self.tree.setColumnWidth(1, 450)
        self.tree.setColumnWidth(2, 120)
        self.tree.setColumnWidth(3, 140)

        body_layout.addWidget(self.tree)

        # 分页栏
        pg = QFrame()
        pg_layout = QHBoxLayout(pg)
        pg_layout.setContentsMargins(5, 5, 5, 5)
        pg_layout.setSpacing(5)
        pg_layout.addStretch()

        self.btn_first = QPushButton("⏮")
        self.btn_first.setEnabled(False)
        self.btn_first.clicked.connect(lambda: self.go_page("first"))
        pg_layout.addWidget(self.btn_first)

        self.btn_prev = QPushButton("◀")
        self.btn_prev.setEnabled(False)
        self.btn_prev.clicked.connect(lambda: self.go_page("prev"))
        pg_layout.addWidget(self.btn_prev)

        self.lbl_page = QLabel("第 1/1 页 (0项)")
        self.lbl_page.setFont(QFont("微软雅黑", 9))
        pg_layout.addWidget(self.lbl_page)

        self.btn_next = QPushButton("▶")
        self.btn_next.setEnabled(False)
        self.btn_next.clicked.connect(lambda: self.go_page("next"))
        pg_layout.addWidget(self.btn_next)

        self.btn_last = QPushButton("⏭")
        self.btn_last.setEnabled(False)
        self.btn_last.clicked.connect(lambda: self.go_page("last"))
        pg_layout.addWidget(self.btn_last)

        # ===== 分页按钮样式：稍小一点，但左右箭头更清晰 =====
        common_style = """
            QPushButton {
                border: 1px solid #cbd5e0;
                border-radius: 7px;
                background: #ffffff;
                color: #1a202c;
            }
            QPushButton:hover { background: #edf2f7; }
            QPushButton:pressed { background: #e2e8f0; }
            QPushButton:disabled { color: #a0aec0; background: #f7fafc; }
        """

        for b in (self.btn_first, self.btn_prev, self.btn_next, self.btn_last):
            b.setFixedHeight(30)                 # 高度稍小
            b.setFont(QFont("微软雅黑", 12, QFont.Bold))
            b.setStyleSheet(common_style)

        # 左右箭头更宽，避免“缩小看不清”
        self.btn_prev.setFixedWidth(56)
        self.btn_next.setFixedWidth(56)

        # 首页/末页稍窄一点
        self.btn_first.setFixedWidth(44)
        self.btn_last.setFixedWidth(44)

        pg_layout.addStretch()
        body_layout.addWidget(pg)

        root_layout.addWidget(body, 1)

        # ========== 状态栏 ==========
        self.status = QLabel("就绪")
        self.status_path = QLabel("")
        self.status_path.setFont(QFont("Consolas", 8))
        self.status_path.setStyleSheet("color: #718096;")

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(200)
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)

        statusbar = QStatusBar()
        statusbar.addWidget(self.status, 1)
        statusbar.addWidget(self.status_path, 3)
        statusbar.addPermanentWidget(self.progress, 0)
        self.setStatusBar(statusbar)

    def _bind_shortcuts(self):
        """绑定快捷键"""
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.entry_kw.setFocus())
        QShortcut(QKeySequence("Ctrl+A"), self, self.select_all)
        QShortcut(QKeySequence("Ctrl+C"), self, self.copy_path)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self.copy_file)
        QShortcut(QKeySequence("Ctrl+E"), self, self.export_results)
        QShortcut(QKeySequence("Ctrl+G"), self, self.scan_large_files)
        QShortcut(QKeySequence("Ctrl+L"), self, self.open_folder)
        QShortcut(QKeySequence("F5"), self, self.refresh_search)
        QShortcut(QKeySequence("Delete"), self, self.delete_file)
        QShortcut(
            QKeySequence("Escape"),
            self,
            lambda: self.stop_search() if self.is_searching else self.entry_kw.clear(),
        )

        self.entry_kw.installEventFilter(self)

    def eventFilter(self, obj, event):
        """事件过滤器"""
        if obj == self.entry_kw and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Down:
                if self.tree.topLevelItemCount() > 0:
                    item = self.tree.topLevelItem(0)
                    self.tree.setCurrentItem(item)
                    self.tree.setFocus()
                return True
        return super().eventFilter(obj, event)

    # ==================== 索引状态检查 ====================
    def _check_index(self):
        """检查索引状态"""
        s = self.index_mgr.get_stats()
        fts = "FTS5✅" if s.get("has_fts") else "FTS5❌"
        mft = "MFT✅" if s.get("used_mft") else "MFT❌"

        time_info = ""
        if s["time"]:
            last_update = datetime.datetime.fromtimestamp(s["time"])
            time_diff = datetime.datetime.now() - last_update
            if time_diff.days > 0:
                time_info = f" ({time_diff.days}天前)"
            elif time_diff.seconds > 3600:
                time_info = f" ({time_diff.seconds//3600}小时前)"
            else:
                time_info = f" ({time_diff.seconds//60}分钟前)"

        if s["building"]:
            txt = f"🔄 构建中({s['count']:,}) [{fts}][{mft}]"
            self.idx_lbl.setStyleSheet("color: orange;")
        elif s["ready"]:
            txt = f"✅ 就绪({s['count']:,}){time_info} [{fts}][{mft}]"
            self.idx_lbl.setStyleSheet("color: green;")

            # ★ 索引就绪时：先加载 DIR_CACHE，再启动 USN 监控
            if not self.file_watcher.running:
                self._load_dir_cache_all()
                self.file_watcher.start(self._get_drives())
                logger.info("👁️ 文件监控已启动（索引已存在）")
        else:
            txt = f"❌ 未构建 [{fts}][{mft}]"
            self.idx_lbl.setStyleSheet("color: red;")
            
        self.idx_lbl.setText(txt)

    def sync_now(self):
        """立即同步：刷新统计 + 触发 USN 立刻检查一次"""
        try:
            # 1) 强制刷新 stats（COUNT、build_time、used_mft 等）
            self.index_mgr.force_reload_stats()
            self._check_index()

            # 2) 触发 USN 立刻检查一次（把刚发生的变化马上写进库）
            if hasattr(self, "file_watcher") and self.file_watcher:
                if hasattr(self.file_watcher, "poll_once"):
                    self.file_watcher.poll_once()

            # 3) 再刷新一次 stats（因为 poll_once 可能写库）
            self.index_mgr.force_reload_stats()
            self._check_index()

            self.status.setText("✅ 已立即同步")
        except Exception as e:
            logger.error(f"立即同步失败: {e}")
            self.status.setText("⚠️ 立即同步失败")

    # ==================== 磁盘和收藏夹 ====================
    def _get_drives(self):
        """获取所有磁盘"""
        if IS_WINDOWS:
            return [
                f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")
            ]
        return ["/"]

    def _load_dir_cache_all(self):
        """启动时加载各盘 DIR_CACHE（如果存在）"""
        if not HAS_RUST_ENGINE:
            return

        try:
            for d in self._get_drives():
                letter = d[0].upper()
                cache_path = _dir_cache_file(letter)
                if os.path.exists(cache_path):
                    b = cache_path.encode("utf-8")
                    ok = RUST_ENGINE.load_dir_cache(ord(letter), b, len(b))
                    if ok == 1:
                        logger.info(f"✅ DIR_CACHE 已加载: {letter} -> {cache_path}")
                    else:
                        logger.info(f"⚠️ DIR_CACHE 加载失败(会自动重建): {letter} -> {cache_path}")
        except Exception as e:
            logger.warning(f"加载 DIR_CACHE 失败: {e}")

    def _save_dir_cache_all(self):
        """退出时保存各盘 DIR_CACHE"""
        if not HAS_RUST_ENGINE:
            return

        try:
            for d in self._get_drives():
                letter = d[0].upper()
                cache_path = _dir_cache_file(letter)
                b = cache_path.encode("utf-8")
                ok = RUST_ENGINE.save_dir_cache(ord(letter), b, len(b))
                if ok == 1:
                    logger.info(f"💾 DIR_CACHE 已保存: {letter} -> {cache_path}")
        except Exception as e:
            logger.warning(f"保存 DIR_CACHE 失败: {e}")

    def _update_drives(self):
        """更新磁盘列表"""
        self.combo_scope.clear()
        self.combo_scope.addItem("所有磁盘 (全盘)")
        self.combo_scope.addItems(self._get_drives())
        self.combo_scope.setCurrentIndex(0)

    def _browse(self):
        """浏览目录"""
        d = QFileDialog.getExistingDirectory(self, "选择目录")
        if d:
            self.combo_scope.setCurrentText(d)

    def _get_search_scope_targets(self):
        """获取搜索范围目标"""
        return parse_search_scope(
            self.combo_scope.currentText(), self._get_drives, self.config_mgr
        )

    def _on_scope_change(self, index):
        """搜索范围改变"""
        if not self.entry_kw.text().strip() or self.is_searching:
            return

        current_scope = self.combo_scope.currentText()

        if self.last_search_scope == "所有磁盘 (全盘)" and self.full_search_results:
            if "所有磁盘" in current_scope:
                with self.results_lock:
                    self.all_results = list(self.full_search_results)
                    self.filtered_results = list(self.all_results)
                self._apply_filter()
                self.status.setText(f"✅ 显示全部结果: {len(self.filtered_results)}项")
            else:
                self._filter_by_drive(current_scope)
        else:
            self.start_search()

    def _filter_by_drive(self, drive_path):
        """按磁盘筛选"""
        if not self.full_search_results:
            return

        drive_letter = drive_path.rstrip("\\").upper()

        with self.results_lock:
            self.all_results = []
            for item in self.full_search_results:
                item_drive = item["fullpath"][:2].upper()
                if item_drive == drive_letter[:2]:
                    self.all_results.append(item)
            self.filtered_results = list(self.all_results)

        self._apply_filter()
        self.status.setText(f"✅ 筛选 {drive_letter}: {len(self.filtered_results)}项")
        self.lbl_filter.setText(
            f"磁盘筛选: {len(self.filtered_results)}/{len(self.full_search_results)}"
        )

    # ==================== 收藏夹功能 ====================
    def _update_fav_combo(self):
        """更新收藏夹下拉框"""
        favorites = self.config_mgr.get_favorites()
        values = (
            ["⭐ 收藏夹"] + [f"📁 {fav['name']}" for fav in favorites]
            if favorites
            else ["⭐ 收藏夹", "(无收藏)"]
        )
        self.combo_fav.clear()
        self.combo_fav.addItems(values)
        self.combo_fav.setCurrentIndex(0)

    def _on_fav_combo_select(self, index):
        """收藏夹选择"""
        sel = self.combo_fav.currentText()
        if sel == "⭐ 收藏夹" or sel == "(无收藏)":
            self.combo_fav.setCurrentIndex(0)
            return

        name = sel.replace("📁 ", "")
        for fav in self.config_mgr.get_favorites():
            if fav["name"] == name:
                if os.path.exists(fav["path"]):
                    self.combo_scope.setCurrentText(fav["path"])
                else:
                    QMessageBox.warning(self, "警告", f"目录不存在: {fav['path']}")
                break

        QTimer.singleShot(100, lambda: self.combo_fav.setCurrentIndex(0))

    def _update_favorites_menu(self):
        """更新收藏夹菜单"""
        self.fav_menu.clear()
        self.fav_menu.addAction("⭐ 收藏当前目录", self._add_current_to_favorites)
        self.fav_menu.addAction("📂 管理收藏夹", self._manage_favorites)
        self.fav_menu.addSeparator()

        favorites = self.config_mgr.get_favorites()
        if favorites:
            for fav in favorites:
                act = self.fav_menu.addAction(f"📁 {fav['name']}")
                act.triggered.connect(
                    lambda checked=False, p=fav["path"]: self._goto_favorite(p)
                )
        else:
            act = self.fav_menu.addAction("(无收藏)")
            act.setEnabled(False)

    def _add_current_to_favorites(self):
        """添加当前目录到收藏"""
        scope = self.combo_scope.currentText()
        if "所有磁盘" in scope:
            QMessageBox.information(self, "提示", "请先选择一个具体目录")
            return
        self.config_mgr.add_favorite(scope)
        self._update_favorites_menu()
        self._update_fav_combo()
        QMessageBox.information(self, "成功", f"已收藏: {scope}")

    def _goto_favorite(self, path):
        """转到收藏目录"""
        if os.path.exists(path):
            self.combo_scope.setCurrentText(path)
        else:
            QMessageBox.warning(self, "警告", f"目录不存在: {path}")

    def _manage_favorites(self):
        """管理收藏夹"""
        dlg = QDialog(self)
        dlg.setWindowTitle("📂 管理收藏夹")
        dlg.setMinimumSize(500, 400)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        label = QLabel("收藏夹列表")
        label.setFont(QFont("微软雅黑", 11, QFont.Bold))
        layout.addWidget(label)

        from PySide6.QtWidgets import QListWidget

        listbox = QListWidget()
        layout.addWidget(listbox, 1)

        def refresh_list():
            listbox.clear()
            for fav in self.config_mgr.get_favorites():
                listbox.addItem(f"{fav['name']} - {fav['path']}")

        refresh_list()

        btn_row = QHBoxLayout()
        btn_del = QPushButton("删除选中")

        def remove_selected():
            row = listbox.currentRow()
            if row >= 0:
                favs = self.config_mgr.get_favorites()
                if row < len(favs):
                    self.config_mgr.remove_favorite(favs[row]["path"])
                    refresh_list()
                    self._update_favorites_menu()
                    self._update_fav_combo()

        btn_del.clicked.connect(remove_selected)
        btn_row.addWidget(btn_del)

        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)
        dlg.exec()

    # ==================== 主题和设置 ====================
    def _on_theme_change(self, theme):
        """主题切换"""
        self.config_mgr.set_theme(theme)
        apply_theme(QApplication.instance(), theme)
        self.status.setText(f"主题已切换: {theme}")

    def _show_settings(self):
        """显示设置对话框"""
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙️ 设置")
        dlg.setMinimumSize(400, 300)
        dlg.setModal(True)

        frame = QVBoxLayout(dlg)
        frame.setContentsMargins(20, 20, 20, 20)
        frame.setSpacing(15)

        title = QLabel("常规设置")
        title.setFont(QFont("微软雅黑", 12, QFont.Bold))
        frame.addWidget(title)

        # 热键设置
        hotkey_frame = QHBoxLayout()
        self.chk_hotkey = QCheckBox("启用全局热键 (Ctrl+Shift+Space)")
        self.chk_hotkey.setChecked(self.config_mgr.get_hotkey_enabled())
        hotkey_frame.addWidget(self.chk_hotkey)
        if not HAS_WIN32:
            lab = QLabel("(需要pywin32)")
            lab.setStyleSheet("color: gray;")
            hotkey_frame.addWidget(lab)
        hotkey_frame.addStretch()
        frame.addLayout(hotkey_frame)

        # 托盘设置
        tray_frame = QHBoxLayout()
        self.chk_tray = QCheckBox("关闭时最小化到托盘")
        self.chk_tray.setChecked(self.config_mgr.get_tray_enabled())
        tray_frame.addWidget(self.chk_tray)
        tray_frame.addStretch()
        frame.addLayout(tray_frame)

        tip = QLabel("💡 提示：修改设置后需要重启程序才能完全生效")
        tip.setFont(QFont("微软雅黑", 9))
        tip.setStyleSheet("color: #888;")
        frame.addWidget(tip)

        frame.addStretch()

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        def save_settings():
            self.config_mgr.set_hotkey_enabled(self.chk_hotkey.isChecked())
            self.config_mgr.set_tray_enabled(self.chk_tray.isChecked())

            if (
                self.chk_hotkey.isChecked()
                and not self.hotkey_mgr.registered
                and HAS_WIN32
            ):
                self.hotkey_mgr.start()
            elif not self.chk_hotkey.isChecked() and self.hotkey_mgr.registered:
                self.hotkey_mgr.stop()

            if self.chk_tray.isChecked() and not self.tray_mgr.running:
                self.tray_mgr.start()
            elif not self.chk_tray.isChecked() and self.tray_mgr.running:
                self.tray_mgr.stop()

            QMessageBox.information(dlg, "成功", "设置已保存")
            dlg.accept()

        btn_save = QPushButton("保存")
        btn_save.setFixedWidth(80)
        btn_save.clicked.connect(save_settings)
        btn_row.addWidget(btn_save)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_cancel)

        frame.addLayout(btn_row)
        dlg.exec()

    def _show_c_drive_settings(self):
        """显示C盘设置对话框"""
        dialog = CDriveSettingsDialog(
            self, self.config_mgr, self.index_mgr, self._rebuild_c_drive
        )
        dialog.show()

    def _rebuild_c_drive(self, drive_letter="C"):
        """重建C盘索引"""
        if self.index_mgr.is_building:
            QMessageBox.warning(self, "提示", "索引正在构建中，请稍后")
            return

        self.index_build_stop = False
        self.status.setText(f"🔄 正在重建 {drive_letter}: 盘索引...")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self._check_index()

        def run():
            try:
                self.index_mgr.rebuild_drive(
                    drive_letter,
                    progress_callback=None,
                    stop_fn=lambda: self.index_build_stop,
                )
            except Exception as e:
                logger.error(f"重建 {drive_letter} 盘索引失败: {e}")
            finally:
                QTimer.singleShot(0, self._on_rebuild_finished)

        threading.Thread(target=run, daemon=True).start()

    def _on_rebuild_finished(self):
        """重建完成后的回调"""
        self.index_mgr.force_reload_stats()
        self._check_index()
        self.progress.setVisible(False)
        self.status.setText(f"✅ 索引重建完成 ({self.index_mgr.file_count:,})")
        
        self.file_watcher.stop()
        self.file_watcher.start(self._get_drives())
        logger.info("👁️ 文件监控已重启")

        # ==================== 筛选功能 ====================

    def _update_ext_combo(self):
        """更新扩展名下拉框"""
        counts = {}
        with self.results_lock:
            for item in self.all_results:
                if item["type_code"] == 0:
                    ext = "📂文件夹"
                elif item["type_code"] == 1:
                    ext = "📦压缩包"
                else:
                    ext = os.path.splitext(item["filename"])[1].lower() or "(无)"
                counts[ext] = counts.get(ext, 0) + 1

        values = ["全部"] + [
            f"{ext} ({cnt})"
            for ext, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]
        ]
        self.ext_var.clear()
        self.ext_var.addItems(values)

    def _get_size_min(self):
        """获取最小大小"""
        mapping = {
            "不限": 0,
            ">1MB": 1 << 20,
            ">10MB": 10 << 20,
            ">100MB": 100 << 20,
            ">500MB": 500 << 20,
            ">1GB": 1 << 30,
        }
        return mapping.get(self.size_var.currentText(), 0)

    def _get_date_min(self):
        """获取最小日期"""
        now = time.time()
        day = 86400
        mapping = {
            "不限": 0,
            "今天": now - day,
            "3天内": now - 3 * day,
            "7天内": now - 7 * day,
            "30天内": now - 30 * day,
            "今年": time.mktime(
                datetime.datetime(datetime.datetime.now().year, 1, 1).timetuple()
            ),
        }
        return mapping.get(self.date_var.currentText(), 0)

    def _apply_filter(self):
        """应用筛选"""
        ext_sel = self.ext_var.currentText()
        size_min = self._get_size_min()
        date_min = self._get_date_min()
        target_ext = ext_sel.split(" (")[0] if ext_sel != "全部" else None

        with self.results_lock:
            self.filtered_results = []
            for item in self.all_results:
                if size_min > 0 and item["type_code"] == 2 and item["size"] < size_min:
                    continue
                if date_min > 0 and item["mtime"] < date_min:
                    continue
                if target_ext:
                    if item["type_code"] == 0:
                        item_ext = "📂文件夹"
                    elif item["type_code"] == 1:
                        item_ext = "📦压缩包"
                    else:
                        item_ext = (
                            os.path.splitext(item["filename"])[1].lower() or "(无)"
                        )
                    if item_ext != target_ext:
                        continue
                self.filtered_results.append(item)

        self.current_page = 1
        self._render_page()

        with self.results_lock:
            all_count = len(self.all_results)
            filtered_count = len(self.filtered_results)

        if ext_sel != "全部" or size_min > 0 or date_min > 0:
            self.lbl_filter.setText(f"筛选: {filtered_count}/{all_count}")
        else:
            self.lbl_filter.setText("")

    def _clear_filter(self):
        """清除筛选"""
        self.ext_var.setCurrentText("全部")
        self.size_var.setCurrentText("不限")
        self.date_var.setCurrentText("不限")
        with self.results_lock:
            self.filtered_results = list(self.all_results)
        self.current_page = 1
        self._render_page()
        self.lbl_filter.setText("")

    # ==================== 分页功能 ====================
    def _update_page_info(self):
        """更新分页信息"""
        total = len(self.filtered_results)
        self.total_pages = max(1, math.ceil(total / self.page_size))
        self.lbl_page.setText(f"第 {self.current_page}/{self.total_pages} 页 ({total}项)")
        self.btn_first.setEnabled(self.current_page > 1)
        self.btn_prev.setEnabled(self.current_page > 1)
        self.btn_next.setEnabled(self.current_page < self.total_pages)
        self.btn_last.setEnabled(self.current_page < self.total_pages)

    def go_page(self, action):
        """翻页"""
        if action == "first":
            self.current_page = 1
        elif action == "prev" and self.current_page > 1:
            self.current_page -= 1
        elif action == "next" and self.current_page < self.total_pages:
            self.current_page += 1
        elif action == "last":
            self.current_page = self.total_pages
        self._render_page()

    def _render_page(self):
        """渲染当前页（优化版：Rust 批量 stat + 减少 UI 重绘）"""
        self.tree.clear()
        self.item_meta.clear()
        self._update_page_info()

        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size

        with self.results_lock:
            page_items = self.filtered_results[start:end]

        if not page_items:
            return

        # ===== 批量获取文件信息（Rust FFI，一次调用） =====
        if HAS_RUST_ENGINE:
            try:
                need_stat_indices = []
                need_stat_paths = []

                for i, it in enumerate(page_items):
                    tc = it.get("type_code", 2)
                    if tc == 2 and it.get("size", 0) == 0:
                        need_stat_indices.append(i)
                        need_stat_paths.append(it["fullpath"])

                if need_stat_paths:
                    paths_joined = "\0".join(need_stat_paths)
                    paths_bytes = paths_joined.encode("utf-8")
                    paths_buf = (ctypes.c_uint8 * len(paths_bytes))(*paths_bytes)

                    count = len(need_stat_paths)
                    FileInfoArray = FileInfo * count
                    results = FileInfoArray()

                    actual = RUST_ENGINE.get_file_info_batch(
                        paths_buf,
                        len(paths_bytes),
                        results,
                        count
                    )

                    for j in range(actual):
                        idx = need_stat_indices[j]
                        if results[j].exists:
                            page_items[idx]["size"] = results[j].size
                            page_items[idx]["mtime"] = results[j].mtime

                    if actual > 0 and self.index_mgr.conn:
                        updates = []
                        for j in range(actual):
                            if results[j].exists:
                                updates.append((
                                    results[j].size,
                                    results[j].mtime,
                                    need_stat_paths[j]
                                ))
                        if updates:
                            threading.Thread(
                                target=self._write_back_stat,
                                args=(updates,),
                                daemon=True
                            ).start()

            except Exception as e:
                logger.debug(f"Rust 批量 stat 失败，回退: {e}")
                self._fallback_stat(page_items)
        else:
            self._fallback_stat(page_items)

        # ===== 格式化显示字符串 =====
        for it in page_items:
            tc = it.get("type_code", 2)
            if tc == 0:
                it["size_str"] = "📂 文件夹"
            elif tc == 1:
                it["size_str"] = "📦 压缩包"
            else:
                it["size_str"] = format_size(it.get("size", 0))
            it["mtime_str"] = format_time(it.get("mtime", 0))

        # ===== 渲染 UI（关闭更新减少重绘） =====
        self.tree.setUpdatesEnabled(False)
        try:
            for i, item in enumerate(page_items):
                row_data = [
                    item.get("filename", ""),
                    item.get("dir_path", ""),
                    item.get("size_str", ""),
                    item.get("mtime_str", ""),
                ]
                q_item = QTreeWidgetItem(row_data)

                q_item.setData(2, Qt.UserRole, item.get("size", 0))
                q_item.setData(3, Qt.UserRole, item.get("mtime", 0))

                self.tree.addTopLevelItem(q_item)
                self.item_meta[id(q_item)] = start + i
        finally:
            self.tree.setUpdatesEnabled(True)

    def _write_back_stat(self, updates):
        """异步写回 stat 结果到数据库"""
        try:
            with self.index_mgr.lock:
                cursor = self.index_mgr.conn.cursor()
                cursor.executemany(
                    "UPDATE files SET size=?, mtime=? WHERE full_path=?",
                    updates
                )
                if not HAS_APSW:
                    self.index_mgr.conn.commit()
        except Exception as e:
            logger.debug(f"stat 写回数据库失败: {e}")

    def _fallback_stat(self, page_items):
        """回退到 Python 批量 stat"""
        try:
            tmp = []
            for it in page_items:
                fullpath = it.get("fullpath", "")
                filename = it.get("filename", "")
                dir_path = it.get("dir_path", "")
                is_dir = 1 if it.get("type_code") == 0 else 0
                ext = "" if is_dir else os.path.splitext(filename)[1].lower()
                tmp.append([
                    filename, filename.lower(), fullpath, dir_path, ext,
                    int(it.get("size", 0) or 0),
                    float(it.get("mtime", 0) or 0),
                    is_dir,
                ])

            _batch_stat_files(
                tmp, only_missing=True, write_back_db=True,
                db_conn=self.index_mgr.conn, db_lock=self.index_mgr.lock,
            )

            for it, t in zip(page_items, tmp):
                it["size"] = t[5]
                it["mtime"] = t[6]
        except Exception as e:
            logger.debug(f"回退 stat 失败: {e}")

    def _preload_all_stats(self):
        """后台预加载所有结果的 size/mtime"""
        try:
            with self.results_lock:
                items_to_load = [
                    it for it in self.all_results
                    if it.get("type_code", 2) == 2 and it.get("size", 0) == 0
                ]

            if not items_to_load or not HAS_RUST_ENGINE:
                return

            # 分批处理，每批 500 条
            batch_size = 500
            for i in range(0, len(items_to_load), batch_size):
                if self.is_searching or self.stop_event:
                    return  # 新搜索开始了，停止预加载

                batch = items_to_load[i:i + batch_size]
                paths = [it["fullpath"] for it in batch]

                try:
                    paths_joined = "\0".join(paths)
                    paths_bytes = paths_joined.encode("utf-8")
                    paths_buf = (ctypes.c_uint8 * len(paths_bytes))(*paths_bytes)

                    count = len(paths)
                    FileInfoArray = FileInfo * count
                    results = FileInfoArray()

                    actual = RUST_ENGINE.get_file_info_batch(
                        paths_buf,
                        len(paths_bytes),
                        results,
                        count
                    )

                    # 写回结果
                    with self.results_lock:
                        for j in range(actual):
                            if results[j].exists:
                                batch[j]["size"] = results[j].size
                                batch[j]["mtime"] = results[j].mtime

                    # 写回数据库
                    if actual > 0 and self.index_mgr.conn:
                        updates = []
                        for j in range(actual):
                            if results[j].exists:
                                updates.append((
                                    results[j].size,
                                    results[j].mtime,
                                    paths[j]
                                ))
                        if updates:
                            self._write_back_stat(updates)

                except Exception as e:
                    logger.debug(f"预加载批次失败: {e}")

                # 稍微让出 CPU
                time.sleep(0.01)

        except Exception as e:
            logger.debug(f"预加载失败: {e}")

    def sort_column(self, logical_index):
        """排序列"""
        if self.sort_column_index == logical_index:
            self.sort_order = (
                Qt.DescendingOrder
                if self.sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self.sort_column_index = logical_index
            self.sort_order = Qt.AscendingOrder

        reverse = self.sort_order == Qt.DescendingOrder

        with self.results_lock:
            if logical_index == 0:
                self.filtered_results.sort(key=lambda x: x.get("filename", "").lower(), reverse=reverse)
            elif logical_index == 1:
                self.filtered_results.sort(key=lambda x: x.get("dir_path", "").lower(), reverse=reverse)
            elif logical_index == 2:
                self.filtered_results.sort(key=lambda x: x.get("size", 0), reverse=reverse)
            elif logical_index == 3:
                self.filtered_results.sort(key=lambda x: x.get("mtime", 0), reverse=reverse)

        try:
            self.tree.header().setSortIndicator(logical_index, self.sort_order)
        except Exception:
            pass

        self.current_page = 1
        self._render_page()

    def select_all(self):
        """全选"""
        if hasattr(self, "tree") and self.tree:
            self.tree.selectAll()

    # ==================== 搜索功能 ====================
    def start_search(self):
        """开始搜索"""
        if self.is_searching:
            return

        kw = self.entry_kw.text().strip()
        if not kw:
            QMessageBox.warning(self, "提示", "请输入关键词")
            return

        self.config_mgr.add_history(kw)
        self.last_search_params = {"kw": kw}
        self.last_search_scope = self.combo_scope.currentText()

        # 清空结果
        self.tree.clear()
        self.item_meta.clear()
        self.total_found = 0
        self.current_page = 1
        self.sort_column_index = -1
        self.ext_var.setCurrentText("全部")
        self.size_var.setCurrentText("不限")
        self.date_var.setCurrentText("不限")
        self.lbl_filter.setText("")

        with self.results_lock:
            self.all_results.clear()
            self.filtered_results.clear()
            self.shown_paths.clear()

        self.is_searching = True
        self.stop_event = False
        self.btn_search.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status.setText("🔍 搜索中...")

        scope_targets = self._get_search_scope_targets()
        use_idx = (
            not self.force_realtime
            and self.index_mgr.is_ready
            and not self.index_mgr.is_building
        )

        if use_idx:
            self.status.setText("⚡ 索引搜索...")
            self.worker = IndexSearchWorker(
                self.index_mgr, kw, scope_targets, self.regex_var, self.fuzzy_var
            )
        else:
            self.status.setText("🔍 实时扫描...")
            self.worker = RealtimeSearchWorker(
                kw, scope_targets, self.regex_var, self.fuzzy_var
            )
            self.worker.progress.connect(self.on_rt_progress)

        self.worker.batch_ready.connect(self.on_batch_ready)
        self.worker.finished.connect(self.on_search_finished)
        self.worker.error.connect(self.on_search_error)
        self.worker.start()

    def refresh_search(self):
        """刷新搜索"""
        if self.last_search_params and not self.is_searching:
            self.entry_kw.setText(self.last_search_params["kw"])
            self.start_search()

    def toggle_pause(self):
        """切换暂停"""
        if (
            not self.is_searching
            or not hasattr(self, "worker")
            or not hasattr(self.worker, "toggle_pause")
        ):
            return
        self.is_paused = not self.is_paused
        self.worker.toggle_pause(self.is_paused)
        if self.is_paused:
            self.btn_pause.setText("▶ 继续")
            self.progress.setRange(0, 100)
        else:
            self.btn_pause.setText("⏸ 暂停")
            self.progress.setRange(0, 0)

    def stop_search(self):
        """停止搜索"""
        if hasattr(self, "worker") and self.worker:
            self.worker.stop()
        self._reset_ui()
        self.status.setText(f"🛑 已停止 ({self.total_found}项)")

    def _reset_ui(self):
        """重置UI状态"""
        self.is_searching = False
        self.is_paused = False
        self.btn_search.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸ 暂停")
        self.btn_stop.setEnabled(False)
        self.btn_refresh.setEnabled(True)
        self.progress.setVisible(False)

    def on_batch_ready(self, batch):
        """处理搜索批次（优化版：避免全量复制）"""
        with self.results_lock:
            for item_data in batch:
                fp = item_data["fullpath"]
                if fp not in self.shown_paths:
                    self.shown_paths.add(fp)
                    self.all_results.append(item_data)
            self.total_found = len(self.all_results)
            # ★ 不再每批都 copy，搜索中只渲染第一页

        now = time.time()
        if (
            self.total_found <= 200
            or (now - self.last_render_time) > self.render_interval
        ):
            # ★ 搜索中只取前 page_size 条渲染，不需要全量 filtered_results
            with self.results_lock:
                self.filtered_results = self.all_results[:self.page_size]
            self._render_page()
            self.last_render_time = now

        self.status.setText(f"已找到: {self.total_found}")

    def on_rt_progress(self, scanned_dirs, speed):
        """实时搜索进度"""
        self.status.setText(f"🔍 实时扫描... ({scanned_dirs:,} 目录，{speed:.0f}/s)")

    def on_search_finished(self, total_time):
        """搜索完成"""
        self._reset_ui()
        self._finalize()
        self.status.setText(f"✅ 完成: {self.total_found}项 ({total_time:.2f}s)")

    def on_search_error(self, error_msg):
        """搜索错误"""
        self._reset_ui()
        QMessageBox.warning(self, "搜索错误", error_msg)

    def _finalize(self):
        """完成搜索后的处理（全量同步）"""
        self._update_ext_combo()
        with self.results_lock:
            self.filtered_results = self.all_results[:]
            if self.last_search_scope == "所有磁盘 (全盘)":
                self.full_search_results = self.all_results[:]
        self._render_page()

        # ★ 后台预加载所有结果的 size/mtime
        threading.Thread(target=self._preload_all_stats, daemon=True).start()

        # ==================== 文件操作 ====================

    def on_dblclick(self, item, column):
        """双击打开"""
        if not item:
            return
        idx = self.item_meta.get(id(item))
        if idx is None:
            return
        with self.results_lock:
            if idx < 0 or idx >= len(self.filtered_results):
                return
            data = self.filtered_results[idx]

        if data["type_code"] == 0:
            try:
                subprocess.Popen(f'explorer "{data["fullpath"]}"')
            except Exception as e:
                logger.error(f"打开文件夹失败: {e}")
                QMessageBox.warning(self, "错误", f"无法打开文件夹: {e}")
        else:
            try:
                os.startfile(data["fullpath"])
            except Exception as e:
                logger.error(f"打开文件失败: {e}")
                QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

    def show_menu(self, pos):
        """显示右键菜单"""
        item = self.tree.itemAt(pos)
        if item:
            self.tree.setCurrentItem(item)
        ctx_menu = QMenu(self)
        ctx_menu.addAction("📂 打开文件", self.open_file)
        ctx_menu.addAction("🎯 定位文件", self.open_folder)
        ctx_menu.addAction("👁️ 预览文件", self.preview_file)
        ctx_menu.addSeparator()
        ctx_menu.addAction("📄 复制文件", self.copy_file)
        ctx_menu.addAction("📝 复制路径", self.copy_path)
        ctx_menu.addSeparator()
        ctx_menu.addAction("🗑️ 删除", self.delete_file)
        ctx_menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _get_sel(self):
        """获取选中项"""
        sel = self.tree.currentItem()
        if not sel:
            return None
        idx = self.item_meta.get(id(sel))
        if idx is None:
            return None
        with self.results_lock:
            if idx < 0 or idx >= len(self.filtered_results):
                return None
            return self.filtered_results[idx]

    def _get_selected_items(self):
        """获取所有选中项"""
        items = []
        for sel in self.tree.selectedItems():
            idx = self.item_meta.get(id(sel))
            if idx is not None:
                with self.results_lock:
                    if 0 <= idx < len(self.filtered_results):
                        items.append(self.filtered_results[idx])
        return items

    def open_file(self):
        """打开文件"""
        item = self._get_sel()
        if item:
            try:
                os.startfile(item["fullpath"])
            except Exception as e:
                logger.error(f"打开文件失败: {e}")
                QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

    def open_folder(self):
        """定位文件"""
        item = self._get_sel()
        if item:
            try:
                subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
            except Exception as e:
                logger.error(f"定位文件失败: {e}")
                QMessageBox.warning(self, "错误", f"无法定位文件: {e}")

    def copy_path(self):
        """复制路径"""
        items = self._get_selected_items()
        if items:
            paths = "\n".join(item["fullpath"] for item in items)
            QApplication.clipboard().setText(paths)
            self.status.setText(f"已复制 {len(items)} 个路径")

    def copy_file(self):
        """复制文件"""
        if not HAS_WIN32:
            QMessageBox.warning(self, "提示", "需要安装 pywin32: pip install pywin32")
            return
        items = self._get_selected_items()
        if not items:
            return
        try:
            files = [
                os.path.abspath(item["fullpath"])
                for item in items
                if os.path.exists(item["fullpath"])
            ]
            if not files:
                return

            file_str = "\0".join(files) + "\0\0"
            data = struct.pack("IIIII", 20, 0, 0, 0, 1) + file_str.encode("utf-16le")

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
            win32clipboard.CloseClipboard()
            self.status.setText(f"已复制 {len(files)} 个文件")
        except Exception as e:
            logger.error(f"复制文件失败: {e}")
            QMessageBox.warning(self, "错误", f"复制文件失败: {e}")

    def delete_file(self):
        """删除文件（同步更新结果集；删除目录会移除其子项）"""
        items = self._get_selected_items()
        if not items:
            return

        if len(items) == 1:
            msg = f"确定删除?\n{items[0]['filename']}"
        else:
            msg = f"确定删除 {len(items)} 个文件/文件夹?"

        if HAS_SEND2TRASH:
            msg += "\n\n(将移至回收站)"
        else:
            msg += "\n\n⚠️ 警告：将永久删除！"

        if (
            QMessageBox.question(self, "确认", msg, QMessageBox.Yes | QMessageBox.No)
            != QMessageBox.Yes
        ):
            return

        deleted = 0
        failed = []

        # ★ 先计算要从内存结果集中移除的路径集合（避免边删边遍历）
        remove_exact = set()   # 精确删除的 fullpath
        remove_prefix = []     # 目录前缀删除：("g:\\xxx\\",)

        for item in items:
            fp = os.path.normpath(item["fullpath"])
            remove_exact.add(fp)

            # 如果是目录：还要删除其子项
            if item.get("type_code") == 0 or item.get("is_dir") == 1:
                prefix = fp.rstrip("\\/") + os.sep
                remove_prefix.append(prefix)

        for item in items:
            try:
                # 1) 执行真实删除
                if HAS_SEND2TRASH:
                    send2trash.send2trash(item["fullpath"])
                else:
                    if item.get("type_code") == 0 or item.get("is_dir") == 1:
                        shutil.rmtree(item["fullpath"])
                    else:
                        os.remove(item["fullpath"])

                deleted += 1

            except Exception as e:
                logger.error(f"删除失败: {item['fullpath']} - {e}")
                failed.append(item["filename"])

        # 2) 同步更新内存结果集 + shown_paths
        with self.results_lock:
            # 从 shown_paths 移除：精确 + 前缀
            for p in list(self.shown_paths):
                pn = os.path.normpath(p)
                if pn in remove_exact:
                    self.shown_paths.discard(p)
                    continue
                for pref in remove_prefix:
                    if pn.startswith(pref):
                        self.shown_paths.discard(p)
                        break

            # 从 all_results / filtered_results 移除：精确 + 前缀
            def keep_item(x):
                xp = os.path.normpath(x.get("fullpath", ""))
                if xp in remove_exact:
                    return False
                for pref in remove_prefix:
                    if xp.startswith(pref):
                        return False
                return True

            self.all_results = [x for x in self.all_results if keep_item(x)]
            self.filtered_results = [x for x in self.filtered_results if keep_item(x)]
            self.total_found = len(self.filtered_results)

        # 3) 重新渲染当前页（分页安全）
        self._render_page()

        # 4) UI 提示
        if failed:
            self.status.setText(f"✅ 已删除 {deleted} 个，失败 {len(failed)} 个")
            QMessageBox.warning(
                self, "部分失败", "以下文件删除失败:\n" + "\n".join(failed[:5])
            )
        else:
            self.status.setText(f"✅ 已删除 {deleted} 个文件/文件夹")

    def preview_file(self):
        """预览文件"""
        item = self._get_sel()
        if not item:
            return

        ext = os.path.splitext(item["filename"])[1].lower()
        text_exts = {
            ".txt",
            ".log",
            ".py",
            ".json",
            ".xml",
            ".md",
            ".csv",
            ".ini",
            ".cfg",
            ".yaml",
            ".yml",
            ".js",
            ".css",
            ".sql",
            ".sh",
            ".bat",
            ".cmd",
        }

        if ext in text_exts:
            self._preview_text(item["fullpath"])
        elif item["type_code"] == 0:
            try:
                subprocess.Popen(f'explorer "{item["fullpath"]}"')
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法打开文件夹: {e}")
        else:
            try:
                os.startfile(item["fullpath"])
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

    def _preview_text(self, path):
        """预览文本文件"""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"预览: {os.path.basename(path)}")
        dlg.resize(800, 600)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(5, 5, 5, 5)

        text = QTextEdit()
        text.setFont(QFont("Consolas", 10))
        text.setReadOnly(True)
        layout.addWidget(text)

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(200000)
            if len(content) >= 200000:
                content += "\n\n... [文件过大，仅显示前200KB] ..."
            text.setPlainText(content)
        except Exception as e:
            text.setPlainText(f"无法读取文件: {e}")

        dlg.exec()

    # ==================== 索引管理 ====================
    def _show_index_mgr(self):
        """显示索引管理对话框"""
        dlg = QDialog(self)
        dlg.setWindowTitle("🔧 索引管理")
        dlg.setMinimumSize(500, 400)
        dlg.setModal(True)

        f = QVBoxLayout(dlg)
        f.setContentsMargins(15, 15, 15, 15)
        f.setSpacing(10)

        s = self.index_mgr.get_stats()

        title = QLabel("📊 索引状态")
        title.setFont(QFont("微软雅黑", 12, QFont.Bold))
        f.addWidget(title)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        f.addWidget(line)

        info = QGridLayout()
        info.setHorizontalSpacing(10)
        info.setVerticalSpacing(5)

        c_dirs = get_c_scan_dirs(self.config_mgr)
        c_dirs_str = ", ".join([os.path.basename(d) for d in c_dirs[:3]]) + (
            "..." if len(c_dirs) > 3 else ""
        )

        last_update_str = "从未"
        if s["time"]:
            last_update = datetime.datetime.fromtimestamp(s["time"])
            last_update_str = last_update.strftime("%m-%d %H:%M")

        rows = [
            ("文件数量:", f"{s['count']:,}" if s["count"] else "未构建"),
            (
                "状态:",
                (
                    "✅就绪"
                    if s["ready"]
                    else ("🔄构建中" if s["building"] else "❌未构建")
                ),
            ),
            ("FTS5:", "✅已启用" if s.get("has_fts") else "❌未启用"),
            ("MFT:", "✅已使用" if s.get("used_mft") else "❌未使用"),
            ("构建时间:", last_update_str),
            ("C盘范围:", c_dirs_str),
            ("索引路径:", os.path.basename(s["path"])),
        ]

        for i, (l, v) in enumerate(rows):
            lab = QLabel(l)
            info.addWidget(lab, i, 0)
            val = QLabel(str(v))
            if "✅" in str(v):
                val.setStyleSheet("color: #28a745;")
            elif "❌" in str(v):
                val.setStyleSheet("color: #e53e3e;")
            else:
                val.setStyleSheet("color: #555;")
            info.addWidget(val, i, 1)

        f.addLayout(info)

        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setFrameShadow(QFrame.Sunken)
        f.addWidget(line2)

        f.addStretch()

        bf = QHBoxLayout()
        bf.setSpacing(10)

        def rebuild():
            dlg.accept()
            self._build_index()

        def delete():
            if QMessageBox.question(self, "确认", "确定删除索引？") == QMessageBox.Yes:
                self.file_watcher.stop()
                self.index_mgr.close()
                for ext in ["", "-wal", "-shm"]:
                    try:
                        os.remove(self.index_mgr.db_path + ext)
                    except:
                        pass
                self.index_mgr = IndexManager(
                    db_path=self.index_mgr.db_path, config_mgr=self.config_mgr
                )
                self.index_mgr.progress_signal.connect(self.on_build_progress)
                self.index_mgr.build_finished_signal.connect(self.on_build_finished)
                self.index_mgr.fts_finished_signal.connect(self.on_fts_finished)
                self.file_watcher = UsnFileWatcher(
                    self.index_mgr, config_mgr=self.config_mgr
                )
                self._check_index()
                dlg.accept()

        btn_rebuild = QPushButton("🔄 重建索引")
        btn_rebuild.clicked.connect(rebuild)
        bf.addWidget(btn_rebuild)

        btn_delete = QPushButton("🗑️ 删除索引")
        btn_delete.clicked.connect(delete)
        bf.addWidget(btn_delete)

        bf.addStretch()

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.reject)
        bf.addWidget(btn_close)

        f.addLayout(bf)
        dlg.exec()

    def _build_index(self):
        """重建索引"""        
        if self.index_mgr.is_building:
            return

        self.index_build_stop = False
        drives = self._get_drives()

        # ===== 预热磁盘：唤醒卷/缓存元数据，减少首次构建抖动 =====
        try:
            self.status.setText("🔥 预热磁盘中(首次构建加速)...")
            self.status_path.setText("正在唤醒磁盘/加载元数据缓存...")
            self.progress.setVisible(True)
            self.progress.setRange(0, 0)
            QApplication.processEvents()

            self._warm_up_drives(drives)
        except Exception as e:
            logger.debug(f"预热失败(可忽略): {e}")

        # ===== 开始构建 =====
        self.status.setText("🔄 正在构建索引...")
        self.status_path.setText("")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        threading.Thread(
            target=self.index_mgr.build_index,
            args=(drives, lambda: self.index_build_stop),
            daemon=True,
        ).start()

        self._check_index()
    # ==================== 工具功能 ====================
    def export_results(self):
        """导出结果"""
        if not self.all_results:
            QMessageBox.information(self, "提示", "没有可导出的结果")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出结果",
            f"search_results_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV文件 (*.csv);;文本文件 (*.txt);;所有文件 (*.*)",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                if path.endswith(".csv"):
                    import csv

                    writer = csv.writer(f)
                    writer.writerow(
                        ["文件名", "完整路径", "所在目录", "大小", "修改时间"]
                    )
                    for item in self.all_results:
                        writer.writerow(
                            [
                                item["filename"],
                                item["fullpath"],
                                item["dir_path"],
                                item["size_str"],
                                item["mtime_str"],
                            ]
                        )
                else:
                    for item in self.all_results:
                        f.write(f"{item['filename']}\t{item['fullpath']}\n")

            self.status.setText(f"✅ 已导出 {len(self.all_results)} 条结果")
            QMessageBox.information(
                self, "成功", f"已导出 {len(self.all_results)} 条结果"
            )
        except Exception as e:
            logger.error(f"导出失败: {e}")
            QMessageBox.warning(self, "错误", f"导出失败: {e}")

    def scan_large_files(self):
        """扫描大文件"""
        dlg = QDialog(self)
        dlg.setWindowTitle("📊 大文件扫描")
        dlg.setMinimumSize(800, 600)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # 参数设置
        param_frame = QHBoxLayout()
        param_frame.addWidget(QLabel("最小大小:"))

        size_combo = QComboBox()
        size_combo.addItems(["100MB", "500MB", "1GB", "5GB", "10GB"])
        size_combo.setCurrentText("1GB")
        param_frame.addWidget(size_combo)

        param_frame.addWidget(QLabel("扫描路径:"))

        path_combo = QComboBox()
        path_combo.addItem("所有磁盘")
        path_combo.addItems(self._get_drives())
        param_frame.addWidget(path_combo, 1)

        param_frame.addStretch()

        btn_scan = QPushButton("🔍 开始扫描")
        param_frame.addWidget(btn_scan)

        layout.addLayout(param_frame)

        # 结果列表
        result_tree = QTreeWidget()
        result_tree.setColumnCount(3)
        result_tree.setHeaderLabels(["文件名", "大小", "路径"])
        result_tree.setAlternatingRowColors(True)
        result_tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        result_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        result_tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(result_tree, 1)

        status_label = QLabel("就绪")
        layout.addWidget(status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        def do_scan():
            result_tree.clear()
            min_size_str = size_combo.currentText()
            min_size = (
                int(min_size_str.replace("GB", "")) * 1024**3
                if "GB" in min_size_str
                else int(min_size_str.replace("MB", "")) * 1024**2
            )

            scan_path = path_combo.currentText()
            paths = self._get_drives() if scan_path == "所有磁盘" else [scan_path]

            status_label.setText("🔍 扫描中...")
            QApplication.processEvents()

            found = []
            for path in paths:
                try:
                    for root, dirs, files in os.walk(path):
                        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS_LOWER]
                        for name in files:
                            fp = os.path.join(root, name)
                            try:
                                size = os.path.getsize(fp)
                                if size >= min_size:
                                    found.append((name, size, fp))
                            except:
                                continue
                except:
                    continue

            found.sort(key=lambda x: -x[1])
            for name, size, fp in found[:500]:
                item = QTreeWidgetItem([name, format_size(size), fp])
                result_tree.addTopLevelItem(item)

            status_label.setText(f"✅ 找到 {len(found)} 个大文件")

        btn_scan.clicked.connect(do_scan)
        dlg.exec()

    def _show_batch_rename(self):
        """显示批量重命名对话框"""
        items = self._get_selected_items()
        if not items:
            QMessageBox.information(self, "提示", "请先选择要重命名的文件")
            return

        scope = self.combo_scope.currentText()
        scope_text = f"当前选中: {len(items)} 个项目 | 范围: {scope}"

        dialog = BatchRenameDialog(self, items, self)
        dialog.show(scope_text)

    def _show_shortcuts(self):
        """显示快捷键列表"""
        shortcuts = """
快捷键列表:

搜索操作:
  Ctrl+F      聚焦搜索框
  Enter       开始搜索
  F5          刷新搜索
  Escape      停止搜索/清空关键词

文件操作:
  Enter       打开选中文件
  Ctrl+L      定位文件
  Delete      删除文件

编辑操作:
  Ctrl+A      全选结果
  Ctrl+C      复制路径
  Ctrl+Shift+C  复制文件

工具:
  Ctrl+E      导出结果
  Ctrl+G      大文件扫描

全局热键(需启用):
  Ctrl+Shift+Space  迷你搜索窗口
  Ctrl+Shift+Tab    主窗口
        """
        QMessageBox.information(self, "⌨️ 快捷键列表", shortcuts)

    def _show_about(self):
        """显示关于对话框"""
        QMessageBox.information(
            self,
            "关于",
            "🚀 极速文件搜索 V42 增强版\n\n"
            "功能特性:\n"
            "• MFT极速索引\n"
            "• FTS5全文搜索\n"
            "• 模糊/正则搜索\n"
            "• 实时文件监控\n"
            "• 收藏夹管理\n"
            "• 多主题支持\n"
            "• 全局热键呼出\n"
            "• 系统托盘常驻\n"
            "• C盘目录自定义\n\n"
            "© 2024",
        )

    # ==================== 窗口关闭处理 ====================
    def closeEvent(self, event):
        """窗口关闭事件"""
        if self.config_mgr.get_tray_enabled() and self.tray_mgr.running:
            self.hide()
            self.tray_mgr.show_notification("极速文件搜索", "程序已最小化到托盘")
            event.ignore()
        else:
            self._do_quit()
            event.accept()

    def _do_quit(self):
        """退出程序"""
        self.index_build_stop = True
        self.stop_event = True

        # ★ 先保存 DIR_CACHE（尽量在停止监控/关闭 DB 前）
        self._save_dir_cache_all()

        self.hotkey_mgr.stop()
        self.tray_mgr.stop()
        self.file_watcher.stop()
        self.index_mgr.close()

        # ⚠️ 这里不要再 self.close()，因为 closeEvent 里也会走 _do_quit，
        # 会导致递归/重复调用。直接退出即可：
        QApplication.quit()

    # ==================== 程序入口 ====================
def main():
    """主函数"""
    logger.info("🚀 极速文件搜索 V42 增强版 - PySide6 UI")

    if IS_WINDOWS:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception as e:
            logger.warning(f"设置DPI失败: {e}")

    app = QApplication(sys.argv)
    app.setApplicationName("极速文件搜索")
    app.setOrganizationName("FileSearch")
    app.setQuitOnLastWindowClosed(False)

    config = ConfigManager()
    apply_theme(app, config.get_theme())

    win = SearchApp()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
