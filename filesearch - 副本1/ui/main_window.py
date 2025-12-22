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

from PySide6.QtCore import QEvent, Qt, QTimer, QSettings
from PySide6.QtGui import QFont, QKeySequence, QShortcut
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
)

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
from .tray_manager import TrayManager
from .hotkey_manager import HotkeyManager
from .mini_search import MiniSearchWindow
from .dialogs.cdrive_settings import CDriveSettingsDialog
from .dialogs.batch_rename import BatchRenameDialog

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

		# 构建 UI
		self._build_menubar()
		self._build_ui()
		self._bind_shortcuts()

		# 初始化托盘和热键
		self._init_tray_and_hotkey()
		self._did_initial_resize = False
		QTimer.singleShot(0, self._auto_resize_columns)

		# 启动时加载 DIR_CACHE，加快监控
		QTimer.singleShot(100, self._load_dir_cache_all)
		QTimer.singleShot(500, self._check_index)

	# ==================== 构建/状态 ====================
	def on_build_progress(self, count, message):
		self.status.setText(f"🔄 构建中... ({count:,})")
		self.status_path.setText(message)

	def on_build_finished(self):
		self.index_mgr.force_reload_stats()
		self._check_index()
		self.status_path.setText("")
		self.status.setText(f"✅ 索引完成 ({self.index_mgr.file_count:,})")
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

	def _build_menubar(self):
		menubar = self.menuBar()

		file_menu = menubar.addMenu("文件(&F)")
		file_menu.addAction("📤 导出结果", self.export_results, QKeySequence("Ctrl+E"))
		file_menu.addSeparator()
		# 保留 Enter 给搜索，避免重复快捷键冲突
		file_menu.addAction("📂 打开文件", self.open_file)
		file_menu.addAction("🎯 定位文件", self.open_folder, QKeySequence("Ctrl+L"))
		file_menu.addSeparator()
		file_menu.addAction("🚪 退出", self._do_quit, QKeySequence("Alt+F4"))

		edit_menu = menubar.addMenu("编辑(&E)")
		edit_menu.addAction("✅ 全选", self.select_all, QKeySequence("Ctrl+A"))
		edit_menu.addSeparator()
		edit_menu.addAction("📋 复制路径", self.copy_path, QKeySequence("Ctrl+C"))
		edit_menu.addAction("📄 复制文件", self.copy_file, QKeySequence("Ctrl+Shift+C"))
		edit_menu.addSeparator()
		edit_menu.addAction("🗑️ 删除", self.delete_file, QKeySequence("Delete"))

		search_menu = menubar.addMenu("搜索(&S)")
		search_menu.addAction("🔍 开始搜索", self.start_search, QKeySequence("Return"))
		search_menu.addAction("🔄 刷新搜索", self.refresh_search, QKeySequence("F5"))
		search_menu.addAction("⏹ 停止搜索", self.stop_search, QKeySequence("Escape"))

		tool_menu = menubar.addMenu("工具(&T)")
		tool_menu.addAction("📊 大文件扫描", self.scan_large_files, QKeySequence("Ctrl+G"))
		tool_menu.addAction("✏ 批量重命名", self._show_batch_rename)
		tool_menu.addSeparator()
		tool_menu.addAction("🔧 索引管理", self._show_index_mgr)
		tool_menu.addAction("🔄 重建索引", self._build_index)
		tool_menu.addSeparator()
		tool_menu.addAction("⚙️ 设置", self._show_settings)

		self.fav_menu = menubar.addMenu("收藏(&B)")
		self._update_favorites_menu()

		help_menu = menubar.addMenu("帮助(&H)")
		help_menu.addAction("⌨️ 快捷键列表", self._show_shortcuts)
		help_menu.addSeparator()
		help_menu.addAction("ℹ️ 关于", self._show_about)

	def _build_ui(self):
		central = QWidget()
		self.setCentralWidget(central)
		root_layout = QVBoxLayout(central)
		root_layout.setContentsMargins(10, 10, 10, 10)
		root_layout.setSpacing(8)

		header = QFrame()
		header_layout = QVBoxLayout(header)
		header_layout.setContentsMargins(0, 0, 0, 0)
		header_layout.setSpacing(8)

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
		self.chk_fuzzy.stateChanged.connect(lambda s: setattr(self, "fuzzy_var", bool(s)))
		row1.addWidget(self.chk_fuzzy)

		self.chk_regex = QCheckBox("正则")
		self.chk_regex.setChecked(self.regex_var)
		self.chk_regex.stateChanged.connect(lambda s: setattr(self, "regex_var", bool(s)))
		row1.addWidget(self.chk_regex)

		self.chk_realtime = QCheckBox("实时")
		self.chk_realtime.setChecked(self.force_realtime)
		self.chk_realtime.stateChanged.connect(lambda s: setattr(self, "force_realtime", bool(s)))
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

		body = QFrame()
		body_layout = QVBoxLayout(body)
		body_layout.setContentsMargins(0, 0, 0, 0)
		body_layout.setSpacing(0)

		self.tree = QTreeWidget()
		self.tree.setColumnCount(4)
		self.tree.setHeaderLabels(["📄 文件名", "📂 所在目录", "📊 大小/类型", "🕒 修改时间"])
		self.tree.setRootIsDecorated(False)
		self.tree.setAlternatingRowColors(True)
		self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
		self.tree.itemDoubleClicked.connect(self.on_dblclick)
		self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
		self.tree.customContextMenuRequested.connect(self.show_menu)
		self.tree.setStyleSheet(
			"""
			QTreeWidget {
				alternate-background-color: #f8f9fa;
				background-color: #ffffff;
			}
			QTreeWidget::item { padding: 2px; }
			QTreeWidget::item:selected { background-color: #0078d4; color: white; }
		"""
		)

		header_view = self.tree.header()
		header_view.setSortIndicatorShown(True)
		header_view.setSectionsClickable(True)
		header_view.sectionClicked.connect(self.sort_column)
		header_view.setStretchLastSection(False)
		header_view.setSectionResizeMode(0, QHeaderView.Interactive)
		header_view.setSectionResizeMode(1, QHeaderView.Interactive)
		header_view.setSectionResizeMode(2, QHeaderView.Interactive)
		header_view.setSectionResizeMode(3, QHeaderView.Interactive)
		header_view.sectionResized.connect(self._on_section_resized)
		self._apply_saved_column_widths()

		body_layout.addWidget(self.tree)

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

		common_style = (
			"""
			QPushButton { border: 1px solid #cbd5e0; border-radius: 7px; background: #ffffff; color: #1a202c; }
			QPushButton:hover { background: #edf2f7; }
			QPushButton:pressed { background: #e2e8f0; }
			QPushButton:disabled { color: #a0aec0; background: #f7fafc; }
		"""
		)
		for b in (self.btn_first, self.btn_prev, self.btn_next, self.btn_last):
			b.setFixedHeight(30)
			b.setFont(QFont("微软雅黑", 12, QFont.Bold))
			b.setStyleSheet(common_style)
		self.btn_prev.setFixedWidth(56)
		self.btn_next.setFixedWidth(56)
		self.btn_first.setFixedWidth(44)
		self.btn_last.setFixedWidth(44)

		pg_layout.addStretch()
		body_layout.addWidget(pg)

		root_layout.addWidget(body, 1)

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
		QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.entry_kw.setFocus())
		QShortcut(QKeySequence("Ctrl+A"), self, self.select_all)
		QShortcut(QKeySequence("Ctrl+C"), self, self.copy_path)
		QShortcut(QKeySequence("Ctrl+Shift+C"), self, self.copy_file)
		QShortcut(QKeySequence("Ctrl+E"), self, self.export_results)
		QShortcut(QKeySequence("Ctrl+G"), self, self.scan_large_files)
		QShortcut(QKeySequence("Ctrl+L"), self, self.open_folder)
		QShortcut(QKeySequence("F5"), self, self.refresh_search)
		QShortcut(QKeySequence("Delete"), self, self.delete_file)
		QShortcut(QKeySequence("Escape"), self, lambda: self.stop_search() if self.is_searching else self.entry_kw.clear())
		self.entry_kw.installEventFilter(self)

	def eventFilter(self, obj, event):
		if obj == self.entry_kw and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Down:
			if self.tree.topLevelItemCount() > 0:
				item = self.tree.topLevelItem(0)
				self.tree.setCurrentItem(item)
				self.tree.setFocus()
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
	def start_search(self):
		if self.is_searching:
			return
		kw = self.entry_kw.text().strip()
		if not kw:
			QMessageBox.warning(self, "提示", "请输入关键词")
			return

		self.config_mgr.add_history(kw)
		self.last_search_params = {"kw": kw}
		self.last_search_scope = self.combo_scope.currentText()

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
		use_idx = not self.force_realtime and self.index_mgr.is_ready and not self.index_mgr.is_building

		if use_idx:
			self.status.setText("⚡ 索引搜索...")
			self.worker = IndexSearchWorker(self.index_mgr, kw, scope_targets, self.regex_var, self.fuzzy_var)
		else:
			self.status.setText("🔍 实时扫描...")
			self.worker = RealtimeSearchWorker(kw, scope_targets, self.regex_var, self.fuzzy_var)
			self.worker.progress.connect(self.on_rt_progress)

		self.worker.batch_ready.connect(self.on_batch_ready)
		self.worker.finished.connect(self.on_search_finished)
		self.worker.error.connect(self.on_search_error)
		self.worker.start()

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
		ctx_menu.addAction("🗑️ 删除", self.delete_file)
		ctx_menu.exec_(self.tree.viewport().mapToGlobal(pos))

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
				os.startfile(item["fullpath"])
			except Exception as e:  # noqa: BLE001
				logger.error(f"打开文件失败: {e}")
				QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

	def open_folder(self):
		item = self._get_sel()
		if item:
			try:
				subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
			except Exception as e:  # noqa: BLE001
				logger.error(f"定位文件失败: {e}")
				QMessageBox.warning(self, "错误", f"无法定位文件: {e}")

	def copy_path(self):
		items = self._get_selected_items()
		if items:
			paths = "\n".join(item["fullpath"] for item in items)
			QApplication.clipboard().setText(paths)
			self.status.setText(f"已复制 {len(items)} 个路径")

	def copy_file(self):
		if not HAS_WIN32 or not win32clipboard or not win32con:
			QMessageBox.warning(self, "提示", "需要安装 pywin32: pip install pywin32")
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

		deleted = 0
		failed = []
		remove_exact = set()
		remove_prefix = []

		for item in items:
			fp = os.path.normpath(item["fullpath"])
			remove_exact.add(fp)
			if item.get("type_code") == 0 or item.get("is_dir") == 1:
				prefix = fp.rstrip("\\/") + os.sep
				remove_prefix.append(prefix)

		for item in items:
			try:
				if HAS_SEND2TRASH and send2trash:
					send2trash.send2trash(item["fullpath"])
				else:
					if item.get("type_code") == 0 or item.get("is_dir") == 1:
						shutil.rmtree(item["fullpath"])
					else:
						os.remove(item["fullpath"])
				deleted += 1
			except Exception as e:  # noqa: BLE001
				logger.error(f"删除失败: {item['fullpath']} - {e}")
				failed.append(item["filename"])

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

		ext = os.path.splitext(item.get("filename", ""))[1].lower()
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
		elif item.get("type_code") == 0:
			try:
				subprocess.Popen(f'explorer "{item["fullpath"]}"')
			except Exception as e:  # noqa: BLE001
				QMessageBox.warning(self, "错误", f"无法打开文件夹: {e}")
		else:
			try:
				os.startfile(item["fullpath"])
			except Exception as e:  # noqa: BLE001
				QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

	def _preview_text(self, path):
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
