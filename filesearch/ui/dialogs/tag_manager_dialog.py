"""
标签管理对话框
"""

import os
import logging
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QLineEdit, QTextEdit,
    QColorDialog, QMessageBox, QInputDialog, QGroupBox,
    QCheckBox, QSplitter, QWidget, QTabWidget
)

from ...core.tag_manager import TagManager

logger = logging.getLogger(__name__)


class TagManagerDialog(QDialog):
    """标签管理对话框"""
    
    tags_changed = Signal()  # 标签变更信号
    
    def __init__(self, parent=None, tag_manager: TagManager = None, selected_files: list = None):
        super().__init__(parent)
        self.tag_manager = tag_manager or TagManager()
        self.selected_files = selected_files or []
        
        self.setWindowTitle("🏷 标签管理")
        self.setMinimumSize(900, 650)
        self.setModal(True)
        
        self._init_ui()
        self._load_data()
    
    def _init_ui(self):
        """初始化 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # 标题
        title = QLabel("🏷 标签管理")
        title.setFont(QFont("微软雅黑", 14, QFont.Bold))
        layout.addWidget(title)
        
        # 创建选项卡
        tabs = QTabWidget()
        layout.addWidget(tabs)
        
        # Tab 1: 标签云
        tabs.addTab(self._create_tag_cloud_tab(), "📊 标签云")
        
        # Tab 2: 文件标签
        if self.selected_files:
            tabs.addTab(self._create_file_tags_tab(), "📄 文件标签")
        
        # Tab 3: 标签搜索
        tabs.addTab(self._create_search_tab(), "🔍 标签搜索")
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_cleanup = QPushButton("🗑 清理失效文件")
        self.btn_cleanup.clicked.connect(self._cleanup_missing_files)
        btn_layout.addWidget(self.btn_cleanup)
        
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)
        
        layout.addLayout(btn_layout)
    
    def _create_tag_cloud_tab(self) -> QWidget:
        """创建标签云选项卡"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 统计信息
        stats = self.tag_manager.get_statistics()
        info_text = f"总标签数: {stats['total_tags']} | 已标记文件: {stats['total_files']} | 平均标签/文件: {stats['avg_tags_per_file']:.1f}"
        info_label = QLabel(info_text)
        info_label.setStyleSheet("color: #666; padding: 5px;")
        layout.addWidget(info_label)
        
        # 标签列表
        self.tag_list = QListWidget()
        self.tag_list.setAlternatingRowColors(True)
        self.tag_list.itemDoubleClicked.connect(self._show_tag_files)
        layout.addWidget(self.tag_list)
        
        # 操作按钮
        btn_layout = QHBoxLayout()
        
        btn_new_tag = QPushButton("➕ 新建标签")
        btn_new_tag.clicked.connect(self._create_new_tag)
        btn_layout.addWidget(btn_new_tag)
        
        btn_rename = QPushButton("✏ 重命名")
        btn_rename.clicked.connect(self._rename_tag)
        btn_layout.addWidget(btn_rename)
        
        btn_set_color = QPushButton("🎨 设置颜色")
        btn_set_color.clicked.connect(self._set_tag_color)
        btn_layout.addWidget(btn_set_color)
        
        btn_delete = QPushButton("🗑 删除标签")
        btn_delete.clicked.connect(self._delete_tag)
        btn_layout.addWidget(btn_delete)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        return widget
    
    def _create_file_tags_tab(self) -> QWidget:
        """创建文件标签选项卡"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 显示选中的文件
        info = QLabel(f"已选择 {len(self.selected_files)} 个文件")
        layout.addWidget(info)
        
        # 文件列表
        file_list = QListWidget()
        for file_path in self.selected_files[:20]:  # 最多显示20个
            item = QListWidgetItem(os.path.basename(file_path))
            item.setToolTip(file_path)
            file_list.addItem(item)
        if len(self.selected_files) > 20:
            file_list.addItem(f"... 还有 {len(self.selected_files) - 20} 个文件")
        layout.addWidget(file_list)
        
        # 当前标签
        layout.addWidget(QLabel("当前标签:"))
        self.current_tags_list = QListWidget()
        self.current_tags_list.setMaximumHeight(100)
        layout.addWidget(self.current_tags_list)
        
        # 添加标签
        add_layout = QHBoxLayout()
        add_layout.addWidget(QLabel("添加标签:"))
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("输入标签名（用逗号分隔多个标签）")
        self.tag_input.returnPressed.connect(self._add_tags_to_files)
        add_layout.addWidget(self.tag_input)
        
        btn_add = QPushButton("➕ 添加")
        btn_add.clicked.connect(self._add_tags_to_files)
        add_layout.addWidget(btn_add)
        
        layout.addLayout(add_layout)
        
        # 移除标签按钮
        btn_remove = QPushButton("🗑 移除选中标签")
        btn_remove.clicked.connect(self._remove_tags_from_files)
        layout.addWidget(btn_remove)
        
        # 加载当前标签
        self._load_file_tags()
        
        return widget
    
    def _create_search_tab(self) -> QWidget:
        """创建搜索选项卡"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 搜索框
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索标签:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("tag1,tag2 (逗号分隔)")
        self.search_input.textChanged.connect(self._search_by_tags)
        search_layout.addWidget(self.search_input)
        
        self.match_all_check = QCheckBox("匹配所有标签(AND)")
        search_layout.addWidget(self.match_all_check)
        
        btn_search = QPushButton("🔍 搜索")
        btn_search.clicked.connect(self._search_by_tags)
        search_layout.addWidget(btn_search)
        
        layout.addLayout(search_layout)
        
        # 结果列表
        layout.addWidget(QLabel("搜索结果:"))
        self.search_results = QListWidget()
        self.search_results.itemDoubleClicked.connect(self._open_search_result)
        layout.addWidget(self.search_results)
        
        return widget
    
    def _load_data(self):
        """加载数据"""
        self._load_tag_cloud()
    
    def _load_tag_cloud(self):
        """加载标签云"""
        self.tag_list.clear()
        
        tag_cloud = self.tag_manager.get_tag_cloud()
        for tag_info in tag_cloud:
            tag = tag_info['tag']
            count = tag_info['count']
            color = tag_info['color']
            
            item = QListWidgetItem(f"🏷 {tag} ({count})")
            item.setData(Qt.UserRole, tag)
            
            # 设置颜色
            try:
                item.setForeground(QColor(color))
            except:
                pass
            
            self.tag_list.addItem(item)
    
    def _load_file_tags(self):
        """加载文件标签"""
        if not self.selected_files:
            return
        
        self.current_tags_list.clear()
        
        # 获取所有选中文件的标签（交集）
        if len(self.selected_files) == 1:
            tags = self.tag_manager.get_file_tags(self.selected_files[0])
        else:
            # 多个文件，显示共同标签
            tag_sets = [set(self.tag_manager.get_file_tags(f)) for f in self.selected_files]
            tags = list(set.intersection(*tag_sets)) if tag_sets else []
        
        for tag in tags:
            item = QListWidgetItem(f"🏷 {tag}")
            item.setData(Qt.UserRole, tag)
            self.current_tags_list.addItem(item)
    
    def _create_new_tag(self):
        """创建新标签"""
        tag, ok = QInputDialog.getText(self, "新建标签", "标签名:")
        if ok and tag.strip():
            tag = tag.strip().lower()
            # 标签创建通过添加到文件实现
            QMessageBox.information(self, "提示", f"标签 '{tag}' 已创建\n请在文件标签页中添加到文件")
    
    def _rename_tag(self):
        """重命名标签"""
        current = self.tag_list.currentItem()
        if not current:
            QMessageBox.warning(self, "警告", "请先选择一个标签")
            return
        
        old_tag = current.data(Qt.UserRole)
        new_tag, ok = QInputDialog.getText(self, "重命名标签", f"将 '{old_tag}' 重命名为:", text=old_tag)
        
        if ok and new_tag.strip() and new_tag.strip().lower() != old_tag:
            if self.tag_manager.rename_tag(old_tag, new_tag.strip().lower()):
                QMessageBox.information(self, "成功", f"标签已重命名: {old_tag} → {new_tag}")
                self._load_tag_cloud()
                self.tags_changed.emit()
            else:
                QMessageBox.warning(self, "失败", "重命名失败")
    
    def _set_tag_color(self):
        """设置标签颜色"""
        current = self.tag_list.currentItem()
        if not current:
            QMessageBox.warning(self, "警告", "请先选择一个标签")
            return
        
        tag = current.data(Qt.UserRole)
        color = QColorDialog.getColor()
        
        if color.isValid():
            if self.tag_manager.set_tag_color(tag, color.name()):
                self._load_tag_cloud()
                self.tags_changed.emit()
    
    def _delete_tag(self):
        """删除标签"""
        current = self.tag_list.currentItem()
        if not current:
            QMessageBox.warning(self, "警告", "请先选择一个标签")
            return
        
        tag = current.data(Qt.UserRole)
        count = self.tag_manager.get_tag_count(tag)
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除标签 '{tag}' 吗？\n这将从 {count} 个文件中移除该标签。",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            if self.tag_manager.delete_tag(tag):
                QMessageBox.information(self, "成功", f"标签 '{tag}' 已删除")
                self._load_tag_cloud()
                self.tags_changed.emit()
    
    def _show_tag_files(self, item):
        """显示标签关联的文件"""
        tag = item.data(Qt.UserRole)
        files = self.tag_manager.get_files_by_tag(tag)
        
        msg = f"标签 '{tag}' 关联的文件 ({len(files)}):\n\n"
        msg += '\n'.join(files[:50])
        if len(files) > 50:
            msg += f"\n... 还有 {len(files) - 50} 个文件"
        
        QMessageBox.information(self, f"标签: {tag}", msg)
    
    def _add_tags_to_files(self):
        """给文件添加标签"""
        if not self.selected_files:
            return
        
        tags_text = self.tag_input.text().strip()
        if not tags_text:
            return
        
        tags = [t.strip().lower() for t in tags_text.split(',') if t.strip()]
        
        success_count = 0
        for file_path in self.selected_files:
            for tag in tags:
                if self.tag_manager.add_tag(file_path, tag):
                    success_count += 1
        
        if success_count > 0:
            QMessageBox.information(self, "成功", f"已添加 {len(tags)} 个标签到 {len(self.selected_files)} 个文件")
            self.tag_input.clear()
            self._load_file_tags()
            self._load_tag_cloud()
            self.tags_changed.emit()
    
    def _remove_tags_from_files(self):
        """从文件移除标签"""
        if not self.selected_files:
            return
        
        current = self.current_tags_list.currentItem()
        if not current:
            QMessageBox.warning(self, "警告", "请先选择要移除的标签")
            return
        
        tag = current.data(Qt.UserRole)
        
        for file_path in self.selected_files:
            self.tag_manager.remove_tag(file_path, tag)
        
        QMessageBox.information(self, "成功", f"已从 {len(self.selected_files)} 个文件移除标签 '{tag}'")
        self._load_file_tags()
        self._load_tag_cloud()
        self.tags_changed.emit()
    
    def _search_by_tags(self):
        """按标签搜索"""
        tags_text = self.search_input.text().strip()
        if not tags_text:
            self.search_results.clear()
            return
        
        tags = [t.strip().lower() for t in tags_text.split(',') if t.strip()]
        match_all = self.match_all_check.isChecked()
        
        files = self.tag_manager.get_files_by_tags(tags, match_all=match_all)
        
        self.search_results.clear()
        for file_path in files[:200]:  # 最多显示200个
            item = QListWidgetItem(file_path)
            item.setData(Qt.UserRole, file_path)
            self.search_results.addItem(item)
        
        if len(files) > 200:
            self.search_results.addItem(f"... 还有 {len(files) - 200} 个结果")
    
    def _open_search_result(self, item):
        """打开搜索结果"""
        file_path = item.data(Qt.UserRole)
        if file_path and os.path.exists(file_path):
            os.startfile(os.path.dirname(file_path))
    
    def _cleanup_missing_files(self):
        """清理失效文件"""
        count = self.tag_manager.cleanup_missing_files()
        QMessageBox.information(self, "清理完成", f"已清理 {count} 个失效文件")
        self._load_tag_cloud()


__all__ = ['TagManagerDialog']
