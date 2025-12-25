"""
重复文件查找对话框
"""
from PySide6.QtWidgets import (
	QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
	QTreeWidget, QTreeWidgetItem, QProgressBar, QLineEdit,
	QCheckBox, QMessageBox, QFileDialog, QHeaderView
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from filesearch.core.file_hash import DuplicateFileFinder
from filesearch.utils import format_size
import os
import subprocess


class DuplicateFinderDialog(QDialog):
	"""重复文件查找对话框"""
	
	def __init__(self, parent=None, default_path=""):
		super().__init__(parent)
		self.setWindowTitle("🔍 重复文件查找")
		self.setMinimumSize(900, 600)
		self.setModal(True)
		
		self.finder = None
		self.duplicates = {}
		
		layout = QVBoxLayout(self)
		layout.setContentsMargins(15, 15, 15, 15)
		layout.setSpacing(10)
		
		# 标题
		title = QLabel("查找重复文件（按内容 Hash 比对）")
		title.setFont(QFont("微软雅黑", 12, QFont.Bold))
		title.setStyleSheet("color: #0078d4;")
		layout.addWidget(title)
		
		# 搜索路径
		path_layout = QHBoxLayout()
		path_layout.addWidget(QLabel("搜索路径:"))
		self.path_input = QLineEdit(default_path)
		path_layout.addWidget(self.path_input, 1)
		browse_btn = QPushButton("📂 浏览")
		browse_btn.setFixedWidth(80)
		browse_btn.clicked.connect(self._browse_path)
		path_layout.addWidget(browse_btn)
		layout.addLayout(path_layout)
		
		# 选项
		options_layout = QHBoxLayout()
		self.min_size_check = QCheckBox("最小文件大小:")
		self.min_size_check.setChecked(True)
		options_layout.addWidget(self.min_size_check)
		self.min_size_input = QLineEdit("1")
		self.min_size_input.setFixedWidth(80)
		options_layout.addWidget(self.min_size_input)
		options_layout.addWidget(QLabel("MB"))
		options_layout.addStretch()
		layout.addLayout(options_layout)
		
		# 进度条
		self.progress = QProgressBar()
		self.progress.setVisible(False)
		layout.addWidget(self.progress)
		
		self.status_label = QLabel("")
		self.status_label.setStyleSheet("color: #666;")
		layout.addWidget(self.status_label)
		
		# 结果树
		self.result_tree = QTreeWidget()
		self.result_tree.setColumnCount(4)
		self.result_tree.setHeaderLabels(["📁 重复组", "📄 文件名", "📊 大小", "📂 完整路径"])
		self.result_tree.setRootIsDecorated(True)
		self.result_tree.setAlternatingRowColors(True)
		header = self.result_tree.header()
		header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
		header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
		header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
		header.setSectionResizeMode(3, QHeaderView.Stretch)
		self.result_tree.itemDoubleClicked.connect(self._open_file)
		layout.addWidget(self.result_tree, 1)
		
		# 统计信息
		self.stats_label = QLabel("")
		self.stats_label.setFont(QFont("微软雅黑", 9))
		self.stats_label.setStyleSheet("color: #0078d4;")
		layout.addWidget(self.stats_label)
		
		# 按钮
		btn_layout = QHBoxLayout()
		self.start_btn = QPushButton("🔍 开始查找")
		self.start_btn.clicked.connect(self._start_find)
		btn_layout.addWidget(self.start_btn)
		
		self.stop_btn = QPushButton("⏹ 停止")
		self.stop_btn.setEnabled(False)
		self.stop_btn.clicked.connect(self._stop_find)
		btn_layout.addWidget(self.stop_btn)
		
		delete_btn = QPushButton("🗑️ 删除选中")
		delete_btn.clicked.connect(self._delete_selected)
		btn_layout.addWidget(delete_btn)
		
		export_btn = QPushButton("📤 导出列表")
		export_btn.clicked.connect(self._export_list)
		btn_layout.addWidget(export_btn)
		
		btn_layout.addStretch()
		close_btn = QPushButton("关闭")
		close_btn.clicked.connect(self.accept)
		btn_layout.addWidget(close_btn)
		
		layout.addLayout(btn_layout)
	
	def _browse_path(self):
		path = QFileDialog.getExistingDirectory(self, "选择搜索目录")
		if path:
			self.path_input.setText(path)
	
	def _start_find(self):
		search_path = self.path_input.text().strip()
		if not search_path or not os.path.isdir(search_path):
			QMessageBox.warning(self, "警告", "请输入有效的搜索路径")
			return
		
		min_size = 0
		if self.min_size_check.isChecked():
			try:
				min_size = int(float(self.min_size_input.text()) * 1024 * 1024)
			except ValueError:
				min_size = 0
		
		self.result_tree.clear()
		self.stats_label.setText("")
		self.progress.setVisible(True)
		self.progress.setRange(0, 0)
		self.start_btn.setEnabled(False)
		self.stop_btn.setEnabled(True)
		
		self.finder = DuplicateFileFinder([search_path], min_size)
		self.finder.progress.connect(self._on_progress)
		self.finder.duplicates_ready.connect(self._on_duplicates_ready)
		self.finder.finished_signal.connect(self._on_finished)
		self.finder.start()
	
	def _stop_find(self):
		if self.finder:
			self.finder.stop()
	
	def _on_progress(self, current, total, message):
		self.status_label.setText(message)
		if total > 0:
			self.progress.setRange(0, total)
			self.progress.setValue(current)
	
	def _on_duplicates_ready(self, duplicates):
		self.duplicates = duplicates
		self._display_results()
	
	def _display_results(self):
		self.result_tree.clear()
		
		if not self.duplicates:
			self.stats_label.setText("✅ 未找到重复文件")
			return
		
		total_groups = len(self.duplicates)
		total_files = sum(len(files) for files in self.duplicates.values())
		total_wasted = 0
		
		for idx, (file_hash, files) in enumerate(self.duplicates.items(), 1):
			if len(files) < 2:
				continue
			
			# 计算浪费的空间（保留1个，删除其他）
			try:
				file_size = os.path.getsize(files[0])
				wasted = file_size * (len(files) - 1)
				total_wasted += wasted
			except Exception:
				file_size = 0
				wasted = 0
			
			# 创建组节点
			group_item = QTreeWidgetItem(self.result_tree)
			group_item.setText(0, f"组 {idx}")
			group_item.setText(1, f"{len(files)} 个重复文件")
			group_item.setText(2, f"浪费: {format_size(wasted)}")
			group_item.setText(3, f"Hash: {file_hash[:16]}...")
			
			# 添加文件节点
			for filepath in sorted(files):
				file_item = QTreeWidgetItem(group_item)
				file_item.setText(0, "")
				file_item.setText(1, os.path.basename(filepath))
				file_item.setText(2, format_size(file_size))
				file_item.setText(3, filepath)
				file_item.setData(0, Qt.UserRole, filepath)
		
		self.result_tree.expandAll()
		self.stats_label.setText(
			f"📊 找到 {total_groups} 组重复文件，共 {total_files} 个文件，"
			f"可释放空间: {format_size(total_wasted)}"
		)
	
	def _on_finished(self):
		self.progress.setVisible(False)
		self.start_btn.setEnabled(True)
		self.stop_btn.setEnabled(False)
		self.status_label.setText("✅ 查找完成")
	
	def _delete_selected(self):
		selected_items = self.result_tree.selectedItems()
		if not selected_items:
			QMessageBox.information(self, "提示", "请先选择要删除的文件")
			return
		
		files_to_delete = []
		for item in selected_items:
			filepath = item.data(0, Qt.UserRole)
			if filepath:
				files_to_delete.append(filepath)
		
		if not files_to_delete:
			return
		
		reply = QMessageBox.question(
			self, "确认删除",
			f"确定要删除 {len(files_to_delete)} 个文件吗？\n（将移动到回收站）",
			QMessageBox.Yes | QMessageBox.No
		)
		
		if reply == QMessageBox.Yes:
			deleted = 0
			for filepath in files_to_delete:
				try:
					import send2trash
					send2trash.send2trash(filepath)
					deleted += 1
				except Exception:
					try:
						os.remove(filepath)
						deleted += 1
					except Exception:
						pass
			
			QMessageBox.information(self, "完成", f"已删除 {deleted}/{len(files_to_delete)} 个文件")
			# 重新查找
			if self.duplicates:
				self._start_find()
	
	def _export_list(self):
		if not self.duplicates:
			QMessageBox.information(self, "提示", "没有可导出的结果")
			return
		
		filepath, _ = QFileDialog.getSaveFileName(
			self, "导出重复文件列表", "duplicates.txt", "文本文件 (*.txt)"
		)
		
		if filepath:
			try:
				with open(filepath, 'w', encoding='utf-8') as f:
					f.write("重复文件列表\n")
					f.write("=" * 80 + "\n\n")
					
					for idx, (file_hash, files) in enumerate(self.duplicates.items(), 1):
						if len(files) < 2:
							continue
						f.write(f"组 {idx} (Hash: {file_hash}):\n")
						for fp in sorted(files):
							f.write(f"  {fp}\n")
						f.write("\n")
				
				QMessageBox.information(self, "成功", f"已导出到: {filepath}")
			except Exception as e:
				QMessageBox.warning(self, "错误", f"导出失败: {e}")
	
	def _open_file(self, item, column):
		filepath = item.data(0, Qt.UserRole)
		if filepath and os.path.exists(filepath):
			try:
				subprocess.Popen(f'explorer /select,"{filepath}"')
			except Exception:
				pass
