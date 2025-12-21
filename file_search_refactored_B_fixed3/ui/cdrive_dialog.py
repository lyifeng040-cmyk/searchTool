"""CDriveSettingsDialog：从原版提取，逻辑不改。"""
from __future__ import annotations
from ..utils.constants import *
from ..config.manager import ConfigManager
from ..core.index_manager import IndexManager
from ..monitors.usn_watcher import UsnFileWatcher
from ..system.tray import TrayManager
from ..system.hotkey import HotkeyManager

class CDriveSettingsDialog:
    """C盘目录设置对话框"""

    def __init__(self, parent, config_mgr, index_mgr=None, on_rebuild_callback=None):
        self.parent = parent
        self.config_mgr = config_mgr
        self.index_mgr = index_mgr
        self.on_rebuild_callback = on_rebuild_callback
        self.dialog = None
        self.path_vars = {}
        self.paths_frame = None
        self.scroll_area = None
        self.stat_label = None
        self.original_paths = []

    def show(self):
        """显示对话框"""
        self.dialog = QDialog(self.parent)
        self.dialog.setWindowTitle("⚙️ C盘扫描目录设置")
        self.dialog.setMinimumSize(650, 500)
        self.dialog.setModal(True)

        self.original_paths = [p.copy() for p in self.config_mgr.get_c_scan_paths()]
        self._build_ui()
        self.dialog.exec_()

    def _build_ui(self):
        """构建UI"""
        main_layout = QVBoxLayout(self.dialog)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # 说明文字
        desc_label = QLabel(
            "设置C盘索引扫描的目录范围，勾选启用，取消勾选禁用，点击 ✕ 删除"
        )
        desc_label.setFont(QFont("微软雅黑", 9))
        desc_label.setStyleSheet("color: #666;")
        main_layout.addWidget(desc_label)

        # 按钮行
        btn_row = QHBoxLayout()

        title_label = QLabel("扫描目录列表:")
        title_label.setFont(QFont("微软雅黑", 10, QFont.Bold))
        btn_row.addWidget(title_label)
        btn_row.addStretch()

        browse_btn = QPushButton("+ 浏览添加")
        browse_btn.clicked.connect(self._browse_add)
        btn_row.addWidget(browse_btn)

        manual_btn = QPushButton("+ 手动输入")
        manual_btn.clicked.connect(self._manual_add)
        btn_row.addWidget(manual_btn)

        main_layout.addLayout(btn_row)

        # 快捷操作行
        quick_row = QHBoxLayout()

        select_all_btn = QPushButton("✓ 全选")
        select_all_btn.clicked.connect(self._select_all)
        quick_row.addWidget(select_all_btn)

        select_none_btn = QPushButton("✗ 全不选")
        select_none_btn.clicked.connect(self._select_none)
        quick_row.addWidget(select_none_btn)

        select_invert_btn = QPushButton("↻ 反选")
        select_invert_btn.clicked.connect(self._select_invert)
        quick_row.addWidget(select_invert_btn)

        quick_row.addStretch()

        self.stat_label = QLabel("")
        self.stat_label.setFont(QFont("微软雅黑", 9))
        self.stat_label.setStyleSheet("color: #666;")
        quick_row.addWidget(self.stat_label)

        main_layout.addLayout(quick_row)

        # 滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet(
            "QScrollArea { background-color: #fafafa; border: 1px solid #ddd; }"
        )

        self.paths_frame = QWidget()
        self.paths_layout = QVBoxLayout(self.paths_frame)
        self.paths_layout.setContentsMargins(5, 5, 5, 5)
        self.paths_layout.setSpacing(2)
        self.paths_layout.addStretch()

        self.scroll_area.setWidget(self.paths_frame)
        main_layout.addWidget(self.scroll_area, 1)

        self._refresh_paths_list()

        # 底部按钮
        bottom_layout = QHBoxLayout()

        reset_btn = QPushButton("恢复系统默认")
        reset_btn.clicked.connect(self._reset_default)
        bottom_layout.addWidget(reset_btn)

        bottom_layout.addStretch()

        rebuild_btn = QPushButton("🔄 立即重建C盘")
        rebuild_btn.clicked.connect(self._rebuild_c_drive)
        bottom_layout.addWidget(rebuild_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.dialog.reject)
        bottom_layout.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save)
        bottom_layout.addWidget(save_btn)

        main_layout.addLayout(bottom_layout)

    def _refresh_paths_list(self):
        """刷新路径列表"""
        while self.paths_layout.count() > 1:
            item = self.paths_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.path_vars.clear()
        paths = self.config_mgr.get_c_scan_paths()

        if not paths:
            empty_label = QLabel("（暂无目录，请点击上方按钮添加）")
            empty_label.setFont(QFont("微软雅黑", 9))
            empty_label.setStyleSheet("color: gray;")
            self.paths_layout.insertWidget(0, empty_label)
            self._update_stats()
            return

        for i, item in enumerate(paths):
            path = item.get("path", "")
            enabled = item.get("enabled", True)

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(5, 2, 5, 2)
            row_layout.setSpacing(8)

            cb = QCheckBox()
            cb.setChecked(enabled)
            cb.stateChanged.connect(self._update_stats)
            self.path_vars[path] = cb
            row_layout.addWidget(cb)

            path_exists = os.path.isdir(path)
            max_len = 55
            if len(path) > max_len:
                display_path = path[:20] + "..." + path[-(max_len - 23) :]
            else:
                display_path = path

            if not path_exists:
                display_path = f"{display_path}  (不存在)"

            path_label = QLabel(display_path)
            path_label.setFont(QFont("Consolas", 9))
            path_label.setStyleSheet(f"color: {'#333' if path_exists else 'red'};")
            path_label.setToolTip(path)
            path_label.setCursor(Qt.PointingHandCursor)
            row_layout.addWidget(path_label, 1)

            del_btn = QPushButton("✕")
            del_btn.setFixedWidth(30)
            del_btn.setStyleSheet("color: red;")
            del_btn.clicked.connect(lambda checked, p=path: self._delete_path(p))
            row_layout.addWidget(del_btn)

            self.paths_layout.insertWidget(i, row_widget)

        self._update_stats()

    def _select_all(self):
        for cb in self.path_vars.values():
            cb.setChecked(True)
        self._update_stats()

    def _select_none(self):
        for cb in self.path_vars.values():
            cb.setChecked(False)
        self._update_stats()

    def _select_invert(self):
        for cb in self.path_vars.values():
            cb.setChecked(not cb.isChecked())
        self._update_stats()

    def _update_stats(self):
        total = len(self.path_vars)
        enabled = sum(1 for cb in self.path_vars.values() if cb.isChecked())
        self.stat_label.setText(f"共 {total} 个目录，已启用 {enabled} 个")

    def _browse_add(self):
        path = QFileDialog.getExistingDirectory(self.dialog, "选择C盘目录", "C:\\")
        if path:
            self._add_path(path)

    def _manual_add(self):
        text, ok = QInputDialog.getText(
            self.dialog, "手动输入C盘目录路径", "路径:", QLineEdit.Normal, ""
        )
        if ok and text:
            self._add_path(text.strip())

    def _add_path(self, path):
        path = os.path.normpath(path)

        if not path.upper().startswith("C:"):
            QMessageBox.warning(self.dialog, "错误", "只能添加C盘路径")
            return False

        if not os.path.isdir(path):
            QMessageBox.warning(self.dialog, "错误", "路径不存在")
            return False

        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            if os.path.normpath(p["path"]).lower() == path.lower():
                QMessageBox.warning(self.dialog, "提示", "路径已存在")
                return False

        paths.append({"path": path, "enabled": True})
        self.config_mgr.set_c_scan_paths(paths)
        self._refresh_paths_list()
        return True

    def _delete_path(self, path):
        if (
            QMessageBox.question(self.dialog, "确认", f"确定删除此目录？\n{path}")
            != QMessageBox.Yes
        ):
            return

        paths = self.config_mgr.get_c_scan_paths()
        paths = [
            p
            for p in paths
            if os.path.normpath(p["path"]).lower() != os.path.normpath(path).lower()
        ]
        self.config_mgr.set_c_scan_paths(paths)
        self._refresh_paths_list()

    def _reset_default(self):
        if (
            QMessageBox.question(
                self.dialog, "确认", "确定恢复系统默认目录？\n这将清空当前列表。"
            )
            == QMessageBox.Yes
        ):
            self.config_mgr.reset_c_scan_paths()
            self._refresh_paths_list()

    def _save(self):
        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            path = p["path"]
            if path in self.path_vars:
                p["enabled"] = self.path_vars[path].isChecked()

        self.config_mgr.set_c_scan_paths(paths)

        current_paths = self.config_mgr.get_c_scan_paths()
        has_changes = self._detect_changes(current_paths)

        if has_changes:
            result = QMessageBox.question(
                self.dialog,
                "设置已保存",
                "C盘目录配置已更改。\n\n是否立即重建C盘索引？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )

            if result == QMessageBox.Yes:
                self.dialog.accept()
                self._do_rebuild_c_drive()
            elif result == QMessageBox.No:
                QMessageBox.information(
                    self.dialog, "提示", "设置已保存，稍后可手动重建C盘索引"
                )
                self.dialog.accept()
        else:
            QMessageBox.information(self.dialog, "成功", "设置已保存")
            self.dialog.accept()

    def _detect_changes(self, current_paths):
        if len(current_paths) != len(self.original_paths):
            return True

        for curr, orig in zip(current_paths, self.original_paths):
            if curr.get("path") != orig.get("path"):
                return True
            if curr.get("enabled") != orig.get("enabled"):
                return True

        return False

    def _rebuild_c_drive(self):
        paths = self.config_mgr.get_c_scan_paths()
        for p in paths:
            path = p["path"]
            if path in self.path_vars:
                p["enabled"] = self.path_vars[path].isChecked()
        self.config_mgr.set_c_scan_paths(paths)

        if (
            QMessageBox.question(self.dialog, "确认", "确定立即重建C盘索引?")
            == QMessageBox.Yes
        ):
            self.dialog.accept()
            self._do_rebuild_c_drive()

    def _do_rebuild_c_drive(self):
        if self.on_rebuild_callback:
            self.on_rebuild_callback("C")
