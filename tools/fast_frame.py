#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最快取帧：**借 MaaCore 自己的截图通道**（用户指出 MAA 实测 MumuExtras 只要 19ms）

我们自己那套 `adb exec-out screencap` 已经优化到 raw 180~220ms，但 MAA 的 GUI 里那四个数说明
还有更快的路：
    RawByNc 265ms / RawWithGzip 319ms / Encode 896ms / **MumuExtras 19ms**
19ms 是 MaaCore 走 MuMu 模拟器的私有通道拿的（不走 adb shell screencap）。

本脚本用 MAA 自带的 Python 绑定（services/maa/v6.11.1/Python）起一个**只读**实例，
`connect(..., "MuMuEmulator12")` 让 MaaCore 用 MuMu 通道，然后 `get_image()` 取帧。
只做 get_image（不 append_task、不接管点击），所以不会跟正在跑的 MAA 抢操作。

用法:
    python tools/fast_frame.py                 # 连打 20 帧，打印每帧耗时与均值
    python tools/fast_frame.py --save runtime/_ff.png
    python tools/fast_frame.py --json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
MAA_DIR = ROOT / "services" / "maa" / "v6.11.1"
sys.path.insert(0, str(MAA_DIR / "Python"))
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev  # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"


def make_client(emu: str = "1", config: str = "MuMuEmulator12"):
    from asst.asst import Asst
    from asst.utils import InstanceOptionType

    # ⚠️ 顺序很关键：**必须先 load（它把 MaaCore.dll 装进 Asst.__lib），再 Asst()**
    #   反了的话 `AsstCreate()` 会因为 __lib 还不存在直接抛 AttributeError。
    if not Asst.load(path=str(MAA_DIR)):
        raise SystemExit(f"MaaCore 加载失败：{MAA_DIR}")
    asst = Asst()
    asst.set_instance_option(InstanceOptionType.touch_type, "maatouch")
    if not asst.connect(ADB, resolve_dev(emu), config):
        raise SystemExit(f"连接失败：{resolve_dev(emu)}（config={config}）")
    return asst


def decode(png: bytes):
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="1")
    ap.add_argument("--config", default="MuMuEmulator12")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--save", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    t0 = time.perf_counter()
    asst = make_client(a.emu, a.config)
    conn_ms = (time.perf_counter() - t0) * 1000

    ts, first = [], None
    for i in range(a.n):
        t = time.perf_counter()
        png = asst.get_image(720 * 1280 * 4)
        dt = (time.perf_counter() - t) * 1000
        if not png:
            print(f"第 {i+1} 帧拿不到（get_image 返回空）")
            continue
        ts.append(dt)
        if first is None:
            first = png
        time.sleep(0.05)
    if not ts:
        print("一帧都没取到")
        return 1
    ts_sorted = sorted(ts)
    out = {
        "config": a.config, "connect_ms": round(conn_ms), "frames": len(ts),
        "median_ms": round(ts_sorted[len(ts_sorted) // 2], 1),
        "min_ms": round(ts_sorted[0], 1), "max_ms": round(ts_sorted[-1], 1),
        "bytes": len(first or b""),
    }
    if a.save and first:
        pathlib.Path(a.save).write_bytes(first)
        out["saved"] = a.save
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"MaaCore 截图通道（config={a.config}）连接 {out['connect_ms']}ms，"
              f"{out['frames']} 帧：中位 {out['median_ms']}ms（min {out['min_ms']} / max {out['max_ms']}），PNG {out['bytes']} 字节")
        if a.save:
            print("已存：", a.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
