from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QCheckBox,
    QComboBox,
    QProgressBar,
)
from PySide6.QtCore import Qt


class ParserSettingsDialog(QDialog):
    """Dialog to show parser availability, copy pip install command,
    and trigger content indexing for selected formats with progress."""

    def __init__(self, index_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("解析器设置")
        self.index_manager = index_manager
        self.resize(520, 380)

        self.layout = QVBoxLayout(self)

        self.info_label = QLabel(
            "检测可用的内容解析器（PDF/DOCX/PPTX/ODT）。\n\n"
            "💡 性能提示：\n"
            "• 纯文本（.txt/.md）：索引速度快，推荐勾选\n"
            "• PDF/DOCX：索引较慢，建议按需构建或在空闲时运行\n"
            "• 构建后增量更新很快，只需全量构建一次"
        )
        self.layout.addWidget(self.info_label)
        self.list_widget = QListWidget()
        self.layout.addWidget(self.list_widget)

        # extension multi-select list
        sel_layout = QHBoxLayout()
        sel_layout.addWidget(QLabel("选择要索引的格式: "))
        self.select_all_cb = QCheckBox("全选支持格式")
        sel_layout.addWidget(self.select_all_cb)
        self.layout.addLayout(sel_layout)

        self.ext_list = QListWidget()
        self.ext_list.setSelectionMode(QListWidget.NoSelection)
        self.layout.addWidget(self.ext_list)

        h = QHBoxLayout()
        self.copy_btn = QPushButton("复制 pip 安装命令")
        self.copy_btn.clicked.connect(self.copy_pip_cmd)
        h.addWidget(self.copy_btn)

        self.build_btn = QPushButton("现在构建内容索引")
        self.build_btn.clicked.connect(self.start_build)
        h.addWidget(self.build_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setEnabled(False)
        h.addWidget(self.cancel_btn)

        self.cleanup_btn = QPushButton("清理内容索引（删除所有已写入）")
        self.cleanup_btn.clicked.connect(self._on_cleanup)
        self.cleanup_btn.setEnabled(True)
        h.addWidget(self.cleanup_btn)

        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.accept)
        h.addWidget(self.close_btn)

        self.layout.addLayout(h)

        # progress
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 100)
        self.pbar.setValue(0)
        self.layout.addWidget(self.pbar)

        self._building = False
        self._pip_cmd = ''
        self.refresh()

    def refresh(self):
        availability, pip_cmd = self.index_manager.check_parsers()
        self.list_widget.clear()
        items = [('\u2022 PDF (PyPDF2 / pdfminer.six)', availability.get('pdf', False)),
                 ('\u2022 DOCX (python-docx)', availability.get('docx', False)),
                 ('\u2022 PPTX (python-pptx)', availability.get('pptx', False)),
                 ('\u2022 ODT (odfpy)', availability.get('odt', False))]
        for label, ok in items:
            it = QListWidgetItem(f"{label}: {'可用' if ok else '不可用'}")
            self.list_widget.addItem(it)
        self._pip_cmd = pip_cmd
        self.copy_btn.setEnabled(bool(pip_cmd))
        # populate ext_list with supported extensions
        self.ext_list.clear()
        exts = ['.txt', '.md', '.pdf', '.docx', '.pptx', '.odt']
        for e in exts:
            it = QListWidgetItem(e)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            # default checked for text types
            if e in ['.txt', '.md']:
                it.setCheckState(Qt.Checked)
            else:
                it.setCheckState(Qt.Checked if (e == '.pdf' and availability.get('pdf', False)) or (e == '.docx' and availability.get('docx', False)) or (e == '.pptx' and availability.get('pptx', False)) or (e == '.odt' and availability.get('odt', False)) else Qt.Unchecked)
            self.ext_list.addItem(it)
        self.select_all_cb.setChecked(False)
        self.select_all_cb.stateChanged.connect(self._on_select_all_changed)

    def copy_pip_cmd(self):
        if not getattr(self, '_pip_cmd', ''):
            return
        clipboard = self.parent().clipboard() if self.parent() else None
        if clipboard is not None:
            clipboard.setText(self._pip_cmd)
            QMessageBox.information(self, '已复制', 'pip 安装命令已复制到剪贴板')
        else:
            QMessageBox.information(self, 'pip 安装命令', self._pip_cmd)

    def _on_progress(self, *args):
        try:
            # content_progress_signal may emit (parsed, written, total, message)
            if len(args) == 2:
                count, message = args
                if isinstance(count, int) and count > 0:
                    self.pbar.setRange(0, max(1, count))
                    self.pbar.setValue(min(self.pbar.maximum(), count))
                self.pbar.setFormat(str(message))
            elif len(args) == 4:
                parsed, written, total, message = args
                if isinstance(total, int) and total > 0:
                    self.pbar.setRange(0, max(1, total))
                    self.pbar.setValue(min(self.pbar.maximum(), written))
                # show parsed/written/total in the format
                try:
                    self.pbar.setFormat(f"{written}/{total} — {message}")
                except Exception:
                    self.pbar.setFormat(str(message))
            else:
                # unexpected signature
                if args:
                    self.pbar.setFormat(str(args[-1]))
        except Exception:
            pass

    def _on_finished(self):
        try:
            try:
                self.index_manager.content_progress_signal.disconnect(self._on_progress)
            except Exception:
                pass
            try:
                self.index_manager.content_build_finished_signal.disconnect(self._on_content_finished)
            except Exception:
                pass
        except Exception:
            pass
        self._building = False
        self.build_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.information(self, '完成', '内容索引构建已完成')

    def start_build(self):
        if self._building:
            return
        # determine allowed_exts from combo
        # collect checked extensions
        allowed = []
        for i in range(self.ext_list.count()):
            it = self.ext_list.item(i)
            if it.checkState() == Qt.Checked:
                allowed.append(it.text())
        if not allowed:
            # none selected -> treat as all supported
            allowed = None

        # connect progress
        try:
            # clear any previous stop flag
            try:
                self.index_manager.clear_stop_build()
            except Exception:
                pass
            self.index_manager.content_progress_signal.connect(self._on_progress)
            # listen for content-build finished/canceled events
            self.index_manager.content_build_finished_signal.connect(self._on_content_finished)
            self.cancel_btn.setEnabled(True)
        except Exception:
            pass

        # run build in background thread
        import threading

        def _run():
            try:
                self._building = True
                self.build_btn.setEnabled(False)
                self.index_manager.build_content_index(allowed_exts=allowed)
            finally:
                # ensure disconnect handled in _on_finished
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _on_cancel(self):
        try:
            # trigger stop in index manager
            try:
                self.index_manager.stop_build_content()
            except Exception:
                pass
            # inform user: cancellation stops further parsing; 已写入内容会保留
            self.pbar.setFormat('取消请求已发送，正在终止解析。已写入的内容会保留。')
            self.cancel_btn.setEnabled(False)
        except Exception:
            pass

    def _on_content_finished(self, canceled):
        # called when content build finishes (canceled==True if canceled)
        try:
            # disconnect progress handlers
            try:
                self.index_manager.content_progress_signal.disconnect(self._on_progress)
            except Exception:
                pass
            try:
                self.index_manager.content_build_finished_signal.disconnect(self._on_content_finished)
            except Exception:
                pass
            self._building = False
            self.build_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            if canceled:
                QMessageBox.information(self, '已取消', '内容索引构建已取消（部分已写入的数据将保留）。如需回滚，请使用“清理内容索引”按钮。')
                self.pbar.setFormat('已取消')
            else:
                QMessageBox.information(self, '完成', '内容索引构建已完成')
                self.pbar.setFormat('已完成')
        except Exception:
            pass

    def _on_select_all_changed(self, state):
        # toggle all items
        for i in range(self.ext_list.count()):
            it = self.ext_list.item(i)
            it.setCheckState(Qt.Checked if state == Qt.Checked else Qt.Unchecked)

    def _on_cleanup(self):
        # confirm destructive action
        ok = QMessageBox.question(self, '确认清理', '此操作将删除 content_fts 中的所有条目，无法恢复。是否继续？')
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            res = False
            try:
                res = self.index_manager.clear_content_fts()
            except Exception:
                res = False
            if res:
                QMessageBox.information(self, '已清理', 'content_fts 条目已全部删除')
                self.pbar.setValue(0)
                self.pbar.setFormat('已清理')
            else:
                QMessageBox.warning(self, '失败', '清理 content_fts 失败')
        except Exception:
            pass
