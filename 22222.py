import sys
import os
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

# Qt Imports
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QPushButton, QLineEdit, QComboBox, QTableView, 
                               QHeaderView, QMenu, QSystemTrayIcon, QMessageBox, QDialog,
                               QAbstractItemView, QCheckBox, QProgressBar, QFileDialog,
                               QFormLayout, QSpinBox, QRadioButton, QButtonGroup, QGroupBox,
                               QListWidget, QListWidgetItem, QTextEdit, QFrame, QStyleFactory, QStyledItemDelegate)
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QThread, Signal, QTimer, QSize, QObject, QEvent, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QAction, QIcon, QCursor, QActionGroup, QColor, QBrush, QKeySequence, QPixmap, QPainter, QFont, QTextDocument

# ==================== [后端] 配置 ====================
LOG_DIR = Path.home() / ".filesearch"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', handlers=[logging.NullHandler()])
logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"
HAS_RUST_ENGINE = False
RUST_ENGINE = None

# Rust DLL 加载
if IS_WINDOWS:
    try:
        class ScanResult(ctypes.Structure): _fields_ = [("data", ctypes.POINTER(ctypes.c_uint8)), ("data_len", ctypes.c_size_t), ("count", ctypes.c_size_t)]
        dll_path = next((p for p in [Path(__file__).parent / "file_scanner_engine.dll", Path.cwd() / "file_scanner_engine.dll"] if p.exists()), None)
        if dll_path:
            if hasattr(os, 'add_dll_directory'): os.add_dll_directory(str(dll_path.parent.resolve()))
            RUST_ENGINE = ctypes.CDLL(str(dll_path))
            RUST_ENGINE.scan_drive_packed.argtypes = [ctypes.c_uint16]; RUST_ENGINE.scan_drive_packed.restype = ScanResult
            RUST_ENGINE.free_scan_result.argtypes = [ScanResult]; RUST_ENGINE.free_scan_result.restype = None
            HAS_RUST_ENGINE = True
    except: pass

# 依赖检查
try: import apsw; HAS_DB = True
except: HAS_DB = False
try: import send2trash; HAS_TRASH = True
except: HAS_TRASH = False
try: import win32clipboard; import win32con; import win32gui; HAS_WIN32 = True
except: HAS_WIN32 = False
try: import pystray; from PIL import Image, ImageDraw; HAS_TRAY = True
except: HAS_TRAY = False
try: from watchdog.observers import Observer; from watchdog.events import FileSystemEventHandler; HAS_WATCHDOG = True
except: HAS_WATCHDOG = False

CAD_PATTERN = re.compile(r'cad20(1[0-9]|2[0-4])', re.IGNORECASE)
AUTOCAD_PATTERN = re.compile(r'autocad_20(1[0-9]|2[0-5])', re.IGNORECASE)
SKIP_DIRS_LOWER = {'windows', 'program files', 'program files (x86)', 'programdata', '$recycle.bin', 'system volume information', 'appdata', 'boot', 'node_modules', '.git', '__pycache__', 'site-packages', 'sys', 'recovery', 'config.msi', '$windows.~bt', '$windows.~ws', 'cache', 'caches', 'temp', 'tmp', 'logs', 'log', '.vscode', '.idea', '.vs', 'obj', 'bin', 'debug', 'release', 'packages', '.nuget', 'bower_components'}
SKIP_EXTS = {'.lsp', '.fas', '.lnk', '.html', '.htm', '.xml', '.ini', '.lsp_bak', '.cuix', '.arx', '.crx', '.fx', '.dbx', '.kid', '.ico', '.rz', '.dll', '.sys', '.tmp', '.log', '.dat', '.db', '.pdb', '.obj', '.pyc', '.class', '.cache', '.lock'}
ARCHIVE_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.jar', '.cab', '.bz2', '.xz'}

# ==================== [后端] 辅助函数 ====================
def format_size(size):
    if size <= 0: return ""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.0f} {unit}" if unit == 'B' else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def format_time(timestamp):
    try: return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
    except: return ""

def get_c_scan_dirs(config_mgr=None):
    if config_mgr: return config_mgr.get_enabled_c_paths()
    default_dirs = [os.path.expandvars(r"%TEMP%"), os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"), os.path.expandvars(r"%USERPROFILE%\Desktop"), os.path.expandvars(r"%USERPROFILE%\Documents"), os.path.expandvars(r"%USERPROFILE%\Downloads")]
    return [os.path.normpath(p) for p in default_dirs if os.path.isdir(p)]

def is_in_allowed_paths(path_lower, allowed_paths_lower):
    if not allowed_paths_lower: return False
    return any(path_lower.startswith(ap + '\\') or path_lower == ap for ap in allowed_paths_lower)

def should_skip_path(path_lower, allowed_paths_lower=None):
    if allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower): return False
    if any(part in SKIP_DIRS_LOWER for part in path_lower.replace('/', '\\').split('\\')): return True
    return any(x in path_lower for x in ['site-packages', 'tangent']) or CAD_PATTERN.search(path_lower) or AUTOCAD_PATTERN.search(path_lower)

def should_skip_dir(name_lower, path_lower=None, allowed_paths_lower=None):
    if CAD_PATTERN.search(name_lower) or AUTOCAD_PATTERN.search(name_lower) or 'tangent' in name_lower: return True
    if path_lower and allowed_paths_lower and is_in_allowed_paths(path_lower, allowed_paths_lower): return False
    return name_lower in SKIP_DIRS_LOWER

def parse_search_scope(scope_str, get_drives_fn, config_mgr=None):
    targets = []
    if "所有磁盘" in scope_str:
        for d in get_drives_fn():
            if d.upper().startswith('C:'): targets.extend(get_c_scan_dirs(config_mgr))
            else: targets.append(os.path.normpath(d).rstrip("\\/ "))
    else:
        s = scope_str.strip()
        if os.path.isdir(s): targets.append(os.path.normpath(s).rstrip("\\/ "))
        else: targets.append(s)
    return targets

