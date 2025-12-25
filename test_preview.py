#!/usr/bin/env python3
"""Test the preview tooltip functionality"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication, QMainWindow, QListWidget, QListWidgetItem, QVBoxLayout, QWidget, QLabel
from PySide6.QtCore import Qt, QEvent
from filesearch.utils import format_size, format_time
import time

class TestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Test Preview")
        self.setGeometry(100, 100, 600, 400)
        
        # Create a test list
        self.list_widget = QListWidget()
        self.list_widget.setMouseTracking(True)
        self.list_widget.installEventFilter(self)
        
        # Add some test items
        for i in range(10):
            item = QListWidgetItem(f"Test File {i}.txt")
            self.list_widget.addItem(item)
        
        # Setup layout
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Hover over items to see preview:"))
        layout.addWidget(self.list_widget)
        
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        
        self._last_hovered_row = -1
    
    def eventFilter(self, obj, event):
        if obj == self.list_widget and event.type() == QEvent.MouseMove:
            try:
                item = self.list_widget.itemAt(event.pos())
                if item:
                    row = self.list_widget.row(item)
                    if row != self._last_hovered_row:
                        self._last_hovered_row = row
                        print(f"Hovering over row {row}: {item.text()}")
                        
                        # Test the tooltip
                        from PySide6.QtWidgets import QToolTip
                        preview = f"Row: {row}\nText: {item.text()}\nSize: 4.5 KB\nMtime: 2025-12-25"
                        global_pos = event.globalPos()
                        QToolTip.showText(global_pos, preview, self.list_widget)
            except Exception as e:
                print(f"Error in eventFilter: {e}")
            return False
        return super().eventFilter(obj, event)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = TestWindow()
    window.show()
    sys.exit(app.exec())
