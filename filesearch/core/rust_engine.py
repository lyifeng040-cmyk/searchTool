"""
Rust engine loader extracted from legacy implementation.
"""

import ctypes
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class ScanResult(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.POINTER(ctypes.c_uint8)),
        ("data_len", ctypes.c_size_t),
        ("count", ctypes.c_size_t),
    ]


class FileInfo(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint64),
        ("mtime", ctypes.c_double),
        ("exists", ctypes.c_uint8),
    ]


class SearchItemFFI(ctypes.Structure):
    _fields_ = [
        ("name_ptr", ctypes.POINTER(ctypes.c_uint8)),
        ("name_len", ctypes.c_size_t),
        ("path_ptr", ctypes.POINTER(ctypes.c_uint8)),
        ("path_len", ctypes.c_size_t),
        ("size", ctypes.c_uint64),
        ("is_dir", ctypes.c_uint8),
        ("mtime", ctypes.c_double),
    ]


class SearchResultFFI(ctypes.Structure):
    _fields_ = [
        ("items", ctypes.POINTER(SearchItemFFI)),
        ("count", ctypes.c_size_t),
    ]


HAS_RUST_ENGINE = False
RUST_ENGINE = None


def _configure_engine(eng):
    """Wire up ctypes signatures."""
    eng.scan_drive_packed.argtypes = [ctypes.c_uint16]
    eng.scan_drive_packed.restype = ScanResult
    eng.free_scan_result.argtypes = [ScanResult]
    eng.free_scan_result.restype = None

    eng.save_dir_cache.argtypes = [ctypes.c_uint16, ctypes.c_char_p, ctypes.c_size_t]
    eng.save_dir_cache.restype = ctypes.c_int32

    eng.load_dir_cache.argtypes = [ctypes.c_uint16, ctypes.c_char_p, ctypes.c_size_t]
    eng.load_dir_cache.restype = ctypes.c_int32

    eng.get_engine_version.argtypes = []
    eng.get_engine_version.restype = ctypes.c_uint32

    eng.get_file_info.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
    eng.get_file_info.restype = FileInfo

    eng.get_file_info_batch.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(FileInfo),
        ctypes.c_size_t,
    ]
    eng.get_file_info_batch.restype = ctypes.c_size_t

    # 搜索索引函数
    eng.init_search_index.argtypes = [ctypes.c_uint16]
    eng.init_search_index.restype = ctypes.c_int32

    eng.search_prefix.argtypes = [ctypes.c_uint16, ctypes.c_char_p, ctypes.c_size_t]
    eng.search_prefix.restype = ctypes.POINTER(SearchResultFFI)

    eng.search_contains.argtypes = [ctypes.c_uint16, ctypes.c_char_p, ctypes.c_size_t]
    eng.search_contains.restype = ctypes.POINTER(SearchResultFFI)

    eng.search_by_ext.argtypes = [ctypes.c_uint16, ctypes.c_char_p, ctypes.c_size_t]
    eng.search_by_ext.restype = ctypes.POINTER(SearchResultFFI)

    # 新增：按修改时间范围搜索
    try:
        eng.search_by_mtime_range.argtypes = [
            ctypes.c_uint16,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_size_t,
        ]
        eng.search_by_mtime_range.restype = ctypes.POINTER(SearchResultFFI)
    except AttributeError:
        # 旧版 DLL 不包含该函数，保持兼容
        pass

    eng.free_search_result.argtypes = [ctypes.POINTER(SearchResultFFI)]
    eng.free_search_result.restype = None

    eng.save_search_index.argtypes = [ctypes.c_uint16]
    eng.save_search_index.restype = ctypes.c_int32

    eng.load_search_index.argtypes = [ctypes.c_uint16]
    eng.load_search_index.restype = ctypes.c_int32


def load_rust_engine():
    """Attempt to load the Rust DLL; returns the engine or None."""
    global HAS_RUST_ENGINE, RUST_ENGINE

    if HAS_RUST_ENGINE and RUST_ENGINE is not None:
        return RUST_ENGINE

    possible_paths = [
        Path(__file__).parent / "file_scanner_engine.dll",
        Path.cwd() / "file_scanner_engine.dll",
    ]

    dll_path = None
    for p in possible_paths:
        if p.exists():
            dll_path = p
            break

    if not dll_path:
        logger.warning("⚠️ 未找到 file_scanner_engine.dll")
        HAS_RUST_ENGINE = False
        return None

    try:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(dll_path.parent.resolve()))

        eng = ctypes.CDLL(str(dll_path))
        _configure_engine(eng)

        RUST_ENGINE = eng
        HAS_RUST_ENGINE = True
        logger.info(f"✅ Rust 核心引擎加载成功: {dll_path}")
        return eng
    except Exception as e:
        logger.warning(f"⚠️ Rust 引擎加载失败: {e}")
        HAS_RUST_ENGINE = False
        RUST_ENGINE = None
        return None


def get_rust_engine():
    """Get a loaded engine, attempting lazy load if needed."""
    if RUST_ENGINE is None or not HAS_RUST_ENGINE:
        return load_rust_engine()
    return RUST_ENGINE


def is_rust_available():
    """Check whether Rust engine is available."""
    return HAS_RUST_ENGINE and RUST_ENGINE is not None


# Eager load to match legacy behavior
load_rust_engine()

__all__ = [
    "HAS_RUST_ENGINE",
    "RUST_ENGINE",
    "load_rust_engine",
    "get_rust_engine",
    "is_rust_available",
    "ScanResult",
    "FileInfo",
    "SearchItemFFI",
    "SearchResultFFI",
]
