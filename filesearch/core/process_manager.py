"""
进程管理器 - 搜索和管理系统进程
"""
import psutil
from typing import List, Dict


class ProcessManager:
	"""系统进程管理器"""
	
	@staticmethod
	def get_all_processes():
		"""获取所有进程"""
		processes = []
		
		for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'create_time']):
			try:
				info = proc.info
				processes.append({
					'pid': info['pid'],
					'name': info['name'],
					'cpu_percent': info['cpu_percent'] or 0,
					'memory_mb': info['memory_info'].rss / (1024 * 1024) if info['memory_info'] else 0,
					'create_time': info['create_time'],
					'process': proc
				})
			except (psutil.NoSuchProcess, psutil.AccessDenied):
				pass
		
		return processes
	
	@staticmethod
	def search_processes(keyword):
		"""搜索进程"""
		all_processes = ProcessManager.get_all_processes()
		
		if not keyword:
			return all_processes
		
		keyword_lower = keyword.lower()
		results = []
		
		for proc_info in all_processes:
			if keyword_lower in proc_info['name'].lower():
				results.append(proc_info)
		
		return results
	
	@staticmethod
	def kill_process(pid):
		"""结束进程"""
		try:
			proc = psutil.Process(pid)
			proc.terminate()
			proc.wait(timeout=3)
			return True, "进程已终止"
		except psutil.NoSuchProcess:
			return False, "进程不存在"
		except psutil.AccessDenied:
			return False, "权限不足，请以管理员身份运行"
		except Exception as e:
			return False, f"结束进程失败: {e}"
	
	@staticmethod
	def get_process_details(pid):
		"""获取进程详细信息"""
		try:
			proc = psutil.Process(pid)
			return {
				'pid': proc.pid,
				'name': proc.name(),
				'exe': proc.exe() if hasattr(proc, 'exe') else '',
				'cmdline': ' '.join(proc.cmdline()) if hasattr(proc, 'cmdline') else '',
				'cpu_percent': proc.cpu_percent(interval=0.1),
				'memory_mb': proc.memory_info().rss / (1024 * 1024),
				'num_threads': proc.num_threads(),
				'status': proc.status(),
			}
		except Exception:
			return None
