"""
Result renderer: contains pure functions for filtering & pagination (testable without Qt)
and a `ResultRenderer` class that wires those functions to a `SearchApp` instance and
performs the QTreeWidget rendering and stat updates.
"""
from typing import List, Dict, Tuple, Optional
import os
import time
import datetime
import threading
import ctypes

from ..components.column_manager import compute_base_widths

# When there are many filtered results, avoid scoring the whole set on the main thread.
# (Scoring helpers were removed; renderer only applies deterministic sorting.)
GLOBAL_SCORE_LIMIT = 2000

# Note: to avoid circular import, the ResultRenderer class accepts a `main` object
# (SearchApp instance) and uses its attributes (tree, index_mgr, config, etc.).


def apply_filter_logic(all_results: List[dict], target_ext: Optional[str], size_min: int, date_min: int) -> List[dict]:
    """Return filtered results according to criteria. Pure function (no Qt).
    `target_ext` should be like '📂文件夹' or '.txt' or None for '全部'.
    """
    out = []
    for item in all_results:
        if size_min > 0 and item.get("type_code") == 2 and item.get("size", 0) < size_min:
            continue
        if date_min > 0 and item.get("mtime", 0) < date_min:
            continue
        if target_ext:
            if item.get("type_code") == 0:
                item_ext = "📂文件夹"
            elif item.get("type_code") == 1:
                item_ext = "📦压缩包"
            else:
                item_ext = os.path.splitext(item.get("filename", ""))[1].lower() or "(无)"
            if item_ext != target_ext:
                continue
        out.append(item)
    return out


def paginate_items(items: List[dict], page_size: int, current_page: int) -> Tuple[List[dict], int]:
    """Return (page_items, total_pages)"""
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = max(1, min(current_page, total_pages))
    start = (current_page - 1) * page_size
    end = start + page_size
    return items[start:end], total_pages


# scoring helpers removed — renderer uses deterministic substring/path ordering


def sort_everything_style(keyword: str, items: List[dict]) -> List[dict]:
    """Deterministic Everything-like ordering.

    Rules (approximation):
    - Exact filename match (case-insensitive) first.
    - Filename prefix match next.
    - Filename contains (earlier position better) next.
    - Fullpath contains (earlier position better) next.
    - Tie-breaker: shorter filename, then shallower path (fewer separators).
    """
    if not keyword:
        return items

    kw = keyword.lower()

    def rank(it: dict):
        fn = (it.get("filename") or "").lower()
        fp = (it.get("fullpath") or "").lower()
        # exact filename
        if fn == kw:
            primary = 0
            pos = 0
        # prefix
        elif fn.startswith(kw):
            primary = 1
            pos = fn.find(kw) if kw in fn else 9999
        # filename contains
        elif kw in fn:
            primary = 2
            pos = fn.find(kw)
        # fullpath contains
        elif kw in fp:
            primary = 3
            pos = fp.find(kw)
        else:
            primary = 4
            pos = 9999

        # shorter filename preferred
        fn_len = len(fn) if fn else 9999
        # shallower path preferred (count separators)
        depth = (fp.count(os.sep) if fp else 9999)
        return (primary, pos, fn_len, depth, it.get("filename", ""))

    try:
        return sorted(items, key=rank)
    except Exception:
        return items


