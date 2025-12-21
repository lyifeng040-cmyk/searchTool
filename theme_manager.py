# theme_manager.py
import qdarkstyle
from PySide6.QtWidgets import QApplication

# 🔴 新增：定义我们的自定义表头样式
CUSTOM_STYLE = """
    QHeaderView::section {
        background-color: #f0f0f0;
        padding: 4px;
        border: 1px solid #dcdcdc;
        border-left: none;
        font-weight: bold;
    }
    QHeaderView::section:first {
        border-left: 1px solid #dcdcdc;
    }
    QTreeWidget {
        border: 1px solid #dcdcdc;
    }
"""

def apply_theme(app, theme_name):
    """
    为应用设置亮色或暗色主题，并附加自定义样式。
    """
    if theme_name == 'dark':
        # 加载暗色主题，并拼接上我们的自定义样式
        base_stylesheet = qdarkstyle.load_stylesheet(qt_api='pyside6')
        # 在暗色模式下，我们可以让边框颜色更深一些
        dark_header_style = CUSTOM_STYLE.replace("#dcdcdc", "#444444").replace("#f0f0f0", "#2d2d2d")
        stylesheet = base_stylesheet + dark_header_style
    else:
        # 亮色模式下，只使用我们的自定义样式
        stylesheet = CUSTOM_STYLE
    
    app.setStyleSheet(stylesheet)
    print(f"主题已应用: {theme_name}")
