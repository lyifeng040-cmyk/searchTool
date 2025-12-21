#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极速文件搜索 V42 增强版 - PySide6 版本
功能: C盘目录设置、磁盘筛选联动、全局热键、系统托盘
"""

import os
import sys
import re
import time
import queue
import struct
import shutil
import string
import logging
import platform
import threading
import datetime
import math
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable, Any

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QTreeWidget,
    QTreeWidgetItem, QMenu, QMenuBar, QStatusBar, QProgressBar,
    QFileDialog, QMessageBox, QDialog, QDialogButtonBox, QTextEdit,
    QListWidget, QListWidgetItem, QFrame, QSplitter, QGroupBox,
    QFormLayout, QSpinBox, QTabWidget, QScrollArea, QSizePolicy,
    QHeaderView, QAbstractItemView, QSystemTrayIcon, QStyle,
    QToolButton, QWidgetAction, QGridLayout
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QTimer, QSize, QPoint, QRect,
    QSettings, QUrl, QMimeData, QEvent, Slot
)
from PySide6.QtGui import (
    QAction, QIcon, QFont, QColor, QPalette, QClipboard,
    QKeySequence, QShortcut, QPixmap, QBrush, QCursor,
    QDesktopServices
)

# 可选依赖
HAS_WIN32 = False
HAS_TRAY = True  # PySide6 内置托盘支持
HAS_SEND2TRASH = False

try:
    import win32clipboard
    import win32con
    import win32api
    import win32gui
    HAS_WIN32 = True
except ImportError:
    pass

try:
    import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    pass

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== 常量定义 ====================
ARCHIVE_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.cab', '.iso'}
SKIP_EXTS = {'.tmp', '.temp', '.cache', '.log', '.bak', '.old'}
SKIP_DIRS = {'$recycle.bin', 'system volume information', 'windows', 
             'programdata', 'recovery', 'config.msi', '$windows.~bt',
             '$windows.~ws', 'windowsapps', 'node_modules', '.git',
             '__pycache__', '.vscode', '.idea'}
SKIP_PATHS = {'c:\\windows', 'c:\\$recycle.bin', 'c:\\system volume information'}

DEFAULT_C_DIRS = [
    "C:\\Users",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
]

# ==================== 工具函数 ====================
def format_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"

def format_time(timestamp: float) -> str:
    """格式化时间戳"""
    if timestamp <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
    except (ValueError, OSError):
        return "-"

def should_skip_dir(name: str) -> bool:
    """判断是否跳过目录"""
    return name in SKIP_DIRS

def should_skip_path(path: str) -> bool:
    """判断是否跳过路径"""
    path_lower = path.lower()
    for skip in SKIP_PATHS:
        if path_lower.startswith(skip):
            return True
    return False

def fuzzy_match(pattern: str, text: str) -> int:
    """模糊匹配，返回匹配度 0-100"""
    if not pattern or not text:
        return 0
    pattern = pattern.lower()
    text = text.lower()
    if pattern in text:
        return 100
    
    # 简单的模糊匹配算法
    pi = 0
    matched = 0
    for char in text:
        if pi < len(pattern) and char == pattern[pi]:
            matched += 1
            pi += 1
    
    if pi == len(pattern):
        return int(matched / len(text) * 100) if text else 0
    return 0

def get_c_scan_dirs(config_mgr) -> List[str]:
    """获取C盘扫描目录"""
    custom_dirs = config_mgr.get_c_drive_dirs()
    if custom_dirs:
        return [d for d in custom_dirs if os.path.exists(d)]
    return [d for d in DEFAULT_C_DIRS if os.path.exists(d)]

def parse_search_scope(scope: str, get_drives_func, config_mgr) -> List[str]:
    """解析搜索范围"""
    if "所有磁盘" in scope:
        targets = []
        for drive in get_drives_func():
            if drive.upper().startswith("C:"):
                targets.extend(get_c_scan_dirs(config_mgr))
            else:
                targets.append(drive)
        return targets
    elif os.path.isdir(scope):
        return [scope]
    else:
        drive = scope.rstrip("\\")
        if drive.upper().startswith("C:"):
            return get_c_scan_dirs(config_mgr)
        return [scope]


# ==================== 配置管理器 ====================
class ConfigManager:
    """配置管理器"""
    def __init__(self):
        self.config_dir = Path.home() / ".fastsearch"
        self.config_dir.mkdir(exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        self.config = self._load()
    
    def _load(self) -> dict:
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
        return self._default_config()
    
    def _default_config(self) -> dict:
        return {
            "theme": "fusion",
            "favorites": [],
            "search_history": [],
            "c_drive_dirs": DEFAULT_C_DIRS.copy(),
            "hotkey_enabled": True,
            "tray_enabled": True,
            "last_scope": "所有磁盘 (全盘)"
        }
    
    def save(self):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
    
    def get_theme(self) -> str:
        return self.config.get("theme", "fusion")
    
    def set_theme(self, theme: str):
        self.config["theme"] = theme
        self.save()
    
    def get_favorites(self) -> List[dict]:
        return self.config.get("favorites", [])
    
    def add_favorite(self, path: str):
        favs = self.get_favorites()
        name = os.path.basename(path) or path
        if not any(f["path"] == path for f in favs):
            favs.append({"name": name, "path": path})
            self.config["favorites"] = favs
            self.save()
    
    def remove_favorite(self, path: str):
        favs = self.get_favorites()
        self.config["favorites"] = [f for f in favs if f["path"] != path]
        self.save()
    
    def get_history(self) -> List[str]:
        return self.config.get("search_history", [])
    
    def add_history(self, keyword: str):
        history = self.get_history()
        if keyword in history:
            history.remove(keyword)
        history.insert(0, keyword)
        self.config["search_history"] = history[:50]
        self.save()
    
    def get_c_drive_dirs(self) -> List[str]:
        return self.config.get("c_drive_dirs", DEFAULT_C_DIRS)
    
    def set_c_drive_dirs(self, dirs: List[str]):
        self.config["c_drive_dirs"] = dirs
        self.save()
    
    def get_hotkey_enabled(self) -> bool:
        return self.config.get("hotkey_enabled", True)
    
    def set_hotkey_enabled(self, enabled: bool):
        self.config["hotkey_enabled"] = enabled
        self.save()
    
    def get_tray_enabled(self) -> bool:
        return self.config.get("tray_enabled", True)
    
    def set_tray_enabled(self, enabled: bool):
        self.config["tray_enabled"] = enabled
        self.save()


# ==================== 索引管理器 ====================
class IndexManager:
    """索引管理器 - 简化版"""
    def __init__(self, db_path: str = None, config_mgr: ConfigManager = None):
        self.config_mgr = config_mgr or ConfigManager()
        self.db_path = db_path or str(self.config_mgr.config_dir / "index.db")
        self.is_ready = False
        self.is_building = False
        self.file_count = 0
        self.last_update = 0
        self.has_fts = False
        self.used_mft = False
        self._lock = threading.Lock()
        self._data = []  # 简化存储
        self._load_index()
    
    def _load_index(self):
        """加载索引"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._data = data.get("files", [])
                    self.file_count = len(self._data)
                    self.last_update = data.get("update_time", 0)
                    self.is_ready = self.file_count > 0
                    self.has_fts = True
                    logger.info(f"索引已加载: {self.file_count} 个文件")
            except Exception as e:
                logger.error(f"加载索引失败: {e}")
                self.is_ready = False
    
    def _save_index(self):
        """保存索引"""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "files": self._data,
                    "update_time": time.time()
                }, f)
            logger.info(f"索引已保存: {len(self._data)} 个文件")
        except Exception as e:
            logger.error(f"保存索引失败: {e}")
    
    def build_index(self, drives: List[str], progress_cb: Callable = None, 
                    stop_check: Callable = None):
        """构建索引"""
        if self.is_building:
            return
        
        self.is_building = True
        self.is_ready = False
        self._data = []
        count = 0
        
        try:
            for drive in drives:
                if stop_check and stop_check():
                    break
                
                targets = parse_search_scope(drive, lambda: drives, self.config_mgr)
                
                for target in targets:
                    if stop_check and stop_check():
                        break
                    
                    for root, dirs, files in os.walk(target):
                        if stop_check and stop_check():
                            break
                        
                        # 过滤跳过的目录
                        dirs[:] = [d for d in dirs if not should_skip_dir(d.lower())]
                        
                        for name in dirs:
                            path = os.path.join(root, name)
                            self._data.append((name, path, 0, 0, True))
                            count += 1
                        
                        for name in files:
                            try:
                                path = os.path.join(root, name)
                                st = os.stat(path)
                                self._data.append((name, path, st.st_size, st.st_mtime, False))
                                count += 1
                            except (OSError, PermissionError):
                                pass
                        
                        if progress_cb and count % 1000 == 0:
                            progress_cb(count, root)
            
            self.file_count = count
            self.last_update = time.time()
            self.is_ready = True
            self._save_index()
            
        except Exception as e:
            logger.error(f"构建索引失败: {e}")
        finally:
            self.is_building = False
    
    def rebuild_drive(self, drive_letter: str, progress_cb: Callable = None,
                      stop_check: Callable = None):
        """重建单个驱动器索引"""
        self.build_index([f"{drive_letter}:\\"], progress_cb, stop_check)
    
    def search(self, keywords: List[str], scope_targets: List[str] = None) -> List[tuple]:
        """搜索索引"""
        if not self.is_ready:
            return None
        
        results = []
        for name, path, size, mtime, is_dir in self._data:
            name_lower = name.lower()
            if all(kw in name_lower for kw in keywords):
                if scope_targets:
                    if any(path.lower().startswith(t.lower()) for t in scope_targets):
                        results.append((name, path, size, mtime, is_dir))
                else:
                    results.append((name, path, size, mtime, is_dir))
        
        return results
    
    def get_stats(self) -> dict:
        """获取索引统计信息"""
        return {
            "count": self.file_count,
            "ready": self.is_ready,
            "building": self.is_building,
            "time": self.last_update,
            "path": self.db_path,
            "has_fts": self.has_fts,
            "used_mft": self.used_mft
        }
    
    def reload_stats(self):
        """重新加载统计"""
        self._load_index()
    
    def change_db_path(self, new_path: str) -> Tuple[bool, str]:
        """更改数据库路径"""
        try:
            self.db_path = new_path
            self._data = []
            self.is_ready = False
            self.file_count = 0
            return True, "路径已更改"
        except Exception as e:
            return False, str(e)
    
    def close(self):
        """关闭索引管理器"""
        pass


