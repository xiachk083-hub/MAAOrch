#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tap / swipe 一条命令搞定（自动解析 MuMu 的 adb 端口）。

为什么要单独一个：`mt_nav.py` / `enter_event.py` 里都有 tap，但它们是库不是命令；
    Node 侧的 `event_sweep.mjs` 想在"打完结算页"上点一下（复核三星前必须先把
    结算页/好友申请弹层点掉），又不想把 adb 路径、端口漂移这些烂事复制一遍。

用法:
    python tools/tap.py 1131 264              # 点一下
    python tools/tap.py 640 630 --wait 8      # 点完等 8 秒
    python tools/tap.py --swipe 1100 400 150 400
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _dev import resolve_dev  # noqa: E402


def adb_exe() -> str:
    for p in (r"D:\Xiach\MuMuPlayer\nx_main\adb.exe",
              r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe"):
        if pathlib.Path(p).exists():
            return p
    return "adb"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("coords", nargs="*", type=int)
    ap.add_argument("--emu", default="1")
    ap.add_argument("--swipe", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    ap.add_argument("--wait", type=float, default=2.0)
    a = ap.parse_args()
    dev = resolve_dev(a.emu)
    if a.swipe:
        cmd = ["shell", "input", "swipe", *map(str, a.swipe), "500"]
    elif len(a.coords) == 2:
        cmd = ["shell", "input", "tap", *map(str, a.coords)]
    else:
        print(__doc__)
        return 2
    subprocess.run([adb_exe(), "-s", dev, *cmd], timeout=30)
    time.sleep(a.wait)
    print(f"ok {dev} {' '.join(cmd[-4:])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
