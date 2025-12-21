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
import random
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

try:
    import win32clipboard
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


# ==================== C盘扫描目录 ====================
C_DRIVE_DIRS = [
    os.path.expandvars(r"%TEMP%"),
    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
    os.path.expandvars(r"%USERPROFILE%\Desktop"),
]

def get_c_scan_dirs():
    return [p for p in C_DRIVE_DIRS if os.path.exists(p)]


# ==================== CAD版本正则 ====================
# 匹配 CAD2010~CAD2024（无下划线）
CAD_PATTERN = re.compile(r'cad20(1[0-9]|2[0-4])', re.IGNORECASE)
# 匹配 AutoCAD_2010~AutoCAD_2025（带下划线）
AUTOCAD_PATTERN = re.compile(r'autocad_20(1[0-9]|2[0-5])', re.IGNORECASE)


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


# ==================== 索引管理器 ====================
class IndexManager:
    def __init__(self, db_path=None):
        if db_path is None:
            home = Path.home()
            idx_dir = home / ".filesearch"
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
        self._init_db()

    def _init_db(self):
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA cache_size=10000")
            self.conn.execute("""
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
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_ext ON files(extension)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            self.conn.commit()
            self._load_stats()
        except Exception as e:
            print(f"DB错误: {e}")
            self.conn = None

    def _load_stats(self):
        if not self.conn:
            return
        try:
            self.file_count = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            row = self.conn.execute("SELECT value FROM meta WHERE key='build_time'").fetchone()
            self.last_build_time = float(row[0]) if row else None
            self.is_ready = self.file_count > 0
        except:
            pass

    def reload_stats(self):
        if self.is_building:
            return
        with self.lock:
            self._load_stats()

    def change_db_path(self, new_path):
        if not new_path:
            return False, "路径不能为空"
        new_path = os.path.abspath(new_path)
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
        except Exception as e:
            return False, f"无法创建目录: {e}"
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
        self.file_count = 0
        self.is_ready = False
        self.last_build_time = None
        self._init_db()
        return True, "索引位置已更改"

    def search(self, keywords, scope=None, limit=50000):
        if not self.conn or not self.is_ready:
            return None
        try:
            with self.lock:
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
        """
        构建全盘索引，过滤规则：
        - 目录：Program Files / ProgramData / node_modules / __pycache__ / site-packages / sys
        - 非C盘过滤 Users 目录
        - 路径包含 CAD2010~CAD2024 / AutoCAD_2010~AutoCAD_2025 / Tangent 的过滤
        - 后缀过滤
        """
        if not self.conn:
            return
        self.is_building = True
        self.is_ready = False

        # 系统目录过滤（小写）
        BASE_SKIP_DIRS = {
            '$recycle.bin',
            'system volume information',
            '$windows.~bt',
            '$windows.~ws',
            'recovery',
            'config.msi',
        }

        # 程序/库目录过滤（小写）
        EXTRA_SKIP_DIRS = {
            'program files',
            'program files (x86)',
            'programdata',
            'node_modules',
            '__pycache__',
            'site-packages',
            'sys',
        }

        # 后缀过滤（索引扫描）
        SKIP_EXTS = {
            '.lsp', '.fas', '.lnk', '.html', '.htm',
            '.xml', '.ini', '.lsp_bak', '.cuix', '.arx', '.crx',
            '.fx', '.dbx', '.kid', '.ico', '.rz', '.dll',
            '.sys', '.tmp', '.log', '.dat', '.db', '.pdb',
            '.obj', '.pyc', '.class'
        }

        try:
            with self.lock:
                self.conn.execute("DELETE FROM files")
                self.conn.commit()

            self.file_count = 0
            batch = []
            scan_list = []
            for drive in drives:
                if drive.upper().startswith('C:'):
                    scan_list.extend(get_c_scan_dirs())
                else:
                    scan_list.append(drive)

            for drive in scan_list:
                if stop_fn and stop_fn():
                    break
                
                # 判断当前是否为C盘扫描
                is_c_drive = drive.upper().startswith('C:')
                
                stack = deque([drive])
                while stack:
                    if stop_fn and stop_fn():
                        break
                    cur = stack.pop()
                    cur_lower = cur.lower().replace("\\", "/")

                    # 路径级别过滤：site-packages
                    if 'site-packages' in cur_lower:
                        continue
                    
                    # 路径级别过滤：CAD2010~2024
                    if CAD_PATTERN.search(cur_lower):
                        continue
                    
                    # 路径级别过滤：AutoCAD_2010~2025
                    if AUTOCAD_PATTERN.search(cur_lower):
                        continue
                    
                    # 路径级别过滤：Tangent
                    if 'tangent' in cur_lower:
                        continue

                    try:
                        with os.scandir(cur) as it:
                            entries = list(it)
                    except:
                        continue

                    for entry in entries:
                        if stop_fn and stop_fn():
                            break
                        try:
                            name = entry.name
                            name_lower = name.lower()
                            is_dir = entry.is_dir(follow_symlinks=False)

                            if is_dir:
                                # 基础系统目录过滤
                                if name_lower in BASE_SKIP_DIRS:
                                    continue
                                # 程序/库目录过滤
                                if name_lower in EXTRA_SKIP_DIRS:
                                    continue
                                # 非C盘过滤 Users 目录
                                if not is_c_drive and name_lower == 'users':
                                    continue
                                # 目录名包含 CAD2010~2024
                                if CAD_PATTERN.search(name_lower):
                                    continue
                                # 目录名包含 AutoCAD_2010~2025
                                if AUTOCAD_PATTERN.search(name_lower):
                                    continue
                                # 目录名包含 Tangent
                                if 'tangent' in name_lower:
                                    continue

                                # 其余目录：加入栈，同时索引该目录本身
                                stack.append(entry.path)
                                batch.append((name, name_lower, entry.path, cur, '', 0, 0, 1))
                            else:
                                # 文件后缀过滤
                                ext = os.path.splitext(name)[1].lower()
                                if ext in SKIP_EXTS:
                                    continue

                                try:
                                    st = entry.stat()
                                    sz, mt = st.st_size, st.st_mtime
                                except:
                                    sz, mt = 0, 0
                                batch.append((name, name_lower, entry.path, cur, ext, sz, mt, 0))

                            if len(batch) >= 2000:
                                self._insert(batch)
                                batch.clear()
                                if progress_cb:
                                    progress_cb(self.file_count, cur)
                        except:
                            continue
            if batch:
                self._insert(batch)
            with self.lock:
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES('build_time', ?)", (str(time.time()),))
                self.conn.commit()
            self._load_stats()
        except Exception as e:
            print(f"构建错误: {e}")
        finally:
            self.is_building = False

    def _insert(self, batch):
        try:
            with self.lock:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO files(filename,filename_lower,full_path,parent_dir,extension,size,mtime,is_dir) VALUES(?,?,?,?,?,?,?,?)",
                    batch)
                self.conn.commit()
                self.file_count += len(batch)
        except:
            pass

    def get_stats(self):
        self._load_stats()
        return {"count": self.file_count, "ready": self.is_ready, "building": self.is_building, 
                "time": self.last_build_time, "path": self.db_path}

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
        if not n or n.startswith(('.', '$', '~')):
            return True
        if os.path.splitext(n)[1].lower() in {'.tmp', '.log', '.bak', '.sys', '.dll', '.pdb'}:
            return True
        for part in Path(p).parts:
            if part.lower() in {'windows', 'program files', 'program files (x86)', 'programdata', 'appdata', 
                               'system volume information', '$recycle.bin'}:
                return True
        return False

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
            watch_paths = []
            for p in paths:
                if p.upper().startswith('C:'):
                    watch_paths.extend(get_c_scan_dirs())
                else:
                    watch_paths.append(p)
            for p in watch_paths:
                if os.path.exists(p):
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
                try:
                    batch.append(self.eq.get(timeout=2.0))
                except queue.Empty:
                    pass
                if batch and (len(batch) >= 100 or time.time() - last >= 2.0):
                    self._apply(batch)
                    batch.clear()
                    last = time.time()
            except:
                time.sleep(1)

    def _apply(self, events):
        if not self.mgr.conn:
            return
        ins, dels = [], []
        for ev in events:
            if ev[0] == 'c':
                p, is_dir = ev[1], ev[2]
                if is_dir:
                    self._scan(ins, p)
                elif os.path.isfile(p):
                    self._add(ins, p)
            elif ev[0] == 'd':
                dels.append(ev[1])
            elif ev[0] == 'm':
                dels.append(ev[1])
                if os.path.isfile(ev[2]):
                    self._add(ins, ev[2])
                elif os.path.isdir(ev[2]):
                    self._scan(ins, ev[2])
        with self.mgr.lock:
            if dels:
                self.mgr.conn.execute(f"DELETE FROM files WHERE full_path IN ({','.join('?' * len(dels))})", dels)
            if ins:
                self.mgr.conn.executemany(
                    "INSERT OR IGNORE INTO files(filename,filename_lower,full_path,parent_dir,extension,size,mtime,is_dir) VALUES(?,?,?,?,?,?,?,?)", ins)
            if dels or ins:
                self.mgr.conn.commit()

    def _add(self, batch, p):
        try:
            n = os.path.basename(p)
            st = os.stat(p)
            batch.append((n, n.lower(), p, os.path.dirname(p), os.path.splitext(n)[1].lower(), st.st_size, st.st_mtime, 0))
        except:
            pass

    def _scan(self, batch, dp, maxd=3):
        stack = deque([(dp, 0)])
        while stack:
            cur, d = stack.pop()
            if d > maxd:
                continue
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            stack.append((e.path, d + 1))
                            batch.append((e.name, e.name.lower(), e.path, cur, '', 0, 0, 1))
                        else:
                            self._add(batch, e.path)
            except:
                continue

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
        
        # 表头样式：增加明显的分割线
        self.style.configure("Treeview.Heading", 
            font=("微软雅黑", 10, "bold"),
            background='#4CAF50',
            foreground='white',
            borderwidth=2,
            relief="groove"
        )
        self.style.map("Treeview.Heading",
            background=[('active', '#45a049')],
            relief=[('active', 'groove')]
        )
        self.style.configure("Treeview", font=("微软雅黑", 9), rowheight=26)

        self.root.title("🚀 极速文件搜索 V33")
        self.root.geometry("1400x900")

        self.result_queue = queue.Queue()
        self.is_searching = False
        self.is_paused = False
        self.stop_event = False
        self.total_found = 0
        self.current_search_id = 0

        self.all_results = []
        self.filtered_results = []
        self.page_size = 500
        self.current_page = 1
        self.total_pages = 1
        self.item_meta = {}
        self.start_time = 0.0
        self.last_search_params = None
        self.force_realtime = tk.BooleanVar(value=False)
        self.shown_paths = set()

        self.config_mgr = ConfigManager()
        self.index_mgr = IndexManager(db_path=db_path)
        self.file_watcher = FileWatcher(self.index_mgr)
        self.index_build_stop = False

        self._build_ui()
        self._bind_shortcuts()
        self.root.after(60, self.process_queue)
        self.root.after(500, self._check_index)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        header = ttk.Frame(self.root, padding=15)
        header.pack(fill=X, padx=10, pady=10)

        # 第一行
        row0 = ttk.Frame(header)
        row0.pack(fill=X, pady=(0, 10))
        ttk.Label(row0, text="⚡ 极速搜 V33", font=("微软雅黑", 18, "bold"), foreground='#4CAF50').pack(side=LEFT)
        self.idx_lbl = ttk.Label(row0, text="检查中...", font=("微软雅黑", 9))
        self.idx_lbl.pack(side=LEFT, padx=20)
        ttk.Button(row0, text="🔄 刷新索引状态", command=self.refresh_index_status, bootstyle="info-outline", width=16).pack(side=LEFT)
        ttk.Button(row0, text="🔧 索引管理", command=self._show_index_mgr, bootstyle="info-outline", width=12).pack(side=RIGHT)

        # 第二行
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

        self.btn_search = ttk.Button(row1, text="🚀 启动搜索", command=self.start_search, bootstyle="primary", width=12)
        self.btn_search.pack(side=LEFT, padx=2)
        self.btn_refresh = ttk.Button(row1, text="🔄 刷新结果", command=self.refresh_search, bootstyle="info", width=12, state="disabled")
        self.btn_refresh.pack(side=LEFT, padx=2)
        self.btn_pause = ttk.Button(row1, text="⏸ 暂停", command=self.toggle_pause, bootstyle="warning", width=8, state="disabled")
        self.btn_pause.pack(side=LEFT, padx=2)
        self.btn_stop = ttk.Button(row1, text="⏹ 停止", command=self.stop_search, bootstyle="danger", width=8, state="disabled")
        self.btn_stop.pack(side=LEFT, padx=2)

        # 第三行 - 筛选
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
        self.combo_size = ttk.Combobox(row2, textvariable=self.size_var, state="readonly", width=10,
                                        values=["不限", ">1MB", ">10MB", ">100MB", ">500MB", ">1GB"])
        self.combo_size.pack(side=LEFT, padx=(0, 15))
        self.combo_size.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())

        ttk.Button(row2, text="清除筛选", bootstyle="secondary-outline", width=8, command=self._clear_filter).pack(side=LEFT, padx=(10, 0))

        self.lbl_filter = ttk.Label(row2, text="", font=("微软雅黑", 9), foreground="#666")
        self.lbl_filter.pack(side=RIGHT, padx=10)

        # 表格
        body = ttk.Frame(self.root, padding=(10, 0))
        body.pack(fill=BOTH, expand=True)

        columns = ("filename", "path", "size", "mtime")
        self.tree = ttk.Treeview(body, columns=columns, show="headings")

        # 表头文字
        header_texts = ["📄 文件名", "📂 所在目录", "📊 类型/大小", "🕒 修改时间"]
        widths = [400, 400, 130, 150]

        for col, text, w in zip(columns, header_texts, widths):
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

        # 分页
        pg = ttk.Frame(body, padding=5)
        pg.pack(fill=X, side=BOTTOM)
        pg_ctr = ttk.Frame(pg)
        pg_ctr.pack(anchor=CENTER)
        self.btn_first = ttk.Button(pg_ctr, text="⏮ 首页", command=lambda: self.go_page('first'), bootstyle="link-secondary", state="disabled")
        self.btn_first.pack(side=LEFT)
        self.btn_prev = ttk.Button(pg_ctr, text="◀ 上一页", command=lambda: self.go_page('prev'), bootstyle="link-secondary", state="disabled")
        self.btn_prev.pack(side=LEFT)
        self.lbl_page = ttk.Label(pg_ctr, text="第 1 / 1 页 (共 0 项)", font=("微软雅黑", 9, "bold"), foreground="#666")
        self.lbl_page.pack(side=LEFT, padx=15)
        self.btn_next = ttk.Button(pg_ctr, text="下一页 ▶", command=lambda: self.go_page('next'), bootstyle="link-secondary", state="disabled")
        self.btn_next.pack(side=LEFT)
        self.btn_last = ttk.Button(pg_ctr, text="末页 ⏭", command=lambda: self.go_page('last'), bootstyle="link-secondary", state="disabled")
        self.btn_last.pack(side=LEFT)

        # 底部
        btm = ttk.Frame(self.root, padding=5)
        btm.pack(side=BOTTOM, fill=X)
        self.status = tk.StringVar(value="就绪 | Ctrl+F 搜索 | Esc 停止 | Del 删除")
        ttk.Label(btm, textvariable=self.status, font=("微软雅黑", 9, "bold"), foreground="#2d3748").pack(side=LEFT, padx=10)
        self.status_path = tk.StringVar()
        ttk.Label(btm, textvariable=self.status_path, font=("Consolas", 8), foreground="#718096").pack(side=LEFT, fill=X, expand=True)
        self.progress = ttk.Progressbar(btm, mode='indeterminate', bootstyle="success", length=200)

        # 菜单
        self.ctx_menu = tk.Menu(self.root, tearoff=0)
        self.ctx_menu.add_command(label="📂 打开文件", command=self.open_file)
        self.ctx_menu.add_command(label="🎯 定位文件", command=self.open_folder)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="📄 复制文件", command=self.copy_file)
        self.ctx_menu.add_command(label="📝 复制路径", command=self.copy_path)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="🗑️ 删除文件", command=self.delete_file)

        self.hist_menu = tk.Menu(self.root, tearoff=0)

    def _bind_shortcuts(self):
        self.root.bind('<Control-f>', lambda e: self._focus())
        self.root.bind('<Control-F>', lambda e: self._focus())
        self.root.bind('<Escape>', lambda e: self._escape())
        self.root.bind('<Delete>', lambda e: self.delete_file())

    def _focus(self):
        self.entry_kw.focus()
        self.entry_kw.select_range(0, tk.END)

    def _escape(self):
        if self.is_searching:
            self.stop_search()
        else:
            self.kw_var.set("")

    def _show_history(self, e):
        self.hist_menu.delete(0, tk.END)
        h = self.config_mgr.get_history()
        if h:
            for kw in h[:15]:
                self.hist_menu.add_command(label=kw, command=lambda k=kw: self._use_hist(k))
            self.hist_menu.add_separator()
            self.hist_menu.add_command(label="清空历史", command=lambda: self.config_mgr.config.update({"search_history": []}) or self.config_mgr.save())
        else:
            self.hist_menu.add_command(label="(无历史)", state="disabled")
        self.hist_menu.post(e.x_root, e.y_root)

    def _use_hist(self, kw):
        self.kw_var.set(kw)
        self.start_search()

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

    # ===== 筛选 =====
    def _update_ext_combo(self):
        counts = {}
        for item in self.all_results:
            if item['type_code'] == 0:
                ext = "📂文件夹"
            else:
                ext = os.path.splitext(item['filename'])[1].lower() or "(无)"
            counts[ext] = counts.get(ext, 0) + 1
        
        sorted_exts = sorted(counts.items(), key=lambda x: -x[1])
        values = ["全部"]
        for ext, cnt in sorted_exts[:30]:
            values.append(f"{ext} ({cnt})")
        self.combo_ext['values'] = values
        self.combo_ext.set("全部")

    def _get_size_min(self):
        m = {"不限": 0, ">1MB": 1<<20, ">10MB": 10<<20, ">100MB": 100<<20, ">500MB": 500<<20, ">1GB": 1<<30}
        return m.get(self.size_var.get(), 0)

    def _apply_filter(self):
        ext_sel = self.ext_var.get()
        size_min = self._get_size_min()

        target_ext = None
        if ext_sel != "全部":
            target_ext = ext_sel.split(" (")[0]

        self.filtered_results = []
        for item in self.all_results:
            if size_min > 0 and item['type_code'] != 0 and item['size_raw'] < size_min:
                continue
            if target_ext:
                if item['type_code'] == 0:
                    item_ext = "📂文件夹"
                else:
                    item_ext = os.path.splitext(item['filename'])[1].lower() or "(无)"
                if item_ext != target_ext:
                    continue
            self.filtered_results.append(item)

        self.current_page = 1
        self._render_page()
        self.lbl_filter.config(text=f"筛选: {len(self.filtered_results)} / {len(self.all_results)}")

    def _clear_filter(self):
        self.ext_var.set("全部")
        self.size_var.set("不限")
        self.filtered_results = list(self.all_results)
        self.current_page = 1
        self._render_page()
        self.lbl_filter.config(text="")

    # ===== 分页 =====
    def _update_page_info(self):
        total = len(self.filtered_results)
        self.total_pages = max(1, math.ceil(total / self.page_size))
        self.lbl_page.config(text=f"第 {self.current_page} / {self.total_pages} 页 (共 {total} 项)")
        self.btn_first.config(state="normal" if self.current_page > 1 else "disabled")
        self.btn_prev.config(state="normal" if self.current_page > 1 else "disabled")
        self.btn_next.config(state="normal" if self.current_page < self.total_pages else "disabled")
        self.btn_last.config(state="normal" if self.current_page < self.total_pages else "disabled")

    def go_page(self, action):
        if action == 'first':
            self.current_page = 1
        elif action == 'prev' and self.current_page > 1:
            self.current_page -= 1
        elif action == 'next' and self.current_page < self.total_pages:
            self.current_page += 1
        elif action == 'last':
            self.current_page = self.total_pages
        self._render_page()

    def _render_page(self):
        self.tree.delete(*self.tree.get_children())
        self.item_meta.clear()
        self._update_page_info()

        start = (self.current_page - 1) * self.page_size
        subset = self.filtered_results[start:start + self.page_size]

        for i, item in enumerate(subset):
            tag = 'even' if i % 2 else 'odd'
            iid = self.tree.insert("", "end", values=(item['filename'], item['dir_path'], item['size_str'], item['mtime_str']), tags=(tag,))
            self.item_meta[iid] = start + i

        self._update_page_info()

    # ===== 搜索 =====
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
            self.status.set("⚡ 索引搜索中...")
            self.btn_refresh.config(state="normal")
            threading.Thread(target=self._search_idx, args=(self.current_search_id, keywords, scope), daemon=True).start()
        else:
            self.status.set("🔍 实时扫描中...")
            self.is_searching = True
            self.stop_event = False
            self.btn_search.config(state="disabled")
            self.btn_refresh.config(state="disabled")
            self.btn_pause.config(state="normal")
            self.btn_stop.config(state="normal")
            self.progress.pack(side=RIGHT, padx=10)
            self.progress.start(10)
            threading.Thread(target=self._search_rt, args=(self.current_search_id, kw, scope), daemon=True).start()

    def refresh_search(self):
        if not self.last_search_params or self.is_searching:
            return
        self.kw_var.set(self.last_search_params['kw'])
        self.start_search()

    def toggle_pause(self):
        if not self.is_searching:
            return
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.btn_pause.config(text="▶ 继续", bootstyle="success")
            self.progress.stop()
        else:
            self.btn_pause.config(text="⏸ 暂停", bootstyle="warning")
            self.progress.start(10)

    def stop_search(self):
        if not self.is_searching:
            return
        self.stop_event = True
        self.current_search_id += 1
        try:
            with self.result_queue.mutex:
                self.result_queue.queue.clear()
        except:
            pass
        self._reset_ui()
        self._finalize()
        self.status.set(f"🛑 已停止 ({time.time() - self.start_time:.2f}s, {len(self.all_results)}项)")

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

    # ===== 索引搜索线程 =====
    def _search_idx(self, sid, keywords, scope):
        try:
            results = self.index_mgr.search(keywords, scope)
            if results is None:
                self.result_queue.put(("MSG", "索引不可用"))
                return
            ARCH = {'.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.jar'}
            batch = []
            for fn, fp, sz, mt, is_dir in results:
                if sid != self.current_search_id:
                    return
                ext = os.path.splitext(fn)[1].lower()
                tc = 0 if is_dir else (1 if ext in ARCH else 2)
                batch.append((fn, fp, sz, mt, sz, tc))
                if len(batch) >= 500:
                    self.result_queue.put(("BATCH", list(batch)))
                    batch.clear()
            if batch:
                self.result_queue.put(("BATCH", batch))
            self.result_queue.put(("DONE", time.time() - self.start_time))
        except Exception as e:
            self.result_queue.put(("ERROR", str(e)))

    # ===== 实时搜索线程 =====
    def _search_rt(self, sid, keyword, scope):
        try:
            keywords = keyword.lower().split()
            use_re = len(keywords) > 1
            if use_re:
                ptn = ''.join(f'(?=.*{re.escape(k)})' for k in keywords) + '.*'
            else:
                ptn = keywords[0]

            if "所有磁盘" in scope:
                targets = []
                for d in self._get_drives():
                    if d.upper().startswith('C:'):
                        targets.extend(get_c_scan_dirs())
                    else:
                        targets.append(d)
            else:
                targets = [scope]

            tasks = [t for t in targets if os.path.isdir(t)]
            max_depth = 12
            workers = min(32, (os.cpu_count() or 4) * 4)

            random.shuffle(tasks)

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [
                    ex.submit(self._scan, sid, p, ptn, use_re, max_depth)
                    for p in tasks
                    if self.current_search_id == sid
                ]
                concurrent.futures.wait(futures)

            if self.current_search_id == sid and not self.stop_event:
                self.result_queue.put(("DONE", time.time() - self.start_time))
        except Exception as e:
            self.result_queue.put(("ERROR", str(e)))

    def _scan(self, sid, start, ptn, use_re, max_depth):
        # 目录过滤（全部小写匹配）
        SKIP_LOWER = {
            'windows', 'program files', 'program files (x86)', 'programdata',
            '$recycle.bin', 'system volume information', 'appdata',
            'boot', 'node_modules', '.git', '__pycache__', 'site-packages',
            'sys'
        }
        
        # 后缀过滤
        JUNK = {
            '.sys', '.dll', '.tmp', '.log', '.dat', '.db', '.pdb',
            '.obj', '.pyc', '.class', '.lsp', '.fas', '.lnk', '.html', '.htm',
            '.xml', '.ini', '.lsp_bak', '.cuix', '.arx', '.crx',
            '.fx', '.dbx', '.kid', '.ico', '.rz'
        }
        ARCH = {'.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.jar'}

        stack = deque([(start, 0)])
        batch = []
        check = re.compile(ptn, re.IGNORECASE).match if use_re else (lambda n: ptn in n.lower())
        cnt = 0

        while stack:
            if cnt % 2000 == 0:
                if self.stop_event or self.current_search_id != sid:
                    return
                while self.is_paused:
                    if self.stop_event:
                        return
                    time.sleep(0.1)

            try:
                cur, depth = stack.pop()
                if depth > max_depth:
                    continue

                cur_lower = cur.lower()
                
                # 路径级别过滤：site-packages
                if 'site-packages' in cur_lower:
                    continue
                
                # 路径级别过滤：CAD2010~2024
                if CAD_PATTERN.search(cur_lower):
                    continue
                
                # 路径级别过滤：AutoCAD_2010~2025
                if AUTOCAD_PATTERN.search(cur_lower):
                    continue

                cnt += 1
                if cnt % 10000 == 0:
                    self.result_queue.put(("PATH", cur))

                with os.scandir(cur) as it:
                    for e in it:
                        name = e.name
                        if not name or name[0] in ('.', '$', '~'):
                            continue

                        name_lower = name.lower()
                        is_dir = e.is_dir(follow_symlinks=False)

                        if is_dir:
                            # 目录名过滤（小写匹配）
                            if name_lower in SKIP_LOWER:
                                continue
                            # 目录名包含 CAD2010~2024
                            if CAD_PATTERN.search(name_lower):
                                continue
                            # 目录名包含 AutoCAD_2010~2025
                            if AUTOCAD_PATTERN.search(name_lower):
                                continue
                            
                            if depth < max_depth:
                                stack.append((e.path, depth + 1))
                            if check(name):
                                batch.append((name, e.path, 0, 0, -1, 0))
                            continue

                        ext = os.path.splitext(name)[1].lower()
                        if ext in JUNK:
                            continue

                        if not check(name):
                            continue

                        try:
                            st = e.stat()
                            tc = 1 if ext in ARCH else 2
                            batch.append((name, e.path, st.st_size, st.st_mtime, st.st_size, tc))
                        except Exception:
                            pass

                        if len(batch) >= 800:
                            self.result_queue.put(("BATCH", list(batch)))
                            batch.clear()
            except Exception:
                continue

        if batch:
            self.result_queue.put(("BATCH", batch))

    # ===== 队列处理 =====
    def process_queue(self):
        try:
            render = False
            for _ in range(150):
                if self.result_queue.empty():
                    break
                t, d = self.result_queue.get_nowait()
                if t == "BATCH":
                    for item in d:
                        self._add_item(*item)
                    render = True
                elif t == "PATH":
                    self.status_path.set(f"扫描: {d[-50:]}")
                elif t == "MSG":
                    self.status.set(d)
                elif t == "DONE":
                    self._reset_ui()
                    self.status_path.set("")
                    self.status.set(f"✅ 完成: {self.total_found} 项 ({d:.2f}s)")
                    self._finalize()
                    render = True
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
                    if HAS_WATCHDOG and not self.file_watcher.running:
                        self.file_watcher.start(self._get_drives())

            if render and self.is_searching:
                self.filtered_results = list(self.all_results)
                self.total_pages = max(1, math.ceil(len(self.filtered_results) / self.page_size))
                self.lbl_page.config(text=f"第 {self.current_page} / {self.total_pages} 页 (共 {len(self.filtered_results)} 项)")
        except:
            pass
        self.root.after(60, self.process_queue)

    def _add_item(self, name, path, size, mtime, sort_val, tc):
        if path in self.shown_paths:
            return
        self.shown_paths.add(path)

        dir_path = os.path.dirname(path)
        if tc == 0:
            size_str, mtime_str = "📂 文件夹", "-"
        elif tc == 1:
            size_str = "📦 压缩包"
            try:
                mtime_str = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
            except:
                mtime_str = "-"
        else:
            size_str = self._fmt_size(size)
            try:
                mtime_str = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
            except:
                mtime_str = "-"

        self.all_results.append({
            'filename': name, 'fullpath': path, 'dir_path': dir_path, 'size_raw': size, 'mtime_raw': mtime,
            'type_code': tc, 'sort_val': sort_val or 0, 'size_str': size_str, 'mtime_str': mtime_str
        })
        self.total_found = len(self.all_results)
        if self.total_found % 100 == 0:
            self.status.set(f"已找到: {self.total_found}")

    def _fmt_size(self, s):
        if not s:
            return '0 B'
        for u in ['B', 'KB', 'MB', 'GB', 'TB']:
            if s < 1024:
                return f"{s:.1f} {u}"
            s /= 1024
        return f"{s:.1f} PB"

    def sort_column(self, col, rev):
        if not self.filtered_results:
            return
        key = {'size': lambda x: (x['type_code'], x['sort_val']), 'mtime': lambda x: x['mtime_raw'],
               'filename': lambda x: x['filename'].lower(), 'path': lambda x: x['dir_path'].lower()}[col]
        self.filtered_results.sort(key=key, reverse=rev)
        self.tree.heading(col, command=lambda: self.sort_column(col, not rev))
        self.current_page = 1
        self._render_page()

    # ===== 索引 =====
    def _check_index(self):
        s = self.index_mgr.get_stats()
        if s['building']:
            txt = f"🔄 构建中... ({s['count']:,})"
        elif s['ready']:
            txt = f"✅ 索引就绪 ({s['count']:,})"
            if HAS_WATCHDOG and not self.file_watcher.running:
                self.file_watcher.start(self._get_drives())
        else:
            txt = "❌ 索引未构建"
        self.idx_lbl.config(text=txt)

    def refresh_index_status(self):
        if self.index_mgr.is_building:
            messagebox.showinfo("提示", "索引正在构建中")
            return
        self.index_mgr.reload_stats()
        self._check_index()
        messagebox.showinfo("成功", "索引状态已刷新")

    def _show_index_mgr(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("🔧 索引管理")
        dlg.geometry("520x380")
        dlg.transient(self.root)
        dlg.grab_set()
        x = self.root.winfo_x() + (self.root.winfo_width() - 520) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 380) // 2
        dlg.geometry(f"+{x}+{y}")

        f = ttk.Frame(dlg, padding=20)
        f.pack(fill=BOTH, expand=True)
        s = self.index_mgr.get_stats()

        ttk.Label(f, text="📊 索引状态信息", font=("微软雅黑", 14, "bold")).pack(anchor=W)
        ttk.Separator(f).pack(fill=X, pady=10)

        info = ttk.Frame(f)
        info.pack(fill=X, pady=5)
        rows = [
            ("📌 索引文件数量：", f"{s['count']:,}" if s['count'] else "未构建"),
            ("📌 索引状态：", "✅ 就绪" if s['ready'] else ("🔄 构建中" if s['building'] else "❌ 未构建")),
            ("📌 上次构建时间：", datetime.datetime.fromtimestamp(s['time']).strftime('%Y-%m-%d %H:%M') if s['time'] else "从未构建"),
            ("📌 当前索引路径：", s['path']),
        ]
        for i, (l, v) in enumerate(rows):
            ttk.Label(info, text=l, font=("微软雅黑", 10)).grid(row=i, column=0, sticky=W, pady=3)
            ttk.Label(info, text=v, font=("微软雅黑", 10), foreground="#555").grid(row=i, column=1, sticky=W, padx=10)

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

        ttk.Button(f, text="📁 更改索引位置", command=browse, bootstyle="secondary", width=18).pack(anchor=W, pady=(15, 0))
        ttk.Separator(f).pack(fill=X, pady=15)

        bf = ttk.Frame(f)
        bf.pack(fill=X)

        def rebuild():
            dlg.destroy()
            self._build_index()

        def delete():
            if messagebox.askyesno("确认", "确定删除索引？"):
                self.file_watcher.stop()
                self.index_mgr.close()
                for ext in ['', '-wal', '-shm']:
                    try:
                        os.remove(self.index_mgr.db_path + ext)
                    except:
                        pass
                self.index_mgr = IndexManager(db_path=self.index_mgr.db_path)
                self.file_watcher = FileWatcher(self.index_mgr)
                self._check_index()
                dlg.destroy()

        ttk.Button(bf, text="🔄 重建索引", command=rebuild, bootstyle="primary", width=14).pack(side=LEFT, padx=5)
        ttk.Button(bf, text="🗑️ 删除索引", command=delete, bootstyle="danger-outline", width=14).pack(side=LEFT)
        ttk.Button(bf, text="❌ 关闭", command=dlg.destroy, bootstyle="secondary", width=12).pack(side=RIGHT)

    def _build_index(self):
        if self.index_mgr.is_building:
            return
        self.index_build_stop = False

        def run():
            self.index_mgr.build_index(
                self._get_drives(),
                progress_cb=lambda c, p: self.result_queue.put(("IDX_PROG", (c, p))),
                stop_fn=lambda: self.index_build_stop
            )
            self.result_queue.put(("IDX_DONE", None))

        threading.Thread(target=run, daemon=True).start()
        self._check_index()

    # ===== 双击/右键 =====
    def on_dblclick(self, e):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid not in self.item_meta:
            return
        idx = self.item_meta[iid]
        if idx >= len(self.filtered_results):
            return
        item = self.filtered_results[idx]
        if item['type_code'] == 0:
            subprocess.Popen(f'explorer "{item["fullpath"]}"')
        else:
            try:
                os.startfile(item['fullpath'])
            except Exception as ex:
                messagebox.showerror("错误", str(ex))

    def show_menu(self, e):
        item = self.tree.identify_row(e.y)
        if item:
            self.tree.selection_set(item)
            self.ctx_menu.post(e.x_root, e.y_root)

    def _get_sel(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid not in self.item_meta:
            return None
        idx = self.item_meta[iid]
        return self.filtered_results[idx] if idx < len(self.filtered_results) else None

    def open_file(self):
        item = self._get_sel()
        if item:
            try:
                os.startfile(item['fullpath'])
            except Exception as e:
                messagebox.showerror("错误", str(e))

    def open_folder(self):
        item = self._get_sel()
        if item:
            subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')

    def copy_path(self):
        item = self._get_sel()
        if item:
            self.root.clipboard_clear()
            self.root.clipboard_append(item['fullpath'])

    def copy_file(self):
        if not HAS_WIN32:
            return
        item = self._get_sel()
        if item:
            try:
                data = struct.pack('IIIII', 20, 0, 0, 0, 1) + (os.path.abspath(item['fullpath']) + '\0').encode('utf-16le') + b'\0'
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
                win32clipboard.CloseClipboard()
            except:
                pass

    def delete_file(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid not in self.item_meta:
            return
        idx = self.item_meta[iid]
        if idx >= len(self.filtered_results):
            return
        item = self.filtered_results[idx]
        msg = f"确定删除?\n\n{item['filename']}\n\n⚠️ 不可恢复！"
        if not messagebox.askyesno("确认删除", msg, icon='warning'):
            return
        try:
            if os.path.isdir(item['fullpath']):
                shutil.rmtree(item['fullpath'])
            else:
                os.remove(item['fullpath'])
            self.tree.delete(iid)
            self.shown_paths.discard(item['fullpath'])
            self.status.set(f"✅ 已删除: {item['filename']}")
        except Exception as e:
            messagebox.showerror("删除失败", str(e))

    def _on_close(self):
        self.index_build_stop = True
        self.stop_event = True
        self.file_watcher.stop()
        self.index_mgr.close()
        self.root.destroy()


if __name__ == "__main__":
    if platform.system() == 'Windows':
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass

    root = ttk.Window(themename="flatly")
    app = SearchApp(root)
    root.mainloop()