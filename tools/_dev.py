#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_dev.py —— 模拟器 adb 地址的**共享动态解析**（2026-09-23 新写）

为什么要有这个文件:
    MuMu 12 实例一重启，adb 转发端口就会漂（实测 16416 → **16417**），
    而仓库里一堆 L3 工具（`mt_nav.py` / `bi_*.py` / `stage_attempt.py` …）都把
    `DEV = "127.0.0.1:16416"` 写死在文件里 ⇒ 端口一漂，它们全部变成
    `cv2.error: !ssize.empty()` / `cannot connect (10061)`，而且**报错看不出根因**
    （2026-09-23 实测：mt_nav 挂在 rapidocr 的 LoadImageError 上，其实是截图空文件）。

用法（在被修的工具里）:
    import pathlib, sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _dev import resolve_dev
    DEV = resolve_dev()          # 默认 #1（官服）
"""
from __future__ import annotations

import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
FALLBACK = "127.0.0.1:16416"


def resolve_dev(emu: str = "1") -> str:
    """返回 "127.0.0.1:<port>"。优先仓库里经验证的 detect_emu_adb，失败才兜底。"""
    try:
        if str(PROJ) not in sys.path:
            sys.path.insert(0, str(PROJ))
        from infrastructure.task_constants import detect_emu_adb
        addr = detect_emu_adb(str(emu))
        if addr:
            return addr
    except Exception:                                             # noqa: BLE001
        pass
    return FALLBACK
