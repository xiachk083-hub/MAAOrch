#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把"一次查看"整条链路的**每一个点**都量出来（2026-09-24，回答"每个点的延迟是多少"）。

链路：HTTP → 连接服务 → 取帧（MaaCore/MuMu 或 adb raw）→ 解码 → 判据（像素/模板）
      →（可选）OCR 文字 →（可选）页面指纹 →（可选）点击 + 等画面稳定

用法: python tools/bench_service.py [--port 8765]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev  # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"


def timed(fn, n=5, warm=1):
    for _ in range(warm):
        try:
            fn()
        except Exception:
            pass
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        try:
            fn()
            xs.append((time.perf_counter() - t) * 1000)
        except Exception as e:                       # noqa: BLE001
            print("   失败:", e)
    if not xs:
        return None
    return {"median": round(statistics.median(xs), 1), "min": round(min(xs), 1), "max": round(max(xs), 1), "n": len(xs)}


def get(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    dev = resolve_dev("1")

    print("【一次查看：每个点的实测延迟】")
    rows = []

    r = timed(lambda: get(f"{base}/health"), n=5)
    rows.append(("/health  HTTP 往返（服务是否活着）", r))
    r = timed(lambda: get(f"{base}/state"), n=5)
    rows.append(("/state   读队列/账本状态（纯文件）", r))
    r = timed(lambda: get(f"{base}/panel"), n=10)
    rows.append(("/panel   取帧 + 星星 + 徽章（不 OCR）", r))
    r = timed(lambda: get(f"{base}/look?roi=980,20,1280,120"), n=3)
    rows.append(("/look   局部 OCR（300x100 区域）", r))
    r = timed(lambda: get(f"{base}/look"), n=2)
    rows.append(("/look   全屏 OCR（1280x720）", r))

    def tap():
        subprocess.run([ADB, "-s", dev, "shell", "input", "tap", "640", "400"],
                       stdout=subprocess.DEVNULL, timeout=30)
    r = timed(tap, n=3)
    rows.append(("adb input tap（一次点击）", r))

    # 页面指纹（本地，用服务刚存的帧）
    get(f"{base}/shot?path={ROOT / 'runtime' / '_bench_page.png'}")
    import cv2
    sys.path.insert(0, str(ROOT / "tools"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("pages", ROOT / "tools" / "pages.py")
    pages = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pages)                    # type: ignore
    img = cv2.imread(str(ROOT / "runtime" / "_bench_page.png"))
    r = timed(lambda: pages.identify(img), n=5)
    rows.append(("页面指纹 6 个模板匹配（判断在哪一屏）", r))

    for name, r in rows:
        if r:
            print(f"  {name:38s} 中位 {r['median']:8.1f} ms   (min {r['min']:7.1f} / max {r['max']:7.1f}, n={r['n']})")
        else:
            print(f"  {name:38s} 失败")
    print("\n注：只有需要 OCR 的两条会到几百毫秒以上；判面板/判页面都在 100ms 量级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
