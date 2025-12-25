"""
最近文件管理器 - 访问 Windows Recent 文件夹
"""
import os
from pathlib import Path
import time


class RecentFilesManager:
	"""最近文件管理器"""
	
	@staticmethod
	def get_recent_folder():
		"""获取 Windows Recent 文件夹路径"""
		return Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Recent'
	
	@staticmethod
	def get_recent_files(days=7):
		"""获取最近的文件（默认7天内）"""
		recent_folder = RecentFilesManager.get_recent_folder()
		
		if not recent_folder.exists():
			return []
		
		recent_files = []
		cutoff_time = time.time() - (days * 24 * 3600)
		
		for lnk_file in recent_folder.glob('*.lnk'):
			try:
				# 获取快捷方式的修改时间
				mtime = lnk_file.stat().st_mtime
				
				if mtime >= cutoff_time:
					# 解析快捷方式目标
					target = RecentFilesManager._resolve_shortcut(lnk_file)
					
					if target and os.path.exists(target):
						recent_files.append({
							'path': target,
							'name': os.path.basename(target),
							'access_time': mtime,
							'shortcut': str(lnk_file)
						})
			except Exception:
				pass
		
		# 按访问时间排序（最新的在前）
		recent_files.sort(key=lambda x: x['access_time'], reverse=True)
		
		return recent_files
	
	@staticmethod
	def _resolve_shortcut(lnk_path):
		"""解析 Windows 快捷方式目标路径"""
		try:
			import win32com.client
			shell = win32com.client.Dispatch("WScript.Shell")
			shortcut = shell.CreateShortcut(str(lnk_path))
			return shortcut.TargetPath
		except Exception:
			# 如果没有 pywin32，尝试简单的文件名匹配
			# 这不是真正的快捷方式解析，只是一个回退方案
			return None
	
	@staticmethod
	def search_recent_files(keyword, days=7):
		"""搜索最近文件"""
		all_files = RecentFilesManager.get_recent_files(days)
		
		if not keyword:
			return all_files
		
		keyword_lower = keyword.lower()
		results = []
		
		for file_info in all_files:
			if keyword_lower in file_info['name'].lower() or keyword_lower in file_info['path'].lower():
				results.append(file_info)
		
		return results
