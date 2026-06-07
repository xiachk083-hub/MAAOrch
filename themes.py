A="#326cf3"
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
