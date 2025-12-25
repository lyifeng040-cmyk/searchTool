"""
浏览器书签管理器 - 搜索 Chrome/Edge/Firefox 书签
"""
import json
import os
import sqlite3
from pathlib import Path
from typing import List, Dict


class BookmarkManager:
	"""浏览器书签管理器"""
	
	@staticmethod
	def get_chrome_bookmarks():
		"""获取 Chrome 书签"""
		bookmarks = []
		
		# Chrome 书签路径
		chrome_paths = [
			Path.home() / "AppData/Local/Google/Chrome/User Data/Default/Bookmarks",
			Path.home() / "AppData/Local/Google/Chrome/User Data/Profile 1/Bookmarks",
		]
		
		for bookmark_file in chrome_paths:
			if bookmark_file.exists():
				try:
					with open(bookmark_file, 'r', encoding='utf-8') as f:
						data = json.load(f)
					bookmarks.extend(BookmarkManager._parse_chrome_bookmarks(data, "Chrome"))
				except Exception:
					pass
		
		return bookmarks
	
	@staticmethod
	def get_edge_bookmarks():
		"""获取 Edge 书签"""
		bookmarks = []
		
		# Edge 书签路径
		edge_paths = [
			Path.home() / "AppData/Local/Microsoft/Edge/User Data/Default/Bookmarks",
			Path.home() / "AppData/Local/Microsoft/Edge/User Data/Profile 1/Bookmarks",
		]
		
		for bookmark_file in edge_paths:
			if bookmark_file.exists():
				try:
					with open(bookmark_file, 'r', encoding='utf-8') as f:
						data = json.load(f)
					bookmarks.extend(BookmarkManager._parse_chrome_bookmarks(data, "Edge"))
				except Exception:
					pass
		
		return bookmarks
	
	@staticmethod
	def get_firefox_bookmarks():
		"""获取 Firefox 书签"""
		bookmarks = []
		
		# Firefox 配置文件目录
		firefox_profile_dir = Path.home() / "AppData/Roaming/Mozilla/Firefox/Profiles"
		
		if not firefox_profile_dir.exists():
			return bookmarks
		
		# 查找所有配置文件
		for profile_dir in firefox_profile_dir.iterdir():
			if profile_dir.is_dir():
				places_db = profile_dir / "places.sqlite"
				if places_db.exists():
					try:
						# 复制数据库文件（避免锁定）
						import tempfile
						import shutil
						with tempfile.NamedTemporaryFile(delete=False, suffix='.sqlite') as tmp:
							tmp_path = tmp.name
						shutil.copy2(places_db, tmp_path)
						
						# 读取书签
						conn = sqlite3.connect(tmp_path)
						cursor = conn.cursor()
						cursor.execute("""
							SELECT moz_bookmarks.title, moz_places.url
							FROM moz_bookmarks
							JOIN moz_places ON moz_bookmarks.fk = moz_places.id
							WHERE moz_bookmarks.type = 1 AND moz_bookmarks.title IS NOT NULL
						""")
						
						for title, url in cursor.fetchall():
							if url and title:
								bookmarks.append({
									'title': title,
									'url': url,
									'browser': 'Firefox',
									'folder': ''
								})
						
						conn.close()
						os.unlink(tmp_path)
						
					except Exception:
						pass
		
		return bookmarks
	
	@staticmethod
	def _parse_chrome_bookmarks(data, browser_name, folder_name=""):
		"""解析 Chrome/Edge 书签 JSON"""
		bookmarks = []
		
		def traverse(node, folder):
			if node.get('type') == 'url':
				bookmarks.append({
					'title': node.get('name', ''),
					'url': node.get('url', ''),
					'browser': browser_name,
					'folder': folder
				})
			elif node.get('type') == 'folder':
				new_folder = f"{folder}/{node.get('name', '')}" if folder else node.get('name', '')
				for child in node.get('children', []):
					traverse(child, new_folder)
		
		# 遍历书签栏和其他书签
		roots = data.get('roots', {})
		for root_key in ['bookmark_bar', 'other', 'synced']:
			if root_key in roots:
				traverse(roots[root_key], "")
		
		return bookmarks
	
	@classmethod
	def get_all_bookmarks(cls):
		"""获取所有浏览器书签"""
		all_bookmarks = []
		
		# Chrome 书签
		all_bookmarks.extend(cls.get_chrome_bookmarks())
		
		# Edge 书签
		all_bookmarks.extend(cls.get_edge_bookmarks())
		
		# Firefox 书签
		all_bookmarks.extend(cls.get_firefox_bookmarks())
		
		return all_bookmarks
	
	@classmethod
	def search_bookmarks(cls, keyword):
		"""搜索书签"""
		all_bookmarks = cls.get_all_bookmarks()
		
		if not keyword:
			return all_bookmarks
		
		keyword_lower = keyword.lower()
		results = []
		
		for bookmark in all_bookmarks:
			title_lower = bookmark['title'].lower()
			url_lower = bookmark['url'].lower()
			
			if keyword_lower in title_lower or keyword_lower in url_lower:
				results.append(bookmark)
		
		return results
