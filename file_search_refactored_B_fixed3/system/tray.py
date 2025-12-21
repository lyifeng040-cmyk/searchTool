"""TrayManager：从原版提取，逻辑不改。"""
from __future__ import annotations
from ..utils.constants import *

class TrayManager:
    """系统托盘管理器"""

    def __init__(self, app):
        self.app = app
        self.tray_icon = None
        self.running = False

    def _create_icon_image(self):
        """创建托盘图标"""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#4CAF50"))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(8, 8, 32, 32)
        painter.drawLine(36, 36, 54, 54)
        painter.end()
        return QIcon(pixmap)

    def _create_menu(self):
        """创建托盘菜单"""
        menu = QMenu()

        show_action = QAction("显示主窗口", self.app)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        menu.addSeparator()

        rebuild_action = QAction("重建索引", self.app)
        rebuild_action.triggered.connect(self._rebuild_index)
        menu.addAction(rebuild_action)

        refresh_action = QAction("刷新状态", self.app)
        refresh_action.triggered.connect(self._refresh_status)
        menu.addAction(refresh_action)

        menu.addSeparator()

        quit_action = QAction("退出", self.app)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        return menu

    def _show_window(self):
        self.app.show()
        self.app.showNormal()
        self.app.raise_()
        self.app.activateWindow()
        self.app.entry_kw.setFocus()

    def _rebuild_index(self):
        QTimer.singleShot(0, self.app._build_index)

    def _refresh_status(self):
        QTimer.singleShot(0, self.app.sync_now)

    def _quit(self):
        self.stop()
        QTimer.singleShot(0, self.app._do_quit)

    def start(self):
        """启动托盘"""
        if self.running:
            return True

        try:
            self.tray_icon = QSystemTrayIcon(self.app)
            self.tray_icon.setIcon(self._create_icon_image())
            self.tray_icon.setToolTip("极速文件搜索")
            self.tray_icon.setContextMenu(self._create_menu())
            self.tray_icon.activated.connect(self._on_activated)
            self.tray_icon.show()
            self.running = True
            logger.info("🔔 托盘已启动")
            return True
        except Exception as e:
            logger.error(f"启动托盘失败: {e}")
            return False

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def stop(self):
        """停止托盘"""
        if self.tray_icon and self.running:
            try:
                self.tray_icon.hide()
                self.tray_icon = None
                self.running = False
                logger.info("🔔 托盘已停止")
            except Exception as e:
                logger.error(f"停止托盘失败: {e}")

    def show_notification(self, title, message):
        """显示通知"""
        if self.tray_icon and self.running:
            try:
                self.tray_icon.showMessage(
                    title, message, QSystemTrayIcon.Information, 3000
                )
            except Exception as e:
                logger.debug(f"显示通知失败: {e}")
