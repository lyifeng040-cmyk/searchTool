from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
    QLineEdit,
    QWidget,
    QFrame,
    QStatusBar,
    QProgressBar,
    QTreeWidget,
    QHeaderView,
    QAbstractItemView,
    QTextEdit,
)
from PySide6.QtGui import QFont, QShortcut
from PySide6.QtCore import QTimer, Qt

import html
import re
import sys


def build_menubar(main):
    menubar = main.menuBar()

    file_menu = menubar.addMenu("文件(&F)")
    file_menu.addAction("📤 导出结果", main.export_results, QKeySequence("Ctrl+E"))
    file_menu.addSeparator()
    # 保留 Enter 给搜索，避免重复快捷键冲突
    file_menu.addAction("📂 打开文件", main.open_file)
    file_menu.addAction("🎯 定位文件", main.open_folder, QKeySequence("Ctrl+L"))
    file_menu.addSeparator()
    file_menu.addAction("🚪 退出", main._do_quit, QKeySequence("Alt+F4"))

    edit_menu = menubar.addMenu("编辑(&E)")
    edit_menu.addAction("✅ 全选", main.select_all, QKeySequence("Ctrl+A"))
    edit_menu.addSeparator()
    edit_menu.addAction("📋 复制路径", main.copy_path, QKeySequence("Ctrl+C"))
    edit_menu.addAction("📄 复制文件", main.copy_file, QKeySequence("Ctrl+Shift+C"))
    edit_menu.addSeparator()
    edit_menu.addAction("🗑️ 删除", main.delete_file, QKeySequence("Delete"))

    search_menu = menubar.addMenu("搜索(&S)")
    search_menu.addAction("🔍 开始搜索", main.start_search, QKeySequence("Return"))
    search_menu.addAction("🔄 刷新搜索", main.refresh_search, QKeySequence("F5"))
    search_menu.addAction("⏹ 停止搜索", main.stop_search, QKeySequence("Escape"))

    tool_menu = menubar.addMenu("工具(&T)")
    tool_menu.addAction("📊 大文件扫描", main.scan_large_files, QKeySequence("Ctrl+G"))
    tool_menu.addAction("✏ 批量重命名", main._show_batch_rename)
    tool_menu.addSeparator()
    tool_menu.addAction("🔧 索引管理", main._show_index_mgr)
    tool_menu.addAction("🔄 重建索引", main._build_index)
    tool_menu.addSeparator()
    tool_menu.addAction("⚙️ 设置", main._show_settings)

    main.fav_menu = menubar.addMenu("收藏(&B)")
    main._update_favorites_menu()

    help_menu = menubar.addMenu("帮助(&H)")
    help_menu.addAction("⌨️ 快捷键列表", main._show_shortcuts)
    help_menu.addSeparator()
    help_menu.addAction("ℹ️ 关于", main._show_about)


