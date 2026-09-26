#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面指纹入口 —— **薄壳**，唯一实现是 `tools/maaorch_service.py::page_of`。

⚠️ 2026-09-24 独立审计（第 N 次"两套判据并存"）：这个文件原来自己抄了一份
   `identify()`：读 `data/templates/pages/*.png`、自带一份 SPECS、自带
   `if t.shape >= g.shape: continue`（**和 service 里被修掉的是同一个 bug**）、
   而且**没有"未知"这个概念**（永远把 argmax 当答案）、也没有 margin 规则。
   结果就是：同一个页面在 service（`/page`、`/all`、`/panel`）和 CLI（`--page`）
   会给出两个不同答案 —— 这正是本项目反复踩的坑。

   现在只有一份判据：**`page_of()`**。本文件只负责把它的字段换个名字，
   让 `pages.py --json` / `fastnav.page()` / `bench_service.py` 这些老调用方不用改。

为什么留着这个文件而不是删掉：`fastnav.page()` 与 `bench_service.py` 按路径加载它；
删文件会让那些工具静默失效。留着薄壳 = 老调用方继续能用，且**不可能再分叉**。

用法:
    python tools/pages.py            # 判定当前屏幕
    python tools/pages.py --json
    python tools/pages.py --shot runtime/_x.png     # 判指定截图（离线）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev  # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"


def identify(img) -> dict:
    """**唯一实现的薄壳**：返回 `{page, score, scores, margin}`（老字段名）。

    与 `page_of()` 的对应关系：`score←page_score`、`scores←page_scores`、`margin←page_margin`。
    `page` 仍可能是「未知」—— 那是**正常的读数**（最佳 <0.80 或与第二名差 <0.06），
    调用方应当据此退回 OCR 导航，而不是硬认一个页面。
    """
    import maaorch_service as svc
    r = svc.page_of(img)
    return {"page": r.get("page"), "score": r.get("page_score", 0),
            "margin": r.get("page_margin"), "scores": r.get("page_scores", {})}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="1")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--shot", default="", help="判指定截图文件（离线，不截图）")
    a = ap.parse_args()

    import cv2
    if a.shot:
        img = cv2.imread(a.shot)
    else:
        dev = resolve_dev(a.emu)
        p = ROOT / "runtime" / "_pages.png"
        with open(p, "wb") as f:
            subprocess.run([ADB, "-s", dev, "exec-out", "screencap", "-p"],
                           stdout=f, timeout=60)
        img = cv2.imread(str(p))
    if img is None:
        print("拿不到画面")
        return 1

    t0 = time.perf_counter()
    res = identify(img)
    res["cost_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"page = {res['page']}（{res['score']}，margin {res['margin']}）"
              f"  判别耗时 {res['cost_ms']} ms")
        for k, v in sorted(res["scores"].items(), key=lambda kv: -kv[1]):
            print(f"   {k:8s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
