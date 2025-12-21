"""
USN journal watcher extracted from legacy implementation.
"""

import ctypes
import logging
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..constants import SKIP_EXTS
from ..utils import should_skip_path, should_skip_dir, get_c_scan_dirs
from .rust_engine import HAS_RUST_ENGINE, RUST_ENGINE
from .dependencies import HAS_APSW

logger = logging.getLogger(__name__)


def _norm_path(path: str) -> str:
	"""Normalize paths to match DB format."""
	norm = os.path.normpath(path)
	if len(norm) > 3 and norm.endswith(os.sep):
		norm = norm.rstrip(os.sep)
	return norm


def _dir_cache_file(drive_letter: str) -> str:
	"""DIR_CACHE persistence file path (per drive)."""
	base = Path(os.getenv("LOCALAPPDATA", ".")) / "SearchTool" / "dir_cache"
	base.mkdir(parents=True, exist_ok=True)
	return str(base / f"dir_cache_{drive_letter.upper()}.bin")


class UsnFileWatcher(QObject):
	"""USN Journal 文件监控器 - 高性能 Windows 原生方案"""

	files_changed = Signal(int, int, list)

	def __init__(self, index_mgr, config_mgr=None):
		super().__init__()
		self.index_mgr = index_mgr
		self.config_mgr = config_mgr
		self.running = False
		self.stop_flag = False
		self.thread = None
		self.usn_positions = {}
		self.drives = []
		self._setup_ffi()

	def _setup_ffi(self):
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
		if not self.running or self.stop_flag:
			return
		if self.index_mgr.is_building:
			return

		for drive in list(self.usn_positions.keys()):
			if self.stop_flag:
				break
			self._check_drive(drive)

	def _poll_loop(self):
		idle_count = 0
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

				if has_changes:
					idle_count = 0
					sleep_time = 0.1
				else:
					idle_count += 1
					sleep_time = min(2.0, 0.2 * (1.3 ** min(idle_count, 10)))

			except Exception as e:
				logger.error(f"[USN监控] 轮询错误: {e}")
				sleep_time = 1.0

			steps = max(1, int(sleep_time / 0.1))
			for _ in range(steps):
				if self.stop_flag:
					break
				time.sleep(0.1)

	def _check_drive(self, drive):
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

			self.usn_positions[drive] = current_usn
			return has_changes

		except Exception as e:
			logger.error(f"[USN监控] {drive} 失败: {e}")
			return False

	def _apply_changes(self, changes):
		if not changes or not self.index_mgr.conn:
			return
		if self.index_mgr.is_building:
			return

		inserts = []
		deletes = []

		c_allowed = get_c_scan_dirs(self.config_mgr)
		c_allowed_lower = [p.lower() for p in c_allowed] if c_allowed else []

		for action, path, is_dir in changes:
			path = _norm_path(path)

			if path.upper().startswith("C:"):
				path_lower = path.lower()
				in_allowed = any(path_lower.startswith(ap.lower()) for ap in c_allowed_lower)
				if not in_allowed:
					continue

			name = os.path.basename(path)
			if not name or name.startswith((".", "$")):
				continue

			if action in (0, 4):
				deletes.append(path)
				continue

			if action in (1, 2, 3):
				if should_skip_path(path.lower()):
					continue
				try:
					if os.path.exists(path):
						if is_dir:
							if not should_skip_dir(name.lower(), path.lower()):
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
								extra = self._scan_dir_records(path)
								if extra:
									inserts.extend(extra)
						else:
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

				if deletes:
					for d in deletes:
						nd = _norm_path(d)
						nd_slash = nd + os.sep

						cursor.execute(
							"DELETE FROM files WHERE full_path = ? OR full_path = ?",
							(nd, nd_slash),
						)

						cursor.execute(
							"DELETE FROM files WHERE full_path LIKE ?",
							(nd_slash + "%",),
						)

				if inserts:
					cursor.executemany(
						"INSERT OR REPLACE INTO files VALUES(NULL,?,?,?,?,?,?,?,?)",
						inserts,
					)

				if not HAS_APSW:
					self.index_mgr.conn.commit()

			self.index_mgr.force_reload_stats()

			logger.info(f"[USN监控] 更新完成: +{len(inserts)} -{len(deletes)}")
			self.files_changed.emit(len(inserts), len(deletes), list(deletes))

		except Exception as e:
			logger.error(f"[USN监控] 数据库更新失败: {e}")

	def _scan_dir_records(self, root_path, max_items=200000, max_depth=15, max_seconds=0.5):
		records = []
		stack = [(root_path, 0)]
		start_time = time.time()

		while stack and len(records) < max_items:
			if time.time() - start_time > max_seconds:
				logger.debug(f"[补扫] 超时退出: {root_path}, 已扫描 {len(records)} 条")
				break

			cur, depth = stack.pop()
			if depth > max_depth:
				continue

			cur_lower = cur.lower()
			if should_skip_path(cur_lower):
				continue

			try:
				with os.scandir(cur) as it:
					for e in it:
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

		if records:
			logger.debug(f"[补扫] {root_path}: {len(records)} 条, 耗时 {time.time() - start_time:.2f}s")

		return records

	def stop(self):
		self.stop_flag = True
		if self.thread and self.thread.is_alive():
			self.thread.join(timeout=3)
		self.running = False
		self.usn_positions.clear()
		logger.info("[USN监控] 已停止")


__all__ = ["UsnFileWatcher", "_norm_path", "_dir_cache_file"]