def build_ui(main):
    # Recreate the full original _build_ui implementation but operating on `main`.
    # This mirrors the original layout in main_window._build_ui to allow full
    # migration out of the large file.
    central = QWidget()
    main.setCentralWidget(central)
    root_layout = QVBoxLayout(central)
    root_layout.setContentsMargins(10, 10, 10, 10)
    root_layout.setSpacing(8)

    header = QFrame()
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setSpacing(8)

    row0 = QHBoxLayout()
    title_lbl = QLabel("⚡ 极速搜 V42")
    title_lbl.setFont(QFont("微软雅黑", 18, QFont.Bold))
    title_lbl.setStyleSheet("color: #4CAF50;")
    row0.addWidget(title_lbl)

    sub_lbl = QLabel("🎯 增强版")
    sub_lbl.setFont(QFont("微软雅黑", 10))
    sub_lbl.setStyleSheet("color: #FF9800;")
    row0.addWidget(sub_lbl)

    main.idx_lbl = QLabel("检查中...")
    main.idx_lbl.setFont(QFont("微软雅黑", 9))
    row0.addWidget(main.idx_lbl)
    row0.addStretch()

    btn_index_mgr = QPushButton("🔧 索引管理")
    btn_index_mgr.setFixedWidth(100)
    btn_index_mgr.clicked.connect(main._show_index_mgr)
    row0.addWidget(btn_index_mgr)

    btn_export = QPushButton("📤 导出")
    btn_export.setFixedWidth(70)
    btn_export.clicked.connect(main.export_results)
    row0.addWidget(btn_export)

    btn_big = QPushButton("📊 大文件")
    btn_big.setFixedWidth(80)
    btn_big.clicked.connect(main.scan_large_files)
    row0.addWidget(btn_big)

    theme_label = QLabel("主题:")
    theme_label.setFont(QFont("微软雅黑", 9))
    row0.addWidget(theme_label)

    main.combo_theme = QComboBox()
    main.combo_theme.addItems(["light", "dark"])
    main.combo_theme.setCurrentText(main.config_mgr.get_theme())
    main.combo_theme.currentTextChanged.connect(main._on_theme_change)
    main.combo_theme.setFixedWidth(80)
    row0.addWidget(main.combo_theme)

    btn_c_drive = QPushButton("📂 C盘目录")
    btn_c_drive.setFixedWidth(90)
    btn_c_drive.clicked.connect(main._show_c_drive_settings)
    row0.addWidget(btn_c_drive)

    btn_batch = QPushButton("✏ 批量重命名")
    btn_batch.setFixedWidth(100)
    btn_batch.clicked.connect(main._show_batch_rename)
    row0.addWidget(btn_batch)

    btn_refresh_idx = QPushButton("🔄 立即同步")
    btn_refresh_idx.setFixedWidth(90)
    btn_refresh_idx.clicked.connect(main.sync_now)
    row0.addWidget(btn_refresh_idx)

    header_layout.addLayout(row0)

    row1 = QHBoxLayout()

    main.combo_fav = QComboBox()
    main._update_fav_combo()
    main.combo_fav.setFixedWidth(110)
    main.combo_fav.currentIndexChanged.connect(main._on_fav_combo_select)
    row1.addWidget(main.combo_fav)

    main.combo_scope = QComboBox()
    main._update_drives()
    main.combo_scope.setFixedWidth(180)
    main.combo_scope.currentIndexChanged.connect(main._on_scope_change)
    row1.addWidget(main.combo_scope)

    btn_browse = QPushButton("📂 选择目录")
    btn_browse.setFixedWidth(90)
    btn_browse.clicked.connect(main._browse)
    row1.addWidget(btn_browse)

    main.entry_kw = QLineEdit()
    main.entry_kw.setFont(QFont("微软雅黑", 12))
    main.entry_kw.setPlaceholderText("请输入搜索关键词...")
    main.entry_kw.returnPressed.connect(main.start_search)
    row1.addWidget(main.entry_kw, 1)

    main.chk_fuzzy = QCheckBox("模糊")
    main.chk_fuzzy.setChecked(main.fuzzy_var)
    main.chk_fuzzy.stateChanged.connect(lambda s: setattr(main, "fuzzy_var", bool(s)))
    row1.addWidget(main.chk_fuzzy)

    main.chk_regex = QCheckBox("正则")
    main.chk_regex.setChecked(main.regex_var)
    main.chk_regex.stateChanged.connect(lambda s: setattr(main, "regex_var", bool(s)))
    row1.addWidget(main.chk_regex)

    main.chk_realtime = QCheckBox("实时")
    main.chk_realtime.setChecked(main.force_realtime)
    main.chk_realtime.stateChanged.connect(lambda s: setattr(main, "force_realtime", bool(s)))
    row1.addWidget(main.chk_realtime)

    main.btn_search = QPushButton("🚀 搜索")
    main.btn_search.setFixedWidth(90)
    main.btn_search.clicked.connect(main.start_search)
    row1.addWidget(main.btn_search)

    main.btn_refresh = QPushButton("🔄 刷新")
    main.btn_refresh.setFixedWidth(80)
    main.btn_refresh.clicked.connect(main.refresh_search)
    main.btn_refresh.setEnabled(False)
    row1.addWidget(main.btn_refresh)

    main.btn_pause = QPushButton("⏸ 暂停")
    main.btn_pause.setFixedWidth(80)
    main.btn_pause.clicked.connect(main.toggle_pause)
    main.btn_pause.setEnabled(False)
    row1.addWidget(main.btn_pause)

    main.btn_stop = QPushButton("⏹ 停止")
    main.btn_stop.setFixedWidth(80)
    main.btn_stop.clicked.connect(main.stop_search)
    main.btn_stop.setEnabled(False)
    row1.addWidget(main.btn_stop)

    header_layout.addLayout(row1)

    row2 = QHBoxLayout()
    row2.addWidget(QLabel("筛选:"))

    row2.addWidget(QLabel("格式"))
    main.ext_var = QComboBox()
    main.ext_var.addItem("全部")
    main.ext_var.currentIndexChanged.connect(lambda i: main._apply_filter())
    main.ext_var.setFixedWidth(150)
    row2.addWidget(main.ext_var)

    row2.addWidget(QLabel("大小"))
    main.size_var = QComboBox()
    main.size_var.addItems(["不限", ">1MB", ">10MB", ">100MB", ">500MB", ">1GB"])
    main.size_var.currentIndexChanged.connect(lambda i: main._apply_filter())
    main.size_var.setFixedWidth(100)
    row2.addWidget(main.size_var)

    row2.addWidget(QLabel("时间"))
    main.date_var = QComboBox()
    main.date_var.addItems(["不限", "今天", "3天内", "7天内", "30天内", "今年"])
    main.date_var.currentIndexChanged.connect(lambda i: main._apply_filter())
    main.date_var.setFixedWidth(100)
    row2.addWidget(main.date_var)

    btn_clear_filter = QPushButton("清除")
    btn_clear_filter.setFixedWidth(60)
    btn_clear_filter.clicked.connect(main._clear_filter)
    row2.addWidget(btn_clear_filter)

    row2.addStretch()
    main.lbl_filter = QLabel("")
    main.lbl_filter.setFont(QFont("微软雅黑", 9))
    main.lbl_filter.setStyleSheet("color: #666;")
    row2.addWidget(main.lbl_filter)

    header_layout.addLayout(row2)
    root_layout.addWidget(header)

    body = QFrame()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    body_layout.setSpacing(0)

    main.tree = QTreeWidget()
    main.tree.setColumnCount(4)
    main.tree.setHeaderLabels(["📄 文件名", "📂 所在目录", "📊 大小/类型", "🕒 修改时间"])
    main.tree.setRootIsDecorated(False)
    main.tree.setAlternatingRowColors(True)
    main.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
    main.tree.itemDoubleClicked.connect(main.on_dblclick)
    main.tree.setContextMenuPolicy(Qt.CustomContextMenu)
    main.tree.customContextMenuRequested.connect(main.show_menu)
    main.tree.setStyleSheet(
        """
        QTreeWidget {
            alternate-background-color: #f8f9fa;
            background-color: #ffffff;
        }
        QTreeWidget::item { padding: 2px; }
        QTreeWidget::item:selected { background-color: #0078d4; color: white; }
    """
    )

    header_view = main.tree.header()
    header_view.setSortIndicatorShown(True)
    header_view.setSectionsClickable(True)
    header_view.sectionResized.connect(main._on_section_resized)
    header_view.setStretchLastSection(False)
    # Make middle two columns stretch to occupy central space by default
    header_view.setSectionResizeMode(0, QHeaderView.Interactive)
    header_view.setSectionResizeMode(1, QHeaderView.Stretch)
    header_view.setSectionResizeMode(2, QHeaderView.Stretch)
    header_view.setSectionResizeMode(3, QHeaderView.Interactive)
    header_view.sectionClicked.connect(main.sort_column)
    main._apply_saved_column_widths()

    # If there were no saved widths (tree default small), apply sensible defaults
    try:
        left_w = header_view.sectionSize(0)
        right_w = header_view.sectionSize(3)
        if (not left_w or left_w < 20) and (not right_w or right_w < 20):
            # Use screen width to compute default sizes when available
            try:
                sw = main.screen().availableGeometry().width()
            except Exception:
                sw = 1200
            # set left (filename) and right (time) columns to reasonable defaults
            name_w = int(min(520, max(240, sw * 0.28)))
            time_w = int(min(260, max(160, sw * 0.12)))
            header_view.resizeSection(0, name_w)
            header_view.resizeSection(3, time_w)
    except Exception:
        pass

    # 高亮 delegate（只用于文件名那一列）
    main._main_highlight_delegate = None
    try:
        # MainHighlightDelegate is defined in the main_window module; import it
        mod = sys.modules.get(main.__class__.__module__)
        if mod and hasattr(mod, "MainHighlightDelegate"):
            MHD = getattr(mod, "MainHighlightDelegate")
            main._main_highlight_delegate = MHD(main)
            main.tree.setItemDelegateForColumn(0, main._main_highlight_delegate)
    except Exception:
        pass

    body_layout.addWidget(main.tree)

    pg = QFrame()
    pg_layout = QHBoxLayout(pg)
    pg_layout.setContentsMargins(5, 5, 5, 5)
    pg_layout.setSpacing(5)
    pg_layout.addStretch()

    main.btn_first = QPushButton("⏮")
    main.btn_first.setEnabled(False)
    main.btn_first.clicked.connect(lambda: main.go_page("first"))
    pg_layout.addWidget(main.btn_first)

    main.btn_prev = QPushButton("◀")
    main.btn_prev.setEnabled(False)
    main.btn_prev.clicked.connect(lambda: main.go_page("prev"))
    pg_layout.addWidget(main.btn_prev)

    main.lbl_page = QLabel("第 1/1 页 (0项)")
    main.lbl_page.setFont(QFont("微软雅黑", 9))
    pg_layout.addWidget(main.lbl_page)

    main.btn_next = QPushButton("▶")
    main.btn_next.setEnabled(False)
    main.btn_next.clicked.connect(lambda: main.go_page("next"))
    pg_layout.addWidget(main.btn_next)

    main.btn_last = QPushButton("⏭")
    main.btn_last.setEnabled(False)
    main.btn_last.clicked.connect(lambda: main.go_page("last"))
    pg_layout.addWidget(main.btn_last)

    common_style = (
        """
        QPushButton { border: 1px solid #cbd5e0; border-radius: 7px; background: #ffffff; color: #1a202c; }
        QPushButton:hover { background: #edf2f7; }
        QPushButton:pressed { background: #e2e8f0; }
        QPushButton:disabled { color: #a0aec0; background: #f7fafc; }
    """
    )
    for b in (main.btn_first, main.btn_prev, main.btn_next, main.btn_last):
        b.setFixedHeight(30)
        b.setFont(QFont("微软雅黑", 12, QFont.Bold))
        b.setStyleSheet(common_style)
    main.btn_prev.setFixedWidth(56)
    main.btn_next.setFixedWidth(56)
    main.btn_first.setFixedWidth(44)
    main.btn_last.setFixedWidth(44)

    pg_layout.addStretch()
    body_layout.addWidget(pg)

    root_layout.addWidget(body, 1)

    main.status = QLabel("就绪")
    main.status_path = QLabel("")
    main.status_path.setFont(QFont("Consolas", 8))
    main.status_path.setStyleSheet("color: #718096;")

    main.progress = QProgressBar()
    main.progress.setMaximumWidth(200)
    main.progress.setVisible(False)
    main.progress.setRange(0, 0)

    statusbar = QStatusBar()
    statusbar.addWidget(main.status, 1)
    statusbar.addWidget(main.status_path, 3)
    statusbar.addPermanentWidget(main.progress, 0)
    main.setStatusBar(statusbar)


def bind_shortcuts(main):
    QShortcut(QKeySequence("Ctrl+F"), main, lambda: main.entry_kw.setFocus())
    QShortcut(QKeySequence("Ctrl+A"), main, main.select_all)
    QShortcut(QKeySequence("Ctrl+C"), main, main.copy_path)
    QShortcut(QKeySequence("Ctrl+Shift+C"), main, main.copy_file)
    QShortcut(QKeySequence("Ctrl+E"), main, main.export_results)
    QShortcut(QKeySequence("Ctrl+G"), main, main.scan_large_files)
    QShortcut(QKeySequence("Ctrl+L"), main, main.open_folder)
    QShortcut(QKeySequence("F5"), main, main.refresh_search)
    QShortcut(QKeySequence("Delete"), main, main.delete_file)
    QShortcut(QKeySequence("Escape"), main, lambda: main.stop_search() if main.is_searching else main.entry_kw.clear())
    try:
        main.entry_kw.installEventFilter(main)
    except Exception:
        pass
