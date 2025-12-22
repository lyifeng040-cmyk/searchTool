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
from ..utils import fuzzy_match, format_size, format_time, should_skip_path, should_skip_dir

logger = logging.getLogger(__name__)


class IndexSearchWorker(QThread):
	"""索引搜索工作线程"""

	batch_ready = Signal(list)
	finished = Signal(float)
	error = Signal(str)

	def __init__(self, index_mgr, keyword, scope_targets, regex_mode, fuzzy_mode):
		super().__init__()
		self.index_mgr = index_mgr
		self.keyword_str = keyword
		self.keywords = keyword
		self.scope_targets = scope_targets
		self.regex_mode = regex_mode
		self.fuzzy_mode = fuzzy_mode
		self.stopped = False

	def stop(self):
		self.stopped = True

	def _match(self, filename):
		if self.regex_mode:
			try:
				return re.search(self.keyword_str, filename, re.IGNORECASE)
			except re.error:
				return False

		keywords = [kw for kw in self.keyword_str.lower().split() if ':' not in kw]
		if not keywords:
			return True

		if self.fuzzy_mode:
			return all(fuzzy_match(kw, filename) >= 50 for kw in keywords)
		return all(kw in filename.lower() for kw in keywords)

	def run(self):
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
		if self.regex_mode:
			try:
				return re.search(self.keyword_str, filename, re.IGNORECASE)
			except re.error:
				return False
		if self.fuzzy_mode:
			return all(fuzzy_match(kw, filename) >= 50 for kw in self.keywords)
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
									ext = os.path.splitext(e.name)[1].lower()
									tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
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
			logger.error(f"实时搜索线程错误: {e}")
			self.error.emit(str(e))


__all__ = ["IndexSearchWorker", "RealtimeSearchWorker"]
