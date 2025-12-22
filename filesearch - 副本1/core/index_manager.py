"""
Index manager extracted from legacy single-file implementation.
"""

import os
import time
import threading
import concurrent.futures
from collections import deque
from pathlib import Path
import logging

from PySide6.QtCore import QObject, Signal

from ..constants import (
    LOG_DIR,
    IS_WINDOWS,
    SKIP_DIRS_LOWER,
    SKIP_EXTS,
)
from ..utils import should_skip_path, should_skip_dir, get_c_scan_dirs
from ..utils import format_size, format_time, fuzzy_match  # noqa: F401 (used indirectly)
from .dependencies import HAS_APSW, get_db_module
from .mft_scanner import enum_volume_files_mft

logger = logging.getLogger(__name__)

db_module = get_db_module()


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
        self.last_build_duration = None
        self.has_fts = False
        self.used_mft = False

        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        try:
            if HAS_APSW:
                self.conn = db_module.Connection(self.db_path)
            else:
                self.conn = db_module.connect(self.db_path, check_same_thread=False)

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

                time_row = list(cursor.execute("SELECT value FROM meta WHERE key='build_time'"))
                if time_row and time_row[0][0]:
                    try:
                        self.last_build_time = float(time_row[0][0])
                    except (ValueError, TypeError):
                        self.last_build_time = None
                else:
                    self.last_build_time = None

                dur_row = list(cursor.execute("SELECT value FROM meta WHERE key='build_duration'"))
                if dur_row and dur_row[0][0]:
                    try:
                        self.last_build_duration = float(dur_row[0][0])
                    except (ValueError, TypeError):
                        self.last_build_duration = None
                else:
                    self.last_build_duration = None

                if self.last_build_time in (None, 0):
                    # 回退：若未写入 meta，则使用索引库文件的修改时间
                    try:
                        if os.path.exists(self.db_path):
                            self.last_build_time = os.path.getmtime(self.db_path)
                    except Exception:
                        pass

                if not preserve_mft:
                    mft_row = list(cursor.execute("SELECT value FROM meta WHERE key='used_mft'"))
                    self.used_mft = bool(mft_row and mft_row[0][0] == "1")

            self.is_ready = self.file_count > 0
        except Exception as e:
            logger.error(f"加载统计信息失败: {e}")
            self.file_count = 0
            self.is_ready = False

    def reload_stats(self):
        if not self.is_building:
            self._load_stats(preserve_mft=True)

    def force_reload_stats(self):
        self._load_stats(preserve_mft=True)

    def close(self):
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
        if not self.conn or not self.is_ready:
            return None

        try:
            with self.lock:
                cursor = self.conn.cursor()
                keyword_str = ' '.join(keywords) if isinstance(keywords, list) else keywords
                parsed_keywords, filters = self._parse_search_syntax(keyword_str)

                if not parsed_keywords and not any([
                    filters['ext'], filters['size_min'], filters['size_max'],
                    filters['dm_after'], filters['type'], filters['path']
                ]):
                    return []

                conditions = []
                params = []
                for kw in parsed_keywords:
                    conditions.append("filename_lower LIKE ?")
                    params.append(f"%{kw}%")

                if filters['ext']:
                    conditions.append("extension = ?")
                    params.append(filters['ext'])

                if filters['type'] == 'folder':
                    conditions.append("is_dir = 1")
                elif filters['type'] == 'file':
                    conditions.append("is_dir = 0")

                if filters['size_min'] > 0:
                    conditions.append("size > ?")
                    params.append(filters['size_min'])
                if filters['size_max'] > 0:
                    conditions.append("size < ?")
                    params.append(filters['size_max'])

                if filters['dm_after'] > 0:
                    conditions.append("(mtime >= ? OR mtime = 0)")
                    params.append(filters['dm_after'])

                where_clause = " AND ".join(conditions) if conditions else "1=1"

                sql = f"""
                    SELECT filename, full_path, size, mtime, is_dir
                    FROM files
                    WHERE {where_clause}
                    LIMIT ?
                """
                params.append(limit)

                raw_results = list(cursor.execute(sql, tuple(params)))

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

                    if filters['path'] and filters['path'] not in path_lower:
                        continue

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

            if filtered and filters.get('dm_after', 0) > 0:
                needs_fix_count = sum(1 for item in filtered if item[3] == 0)
                if needs_fix_count > 0:
                    logger.info(f"⚠️ 发现 {needs_fix_count} 个文件 mtime=0，使用快速补齐（多线程）...")
                    start_time = time.time()
                    max_fix = 10000
                    needs_fix = [(i, item[1]) for i, item in enumerate(filtered) if item[3] == 0][:max_fix]

                    def get_mtime_fast(idx_path):
                        idx, fpath = idx_path
                        try:
                            return (idx, os.stat(fpath).st_mtime, fpath)
                        except Exception:
                            return (idx, 0, fpath)

                    fixed_items = {}
                    db_updates = []

                    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                        for idx, mt, fpath in executor.map(get_mtime_fast, needs_fix):
                            fixed_items[idx] = mt
                            if mt > 0:
                                db_updates.append((mt, fpath))

                    new_filtered = []
                    for i, (fn, fp, sz, mt, is_dir) in enumerate(filtered):
                        if i in fixed_items:
                            mt = fixed_items[i]
                        if mt > 0 and mt >= filters['dm_after']:
                            new_filtered.append((fn, fp, sz, mt, is_dir))

                    filtered = new_filtered
                    elapsed = time.time() - start_time
                    logger.info(f"✅ 补齐完成: {len(needs_fix)} 个文件，耗时 {elapsed:.2f}s，剩余 {len(filtered)} 个")

                    if db_updates:
                        def update_db():
                            try:
                                with self.lock:
                                    cursor = self.conn.cursor()
                                    cursor.executemany("UPDATE files SET mtime=? WHERE full_path=?", db_updates)
                                    if not HAS_APSW:
                                        self.conn.commit()
                                logger.info(f"📝 已缓存 {len(db_updates)} 个文件的 mtime 到数据库")
                            except Exception as e:
                                logger.debug(f"数据库更新失败: {e}")

                        threading.Thread(target=update_db, daemon=True).start()

            return filtered

        except Exception as e:
            logger.error(f"搜索错误: {e}")
            return None

    def _search_like(self, cursor, keywords, limit):
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
        self._load_stats(preserve_mft=True)
        return {
            "count": self.file_count,
            "ready": self.is_ready,
            "building": self.is_building,
            "time": self.last_build_time,
            "duration": self.last_build_duration,
            "path": self.db_path,
            "has_fts": self.has_fts,
            "used_mft": self.used_mft,
        }

    def build_index(self, drives, stop_fn=None):
        from . import mft_scanner  # avoid circular

        if not self.conn or self.is_building:
            return

        self.is_building = True
        self.is_ready = False
        self.used_mft = False
        mft_scanner.MFT_AVAILABLE = False
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

            if all_data:
                self.progress_signal.emit(len(all_data), "阶段3/5: 写入数据库...")
                write_start = time.time()

                with self.lock:
                    cursor = self.conn.cursor()
                    cursor.execute("PRAGMA synchronous=OFF")
                    cursor.execute("PRAGMA journal_mode=MEMORY")
                    cursor.execute("PRAGMA locking_mode=EXCLUSIVE")
                    cursor.execute("PRAGMA temp_store=MEMORY")
                    cursor.execute("PRAGMA cache_size=-500000")
                    cursor.execute("PRAGMA mmap_size=268435456")

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

            self.progress_signal.emit(self.file_count, "阶段4/5: 创建索引...")
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_fn ON files(filename_lower)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_parent ON files(parent_dir)")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA journal_mode=WAL")
                now_ts = time.time()
                cursor.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_time', ?)",
                    (str(now_ts),),
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_duration', ?)",
                    (str(time.time() - build_start),),
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('used_mft', ?)",
                    ("1" if self.used_mft else "0",),
                )
                if not HAS_APSW:
                    self.conn.commit()

            logger.info(f"✅ 阶段4完成: {time.time() - build_start:.2f}s")

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
                        cursor.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
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

            for drv in failed_drives:
                if stop_fn and stop_fn():
                    break
                paths_to_scan = c_allowed_paths if drv == "C" else [f"{drv}:\\"]
                for path in paths_to_scan:
                    logger.info(f"[传统扫描] {path}")
                    self._scan_dir(path, c_allowed_paths if drv == "C" else None, stop_fn)

            try:
                with self.lock:
                    cursor = self.conn.cursor()
                    final_count = list(cursor.execute("SELECT COUNT(*) FROM files"))[0][0]
                    self.file_count = final_count
            except Exception:
                pass

            total_time = time.time() - build_start
            logger.info(f"✅ 索引构建完成: {self.file_count:,} 条, 总耗时 {total_time:.2f}s")
            self.is_ready = self.file_count > 0
            self.build_finished_signal.emit()

        except Exception as e:
            import traceback
            logger.error(f"❌ 构建错误: {e}")
            traceback.print_exc()
        finally:
            self.is_building = False

    def _scan_dir(self, target, allowed_paths=None, stop_fn=None):
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
                            if should_skip_dir(e.name.lower(), path_lower, allowed_paths_lower):
                                continue
                            stack.append(e.path)
                            batch.append((e.name, e.name.lower(), e.path, cur, "", 0, st.st_mtime, 1))
                        else:
                            ext = os.path.splitext(e.name)[1].lower()
                            if ext in SKIP_EXTS:
                                continue
                            batch.append((e.name, e.name.lower(), e.path, cur, ext, st.st_size, st.st_mtime, 0))

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
        if not self.conn:
            return
        if self.is_building:
            logger.warning("索引正在构建中，跳过")
            return

        self.is_building = True
        drive = drive_letter.upper().rstrip(":\\")
        try:
            logger.info(f"开始重建 {drive}: 盘索引...")
            with self.lock:
                cursor = self.conn.cursor()
                cursor.execute("DELETE FROM files WHERE full_path LIKE ?", (f"{drive}:%",))
                if not HAS_APSW:
                    self.conn.commit()

            c_allowed_paths = get_c_scan_dirs(self.config_mgr)
            allowed_paths = c_allowed_paths if drive == 'C' else None

            try:
                data = enum_volume_files_mft(drive, SKIP_DIRS_LOWER, SKIP_EXTS, allowed_paths)
                if data:
                    logger.info(f"开始写入 {len(data)} 条记录...")
                    write_start = time.time()
                    with self.lock:
                        cursor = self.conn.cursor()
                        cursor.execute("PRAGMA synchronous=OFF")
                        cursor.execute("PRAGMA journal_mode=OFF")
                        cursor.execute("PRAGMA locking_mode=EXCLUSIVE")
                        cursor.execute("PRAGMA temp_store=MEMORY")
                        cursor.execute("PRAGMA cache_size=-500000")

                        if HAS_APSW:
                            with self.conn:
                                cursor.executemany(
                                    "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                                    data
                                )
                                now_ts = time.time()
                                cursor.execute(
                                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_time', ?)",
                                    (str(now_ts),)
                                )
                                cursor.execute(
                                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_duration', ?)",
                                    (str(time.time() - build_start),)
                                )
                                cursor.execute(
                                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('used_mft', '1')"
                                )
                        else:
                            cursor.execute("BEGIN TRANSACTION")
                            cursor.executemany(
                                "INSERT OR IGNORE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
                                data
                            )
                            now_ts = time.time()
                            cursor.execute(
                                "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_time', ?)",
                                (str(now_ts),)
                            )
                            cursor.execute(
                                "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_duration', ?)",
                                (str(time.time() - build_start),)
                            )
                            cursor.execute(
                                "INSERT OR REPLACE INTO meta (key, value) VALUES ('used_mft', '1')"
                            )
                            cursor.execute("COMMIT")

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

    def _parse_search_syntax(self, keyword_str):
        import re

        keywords = []
        filters = {
            'ext': None,
            'size_min': 0,
            'size_max': 0,
            'dm_after': 0,
            'type': None,
            'path': None,
        }

        tokens = keyword_str.split()
        for token in tokens:
            token_lower = token.lower()
            if token_lower.startswith('ext:'):
                ext = token[4:].strip()
                if ext and not ext.startswith('.'):
                    ext = '.' + ext
                filters['ext'] = ext.lower()
                continue
            if token_lower.startswith('size:'):
                size_part = token[5:].strip().lower()
                match = re.match(r'([<>])(\d+)(kb|mb|gb)?', size_part)
                if match:
                    op, num, unit = match.groups()
                    num = int(num)
                    multiplier = {'kb': 1024, 'mb': 1024**2, 'gb': 1024**3}.get(unit, 1)
                    size_bytes = num * multiplier
                    if op == '>':
                        filters['size_min'] = size_bytes
                    else:
                        filters['size_max'] = size_bytes
                continue
            if token_lower.startswith('dm:'):
                dm_part = token[3:].strip().lower()
                now = time.time()
                day = 86400
                if dm_part == 'today':
                    import datetime
                    today_start = datetime.datetime.now().replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ).timestamp()
                    filters['dm_after'] = today_start
                elif dm_part.endswith('d') and dm_part[:-1].isdigit():
                    days = int(dm_part[:-1])
                    filters['dm_after'] = now - (days * day)
                elif dm_part.endswith('h') and dm_part[:-1].isdigit():
                    hours = int(dm_part[:-1])
                    filters['dm_after'] = now - (hours * 3600)
                continue
            if token_lower.startswith('folder:'):
                filters['type'] = 'folder'
                rest = token[7:].strip()
                if rest:
                    keywords.append(rest.lower())
                continue
            if token_lower.startswith('file:'):
                filters['type'] = 'file'
                rest = token[5:].strip()
                if rest:
                    keywords.append(rest.lower())
                continue
            if token_lower.startswith('path:'):
                path_part = token[5:].strip()
                if path_part:
                    filters['path'] = path_part.lower()
                continue
            keywords.append(token.lower())

        return keywords, filters


__all__ = ["IndexManager"]
