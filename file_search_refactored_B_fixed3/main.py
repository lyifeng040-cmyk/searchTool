#!/usr/bin/env python3
# 模块化入口：使用包内相对导入，要求用 -m 启动
import os
os.environ["QT_LOGGING_RULES"] = "*.debug=false;*.warning=false"

from .utils.constants import *
from .utils.helpers import *
from .ui.themes import apply_theme
from .ui.main_window import SearchApp
from .config.manager import ConfigManager
from .core.index_manager import IndexManager

def main():
    """主函数"""
    logger.info("🚀 极速文件搜索 V42 增强版 - PySide6 UI")

    if IS_WINDOWS:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception as e:
            logger.warning(f"设置DPI失败: {e}")

    app = QApplication(sys.argv)
    app.setApplicationName("极速文件搜索")
    app.setOrganizationName("FileSearch")
    app.setQuitOnLastWindowClosed(False)

    config = ConfigManager()
    apply_theme(app, config.get_theme())

    win = SearchApp()
    win.show()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()
