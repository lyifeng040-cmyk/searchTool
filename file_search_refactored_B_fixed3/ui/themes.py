"""主题：从原版提取，逻辑不改。"""
from __future__ import annotations
from ..utils.constants import *

def apply_theme(app, theme_name):
    """应用主题到应用程序"""
    if theme_name == "dark":
        app.setStyleSheet(
            """
            QMainWindow, QDialog { background-color: #2d2d2d; color: #ffffff; }
            QTreeWidget { background-color: #3d3d3d; color: #ffffff; alternate-background-color: #454545; }
            QTreeWidget::item:selected { background-color: #0078d4; }
            QLineEdit, QComboBox, QSpinBox { background-color: #3d3d3d; color: #ffffff; border: 1px solid #555; padding: 4px; }
            QPushButton { background-color: #4d4d4d; color: #ffffff; border: 1px solid #666; padding: 5px 10px; }
            QPushButton:hover { background-color: #5d5d5d; }
            QLabel { color: #ffffff; }
            QGroupBox { color: #ffffff; border: 1px solid #555; }
            QCheckBox, QRadioButton { color: #ffffff; }
            QMenu { background-color: #3d3d3d; color: #ffffff; }
            QMenu::item:selected { background-color: #0078d4; }
            QStatusBar { background-color: #2d2d2d; color: #aaaaaa; }
            QHeaderView::section { background-color: #3d3d3d; color: #ffffff; padding: 4px; border: 1px solid #555; }
            QScrollBar { background-color: #2d2d2d; }
        """
        )
    else:
        app.setStyleSheet(
            """
            QMainWindow, QDialog { background-color: #ffffff; }
            QTreeWidget { alternate-background-color: #f8f9fa; }
            QTreeWidget::item:selected { background-color: #0078d4; color: white; }
            QHeaderView::section { background-color: #f0f0f0; padding: 4px; border: 1px solid #dcdcdc; font-weight: bold; }
            QTreeWidget { border: 1px solid #dcdcdc; }
        """
        )
