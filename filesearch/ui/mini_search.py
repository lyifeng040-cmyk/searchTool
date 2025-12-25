"""
Mini search window extracted from legacy implementation.
"""

import logging
import os
import shutil
import subprocess
import struct
import html
import re
import time

from PySide6.QtCore import Qt, QEvent, QObject, QRectF, QTimer
from PySide6.QtGui import QFont, QColor, QTextDocument, QKeySequence, QShortcut
from PySide6.QtWidgets import (
	QApplication,
	QDialog,
	QWidget,
	QVBoxLayout,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QPushButton,
	QMenu,
	QMessageBox,
	QListWidget,
	QListWidgetItem,
	QStyledItemDelegate,
	QStyle,
	QToolTip,
)


class EnterLineEdit(QLineEdit):
	"""QLineEdit that handles Enter/Return; single Enter triggers search."""

	def __init__(self, on_enter=None, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._on_enter = on_enter

	def keyPressEvent(self, event):
		try:
			if event.key() in (Qt.Key_Return, Qt.Key_Enter):
				if callable(self._on_enter):
					self._on_enter()
					return
		except Exception:
			pass
		super().keyPressEvent(event)


class EnterListWidget(QListWidget):
	"""QListWidget that opens item on double Enter within 2 seconds."""

	def __init__(self, on_enter_double=None, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._on_enter_double = on_enter_double
		self._last_enter_ts = 0.0
		self._enter_double_threshold = 2.0  # 2 seconds

	def keyPressEvent(self, event):
		try:
			if event.key() in (Qt.Key_Return, Qt.Key_Enter):
				now = time.time()
				if (now - self._last_enter_ts) <= self._enter_double_threshold:
					if callable(self._on_enter_double):
						self._on_enter_double()
						self._last_enter_ts = 0.0
						return
				self._last_enter_ts = now
				# single Enter in list does nothing (per requirement)
				return
		except Exception:
			pass
		super().keyPressEvent(event)

from ..constants import SKIP_DIRS_LOWER, ARCHIVE_EXTS
from ..utils import format_size, format_time
from ..dependencies import HAS_SEND2TRASH, HAS_WIN32
from ..core.rust_search import get_rust_search_engine
from ..core.search_syntax import SearchSyntaxParser
from ..config import ConfigManager

logger = logging.getLogger(__name__)

if HAS_SEND2TRASH:
	try:
		import send2trash
	except ImportError:  # pragma: no cover
		send2trash = None
else:
	send2trash = None


class MiniSearchWindow(QObject):
	"""迷你搜索窗口"""

	def __init__(self, app):
		super().__init__()
		self.app = app
		self.window = None
		self.search_mode = "index"
		self.results = []
		self.result_listbox = None
		self.mode_label = None
		self.search_entry = None
		self.tip_label = None
		self.result_frame = None
		self.tip_frame = None
		self.button_frame = None
		self.ctx_menu = None
		self._highlight_delegate = None
		# 使用稳定的 QTimer 实例，避免重复创建导致已删除对象错误
		self._search_timer = QTimer(self)
		self._search_timer.setSingleShot(True)
		try:
			self._search_timer.timeout.connect(self._on_search)
		except Exception:
			pass
		self._last_search_keyword = ""
		# 记录输入框 Enter 时间用于判定连续 Enter 打开首项
		self._last_enter_main_ts = 0.0
		# 分页控制
		self._results_all = []
		self._results_shown = 0
		self._load_more_btn = None
		# 自动加载相关标志
		self._loading_more = False
		# 用于在迷你窗口打开时临时包装 QMessageBox（避免被迷你窗口遮挡）
		self._orig_qmsg_funcs = {}
		# 拖动窗口相关
		self._drag_start_pos = None
		# 保存窗口位置
		self._window_pos_key = "mini_search_window_pos"

		# config and page size
		try:
			if getattr(self.app, "config_mgr", None):
				self.config_mgr = self.app.config_mgr
			else:
				self.config_mgr = ConfigManager()
		except Exception:
			self.config_mgr = ConfigManager()
		self.page_size = self.config_mgr.get_results_page_size() if self.config_mgr else 200

	def show(self):
		if self.window is not None:
			try:
				self.window.activateWindow()
				self.window.raise_()
				self.search_entry.setFocus()
				self.search_entry.selectAll()
				return
			except Exception:
				self.window = None

		self._create_window()

		# 恢复窗口位置（如果有保存的位置）
		try:
			if self.config_mgr:
				saved_pos = self.config_mgr._settings.value(self._window_pos_key, None)
				if saved_pos:
					parts = saved_pos.split(",")
					if len(parts) == 2:
						x, y = int(parts[0]), int(parts[1])
						self.window.move(x, y)
						return
		except Exception:
			pass

		# 默认位置（屏幕中上方）
		screen = QApplication.primaryScreen().geometry()
		x = (screen.width() - 720) // 2
		y = int(screen.height() * 0.20)
		self.window.move(x, y)

		# 在显示迷你窗口期间，包装 QMessageBox 的全局函数，使其 parent 指向迷你窗口（避免被小窗口遮挡）
		try:
			from PySide6.QtWidgets import QMessageBox as _QMsg
			# 保存原函数
			for name in ("information", "warning", "question", "critical"):
				if hasattr(_QMsg, name):
					self._orig_qmsg_funcs[name] = getattr(_QMsg, name)

			def _make_wrapper(orig):
				def wrapper(*args, **kwargs):
					# 如果第一个参数是 parent，替换为 self.window；否则在前面插入 self.window
					new_args = list(args)
					if len(new_args) > 0 and hasattr(new_args[0], '__class__') and issubclass(new_args[0].__class__, QWidget):
						new_args[0] = self.window
					else:
						new_args.insert(0, self.window)
					return orig(*new_args, **kwargs)
				return wrapper

			for name, orig in list(self._orig_qmsg_funcs.items()):
				setattr(_QMsg, name, _make_wrapper(orig))
		except Exception:
			# 若包装失败，继续但不阻塞显示
			pass

	def _create_window(self):
		self.window = QDialog(None)
		self.window.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
		self.window.setAttribute(Qt.WA_TranslucentBackground, False)
		self.window.setFixedSize(720, 70)
		self.window.setStyleSheet("""
		/* Modern theme with enhanced visuals */
		QDialog {
		  background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:0.5 #fafbfc, stop:1 #f5f8fb);
		  border-radius: 28px;
		  border: 1px solid rgba(10,36,48,0.08);
		  padding: 10px;
		  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
		}
		QLineEdit {
		  padding: 12px 16px;
		  font-size: 14px;
		  border: 1px solid rgba(6,100,140,0.15);
		  border-radius: 16px;
		  background: #ffffff;
		  color: #073043;
		  selection-background-color: #0B84FF;
		}
		QLineEdit:focus {
		  border: 2px solid #0B84FF;
		  background: #ffffff;
		}
		QLabel#mode_label {
		  color: #0B84FF;
		  font-weight: 700;
		  padding: 0 8px;
		}
		QListWidget {
		  background: transparent;
		  border: 0;
		  font-size: 13px;
		  color: #042f36;
		  outline: none;
		}
		QListWidget::item { 
		  padding: 12px 14px; 
		  border-radius: 8px;
		  margin: 2px 0;
		  border: 1px solid transparent;
		}
		QListWidget::item:hover { 
		  background: rgba(11,132,255,0.08);
		  border: 1px solid rgba(11,132,255,0.2);
		}
		QListWidget::item:selected { 
		  background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0B84FF, stop:1 #0c6fd1);
		  color: #fff;
		  border: 1px solid #0B84FF;
		  font-weight: 500;
		}
		QPushButton { 
		  padding: 8px 12px; 
		  border-radius: 8px; 
		  background: transparent; 
		  color: #0B84FF; 
		  border: 1px solid rgba(11,132,255,0.2);
		  font-weight: 500;
		  font-size: 13px;
		}
		QPushButton:hover { 
		  background: rgba(11,132,255,0.08);
		  border: 1px solid rgba(11,132,255,0.4);
		}
		QPushButton:pressed { 
		  background: rgba(11,132,255,0.15);
		}
		#tip_label { 
		  color: #4a6b72;
		  font-size: 11px;
		}
		""")

		screen = QApplication.primaryScreen().geometry()
		x = (screen.width() - 720) // 2
		y = int(screen.height() * 0.20)
		self.window.move(x, y)

		main_layout = QVBoxLayout(self.window)
		main_layout.setContentsMargins(10, 8, 10, 8)
		main_layout.setSpacing(8)

		search_layout = QHBoxLayout()
		search_layout.setSpacing(12)

		self.search_icon = QLabel("🔍")
		self.search_icon.setFont(QFont("Segoe UI Emoji", 18))
		self.search_icon.setStyleSheet("color: #0B84FF;")
		self.search_icon.setCursor(Qt.PointingHandCursor)
		self.search_icon.mousePressEvent = lambda e: self._on_search()
		search_layout.addWidget(self.search_icon)

		# Use a QLineEdit subclass that reliably handles Enter/Return
		# 输入框按 Enter 触发搜索
		self.search_entry = EnterLineEdit(on_enter=self._on_search)
		self.search_entry.setFont(QFont("微软雅黑", 14))
		self.search_entry.setPlaceholderText("搜索文件... (支持 dm:7d ext:pdf size:>10mb 等)")
		search_layout.addWidget(self.search_entry, 1)

		mode_frame = QHBoxLayout()
		mode_frame.setSpacing(6)

		self.left_arrow = QLabel("◄")
		self.left_arrow.setFont(QFont("Arial", 11, QFont.Bold))
		self.left_arrow.setStyleSheet("color: #0B84FF;")
		self.left_arrow.setCursor(Qt.PointingHandCursor)
		self.left_arrow.mousePressEvent = lambda e: self._on_mode_switch()
		mode_frame.addWidget(self.left_arrow)

		self.mode_label = QLabel("⚡ Rust")
		self.mode_label.setObjectName("mode_label")
		self.mode_label.setFont(QFont("微软雅黑", 11, QFont.Bold))
		# 宽度适中，显示图标与文字
		self.mode_label.setFixedWidth(90)
		self.mode_label.setAlignment(Qt.AlignCenter)
		mode_frame.addWidget(self.mode_label)

		self.right_arrow = QLabel("►")
		self.right_arrow.setFont(QFont("Arial", 11, QFont.Bold))
		self.right_arrow.setStyleSheet("color: #0B84FF;")
		self.right_arrow.setCursor(Qt.PointingHandCursor)
		self.right_arrow.mousePressEvent = lambda e: self._on_mode_switch()
		mode_frame.addWidget(self.right_arrow)

		search_layout.addLayout(mode_frame)

		self.close_btn = QLabel("✕")
		self.close_btn.setFont(QFont("Arial", 13, QFont.Bold))
		self.close_btn.setStyleSheet("color: #999999; padding: 4px 6px;")
		self.close_btn.setCursor(Qt.PointingHandCursor)
		self.close_btn.mousePressEvent = lambda e: self._on_close()
		self.close_btn.enterEvent = lambda e: self.close_btn.setStyleSheet("color: #ff4444; padding: 4px 6px; background: rgba(255,68,68,0.1); border-radius: 4px;")
		self.close_btn.leaveEvent = lambda e: self.close_btn.setStyleSheet("color: #999999; padding: 4px 6px;")
		search_layout.addWidget(self.close_btn)

		main_layout.addLayout(search_layout)

		self.result_frame = QWidget()
		self.result_frame.setVisible(False)
		result_layout = QHBoxLayout(self.result_frame)
		result_layout.setContentsMargins(0, 0, 0, 0)

		self.result_listbox = EnterListWidget(on_enter_double=self._on_open)
		self.result_listbox.setFont(QFont("微软雅黑", 12))
		self.result_listbox.setMinimumHeight(300)
		self.result_listbox.setAlternatingRowColors(False)
		self.result_listbox.setFocusPolicy(Qt.StrongFocus)
		self.result_listbox.setMouseTracking(True)  # 启用鼠标追踪以接收 MouseMove 事件
		self._highlight_delegate = KeywordHighlightDelegate(self.result_listbox)
		self.result_listbox.setItemDelegate(self._highlight_delegate)
		self.result_listbox.itemDoubleClicked.connect(self._on_open)
		self.result_listbox.itemClicked.connect(self._on_item_clicked)
		self.result_listbox.setContextMenuPolicy(Qt.CustomContextMenu)
		self.result_listbox.customContextMenuRequested.connect(self._on_right_click)
		# 悬停预览相关
		self.result_listbox.installEventFilter(self)
		self._preview_tooltip = None
		self._last_hovered_row = -1
		# 自动加载：当滚动接近底部时自动加载下一页
		try:
			vsb = self.result_listbox.verticalScrollBar()
			vsb.valueChanged.connect(self._on_scroll)
		except Exception:
			pass
		result_layout.addWidget(self.result_listbox)

		main_layout.addWidget(self.result_frame)

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
		self.btn_delete.setStyleSheet("""
			color: #d32f2f;
			border: 1px solid rgba(211,47,47,0.2);
		}
		QPushButton:hover {
			background: rgba(211,47,47,0.08);
			border: 1px solid rgba(211,47,47,0.4);
		"""
		)
		self.btn_delete.clicked.connect(self._btn_delete)
		btn_layout.addWidget(self.btn_delete)

		self.btn_to_main = QPushButton("主页面查看")
		self.btn_to_main.clicked.connect(self._btn_to_main)
		btn_layout.addWidget(self.btn_to_main)

		# 加载更多按钮（默认隐藏，索引搜索时可见）
		self._load_more_btn = QPushButton("加载更多")
		self._load_more_btn.clicked.connect(self._load_more)
		self._load_more_btn.setVisible(False)
		btn_layout.addWidget(self._load_more_btn)

		btn_layout.addStretch()
		main_layout.addWidget(self.button_frame)

		self.tip_frame = QWidget()
		self.tip_frame.setVisible(False)
		tip_layout = QHBoxLayout(self.tip_frame)
		tip_layout.setContentsMargins(0, 5, 0, 0)

		self.tip_label = QLabel(
			"🎹 Enter=打开 | Ctrl+E=定位 | Ctrl+C=复制 | Ctrl+T=终端 | Ctrl+1-9=快速选 | Delete=删除 | Tab=主页 | Esc=关闭"
		)
		self.tip_label.setFont(QFont("微软雅黑", 10))
		self.tip_label.setStyleSheet("color: #4a6b72; line-height: 1.5;")
		tip_layout.addWidget(self.tip_label)

		main_layout.addWidget(self.tip_frame)

		self._create_context_menu()

		self.window.installEventFilter(self)
		self.search_entry.installEventFilter(self)

		# Connect text changed for instant search
		self.search_entry.textChanged.connect(self._on_text_changed)
		
		# Install custom mouse event filter on window for drag-to-move
		self.window.mousePressEvent = self._on_window_mouse_press
		self.window.mouseMoveEvent = self._on_window_mouse_move
		self.window.mouseReleaseEvent = self._on_window_mouse_release

		self.window.show()
		self.window.activateWindow()
		self.search_entry.setFocus()

	def eventFilter(self, obj, event):
		# 移除 MouseMove 悬停检测，改由选择时显示预览
		
		# 处理鼠标离开列表的情况
		if obj == self.result_listbox and event.type() == QEvent.Leave:
			self._last_hovered_row = -1
			# 恢复默认提示
			if hasattr(self, 'tip_label'):
				self.tip_label.setStyleSheet("color: #004466;")
				if self._results_all:
					self.tip_label.setText(f"共找到 {len(self._results_all)} 项 (显示 {self._results_shown})")
				else:
					self.tip_label.setText("按 Esc 关闭  |  Tab 到主窗口  |  双击 Enter=打开  Ctrl+E=定位")
			return False
		
		if event.type() == QEvent.KeyPress:
			key = event.key()
			modifiers = event.modifiers()
			# 按键处理

			if key == Qt.Key_Escape:
				self._on_close()
				return True
			if key == Qt.Key_Tab:
				self._on_switch_to_main()
				return True
			if key in (Qt.Key_Return, Qt.Key_Enter):
				# 输入框和结果列表的 Enter 由各自控件处理，这里不拦截
				return False
			if key == Qt.Key_C and modifiers & Qt.ControlModifier:
				self._on_copy_shortcut()
				return True
			if key == Qt.Key_E and modifiers & Qt.ControlModifier:
				self._on_locate()
				return True
			if key == Qt.Key_T and modifiers & Qt.ControlModifier:
				self._on_terminal_open()
				return True
			if key == Qt.Key_Delete:
				self._on_delete_shortcut()
				return True
			if key == Qt.Key_Up:
				if obj == self.result_listbox and self.result_listbox.currentRow() == 0:
					# 在结果列表顶部按上箭头，跳回输入框
					try:
						self.search_entry.setFocus()
						self.search_entry.selectAll()
						return True
					except Exception:
						pass
				else:
					self._on_up()
					return True
			if key == Qt.Key_Down:
				if obj == self.search_entry and self.results:
					# 在输入框按下箭头，跳到结果列表
					try:
						self.result_listbox.setFocus()
						if self.result_listbox.currentRow() < 0:
							self.result_listbox.setCurrentRow(0)
						return True
					except Exception:
						pass
				else:
					self._on_down()
					return True
			if Qt.Key_1 <= key <= Qt.Key_9 and modifiers & Qt.ControlModifier:
				num = key - Qt.Key_0
				if 0 < num <= len(self.results):
					self.result_listbox.setCurrentRow(num - 1)
				return True

			if obj == self.search_entry:
				text = self.search_entry.text()
				cursor = self.search_entry.cursorPosition()
				if key == Qt.Key_Left and cursor == 0:
					self._on_mode_switch()
					return True
				if key == Qt.Key_Right and cursor == len(text):
					self._on_mode_switch()
					return True

		return super().eventFilter(obj, event)

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
			self.mode_label.setText("⚡ 实时")
		else:
			self.search_mode = "index"
			self.mode_label.setText("⚡ Rust")

	def _on_text_changed(self, text):
		"""Instant search: search as user types (debounced)"""
		text = text.strip()
		# Only search if text changed significantly and is not empty
		if text:
			# 安全停止可能仍在运行的定时器
			try:
				if self._search_timer and self._search_timer.isActive():
					self._search_timer.stop()
			except Exception:
				pass
			# 重新启动定时器（去抖动）
			try:
				self._search_timer.start(300)
			except Exception:
				# 若出现异常，重新创建稳定定时器
				self._search_timer = QTimer(self)
				self._search_timer.setSingleShot(True)
				self._search_timer.timeout.connect(self._on_search)
				self._search_timer.start(300)
		elif not text:
			# clear results when search box is emptied
			self.result_listbox.clear()
			self.results.clear()
			self.result_frame.setVisible(False)
			self.button_frame.setVisible(False)
			self.tip_frame.setVisible(False)
			# 停止定时器，避免空输入触发搜索
			try:
				if self._search_timer and self._search_timer.isActive():
					self._search_timer.stop()
			except Exception:
				pass
			# shrink back to compact size
			try:
				self.window.setFixedSize(720, 70)
			except Exception:
				pass

	def _on_search(self, event=None):
		keyword = self.search_entry.text().strip()
		if not keyword:
			return

		self._last_search_keyword = keyword
		self.results.clear()
		self.result_listbox.clear()
		self._show_results_area()

		if self.search_mode == "index":
			self._search_index(keyword)
		else:
			self._search_realtime(keyword)

	def _search_index(self, keyword):
		eng = get_rust_search_engine()
		if not eng:
			self.result_listbox.addItem(QListWidgetItem("   ❌ Rust 引擎不可用"))
			return

		parser = SearchSyntaxParser()
		clean_kw, filters = parser.parse(keyword)

		scope_targets = self.app._get_search_scope_targets()
		drives = []
		for target in scope_targets:
			if len(target) >= 2 and target[1] == ":":
				drives.append(target[0].upper())
		if not drives:
			self.result_listbox.addItem(QListWidgetItem("   ⚠️ 未识别驱动器"))
			return

		collected = []
		try:
			if (not clean_kw) and filters and filters.get("date_after"):
				da = filters["date_after"]
				min_ts = da.timestamp() if hasattr(da, "timestamp") else float(da)
				for d in drives:
					part = eng.search_by_mtime_range(d, min_ts, 4.611686e18, 5000)
					for r in part:
						fn, fp, sz, is_dir, mt = r[0], r[1], r[2], r[3], r[4]
						collected.append({
							"filename": fn,
							"fullpath": fp,
							"size": sz,
							"mtime": mt,
							"is_dir": is_dir,
						})
			else:
				kw = clean_kw or keyword
				for d in drives:
					part = eng.search_contains(d, kw)
					for r in part:
						fn, fp, sz, is_dir, mt = r[0], r[1], r[2], r[3], r[4]
						collected.append({
							"filename": fn,
							"fullpath": fp,
							"size": sz,
							"mtime": mt,
							"is_dir": is_dir,
						})
		except Exception:
			self.result_listbox.addItem(QListWidgetItem("   ⚠️ 搜索失败"))
			return

		if filters:
			parser.filters = filters
			collected = parser.apply_filters(collected)

		# 保存所有结果，显示前 N 项（分页）
		out_all = []
		for item in collected:
			fn = item["filename"]
			fp = item["fullpath"]
			sz = item["size"]
			mt = item["mtime"]
			is_dir = 1 if item.get("is_dir") else 0
			out_all.append((fn, fp, sz, mt, is_dir))

		# 保存供分页使用
		self._results_all = out_all
		self._results_shown = 0
		# 显示第一页
		self._display_page((clean_kw or keyword).lower().split())

	def _search_realtime(self, keyword):
		self.result_listbox.addItem(QListWidgetItem("   🔍 正在搜索..."))
		QApplication.processEvents()

		keywords = keyword.lower().split()
		scope_targets = self.app._get_search_scope_targets()
		results = []
		count = 0

		for target in scope_targets:
			# 为实时搜索设置上限，避免长时间遍历造成卡顿
			if count >= (self.page_size * 10) or not os.path.isdir(target):
				continue
			try:
				for root, dirs, files in os.walk(target):
					dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS_LOWER and not d.startswith(".")]
					for name in files + dirs:
						if count >= 200:
							break
						if all(kw in name.lower() for kw in keywords):
							fp = os.path.join(root, name)
							is_dir = os.path.isdir(fp)
							try:
								st = os.stat(fp)
								sz, mt = ((0, st.st_mtime) if is_dir else (st.st_size, st.st_mtime))
							except Exception:
								sz, mt = 0, 0
							results.append((name, fp, sz, mt, 1 if is_dir else 0))
							count += 1
			except Exception:
				continue

		self.result_listbox.clear()
		self._display_results(results, keywords)

	def _display_results(self, results, keywords=None):
		keywords = keywords or []
		if self._highlight_delegate:
			self._highlight_delegate.set_keywords(keywords)
		if not results:
			self.result_listbox.addItem(QListWidgetItem("   😔 未找到匹配的文件"))
			return

		self.results = []
		for i, (fn, fp, sz, mt, is_dir) in enumerate(results):
			ext = os.path.splitext(fn)[1].lower()
			icon = "📁" if is_dir else ("📦" if ext in ARCHIVE_EXTS else "📄")

			item = QListWidgetItem(f"   {icon}  {fn}")
			item.setBackground(QColor("#ffffff" if i % 2 == 0 else "#e8f4f8"))
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
			self._show_current_preview()

		# 显示当前已经加载/展示的条数
		shown = len(self.results)
		total = len(self._results_all) if self._results_all else shown
		self.tip_label.setText(
			f"找到 {total} 个，已显示 {shown} 个 | 双击Enter=打开 | Ctrl+E=定位 | Ctrl+C=复制 | Delete=删除 | Tab=主页 | Esc=关闭"
		)
		# 根据是否还有未显示的结果，切换“加载更多”按钮可见性
		if total > shown:
			self._load_more_btn.setVisible(True)
		else:
			self._load_more_btn.setVisible(False)

	def _show_results_area(self):
		self.result_frame.setVisible(True)
		self.button_frame.setVisible(True)
		self.tip_frame.setVisible(True)

		# 只调整窗口大小，不改变位置（保持用户拖动的位置）
		self.window.setFixedSize(720, 480)
		# ensure load more button state is correct (默认隐藏)
		try:
			if self._load_more_btn:
				self._load_more_btn.setVisible(False)
		except Exception:
			pass

	def _get_current_item(self):
		if not self.results:
			return None
		row = self.result_listbox.currentRow()
		if row < 0 or row >= len(self.results):
			return None
		return self.results[row]

	def _display_page(self, keywords=None):
		"""Display next page (or first page) from self._results_all."""
		keywords = keywords or []
		# 将关键词传递给高亮委托（分页情况下也要高亮）
		try:
			if self._highlight_delegate:
				self._highlight_delegate.set_keywords(keywords)
		except Exception:
			pass
		start = self._results_shown
		end = min(start + self.page_size, len(self._results_all))
		page = self._results_all[start:end]
		# 如果是第一页，清空列表，否则追加
		if start == 0:
			self.result_listbox.clear()
			self.results = []
		for i, (fn, fp, sz, mt, is_dir) in enumerate(page, start=start):
			ext = os.path.splitext(fn)[1].lower()
			icon = "📁" if is_dir else ("📦" if ext in ARCHIVE_EXTS else "📄")
			item = QListWidgetItem(f"   {icon}  {fn}")
			item.setBackground(QColor("#ffffff" if i % 2 == 0 else "#e8f4f8"))
			self.result_listbox.addItem(item)
			self.results.append({
				"filename": fn,
				"fullpath": fp,
				"size": sz,
				"mtime": mt,
				"is_dir": is_dir,
			})
		self._results_shown = end
		if self.results:
			self.result_listbox.setCurrentRow(0)
			self._show_current_preview()
		# 更新提示与按钮状态
		self.tip_label.setText(f"找到 {len(self._results_all)} 个，已显示 {self._results_shown} 个 | 双击Enter=打开 | Ctrl+E=定位 | Ctrl+C=复制 | Delete=删除 | Tab=主页 | Esc=关闭")
		if self._results_shown < len(self._results_all):
			self._load_more_btn.setVisible(True)
		else:
			self._load_more_btn.setVisible(False)

	def _load_more(self):
		# Append next page (guard against并发触发)
		if self._loading_more:
			return
		if not self._results_all or self._results_shown >= len(self._results_all):
			return
		self._loading_more = True
		try:
			self._display_page()
		finally:
			self._loading_more = False

	def _on_scroll(self, value):
		"""Auto-load more when scrollbar nears bottom."""
		try:
			sb = self.result_listbox.verticalScrollBar()
			if sb is None:
				return
			maxv = sb.maximum()
			# 当接近底部 40px 时触发加载
			if maxv > 0 and value >= maxv - 40:
				if not self._loading_more and self._results_shown < len(self._results_all):
					# 使用微延迟以避免重复连续触发
					QTimer.singleShot(30, self._load_more)
		except Exception:
			pass

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
		try:
			if HAS_WIN32 and not item.get("is_dir") and os.path.exists(item["fullpath"]):
				try:
					import win32clipboard
					import win32con

					files = [os.path.abspath(item["fullpath"])]
					file_str = "\0".join(files) + "\0\0"
					data = struct.pack("IIIII", 20, 0, 0, 0, 1) + file_str.encode("utf-16le")
					win32clipboard.OpenClipboard()
					win32clipboard.EmptyClipboard()
					win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
					win32clipboard.CloseClipboard()
					return
				except Exception as e:
					logger.debug(f"文件复制失败，回退路径: {e}")
			QApplication.clipboard().setText(item["fullpath"])
		except Exception as e:
			logger.error(f"复制失败: {e}")

	def _on_delete_shortcut(self, event=None):
		item = self._get_current_item()
		if not item:
			return
		path = item["fullpath"]
		name = item["filename"]

		if HAS_SEND2TRASH and send2trash is not None:
			msg = f"确定删除？\n{name}\n\n将移动到回收站。"
		else:
			msg = f"确定永久删除？\n{name}\n\n⚠ 此操作不可恢复。"

		if QMessageBox.question(self.window, "确认删除", msg) != QMessageBox.Yes:
			return

		try:
			if HAS_SEND2TRASH and send2trash is not None:
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

	def _on_open(self, item=None):
		item = self._get_current_item()
		if not item:
			return
		try:
			if item["is_dir"]:
				subprocess.Popen(f'explorer "{item["fullpath"]}"')
			else:
				os.startfile(item["fullpath"])
		except Exception as e:
			logger.error(f"打开失败: {e}")

	def _on_locate(self, event=None):
		item = self._get_current_item()
		if not item:
			return
		try:
			subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
		except Exception as e:
			logger.error(f"定位失败: {e}")

	def _on_terminal_open(self, event=None):
		"""Open file explorer in terminal (cmd or PowerShell)"""
		item = self._get_current_item()
		if not item:
			return
		try:
			path = item["fullpath"]
			if item["is_dir"]:
				# For directory, open terminal in that directory
				subprocess.Popen(f'powershell -NoExit -Command "Set-Location \\"{path}\\""; Set-Location \\"{path}\\"')
			else:
				# For file, open terminal in parent directory and list the file
				parent_dir = os.path.dirname(path)
				subprocess.Popen(f'powershell -NoExit -Command "Set-Location \\"{parent_dir}\\""')
		except Exception as e:
			logger.error(f"终端打开失败: {e}")

	def _on_switch_to_main(self, event=None):
		try:
			keyword = self.search_entry.text().strip()
			results_copy = list(self.results)

			# 从迷你窗口切换到主窗口

			self.close()

			# Ensure main window is restored/unminimized and brought to front
			try:
				self.app.show()
				self.app.showNormal()
				# clear minimized state if present
				try:
					state = int(self.app.windowState())
					self.app.setWindowState(state & ~int(Qt.WindowMinimized))
				except Exception:
					pass
				self.app.raise_()
				self.app.activateWindow()
			except Exception:
				# ignore
				pass

			if keyword:
				self.app.entry_kw.setText(keyword)

				if results_copy:
					with self.app.results_lock:
						self.app.all_results.clear()
						self.app.filtered_results.clear()
						self.app.shown_paths.clear()

						for item in results_copy:
							ext = os.path.splitext(item["filename"])[1].lower()
							if item["is_dir"]:
								tc, ss = 0, "📂 文件夹"
							elif ext in ARCHIVE_EXTS:
								tc, ss = 1, "📦 压缩包"
							else:
								tc, ss = 2, format_size(item["size"])

							self.app.all_results.append({
								"filename": item["filename"],
								"fullpath": item["fullpath"],
								"dir_path": os.path.dirname(item["fullpath"]),
								"size": item["size"],
								"mtime": item["mtime"],
								"type_code": tc,
								"size_str": ss,
								"mtime_str": format_time(item["mtime"]),
							})
							self.app.shown_paths.add(item["fullpath"])

						self.app.filtered_results = list(self.app.all_results)
						self.app.total_found = len(self.app.all_results)

					self.app.current_page = 1
					self.app._update_ext_combo()
					# 通知主窗口 delegate 使用相同关键词进行高亮
					try:
						if getattr(self.app, "_main_highlight_delegate", None):
							self.app._main_highlight_delegate.set_keywords([keyword.lower()])
					except Exception:
						pass
					self.app._render_page()
					self.app.status.setText(f"✅ 从迷你窗口导入 {len(results_copy)} 个结果")
					self.app.btn_refresh.setEnabled(True)

			# ensure entry gets focus (delay to let window manager update)
			try:
				QTimer.singleShot(50, lambda: (self.app.entry_kw.setFocus(), self.app.entry_kw.selectAll()))
			except Exception:
				self.app.entry_kw.setFocus()
		except Exception:
			logger.exception("_on_switch_to_main failed")

	def _on_window_mouse_press(self, event):
		"""记录鼠标按下位置用于拖动窗口"""
		if event.button() == Qt.LeftButton:
			self._drag_start_pos = event.globalPos() - self.window.frameGeometry().topLeft()
			event.accept()

	def _on_window_mouse_move(self, event):
		"""在拖动时移动窗口"""
		if event.buttons() == Qt.LeftButton and self._drag_start_pos:
			try:
				self.window.move(event.globalPos() - self._drag_start_pos)
				event.accept()
			except Exception:
				pass

	def _on_window_mouse_release(self, event):
		"""释放鼠标时停止拖动并保存位置"""
		if self._drag_start_pos:
			# 保存窗口当前位置
			try:
				pos = self.window.pos()
				if self.config_mgr:
					self.config_mgr._settings.setValue(self._window_pos_key, f"{pos.x()},{pos.y()}")
					self.config_mgr._settings.sync()
			except Exception:
				pass
		self._drag_start_pos = None
		event.accept()

	def _show_preview_tooltip(self, result_item, global_pos):
		"""显示文件预览提示 - 在窗口底部状态栏显示"""
		try:
			fn = result_item.get("filename", "")
			fp = result_item.get("fullpath", "")
			sz = result_item.get("size", 0)
			mt = result_item.get("mtime", 0)
			is_dir = result_item.get("is_dir", False)
			
			if not fn:
				return
			
			# 构建预览文本 - 显示文件名、盘符和大小
			size_str = format_size(sz) if not is_dir else "文件夹"
			# 从路径中提取盘符
			drive = os.path.splitdrive(fp)[0] if fp else "?"
			preview_text = f"📄 {fn}  |  {drive}  |  大小: {size_str}"
			
			# 更新状态提示标签
			if hasattr(self, 'tip_label'):
				self.tip_label.setText(preview_text)
				self.tip_label.setStyleSheet("color: #006688; font-weight: bold;")
		except Exception as e:
			logger.debug(f"_show_preview_tooltip error: {e}")

	def _on_item_clicked(self, item):
		"""项被点击时显示预览"""
		row = self.result_listbox.row(item)
		if 0 <= row < len(self.results):
			result_item = self.results[row]
			self._show_preview_tooltip(result_item, None)

	def _show_current_preview(self):
		"""显示当前选中项的预览"""
		row = self.result_listbox.currentRow()
		if 0 <= row < len(self.results):
			result_item = self.results[row]
			self._show_preview_tooltip(result_item, None)
		if not self.results:
			return
		row = self.result_listbox.currentRow()
		if row > 0:
			self.result_listbox.setCurrentRow(row - 1)
			self._show_current_preview()

	def _on_down(self, event=None):
		if not self.results:
			return
		row = self.result_listbox.currentRow()
		if row < len(self.results) - 1:
			self.result_listbox.setCurrentRow(row + 1)
			self._show_current_preview()

	def _on_right_click(self, pos):
		if not self.results:
			return
		item = self.result_listbox.itemAt(pos)
		if item:
			row = self.result_listbox.row(item)
			self.result_listbox.setCurrentRow(row)
			self.ctx_menu.exec_(self.result_listbox.viewport().mapToGlobal(pos))

	def _handle_input_enter(self):
		"""输入框按 Enter：触发搜索（索引或实时模式由 _on_search 判断）"""
		self._on_search()

	def _open_top_or_current(self):
		"""Open current item if selected; otherwise open the first result."""
		try:
			if not self.results:
				return
			row = self.result_listbox.currentRow()
			if row < 0:
				self.result_listbox.setCurrentRow(0)
			self._on_open()
		except Exception:
			pass

	def _on_close(self, event=None):
		self.close()

	def close(self):
		if self.window:
			try:
				self.window.close()
			except Exception:
				pass
			self.window = None
		# 恢复 QMessageBox 原函数（如果我们曾经包装过）
		try:
			from PySide6.QtWidgets import QMessageBox as _QMsg
			for name, orig in list(self._orig_qmsg_funcs.items()):
				try:
					setattr(_QMsg, name, orig)
				except Exception:
					pass
		except Exception:
			pass
		self.results.clear()


class KeywordHighlightDelegate(QStyledItemDelegate):
	"""Draws QListWidget items while highlighting the current keywords."""

	def __init__(self, parent=None):
		super().__init__(parent)
		self._pattern = None

	def set_keywords(self, keywords):
		terms = [kw for kw in keywords if kw]
		if terms:
			joined = "|".join(re.escape(term) for term in terms)
			self._pattern = re.compile(joined, re.IGNORECASE)
		else:
			self._pattern = None
		# 设置关键词模式（不输出调试日志）

	def paint(self, painter, option, index):
		painter.save()
		bg_brush = index.data(Qt.BackgroundRole)
		# 如果当前行被选中，优先绘制选中背景（覆盖交替背景），避免选中时文字与背景颜色冲突
		if option.state & QStyle.State_Selected:
			painter.fillRect(option.rect, option.palette.highlight())
		elif bg_brush:
			painter.fillRect(option.rect, bg_brush)
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
			return self._render_text(escaped, option)
		# 检查是否能在文本中找到匹配
		m = self._pattern.search(escaped)
		# 使用与主窗口一致的浅黄色高亮
		# 选中时使用更深的高亮背景以提升对比度，未选中时使用浅黄色
		is_selected = bool(option.state & QStyle.State_Selected)
		text_color = (
			option.palette.highlightedText().color().name()
			if is_selected
			else option.palette.text().color().name()
		)
		# 深色高亮（选中时）与浅色高亮（未选中）
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
		return self._render_text(highlighted, option)

	def _render_text(self, html_text, option):
		is_selected = bool(option.state & QStyle.State_Selected)
		color = (
			option.palette.highlightedText().color().name()
			if is_selected
			else option.palette.text().color().name()
		)
		return f'<div style="color:{color};">{html_text}</div>'


__all__ = ["MiniSearchWindow"]