# ==================== 文件监控器 ====================
class FileWatcher:
    """文件监控器"""
    def __init__(self, index_mgr: IndexManager, config_mgr: ConfigManager = None):
        self.index_mgr = index_mgr
        self.config_mgr = config_mgr
        self.running = False
        self._thread = None
    
    def start(self, drives: List[str] = None):
        """启动监控"""
        self.running = True
        logger.info("文件监控已启动")
    
    def stop(self):
        """停止监控"""
        self.running = False
        logger.info("文件监控已停止")


# ==================== 托盘管理器 ====================
class TrayManager:
    """系统托盘管理器"""
    def __init__(self, app: 'SearchApp'):
        self.app = app
        self.tray_icon = None
        self.running = False
    
    def start(self):
        """启动托盘"""
        if not self.app.main_window:
            return
        
        self.tray_icon = QSystemTrayIcon(self.app.main_window)
        
        # 使用系统默认图标
        icon = self.app.main_window.style().standardIcon(QStyle.SP_FileDialogStart)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("极速文件搜索 V42")
        
        # 创建托盘菜单
        menu = QMenu()
        
        show_action = QAction("显示主窗口", menu)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)
        
        mini_action = QAction("迷你搜索", menu)
        mini_action.triggered.connect(self._show_mini)
        menu.addAction(mini_action)
        
        menu.addSeparator()
        
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_activated)
        self.tray_icon.show()
        self.running = True
    
    def stop(self):
        """停止托盘"""
        if self.tray_icon:
            self.tray_icon.hide()
            self.running = False
    
    def show_notification(self, title: str, message: str):
        """显示通知"""
        if self.tray_icon:
            self.tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 2000)
    
    def _show_window(self):
        if self.app.main_window:
            self.app.main_window.show()
            self.app.main_window.activateWindow()
    
    def _show_mini(self):
        self.app.show_mini_search()
    
    def _quit(self):
        self.app.do_quit()
    
    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()


# ==================== 热键管理器 ====================
class HotkeyManager:
    """全局热键管理器"""
    def __init__(self, app: 'SearchApp'):
        self.app = app
        self.registered = False
        self._thread = None
        self._stop_event = threading.Event()
    
    def start(self):
        """启动热键监听"""
        if not HAS_WIN32:
            logger.warning("全局热键需要 pywin32")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        self.registered = True
        logger.info("全局热键已注册")
    
    def stop(self):
        """停止热键监听"""
        self._stop_event.set()
        self.registered = False
    
    def _listen(self):
        """监听热键"""
        try:
            # Ctrl+Shift+Space
            HOTKEY_ID = 1
            MOD = win32con.MOD_CONTROL | win32con.MOD_SHIFT
            VK = win32con.VK_SPACE
            
            win32gui.RegisterHotKey(None, HOTKEY_ID, MOD, VK)
            
            while not self._stop_event.is_set():
                try:
                    msg = win32gui.GetMessage(None, 0, 0)
                    if msg and msg[1][1] == win32con.WM_HOTKEY:
                        self.app.show_mini_search_from_thread()
                except Exception:
                    pass
            
            win32gui.UnregisterHotKey(None, HOTKEY_ID)
        except Exception as e:
            logger.error(f"热键注册失败: {e}")


# ==================== 迷你搜索窗口 ====================
class MiniSearchWindow(QDialog):
    """迷你搜索窗口"""
    def __init__(self, app: 'SearchApp'):
        super().__init__()
        self.app = app
        self._setup_ui()
    
    def _setup_ui(self):
        self.setWindowTitle("快速搜索")
        self.setFixedSize(500, 60)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("输入关键词搜索...")
        self.entry.setFont(QFont("微软雅黑", 14))
        self.entry.returnPressed.connect(self._do_search)
        layout.addWidget(self.entry)
        
        self.btn_search = QPushButton("🔍")
        self.btn_search.setFixedSize(40, 40)
        self.btn_search.clicked.connect(self._do_search)
        layout.addWidget(self.btn_search)
    
    def _do_search(self):
        kw = self.entry.text().strip()
        if kw:
            self.hide()
            self.app.main_window.show()
            self.app.main_window.activateWindow()
            self.app.kw_var = kw
            self.app.entry_kw.setText(kw)
            self.app.start_search()
    
    def show_centered(self):
        """居中显示"""
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 3
        self.move(x, y)
        self.entry.clear()
        self.show()
        self.entry.setFocus()


# ==================== C盘设置对话框 ====================
class CDriveSettingsDialog(QDialog):
    """C盘目录设置对话框"""
    def __init__(self, parent, config_mgr: ConfigManager, index_mgr: IndexManager,
                 rebuild_callback: Callable):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.index_mgr = index_mgr
        self.rebuild_callback = rebuild_callback
        self._setup_ui()
    
    def _setup_ui(self):
        self.setWindowTitle("C盘目录设置")
        self.setFixedSize(500, 450)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        
        # 说明
        info_label = QLabel(
            "💡 为提高搜索效率，可自定义C盘扫描目录\n"
            "默认跳过 Windows、System 等系统目录"
        )
        info_label.setStyleSheet("color: #666; padding: 10px;")
        layout.addWidget(info_label)
        
        # 目录列表
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.list_widget)
        
        # 加载当前目录
        for d in self.config_mgr.get_c_drive_dirs():
            item = QListWidgetItem(d)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.list_widget.addItem(item)
        
        # 按钮组
        btn_layout = QHBoxLayout()
        
        btn_add = QPushButton("添加目录")
        btn_add.clicked.connect(self._add_dir)
        btn_layout.addWidget(btn_add)
        
        btn_remove = QPushButton("删除选中")
        btn_remove.clicked.connect(self._remove_dir)
        btn_layout.addWidget(btn_remove)
        
        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self._reset_default)
        btn_layout.addWidget(btn_reset)
        
        layout.addLayout(btn_layout)
        
        # 底部按钮
        bottom_layout = QHBoxLayout()
        
        btn_save = QPushButton("保存")
        btn_save.clicked.connect(self._save)
        bottom_layout.addWidget(btn_save)
        
        btn_rebuild = QPushButton("保存并重建C盘索引")
        btn_rebuild.clicked.connect(self._save_and_rebuild)
        bottom_layout.addWidget(btn_rebuild)
        
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        bottom_layout.addWidget(btn_cancel)
        
        layout.addLayout(bottom_layout)
    
    def _add_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择目录", "C:\\")
        if path:
            self.list_widget.addItem(path)
    
    def _remove_dir(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))
    
    def _reset_default(self):
        self.list_widget.clear()
        for d in DEFAULT_C_DIRS:
            self.list_widget.addItem(d)
    
    def _get_dirs(self) -> List[str]:
        return [self.list_widget.item(i).text() 
                for i in range(self.list_widget.count())]
    
    def _save(self):
        self.config_mgr.set_c_drive_dirs(self._get_dirs())
        QMessageBox.information(self, "成功", "设置已保存")
        self.accept()
    
    def _save_and_rebuild(self):
        self.config_mgr.set_c_drive_dirs(self._get_dirs())
        self.accept()
        self.rebuild_callback("C")


# ==================== 批量重命名对话框 ====================
class BatchRenameDialog(QDialog):
    """批量重命名对话框"""
    def __init__(self, parent, targets: List[dict], app: 'SearchApp'):
        super().__init__(parent)
        self.targets = targets
        self.app = app
        self._setup_ui()
    
    def _setup_ui(self):
        self.setWindowTitle("批量重命名")
        self.setMinimumSize(600, 500)
        
        layout = QVBoxLayout(self)
        
        # 模式选择
        mode_group = QGroupBox("重命名模式")
        mode_layout = QFormLayout(mode_group)
        
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["查找替换", "添加前缀", "添加后缀", "序号命名"])
        mode_layout.addRow("模式:", self.mode_combo)
        
        self.find_edit = QLineEdit()
        mode_layout.addRow("查找:", self.find_edit)
        
        self.replace_edit = QLineEdit()
        mode_layout.addRow("替换为:", self.replace_edit)
        
        layout.addWidget(mode_group)
        
        # 预览列表
        preview_group = QGroupBox("预览")
        preview_layout = QVBoxLayout(preview_group)
        
        self.preview_list = QListWidget()
        preview_layout.addWidget(self.preview_list)
        
        btn_preview = QPushButton("刷新预览")
        btn_preview.clicked.connect(self._update_preview)
        preview_layout.addWidget(btn_preview)
        
        layout.addWidget(preview_group)
        
        # 操作按钮
        btn_layout = QHBoxLayout()
        
        btn_execute = QPushButton("执行重命名")
        btn_execute.clicked.connect(self._execute)
        btn_layout.addWidget(btn_execute)
        
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        
        layout.addLayout(btn_layout)
    
    def _update_preview(self):
        self.preview_list.clear()
        mode = self.mode_combo.currentText()
        find_text = self.find_edit.text()
        replace_text = self.replace_edit.text()
        
        for item in self.targets[:100]:
            old_name = item['filename']
            new_name = old_name
            
            if mode == "查找替换" and find_text:
                new_name = old_name.replace(find_text, replace_text)
            elif mode == "添加前缀":
                new_name = replace_text + old_name
            elif mode == "添加后缀":
                base, ext = os.path.splitext(old_name)
                new_name = base + replace_text + ext
            elif mode == "序号命名":
                idx = self.targets.index(item) + 1
                base, ext = os.path.splitext(old_name)
                new_name = f"{replace_text}{idx:04d}{ext}"
            
            self.preview_list.addItem(f"{old_name} → {new_name}")
    
    def _execute(self):
        mode = self.mode_combo.currentText()
        find_text = self.find_edit.text()
        replace_text = self.replace_edit.text()
        
        success = 0
        failed = 0
        
        for idx, item in enumerate(self.targets):
            try:
                old_path = item['fullpath']
                old_name = item['filename']
                
                if mode == "查找替换" and find_text:
                    new_name = old_name.replace(find_text, replace_text)
                elif mode == "添加前缀":
                    new_name = replace_text + old_name
                elif mode == "添加后缀":
                    base, ext = os.path.splitext(old_name)
                    new_name = base + replace_text + ext
                elif mode == "序号命名":
                    base, ext = os.path.splitext(old_name)
                    new_name = f"{replace_text}{idx + 1:04d}{ext}"
                else:
                    continue
                
                if new_name != old_name:
                    new_path = os.path.join(os.path.dirname(old_path), new_name)
                    os.rename(old_path, new_path)
                    success += 1
            except Exception as e:
                logger.error(f"重命名失败: {e}")
                failed += 1
        
        QMessageBox.information(
            self, "完成", 
            f"重命名完成\n成功: {success}\n失败: {failed}"
        )
        self.accept()
    
    def show(self, scope_text: str = ""):
        self.setWindowTitle(f"批量重命名 - {scope_text}")
        self._update_preview()
        super().exec()


