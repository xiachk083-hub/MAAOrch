A="#326cf3"
G="#2d5a3d"
BTN_DELETE='QPushButton{{background:transparent;color:#888;border:none}}QPushButton:hover{{background:#326cf3;color:#fff;border-radius:{r}px}}'

DARK_STYLE=f"""QMainWindow,QDialog{{background:#1c1c1c;color:#e6e6e6}}
QLabel{{color:#e6e6e6;font-size:9pt}}
QWidget{{font-family:'Microsoft YaHei UI','PingFang SC',sans-serif}}

QGroupBox{{color:#e6e6e6;border:1px solid #2b2b30;border-radius:6px;margin-top:10px;padding-top:10px;background:#1e1e20}}
QGroupBox::title{{subcontrol-origin:margin;padding:2px 6px;color:#888}}

QPushButton{{background:#2b2b30;color:#ccc;border:1px solid #3a3a3a;border-radius:6px;padding:5px 16px;min-height:28px;font-size:9pt}}
QPushButton:hover{{background:#333;border-color:#555}}
QPushButton:pressed{{background:#252525}}
QPushButton:disabled{{background:transparent;color:#555;border-color:#2b2b30}}
QPushButton#startBtn{{background:{A};color:#fff;border-color:{A};font-weight:bold;min-height:28px;font-size:9pt}}
QPushButton#startBtn:hover{{background:#4070f5}}
QPushButton#stopBtn{{background:#5d2626;color:#d0a0a0;border-color:#7a2b2b}}
QPushButton#stopBtn:hover{{background:#8e0000;color:#fff}}
QPushButton#iconBtn{{background:transparent;color:#888;border:none;border-radius:5px;padding:3px 5px;font-size:11pt}}
QPushButton#iconBtn:hover{{background:#2b2b30;color:#e6e6e6}}

QFrame#sideBar QLabel{{color:#888;font-size:9pt;padding:5px 8px;border-radius:5px}}
QFrame#sideBar QLabel:hover{{background:#2b2b30;color:#e6e6e6}}

QLineEdit,QSpinBox,QComboBox{{background:#2b2b30;color:#e6e6e6;border:1px solid #3a3a3a;border-radius:6px;padding:6px 10px;min-height:24px;font-size:9pt}}
QLineEdit:hover,QSpinBox:hover,QComboBox:hover{{border-color:#555}}
QLineEdit:focus,QSpinBox:focus{{border:1px solid {A};background:#25252a}}
QComboBox:disabled,QSpinBox:disabled{{background:#1c1c1c;color:#666;border-color:#2b2b30}}
QComboBox::drop-down{{background:#2b2b30;border:none;border-radius:6px;width:22px}}
QComboBox QAbstractItemView{{background:#2b2b30;color:#e6e6e6;selection-background-color:{A};border:1px solid #3a3a3a;border-radius:6px;padding:4px}}

QTableWidget{{background:transparent;color:#ccc;border:1px solid #2b2b30;alternate-background-color:#1e1e20;font-size:9pt;border-radius:6px}}
QTableWidget::item{{padding:4px 8px;border:none}}
QTableWidget::item:selected{{background:{A}30;color:#e6e6e6}}
QHeaderView::section{{background:transparent;color:#666;border:none;border-bottom:1px solid #2b2b30;padding:6px 10px;font-weight:bold;font-size:9pt}}

QPlainTextEdit{{background:#1c1c1c;color:#ccc;border:1px solid #2b2b30;font-family:Consolas;font-size:11px;border-radius:6px;padding:6px}}

QMenu{{background:#2b2b30;color:#ccc;border:1px solid #3a3a3a;border-radius:6px;padding:4px;font-size:9pt}}
QMenu::item{{padding:6px 28px 6px 12px;border-radius:4px}}
QMenu::item:selected{{background:{A};color:#fff}}
QMenu::separator{{height:1px;background:#3a3a3a;margin:4px 8px}}
QMenuBar{{background:#1c1c1c;color:#888;border:none;border-bottom:1px solid #2b2b30}}
QMenuBar::item{{padding:6px 14px}}
QMenuBar::item:selected{{background:#2b2b30;color:#e6e6e6;border-radius:5px}}

QCheckBox{{color:#aaa;spacing:8px;font-size:9pt}}
QCheckBox::indicator{{width:18px;height:18px;border:2px solid #3a3a3a;border-radius:5px;background:#2b2b30}}
QCheckBox::indicator:hover{{border-color:#555}}
QCheckBox::indicator:checked{{background:{A};border-color:{A}}}

QProgressBar{{border:1px solid #2b2b30;border-radius:6px;background:#2b2b30;color:#aaa;text-align:center;height:18px;font-size:8pt}}
QProgressBar::chunk{{background:{A};border-radius:5px}}

QScrollArea{{border:none;background:transparent}}
QSplitter::handle{{background:#2b2b30;width:3px}}
QSplitter::handle:hover{{background:#3a3a3a}}
QListWidget{{background:transparent;color:#ccc;border:1px solid #2b2b30;border-radius:6px;font-size:9pt}}
QListWidget::item{{padding:6px 10px;border-radius:4px}}
QListWidget::item:hover{{background:#2b2b30}}
QListWidget::item:selected{{background:{A}30;color:#e6e6e6}}
QFrame#card{{background:transparent;border:1px solid #2b2b30;border-radius:8px;padding:12px;margin-bottom:6px}}
QStatusBar{{background:#1c1c1c;color:#666;border-top:1px solid #2b2b30;padding:2px 8px;font-size:8pt}}
QStatusBar QLabel{{color:#666;font-size:8pt}}
QStatusBar QPushButton{{background:transparent;color:#666;border:none;border-radius:4px;padding:2px 6px;min-height:18px;font-size:8pt}}
QStatusBar QPushButton:hover{{color:#e6e6e6;background:#2b2b30}}
QToolTip{{background:#2b2b30;color:#e6e6e6;border:1px solid #3a3a3a;border-radius:5px;padding:4px 8px;font-size:9pt}}
"""

