import os
import string
import platform
import threading
import time
import datetime
import tkinter as tk
from tkinter import messagebox, filedialog
import struct
import subprocess
import queue
import concurrent.futures
from collections import deque
import re
import sqlite3
from pathlib import Path
import shutil
import math
import json
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

# ==================== 依赖检查 ====================
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    print("警告: watchdog 未安装，文件监控功能不可用")

try:
    import win32clipboard
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    print("警告: pywin32 未安装，复制文件功能不可用")

# ==================== C盘扫描目录 ====================
C_DRIVE_DIRS = [
    os.path.expandvars(r"%TEMP%"),
    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
    os.path.expandvars(r"%USERPROFILE%\Desktop"),
]

def get_c_scan_dirs():
    return [p for p in C_DRIVE_DIRS if os.path.exists(p)]

# ==================== 过滤规则 ====================
CAD_PATTERN = re.compile(r'cad20(1[0-9]|2[0-4])', re.IGNORECASE)
AUTOCAD_PATTERN = re.compile(r'autocad_20(1[0-9]|2[0-5])', re.IGNORECASE)

SKIP_DIRS_LOWER = {
    'windows', 'program files', 'program files (x86)', 'programdata',
    '$recycle.bin', 'system volume information', 'appdata',
    'boot', 'node_modules', '.git', '__pycache__', 'site-packages', 'sys',
    'recovery', 'config.msi', '$windows.~bt', '$windows.~ws',
    'cache', 'caches', 'temp', 'tmp', 'logs', 'log',
    '.vscode', '.idea', '.vs', 'obj', 'bin', 'debug', 'release',
    'packages', '.nuget', 'bower_components',
}

SKIP_EXTS = {
    '.lsp', '.fas', '.lnk', '.html', '.htm',
    '.xml', '.ini', '.lsp_bak', '.cuix', '.arx', '.crx',
    '.fx', '.dbx', '.kid', '.ico', '.rz', '.dll',
    '.sys', '.tmp', '.log', '.dat', '.db', '.pdb',
    '.obj', '.pyc', '.class', '.cache', '.lock',
}

ARCHIVE_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.jar', '.cab', '.bz2', '.xz'}

def should_skip_path(path_lower):
    if 'site-packages' in path_lower:
        return True
    if CAD_PATTERN.search(path_lower):
        return True
    if AUTOCAD_PATTERN.search(path_lower):
        return True
    if 'tangent' in path_lower:
        return True
    return False

def should_skip_dir(name_lower, is_c_drive=False):
    if name_lower in SKIP_DIRS_LOWER:
        return True
    if not is_c_drive and name_lower == 'users':
        return True
    if CAD_PATTERN.search(name_lower):
        return True
    if AUTOCAD_PATTERN.search(name_lower):
        return True
    if 'tangent' in name_lower:
        return True
    return False

def format_size(size):
    if size <= 0:
        return "-"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == 'B' else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def format_time(timestamp):
    if timestamp <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
    except:
        return "-"