# ==================== 搜索工作线程 ====================
class SearchWorker(QThread):
    """搜索工作线程"""
    result_batch = Signal(list)
    progress = Signal(int, str)
    finished = Signal(float)
    error = Signal(str)
    
    def __init__(self, search_type: str, keywords: List[str], 
                 scope_targets: List[str], index_mgr: IndexManager,
                 config_mgr: ConfigManager, search_params: dict):
        super().__init__()
        self.search_type = search_type
        self.keywords = keywords
        self.scope_targets = scope_targets
        self.index_mgr = index_mgr
        self.config_mgr = config_mgr
        self.search_params = search_params
        self.stop_flag = False
        self.pause_flag = False
    
    def run(self):
        start_time = time.time()
        
        try:
            if self.search_type == "index":
                self._search_index()
            else:
                self._search_realtime()
            
            elapsed = time.time() - start_time
            self.finished.emit(elapsed)
        except Exception as e:
            self.error.emit(str(e))
    
    def _search_index(self):
        """索引搜索"""
        results = self.index_mgr.search(self.keywords, self.scope_targets)
        if results is None:
            self.error.emit("索引不可用")
            return
        
        batch = []
        for fn, fp, sz, mt, is_dir in results:
            if self.stop_flag:
                return
            
            if not self._match_keyword(fn):
                continue
            
            ext = os.path.splitext(fn)[1].lower()
            tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
            batch.append((fn, fp, sz, mt, tc))
            
            if len(batch) >= 100:
                self.result_batch.emit(list(batch))
                batch.clear()
        
        if batch:
            self.result_batch.emit(batch)
    
    def _search_realtime(self):
        """实时搜索"""
        batch = []
        scanned = 0
        
        for target in self.scope_targets:
            if self.stop_flag:
                break
            
            if not os.path.isdir(target):
                continue
            
            for root, dirs, files in os.walk(target):
                if self.stop_flag:
                    break
                
                while self.pause_flag and not self.stop_flag:
                    time.sleep(0.1)
                
                if should_skip_path(root.lower()):
                    continue
                
                dirs[:] = [d for d in dirs if not should_skip_dir(d.lower())]
                scanned += 1
                
                for name in dirs:
                    if self._match_keyword(name):
                        path = os.path.join(root, name)
                        batch.append((name, path, 0, 0, 0))
                
                for name in files:
                    ext = os.path.splitext(name)[1].lower()
                    if ext in SKIP_EXTS:
                        continue
                    
                    if self._match_keyword(name):
                        try:
                            path = os.path.join(root, name)
                            st = os.stat(path)
                            tc = 1 if ext in ARCHIVE_EXTS else 2
                            batch.append((name, path, st.st_size, st.st_mtime, tc))
                        except (OSError, PermissionError):
                            pass
                
                if len(batch) >= 50:
                    self.result_batch.emit(list(batch))
                    self.progress.emit(scanned, root)
                    batch.clear()
        
        if batch:
            self.result_batch.emit(batch)
    
    def _match_keyword(self, filename: str) -> bool:
        """匹配关键词"""
        if self.search_params.get("regex"):
            try:
                pattern = self.keywords[0] if self.keywords else ""
                return re.search(pattern, filename, re.IGNORECASE) is not None
            except re.error:
                return False
        elif self.search_params.get("fuzzy"):
            filename_lower = filename.lower()
            for kw in self.keywords:
                if kw in filename_lower:
                    continue
                if fuzzy_match(kw, filename) >= 50:
                    continue
                return False
            return True
        else:
            filename_lower = filename.lower()
            return all(kw in filename_lower for kw in self.keywords)
    
    def stop(self):
        self.stop_flag = True
    
    def toggle_pause(self):
        self.pause_flag = not self.pause_flag


# ==================== 索引构建线程 ====================
class IndexBuildWorker(QThread):
    """索引构建工作线程"""
    progress = Signal(int, str)
    finished = Signal()
    
    def __init__(self, index_mgr: IndexManager, drives: List[str]):
        super().__init__()
        self.index_mgr = index_mgr
        self.drives = drives
        self.stop_flag = False
    
    def run(self):
        self.index_mgr.build_index(
            self.drives,
            lambda c, p: self.progress.emit(c, p),
            lambda: self.stop_flag
        )
        self.finished.emit()
    
    def stop(self):
        self.stop_flag = True


