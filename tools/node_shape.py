#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**按节点形状判断"这关打过没有"** —— 用户 2026-09-24 给的关键判据：

    · 关卡**没打过** ⇒ 地图上就是**一个标准的长方形**（白色标签，纯的）
    · 关卡**打过一次** ⇒ 长方形**左边会多出一个六角形**（蓝色/白色六边形标记）

这一条比"点进面板读徽章"快得多也稳得多：**在图上就能看出哪些关是没打的（=还有首通源石）**，
根本不需要逐关点进详情页。之前我在"完成进度网格页"上把卡片自带的六边形当成"地图节点"、
又用一个垃圾分判 `⊘ 无源石`，全是因为没看这层形状语义。

用法:
    python tools/node_shape.py --shot runtime/_map_now.png     # 离线验证
    python tools/node_shape.py                                  # 抓当前屏
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")


def analyze(img) -> list[dict]:
    """找白色长方形标签（关卡名条），看它左边有没有六角形 ⇒ cleared/or not。"""
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0].astype(np.int16), hsv[:, :, 1].astype(np.int16), hsv[:, :, 2].astype(np.int16)
    white = ((s < 85) & (v > 175)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, hh = cv2.boundingRect(c)
        if w < 70 or not (16 <= hh <= 90) or w / max(hh, 1) < 1.8:
            continue
        # 看标签**左侧** 90px 内有没有"六角形标记"：彩色（蓝/白）+ 面积中等 + 形状接近圆/六边形
        x0 = max(0, x - 95)
        roi = img[max(0, y - 30):y + hh + 30, x0:x]
        cleared = False
        if roi.size:
            rh, rs, rv = (cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)[:, :, i].astype(np.int16) for i in range(3))
            colored = (((rs > 60) & (rv > 120)) | ((rs < 60) & (rv > 220))).astype(np.uint8) * 255
            colored = cv2.morphologyEx(colored, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
            cs, _ = cv2.findContours(colored, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cc in cs:
                cx, cy, cw, ch = cv2.boundingRect(cc)
                if cw < 18 or ch < 18:
                    continue
                if 0.6 <= cw / max(ch, 1) <= 1.7 and cw * ch >= 400:      # 近方形/六边形
                    cleared = True
                    break
        out.append({"x": x + w // 2, "y": y + hh // 2, "box": [x, y, x + w, y + hh],
                    "cleared": cleared})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    import cv2
    if a.shot:
        p = pathlib.Path(a.shot)
        img = cv2.imread(str(p if p.is_absolute() else ROOT / p))
    else:
        p = ROOT / "runtime" / "_shape.png"
        urllib.request.urlopen(f"http://127.0.0.1:8765/shot?path={p}", timeout=25).read()
        img = cv2.imread(str(p))
    if img is None:
        print(json.dumps({"ok": False, "detail": "拿不到画面"}, ensure_ascii=False))
        return 1
    items = analyze(img)
    unplayed = [it for it in items if not it["cleared"]]
    if a.json:
        print(json.dumps({"ok": True, "labels": len(items), "unplayed": unplayed, "all": items},
                         ensure_ascii=False))
    else:
        print(f"找到 {len(items)} 个关卡名条；其中**看起来没打过**（长方形，无六角形）{len(unplayed)} 个：")
        for it in items:
            print(f"  ({it['x']:4d},{it['y']:3d})  {'已打过(有六角形)' if it['cleared'] else '★没打过(纯长方形)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
