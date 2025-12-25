"""
重复文件查找和文件Hash计算工具
"""
import hashlib
import os
from collections import defaultdict
from PySide6.QtCore import QThread, Signal


class FileHashCalculator(QThread):
	"""文件 Hash 计算线程"""
	
	progress = Signal(int, int, str)  # (current, total, message)
	hash_ready = Signal(str, str, str)  # (filepath, md5, sha256)
	finished_signal = Signal()
	
	def __init__(self, files):
		super().__init__()
		self.files = files  # [filepath1, filepath2, ...]
		self.stopped = False
	
	def run(self):
		total = len(self.files)
		for idx, filepath in enumerate(self.files):
			if self.stopped:
				break
			
			try:
				md5_hash, sha256_hash = self._calculate_hash(filepath)
				self.hash_ready.emit(filepath, md5_hash, sha256_hash)
				self.progress.emit(idx + 1, total, f"已计算: {os.path.basename(filepath)}")
			except Exception as e:
				self.progress.emit(idx + 1, total, f"失败: {e}")
		
		self.finished_signal.emit()
	
	def _calculate_hash(self, filepath):
		"""计算文件的 MD5 和 SHA256"""
		md5 = hashlib.md5()
		sha256 = hashlib.sha256()
		
		with open(filepath, 'rb') as f:
			while chunk := f.read(8192):
				md5.update(chunk)
				sha256.update(chunk)
		
		return md5.hexdigest(), sha256.hexdigest()
	
	def stop(self):
		self.stopped = True


class DuplicateFileFinder(QThread):
	"""重复文件查找线程"""
	
	progress = Signal(int, int, str)  # (current, total, message)
	duplicates_ready = Signal(dict)  # {hash: [filepath1, filepath2, ...]}
	finished_signal = Signal()
	
	def __init__(self, search_paths, min_size=0):
		super().__init__()
		self.search_paths = search_paths  # [path1, path2, ...]
		self.min_size = min_size  # 最小文件大小（字节）
		self.stopped = False
	
	def run(self):
		# 第一步：按大小分组
		size_groups = defaultdict(list)
		file_count = 0
		
		for search_path in self.search_paths:
			if self.stopped:
				break
			
			for root, dirs, files in os.walk(search_path):
				if self.stopped:
					break
				
				for filename in files:
					if self.stopped:
						break
					
					filepath = os.path.join(root, filename)
					try:
						size = os.path.getsize(filepath)
						if size >= self.min_size:
							size_groups[size].append(filepath)
							file_count += 1
							if file_count % 100 == 0:
								self.progress.emit(file_count, 0, f"已扫描 {file_count} 个文件")
					except Exception:
						continue
		
		if self.stopped:
			self.finished_signal.emit()
			return
		
		# 第二步：对相同大小的文件计算 Hash
		duplicates = defaultdict(list)
		potential_files = []
		
		for size, files in size_groups.items():
			if len(files) > 1:  # 只处理有多个文件的大小组
				potential_files.extend(files)
		
		total = len(potential_files)
		for idx, filepath in enumerate(potential_files):
			if self.stopped:
				break
			
			try:
				file_hash = self._calculate_quick_hash(filepath)
				duplicates[file_hash].append(filepath)
				self.progress.emit(idx + 1, total, f"正在比对: {os.path.basename(filepath)}")
			except Exception:
				continue
		
		# 只保留真正重复的（hash相同且有多个文件）
		real_duplicates = {h: files for h, files in duplicates.items() if len(files) > 1}
		
		self.duplicates_ready.emit(real_duplicates)
		self.finished_signal.emit()
	
	def _calculate_quick_hash(self, filepath):
		"""快速计算文件 Hash（MD5）"""
		md5 = hashlib.md5()
		with open(filepath, 'rb') as f:
			while chunk := f.read(8192):
				md5.update(chunk)
		return md5.hexdigest()
	
	def stop(self):
		self.stopped = True
