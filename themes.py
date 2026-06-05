DARK_STYLE="""QMainWindow,QDialog{background:#1e1e1e;color:#ccc}QLabel{color:#ccc}
QGroupBox{color:#ccc;border:1px solid #3c3c3c;border-radius:6px;margin-top:10px;padding-top:10px}
QPushButton{background:#333;color:#ccc;border:1px solid #555;border-radius:6px;padding:5px 14px;min-height:26px}
QPushButton:hover{background:#444}QPushButton:disabled{background:#2a2a2a;color:#666}
QPushButton#startBtn{background:#265d33;color:#a0d0a0;border-color:#2b6a3a;font-weight:bold}QPushButton#startBtn:hover{background:#1e5a28;color:#fff}
QPushButton#stopBtn{background:#5d2626;color:#d0a0a0;border-color:#6a2b2b}QPushButton#stopBtn:hover{background:#8e0000;color:#fff}
QPushButton#addProgBtn{background:#26405d;color:#a0c0d0;font-weight:bold;border-color:#2b4a6a}QPushButton#addProgBtn:hover{background:#1f5380;color:#fff}
QPushButton#tabBtn{background:transparent;color:#777;border:none;padding:6px 18px;font-size:13px;border-radius:0}QPushButton#tabBtn:hover{color:#aaa;border-bottom:2px solid #444}
QPushButton#tabBtnActive{background:transparent;color:#ccc;border:none;padding:6px 18px;font-size:13px;border-bottom:2px solid #666;border-radius:0}
QLineEdit,QSpinBox,QComboBox{background:#353535;color:#ccc;border:1px solid #444;border-radius:4px;padding:5px 8px;min-height:24px}
QLineEdit:focus,QSpinBox:focus{border:1px solid #666}
QComboBox::drop-down{background:#333;border:none}QComboBox QAbstractItemView{background:#333;color:#ccc;selection-background-color:#3a7ebf}
QTableWidget{background:#252526;color:#bbb;border:1px solid #333;alternate-background-color:#28282e}
QTableWidget::item{padding:4px 8px}QTableWidget::item:selected{background:#2a3a4a;color:#ddd}
QHeaderView::section{background:#2a2a2a;color:#888;border:none;border-bottom:1px solid #333;padding:6px 8px;font-weight:bold;font-size:11px}
QPlainTextEdit{background:#1a1a1a;color:#aaa;border:1px solid #333;font-family:Consolas;font-size:12px;border-radius:4px}
QMenu{background:#2a2a2a;color:#bbb;border:1px solid #3a3a3a;border-radius:6px;padding:4px}QMenu::item{padding:6px 28px 6px 12px;border-radius:3px}QMenu::item:selected{background:#3a3a3a;color:#ddd}QMenu::separator{height:1px;background:#3a3a3a;margin:4px 8px}
QMenuBar{background:#252526;color:#aaa;border:none}QMenuBar::item{padding:6px 14px}QMenuBar::item:selected{background:#333;color:#ddd;border-radius:4px}
QCheckBox{color:#bbb;spacing:6px}QCheckBox::indicator{width:16px;height:16px;border:2px solid #444;border-radius:3px;background:#333}QCheckBox::indicator:checked{background:#555;border-color:#666}
QProgressBar{border:1px solid #3a3a3a;border-radius:4px;background:#333;color:#aaa;text-align:center;height:18px}QProgressBar::chunk{background:#555;border-radius:3px}
QScrollArea{border:none;background:transparent}
QListWidget{background:#252526;color:#bbb;border:1px solid #333;border-radius:6px}QListWidget::item{padding:6px 10px;border-radius:3px}QListWidget::item:hover{background:#303030}QListWidget::item:selected{background:#2a3a4a;color:#ddd}
QFrame#card{background:#282830;border:1px solid #353535;border-radius:8px;padding:12px;margin-bottom:6px}
QStatusBar{background:#1e1e1e;color:#888;border-top:1px solid #333}QStatusBar QLabel{color:#888}QStatusBar QPushButton{background:transparent;color:#888;border:1px solid #333;border-radius:4px;padding:2px 10px;min-height:22px}QStatusBar QPushButton:hover{color:#ccc;border-color:#555}
QTabWidget::pane{border:1px solid #333;border-top:none;background:#252526}QTabBar::tab{background:#2a2a2a;color:#777;padding:7px 18px;border:1px solid #333;border-bottom:none;margin-right:2px;border-radius:4px 4px 0 0}QTabBar::tab:selected{background:#252526;color:#aaa;font-weight:bold}QTabBar::tab:hover{color:#ccc}
"""

