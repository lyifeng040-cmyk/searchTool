"""
图片预览对话框
"""
from PySide6.QtWidgets import (
	QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea
)
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtCore import Qt
from filesearch.utils import format_size
import os


class ImagePreviewDialog(QDialog):
	"""图片预览对话框"""
	
	def __init__(self, parent=None, filepath=""):
		super().__init__(parent)
		self.setWindowTitle(f"🖼️ 图片预览 - {os.path.basename(filepath)}")
		self.setMinimumSize(800, 600)
		
		self.filepath = filepath
		
		layout = QVBoxLayout(self)
		layout.setContentsMargins(10, 10, 10, 10)
		layout.setSpacing(10)
		
		# 文件信息
		info_layout = QHBoxLayout()
		self.info_label = QLabel()
		self.info_label.setFont(QFont("微软雅黑", 9))
		self.info_label.setStyleSheet("color: #666;")
		info_layout.addWidget(self.info_label)
		info_layout.addStretch()
		layout.addLayout(info_layout)
		
		# 图片显示区域（可滚动）
		scroll = QScrollArea()
		scroll.setWidgetResizable(True)
		scroll.setAlignment(Qt.AlignCenter)
		
		self.image_label = QLabel()
		self.image_label.setAlignment(Qt.AlignCenter)
		self.image_label.setScaledContents(False)
		scroll.setWidget(self.image_label)
		
		layout.addWidget(scroll, 1)
		
		# 按钮
		btn_layout = QHBoxLayout()
		self.zoom_in_btn = QPushButton("🔍 放大")
		self.zoom_in_btn.clicked.connect(self._zoom_in)
		btn_layout.addWidget(self.zoom_in_btn)
		
		self.zoom_out_btn = QPushButton("🔎 缩小")
		self.zoom_out_btn.clicked.connect(self._zoom_out)
		btn_layout.addWidget(self.zoom_out_btn)
		
		self.fit_btn = QPushButton("📐 适应窗口")
		self.fit_btn.clicked.connect(self._fit_window)
		btn_layout.addWidget(self.fit_btn)
		
		self.actual_btn = QPushButton("💯 实际大小")
		self.actual_btn.clicked.connect(self._actual_size)
		btn_layout.addWidget(self.actual_btn)
		
		btn_layout.addStretch()
		close_btn = QPushButton("关闭")
		close_btn.clicked.connect(self.accept)
		btn_layout.addWidget(close_btn)
		
		layout.addLayout(btn_layout)
		
		# 加载图片
		self.pixmap = None
		self.scale_factor = 1.0
		self._load_image()
	
	def _load_image(self):
		"""加载并显示图片"""
		if not os.path.exists(self.filepath):
			self.info_label.setText("❌ 文件不存在")
			return
		
		try:
			self.pixmap = QPixmap(self.filepath)
			if self.pixmap.isNull():
				self.info_label.setText("❌ 无法加载图片")
				return
			
			# 显示文件信息
			size = os.path.getsize(self.filepath)
			width = self.pixmap.width()
			height = self.pixmap.height()
			self.info_label.setText(
				f"📊 {width} × {height} 像素  |  {format_size(size)}  |  {self.filepath}"
			)
			
			# 适应窗口
			self._fit_window()
		
		except Exception as e:
			self.info_label.setText(f"❌ 加载失败: {e}")
	
	def _fit_window(self):
		"""适应窗口大小"""
		if not self.pixmap:
			return
		
		# 计算缩放比例
		available_size = self.size()
		available_width = available_size.width() - 40
		available_height = available_size.height() - 150
		
		scale_w = available_width / self.pixmap.width()
		scale_h = available_height / self.pixmap.height()
		self.scale_factor = min(scale_w, scale_h, 1.0)  # 不放大，只缩小
		
		self._update_display()
	
	def _actual_size(self):
		"""显示实际大小"""
		self.scale_factor = 1.0
		self._update_display()
	
	def _zoom_in(self):
		"""放大"""
		self.scale_factor *= 1.25
		self._update_display()
	
	def _zoom_out(self):
		"""缩小"""
		self.scale_factor /= 1.25
		if self.scale_factor < 0.1:
			self.scale_factor = 0.1
		self._update_display()
	
	def _update_display(self):
		"""更新图片显示"""
		if not self.pixmap:
			return
		
		scaled_pixmap = self.pixmap.scaled(
			int(self.pixmap.width() * self.scale_factor),
			int(self.pixmap.height() * self.scale_factor),
			Qt.KeepAspectRatio,
			Qt.SmoothTransformation
		)
		self.image_label.setPixmap(scaled_pixmap)
		self.image_label.adjustSize()
