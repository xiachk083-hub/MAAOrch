#!/usr/bin/env python3
"""等理智 — 每隔一段时间截图读大厅左上角的理智数，够用就退出。

用法:
    python -u tools/sanity_wait.py --need 15                 # 等到 >=15
    python -u tools/sanity_wait.py --need 15 --max-min 180    # 最多等 3 小时
    python -u tools/sanity_wait.py --read                     # 只读一次

为什么单独写：理智是本项目唯一的硬约束（这号一粒理智药都没有），
    开工前必须先知道够不够，别把 --potion 打开去撞源石兑换框。
读数 ROI 来自实测：1280x720 画面里理智数字在大厅左上 (800,130)-(1000,230)，
    顶栏「+」按钮、脑形图标、「理智/210」标签都在附近，所以 OCR 后只取
    纯数字且 <= 上限的那一段。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "tools"))

from _dev import resolve_dev  # noqa: E402

SHOT = PROJ / "runtime" / "_sanity.png"
# 只框住数字本身：把「理智/210」标签排除在外，否则 OCR 会把 6 读成 60
# （2026-09-23 实测踩坑：上界 230 会把 "/210" 框进来 ⇒ 误报 60 直接触发开工）
ROI = (800, 120, 930, 230)  # x0,y0,x1,y1（留足上下文，RapidOCR 才认得出那个数字）


def adb_exe() -> str:
    """找 adb：MuMu 自带的最稳（PATH 里的 adb 版本可能不认 MuMu 的转发端口）。"""
    for p in (r"D:\Xiach\MuMuPlayer\nx_main\adb.exe",
              r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe"):
        if pathlib.Path(p).exists():
            return p
    return "adb"


def grab(dev: str) -> bytes:
    return subprocess.run(
        [adb_exe(), "-s", dev, "exec-out", "screencap", "-p"],
        stdout=subprocess.PIPE, check=True).stdout


def read_sanity(dev: str) -> int | None:
    """截图 → OCR → 只认「理智数字」那个框里的纯数字。

    踩坑记录（2026-09-23）：先试过把 ROI 收到只剩数字（90x88），RapidOCR 的检测
    头认不出孤立的一个数字，直接返回空 ⇒ 读数 None。所以现在**框宽一点**，
    OCR 后再按坐标筛选 token —— 数字框实测在 x 820~915 / y 125~215，
    「/210」标签在 y>=230，不会被选中。
    """
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    raw = grab(dev)
    SHOT.write_bytes(raw)
    img = cv2.imread(str(SHOT))
    if img is None:
        return None
    x0, y0, x1, y1 = ROI
    crop = img[y0:y1, x0:x1]
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    hits, _ = RapidOCR()(crop)
    for box, text, conf in hits or []:
        t = text.strip()
        if not re.fullmatch(r"\d{1,3}", t) or float(conf) < 0.5:
            continue
        cx = (min(p[0] for p in box) + max(p[0] for p in box)) / 2 / 3 + x0
        cy = (min(p[1] for p in box) + max(p[1] for p in box)) / 2 / 3 + y0
        if 815 <= cx <= 920 and 120 <= cy <= 220:
            return int(t)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="1")
    ap.add_argument("--need", type=int, default=None)
    ap.add_argument("--interval", type=int, default=120)
    ap.add_argument("--max-min", type=int, default=240)
    ap.add_argument("--read", action="store_true", help="只读一次就退出")
    a = ap.parse_args()

    dev = resolve_dev(a.emu)
    val = read_sanity(dev)
    print(f"{time.strftime('%H:%M:%S')} 理智={val}", flush=True)
    if a.read or a.need is None:
        return 0 if val is not None else 1

    deadline = time.time() + a.max_min * 60
    while val is None or val < a.need:
        if time.time() > deadline:
            print(f"{time.strftime('%H:%M:%S')} 等满 {a.max_min} 分钟仍未到 "
                  f"{a.need}（最后读到 {val}）", flush=True)
            return 2
        time.sleep(a.interval)
        val = read_sanity(dev)
        print(f"{time.strftime('%H:%M:%S')} 理智={val}", flush=True)
    print(f"{time.strftime('%H:%M:%S')} 理智={val} >= {a.need}，可以开工", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
