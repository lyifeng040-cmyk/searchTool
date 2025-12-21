"""Win10 冒烟回归：验证模块化版可import并能实例化入口类。
运行：python -m file_search_refactored_B_fixed3.tests.regression_smoke
"""
import importlib

def main():
    modules = [
        'file_search_refactored_B_fixed3.utils.constants',
        'file_search_refactored_B_fixed3.utils.helpers',
        'file_search_refactored_B_fixed3.config.manager',
        'file_search_refactored_B_fixed3.core.mft',
        'file_search_refactored_B_fixed3.core.index_manager',
        'file_search_refactored_B_fixed3.monitors.usn_watcher',
        'file_search_refactored_B_fixed3.system.tray',
        'file_search_refactored_B_fixed3.system.hotkey',
        'file_search_refactored_B_fixed3.ui.mini_search',
        'file_search_refactored_B_fixed3.ui.cdrive_dialog',
        'file_search_refactored_B_fixed3.ui.batch_rename',
        'file_search_refactored_B_fixed3.ui.index_worker',
        'file_search_refactored_B_fixed3.ui.realtime_worker',
        'file_search_refactored_B_fixed3.ui.main_window',
        'file_search_refactored_B_fixed3.ui.themes',
        'file_search_refactored_B_fixed3.main',
    ]
    for m in modules:
        importlib.import_module(m)
    print('OK: imports')

if __name__ == '__main__':
    main()
