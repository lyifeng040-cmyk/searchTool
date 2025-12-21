"""IndexSearchWorker：从原版提取，逻辑不改。"""
from __future__ import annotations
from ..utils.constants import *
from ..config.manager import ConfigManager
from ..core.index_manager import IndexManager
from ..monitors.usn_watcher import UsnFileWatcher
from ..system.tray import TrayManager
from ..system.hotkey import HotkeyManager

class IndexSearchWorker(QThread):
    """索引搜索工作线程"""

    batch_ready = Signal(list)
    finished = Signal(float)
    error = Signal(str)

    def __init__(self, index_mgr, keyword, scope_targets, regex_mode, fuzzy_mode):
        super().__init__()
        self.index_mgr = index_mgr
        self.keyword_str = keyword
        self.keywords = keyword  # ✅ 改这里：不要 .lower().split()
        self.scope_targets = scope_targets
        self.regex_mode = regex_mode
        self.fuzzy_mode = fuzzy_mode
        self.stopped = False

    def stop(self):
        self.stopped = True

    def _match(self, filename):
        """匹配文件名"""
        if self.regex_mode:
            try:
                return re.search(self.keyword_str, filename, re.IGNORECASE)
            except re.error:
                return False
        
        # ✅ 改这里：过滤掉语法关键词（包含冒号的）
        keywords = [kw for kw in self.keyword_str.lower().split() if ':' not in kw]
        
        if not keywords:
            return True
        
        if self.fuzzy_mode:
            return all(fuzzy_match(kw, filename) >= 50 for kw in keywords)
        return all(kw in filename.lower() for kw in keywords)

    def run(self):
        """运行搜索"""
        start_time = time.time()
        try:
            results = self.index_mgr.search(self.keywords, self.scope_targets)
            if results is None:
                self.error.emit("索引不可用或搜索失败")
                return

            batch = []
            for fn, fp, sz, mt, is_dir in results:
                if self.stopped:
                    return

                if not self._match(fn):
                    continue

                ext = os.path.splitext(fn)[1].lower()
                tc = 0 if is_dir else (1 if ext in ARCHIVE_EXTS else 2)
                batch.append(
                    {
                        "filename": fn,
                        "fullpath": fp,
                        "dir_path": os.path.dirname(fp),
                        "size": sz,
                        "mtime": mt,
                        "type_code": tc,
                        "size_str": (
                            "📂 文件夹"
                            if tc == 0
                            else ("📦 压缩包" if tc == 1 else format_size(sz))
                        ),
                        "mtime_str": format_time(mt),
                    }
                )
                if len(batch) >= 200:
                    self.batch_ready.emit(list(batch))
                    batch.clear()

            if batch:
                self.batch_ready.emit(batch)
            self.finished.emit(time.time() - start_time)
        except Exception as e:
            logger.error(f"索引搜索线程错误: {e}")
            self.error.emit(str(e))