# ==================== 主应用程序类 ====================
class SearchApp:
    """主应用程序类"""
    
    def __init__(self, db_path: str = None):
        self.config_mgr = ConfigManager()
        self.index_mgr = IndexManager(db_path=db_path, config_mgr=self.config_mgr)
        self.file_watcher = FileWatcher(self.index_mgr, config_mgr=self.config_mgr)
        
        # 状态变量
        self.is_searching = False
        self.is_paused = False
        self.total_found = 0
        self.all_results = []
        self.filtered_results = []
        self.shown_paths = set()
        self.page_size = 1000
        self.current_page = 1
        self.total_pages = 1
        self.start_time = 0.0
        self.last_search_params = None
        self.last_search_scope = None
        self.full_search_results = []
        
        # 搜索参数
        self.kw_var = ""
        self.fuzzy_var = True
        self.regex_var = False
        self.force_realtime = False
        
        # 线程
        self.search_worker = None
        self.index_worker = None
        
        # 创建主窗口
        self.main_window = None
        self._create_main_window()
        
        # 管理器
        self.tray_mgr = TrayManager(self)
        self.hotkey_mgr = HotkeyManager(self)
        self.mini_search = None
        
        # 初始化托盘和热键
        self._init_tray_and_hotkey()
        
        # 检查索引状态
        QTimer.singleShot(500, self._check_index)
    
    def _create_main_window(self):
        """创建主窗口"""
        self.main_window = QMainWindow()
        self.main_window.setWindowTitle("🚀 极速文件搜索 V42 增强版")
        self.main_window.setMinimumSize(1400, 900)
        
        # 中心部件
        central_widget = QWidget()
        self.main_window.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # 构建UI
        self._build_menubar()
        self._build_header(main_layout)
        self._build_filter_bar(main_layout)
        self._build_result_area(main_layout)
        self._build_pagination(main_layout)
        self._build_statusbar()
        self._bind_shortcuts()
        
        # 关闭事件
        self.main_window.closeEvent = self._on_close
    
    def _build_menubar(self):
        """构建菜单栏"""
        menubar = self.main_window.menuBar()
        
        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        
        export_action = QAction("📤 导出结果", self.main_window)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_results)
        file_menu.addAction(export_action)
        
        file_menu.addSeparator()
        
        open_action = QAction("📂 打开文件", self.main_window)
        open_action.setShortcut("Return")
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)
        
        locate_action = QAction("🎯 定位文件", self.main_window)
        locate_action.setShortcut("Ctrl+L")
        locate_action.triggered.connect(self.open_folder)
        file_menu.addAction(locate_action)
        
        file_menu.addSeparator()
        
        quit_action = QAction("🚪 退出", self.main_window)
        quit_action.setShortcut("Alt+F4")
        quit_action.triggered.connect(self.do_quit)
        file_menu.addAction(quit_action)
        
        # 编辑菜单
        edit_menu = menubar.addMenu("编辑(&E)")
        
        select_all_action = QAction("✅ 全选", self.main_window)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self.select_all)
        edit_menu.addAction(select_all_action)
        
        edit_menu.addSeparator()
        
        copy_path_action = QAction("📋 复制路径", self.main_window)
        copy_path_action.setShortcut("Ctrl+C")
        copy_path_action.triggered.connect(self.copy_path)
        edit_menu.addAction(copy_path_action)
        
        copy_file_action = QAction("📄 复制文件", self.main_window)
        copy_file_action.setShortcut("Ctrl+Shift+C")
        copy_file_action.triggered.connect(self.copy_file)
        edit_menu.addAction(copy_file_action)
        
        edit_menu.addSeparator()
        
        delete_action = QAction("🗑️ 删除", self.main_window)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_file)
        edit_menu.addAction(delete_action)
        
        # 搜索菜单
        search_menu = menubar.addMenu("搜索(&S)")
        
        start_search_action = QAction("🔍 开始搜索", self.main_window)
        start_search_action.triggered.connect(self.start_search)
        search_menu.addAction(start_search_action)
        
        refresh_action = QAction("🔄 刷新搜索", self.main_window)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.refresh_search)
        search_menu.addAction(refresh_action)
        
        stop_action = QAction("⏹ 停止搜索", self.main_window)
        stop_action.setShortcut("Escape")
        stop_action.triggered.connect(self.stop_search)
        search_menu.addAction(stop_action)
        
        search_menu.addSeparator()
        
        self.fuzzy_action = QAction("模糊搜索", self.main_window)
        self.fuzzy_action.setCheckable(True)
        self.fuzzy_action.setChecked(True)
        self.fuzzy_action.triggered.connect(lambda: setattr(self, 'fuzzy_var', self.fuzzy_action.isChecked()))
        search_menu.addAction(self.fuzzy_action)
        
        self.regex_action = QAction("正则表达式", self.main_window)
        self.regex_action.setCheckable(True)
        self.regex_action.triggered.connect(lambda: setattr(self, 'regex_var', self.regex_action.isChecked()))
        search_menu.addAction(self.regex_action)
        
        # 工具菜单
        tool_menu = menubar.addMenu("工具(&T)")
        
        large_file_action = QAction("📊 大文件扫描", self.main_window)
        large_file_action.setShortcut("Ctrl+G")
        large_file_action.triggered.connect(self.scan_large_files)
        tool_menu.addAction(large_file_action)
        
        rename_action = QAction("✏ 批量重命名", self.main_window)
        rename_action.triggered.connect(self._show_batch_rename)
        tool_menu.addAction(rename_action)
        
        dup_action = QAction("🔍 查找重复文件", self.main_window)
        dup_action.triggered.connect(self.find_duplicates)
        tool_menu.addAction(dup_action)
        
        empty_action = QAction("📁 查找空文件夹", self.main_window)
        empty_action.triggered.connect(self.find_empty_folders)
        tool_menu.addAction(empty_action)
        
        tool_menu.addSeparator()
        
        index_mgr_action = QAction("🔧 索引管理", self.main_window)
        index_mgr_action.triggered.connect(self._show_index_mgr)
        tool_menu.addAction(index_mgr_action)
        
        rebuild_action = QAction("🔄 重建索引", self.main_window)
        rebuild_action.triggered.connect(self._build_index)
        tool_menu.addAction(rebuild_action)
        
        tool_menu.addSeparator()
        
        settings_action = QAction("⚙️ 设置", self.main_window)
        settings_action.triggered.connect(self._show_settings)
        tool_menu.addAction(settings_action)
        
        # 收藏菜单
        self.fav_menu = menubar.addMenu("收藏(&B)")
        self._update_favorites_menu()
        
        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        
        shortcut_action = QAction("⌨️ 快捷键列表", self.main_window)
        shortcut_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcut_action)
        
        help_menu.addSeparator()
        
        about_action = QAction("ℹ️ 关于", self.main_window)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
    
    def _update_favorites_menu(self):
        """更新收藏夹菜单"""
        self.fav_menu.clear()
        
        add_action = QAction("⭐ 收藏当前目录", self.main_window)
        add_action.triggered.connect(self._add_current_to_favorites)
        self.fav_menu.addAction(add_action)
        
        manage_action = QAction("📂 管理收藏夹", self.main_window)
        manage_action.triggered.connect(self._manage_favorites)
        self.fav_menu.addAction(manage_action)
        
        self.fav_menu.addSeparator()
        
        favorites = self.config_mgr.get_favorites()
        if favorites:
            for fav in favorites:
                action = QAction(f"📁 {fav['name']}", self.main_window)
                action.triggered.connect(lambda checked, p=fav['path']: self._goto_favorite(p))
                self.fav_menu.addAction(action)
        else:
            no_fav = QAction("(无收藏)", self.main_window)
            no_fav.setEnabled(False)
            self.fav_menu.addAction(no_fav)
    
    def _build_header(self, parent_layout: QVBoxLayout):
        """构建头部区域"""
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(5, 5, 5, 5)
        
        # Row0: 标题和工具栏
        row0 = QHBoxLayout()
        
        title_label = QLabel("⚡ 极速搜 V42")
        title_label.setFont(QFont("微软雅黑", 18, QFont.Bold))
        title_label.setStyleSheet("color: #4CAF50;")
        row0.addWidget(title_label)
        
        enhance_label = QLabel("🎯 增强版")
        enhance_label.setFont(QFont("微软雅黑", 10))
        enhance_label.setStyleSheet("color: #FF9800;")
        row0.addWidget(enhance_label)
        
        self.idx_label = QLabel("检查中...")
        self.idx_label.setFont(QFont("微软雅黑", 9))
        row0.addWidget(self.idx_label)
        
        row0.addStretch()
        
        # 主题选择
        theme_label = QLabel("主题:")
        theme_label.setFont(QFont("微软雅黑", 9))
        row0.addWidget(theme_label)
        
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["fusion", "windows", "windowsvista"])
        self.theme_combo.setCurrentText(self.config_mgr.get_theme())
        self.theme_combo.currentTextChanged.connect(self._on_theme_change)
        self.theme_combo.setFixedWidth(100)
        row0.addWidget(self.theme_combo)
        
        # 工具按钮
        btn_refresh_status = QPushButton("🔄 刷新状态")
        btn_refresh_status.setFixedWidth(100)
        btn_refresh_status.clicked.connect(self.refresh_index_status)
        row0.addWidget(btn_refresh_status)
        
        btn_c_drive = QPushButton("📂 C盘目录")
        btn_c_drive.setFixedWidth(100)
        btn_c_drive.clicked.connect(self._show_c_drive_settings)
        row0.addWidget(btn_c_drive)
        
        btn_rename = QPushButton("✏ 批量重命名")
        btn_rename.setFixedWidth(110)
        btn_rename.clicked.connect(self._show_batch_rename)
        row0.addWidget(btn_rename)
        
        btn_large = QPushButton("📊 大文件")
        btn_large.setFixedWidth(90)
        btn_large.clicked.connect(self.scan_large_files)
        row0.addWidget(btn_large)
        
        btn_export = QPushButton("📤 导出")
        btn_export.setFixedWidth(70)
        btn_export.clicked.connect(self.export_results)
        row0.addWidget(btn_export)
        
        btn_index = QPushButton("🔧 索引管理")
        btn_index.setFixedWidth(100)
        btn_index.clicked.connect(self._show_index_mgr)
        row0.addWidget(btn_index)
        
        header_layout.addLayout(row0)
        
        # Row1: 搜索栏
        row1 = QHBoxLayout()
        
        # 收藏夹下拉
        self.fav_combo = QComboBox()
        self.fav_combo.setFixedWidth(100)
        self._update_fav_combo()
        self.fav_combo.currentIndexChanged.connect(self._on_fav_combo_select)
        row1.addWidget(self.fav_combo)
        
        # 范围选择
        self.scope_combo = QComboBox()
        self.scope_combo.setFixedWidth(150)
        self._update_drives()
        self.scope_combo.currentTextChanged.connect(self._on_scope_change)
        row1.addWidget(self.scope_combo)
        
        btn_browse = QPushButton("📂 选择目录")
        btn_browse.setFixedWidth(100)
        btn_browse.clicked.connect(self._browse)
        row1.addWidget(btn_browse)
        
        # 搜索框
        self.entry_kw = QLineEdit()
        self.entry_kw.setFont(QFont("微软雅黑", 12))
        self.entry_kw.setPlaceholderText("输入关键词搜索...")
        self.entry_kw.returnPressed.connect(self.start_search)
        self.entry_kw.setContextMenuPolicy(Qt.CustomContextMenu)
        self.entry_kw.customContextMenuRequested.connect(self._show_entry_menu)
        row1.addWidget(self.entry_kw, 1)
        
        # 搜索选项
        self.fuzzy_check = QCheckBox("模糊")
        self.fuzzy_check.setChecked(True)
        self.fuzzy_check.stateChanged.connect(lambda s: setattr(self, 'fuzzy_var', s == Qt.Checked))
        row1.addWidget(self.fuzzy_check)
        
        self.regex_check = QCheckBox("正则")
        self.regex_check.stateChanged.connect(lambda s: setattr(self, 'regex_var', s == Qt.Checked))
        row1.addWidget(self.regex_check)
        
        self.realtime_check = QCheckBox("实时")
        self.realtime_check.stateChanged.connect(lambda s: setattr(self, 'force_realtime', s == Qt.Checked))
        row1.addWidget(self.realtime_check)
        
        # 搜索按钮
        self.btn_search = QPushButton("🚀 搜索")
        self.btn_search.setFixedWidth(80)
        self.btn_search.clicked.connect(self.start_search)
        row1.addWidget(self.btn_search)
        
        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.setFixedWidth(70)
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.clicked.connect(self.refresh_search)
        row1.addWidget(self.btn_refresh)
        
        self.btn_pause = QPushButton("⏸ 暂停")
        self.btn_pause.setFixedWidth(70)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        row1.addWidget(self.btn_pause)
        
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setFixedWidth(70)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_search)
        row1.addWidget(self.btn_stop)
        
        header_layout.addLayout(row1)
        parent_layout.addWidget(header)
    
    def _build_filter_bar(self, parent_layout: QVBoxLayout):
        """构建筛选栏"""
        filter_widget = QWidget()
        filter_layout = QHBoxLayout(filter_widget)
        filter_layout.setContentsMargins(5, 0, 5, 0)
        
        filter_layout.addWidget(QLabel("筛选:"))
        
        filter_layout.addWidget(QLabel("格式"))
        self.ext_combo = QComboBox()
        self.ext_combo.addItem("全部")
        self.ext_combo.setFixedWidth(120)
        self.ext_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.ext_combo)
        
        filter_layout.addWidget(QLabel("大小"))
        self.size_combo = QComboBox()
        self.size_combo.addItems(["不限", ">1MB", ">10MB", ">100MB", ">500MB", ">1GB"])
        self.size_combo.setFixedWidth(80)
        self.size_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.size_combo)
        
        filter_layout.addWidget(QLabel("时间"))
        self.date_combo = QComboBox()
        self.date_combo.addItems(["不限", "今天", "3天内", "7天内", "30天内", "今年"])
        self.date_combo.setFixedWidth(80)
        self.date_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.date_combo)
        
        btn_clear = QPushButton("清除")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self._clear_filter)
        filter_layout.addWidget(btn_clear)
        
        filter_layout.addStretch()
        
        self.filter_label = QLabel("")
        self.filter_label.setStyleSheet("color: #666;")
        filter_layout.addWidget(self.filter_label)
        
        parent_layout.addWidget(filter_widget)
    
    def _build_result_area(self, parent_layout: QVBoxLayout):
        """构建结果区域"""
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["📄 文件名", "📂 所在目录", "📊 类型/大小", "🕒 修改时间"])
        self.tree.setColumnWidth(0, 400)
        self.tree.setColumnWidth(1, 400)
        self.tree.setColumnWidth(2, 130)
        self.tree.setColumnWidth(3, 150)
        
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        
        self.tree.itemDoubleClicked.connect(self.on_dblclick)
        self.tree.customContextMenuRequested.connect(self.show_menu)
        
        header = self.tree.header()
        header.setStretchLastSection(True)
        header.setSectionsClickable(True)
        
        parent_layout.addWidget(self.tree, 1)
        
        # 创建右键菜单
        self.ctx_menu = QMenu(self.main_window)
        
        open_action = QAction("📂 打开文件", self.ctx_menu)
        open_action.triggered.connect(self.open_file)
        self.ctx_menu.addAction(open_action)
        
        locate_action = QAction("🎯 定位文件", self.ctx_menu)
        locate_action.triggered.connect(self.open_folder)
        self.ctx_menu.addAction(locate_action)
        
        preview_action = QAction("👁️ 预览文件", self.ctx_menu)
        preview_action.triggered.connect(self.preview_file)
        self.ctx_menu.addAction(preview_action)
        
        self.ctx_menu.addSeparator()
        
        copy_file_action = QAction("📄 复制文件", self.ctx_menu)
        copy_file_action.triggered.connect(self.copy_file)
        self.ctx_menu.addAction(copy_file_action)
        
        copy_path_action = QAction("📝 复制路径", self.ctx_menu)
        copy_path_action.triggered.connect(self.copy_path)
        self.ctx_menu.addAction(copy_path_action)
        
        self.ctx_menu.addSeparator()
        
        delete_action = QAction("🗑️ 删除", self.ctx_menu)
        delete_action.triggered.connect(self.delete_file)
        self.ctx_menu.addAction(delete_action)
    
    def _build_pagination(self, parent_layout: QVBoxLayout):
        """构建分页栏"""
        page_widget = QWidget()
        page_layout = QHBoxLayout(page_widget)
        page_layout.setContentsMargins(0, 5, 0, 5)
        
        page_layout.addStretch()
        
        self.btn_first = QPushButton("⏮")
        self.btn_first.setFixedWidth(40)
        self.btn_first.setEnabled(False)
        self.btn_first.clicked.connect(lambda: self.go_page("first"))
        page_layout.addWidget(self.btn_first)
        
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedWidth(40)
        self.btn_prev.setEnabled(False)
        self.btn_prev.clicked.connect(lambda: self.go_page("prev"))
        page_layout.addWidget(self.btn_prev)
        
        self.page_label = QLabel("第 1/1 页 (0项)")
        self.page_label.setFont(QFont("微软雅黑", 9))
        page_layout.addWidget(self.page_label)
        
        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedWidth(40)
        self.btn_next.setEnabled(False)
        self.btn_next.clicked.connect(lambda: self.go_page("next"))
        page_layout.addWidget(self.btn_next)
        
        self.btn_last = QPushButton("⏭")
        self.btn_last.setFixedWidth(40)
        self.btn_last.setEnabled(False)
        self.btn_last.clicked.connect(lambda: self.go_page("last"))
        page_layout.addWidget(self.btn_last)
        
        page_layout.addStretch()
        
        parent_layout.addWidget(page_widget)
    
    def _build_statusbar(self):
        """构建状态栏"""
        self.statusbar = QStatusBar()
        self.main_window.setStatusBar(self.statusbar)
        
        self.status_label = QLabel("就绪")
        self.statusbar.addWidget(self.status_label)
        
        self.status_path_label = QLabel("")
        self.status_path_label.setStyleSheet("color: #718096;")
        self.statusbar.addWidget(self.status_path_label, 1)
        
        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.setMaximum(0)
        self.progress.setVisible(False)
        self.statusbar.addPermanentWidget(self.progress)
    
    def _bind_shortcuts(self):
        """绑定快捷键"""
        QShortcut(QKeySequence("Ctrl+F"), self.main_window, self.entry_kw.setFocus)
        QShortcut(QKeySequence("Escape"), self.main_window, self._on_escape)
        QShortcut(QKeySequence("Delete"), self.main_window, self.delete_file)
        QShortcut(QKeySequence("F5"), self.main_window, self.refresh_search)
        QShortcut(QKeySequence("Ctrl+A"), self.main_window, self.select_all)
        QShortcut(QKeySequence("Ctrl+C"), self.main_window, self.copy_path)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self.main_window, self.copy_file)
        QShortcut(QKeySequence("Ctrl+E"), self.main_window, self.export_results)
        QShortcut(QKeySequence("Ctrl+G"), self.main_window, self.scan_large_files)
        QShortcut(QKeySequence("Ctrl+L"), self.main_window, self.open_folder)
        QShortcut(QKeySequence("Return"), self.main_window, self.open_file)
        QShortcut(QKeySequence("Space"), self.main_window, self.preview_file)
    
    def _init_tray_and_hotkey(self):
        """初始化托盘和热键"""
        if self.config_mgr.get_tray_enabled():
            self.tray_mgr.start()
        
        if self.config_mgr.get_hotkey_enabled() and HAS_WIN32:
            self.hotkey_mgr.start()
    
    # ==================== 事件处理 ====================
    def _on_escape(self):
        if self.is_searching:
            self.stop_search()
        else:
            self.entry_kw.clear()
    
    def _on_theme_change(self, theme: str):
        self.config_mgr.set_theme(theme)
        QApplication.setStyle(theme)
        self.status_label.setText(f"主题已切换: {theme}")
    
    def _on_scope_change(self, scope: str):
        if not self.kw_var:
            return
        if self.is_searching:
            return
        
        if self.last_search_scope == "所有磁盘 (全盘)" and self.full_search_results:
            if "所有磁盘" in scope:
                self.all_results = list(self.full_search_results)
                self.filtered_results = list(self.all_results)
                self._apply_filter()
                self.status_label.setText(f"✅ 显示全部结果: {len(self.filtered_results)}项")
            else:
                self._filter_by_drive(scope)
        else:
            self.start_search()
    
    def _filter_by_drive(self, drive_path: str):
        if not self.full_search_results:
            return
        
        drive_letter = drive_path.rstrip("\\").upper()
        
        self.all_results = []
        for item in self.full_search_results:
            item_drive = item["fullpath"][:2].upper()
            if item_drive == drive_letter[:2]:
                self.all_results.append(item)
        
        self.filtered_results = list(self.all_results)
        self._apply_filter()
        self.status_label.setText(f"✅ 筛选 {drive_letter}: {len(self.filtered_results)}项")
    
    def _on_close(self, event):
        """关闭事件处理"""
        if self.config_mgr.get_tray_enabled() and self.tray_mgr.running:
            self.main_window.hide()
            self.tray_mgr.show_notification("极速文件搜索", "程序已最小化到托盘")
            event.ignore()
        else:
            self.do_quit()
            event.accept()
    
    # ==================== 辅助方法 ====================
    def _update_drives(self):
        """更新驱动器列表"""
        self.scope_combo.clear()
        self.scope_combo.addItem("所有磁盘 (全盘)")
        
        if platform.system() == "Windows":
            for d in string.ascii_uppercase:
                if os.path.exists(f"{d}:\\"):
                    self.scope_combo.addItem(f"{d}:\\")
        else:
            self.scope_combo.addItem("/")
    
    def _get_drives(self) -> List[str]:
        if platform.system() == "Windows":
            return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        return ["/"]
    
    def _get_search_scope_targets(self) -> List[str]:
        scope = self.scope_combo.currentText()
        return parse_search_scope(scope, self._get_drives, self.config_mgr)
    
    def _browse(self):
        """选择目录"""
        path = QFileDialog.getExistingDirectory(self.main_window, "选择目录")
        if path:
            self.scope_combo.setCurrentText(path)
    
    def _update_fav_combo(self):
        """更新收藏夹下拉"""
        self.fav_combo.clear()
        self.fav_combo.addItem("⭐ 收藏夹")
        
        favorites = self.config_mgr.get_favorites()
        for fav in favorites:
            self.fav_combo.addItem(f"📁 {fav['name']}")
    
    def _on_fav_combo_select(self, index: int):
        if index <= 0:
            return
        
        favorites = self.config_mgr.get_favorites()
        if index - 1 < len(favorites):
            fav = favorites[index - 1]
            if os.path.exists(fav["path"]):
                self.scope_combo.setCurrentText(fav["path"])
            else:
                QMessageBox.warning(self.main_window, "警告", f"目录不存在: {fav['path']}")
        
        QTimer.singleShot(100, lambda: self.fav_combo.setCurrentIndex(0))
    
    def _add_current_to_favorites(self):
        scope = self.scope_combo.currentText()
        if "所有磁盘" in scope:
            QMessageBox.information(self.main_window, "提示", "请先选择一个具体目录")
            return
        
        self.config_mgr.add_favorite(scope)
        self._update_favorites_menu()
        self._update_fav_combo()
        QMessageBox.information(self.main_window, "成功", f"已收藏: {scope}")
    
    def _goto_favorite(self, path: str):
        if os.path.exists(path):
            self.scope_combo.setCurrentText(path)
        else:
            QMessageBox.warning(self.main_window, "警告", f"目录不存在: {path}")
    
    def _manage_favorites(self):
        """管理收藏夹对话框"""
        dlg = QDialog(self.main_window)
        dlg.setWindowTitle("📂 管理收藏夹")
        dlg.setFixedSize(500, 400)
        
        layout = QVBoxLayout(dlg)
        
        label = QLabel("收藏夹列表")
        label.setFont(QFont("微软雅黑", 11, QFont.Bold))
        layout.addWidget(label)
        
        list_widget = QListWidget()
        for fav in self.config_mgr.get_favorites():
            list_widget.addItem(f"{fav['name']} - {fav['path']}")
        layout.addWidget(list_widget)
        
        btn_layout = QHBoxLayout()
        
        def remove_selected():
            row = list_widget.currentRow()
            if row >= 0:
                favs = self.config_mgr.get_favorites()
                if row < len(favs):
                    self.config_mgr.remove_favorite(favs[row]["path"])
                    list_widget.takeItem(row)
                    self._update_favorites_menu()
                    self._update_fav_combo()
        
        btn_remove = QPushButton("删除选中")
        btn_remove.clicked.connect(remove_selected)
        btn_layout.addWidget(btn_remove)
        
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        btn_layout.addWidget(btn_close)
        
        layout.addLayout(btn_layout)
        dlg.exec()
    
    def _show_entry_menu(self, pos):
        """显示搜索框右键菜单"""
        menu = QMenu(self.main_window)
        
        menu.addAction("剪切", self.entry_kw.cut)
        menu.addAction("复制", self.entry_kw.copy)
        menu.addAction("粘贴", self.entry_kw.paste)
        menu.addAction("全选", self.entry_kw.selectAll)
        menu.addSeparator()
        menu.addAction("清空", self.entry_kw.clear)
        menu.addSeparator()
        
        history_menu = menu.addMenu("📜 搜索历史")
        history = self.config_mgr.get_history()
        if history:
            for kw in history[:15]:
                action = history_menu.addAction(kw)
                action.triggered.connect(lambda checked, k=kw: self._use_history(k))
            history_menu.addSeparator()
            history_menu.addAction("清除历史", self._clear_history)
        else:
            no_history = history_menu.addAction("(无历史记录)")
            no_history.setEnabled(False)
        
        menu.exec(self.entry_kw.mapToGlobal(pos))
    
    def _use_history(self, keyword: str):
        self.entry_kw.setText(keyword)
        self.start_search()
    
    def _clear_history(self):
        self.config_mgr.config["search_history"] = []
        self.config_mgr.save()
    
    # ==================== 筛选功能 ====================
    def _update_ext_combo(self):
        """更新格式下拉"""
        counts = {}
        for item in self.all_results:
            if item["type_code"] == 0:
                ext = "📂文件夹"
            elif item["type_code"] == 1:
                ext = "📦压缩包"
            else:
                ext = os.path.splitext(item["filename"])[1].lower() or "(无)"
            counts[ext] = counts.get(ext, 0) + 1
        
        self.ext_combo.clear()
        self.ext_combo.addItem("全部")
        for ext, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]:
            self.ext_combo.addItem(f"{ext} ({cnt})")
    
    def _get_size_min(self) -> int:
        size_map = {
            "不限": 0,
            ">1MB": 1 << 20,
                        ">10MB": 10 << 20,
            ">100MB": 100 << 20,
            ">500MB": 500 << 20,
            ">1GB": 1 << 30,
        }
        return size_map.get(self.size_combo.currentText(), 0)
    
    def _get_date_min(self) -> float:
        """获取日期筛选最小值"""
        now = time.time()
        day = 86400
        date_map = {
            "不限": 0,
            "今天": now - day,
            "3天内": now - 3 * day,
            "7天内": now - 7 * day,
            "30天内": now - 30 * day,
            "今年": time.mktime(
                datetime.datetime(datetime.datetime.now().year, 1, 1).timetuple()
            ),
        }
        return date_map.get(self.date_combo.currentText(), 0)
    
    def _apply_filter(self):
        """应用筛选"""
        ext_sel = self.ext_combo.currentText()
        size_min = self._get_size_min()
        date_min = self._get_date_min()
        target_ext = ext_sel.split(" (")[0] if ext_sel != "全部" else None
        
        self.filtered_results = []
        for item in self.all_results:
            if size_min > 0 and item["type_code"] == 2 and item["size"] < size_min:
                continue
            if date_min > 0 and item["mtime"] < date_min:
                continue
            if target_ext:
                if item["type_code"] == 0:
                    item_ext = "📂文件夹"
                elif item["type_code"] == 1:
                    item_ext = "📦压缩包"
                else:
                    item_ext = os.path.splitext(item["filename"])[1].lower() or "(无)"
                if item_ext != target_ext:
                    continue
            self.filtered_results.append(item)
        
        self.current_page = 1
        self._render_page()
        
        all_count = len(self.all_results)
        filtered_count = len(self.filtered_results)
        
        if ext_sel != "全部" or size_min > 0 or date_min > 0:
            self.filter_label.setText(f"筛选: {filtered_count}/{all_count}")
        else:
            self.filter_label.setText("")
    
    def _clear_filter(self):
        """清除筛选"""
        self.ext_combo.setCurrentIndex(0)
        self.size_combo.setCurrentIndex(0)
        self.date_combo.setCurrentIndex(0)
        self.filtered_results = list(self.all_results)
        self.current_page = 1
        self._render_page()
        self.filter_label.setText("")
    
    # ==================== 分页功能 ====================
    def _update_page_info(self):
        """更新分页信息"""
        total = len(self.filtered_results)
        self.total_pages = max(1, math.ceil(total / self.page_size))
        self.page_label.setText(f"第 {self.current_page}/{self.total_pages} 页 ({total}项)")
        
        self.btn_first.setEnabled(self.current_page > 1)
        self.btn_prev.setEnabled(self.current_page > 1)
        self.btn_next.setEnabled(self.current_page < self.total_pages)
        self.btn_last.setEnabled(self.current_page < self.total_pages)
    
    def go_page(self, action: str):
        """翻页"""
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
        """渲染当前页"""
        self.tree.clear()
        self._update_page_info()
        
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        
        for i, item in enumerate(self.filtered_results[start:end]):
            tree_item = QTreeWidgetItem([
                item["filename"],
                item["dir_path"],
                item["size_str"],
                item["mtime_str"]
            ])
            # 存储索引用于后续操作
            tree_item.setData(0, Qt.UserRole, start + i)
            self.tree.addTopLevelItem(tree_item)
    
    def select_all(self):
        """全选"""
        self.tree.selectAll()
    
    # ==================== 文件操作 ====================
    def _get_sel(self) -> Optional[dict]:
        """获取当前选中项"""
        items = self.tree.selectedItems()
        if not items:
            return None
        
        idx = items[0].data(0, Qt.UserRole)
        if idx is None or idx < 0 or idx >= len(self.filtered_results):
            return None
        
        return self.filtered_results[idx]
    
    def _get_selected_items(self) -> List[dict]:
        """获取所有选中项"""
        result = []
        for item in self.tree.selectedItems():
            idx = item.data(0, Qt.UserRole)
            if idx is not None and 0 <= idx < len(self.filtered_results):
                result.append(self.filtered_results[idx])
        return result
    
    def on_dblclick(self, item: QTreeWidgetItem, column: int):
        """双击事件"""
        idx = item.data(0, Qt.UserRole)
        if idx is None or idx >= len(self.filtered_results):
            return
        
        file_item = self.filtered_results[idx]
        if file_item["type_code"] == 0:
            # 文件夹 - 打开目录
            try:
                if platform.system() == "Windows":
                    subprocess.Popen(f'explorer "{file_item["fullpath"]}"')
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(file_item["fullpath"]))
            except Exception as e:
                logger.error(f"打开文件夹失败: {e}")
                QMessageBox.critical(self.main_window, "错误", f"无法打开文件夹: {e}")
        else:
            # 文件 - 用默认程序打开
            try:
                if platform.system() == "Windows":
                    os.startfile(file_item["fullpath"])
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(file_item["fullpath"]))
            except Exception as e:
                logger.error(f"打开文件失败: {e}")
                QMessageBox.critical(self.main_window, "错误", f"无法打开文件: {e}")
    
    def show_menu(self, pos):
        """显示右键菜单"""
        item = self.tree.itemAt(pos)
        if item:
            self.tree.setCurrentItem(item)
            self.ctx_menu.exec(self.tree.mapToGlobal(pos))
    
    def open_file(self):
        """打开文件"""
        item = self._get_sel()
        if item:
            try:
                if platform.system() == "Windows":
                    os.startfile(item["fullpath"])
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(item["fullpath"]))
            except Exception as e:
                logger.error(f"打开文件失败: {e}")
                QMessageBox.critical(self.main_window, "错误", f"无法打开文件: {e}")
    
    def open_folder(self):
        """定位文件"""
        item = self._get_sel()
        if item:
            try:
                if platform.system() == "Windows":
                    subprocess.Popen(f'explorer /select,"{item["fullpath"]}"')
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(item["fullpath"])))
            except Exception as e:
                logger.error(f"定位文件失败: {e}")
                QMessageBox.critical(self.main_window, "错误", f"无法定位文件: {e}")
    
    def copy_path(self):
        """复制路径"""
        items = self._get_selected_items()
        if items:
            paths = "\n".join(item["fullpath"] for item in items)
            QApplication.clipboard().setText(paths)
            self.status_label.setText(f"已复制 {len(items)} 个路径")
    
    def copy_file(self):
        """复制文件"""
        if not HAS_WIN32:
            QMessageBox.warning(self.main_window, "提示", "需要安装 pywin32: pip install pywin32")
            return
        
        items = self._get_selected_items()
        if not items:
            return
        
        try:
            files = [
                os.path.abspath(item["fullpath"])
                for item in items
                if os.path.exists(item["fullpath"])
            ]
            if not files:
                return
            
            file_str = "\0".join(files) + "\0\0"
            data = struct.pack("IIIII", 20, 0, 0, 0, 1) + file_str.encode("utf-16le")
            
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
            win32clipboard.CloseClipboard()
            self.status_label.setText(f"已复制 {len(files)} 个文件")
        except Exception as e:
            logger.error(f"复制文件失败: {e}")
            QMessageBox.critical(self.main_window, "错误", f"复制文件失败: {e}")
    
    def delete_file(self):
        """删除文件"""
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
        
        reply = QMessageBox.question(
            self.main_window, "确认", msg,
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        deleted = 0
        failed = []
        
        for item in items:
            try:
                if HAS_SEND2TRASH:
                    send2trash.send2trash(item["fullpath"])
                else:
                    if item["type_code"] == 0:
                        shutil.rmtree(item["fullpath"])
                    else:
                        os.remove(item["fullpath"])
                
                self.shown_paths.discard(item["fullpath"])
                deleted += 1
            except Exception as e:
                logger.error(f"删除失败: {item['fullpath']} - {e}")
                failed.append(item["filename"])
        
        # 从树中移除
        for tree_item in self.tree.selectedItems():
            idx = self.tree.indexOfTopLevelItem(tree_item)
            if idx >= 0:
                self.tree.takeTopLevelItem(idx)
        
        if failed:
            self.status_label.setText(f"✅ 已删除 {deleted} 个，失败 {len(failed)} 个")
            QMessageBox.warning(
                self.main_window, "部分失败",
                f"以下文件删除失败:\n" + "\n".join(failed[:5])
            )
        else:
            self.status_label.setText(f"✅ 已删除 {deleted} 个文件")
    
    def preview_file(self):
        """预览文件"""
        item = self._get_sel()
        if not item:
            return
        
        ext = os.path.splitext(item["filename"])[1].lower()
        text_exts = {
            ".txt", ".log", ".py", ".json", ".xml", ".md", ".csv",
            ".ini", ".cfg", ".yaml", ".yml", ".js", ".css", ".sql",
            ".sh", ".bat", ".cmd", ".html", ".htm", ".c", ".cpp",
            ".h", ".java", ".go", ".rs", ".ts", ".vue"
        }
        
        if ext in text_exts:
            self._preview_text(item["fullpath"])
        elif item["type_code"] == 0:
            # 打开文件夹
            try:
                if platform.system() == "Windows":
                    subprocess.Popen(f'explorer "{item["fullpath"]}"')
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(item["fullpath"]))
            except Exception as e:
                logger.error(f"打开文件夹失败: {e}")
        else:
            # 用默认程序打开
            try:
                if platform.system() == "Windows":
                    os.startfile(item["fullpath"])
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(item["fullpath"]))
            except Exception as e:
                logger.error(f"打开文件失败: {e}")
    
    def _preview_text(self, path: str):
        """文本预览窗口"""
        dlg = QDialog(self.main_window)
        dlg.setWindowTitle(f"预览: {os.path.basename(path)}")
        dlg.resize(800, 600)
        
        layout = QVBoxLayout(dlg)
        
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Consolas", 10))
        
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(200000)
            if len(content) >= 200000:
                content += "\n\n... [文件过大，仅显示前200KB] ..."
            text_edit.setPlainText(content)
        except Exception as e:
            logger.error(f"读取文件失败 {path}: {e}")
            text_edit.setPlainText(f"无法读取文件: {e}")
        
        layout.addWidget(text_edit)
        
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        layout.addWidget(btn_close)
        
        dlg.exec()
    
    # ==================== 搜索功能 ====================
    def start_search(self):
        """开始搜索"""
        if self.is_searching:
            return
        
        kw = self.entry_kw.text().strip()
        if not kw:
            QMessageBox.warning(self.main_window, "提示", "请输入关键词")
            return
        
        self.kw_var = kw
        self.config_mgr.add_history(kw)
        
        # 清空结果
        self.tree.clear()
        self.all_results.clear()
        self.filtered_results.clear()
        self.shown_paths.clear()
        self.total_found = 0
        self.current_page = 1
        self.start_time = time.time()
        
        # 重置筛选
        self.ext_combo.clear()
        self.ext_combo.addItem("全部")
        self.size_combo.setCurrentIndex(0)
        self.date_combo.setCurrentIndex(0)
        self.filter_label.setText("")
        
        current_scope = self.scope_combo.currentText()
        self.last_search_scope = current_scope
        self.full_search_results = []
        
        # 解析关键词
        if self.regex_var:
            try:
                re.compile(kw)
                keywords = [kw]
            except re.error as e:
                QMessageBox.critical(self.main_window, "正则错误", f"正则表达式无效: {e}")
                return
        else:
            keywords = kw.lower().split()
        
        scope_targets = self._get_search_scope_targets()
        
        self.last_search_params = {
            "keywords": keywords,
            "scope_targets": scope_targets,
            "kw": kw,
            "regex": self.regex_var,
            "fuzzy": self.fuzzy_var,
        }
        
        # 决定使用索引搜索还是实时搜索
        use_idx = (
            not self.force_realtime
            and self.index_mgr.is_ready
            and not self.index_mgr.is_building
        )
        
        if use_idx:
            self.status_label.setText("⚡ 索引搜索...")
            search_type = "index"
        else:
            self.status_label.setText("🔍 实时扫描...")
            search_type = "realtime"
        
        # 更新UI状态
        self.is_searching = True
        self.btn_search.setEnabled(False)
        self.btn_refresh.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.progress.setVisible(True)
        
        # 创建搜索线程
        self.search_worker = SearchWorker(
            search_type, keywords, scope_targets,
            self.index_mgr, self.config_mgr, self.last_search_params
        )
        self.search_worker.result_batch.connect(self._on_result_batch)
        self.search_worker.progress.connect(self._on_search_progress)
        self.search_worker.finished.connect(self._on_search_finished)
        self.search_worker.error.connect(self._on_search_error)
        self.search_worker.start()
    
    def refresh_search(self):
        """刷新搜索"""
        if self.last_search_params and not self.is_searching:
            self.entry_kw.setText(self.last_search_params["kw"])
            self.start_search()
    
    def toggle_pause(self):
        """暂停/继续"""
        if not self.is_searching or not self.search_worker:
            return
        
        self.is_paused = not self.is_paused
        self.search_worker.toggle_pause()
        
        if self.is_paused:
            self.btn_pause.setText("▶ 继续")
            self.progress.setVisible(False)
        else:
            self.btn_pause.setText("⏸ 暂停")
            self.progress.setVisible(True)
    
    def stop_search(self):
        """停止搜索"""
        if not self.is_searching:
            return
        
        if self.search_worker:
            self.search_worker.stop()
        
        self._reset_search_ui()
        self._finalize_search()
        self.status_label.setText(f"🛑 已停止 ({len(self.all_results)}项)")
    
    def _reset_search_ui(self):
        """重置搜索UI"""
        self.is_searching = False
        self.is_paused = False
        self.btn_search.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸ 暂停")
        self.btn_stop.setEnabled(False)
        self.progress.setVisible(False)
    
    def _finalize_search(self):
        """完成搜索"""
        self._update_ext_combo()
        self.filtered_results = list(self.all_results)
        
        if self.last_search_scope == "所有磁盘 (全盘)":
            self.full_search_results = list(self.all_results)
        
        self._render_page()
    
    @Slot(list)
    def _on_result_batch(self, batch: list):
        """处理搜索结果批次"""
        for name, path, size, mtime, type_code in batch:
            if path in self.shown_paths:
                continue
            
            self.shown_paths.add(path)
            
            size_str = (
                "📂 文件夹" if type_code == 0
                else ("📦 压缩包" if type_code == 1 else format_size(size))
            )
            mtime_str = "-" if type_code == 0 else format_time(mtime)
            
            self.all_results.append({
                "filename": name,
                "fullpath": path,
                "dir_path": os.path.dirname(path),
                "size": size,
                "mtime": mtime,
                "type_code": type_code,
                "size_str": size_str,
                "mtime_str": mtime_str,
            })
        
        self.total_found = len(self.all_results)
        self.status_label.setText(f"已找到: {self.total_found}")
        
        # 实时更新显示
        if self.total_found <= 200 or self.total_found % 100 == 0:
            self.filtered_results = list(self.all_results)
            self._render_page()
    
    @Slot(int, str)
    def _on_search_progress(self, scanned: int, current_path: str):
        """搜索进度更新"""
        elapsed = time.time() - self.start_time
        speed = scanned / elapsed if elapsed > 0 else 0
        self.status_label.setText(
            f"🔍 实时扫描中... (已扫描 {scanned:,} 个目录，{speed:.0f}/s)"
        )
        self.status_path_label.setText(current_path[-60:] if len(current_path) > 60 else current_path)
    
    @Slot(float)
    def _on_search_finished(self, elapsed: float):
        """搜索完成"""
        self._reset_search_ui()
        self._finalize_search()
        self.status_label.setText(f"✅ 完成: {self.total_found}项 ({elapsed:.2f}s)")
        self.status_path_label.setText("")
    
    @Slot(str)
    def _on_search_error(self, error: str):
        """搜索错误"""
        self._reset_search_ui()
        QMessageBox.critical(self.main_window, "错误", error)
    
    # ==================== 索引管理 ====================
    def _check_index(self):
        """检查索引状态"""
        s = self.index_mgr.get_stats()
        fts = "FTS5✅" if s.get("has_fts") else "FTS5❌"
        mft = "MFT✅" if s.get("used_mft") else "MFT❌"
        
        time_info = ""
        if s["time"]:
            last_update = datetime.datetime.fromtimestamp(s["time"])
            time_diff = datetime.datetime.now() - last_update
            if time_diff.days > 0:
                time_info = f" (更新于{time_diff.days}天前)"
            elif time_diff.seconds > 3600:
                time_info = f" (更新于{time_diff.seconds//3600}小时前)"
            else:
                time_info = f" (更新于{time_diff.seconds//60}分钟前)"
        
        if s["building"]:
            txt = f"🔄 构建中({s['count']:,}) [{fts}][{mft}]"
        elif s["ready"]:
            txt = f"✅ 就绪({s['count']:,}){time_info} [{fts}][{mft}]"
        else:
            txt = f"❌ 未构建 [{fts}][{mft}]"
        
        self.idx_label.setText(txt)
    
    def refresh_index_status(self):
        """刷新索引状态"""
        self.index_mgr.reload_stats()
        self._check_index()
    
    def _show_index_mgr(self):
        """索引管理对话框"""
        dlg = QDialog(self.main_window)
        dlg.setWindowTitle("🔧 索引管理")
        dlg.setFixedSize(500, 400)
        
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        
        # 标题
        title = QLabel("📊 索引状态")
        title.setFont(QFont("微软雅黑", 12, QFont.Bold))
        layout.addWidget(title)
        
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)
        
        # 信息表格
        s = self.index_mgr.get_stats()
        c_dirs = get_c_scan_dirs(self.config_mgr)
        c_dirs_str = ", ".join([os.path.basename(d) for d in c_dirs[:3]]) + (
            "..." if len(c_dirs) > 3 else ""
        )
        
        last_update_str = "从未"
        if s["time"]:
            last_update = datetime.datetime.fromtimestamp(s["time"])
            last_update_str = last_update.strftime("%m-%d %H:%M")
        
        info_layout = QFormLayout()
        info_layout.addRow("文件数量:", QLabel(f"{s['count']:,}" if s["count"] else "未构建"))
        
        status_label = QLabel(
            "✅就绪" if s["ready"] else ("🔄构建中" if s["building"] else "❌未构建")
        )
        status_label.setStyleSheet("color: #28a745;" if s["ready"] else "color: #555;")
        info_layout.addRow("状态:", status_label)
        
        info_layout.addRow("FTS5:", QLabel("✅已启用" if s.get("has_fts") else "❌未启用"))
        info_layout.addRow("MFT:", QLabel("✅已使用" if s.get("used_mft") else "❌未使用"))
        info_layout.addRow("构建时间:", QLabel(last_update_str))
        info_layout.addRow("C盘范围:", QLabel(c_dirs_str))
        info_layout.addRow("索引路径:", QLabel(os.path.basename(s["path"])))
        
        layout.addLayout(info_layout)
        
        # 分隔线
        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        layout.addWidget(line2)
        
        # 按钮
        btn_layout = QHBoxLayout()
        
        def rebuild():
            dlg.accept()
            self._build_index()
        
        def browse():
            p, _ = QFileDialog.getSaveFileName(
                dlg, "选择索引位置",
                os.path.dirname(s["path"]),
                "SQLite (*.db)"
            )
            if p:
                ok, msg = self.index_mgr.change_db_path(p)
                if ok:
                    self.file_watcher.stop()
                    self.file_watcher = FileWatcher(self.index_mgr, config_mgr=self.config_mgr)
                    self._check_index()
                    dlg.accept()
                    self._show_index_mgr()
                else:
                    QMessageBox.critical(dlg, "错误", msg)
        
        def delete():
            reply = QMessageBox.question(dlg, "确认", "确定删除索引？")
            if reply == QMessageBox.Yes:
                self.file_watcher.stop()
                self.index_mgr.close()
                for ext in ["", "-wal", "-shm"]:
                    try:
                        os.remove(self.index_mgr.db_path + ext)
                    except (FileNotFoundError, PermissionError, OSError) as e:
                        logger.warning(f"删除索引文件失败 {ext}: {e}")
                self.index_mgr = IndexManager(
                    db_path=self.index_mgr.db_path,
                    config_mgr=self.config_mgr
                )
                self.file_watcher = FileWatcher(self.index_mgr, config_mgr=self.config_mgr)
                self._check_index()
                dlg.accept()
        
        btn_rebuild = QPushButton("🔄 重建索引")
        btn_rebuild.clicked.connect(rebuild)
        btn_layout.addWidget(btn_rebuild)
        
        btn_browse = QPushButton("📁 更改位置")
        btn_browse.clicked.connect(browse)
        btn_layout.addWidget(btn_browse)
        
        btn_delete = QPushButton("🗑️ 删除索引")
        btn_delete.clicked.connect(delete)
        btn_layout.addWidget(btn_delete)
        
        layout.addLayout(btn_layout)
        dlg.exec()
    
    def _build_index(self):
        """构建索引"""
        if self.index_mgr.is_building:
            return
        
        self.status_label.setText("🔄 正在构建索引...")
        
        self.index_worker = IndexBuildWorker(self.index_mgr, self._get_drives())
        self.index_worker.progress.connect(self._on_index_progress)
        self.index_worker.finished.connect(self._on_index_finished)
        self.index_worker.start()
        
        self._check_index()
    
    @Slot(int, str)
    def _on_index_progress(self, count: int, path: str):
        """索引构建进度"""
        self._check_index()
        self.status_path_label.setText(f"索引: {path[-40:]}")
    
    @Slot()
    def _on_index_finished(self):
        """索引构建完成"""
        self._check_index()
        self.status_path_label.setText("")
        self.status_label.setText(f"✅ 索引完成 ({self.index_mgr.file_count:,})")
        
        self.file_watcher.stop()
        self.file_watcher.start(self._get_drives())
        logger.info("👁️ 文件监控已启动")
    
    # ==================== 工具功能 ====================
    def export_results(self):
        """导出搜索结果"""
        if not self.filtered_results:
            QMessageBox.information(self.main_window, "提示", "没有可导出的结果")
            return
        
        path, _ = QFileDialog.getSaveFileName(
            self.main_window, "导出结果", "",
            "CSV文件 (*.csv);;文本文件 (*.txt);;Excel文件 (*.xlsx)"
        )
        if not path:
            return
        
        ext = os.path.splitext(path)[1].lower()
        
        try:
            data = [
                (r["filename"], r["fullpath"], r["size_str"], r["mtime_str"])
                for r in self.filtered_results
            ]
            
            if ext == ".xlsx":
                try:
                    import openpyxl
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.append(["文件名", "完整路径", "大小", "修改时间"])
                    for row in data:
                        ws.append(row)
                    wb.save(path)
                except ImportError:
                    QMessageBox.warning(self.main_window, "提示", "需要安装openpyxl: pip install openpyxl")
                    return
            elif ext == ".csv":
                import csv
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["文件名", "完整路径", "大小", "修改时间"])
                    writer.writerows(data)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("文件名\t完整路径\t大小\t修改时间\n")
                    for row in data:
                        f.write("\t".join(row) + "\n")
            
            QMessageBox.information(self.main_window, "成功", f"已导出 {len(data)} 条记录")
            logger.info(f"导出成功: {len(data)} 条记录 -> {path}")
        except Exception as e:
            logger.error(f"导出失败: {e}")
            QMessageBox.critical(self.main_window, "导出失败", str(e))
    
    def scan_large_files(self):
        """扫描大文件"""
        if not self.all_results:
            QMessageBox.information(self.main_window, "提示", "请先进行搜索")
            return
        
        min_size = 100 * 1024 * 1024
        large_files = [
            item for item in self.all_results
            if item["type_code"] in (1, 2) and item["size"] >= min_size
        ]
        
        large_files.sort(key=lambda x: x["size"], reverse=True)
        self.filtered_results = large_files
        self.current_page = 1
        self._render_page()
        
        total_size = sum(f["size"] for f in large_files)
        self.status_label.setText(
            f"找到 {len(large_files)} 个大文件 (≥100MB)，共 {format_size(total_size)}"
        )
        self.filter_label.setText(f"大文件: {len(large_files)}/{len(self.all_results)}")
    
    def find_duplicates(self):
        """查找重复文件"""
        if not self.all_results:
            QMessageBox.information(self.main_window, "提示", "请先进行搜索")
            return
        
        from collections import defaultdict
        size_groups = defaultdict(list)
        
        for item in self.all_results:
            if item["type_code"] == 2 and item["size"] > 0:
                key = (item["size"], item["filename"].lower())
                size_groups[key].append(item)
        
        duplicates = []
        for key, items in size_groups.items():
            if len(items) > 1:
                duplicates.extend(items)
        
        duplicates.sort(key=lambda x: (x["size"], x["filename"].lower()), reverse=True)
        
        self.filtered_results = duplicates
        self.current_page = 1
        self._render_page()
        
        self.status_label.setText(f"找到 {len(duplicates)} 个可能重复的文件")
        self.filter_label.setText(f"重复: {len(duplicates)}/{len(self.all_results)}")
    
    def find_empty_folders(self):
        """查找空文件夹"""
        if not self.all_results:
            QMessageBox.information(self.main_window, "提示", "请先进行搜索")
            return
        
        empty_folders = []
        for item in self.all_results:
            if item["type_code"] == 0:
                try:
                    if os.path.exists(item["fullpath"]) and not os.listdir(item["fullpath"]):
                        empty_folders.append(item)
                except (PermissionError, OSError):
                    pass
        
        self.filtered_results = empty_folders
        self.current_page = 1
        self._render_page()
        
        self.status_label.setText(f"找到 {len(empty_folders)} 个空文件夹")
        self.filter_label.setText(f"空文件夹: {len(empty_folders)}/{len(self.all_results)}")
    
    def _show_batch_rename(self):
        """显示批量重命名对话框"""
        selected_items = self._get_selected_items()
        if selected_items:
            targets = selected_items
            scope_text = f"当前选中 {len(targets)} 个项目"
        else:
            targets = list(self.filtered_results)
            if not targets:
                QMessageBox.information(self.main_window, "提示", "没有可重命名的结果")
                return
            scope_text = f"当前筛选结果 {len(targets)} 个项目"
        
        dlg = BatchRenameDialog(self.main_window, targets, self)
        dlg.show(scope_text)
    
    # ==================== 设置对话框 ====================
    def _show_settings(self):
        """显示设置对话框"""
        dlg = QDialog(self.main_window)
        dlg.setWindowTitle("⚙️ 设置")
        dlg.setFixedSize(400, 300)
        
        layout = QVBoxLayout(dlg)
        layout.setSpacing(15)
        
        # 标题
        title = QLabel("常规设置")
        title.setFont(QFont("微软雅黑", 12, QFont.Bold))
        layout.addWidget(title)
        
        # 热键设置
        hotkey_check = QCheckBox("启用全局热键 (Ctrl+Shift+Space)")
        hotkey_check.setChecked(self.config_mgr.get_hotkey_enabled())
        if not HAS_WIN32:
            hotkey_check.setEnabled(False)
            hotkey_check.setText(hotkey_check.text() + " (需要pywin32)")
        layout.addWidget(hotkey_check)
        
        # 托盘设置
        tray_check = QCheckBox("关闭时最小化到托盘")
        tray_check.setChecked(self.config_mgr.get_tray_enabled())
        layout.addWidget(tray_check)
        
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)
        
        # 提示
        tip = QLabel("💡 提示：修改设置后需要重启程序才能完全生效")
        tip.setStyleSheet("color: #888;")
        layout.addWidget(tip)
        
        layout.addStretch()
        
        # 按钮
        btn_layout = QHBoxLayout()
        
        def save_settings():
            self.config_mgr.set_hotkey_enabled(hotkey_check.isChecked())
            self.config_mgr.set_tray_enabled(tray_check.isChecked())
            
            if hotkey_check.isChecked() and not self.hotkey_mgr.registered and HAS_WIN32:
                self.hotkey_mgr.start()
            elif not hotkey_check.isChecked() and self.hotkey_mgr.registered:
                self.hotkey_mgr.stop()
            
            QMessageBox.information(dlg, "成功", "设置已保存")
            dlg.accept()
        
        btn_save = QPushButton("保存")
        btn_save.clicked.connect(save_settings)
        btn_layout.addWidget(btn_save)
        
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dlg.reject)
        btn_layout.addWidget(btn_cancel)
        
        layout.addLayout(btn_layout)
        dlg.exec()
    
    def _show_c_drive_settings(self):
        """显示C盘目录设置"""
        dlg = CDriveSettingsDialog(
            self.main_window,
            self.config_mgr,
            self.index_mgr,
            self._rebuild_c_drive
        )
        dlg.exec()
    
    def _rebuild_c_drive(self, drive_letter: str = "C"):
        """重建C盘索引"""
        if self.index_mgr.is_building:
            QMessageBox.warning(self.main_window, "提示", "索引正在构建中，请稍后")
            return
        
        self.status_label.setText(f"🔄 正在重建 {drive_letter}: 盘索引...")
        
        self.index_worker = IndexBuildWorker(self.index_mgr, [f"{drive_letter}:\\"])
        self.index_worker.progress.connect(self._on_index_progress)
        self.index_worker.finished.connect(self._on_index_finished)
        self.index_worker.start()
        
        self._check_index()
    
    def _show_shortcuts(self):
        """显示快捷键列表"""
        shortcuts = """
快捷键列表:

搜索操作:
  Ctrl+F      聚焦搜索框
  Enter       开始搜索
  F5          刷新搜索
  Escape      停止搜索/清空

文件操作:
  Enter       打开选中文件
  Ctrl+O      打开文件
  Ctrl+L      定位文件
  Delete      删除文件
  Space       预览文件

编辑操作:
  Ctrl+A      全选
  Ctrl+C      复制路径
  Ctrl+Shift+C  复制文件

工具:
  Ctrl+E      导出结果
  Ctrl+G      大文件扫描

全局热键:
  Ctrl+Shift+Space  迷你搜索窗口
  Ctrl+Shift+Tab    主窗口
        """
        
        dlg = QDialog(self.main_window)
        dlg.setWindowTitle("⌨️ 快捷键列表")
        dlg.setFixedSize(350, 480)
        
        layout = QVBoxLayout(dlg)
        
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 10))
        text.setPlainText(shortcuts)
        layout.addWidget(text)
        
        btn = QPushButton("关闭")
        btn.clicked.connect(dlg.accept)
        layout.addWidget(btn)
        
        dlg.exec()
    
    def _show_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self.main_window,
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
            "© 2024"
        )
    
    # ==================== 迷你搜索 ====================
    def show_mini_search(self):
        """显示迷你搜索窗口"""
        if not self.mini_search:
            self.mini_search = MiniSearchWindow(self)
        self.mini_search.show_centered()
    
    def show_mini_search_from_thread(self):
        """从线程调用显示迷你搜索"""
        QTimer.singleShot(0, self.show_mini_search)
    
    # ==================== 程序退出 ====================
    def do_quit(self):
        """退出程序"""
        # 停止搜索
        if self.search_worker:
            self.search_worker.stop()
        if self.index_worker:
            self.index_worker.stop()
        
        # 停止各管理器
        self.hotkey_mgr.stop()
        self.tray_mgr.stop()
        self.file_watcher.stop()
        self.index_mgr.close()
        
        # 关闭窗口
        if self.main_window:
            self.main_window.close()
        
        QApplication.quit()
    
    def run(self):
        """运行应用"""
        self.main_window.show()


# ==================== 程序入口 ====================
def main():
    logger.info("🚀 极速文件搜索 V42 增强版 - PySide6 版本")
    logger.info("新增功能: C盘目录设置、磁盘筛选联动、全局热键、系统托盘")
    
    # 高DPI支持
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    
    # 设置应用样式
    config = ConfigManager()
    app.setStyle(config.get_theme())
    
    # 设置应用字体
    font = QFont("微软雅黑", 9)
    app.setFont(font)
    
    # 创建并运行应用
    search_app = SearchApp()
    search_app.run()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()