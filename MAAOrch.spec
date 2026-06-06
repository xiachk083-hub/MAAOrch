# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MAAOrch — MAA 多账号批量管理工具.

Usage:
    pyinstaller MAAOrch.spec       # build
    pyinstaller --clean MAAOrch.spec  # clean build
"""

import sys, os
from pathlib import Path

# PyInstaller resolves paths relative to the spec file's directory
BASE = Path('.')

a = Analysis(
    ['main.pyw'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('maa-cli', 'maa-cli'),
        ('themes.py', '.'),
        ('task_constants.py', '.'),
        ('callbacks.py', '.'),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "urllib.request",
        "json",
        "re",
        "subprocess",
        # Project modules
        "config",
        "utils",
        "dialogs",
        "updater",
        "runner",
        "launch_queue",
        "stats",
        "account",
        "emu_ops",
        "config_ops",
        "log_ops",
        "maint_ops",
        "pipeline_thread",
        "schedule_thread",
        "api_server",
        "background",
        "callbacks",
        "task_constants",
        "themes",
        # UI submodules
        "ui.dashboard",
        "ui.accounts_panel",
        "ui.queue_panel",
        "ui.config_cards",
        "ui.schedule_panel",
        "ui",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "email",
        "http",
        "xml",
        "pydoc",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MAAOrch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # .pyw = windowed, no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' if Path('icon.ico').exists() else None,
)
