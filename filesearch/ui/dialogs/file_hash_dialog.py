"""
文件 Hash 计算对话框
"""
from PySide6.QtWidgets import (
	QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
	QTreeWidget, QTreeWidgetItem, QProgressBar, QHeaderView,
	QMessageBox, QFileDialog, QApplication
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from filesearch.core.file_hash import FileHashCalculator
from filesearch.utils import format_size
import os


class FileHashDialog(QDialog):
	"""文件 Hash 计算对话框"""
	
	def __init__(self, parent=None, files=None):
		super().__init__(parent)
		self.setWindowTitle("🔐 文件 Hash 计算")
		self.setMinimumSize(800, 500)
		self.setModal(True)
		
		self.files = files or []
		self.calculator = None
		self.hash_results = {}  # {filepath: (md5, sha256)}
		
		layout = QVBoxLayout(self)
		layout.setContentsMargins(15, 15, 15, 15)
		layout.setSpacing(10)
		
		# 标题
		title = QLabel(f"计算 {len(self.files)} 个文件的 Hash 值")
		title.setFont(QFont("微软雅黑", 12, QFont.Bold))
		title.setStyleSheet("color: #0078d4;")
		layout.addWidget(title)
		
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
		self.result_tree.setHeaderLabels(["📄 文件名", "📊 大小", "🔐 MD5", "🔐 SHA256"])
		self.result_tree.setAlternatingRowColors(True)
		header = self.result_tree.header()
		header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
		header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
		header.setSectionResizeMode(2, QHeaderView.Stretch)
		header.setSectionResizeMode(3, QHeaderView.Stretch)
		layout.addWidget(self.result_tree, 1)
		
		# 按钮
		btn_layout = QHBoxLayout()
		self.calc_btn = QPushButton("🔐 开始计算")
		self.calc_btn.clicked.connect(self._start_calculate)
		btn_layout.addWidget(self.calc_btn)
		
		self.stop_btn = QPushButton("⏹ 停止")
		self.stop_btn.setEnabled(False)
		self.stop_btn.clicked.connect(self._stop_calculate)
		btn_layout.addWidget(self.stop_btn)
		
		copy_btn = QPushButton("📋 复制 Hash")
		copy_btn.clicked.connect(self._copy_hash)
		btn_layout.addWidget(copy_btn)
		
		export_btn = QPushButton("📤 导出列表")
		export_btn.clicked.connect(self._export_list)
		btn_layout.addWidget(export_btn)
		
		btn_layout.addStretch()
		close_btn = QPushButton("关闭")
		close_btn.clicked.connect(self.accept)
		btn_layout.addWidget(close_btn)
		
		layout.addLayout(btn_layout)
		
		# 自动开始计算
		if self.files:
			self._start_calculate()
	
	def _start_calculate(self):
		if not self.files:
			QMessageBox.warning(self, "警告", "没有文件需要计算")
			return
		
		self.result_tree.clear()
		self.hash_results.clear()
		self.progress.setVisible(True)
		self.progress.setRange(0, len(self.files))
		self.progress.setValue(0)
		self.calc_btn.setEnabled(False)
		self.stop_btn.setEnabled(True)
		
		self.calculator = FileHashCalculator(self.files)
		self.calculator.progress.connect(self._on_progress)
		self.calculator.hash_ready.connect(self._on_hash_ready)
		self.calculator.finished_signal.connect(self._on_finished)
		self.calculator.start()
	
	def _stop_calculate(self):
		if self.calculator:
			self.calculator.stop()
	
	def _on_progress(self, current, total, message):
		self.progress.setValue(current)
		self.status_label.setText(message)
	
	def _on_hash_ready(self, filepath, md5_hash, sha256_hash):
		self.hash_results[filepath] = (md5_hash, sha256_hash)
		
		item = QTreeWidgetItem(self.result_tree)
		item.setText(0, os.path.basename(filepath))
		try:
			size = os.path.getsize(filepath)
			item.setText(1, format_size(size))
		except Exception:
			item.setText(1, "N/A")
		item.setText(2, md5_hash)
		item.setText(3, sha256_hash)
		item.setData(0, Qt.UserRole, filepath)
	
	def _on_finished(self):
		self.progress.setVisible(False)
		self.calc_btn.setEnabled(True)
		self.stop_btn.setEnabled(False)
		self.status_label.setText(f"✅ 计算完成，共 {len(self.hash_results)} 个文件")
	
	def _copy_hash(self):
		selected_items = self.result_tree.selectedItems()
		if not selected_items:
			# 复制所有
			text_lines = []
			for i in range(self.result_tree.topLevelItemCount()):
				item = self.result_tree.topLevelItem(i)
				filename = item.text(0)
				md5 = item.text(2)
				sha256 = item.text(3)
				text_lines.append(f"{filename}\nMD5: {md5}\nSHA256: {sha256}\n")
			text = "\n".join(text_lines)
		else:
			# 复制选中
			text_lines = []
			for item in selected_items:
				filename = item.text(0)
				md5 = item.text(2)
				sha256 = item.text(3)
				text_lines.append(f"{filename}\nMD5: {md5}\nSHA256: {sha256}\n")
			text = "\n".join(text_lines)
		
		QApplication.clipboard().setText(text)
		self.status_label.setText("✅ Hash 值已复制到剪贴板")
	
	def _export_list(self):
		if not self.hash_results:
			QMessageBox.information(self, "提示", "没有可导出的结果")
			return
		
		filepath, _ = QFileDialog.getSaveFileName(
			self, "导出 Hash 列表", "file_hashes.txt", "文本文件 (*.txt)"
		)
		
		if filepath:
			try:
				with open(filepath, 'w', encoding='utf-8') as f:
					f.write("文件 Hash 列表\n")
					f.write("=" * 80 + "\n\n")
					
					for fp, (md5, sha256) in self.hash_results.items():
						f.write(f"文件: {fp}\n")
						f.write(f"MD5:    {md5}\n")
						f.write(f"SHA256: {sha256}\n\n")
				
				QMessageBox.information(self, "成功", f"已导出到: {filepath}")
			except Exception as e:
				QMessageBox.warning(self, "错误", f"导出失败: {e}")