class ConfigManager:
    def __init__(self):
        self.config_file = LOG_DIR / "config.json"
        self.config = self._load()
    def _load(self):
        try: return json.loads(self.config_file.read_text("utf-8"))
        except: return {"search_history": [], "favorites": [], "c_scan_paths": {"initialized": False}, "enable_global_hotkey": True, "minimize_to_tray": True}
    def save(self):
        try: self.config_file.write_text(json.dumps(self.config, indent=2), "utf-8")
        except: pass
    def add_history(self, txt):
        if not txt: return
        h = self.config.get("search_history", [])
        if txt in h: h.remove(txt)
        h.insert(0, txt); self.config["search_history"] = h[:20]; self.save()
    def get_history(self): return self.config.get("search_history", [])
    def clear_history(self): self.config["search_history"] = []; self.save()
    def add_favorite(self, path, name=None):
        favs = self.config.get("favorites", []); name = name or os.path.basename(path) or path
        if not any(f['path'].lower() == path.lower() for f in favs):
            favs.append({"name": name, "path": path}); self.config["favorites"] = favs; self.save()
    def remove_favorite(self, path):
        self.config["favorites"] = [f for f in self.config.get("favorites", []) if f['path'].lower() != path.lower()]
        self.save()
    def get_favorites(self): return self.config.get("favorites", [])
    def get_c_scan_paths(self): return self.config.get("c_scan_paths", {}).get("paths", self._default_c())
    def set_c_scan_paths(self, paths): self.config["c_scan_paths"] = {"paths": paths, "initialized": True}; self.save()
    def _default_c(self):
        dirs = [r"%TEMP%", r"%APPDATA%\Microsoft\Windows\Recent", r"%USERPROFILE%\Desktop", r"%USERPROFILE%\Documents", r"%USERPROFILE%\Downloads"]
        return [{"path": os.path.normpath(os.path.expandvars(d)), "enabled": True} for d in dirs if os.path.isdir(os.path.expandvars(d))]
    def get_enabled_c_paths(self): return [p['path'] for p in self.get_c_scan_paths() if p.get('enabled', True) and os.path.isdir(p['path'])]
    def get_hotkey_enabled(self): return self.config.get("enable_global_hotkey", True)
    def set_hotkey_enabled(self, v): self.config["enable_global_hotkey"] = v; self.save()
    def get_tray_enabled(self): return self.config.get("minimize_to_tray", True)
    def set_tray_enabled(self, v): self.config["minimize_to_tray"] = v; self.save()

