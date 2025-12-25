"""
Search worker threads extracted from legacy implementation.
"""

import os
import queue
import re
import threading
import time
import logging

from PySide6.QtCore import QThread, Signal

from ..constants import ARCHIVE_EXTS
from ..utils import format_size, format_time, should_skip_path, should_skip_dir
from ..utils import compile_search_predicate
from .rust_search import get_rust_search_engine
from .search_syntax import SearchSyntaxParser

logger = logging.getLogger(__name__)


class IndexSearchWorker(QThread):
    """索引搜索工作线程"""

    batch_ready = Signal(list)
    finished = Signal(float)
    error = Signal(str)

    def __init__(self, index_mgr, keyword, scope_targets, regex_mode):
        super().__init__()
        self.index_mgr = index_mgr
        self.keyword_str = keyword
        self.keywords = keyword
        self.scope_targets = scope_targets
        self.regex_mode = regex_mode
        self.predicate = None
        try:
            ks = self.keyword_str or ""
            if any(c in ks for c in "()|!") or "re:" in ks or "*" in ks or "?" in ks:
                self.predicate = compile_search_predicate(ks)
        except Exception:
            self.predicate = None
        self.stopped = False

    def stop(self):
        self.stopped = True

    def _match(self, filename):
        if self.predicate is not None:
            try:
                return bool(self.predicate(filename))
            except Exception:
                return False
        if self.regex_mode:
            try:
                return re.search(self.keyword_str, filename, re.IGNORECASE)
            except re.error:
                return False

        keywords = [kw for kw in self.keyword_str.lower().split() if ":" not in kw]
        if not keywords:
            return True

        return all(kw in filename.lower() for kw in keywords)

    def run(self):
        start_time = time.time()
        try:
            parser = SearchSyntaxParser()
            pure_keyword, filters = parser.parse(self.keyword_str)

            rust_engine = get_rust_search_engine()
            if not rust_engine:
                self.error.emit("❌ Rust 搜索引擎不可用")
                return

            if not self.scope_targets:
                self.error.emit("❌ 未指定搜索范围")
                return

            drives = set()
            for target in self.scope_targets:
                if len(target) >= 2 and target[1] == ":":
                    drives.add(target[0].upper())

            if not drives:
                self.error.emit("❌ 无法识别驱动器")
                return

            # 预先确保所有驱动器的索引已加载/初始化
            for drive in sorted(drives):
                if self.stopped:
                    return
                if drive not in rust_engine.initialized_drives:
                    logger.info(f"🔍 正在加载/构建 {drive}: 盘索引...")
                    if not rust_engine.load_index(drive):
                        logger.info(f"📊 首次搜索 {drive}: 盘，正在构建索引（可能需要10-60秒）...")
                        if not rust_engine.init_index(drive):
                            self.error.emit(f"❌ 无法初始化 {drive}: 盘索引")
                            return
                    logger.info(f"✅ {drive}: 盘索引就绪")

            keyword = (pure_keyword or "").strip().lower()
            has_filters = bool(filters) and any(filters.values())

            # 仅过滤条件（如 dm:7d）走流式，避免一次性构建/补元数据卡死
            if (not keyword) and has_filters:
                ext_filters = filters.get("ext") if isinstance(filters, dict) else None
                exts = [e for e in (ext_filters or []) if e]
                # 如果包含日期过滤，优先使用 Rust 端按时间范围搜索，避免前缀枚举
                date_after = None
                try:
                    da = filters.get("date_after") if isinstance(filters, dict) else None
                    if da:
                        import datetime as _dt
                        if isinstance(da, _dt.datetime):
                            date_after = da.timestamp()
                        elif isinstance(da, (int, float)):
                            date_after = float(da)
                except Exception:
                    date_after = None
                prefixes = [
                    "a","b","c","d","e","f","g","h","i","j","k","l","m",
                    "n","o","p","q","r","s","t","u","v","w","x","y","z",
                    "0","1","2","3","4","5","6","7","8","9","_",
                ]

                batch = []
                for drive in sorted(drives):
                    if self.stopped:
                        return
                    try:
                        if date_after is not None and not exts:
                            # 直接按时间范围搜索并流式输出
                            part = rust_engine.search_by_mtime_range(drive, date_after, 4.611686e18, 150000)
                            for r in part:
                                fn, fp, sz, is_dir, mt = r[0], r[1], r[2], r[3], r[4]
                                extname = os.path.splitext(fn)[1].lower()
                                tc = 0 if is_dir else (1 if extname in ARCHIVE_EXTS else 2)
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
                        elif exts:
                            for ext in exts:
                                if self.stopped:
                                    return
                                part = rust_engine.search_by_ext(drive, ext, 20000)
                                part = rust_engine.apply_filters_to_results(part, filters)
                                for r in part:
                                    fn, fp, sz, is_dir, mt = r[0], r[1], r[2], r[3], r[4]
                                    extname = os.path.splitext(fn)[1].lower()
                                    tc = 0 if is_dir else (1 if extname in ARCHIVE_EXTS else 2)
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
                        else:
                            for pref in prefixes:
                                if self.stopped:
                                    return
                                part = rust_engine.search_prefix(drive, pref, 5000)
                                part = rust_engine.apply_filters_to_results(part, filters)
                                for r in part:
                                    fn, fp, sz, is_dir, mt = r[0], r[1], r[2], r[3], r[4]
                                    extname = os.path.splitext(fn)[1].lower()
                                    tc = 0 if is_dir else (1 if extname in ARCHIVE_EXTS else 2)
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
                    except Exception as e:
                        logger.error("❌ Rust 流式搜索异常(%s): %s", drive, e)
                        self.error.emit(f"搜索失败: {e}")
                        return

                if batch:
                    self.batch_ready.emit(list(batch))
                self.finished.emit(time.time() - start_time)
                return

            # 普通搜索：按盘逐个搜索并持续输出
            if (not keyword) and (not has_filters):
                return

            batch = []
            try:
                for drive in sorted(drives):
                    if self.stopped:
                        return
                    drive_results = rust_engine.search_with_filters(drive, keyword, filters)
                    if not drive_results:
                        continue
                    for r in drive_results:
                        fn, fp, sz, is_dir, mt = r[0], r[1], r[2], r[3], r[4]
                        extname = os.path.splitext(fn)[1].lower()
                        tc = 0 if is_dir else (1 if extname in ARCHIVE_EXTS else 2)
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
            except Exception as e:
                logger.error("❌ Rust 搜索异常: %s", e)
                self.error.emit(f"搜索失败: {e}")
                return

            if batch:
                self.batch_ready.emit(list(batch))
            self.finished.emit(time.time() - start_time)
        except Exception as e:
            logger.error("索引搜索线程错误: %s", e)
            self.error.emit(str(e))