# ==================== 配置管理 ====================
class ConfigManager:
    def __init__(self):
        self.config_dir = Path.home() / ".filesearch"
        self.config_dir.mkdir(exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        self.config = self._load()
    
    def _load(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except:
            pass
        return {"search_history": []}
    
    def save(self):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except:
            pass
    
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

# ==================== MFT/USN 模块 ====================
IS_WINDOWS = platform.system() == "Windows"
MFT_AVAILABLE = False

if IS_WINDOWS:
    import ctypes
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

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    CreateFileW.restype = wintypes.HANDLE
    DeviceIoControl = kernel32.DeviceIoControl
    DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    DeviceIoControl.restype = wintypes.BOOL
    CloseHandle = kernel32.CloseHandle
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts):
        global MFT_AVAILABLE
        drive = drive_letter.rstrip(':').upper()
        root_path = f"{drive}:\\"
        
        volume_path = f"\\\\.\\{drive}:"
        h = CreateFileW(volume_path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if h == INVALID_HANDLE_VALUE:
            raise OSError(f"打开卷失败: {ctypes.get_last_error()}")
        
        try:
            jd = USN_JOURNAL_DATA_V0()
            br = wintypes.DWORD()
            if not DeviceIoControl(h, FSCTL_QUERY_USN_JOURNAL, None, 0, ctypes.byref(jd), ctypes.sizeof(jd), ctypes.byref(br), None):
                raise OSError(f"查询USN失败: {ctypes.get_last_error()}")
            
            MFT_AVAILABLE = True
            records = {}
            BUFFER_SIZE = 1024 * 1024
            buf = (ctypes.c_ubyte * BUFFER_SIZE)()
            
            class MFT_ENUM_DATA(ctypes.Structure):
                _pack_ = 8
                _fields_ = [("StartFileReferenceNumber", ctypes.c_uint64), ("LowUsn", ctypes.c_int64), ("HighUsn", ctypes.c_int64)]
            
            med = MFT_ENUM_DATA()
            med.StartFileReferenceNumber = 0
            med.LowUsn = 0
            med.HighUsn = jd.NextUsn
            
            total = 0
            start_time = time.time()
            
            while True:
                ctypes.set_last_error(0)
                ok = DeviceIoControl(h, FSCTL_ENUM_USN_DATA, ctypes.byref(med), ctypes.sizeof(med), ctypes.byref(buf), BUFFER_SIZE, ctypes.byref(br), None)
                err = ctypes.get_last_error()
                returned = br.value
                
                if not ok:
                    if err == 38:
                        break
                    if err != 0:
                        raise OSError(f"枚举失败: {err}")
                    if returned <= 8:
                        break
                if returned <= 8:
                    break
                
                next_frn = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(ctypes.c_uint64))[0]
                offset = 8
                batch_count = 0
                
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
                            filename = bytes(buf[offset + name_off:offset + name_off + name_len]).decode('utf-16le', errors='replace')
                            if filename and filename[0] not in ('$', '.'):
                                file_ref = rec.FileReferenceNumber & 0x0000FFFFFFFFFFFF
                                parent_ref = rec.ParentFileReferenceNumber & 0x0000FFFFFFFFFFFF
                                is_dir = bool(rec.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
                                records[file_ref] = (filename, parent_ref, is_dir)
                                batch_count += 1
                    offset += rec_len
                
                total += batch_count
                if total and total % 100000 < batch_count:
                    print(f"  [MFT] {drive}: 已枚举 {total:,} 条, 用时 {time.time()-start_time:.1f}s")
                
                med.StartFileReferenceNumber = next_frn
                if batch_count == 0:
                    break
            
            print(f"  [MFT] {drive}: 枚举完成 {len(records):,} 条")
            
            root_ref = 5
            path_cache = {root_ref: root_path}
            
            def get_path(ref, depth=0):
                if depth > 256 or ref not in records:
                    return None
                if ref in path_cache:
                    return path_cache[ref]
                name, parent, _ = records[ref]
                parent_path = get_path(parent, depth + 1) if parent != root_ref else root_path
                if not parent_path:
                    return None
                full = parent_path.rstrip("\\") + "\\" + name
                path_cache[ref] = full
                return full
            
            for ref in records:
                get_path(ref)
            
            result = []
            for ref, (name, parent, is_dir) in records.items():
                full_path = path_cache.get(ref)
                if not full_path or full_path == root_path:
                    continue
                
                name_lower = name.lower()
                path_lower = full_path.lower()
                
                skip = False
                for sd in skip_dirs:
                    if f"\\{sd}\\" in path_lower or path_lower.endswith(f"\\{sd}"):
                        skip = True
                        break
                if skip:
                    continue
                
                if is_dir:
                    if name_lower in skip_dirs:
                        continue
                else:
                    ext = name_lower[name_lower.rfind('.'):] if '.' in name_lower else ''
                    if ext in skip_exts:
                        continue
                
                sep_pos = full_path.rfind("\\")
                parent_dir = full_path[:sep_pos] if sep_pos > 0 else root_path
                ext = '' if is_dir else (name_lower[name_lower.rfind('.'):] if '.' in name_lower else '')
                result.append([name, name_lower, full_path, parent_dir, ext, 0, 0, 1 if is_dir else 0])
            
            # 获取文件大小和时间
            for item in result:
                if item[7] == 0:
                    try:
                        st = os.stat(item[2])
                        item[5] = st.st_size
                        item[6] = st.st_mtime
                    except:
                        pass
            
            print(f"  [MFT] {drive}: 过滤后 {len(result):,} 条")
            return [tuple(item) for item in result]
        finally:
            CloseHandle(h)
else:
    def enum_volume_files_mft(drive_letter, skip_dirs, skip_exts):
        raise OSError("MFT仅Windows可用")

# ==================== 索引管理器 ====================
class IndexManager:
    def __init__(self, db_path=None):
        if db_path is None:
            idx_dir = Path.home() / ".filesearch"
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
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=60)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA cache_size=100000")
            self.conn.execute("PRAGMA temp_store=MEMORY")
            self.conn.execute("PRAGMA mmap_size=268435456")
            
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY, filename TEXT NOT NULL, filename_lower TEXT NOT NULL,
                    full_path TEXT UNIQUE NOT NULL, parent_dir TEXT NOT NULL, extension TEXT,
                    size INTEGER DEFAULT 0, mtime REAL DEFAULT 0, is_dir INTEGER DEFAULT 0
                )
            """)
            
            try:
                self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(filename, content=files, content_rowid=id)")
                self.conn.execute("CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN INSERT INTO files_fts(rowid, filename) VALUES (new.id, new.filename); END")
                self.conn.execute("CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN INSERT INTO files_fts(files_fts, rowid, filename) VALUES('delete', old.id, old.filename); END")
                self.has_fts = True
                print("✅ FTS5 已启用")
            except:
                self.has_fts = False
            
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            self.conn.commit()
            self._load_stats()
        except Exception as e:
            print(f"❌ 数据库错误: {e}")
            self.conn = None

    def _load_stats(self, preserve_mft=False):
        if not self.conn:
            return
        try:
            self.file_count = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            row = self.conn.execute("SELECT value FROM meta WHERE key='build_time'").fetchone()
            self.last_build_time = float(row[0]) if row else None
            if not preserve_mft:
                 row2 = self.conn.execute("SELECT value FROM meta WHERE key='used_mft'").fetchone()
                 self.used_mft = (row2 is not None and row2[0] == '1')
            self.is_ready = self.file_count > 0
        except:
            pass

    def reload_stats(self):
        if not self.is_building:
            with self.lock:
                self._load_stats(preserve_mft=True)

    def change_db_path(self, new_path):
        if not new_path:
            return False, "路径不能为空"
        new_path = os.path.abspath(new_path)
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
        except:
            pass
        self.close()
        if os.path.exists(self.db_path):
            for ext in ['', '-wal', '-shm']:
                src, dst = self.db_path + ext, new_path + ext
                if os.path.exists(src):
                    try:
                        shutil.copy2(src, dst)
                    except:
                        pass
        self.db_path = new_path
        self.conn = None
        self._init_db()
        return True, "已更改"

    def search(self, keywords, scope=None, limit=50000):
        if not self.conn or not self.is_ready:
            return None
        try:
            with self.lock:
                if self.has_fts and keywords:
                    fts_query = ' AND '.join(f'"{kw}"' for kw in keywords)
                    sql = "SELECT f.filename, f.full_path, f.size, f.mtime, f.is_dir FROM files f INNER JOIN files_fts fts ON f.id = fts.rowid WHERE fts MATCH ?"
                    params = [fts_query]
                    if scope and "所有磁盘" not in scope:
                        sql += " AND f.full_path LIKE ?"
                        params.append(f"{scope}%")
                    sql += f" LIMIT {limit}"
                    try:
                        return self.conn.execute(sql, params).fetchall()
                    except:
                        pass
                
                wheres, params = [], []
                for kw in keywords:
                    wheres.append("filename_lower LIKE ?")
                    params.append(f"%{kw}%")
                if scope and "所有磁盘" not in scope:
                    wheres.append("full_path LIKE ?")
                    params.append(f"{scope}%")
                sql = f"SELECT filename,full_path,size,mtime,is_dir FROM files WHERE {' AND '.join(wheres)} LIMIT ?"
                params.append(limit)
                return self.conn.execute(sql, params).fetchall()
        except Exception as e:
            print(f"搜索错误: {e}")
            return None

    def build_index(self, drives, progress_cb=None, stop_fn=None):
        global MFT_AVAILABLE
        if not self.conn:
            return
        self.is_building = True
        self.is_ready = False
        self.used_mft = False
        MFT_AVAILABLE = False
        build_start = time.time()
        
        try:
            with self.lock:
                self.conn.execute("PRAGMA synchronous=OFF")
                self.conn.execute("DELETE FROM files")
                if self.has_fts:
                    try:
                        self.conn.execute("DELETE FROM files_fts")
                    except:
                        pass
                self.conn.commit()
            self.file_count = 0

            c_targets, mft_drives = [], []
            for d in drives:
                up = d.upper()
                if up.startswith('C:'):
                    c_targets.extend(get_c_scan_dirs())
                else:
                    mft_drives.append(up.rstrip('\\').rstrip(':'))

            print(f"🔧 构建索引: C盘 {len(c_targets)} 个目录, MFT扫描 {mft_drives}")

            # MFT扫描
            if mft_drives and IS_WINDOWS:
                all_data = []
                lock = threading.Lock()
                mft_ok = False
                
                def scan_one(drv):
                    nonlocal mft_ok
                    try:
                        print(f"[MFT] 开始扫描 {drv}: ...")
                        data = enum_volume_files_mft(drv, SKIP_DIRS_LOWER, SKIP_EXTS)
                        with lock:
                            all_data.extend(data)
                            mft_ok = True
                        return len(data)
                    except Exception as e:
                        print(f"[MFT] {drv}: 失败 - {e}")
                        return -1
                
                failed = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(mft_drives)) as ex:
                    futures = {ex.submit(scan_one, d): d for d in mft_drives}
                    for f in concurrent.futures.as_completed(futures):
                        if stop_fn and stop_fn():
                            break
                        if f.result() < 0:
                            failed.append(futures[f])
                        if progress_cb:
                            progress_cb(len(all_data), f"MFT {futures[f]}:")
                
                if len(all_data) > 0:
                    self.used_mft = True
                    print(f"[MFT] 写入数据库: {len(all_data):,} 条, used_mft={self.used_mft}")
                    for i in range(0, len(all_data), 50000):
                        if stop_fn and stop_fn():
                            break
                        batch = all_data[i:i+50000]
                        with self.lock:
                            self.conn.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", batch)
                            self.conn.commit()
                            self.file_count += len(batch)
                
                for drv in failed:
                    if stop_fn and stop_fn():
                        break
                    self._scan_dir(f"{drv}:\\", False, progress_cb, stop_fn)

            # C盘扫描
            for target in c_targets:
                if stop_fn and stop_fn():
                    break
                self._scan_dir(target, True, progress_cb, stop_fn)

            with self.lock:
                self.conn.execute("PRAGMA synchronous=NORMAL")
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES('build_time', ?)", (str(time.time()),))
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES('used_mft', ?)", ('1' if self.used_mft else '0',))
                self.conn.execute("ANALYZE")
                self.conn.commit()
            
            final_mft = self.used_mft
            final_count = self.file_count
            elapsed = time.time() - build_start

            self._load_stats()

            mft_str = "MFT✅" if final_mft else "传统扫描"
            print(f"✅ 索引完成: {final_count:,} 条 ({mft_str}), 耗时 {elapsed:.2f}s")
        except Exception as e:
            print(f"❌ 构建错误: {e}")
        finally:
            self.is_building = False

    def _scan_dir(self, target, is_c_drive, progress_cb=None, stop_fn=None):
        if not os.path.exists(target):
            return
        batch = []
        stack = deque([target])
        while stack:
            if stop_fn and stop_fn():
                break
            cur = stack.pop()
            if should_skip_path(cur.lower()):
                continue
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        if stop_fn and stop_fn():
                            break
                        name = e.name
                        if not name or name[0] in ('.', '$'):
                            continue
                        name_lower = name.lower()
                        try:
                            st = e.stat(follow_symlinks=False)
                            is_dir = st.st_mode & 0o040000
                        except:
                            continue
                        if is_dir:
                            if should_skip_dir(name_lower, is_c_drive):
                                continue
                            stack.append(e.path)
                            batch.append((name, name_lower, e.path, cur, '', 0, 0, 1))
                        else:
                            ext = os.path.splitext(name)[1].lower()
                            if ext in SKIP_EXTS:
                                continue
                            batch.append((name, name_lower, e.path, cur, ext, st.st_size, st.st_mtime, 0))
                        if len(batch) >= 20000:
                            with self.lock:
                                self.conn.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", batch)
                                self.conn.commit()
                                self.file_count += len(batch)
                            if progress_cb:
                                progress_cb(self.file_count, cur)
                            batch = []
            except:
                continue
        if batch:
            with self.lock:
                self.conn.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", batch)
                self.conn.commit()
                self.file_count += len(batch)

    def get_stats(self):
        self._load_stats(preserve_mft=True)
        return {"count": self.file_count, "ready": self.is_ready, "building": self.is_building,
                "time": self.last_build_time, "path": self.db_path, "has_fts": self.has_fts, "used_mft": self.used_mft}

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except:
                pass

# ==================== 文件监控 ====================
class _Handler(FileSystemEventHandler):
    def __init__(self, mgr, eq):
        self.mgr, self.eq = mgr, eq
    def _ignore(self, p):
        n = os.path.basename(p)
        if not n or n.startswith(('.', '$')):
            return True
        if os.path.splitext(n)[1].lower() in SKIP_EXTS:
            return True
        return any(part.lower() in SKIP_DIRS_LOWER for part in Path(p).parts)
    def on_created(self, e):
        if not self._ignore(e.src_path):
            self.eq.put(('c', e.src_path, e.is_directory))
    def on_deleted(self, e):
        if not self._ignore(e.src_path):
            self.eq.put(('d', e.src_path))
    def on_moved(self, e):
        self.eq.put(('m', e.src_path, e.dest_path))

class FileWatcher:
    def __init__(self, mgr):
        self.mgr = mgr
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
            handler = _Handler(self.mgr, self.eq)
            for p in paths:
                if p.upper().startswith('C:'):
                    for cp in get_c_scan_dirs():
                        if os.path.exists(cp):
                            try:
                                self.observer.schedule(handler, cp, recursive=True)
                            except:
                                pass
                elif os.path.exists(p):
                    try:
                        self.observer.schedule(handler, p, recursive=True)
                    except:
                        pass
            self.observer.start()
            self.running = True
            self.stop_flag = False
            self.thread = threading.Thread(target=self._process, daemon=True)
            self.thread.start()
        except:
            pass
            
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
        if not self.mgr.conn:
            return
        ins, dels = [], []
        for ev in events:
            if ev[0] == 'c':
                p = ev[1]
                if os.path.isfile(p):
                    try:
                        n = os.path.basename(p)
                        st = os.stat(p)
                        ins.append((n, n.lower(), p, os.path.dirname(p), os.path.splitext(n)[1].lower(), st.st_size, st.st_mtime, 0))
                    except:
                        pass
            elif ev[0] == 'd':
                dels.append(ev[1])
            elif ev[0] == 'm':
                dels.append(ev[1])
        with self.mgr.lock:
            if dels:
                self.mgr.conn.execute(f"DELETE FROM files WHERE full_path IN ({','.join('?' * len(dels))})", dels)
            if ins:
                self.mgr.conn.executemany("INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)", ins)
            if dels or ins:
                self.mgr.conn.commit()
                
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

# ==================== 主程序 ====================
class SearchApp:
    def __init__(self, root, db_path=None):
        self.root = root
        self.style = ttk.Style("flatly")
        self.style.configure("Treeview.Heading", font=("微软雅黑", 10, "bold"), background='#4CAF50', foreground='white', borderwidth=2, relief="groove")
        self.style.map("Treeview.Heading", background=[('active', '#45a049')], relief=[('active', 'groove')])
        self.style.configure("Treeview", font=("微软雅黑", 9), rowheight=26)
        self.root.title("🚀 极速文件搜索 V38 MFT优化版")
        self.root.geometry("1400x900")
        
        self.result_queue = queue.Queue()
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
        self.force_realtime = tk.BooleanVar(value=False)
        self.shown_paths = set()
        self.last_render_time = 0
        self.render_interval = 0.15
        
        self.config_mgr = ConfigManager()
        self.index_mgr = IndexManager(db_path=db_path)
        self.file_watcher = FileWatcher(self.index_mgr)
        self.index_build_stop = False
        
        self._build_ui()
        self._bind_shortcuts()
        self.root.after(100, self.process_queue)
        self.root.after(500, self._check_index)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        header = ttk.Frame(self.root, padding=15)
        header.pack(fill=X, padx=10, pady=10)
        
        row0 = ttk.Frame(header)
        row0.pack(fill=X, pady=(0, 10))
        ttk.Label(row0, text="⚡ 极速搜 V38", font=("微软雅黑", 18, "bold"), foreground='#4CAF50').pack(side=LEFT)
        ttk.Label(row0, text="🎯 MFT优化版", font=("微软雅黑", 10), foreground='#FF9800').pack(side=LEFT, padx=10)
        self.idx_lbl = ttk.Label(row0, text="检查中...", font=("微软雅黑", 9))
        self.idx_lbl.pack(side=LEFT, padx=20)
        ttk.Button(row0, text="🔄 刷新状态", command=self.refresh_index_status, bootstyle="info-outline", width=12).pack(side=LEFT)
        ttk.Button(row0, text="🔧 索引管理", command=self._show_index_mgr, bootstyle="info-outline", width=12).pack(side=RIGHT)
        
        row1 = ttk.Frame(header)
        row1.pack(fill=X, pady=(0, 8))
        self.scope_var = tk.StringVar(value="所有磁盘 (全盘)")
        self.combo_scope = ttk.Combobox(row1, textvariable=self.scope_var, state="readonly", width=18, font=("微软雅黑", 9))
        self._update_drives()
        self.combo_scope.pack(side=LEFT, padx=(0, 5))
        ttk.Button(row1, text="📂 选择目录", command=self._browse, bootstyle="secondary", width=10).pack(side=LEFT, padx=(0, 15))
        
        self.kw_var = tk.StringVar()
        self.entry_kw = ttk.Entry(row1, textvariable=self.kw_var, font=("微软雅黑", 12), width=45)
        self.entry_kw.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        self.entry_kw.bind('<Return>', lambda e: self.start_search())
        self.entry_kw.bind('<Button-3>', self._show_history)
        self.entry_kw.focus()
        
        ttk.Checkbutton(row1, text="强制实时", variable=self.force_realtime, bootstyle="round-toggle").pack(side=LEFT, padx=(0, 10))
        self.btn_search = ttk.Button(row1, text="🚀 搜索", command=self.start_search, bootstyle="primary", width=10)
        self.btn_search.pack(side=LEFT, padx=2)
        self.btn_refresh = ttk.Button(row1, text="🔄 刷新", command=self.refresh_search, bootstyle="info", width=8, state="disabled")
        self.btn_refresh.pack(side=LEFT, padx=2)
        self.btn_pause = ttk.Button(row1, text="⏸ 暂停", command=self.toggle_pause, bootstyle="warning", width=8, state="disabled")
        self.btn_pause.pack(side=LEFT, padx=2)
        self.btn_stop = ttk.Button(row1, text="⏹ 停止", command=self.stop_search, bootstyle="danger", width=8, state="disabled")
        self.btn_stop.pack(side=LEFT, padx=2)
        
        row2 = ttk.Frame(header)
        row2.pack(fill=X)
        ttk.Label(row2, text="筛选:", font=("微软雅黑", 9)).pack(side=LEFT)
        ttk.Label(row2, text="格式", font=("微软雅黑", 9)).pack(side=LEFT, padx=(10, 2))
        self.ext_var = tk.StringVar(value="全部")
        self.combo_ext = ttk.Combobox(row2, textvariable=self.ext_var, state="readonly", width=15, values=["全部"])
        self.combo_ext.pack(side=LEFT, padx=(0, 15))
        self.combo_ext.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
        ttk.Label(row2, text="大小", font=("微软雅黑", 9)).pack(side=LEFT, padx=(0, 2))
        self.size_var = tk.StringVar(value="不限")
        self.combo_size = ttk.Combobox(row2, textvariable=self.size_var, state="readonly", width=10, values=["不限", ">1MB", ">10MB", ">100MB", ">500MB", ">1GB"])
        self.combo_size.pack(side=LEFT, padx=(0, 15))
        self.combo_size.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
        ttk.Button(row2, text="清除", bootstyle="secondary-outline", width=6, command=self._clear_filter).pack(side=LEFT)
        self.lbl_filter = ttk.Label(row2, text="", font=("微软雅黑", 9), foreground="#666")
        self.lbl_filter.pack(side=RIGHT, padx=10)
        
        body = ttk.Frame(self.root, padding=(10, 0))
        body.pack(fill=BOTH, expand=True)
        columns = ("filename", "path", "size", "mtime")
        self.tree = ttk.Treeview(body, columns=columns, show="headings")
        for col, text, w in zip(columns, ["📄 文件名", "📂 所在目录", "📊 类型/大小", "🕒 修改时间"], [400, 400, 130, 150]):
            self.tree.heading(col, text=text, command=lambda c=col: self.sort_column(c, False))
            self.tree.column(col, width=w, anchor="w" if col in ("filename", "path") else "center")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        sb.pack(side=RIGHT, fill=Y)
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.tag_configure('odd', background='white')
        self.tree.tag_configure('even', background='#f8f9fa')
        self.tree.bind("<Double-1>", self.on_dblclick)
        self.tree.bind("<Button-3>", self.show_menu)
        
        pg = ttk.Frame(body, padding=5)
        pg.pack(fill=X, side=BOTTOM)
        pg_ctr = ttk.Frame(pg)
        pg_ctr.pack(anchor=CENTER)
        self.btn_first = ttk.Button(pg_ctr, text="⏮", command=lambda: self.go_page('first'), bootstyle="link", state="disabled")
        self.btn_first.pack(side=LEFT)
        self.btn_prev = ttk.Button(pg_ctr, text="◀", command=lambda: self.go_page('prev'), bootstyle="link", state="disabled")
        self.btn_prev.pack(side=LEFT)
        self.lbl_page = ttk.Label(pg_ctr, text="第 1/1 页 (0项)", font=("微软雅黑", 9))
        self.lbl_page.pack(side=LEFT, padx=15)
        self.btn_next = ttk.Button(pg_ctr, text="▶", command=lambda: self.go_page('next'), bootstyle="link", state="disabled")
        self.btn_next.pack(side=LEFT)
        self.btn_last = ttk.Button(pg_ctr, text="⏭", command=lambda: self.go_page('last'), bootstyle="link", state="disabled")
        self.btn_last.pack(side=LEFT)
        
        btm = ttk.Frame(self.root, padding=5)
        btm.pack(side=BOTTOM, fill=X)
        self.status = tk.StringVar(value="就绪")
        ttk.Label(btm, textvariable=self.status, font=("微软雅黑", 9)).pack(side=LEFT, padx=10)
        self.status_path = tk.StringVar()
        ttk.Label(btm, textvariable=self.status_path, font=("Consolas", 8), foreground="#718096").pack(side=LEFT, fill=X, expand=True)
        self.progress = ttk.Progressbar(btm, mode='indeterminate', bootstyle="success", length=200)
        
        self.ctx_menu = tk.Menu(self.root, tearoff=0)
        self.ctx_menu.add_command(label="📂 打开文件", command=self.open_file)
        self.ctx_menu.add_command(label="🎯 定位文件", command=self.open_folder)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="📄 复制文件", command=self.copy_file)
        self.ctx_menu.add_command(label="📝 复制路径", command=self.copy_path)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="🗑️ 删除", command=self.delete_file)
        self.hist_menu = tk.Menu(self.root, tearoff=0)

    def _bind_shortcuts(self):
        self.root.bind('<Control-f>', lambda e: self.entry_kw.focus())
        self.root.bind('<Escape>', lambda e: self.stop_search() if self.is_searching else self.kw_var.set(""))
        self.root.bind('<Delete>', lambda e: self.delete_file())

    def _show_history(self, e):
        self.hist_menu.delete(0, tk.END)
        h = self.config_mgr.get_history()
        for kw in h[:15]:
            self.hist_menu.add_command(label=kw, command=lambda k=kw: (self.kw_var.set(k), self.start_search()))
        if not h:
            self.hist_menu.add_command(label="(无历史)", state="disabled")
        self.hist_menu.post(e.x_root, e.y_root)

    def _update_drives(self):
        if platform.system() == 'Windows':
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            self.combo_scope['values'] = ["所有磁盘 (全盘)"] + drives
        else:
            self.combo_scope['values'] = ["所有磁盘 (全盘)", "/"]
        self.combo_scope.current(0)

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.combo_scope.set(d)

    def _get_drives(self):
        if platform.system() == 'Windows':
            return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        return ["/"]

    def _update_ext_combo(self):
        counts = {}
        for item in self.all_results:
            if item['type_code'] == 0:
                ext = "📂文件夹"
            elif item['type_code'] == 1:
                ext = "📦压缩包"
            else:
                ext = os.path.splitext(item['filename'])[1].lower() or "(无)"
            counts[ext] = counts.get(ext, 0) + 1
        values = ["全部"] + [f"{ext} ({cnt})" for ext, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]]
        self.combo_ext['values'] = values

    def _get_size_min(self):
        return {"不限": 0, ">1MB": 1<<20, ">10MB": 10<<20, ">100MB": 100<<20, ">500MB": 500<<20, ">1GB": 1<<30}.get(self.size_var.get(), 0)

    def _apply_filter(self):
        ext_sel = self.ext_var.get()
        size_min = self._get_size_min()
        target_ext = ext_sel.split(" (")[0] if ext_sel != "全部" else None
        self.filtered_results = [item for item in self.all_results
            if not (size_min > 0 and item['type_code'] == 2 and item['size'] < size_min)
            and not (target_ext and (("📂文件夹" if item['type_code'] == 0 else "📦压缩包" if item['type_code'] == 1 else os.path.splitext(item['filename'])[1].lower() or "(无)") != target_ext))]
        self.current_page = 1
        self._render_page()
        self.lbl_filter.config(text=f"筛选: {len(self.filtered_results)}/{len(self.all_results)}")

    def _clear_filter(self):
        self.ext_var.set("全部")
        self.size_var.set("不限")
        self.filtered_results = list(self.all_results)
        self.current_page = 1
        self._render_page()
        self.lbl_filter.config(text="")

    def _update_page_info(self):
        total = len(self.filtered_results)
        self.total_pages = max(1, math.ceil(total / self.page_size))
        self.lbl_page.config(text=f"第 {self.current_page}/{self.total_pages} 页 ({total}项)")
        self.btn_first.config(state="normal" if self.current_page > 1 else "disabled")
        self.btn_prev.config(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next.config(state="normal" if self.current_page < self.total_pages else "disabled")
        self.btn_last.config(state="normal" if self.current_page < self.total_pages else "disabled")

    def go_page(self, action):
        if action == 'first': self.current_page = 1
        elif action == 'prev' and self.current_page > 1: self.current_page -= 1
        elif action == 'next' and self.current_page < self.total_pages: self.current_page += 1
        elif action == 'last': self.current_page = self.total_pages
        self._render_page()

    def _render_page(self):
        self.tree.delete(*self.tree.get_children())
        self.item_meta.clear()
        self._update_page_info()
        start = (self.current_page - 1) * self.page_size
        for i, item in enumerate(self.filtered_results[start:start + self.page_size]):
            iid = self.tree.insert("", "end", values=(item['filename'], item['dir_path'], item['size_str'], item['mtime_str']), tags=('even' if i % 2 else 'odd',))
            self.item_meta[iid] = start + i

    def start_search(self):
        if self.is_searching:
            return
        kw = self.kw_var.get().strip()
        if not kw:
            messagebox.showwarning("提示", "请输入关键词")
            return
        self.config_mgr.add_history(kw)
        self.tree.delete(*self.tree.get_children())
        self.all_results.clear()
        self.filtered_results.clear()
        self.shown_paths.clear()
        self.item_meta.clear()
        self.total_found = 0
        self.current_search_id += 1
        self.start_time = time.time()
        self.current_page = 1
        self.ext_var.set("全部")
        self.size_var.set("不限")
        self.combo_ext['values'] = ["全部"]
        self.lbl_filter.config(text="")
        
        keywords = kw.lower().split()
        scope = self.scope_var.get()
        self.last_search_params = {'keywords': keywords, 'scope': scope, 'kw': kw}
        
        use_idx = not self.force_realtime.get() and self.index_mgr.is_ready and not self.index_mgr.is_building
        if use_idx:
            self.status.set("⚡ 索引搜索...")
            self.btn_refresh.config(state="normal")
            threading.Thread(target=self._search_idx, args=(self.current_search_id, keywords, scope), daemon=True).start()
        else:
            self.status.set("🔍 实时扫描...")
            self.is_searching = True
            self.stop_event = False
            self.btn_search.config(state="disabled")
            self.btn_pause.config(state="normal")
            self.btn_stop.config(state="normal")
            self.progress.pack(side=RIGHT, padx=10)
            self.progress.start(10)
            threading.Thread(target=self._search_rt, args=(self.current_search_id, kw, scope), daemon=True).start()

    def refresh_search(self):
        if self.last_search_params and not self.is_searching:
            self.kw_var.set(self.last_search_params['kw'])
            self.start_search()

    def toggle_pause(self):
        if not self.is_searching:
            return
        self.is_paused = not self.is_paused
        self.btn_pause.config(text="▶ 继续" if self.is_paused else "⏸ 暂停", bootstyle="success" if self.is_paused else "warning")
        (self.progress.stop if self.is_paused else lambda: self.progress.start(10))()

    def stop_search(self):
        if not self.is_searching:
            return
        self.stop_event = True
        self.current_search_id += 1
        self._reset_ui()
        self._finalize()
        self.status.set(f"🛑 已停止 ({len(self.all_results)}项)")

    def _reset_ui(self):
        self.is_searching = False
        self.is_paused = False
        self.btn_search.config(state="normal")
        self.btn_pause.config(state="disabled", text="⏸ 暂停", bootstyle="warning")
        self.btn_stop.config(state="disabled")
        self.progress.stop()
        self.progress.pack_forget()

    def _finalize(self):
        self._update_ext_combo()
        self.filtered_results = list(self.all_results)
        self._render_page()

    def _search_idx(self, sid, keywords, scope):
        try:
            results = self.index_mgr.search(keywords, scope)
            if results is None:
                self.result_queue.put(("MSG", "索引不可用"))
                return
            batch = []
            for fn, fp, sz, mt, is_dir in results:
                if sid != self.current_search_id:
                    return
                ext = os.path.splitext(fn)[1].lower()
                tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
                batch.append((fn, fp, sz, mt, tc))
                if len(batch) >= 100:
                    self.result_queue.put(("BATCH", list(batch)))
                    batch.clear()
            if batch:
                self.result_queue.put(("BATCH", batch))
            self.result_queue.put(("DONE", time.time() - self.start_time))
        except Exception as e:
            self.result_queue.put(("ERROR", str(e)))

    def _search_rt(self, sid, keyword, scope):
        try:
            keywords = keyword.lower().split()
            check = (lambda n, kws=keywords: all(k in n.lower() for k in kws)) if len(keywords) > 1 else (lambda n, kw=keywords[0]: kw in n.lower())
            
            targets = []
            if "所有磁盘" in scope:
                for d in self._get_drives():
                    targets.extend(get_c_scan_dirs() if d.upper().startswith('C:') else [d])
            else:
                targets = [scope]
            
            task_queue = queue.Queue()
            for t in targets:
                if os.path.isdir(t):
                    task_queue.put(t)
            
            active = [0]
            lock = threading.Lock()
            
            def worker():
                local_batch = []
                while not self.stop_event and self.current_search_id == sid:
                    while self.is_paused:
                        if self.stop_event: return
                        time.sleep(0.1)
                    try:
                        cur = task_queue.get(timeout=0.1)
                    except queue.Empty:
                        with lock:
                            if task_queue.empty() and active[0] <= 1: break
                        continue
                    
                    with lock: active[0] += 1
                    if should_skip_path(cur.lower()):
                        with lock: active[0] -= 1
                        continue
                    
                    try:
                        with os.scandir(cur) as it:
                            for e in it:
                                if self.stop_event or self.current_search_id != sid: break
                                name = e.name
                                if not name or name[0] in ('.', '$'): continue
                                try:
                                    st = e.stat(follow_symlinks=False)
                                    is_dir = st.st_mode & 0o040000
                                except: continue
                                
                                if is_dir:
                                    if should_skip_dir(name.lower()): continue
                                    task_queue.put(e.path)
                                    if check(name):
                                        local_batch.append((name, e.path, 0, 0, 0))
                                else:
                                    ext = os.path.splitext(name)[1].lower()
                                    if ext in SKIP_EXTS: continue
                                    if check(name):
                                        local_batch.append((name, e.path, st.st_size, st.st_mtime, 1 if ext in ARCHIVE_EXTS else 2))
                                
                                if len(local_batch) >= 50:
                                    self.result_queue.put(("BATCH", list(local_batch)))
                                    local_batch.clear()
                    except: pass
                    with lock: active[0] -= 1
                
                if local_batch:
                    self.result_queue.put(("BATCH", local_batch))
            
            threads = [threading.Thread(target=worker, daemon=True) for _ in range(16)]
            for t in threads: t.start()
            for t in threads: t.join()
            
            if self.current_search_id == sid and not self.stop_event:
                self.result_queue.put(("DONE", time.time() - self.start_time))
        except Exception as e:
            self.result_queue.put(("ERROR", str(e)))

    def process_queue(self):
        try:
            for _ in range(200):
                if self.result_queue.empty(): break
                t, d = self.result_queue.get_nowait()
                if t == "BATCH":
                    for item in d: self._add_item(*item)
                elif t == "DONE":
                    self._reset_ui()
                    self.status.set(f"✅ 完成: {self.total_found}项 ({d:.2f}s)")
                    self._finalize()
                elif t == "ERROR":
                    self._reset_ui()
                    messagebox.showerror("错误", d)
                elif t == "IDX_PROG":
                    self._check_index()
                    self.status_path.set(f"索引: {d[1][-40:]}")
                elif t == "IDX_DONE":
                    self._check_index()
                    self.status_path.set("")
                    self.status.set(f"✅ 索引完成 ({self.index_mgr.file_count:,})")
            
            if self.all_results and self.is_searching:
                now = time.time()
                if len(self.all_results) <= 200 or (now - self.last_render_time) > self.render_interval:
                    self.filtered_results = list(self.all_results)
                    self._render_page()
                    self.last_render_time = now
        except: pass
        self.root.after(100, self.process_queue)

    def _add_item(self, name, path, size, mtime, type_code):
        if path in self.shown_paths: return
        self.shown_paths.add(path)
        size_str = "📂 文件夹" if type_code == 0 else ("📦 压缩包" if type_code == 1 else format_size(size))
        mtime_str = "-" if type_code == 0 else format_time(mtime)
        self.all_results.append({'filename': name, 'fullpath': path, 'dir_path': os.path.dirname(path),
            'size': size, 'mtime': mtime, 'type_code': type_code, 'size_str': size_str, 'mtime_str': mtime_str})
        self.total_found = len(self.all_results)
        if self.total_found % 100 == 0:
            self.status.set(f"已找到: {self.total_found}")

    def sort_column(self, col, rev):
        if not self.filtered_results: return
        key = {'size': lambda x: (x['type_code'], x['size']), 'mtime': lambda x: x['mtime'],
               'filename': lambda x: x['filename'].lower(), 'path': lambda x: x['dir_path'].lower()}[col]
        self.filtered_results.sort(key=key, reverse=rev)
        self.tree.heading(col, command=lambda: self.sort_column(col, not rev))
        self.current_page = 1
        self._render_page()

    def _check_index(self):
        s = self.index_mgr.get_stats()
        fts = "FTS5✅" if s.get('has_fts') else "FTS5❌"
        mft = "MFT✅" if s.get('used_mft') else "MFT❌"
        if s['building']:
            txt = f"🔄 构建中({s['count']:,}) [{fts}][{mft}]"
        elif s['ready']:
            txt = f"✅ 就绪({s['count']:,}) [{fts}][{mft}]"
        else:
            txt = f"❌ 未构建 [{fts}][{mft}]"
        self.idx_lbl.config(text=txt)

    def refresh_index_status(self):
        self.index_mgr.reload_stats()
        self._check_index()

    def _show_index_mgr(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("🔧 索引管理")
        dlg.geometry("480x300")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry(f"+{self.root.winfo_x()+100}+{self.root.winfo_y()+100}")
        
        f = ttk.Frame(dlg, padding=15)
        f.pack(fill=BOTH, expand=True)
        s = self.index_mgr.get_stats()
        
        ttk.Label(f, text="📊 索引状态", font=("微软雅黑", 12, "bold")).pack(anchor=W)
        ttk.Separator(f).pack(fill=X, pady=8)
        
        info = ttk.Frame(f)
        info.pack(fill=X, pady=5)
        rows = [
            ("文件数量:", f"{s['count']:,}" if s['count'] else "未构建"),
            ("状态:", "✅就绪" if s['ready'] else ("🔄构建中" if s['building'] else "❌未构建")),
            ("FTS5:", "✅已启用" if s.get('has_fts') else "❌未启用"),
            ("MFT:", "✅已使用" if s.get('used_mft') else "❌未使用"),
            ("构建时间:", datetime.datetime.fromtimestamp(s['time']).strftime('%m-%d %H:%M') if s['time'] else "从未"),
        ]
        for i, (l, v) in enumerate(rows):
            ttk.Label(info, text=l).grid(row=i, column=0, sticky=W, pady=2)
            ttk.Label(info, text=v, foreground="#28a745" if "✅" in v else "#555").grid(row=i, column=1, sticky=W, padx=10)
        
        ttk.Separator(f).pack(fill=X, pady=10)
        
        bf = ttk.Frame(f)
        bf.pack(fill=X, pady=5)
        
        def browse():
            p = filedialog.asksaveasfilename(title="选择索引位置", initialdir=os.path.dirname(s['path']),
                initialfile="index.db", defaultextension=".db", filetypes=[("SQLite", "*.db")])
            if p:
                ok, msg = self.index_mgr.change_db_path(p)
                if ok:
                    self.file_watcher.stop()
                    self.file_watcher = FileWatcher(self.index_mgr)
                    self._check_index()
                    dlg.destroy()
                    self._show_index_mgr()
                else:
                    messagebox.showerror("错误", msg)
        
        def rebuild():
            dlg.destroy()
            self._build_index()
        
        def delete():
            if messagebox.askyesno("确认", "确定删除索引？"):
                self.file_watcher.stop()
                self.index_mgr.close()
                for ext in ['', '-wal', '-shm']:
                    try: os.remove(self.index_mgr.db_path + ext)
                    except: pass
                self.index_mgr = IndexManager(db_path=self.index_mgr.db_path)
                self.file_watcher = FileWatcher(self.index_mgr)
                self._check_index()
                dlg.destroy()
        
        ttk.Button(bf, text="🔄 重建索引", command=rebuild, bootstyle="primary", width=12).pack(side=LEFT, padx=3)
        ttk.Button(bf, text="📁 更改位置", command=browse, bootstyle="secondary", width=12).pack(side=LEFT, padx=3)
        ttk.Button(bf, text="🗑️ 删除索引", command=delete, bootstyle="danger-outline", width=12).pack(side=LEFT, padx=3)
        ttk.Button(bf, text="关闭", command=dlg.destroy, bootstyle="secondary", width=8).pack(side=RIGHT, padx=3)

    def _build_index(self):
        if self.index_mgr.is_building: return
        self.index_build_stop = False
        def run():
            self.index_mgr.build_index(self._get_drives(), lambda c, p: self.result_queue.put(("IDX_PROG", (c, p))), lambda: self.index_build_stop)
            self.result_queue.put(("IDX_DONE", None))
        threading.Thread(target=run, daemon=True).start()
        self._check_index()

    def on_dblclick(self, e):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.item_meta: return
        item = self.filtered_results[self.item_meta[sel[0]]]
        if item['type_code'] == 0:
            subprocess.Popen(f'explorer "{item["fullpath"]}"')
        else:
            try: os.startfile(item['fullpath'])
            except Exception as ex: messagebox.showerror("错误", str(ex))

    def show_menu(self, e):
        item = self.tree.identify_row(e.y)
        if item:
            self.tree.selection_set(item)
            self.ctx_menu.post(e.x_root, e.y_root)

    def _get_sel(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.item_meta: return None
        return self.filtered_results[self.item_meta[sel[0]]]

    def open_file(self):
        item = self._get_sel()
        if item:
            try: os.startfile(item['fullpath'])
            except Exception as e: messagebox.showerror("错误", str(e))

    def open_folder(self):
        item = self._get_sel()
        if item: subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')

    def copy_path(self):
        item = self._get_sel()
        if item:
            self.root.clipboard_clear()
            self.root.clipboard_append(item['fullpath'])

    def copy_file(self):
        if not HAS_WIN32: return
        item = self._get_sel()
        if item:
            try:
                data = struct.pack('IIIII', 20, 0, 0, 0, 1) + (os.path.abspath(item['fullpath']) + '\0').encode('utf-16le') + b'\0'
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
                win32clipboard.CloseClipboard()
            except: pass

    def delete_file(self):
        item = self._get_sel()
        if not item: return
        if not messagebox.askyesno("确认", f"确定删除?\n{item['filename']}", icon='warning'): return
        try:
            (shutil.rmtree if item['type_code'] == 0 else os.remove)(item['fullpath'])
            sel = self.tree.selection()
            if sel: self.tree.delete(sel[0])
            self.shown_paths.discard(item['fullpath'])
            self.status.set(f"✅ 已删除: {item['filename']}")
        except Exception as e:
            messagebox.showerror("失败", str(e))

    def _on_close(self):
        self.index_build_stop = True
        self.stop_event = True
        self.file_watcher.stop()
        self.index_mgr.close()
        self.root.destroy()

if __name__ == "__main__":
    print("🚀 极速文件搜索 V38 MFT优化版")
    if platform.system() == 'Windows':
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except: pass
    root = ttk.Window(themename="flatly")
    app = SearchApp(root)
    root.mainloop()