LIGHT_STYLE=f"""QMainWindow,QDialog{{background:#f3f3f3;color:#333}}
QLabel{{color:#333;font-size:9pt}}
QWidget{{font-family:'Microsoft YaHei UI','PingFang SC',sans-serif}}

QGroupBox{{color:#333;border:1px solid #ddd;border-radius:6px;margin-top:10px;padding-top:10px;background:#f8f8f8}}
QGroupBox::title{{subcontrol-origin:margin;padding:2px 6px;color:#666}}

QPushButton{{background:#e8e8e8;color:#333;border:1px solid #ccc;border-radius:6px;padding:5px 16px;min-height:28px;font-size:9pt}}
QPushButton:hover{{background:#ddd;border-color:#aaa}}
QPushButton:pressed{{background:#d5d5d5}}
QPushButton:disabled{{background:transparent;color:#999;border-color:#ddd}}
QPushButton#startBtn{{background:{A};color:#fff;border-color:{A};font-weight:bold;min-height:28px;font-size:9pt}}
QPushButton#startBtn:hover{{background:#4070f5}}
QPushButton#stopBtn{{background:#8b4a4a;color:#fff;border-color:#8b4a4a}}
QPushButton#stopBtn:hover{{background:#6b3535}}
QPushButton#iconBtn{{background:transparent;color:#666;border:none;border-radius:5px;padding:3px 5px;font-size:11pt}}
QPushButton#iconBtn:hover{{background:#e0e0e0;color:#333}}

QFrame#sideBar QLabel{{color:#666;font-size:9pt;padding:5px 8px;border-radius:5px}}
QFrame#sideBar QLabel:hover{{background:#e0e0e0;color:#333}}

QLineEdit,QSpinBox,QComboBox{{background:#fff;color:#333;border:1px solid #ccc;border-radius:6px;padding:6px 10px;min-height:24px;font-size:9pt}}
QLineEdit:hover,QSpinBox:hover,QComboBox:hover{{border-color:#aaa}}
QLineEdit:focus,QSpinBox:focus{{border:1px solid {A};background:#f8f8ff}}
QComboBox:disabled,QSpinBox:disabled{{background:#eee;color:#888}}
QComboBox::drop-down{{background:#eee;border:none;border-radius:6px;width:22px}}
QComboBox QAbstractItemView{{background:#fff;color:#333;selection-background-color:{A}30;border:1px solid #ddd;border-radius:6px;padding:4px}}

QTableWidget{{background:transparent;color:#333;border:1px solid #e0e0e0;alternate-background-color:#fafafa;font-size:9pt;border-radius:6px}}
QTableWidget::item{{padding:4px 8px;border:none}}
QTableWidget::item:selected{{background:{A}20;color:#333}}
QHeaderView::section{{background:transparent;color:#666;border:none;border-bottom:1px solid #e0e0e0;padding:6px 10px;font-weight:bold;font-size:9pt}}

QPlainTextEdit{{background:#fff;color:#333;border:1px solid #ddd;border-radius:6px;padding:6px;font-family:Consolas;font-size:11px}}

QMenu{{background:#fff;color:#333;border:1px solid #ddd;border-radius:6px;padding:4px;font-size:9pt}}
QMenu::item{{padding:6px 28px 6px 12px;border-radius:4px}}
QMenu::item:selected{{background:{A}20;color:#333}}
QMenu::separator{{height:1px;background:#e0e0e0;margin:4px 8px}}
QMenuBar{{background:#f3f3f3;color:#555;border:none;border-bottom:1px solid #ddd}}
QMenuBar::item{{padding:6px 14px}}
QMenuBar::item:selected{{background:#e0e0e0;color:#333;border-radius:5px}}

QCheckBox{{color:#555;spacing:8px;font-size:9pt}}
QCheckBox::indicator{{width:18px;height:18px;border:2px solid #ccc;border-radius:5px;background:#fff}}
QCheckBox::indicator:hover{{border-color:#aaa}}
QCheckBox::indicator:checked{{background:{A};border-color:{A}}}

QProgressBar{{border:1px solid #e0e0e0;border-radius:6px;background:#eee;color:#555;text-align:center;height:18px;font-size:8pt}}
QProgressBar::chunk{{background:{A};border-radius:5px}}

QScrollArea{{border:none;background:transparent}}
QSplitter::handle{{background:#e0e0e0;width:3px}}
QSplitter::handle:hover{{background:#ccc}}
QListWidget{{background:transparent;color:#333;border:1px solid #e0e0e0;border-radius:6px;font-size:9pt}}
QListWidget::item{{padding:6px 10px;border-radius:4px}}
QListWidget::item:hover{{background:#eee}}
QListWidget::item:selected{{background:{A}20;color:#333}}
QFrame#card{{background:transparent;border:1px solid #e0e0e0;border-radius:8px;padding:12px;margin-bottom:6px}}
QStatusBar{{background:#f3f3f3;color:#888;border-top:1px solid #ddd;padding:2px 8px;font-size:8pt}}
QStatusBar QLabel{{color:#888;font-size:8pt}}
QStatusBar QPushButton{{background:transparent;color:#888;border:none;border-radius:4px;padding:2px 6px;min-height:18px;font-size:8pt}}
QStatusBar QPushButton:hover{{color:#333;background:#e0e0e0}}
QToolTip{{background:#eee;color:#333;border:1px solid #ccc;border-radius:5px;padding:4px 8px;font-size:9pt}}
"""

