"""
Rust 搜索引擎包装模块
使用 Rust 实现的高性能搜索索引
"""

import ctypes
import logging
from typing import List, Tuple, Optional

from .rust_engine import (
    get_rust_engine,
    is_rust_available,
    SearchResultFFI,
    SearchItemFFI,
    get_rust_engine as _get_eng_for_meta,
    FileInfo as _FileInfo,
)

logger = logging.getLogger(__name__)

# 默认单盘最大返回条数（适中偏大，后续在 UI 端分页）
MAX_RESULTS_PER_DRIVE = 100000
META_BATCH_COUNT = 1000


class RustSearchEngine:
    """Rust 搜索引擎包装器"""

    def __init__(self):
        self.engine = get_rust_engine()
        self.initialized_drives = set()

    def is_available(self) -> bool:
        """检查 Rust 引擎是否可用"""
        return is_rust_available()

    def init_index(self, drive: str) -> bool:
        """初始化指定驱动器的搜索索引"""
        if not self.is_available():
            logger.warning("Rust 引擎不可用")
            return False

        drive_letter = ord(drive.upper())
        result = self.engine.init_search_index(drive_letter)

        if result == 1:  # Rust 返回 1 表示成功
            self.initialized_drives.add(drive.upper())
            logger.info(f"✅ 初始化 {drive}: 搜索索引成功")
            return True
        else:
            logger.error(f"❌ 初始化 {drive}: 搜索索引失败 (返回值: {result})")
            return False

    def search_contains(
        self, drive: str, keyword: str
    ) -> List[Tuple[str, str, int, bool, float]]:
        """包含搜索（文件名包含 keyword）"""
        if not self.is_available():
            return []

        drive = drive.upper()
        
        # 确保索引已初始化
        if drive not in self.initialized_drives:
            # 先尝试加载已保存的索引
            if not self.load_index(drive):
                # 加载失败，初始化新索引
                logger.info(f"📊 首次使用 Rust 搜索，正在为 {drive}: 盘建立索引...")
                if not self.init_index(drive):
                    logger.error(f"❌ 无法初始化 {drive}: 索引")
                    return []

        drive_letter = ord(drive)
        keyword_bytes = (keyword or "").lower().encode("utf-8")

        # 第3个参数是最大返回条数，而不是关键字长度
        result_ptr = self.engine.search_contains(
            drive_letter, ctypes.c_char_p(keyword_bytes), MAX_RESULTS_PER_DRIVE
        )

        return self._parse_search_result(result_ptr)

    def search_prefix(self, drive: str, prefix: str, max_results: int) -> List[Tuple[str, str, int, bool, float]]:
        if not self.is_available():
            return []
        drive = drive.upper()
        if drive not in self.initialized_drives:
            if not self.load_index(drive):
                if not self.init_index(drive):
                    return []
        drive_letter = ord(drive)
        prefix_bytes = (prefix or "").lower().encode("utf-8")
        result_ptr = self.engine.search_prefix(
            drive_letter, ctypes.c_char_p(prefix_bytes), max_results
        )
        return self._parse_search_result(result_ptr)

    def search_by_ext(self, drive: str, ext: str, max_results: int) -> List[Tuple[str, str, int, bool, float]]:
        if not self.is_available():
            return []
        drive = drive.upper()
        if drive not in self.initialized_drives:
            if not self.load_index(drive):
                if not self.init_index(drive):
                    return []
        drive_letter = ord(drive)
        ext_bytes = (ext or "").lower().encode("utf-8")
        result_ptr = self.engine.search_by_ext(
            drive_letter, ctypes.c_char_p(ext_bytes), max_results
        )
        return self._parse_search_result(result_ptr)

    def search_by_mtime_range(self, drive: str, min_mtime: float, max_mtime: float, max_results: int) -> List[Tuple[str, str, int, bool, float]]:
        """按修改时间范围搜索（从 Rust 端直接过滤，避免 Python 端大批量过滤卡顿）"""
        if not self.is_available():
            return []
        drive = drive.upper()
        if drive not in self.initialized_drives:
            if not self.load_index(drive):
                if not self.init_index(drive):
                    return []
        drive_letter = ord(drive)
        result_ptr = self.engine.search_by_mtime_range(
            drive_letter, float(min_mtime), float(max_mtime), max_mtime and max_results or max_results
        )
        return self._parse_search_result(result_ptr)

    def apply_filters_to_results(
        self,
        results: List[Tuple[str, str, int, bool, float]],
        filters: dict,
    ) -> List[Tuple[str, str, int, bool, float]]:
        if not results:
            return []
        if not filters or not any(filters.values()):
            return results

        from .search_syntax import SearchSyntaxParser

        parser = SearchSyntaxParser()
        parser.filters = filters

        # Rust 索引现在已包含完整元数据，无需再补全
        dict_results = [
            {
                "filename": r[0],
                "fullpath": r[1],
                "size": r[2],
                "is_dir": r[3],
                "mtime": r[4],
            }
            for r in results
        ]
        filtered = parser.apply_filters(dict_results)
        return [
            (r["filename"], r["fullpath"], r["size"], r["is_dir"], r["mtime"])
            for r in filtered
        ]
    
    def search_with_filters(
        self, drive: str, keyword: str, filters: dict
    ) -> Optional[List[Tuple[str, str, int, bool, float]]]:
        """带过滤条件的搜索"""
        # 先进行 Rust 搜索（对空关键词走受限路径，避免一次性取全导致卡顿）
        if (not keyword) and filters and any(filters.values()):
            results: List[Tuple[str, str, int, bool, float]] = []
            # 优先使用 Rust 端时间范围过滤避免前缀枚举
            date_after = None
            try:
                if isinstance(filters, dict) and filters.get("date_after"):
                    da = filters["date_after"]
                    # 支持 datetime 或时间戳
                    import datetime as _dt
                    if isinstance(da, _dt.datetime):
                        date_after = da.timestamp()
                    elif isinstance(da, (int, float)):
                        date_after = float(da)
            except Exception:
                date_after = None

            if date_after is not None and not keyword:
                # 直接使用 Rust 索引按时间过滤
                # 上限给到较大的值，避免遗漏；后续在 Python 层再应用其他过滤（如 ext/size/path）
                cap = 100000
                results = self.search_by_mtime_range(drive, date_after, 4.611686e18, cap)
            else:
                # 回退逻辑：保留原有扩展名或前缀枚举路径
                exts = (filters.get("ext") or []) if isinstance(filters, dict) else []
                if exts:
                    cap_per_ext = 20000
                    for ext in exts:
                        ext_bytes = (ext or "").lower().encode("utf-8")
                        drive_letter = ord(drive.upper())
                        ptr = self.engine.search_by_ext(
                            drive_letter, ctypes.c_char_p(ext_bytes), cap_per_ext
                        )
                        results.extend(self._parse_search_result(ptr))
                else:
                    prefixes = [
                        "a","b","c","d","e","f","g","h","i","j","k","l","m",
                        "n","o","p","q","r","s","t","u","v","w","x","y","z",
                        "0","1","2","3","4","5","6","7","8","9","_"
                    ]
                    cap_per_prefix = 8000
                    target_cap = 50000
                    for pref in prefixes:
                        part = self.search_prefix(drive, pref, cap_per_prefix)
                        if part:
                            results.extend(part)
                        if len(results) >= target_cap:
                            break
        else:
            results = self.search_contains(drive, keyword)
        if results is None:
            return None
        
        # 应用过滤条件
        if not filters or not any(filters.values()):
            return results
        
        from .search_syntax import SearchSyntaxParser
        parser = SearchSyntaxParser()
        parser.filters = filters
        
        # Rust 索引现在已包含完整元数据，无需再补全
        # 转换为字典格式应用过滤
        dict_results = [
            {
                "filename": r[0],
                "fullpath": r[1],
                "size": r[2],
                "is_dir": r[3],
                "mtime": r[4],
            }
            for r in results
        ]
        
        filtered = parser.apply_filters(dict_results)
        
        # 转换回元组格式
        return [
            (r["filename"], r["fullpath"], r["size"], r["is_dir"], r["mtime"])
            for r in filtered
        ]

    def _fill_metadata_batch(self, items: List[Tuple[str, str, int, bool, float]]
                             ) -> List[Tuple[str, str, int, bool, float]]:
        """使用 Rust 的 get_file_info_batch 批量补齐 size/mtime"""
        if not items:
            return items

        try:
            eng = _get_eng_for_meta()
            if not eng:
                return items

            # 仅对 size/mtime 为 0 的项补齐，避免浪费；并且分批处理避免超大 buffer/ctypes 构造卡死
            need_idx: List[int] = []
            need_paths: List[str] = []
            for idx, (_name, path, sz, _is_dir, mt) in enumerate(items):
                if sz == 0 or mt == 0.0:
                    need_idx.append(idx)
                    need_paths.append(path)

            if not need_paths:
                return items

            mutable = list(items)

            for start in range(0, len(need_paths), META_BATCH_COUNT):
                batch_paths = need_paths[start : start + META_BATCH_COUNT]
                batch_idx = need_idx[start : start + META_BATCH_COUNT]

                joined = ("\0".join(batch_paths) + "\0").encode("utf-8")
                buf = ctypes.create_string_buffer(joined)
                ptr_u8 = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8))

                FileInfoArray = _FileInfo * len(batch_paths)
                out = FileInfoArray()

                count = eng.get_file_info_batch(
                    ptr_u8,
                    ctypes.c_size_t(len(joined)),
                    out,
                    ctypes.c_size_t(len(batch_paths)),
                )
                count = int(count)
                if count <= 0:
                    continue

                for j in range(min(count, len(batch_idx))):
                    orig_i = batch_idx[j]
                    name, path, sz, is_dir, mt = mutable[orig_i]
                    info = out[j]
                    mutable[orig_i] = (name, path, int(info.size), is_dir, float(info.mtime))

            return mutable
        except Exception:
            return items

    def _parse_search_result(
        self, result_ptr
    ) -> List[Tuple[str, str, int, bool, float]]:
        """解析 Rust 返回的搜索结果"""
        if not result_ptr:
            return []

        try:
            result = result_ptr.contents
            items = []

            for i in range(result.count):
                item = result.items[i]

                # 解析文件名
                name_bytes = ctypes.string_at(item.name_ptr, item.name_len)
                name = name_bytes.decode("utf-8", errors="replace")

                # 解析完整路径
                path_bytes = ctypes.string_at(item.path_ptr, item.path_len)
                path = path_bytes.decode("utf-8", errors="replace")

                items.append(
                    (
                        name,  # filename
                        path,  # fullpath
                        item.size,  # size
                        bool(item.is_dir),  # is_dir
                        item.mtime,  # mtime
                    )
                )

            return items
        finally:
            # 释放 Rust 分配的内存
            if result_ptr:
                self.engine.free_search_result(result_ptr)

    def load_index(self, drive: str) -> bool:
        """从磁盘加载搜索索引"""
        if not self.is_available():
            return False

        drive_letter = ord(drive.upper())
        result = self.engine.load_search_index(drive_letter)

        if result == 1:  # Rust 返回 1 表示成功
            self.initialized_drives.add(drive.upper())
            logger.info(f"✅ 加载 {drive}: 搜索索引成功")
            return True
        else:
            logger.warning(f"⚠️ 加载 {drive}: 搜索索引失败 (返回值: {result})")
            return False


# 全局单例
_rust_search_engine = None


def get_rust_search_engine() -> Optional[RustSearchEngine]:
    """获取 Rust 搜索引擎全局实例"""
    global _rust_search_engine
    if _rust_search_engine is None:
        _rust_search_engine = RustSearchEngine()
    return _rust_search_engine if _rust_search_engine.is_available() else None
