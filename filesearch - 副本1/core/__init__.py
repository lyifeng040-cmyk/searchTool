"""
极速文件搜索 - 核心模块
"""

from .rust_engine import (
    HAS_RUST_ENGINE,
    RUST_ENGINE,
    load_rust_engine,
    get_rust_engine,
    is_rust_available,
    ScanResult,
    FileInfo,
)

from .dependencies import (
    HAS_WIN32,
    HAS_SEND2TRASH,
    HAS_APSW,
    get_db_module,
)

from .mft_scanner import (
    enum_volume_files_mft,
    MFT_AVAILABLE,
)

from .index_manager import IndexManager
from .file_watcher import UsnFileWatcher
from .search_workers import IndexSearchWorker, RealtimeSearchWorker

__all__ = [
    'HAS_RUST_ENGINE',
    'RUST_ENGINE',
    'load_rust_engine',
    'get_rust_engine',
    'is_rust_available',
    'ScanResult',
    'FileInfo',
    'HAS_WIN32',
    'HAS_SEND2TRASH',
    'HAS_APSW',
    'get_db_module',
    'enum_volume_files_mft',
    'MFT_AVAILABLE',
    'IndexManager',
    'UsnFileWatcher',
    'IndexSearchWorker',
    'RealtimeSearchWorker',
]
