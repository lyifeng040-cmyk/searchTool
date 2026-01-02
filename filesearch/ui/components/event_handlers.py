import os
from typing import List, Tuple, Set

from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QDialog, QVBoxLayout, QTextEdit
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QTimer

from .file_operations import (
    open_file as fo_open_file,
    open_folder_and_select as fo_open_folder,
    copy_paths_to_clipboard as fo_copy_paths,
    copy_files_to_clipboard_win32 as fo_copy_files_win32,
    delete_items as fo_delete_items,
)


class EventHandlers:
    """Event handlers and helpers for `SearchApp`.

    Responsibilities:
    - Provide selection accessors (`get_selected_model_item(s)`).
    - Implement open / locate / copy / delete / preview actions.
    - Keep a pure helper `finalize_delete_pure` for unit testing.
    """

    def __init__(self, main):
        self.main = main
        # adaptive prompt counters
        # counts within current session for manual toggles or sensitivity adjustments
        try:
            if not hasattr(self.main, '_manual_mode_toggle_count'):
                self.main._manual_mode_toggle_count = 0
            if not hasattr(self.main, '_sens_change_count'):
                self.main._sens_change_count = 0
            if not hasattr(self.main, '_adaptive_prompt_shown'):
                self.main._adaptive_prompt_shown = False
        except Exception:
            pass

    # ----------------- selection helpers -----------------
    def get_selected_model_item(self):
        sel = self.main.tree.currentItem()
        if not sel:
            return None
        idx = self.main.item_meta.get(id(sel))
        if idx is None:
            return None
        with self.main.results_lock:
            if idx < 0 or idx >= len(self.main.filtered_results):
                return None
            return self.main.filtered_results[idx]

    def get_selected_model_items(self) -> List[dict]:
        items: List[dict] = []
        for sel in self.main.tree.selectedItems():
            idx = self.main.item_meta.get(id(sel))
            if idx is not None:
                with self.main.results_lock:
                    if 0 <= idx < len(self.main.filtered_results):
                        items.append(self.main.filtered_results[idx])
        return items

    # ----------------- UI actions -----------------
    def on_dblclick(self, item, column):
        if not item:
            return
        idx = self.main.item_meta.get(id(item))
        if idx is None:
            return
        with self.main.results_lock:
            if idx < 0 or idx >= len(self.main.filtered_results):
                return
            data = self.main.filtered_results[idx]

        if data.get("type_code") == 0:
            try:
                fo_open_folder(data["fullpath"])
            except Exception as e:
                QMessageBox.warning(self.main, "错误", f"无法打开文件夹: {e}")
        else:
            try:
                fo_open_file(data["fullpath"])
            except Exception as e:
                QMessageBox.warning(self.main, "错误", f"无法打开文件: {e}")

    def show_menu(self, pos):
        item = self.main.tree.itemAt(pos)
        if item:
            self.main.tree.setCurrentItem(item)
        ctx_menu = QMenu(self.main)
        ctx_menu.addAction("📂 打开文件", self.open_file)
        ctx_menu.addAction("🎯 定位文件", self.open_folder)
        ctx_menu.addAction("👁️ 预览文件", self.preview_file)
        ctx_menu.addSeparator()
        ctx_menu.addAction("📄 复制文件", self.copy_file)
        ctx_menu.addAction("📝 复制路径", self.copy_path)
        ctx_menu.addSeparator()
        ctx_menu.addAction("🗑️ 删除", self.delete_file)
        ctx_menu.exec_(self.main.tree.viewport().mapToGlobal(pos))

    def open_file(self):
        item = self.get_selected_model_item()
        if item:
            try:
                fo_open_file(item["fullpath"])
            except Exception as e:
                QMessageBox.warning(self.main, "错误", f"无法打开文件: {e}")

    def open_folder(self):
        item = self.get_selected_model_item()
        if item:
            try:
                fo_open_folder(item["fullpath"])
            except Exception as e:
                QMessageBox.warning(self.main, "错误", f"无法定位文件: {e}")

    def copy_path(self):
        items = self.get_selected_model_items()
        if not items:
            return
        paths = [item["fullpath"] for item in items]
        try:
            fo_copy_paths(QApplication, paths)
        except Exception:
            QApplication.clipboard().setText("\n".join(paths))
        self.main.status.setText(f"已复制 {len(paths)} 个路径")

    def copy_file(self):
        if not self.main.HAS_WIN32:
            QMessageBox.warning(self.main, "提示", "需要在 Windows 上使用此功能")
            return
        items = self.get_selected_model_items()
        if not items:
            return
        files = [item["fullpath"] for item in items if os.path.exists(item["fullpath"]) ]
        if not files:
            return
        try:
            fo_copy_files_win32(files)
            self.main.status.setText(f"已复制 {len(files)} 个文件")
        except Exception as e:
            QMessageBox.warning(self.main, "错误", f"复制文件失败: {e}")

    def delete_file(self):
        items = self.get_selected_model_items()
        if not items:
            return

        if len(items) == 1:
            msg = f"确定删除?\n{items[0]['filename']}"
        else:
            msg = f"确定删除 {len(items)} 个文件/文件夹?"

        if self.main.HAS_SEND2TRASH:
            msg += "\n\n(将移至回收站)"
        else:
            msg += "\n\n⚠️ 警告：将永久删除！"

        if QMessageBox.question(self.main, "确认", msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        deleted, failed, remove_exact, remove_prefix = fo_delete_items(items, use_send2trash=self.main.HAS_SEND2TRASH)

        # update internal state
        self.finalize_delete(deleted, failed, remove_exact, remove_prefix)

        if failed:
            self.main.status.setText(f"✅ 已删除 {deleted} 个，失败 {len(failed)} 个")
            QMessageBox.warning(self.main, "部分失败", "以下文件删除失败:\n" + "\n".join(failed[:5]))
        else:
            self.main.status.setText(f"✅ 已删除 {deleted} 个文件/文件夹")

    # pure helper for unit testing
    @staticmethod
    def finalize_delete_pure(all_results: List[dict], removed_exact: Set[str], removed_prefix: List[str]) -> List[dict]:
        # normalize exact paths and prefixes for reliable comparison
        removed_exact_norm = {os.path.normpath(p) for p in removed_exact}
        removed_prefix_norm = [os.path.normpath(p) for p in removed_prefix]

        def keep_item(x):
            xp = os.path.normpath(x.get("fullpath", ""))
            if xp in removed_exact_norm:
                return False
            for pref in removed_prefix_norm:
                # match either the directory itself or any child path
                if xp == pref or xp.startswith(pref + os.sep):
                    return False
            return True

        return [x for x in all_results if keep_item(x)]

    # instance method that mutates main state (uses locks and Qt)
    def finalize_delete(self, deleted: int, failed: List[str], remove_exact: Set[str], remove_prefix: List[str]):
        with self.main.results_lock:
            for p in list(self.main.shown_paths):
                pn = os.path.normpath(p)
                if pn in remove_exact:
                    self.main.shown_paths.discard(p)
                    continue
                for pref in remove_prefix:
                    if pn.startswith(pref):
                        self.main.shown_paths.discard(p)
                        break

            def keep_item(x):
                # normalize inputs for safe comparison
                xp = os.path.normpath(x.get("fullpath", ""))
                remove_exact_norm = {os.path.normpath(p) for p in remove_exact}
                remove_prefix_norm = [os.path.normpath(p) for p in remove_prefix]

                if xp in remove_exact_norm:
                    return False
                for pref in remove_prefix_norm:
                    if xp == pref or xp.startswith(pref + os.sep):
                        return False
                return True

            self.main.all_results = [x for x in self.main.all_results if keep_item(x)]
            self.main.filtered_results = [x for x in self.main.filtered_results if keep_item(x)]
            self.main.total_found = len(self.main.filtered_results)

        try:
            self.main._render_page()
        except Exception:
            pass

    def preview_file(self):
        item = self.get_selected_model_item()
        if not item:
            return

        ext = os.path.splitext(item.get("filename", ""))[1].lower()
        text_exts = {
            ".txt",
            ".log",
            ".py",
            ".json",
            ".xml",
            ".md",
            ".csv",
            ".ini",
            ".cfg",
            ".yaml",
            ".yml",
            ".js",
            ".css",
            ".sql",
            ".sh",
            ".bat",
            ".cmd",
        }

        if ext in text_exts:
            self._preview_text(item["fullpath"])
        elif item.get("type_code") == 0:
            try:
                fo_open_folder(item["fullpath"])
            except Exception as e:
                QMessageBox.warning(self.main, "错误", f"无法打开文件夹: {e}")
        else:
            try:
                fo_open_file(item["fullpath"])
            except Exception as e:
                QMessageBox.warning(self.main, "错误", f"无法打开文件: {e}")

    def _preview_text(self, path):
        dlg = QDialog(self.main)
        dlg.setWindowTitle(f"预览: {os.path.basename(path)}")
        dlg.resize(800, 600)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(5, 5, 5, 5)

        text = QTextEdit()
        text.setFont(QFont("Consolas", 10))
        text.setReadOnly(True)
        layout.addWidget(text)

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(200000)
            if len(content) >= 200000:
                content += "\n\n... [文件过大，仅显示前200KB] ..."
            text.setPlainText(content)
        except Exception as e:
            text.setPlainText(f"无法读取文件: {e}")

        dlg.exec()

    # `on_fuzzy_changed` removed — obsolete sensitivity handler removed along with related UI.

    def on_auto_mode_toggled(self, enabled: bool):
        # track manual toggles: if user repeatedly toggles to manual, prompt
        try:
            if getattr(self.main, 'config_mgr', None) and self.main.config_mgr.get_auto_mode_prompt_disabled():
                return
            # only count toggles to manual (enabled == False)
            if not enabled:
                self.main._manual_mode_toggle_count = getattr(self.main, '_manual_mode_toggle_count', 0) + 1
            # if toggled to manual 2 times in session, prompt once
            if getattr(self.main, '_manual_mode_toggle_count', 0) >= 2 and not getattr(self.main, '_adaptive_prompt_shown', False):
                self._show_adaptive_prompt(reason='manual_toggle')
        except Exception:
            pass

    def _show_adaptive_prompt(self, reason: str = 'manual_toggle'):
        """Show a suggestion dialog asking user whether to disable auto-mode permanently or stop prompting."""
        try:
            self.main._adaptive_prompt_shown = True
            msg = "检测到您最近多次手动控制搜索行为。\n是否要关闭“自动模式”，以便更直接地控制模糊/精确搜索？"
            dlg = QMessageBox(self.main)
            dlg.setWindowTitle("提示: 自动模式")
            dlg.setText(msg)
            btn_disable = dlg.addButton("关闭自动模式", QMessageBox.AcceptRole)
            btn_keep = dlg.addButton("继续保留自动模式", QMessageBox.RejectRole)
            btn_never = dlg.addButton("不再提示", QMessageBox.DestructiveRole)
            dlg.exec()
            clicked = dlg.clickedButton()
            if clicked == btn_disable:
                # disable auto mode and persist
                try:
                    self.main.auto_mode = False
                    if hasattr(self.main, 'chk_auto_mode'):
                        self.main.chk_auto_mode.setChecked(False)
                    self.main.config_mgr.set_search_auto_mode(False)
                except Exception:
                    pass
            elif clicked == btn_never:
                try:
                    self.main.config_mgr.set_auto_mode_prompt_disabled(True)
                except Exception:
                    pass
            # else keep auto mode and do nothing
        except Exception:
            pass
