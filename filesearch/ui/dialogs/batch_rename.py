"""
Batch rename dialog extracted from the legacy implementation.
"""

import logging
import os

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
	QDialog,
	QVBoxLayout,
	QLabel,
	QGroupBox,
	QHBoxLayout,
	QRadioButton,
	QLineEdit,
	QSpinBox,
	QTextEdit,
	QPushButton,
	QMessageBox,
)

logger = logging.getLogger(__name__)


class BatchRenameDialog:
	"""批量重命名对话框"""

	def __init__(self, parent, targets, app):
		self.parent = parent
		self.targets = targets
		self.app = app
		self.dialog = None
		self.preview_lines = []

	def show(self, scope_text=""):
		"""显示对话框"""
		self.dialog = QDialog(self.parent)
		self.dialog.setWindowTitle("✏ 批量重命名")
		self.dialog.setMinimumSize(780, 650)
		self.dialog.setModal(True)

		main_layout = QVBoxLayout(self.dialog)
		main_layout.setContentsMargins(15, 15, 15, 15)
		main_layout.setSpacing(10)

		title_label = QLabel("批量重命名")
		title_label.setFont(QFont("微软雅黑", 12, QFont.Bold))
		main_layout.addWidget(title_label)

		scope_label = QLabel(scope_text)
		scope_label.setFont(QFont("微软雅黑", 9))
		scope_label.setStyleSheet("color: #555;")
		main_layout.addWidget(scope_label)

		rule_group = QGroupBox("重命名规则")
		rule_layout = QVBoxLayout(rule_group)

		mode_layout = QHBoxLayout()
		self.mode_prefix_radio = QRadioButton("前缀 + 序号")
		self.mode_prefix_radio.setChecked(True)
		self.mode_prefix_radio.toggled.connect(self._on_mode_change)
		mode_layout.addWidget(self.mode_prefix_radio)

		self.mode_replace_radio = QRadioButton("替换文本")
		self.mode_replace_radio.toggled.connect(self._on_mode_change)
		mode_layout.addWidget(self.mode_replace_radio)

		mode_layout.addStretch()
		rule_layout.addLayout(mode_layout)

		prefix_layout = QHBoxLayout()
		prefix_layout.addWidget(QLabel("新前缀:"))
		self.prefix_input = QLineEdit()
		self.prefix_input.setMaximumWidth(150)
		self.prefix_input.textChanged.connect(self._update_preview)
		prefix_layout.addWidget(self.prefix_input)

		prefix_layout.addWidget(QLabel("起始序号:"))
		self.start_num_input = QSpinBox()
		self.start_num_input.setRange(1, 99999)
		self.start_num_input.setValue(1)
		self.start_num_input.valueChanged.connect(self._update_preview)
		prefix_layout.addWidget(self.start_num_input)

		prefix_layout.addWidget(QLabel("序号位数:"))
		self.width_input = QSpinBox()
		self.width_input.setRange(1, 10)
		self.width_input.setValue(3)
		self.width_input.valueChanged.connect(self._update_preview)
		prefix_layout.addWidget(self.width_input)

		prefix_layout.addStretch()
		rule_layout.addLayout(prefix_layout)

		replace_layout = QHBoxLayout()
		replace_layout.addWidget(QLabel("查找文本:"))
		self.find_input = QLineEdit()
		self.find_input.setMaximumWidth(150)
		self.find_input.textChanged.connect(self._update_preview)
		replace_layout.addWidget(self.find_input)

		replace_layout.addWidget(QLabel("替换为:"))
		self.replace_input = QLineEdit()
		self.replace_input.setMaximumWidth(150)
		self.replace_input.textChanged.connect(self._update_preview)
		replace_layout.addWidget(self.replace_input)

		replace_layout.addStretch()
		rule_layout.addLayout(replace_layout)

		main_layout.addWidget(rule_group)

		preview_group = QGroupBox("预览")
		preview_layout = QVBoxLayout(preview_group)

		self.preview_text = QTextEdit()
		self.preview_text.setFont(QFont("Consolas", 9))
		self.preview_text.setReadOnly(True)
		self.preview_text.setMinimumHeight(250)
		preview_layout.addWidget(self.preview_text)

		main_layout.addWidget(preview_group, 1)

		btn_layout = QHBoxLayout()

		preview_btn = QPushButton("预览效果")
		preview_btn.clicked.connect(self._update_preview)
		btn_layout.addWidget(preview_btn)

		execute_btn = QPushButton("执行重命名")
		execute_btn.clicked.connect(self._do_rename)
		btn_layout.addWidget(execute_btn)

		btn_layout.addStretch()

		close_btn = QPushButton("关闭")
		close_btn.clicked.connect(self.dialog.reject)
		btn_layout.addWidget(close_btn)

		main_layout.addLayout(btn_layout)

		self._update_preview()
		self.dialog.exec_()

	def _on_mode_change(self):
		self._update_preview()

	def _update_preview(self):
		"""更新预览"""
		self.preview_text.clear()
		self.preview_lines = []

		if not self.targets:
			self.preview_text.setPlainText("（没有可重命名的项目）")
			return

		mode = "prefix" if self.mode_prefix_radio.isChecked() else "replace"

		if mode == "prefix":
			prefix = self.prefix_input.text()
			start = self.start_num_input.value()
			width = self.width_input.value()

			num = start
			for item in self.targets:
				old_full = item["fullpath"]
				old_name = item["filename"]
				name, ext = os.path.splitext(old_name)
				new_name = f"{prefix}{str(num).zfill(width)}{ext}"
				num += 1
				new_full = os.path.join(os.path.dirname(old_full), new_name)
				self.preview_lines.append((old_full, new_full))
		else:
			find = self.find_input.text()
			replace = self.replace_input.text()
			for item in self.targets:
				old_full = item["fullpath"]
				old_name = item["filename"]
				name, ext = os.path.splitext(old_name)
				new_name = name.replace(find, replace) + ext if find else old_name
				new_full = os.path.join(os.path.dirname(old_full), new_name)
				self.preview_lines.append((old_full, new_full))

		lines = []
		for old_full, new_full in self.preview_lines:
			old_name = os.path.basename(old_full)
			new_name = os.path.basename(new_full)
			mark = ""
			if old_full == new_full:
				mark = "  (未变化)"
			elif (
				os.path.exists(new_full)
				and os.path.normpath(old_full).lower()
				!= os.path.normpath(new_full).lower()
			):
				mark = "  (⚠ 目标已存在)"
			lines.append(f"{old_name}  →  {new_name}{mark}")

		self.preview_text.setPlainText("\n".join(lines))

	def _do_rename(self):
		"""执行重命名"""
		if not self.preview_lines:
			QMessageBox.warning(self.dialog, "提示", "没有可执行的重命名记录")
			return

		if (
			QMessageBox.question(self.dialog, "确认", "确定执行重命名？\n请先确认预览无误。")
			!= QMessageBox.Yes
		):
			return

		success = 0
		skipped = 0
		failed = 0
		renamed_pairs = []

		for old_full, new_full in self.preview_lines:
			if old_full == new_full:
				skipped += 1
				continue
			try:
				if (
					os.path.exists(new_full)
					and os.path.normpath(old_full).lower()
					!= os.path.normpath(new_full).lower()
				):
					skipped += 1
					continue
				os.rename(old_full, new_full)
				success += 1
				renamed_pairs.append((old_full, new_full))
			except Exception as e:  # noqa: BLE001
				failed += 1
				logger.error(f"[重命名失败] {old_full} -> {new_full} - {e}")

		if renamed_pairs:
			with self.app.results_lock:
				for old_full, new_full in renamed_pairs:
					old_norm = os.path.normpath(old_full)
					new_norm = os.path.normpath(new_full)
					new_name = os.path.basename(new_norm)
					new_dir = os.path.dirname(new_norm)

					for item in self.app.all_results:
						if os.path.normpath(item.get("fullpath", "")) == old_norm:
							item["fullpath"] = new_norm
							item["filename"] = new_name
							item["dir_path"] = new_dir
							break

					for item in self.app.filtered_results:
						if os.path.normpath(item.get("fullpath", "")) == old_norm:
							item["fullpath"] = new_norm
							item["filename"] = new_name
							item["dir_path"] = new_dir
							break

					if hasattr(self.app, "shown_paths"):
						self.app.shown_paths.discard(old_norm)
						self.app.shown_paths.add(new_norm)

				self.app.current_page = 1

		try:
			self.app._render_page()  # noqa: SLF001
		except Exception as e:  # noqa: BLE001
			logger.error(f"[同步] 刷新界面失败: {e}")

		self.app.status.setText(
			f"批量重命名完成：成功 {success}，跳过 {skipped}，失败 {failed}"
		)
		QMessageBox.information(
			self.dialog,
			"完成",
			f"重命名完成：成功 {success}，跳过 {skipped}，失败 {failed}",
		)
		self.dialog.accept()


__all__ = ["BatchRenameDialog"]