# ==================== [后端] MFT/USN (核心) ====================
if IS_WINDOWS:
    import ctypes.wintypes as wintypes
    GENERIC_READ = 0x80000000; GENERIC_WRITE = 0x40000000; FILE_SHARE_READ = 0x00000001; FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3; FILE_FLAG_BACKUP_SEMANTICS = 0x02000000; FSCTL_ENUM_USN_DATA = 0x000900B3; FSCTL_QUERY_USN_JOURNAL = 0x000900F4
    FILE_ATTRIBUTE_DIRECTORY = 0x10
    class USN_JOURNAL_DATA_V0(ctypes.Structure): _fields_ = [("UsnJournalID", ctypes.c_uint64), ("FirstUsn", ctypes.c_int64), ("NextUsn", ctypes.c_int64), ("LowestValidUsn", ctypes.c_int64), ("MaxUsn", ctypes.c_int64), ("MaximumSize", ctypes.c_uint64), ("AllocationDelta", ctypes.c_uint64)]
    class USN_RECORD_V2(ctypes.Structure): _fields_ = [("RecordLength", ctypes.c_uint32), ("MajorVersion", ctypes.c_uint16), ("MinorVersion", ctypes.c_uint16), ("FileReferenceNumber", ctypes.c_uint64), ("ParentFileReferenceNumber", ctypes.c_uint64), ("Usn", ctypes.c_int64), ("TimeStamp", ctypes.c_int64), ("Reason", ctypes.c_uint32), ("SourceInfo", ctypes.c_uint32), ("SecurityId", ctypes.c_uint32), ("FileAttributes", ctypes.c_uint32), ("FileNameLength", ctypes.c_uint16), ("FileNameOffset", ctypes.c_uint16)]
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    CreateFileW = kernel32.CreateFileW; CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]; CreateFileW.restype = wintypes.HANDLE
    DeviceIoControl = kernel32.DeviceIoControl; DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]; DeviceIoControl.restype = wintypes.BOOL
    CloseHandle = kernel32.CloseHandle; INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None):
        global MFT_AVAILABLE
        if HAS_RUST_ENGINE:
            try:
                result = RUST_ENGINE.scan_drive_packed(ord(drive_letter.upper()[0]))
                if not result.data or result.count == 0: raise Exception("空数据")
                raw_data = ctypes.string_at(result.data, result.data_len)
                py_list = []; off = 0; n = len(raw_data)
                allowed_paths_lower = [p.lower().rstrip('\\') for p in allowed_paths] if allowed_paths else None
                while off < n:
                    is_dir = raw_data[off]; name_len = int.from_bytes(raw_data[off+1:off+3], 'little')
                    name_lower_len = int.from_bytes(raw_data[off+3:off+5], 'little'); path_len = int.from_bytes(raw_data[off+5:off+7], 'little')
                    parent_len = int.from_bytes(raw_data[off+7:off+9], 'little'); ext_len = raw_data[off+9]; off += 10
                    if off + name_len + name_lower_len + path_len + parent_len + ext_len > n: break
                    name = raw_data[off:off+name_len].decode('utf-8', 'replace'); off += name_len
                    name_lower = raw_data[off:off+name_lower_len].decode('utf-8', 'replace'); off += name_lower_len
                    path = raw_data[off:off+path_len].decode('utf-8', 'replace'); off += path_len
                    parent = raw_data[off:off+parent_len].decode('utf-8', 'replace'); off += parent_len
                    ext = raw_data[off:off+ext_len].decode('utf-8', 'replace') if ext_len else ''; off += ext_len
                    path_lower = path.lower()
                    if allowed_paths_lower:
                        if not any(path_lower.startswith(ap + '\\') or path_lower == ap for ap in allowed_paths_lower): continue
                    else:
                        if should_skip_path(path_lower, None): continue
                        if is_dir and should_skip_dir(name_lower, path_lower, None): continue
                        if not is_dir and ext in skip_exts: continue
                    py_list.append([name, name_lower, path, parent, ext, 0, 0, is_dir])
                
                files_to_stat = [item for item in py_list if item[7] == 0]
                if files_to_stat:
                    GetFileAttributesExW = kernel32.GetFileAttributesExW; GetFileAttributesExW.restype = wintypes.BOOL
                    GetFileAttributesExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p]
                    class WIN32_FILE_ATTRIBUTE_DATA(ctypes.Structure): _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME), ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME), ("nFileSizeHigh", wintypes.DWORD), ("nFileSizeLow", wintypes.DWORD),]
                    def stat_batch(items):
                        for item in items:
                            try:
                                data = WIN32_FILE_ATTRIBUTE_DATA()
                                if GetFileAttributesExW(item[2], 0, ctypes.byref(data)):
                                    item[5] = (data.nFileSizeHigh << 32) + data.nFileSizeLow
                                    item[6] = ((data.ftLastWriteTime.dwHighDateTime << 32) + data.ftLastWriteTime.dwLowDateTime - 116444736000000000) / 10000000
                            except: pass
                    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                        batch_size = max(1, len(files_to_stat) // 16)
                        futures = [executor.submit(stat_batch, files_to_stat[i:i+batch_size]) for i in range(0, len(files_to_stat), batch_size)]
                        concurrent.futures.wait(futures)
                RUST_ENGINE.free_scan_result(result)
                return [tuple(item) for item in py_list]
            except Exception as e: logger.error(f"Rust错误: {e}")

        # Python MFT Fallback
        drive = drive_letter.rstrip(':').upper(); root_path = f"{drive}:\\"
        volume_path = f"\\\\.\\{drive}:"
        h = CreateFileW(volume_path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if h == INVALID_HANDLE_VALUE: raise OSError(f"打开卷失败")
        try:
            jd = USN_JOURNAL_DATA_V0(); br = wintypes.DWORD()
            if not DeviceIoControl(h, FSCTL_QUERY_USN_JOURNAL, None, 0, ctypes.byref(jd), ctypes.sizeof(jd), ctypes.byref(br), None): raise OSError("查询USN失败")
            MFT_AVAILABLE = True; records = {}; BUFFER_SIZE = 1024 * 1024; buf = (ctypes.c_ubyte * BUFFER_SIZE)()
            class MFT_ENUM_DATA(ctypes.Structure): _pack_ = 8; _fields_ = [("StartFileReferenceNumber", ctypes.c_uint64), ("LowUsn", ctypes.c_int64), ("HighUsn", ctypes.c_int64)]
            med = MFT_ENUM_DATA(); med.StartFileReferenceNumber = 0; med.LowUsn = 0; med.HighUsn = jd.NextUsn
            allowed_paths_lower = [p.lower().rstrip('\\') for p in allowed_paths] if allowed_paths else None
            while True:
                ctypes.set_last_error(0)
                if not DeviceIoControl(h, FSCTL_ENUM_USN_DATA, ctypes.byref(med), ctypes.sizeof(med), ctypes.byref(buf), BUFFER_SIZE, ctypes.byref(br), None):
                     if ctypes.get_last_error() != 0: break 
                if br.value <= 8: break
                next_frn = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(ctypes.c_uint64))[0]; offset = 8
                while offset < br.value:
                    if offset + 4 > br.value: break
                    rec_len = ctypes.cast(ctypes.byref(buf, offset), ctypes.POINTER(ctypes.c_uint32))[0]
                    if rec_len == 0 or offset + rec_len > br.value: break
                    if rec_len >= ctypes.sizeof(USN_RECORD_V2):
                        rec = ctypes.cast(ctypes.byref(buf, offset), ctypes.POINTER(USN_RECORD_V2)).contents
                        name_off, name_len = rec.FileNameOffset, rec.FileNameLength
                        if name_len > 0 and offset + name_off + name_len <= br.value:
                            filename = bytes(buf[offset + name_off:offset + name_off + name_len]).decode('utf-16le', errors='replace')
                            if filename and filename[0] not in ('$', '.'):
                                records[rec.FileReferenceNumber & 0x0000FFFFFFFFFFFF] = (filename, rec.ParentFileReferenceNumber & 0x0000FFFFFFFFFFFF, bool(rec.FileAttributes & FILE_ATTRIBUTE_DIRECTORY))
                    offset += rec_len
                med.StartFileReferenceNumber = next_frn
            dirs = {}; files = {}; parent_to_children = {}
            for ref, (name, parent_ref, is_dir) in records.items():
                if is_dir:
                    dirs[ref] = (name, parent_ref)
                    if parent_ref not in parent_to_children: parent_to_children[parent_ref] = []
                    parent_to_children[parent_ref].append(ref)
                else: files[ref] = (name, parent_ref)
            path_cache = {5: root_path}; q = deque([5])
            while q:
                parent_ref = q.popleft(); parent_path = path_cache.get(parent_ref)
                if not parent_path: continue
                parent_path_lower = parent_path.lower()
                if should_skip_path(parent_path_lower, allowed_paths_lower) or should_skip_dir(os.path.basename(parent_path_lower), parent_path_lower, allowed_paths_lower): continue
                if parent_ref in parent_to_children:
                    for child_ref in parent_to_children[parent_ref]:
                        child_name, _ = dirs[child_ref]; path_cache[child_ref] = os.path.join(parent_path, child_name); q.append(child_ref)
            result = []
            for ref, (name, parent_ref) in dirs.items():
                full_path = path_cache.get(ref)
                if full_path and full_path != root_path:
                    parent_dir = path_cache.get(parent_ref, root_path)
                    result.append([name, name.lower(), full_path, parent_dir, '', 0, 0, 1])
            for ref, (name, parent_ref) in files.items():
                parent_path = path_cache.get(parent_ref)
                if parent_path:
                    full_path = os.path.join(parent_path, name)
                    if not should_skip_path(full_path.lower(), allowed_paths_lower):
                        ext = os.path.splitext(name)[1].lower()
                        if ext not in skip_exts:
                            if not allowed_paths_lower or is_in_allowed_paths(full_path.lower(), allowed_paths_lower):
                                result.append([name, name.lower(), full_path, parent_path, ext, 0, 0, 0])
            files_to_stat = [item for item in result if item[7] == 0]
            if files_to_stat:
                GetFileAttributesExW = kernel32.GetFileAttributesExW; GetFileAttributesExW.restype = wintypes.BOOL
                class WIN32_FILE_ATTRIBUTE_DATA(ctypes.Structure): _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME), ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME), ("nFileSizeHigh", wintypes.DWORD), ("nFileSizeLow", wintypes.DWORD),]
                def stat_worker_win32(items_batch):
                    for item in items_batch:
                        try:
                            data = WIN32_FILE_ATTRIBUTE_DATA()
                            if GetFileAttributesExW(item[2], 0, ctypes.byref(data)):
                                item[5] = (data.nFileSizeHigh << 32) + data.nFileSizeLow
                                item[6] = ((data.ftLastWriteTime.dwHighDateTime << 32) + data.ftLastWriteTime.dwLowDateTime - 116444736000000000) / 10000000
                        except: pass
                batch_size = math.ceil(len(files_to_stat) / 32)
                with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
                    futures = [executor.submit(stat_worker_win32, files_to_stat[i:i+batch_size]) for i in range(0, len(files_to_stat), batch_size)]
                    concurrent.futures.wait(futures)
            return [tuple(item) for item in result]
        finally: CloseHandle(h)
else:
    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts, allowed_paths=None): raise OSError("MFT仅Windows可用")

