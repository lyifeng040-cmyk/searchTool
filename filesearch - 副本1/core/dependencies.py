"""
Runtime dependency probes extracted from legacy implementation.
"""

import logging

logger = logging.getLogger(__name__)

try:
    import win32clipboard  # noqa: F401
    import win32con  # noqa: F401

    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    logger.warning("pywin32 未安装，部分功能不可用")

try:
    import send2trash  # noqa: F401

    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False
    logger.warning("send2trash 未安装，删除将直接删除而非进入回收站")

try:
    import apsw  # type: ignore

    HAS_APSW = True

    def get_db_module():
        return apsw
except ImportError:
    HAS_APSW = False
    import sqlite3

    logger.warning("apsw 未安装，使用 sqlite3")

    def get_db_module():
        return sqlite3


__all__ = [
    "HAS_WIN32",
    "HAS_SEND2TRASH",
    "HAS_APSW",
    "get_db_module",
]
