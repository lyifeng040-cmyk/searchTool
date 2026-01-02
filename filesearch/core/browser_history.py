"""
浏览器历史记录管理器
"""
import sqlite3
import os
from pathlib import Path
import shutil
import tempfile


class BrowserHistoryManager:
	"""浏览器历史记录管理器"""
	
	@staticmethod
	def get_chrome_history():
		"""获取 Chrome 历史记录"""
		history = []
		
		chrome_paths = [
			Path.home() / "AppData/Local/Google/Chrome/User Data/Default/History",
			Path.home() / "AppData/Local/Google/Chrome/User Data/Profile 1/History",
		]
		
		for history_db in chrome_paths:
			if history_db.exists():
				history.extend(BrowserHistoryManager._read_chromium_history(history_db, "Chrome"))
		
		return history
	
	@staticmethod
	def get_edge_history():
		"""获取 Edge 历史记录"""
		history = []
		
		edge_paths = [
			Path.home() / "AppData/Local/Microsoft/Edge/User Data/Default/History",
			Path.home() / "AppData/Local/Microsoft/Edge/User Data/Profile 1/History",
		]
		
		for history_db in edge_paths:
			if history_db.exists():
				history.extend(BrowserHistoryManager._read_chromium_history(history_db, "Edge"))
		
		return history
	
	@staticmethod
	def get_firefox_history():
		"""获取 Firefox 历史记录"""
		history = []
		
		firefox_profile_dir = Path.home() / "AppData/Roaming/Mozilla/Firefox/Profiles"
		
		if not firefox_profile_dir.exists():
			return history
		
		for profile_dir in firefox_profile_dir.iterdir():
			if profile_dir.is_dir():
				places_db = profile_dir / "places.sqlite"
				if places_db.exists():
					history.extend(BrowserHistoryManager._read_firefox_history(places_db))
		
		return history
	
	@staticmethod
	def _read_chromium_history(db_path, browser_name):
		"""读取 Chromium 系浏览器历史（Chrome/Edge）"""
		history = []
		
		try:
			# 复制数据库文件（避免锁定）
			with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as tmp:
				tmp_path = tmp.name
			shutil.copy2(db_path, tmp_path)
			
			conn = sqlite3.connect(tmp_path)
			cursor = conn.cursor()
			
			# 查询最近 1000 条历史记录
			cursor.execute("""
				SELECT url, title, visit_count, last_visit_time
				FROM urls
				ORDER BY last_visit_time DESC
				LIMIT 1000
			""")
			
			for url, title, visit_count, last_visit_time in cursor.fetchall():
				if url and title:
					# Chrome 时间戳转换（微秒，从 1601-01-01）
					try:
						timestamp = (last_visit_time / 1000000) - 11644473600
					except Exception:
						timestamp = 0
					
					history.append({
						'title': title,
						'url': url,
						'visit_count': visit_count,
						'timestamp': timestamp,
						'browser': browser_name
					})
			
			conn.close()
			os.unlink(tmp_path)
			
		except Exception:
			pass
		
		return history
	
	@staticmethod
	def _read_firefox_history(db_path):
		"""读取 Firefox 历史记录"""
		history = []
		
		try:
			with tempfile.NamedTemporaryFile(delete=False, suffix='.sqlite') as tmp:
				tmp_path = tmp.name
			shutil.copy2(db_path, tmp_path)
			
			conn = sqlite3.connect(tmp_path)
			cursor = conn.cursor()
			
			cursor.execute("""
				SELECT url, title, visit_count, last_visit_date
				FROM moz_places
				WHERE title IS NOT NULL
				ORDER BY last_visit_date DESC
				LIMIT 1000
			""")
			
			for url, title, visit_count, last_visit_date in cursor.fetchall():
				if url and title:
					# Firefox 时间戳（微秒）
					try:
						timestamp = last_visit_date / 1000000 if last_visit_date else 0
					except Exception:
						timestamp = 0
					
					history.append({
						'title': title,
						'url': url,
						'visit_count': visit_count,
						'timestamp': timestamp,
						'browser': 'Firefox'
					})
			
			conn.close()
			os.unlink(tmp_path)
			
		except Exception:
			pass
		
		return history
	
	@classmethod
	def get_all_history(cls):
		"""获取所有浏览器历史"""
		all_history = []
		
		all_history.extend(cls.get_chrome_history())
		all_history.extend(cls.get_edge_history())
		all_history.extend(cls.get_firefox_history())
		
		# 按时间排序
		all_history.sort(key=lambda x: x['timestamp'], reverse=True)
		
		return all_history
	
	@classmethod
	def search_history(cls, keyword, limit=100):
		"""搜索浏览器历史"""
		all_history = cls.get_all_history()
		
		if not keyword:
			return all_history[:limit]
		
		keyword_lower = keyword.lower()
		results = []
		
		for item in all_history:
			title_lower = item['title'].lower()
			url_lower = item['url'].lower()
			
			if keyword_lower in title_lower or keyword_lower in url_lower:
				results.append(item)
				if len(results) >= limit:
					break
		
		return results
