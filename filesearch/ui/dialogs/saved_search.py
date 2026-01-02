"""
保存的搜索条件管理对话框
"""
from PySide6.QtWidgets import (
	QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
	QListWidget, QListWidgetItem, QInputDialog, QMessageBox,
	QLineEdit
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
import json


class SavedSearchDialog(QDialog):
	"""保存的搜索条件管理对话框"""
	
	def __init__(self, parent=None, config_mgr=None):
		super().__init__(parent)
		self.setWindowTitle("💾 保存的搜索")
		self.setMinimumSize(600, 500)
		self.setModal(True)
		
		self.parent_window = parent
		self.config_mgr = config_mgr
		self.saved_searches = self._load_saved_searches()
		
		layout = QVBoxLayout(self)
		layout.setContentsMargins(15, 15, 15, 15)
		layout.setSpacing(10)
		
		# 标题
		title = QLabel("💾 保存的搜索条件")
		title.setFont(QFont("微软雅黑", 12, QFont.Bold))
		title.setStyleSheet("color: #0078d4;")
		layout.addWidget(title)
		
		# 列表
		self.list_widget = QListWidget()
		self.list_widget.itemDoubleClicked.connect(self._execute_search)
		layout.addWidget(self.list_widget, 1)
		
		# 按钮
		btn_layout = QHBoxLayout()
		save_current_btn = QPushButton("💾 保存当前搜索")
		save_current_btn.clicked.connect(self._save_current)
		btn_layout.addWidget(save_current_btn)
		
		execute_btn = QPushButton("▶ 执行")
		execute_btn.clicked.connect(lambda: self._execute_search(self.list_widget.currentItem()))
		btn_layout.addWidget(execute_btn)
		
		rename_btn = QPushButton("✏ 重命名")
		rename_btn.clicked.connect(self._rename)
		btn_layout.addWidget(rename_btn)
		
		delete_btn = QPushButton("🗑️ 删除")
		delete_btn.clicked.connect(self._delete)
		btn_layout.addWidget(delete_btn)
		
		btn_layout.addStretch()
		close_btn = QPushButton("关闭")
		close_btn.clicked.connect(self.accept)
		btn_layout.addWidget(close_btn)
		
		layout.addLayout(btn_layout)
		
		# 刷新列表
		self._refresh_list()
	
	def _load_saved_searches(self):
		"""从配置加载保存的搜索"""
		if self.config_mgr:
			try:
				return self.config_mgr.get_saved_searches()
			except Exception:
				pass
		return []
	
	def _save_saved_searches(self):
		"""保存搜索列表到配置"""
		if self.config_mgr:
			try:
				self.config_mgr.set_saved_searches(self.saved_searches)
			except Exception:
				pass
	
	def _refresh_list(self):
		"""刷新列表显示"""
		self.list_widget.clear()
		for search in self.saved_searches:
			name = search.get("name", "未命名")
			query = search.get("query", "")
			filters = search.get("filters", {})
			
			# 构建描述
			desc_parts = [query] if query else []
			if filters.get("ext"):
				desc_parts.append(f"ext:{','.join(filters['ext'])}")
			if filters.get("size_min"):
				desc_parts.append(f"size:>{filters['size_min']//1024//1024}MB")
			if filters.get("date_after"):
				desc_parts.append(f"dm:{filters['date_after']}")
			
			desc = " ".join(desc_parts) or "(无条件)"
			
			item = QListWidgetItem(f"🔍 {name}\n    {desc}")
			item.setData(Qt.UserRole, search)
			self.list_widget.addItem(item)
	
	def _save_current(self):
		"""保存当前搜索条件"""
		if not self.parent_window:
			QMessageBox.warning(self, "警告", "无法获取当前搜索条件")
			return
		
		# 获取当前搜索条件
		try:
			query = self.parent_window.entry_kw.text().strip()
			if not query:
				QMessageBox.warning(self, "警告", "当前没有搜索关键词")
				return
		except Exception:
			QMessageBox.warning(self, "警告", "无法获取当前搜索条件")
			return
		
		# 输入名称
		name, ok = QInputDialog.getText(
			self, "保存搜索", "请输入搜索名称:",
			QLineEdit.Normal, query
		)
		
		if ok and name:
			# 创建搜索对象
			search = {
				"name": name,
				"query": query,
				"filters": {}  # 可以扩展保存更多过滤条件
			}
			
			self.saved_searches.append(search)
			self._save_saved_searches()
			self._refresh_list()
			QMessageBox.information(self, "成功", f"已保存搜索: {name}")
	
	def _execute_search(self, item):
		"""执行选中的搜索"""
		if not item:
			return
		
		search = item.data(Qt.UserRole)
		if not search or not self.parent_window:
			return
		
		# 设置搜索条件并执行
		try:
			query = search.get("query", "")
			self.parent_window.entry_kw.setText(query)
			self.parent_window.start_search()
			self.accept()  # 关闭对话框
		except Exception as e:
			QMessageBox.warning(self, "错误", f"执行搜索失败: {e}")
	
	def _rename(self):
		"""重命名选中的搜索"""
		item = self.list_widget.currentItem()
		if not item:
			QMessageBox.information(self, "提示", "请先选择一个搜索")
			return
		
		search = item.data(Qt.UserRole)
		old_name = search.get("name", "")
		
		new_name, ok = QInputDialog.getText(
			self, "重命名", "请输入新名称:",
			QLineEdit.Normal, old_name
		)
		
		if ok and new_name:
			search["name"] = new_name
			self._save_saved_searches()
			self._refresh_list()
	
	def _delete(self):
		"""删除选中的搜索"""
		item = self.list_widget.currentItem()
		if not item:
			QMessageBox.information(self, "提示", "请先选择一个搜索")
			return
		
		search = item.data(Qt.UserRole)
		name = search.get("name", "")
		
		reply = QMessageBox.question(
			self, "确认删除",
			f"确定要删除搜索 '{name}' 吗？",
			QMessageBox.Yes | QMessageBox.No
		)
		
		if reply == QMessageBox.Yes:
			self.saved_searches.remove(search)
			self._save_saved_searches()
			self._refresh_list()
