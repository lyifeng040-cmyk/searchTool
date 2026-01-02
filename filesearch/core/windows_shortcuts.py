"""
Windows 系统快捷方式 - 控制面板和常用设置
"""
import subprocess
import os


class WindowsShortcuts:
	"""Windows 系统快捷方式"""
	
	# 控制面板项目
	CONTROL_PANEL_ITEMS = {
		# 网络和Internet
		'network': ('control.exe', '/name Microsoft.NetworkAndSharingCenter', '网络和共享中心', '🌐'),
		'adapter': ('ncpa.cpl', '', '网络适配器', '🔌'),
		'firewall': ('firewall.cpl', '', 'Windows 防火墙', '🛡️'),
		
		# 系统和安全
		'system': ('sysdm.cpl', '', '系统属性', '💻'),
		'device': ('devmgmt.msc', '', '设备管理器', '🔧'),
		'disk': ('diskmgmt.msc', '', '磁盘管理', '💾'),
		'services': ('services.msc', '', '服务', '⚙️'),
		'taskmgr': ('taskmgr', '', '任务管理器', '📊'),
		'regedit': ('regedit', '', '注册表编辑器', '📝'),
		'msconfig': ('msconfig', '', '系统配置', '⚡'),
		
		# 程序
		'programs': ('appwiz.cpl', '', '程序和功能', '📦'),
		'features': ('optionalfeatures', '', 'Windows 功能', '🎯'),
		
		# 用户账户
		'users': ('netplwiz', '', '用户账户', '👤'),
		
		# 外观和个性化
		'display': ('desk.cpl', '', '显示设置', '🖥️'),
		'personalization': ('control.exe', '/name Microsoft.Personalization', '个性化', '🎨'),
		'fonts': ('control.exe', 'fonts', '字体', '🔤'),
		
		# 硬件和声音
		'sound': ('mmsys.cpl', '', '声音', '🔊'),
		'power': ('powercfg.cpl', '', '电源选项', '🔋'),
		'mouse': ('main.cpl', '', '鼠标属性', '🖱️'),
		'keyboard': ('control.exe', 'keyboard', '键盘', '⌨️'),
		
		# 时钟和区域
		'datetime': ('timedate.cpl', '', '日期和时间', '🕐'),
		'region': ('intl.cpl', '', '区域', '🌍'),
		
		# 其他
		'cleanup': ('cleanmgr', '', '磁盘清理', '🧹'),
		'defrag': ('dfrgui', '', '磁盘碎片整理', '📊'),
		'env': ('rundll32', 'sysdm.cpl,EditEnvironmentVariables', '环境变量', '🔧'),
		'startup': ('shell:startup', '', '启动文件夹', '🚀'),
	}
	
	# Windows 设置（Settings）
	SETTINGS_ITEMS = {
		'settings': ('ms-settings:', '设置', '⚙️'),
		'wifi': ('ms-settings:network-wifi', 'Wi-Fi 设置', '📡'),
		'bluetooth': ('ms-settings:bluetooth', '蓝牙设置', '📶'),
		'vpn': ('ms-settings:network-vpn', 'VPN 设置', '🔒'),
		'proxy': ('ms-settings:network-proxy', '代理设置', '🌐'),
		'apps': ('ms-settings:appsfeatures', '应用和功能', '📱'),
		'defaultapps': ('ms-settings:defaultapps', '默认应用', '🎯'),
		'notifications': ('ms-settings:notifications', '通知', '🔔'),
		'privacy': ('ms-settings:privacy', '隐私', '🔐'),
		'update': ('ms-settings:windowsupdate', 'Windows 更新', '🔄'),
		'recovery': ('ms-settings:recovery', '恢复', '🔄'),
		'activation': ('ms-settings:activation', '激活', '🔑'),
	}
	
	@classmethod
	def search_shortcuts(cls, keyword):
		"""搜索快捷方式"""
		results = []
		keyword_lower = keyword.lower()
		
		# 搜索控制面板项目
		for key, (cmd, args, name, icon) in cls.CONTROL_PANEL_ITEMS.items():
			if keyword_lower in key.lower() or keyword_lower in name.lower():
				results.append({
					'key': key,
					'name': name,
					'icon': icon,
					'command': cmd,
					'args': args,
					'type': 'control'
				})
		
		# 搜索设置项目
		for key, (uri, name, icon) in cls.SETTINGS_ITEMS.items():
			if keyword_lower in key.lower() or keyword_lower in name.lower():
				results.append({
					'key': key,
					'name': name,
					'icon': icon,
					'command': uri,
					'args': '',
					'type': 'settings'
				})
		
		return results
	
	@classmethod
	def open_shortcut(cls, key):
		"""打开快捷方式"""
		# 检查控制面板项目
		if key in cls.CONTROL_PANEL_ITEMS:
			cmd, args, name, icon = cls.CONTROL_PANEL_ITEMS[key]
			try:
				if args:
					subprocess.Popen([cmd, args])
				else:
					subprocess.Popen(cmd)
				return True, f"已打开 {name}"
			except Exception as e:
				return False, f"打开失败: {e}"
		
		# 检查设置项目
		if key in cls.SETTINGS_ITEMS:
			uri, name, icon = cls.SETTINGS_ITEMS[key]
			try:
				subprocess.Popen(['start', uri], shell=True)
				return True, f"已打开 {name}"
			except Exception as e:
				return False, f"打开失败: {e}"
		
		return False, "未找到快捷方式"
	
	@classmethod
	def get_all_shortcuts(cls):
		"""获取所有快捷方式"""
		results = []
		
		for key, (cmd, args, name, icon) in cls.CONTROL_PANEL_ITEMS.items():
			results.append({
				'key': key,
				'name': name,
				'icon': icon,
				'command': cmd,
				'args': args,
				'type': 'control'
			})
		
		for key, (uri, name, icon) in cls.SETTINGS_ITEMS.items():
			results.append({
				'key': key,
				'name': name,
				'icon': icon,
				'command': uri,
				'args': '',
				'type': 'settings'
			})
		
		return results
