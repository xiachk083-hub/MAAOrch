#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_primes.py —— 读大厅顶栏的**至纯源石**数量（目标计量单位，2026-09-23 新写）

为什么需要它:
    目标是"+100 源石"，所以账本必须有**独立于 MAA 日志的硬证据**：
    每打几关读一次顶栏数字，涨没涨一眼可判（MAA 的"MissionCompleted"之类只是过程信号）。

做法:
    ① `tools/maa_home.py` 把游戏送到大厅（StartUp 不启动客户端，不消耗任何资源）
    ② 截图 → 裁顶栏右侧（源石图标 + 数字）→ RapidOCR 认数字
    ③ 输出一行 JSON {"primes": int, "raw": "..."}；认不出时 primes = -1

用法:
    python tools/read_primes.py [--emu 1] [--no-home] [--keep runtime/_primes.png]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

import cv2
from rapidocr_onnxruntime import RapidOCR

PROJ = pathlib.Path(__file__).resolve().parent.parent
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"


def resolve_dev(emu: str = "1") -> str:
    """动态取 ab 地址（实例重启后端口会漂：16416→16417，2026-09-23 实测）。"""
    try:
        sys.path.insert(0, str(PROJ))
        from infrastructure.task_constants import detect_emu_adb
        addr = detect_emu_adb(emu)
        if addr:
            return addr
    except Exception:                                             # noqa: BLE001
        pass
    return "127.0.0.1:16416"


DEV = resolve_dev("1")
# 顶栏右侧：源石图标(≈1090,36) + 数字(≈1130~1165,36)。
# **右边界要卡在「+」按钮左边**（否则 `+` 会被 OCR 成 6/8/0，把 18 变成 168 ✗）。
ROI = (1075, 12, 1170, 60)
_engine = None


def ocr_words(img):
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    res, _ = _engine(img)
    return [t for _b, t, *_ in (res or [])]


def screencap(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return cv2.imread(str(path))


def to_lobby(shot, tries: int = 8) -> bool:
    """按返回把界面弄回**大厅**（顶栏才有源石数字）。

    实测（2026-09-23）：MAA 的 StartUp（`start_game_enabled=False`）**不会**从章节地图回大厅，
    它只清弹窗 ⇒ 想读顶栏必须自己按返回；而大厅再按一次返回会弹「是否确认退出游戏」，
    那一下要点**取消**(439,475)，点错就把游戏关掉。
    """
    for _ in range(tries):
        img = screencap(shot)
        if img is None:
            return False
        words = ocr_words(img)
        blob = " ".join(words)
        if "终端" in blob and "仓库" in blob:
            return True
        if "是否确认退出游戏" in blob:
            subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", "439", "475"], timeout=30)
            time.sleep(2)
            continue
        subprocess.run([ADB, "-s", DEV, "shell", "input", "keyevent", "KEYCODE_BACK"], timeout=30)
        time.sleep(2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="1")
    ap.add_argument("--no-home", action="store_true", help="游戏已经在大厅，不再跑 maa_home / 不按返回")
    ap.add_argument("--keep", default="runtime/_primes.png")
    a = ap.parse_args()

    shot = PROJ / a.keep
    if not a.no_home:
        subprocess.run([sys.executable, str(PROJ / "tools" / "maa_home.py"), "--emu", a.emu],
                       capture_output=True, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not to_lobby(shot):
            print(json.dumps({"primes": -1, "err": "回不到大厅（读不到顶栏）"}, ensure_ascii=False))
            return 1

    img = screencap(shot)
    if img is None:
        print(json.dumps({"primes": -1, "err": "截图失败"}, ensure_ascii=False))
        return 1
    x0, y0, x1, y1 = ROI
    crop = img[y0:y1, x0:x1]
    # **先放大 2 倍再 OCR**（2026-09-23 补）：顶栏数字只有十几像素高，RapidOCR 会把
    # `18` 读成 `8`/`68`/`74`（漏读或错读首位）⇒ 放大后识别率明显稳定。
    crop = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    # **按 x 位置取**（2026-09-23 修）：源石图标+数字在顶栏最右（crop 内 x≈180~250），
    # 而"合成玉/龙门币/等级"等数字会混进来（实测 `max(nums)` 把 712、120 当成了源石 ✗）。
    # OCR 会把数字和右边的「+」粘成 `12+`→`120`、`9+`→`90`，所以只取**开头那段数字**，
    # 并在最右一列里挑。
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    res, _ = _engine(crop)
    cands = []
    for box, text, *_ in (res or []):
        xs = [p[0] for p in box]
        cx = int(sum(xs) / len(xs))
        m = re.match(r"\s*(\d{1,4})", text)
        if m:
            cands.append((cx, int(m.group(1))))
    best = -1
    # **把挨得近的数字块拼起来**（实测 OCR 会把「12」拆成 `1`(cx=42) 和 `2`(cx=65) 两块 ✗）
    merged = []
    for cx, n in sorted(cands):
        if merged and cx - merged[-1][0] <= 45:
            merged[-1] = (cx, int(str(merged[-1][1]) + str(n)))
        else:
            merged.append((cx, n))
    # 挑法：**1~3 位里最小的那个**（源石数总比"合成玉/龙门币/等级"小一个数量级，
    # 而「+」被 OCR 成 0 会让 12→120 之类的假值变大）⇒ 取小值里最小的最稳。
    small = [n for _cx, n in merged if 0 < n < 1000]
    best = min(small) if small else (merged[-1][1] if merged else -1)
    print(json.dumps({"primes": best, "cands": cands, "merged": merged}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
