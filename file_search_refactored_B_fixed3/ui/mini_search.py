"""MiniSearchWindow：从原版提取，逻辑不改。"""
from __future__ import annotations
from ..utils.constants import *
from ..config.manager import ConfigManager
from ..core.index_manager import IndexManager
from ..monitors.usn_watcher import UsnFileWatcher
from ..system.tray import TrayManager
from ..system.hotkey import HotkeyManager

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

    def show(self):
        """显示迷你窗口"""
        if self.window is not None:
            try:
                self.window.activateWindow()
                self.window.raise_()
                self.search_entry.setFocus()
                self.search_entry.selectAll()
                return
            except:
                self.window = None

        self._create_window()

    def _create_window(self):
        """创建窗口"""
        self.window = QDialog(None)
        self.window.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.window.setAttribute(Qt.WA_TranslucentBackground, False)
        self.window.setFixedSize(720, 70)
        self.window.setStyleSheet(
            """
            QDialog { background-color: #b8e0f0; border: 3px solid #006699; }
            QLineEdit { padding: 8px; font-size: 14px; border: 2px solid #88c0d8; border-radius: 4px; background: white; }
            QLineEdit:focus { border-color: #006699; }
            QListWidget { background: white; border: 1px solid #88c0d8; font-size: 11px; }
            QListWidget::item { padding: 4px; }
            QListWidget::item:selected { background-color: #006699; color: white; }
            QListWidget::item:hover { background-color: #e0f0f8; }
            QPushButton { padding: 5px 10px; background: white; border: 1px groove #ccc; border-radius: 3px; font-size: 9px; color: #004466; }
            QPushButton:hover { background: #e8f4f8; }
            QLabel { color: #004466; }
        """
        )

        # 居中显示
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - 720) // 2
        y = int(screen.height() * 0.20)
        self.window.move(x, y)

        # 主布局
        main_layout = QVBoxLayout(self.window)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.setSpacing(12)

        # 搜索图标
        self.search_icon = QLabel("🔍")
        self.search_icon.setFont(QFont("Segoe UI Emoji", 18))
        self.search_icon.setStyleSheet("color: #004466;")
        self.search_icon.setCursor(Qt.PointingHandCursor)
        self.search_icon.mousePressEvent = lambda e: self._on_search()
        search_layout.addWidget(self.search_icon)

        # 搜索框
        self.search_entry = QLineEdit()
        self.search_entry.setFont(QFont("微软雅黑", 14))
        self.search_entry.setPlaceholderText("输入关键词搜索...")
        search_layout.addWidget(self.search_entry, 1)

        # 模式切换
        mode_frame = QHBoxLayout()
        mode_frame.setSpacing(3)

        self.left_arrow = QLabel("◀")
        self.left_arrow.setFont(QFont("Arial", 12, QFont.Bold))
        self.left_arrow.setStyleSheet("color: #004466;")
        self.left_arrow.setCursor(Qt.PointingHandCursor)
        self.left_arrow.mousePressEvent = lambda e: self._on_mode_switch()
        mode_frame.addWidget(self.left_arrow)

        self.mode_label = QLabel("索引搜索")
        self.mode_label.setFont(QFont("微软雅黑", 10, QFont.Bold))
        self.mode_label.setFixedWidth(70)
        self.mode_label.setAlignment(Qt.AlignCenter)
        self.mode_label.setStyleSheet("color: #004466;")
        mode_frame.addWidget(self.mode_label)

        self.right_arrow = QLabel("▶")
        self.right_arrow.setFont(QFont("Arial", 12, QFont.Bold))
        self.right_arrow.setStyleSheet("color: #004466;")
        self.right_arrow.setCursor(Qt.PointingHandCursor)
        self.right_arrow.mousePressEvent = lambda e: self._on_mode_switch()
        mode_frame.addWidget(self.right_arrow)

        search_layout.addLayout(mode_frame)

        # 关闭按钮
        self.close_btn = QLabel("✕")
        self.close_btn.setFont(QFont("Arial", 14, QFont.Bold))
        self.close_btn.setStyleSheet("color: #666666;")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.mousePressEvent = lambda e: self._on_close()
        self.close_btn.enterEvent = lambda e: self.close_btn.setStyleSheet(
            "color: #cc0000;"
        )
        self.close_btn.leaveEvent = lambda e: self.close_btn.setStyleSheet(
            "color: #666666;"
        )
        search_layout.addWidget(self.close_btn)

        main_layout.addLayout(search_layout)

        # 结果列表（初始隐藏）
        self.result_frame = QWidget()
        self.result_frame.setVisible(False)
        result_layout = QHBoxLayout(self.result_frame)
        result_layout.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        self.result_listbox = QListWidget()
        self.result_listbox.setFont(QFont("微软雅黑", 11))
        self.result_listbox.setMinimumHeight(280)
        self.result_listbox.setAlternatingRowColors(False)
        self.result_listbox.itemDoubleClicked.connect(self._on_open)
        self.result_listbox.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_listbox.customContextMenuRequested.connect(self._on_right_click)
        result_layout.addWidget(self.result_listbox)

        main_layout.addWidget(self.result_frame)

        # 按钮栏（初始隐藏）
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
        self.btn_delete.setStyleSheet("color: #aa0000;")
        self.btn_delete.clicked.connect(self._btn_delete)
        btn_layout.addWidget(self.btn_delete)

        self.btn_to_main = QPushButton("主页面查看")
        self.btn_to_main.clicked.connect(self._btn_to_main)
        btn_layout.addWidget(self.btn_to_main)

        btn_layout.addStretch()
        main_layout.addWidget(self.button_frame)

        # 提示栏（初始隐藏）
        self.tip_frame = QWidget()
        self.tip_frame.setVisible(False)
        tip_layout = QHBoxLayout(self.tip_frame)
        tip_layout.setContentsMargins(0, 5, 0, 0)

        self.tip_label = QLabel(
            "Enter=打开  Ctrl+Enter=定位  Ctrl+C=复制  Delete=删除  Tab=主页面  Esc=关闭"
        )
        self.tip_label.setFont(QFont("微软雅黑", 9))
        self.tip_label.setStyleSheet("color: #004466;")
        tip_layout.addWidget(self.tip_label)

        main_layout.addWidget(self.tip_frame)

        # 创建右键菜单
        self._create_context_menu()

        # 安装事件过滤器
        self.window.installEventFilter(self)
        self.search_entry.installEventFilter(self)
        self.result_listbox.installEventFilter(self)

        # 显示窗口
        self.window.show()
        self.window.activateWindow()
        self.search_entry.setFocus()

    def eventFilter(self, obj, event):
        """事件过滤器"""
        if event.type() == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            if key == Qt.Key_Escape:
                self._on_close()
                return True

            if key == Qt.Key_Tab:
                self._on_switch_to_main()
                return True

            if key in (Qt.Key_Return, Qt.Key_Enter):
                if modifiers & Qt.ControlModifier:
                    self._on_locate()
                else:
                    self._on_search()
                return True

            if key == Qt.Key_C and modifiers & Qt.ControlModifier:
                self._on_copy_shortcut()
                return True

            if key == Qt.Key_Delete:
                self._on_delete_shortcut()
                return True

            if key == Qt.Key_Up:
                self._on_up()
                return True
            if key == Qt.Key_Down:
                self._on_down()
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
        """创建右键菜单"""
        self.ctx_menu = QMenu(self.window)
        self.ctx_menu.addAction("打开", self._btn_open)
        self.ctx_menu.addAction("定位", self._btn_locate)
        self.ctx_menu.addSeparator()
        self.ctx_menu.addAction("复制", self._btn_copy)
        self.ctx_menu.addSeparator()
        self.ctx_menu.addAction("删除", self._btn_delete)
        self.ctx_menu.addAction("主页面查看", self._btn_to_main)

    def _on_mode_switch(self, event=None):
        """切换搜索模式"""
        if self.search_mode == "index":
            self.search_mode = "realtime"
            self.mode_label.setText("实时搜索")
        else:
            self.search_mode = "index"
            self.mode_label.setText("索引搜索")

    def _on_search(self, event=None):
        """执行搜索"""
        keyword = self.search_entry.text().strip()
        if not keyword:
            return

        self.results.clear()
        self.result_listbox.clear()
        self._show_results_area()

        if self.search_mode == "index":
            self._search_index(keyword)
        else:
            self._search_realtime(keyword)

    def _search_index(self, keyword):
        """索引搜索"""
        if not self.app.index_mgr.is_ready:
            from PySide6.QtWidgets import QListWidgetItem

            self.result_listbox.addItem(
                QListWidgetItem("   ⚠️ 索引未就绪，请先构建索引")
            )
            return

        keywords = keyword.lower().split()
        scope_targets = self.app._get_search_scope_targets()
        results = self.app.index_mgr.search(keywords, scope_targets, limit=200)

        if results is None:
            from PySide6.QtWidgets import QListWidgetItem

            self.result_listbox.addItem(QListWidgetItem("   ⚠️ 搜索失败"))
            return

        self._display_results(results)

    def _search_realtime(self, keyword):
        """实时搜索"""
        from PySide6.QtWidgets import QListWidgetItem

        self.result_listbox.addItem(QListWidgetItem("   🔍 正在搜索..."))
        QApplication.processEvents()

        keywords = keyword.lower().split()
        scope_targets = self.app._get_search_scope_targets()
        results = []
        count = 0

        for target in scope_targets:
            if count >= 200 or not os.path.isdir(target):
                continue
            try:
                for root, dirs, files in os.walk(target):
                    dirs[:] = [
                        d
                        for d in dirs
                        if d.lower() not in SKIP_DIRS_LOWER and not d.startswith(".")
                    ]
                    for name in files + dirs:
                        if count >= 200:
                            break
                        if all(kw in name.lower() for kw in keywords):
                            fp = os.path.join(root, name)
                            is_dir = os.path.isdir(fp)
                            try:
                                st = os.stat(fp)
                                sz, mt = (
                                    (0, st.st_mtime)
                                    if is_dir
                                    else (st.st_size, st.st_mtime)
                                )
                            except:
                                sz, mt = 0, 0
                            results.append((name, fp, sz, mt, 1 if is_dir else 0))
                            count += 1
            except:
                continue

        self.result_listbox.clear()
        self._display_results(results)

    def _display_results(self, results):
        """显示搜索结果"""
        from PySide6.QtWidgets import QListWidgetItem

        if not results:
            self.result_listbox.addItem(QListWidgetItem("   😔 未找到匹配的文件"))
            return

        self.results = []
        for i, (fn, fp, sz, mt, is_dir) in enumerate(results):
            ext = os.path.splitext(fn)[1].lower()
            if is_dir:
                icon = "📁"
            elif ext in ARCHIVE_EXTS:
                icon = "📦"
            else:
                icon = "📄"

            item = QListWidgetItem(f"   {icon}  {fn}")
            if i % 2 == 0:
                item.setBackground(QColor("#ffffff"))
            else:
                item.setBackground(QColor("#e8f4f8"))

            self.result_listbox.addItem(item)
            self.results.append(
                {
                    "filename": fn,
                    "fullpath": fp,
                    "size": sz,
                    "mtime": mt,
                    "is_dir": is_dir,
                }
            )

        if self.results:
            self.result_listbox.setCurrentRow(0)

        self.tip_label.setText(
            f"找到 {len(self.results)} 个  │  Enter=打开  Ctrl+Enter=定位  Delete=删除  Tab=主页面  Esc=关闭"
        )

    def _show_results_area(self):
        """显示结果区域"""
        self.result_frame.setVisible(True)
        self.button_frame.setVisible(True)
        self.tip_frame.setVisible(True)

        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - 720) // 2
        y = int(screen.height() * 0.15)
        self.window.setFixedSize(720, 480)
        self.window.move(x, y)

    def _get_current_item(self):
        """获取当前选中项"""
        if not self.results:
            return None
        row = self.result_listbox.currentRow()
        if row < 0 or row >= len(self.results):
            return None
        return self.results[row]

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
        """复制路径"""
        item = self._get_current_item()
        if not item:
            return
        try:
            QApplication.clipboard().setText(item["fullpath"])
        except Exception as e:
            logger.error(f"复制路径失败: {e}")

    def _on_delete_shortcut(self, event=None):
        """删除文件"""
        item = self._get_current_item()
        if not item:
            return
        path = item["fullpath"]
        name = item["filename"]

        if HAS_SEND2TRASH:
            msg = f"确定删除？\n{name}\n\n将移动到回收站。"
        else:
            msg = f"确定永久删除？\n{name}\n\n⚠ 此操作不可恢复。"

        if QMessageBox.question(self.window, "确认删除", msg) != QMessageBox.Yes:
            return

        try:
            if HAS_SEND2TRASH:
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
        """打开文件"""
        item = self._get_current_item()
        if not item:
            return
        try:
            if item["is_dir"]:
                subprocess.Popen(f'explorer "{item["fullpath"]}"')
            else:
                os.startfile(item["fullpath"])
            self.close()
        except Exception as e:
            logger.error(f"打开失败: {e}")

    def _on_locate(self, event=None):
        """定位文件"""
        item = self._get_current_item()
        if not item:
            return
        try:
            subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
            self.close()
        except Exception as e:
            logger.error(f"定位失败: {e}")

    def _on_switch_to_main(self, event=None):
        """切换到主窗口"""
        keyword = self.search_entry.text().strip()
        results_copy = list(self.results)

        self.close()

        self.app.show()
        self.app.showNormal()
        self.app.raise_()
        self.app.activateWindow()

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

                        self.app.all_results.append(
                            {
                                "filename": item["filename"],
                                "fullpath": item["fullpath"],
                                "dir_path": os.path.dirname(item["fullpath"]),
                                "size": item["size"],
                                "mtime": item["mtime"],
                                "type_code": tc,
                                "size_str": ss,
                                "mtime_str": format_time(item["mtime"]),
                            }
                        )
                        self.app.shown_paths.add(item["fullpath"])

                    self.app.filtered_results = list(self.app.all_results)
                    self.app.total_found = len(self.app.all_results)

                self.app.current_page = 1
                self.app._update_ext_combo()
                self.app._render_page()
                self.app.status.setText(f"✅ 从迷你窗口导入 {len(results_copy)} 个结果")
                self.app.btn_refresh.setEnabled(True)

        self.app.entry_kw.setFocus()

    def _on_up(self, event=None):
        """向上选择"""
        if not self.results:
            return
        row = self.result_listbox.currentRow()
        if row > 0:
            self.result_listbox.setCurrentRow(row - 1)

    def _on_down(self, event=None):
        """向下选择"""
        if not self.results:
            return
        row = self.result_listbox.currentRow()
        if row < len(self.results) - 1:
            self.result_listbox.setCurrentRow(row + 1)

    def _on_right_click(self, pos):
        """右键菜单"""
        if not self.results:
            return
        item = self.result_listbox.itemAt(pos)
        if item:
            row = self.result_listbox.row(item)
            self.result_listbox.setCurrentRow(row)
            self.ctx_menu.exec_(self.result_listbox.viewport().mapToGlobal(pos))

    def _on_close(self, event=None):
        """关闭窗口"""
        self.close()

    def close(self):
        """关闭窗口"""
        if self.window:
            try:
                self.window.close()
            except:
                pass
            self.window = None
        self.results.clear()
