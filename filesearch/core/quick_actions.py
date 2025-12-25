"""
快速动作系统 - 对选中文件执行自定义操作
"""
import os
import subprocess
import shutil
import zipfile
from pathlib import Path


class QuickAction:
	"""快速动作基类"""
	
	def __init__(self, name, description, icon, keywords):
		self.name = name
		self.description = description
		self.icon = icon
		self.keywords = keywords  # 触发关键词列表
	
	def can_execute(self, filepaths):
		"""检查是否可以对给定文件执行此操作"""
		return True
	
	def execute(self, filepaths):
		"""执行操作，返回 (success, message)"""
		raise NotImplementedError


class CompressAction(QuickAction):
	"""压缩文件动作"""
	
	def __init__(self):
		super().__init__(
			name="压缩文件",
			description="将选中文件压缩为 ZIP 格式",
			icon="📦",
			keywords=["compress", "zip", "压缩"]
		)
	
	def execute(self, filepaths):
		if not filepaths:
			return False, "没有选中文件"
		
		try:
			# 确定输出文件名
			if len(filepaths) == 1:
				base_name = Path(filepaths[0]).stem
			else:
				base_name = "archive"
			
			# 在第一个文件的目录创建压缩包
			output_dir = Path(filepaths[0]).parent
			zip_path = output_dir / f"{base_name}.zip"
			
			# 如果文件已存在，添加序号
			counter = 1
			while zip_path.exists():
				zip_path = output_dir / f"{base_name}_{counter}.zip"
				counter += 1
			
			# 创建压缩包
			with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
				for filepath in filepaths:
					if os.path.isfile(filepath):
						zipf.write(filepath, Path(filepath).name)
					elif os.path.isdir(filepath):
						for root, dirs, files in os.walk(filepath):
							for file in files:
								file_path = os.path.join(root, file)
								arcname = os.path.relpath(file_path, Path(filepath).parent)
								zipf.write(file_path, arcname)
			
			return True, f"已创建压缩包: {zip_path.name}"
		
		except Exception as e:
			return False, f"压缩失败: {e}"


class OpenWithVSCodeAction(QuickAction):
	"""用 VS Code 打开"""
	
	def __init__(self):
		super().__init__(
			name="VS Code 打开",
			description="使用 Visual Studio Code 打开文件或文件夹",
			icon="💻",
			keywords=["vscode", "code", "vs"]
		)
	
	def execute(self, filepaths):
		if not filepaths:
			return False, "没有选中文件"
		
		try:
			# 尝试找到 VS Code
			vscode_commands = ['code', 'code-insiders', 'Code.exe']
			
			for cmd in vscode_commands:
				try:
					# 测试命令是否存在
					subprocess.run([cmd, '--version'], 
					             capture_output=True, 
					             timeout=2, 
					             check=False)
					# 打开文件
					subprocess.Popen([cmd] + filepaths)
					return True, f"已用 VS Code 打开 {len(filepaths)} 个项目"
				except (FileNotFoundError, subprocess.TimeoutExpired):
					continue
			
			return False, "未找到 VS Code，请确保已安装并添加到 PATH"
		
		except Exception as e:
			return False, f"打开失败: {e}"


class GitAction(QuickAction):
	"""Git 操作"""
	
	def __init__(self):
		super().__init__(
			name="Git 操作",
			description="在 Git Bash 中打开文件所在目录",
			icon="🔀",
			keywords=["git", "版本控制"]
		)
	
	def execute(self, filepaths):
		if not filepaths:
			return False, "没有选中文件"
		
		try:
			# 获取第一个文件的目录
			if os.path.isdir(filepaths[0]):
				target_dir = filepaths[0]
			else:
				target_dir = Path(filepaths[0]).parent
			
			# 尝试打开 Git Bash
			git_bash_paths = [
				r"C:\Program Files\Git\git-bash.exe",
				r"C:\Program Files (x86)\Git\git-bash.exe",
			]
			
			for git_bash in git_bash_paths:
				if os.path.exists(git_bash):
					subprocess.Popen([git_bash, '--cd', str(target_dir)])
					return True, f"已在 Git Bash 中打开: {Path(target_dir).name}"
			
			# 如果没有 Git Bash，尝试在 cmd 中运行 git
			subprocess.Popen(['cmd', '/k', f'cd /d "{target_dir}" && git status'])
			return True, f"已在命令行中打开: {Path(target_dir).name}"
		
		except Exception as e:
			return False, f"打开失败: {e}"


