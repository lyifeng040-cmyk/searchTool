"""
搜索语法帮助对话框
"""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt


class SearchSyntaxHelpDialog(QDialog):
	"""搜索语法帮助对话框"""
	
	def __init__(self, parent=None):
		super().__init__(parent)
		self.setWindowTitle("🔍 搜索语法帮助")
		self.setMinimumSize(700, 600)
		self.setModal(True)
		
		layout = QVBoxLayout(self)
		layout.setContentsMargins(20, 20, 20, 20)
		layout.setSpacing(15)
		
		# 标题
		title = QLabel("⚡ 高级搜索语法")
		title.setFont(QFont("微软雅黑", 14, QFont.Bold))
		title.setStyleSheet("color: #4CAF50;")
		layout.addWidget(title)
		
		# 帮助文本
		help_text = QTextEdit()
		help_text.setReadOnly(True)
		help_text.setFont(QFont("Consolas", 10))
		help_text.setHtml(self._get_help_html())
		layout.addWidget(help_text, 1)
		
		# 关闭按钮
		close_btn = QPushButton("关闭")
		close_btn.setFixedWidth(100)
		close_btn.clicked.connect(self.accept)
		layout.addWidget(close_btn, 0, Qt.AlignRight)
	
	def _get_help_html(self):
		return """
<style>
	body { font-family: 'Microsoft YaHei', Arial; }
	h3 { color: #0078d4; margin-top: 15px; }
	code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; color: #d63384; }
	.example { background: #e3f2fd; padding: 10px; margin: 5px 0; border-left: 3px solid #2196F3; }
	.note { background: #fff3cd; padding: 8px; margin: 5px 0; border-left: 3px solid #ffc107; }
</style>

<h3>📌 基础搜索</h3>
<div class="example">
直接输入关键词：<code>report</code><br>
多个关键词（空格分隔）：<code>report 2024</code><br>
精确短语（双引号）：<code>"annual report"</code>
</div>

<h3>📁 按扩展名搜索</h3>
<div class="example">
<code>ext:pdf</code> - 只搜索 PDF 文件<br>
<code>ext:jpg,png</code> - 搜索 JPG 或 PNG 图片<br>
<code>report ext:docx</code> - 文件名含 report 的 Word 文档
</div>

<h3>📊 按文件大小搜索</h3>
<div class="example">
<code>size:&gt;100mb</code> - 大于 100MB 的文件<br>
<code>size:&lt;1kb</code> - 小于 1KB 的文件<br>
<code>size:10mb-50mb</code> - 10MB 到 50MB 之间<br>
支持单位：<code>kb</code>, <code>mb</code>, <code>gb</code>
</div>

<h3>🕒 按修改时间搜索</h3>
<div class="example">
<code>dm:today</code> - 今天修改的文件<br>
<code>dm:yesterday</code> - 昨天修改的<br>
<code>dm:week</code> - 本周修改的<br>
<code>dm:month</code> - 本月修改的<br>
<code>dm:year</code> - 今年修改的<br>
<code>dm:2024-12-01</code> - 指定日期之后修改的
</div>

<h3>📂 按路径搜索</h3>
<div class="example">
<code>path:D:\\Projects</code> - 只在 D:\\Projects 目录下搜索<br>
<code>path:"C:\\Program Files"</code> - 路径含空格用引号<br>
<code>report path:Desktop</code> - Desktop 目录下含 report 的文件
</div>

<h3>🔤 按文件名/目录名搜索</h3>
<div class="example">
<code>name:readme</code> - 文件名含 readme（不含路径）<br>
<code>dir:projects</code> - 所在目录名含 projects<br>
<code>name:*.log</code> - 所有 .log 文件（支持通配符）
</div>

<h3>🔗 组合搜索</h3>
<div class="example">
<code>report ext:pdf size:&gt;1mb dm:month</code><br>
→ 搜索本月修改的、大于1MB的、文件名含report的PDF文件
</div>

<div class="example">
<code>*.jpg path:D:\\Photos size:&gt;5mb dm:2024-01-01</code><br>
→ 搜索D:\\Photos下、2024年以后的、大于5MB的JPG图片
</div>

<h3>🎯 特殊操作符</h3>
<div class="example">
<code>!</code> 开头 - 强制精确搜索（不模糊匹配）<br>
<code>regex:</code> - 正则表达式搜索<br>
<code>content:</code> - 只搜索文件内容（需要内容索引）
</div>

<div class="note">
<b>💡 提示：</b><br>
• 搜索语法不区分大小写<br>
• 可以混合使用多个条件<br>
• 路径分隔符使用 <code>\\</code> 或 <code>/</code> 都可以<br>
• 带空格的值用双引号包裹
</div>
"""