LIGHT_STYLE="""QMainWindow,QDialog{background:#f0f0f0;color:#333}QLabel{color:#333}
QGroupBox{color:#333;border:1px solid #d5d5d5;border-radius:6px;margin-top:10px;padding-top:10px}
QPushButton{background:#e8e8e8;color:#333;border:1px solid #ccc;border-radius:6px;padding:5px 14px;min-height:26px}
QPushButton:hover{background:#ddd}QPushButton:disabled{background:#eee;color:#999}
QPushButton#startBtn{background:#3a6b4a;color:#fff;border-color:#3a6b4a;font-weight:bold}QPushButton#startBtn:hover{background:#2e543a}
QPushButton#stopBtn{background:#8b4a4a;color:#fff;border-color:#8b4a4a}QPushButton#stopBtn:hover{background:#6b3535}
QPushButton#addProgBtn{background:#4a608b;color:#fff;font-weight:bold;border-color:#4a608b}QPushButton#addProgBtn:hover{background:#354570}
QPushButton#tabBtn{background:transparent;color:#666;border:none;padding:6px 18px;font-size:13px;border-radius:0}QPushButton#tabBtn:hover{color:#333;border-bottom:2px solid #ccc}
QPushButton#tabBtnActive{background:transparent;color:#333;border:none;padding:6px 18px;font-size:13px;border-bottom:2px solid #888;border-radius:0}
QLineEdit,QSpinBox,QComboBox{background:#fff;color:#333;border:1px solid #ccc;padding:5px 8px}
QComboBox:disabled,QSpinBox:disabled{background:#eaeaea;color:#888}
QLineEdit:focus,QSpinBox:focus{border:1px solid #999}
QTableWidget{background:#fff;color:#333;border:1px solid #ddd}QTableWidget::item:selected{background:#e0e8f0;color:#333}
QHeaderView::section{background:#f5f5f5;color:#666;border:none;border-bottom:1px solid #ddd;padding:6px 8px;font-weight:bold}
QPlainTextEdit{background:#fff;color:#333;border:1px solid #ddd;font-family:Consolas;font-size:12px}
QMenu{background:#fff;color:#333;border:1px solid #ddd;border-radius:6px;padding:4px}QMenu::item:selected{background:#e8e8e8;color:#333}
QMenuBar{background:#f5f5f5;color:#555;border:none}QMenuBar::item:selected{background:#e0e0e0;color:#333;border-radius:4px}
QListWidget{background:#fff;color:#333;border:1px solid #ddd;border-radius:6px}QListWidget::item:hover{background:#f0f0f0}QListWidget::item:selected{background:#e0e8f0;color:#333}
QCheckBox{color:#333}QCheckBox::indicator:checked{background:#888;border-color:#888}
QProgressBar{border:1px solid #ddd;border-radius:4px;background:#f5f5f5;color:#555}QProgressBar::chunk{background:#aaa}
QScrollArea{border:none;background:transparent}QScrollBar:vertical{background:transparent;width:8px}QScrollBar::handle:vertical{background:#ccc;border-radius:4px}QScrollBar:horizontal{background:transparent;height:8px}QScrollBar::handle:horizontal{background:#ccc;border-radius:4px}
QSplitter::handle{background:#ddd;width:3px}
QFrame#card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:12px;margin-bottom:6px}
QStatusBar{background:#f0f0f0;color:#888;border-top:1px solid #ddd}QStatusBar QLabel{color:#888}
QTabWidget::pane{border:1px solid #ddd;background:#fff}QTabBar::tab{background:#eee;color:#777;padding:7px 18px;border:1px solid #ddd;margin-right:2px;border-radius:4px 4px 0 0}QTabBar::tab:selected{background:#fff;color:#333}QTabBar::tab:hover{color:#555}
"""
