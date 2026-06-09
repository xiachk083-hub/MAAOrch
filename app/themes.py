A="#326cf3"
G="#2d5a3d"  # bamboo green
BTN_DELETE='QPushButton{{background:transparent;color:#888;border:none}}QPushButton:hover{{background:#326cf3;color:#fff;border-radius:{r}px}}'
DARK_STYLE=f"""QMainWindow,QDialog{{background:#1c1c1c;color:#e6e6e6}}QLabel{{color:#e6e6e6}}
QGroupBox{{color:#e6e6e6;border:1px solid #2b2b30;border-radius:6px;margin-top:10px;padding-top:10px}}
QPushButton{{background:#2b2b30;color:#ccc;border:1px solid #3a3a3a;border-radius:6px;padding:5px 16px;min-height:28px}}
QPushButton:hover{{background:#333;border-color:#555}}QPushButton:pressed{{background:#252525}}
QPushButton:disabled{{background:transparent;color:#555;border-color:#2b2b30}}
QPushButton#startBtn{{background:{A};color:#fff;border-color:{A};font-weight:bold;min-height:28px}}QPushButton#startBtn:hover{{background:#4070f5}}
QPushButton#stopBtn{{background:#5d2626;color:#d0a0a0;border-color:#7a2b2b}}QPushButton#stopBtn:hover{{background:#8e0000;color:#fff}}
QPushButton#addProgBtn{{background:{A};color:#fff;font-weight:bold;border-color:{A};min-height:28px}}
QPushButton#tabBtn{{background:transparent;color:#666;border:none;padding:6px 14px;font-size:11px;border-radius:0}}QPushButton#tabBtn:hover{{color:#aaa;border-bottom:2px solid #3a3a3a}}
QPushButton#tabBtnActive{{background:transparent;color:#e6e6e6;border:none;padding:6px 14px;font-size:11px;border-bottom:2px solid {A};border-radius:0;font-weight:bold}}
QPushButton#iconBtn{{background:transparent;color:#888;border:none;border-radius:6px;padding:3px 6px;font-size:11pt}}QPushButton#iconBtn:hover{{background:#2b2b30;color:#e6e6e6}}
QFrame#sideBar QLabel{{color:#888;font-size:9pt;padding:5px 8px;border-radius:6px}}QFrame#sideBar QLabel:hover{{background:#2b2b30;color:#e6e6e6}}
QLineEdit,QSpinBox,QComboBox{{background:#2b2b30;color:#e6e6e6;border:1px solid #3a3a3a;border-radius:5px;padding:6px 10px;min-height:24px}}
QLineEdit:hover,QSpinBox:hover,QComboBox:hover{{border:1px solid #555}}
QLineEdit:focus,QSpinBox:focus{{border:1px solid {A};background:#25252a}}
QComboBox::drop-down{{background:#2b2b30;border:none}}QComboBox QAbstractItemView{{background:#2b2b30;color:#e6e6e6;selection-background-color:{A};border:1px solid #3a3a3a;border-radius:4px}}
QTableWidget{{background:transparent;color:#ccc;border:1px solid #2b2b30;alternate-background-color:#1e1e20}}
QTableWidget::item{{padding:4px 8px;border:none}}QTableWidget::item:selected{{background:{A}30;color:#e6e6e6}}
QHeaderView::section{{background:transparent;color:#666;border:none;border-bottom:1px solid #2b2b30;padding:6px 10px;font-weight:bold;font-size:10px}}
QPlainTextEdit{{background:#1c1c1c;color:#ccc;border:1px solid #2b2b30;font-family:Consolas;font-size:12px;border-radius:4px;padding:6px}}
QMenu{{background:#2b2b30;color:#ccc;border:1px solid #3a3a3a;border-radius:6px;padding:4px}}QMenu::item{{padding:6px 28px 6px 12px;border-radius:3px}}QMenu::item:selected{{background:{A};color:#fff}}QMenu::separator{{height:1px;background:#3a3a3a;margin:4px 8px}}
QMenuBar{{background:#1c1c1c;color:#888;border:none;border-bottom:1px solid #2b2b30}}QMenuBar::item{{padding:6px 14px}}QMenuBar::item:selected{{background:#2b2b30;color:#e6e6e6;border-radius:4px}}
QCheckBox{{color:#aaa;spacing:8px}}QCheckBox::indicator{{width:18px;height:18px;border:2px solid #3a3a3a;border-radius:4px;background:#2b2b30}}QCheckBox::indicator:hover{{border-color:#555}}QCheckBox::indicator:checked{{background:{A};border-color:{A}}}
QProgressBar{{border:1px solid #2b2b30;border-radius:4px;background:#2b2b30;color:#aaa;text-align:center;height:18px}}QProgressBar::chunk{{background:{A};border-radius:3px}}
QScrollArea{{border:none;background:transparent}}
QSplitter::handle{{background:#2b2b30;width:3px}}QSplitter::handle:hover{{background:#3a3a3a}}
QListWidget{{background:transparent;color:#ccc;border:1px solid #2b2b30;border-radius:6px}}QListWidget::item{{padding:6px 10px;border-radius:3px}}QListWidget::item:hover{{background:#2b2b30}}QListWidget::item:selected{{background:{A}30;color:#e6e6e6}}
QFrame#card{{background:transparent;border:1px solid #2b2b30;border-radius:8px;padding:12px;margin-bottom:6px}}
QStatusBar{{background:#1c1c1c;color:#666;border-top:1px solid #2b2b30;padding:2px 8px;font-size:9pt}}QStatusBar QLabel{{color:#666;font-size:9pt}}
QTabWidget::pane{{border:1px solid #2b2b30;border-top:none;background:#1c1c1c}}QTabBar::tab{{background:#2b2b30;color:#666;padding:8px 20px;border:1px solid #2b2b30;border-bottom:none;margin-right:2px;border-radius:6px 6px 0 0;font-size:11px}}QTabBar::tab:selected{{background:#1c1c1c;color:#e6e6e6;font-weight:bold;border-bottom:1px solid #1c1c1c}}QTabBar::tab:hover{{color:#aaa}}
QToolTip{{background:#2b2b30;color:#e6e6e6;border:1px solid #3a3a3a;border-radius:4px;padding:4px 8px;font-size:9pt}}
QStatusBar QPushButton{{background:transparent;color:#666;border:1px solid #2b2b30;border-radius:4px;padding:2px 10px;min-height:22px;font-size:9pt}}QStatusBar QPushButton:hover{{color:#e6e6e6;border-color:#3a3a3a}}
"""