class CopyToAction(QuickAction):
	"""复制到指定位置"""
	
	def __init__(self):
		super().__init__(
			name="复制到桌面",
			description="将文件复制到桌面",
			icon="📋",
			keywords=["copyto", "desktop", "复制到桌面"]
		)
	
	def execute(self, filepaths):
		if not filepaths:
			return False, "没有选中文件"
		
		try:
			desktop = Path.home() / "Desktop"
			copied_count = 0
			
			for filepath in filepaths:
				filename = Path(filepath).name
				dest = desktop / filename
				
				# 如果目标文件已存在，添加序号
				if dest.exists():
					counter = 1
					stem = Path(filepath).stem
					suffix = Path(filepath).suffix
					while dest.exists():
						dest = desktop / f"{stem}_{counter}{suffix}"
						counter += 1
				
				if os.path.isfile(filepath):
					shutil.copy2(filepath, dest)
					copied_count += 1
				elif os.path.isdir(filepath):
					shutil.copytree(filepath, dest)
					copied_count += 1
			
			return True, f"已复制 {copied_count} 个项目到桌面"
		
		except Exception as e:
			return False, f"复制失败: {e}"


class EmailAction(QuickAction):
	"""邮件发送"""
	
	def __init__(self):
		super().__init__(
			name="邮件发送",
			description="创建包含文件的邮件",
			icon="📧",
			keywords=["email", "mail", "邮件"]
		)
	
	def execute(self, filepaths):
		if not filepaths:
			return False, "没有选中文件"
		
		try:
			# 构建 mailto URL
			import urllib.parse
			
			file_list = "\n".join([f"- {Path(fp).name}" for fp in filepaths[:10]])
			if len(filepaths) > 10:
				file_list += f"\n... 还有 {len(filepaths) - 10} 个文件"
			
			subject = f"分享 {len(filepaths)} 个文件"
			body = f"附件文件列表:\n{file_list}\n\n文件路径:\n{filepaths[0]}"
			
			mailto_url = f"mailto:?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"
			
			import webbrowser
			webbrowser.open(mailto_url)
			
			return True, "已打开默认邮件客户端"
		
		except Exception as e:
			return False, f"打开失败: {e}"


class ActionManager:
	"""动作管理器"""
	
	def __init__(self):
		self.actions = [
			CompressAction(),
			OpenWithVSCodeAction(),
			GitAction(),
			CopyToAction(),
			EmailAction(),
		]
	
	def find_action(self, keyword):
		"""根据关键词查找动作"""
		keyword_lower = keyword.lower().strip()
		
		for action in self.actions:
			if keyword_lower in [kw.lower() for kw in action.keywords]:
				return action
		
		return None
	
	def get_all_actions(self):
		"""获取所有动作"""
		return self.actions
	
	def execute_action(self, keyword, filepaths):
		"""执行动作"""
		action = self.find_action(keyword)
		if not action:
			return False, f"未找到动作: {keyword}"
		
		return action.execute(filepaths)
	
	def get_help_text(self):
		"""生成帮助文本"""
		lines = ["快速动作：\n"]
		for action in self.actions:
			keywords = ', '.join(action.keywords)
			lines.append(f"{action.icon} {action.name}")
			lines.append(f"  关键词: {keywords}")
			lines.append(f"  说明: {action.description}")
			lines.append("")
		return "\n".join(lines)
