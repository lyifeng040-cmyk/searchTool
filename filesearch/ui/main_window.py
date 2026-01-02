"""
Main window (SearchApp) migrated from legacy monolith.
"""

import ctypes
import datetime
import logging
import math
import os
import shutil
import string
import struct
import subprocess
import sys
import threading
import time
from typing import List

from PySide6.QtCore import QEvent, Qt, QTimer, QSettings, QRectF
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
	QApplication,
	QAbstractItemView,
	QCheckBox,
	QComboBox,
	QDialog,
	QFrame,
	QGridLayout,
	QHeaderView,
	QLabel,
	QLineEdit,
	QMainWindow,
	QMenu,
	QProgressBar,
	QPushButton,
	QStatusBar,
	QTextEdit,
	QTreeWidget,
	QTreeWidgetItem,
	QVBoxLayout,
	QHBoxLayout,
	QWidget,
	QFileDialog,
	QMessageBox,
	QStyledItemDelegate,
	QStyle,
)
import html
import re

from filesearch.core.search_syntax import SearchSyntaxParser

from ..config import ConfigManager
from ..constants import IS_WINDOWS, SKIP_DIRS_LOWER
from ..dependencies import HAS_APSW, HAS_SEND2TRASH, HAS_WIN32
from ..utils import (
	apply_theme,
	format_size,
	format_time,
	get_c_scan_dirs,
	parse_search_scope,
)
from ..core.index_manager import IndexManager
from ..core.mft_scanner import _batch_stat_files
from ..core.file_watcher import UsnFileWatcher, _dir_cache_file
from ..core.rust_engine import HAS_RUST_ENGINE, RUST_ENGINE, FileInfo
from ..core.search_workers import IndexSearchWorker, RealtimeSearchWorker
from .components.search_logic import create_worker
from .components.file_operations import (
	open_file as fo_open_file,
	open_folder_and_select as fo_open_folder,
	copy_paths_to_clipboard as fo_copy_paths,
	copy_files_to_clipboard_win32 as fo_copy_files_win32,
	delete_items as fo_delete_items,
)
from .components.ui_builder import build_menubar, build_ui, bind_shortcuts
from .tray_manager import TrayManager
from .hotkey_manager import HotkeyManager
from .mini_search import MiniSearchWindow
from .dialogs.cdrive_settings import CDriveSettingsDialog
from .dialogs.batch_rename import BatchRenameDialog
from .dialogs.tag_manager_dialog import TagManagerDialog
# component helpers moved incrementally; keep original methods in this file for now

logger = logging.getLogger(__name__)

if HAS_WIN32:
	try:  # noqa: SIM105
		import win32clipboard  # type: ignore
		import win32con  # type: ignore
	except Exception:  # noqa: BLE001
		win32clipboard = None
		win32con = None
else:
	win32clipboard = None
	win32con = None

if HAS_SEND2TRASH:
	try:  # noqa: SIM105
		import send2trash  # type: ignore
	except Exception:  # noqa: BLE001
		send2trash = None
else:
	send2trash = None


class MainHighlightDelegate(QStyledItemDelegate):
	"""Delegate for main window file-name column highlighting."""

	def __init__(self, app=None):
		super().__init__(app)
		self._pattern = None
		self.app = app

	def set_keywords(self, keywords):
		terms = [kw for kw in keywords if kw]
		if terms:
			joined = "|".join(re.escape(term) for term in terms)
			self._pattern = re.compile(joined, re.IGNORECASE)
		else:
			self._pattern = None
		# 设置关键词模式（调试信息已移除）

	def paint(self, painter, option, index):
		painter.save()
		# background
		bg_brush = index.data(Qt.BackgroundRole)
		if bg_brush:
			painter.fillRect(option.rect, bg_brush)
		elif option.state & QStyle.State_Selected:
			painter.fillRect(option.rect, option.palette.highlight())
		else:
			painter.fillRect(option.rect, option.palette.base())

		text = index.data(Qt.DisplayRole) or ""
		doc = QTextDocument()
		doc.setDefaultFont(option.font)
		doc.setDocumentMargin(0)
		doc.setHtml(self._build_html(text, option))
		doc.setTextWidth(option.rect.width())
		painter.translate(option.rect.topLeft())
		doc.drawContents(painter, QRectF(0, 0, option.rect.width(), option.rect.height()))
		painter.restore()

	def _build_html(self, text, option):
		escaped = html.escape(text)
		if not self._pattern:
			return f"<div style=\"color:{option.palette.text().color().name()}\">{escaped}</div>"
		m = self._pattern.search(escaped)
		# 为高亮选择合适的前景色，选中时使用 highlightedText 并用深色背景以提升对比度
		is_selected = bool(option.state & QStyle.State_Selected)
		text_color = (
			option.palette.highlightedText().color().name()
			if is_selected
			else option.palette.text().color().name()
		)
		# 更明显的高亮样式：加粗、内边距、圆角和细边框
		highlight_bg = "#ff6f00" if is_selected else "#ff9800"
		span_style = (
			f"background-color:{highlight_bg};color:{text_color};"
			"font-weight:600;padding:0 4px;border-radius:3px;"
			"border:1px solid rgba(0,0,0,0.28);"
		)
		highlighted = self._pattern.sub(
			lambda m: f'<span style="{span_style}">{m.group(0)}</span>',
			escaped,
		)
		return f'<div style="color:{text_color};">{highlighted}</div>'