class RealtimeSearchWorker(QThread):
    """实时搜索工作线程"""

    batch_ready = Signal(list)
    progress = Signal(int, float)
    finished = Signal(float)
    error = Signal(str)

    def __init__(self, keyword, scope_targets, regex_mode):
        super().__init__()
        self.keyword_str = keyword
        self.keywords = keyword.lower().split()
        self.scope_targets = scope_targets
        self.regex_mode = regex_mode
        self.stopped = False
        self.is_paused = False

        self.predicate = None
        try:
            ks = self.keyword_str or ""
            if any(c in ks for c in "()|!") or "re:" in ks or "*" in ks or "?" in ks:
                from ..utils import compile_search_predicate as _csp
                self.predicate = _csp(ks)
        except Exception:
            self.predicate = None

    def stop(self):
        self.stopped = True

    def toggle_pause(self, paused):
        self.is_paused = paused

    def _match(self, filename):
        if self.predicate is not None:
            try:
                return bool(self.predicate(filename))
            except Exception:
                return False
        if self.regex_mode:
            try:
                return re.search(self.keyword_str, filename, re.IGNORECASE)
            except re.error:
                return False
        return all(kw in filename.lower() for kw in self.keywords)

    def run(self):
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
                                    extname = os.path.splitext(e.name)[1].lower()
                                    tc = 0 if is_dir else (1 if extname in ARCHIVE_EXTS else 2)
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
                                                else ("📦 压缩包" if tc == 1 else format_size(st.st_size))
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
                                    speed = scanned_dirs[0] / elapsed if elapsed > 0 else 0
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
            logger.error("实时搜索线程错误: %s", e)
            self.error.emit(str(e))


__all__ = ["IndexSearchWorker", "RealtimeSearchWorker"]
