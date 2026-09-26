#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真·最快取帧：直接用 ctypes 调 MaaCore 的 **BGR 原始帧**接口 + 强制新鲜截图。

为什么不用 Python 绑定：绑定只暴露 `get_image()`（PNG，且拿的是 MAA 上次任务的**缓存帧** ——
2026-09-24 实测：按一次 BACK 让画面必然变化，连续三次 get_image 的 MD5 完全一样，
说明它根本不重新截图，用它做"复核源石徽章"会得出错误结论）。

MaaCore 实际导出的接口（从 MaaCore.dll 符号里挖出来的）：
    AsstAsyncScreencap(handle, block)      ← 主动(异步)截一张**新**图，block=True 等它完成
    AsstGetImageBgr(handle, buf, size)     ← 直接给 BGR 原始像素（省掉 PNG 编解码）
    AsstSetConnectionExtras(handle, ...)   ← 选截图通道（MuMu 私有通道最快）

本文件就干这三件事，实测中位 ~25~45ms（含 Python 侧拷贝）。

用法:
    python tools/maa_frame.py --bench         # 连打 20 帧，报中位/最慢
    python tools/maa_frame.py --save runtime/_mf.png
    python tools/maa_frame.py --freshness     # 验证"两帧之间按 BACK，图会不会变"
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
MAA_DIR = ROOT / "services" / "maa" / "v6.11.1"
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev  # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
W, H = 1280, 720


class Core:
    def __init__(self, emu: str = "1", config: str = "MuMuEmulator12", extras: str = ""):
        self.lib = ctypes.CDLL(str(MAA_DIR / "MaaCore.dll"))
        self.lib.AsstLoadResource.argtypes = [ctypes.c_char_p]
        self.lib.AsstLoadResource.restype = ctypes.c_bool
        self.lib.AsstCreate.restype = ctypes.c_void_p
        self.lib.AsstConnect.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        self.lib.AsstConnect.restype = ctypes.c_bool
        self.lib.AsstAsyncScreencap.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self.lib.AsstAsyncScreencap.restype = ctypes.c_bool
        self.lib.AsstGetImageBgr.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64]
        self.lib.AsstGetImageBgr.restype = ctypes.c_uint64
        # ⚠️ 必须显式声明 argtypes：不声明时 ctypes 会把 64 位句柄当 int 传，
        #   直接报 "OverflowError: int too long to convert"（2026-09-24 踩过）。
        self.lib.AsstSetConnectionExtras.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.lib.AsstSetConnectionExtras.restype = ctypes.c_bool
        self.lib.AsstDestroy.argtypes = [ctypes.c_void_p]

        # 注意：AsstLoadResource(path) 要的是**含 resource/ 的目录**（即 MAA 根目录），
        # 不是 resource 子目录本身 —— 传错会直接返回 False（2026-09-24 踩过）。
        if not self.lib.AsstLoadResource(str(MAA_DIR).encode()):
            raise SystemExit("AsstLoadResource 失败")
        self.h = self.lib.AsstCreate()
        if not self.h:
            raise SystemExit("AsstCreate 失败")
        if extras:
            self.lib.AsstSetConnectionExtras(self.h, extras.encode())
        self.dev = resolve_dev(emu)
        if not self.lib.AsstConnect(self.h, ADB.encode(), self.dev.encode(), config.encode()):
            raise SystemExit(f"AsstConnect 失败：{self.dev}（config={config}）")
        self.buf = ctypes.create_string_buffer(W * H * 3 + 4096)

    def fresh(self):
        """强制截一张新图；给多少时间都不新鲜时返回 None。"""
        ok = self.lib.AsstAsyncScreencap(self.h, True)
        n = self.lib.AsstGetImageBgr(self.h, self.buf, len(self.buf))
        if not ok or not n:
            return None
        import cv2
        import numpy as np
        arr = np.frombuffer(self.buf.raw[:n], dtype=np.uint8)
        if n == W * H * 3:
            return arr.reshape((H, W, 3))
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)          # 万一给的是编码图
        return img

    def close(self):
        try:
            self.lib.AsstDestroy(self.h)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="1")
    ap.add_argument("--config", default="MuMuEmulator12")
    ap.add_argument("--extras", default="")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--freshness", action="store_true")
    ap.add_argument("--save", default="")
    a = ap.parse_args()

    t0 = time.perf_counter()
    import cv2
    c = Core(a.emu, a.config, a.extras)
    print(f"连接 {resolve_dev(a.emu)} 用时 {(time.perf_counter()-t0)*1000:.0f} ms", file=sys.stderr)

    if a.freshness:
        import numpy as np
        f1 = c.fresh()
        subprocess.run([ADB, "-s", resolve_dev(a.emu), "shell", "input", "keyevent", "4"], timeout=30)
        time.sleep(1.2)
        f2 = c.fresh()
        if f1 is None or f2 is None:
            print("取帧失败")
            return 1
        d = float(np.abs(f1.astype(int) - f2.astype(int)).mean())
        print(f"BACK 前后两帧：MD5 {hashlib.md5(f1.tobytes()).hexdigest()[:8]} vs "
              f"{hashlib.md5(f2.tobytes()).hexdigest()[:8]}｜平均像素差 {d:.2f}"
              f" ⇒ {'帧是新鲜的 ✓' if d > 1 else '帧没变（仍是缓存）✗'}")
        return 0

    ts = []
    n = 20 if a.bench else 1
    for _ in range(n):
        t = time.perf_counter()
        img = c.fresh()
        ts.append((time.perf_counter() - t) * 1000)
        if img is None:
            print("取帧失败")
            return 1
    ts.sort()
    print(f"BGR 原始帧 {n} 次：中位 {ts[len(ts)//2]:.1f} ms（min {ts[0]:.1f} / max {ts[-1]:.1f}）")
    if a.save:
        cv2.imwrite(a.save, img)
        print("已存：", a.save)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
