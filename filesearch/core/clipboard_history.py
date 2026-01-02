"""
剪贴板历史管理器
"""
import json
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal, QTimer


class ClipboardHistory(QObject):
	"""剪贴板历史记录管理器"""
	
	clipboard_changed = Signal(str)  # 剪贴板内容变化信号
	
	def __init__(self, max_items=100, save_file=None):
		super().__init__()
		self.max_items = max_items
		self.history = []  # [(timestamp, text), ...]
		self.last_text = ""
		
		# 保存文件路径
		if save_file:
			self.save_file = Path(save_file)
		else:
			from ..constants import LOG_DIR
			self.save_file = LOG_DIR / "clipboard_history.json"
		
		# 加载历史记录
		self.load_history()
		
		# 设置定时器监控剪贴板
		self.timer = QTimer()
		self.timer.timeout.connect(self._check_clipboard)
		self.timer.start(1000)  # 每秒检查一次
		
		# 初始化当前剪贴板内容
		clipboard = QApplication.clipboard()
		self.last_text = clipboard.text()
	
	def _check_clipboard(self):
		"""检查剪贴板是否有新内容"""
		try:
			clipboard = QApplication.clipboard()
			current_text = clipboard.text()
			
			if current_text and current_text != self.last_text:
				self.last_text = current_text
				self.add_item(current_text)
				self.clipboard_changed.emit(current_text)
		except Exception:
			pass
	
	def add_item(self, text):
		"""添加新条目到历史记录"""
		if not text or not text.strip():
			return
		
		# 移除重复项（如果存在）
		self.history = [(ts, t) for ts, t in self.history if t != text]
		
		# 添加新项到开头
		self.history.insert(0, (time.time(), text))
		
		# 限制历史记录数量
		if len(self.history) > self.max_items:
			self.history = self.history[:self.max_items]
		
		# 保存到文件
		self.save_history()
	
	def get_history(self, limit=None):
		"""获取历史记录"""
		if limit:
			return self.history[:limit]
		return self.history
	
	def search_history(self, keyword):
		"""搜索历史记录"""
		keyword_lower = keyword.lower()
		results = []
		
		for timestamp, text in self.history:
			if keyword_lower in text.lower():
				results.append((timestamp, text))
		
		return results
	
	def clear_history(self):
		"""清空历史记录"""
		self.history = []
		self.save_history()
	
	def remove_item(self, text):
		"""删除指定条目"""
		self.history = [(ts, t) for ts, t in self.history if t != text]
		self.save_history()
	
	def save_history(self):
		"""保存历史记录到文件"""
		try:
			self.save_file.parent.mkdir(parents=True, exist_ok=True)
			with open(self.save_file, 'w', encoding='utf-8') as f:
				json.dump(self.history, f, ensure_ascii=False, indent=2)
		except Exception:
			pass
	
	def load_history(self):
		"""从文件加载历史记录"""
		try:
			if self.save_file.exists():
				with open(self.save_file, 'r', encoding='utf-8') as f:
					self.history = json.load(f)
		except Exception:
			self.history = []
	
	def stop(self):
		"""停止监控剪贴板"""
		if self.timer:
			self.timer.stop()
