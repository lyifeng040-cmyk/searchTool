"""HotkeyManager：从原版提取，逻辑不改。"""
from __future__ import annotations
from ..utils.constants import *

class HotkeyManager(QObject):
    """全局热键管理器"""

    show_mini_signal = Signal()
    show_main_signal = Signal()

    HOTKEY_MINI = 1
    HOTKEY_MAIN = 2

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.registered = False
        self.thread = None
        self.stop_flag = False

        self.show_mini_signal.connect(self._on_hotkey_mini)
        self.show_main_signal.connect(self._on_hotkey_main)

    def start(self):
        """启动热键监听"""
        if not IS_WINDOWS:
            logger.warning("全局热键仅支持Windows系统")
            return False

        if self.registered:
            return True

        self.stop_flag = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def _run(self):
        """热键监听线程"""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)

            RegisterHotKey = user32.RegisterHotKey
            RegisterHotKey.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                wintypes.UINT,
                wintypes.UINT,
            ]
            RegisterHotKey.restype = wintypes.BOOL

            UnregisterHotKey = user32.UnregisterHotKey
            UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            UnregisterHotKey.restype = wintypes.BOOL

            PeekMessageW = user32.PeekMessageW
            PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            PeekMessageW.restype = wintypes.BOOL

            MOD_CONTROL = 0x0002
            MOD_SHIFT = 0x0004
            VK_SPACE = 0x20
            VK_TAB = 0x09
            WM_HOTKEY = 0x0312
            PM_REMOVE = 0x0001

            if not RegisterHotKey(
                None, self.HOTKEY_MINI, MOD_CONTROL | MOD_SHIFT, VK_SPACE
            ):
                logger.error("注册迷你窗口热键失败")
            else:
                logger.info("⌨️ 热键已注册: Ctrl+Shift+Space → 迷你窗口")

            if not RegisterHotKey(
                None, self.HOTKEY_MAIN, MOD_CONTROL | MOD_SHIFT, VK_TAB
            ):
                logger.error("注册主窗口热键失败")
            else:
                logger.info("⌨️ 热键已注册: Ctrl+Shift+Tab → 主窗口")

            self.registered = True

            msg = wintypes.MSG()
            while not self.stop_flag:
                if PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_HOTKEY:
                        if msg.wParam == self.HOTKEY_MINI:
                            self.show_mini_signal.emit()
                        elif msg.wParam == self.HOTKEY_MAIN:
                            self.show_main_signal.emit()
                else:
                    for _ in range(5):
                        if self.stop_flag:
                            break
                        time.sleep(0.02)

            UnregisterHotKey(None, self.HOTKEY_MINI)
            UnregisterHotKey(None, self.HOTKEY_MAIN)
            self.registered = False
            logger.info("⌨️ 全局热键已注销")

        except Exception as e:
            logger.error(f"热键监听错误: {e}")
            self.registered = False

    def _on_hotkey_mini(self):
        """处理迷你窗口热键"""
        logger.info("⌨️ 热键触发: 迷你窗口")
        if hasattr(self.app, "mini_search") and self.app.mini_search:
            self.app.mini_search.show()

    def _on_hotkey_main(self):
        """处理主窗口热键"""
        logger.info("⌨️ 热键触发: 主窗口")
        try:
            self.app.show()
            self.app.showNormal()
            self.app.raise_()
            self.app.activateWindow()
            self.app.entry_kw.setFocus()
            self.app.entry_kw.selectAll()
        except Exception as e:
            logger.error(f"显示主窗口失败: {e}")

    def stop(self):
        """停止热键监听"""
        self.stop_flag = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.registered = False