LIGHT_STYLE=f"""QMainWindow,QDialog{{background:#f3f3f3;color:#333}}QLabel{{color:#333}}
QGroupBox{{color:#333;border:1px solid #ddd;border-radius:6px;margin-top:10px;padding-top:10px}}
QPushButton{{background:#e8e8e8;color:#333;border:1px solid #ccc;border-radius:6px;padding:5px 16px;min-height:28px}}
QPushButton:hover{{background:#ddd;border-color:#aaa}}QPushButton:pressed{{background:#d5d5d5}}
QPushButton:disabled{{background:transparent;color:#999;border-color:#ddd}}
QPushButton#startBtn{{background:{A};color:#fff;border-color:{A};font-weight:bold;min-height:28px}}QPushButton#startBtn:hover{{background:#4070f5}}
QPushButton#stopBtn{{background:#8b4a4a;color:#fff;border-color:#8b4a4a}}QPushButton#stopBtn:hover{{background:#6b3535}}
QPushButton#addProgBtn{{background:{A};color:#fff;font-weight:bold;border-color:{A};min-height:28px}}
QPushButton#tabBtn{{background:transparent;color:#666;border:none;padding:6px 14px;font-size:11px;border-radius:0}}QPushButton#tabBtn:hover{{color:#333;border-bottom:2px solid #ccc}}
QPushButton#tabBtnActive{{background:transparent;color:#333;border:none;padding:6px 14px;font-size:11px;border-bottom:2px solid {A};border-radius:0;font-weight:bold}}
QPushButton#iconBtn{{background:transparent;color:#666;border:none;border-radius:6px;padding:3px 6px;font-size:11pt}}QPushButton#iconBtn:hover{{background:#e0e0e0;color:#333}}
QFrame#sideBar QLabel{{color:#666;font-size:9pt;padding:5px 8px;border-radius:6px}}QFrame#sideBar QLabel:hover{{background:#e0e0e0;color:#333}}
QLineEdit,QSpinBox,QComboBox{{background:#fff;color:#333;border:1px solid #ccc;border-radius:5px;padding:6px 10px;min-height:24px}}
QLineEdit:hover,QSpinBox:hover,QComboBox:hover{{border:1px solid #aaa}}
QLineEdit:focus,QSpinBox:focus{{border:1px solid {A};background:#f8f8ff}}
QComboBox:disabled,QSpinBox:disabled{{background:#eee;color:#888}}
QComboBox::drop-down{{background:#eee;border:none}}QComboBox QAbstractItemView{{background:#fff;color:#333;selection-background-color:{A}30;border:1px solid #ddd;border-radius:4px}}
QTableWidget{{background:transparent;color:#333;border:1px solid #e0e0e0;alternate-background-color:#fafafa}}
QTableWidget::item{{padding:4px 8px;border:none}}QTableWidget::item:selected{{background:{A}20;color:#333}}
QHeaderView::section{{background:transparent;color:#666;border:none;border-bottom:1px solid #e0e0e0;padding:6px 10px;font-weight:bold;font-size:10px}}
QPlainTextEdit{{background:#fff;color:#333;border:1px solid #ddd;border-radius:4px;padding:6px;font-family:Consolas;font-size:12px}}
QMenu{{background:#fff;color:#333;border:1px solid #ddd;border-radius:6px;padding:4px}}QMenu::item{{padding:6px 28px 6px 12px;border-radius:3px}}QMenu::item:selected{{background:{A}20;color:#333}}QMenu::separator{{height:1px;background:#e0e0e0;margin:4px 8px}}
QMenuBar{{background:#f3f3f3;color:#555;border:none;border-bottom:1px solid #e0e0e0}}QMenuBar::item{{padding:6px 14px}}QMenuBar::item:selected{{background:#e0e0e0;color:#333;border-radius:4px}}
QCheckBox{{color:#555;spacing:8px}}QCheckBox::indicator{{width:18px;height:18px;border:2px solid #ccc;border-radius:4px;background:#fff;}}QCheckBox::indicator:hover{{border-color:#aaa}}QCheckBox::indicator:checked{{background:{A};border-color:{A}}}
QProgressBar{{border:1px solid #e0e0e0;border-radius:4px;background:#eee;color:#555;text-align:center;height:18px}}QProgressBar::chunk{{background:{A};border-radius:3px}}
QScrollArea{{border:none;background:transparent}}
QSplitter::handle{{background:#e0e0e0;width:3px}}QSplitter::handle:hover{{background:#ccc}}
QListWidget{{background:transparent;color:#333;border:1px solid #e0e0e0;border-radius:6px}}QListWidget::item{{padding:6px 10px;border-radius:3px}}QListWidget::item:hover{{background:#eee}}QListWidget::item:selected{{background:{A}20;color:#333}}
QFrame#card{{background:transparent;border:1px solid #e0e0e0;border-radius:8px;padding:12px;margin-bottom:6px}}
QStatusBar{{background:#f3f3f3;color:#888;border-top:1px solid #e0e0e0;padding:2px 8px;font-size:9pt}}QStatusBar QLabel{{color:#888;font-size:9pt}}
QTabWidget::pane{{border:1px solid #e0e0e0;border-top:none;background:#f3f3f3}}QTabBar::tab{{background:#eee;color:#666;padding:8px 20px;border:1px solid #e0e0e0;border-bottom:none;margin-right:2px;border-radius:6px 6px 0 0;font-size:11px}}QTabBar::tab:selected{{background:#f3f3f3;color:#333;font-weight:bold}}QTabBar::tab:hover{{color:#555}}
QToolTip{{background:#eee;color:#333;border:1px solid #ccc;border-radius:4px;padding:4px 8px;font-size:9pt}}
QStatusBar QPushButton{{background:transparent;color:#888;border:1px solid #ddd;border-radius:4px;padding:2px 10px;min-height:22px;font-size:9pt}}QStatusBar QPushButton:hover{{color:#333;border-color:#ccc}}
"""