NOTEPAPER_STYLE=f"""QMainWindow,QDialog{{background:#f6f3ec;color:#2a2a28}}
QLabel{{color:#2a2a28;font-size:9pt;font-family:'HarmonyOS Sans SC','PingFang SC','Microsoft YaHei UI',sans-serif}}
QWidget{{font-family:'HarmonyOS Sans SC','PingFang SC','Microsoft YaHei UI',sans-serif}}

QGroupBox{{color:#2a2a28;border:1px solid #e6e2d8;border-radius:10px;margin-top:10px;padding-top:10px;background:#f0ece4}}
QGroupBox::title{{subcontrol-origin:margin;padding:2px 8px;color:#5a5650;font-size:9pt}}

QPushButton{{background:#ede9e0;color:#2a2a28;border:1px solid #ddd8ce;border-radius:8px;padding:5px 16px;min-height:28px;font-size:9pt}}
QPushButton:hover{{background:#e4dfd5;border-color:#c8c2b6}}
QPushButton:pressed{{background:#d8d2c6}}
QPushButton:disabled{{background:transparent;color:#b0aca2;border-color:#e6e2d8}}
QPushButton#startBtn{{background:{G};color:#f6f3ec;border-color:{G};font-weight:bold;min-height:28px;border-radius:8px;font-size:9pt}}
QPushButton#startBtn:hover{{background:#3a7a50}}
QPushButton#stopBtn{{background:#a04040;color:#f0dcdc;border-color:#a04040}}
QPushButton#stopBtn:hover{{background:#c04040;color:#fff}}
QPushButton#iconBtn{{background:transparent;color:#8a867e;border:none;border-radius:5px;padding:3px 5px;font-size:11pt}}
QPushButton#iconBtn:hover{{background:#ede9e0;color:#2a2a28}}

QFrame#sideBar QLabel{{color:#8a867e;font-size:9pt;padding:5px 8px;border-radius:5px}}
QFrame#sideBar QLabel:hover{{background:#ede9e0;color:#2a2a28}}

QLineEdit,QSpinBox,QComboBox{{background:#f0ece4;color:#2a2a28;border:1px solid #ddd8ce;border-radius:8px;padding:6px 10px;min-height:24px;font-size:9pt}}
QLineEdit:hover,QSpinBox:hover,QComboBox:hover{{border-color:#c8c2b6}}
QLineEdit:focus,QSpinBox:focus{{border:1px solid {G};background:#ece8e0}}
QComboBox:disabled,QSpinBox:disabled{{background:#eae6de;color:#b0aca2}}
QComboBox::drop-down{{background:#eae6de;border:none;border-radius:8px;width:22px}}
QComboBox QAbstractItemView{{background:#f6f3ec;color:#2a2a28;selection-background-color:{G}30;border:1px solid #ddd8ce;border-radius:8px;padding:4px}}

QTableWidget{{background:transparent;color:#2a2a28;border:1px solid #e6e2d8;alternate-background-color:#f3efe8;font-size:9pt;border-radius:8px}}
QTableWidget::item{{padding:5px 10px;border:none}}
QTableWidget::item:selected{{background:{G}25;color:#2a2a28}}
QHeaderView::section{{background:transparent;color:#6a665e;border:none;border-bottom:1px solid #e6e2d8;padding:6px 10px;font-weight:bold;font-size:9pt}}

QPlainTextEdit{{background:#f0ece4;color:#2a2a28;border:1px solid #ddd8ce;font-family:Consolas,'Courier New',monospace;font-size:11px;border-radius:8px;padding:6px}}

QMenu{{background:#f6f3ec;color:#2a2a28;border:1px solid #ddd8ce;border-radius:8px;padding:4px;font-size:9pt}}
QMenu::item{{padding:6px 28px 6px 12px;border-radius:4px}}
QMenu::item:selected{{background:{G}25;color:#2a2a28}}
QMenu::separator{{height:1px;background:#e6e2d8;margin:4px 8px}}
QMenuBar{{background:#f6f3ec;color:#6a665e;border:none;border-bottom:1px solid #e6e2d8}}
QMenuBar::item{{padding:6px 14px}}
QMenuBar::item:selected{{background:#e4dfd5;color:#2a2a28;border-radius:5px}}

QCheckBox{{color:#5a5650;spacing:8px;font-size:9pt}}
QCheckBox::indicator{{width:18px;height:18px;border:2px solid #c8c2b6;border-radius:5px;background:#f0ece4}}
QCheckBox::indicator:hover{{border-color:#a8a298}}
QCheckBox::indicator:checked{{background:{G};border-color:{G}}}

QProgressBar{{border:1px solid #e6e2d8;border-radius:6px;background:#ede9e0;color:#6a665e;text-align:center;height:18px;font-size:8pt}}
QProgressBar::chunk{{background:{G};border-radius:5px}}

QScrollBar:vertical{{background:transparent;width:4px;margin:0}}
QScrollBar::handle:vertical{{background:#d8d2c6;border-radius:2px;min-height:30px}}
QScrollBar::handle:vertical:hover{{background:#c8c2b6}}
QScrollBar:horizontal{{background:transparent;height:4px;margin:0}}
QScrollBar::handle:horizontal{{background:#d8d2c6;border-radius:2px;min-width:30px}}
QScrollBar::handle:horizontal:hover{{background:#c8c2b6}}

QScrollArea{{border:none;background:transparent}}
QSplitter::handle{{background:#e6e2d8;width:3px}}
QSplitter::handle:hover{{background:#c8c2b6}}
QListWidget{{background:transparent;color:#2a2a28;border:1px solid #e6e2d8;border-radius:8px;font-size:9pt}}
QListWidget::item{{padding:6px 10px;border-radius:4px}}
QListWidget::item:hover{{background:#ede9e0}}
QListWidget::item:selected{{background:{G}25;color:#2a2a28}}
QFrame#card{{background:#f0ece4;border:1px solid #e6e2d8;border-radius:10px;padding:12px;margin-bottom:6px}}
QStatusBar{{background:#f0ece4;color:#8a867e;border-top:1px solid #e6e2d8;padding:2px 10px;font-size:8pt}}
QStatusBar QLabel{{color:#8a867e;font-size:8pt}}
QStatusBar QPushButton{{background:transparent;color:#8a867e;border:none;border-radius:4px;padding:2px 6px;min-height:18px;font-size:8pt}}
QStatusBar QPushButton:hover{{color:#2a2a28;background:#ede9e0}}
QToolTip{{background:#f6f3ec;color:#2a2a28;border:1px solid #ddd8ce;border-radius:6px;padding:4px 8px;font-size:9pt}}
"""
