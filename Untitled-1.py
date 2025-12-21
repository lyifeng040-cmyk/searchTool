"""
极速文件搜索 V42 增强版 - PySide6 版本
功能: MFT索引、FTS5全文搜索、文件监控、系统托盘、全局热键、批量重命名等
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
from collections import deque, defaultdict
import re
from pathlib import Path
import shutil
import math
import json
import logging
import ctypes

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QLabel,
    QComboBox, QMenu, QMenuBar, QStatusBar, QProgressBar, QDialog,
    QFormLayout, QCheckBox, QListWidget, QListWidgetItem, QMessageBox,
    QFileDialog, QSplitter, QFrame, QToolBar, QSystemTrayIcon,
    QHeaderView, QAbstractItemView, QGroupBox, QScrollArea,
    QTabWidget, QTextEdit, QSpinBox, QRadioButton, QButtonGroup,
    QGridLayout, QSizePolicy, QToolTip, QInputDialog
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QTimer, QSize, QSettings, QUrl, QPoint, QEvent, QObject
)
from PySide6.QtGui import (
    QAction, QIcon, QFont, QColor, QDesktopServices,
    QClipboard, QKeySequence, QShortcut, QPixmap, QPainter, QBrush, QCursor
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

# ==================== Rust 核心引擎加载 ====================
HAS_RUST_ENGINE = False
RUST_ENGINE = None

if platform.system() == "Windows":
    try:
        class ScanResult(ctypes.Structure):
            _fields_ = [
                ("data", ctypes.POINTER(ctypes.c_uint8)),
                ("data_len", ctypes.c_size_t),
                ("count", ctypes.c_size_t),
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
            RUST_ENGINE.scan_drive_packed.argtypes = [ctypes.c_uint16]
            RUST_ENGINE.scan_drive_packed.restype = ScanResult
            RUST_ENGINE.free_scan_result.argtypes = [ScanResult]
            RUST_ENGINE.free_scan_result.restype = None

            HAS_RUST_ENGINE = True
            logger.info(f"✅ Rust 核心引擎加载成功: {dll_path}")
    except Exception as e:
        logger.warning(f"⚠️ Rust 引擎加载失败: {e}")

# ==================== 依赖检查 ====================
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    logger.warning("watchdog 未安装，文件监控功能不可用")

try:
    import win32clipboard
    import win32con
    import win32api
    import win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    logger.warning("pywin32 未安装，部分功能不可用")

try:
    import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False
    logger.warning("send2trash 未安装")

# ==================== 系统常量 ====================
IS_WINDOWS = platform.system() == "Windows"
MFT_AVAILABLE = False

# ==================== 过滤规则 ====================
CAD_PATTERN = re.compile(r"cad20(1[0-9]|2[0-4])", re.IGNORECASE)
AUTOCAD_PATTERN = re.compile(r"autocad_20(1[0-9]|2[0-5])", re.IGNORECASE)

SKIP_DIRS_LOWER = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "appdata", "boot",
    "node_modules", ".git", "__pycache__", "site-packages", "sys",
    "recovery", "config.msi", "$windows.~bt", "$windows.~ws",
    "cache", "caches", "temp", "tmp", "logs", "log",
    ".vscode", ".idea", ".vs", "obj", "bin", "debug", "release",
    "packages", ".nuget", "bower_components",
}

SKIP_EXTS = {
    ".lsp", ".fas", ".lnk", ".html", ".htm", ".xml", ".ini", ".lsp_bak",
    ".cuix", ".arx", ".crx", ".fx", ".dbx", ".kid", ".ico", ".rz",
    ".dll", ".sys", ".tmp", ".log", ".dat", ".db", ".pdb", ".obj",
    ".pyc", ".class", ".cache", ".lock",
}

ARCHIVE_EXTS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".jar", ".cab", ".bz2", ".xz",
}


# ==================== 工具函数 ====================
def get_c_scan_dirs(config_mgr=None):
    if config_mgr:
        return config_mgr.get_enabled_c_paths()
    default_dirs = [
        os.path.expandvars(r"%TEMP%"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        os.path.expandvars(r"%USERPROFILE%\Documents"),
        os.path.expandvars(r"%USERPROFILE%\Downloads"),
    ]
    return [os.path.normpath(p) for p in default_dirs if p and os.path.isdir(p)]


def is_in_allowed_paths(path_lower, allowed_paths_lower):
    if not allowed_paths_lower:
        return False
    for ap in allowed_paths_lower:
        if path_lower.startswith(ap + "\\") or path_lower == ap:
            return True
    return False


def should_skip_path(path_lower, allowed_paths_lower=None):
    if allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower):
        return False
    path_parts = path_lower.replace("/", "\\").split("\\")
    for part in path_parts:
        if part in SKIP_DIRS_LOWER:
            return True
    if "site-packages" in path_lower or CAD_PATTERN.search(path_lower):
        return True
    if AUTOCAD_PATTERN.search(path_lower) or "tangent" in path_lower:
        return True
    return False


def should_skip_dir(name_lower, path_lower=None, allowed_paths_lower=None):
    if CAD_PATTERN.search(name_lower) or AUTOCAD_PATTERN.search(name_lower):
        return True
    if "tangent" in name_lower:
        return True
    if path_lower and allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower):
        return False
    return name_lower in SKIP_DIRS_LOWER


def format_size(size):
    if size <= 0:
        return "-"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_time(timestamp):
    if timestamp <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return "-"


def get_drives():
    if IS_WINDOWS:
        return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    return ["/"]


def fuzzy_match(keyword, filename):
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


# ==================== 配置管理 ====================
class ConfigManager:
    def __init__(self):
        self.config_dir = LOG_DIR
        self.config_file = self.config_dir / "config.json"
        self.config = self._load()

    def _load(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"配置加载失败: {e}")
        return {
            "search_history": [], "favorites": [], "theme": "light",
            "c_scan_paths": {"custom": [], "use_default": True, "disabled_defaults": []},
            "enable_global_hotkey": True, "minimize_to_tray": True, "window_geometry": None,
        }

    def save(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"配置保存失败: {e}")

    def add_history(self, keyword):
        if not keyword:
            return
        history = self.config.get("search_history", [])
        if keyword in history:
            history.remove(keyword)
        history.insert(0, keyword)
        self.config["search_history"] = history[:20]
        self.save()

    def get_history(self):
        return self.config.get("search_history", [])

    def add_favorite(self, path, name=None):
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
        favs = self.config.get("favorites", [])
        self.config["favorites"] = [f for f in favs if f.get("path", "").lower() != path.lower()]
        self.save()

    def get_favorites(self):
        return self.config.get("favorites", [])

    def set_theme(self, theme):
        self.config["theme"] = theme
        self.save()

    def get_theme(self):
        return self.config.get("theme", "light")

    def get_c_scan_paths(self):
        config = self.config.get("c_scan_paths", {})
        if not config.get("initialized", False):
            return self._get_default_c_paths()
        return config.get("paths", [])

    def _get_default_c_paths(self):
        default_dirs = [
            os.path.expandvars(r"%TEMP%"),
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
            os.path.expandvars(r"%USERPROFILE%\Desktop"),
            os.path.expandvars(r"%USERPROFILE%\Documents"),
            os.path.expandvars(r"%USERPROFILE%\Downloads"),
        ]
        return [{"path": os.path.normpath(p), "enabled": True} for p in default_dirs if p and os.path.isdir(p)]

    def set_c_scan_paths(self, paths):
        self.config["c_scan_paths"] = {"paths": paths, "initialized": True}
        self.save()

    def reset_c_scan_paths(self):
        default_paths = self._get_default_c_paths()
        self.set_c_scan_paths(default_paths)
        return default_paths

    def get_enabled_c_paths(self):
        paths = self.get_c_scan_paths()
        return [p["path"] for p in paths if p.get("enabled", True) and os.path.isdir(p["path"])]

    def get_hotkey_enabled(self):
        return self.config.get("enable_global_hotkey", True)

    def set_hotkey_enabled(self, enabled):
        self.config["enable_global_hotkey"] = enabled
        self.save()

    def get_tray_enabled(self):
        return self.config.get("minimize_to_tray", True)

    def set_tray_enabled(self, enabled):
        self.config["minimize_to_tray"] = enabled
        self.save()

    def set_window_geometry(self, geometry):
        self.config["window_geometry"] = geometry
        self.save()

    def get_window_geometry(self):
        return self.config.get("window_geometry")


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
            ("UsnJournalID", ctypes.c_uint64), ("FirstUsn", ctypes.c_int64),
            ("NextUsn", ctypes.c_int64), ("LowestValidUsn", ctypes.c_int64),
            ("MaxUsn", ctypes.c_int64), ("MaximumSize", ctypes.c_uint64),
            ("AllocationDelta", ctypes.c_uint64),
        ]

    class USN_RECORD_V2(ctypes.Structure):
        _fields_ = [
            ("RecordLength", ctypes.c_uint32), ("MajorVersion", ctypes.c_uint16),
            ("MinorVersion", ctypes.c_uint16), ("FileReferenceNumber", ctypes.c_uint64),
            ("ParentFileReferenceNumber", ctypes.c_uint64), ("Usn", ctypes.c_int64),
            ("TimeStamp", ctypes.c_int64), ("Reason", ctypes.c_uint32),
            ("SourceInfo", ctypes.c_uint32), ("SecurityId", ctypes.c_uint32),
            ("FileAttributes", ctypes.c_uint32), ("FileNameLength", ctypes.c_uint16),
            ("FileNameOffset", ctypes.c_uint16),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    CreateFileW.restype = wintypes.HANDLE

    DeviceIoControl = kernel32.DeviceIoControl
    DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    DeviceIoControl.restype = wintypes.BOOL

    CloseHandle = kernel32.CloseHandle
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
        """MFT枚举文件"""
        global MFT_AVAILABLE

        if HAS_RUST_ENGINE:
            logger.info(f"🚀 使用 Rust 引擎扫描 {drive_letter}: ...")
            result = None
            try:
                result = RUST_ENGINE.scan_drive_packed(ord(drive_letter.upper()[0]))
                if not result.data or result.count == 0:
                    raise Exception("空数据")

                raw_data = ctypes.string_at(result.data, result.data_len)
                py_list = []
                off = 0
                n = len(raw_data)
                allowed_paths_lower = [p.lower().rstrip("\\") for p in allowed_paths] if allowed_paths else None

                while off < n:
                    is_dir = raw_data[off]
                    name_len = int.from_bytes(raw_data[off + 1:off + 3], "little")
                    name_lower_len = int.from_bytes(raw_data[off + 3:off + 5], "little")
                    path_len = int.from_bytes(raw_data[off + 5:off + 7], "little")
                    parent_len = int.from_bytes(raw_data[off + 7:off + 9], "little")
                    ext_len = raw_data[off + 9]
                    off += 10

                    total_len = name_len + name_lower_len + path_len + parent_len + ext_len
                    if off + total_len > n:
                        break

                    name = raw_data[off:off + name_len].decode("utf-8", "replace")
                    off += name_len
                    name_lower = raw_data[off:off + name_lower_len].decode("utf-8", "replace")
                    off += name_lower_len
                    path = raw_data[off:off + path_len].decode("utf-8", "replace")
                    off += path_len
                    parent = raw_data[off:off + parent_len].decode("utf-8", "replace")
                    off += parent_len
                    ext = raw_data[off:off + ext_len].decode("utf-8", "replace") if ext_len else ""
                    off += ext_len

                    path_lower = path.lower()
                    if allowed_paths_lower:
                        if not any(path_lower.startswith(ap + "\\") or path_lower == ap for ap in allowed_paths_lower):
                            continue
                    else:
                        if should_skip_path(path_lower, None):
                            continue
                        if is_dir and should_skip_dir(name_lower, path_lower, None):
                            continue
                        if not is_dir and ext in skip_exts:
                            continue

                    py_list.append([name, name_lower, path, parent, ext, 0, 0, is_dir])

                return [tuple(item) for item in py_list]
            except Exception as e:
                logger.error(f"Rust 引擎错误: {e}")
            finally:
                if result and result.data:
                    try:
                        RUST_ENGINE.free_scan_result(result)
                    except:
                        pass

        # Python MFT fallback
        logger.info(f"使用 Python MFT 扫描 {drive_letter}...")
        drive = drive_letter.rstrip(":").upper()
        root_path = f"{drive}:\\"

        volume_path = f"\\\\.\\{drive}:"
        h = CreateFileW(volume_path, GENERIC_READ | GENERIC_WRITE,
                       FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                       OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if h == INVALID_HANDLE_VALUE:
            raise OSError(f"打开卷失败: {ctypes.get_last_error()}")

        try:
            jd = USN_JOURNAL_DATA_V0()
            br = wintypes.DWORD()
            if not DeviceIoControl(h, FSCTL_QUERY_USN_JOURNAL, None, 0,
                                  ctypes.byref(jd), ctypes.sizeof(jd), ctypes.byref(br), None):
                raise OSError(f"查询USN失败: {ctypes.get_last_error()}")

            MFT_AVAILABLE = True
            records = {}
            BUFFER_SIZE = 1024 * 1024
            buf = (ctypes.c_ubyte * BUFFER_SIZE)()

            class MFT_ENUM_DATA(ctypes.Structure):
                _pack_ = 8
                _fields_ = [("StartFileReferenceNumber", ctypes.c_uint64),
                           ("LowUsn", ctypes.c_int64), ("HighUsn", ctypes.c_int64)]

            med = MFT_ENUM_DATA()
            med.StartFileReferenceNumber = 0
            med.LowUsn = 0
            med.HighUsn = jd.NextUsn
            allowed_paths_lower = [p.lower().rstrip("\\") for p in allowed_paths] if allowed_paths else None

            while True:
                ctypes.set_last_error(0)
                ok = DeviceIoControl(h, FSCTL_ENUM_USN_DATA, ctypes.byref(med), ctypes.sizeof(med),
                                    ctypes.byref(buf), BUFFER_SIZE, ctypes.byref(br), None)
                err = ctypes.get_last_error()
                returned = br.value

                if not ok:
                    if err == 38 or returned <= 8:
                        break
                    if err != 0:
                        raise OSError(f"枚举失败: {err}")
                if returned <= 8:
                    break

                next_frn = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(ctypes.c_uint64))[0]
                offset = 8

                while offset < returned:
                    if offset + 4 > returned:
                        break
                    rec_len = ctypes.cast(ctypes.byref(buf, offset), ctypes.POINTER(ctypes.c_uint32))[0]
                    if rec_len == 0 or offset + rec_len > returned:
                        break

                    if rec_len >= ctypes.sizeof(USN_RECORD_V2):
                        rec = ctypes.cast(ctypes.byref(buf, offset), ctypes.POINTER(USN_RECORD_V2)).contents
                        name_off, name_len = rec.FileNameOffset, rec.FileNameLength
                        if name_len > 0 and offset + name_off + name_len <= returned:
                            filename = bytes(buf[offset + name_off:offset + name_off + name_len]).decode("utf-16le", errors="replace")
                            if filename and filename[0] not in ("$", "."):
                                file_ref = rec.FileReferenceNumber & 0x0000FFFFFFFFFFFF
                                parent_ref = rec.ParentFileReferenceNumber & 0x0000FFFFFFFFFFFF
                                is_dir = bool(rec.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
                                records[file_ref] = (filename, parent_ref, is_dir)
                    offset += rec_len

                med.StartFileReferenceNumber = next_frn

            # Build paths
            dirs = {ref: (name, parent_ref) for ref, (name, parent_ref, is_dir) in records.items() if is_dir}
            files = {ref: (name, parent_ref) for ref, (name, parent_ref, is_dir) in records.items() if not is_dir}
            parent_to_children = {}
            for ref, (name, parent_ref) in dirs.items():
                parent_to_children.setdefault(parent_ref, []).append(ref)

            path_cache = {5: root_path}
            q = deque([5])
            while q:
                parent_ref = q.popleft()
                parent_path = path_cache.get(parent_ref)
                if not parent_path or should_skip_path(parent_path.lower(), allowed_paths_lower):
                    continue
                for child_ref in parent_to_children.get(parent_ref, []):
                    child_name, _ = dirs[child_ref]
                    path_cache[child_ref] = os.path.join(parent_path, child_name)
                    q.append(child_ref)

            result = []
            for ref, (name, parent_ref) in dirs.items():
                full_path = path_cache.get(ref)
                if full_path and full_path != root_path:
                    result.append([name, name.lower(), full_path, path_cache.get(parent_ref, root_path), "", 0, 0, 1])

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
                if allowed_paths_lower and not is_in_allowed_paths(full_path.lower(), allowed_paths_lower):
                    continue
                result.append([name, name.lower(), full_path, parent_path, ext, 0, 0, 0])

            return [tuple(item) for item in result]
        finally:
            CloseHandle(h)
else:
    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
        raise OSError("MFT仅Windows可用")


# ==================== 索引管理器 ====================
try:
    import apsw
    HAS_APSW = True
except ImportError:
    HAS_APSW = False
    import sqlite3


class IndexManager:
    def __init__(self, db_path=None, config_mgr=None):
        self.config_mgr = config_mgr
        self.db_path = db_path or str(LOG_DIR / "index.db")
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
        try:
            self.conn = apsw.Connection(self.db_path) if HAS_APSW else sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-2000000")

            cursor.execute("""CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY, filename TEXT NOT NULL, filename_lower TEXT NOT NULL,
                full_path TEXT UNIQUE NOT NULL, parent_dir TEXT NOT NULL, extension TEXT,
                size INTEGER DEFAULT 0, mtime REAL DEFAULT 0, is_dir INTEGER DEFAULT 0)""")

            try:
                if not list(cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'")):
                    cursor.execute("CREATE VIRTUAL TABLE files_fts USING fts5(filename, content=files, content_rowid=id)")
                self.has_fts = True
            except:
                self.has_fts = False

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
            cursor.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            if not HAS_APSW:
                self.conn.commit()
            self._load_stats()
        except Exception as e:
            logger.error(f"数据库初始化错误: {e}")
            self.conn = None

    def _load_stats(self, preserve_mft=False):
        if not self.conn:
            return
        try:
            with self.lock:
                cursor = self.conn.cursor()
                self.file_count = list(cursor.execute("SELECT COUNT(*) FROM files"))[0][0]
                time_row = list(cursor.execute("SELECT value FROM meta WHERE key='build_time'"))
                self.last_build_time = float(time_row[0][0]) if time_row and time_row[0][0] else None
                if not preserve_mft:
                    mft_row = list(cursor.execute("SELECT value FROM meta WHERE key='used_mft'"))
                    self.used_mft = bool(mft_row and mft_row[0][0] == "1")
            self.is_ready = self.file_count > 0
        except Exception as e:
            logger.error(f"加载统计失败: {e}")

    def reload_stats(self):
        if not self.is_building:
            self._load_stats(preserve_mft=True)

    def close(self):
        with self.lock:
            if self.conn:
                self.conn.close()
                self.conn = None

    def search(self, keywords, scope_targets, limit=50000):
        if not self.conn or not self.is_ready:
            return None
        try:
            with self.lock:
                cursor = self.conn.cursor()
                
                # 尝试使用 FTS5
                if self.has_fts and keywords:
                    try:
                        # FTS5 查询语法
                        fts_query = " ".join(f'"{kw}"*' for kw in keywords)
                        sql = """
                            SELECT f.filename, f.full_path, f.size, f.mtime, f.is_dir 
                            FROM files f 
                            INNER JOIN files_fts ON f.id = files_fts.rowid 
                            WHERE files_fts MATCH ? 
                            LIMIT ?
                        """
                        params = (fts_query, limit)
                        raw_results = list(cursor.execute(sql, params))
                    except Exception as e:
                        logger.warning(f"FTS5查询失败，降级为LIKE: {e}")
                        # 降级为 LIKE 查询
                        wheres = " AND ".join(["filename_lower LIKE ?"] * len(keywords))
                        sql = f"SELECT filename, full_path, size, mtime, is_dir FROM files WHERE {wheres} LIMIT ?"
                        params = tuple([f"%{kw}%" for kw in keywords] + [limit])
                        raw_results = list(cursor.execute(sql, params))
                else:
                    # 使用 LIKE 查询
                    wheres = " AND ".join(["filename_lower LIKE ?"] * len(keywords))
                    sql = f"SELECT filename, full_path, size, mtime, is_dir FROM files WHERE {wheres} LIMIT ?"
                    params = tuple([f"%{kw}%" for kw in keywords] + [limit])
                    raw_results = list(cursor.execute(sql, params))

                # 过滤结果
                scope_targets_lower = [t.lower().rstrip("\\") for t in scope_targets] if scope_targets else None

                filtered = []
                for row in raw_results:
                    path_lower = row[1].lower()
                    if scope_targets_lower and not is_in_allowed_paths(path_lower, scope_targets_lower):
                        continue
                    if should_skip_path(path_lower, scope_targets_lower):
                        continue
                    filtered.append(row)
                return filtered
        except Exception as e:
            logger.error(f"搜索错误: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_stats(self):
        self._load_stats(preserve_mft=True)
        return {"count": self.file_count, "ready": self.is_ready, "building": self.is_building,
                "time": self.last_build_time, "path": self.db_path, "has_fts": self.has_fts, "used_mft": self.used_mft}

    def build_index(self, drives, progress_cb=None, stop_fn=None):
        global MFT_AVAILABLE
        if not self.conn or self.is_building:
            return

        self.is_building = True
        self.is_ready = False
        self.used_mft = False

        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("DROP TABLE IF EXISTS files_fts")
                cursor.execute("DROP TABLE IF EXISTS files")
                cursor.execute("""CREATE TABLE files (
                    id INTEGER PRIMARY KEY, filename TEXT NOT NULL, filename_lower TEXT NOT NULL,
                    full_path TEXT UNIQUE NOT NULL, parent_dir TEXT NOT NULL, extension TEXT,
                    size INTEGER DEFAULT 0, mtime REAL DEFAULT 0, is_dir INTEGER DEFAULT 0)""")
                if not HAS_APSW:
                    self.conn.commit()

            all_drives = [d.upper().rstrip(":\\") for d in drives if os.path.exists(d)]
            c_allowed_paths = get_c_scan_dirs(self.config_mgr) if "C" in all_drives else None
            all_data = []

            if all_drives and IS_WINDOWS:
                for drv in all_drives:
                    if stop_fn and stop_fn():
                        break
                    try:
                        allowed = c_allowed_paths if drv == "C" else None
                        data = enum_volume_files_mft(drv, SKIP_DIRS_LOWER, SKIP_EXTS, allowed_paths=allowed)
                        all_data.extend(data)
                        if progress_cb:
                            progress_cb(len(all_data), f"MFT {drv}: {len(data)}")
                    except Exception as e:
                        logger.error(f"MFT {drv}: 失败 - {e}")

                if all_data:
                    self.used_mft = True
                    
                    # 批量获取文件大小和时间
                    if progress_cb:
                        progress_cb(len(all_data), "获取文件信息...")
                    
                    def get_file_info(items):
                        for i, item in enumerate(items):
                            if item[7] == 0:  # 不是目录
                                try:
                                    path = item[2]
                                    if os.path.exists(path):
                                        st = os.stat(path)
                                        # 转换为列表修改
                                        items[i] = (item[0], item[1], item[2], item[3], item[4], 
                                                   st.st_size, st.st_mtime, item[7])
                                except:
                                    pass
                    
                    # 分批处理
                    batch_size = 10000
                    all_data_list = list(all_data)
                    for i in range(0, len(all_data_list), batch_size):
                        if stop_fn and stop_fn():
                            break
                        batch = all_data_list[i:i+batch_size]
                        get_file_info(batch)
                        all_data_list[i:i+batch_size] = batch
                        if progress_cb:
                            progress_cb(i + len(batch), f"获取文件信息: {i + len(batch):,}/{len(all_data_list):,}")
                    
                    all_data = all_data_list

            if all_data:
                with self.lock:
                    cursor = self.conn.cursor()
                    cursor.execute("PRAGMA synchronous=OFF")
                    for i in range(0, len(all_data), 50000):
                        if stop_fn and stop_fn():
                            break
                        batch = all_data[i:i + 50000]
                        cursor.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", batch)
                        if not HAS_APSW:
                            self.conn.commit()
                        if progress_cb:
                            progress_cb(i + len(batch), f"写入: {i + len(batch):,}")

                self.file_count = len(all_data)

            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
                cursor.execute("PRAGMA synchronous=NORMAL")
                try:
                    cursor.execute("CREATE VIRTUAL TABLE files_fts USING fts5(filename, content=files, content_rowid=id)")
                    cursor.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
                    self.has_fts = True
                except:
                    self.has_fts = False
                cursor.execute("INSERT OR REPLACE INTO meta VALUES('build_time', ?)", (str(time.time()),))
                cursor.execute("INSERT OR REPLACE INTO meta VALUES('used_mft', ?)", ("1" if self.used_mft else "0",))
                if not HAS_APSW:
                    self.conn.commit()

            self.is_ready = self.file_count > 0
            logger.info(f"✅ 索引完成: {self.file_count:,} 条")
        except Exception as e:
            logger.error(f"构建错误: {e}")
        finally:
            self.is_building = False

    def rebuild_drive(self, drive_letter, progress_cb=None, stop_fn=None):
        if not self.conn:
            return
        drive = drive_letter.upper().rstrip(":\\")
        self.is_building = True
        try:
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("DELETE FROM files WHERE full_path LIKE ?", (f"{drive}:%",))
                if not HAS_APSW:
                    self.conn.commit()

            scan_paths = get_c_scan_dirs(self.config_mgr) if drive == "C" else [f"{drive}:\\"]
            data = []
            try:
                data = enum_volume_files_mft(drive, SKIP_DIRS_LOWER, SKIP_EXTS,
                                            allowed_paths=(scan_paths if drive == "C" else None))
                if progress_cb:
                    progress_cb(len(data), f"MFT {drive}:")
            except Exception as e:
                logger.error(f"MFT {drive}: 失败 - {e}")

            if data:
                with self.lock:
                    cursor = self.conn.cursor()
                    cursor.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", data)
                    if self.has_fts:
                        cursor.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
                    cursor.execute("INSERT OR REPLACE INTO meta VALUES('build_time', ?)", (str(time.time()),))
                    if not HAS_APSW:
                        self.conn.commit()

            self.reload_stats()
        finally:
            self.is_building = False


# ==================== 文件监控 ====================
if HAS_WATCHDOG:
    class _Handler(FileSystemEventHandler):
        def __init__(self, mgr, eq, config_mgr=None):
            self.mgr = mgr
            self.eq = eq
            self.config_mgr = config_mgr

        def _ignore(self, p):
            n = os.path.basename(p)
            if not n or n.startswith((".", "$")):
                return True
            if os.path.splitext(n)[1].lower() in SKIP_EXTS:
                return True
            return any(part.lower() in SKIP_DIRS_LOWER for part in Path(p).parts)

        def on_created(self, e):
            if not self._ignore(e.src_path):
                self.eq.put(("c", e.src_path, e.is_directory))

        def on_deleted(self, e):
            if not self._ignore(e.src_path):
                self.eq.put(("d", e.src_path))

        def on_moved(self, e):
            self.eq.put(("m", e.src_path, e.dest_path))
else:
    class _Handler:
        pass


class FileWatcher:
    def __init__(self, mgr, config_mgr=None):
        self.mgr = mgr
        self.db_path = mgr.db_path
        self.config_mgr = config_mgr
        self.observer = None
        self.running = False
        self.eq = queue.Queue()
        self.thread = None
        self.stop_flag = False

    def start(self, paths):
        if not HAS_WATCHDOG or self.running:
            return
        try:
            self.observer = Observer()
            handler = _Handler(self.mgr, self.eq, self.config_mgr)
            for p in paths:
                if p.upper().startswith("C:"):
                    for cp in get_c_scan_dirs(self.config_mgr):
                        if os.path.exists(cp):
                            try:
                                self.observer.schedule(handler, cp, recursive=True)
                            except Exception as e:
                                logger.error(f"监控失败: {cp} - {e}")
                elif os.path.exists(p):
                    try:
                        self.observer.schedule(handler, p, recursive=True)
                    except Exception as e:
                        logger.error(f"监控失败: {p} - {e}")
            self.observer.start()
            self.running = True
            self.stop_flag = False
            self.thread = threading.Thread(target=self._process, daemon=True)
            self.thread.start()
        except Exception as e:
            logger.error(f"监控启动失败: {e}")

    def _process(self):
        batch, last = [], time.time()
        while not self.stop_flag:
            try:
                batch.append(self.eq.get(timeout=2.0))
            except queue.Empty:
                pass
            if batch and (len(batch) >= 100 or time.time() - last >= 2.0):
                self._apply(batch)
                batch.clear()
                last = time.time()

    def _apply(self, events):
        if not events:
            return
        ins, dels = [], []
        for ev in events:
            if ev[0] == "c":
                try:
                    if os.path.isfile(ev[1]):
                        n = os.path.basename(ev[1])
                        st = os.stat(ev[1])
                        ins.append((n, n.lower(), ev[1], os.path.dirname(ev[1]),
                                   os.path.splitext(n)[1].lower(), st.st_size, st.st_mtime, 0))
                except:
                    pass
            elif ev[0] == "d":
                dels.append(ev[1])
            elif ev[0] == "m":
                dels.append(ev[1])
                try:
                    if os.path.isfile(ev[2]):
                        n = os.path.basename(ev[2])
                        st = os.stat(ev[2])
                        ins.append((n, n.lower(), ev[2], os.path.dirname(ev[2]),
                                   os.path.splitext(n)[1].lower(), st.st_size, st.st_mtime, 0))
                except:
                    pass

        if ins or dels:
            try:
                conn = apsw.Connection(self.db_path) if HAS_APSW else sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                for d in dels:
                    cursor.execute("DELETE FROM files WHERE full_path = ?", (d,))
                if ins:
                    cursor.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", ins)
                if not HAS_APSW:
                    conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"监控更新失败: {e}")

    def stop(self):
        self.stop_flag = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        if self.observer and self.running:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except:
                pass
            self.running = False


# ==================== 工作线程 ====================
class IndexBuildThread(QThread):
    progress = Signal(int, str)
    finished_signal = Signal(bool, str)

    def __init__(self, index_mgr, drives):
        super().__init__()
        self.index_mgr = index_mgr
        self.drives = drives
        self._stop_flag = False

    def run(self):
        try:
            self.index_mgr.build_index(
                self.drives,
                progress_cb=lambda c, m: self.progress.emit(c, m),
                stop_fn=lambda: self._stop_flag
            )
            self.finished_signal.emit(True, f"索引完成，共 {self.index_mgr.file_count:,} 个文件")
        except Exception as e:
            self.finished_signal.emit(False, f"构建失败: {e}")

    def stop(self):
        self._stop_flag = True


class SearchThread(QThread):
    results_ready = Signal(list)
    search_error = Signal(str)

    def __init__(self, index_mgr, keywords, scope_targets, fuzzy=False, regex=False):
        super().__init__()
        self.index_mgr = index_mgr
        self.keywords = keywords
        self.scope_targets = scope_targets
        self.fuzzy = fuzzy
        self.regex = regex

    def run(self):
        try:
            results = self.index_mgr.search(self.keywords, self.scope_targets)
            if results is not None:
                self.results_ready.emit(results)
            else:
                self.search_error.emit("搜索失败")
        except Exception as e:
            self.search_error.emit(str(e))


class RealtimeSearchThread(QThread):
    batch_ready = Signal(list)
    progress_update = Signal(int, str)
    finished_signal = Signal(float)

    def __init__(self, keyword, scope_targets, fuzzy=False, regex=False):
        super().__init__()
        self.keyword = keyword
        self.scope_targets = scope_targets
        self.fuzzy = fuzzy
        self.regex = regex
        self._stop_flag = False
        self._paused = False

    def run(self):
        start_time = time.time()
        keywords = self.keyword.lower().split()

        def check(name):
            if self.regex:
                try:
                    return re.search(self.keyword, name, re.IGNORECASE) is not None
                except:
                    return False
            elif self.fuzzy:
                name_lower = name.lower()
                return all(kw in name_lower or fuzzy_match(kw, name) >= 50 for kw in keywords)
            else:
                name_lower = name.lower()
                return all(kw in name_lower for kw in keywords)

        scanned = 0
        for target in self.scope_targets:
            if self._stop_flag or not os.path.isdir(target):
                continue
            try:
                for root, dirs, files in os.walk(target):
                    while self._paused and not self._stop_flag:
                        time.sleep(0.1)
                    if self._stop_flag:
                        break

                    dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS_LOWER and not d.startswith(".")]
                    scanned += 1
                    batch = []

                    for name in files + dirs:
                        if self._stop_flag:
                            break
                        if check(name):
                            fp = os.path.join(root, name)
                            is_dir = os.path.isdir(fp)
                            try:
                                st = os.stat(fp)
                                sz, mt = (0, st.st_mtime) if is_dir else (st.st_size, st.st_mtime)
                            except:
                                sz, mt = 0, 0
                            ext = os.path.splitext(name)[1].lower()
                            tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
                            batch.append((name, fp, sz, mt, tc))

                    if batch:
                        self.batch_ready.emit(batch)
                    if scanned % 50 == 0:
                        self.progress_update.emit(scanned, root)
            except:
                continue

        self.finished_signal.emit(time.time() - start_time)

    def stop(self):
        self._stop_flag = True

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False


# ==================== 设置对话框 ====================
class SettingsDialog(QDialog):
    def __init__(self, parent, config_mgr):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.setWindowTitle("设置")
        self.setMinimumSize(550, 450)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # 常规设置
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)

        theme_group = QGroupBox("主题")
        theme_layout = QHBoxLayout(theme_group)
        theme_layout.addWidget(QLabel("界面主题:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        self.theme_combo.setCurrentText(self.config_mgr.get_theme())
        theme_layout.addWidget(self.theme_combo)
        theme_layout.addStretch()
        general_layout.addWidget(theme_group)

        hotkey_group = QGroupBox("快捷键")
        hotkey_layout = QVBoxLayout(hotkey_group)
        self.hotkey_check = QCheckBox("启用全局热键 (Ctrl+Shift+Space)")
        self.hotkey_check.setChecked(self.config_mgr.get_hotkey_enabled())
        if not HAS_WIN32:
            self.hotkey_check.setEnabled(False)
            self.hotkey_check.setText("启用全局热键 (需要pywin32)")
        hotkey_layout.addWidget(self.hotkey_check)
        general_layout.addWidget(hotkey_group)

        tray_group = QGroupBox("托盘")
        tray_layout = QVBoxLayout(tray_group)
        self.tray_check = QCheckBox("关闭时最小化到托盘")
        self.tray_check.setChecked(self.config_mgr.get_tray_enabled())
        tray_layout.addWidget(self.tray_check)
        general_layout.addWidget(tray_group)

        general_layout.addStretch()
        tabs.addTab(general_tab, "常规")

        # C盘路径设置
        c_drive_tab = QWidget()
        c_layout = QVBoxLayout(c_drive_tab)
        c_layout.addWidget(QLabel("C盘扫描目录（勾选启用）:"))

        self.path_list = QListWidget()
        self.path_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        c_layout.addWidget(self.path_list)
        self._load_c_paths()

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("添加目录")
        add_btn.clicked.connect(self._add_path)
        remove_btn = QPushButton("删除选中")
        remove_btn.clicked.connect(self._remove_path)
        reset_btn = QPushButton("重置默认")
        reset_btn.clicked.connect(self._reset_paths)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addWidget(reset_btn)
        btn_layout.addStretch()
        c_layout.addLayout(btn_layout)
        tabs.addTab(c_drive_tab, "C盘路径")

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

    def _load_c_paths(self):
        self.path_list.clear()
        for p in self.config_mgr.get_c_scan_paths():
            item = QListWidgetItem(p["path"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if p.get("enabled", True) else Qt.Unchecked)
            self.path_list.addItem(item)

    def _add_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择目录", "C:\\")
        if path:
            path = os.path.normpath(path)
            for i in range(self.path_list.count()):
                if self.path_list.item(i).text().lower() == path.lower():
                    QMessageBox.warning(self, "提示", "该目录已存在")
                    return
            item = QListWidgetItem(path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.path_list.addItem(item)

    def _remove_path(self):
        for item in self.path_list.selectedItems():
            self.path_list.takeItem(self.path_list.row(item))

    def _reset_paths(self):
        self.config_mgr.reset_c_scan_paths()
        self._load_c_paths()

    def _save(self):
        self.config_mgr.set_theme(self.theme_combo.currentText())
        self.config_mgr.set_hotkey_enabled(self.hotkey_check.isChecked())
        self.config_mgr.set_tray_enabled(self.tray_check.isChecked())

        paths = []
        for i in range(self.path_list.count()):
            item = self.path_list.item(i)
            paths.append({"path": item.text(), "enabled": item.checkState() == Qt.Checked})
        self.config_mgr.set_c_scan_paths(paths)
        self.accept()


# ==================== 批量重命名对话框 ====================
class BatchRenameDialog(QDialog):
    def __init__(self, parent, targets, on_rename_callback=None):
        super().__init__(parent)
        self.targets = targets
        self.on_rename_callback = on_rename_callback
        self.setWindowTitle("批量重命名")
        self.setMinimumSize(700, 550)
        self.preview_lines = []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 模式选择
        mode_group = QGroupBox("重命名规则")
        mode_layout = QVBoxLayout(mode_group)

        self.mode_prefix = QRadioButton("前缀 + 序号")
        self.mode_replace = QRadioButton("替换文本")
        self.mode_prefix.setChecked(True)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_prefix)
        mode_row.addWidget(self.mode_replace)
        mode_row.addStretch()
        mode_layout.addLayout(mode_row)

        # 前缀模式参数
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel("新前缀:"))
        self.prefix_input = QLineEdit()
        self.prefix_input.setMaximumWidth(150)
        prefix_row.addWidget(self.prefix_input)
        prefix_row.addWidget(QLabel("起始序号:"))
        self.start_num = QSpinBox()
        self.start_num.setRange(1, 99999)
        self.start_num.setValue(1)
        prefix_row.addWidget(self.start_num)
        prefix_row.addWidget(QLabel("位数:"))
        self.width_num = QSpinBox()
        self.width_num.setRange(1, 10)
        self.width_num.setValue(3)
        prefix_row.addWidget(self.width_num)
        prefix_row.addStretch()
        mode_layout.addLayout(prefix_row)

        # 替换模式参数
        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("查找:"))
        self.find_input = QLineEdit()
        self.find_input.setMaximumWidth(150)
        replace_row.addWidget(self.find_input)
        replace_row.addWidget(QLabel("替换为:"))
        self.replace_input = QLineEdit()
        self.replace_input.setMaximumWidth(150)
        replace_row.addWidget(self.replace_input)
        replace_row.addStretch()
        mode_layout.addLayout(replace_row)

        layout.addWidget(mode_group)

        # 预览
        preview_group = QGroupBox("预览")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setFont(QFont("Consolas", 9))
        preview_layout.addWidget(self.preview_text)
        layout.addWidget(preview_group)

        # 按钮
        btn_layout = QHBoxLayout()
        preview_btn = QPushButton("预览效果")
        preview_btn.clicked.connect(self._update_preview)
        execute_btn = QPushButton("执行重命名")
        execute_btn.clicked.connect(self._do_rename)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(preview_btn)
        btn_layout.addWidget(execute_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self._update_preview()

    def _update_preview(self):
        self.preview_text.clear()
        self.preview_lines = []

        if not self.targets:
            self.preview_text.setPlainText("（没有可重命名的项目）")
            return

        if self.mode_prefix.isChecked():
            prefix = self.prefix_input.text()
            start = self.start_num.value()
            width = self.width_num.value()
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
                new_name = (name.replace(find, replace) + ext) if find else old_name
                new_full = os.path.join(os.path.dirname(old_full), new_name)
                self.preview_lines.append((old_full, new_full))

        lines = []
        for old_full, new_full in self.preview_lines:
            old_name = os.path.basename(old_full)
            new_name = os.path.basename(new_full)
            mark = "  (未变化)" if old_full == new_full else ""
            if os.path.exists(new_full) and old_full.lower() != new_full.lower():
                mark = "  (⚠ 目标已存在)"
            lines.append(f"{old_name}  →  {new_name}{mark}")

        self.preview_text.setPlainText("\n".join(lines))

    def _do_rename(self):
        if not self.preview_lines:
            QMessageBox.warning(self, "提示", "没有可执行的重命名记录")
            return

        if QMessageBox.question(self, "确认", "确定执行重命名？") != QMessageBox.Yes:
            return

        success, skipped, failed = 0, 0, 0
        renamed_pairs = []

        for old_full, new_full in self.preview_lines:
            if old_full == new_full:
                skipped += 1
                continue
            try:
                if os.path.exists(new_full) and old_full.lower() != new_full.lower():
                    skipped += 1
                    continue
                os.rename(old_full, new_full)
                success += 1
                renamed_pairs.append((old_full, new_full))
            except Exception as e:
                failed += 1
                logger.error(f"重命名失败: {old_full} -> {new_full} - {e}")

        if self.on_rename_callback and renamed_pairs:
            self.on_rename_callback(renamed_pairs)

        QMessageBox.information(self, "完成", f"成功 {success}，跳过 {skipped}，失败 {failed}")
        self.accept()
# ==================== 全局热键管理 ====================
class HotkeyManager(QObject):
    """全局热键管理器"""
    
    # 定义信号
    show_mini_signal = Signal()
    show_main_signal = Signal()
    
    HOTKEY_MINI = 1
    HOTKEY_MAIN = 2

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.registered = False
        self.thread = None
        self.stop_flag = False
        
        # 连接信号到槽
        self.show_mini_signal.connect(self._do_show_mini)
        self.show_main_signal.connect(self._do_show_main)

    def start(self):
        if not IS_WINDOWS or not HAS_WIN32:
            logger.warning("全局热键仅支持Windows + pywin32")
            return False

        if self.registered:
            return True

        self.stop_flag = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def _run(self):
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)

            RegisterHotKey = user32.RegisterHotKey
            RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            RegisterHotKey.restype = wintypes.BOOL

            UnregisterHotKey = user32.UnregisterHotKey
            UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            UnregisterHotKey.restype = wintypes.BOOL

            GetMessageW = user32.GetMessageW
            GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT]
            GetMessageW.restype = wintypes.BOOL

            PeekMessageW = user32.PeekMessageW
            PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                     wintypes.UINT, wintypes.UINT, wintypes.UINT]
            PeekMessageW.restype = wintypes.BOOL

            MOD_CONTROL = 0x0002
            MOD_SHIFT = 0x0004
            VK_SPACE = 0x20
            VK_TAB = 0x09
            WM_HOTKEY = 0x0312
            PM_REMOVE = 0x0001

            # 注册热键
            if RegisterHotKey(None, self.HOTKEY_MINI, MOD_CONTROL | MOD_SHIFT, VK_SPACE):
                logger.info("⌨️ 热键注册: Ctrl+Shift+Space → 迷你窗口")
            else:
                logger.error(f"注册迷你窗口热键失败: {ctypes.get_last_error()}")

            if RegisterHotKey(None, self.HOTKEY_MAIN, MOD_CONTROL | MOD_SHIFT, VK_TAB):
                logger.info("⌨️ 热键注册: Ctrl+Shift+Tab → 主窗口")
            else:
                logger.error(f"注册主窗口热键失败: {ctypes.get_last_error()}")

            self.registered = True

            msg = wintypes.MSG()
            while not self.stop_flag:
                # 使用 PeekMessage 非阻塞检查
                if PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_HOTKEY:
                        if msg.wParam == self.HOTKEY_MINI:
                            logger.info("⌨️ 检测到热键: Ctrl+Shift+Space")
                            self.show_mini_signal.emit()
                        elif msg.wParam == self.HOTKEY_MAIN:
                            logger.info("⌨️ 检测到热键: Ctrl+Shift+Tab")
                            self.show_main_signal.emit()
                else:
                    time.sleep(0.05)

            UnregisterHotKey(None, self.HOTKEY_MINI)
            UnregisterHotKey(None, self.HOTKEY_MAIN)
            self.registered = False
            logger.info("⌨️ 全局热键已注销")

        except Exception as e:
            logger.error(f"热键监听错误: {e}")
            import traceback
            traceback.print_exc()
            self.registered = False

    def _do_show_mini(self):
        """显示迷你窗口（在主线程执行）"""
        try:
            if hasattr(self.main_window, 'mini_window') and self.main_window.mini_window:
                self.main_window.mini_window.show_window()
        except Exception as e:
            logger.error(f"显示迷你窗口失败: {e}")

    def _do_show_main(self):
        """显示主窗口（在主线程执行）"""
        try:
            self.main_window.show()
            self.main_window.showNormal()
            self.main_window.raise_()
            self.main_window.activateWindow()
            self.main_window.search_input.setFocus()
            self.main_window.search_input.selectAll()
        except Exception as e:
            logger.error(f"显示主窗口失败: {e}")

    def stop(self):
        self.stop_flag = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.registered = False


# ==================== 系统托盘管理 ====================
class TrayManager:
    """系统托盘管理器"""

    def __init__(self, main_window):
        self.main_window = main_window
        self.tray_icon = None
        self.is_running = False

    def start(self):
        if self.is_running:
            return True

        try:
            self.tray_icon = QSystemTrayIcon(self.main_window)

            # 创建图标
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QColor("#4CAF50"))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(8, 8, 32, 32)
            painter.drawLine(36, 36, 54, 54)
            painter.end()

            self.tray_icon.setIcon(QIcon(pixmap))
            self.tray_icon.setToolTip("极速文件搜索")

            # 创建菜单
            menu = QMenu()

            show_action = QAction("显示主窗口", self.main_window)
            show_action.triggered.connect(self._show_window)
            menu.addAction(show_action)

            mini_action = QAction("迷你搜索", self.main_window)
            mini_action.triggered.connect(self._show_mini)
            menu.addAction(mini_action)

            menu.addSeparator()

            rebuild_action = QAction("重建索引", self.main_window)
            rebuild_action.triggered.connect(self.main_window._build_index)
            menu.addAction(rebuild_action)

            menu.addSeparator()

            quit_action = QAction("退出", self.main_window)
            quit_action.triggered.connect(self._quit)
            menu.addAction(quit_action)

            self.tray_icon.setContextMenu(menu)
            self.tray_icon.activated.connect(self._on_activated)
            self.tray_icon.show()

            self.is_running = True
            logger.info("🔔 系统托盘已启动")
            return True

        except Exception as e:
            logger.error(f"启动托盘失败: {e}")
            return False

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def _show_window(self):
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()
        self.main_window.search_input.setFocus()

    def _show_mini(self):
        if hasattr(self.main_window, 'mini_window') and self.main_window.mini_window:
            self.main_window.mini_window.show_window()

    def _quit(self):
        self.stop()
        self.main_window._do_quit()

    def stop(self):
        if self.tray_icon:
            self.tray_icon.hide()
            self.tray_icon = None
        self.is_running = False
        logger.info("🔔 系统托盘已停止")

    def show_message(self, title, message):
        if self.tray_icon and self.is_running:
            self.tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 3000)


# ==================== 迷你搜索窗口 ====================
class MiniSearchWindow:
    """迷你搜索窗口"""

    def __init__(self, app):
        self.app = app
        self.window = None
        self.search_mode = "index"
        self.results = []
        self.result_listbox = None
        self.mode_label = None
        self.search_entry = None
        self.search_var = None
        self.tip_label = None
        self.result_frame = None
        self.tip_frame = None
        self.button_frame = None
        self.ctx_menu = None

    def show(self):
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
        self.window = QDialog(None)
        self.window.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.window.setAttribute(Qt.WA_TranslucentBackground, False)
        self.window.setFixedSize(720, 70)
        self.window.setStyleSheet("""
            QDialog { background-color: #b8e0f0; border: 3px solid #006699; }
            QLineEdit { padding: 8px; font-size: 14px; border: 2px solid #88c0d8; background: white; }
            QLineEdit:focus { border-color: #006699; }
            QListWidget { background: white; border: 1px solid #88c0d8; font-size: 11px; }
            QListWidget::item { padding: 4px; }
            QListWidget::item:selected { background-color: #006699; color: white; }
            QListWidget::item:hover { background-color: #e0f0f8; }
            QPushButton { padding: 5px 10px; background: white; border: 1px groove #ccc; font-size: 9px; }
            QPushButton:hover { background: #e8f4f8; }
            QLabel { color: #004466; }
        """)

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
        self.left_arrow.setCursor(Qt.PointingHandCursor)
        self.left_arrow.mousePressEvent = lambda e: self._on_mode_switch()
        mode_frame.addWidget(self.left_arrow)

        self.mode_label = QLabel("索引搜索")
        self.mode_label.setFont(QFont("微软雅黑", 10, QFont.Bold))
        self.mode_label.setFixedWidth(70)
        self.mode_label.setAlignment(Qt.AlignCenter)
        mode_frame.addWidget(self.mode_label)

        self.right_arrow = QLabel("▶")
        self.right_arrow.setFont(QFont("Arial", 12, QFont.Bold))
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
        self.close_btn.enterEvent = lambda e: self.close_btn.setStyleSheet("color: #cc0000;")
        self.close_btn.leaveEvent = lambda e: self.close_btn.setStyleSheet("color: #666666;")
        search_layout.addWidget(self.close_btn)

        main_layout.addLayout(search_layout)

        # 结果列表（初始隐藏）
        self.result_frame = QWidget()
        self.result_frame.setVisible(False)
        result_layout = QHBoxLayout(self.result_frame)
        result_layout.setContentsMargins(0, 0, 0, 0)

        self.result_listbox = QListWidget()
        self.result_listbox.setFont(QFont("微软雅黑", 11))
        self.result_listbox.setMinimumHeight(250)
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

        self.tip_label = QLabel("Enter=打开  Ctrl+Enter=定位  Ctrl+C=复制  Delete=删除  Tab=主页面  Esc=关闭")
        self.tip_label.setFont(QFont("微软雅黑", 9))
        tip_layout.addWidget(self.tip_label)

        main_layout.addWidget(self.tip_frame)

        # 创建右键菜单
        self._create_context_menu()

        # 安装事件过滤器处理按键
        self.window.installEventFilter(self)
        self.search_entry.installEventFilter(self)
        self.result_listbox.installEventFilter(self)

        # 显示窗口
        self.window.show()
        self.window.activateWindow()
        self.search_entry.setFocus()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            # Escape - 关闭
            if key == Qt.Key_Escape:
                self._on_close()
                return True

            # Tab - 切换到主页面
            if key == Qt.Key_Tab:
                self._on_switch_to_main()
                return True

            # Enter
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if modifiers & Qt.ControlModifier:
                    self._on_locate()
                else:
                    self._on_search()
                return True

            # Ctrl+C - 复制
            if key == Qt.Key_C and modifiers & Qt.ControlModifier:
                self._on_copy_shortcut()
                return True

            # Delete - 删除
            if key == Qt.Key_Delete:
                self._on_delete_shortcut()
                return True

            # 上下键
            if key == Qt.Key_Up:
                self._on_up()
                return True
            if key == Qt.Key_Down:
                self._on_down()
                return True

            # 左右键切换模式（仅在搜索框且光标在边界时）
            if obj == self.search_entry:
                text = self.search_entry.text()
                cursor = self.search_entry.cursorPosition()
                if key == Qt.Key_Left and cursor == 0:
                    self._on_mode_switch()
                    return True
                if key == Qt.Key_Right and cursor == len(text):
                    self._on_mode_switch()
                    return True

        return False

    def _create_context_menu(self):
        self.ctx_menu = QMenu(self.window)
        self.ctx_menu.addAction("打开", self._btn_open)
        self.ctx_menu.addAction("定位", self._btn_locate)
        self.ctx_menu.addSeparator()
        self.ctx_menu.addAction("复制", self._btn_copy)
        self.ctx_menu.addSeparator()
        self.ctx_menu.addAction("删除", self._btn_delete)
        self.ctx_menu.addAction("主页面查看", self._btn_to_main)

    def _on_mode_switch(self, event=None):
        if self.search_mode == "index":
            self.search_mode = "realtime"
            self.mode_label.setText("实时搜索")
        else:
            self.search_mode = "index"
            self.mode_label.setText("索引搜索")

    def _on_search(self, event=None):
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
        if not self.app.index_mgr.is_ready:
            self.result_listbox.addItem("   ⚠️ 索引未就绪，请先构建索引")
            return

        keywords = keyword.lower().split()
        scope_targets = self.app._get_scope_targets()
        results = self.app.index_mgr.search(keywords, scope_targets, limit=200)

        if results is None:
            self.result_listbox.addItem("   ⚠️ 搜索失败")
            return

        self._display_results(results)

    def _search_realtime(self, keyword):
        self.result_listbox.addItem("   🔍 正在搜索...")
        QApplication.processEvents()

        keywords = keyword.lower().split()
        scope_targets = self.app._get_scope_targets()
        results = []
        count = 0

        for target in scope_targets:
            if count >= 200 or not os.path.isdir(target):
                continue
            try:
                for root, dirs, files in os.walk(target):
                    dirs[:] = [
                        d for d in dirs
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
                                sz, mt = (0, st.st_mtime) if is_dir else (st.st_size, st.st_mtime)
                            except:
                                sz, mt = 0, 0
                            results.append((name, fp, sz, mt, 1 if is_dir else 0))
                            count += 1
            except:
                continue

        self.result_listbox.clear()
        self._display_results(results)

    def _display_results(self, results):
        if not results:
            self.result_listbox.addItem("   😔 未找到匹配的文件")
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

            self.results.append({
                "filename": fn,
                "fullpath": fp,
                "size": sz,
                "mtime": mt,
                "is_dir": is_dir,
            })

        if self.results:
            self.result_listbox.setCurrentRow(0)

        self.tip_label.setText(
            f"找到 {len(self.results)} 个  │  Enter=打开  Ctrl+Enter=定位  Delete=删除  Tab=主页面  Esc=关闭"
        )

    def _show_results_area(self):
        self.result_frame.setVisible(True)
        self.button_frame.setVisible(True)
        self.tip_frame.setVisible(True)

        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - 720) // 2
        y = int(screen.height() * 0.15)
        self.window.setFixedSize(720, 480)
        self.window.move(x, y)

    def _get_current_item(self):
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
        item = self._get_current_item()
        if not item:
            return
        QApplication.clipboard().setText(item["fullpath"])

    def _on_delete_shortcut(self, event=None):
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

    def _on_open(self, event=None):
        item = self._get_current_item()
        if not item:
            return
        try:
            if item["is_dir"]:
                if IS_WINDOWS:
                    subprocess.Popen(f'explorer "{item["fullpath"]}"')
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(item["fullpath"]))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(item["fullpath"]))
            self.close()
        except Exception as e:
            logger.error(f"打开失败: {e}")

    def _on_locate(self, event=None):
        item = self._get_current_item()
        if not item:
            return
        try:
            if IS_WINDOWS:
                subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(item["fullpath"])))
            self.close()
        except Exception as e:
            logger.error(f"定位失败: {e}")

    def _on_switch_to_main(self, event=None):
        keyword = self.search_entry.text().strip()
        results_copy = list(self.results)

        self.close()

        # 显示主窗口
        self.app.show()
        self.app.showNormal()
        self.app.raise_()
        self.app.activateWindow()

        if keyword:
            self.app.search_input.setText(keyword)

            if results_copy:
                self.app._clear_results()

                for item in results_copy:
                    ext = os.path.splitext(item["filename"])[1].lower()
                    if item["is_dir"]:
                        tc = 0
                    elif ext in ARCHIVE_EXTS:
                        tc = 1
                    else:
                        tc = 2

                    size_str = (
                        "📂 文件夹" if tc == 0
                        else ("📦 压缩包" if tc == 1 else format_size(item["size"]))
                    )
                    mtime_str = "-" if tc == 0 else format_time(item["mtime"])

                    with self.app.results_lock:
                        self.app.all_results.append({
                            "filename": item["filename"],
                            "fullpath": item["fullpath"],
                            "dir_path": os.path.dirname(item["fullpath"]),
                            "size": item["size"],
                            "mtime": item["mtime"],
                            "type_code": tc,
                            "size_str": size_str,
                            "mtime_str": mtime_str,
                        })
                        self.app.shown_paths.add(item["fullpath"])

                with self.app.results_lock:
                    self.app.filtered_results = list(self.app.all_results)

                self.app._render_page()
                self.app.status_label.setText(f"✅ 从迷你窗口导入 {len(results_copy)} 个结果")

        self.app.search_input.setFocus()
        self.app.search_input.selectAll()

    def _on_up(self, event=None):
        if not self.results:
            return
        row = self.result_listbox.currentRow()
        if row > 0:
            self.result_listbox.setCurrentRow(row - 1)

    def _on_down(self, event=None):
        if not self.results:
            return
        row = self.result_listbox.currentRow()
        if row < len(self.results) - 1:
            self.result_listbox.setCurrentRow(row + 1)

    def _on_right_click(self, pos):
        if not self.results:
            return
        item = self.result_listbox.itemAt(pos)
        if item:
            row = self.result_listbox.row(item)
            self.result_listbox.setCurrentRow(row)
            self.ctx_menu.exec_(self.result_listbox.viewport().mapToGlobal(pos))

    def _on_close(self, event=None):
        self.close()

    def close(self):
        """关闭窗口"""
        if self.window:
            self.window.close()
            self.window = None
        self.results.clear()


# ==================== 主窗口 ====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config_mgr = ConfigManager()
        self.index_mgr = IndexManager(config_mgr=self.config_mgr)
        self.file_watcher = FileWatcher(self.index_mgr, config_mgr=self.config_mgr)

        self.search_thread = None
        self.realtime_thread = None
        self.build_thread = None

        self.all_results = []
        self.filtered_results = []
        self.shown_paths = set()
        self.results_lock = threading.Lock()
        self.current_page = 1
        self.page_size = 1000
        self.is_searching = False
        self.last_search_params = None

        self.setWindowTitle("🚀 极速文件搜索 V42 - PySide6版")
        self.setMinimumSize(1200, 800)

        self._apply_theme()
        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()
        self._restore_geometry()
        # ====== 新增：初始化迷你窗口、热键、托盘 ======
        self.mini_window = MiniSearchWindow(self)
        self.hotkey_mgr = HotkeyManager(self)
        self.tray_mgr = TrayManager(self)

        # 启动热键和托盘
        if self.config_mgr.get_hotkey_enabled():
            self.hotkey_mgr.start()
        if self.config_mgr.get_tray_enabled():
            self.tray_mgr.start()
        # ====== 新增结束 ======

        QTimer.singleShot(100, self._check_index)
        QTimer.singleShot(500, self._start_file_watcher)

    def _apply_theme(self):
        theme = self.config_mgr.get_theme()
        if theme == "dark":
            self.setStyleSheet("""
                QMainWindow, QWidget { background-color: #1e1e1e; color: #d4d4d4; }
                QTreeWidget { background-color: #252526; border: 1px solid #3c3c3c; color: #d4d4d4;
                              alternate-background-color: #2d2d2d; }
                QTreeWidget::item:selected { background-color: #094771; }
                QTreeWidget::item:hover { background-color: #2a2d2e; }
                QHeaderView::section { background-color: #333333; color: #d4d4d4; border: 1px solid #3c3c3c; padding: 5px; }
                QLineEdit { padding: 8px; border: 1px solid #3c3c3c; border-radius: 4px; background: #3c3c3c; color: #d4d4d4; }
                QLineEdit:focus { border-color: #0078d4; }
                QPushButton { padding: 8px 16px; background-color: #0e639c; color: white; border: none; border-radius: 4px; }
                QPushButton:hover { background-color: #1177bb; }
                QPushButton:disabled { background-color: #555555; color: #888888; }
                QComboBox { padding: 6px; border: 1px solid #3c3c3c; border-radius: 4px; background: #3c3c3c; color: #d4d4d4; }
                QComboBox QAbstractItemView { background-color: #3c3c3c; color: #d4d4d4; selection-background-color: #094771; }
                QStatusBar { background-color: #007acc; color: white; }
                QProgressBar { border: 1px solid #3c3c3c; border-radius: 4px; background: #3c3c3c; }
                QProgressBar::chunk { background-color: #0e639c; }
                QMenu { background-color: #252526; color: #d4d4d4; border: 1px solid #3c3c3c; }
                QMenu::item:selected { background-color: #094771; }
                QMenuBar { background-color: #333333; color: #d4d4d4; }
                QGroupBox { font-weight: bold; border: 1px solid #3c3c3c; border-radius: 4px; margin-top: 10px; padding-top: 10px; }
                QCheckBox, QRadioButton, QLabel { color: #d4d4d4; }
                QTabWidget::pane { border: 1px solid #3c3c3c; }
                QTabBar::tab { background-color: #2d2d2d; color: #d4d4d4; padding: 8px 16px; border: 1px solid #3c3c3c; }
                QTabBar::tab:selected { background-color: #1e1e1e; }
                QListWidget { background-color: #252526; border: 1px solid #3c3c3c; color: #d4d4d4; }
                QTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3c3c3c; }
                QScrollBar:vertical { background: #1e1e1e; width: 12px; }
                QScrollBar::handle:vertical { background: #5a5a5a; border-radius: 6px; }
            """)
        else:
            self.setStyleSheet("""
                QMainWindow { background-color: #f5f5f5; }
                QTreeWidget { background-color: white; border: 1px solid #ddd; alternate-background-color: #f8f9fa; }
                QTreeWidget::item:selected { background-color: #0078d4; color: white; }
                QTreeWidget::item:hover { background-color: #e5f3ff; }
                QHeaderView::section { background-color: #4CAF50; color: white; border: 1px solid #45a049; padding: 5px; font-weight: bold; }
                QLineEdit { padding: 8px; border: 1px solid #ccc; border-radius: 4px; background: white; }
                QLineEdit:focus { border-color: #0078d4; }
                QPushButton { padding: 8px 16px; background-color: #0078d4; color: white; border: none; border-radius: 4px; }
                QPushButton:hover { background-color: #106ebe; }
                QPushButton:disabled { background-color: #cccccc; color: #666666; }
                QComboBox { padding: 6px; border: 1px solid #ccc; border-radius: 4px; background: white; }
                QStatusBar { background-color: #f0f0f0; }
                QProgressBar { border: 1px solid #ccc; border-radius: 4px; }
                QProgressBar::chunk { background-color: #0078d4; }
                QGroupBox { font-weight: bold; border: 1px solid #ddd; border-radius: 4px; margin-top: 10px; padding-top: 10px; }
            """)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        # 标题栏
        title_layout = QHBoxLayout()
        title_label = QLabel("⚡ 极速搜 V42")
        title_label.setFont(QFont("微软雅黑", 16, QFont.Bold))
        title_label.setStyleSheet("color: #4CAF50;")
        title_layout.addWidget(title_label)

        self.idx_label = QLabel("检查中...")
        self.idx_label.setFont(QFont("微软雅黑", 9))
        title_layout.addWidget(self.idx_label)
        title_layout.addStretch()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        self.theme_combo.setCurrentText(self.config_mgr.get_theme())
        self.theme_combo.currentTextChanged.connect(self._on_theme_change)
        title_layout.addWidget(QLabel("主题:"))
        title_layout.addWidget(self.theme_combo)

        layout.addLayout(title_layout)

        # 搜索栏
        search_layout = QHBoxLayout()

        self.scope_combo = QComboBox()
        self.scope_combo.setMinimumWidth(150)
        self._update_scope_combo()
        search_layout.addWidget(self.scope_combo)

        browse_btn = QPushButton("📂 选择")
        browse_btn.clicked.connect(self._browse_folder)
        search_layout.addWidget(browse_btn)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键词搜索...")
        self.search_input.returnPressed.connect(self._do_search)
        self.search_input.setMinimumWidth(300)
        search_layout.addWidget(self.search_input, 1)

        self.fuzzy_check = QCheckBox("模糊")
        self.fuzzy_check.setChecked(True)
        search_layout.addWidget(self.fuzzy_check)

        self.regex_check = QCheckBox("正则")
        search_layout.addWidget(self.regex_check)

        self.realtime_check = QCheckBox("实时")
        search_layout.addWidget(self.realtime_check)

        self.search_btn = QPushButton("🚀 搜索")
        self.search_btn.clicked.connect(self._do_search)
        search_layout.addWidget(self.search_btn)

        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.clicked.connect(self._stop_search)
        self.stop_btn.setEnabled(False)
        search_layout.addWidget(self.stop_btn)

        self.build_btn = QPushButton("🔄 构建索引")
        self.build_btn.clicked.connect(self._build_index)
        search_layout.addWidget(self.build_btn)

        layout.addLayout(search_layout)

        # 筛选栏
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("筛选:"))

        filter_layout.addWidget(QLabel("格式"))
        self.ext_combo = QComboBox()
        self.ext_combo.addItem("全部")
        self.ext_combo.setMinimumWidth(100)
        self.ext_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.ext_combo)

        filter_layout.addWidget(QLabel("大小"))
        self.size_combo = QComboBox()
        self.size_combo.addItems(["不限", ">1MB", ">10MB", ">100MB", ">500MB", ">1GB"])
        self.size_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.size_combo)

        filter_layout.addWidget(QLabel("时间"))
        self.date_combo = QComboBox()
        self.date_combo.addItems(["不限", "今天", "3天内", "7天内", "30天内", "今年"])
        self.date_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.date_combo)

        clear_filter_btn = QPushButton("清除")
        clear_filter_btn.clicked.connect(self._clear_filter)
        filter_layout.addWidget(clear_filter_btn)

        filter_layout.addStretch()
        self.filter_label = QLabel("")
        filter_layout.addWidget(self.filter_label)

        layout.addLayout(filter_layout)

        # 结果列表
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["📄 文件名", "📂 所在目录", "📊 大小", "🕒 修改时间"])
        self.result_tree.setRootIsDecorated(False)
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setSortingEnabled(True)
        self.result_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_tree.customContextMenuRequested.connect(self._show_context_menu)
        self.result_tree.itemDoubleClicked.connect(self._open_file)

        header = self.result_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.result_tree.setColumnWidth(0, 350)

        layout.addWidget(self.result_tree, 1)

        # 分页栏
        page_layout = QHBoxLayout()
        page_layout.addStretch()
        self.first_btn = QPushButton("⏮")
        self.first_btn.clicked.connect(lambda: self._go_page("first"))
        self.prev_btn = QPushButton("◀")
        self.prev_btn.clicked.connect(lambda: self._go_page("prev"))
        self.page_label = QLabel("第 1/1 页 (0项)")
        self.next_btn = QPushButton("▶")
        self.next_btn.clicked.connect(lambda: self._go_page("next"))
        self.last_btn = QPushButton("⏭")
        self.last_btn.clicked.connect(lambda: self._go_page("last"))

        for btn in [self.first_btn, self.prev_btn, self.next_btn, self.last_btn]:
            btn.setMaximumWidth(40)

        page_layout.addWidget(self.first_btn)
        page_layout.addWidget(self.prev_btn)
        page_layout.addWidget(self.page_label)
        page_layout.addWidget(self.next_btn)
        page_layout.addWidget(self.last_btn)
        page_layout.addStretch()
        layout.addLayout(page_layout)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        # 状态栏
        self.status_bar = self.statusBar()
        self.status_label = QLabel("就绪")
        self.status_bar.addWidget(self.status_label, 1)
        self.stats_label = QLabel()
        self.status_bar.addPermanentWidget(self.stats_label)

    def _setup_menu(self):
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction("📤 导出结果", self._export_results, QKeySequence("Ctrl+E"))
        file_menu.addSeparator()
        file_menu.addAction("📂 打开文件", self._open_file, QKeySequence("Return"))
        file_menu.addAction("🎯 定位文件", self._locate_file, QKeySequence("Ctrl+L"))
        file_menu.addSeparator()
        file_menu.addAction("🚪 退出", self.close, QKeySequence("Alt+F4"))

        # 编辑菜单
        edit_menu = menubar.addMenu("编辑(&E)")
        edit_menu.addAction("✅ 全选", self._select_all, QKeySequence("Ctrl+A"))
        edit_menu.addSeparator()
        edit_menu.addAction("📋 复制路径", self._copy_path, QKeySequence("Ctrl+C"))
        edit_menu.addAction("📄 复制文件", self._copy_file, QKeySequence("Ctrl+Shift+C"))
        edit_menu.addSeparator()
        edit_menu.addAction("🗑️ 删除", self._delete_file, QKeySequence("Delete"))

        # 搜索菜单
        search_menu = menubar.addMenu("搜索(&S)")
        search_menu.addAction("🔍 开始搜索", self._do_search, QKeySequence("Return"))
        search_menu.addAction("🔄 刷新搜索", self._refresh_search, QKeySequence("F5"))
        search_menu.addAction("⏹ 停止搜索", self._stop_search, QKeySequence("Escape"))

        # 工具菜单
        tool_menu = menubar.addMenu("工具(&T)")
        # ====== 新增：迷你搜索入口 ======
        tool_menu.addAction("🔍 迷你搜索", self._show_mini_window, QKeySequence("Ctrl+Shift+Space"))
        tool_menu.addSeparator()
        # ====== 新增结束 ======
        tool_menu.addAction("📊 大文件扫描", self._scan_large_files, QKeySequence("Ctrl+G"))
        tool_menu.addAction("✏ 批量重命名", self._show_batch_rename)
        tool_menu.addAction("🔍 查找重复文件", self._find_duplicates)
        tool_menu.addAction("📁 查找空文件夹", self._find_empty_folders)
        tool_menu.addSeparator()
        tool_menu.addAction("🔧 索引管理", self._show_index_manager)
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

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.search_input.setFocus())
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._on_escape)
        QShortcut(QKeySequence(Qt.Key_Down), self.search_input, self._focus_to_tree)

    def _restore_geometry(self):
        geometry = self.config_mgr.get_window_geometry()
        if geometry:
            try:
                self.restoreGeometry(bytes.fromhex(geometry))
            except:
                pass

    def closeEvent(self, event):
        # 如果启用托盘，最小化到托盘而不是退出
        if self.config_mgr.get_tray_enabled() and self.tray_mgr.is_running:
            event.ignore()
            self.hide()
            self.tray_mgr.show_message("极速文件搜索", "程序已最小化到托盘")
            return

        self._do_quit()
        event.accept()

    def _do_quit(self):
        """真正退出程序"""
        self.config_mgr.set_window_geometry(self.saveGeometry().toHex().data().decode())
        self._stop_search()

        if self.build_thread and self.build_thread.isRunning():
            self.build_thread.stop()
            self.build_thread.wait(3000)

        # 停止热键、托盘、文件监控
        self.hotkey_mgr.stop()
        self.tray_mgr.stop()
        self.file_watcher.stop()
        self.index_mgr.close()

        # 关闭迷你窗口
        if self.mini_window:
            self.mini_window.close()

        QApplication.quit()

    def _on_escape(self):
        if self.is_searching:
            self._stop_search()
        else:
            self.search_input.clear()

    def _focus_to_tree(self):
        if self.result_tree.topLevelItemCount() > 0:
            self.result_tree.setFocus()
            self.result_tree.setCurrentItem(self.result_tree.topLevelItem(0))

    # ==================== 搜索相关 ====================
    def _update_scope_combo(self):
        self.scope_combo.clear()
        self.scope_combo.addItem("所有磁盘 (全盘)")
        for drive in get_drives():
            self.scope_combo.addItem(drive)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择目录")
        if folder:
            self.scope_combo.setCurrentText(folder)

    def _get_scope_targets(self):
        scope = self.scope_combo.currentText()
        if "所有磁盘" in scope:
            targets = []
            for d in get_drives():
                if d.upper().startswith("C"):
                    targets.extend(get_c_scan_dirs(self.config_mgr))
                else:
                    targets.append(d)
            return targets
        return [scope]

    def _do_search(self):
        keyword = self.search_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入关键词")
            return

        if self.is_searching:
            return

        self.config_mgr.add_history(keyword)
        self._clear_results()

        keywords = keyword.lower().split()
        scope_targets = self._get_scope_targets()

        self.last_search_params = {
            "keyword": keyword,
            "keywords": keywords,
            "scope_targets": scope_targets,
            "fuzzy": self.fuzzy_check.isChecked(),
            "regex": self.regex_check.isChecked(),
        }

        use_realtime = self.realtime_check.isChecked() or not self.index_mgr.is_ready

        if use_realtime:
            self._start_realtime_search(keyword, scope_targets)
        else:
            self._start_index_search(keywords, scope_targets)

    def _start_index_search(self, keywords, scope_targets):
        self.status_label.setText("⚡ 索引搜索中...")
        self.is_searching = True
        self._update_search_buttons()

        self.search_thread = SearchThread(
            self.index_mgr, keywords, scope_targets,
            self.fuzzy_check.isChecked(), self.regex_check.isChecked()
        )
        self.search_thread.results_ready.connect(self._on_index_results)
        self.search_thread.search_error.connect(self._on_search_error)
        self.search_thread.finished.connect(self._on_search_finished)
        self.search_thread.start()

    def _start_realtime_search(self, keyword, scope_targets):
        self.status_label.setText("🔍 实时扫描中...")
        self.is_searching = True
        self._update_search_buttons()
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.realtime_thread = RealtimeSearchThread(
            keyword, scope_targets,
            self.fuzzy_check.isChecked(), self.regex_check.isChecked()
        )
        self.realtime_thread.batch_ready.connect(self._on_realtime_batch)
        self.realtime_thread.progress_update.connect(self._on_realtime_progress)
        self.realtime_thread.finished_signal.connect(self._on_realtime_finished)
        self.realtime_thread.start()

    def _on_index_results(self, results):
        keyword = self.last_search_params.get("keyword", "") if self.last_search_params else ""
        keywords = self.last_search_params.get("keywords", []) if self.last_search_params else []

        for row in results:
            fn, fp, sz, mt, is_dir = row[0], row[1], row[2], row[3], row[4]
            
            # 模糊匹配过滤
            if self.last_search_params and self.last_search_params.get("fuzzy"):
                if not self._match_keyword(fn, keywords):
                    continue

            # 补充获取文件大小和时间（如果为0）
            if sz == 0 and mt == 0 and not is_dir:
                try:
                    if os.path.exists(fp):
                        st = os.stat(fp)
                        sz = st.st_size
                        mt = st.st_mtime
                except:
                    pass

            ext = os.path.splitext(fn)[1].lower()
            tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
            self._add_result(fn, fp, sz, mt, tc)

        self._finalize_search()
        self.status_label.setText(f"✅ 找到 {len(self.all_results):,} 个结果")

    def _on_realtime_batch(self, batch):
        for name, fp, sz, mt, tc in batch:
            self._add_result(name, fp, sz, mt, tc)

        # 每批次更新显示
        with self.results_lock:
            self.filtered_results = list(self.all_results)
        self._render_page()
        self.status_label.setText(f"已找到: {len(self.all_results):,}")

    def _on_realtime_progress(self, scanned, path):
        self.progress_bar.setFormat(f"扫描: {scanned} 个目录")

    def _on_realtime_finished(self, elapsed):
        self._finalize_search()
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"✅ 完成: {len(self.all_results):,} 项 ({elapsed:.2f}s)")

    def _on_search_error(self, error):
        self.is_searching = False
        self._update_search_buttons()
        QMessageBox.warning(self, "搜索错误", error)

    def _on_search_finished(self):
        self.is_searching = False
        self._update_search_buttons()

    def _match_keyword(self, filename, keywords):
        if self.last_search_params and self.last_search_params.get("regex"):
            try:
                pattern = keywords[0] if keywords else ""
                return re.search(pattern, filename, re.IGNORECASE) is not None
            except:
                return False
        elif self.last_search_params and self.last_search_params.get("fuzzy"):
            filename_lower = filename.lower()
            for kw in keywords:
                if kw in filename_lower:
                    continue
                if fuzzy_match(kw, filename) >= 50:
                    continue
                return False
            return True
        else:
            filename_lower = filename.lower()
            return all(kw in filename_lower for kw in keywords)

    def _add_result(self, name, path, size, mtime, type_code):
        with self.results_lock:
            if path in self.shown_paths:
                return
            self.shown_paths.add(path)

            size_str = "📂 文件夹" if type_code == 0 else ("📦 压缩包" if type_code == 1 else format_size(size))
            mtime_str = "-" if type_code == 0 else format_time(mtime)

            self.all_results.append({
                "filename": name,
                "fullpath": path,
                "dir_path": os.path.dirname(path),
                "size": size,
                "mtime": mtime,
                "type_code": type_code,
                "size_str": size_str,
                "mtime_str": mtime_str,
            })

    def _clear_results(self):
        self.result_tree.clear()
        with self.results_lock:
            self.all_results.clear()
            self.filtered_results.clear()
            self.shown_paths.clear()
        self.current_page = 1
        self._update_ext_combo()

    def _finalize_search(self):
        self.is_searching = False
        self._update_search_buttons()
        self._update_ext_combo()
        with self.results_lock:
            self.filtered_results = list(self.all_results)
        self._render_page()

    def _stop_search(self):
        if self.realtime_thread and self.realtime_thread.isRunning():
            self.realtime_thread.stop()
            self.realtime_thread.wait(2000)
        if self.search_thread and self.search_thread.isRunning():
            self.search_thread.wait(2000)

        self.is_searching = False
        self._update_search_buttons()
        self.progress_bar.setVisible(False)
        self._finalize_search()
        self.status_label.setText(f"🛑 已停止 ({len(self.all_results):,} 项)")

    def _refresh_search(self):
        if self.last_search_params and not self.is_searching:
            self.search_input.setText(self.last_search_params["keyword"])
            self._do_search()

    def _update_search_buttons(self):
        self.search_btn.setEnabled(not self.is_searching)
        self.stop_btn.setEnabled(self.is_searching)
        self.build_btn.setEnabled(not self.is_searching and not self.index_mgr.is_building)

    # ==================== 筛选功能 ====================
    def _update_ext_combo(self):
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

        self.ext_combo.clear()
        self.ext_combo.addItem("全部")
        for ext, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]:
            self.ext_combo.addItem(f"{ext} ({cnt})")

    def _get_size_min(self):
        mapping = {"不限": 0, ">1MB": 1 << 20, ">10MB": 10 << 20,
                   ">100MB": 100 << 20, ">500MB": 500 << 20, ">1GB": 1 << 30}
        return mapping.get(self.size_combo.currentText(), 0)

    def _get_date_min(self):
        now = time.time()
        day = 86400
        mapping = {"不限": 0, "今天": now - day, "3天内": now - 3 * day,
                   "7天内": now - 7 * day, "30天内": now - 30 * day,
                   "今年": time.mktime(datetime.datetime(datetime.datetime.now().year, 1, 1).timetuple())}
        return mapping.get(self.date_combo.currentText(), 0)

    def _apply_filter(self):
        ext_sel = self.ext_combo.currentText()
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
                        item_ext = os.path.splitext(item["filename"])[1].lower() or "(无)"
                    if item_ext != target_ext:
                        continue
                self.filtered_results.append(item)

        self.current_page = 1
        self._render_page()

        with self.results_lock:
            total = len(self.all_results)
            filtered = len(self.filtered_results)

        if ext_sel != "全部" or size_min > 0 or date_min > 0:
            self.filter_label.setText(f"筛选: {filtered}/{total}")
        else:
            self.filter_label.setText("")

    def _clear_filter(self):
        self.ext_combo.setCurrentIndex(0)
        self.size_combo.setCurrentIndex(0)
        self.date_combo.setCurrentIndex(0)
        with self.results_lock:
            self.filtered_results = list(self.all_results)
        self.current_page = 1
        self._render_page()
        self.filter_label.setText("")

    # ==================== 分页功能 ====================
    def _render_page(self):
        self.result_tree.clear()
        total = len(self.filtered_results)
        total_pages = max(1, math.ceil(total / self.page_size))
        self.current_page = min(self.current_page, total_pages)

        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size

        for item in self.filtered_results[start:end]:
            tree_item = QTreeWidgetItem([
                item["filename"],
                item["dir_path"],
                item["size_str"],
                item["mtime_str"]
            ])
            tree_item.setData(0, Qt.UserRole, item["fullpath"])
            tree_item.setData(1, Qt.UserRole, item)
            self.result_tree.addTopLevelItem(tree_item)

        self.page_label.setText(f"第 {self.current_page}/{total_pages} 页 ({total}项)")
        self._update_page_buttons(total_pages)

    def _update_page_buttons(self, total_pages):
        self.first_btn.setEnabled(self.current_page > 1)
        self.prev_btn.setEnabled(self.current_page > 1)
        self.next_btn.setEnabled(self.current_page < total_pages)
        self.last_btn.setEnabled(self.current_page < total_pages)

    def _go_page(self, action):
        total = len(self.filtered_results)
        total_pages = max(1, math.ceil(total / self.page_size))

        if action == "first":
            self.current_page = 1
        elif action == "prev" and self.current_page > 1:
            self.current_page -= 1
        elif action == "next" and self.current_page < total_pages:
            self.current_page += 1
        elif action == "last":
            self.current_page = total_pages

        self._render_page()

    # ==================== 文件操作 ====================
    def _get_selected_items(self):
        items = []
        for tree_item in self.result_tree.selectedItems():
            data = tree_item.data(1, Qt.UserRole)
            if data:
                items.append(data)
        return items

    def _get_current_item(self):
        items = self._get_selected_items()
        return items[0] if items else None

    def _open_file(self, item=None, column=None):
        data = self._get_current_item()
        if not data:
            return
        try:
            path = data["fullpath"]
            if os.path.exists(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            else:
                QMessageBox.warning(self, "错误", "文件不存在")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开: {e}")

    def _locate_file(self):
        data = self._get_current_item()
        if not data:
            return
        try:
            path = data["fullpath"]
            if IS_WINDOWS:
                subprocess.run(["explorer", "/select,", path])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法定位: {e}")

    def _copy_path(self):
        items = self._get_selected_items()
        if items:
            paths = "\n".join(item["fullpath"] for item in items)
            QApplication.clipboard().setText(paths)
            self.status_label.setText(f"已复制 {len(items)} 个路径")

    def _copy_file(self):
        if not HAS_WIN32:
            QMessageBox.warning(self, "提示", "需要安装 pywin32")
            return

        items = self._get_selected_items()
        if not items:
            return

        try:
            files = [os.path.abspath(item["fullpath"]) for item in items if os.path.exists(item["fullpath"])]
            if not files:
                return

            file_str = "\0".join(files) + "\0\0"
            data = struct.pack("IIIII", 20, 0, 0, 0, 1) + file_str.encode("utf-16le")

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
            win32clipboard.CloseClipboard()
            self.status_label.setText(f"已复制 {len(files)} 个文件")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"复制失败: {e}")

    def _delete_file(self):
        items = self._get_selected_items()
        if not items:
            return

        msg = f"确定删除 {len(items)} 个文件/文件夹？"
        if HAS_SEND2TRASH:
            msg += "\n\n(将移至回收站)"
        else:
            msg += "\n\n⚠️ 警告：将永久删除！"

        if QMessageBox.question(self, "确认删除", msg) != QMessageBox.Yes:
            return

        deleted = 0
        for item in items:
            try:
                path = item["fullpath"]
                if HAS_SEND2TRASH:
                    send2trash.send2trash(path)
                else:
                    if item["type_code"] == 0:
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                with self.results_lock:
                    self.shown_paths.discard(path)
                deleted += 1
            except Exception as e:
                logger.error(f"删除失败: {item['fullpath']} - {e}")

        # 从列表移除
        for tree_item in self.result_tree.selectedItems():
            index = self.result_tree.indexOfTopLevelItem(tree_item)
            self.result_tree.takeTopLevelItem(index)

        self.status_label.setText(f"✅ 已删除 {deleted} 个文件")

    def _select_all(self):
        self.result_tree.selectAll()

    def _show_context_menu(self, pos):
        item = self.result_tree.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        menu.addAction("📂 打开", self._open_file)
        menu.addAction("🎯 定位", self._locate_file)
        menu.addSeparator()
        menu.addAction("📋 复制路径", self._copy_path)
        menu.addAction("📄 复制文件", self._copy_file)
        menu.addSeparator()
        menu.addAction("⭐ 收藏", self._add_to_favorites)
        menu.addSeparator()
        menu.addAction("🗑️ 删除", self._delete_file)

        menu.exec_(self.result_tree.viewport().mapToGlobal(pos))

    # ==================== 索引管理 ====================
    def _check_index(self):
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
                time_info = f" ({time_diff.seconds // 3600}小时前)"

        if s["building"]:
            txt = f"🔄 构建中({s['count']:,}) [{fts}][{mft}]"
        elif s["ready"]:
            txt = f"✅ 就绪({s['count']:,}){time_info} [{fts}][{mft}]"
        else:
            txt = f"❌ 未构建 [{fts}][{mft}]"

        self.idx_label.setText(txt)
        self.stats_label.setText(f"索引: {s['count']:,}")

    def _build_index(self):
        if self.index_mgr.is_building:
            QMessageBox.warning(self, "提示", "索引正在构建中...")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("正在构建索引...")
        self.build_btn.setEnabled(False)
        self.search_btn.setEnabled(False)

        self.build_thread = IndexBuildThread(self.index_mgr, get_drives())
        self.build_thread.progress.connect(self._on_build_progress)
        self.build_thread.finished_signal.connect(self._on_build_finished)
        self.build_thread.start()

    def _on_build_progress(self, count, message):
        self.progress_bar.setFormat(f"{message} ({count:,})")
        self.status_label.setText(message)

    def _on_build_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.build_btn.setEnabled(True)
        self.search_btn.setEnabled(True)
        self._check_index()
        self.status_label.setText(message)

        if success:
            QMessageBox.information(self, "完成", message)
            self._start_file_watcher()
        else:
            QMessageBox.warning(self, "错误", message)

    def _start_file_watcher(self):
        if HAS_WATCHDOG and self.index_mgr.is_ready:
            self.file_watcher.start(get_drives())
            logger.info("👁️ 文件监控已启动")

    def _show_index_manager(self):
        s = self.index_mgr.get_stats()

        dialog = QDialog(self)
        dialog.setWindowTitle("🔧 索引管理")
        dialog.setMinimumSize(450, 350)

        layout = QVBoxLayout(dialog)

        # 状态信息
        info_group = QGroupBox("📊 索引状态")
        info_layout = QGridLayout(info_group)

        c_dirs = get_c_scan_dirs(self.config_mgr)
        c_dirs_str = ", ".join([os.path.basename(d) for d in c_dirs[:3]]) + ("..." if len(c_dirs) > 3 else "")
        last_update_str = datetime.datetime.fromtimestamp(s["time"]).strftime("%m-%d %H:%M") if s["time"] else "从未"

        rows = [
            ("文件数量:", f"{s['count']:,}" if s["count"] else "未构建"),
            ("状态:", "✅就绪" if s["ready"] else ("🔄构建中" if s["building"] else "❌未构建")),
            ("FTS5:", "✅已启用" if s.get("has_fts") else "❌未启用"),
            ("MFT:", "✅已使用" if s.get("used_mft") else "❌未使用"),
            ("构建时间:", last_update_str),
            ("C盘范围:", c_dirs_str),
        ]

        for i, (label, value) in enumerate(rows):
            info_layout.addWidget(QLabel(label), i, 0)
            value_label = QLabel(value)
            if "✅" in value:
                value_label.setStyleSheet("color: #28a745;")
            info_layout.addWidget(value_label, i, 1)

        layout.addWidget(info_group)

        # 按钮
        btn_layout = QHBoxLayout()
        rebuild_btn = QPushButton("🔄 重建索引")
        rebuild_btn.clicked.connect(lambda: (dialog.accept(), self._build_index()))
        delete_btn = QPushButton("🗑️ 删除索引")
        delete_btn.clicked.connect(lambda: self._delete_index(dialog))
        btn_layout.addWidget(rebuild_btn)
        btn_layout.addWidget(delete_btn)
        btn_layout.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)
        dialog.exec_()

    def _delete_index(self, dialog):
        if QMessageBox.question(self, "确认", "确定删除索引？") != QMessageBox.Yes:
            return

        self.file_watcher.stop()
        self.index_mgr.close()

        for ext in ["", "-wal", "-shm"]:
            try:
                os.remove(self.index_mgr.db_path + ext)
            except:
                pass

        self.index_mgr = IndexManager(db_path=self.index_mgr.db_path, config_mgr=self.config_mgr)
        self.file_watcher = FileWatcher(self.index_mgr, config_mgr=self.config_mgr)
        self._check_index()
        dialog.accept()

    # ==================== 工具功能 ====================
    def _export_results(self):
        if not self.filtered_results:
            QMessageBox.information(self, "提示", "没有可导出的结果")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果", "", "CSV文件 (*.csv);;文本文件 (*.txt)"
        )
        if not path:
            return

        try:
            import csv
            with self.results_lock:
                data = [(r["filename"], r["fullpath"], r["size_str"], r["mtime_str"])
                        for r in self.filtered_results]

            if path.endswith(".csv"):
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["文件名", "完整路径", "大小", "修改时间"])
                    writer.writerows(data)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("文件名\t完整路径\t大小\t修改时间\n")
                    for row in data:
                        f.write("\t".join(row) + "\n")

            QMessageBox.information(self, "成功", f"已导出 {len(data)} 条记录")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _scan_large_files(self):
        if not self.all_results:
            QMessageBox.information(self, "提示", "请先进行搜索")
            return

        min_size = 100 * 1024 * 1024
        with self.results_lock:
            large_files = [item for item in self.all_results
                          if item["type_code"] in (1, 2) and item["size"] >= min_size]
            large_files.sort(key=lambda x: x["size"], reverse=True)
            self.filtered_results = large_files

        self.current_page = 1
        self._render_page()

        total_size = sum(f["size"] for f in large_files)
        self.status_label.setText(f"找到 {len(large_files)} 个大文件 (≥100MB)，共 {format_size(total_size)}")
        self.filter_label.setText(f"大文件: {len(large_files)}/{len(self.all_results)}")

    def _find_duplicates(self):
        if not self.all_results:
            QMessageBox.information(self, "提示", "请先进行搜索")
            return

        size_groups = defaultdict(list)
        with self.results_lock:
            for item in self.all_results:
                if item["type_code"] == 2 and item["size"] > 0:
                    key = (item["size"], item["filename"].lower())
                    size_groups[key].append(item)

        duplicates = []
        for key, items in size_groups.items():
            if len(items) > 1:
                duplicates.extend(items)

        duplicates.sort(key=lambda x: (x["size"], x["filename"].lower()), reverse=True)

        with self.results_lock:
            self.filtered_results = duplicates

        self.current_page = 1
        self._render_page()
        self.status_label.setText(f"找到 {len(duplicates)} 个可能重复的文件")
        self.filter_label.setText(f"重复: {len(duplicates)}/{len(self.all_results)}")

    def _find_empty_folders(self):
        if not self.all_results:
            QMessageBox.information(self, "提示", "请先进行搜索")
            return

        empty_folders = []
        with self.results_lock:
            for item in self.all_results:
                if item["type_code"] == 0:
                    try:
                        if os.path.exists(item["fullpath"]) and not os.listdir(item["fullpath"]):
                            empty_folders.append(item)
                    except:
                        pass
            self.filtered_results = empty_folders

        self.current_page = 1
        self._render_page()
        self.status_label.setText(f"找到 {len(empty_folders)} 个空文件夹")
        self.filter_label.setText(f"空文件夹: {len(empty_folders)}/{len(self.all_results)}")

    def _show_batch_rename(self):
        items = self._get_selected_items()
        if not items:
            with self.results_lock:
                items = list(self.filtered_results)
        if not items:
            QMessageBox.information(self, "提示", "没有可重命名的结果")
            return

        def on_rename(renamed_pairs):
            with self.results_lock:
                for old_path, new_path in renamed_pairs:
                    for item in self.all_results:
                        if item["fullpath"] == old_path:
                            item["fullpath"] = new_path
                            item["filename"] = os.path.basename(new_path)
                            item["dir_path"] = os.path.dirname(new_path)
                            break
            self._render_page()

        dialog = BatchRenameDialog(self, items, on_rename)
        dialog.exec_()

    # ==================== 收藏夹 ====================
    def _update_favorites_menu(self):
        self.fav_menu.clear()
        self.fav_menu.addAction("⭐ 收藏当前目录", self._add_scope_to_favorites)
        self.fav_menu.addAction("📂 管理收藏夹", self._manage_favorites)
        self.fav_menu.addSeparator()

        favorites = self.config_mgr.get_favorites()
        if favorites:
            for fav in favorites:
                action = self.fav_menu.addAction(f"📁 {fav['name']}")
                action.triggered.connect(lambda checked, p=fav["path"]: self._goto_favorite(p))
        else:
            self.fav_menu.addAction("(无收藏)").setEnabled(False)

    def _add_to_favorites(self):
        item = self._get_current_item()
        if item:
            self.config_mgr.add_favorite(item["fullpath"])
            self._update_favorites_menu()
            self.status_label.setText(f"已收藏: {item['filename']}")

    def _add_scope_to_favorites(self):
        scope = self.scope_combo.currentText()
        if "所有磁盘" in scope:
            QMessageBox.information(self, "提示", "请先选择一个具体目录")
            return
        self.config_mgr.add_favorite(scope)
        self._update_favorites_menu()
        QMessageBox.information(self, "成功", f"已收藏: {scope}")

    def _goto_favorite(self, path):
        if os.path.exists(path):
            self.scope_combo.setCurrentText(path)
        else:
            QMessageBox.warning(self, "警告", f"目录不存在: {path}")

    def _manage_favorites(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("📂 管理收藏夹")
        dialog.setMinimumSize(450, 350)

        layout = QVBoxLayout(dialog)

        listbox = QListWidget()
        for fav in self.config_mgr.get_favorites():
            listbox.addItem(f"{fav['name']} - {fav['path']}")
        layout.addWidget(listbox)

        btn_layout = QHBoxLayout()
        remove_btn = QPushButton("删除选中")

        def remove_selected():
            row = listbox.currentRow()
            if row >= 0:
                favs = self.config_mgr.get_favorites()
                if row < len(favs):
                    self.config_mgr.remove_favorite(favs[row]["path"])
                    listbox.takeItem(row)
                    self._update_favorites_menu()

        remove_btn.clicked.connect(remove_selected)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)
        dialog.exec_()

    # ==================== 设置和帮助 ====================
    def _show_mini_window(self):
        """显示迷你搜索窗口"""
        if self.mini_window:
            self.mini_window.show_window()

    def _show_settings(self):
        dialog = SettingsDialog(self, self.config_mgr)
        if dialog.exec_() == QDialog.Accepted:
            self._apply_theme()
            self._update_scope_combo()

            # ====== 新增：更新热键状态 ======
            if self.config_mgr.get_hotkey_enabled():
                if not self.hotkey_mgr.registered:
                    self.hotkey_mgr.start()
            else:
                if self.hotkey_mgr.registered:
                    self.hotkey_mgr.stop()

            # 更新托盘状态
            if self.config_mgr.get_tray_enabled():
                if not self.tray_mgr.is_running:
                    self.tray_mgr.start()
            else:
                if self.tray_mgr.is_running:
                    self.tray_mgr.stop()
            # ====== 新增结束 ======

            self.status_label.setText("设置已保存")

    def _on_theme_change(self, theme):
        self.config_mgr.set_theme(theme)
        self._apply_theme()
        self.status_label.setText(f"主题已切换: {theme}")

    def _show_shortcuts(self):
        shortcuts = """
快捷键列表:

【全局热键】(任何时候都可用)
  Ctrl+Shift+Space    打开迷你搜索窗口
  Ctrl+Shift+Tab      显示/激活主窗口

【搜索操作】
  Ctrl+F              聚焦搜索框
  Enter               开始搜索
  F5                  刷新搜索
  Escape              停止搜索/清空

【文件操作】
  Enter               打开选中文件
  Ctrl+L              定位文件
  Delete              删除文件

【编辑操作】
  Ctrl+A              全选
  Ctrl+C              复制路径
  Ctrl+Shift+C        复制文件

【工具】
  Ctrl+E              导出结果
  Ctrl+G              大文件扫描

【迷你窗口快捷键】
  Enter               打开文件
  Ctrl+Enter          定位文件
  Delete              删除文件
  Tab                 切换到主页面
  Escape              关闭迷你窗口
  ↑/↓                 选择上/下一个
        """
        QMessageBox.information(self, "⌨️ 快捷键列表", shortcuts)

    def _show_about(self):
        QMessageBox.about(self, "关于", """
<h3>🚀 极速文件搜索 V42 - PySide6版</h3>
<p>功能特性:</p>
<ul>
<li>MFT极速索引 (Windows NTFS)</li>
<li>FTS5全文搜索</li>
<li>模糊/正则搜索</li>
<li>实时文件监控</li>
<li>收藏夹管理</li>
<li>批量重命名</li>
<li>大文件扫描</li>
<li>重复文件查找</li>
</ul>
<p>© 2024</p>
        """)


# ==================== 程序入口 ====================
def main():
    logger.info("🚀 极速文件搜索 V42 - PySide6版 启动")
    logger.info("功能: MFT索引、FTS5搜索、迷你窗口、全局热键、系统托盘")

    # 高DPI支持 - PySide6 默认已启用，这里只做兼容处理
    try:
        if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
        if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        # PySide6 6.4+ 已移除这些属性，默认启用高DPI
        pass

    # Windows DPI
    if IS_WINDOWS:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("极速文件搜索")
    app.setOrganizationName("FileSearch")

    # ====== 新增：防止窗口关闭时退出（因为有托盘）======
    app.setQuitOnLastWindowClosed(False)
    # ====== 新增结束 ======

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()