NOTEPAPER_STYLE=f"""/* ── Base ── */
QMainWindow,QDialog{{background:#f6f3ec;color:#2a2a28}}QLabel{{color:#2a2a28;font-family:'Microsoft YaHei UI','HarmonyOS Sans SC','PingFang SC','Noto Sans SC',sans-serif}}
QWidget{{font-family:'Microsoft YaHei UI','HarmonyOS Sans SC','PingFang SC','Noto Sans SC',sans-serif}}

/* ── Groups ── */
QGroupBox{{color:#2a2a28;border:1px solid #e6e2d8;border-radius:12px;margin-top:12px;padding-top:14px;padding-left:10px;padding-right:10px;padding-bottom:8px;background:#f0ece4}}
QGroupBox::title{{subcontrol-origin:margin;padding:2px 8px;color:#5a5650}}

/* ── Buttons ── */
QPushButton{{background:#ede9e0;color:#2a2a28;border:1px solid #ddd8ce;border-radius:10px;padding:6px 18px;min-height:28px}}
QPushButton:hover{{background:#e4dfd5;border-color:#c8c2b6}}QPushButton:pressed{{background:#d8d2c6}}
QPushButton:disabled{{background:transparent;color:#b0aca2;border-color:#e6e2d8}}
QPushButton#startBtn{{background:{G};color:#f6f3ec;border-color:{G};font-weight:bold;min-height:28px;border-radius:10px;padding:6px 20px}}QPushButton#startBtn:hover{{background:#3a7a50}}
QPushButton#stopBtn{{background:#a04040;color:#f0dcdc;border-color:#a04040}}QPushButton#stopBtn:hover{{background:#c04040;color:#fff}}
QPushButton#addProgBtn{{background:{G};color:#f6f3ec;font-weight:bold;border-color:{G};min-height:28px;border-radius:10px}}
QPushButton#tabBtn{{background:transparent;color:#8a867e;border:none;padding:7px 16px;font-size:11px;border-radius:0}}QPushButton#tabBtn:hover{{color:#2a2a28;border-bottom:2px solid #c8c2b6}}
QPushButton#tabBtnActive{{background:transparent;color:#2a2a28;border:none;padding:7px 16px;font-size:11px;border-bottom:2px solid {G};border-radius:0;font-weight:bold}}
QPushButton#iconBtn{{background:transparent;color:#8a867e;border:none;border-radius:6px;padding:3px 6px;font-size:11pt}}QPushButton#iconBtn:hover{{background:#ede9e0;color:#2a2a28}}
QFrame#sideBar QLabel{{color:#8a867e;font-size:9pt;padding:5px 8px;border-radius:6px}}QFrame#sideBar QLabel:hover{{background:#ede9e0;color:#2a2a28}}

/* ── Inputs ── */
QLineEdit,QSpinBox,QComboBox{{background:#f0ece4;color:#2a2a28;border:1px solid #ddd8ce;border-radius:8px;padding:7px 12px;min-height:24px}}
QLineEdit:hover,QSpinBox:hover,QComboBox:hover{{border:1px solid #c8c2b6}}
QLineEdit:focus,QSpinBox:focus{{border:1px solid {G};background:#ece8e0}}
QComboBox:disabled,QSpinBox:disabled{{background:#eae6de;color:#b0aca2}}
QComboBox::drop-down{{background:#eae6de;border:none;border-radius:8px;width:24px}}QComboBox QAbstractItemView{{background:#f6f3ec;color:#2a2a28;selection-background-color:{G}30;border:1px solid #ddd8ce;border-radius:8px;padding:4px}}

/* ── Tables ── */
QTableWidget{{background:transparent;color:#2a2a28;border:1px solid #e6e2d8;border-radius:8px;alternate-background-color:#f3efe8;gridline-color:#ede9e0}}
QTableWidget::item{{padding:5px 10px;border:none}}QTableWidget::item:selected{{background:{G}25;color:#2a2a28}}
QHeaderView::section{{background:transparent;color:#6a665e;border:none;border-bottom:1px solid #e6e2d8;padding:7px 10px;font-weight:bold;font-size:10px}}

/* ── Text edit ── */
QPlainTextEdit{{background:#f0ece4;color:#2a2a28;border:1px solid #ddd8ce;font-family:Consolas,'Courier New',monospace;font-size:12px;border-radius:8px;padding:8px}}

/* ── Menus ── */
QMenu{{background:#f6f3ec;color:#2a2a28;border:1px solid #ddd8ce;border-radius:10px;padding:6px}}QMenu::item{{padding:7px 30px 7px 14px;border-radius:5px}}QMenu::item:selected{{background:{G}25;color:#2a2a28}}QMenu::separator{{height:1px;background:#e6e2d8;margin:5px 10px}}
QMenuBar{{background:#f6f3ec;color:#6a665e;border:none;border-bottom:1px solid #e6e2d8}}QMenuBar::item{{padding:7px 16px}}QMenuBar::item:selected{{background:#e4dfd5;color:#2a2a28;border-radius:6px}}

/* ── Checkbox ── */
QCheckBox{{color:#5a5650;spacing:10px}}QCheckBox::indicator{{width:20px;height:20px;border:2px solid #c8c2b6;border-radius:6px;background:#f0ece4}}QCheckBox::indicator:hover{{border-color:#a8a298}}QCheckBox::indicator:checked{{background:{G};border-color:{G}}}

/* ── Progress ── */
QProgressBar{{border:1px solid #e6e2d8;border-radius:7px;background:#ede9e0;color:#6a665e;text-align:center;height:20px}}QProgressBar::chunk{{background:{G};border-radius:6px}}

/* ── Scrollbars ── */
QScrollBar:vertical{{background:transparent;width:5px;margin:0}}QScrollBar::handle:vertical{{background:#d8d2c6;border-radius:3px;min-height:30px}}QScrollBar::handle:vertical:hover{{background:#c8c2b6}}QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0}}
QScrollBar:horizontal{{background:transparent;height:5px;margin:0}}QScrollBar::handle:horizontal{{background:#d8d2c6;border-radius:3px;min-width:30px}}QScrollBar::handle:horizontal:hover{{background:#c8c2b6}}QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0}}

/* ── Containers ── */
QScrollArea{{border:none;background:transparent}}
QSplitter::handle{{background:#e6e2d8;width:3px}}QSplitter::handle:hover{{background:#c8c2b6}}
QListWidget{{background:transparent;color:#2a2a28;border:1px solid #e6e2d8;border-radius:8px}}QListWidget::item{{padding:7px 12px;border-radius:5px}}QListWidget::item:hover{{background:#ede9e0}}QListWidget::item:selected{{background:{G}25;color:#2a2a28}}
QFrame#card{{background:#f0ece4;border:1px solid #e6e2d8;border-radius:12px;padding:14px;margin-bottom:8px}}
QFrame#configCard{{background:#f0ece4;border:2px solid transparent;border-radius:12px;padding:10px}}
QFrame#configCard:hover{{background:#ede9e0;border-color:#ddd8ce}}

/* ── Status bar ── */
QStatusBar{{background:#f0ece4;color:#8a867e;border-top:1px solid #e6e2d8;padding:2px 10px;font-size:9pt}}QStatusBar QLabel{{color:#8a867e;font-size:9pt}}
QStatusBar QPushButton{{background:transparent;color:#8a867e;border:1px solid #e6e2d8;border-radius:5px;padding:2px 12px;min-height:22px;font-size:9pt}}QStatusBar QPushButton:hover{{color:#2a2a28;border-color:#c8c2b6}}

/* ── Tabs ── */
QTabWidget::pane{{border:1px solid #e6e2d8;border-top:none;background:#f6f3ec;border-radius:0 0 12px 12px}}QTabBar::tab{{background:#ede9e0;color:#6a665e;padding:9px 22px;border:1px solid #e6e2d8;border-bottom:none;margin-right:2px;border-radius:10px 10px 0 0;font-size:11px;font-weight:normal}}QTabBar::tab:selected{{background:#f6f3ec;color:#2a2a28;font-weight:bold}}QTabBar::tab:hover{{color:#2a2a28}}

/* ── Tooltip ── */
QToolTip{{background:#f6f3ec;color:#2a2a28;border:1px solid #ddd8ce;border-radius:8px;padding:6px 10px;font-size:10pt}}
"""
