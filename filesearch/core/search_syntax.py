"""
高级搜索语法解析器
支持 ext:、size:、dm:、path:、name:、dir: 等语法
"""
import re
import datetime
import os


class SearchSyntaxParser:
	"""解析 Everything 风格的搜索语法"""
	
	def __init__(self):
		self.parsed_text = ""
		self.filters = {
			"ext": [],
			"size_min": 0,
			"size_max": 0,
			"date_after": None,
			"path": "",
			"name_pattern": "",
			"dir_pattern": "",
			"content_only": False,
		}
	
	def parse(self, query):
		"""
		解析搜索查询，返回 (纯文本关键词, 过滤条件字典)
		
		示例:
		  "report ext:pdf size:>1mb dm:today path:D:\\Projects"
		  → ("report", {"ext": ["pdf"], "size_min": 1048576, ...})
		"""
		if not query:
			return "", {}
		
		# 重置
		self.parsed_text = query
		self.filters = {
			"ext": [],
			"size_min": 0,
			"size_max": 0,
			"date_after": None,
			"path": "",
			"name_pattern": "",
			"dir_pattern": "",
			"content_only": False,
		}
		
		# 提取各种语法
		self.parsed_text = self._extract_ext(self.parsed_text)
		self.parsed_text = self._extract_size(self.parsed_text)
		self.parsed_text = self._extract_date(self.parsed_text)
		self.parsed_text = self._extract_path(self.parsed_text)
		self.parsed_text = self._extract_name(self.parsed_text)
		self.parsed_text = self._extract_dir(self.parsed_text)
		self.parsed_text = self._extract_content(self.parsed_text)
		
		# 清理多余空格
		self.parsed_text = " ".join(self.parsed_text.split())
		
		return self.parsed_text, self.filters
	
	def _extract_ext(self, text):
		"""提取 ext:pdf 或 ext:jpg,png"""
		pattern = r'ext:([a-zA-Z0-9,]+)'
		matches = re.findall(pattern, text, re.IGNORECASE)
		for match in matches:
			exts = [e.strip().lower() for e in match.split(',') if e.strip()]
			self.filters["ext"].extend(exts)
		return re.sub(pattern, '', text, flags=re.IGNORECASE)
	
	def _extract_size(self, text):
		"""提取 size:>100mb、size:<1kb、size:10mb-50mb"""
		# size:>100mb
		pattern1 = r'size:>(\d+(?:\.\d+)?)(kb|mb|gb)'
		match = re.search(pattern1, text, re.IGNORECASE)
		if match:
			num = float(match.group(1))
			unit = match.group(2).lower()
			self.filters["size_min"] = self._parse_size(num, unit)
			text = re.sub(pattern1, '', text, flags=re.IGNORECASE)
		
		# size:<1kb
		pattern2 = r'size:<(\d+(?:\.\d+)?)(kb|mb|gb)'
		match = re.search(pattern2, text, re.IGNORECASE)
		if match:
			num = float(match.group(1))
			unit = match.group(2).lower()
			self.filters["size_max"] = self._parse_size(num, unit)
			text = re.sub(pattern2, '', text, flags=re.IGNORECASE)
		
		# size:10mb-50mb
		pattern3 = r'size:(\d+(?:\.\d+)?)(kb|mb|gb)-(\d+(?:\.\d+)?)(kb|mb|gb)'
		match = re.search(pattern3, text, re.IGNORECASE)
		if match:
			num1 = float(match.group(1))
			unit1 = match.group(2).lower()
			num2 = float(match.group(3))
			unit2 = match.group(4).lower()
			self.filters["size_min"] = self._parse_size(num1, unit1)
			self.filters["size_max"] = self._parse_size(num2, unit2)
			text = re.sub(pattern3, '', text, flags=re.IGNORECASE)
		
		return text
	
	def _parse_size(self, num, unit):
		"""将大小转换为字节数"""
		multipliers = {'kb': 1024, 'mb': 1024*1024, 'gb': 1024*1024*1024}
		return int(num * multipliers.get(unit, 1))
	
	def _extract_date(self, text):
		"""提取 dm:today、dm:week、dm:7d、dm:2024-12-01"""
		pattern = r'dm:(\S+)'
		match = re.search(pattern, text, re.IGNORECASE)
		if match:
			date_str = match.group(1).lower()
			now = datetime.datetime.now()
			
			if date_str == 'today':
				self.filters["date_after"] = now.replace(hour=0, minute=0, second=0, microsecond=0)
			elif date_str == 'yesterday':
				self.filters["date_after"] = (now - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
			elif date_str == 'week':
				self.filters["date_after"] = now - datetime.timedelta(days=7)
			elif date_str == 'month':
				self.filters["date_after"] = now - datetime.timedelta(days=30)
			elif date_str == 'year':
				self.filters["date_after"] = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
			else:
				# 尝试解析相对时间：7d, 12h, 30m
				rel_match = re.match(r'^(\d+)([dhm])$', date_str)
				if rel_match:
					num = int(rel_match.group(1))
					unit = rel_match.group(2)
					if unit == 'd':
						self.filters["date_after"] = now - datetime.timedelta(days=num)
					elif unit == 'h':
						self.filters["date_after"] = now - datetime.timedelta(hours=num)
					elif unit == 'm':
						self.filters["date_after"] = now - datetime.timedelta(minutes=num)
				else:
					# 尝试解析日期格式 YYYY-MM-DD
					try:
						self.filters["date_after"] = datetime.datetime.strptime(date_str, '%Y-%m-%d')
					except ValueError:
						pass
			
			text = re.sub(pattern, '', text, flags=re.IGNORECASE)
		
		return text
	
	def _extract_path(self, text):
		"""提取 path:D:\\Projects 或 path:"C:\\Program Files" """
		# 带引号的路径
		pattern1 = r'path:"([^"]+)"'
		match = re.search(pattern1, text, re.IGNORECASE)
		if match:
			self.filters["path"] = os.path.normpath(match.group(1))
			return re.sub(pattern1, '', text, flags=re.IGNORECASE)
		
		# 不带引号的路径
		pattern2 = r'path:(\S+)'
		match = re.search(pattern2, text, re.IGNORECASE)
		if match:
			self.filters["path"] = os.path.normpath(match.group(1))
			text = re.sub(pattern2, '', text, flags=re.IGNORECASE)
		
		return text
	
	def _extract_name(self, text):
		"""提取 name:readme 或 name:*.log"""
		pattern = r'name:(\S+)'
		match = re.search(pattern, text, re.IGNORECASE)
		if match:
			self.filters["name_pattern"] = match.group(1)
			text = re.sub(pattern, '', text, flags=re.IGNORECASE)
		return text
	
	def _extract_dir(self, text):
		"""提取 dir:projects"""
		pattern = r'dir:(\S+)'
		match = re.search(pattern, text, re.IGNORECASE)
		if match:
			self.filters["dir_pattern"] = match.group(1)
			text = re.sub(pattern, '', text, flags=re.IGNORECASE)
		return text
	
	def _extract_content(self, text):
		"""提取 content: 前缀"""
		if text.strip().lower().startswith('content:'):
			self.filters["content_only"] = True
			return text[8:].strip()
		return text
	
	def apply_filters(self, results):
		"""
		对搜索结果应用过滤条件
		results: [{"fullpath": "...", "size": ..., "mtime": ..., ...}, ...]
		返回: 过滤后的结果列表
		"""
		if not results:
			return results
		
		filtered = []
		for item in results:
			if not self._match_item(item):
				continue
			filtered.append(item)
		
		return filtered
	
	def _match_item(self, item):
		"""检查单个文件是否匹配所有过滤条件"""
		fullpath = item.get("fullpath", "")
		filename = os.path.basename(fullpath)
		dirname = os.path.dirname(fullpath)
		size = item.get("size", 0)
		mtime = item.get("mtime", 0)
		
		# 扩展名过滤
		if self.filters["ext"]:
			ext = os.path.splitext(filename)[1].lstrip('.').lower()
			if ext not in self.filters["ext"]:
				return False
		
		# 大小过滤
		if self.filters["size_min"] > 0 and size < self.filters["size_min"]:
			return False
		if self.filters["size_max"] > 0 and size > self.filters["size_max"]:
			return False
		
		# 日期过滤
		if self.filters["date_after"]:
			if mtime == 0:
				return False
			file_dt = datetime.datetime.fromtimestamp(mtime)
			if file_dt < self.filters["date_after"]:
				return False
		
		# 路径过滤
		if self.filters["path"]:
			path_filter = self.filters["path"].lower()
			if path_filter not in fullpath.lower():
				return False
		
		# 文件名模式过滤
		if self.filters["name_pattern"]:
			pattern = self.filters["name_pattern"].lower()
			# 支持通配符
			if '*' in pattern or '?' in pattern:
				import fnmatch
				if not fnmatch.fnmatch(filename.lower(), pattern):
					return False
			else:
				if pattern not in filename.lower():
					return False
		
		# 目录名过滤
		if self.filters["dir_pattern"]:
			pattern = self.filters["dir_pattern"].lower()
			if pattern not in dirname.lower():
				return False
		
		return True