# ==================== [后端] 数据库核心 ====================
class IndexManager:
    def __init__(self, config_mgr=None):
        self.db_path = str(LOG_DIR / "index.db")
        self.conn = None
        self.lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        if not HAS_DB: return
        try:
            self.conn = apsw.Connection(self.db_path)
            c = self.conn.cursor()
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA cache_size=-2000000")
            c.execute("PRAGMA temp_store=MEMORY")
            c.execute("""CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, filename TEXT, filename_lower TEXT, full_path TEXT, parent_dir TEXT, extension TEXT, size INT, mtime REAL, is_dir INT)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
            try:
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(filename, content=files, content_rowid=id)")
                c.execute("CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN INSERT INTO fts(rowid, filename) VALUES (new.id, new.filename); END")
                c.execute("CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN INSERT INTO fts(fts, rowid, filename) VALUES('delete', old.id, old.filename); END")
            except: pass
            c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        except Exception as e:
            logger.error(f"DB Init Error: {e}")

    def search(self, keywords, scope=None, limit=5000):
        if not self.conn: return []
        try:
            with self.lock:
                c = self.conn.cursor()
                query = " AND ".join(f'"{k}"' for k in keywords)
                try:
                    sql = "SELECT filename, full_path, size, mtime, is_dir FROM files JOIN fts ON files.id = fts.rowid WHERE fts MATCH ? LIMIT ?"
                    return list(c.execute(sql, (query, limit)))
                except:
                    sql = "SELECT filename, full_path, size, mtime, is_dir FROM files WHERE " + " AND ".join(["filename_lower LIKE ?"]*len(keywords)) + " LIMIT ?"
                    args = [f"%{k.lower()}%" for k in keywords] + [limit]
                    return list(c.execute(sql, args))
        except: return []

    def get_stats(self):
        if not self.conn: return {"count":0, "ready":False, "building":False}
        try:
            with self.lock:
                c = self.conn.cursor()
                count = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                return {"count": count, "ready": count>0, "building": False}
        except: return {"count":0, "ready":False, "building":False}

    def build_index(self, drives, progress_cb=None, stop_fn=None):
        if not self.conn: return
        try:
            with self.lock:
                c = self.conn.cursor()
                c.execute("DROP TRIGGER IF EXISTS files_ai"); c.execute("DROP TRIGGER IF EXISTS files_ad")
                c.execute("DROP TABLE IF EXISTS fts"); c.execute("DROP TABLE IF EXISTS files")
                c.execute("""CREATE TABLE files (id INTEGER PRIMARY KEY, filename TEXT, filename_lower TEXT, full_path TEXT, parent_dir TEXT, extension TEXT, size INT, mtime REAL, is_dir INT)""")
                
            all_drives = [d.upper().rstrip(':\\') for d in drives if os.path.exists(d)]
            all_data = []
            
            if IS_WINDOWS:
                def scan_one(drv):
                    try: return drv, enum_volume_files_mft(drv, SKIP_DIRS_LOWER, SKIP_EXTS)
                    except: return drv, []
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                    futures = [ex.submit(scan_one, d) for d in all_drives]
                    for f in concurrent.futures.as_completed(futures):
                        if stop_fn and stop_fn(): break
                        drv, data = f.result(); all_data.extend(data)
                        if progress_cb: progress_cb(len(all_data), f"MFT {drv}")

            if all_data:
                with self.lock:
                    c = self.conn.cursor(); c.execute("PRAGMA synchronous=OFF"); c.execute("PRAGMA journal_mode=MEMORY")
                batch = 50000; total = 0
                for i in range(0, len(all_data), batch):
                    if stop_fn and stop_fn(): break
                    b = all_data[i:i+batch]
                    try:
                        with self.lock: self.conn.cursor().executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", b)
                        total += len(b)
                        if progress_cb: progress_cb(total, f"Writing {total}/{len(all_data)}")
                    except: pass
            
            with self.lock:
                c = self.conn.cursor()
                c.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_parent ON files(parent_dir)")
                try:
                    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(filename, content=files, content_rowid=id)")
                    c.execute("INSERT INTO fts(fts) VALUES('rebuild')")
                    c.execute("CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN INSERT INTO fts(rowid, filename) VALUES (new.id, new.filename); END")
                    c.execute("CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN INSERT INTO fts(fts, rowid, filename) VALUES('delete', old.id, old.filename); END")
                except: pass
                c.execute("PRAGMA synchronous=NORMAL"); c.execute("PRAGMA journal_mode=WAL")
        except: pass

    def close(self):
        if self.conn: self.conn.close()

# ==================== [后端] FileWatcher (补全) ====================
if HAS_WATCHDOG:
    class _Handler(FileSystemEventHandler):
        def __init__(self, eq): self.eq = eq
        def on_created(self, e): 
            if not any(x in e.src_path.lower() for x in SKIP_EXTS) and not os.path.basename(e.src_path).startswith('.'): self.eq.put(('c', e.src_path))
        def on_deleted(self, e): self.eq.put(('d', e.src_path))
        def on_moved(self, e): self.eq.put(('m', e.src_path, e.dest_path))

class FileWatcher:
    def __init__(self, mgr, config_mgr=None):
        self.mgr = mgr; self.db_path = mgr.db_path; self.config_mgr = config_mgr; self.observer = None; self.running = False; self.eq = queue.Queue(); self.stop_flag = False
    def start(self, paths):
        if not HAS_WATCHDOG or self.running: return
        try:
            self.observer = Observer(); handler = _Handler(self.eq)
            for p in paths:
                if p.upper().startswith('C:'):
                    for cp in get_c_scan_dirs(self.config_mgr):
                        if os.path.exists(cp): self.observer.schedule(handler, cp, recursive=True)
                elif os.path.exists(p): self.observer.schedule(handler, p, recursive=True)
            self.observer.start(); self.running = True; self.stop_flag = False; threading.Thread(target=self._process, daemon=True).start()
        except: pass
    def _process(self):
        batch = []; last = time.time()
        while not self.stop_flag:
            try: batch.append(self.eq.get(timeout=2.0))
            except: pass
            if batch and (len(batch) >= 100 or time.time() - last >= 2.0):
                self._apply(batch); batch.clear(); last = time.time()
    def _apply(self, events):
        ins, dels = [], []
        for ev in events:
            if ev[0] == 'c':
                p = ev[1]
                try:
                    if os.path.isfile(p):
                        n = os.path.basename(p); st = os.stat(p)
                        ins.append((n, n.lower(), p, os.path.dirname(p), os.path.splitext(n)[1].lower(), st.st_size, st.st_mtime, 0))
                except: pass
            elif ev[0] == 'd': dels.append(ev[1])
            elif ev[0] == 'm': dels.append(ev[1]); p = ev[2] 
        if not ins and not dels: return
        try:
            with apsw.Connection(self.db_path) as conn:
                c = conn.cursor()
                if dels:
                    for d in dels: c.execute("DELETE FROM files WHERE full_path = ? OR full_path LIKE ?", (d, d + os.path.sep + '%'))
                if ins: c.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", ins)
        except: pass
    def stop(self):
        self.stop_flag = True
        if self.observer: self.observer.stop(); self.observer.join()
        self.running = False

# ==================== [UI] 毛玻璃特效 ====================
class WindowsEffect:
    def set_acrylic(self, hwnd):
        if not IS_WINDOWS: return
        try:
            from ctypes import windll, c_int, byref
            dwm = windll.dwmapi
            dwm.DwmSetWindowAttribute(hwnd, 20, byref(c_int(1)), 4)
            dwm.DwmSetWindowAttribute(hwnd, 38, byref(c_int(2)), 4)
        except: pass

# ==================== [UI] Models & Delegates ====================
class HighlightDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = QTextDocument() # 修复：直接使用 QTextDocument 而不是 QTextEdit().document()
        self.keywords = []
        self.doc.setDocumentMargin(2)
        
    def set_keywords(self, kws):
        self.keywords = [k.lower() for k in kws if k.strip()]

    def paint(self, painter, option, index):
        if index.column() != 0:
            super().paint(painter, option, index)
            return
        
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#0078d4"))
        
        text = index.data(Qt.DisplayRole)
        html = text
        for k in self.keywords:
            pattern = re.compile(re.escape(k), re.IGNORECASE)
            html = pattern.sub(lambda m: f"<span style='color:#ff4d4f; font-weight:bold;'>{m.group(0)}</span>", html)
        
        color = "#ffffff" if option.state & QStyle.State_Selected else "#e0e0e0"
        self.doc.setHtml(f"<div style='font-family:Segoe UI; font-size:10pt; color:{color};'>{html}</div>")
        
        ctx = QAbstractItemView.viewOptions(index.view()) if index.view() else option
        painter.translate(option.rect.left(), option.rect.top() + (option.rect.height() - self.doc.size().height())/2)
        self.doc.drawContents(painter)
        painter.restore()

class FileResultModel(QAbstractTableModel):
    def __init__(self, data=None):
        super().__init__(); self._data = data or []; self._headers = ["📄 文件名", "📂 所在目录", "📊 大小", "🕒 修改时间"]
    def update_data(self, new_data): self.beginResetModel(); self._data = new_data; self.endResetModel()
    def rowCount(self, p=None): return len(self._data)
    def columnCount(self, p=None): return 4
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        item = self._data[index.row()]
        if role == Qt.DisplayRole:
            if index.column() == 0: return item['filename']
            if index.column() == 1: return item['dir_path']
            if index.column() == 2: return item['size_str']
            if index.column() == 3: return item['mtime_str']
        elif role == Qt.ForegroundRole and index.column() == 0:
            if item['type_code'] == 0: return QBrush(QColor("#d48806"))
            if item['type_code'] == 1: return QBrush(QColor("#722ed1"))
        return None
    def headerData(self, sec, ori, role):
        if role == Qt.DisplayRole and ori == Qt.Horizontal: return self._headers[sec]
    def get_item(self, idx):
        if 0 <= idx < len(self._data): return self._data[idx]
        return None

class SearchWorker(QThread):
    results_ready = Signal(list); finished_signal = Signal(int, float)
    def __init__(self, mgr, keywords, scope, filters):
        super().__init__(); self.mgr = mgr; self.keywords = keywords; self.scope = scope; self.filters = filters; self.stopped = False
    def stop(self): self.stopped = True
    def run(self):
        t0 = time.time(); raw = self.mgr.search(self.keywords, self.scope); res = []
        sz_min = self.filters.get('size_min', 0); dt_min = self.filters.get('date_min', 0); regex = self.filters.get('regex', False)
        for fn, fp, sz, mt, is_dir in raw:
            if self.stopped: break
            if sz_min > 0 and not is_dir and sz < sz_min: continue
            if dt_min > 0 and mt < dt_min: continue
            if regex:
                try: 
                    if not re.search(self.keywords[0], fn, re.IGNORECASE): continue
                except: pass
            ext = os.path.splitext(fn)[1].lower()
            tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
            res.append({'filename': fn, 'fullpath': fp, 'dir_path': os.path.dirname(fp), 'size': sz, 'mtime': mt, 'type_code': tc, 'size_str': "📂" if is_dir else ("📦" if tc==1 else format_size(sz)), 'mtime_str': format_time(mt)})
        if not self.stopped: self.results_ready.emit(res); self.finished_signal.emit(len(res), time.time() - t0)

class IndexWorker(QThread):
    progress = Signal(int, str); finished = Signal()
    def __init__(self, mgr, drives): super().__init__(); self.mgr = mgr; self.drives = drives; self.stopped = False
    def stop(self): self.stopped = True
    def run(self): 
        self.mgr.build_index(self.drives, progress_cb=lambda c, m: self.progress.emit(c, m), stop_fn=lambda: self.stopped)
        self.finished.emit()

class HotkeyWorker(QThread):
    activated = Signal()
    def run(self):
        if not HAS_WIN32: return
        import ctypes; from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.RegisterHotKey(None, 1, 0x0002 | 0x0004, 0x20) # Ctrl+Shift+Space
        msg = wintypes.MSG()
        while True:
            if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312 and msg.wParam == 1: self.activated.emit()
                user32.TranslateMessage(ctypes.byref(msg)); user32.DispatchMessageW(ctypes.byref(msg))

# ==================== [UI] Dialogs ====================
class MiniSearchWindow(QDialog):
    def __init__(self, parent_app):
        super().__init__(); self.app_ref = parent_app; self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool); self.setAttribute(Qt.WA_TranslucentBackground); self.resize(700, 70); self.setup_ui(); self.we = WindowsEffect()
    def setup_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.frame = QFrame(); self.frame.setStyleSheet("QFrame { background-color: rgba(30,30,30, 240); border: 1px solid #444; border-radius: 12px; } QLineEdit { background: transparent; border: none; color: white; font-size: 22px; font-family: 'Segoe UI'; }")
        fl = QHBoxLayout(self.frame)
        lbl_icon = QLabel("🔍"); lbl_icon.setStyleSheet("font-size: 24px; border:none; background:transparent; color:#ccc;")
        self.inp = QLineEdit(); self.inp.setPlaceholderText("极速搜索..."); self.inp.returnPressed.connect(self.on_enter); self.inp.textChanged.connect(self.on_type)
        btn_close = QPushButton("✕"); btn_close.setFixedSize(30,30); btn_close.setStyleSheet("QPushButton { border: none; color: #999; font-size: 16px; background: transparent; } QPushButton:hover { color: red; }"); btn_close.clicked.connect(self.hide)
        fl.addWidget(lbl_icon); fl.addWidget(self.inp); fl.addWidget(btn_close); layout.addWidget(self.frame)
        self.anim = QPropertyAnimation(self, b"windowOpacity"); self.anim.setDuration(200)
    def show_active(self):
        self.show(); self.activateWindow(); self.inp.setFocus(); self.inp.selectAll()
        geo = QApplication.primaryScreen().geometry(); self.move((geo.width()-self.width())//2, geo.height()//4)
        self.anim.setStartValue(0.0); self.anim.setEndValue(1.0); self.anim.start()
        self.we.set_acrylic(int(self.winId()))
    def on_type(self, txt): self.app_ref.debounce_mini(txt)
    def on_enter(self): self.hide(); self.app_ref.show_normal_and_search(self.inp.text())
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape: self.hide()

class BatchRenameDialog(QDialog):
    def __init__(self, parent, items):
        super().__init__(parent); self.setWindowTitle("✏ 批量重命名"); self.resize(800, 600); self.items = items; self.setup_ui(); self.update_preview()
    def setup_ui(self):
        layout = QVBoxLayout(self)
        grp = QGroupBox("重命名规则"); form = QFormLayout(grp)
        self.rb_prefix = QRadioButton("前缀 + 序号"); self.rb_replace = QRadioButton("替换文本"); self.rb_prefix.setChecked(True)
        self.bg = QButtonGroup(); self.bg.addButton(self.rb_prefix); self.bg.addButton(self.rb_replace); self.bg.buttonClicked.connect(self.update_preview)
        hbox_mode = QHBoxLayout(); hbox_mode.addWidget(self.rb_prefix); hbox_mode.addWidget(self.rb_replace); form.addRow("模式:", hbox_mode)
        self.inp_prefix = QLineEdit(); self.inp_prefix.textChanged.connect(self.update_preview)
        self.inp_start = QSpinBox(); self.inp_start.setValue(1); self.inp_start.valueChanged.connect(self.update_preview)
        self.inp_width = QSpinBox(); self.inp_width.setValue(3); self.inp_width.valueChanged.connect(self.update_preview)
        hbox_pref = QHBoxLayout(); hbox_pref.addWidget(QLabel("前缀:")); hbox_pref.addWidget(self.inp_prefix); hbox_pref.addWidget(QLabel("起始:")); hbox_pref.addWidget(self.inp_start); hbox_pref.addWidget(QLabel("位数:")); hbox_pref.addWidget(self.inp_width); form.addRow("序列设置:", hbox_pref)
        self.inp_find = QLineEdit(); self.inp_find.textChanged.connect(self.update_preview)
        self.inp_replace = QLineEdit(); self.inp_replace.textChanged.connect(self.update_preview)
        hbox_rep = QHBoxLayout(); hbox_rep.addWidget(QLabel("查找:")); hbox_rep.addWidget(self.inp_find); hbox_rep.addWidget(QLabel("替换:")); hbox_rep.addWidget(self.inp_replace); form.addRow("文本替换:", hbox_rep)
        layout.addWidget(grp)
        self.txt_preview = QTextEdit(); self.txt_preview.setReadOnly(True); layout.addWidget(QLabel("预览:")); layout.addWidget(self.txt_preview)
        btn_box = QHBoxLayout(); btn_ok = QPushButton("执行重命名"); btn_ok.clicked.connect(self.do_rename); btn_cancel = QPushButton("关闭"); btn_cancel.clicked.connect(self.close)
        btn_box.addStretch(); btn_box.addWidget(btn_ok); btn_box.addWidget(btn_cancel); layout.addLayout(btn_box)
    def update_preview(self):
        self.txt_preview.clear(); self.preview_pairs = []; is_prefix = self.rb_prefix.isChecked(); prefix = self.inp_prefix.text(); num = self.inp_start.value(); width = self.inp_width.value(); find_txt = self.inp_find.text(); rep_txt = self.inp_replace.text(); txt = ""
        for item in self.items:
            old_path = item['fullpath']; old_name = item['filename']; name_base, ext = os.path.splitext(old_name)
            if is_prefix: new_name = f"{prefix}{str(num).zfill(width)}{ext}"; num += 1
            else: new_name = old_name.replace(find_txt, rep_txt) if find_txt else old_name
            new_path = os.path.join(os.path.dirname(old_path), new_name); self.preview_pairs.append((old_path, new_path)); txt += f"{old_name} -> {new_name}\n"
        self.txt_preview.setText(txt)
    def do_rename(self):
        if QMessageBox.question(self, "确认", "确定执行重命名？") != QMessageBox.Yes: return
        count = 0
        for old, new in self.preview_pairs:
            if old != new:
                try: os.rename(old, new); count += 1
                except: pass
        QMessageBox.information(self, "完成", f"成功重命名 {count} 个文件"); self.accept()

class CDriveDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent); self.setWindowTitle("C盘目录设置"); self.resize(600, 400); self.config = config; self.setup_ui()
    def setup_ui(self):
        layout = QVBoxLayout(self); self.list_widget = QListWidget(); self.refresh_list()
        btn_add = QPushButton("添加目录"); btn_add.clicked.connect(self.add_dir)
        btn_save = QPushButton("保存并重建"); btn_save.clicked.connect(self.save)
        layout.addWidget(QLabel("勾选需要扫描的目录:")); layout.addWidget(self.list_widget); layout.addWidget(btn_add); layout.addWidget(btn_save)
    def refresh_list(self):
        self.list_widget.clear()
        for item in self.config.get_c_scan_paths():
            w = QListWidgetItem(item['path']); w.setFlags(w.flags() | Qt.ItemIsUserCheckable); w.setCheckState(Qt.Checked if item['enabled'] else Qt.Unchecked); self.list_widget.addItem(w)
    def add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择目录", "C:\\")
        if d and d.upper().startswith("C:"):
            paths = self.config.get_c_scan_paths(); paths.append({'path': os.path.normpath(d), 'enabled': True}); self.config.set_c_scan_paths(paths); self.refresh_list()
    def save(self):
        paths = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i); paths.append({'path': item.text(), 'enabled': item.checkState() == Qt.Checked})
        self.config.set_c_scan_paths(paths); self.accept()

class SettingsDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent); self.setWindowTitle("⚙️ 设置"); self.resize(300, 200); self.config = config
        layout = QVBoxLayout(self)
        self.chk_hotkey = QCheckBox("启用全局热键 (Ctrl+Shift+Space)"); self.chk_hotkey.setChecked(self.config.get_hotkey_enabled())
        self.chk_tray = QCheckBox("关闭时最小化到托盘"); self.chk_tray.setChecked(self.config.get_tray_enabled())
        btn_save = QPushButton("保存"); btn_save.clicked.connect(self.save)
        layout.addWidget(QLabel("常规设置:")); layout.addWidget(self.chk_hotkey); layout.addWidget(self.chk_tray); layout.addStretch(); layout.addWidget(btn_save)
    def save(self):
        self.config.set_hotkey_enabled(self.chk_hotkey.isChecked()); self.config.set_tray_enabled(self.chk_tray.isChecked())
        QMessageBox.information(self, "提示", "设置已保存"); self.accept()

# ==================== [UI] MainWindow ====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = ConfigManager(); self.index_mgr = IndexManager(self.config); self.file_watcher = FileWatcher(self.index_mgr, self.config)
        self.setWindowTitle("🚀 极速文件搜索 V42 (Qt终极版)"); self.resize(1200, 800)
        self.all_results = []; self.model = FileResultModel(); self.worker = None; self.idx_worker = None; self.mini_window = MiniSearchWindow(self)
        
        self.setup_style()
        self.setup_ui()
        self.setup_tray()
        self.setup_hotkey()
        self.refresh_favorites_menu()
        
        self.debounce = QTimer(); self.debounce.setInterval(300); self.debounce.setSingleShot(True); self.debounce.timeout.connect(self.do_search)
        self.timer = QTimer(); self.timer.timeout.connect(self.check_status); self.timer.start(1000)
        self.file_watcher.start(self._get_drives())
        WindowsEffect().set_acrylic(int(self.winId()))

    def setup_style(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QWidget { color: #e0e0e0; font-family: 'Segoe UI'; font-size: 14px; }
            QLineEdit { background: #2d2d2d; border: 1px solid #3e3e3e; border-radius: 6px; padding: 8px; font-size: 16px; selection-background-color: #0078d4; }
            QLineEdit:focus { border: 1px solid #0078d4; }
            QComboBox { background: #2d2d2d; border: 1px solid #3e3e3e; border-radius: 6px; padding: 5px; }
            QTableView { background: #1e1e1e; alternate-background-color: #252525; border: none; gridline-color: transparent; }
            QTableView::item:selected { background-color: #0078d4; color: white; border-radius: 4px; }
            QHeaderView::section { background: #1e1e1e; color: #888; border: none; padding: 5px; font-weight: bold; }
            QPushButton { background: #0078d4; color: white; border-radius: 6px; padding: 6px 15px; font-weight: bold; }
            QPushButton:hover { background: #1084e0; }
            QPushButton#flat { background: transparent; border: 1px solid #3e3e3e; }
            QPushButton#flat:hover { background: #3e3e3e; }
        """)

    def setup_ui(self):
        cw = QWidget(); self.setCentralWidget(cw); main_layout = QVBoxLayout(cw); main_layout.setContentsMargins(15,15,15,15); main_layout.setSpacing(12)
        
        top = QHBoxLayout()
        self.combo_scope = QComboBox(); self.combo_scope.addItems(["全部磁盘"] + self._get_drives()); self.combo_scope.setFixedWidth(120); self.combo_scope.currentIndexChanged.connect(lambda: self.debounce.start())
        self.inp = QLineEdit(); self.inp.setPlaceholderText("输入文件名... (无需回车)"); self.inp.textChanged.connect(lambda: self.debounce.start())
        self.inp.setContextMenuPolicy(Qt.CustomContextMenu); self.inp.customContextMenuRequested.connect(self.show_search_menu)
        
        btn_menu = QPushButton("☰"); btn_menu.setObjectName("flat"); btn_menu.setFixedWidth(40)
        self.main_menu = QMenu(self) 
        self.setup_main_menu(self.main_menu)
        btn_menu.setMenu(self.main_menu)
        
        top.addWidget(self.combo_scope); top.addWidget(self.inp); top.addWidget(btn_menu)
        
        filt = QHBoxLayout()
        self.combo_size = QComboBox(); self.combo_size.addItems(["大小不限", ">1MB", ">10MB", ">100MB", ">1GB"]); self.combo_size.currentIndexChanged.connect(lambda: self.debounce.start())
        self.combo_date = QComboBox(); self.combo_date.addItems(["时间不限", "今天", "最近3天", "最近7天", "最近30天"]); self.combo_date.currentIndexChanged.connect(lambda: self.debounce.start())
        self.combo_ext = QComboBox(); self.combo_ext.addItem("类型不限"); self.combo_ext.currentIndexChanged.connect(self.apply_ext_filter)
        self.chk_fuzzy = QCheckBox("模糊"); self.chk_fuzzy.setChecked(True); self.chk_regex = QCheckBox("正则")
        filt.addWidget(QLabel("筛选:")); filt.addWidget(self.combo_size); filt.addWidget(self.combo_date); filt.addWidget(self.combo_ext); filt.addWidget(self.chk_fuzzy); filt.addWidget(self.chk_regex); filt.addStretch()
        
        self.table = QTableView(); self.table.setModel(self.model); self.table.setAlternatingRowColors(True); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.verticalHeader().setVisible(False); self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu); self.table.customContextMenuRequested.connect(self.show_result_menu); self.table.doubleClicked.connect(self.open_current)
        self.delegate = HighlightDelegate(self.table); self.table.setItemDelegate(self.delegate)
        
        self.bar_btm = QLabel("就绪"); self.bar_btm.setStyleSheet("color: #666; font-size: 12px;")
        main_layout.addLayout(top); main_layout.addLayout(filt); main_layout.addWidget(self.table); main_layout.addWidget(self.bar_btm)

    def setup_main_menu(self, menu):
        m_file = menu.addMenu("文件(&F)"); m_file.addAction("📤 导出结果", self.export_results); m_file.addSeparator(); m_file.addAction("退出", self.close)
        m_tool = menu.addMenu("工具(&T)"); m_tool.addAction("📊 扫描大文件 (>100MB)", self.tool_large); m_tool.addAction("👯 查找重复文件", self.tool_dupe); m_tool.addAction("📁 查找空文件夹", self.tool_empty); m_tool.addSeparator(); m_tool.addAction("📋 复制文件对象", self.copy_file_object)
        self.fav_menu = menu.addMenu("收藏(&B)"); self.fav_menu.aboutToShow.connect(self.refresh_favorites_menu)
        menu.addSeparator()
        menu.addAction("⚙️ 设置", lambda: SettingsDialog(self, self.config).exec())

    def _get_drives(self): return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")] if IS_WINDOWS else ["/"]
    def check_status(self):
        s = self.index_mgr.get_stats()
        if s['building']: self.bar_btm.setText(f"🔄 构建中 ({s['count']})")
        elif s['ready']: self.bar_btm.setText(f"✅ 就绪 ({s['count']})")
        else: self.bar_btm.setText("❌ 未构建 (请在工具菜单中重建索引)")
    
    def debounce_mini(self, txt): self.inp.setText(txt); self.debounce.start()
    def show_normal_and_search(self, t): self.showNormal(); self.activateWindow(); self.inp.setText(t); self.debounce.start()

    def do_search(self):
        kw = self.inp.text().strip()
        if not kw: self.model.update_data([]); return
        self.config.add_history(kw)
        self.delegate.set_keywords(kw.split()); self.table.viewport().update()
        
        filters = {'regex': self.chk_regex.isChecked()}
        sz_txt = self.combo_size.currentText(); dt_txt = self.combo_date.currentText(); now = time.time()
        if ">1MB" in sz_txt: filters['size_min'] = 1024**2
        elif ">10MB" in sz_txt: filters['size_min'] = 10*1024**2
        elif ">100MB" in sz_txt: filters['size_min'] = 100*1024**2
        elif ">1GB" in sz_txt: filters['size_min'] = 1024**3
        if "今天" in dt_txt: filters['date_min'] = now - 86400
        elif "3天" in dt_txt: filters['date_min'] = now - 3*86400
        elif "7天" in dt_txt: filters['date_min'] = now - 7*86400
        elif "30天" in dt_txt: filters['date_min'] = now - 30*86400
        
        scope = self.combo_scope.currentText()
        scope = parse_search_scope(scope, self._get_drives, self.config)
        
        if self.worker: self.worker.stop()
        self.worker = SearchWorker(self.index_mgr, kw.split(), scope, filters)
        self.worker.results_ready.connect(self.on_results)
        self.worker.start()
        self.bar_btm.setText("🔍 搜索中...")

    def on_results(self, data):
        self.all_results = data; self.model.update_data(data)
        exts = {}; 
        for x in data: k = "文件夹" if x['type_code']==0 else (os.path.splitext(x['filename'])[1].lower() or "无后缀"); exts[k] = exts.get(k, 0) + 1
        self.combo_ext.blockSignals(True); self.combo_ext.clear(); self.combo_ext.addItem("类型不限")
        for e, c in sorted(exts.items(), key=lambda x: -x[1])[:30]: self.combo_ext.addItem(f"{e} ({c})")
        self.combo_ext.blockSignals(False)
        self.bar_btm.setText(f"✅ 找到 {len(data)} 项")
    
    def apply_ext_filter(self):
        txt = self.combo_ext.currentText().split(" (")[0]
        if txt == "类型不限": self.model.update_data(self.all_results)
        else: self.model.update_data([x for x in self.all_results if (txt == "文件夹" and x['type_code']==0) or (txt != "文件夹" and os.path.splitext(x['filename'])[1].lower() == txt)])

    def rebuild_index(self):
        if self.index_mgr.is_building: return
        self.file_watcher.stop(); self.idx_worker = IndexWorker(self.index_mgr, self._get_drives())
        self.idx_worker.progress.connect(lambda c, m: self.bar_btm.setText(m))
        self.idx_worker.finished.connect(lambda: (self.file_watcher.start(self._get_drives()), QMessageBox.information(self, "完成", "索引构建完成")))
        self.idx_worker.start()

    def tool_large(self): self.model.update_data(sorted([x for x in self.all_results if x['size'] > 100*1024*1024 and x['type_code']!=0], key=lambda x: -x['size'])); self.bar_btm.setText(f"📊 找到大文件")
    def tool_dupe(self):
        from collections import defaultdict; groups = defaultdict(list)
        for x in self.all_results: 
            if x['type_code']!=0: groups[(x['size'], x['filename'])].append(x)
        self.model.update_data([x for v in groups.values() if len(v)>1 for x in v]); self.bar_btm.setText(f"👯 找到重复文件")
    def tool_empty(self):
        emp = []
        for x in self.all_results:
            if x['type_code']==0:
                try: 
                    if not os.listdir(x['fullpath']): emp.append(x)
                except: pass
        self.model.update_data(emp); self.bar_btm.setText(f"📁 找到空文件夹")
    def copy_file_object(self):
        if not HAS_WIN32: return
        idxs = self.table.selectionModel().selectedRows(); files = [self.model.get_item(i.row())['fullpath'] for i in idxs]
        if not files: return
        try:
            buf = b'\0' * 20 + ('\0'.join(files) + '\0\0').encode('utf-16le'); struct.pack_into('IIIII', buf, 0, 20, 0, 0, 0, 1)
            win32clipboard.OpenClipboard(); win32clipboard.EmptyClipboard(); win32clipboard.SetClipboardData(win32con.CF_HDROP, buf); win32clipboard.CloseClipboard()
            self.bar_btm.setText("✅ 已复制文件到剪贴板")
        except: pass
    def export_results(self):
        if not self.all_results: return QMessageBox.information(self, "提示", "没有结果可导出")
        path, _ = QFileDialog.getSaveFileName(self, "导出", "", "CSV Files (*.csv);;Excel Files (*.xlsx)")
        if not path: return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write("Filename,Path,Size,Modified\n")
                for r in self.all_results: f.write(f"{r['filename']},{r['fullpath']},{r['size']},{r['mtime']}\n")
            QMessageBox.information(self, "完成", f"已导出到 {path}")
        except Exception as e: QMessageBox.critical(self, "错误", str(e))
    def show_cdrive(self): CDriveDialog(self, self.config).exec()
    def show_search_menu(self, pos):
        m = self.inp.createStandardContextMenu(); m.addSeparator(); hm = m.addMenu("📜 搜索历史")
        for h in self.config.get_history(): hm.addAction(h, lambda t=h: (self.inp.setText(t), self.debounce.start()))
        hm.addSeparator(); hm.addAction("清空历史", self.config.clear_history); m.exec(self.inp.mapToGlobal(pos))
    def show_result_menu(self, pos):
        idx = self.table.indexAt(pos); 
        if not idx.isValid(): return
        item = self.model.get_item(idx.row()); m = QMenu()
        m.addAction("📂 打开", lambda: os.startfile(item['fullpath']))
        m.addAction("🎯 定位", lambda: subprocess.Popen(f'explorer /select,"{item["fullpath"]}"'))
        m.addSeparator(); m.addAction("📄 复制路径", lambda: QApplication.clipboard().setText(item['fullpath']))
        m.addAction("✏ 批量重命名", self.show_rename)
        if HAS_SEND2TRASH: m.addAction("🗑️ 删除", lambda: self.del_file(item['fullpath']))
        m.exec(self.table.viewport().mapToGlobal(pos))
    def show_rename(self):
        idxs = self.table.selectionModel().selectedRows(); items = [self.model.get_item(i.row()) for i in idxs]
        if not items: items = self.all_results
        BatchRenameDialog(self, items).exec()
    def del_file(self, p):
        if QMessageBox.question(self, "确认", f"删除到回收站?\n{p}") == QMessageBox.Yes:
            try: send2trash.send2trash(p)
            except: pass
    def open_current(self, idx): os.startfile(self.model.get_item(idx.row())['fullpath'])
    def refresh_favorites_menu(self):
        self.fav_menu.clear(); self.fav_menu.addAction("⭐ 收藏当前目录", lambda: self.config.add_favorite(QFileDialog.getExistingDirectory(self))); self.fav_menu.addSeparator()
        for f in self.config.get_favorites(): self.fav_menu.addAction(f"📁 {f['name']}", lambda p=f['path']: self.combo_scope.setCurrentText(p))
    def setup_tray(self):
        if HAS_TRAY: 
            self.tray = QSystemTrayIcon(self); pix = QPixmap(64,64); pix.fill(Qt.transparent); p = QPainter(pix); p.setBrush(QColor("#4CAF50")); p.drawEllipse(8,8,48,48); p.end(); self.tray.setIcon(QIcon(pix))
            m = QMenu(); m.addAction("显示", self.show_norm); m.addAction("退出", QApplication.instance().quit); self.tray.setContextMenu(m); self.tray.show()
    def setup_hotkey(self):
        if HAS_WIN32: self.hk = HotkeyWorker(); self.hk.activated.connect(self.mini_window.show_active); self.hk.start()
    def show_norm(self): self.showNormal(); self.activateWindow()
    def closeEvent(self, e):
        if self.config.get_tray_enabled() and HAS_TRAY: self.hide(); self.tray.showMessage("最小化", "仍在后台运行"); e.ignore()
        else: self.index_mgr.close(); e.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyle("Fusion")
    w = MainWindow(); w.show()
    sys.exit(app.exec())