class SearchApp(QMainWindow):
	"""主应用程序窗口"""

	def __init__(self, db_path=None):
		super().__init__()

		self.config_mgr = ConfigManager()
		self.setWindowTitle("🚀 极速文件搜索 V42 增强版")
		self.resize(1400, 900)

		# 状态变量
		self.results_lock = threading.Lock()
		self.is_searching = False
		self.is_paused = False
		self.stop_event = False
		self.total_found = 0
		self.current_search_id = 0
		self.all_results: List[dict] = []
		self.filtered_results: List[dict] = []
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
		self.last_render_time = 0.0
		self.render_interval = 0.15
		self.last_search_scope = None
		self.full_search_results: List[dict] = []
		self.worker = None

		# 排序状态
		self.sort_column_index = -1
		self.sort_order = Qt.AscendingOrder

		# 索引与监控
		self.index_mgr = IndexManager(db_path=db_path, config_mgr=self.config_mgr)
		self.file_watcher = UsnFileWatcher(self.index_mgr, config_mgr=self.config_mgr)
		self.index_build_stop = False
		self.file_watcher.files_changed.connect(self._on_files_changed)
		
		# 新增功能管理器
		from filesearch.core.clipboard_history import ClipboardHistory
		from filesearch.core.quick_actions import ActionManager
		from filesearch.core.tag_manager import TagManager
		from filesearch.core.content_search import ContentSearchEngine
		from filesearch.core.document_search import DocumentSearchEngine
		self.clipboard_mgr = ClipboardHistory()
		self.action_mgr = ActionManager()
		self.tag_mgr = TagManager()
		self.content_search = ContentSearchEngine()
		self.doc_search = DocumentSearchEngine()

		# 状态定时器
		self.status_timer = QTimer(self)
		self.status_timer.timeout.connect(self._auto_refresh_status)
		self.status_timer.start(5000)

		# 托盘与热键
		self.tray_mgr = TrayManager(self)
		self.hotkey_mgr = HotkeyManager(self)
		self.mini_search = MiniSearchWindow(self)
		self._user_resized_columns = False
		self._settings = QSettings("SearchTool", "UI")
		self._saved_ratios = [0.33, 0.39, 0.14, 0.14]

		# 信号绑定
		self.index_mgr.progress_signal.connect(self.on_build_progress)
		self.index_mgr.build_finished_signal.connect(self.on_build_finished)
		self.index_mgr.fts_finished_signal.connect(self.on_fts_finished)

		# 构建 UI（已拆分到 ui_builder）
		build_menubar(self)
		build_ui(self)
		bind_shortcuts(self)

		# 确保启动后窗口激活并聚焦搜索框
		QTimer.singleShot(0, self._ensure_initial_focus)

		# 即时搜索定时器（去抖动）
		self._search_timer = None
		self._last_search_text = ""
		# 连接搜索框 textChanged 信号实现即时搜索
		try:
			self.entry_kw.textChanged.connect(self._on_text_changed)
		except Exception:
			pass

		# 初始化托盘和热键
		self._init_tray_and_hotkey()
		self._did_initial_resize = False
		QTimer.singleShot(0, self._auto_resize_columns)

		# 启动时加载 DIR_CACHE，加快监控
		QTimer.singleShot(100, self._load_dir_cache_all)
		QTimer.singleShot(500, self._check_index)
		
		# 首次显示标记
		self._first_show = True

	# ==================== 窗口事件 ====================
	def showEvent(self, event):
		"""窗口显示事件 - 首次打开自动聚焦搜索框"""
		super().showEvent(event)
		if self._first_show:
			self._first_show = False
			# 进一步延迟聚焦，避免其他控件抢焦点
			QTimer.singleShot(150, self._ensure_initial_focus)
			QTimer.singleShot(300, self._ensure_initial_focus)

	def _ensure_initial_focus(self):
		try:
			self.activateWindow()
			if hasattr(self, 'entry_kw') and self.entry_kw is not None:
				self.entry_kw.setFocus()
				self.entry_kw.selectAll()
		except Exception:
			pass

	# ==================== 构建/状态 ====================
	def on_build_progress(self, count, message):
		self.status.setText(f"🔄 构建中... ({count:,})")
		self.status_path.setText(message)

	def on_build_finished(self):
		self.index_mgr.force_reload_stats()
		self._check_index()
		self.status_path.setText("")
		self.status.setText(f"✅ 索引完成 ({self.index_mgr.file_count:,})")
		
		# 同步初始化所有盘的 Rust 搜索索引
		from ..core.rust_search import get_rust_search_engine
		rust_engine = get_rust_search_engine()
		if rust_engine:
			drives = []
			for c in "CDEFGHIJKLMNOPQRSTUVWXYZ":
				if os.path.exists(f"{c}:\\"):
					drives.append(c)
			
			if drives:
				logger.info(f"📊 开始初始化 Rust 搜索索引: {', '.join([f'{d}:' for d in drives])}")
				for drive in drives:
					try:
						# 先尝试加载，加载失败才初始化
						if not rust_engine.load_index(drive):
							logger.info(f"🔄 {drive}: 盘首次初始化...")
							rust_engine.init_index(drive)
					except Exception as e:
						logger.error(f"❌ {drive}: 初始化失败: {e}")
				logger.info("✅ Rust 搜索索引初始化完成")
		
		self.file_watcher.stop()
		self.file_watcher.start(self._get_drives())
		logger.info("👁️ 文件监控已启动")

	def _on_files_changed(self, added, deleted, deleted_paths):
		self.index_mgr.force_reload_stats()
		self._check_index()

		if deleted_paths:
			prefixes = []
			exact = set()
			for p in deleted_paths:
				p_norm = os.path.normpath(p)
				exact.add(p_norm)
				prefixes.append(p_norm.rstrip("\\/") + os.sep)

			with self.results_lock:
				def keep_item(x):
					fp = os.path.normpath(x.get("fullpath", ""))
					if fp in exact:
						return False
					for pref in prefixes:
						if fp.startswith(pref):
							return False
					return True

				self.all_results = [x for x in self.all_results if keep_item(x)]
				self.filtered_results = [x for x in self.filtered_results if keep_item(x)]
				self.total_found = len(self.filtered_results)

			if not self.is_searching:
				self._render_page()

		if added > 0 or deleted > 0:
			self.status.setText(f"📁 文件变更: +{added} -{deleted}")

	def _auto_refresh_status(self):
		if not self.index_mgr.is_building:
			self.index_mgr.reload_stats()
			self._check_index()

	def on_fts_finished(self):
		logger.info("接收到 FTS_DONE 信号")
		self.index_mgr.force_reload_stats()
		self._check_index()

	def _init_tray_and_hotkey(self):
		if self.config_mgr.get_tray_enabled():
			self.tray_mgr.start()

		if self.config_mgr.get_hotkey_enabled() and HAS_WIN32:
			self.hotkey_mgr.start()

	# Shortcuts are provided by `ui.components.ui_builder.bind_shortcuts`.
	# The fallback implementations for menu/ui construction have been removed
	# — UI is now fully constructed by `ui_builder`.

	def eventFilter(self, obj, event):
		"""统一事件过滤器：处理搜索框下键和树控件快捷键"""
		if event.type() == QEvent.KeyPress:
			key = event.key()
			modifiers = event.modifiers()

			# 搜索框按下向下键，聚焦到结果树
			if obj == getattr(self, 'entry_kw', None) and key == Qt.Key_Down:
				if getattr(self, 'tree', None) and self.tree.topLevelItemCount() > 0:
					item = self.tree.topLevelItem(0)
					self.tree.setCurrentItem(item)
					self.tree.setFocus()
				return True

			# 检测哪个树控件有焦点
			focused_tree = None
			if getattr(self, 'tree', None) and self.tree.hasFocus():
				focused_tree = self.tree

			if focused_tree:
				item = focused_tree.currentItem()
				if not item:
					return super().eventFilter(obj, event)

				try:
					fp = item.text(2)  # 完整路径在第3列
					is_dir = os.path.isdir(fp)
				except Exception:
					return super().eventFilter(obj, event)

				# Ctrl+C: 复制文件路径
				if key == Qt.Key_C and modifiers & Qt.ControlModifier:
					QApplication.clipboard().setText(fp)
					self.status.setText("✅ 路径已复制")
					return True

				# Ctrl+E: 在资源管理器中定位
				if key == Qt.Key_E and modifiers & Qt.ControlModifier:
					try:
						subprocess.Popen(f'explorer /select,"{fp}"')
					except Exception as e:
						logger.error(f"定位失败: {e}")
					return True

				# Ctrl+T: 在终端打开
				if key == Qt.Key_T and modifiers & Qt.ControlModifier:
					try:
						if is_dir:
							subprocess.Popen(f'powershell -NoExit -Command "Set-Location \\"{fp}\\""')
						else:
							parent_dir = os.path.dirname(fp)
							subprocess.Popen(f'powershell -NoExit -Command "Set-Location \\"{parent_dir}\\""')
					except Exception as e:
						logger.error(f"终端打开失败: {e}")
					return True

				# Delete: 删除文件或目录
				if key == Qt.Key_Delete:
					if QMessageBox.question(self, "确认删除", f"删除: {item.text(0)}?") == QMessageBox.Yes:
						try:
							if HAS_SEND2TRASH and HAS_WIN32:
								import send2trash
								send2trash.send2trash(fp)
							else:
								if is_dir:
									shutil.rmtree(fp)
								else:
									os.remove(fp)
							focused_tree.takeTopLevelItem(focused_tree.indexOfTopLevelItem(item))
							self.status.setText(f"✅ 已删除: {item.text(0)}")
						except Exception as e:
							QMessageBox.warning(self, "删除失败", f"无法删除: {e}")
					return True

				# Ctrl+1-9: 快速选择
				if Qt.Key_1 <= key <= Qt.Key_9 and modifiers & Qt.ControlModifier:
					num = key - Qt.Key_0
					if 0 < num <= focused_tree.topLevelItemCount():
						focused_tree.setCurrentItem(focused_tree.topLevelItem(num - 1))
					return True

				# Enter: 打开文件或目录
				if key in (Qt.Key_Return, Qt.Key_Enter):
					try:
						if is_dir:
							subprocess.Popen(f'explorer "{fp}"')
						else:
							os.startfile(fp)
					except Exception as e:
						logger.error(f"打开失败: {e}")
					return True

		return super().eventFilter(obj, event)

	# ==================== 索引状态 ====================
	def _check_index(self):
		s = self.index_mgr.get_stats()
		fts = "FTS5✅" if s.get("has_fts") else "FTS5❌"
		mft = "MFT✅" if s.get("used_mft") else "MFT❌"

		time_info = ""
		if s.get("time"):
			last_update = datetime.datetime.fromtimestamp(s["time"])
			time_diff = datetime.datetime.now() - last_update
			if time_diff.days > 0:
				time_info = f" ({time_diff.days}天前)"
			elif time_diff.seconds > 3600:
				time_info = f" ({time_diff.seconds//3600}小时前)"
			else:
				time_info = f" ({time_diff.seconds//60}分钟前)"

		if s.get("building"):
			txt = f"🔄 构建中({s['count']:,}) [{fts}][{mft}]"
			self.idx_lbl.setStyleSheet("color: orange;")
		elif s.get("ready"):
			txt = f"✅ 就绪({s['count']:,}){time_info} [{fts}][{mft}]"
			self.idx_lbl.setStyleSheet("color: green;")
			if not self.file_watcher.running:
				self._load_dir_cache_all()
				self.file_watcher.start(self._get_drives())
				logger.info("👁️ 文件监控已启动（索引已存在）")
		else:
			txt = f"❌ 未构建 [{fts}][{mft}]"
			self.idx_lbl.setStyleSheet("color: red;")

		self.idx_lbl.setText(txt)

	def sync_now(self):
		try:
			self.index_mgr.force_reload_stats()
			self._check_index()

			if hasattr(self.file_watcher, "poll_once"):
				self.file_watcher.poll_once()

			self.index_mgr.force_reload_stats()
			self._check_index()

			self.status.setText("✅ 已立即同步")
		except Exception as e:  # noqa: BLE001
			logger.error(f"立即同步失败: {e}")
			self.status.setText("⚠️ 立即同步失败")

	# ==================== 磁盘/收藏 ====================
	def _get_drives(self):
		if IS_WINDOWS:
			return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
		return ["/"]

	def _load_dir_cache_all(self):
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
		except Exception as e:  # noqa: BLE001
			logger.warning(f"加载 DIR_CACHE 失败: {e}")

	def _save_dir_cache_all(self):
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
		except Exception as e:  # noqa: BLE001
			logger.warning(f"保存 DIR_CACHE 失败: {e}")

	def _update_drives(self):
		self.combo_scope.clear()
		self.combo_scope.addItem("所有磁盘 (全盘)")
		self.combo_scope.addItems(self._get_drives())
		self.combo_scope.setCurrentIndex(0)

	def _browse(self):
		d = QFileDialog.getExistingDirectory(self, "选择目录")
		if d:
			self.combo_scope.setCurrentText(d)

	def _get_search_scope_targets(self):
		return parse_search_scope(self.combo_scope.currentText(), self._get_drives, self.config_mgr)

	def _on_scope_change(self, index):  # noqa: ARG002
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
		if not self.full_search_results:
			return
		drive_letter = drive_path.rstrip("\\").upper()
		with self.results_lock:
			self.all_results = [
				item for item in self.full_search_results
				if item["fullpath"][:2].upper() == drive_letter[:2]
			]
			self.filtered_results = list(self.all_results)
		self._apply_filter()
		self.status.setText(f"✅ 筛选 {drive_letter}: {len(self.filtered_results)}项")
		self.lbl_filter.setText(f"磁盘筛选: {len(self.filtered_results)}/{len(self.full_search_results)}")

	def _update_fav_combo(self):
		favorites = self.config_mgr.get_favorites()
		values = ["⭐ 收藏夹"] + [f"📁 {fav['name']}" for fav in favorites] if favorites else ["⭐ 收藏夹", "(无收藏)"]
		self.combo_fav.clear()
		self.combo_fav.addItems(values)
		self.combo_fav.setCurrentIndex(0)

	def _on_fav_combo_select(self, index):  # noqa: ARG002
		sel = self.combo_fav.currentText()
		if sel in {"⭐ 收藏夹", "(无收藏)"}:
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
		self.fav_menu.clear()
		self.fav_menu.addAction("⭐ 收藏当前目录", self._add_current_to_favorites)
		self.fav_menu.addAction("📂 管理收藏夹", self._manage_favorites)
		self.fav_menu.addSeparator()

		favorites = self.config_mgr.get_favorites()
		if favorites:
			for fav in favorites:
				act = self.fav_menu.addAction(f"📁 {fav['name']}")
				act.triggered.connect(lambda checked=False, p=fav["path"]: self._goto_favorite(p))
		else:
			act = self.fav_menu.addAction("(无收藏)")
			act.setEnabled(False)

	def _add_current_to_favorites(self):
		scope = self.combo_scope.currentText()
		if "所有磁盘" in scope:
			QMessageBox.information(self, "提示", "请先选择一个具体目录")
			return
		self.config_mgr.add_favorite(scope)
		self._update_favorites_menu()
		self._update_fav_combo()
		QMessageBox.information(self, "成功", f"已收藏: {scope}")

	def _goto_favorite(self, path):
		if os.path.exists(path):
			self.combo_scope.setCurrentText(path)
		else:
			QMessageBox.warning(self, "警告", f"目录不存在: {path}")

	def _manage_favorites(self):
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

	# ==================== 主题/设置 ====================
	def _on_theme_change(self, theme):
		self.config_mgr.set_theme(theme)
		apply_theme(QApplication.instance(), theme)
		self.status.setText(f"主题已切换: {theme}")

	def _show_settings(self):
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

		btn_row = QHBoxLayout()
		btn_row.addStretch()

		def save_settings():
			self.config_mgr.set_hotkey_enabled(self.chk_hotkey.isChecked())
			self.config_mgr.set_tray_enabled(self.chk_tray.isChecked())

			if self.chk_hotkey.isChecked() and not self.hotkey_mgr.registered and HAS_WIN32:
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
		dialog = CDriveSettingsDialog(self, self.config_mgr, self.index_mgr, self._rebuild_c_drive)
		dialog.show()

	def _rebuild_c_drive(self, drive_letter="C"):
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
			except Exception as e:  # noqa: BLE001
				logger.error(f"重建 {drive_letter} 盘索引失败: {e}")
			finally:
				QTimer.singleShot(0, self._on_rebuild_finished)

		threading.Thread(target=run, daemon=True).start()

	def _on_rebuild_finished(self):
		self.index_mgr.force_reload_stats()
		self._check_index()
		self.progress.setVisible(False)
		self.status.setText(f"✅ 索引重建完成 ({self.index_mgr.file_count:,})")
		self.file_watcher.stop()
		self.file_watcher.start(self._get_drives())
		logger.info("👁️ 文件监控已重启")

	# ==================== 筛选 ====================
	def _update_ext_combo(self):
		counts = {}
		with self.results_lock:
			for item in self.all_results:
				if item.get("type_code") == 0:
					ext = "📂文件夹"
				elif item.get("type_code") == 1:
					ext = "📦压缩包"
				else:
					ext = os.path.splitext(item.get("filename", ""))[1].lower() or "(无)"
				counts[ext] = counts.get(ext, 0) + 1

		values = ["全部"] + [f"{ext} ({cnt})" for ext, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]]
		self.ext_var.clear()
		self.ext_var.addItems(values)

	def _get_size_min(self):
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
		now = time.time()
		day = 86400
		mapping = {
			"不限": 0,
			"今天": now - day,
			"3天内": now - 3 * day,
			"7天内": now - 7 * day,
			"30天内": now - 30 * day,
			"今年": time.mktime(datetime.datetime(datetime.datetime.now().year, 1, 1).timetuple()),
		}
		return mapping.get(self.date_var.currentText(), 0)

	def _apply_filter(self):
		ext_sel = self.ext_var.currentText()
		size_min = self._get_size_min()
		date_min = self._get_date_min()
		target_ext = ext_sel.split(" (")[0] if ext_sel != "全部" else None

		with self.results_lock:
			self.filtered_results = []
			for item in self.all_results:
				if size_min > 0 and item.get("type_code") == 2 and item.get("size", 0) < size_min:
					continue
				if date_min > 0 and item.get("mtime", 0) < date_min:
					continue
				if target_ext:
					if item.get("type_code") == 0:
						item_ext = "📂文件夹"
					elif item.get("type_code") == 1:
						item_ext = "📦压缩包"
					else:
						item_ext = os.path.splitext(item.get("filename", ""))[1].lower() or "(无)"
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
		self.ext_var.setCurrentText("全部")
		self.size_var.setCurrentText("不限")
		self.date_var.setCurrentText("不限")
		with self.results_lock:
			self.filtered_results = list(self.all_results)
		self.current_page = 1
		self._render_page()
		self.lbl_filter.setText("")

	# ==================== 分页 ====================
	def _update_page_info(self):
		total = len(self.filtered_results)
		self.total_pages = max(1, math.ceil(total / self.page_size))
		self.lbl_page.setText(f"第 {self.current_page}/{self.total_pages} 页 ({total}项)")
		self.btn_first.setEnabled(self.current_page > 1)
		self.btn_prev.setEnabled(self.current_page > 1)
		self.btn_next.setEnabled(self.current_page < self.total_pages)
		self.btn_last.setEnabled(self.current_page < self.total_pages)

	def go_page(self, action):
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
		self.tree.clear()
		self.item_meta.clear()
		self._update_page_info()

		start = (self.current_page - 1) * self.page_size
		end = start + self.page_size
		with self.results_lock:
			page_items = self.filtered_results[start:end]
		if not page_items:
			return

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
						count,
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
								updates.append((results[j].size, results[j].mtime, need_stat_paths[j]))
						if updates:
							threading.Thread(target=self._write_back_stat, args=(updates,), daemon=True).start()
			except Exception as e:  # noqa: BLE001
				logger.debug(f"Rust 批量 stat 失败，回退: {e}")
				self._fallback_stat(page_items)
		else:
			self._fallback_stat(page_items)

		# 填充缺失的 mtime（文件/目录均处理，确保时间列有值）
		missing_updates = []
		for it in page_items:
			if it.get("mtime", 0) <= 0:
				try:
					it["mtime"] = os.path.getmtime(it["fullpath"])
					missing_updates.append((it.get("size", 0), it["mtime"], it["fullpath"]))
				except Exception:
					continue
		if missing_updates and self.index_mgr.conn:
			threading.Thread(
				target=self._write_back_stat, args=(missing_updates,), daemon=True
			).start()

		for it in page_items:
			tc = it.get("type_code", 2)
			if tc == 0:
				it["size_str"] = "📂 文件夹"
			elif tc == 1:
				it["size_str"] = "📦 压缩包"
			else:
				it["size_str"] = format_size(it.get("size", 0))
			it["mtime_str"] = format_time(it.get("mtime", 0))

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
				q_item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
				q_item.setTextAlignment(3, Qt.AlignRight | Qt.AlignVCenter)
				q_item.setData(2, Qt.UserRole, item.get("size", 0))
				q_item.setData(3, Qt.UserRole, item.get("mtime", 0))
				self.tree.addTopLevelItem(q_item)
				self.item_meta[id(q_item)] = start + i
		finally:
			self.tree.setUpdatesEnabled(True)

	def _write_back_stat(self, updates):
		try:
			with self.index_mgr.lock:
				cursor = self.index_mgr.conn.cursor()
				cursor.executemany("UPDATE files SET size=?, mtime=? WHERE full_path=?", updates)
				if not HAS_APSW:
					self.index_mgr.conn.commit()
		except Exception as e:  # noqa: BLE001
			logger.debug(f"stat 写回数据库失败: {e}")

	def _fallback_stat(self, page_items):
		try:
			tmp = []
			for it in page_items:
				fullpath = it.get("fullpath", "")
				filename = it.get("filename", "")
				dir_path = it.get("dir_path", "")
				is_dir = 1 if it.get("type_code") == 0 else 0
				ext = "" if is_dir else os.path.splitext(filename)[1].lower()
				tmp.append([
					filename,
					filename.lower(),
					fullpath,
					dir_path,
					ext,
					int(it.get("size", 0) or 0),
					float(it.get("mtime", 0) or 0),
					is_dir,
				])

			_batch_stat_files(tmp, only_missing=True, write_back_db=True, db_conn=self.index_mgr.conn, db_lock=self.index_mgr.lock)

			for it, t in zip(page_items, tmp):
				it["size"] = t[5]
				it["mtime"] = t[6]
		except Exception as e:  # noqa: BLE001
			logger.debug(f"回退 stat 失败: {e}")

	def _preload_all_stats(self):
		try:
			with self.results_lock:
				items_to_load = [it for it in self.all_results if it.get("type_code", 2) == 2 and it.get("size", 0) == 0]

			if not items_to_load or not HAS_RUST_ENGINE:
				return

			batch_size = 500
			for i in range(0, len(items_to_load), batch_size):
				if self.is_searching or self.stop_event:
					return
				batch = items_to_load[i : i + batch_size]
				paths = [it["fullpath"] for it in batch]

				try:
					paths_joined = "\0".join(paths)
					paths_bytes = paths_joined.encode("utf-8")
					paths_buf = (ctypes.c_uint8 * len(paths_bytes))(*paths_bytes)

					count = len(paths)
					FileInfoArray = FileInfo * count
					results = FileInfoArray()

					actual = RUST_ENGINE.get_file_info_batch(paths_buf, len(paths_bytes), results, count)

					with self.results_lock:
						for j in range(actual):
							if results[j].exists:
								batch[j]["size"] = results[j].size
								batch[j]["mtime"] = results[j].mtime

					if actual > 0 and self.index_mgr.conn:
						updates = []
						for j in range(actual):
							if results[j].exists:
								updates.append((results[j].size, results[j].mtime, paths[j]))
						if updates:
							self._write_back_stat(updates)
				except Exception as e:  # noqa: BLE001
					logger.debug(f"预加载批次失败: {e}")
				time.sleep(0.01)
		except Exception as e:  # noqa: BLE001
			logger.debug(f"预加载失败: {e}")

	def sort_column(self, logical_index):
		if self.sort_column_index == logical_index:
			self.sort_order = Qt.DescendingOrder if self.sort_order == Qt.AscendingOrder else Qt.AscendingOrder
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

	def _on_section_resized(self, index, old_size, new_size):
		# 用户拖拽后立即保存并更新比例，后续窗口大小变化按比例缩放
		self._user_resized_columns = True
		self._save_column_widths()

	def _auto_resize_columns(self):
		if not hasattr(self, "tree") or not self.tree:
			return
		try:
			viewport_w = max(self.tree.viewport().width(), 600)
			self._apply_ratio_resize(viewport_w)
		except Exception:
			pass

	def _save_column_widths(self):
		try:
			widths = [self.tree.columnWidth(i) for i in range(self.tree.columnCount())]
			total = sum(widths) or 1
			ratios = [w / total for w in widths]
			self._saved_ratios = ratios
			self._settings.setValue("column_widths", widths)
			self._settings.setValue("column_ratios", ratios)
		except Exception:
			pass

	def _apply_saved_column_widths(self):
		try:
			saved = self._settings.value("column_widths")
			saved_ratio = self._settings.value("column_ratios")
			if saved_ratio and isinstance(saved_ratio, (list, tuple)) and len(saved_ratio) >= 4:
				self._saved_ratios = [float(x) for x in saved_ratio]
				viewport_w = max(self.tree.viewport().width(), 600)
				self._apply_ratio_resize(viewport_w)
				self._user_resized_columns = False
				return
			if saved and isinstance(saved, (list, tuple)) and len(saved) >= 4:
				for i in range(4):
					self.tree.setColumnWidth(i, int(saved[i]))
				self._recalc_ratios_from_current()
				self._user_resized_columns = False
				self._fill_extra_space()
				return
		except Exception:
			pass
		self._auto_resize_columns()

	def _recalc_ratios_from_current(self):
		try:
			widths = [self.tree.columnWidth(i) for i in range(self.tree.columnCount())]
			total = sum(widths) or 1
			self._saved_ratios = [w / total for w in widths]
			self._settings.setValue("column_ratios", self._saved_ratios)
		except Exception:
			pass

	def _fill_extra_space(self):
		if not hasattr(self, "tree") or not self.tree:
			return
		try:
			viewport_w = self.tree.viewport().width()
			total = sum(self.tree.columnWidth(i) for i in range(self.tree.columnCount()))
			gap = viewport_w - total
			if gap > 8:
				add_size = int(gap * 0.4)
				add_time = gap - add_size
				self.tree.setColumnWidth(2, self.tree.columnWidth(2) + add_size)
				self.tree.setColumnWidth(3, self.tree.columnWidth(3) + add_time)
		except Exception:
			pass

	def _apply_ratio_resize(self, viewport_w):
		min_widths = [260, 360, 140, 150]
		ratios = self._saved_ratios if self._saved_ratios and len(self._saved_ratios) >= 4 else [0.33, 0.39, 0.14, 0.14]
		base = [max(int(viewport_w * r), m) for r, m in zip(ratios, min_widths)]
		total_base = sum(base)
		if total_base != viewport_w:
			extra = viewport_w - total_base
			base[-1] = max(min_widths[-1], base[-1] + extra)
		for i in range(4):
			self.tree.setColumnWidth(i, base[i])
		self._fill_extra_space()

	def select_all(self):
		if hasattr(self, "tree") and self.tree:
			self.tree.selectAll()

	def resizeEvent(self, event):
		try:
			self._auto_resize_columns()
		except Exception:
			pass
		super().resizeEvent(event)

	def showEvent(self, event):
		super().showEvent(event)
		if not getattr(self, "_did_initial_resize", False):
			self._auto_resize_columns()
			self._did_initial_resize = True

	# ==================== 搜索 ====================
	def _on_text_changed(self, text):
		"""即时搜索：输入变化时使用去抖动定时器触发搜索"""
		if self._search_timer is not None:
			self._search_timer.stop()
			self._search_timer.deleteLater()
			self._search_timer = None

		text = text.strip()
		if not text:
			return

		# 去抖动：100ms 后触发搜索
		if text != self._last_search_text:
			self._search_timer = QTimer()
			self._search_timer.setSingleShot(True)
			self._search_timer.timeout.connect(lambda: self._trigger_instant_search(text))
			self._search_timer.start(100)

	def _trigger_instant_search(self, text):
		"""真正触发即时搜索"""
		# 若正在搜索，优先取消当前任务以避免旧查询占用资源
		if self.is_searching:
			try:
				self.stop_search()
			except Exception:
				pass

		# 对不完整的增强语法做延迟：例如 dm: 尚未输入完整的值/单位时不触发重负载搜索
		try:
			from filesearch.core.search_syntax import SearchSyntaxParser
			parser = SearchSyntaxParser()
			clean_kw, filters = parser.parse(text)
			# 当用户输入的是增强语法但尚未形成有效过滤（例如正在输入 dm:7d 过程中的 dm: 或 dm:7）时，跳过即时搜索
			if text.strip().lower().startswith('dm:') and not filters.get('date_after'):
				return
		except Exception:
			# 解析失败则继续正常流程，以免阻塞
			pass

		self._last_search_text = text
		self.start_search(silent=True)

	def start_search_wrapper(self):
		"""Enter 键触发搜索的包装方法（检查是否有焦点在树上）"""
		# 如果焦点在结果树上，Enter 用于打开文件，不触发搜索
		if getattr(self, 'tree', None) and self.tree.hasFocus():
			return
		self.start_search()

	def start_search(self, silent=False):
		if self.is_searching:
			return
		kw = self.entry_kw.text().strip()
		if not kw:
			if not silent:
				QMessageBox.warning(self, "提示", "请输入关键词")
			return
		
		# 检测书签搜索 (bm: 前缀)
		if kw.lower().startswith('bm:'):
			keyword = kw[3:].strip()
			self._show_bookmark_search(keyword)
			return
		
		# 检测进程搜索 (ps: 或 process: 前缀)
		if kw.lower().startswith('ps:') or kw.lower().startswith('process:'):
			keyword = kw.split(':', 1)[1].strip()
			self._show_process_manager(keyword)
			return
		
		# 检测最近文件 (recent: 前缀)
		if kw.lower().startswith('recent:'):
			keyword = kw[7:].strip()
			self._show_recent_files(keyword)
			return
		
		# 检测浏览器历史 (history: 前缀)
		if kw.lower().startswith('history:'):
			keyword = kw[8:].strip()
			self._show_browser_history(keyword)
			return
		
		# 检测系统快捷方式 (sys: 或 control: 前缀)
		if kw.lower().startswith('sys:') or kw.lower().startswith('control:'):
			keyword = kw.split(':', 1)[1].strip()
			self._show_system_shortcuts(keyword)
			return
		
		# 检测内容搜索 (content: 前缀)
		if kw.lower().startswith('content:'):
			pattern = kw[8:].strip()
			self._show_content_search(pattern)
			return
		
		# 检测文档搜索 (doc: 前缀)
		if kw.lower().startswith('doc:'):
			pattern = kw[4:].strip()
			self._show_document_search(pattern)
			return
		
		# 检测标签搜索 (tag: 前缀)
		if kw.lower().startswith('tag:'):
			tags = kw[4:].strip()
			self._show_tag_search(tags)
			return
		
		# 检测颜色工具
		from filesearch.core.color_unit_tools import ColorTool
		if ColorTool.is_color(kw):
			color_info = ColorTool.parse_color(kw)
			if color_info:
				self._show_color_info(color_info)
				return
		
		# 检测单位转换
		from filesearch.core.color_unit_tools import UnitConverter
		if UnitConverter.is_conversion(kw):
			success, result = UnitConverter.convert(kw)
			if success:
				self.status.setText(f"🔧 转换结果: {result}")
				clipboard = QApplication.clipboard()
				clipboard.setText(result)
				QMessageBox.information(self, "单位转换", f"{result}\n\n结果已复制到剪贴板")
				return
		
		# 检测网页搜索
		from filesearch.core.web_search import WebSearchEngine
		engine_key, web_query = WebSearchEngine.parse_query(kw)
		if engine_key and web_query:
			engine_info = WebSearchEngine.get_engine_info(engine_key)
			success = WebSearchEngine.search(engine_key, web_query)
			if success:
				self.status.setText(f"🌐 已在 {engine_info['name']} 中搜索: {web_query}")
				return
			else:
				self.status.setText(f"❌ 无法打开 {engine_info['name']}")
				return
		
		# 检测计算器
		from filesearch.core.calculator import Calculator
		if Calculator.is_expression(kw):
			success, result = Calculator.calculate(kw)
			if success:
				# 显示计算结果
				self.status.setText(f"🔢 计算结果: {kw} = {result}")
				# 复制结果到剪贴板
				clipboard = QApplication.clipboard()
				clipboard.setText(str(result))
				QMessageBox.information(self, "计算结果", 
					f"{kw}\n\n= {result}\n\n结果已复制到剪贴板")
				return
			else:
				# 计算失败，继续文件搜索
				pass
		
		# 检测快速动作
		action_keywords = ["compress", "zip", "压缩", "vscode", "code", "git", "email", "邮件", "copyto", "复制到桌面"]
		kw_lower = kw.lower()
		for action_kw in action_keywords:
			if action_kw in kw_lower:
				# 获取选中的文件
				selected_items = self._get_selected_items()
				if selected_items:
					filepaths = [item["fullpath"] for item in selected_items]
					success, message = self.action_mgr.execute_action(action_kw, filepaths)
					if success:
						self.status.setText(f"✅ {message}")
					else:
						self.status.setText(f"❌ {message}")
					return

		# 解析搜索语法
		syntax_parser = SearchSyntaxParser()
		clean_kw, syntax_filters = syntax_parser.parse(kw)
		
		# 保存原始关键词和过滤器
		self.config_mgr.add_history(kw)
		self.last_search_params = {"kw": kw, "clean_kw": clean_kw, "syntax_filters": syntax_filters}
		self.last_search_scope = self.combo_scope.currentText()

		self.tree.clear()
		self.item_meta.clear()
		self.total_found = 0
		self.current_page = 1
		self.sort_column_index = -1
		self.ext_var.setCurrentText("全部")
		self.size_var.setCurrentText("不限")
		self.date_var.setCurrentText("不限")
		
		# 显示语法过滤器提示
		filter_hints = []
		if syntax_filters.get("extensions"):
			filter_hints.append(f"扩展名: {', '.join(syntax_filters['extensions'])}")
		if syntax_filters.get("size_min") or syntax_filters.get("size_max"):
			size_hint = "大小: "
			if syntax_filters.get("size_min"):
				size_hint += f">={self._format_size(syntax_filters['size_min'])} "
			if syntax_filters.get("size_max"):
				size_hint += f"<={self._format_size(syntax_filters['size_max'])}"
			filter_hints.append(size_hint)
		if syntax_filters.get("date_start") or syntax_filters.get("date_end"):
			filter_hints.append("日期: 已设置")
		if syntax_filters.get("path_include"):
			filter_hints.append(f"路径包含: {', '.join(syntax_filters['path_include'])}")
		if syntax_filters.get("name_pattern"):
			filter_hints.append(f"名称: {syntax_filters['name_pattern']}")
		if syntax_filters.get("dir_name"):
			filter_hints.append(f"目录名: {syntax_filters['dir_name']}")
		
		self.lbl_filter.setText(" | ".join(filter_hints) if filter_hints else "")

		with self.results_lock:
			self.all_results.clear()
			self.filtered_results.clear()
			self.shown_paths.clear()

		# 通知高亮 delegate 当前关键词
		try:
			if getattr(self, "_main_highlight_delegate", None):
				keywords = clean_kw.lower().split() if clean_kw else kw.lower().split()
				self._main_highlight_delegate.set_keywords(keywords)
		except Exception:
			pass

		self.is_searching = True
		self.stop_event = False
		self.btn_search.setEnabled(False)
		self.btn_pause.setEnabled(True)
		self.btn_stop.setEnabled(True)
		self.progress.setVisible(True)
		self.progress.setRange(0, 0)
		self.status.setText("🔍 搜索中...")

		scope_targets = self._get_search_scope_targets()
		self.status.setText("⚡ Rust 索引搜索..." if not self.force_realtime else "🔍 实时扫描...")
		# 使用清理后的关键词进行搜索
		search_kw = clean_kw if clean_kw else kw
		self.worker, is_realtime = create_worker(self.index_mgr, search_kw, scope_targets, self.regex_var, self.force_realtime)
		if is_realtime:
			try:
				self.worker.progress.connect(self.on_rt_progress)
			except Exception:
				pass

		self.worker.batch_ready.connect(self.on_batch_ready)
		self.worker.finished.connect(self.on_search_finished)
		self.worker.error.connect(self.on_search_error)
		self.worker.start()
	
	def _format_size(self, size_bytes):
		"""格式化字节大小为人类可读格式"""
		if size_bytes < 1024:
			return f"{size_bytes}B"
		elif size_bytes < 1024 * 1024:
			return f"{size_bytes / 1024:.1f}KB"
		elif size_bytes < 1024 * 1024 * 1024:
			return f"{size_bytes / (1024 * 1024):.1f}MB"
		else:
			return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"

	def refresh_search(self):
		if self.last_search_params and not self.is_searching:
			self.entry_kw.setText(self.last_search_params["kw"])
			self.start_search()

	def toggle_pause(self):
		if not self.is_searching or not hasattr(self, "worker") or not hasattr(self.worker, "toggle_pause"):
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
		if hasattr(self, "worker") and self.worker:
			self.worker.stop()
		self._reset_ui()
		self.status.setText(f"🛑 已停止 ({self.total_found}项)")

	def _reset_ui(self):
		self.is_searching = False
		self.is_paused = False
		self.btn_search.setEnabled(True)
		self.btn_pause.setEnabled(False)
		self.btn_pause.setText("⏸ 暂停")
		self.btn_stop.setEnabled(False)
		self.btn_refresh.setEnabled(True)
		self.progress.setVisible(False)

	def on_batch_ready(self, batch):
		# 应用语法过滤器
		syntax_filters = self.last_search_params.get("syntax_filters", {})
		if syntax_filters:
			syntax_parser = SearchSyntaxParser()
			# 设置过滤器
			syntax_parser.filters = syntax_filters
			# 应用过滤
			batch = syntax_parser.apply_filters(batch)
		
		with self.results_lock:
			for item_data in batch:
				fp = item_data["fullpath"]
				if fp not in self.shown_paths:
					self.shown_paths.add(fp)
					self.all_results.append(item_data)
			self.total_found = len(self.all_results)

		now = time.time()
		if self.total_found <= 200 or (now - self.last_render_time) > self.render_interval:
			with self.results_lock:
				self.filtered_results = self.all_results[: self.page_size]
			self._render_page()
			self.last_render_time = now
		self.status.setText(f"已找到: {self.total_found}")

	def on_rt_progress(self, scanned_dirs, speed):
		self.status.setText(f"🔍 实时扫描... ({scanned_dirs:,} 目录，{speed:.0f}/s)")

	def on_search_finished(self, total_time):
		self._reset_ui()
		self._finalize()
		self.status.setText(f"✅ 完成: {self.total_found}项 ({total_time:.2f}s)")

	def on_search_error(self, error_msg):
		self._reset_ui()
		QMessageBox.warning(self, "搜索错误", error_msg)

	def _finalize(self):
		self._update_ext_combo()
		with self.results_lock:
			self.filtered_results = self.all_results[:]
			if self.last_search_scope == "所有磁盘 (全盘)":
				self.full_search_results = self.all_results[:]
		self._render_page()
		threading.Thread(target=self._preload_all_stats, daemon=True).start()

	# ==================== 文件操作 ====================
	def on_dblclick(self, item, column):  # noqa: ARG002
		if not item:
			return
		idx = self.item_meta.get(id(item))
		if idx is None:
			return
		with self.results_lock:
			if idx < 0 or idx >= len(self.filtered_results):
				return
			data = self.filtered_results[idx]

		if data.get("type_code") == 0:
			try:
				subprocess.Popen(f'explorer "{data["fullpath"]}"')
			except Exception as e:  # noqa: BLE001
				logger.error(f"打开文件夹失败: {e}")
				QMessageBox.warning(self, "错误", f"无法打开文件夹: {e}")
		else:
			try:
				os.startfile(data["fullpath"])
			except Exception as e:  # noqa: BLE001
				logger.error(f"打开文件失败: {e}")
				QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

	def show_menu(self, pos):
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
		ctx_menu.addAction("🔐 计算 Hash", self._show_file_hash_from_menu)
		ctx_menu.addSeparator()
		ctx_menu.addAction("🗑️ 删除", self.delete_file)
		ctx_menu.exec_(self.tree.viewport().mapToGlobal(pos))
	
	def _show_file_hash_from_menu(self):
		"""从右键菜单显示文件 Hash 计算对话框"""
		items = self._get_selected_items()
		if items:
			filepaths = [item["fullpath"] for item in items if item.get("type_code") == 1]
			if filepaths:
				self._show_file_hash_calculator(filepaths)
			else:
				QMessageBox.warning(self, "提示", "请选择文件（不支持文件夹）")

	def _get_sel(self):
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
		items = []
		for sel in self.tree.selectedItems():
			idx = self.item_meta.get(id(sel))
			if idx is not None:
				with self.results_lock:
					if 0 <= idx < len(self.filtered_results):
						items.append(self.filtered_results[idx])
		return items

	def open_file(self):
		item = self._get_sel()
		if item:
			try:
				fo_open_file(item["fullpath"])
			except Exception as e:  # noqa: BLE001
				logger.error(f"打开文件失败: {e}")
				QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

	def open_folder(self):
		item = self._get_sel()
		if item:
			try:
				fo_open_folder(item["fullpath"])
			except Exception as e:  # noqa: BLE001
				logger.error(f"定位文件失败: {e}")
				QMessageBox.warning(self, "错误", f"无法定位文件: {e}")

	def copy_path(self):
		items = self._get_selected_items()
		if items:
			paths = [item["fullpath"] for item in items]
			try:
				fo_copy_paths(QApplication, paths)
				self.status.setText(f"已复制 {len(items)} 个路径")
			except Exception:
				# fallback to direct clipboard
				QApplication.clipboard().setText("\n".join(paths))
				self.status.setText(f"已复制 {len(items)} 个路径")

	def copy_file(self):
		if not HAS_WIN32:
			QMessageBox.warning(self, "提示", "需要在 Windows 上使用此功能")
			return
		items = self._get_selected_items()
		if not items:
			return
		files = [item["fullpath"] for item in items if os.path.exists(item["fullpath"]) ]
		if not files:
			return
		try:
			fo_copy_files_win32(files)
			self.status.setText(f"已复制 {len(files)} 个文件")
		except Exception as e:  # noqa: BLE001
			logger.error(f"复制文件失败: {e}")
			QMessageBox.warning(self, "错误", f"复制文件失败: {e}")

	def delete_file(self):
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

		if QMessageBox.question(self, "确认", msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
			return

		# Delegate deletion to helper
		deleted, failed, remove_exact, remove_prefix = fo_delete_items(items, use_send2trash=HAS_SEND2TRASH)

		with self.results_lock:
			for p in list(self.shown_paths):
				pn = os.path.normpath(p)
				if pn in remove_exact:
					self.shown_paths.discard(p)
					continue
				for pref in remove_prefix:
					if pn.startswith(pref):
						self.shown_paths.discard(p)
						break

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

		self._render_page()

		if failed:
			self.status.setText(f"✅ 已删除 {deleted} 个，失败 {len(failed)} 个")
			QMessageBox.warning(self, "部分失败", "以下文件删除失败:\n" + "\n".join(failed[:5]))
		else:
			self.status.setText(f"✅ 已删除 {deleted} 个文件/文件夹")

	def preview_file(self):
		item = self._get_sel()
		if not item:
			return

		fullpath = item.get("fullpath", "")
		ext = os.path.splitext(item.get("filename", ""))[1].lower()
		
		# 图片文件预览
		image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".svg"}
		if ext in image_exts:
			self._show_image_preview(fullpath)
			return
		
		# 文本文件预览
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
			self._preview_text(fullpath)
		elif item.get("type_code") == 0:
			try:
				subprocess.Popen(f'explorer "{fullpath}"')
			except Exception as e:  # noqa: BLE001
				QMessageBox.warning(self, "错误", f"无法打开文件夹: {e}")
		else:
			try:
				os.startfile(fullpath)
			except Exception as e:  # noqa: BLE001
				QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

	def _preview_text(self, path):
		dlg = QDialog(self)
		dlg.setWindowTitle(f"预览: {os.path.basename(path)}")
		dlg.resize(900, 650)
		dlg.setModal(True)

		layout = QVBoxLayout(dlg)
		layout.setContentsMargins(5, 5, 5, 5)
		
		# 添加搜索栏
		search_layout = QHBoxLayout()
		search_label = QLabel("搜索:")
		search_input = QLineEdit()
		search_input.setPlaceholderText("输入关键词高亮显示...")
		search_layout.addWidget(search_label)
		search_layout.addWidget(search_input)
		layout.addLayout(search_layout)

		text = QTextEdit()
		text.setFont(QFont("Consolas", 10))
		text.setReadOnly(True)
		layout.addWidget(text)

		try:
			with open(path, "r", encoding="utf-8", errors="ignore") as f:
				lines = f.readlines()
			
			# 限制显示行数
			max_lines = 5000
			if len(lines) > max_lines:
				lines = lines[:max_lines]
				truncated = True
			else:
				truncated = False
			
			# 添加行号
			content_with_line_numbers = ""
			for i, line in enumerate(lines, 1):
				content_with_line_numbers += f"{i:5d} | {line}"
			
			if truncated:
				content_with_line_numbers += f"\n\n... [文件过大，仅显示前{max_lines}行] ..."
			
			text.setPlainText(content_with_line_numbers)
			
			# 搜索高亮功能
			def highlight_search(keyword):
				if not keyword:
					# 清除高亮
					text.setPlainText(content_with_line_numbers)
					return
				
				# 使用 HTML 高亮关键词
				import html as html_module
				highlighted = content_with_line_numbers
				keyword_escaped = html_module.escape(keyword)
				
				# 简单的关键词高亮（不区分大小写）
				import re
				pattern = re.compile(re.escape(keyword), re.IGNORECASE)
				highlighted = pattern.sub(
					lambda m: f'<span style="background-color: yellow; color: black;">{html_module.escape(m.group())}</span>',
					html_module.escape(highlighted)
				)
				highlighted = highlighted.replace('\n', '<br>')
				highlighted = highlighted.replace(' ', '&nbsp;')
				
				text.setHtml(f'<pre style="font-family: Consolas; font-size: 10pt;">{highlighted}</pre>')
			
			search_input.textChanged.connect(highlight_search)
			
			# 如果有当前搜索关键词，自动高亮
			try:
				current_kw = self.entry_kw.text().strip()
				if current_kw and len(current_kw) >= 2:
					search_input.setText(current_kw)
			except Exception:
				pass
				
		except Exception as e:  # noqa: BLE001
			text.setPlainText(f"无法读取文件: {e}")

		dlg.exec()

	# ==================== 索引管理 ====================
	def _show_index_mgr(self):
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
		c_dirs_str = ", ".join([os.path.basename(d) for d in c_dirs[:3]]) + ("..." if len(c_dirs) > 3 else "")

		last_update_str = "从未"
		if s.get("time"):
			last_update = datetime.datetime.fromtimestamp(s["time"])
			last_update_str = last_update.strftime("%m-%d %H:%M")

		duration_str = "-"
		if s.get("duration"):
			duration_str = f"{s['duration']:.1f}s"

		rows = [
			("文件数量:", f"{s['count']:,}" if s.get("count") else "未构建"),
			("状态:", "✅就绪" if s.get("ready") else ("🔄构建中" if s.get("building") else "❌未构建")),
			("FTS5:", "✅已启用" if s.get("has_fts") else "❌未启用"),
			("MFT:", "✅已使用" if s.get("used_mft") else "❌未使用"),
			("构建时间:", last_update_str),
			("上次耗时:", duration_str),
			("C盘范围:", c_dirs_str),
			("索引路径:", os.path.basename(s.get("path", ""))),
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
					except Exception:
						pass
				self.index_mgr = IndexManager(db_path=self.index_mgr.db_path, config_mgr=self.config_mgr)
				self.index_mgr.progress_signal.connect(self.on_build_progress)
				self.index_mgr.build_finished_signal.connect(self.on_build_finished)
				self.index_mgr.fts_finished_signal.connect(self.on_fts_finished)
				self.file_watcher = UsnFileWatcher(self.index_mgr, config_mgr=self.config_mgr)
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
		if self.index_mgr.is_building:
			return

		self.index_build_stop = False
		drives = self._get_drives()

		try:
			self.status.setText("🔥 预热磁盘中(首次构建加速)...")
			self.status_path.setText("正在唤醒磁盘/加载元数据缓存...")
			self.progress.setVisible(True)
			self.progress.setRange(0, 0)
			QApplication.processEvents()
			self._warm_up_drives(drives)
		except Exception as e:  # noqa: BLE001
			logger.debug(f"预热失败(可忽略): {e}")

		self.status.setText("🔄 正在构建索引...")
		self.status_path.setText("")
		self.progress.setVisible(True)
		self.progress.setRange(0, 0)

		threading.Thread(target=self.index_mgr.build_index, args=(drives, lambda: self.index_build_stop), daemon=True).start()
		self._check_index()

	def _warm_up_drives(self, drives):
		for drive in drives:
			try:
				os.listdir(drive)
			except Exception:
				pass

	# ==================== 工具 ====================
	def export_results(self):
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
					writer.writerow(["文件名", "完整路径", "所在目录", "大小", "修改时间"])
					for item in self.all_results:
						writer.writerow([item["filename"], item["fullpath"], item["dir_path"], item.get("size_str", ""), item.get("mtime_str", "")])
				else:
					for item in self.all_results:
						f.write(f"{item['filename']}\t{item['fullpath']}\n")

			self.status.setText(f"✅ 已导出 {len(self.all_results)} 条结果")
			QMessageBox.information(self, "成功", f"已导出 {len(self.all_results)} 条结果")
		except Exception as e:  # noqa: BLE001
			logger.error(f"导出失败: {e}")
			QMessageBox.warning(self, "错误", f"导出失败: {e}")

	def scan_large_files(self):
		dlg = QDialog(self)
		dlg.setWindowTitle("📊 大文件扫描")
		dlg.setMinimumSize(800, 600)
		dlg.setModal(True)

		layout = QVBoxLayout(dlg)
		layout.setContentsMargins(15, 15, 15, 15)
		layout.setSpacing(10)

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
			min_size = int(min_size_str.replace("GB", "")) * 1024**3 if "GB" in min_size_str else int(min_size_str.replace("MB", "")) * 1024**2

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
							except Exception:
								continue
				except Exception:
					continue

			found.sort(key=lambda x: -x[1])
			for name, size, fp in found[:500]:
				item = QTreeWidgetItem([name, format_size(size), fp])
				result_tree.addTopLevelItem(item)

			status_label.setText(f"✅ 找到 {len(found)} 个大文件")

		btn_scan.clicked.connect(do_scan)
		dlg.exec()

	def _show_batch_rename(self):
		items = self._get_selected_items()
		if not items:
			QMessageBox.information(self, "提示", "请先选择要重命名的文件")
			return
		scope = self.combo_scope.currentText()
		scope_text = f"当前选中: {len(items)} 个项目 | 范围: {scope}"
		dialog = BatchRenameDialog(self, items, self)
		dialog.show(scope_text)

	def _show_shortcuts(self):
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
		QMessageBox.information(
			self,
			"关于",
			"🚀 极速文件搜索 V42 增强版\n\n"
			"核心功能:\n"
			"• MFT极速索引\n"
			"• FTS5全文搜索\n"
			"• 高级搜索语法 (ext:、size:、dm:、path:)\n"
			"• 重复文件查找\n"
			"• 文件 Hash 计算 (MD5/SHA256)\n"
			"• 图片预览 (支持缩放)\n"
			"• 模糊/正则搜索\n"
			"• 实时文件监控\n"
			"• 保存搜索条件\n\n"
			"新增超能力:\n"
			"• 🌐 网页搜索 (g:, bd:, gh:, yt: 等)\n"
			"• 🔢 智能计算器 (数学表达式)\n"
			"• ⚡ 快速动作 (compress, vscode, git 等)\n"
			"• 📋 剪贴板历史\n"
			"• 📝 文本预览增强 (行号+高亮)\n\n"
			"其他特性:\n"
			"• 收藏夹管理\n"
			"• 多主题支持\n"
			"• 全局热键呼出\n"
			"• 系统托盘常驻\n"
			"• C盘目录自定义\n\n"
			"© 2024",
		)

	# ==================== 新增高级功能 ====================
	def _show_search_syntax_help(self):
		"""显示搜索语法帮助"""
		try:
			from .dialogs.search_syntax_help import SearchSyntaxHelpDialog
			dlg = SearchSyntaxHelpDialog(self)
			dlg.exec()
		except Exception as e:
			logger.error(f"显示搜索语法帮助失败: {e}")
			QMessageBox.warning(self, "错误", f"无法显示搜索语法帮助: {e}")

	def _show_duplicate_finder(self):
		"""显示重复文件查找对话框"""
		try:
			from .dialogs.duplicate_finder import DuplicateFinderDialog
			default_path = self.combo_scope.currentText()
			if "所有磁盘" in default_path:
				default_path = ""
			dlg = DuplicateFinderDialog(self, default_path)
			dlg.exec()
		except Exception as e:
			logger.error(f"显示重复文件查找失败: {e}")
			QMessageBox.warning(self, "错误", f"无法打开重复文件查找: {e}")

	def _show_file_hash_calculator(self, filepaths=None):
		"""显示文件 Hash 计算对话框"""
		try:
			from .dialogs.file_hash_dialog import FileHashDialog
			# 使用传入的文件列表或获取选中的文件
			selected_files = filepaths if filepaths else []
			
			if not selected_files:
				for item in self.tree.selectedItems():
					try:
						idx = self.tree.indexOfTopLevelItem(item)
						if 0 <= idx < len(self.filtered_results):
							data = self.filtered_results[idx]
							fullpath = data.get("fullpath", "")
							if fullpath and os.path.isfile(fullpath):
								selected_files.append(fullpath)
					except Exception:
						continue
			
			if not selected_files:
				QMessageBox.information(self, "提示", "请先选择要计算 Hash 的文件")
				return
			
			dlg = FileHashDialog(self, selected_files)
			dlg.exec()
		except Exception as e:
			logger.error(f"显示文件 Hash 计算失败: {e}")
			QMessageBox.warning(self, "错误", f"无法打开 Hash 计算: {e}")

	def _show_saved_searches(self):
		"""显示保存的搜索对话框"""
		try:
			from .dialogs.saved_search import SavedSearchDialog
			dlg = SavedSearchDialog(self, self.config_mgr)
			dlg.exec()
		except Exception as e:
			logger.error(f"显示保存的搜索失败: {e}")
			QMessageBox.warning(self, "错误", f"无法打开保存的搜索: {e}")

	def _show_image_preview(self, filepath):
		"""显示图片预览"""
		try:
			from .dialogs.image_preview import ImagePreviewDialog
			dlg = ImagePreviewDialog(self, filepath)
			dlg.exec()
		except Exception as e:
			logger.error(f"显示图片预览失败: {e}")
			QMessageBox.warning(self, "错误", f"无法预览图片: {e}")
	
	def _show_clipboard_history(self):
		"""显示剪贴板历史"""
		try:
			from .dialogs.clipboard_history_dialog import ClipboardHistoryDialog
			dlg = ClipboardHistoryDialog(self, self.clipboard_mgr)
			dlg.exec()
		except Exception as e:
			logger.error(f"显示剪贴板历史失败: {e}")
			QMessageBox.warning(self, "错误", f"无法打开剪贴板历史: {e}")
	
	def _show_web_search_help(self):
		"""显示网页搜索帮助"""
		from filesearch.core.web_search import WebSearchEngine
		help_text = WebSearchEngine.get_help_text()
		QMessageBox.information(self, "🌐 网页搜索帮助", help_text)
	
	def _show_calculator_help(self):
		"""显示计算器帮助"""
		from filesearch.core.calculator import Calculator
		help_text = Calculator.get_help_text()
		QMessageBox.information(self, "🔢 计算器帮助", help_text)
	
	def _show_quick_actions_help(self):
		"""显示快速动作帮助"""
		help_text = self.action_mgr.get_help_text()
		QMessageBox.information(self, "⚡ 快速动作帮助", help_text)
	
	def _show_content_search_help(self):
		"""显示内容搜索帮助"""
		help_text = """
📄 内容搜索 - 搜索文件内容

使用方法:
  content:关键词    - 搜索文本文件内容
  
示例:
  content:TODO      - 搜索包含 TODO 的文件
  content:import    - 搜索包含 import 的代码文件
  content:bug       - 搜索包含 bug 的日志文件

支持的文件类型:
  • 文本文件: .txt, .log, .md
  • 代码文件: .py, .js, .java, .c, .cpp, .html, .css
  • 配置文件: .json, .yaml, .xml, .ini, .cfg
  • 其他: 所有纯文本文件

高级搜索:
  • 使用 doc: 前缀搜索 Office 文档 (需要安装依赖)
  • 支持正则表达式 (在搜索框中输入)
  • 显示匹配行的上下文

注意:
  • 默认搜索当前选择的范围
  • 文件大小限制: 10MB
  • 自动检测文件编码
		"""
		QMessageBox.information(self, "📄 内容搜索帮助", help_text.strip())
	
	def _show_tag_search_help(self):
		"""显示标签搜索帮助"""
		help_text = """
🏷 标签管理 - 给文件打标签，快速分类

使用方法:
  tag:标签名        - 搜索具有该标签的文件
  tag:tag1,tag2     - 搜索具有任一标签的文件
  Ctrl+T            - 打开标签管理器

标签管理器功能:
  • 📊 标签云 - 查看所有标签和使用频率
  • 📄 文件标签 - 给选中文件添加/删除标签
  • 🔍 标签搜索 - 按标签搜索文件

标签操作:
  • 添加标签: 选中文件 → Ctrl+T → 输入标签名
  • 删除标签: 标签管理器 → 选择标签 → 删除
  • 重命名标签: 标签管理器 → 重命名
  • 设置颜色: 标签管理器 → 设置颜色

示例:
  tag:工作          - 查找工作相关文件
  tag:重要,紧急     - 查找重要或紧急的文件
  
提示:
  • 标签数据保存在: ~/.filesearch_tags.json
  • 支持标签云可视化
  • 可以给同一文件添加多个标签
		"""
		QMessageBox.information(self, "🏷 标签搜索帮助", help_text.strip())
	
	def _show_bookmark_search(self, keyword=""):
		"""显示书签搜索"""
		from filesearch.core.bookmark_manager import BookmarkManager
		import webbrowser
		
		bookmarks = BookmarkManager.search_bookmarks(keyword)
		
		if not bookmarks:
			QMessageBox.information(self, "书签搜索", "未找到书签")
			return
		
		# 创建简单对话框显示书签
		dlg = QDialog(self)
		dlg.setWindowTitle(f"📚 书签搜索: {keyword or '全部'}")
		dlg.resize(800, 500)
		
		layout = QVBoxLayout(dlg)
		
		from PySide6.QtWidgets import QListWidget, QListWidgetItem
		list_widget = QListWidget()
		
		for bm in bookmarks[:100]:  # 限制显示100个
			item_text = f"[{bm['browser']}] {bm['title']}\n{bm['url']}"
			item = QListWidgetItem(item_text)
			item.setData(Qt.UserRole, bm['url'])
			list_widget.addItem(item)
		
		def open_bookmark(item):
			url = item.data(Qt.UserRole)
			webbrowser.open(url)
			dlg.close()
		
		list_widget.itemDoubleClicked.connect(open_bookmark)
		layout.addWidget(list_widget)
		
		btn_layout = QHBoxLayout()
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addStretch()
		btn_layout.addWidget(btn_close)
		layout.addLayout(btn_layout)
		
		dlg.exec()
	
	def _show_process_manager(self, keyword=""):
		"""显示进程管理器"""
		from filesearch.core.process_manager import ProcessManager
		
		processes = ProcessManager.search_processes(keyword)
		
		dlg = QDialog(self)
		dlg.setWindowTitle(f"🔄 进程管理器: {keyword or '全部'}")
		dlg.resize(900, 600)
		
		layout = QVBoxLayout(dlg)
		
		from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
		tree = QTreeWidget()
		tree.setHeaderLabels(["PID", "进程名", "CPU %", "内存 (MB)"])
		tree.setColumnWidth(0, 80)
		tree.setColumnWidth(1, 300)
		tree.setColumnWidth(2, 100)
		
		for proc in sorted(processes, key=lambda x: x['memory_mb'], reverse=True)[:200]:
			item = QTreeWidgetItem([
				str(proc['pid']),
				proc['name'],
				f"{proc['cpu_percent']:.1f}",
				f"{proc['memory_mb']:.1f}"
			])
			item.setData(0, Qt.UserRole, proc['pid'])
			tree.addItem(item)
		
		layout.addWidget(tree)
		
		btn_layout = QHBoxLayout()
		btn_kill = QPushButton("🗑️ 结束进程")
		
		def kill_selected():
			current = tree.currentItem()
			if current:
				pid = current.data(0, Qt.UserRole)
				reply = QMessageBox.question(dlg, "确认", f"确定要结束进程 {pid} 吗？")
				if reply == QMessageBox.Yes:
					success, msg = ProcessManager.kill_process(pid)
					if success:
						QMessageBox.information(dlg, "成功", msg)
						tree.takeTopLevelItem(tree.indexOfTopLevelItem(current))
					else:
						QMessageBox.warning(dlg, "失败", msg)
		
		btn_kill.clicked.connect(kill_selected)
		btn_layout.addWidget(btn_kill)
		btn_layout.addStretch()
		
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addWidget(btn_close)
		layout.addLayout(btn_layout)
		
		dlg.exec()
	
	def _show_recent_files(self, keyword=""):
		"""显示最近文件"""
		from filesearch.core.recent_files import RecentFilesManager
		
		files = RecentFilesManager.search_recent_files(keyword)
		
		dlg = QDialog(self)
		dlg.setWindowTitle(f"📝 最近文件: {keyword or '全部'}")
		dlg.resize(800, 500)
		
		layout = QVBoxLayout(dlg)
		
		from PySide6.QtWidgets import QListWidget, QListWidgetItem
		import datetime
		
		list_widget = QListWidget()
		
		for file_info in files:
			dt = datetime.datetime.fromtimestamp(file_info['access_time'])
			time_str = dt.strftime("%Y-%m-%d %H:%M")
			item_text = f"[{time_str}] {file_info['name']}\n{file_info['path']}"
			item = QListWidgetItem(item_text)
			item.setData(Qt.UserRole, file_info['path'])
			list_widget.addItem(item)
		
		def open_file(item):
			path = item.data(Qt.UserRole)
			try:
				os.startfile(path)
			except Exception as e:
				QMessageBox.warning(dlg, "错误", f"无法打开文件: {e}")
		
		list_widget.itemDoubleClicked.connect(open_file)
		layout.addWidget(list_widget)
		
		btn_layout = QHBoxLayout()
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addStretch()
		btn_layout.addWidget(btn_close)
		layout.addLayout(btn_layout)
		
		dlg.exec()
	
	def _show_browser_history(self, keyword=""):
		"""显示浏览器历史"""
		from filesearch.core.browser_history import BrowserHistoryManager
		import webbrowser
		
		history = BrowserHistoryManager.search_history(keyword, limit=200)
		
		dlg = QDialog(self)
		dlg.setWindowTitle(f"🌐 浏览器历史: {keyword or '全部'}")
		dlg.resize(900, 600)
		
		layout = QVBoxLayout(dlg)
		
		from PySide6.QtWidgets import QListWidget, QListWidgetItem
		import datetime
		
		list_widget = QListWidget()
		
		for item_data in history:
			dt = datetime.datetime.fromtimestamp(item_data['timestamp'])
			time_str = dt.strftime("%Y-%m-%d %H:%M")
			item_text = f"[{item_data['browser']}] [{time_str}] {item_data['title']}\n{item_data['url']}"
			item = QListWidgetItem(item_text)
			item.setData(Qt.UserRole, item_data['url'])
			list_widget.addItem(item)
		
		def open_url(item):
			url = item.data(Qt.UserRole)
			webbrowser.open(url)
		
		list_widget.itemDoubleClicked.connect(open_url)
		layout.addWidget(list_widget)
		
		btn_layout = QHBoxLayout()
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addStretch()
		btn_layout.addWidget(btn_close)
		layout.addLayout(btn_layout)
		
		dlg.exec()
	
	def _show_system_shortcuts(self, keyword=""):
		"""显示系统快捷方式"""
		from filesearch.core.windows_shortcuts import WindowsShortcuts
		
		shortcuts = WindowsShortcuts.search_shortcuts(keyword) if keyword else WindowsShortcuts.get_all_shortcuts()
		
		dlg = QDialog(self)
		dlg.setWindowTitle(f"⚙️ 系统快捷方式: {keyword or '全部'}")
		dlg.resize(700, 500)
		
		layout = QVBoxLayout(dlg)
		
		from PySide6.QtWidgets import QListWidget, QListWidgetItem
		
		list_widget = QListWidget()
		
		for shortcut in shortcuts:
			item_text = f"{shortcut['icon']} {shortcut['name']} ({shortcut['key']})"
			item = QListWidgetItem(item_text)
			item.setData(Qt.UserRole, shortcut['key'])
			list_widget.addItem(item)
		
		def open_shortcut(item):
			key = item.data(Qt.UserRole)
			success, msg = WindowsShortcuts.open_shortcut(key)
			if success:
				self.status.setText(msg)
				dlg.close()
			else:
				QMessageBox.warning(dlg, "错误", msg)
		
		list_widget.itemDoubleClicked.connect(open_shortcut)
		layout.addWidget(list_widget)
		
		btn_layout = QHBoxLayout()
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addStretch()
		btn_layout.addWidget(btn_close)
		layout.addLayout(btn_layout)
		
		dlg.exec()
	
	def _show_color_info(self, color_info):
		"""显示颜色信息"""
		dlg = QDialog(self)
		dlg.setWindowTitle("🎨 颜色信息")
		dlg.resize(400, 350)
		
		layout = QVBoxLayout(dlg)
		
		# 颜色预览
		from PySide6.QtWidgets import QFrame
		color_preview = QFrame()
		color_preview.setMinimumHeight(100)
		color_preview.setStyleSheet(f"background-color: {color_info['hex']}; border: 2px solid #ccc;")
		layout.addWidget(color_preview)
		
		# 颜色信息
		info_text = f"""
HEX:  {color_info['hex']}
RGB:  {color_info['rgb']}
RGBA: {color_info['rgba']}
HSL:  {color_info['hsl']}

R: {color_info['r']}
G: {color_info['g']}
B: {color_info['b']}
		"""
		
		from PySide6.QtWidgets import QTextEdit
		text_edit = QTextEdit()
		text_edit.setPlainText(info_text.strip())
		text_edit.setReadOnly(True)
		layout.addWidget(text_edit)
		
		# 按钮
		btn_layout = QHBoxLayout()
		
		btn_copy_hex = QPushButton("复制 HEX")
		btn_copy_hex.clicked.connect(lambda: QApplication.clipboard().setText(color_info['hex']))
		btn_layout.addWidget(btn_copy_hex)
		
		btn_copy_rgb = QPushButton("复制 RGB")
		btn_copy_rgb.clicked.connect(lambda: QApplication.clipboard().setText(color_info['rgb']))
		btn_layout.addWidget(btn_copy_rgb)
		
		btn_layout.addStretch()
		
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addWidget(btn_close)
		
		layout.addLayout(btn_layout)
		
		dlg.exec()
	
	def _show_content_search(self, pattern):
		"""显示内容搜索对话框"""
		from PySide6.QtWidgets import QListWidget, QListWidgetItem, QProgressDialog
		from PySide6.QtCore import QThread, Signal
		
		# 获取搜索范围
		scope_text = self.combo_scope.currentText()
		search_dir = None
		
		if scope_text == "C 盘":
			search_dir = "C:\\"
		elif scope_text == "D 盘":
			search_dir = "D:\\"
		elif scope_text.startswith("自定义:"):
			search_dir = scope_text.split(":", 1)[1].strip()
		else:
			# 默认搜索用户目录
			search_dir = os.path.expanduser("~")
		
		if not os.path.exists(search_dir):
			QMessageBox.warning(self, "错误", f"搜索目录不存在: {search_dir}")
			return
		
		# 创建对话框
		dlg = QDialog(self)
		dlg.setWindowTitle(f"📄 内容搜索: {pattern}")
		dlg.resize(900, 600)
		
		layout = QVBoxLayout(dlg)
		
		info_label = QLabel(f"搜索目录: {search_dir}")
		layout.addWidget(info_label)
		
		result_list = QListWidget()
		layout.addWidget(result_list)
		
		btn_layout = QHBoxLayout()
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addStretch()
		btn_layout.addWidget(btn_close)
		layout.addLayout(btn_layout)
		
		# 在后台线程中搜索
		progress = QProgressDialog("正在搜索文件内容...", "取消", 0, 0, dlg)
		progress.setWindowModality(Qt.WindowModal)
		progress.show()
		
		class SearchThread(QThread):
			results_ready = Signal(list)
			
			def __init__(self, engine, directory, pattern):
				super().__init__()
				self.engine = engine
				self.directory = directory
				self.pattern = pattern
			
			def run(self):
				results = self.engine.search_in_directory(self.directory, self.pattern, recursive=True)
				self.results_ready.emit(results)
		
		def on_results(results):
			progress.close()
			result_list.clear()
			
			for result in results[:100]:  # 最多显示100个文件
				file_path = result['file_path']
				match_count = result['match_count']
				
				item_text = f"{os.path.basename(file_path)} ({match_count} 处匹配)\n  {file_path}"
				item = QListWidgetItem(item_text)
				item.setData(Qt.UserRole, result)
				result_list.addItem(item)
			
			if len(results) > 100:
				result_list.addItem(f"... 还有 {len(results) - 100} 个结果")
			
			info_label.setText(f"搜索目录: {search_dir} | 找到 {len(results)} 个文件")
		
		def on_item_clicked(item):
			result = item.data(Qt.UserRole)
			if result:
				# 显示匹配详情
				details = f"文件: {result['file_path']}\n\n"
				for match in result['matches'][:10]:
					details += f"行 {match['line_number']}: {match['line_content']}\n"
				QMessageBox.information(dlg, "匹配详情", details)
		
		result_list.itemDoubleClicked.connect(on_item_clicked)
		
		search_thread = SearchThread(self.content_search, search_dir, pattern)
		search_thread.results_ready.connect(on_results)
		search_thread.start()
		
		dlg.exec()
	
	def _show_document_search(self, pattern):
		"""显示文档搜索对话框"""
		from PySide6.QtWidgets import QListWidget, QListWidgetItem, QProgressDialog
		from PySide6.QtCore import QThread, Signal
		
		# 检查依赖
		from filesearch.core.document_search import HAS_DOCX, HAS_OPENPYXL, HAS_PYPDF
		
		supported = []
		if HAS_DOCX:
			supported.append("Word")
		if HAS_OPENPYXL:
			supported.append("Excel")
		if HAS_PYPDF:
			supported.append("PDF")
		
		if not supported:
			QMessageBox.warning(self, "缺少依赖", 
				"未安装文档搜索依赖库\n\n请运行: pip install python-docx openpyxl pypdf")
			return
		
		# 获取搜索范围
		scope_text = self.combo_scope.currentText()
		search_dir = None
		
		if scope_text == "C 盘":
			search_dir = "C:\\"
		elif scope_text == "D 盘":
			search_dir = "D:\\"
		elif scope_text.startswith("自定义:"):
			search_dir = scope_text.split(":", 1)[1].strip()
		else:
			search_dir = os.path.expanduser("~\\Documents")  # 默认文档目录
		
		# 创建对话框
		dlg = QDialog(self)
		dlg.setWindowTitle(f"📋 文档搜索: {pattern}")
		dlg.resize(900, 600)
		
		layout = QVBoxLayout(dlg)
		
		info_label = QLabel(f"搜索目录: {search_dir} | 支持: {', '.join(supported)}")
		layout.addWidget(info_label)
		
		result_list = QListWidget()
		layout.addWidget(result_list)
		
		btn_layout = QHBoxLayout()
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addStretch()
		btn_layout.addWidget(btn_close)
		layout.addLayout(btn_layout)
		
		# 收集文档文件
		doc_files = []
		for root, dirs, files in os.walk(search_dir):
			for file in files:
				ext = os.path.splitext(file)[1].lower()
				if ext in ['.docx', '.xlsx', '.pdf']:
					doc_files.append(os.path.join(root, file))
			
			if len(doc_files) > 500:  # 限制最多搜索500个文档
				break
		
		if not doc_files:
			QMessageBox.information(self, "提示", f"在 {search_dir} 中未找到文档文件")
			dlg.close()
			return
		
		# 在后台线程中搜索
		progress = QProgressDialog("正在搜索文档内容...", "取消", 0, len(doc_files), dlg)
		progress.setWindowModality(Qt.WindowModal)
		progress.show()
		
		class SearchThread(QThread):
			results_ready = Signal(list)
			progress_update = Signal(int, int)
			
			def __init__(self, engine, files, pattern):
				super().__init__()
				self.engine = engine
				self.files = files
				self.pattern = pattern
			
			def run(self):
				def progress_callback(current, total):
					self.progress_update.emit(current, total)
				
				results = self.engine.search_in_documents(
					self.files, self.pattern, progress_callback=progress_callback
				)
				self.results_ready.emit(results)
		
		def on_progress(current, total):
			progress.setValue(current)
		
		def on_results(results):
			progress.close()
			result_list.clear()
			
			for result in results[:100]:
				file_path = result['file_path']
				match_count = result['match_count']
				file_type = result['file_type']
				
				item_text = f"[{file_type}] {os.path.basename(file_path)} ({match_count} 处匹配)\n  {file_path}"
				item = QListWidgetItem(item_text)
				item.setData(Qt.UserRole, result)
				result_list.addItem(item)
			
			if len(results) > 100:
				result_list.addItem(f"... 还有 {len(results) - 100} 个结果")
			
			info_label.setText(f"搜索完成 | 找到 {len(results)} 个文档")
		
		def on_item_clicked(item):
			result = item.data(Qt.UserRole)
			if result:
				details = f"文件: {result['file_path']}\n类型: {result['file_type']}\n\n"
				for match in result['matches'][:10]:
					details += f"行 {match['line_number']}: {match['line_content'][:100]}\n"
				QMessageBox.information(dlg, "匹配详情", details)
		
		result_list.itemDoubleClicked.connect(on_item_clicked)
		
		search_thread = SearchThread(self.doc_search, doc_files, pattern)
		search_thread.results_ready.connect(on_results)
		search_thread.progress_update.connect(on_progress)
		search_thread.start()
		
		dlg.exec()
	
	def _show_tag_search(self, tags_text):
		"""显示标签搜索结果"""
		tags = [t.strip().lower() for t in tags_text.split(',') if t.strip()]
		
		if not tags:
			QMessageBox.warning(self, "提示", "请输入标签名（用逗号分隔）")
			return
		
		files = self.tag_mgr.get_files_by_tags(tags, match_all=False)
		
		from PySide6.QtWidgets import QListWidget, QListWidgetItem
		
		dlg = QDialog(self)
		dlg.setWindowTitle(f"🏷 标签搜索: {tags_text}")
		dlg.resize(800, 600)
		
		layout = QVBoxLayout(dlg)
		
		info = QLabel(f"包含标签 {tags_text} 的文件 ({len(files)})")
		layout.addWidget(info)
		
		result_list = QListWidget()
		for file_path in files[:200]:
			file_tags = self.tag_mgr.get_file_tags(file_path)
			item_text = f"{os.path.basename(file_path)}\n  标签: {', '.join(file_tags)}\n  {file_path}"
			item = QListWidgetItem(item_text)
			item.setData(Qt.UserRole, file_path)
			result_list.addItem(item)
		
		if len(files) > 200:
			result_list.addItem(f"... 还有 {len(files) - 200} 个结果")
		
		def open_file(item):
			file_path = item.data(Qt.UserRole)
			if file_path and os.path.exists(file_path):
				os.startfile(file_path)
		
		result_list.itemDoubleClicked.connect(open_file)
		layout.addWidget(result_list)
		
		btn_layout = QHBoxLayout()
		
		btn_manage = QPushButton("🏷 管理标签")
		btn_manage.clicked.connect(lambda: self._show_tag_manager([]))
		btn_layout.addWidget(btn_manage)
		
		btn_layout.addStretch()
		
		btn_close = QPushButton("关闭")
		btn_close.clicked.connect(dlg.close)
		btn_layout.addWidget(btn_close)
		
		layout.addLayout(btn_layout)
		
		dlg.exec()
	
	def _show_tag_manager(self, selected_files=None):
		"""显示标签管理器"""
		if selected_files is None:
			# 获取当前选中的文件
			selected_files = []
			selected_items = self._get_selected_items()
			if selected_items:
				selected_files = [item["fullpath"] for item in selected_items]
		
		dialog = TagManagerDialog(self, self.tag_mgr, selected_files)
		dialog.exec()



	# ==================== 关闭/退出 ====================
	def closeEvent(self, event):  # noqa: N802
		if self.config_mgr.get_tray_enabled() and self.tray_mgr.running:
			self.hide()
			self.tray_mgr.show_notification("极速文件搜索", "程序已最小化到托盘")
			event.ignore()
		else:
			self._do_quit()
			event.accept()

	def _do_quit(self):
		self.index_build_stop = True
		self.stop_event = True
		self._save_dir_cache_all()
		self.hotkey_mgr.stop()
		self.tray_mgr.stop()
		self.file_watcher.stop()
		self.index_mgr.close()
		QApplication.quit()


def main():
	logger.info("🚀 极速文件搜索 V42 增强版 - PySide6 UI")

	app = QApplication(sys.argv)
	app.setApplicationName("极速文件搜索")
	app.setOrganizationName("FileSearch")
	app.setQuitOnLastWindowClosed(False)

	config = ConfigManager()
	apply_theme(app, config.get_theme())

	win = SearchApp()
	win.show()

	sys.exit(app.exec())


__all__ = ["SearchApp", "main"]

