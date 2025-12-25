"""
剪贴板历史对话框
"""
from PySide6.QtWidgets import (
	QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
	QListWidget, QListWidgetItem, QPushButton, QLabel,
	QMessageBox, QApplication
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
import datetime


class ClipboardHistoryDialog(QDialog):
	"""剪贴板历史对话框"""
	
	def __init__(self, parent, clipboard_mgr):
		super().__init__(parent)
		self.clipboard_mgr = clipboard_mgr
		self.filtered_items = []
		
		self.setWindowTitle("剪贴板历史")
		self.setMinimumSize(700, 500)
		
		self._init_ui()
		self._load_history()
	
	def _init_ui(self):
		"""初始化界面"""
		layout = QVBoxLayout(self)
		
		# 标题
		title = QLabel("📋 剪贴板历史记录")
		title_font = QFont()
		title_font.setPointSize(12)
		title_font.setBold(True)
		title.setFont(title_font)
		layout.addWidget(title)
		
		# 搜索框
		search_layout = QHBoxLayout()
		search_label = QLabel("搜索:")
		self.search_input = QLineEdit()
		self.search_input.setPlaceholderText("输入关键词搜索...")
		self.search_input.textChanged.connect(self._on_search)
		search_layout.addWidget(search_label)
		search_layout.addWidget(self.search_input)
		layout.addLayout(search_layout)
		
		# 历史列表
		self.list_widget = QListWidget()
		self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
		layout.addWidget(self.list_widget)
		
		# 按钮栏
		btn_layout = QHBoxLayout()
		
		self.btn_copy = QPushButton("📋 复制")
		self.btn_copy.clicked.connect(self._copy_selected)
		btn_layout.addWidget(self.btn_copy)
		
		self.btn_delete = QPushButton("🗑️ 删除")
		self.btn_delete.clicked.connect(self._delete_selected)
		btn_layout.addWidget(self.btn_delete)
		
		self.btn_clear = QPushButton("🧹 清空全部")
		self.btn_clear.clicked.connect(self._clear_all)
		btn_layout.addWidget(self.btn_clear)
		
		btn_layout.addStretch()
		
		self.btn_close = QPushButton("关闭")
		self.btn_close.clicked.connect(self.close)
		btn_layout.addWidget(self.btn_close)
		
		layout.addLayout(btn_layout)
		
		# 状态标签
		self.status_label = QLabel()
		layout.addWidget(self.status_label)
	
	def _load_history(self, keyword=None):
		"""加载历史记录"""
		self.list_widget.clear()
		
		if keyword:
			items = self.clipboard_mgr.search_history(keyword)
		else:
			items = self.clipboard_mgr.get_history()
		
		self.filtered_items = items
		
		for timestamp, text in items:
			# 格式化时间
			dt = datetime.datetime.fromtimestamp(timestamp)
			time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
			
			# 截断过长的文本
			preview = text.replace('\n', ' ')[:100]
			if len(text) > 100:
				preview += "..."
			
			# 创建列表项
			item_text = f"[{time_str}] {preview}"
			item = QListWidgetItem(item_text)
			item.setData(Qt.UserRole, text)  # 存储完整文本
			self.list_widget.addItem(item)
		
		self.status_label.setText(f"共 {len(items)} 条记录")
	
	def _on_search(self, text):
		"""搜索处理"""
		self._load_history(text if text.strip() else None)
	
	def _on_item_double_clicked(self, item):
		"""双击复制"""
		self._copy_selected()
		self.close()
	
	def _copy_selected(self):
		"""复制选中项"""
		current_item = self.list_widget.currentItem()
		if not current_item:
			QMessageBox.information(self, "提示", "请先选择一个条目")
			return
		
		text = current_item.data(Qt.UserRole)
		clipboard = QApplication.clipboard()
		clipboard.setText(text)
		
		# 临时停止监控，避免把刚复制的内容再次添加到历史
		self.clipboard_mgr.timer.stop()
		self.clipboard_mgr.last_text = text
		self.clipboard_mgr.timer.start(1000)
		
		self.status_label.setText("✅ 已复制到剪贴板")
	
	def _delete_selected(self):
		"""删除选中项"""
		current_item = self.list_widget.currentItem()
		if not current_item:
			QMessageBox.information(self, "提示", "请先选择一个条目")
			return
		
		text = current_item.data(Qt.UserRole)
		self.clipboard_mgr.remove_item(text)
		self._load_history()
		self.status_label.setText("✅ 已删除")
	
	def _clear_all(self):
		"""清空所有记录"""
		reply = QMessageBox.question(
			self, 
			"确认", 
			"确定要清空所有剪贴板历史记录吗？",
			QMessageBox.Yes | QMessageBox.No
		)
		
		if reply == QMessageBox.Yes:
			self.clipboard_mgr.clear_history()
			self._load_history()
			self.status_label.setText("✅ 已清空")