class ResultRenderer:
    def __init__(self, main):
        self.main = main

    # ---------- Pure helpers that use main state ----------
    def _get_size_min(self):
        mapping = {
            "不限": 0,
            ">1MB": 1 << 20,
            ">10MB": 10 << 20,
            ">100MB": 100 << 20,
            ">500MB": 500 << 20,
            ">1GB": 1 << 30,
        }
        return mapping.get(self.main.size_var.currentText(), 0)

    def _get_date_min(self):
        now = time.time()
        day = 86400
        mapping = {
            "不限": 0,
            "今天": now - day,
            "3天内": now - 3 * day,
            "7天内": now - 7 * day,
            "30天内": now - 30 * day,
            "今年": time.mktime(datetime.datetime(datetime.datetime.now().year, 1, 1).timetuple()),
        }
        return mapping.get(self.main.date_var.currentText(), 0)

    def update_ext_combo(self):
        counts = {}
        with self.main.results_lock:
            for item in self.main.all_results:
                if item.get("type_code") == 0:
                    ext = "📂文件夹"
                elif item.get("type_code") == 1:
                    ext = "📦压缩包"
                else:
                    ext = os.path.splitext(item.get("filename", ""))[1].lower() or "(无)"
                counts[ext] = counts.get(ext, 0) + 1

        values = ["全部"] + [f"{ext} ({cnt})" for ext, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]]
        self.main.ext_var.clear()
        self.main.ext_var.addItems(values)

    # ---------- Filtering & rendering ----------
    def apply_filter(self):
        ext_sel = self.main.ext_var.currentText()
        size_min = self._get_size_min()
        date_min = self._get_date_min()
        target_ext = ext_sel.split(" (")[0] if ext_sel != "全部" else None

        with self.main.results_lock:
            self.main.filtered_results = apply_filter_logic(self.main.all_results, target_ext, size_min, date_min)

        self.main.current_page = 1
        self.render_page()

        with self.main.results_lock:
            all_count = len(self.main.all_results)
            filtered_count = len(self.main.filtered_results)

        if ext_sel != "全部" or size_min > 0 or date_min > 0:
            self.main.lbl_filter.setText(f"筛选: {filtered_count}/{all_count}")
        else:
            self.main.lbl_filter.setText("")

    def clear_filter(self):
        self.main.ext_var.setCurrentText("全部")
        self.main.size_var.setCurrentText("不限")
        self.main.date_var.setCurrentText("不限")
        with self.main.results_lock:
            self.main.filtered_results = list(self.main.all_results)
        self.main.current_page = 1
        self.render_page()
        self.main.lbl_filter.setText("")

    def update_page_info(self):
        total = len(self.main.filtered_results)
        self.main.total_pages = max(1, int((total + self.main.page_size - 1) / self.main.page_size))
        self.main.lbl_page.setText(f"第 {self.main.current_page}/{self.main.total_pages} 页 ({total}项)")
        self.main.btn_first.setEnabled(self.main.current_page > 1)
        self.main.btn_prev.setEnabled(self.main.current_page > 1)
        self.main.btn_next.setEnabled(self.main.current_page < self.main.total_pages)
        self.main.btn_last.setEnabled(self.main.current_page < self.main.total_pages)

    def go_page(self, action: str):
        if action == "first":
            self.main.current_page = 1
        elif action == "prev" and self.main.current_page > 1:
            self.main.current_page -= 1
        elif action == "next" and self.main.current_page < self.main.total_pages:
            self.main.current_page += 1
        elif action == "last":
            self.main.current_page = self.main.total_pages
        self.render_page()

    def render_page(self):
        # main keeps filtered_results and item_meta; this method performs stat updates and QTreeWidget rendering
        self.main.tree.clear()
        self.main.item_meta.clear()
        self.update_page_info()

        # prepare items list (Everything-style ordering applied above; no scoring)
        with self.main.results_lock:
            all_filtered = list(self.main.filtered_results)

        # If simple Everything-style mode is enabled, apply deterministic Everything sorting
        try:
            simple_mode = False
            try:
                if getattr(self.main, 'config_mgr', None) is not None:
                    simple_mode = bool(self.main.config_mgr.get_search_simple_mode())
            except Exception:
                simple_mode = False
            kw_simple = None
            if getattr(self.main, 'last_search_params', None):
                kw_simple = self.main.last_search_params.get('kw')
            # Only apply Everything-style automatic ordering when the user has not
            # manually requested a column sort. Manual sorts should take precedence.
            if simple_mode and kw_simple and not getattr(self.main, 'user_sorted', False):
                try:
                    all_filtered = sort_everything_style(kw_simple, all_filtered)
                except Exception:
                    pass
        except Exception:
            # ignore sorting errors and continue
            pass

        # Note: optional scoring removed from renderer — search uses precise substring/regex
        # Everything-style deterministic ordering (if enabled) already applied above.

        start = (self.main.current_page - 1) * self.main.page_size
        end = start + self.main.page_size
        page_items = all_filtered[start:end]
        if not page_items:
            return

        # try rust engine batch stat if available; otherwise fallback stat method on main
        if hasattr(self.main, 'index_mgr') and getattr(self.main, 'index_mgr') and hasattr(self.main, 'index_mgr'):
            try:
                if hasattr(self.main, 'index_mgr') and self.main.index_mgr and hasattr(self.main, 'conn'):
                    pass
            except Exception:
                pass

        # call existing fallback/stat logic on main to avoid duplicating DB specifics
        try:
            self.main._fallback_stat(page_items)
        except Exception:
            pass

        # fill missing mtimes
        missing_updates = []
        for it in page_items:
            if it.get("mtime", 0) <= 0:
                try:
                    it["mtime"] = os.path.getmtime(it["fullpath"])
                    missing_updates.append((it.get("size", 0), it["mtime"], it["fullpath"]))
                except Exception:
                    continue
        if missing_updates and getattr(self.main, 'index_mgr', None) and self.main.index_mgr.conn:
            threading.Thread(target=self.main._write_back_stat, args=(missing_updates,), daemon=True).start()

        for it in page_items:
            tc = it.get("type_code", 2)
            if tc == 0:
                it["size_str"] = "📂 文件夹"
            elif tc == 1:
                it["size_str"] = "📦 压缩包"
            else:
                from ...utils import format_size, format_time
                it["size_str"] = format_size(it.get("size", 0))
            from ...utils import format_time
            it["mtime_str"] = format_time(it.get("mtime", 0))

        self.main.tree.setUpdatesEnabled(False)
        try:
            for i, item in enumerate(page_items):
                filename = item.get("filename", "")
                dir_path = item.get("dir_path", "")
                size_str = item.get("size_str", "")
                mtime_str = item.get("mtime_str", "")
                
                row_data = [filename, dir_path, size_str, mtime_str]
                
                from PySide6.QtWidgets import QTreeWidgetItem
                from PySide6.QtCore import Qt
                q_item = QTreeWidgetItem(row_data)
                
                # 为长文本设置 tooltip，鼠标悬停时显示完整内容
                q_item.setToolTip(0, filename)
                q_item.setToolTip(1, dir_path)
                
                q_item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
                q_item.setTextAlignment(3, Qt.AlignRight | Qt.AlignVCenter)
                q_item.setData(2, Qt.UserRole, item.get("size", 0))
                q_item.setData(3, Qt.UserRole, item.get("mtime", 0))
                self.main.tree.addTopLevelItem(q_item)
                self.main.item_meta[id(q_item)] = start + i
        finally:
            self.main.tree.setUpdatesEnabled(True)

    # Keep minimal stat/write_back hooks — delegate to main for DB operations
    def _write_back_stat(self, updates: List[Tuple[int, int, str]]):
        try:
            self.main._write_back_stat(updates)
        except Exception:
            pass
