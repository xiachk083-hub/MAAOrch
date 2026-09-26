#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""活动卡片用**封面模板**认，不用 OCR 认艺术字（用户 2026-09-24 的建议）。

为什么：完成进度网格里每张卡的标题是**艺术字**（灯火序曲/红丝绒/熔炉还魂记…），OCR 又慢又不稳；
而**卡片封面图是不变的** ⇒ 裁一次存模板，以后 5ms 就能认出"这张卡是哪个活动"，
而且**模板匹配与滚动位置无关**：卡在当前屏的哪一行都能匹配到 ⇒ 连"归零"都不再是硬前提。

用法:
    # 学一张（我在截图里读出来后，给出卡片封面的框）
    python tools/event_templates.py --learn runtime/_grid_now.png 红丝绒 80,290,270,440

    # 认一屏（列出当前屏上所有能认出的活动卡 + 位置）
    python tools/event_templates.py --match runtime/_grid_now.png

模板目录: data/templates/events/<活动名>.png
"""
from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
TPL = ROOT / "data" / "templates" / "events"


def learn(shot: str, name: str, box: str) -> int:
    import cv2
    img = cv2.imread(str(ROOT / shot if not pathlib.Path(shot).is_absolute() else shot))
    if img is None:
        print("读不到截图", shot)
        return 1
    x0, y0, x1, y1 = (int(v) for v in box.split(","))
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        print("裁剪为空")
        return 1
    TPL.mkdir(parents=True, exist_ok=True)
    p = TPL / f"{name}.png"
    cv2.imwrite(str(p), crop)
    print(f"已学：{name}  {crop.shape[1]}x{crop.shape[0]}  → {p}")
    return 0


def match(shot: str, thr: float = 0.80) -> int:
    import cv2
    img = cv2.imread(str(ROOT / shot if not pathlib.Path(shot).is_absolute() else shot))
    if img is None:
        print("读不到截图", shot)
        return 1
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hits = []
    for p in sorted(TPL.glob("*.png")):
        t = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if t is None or t.shape[0] >= g.shape[0] or t.shape[1] >= g.shape[1]:
            continue
        r = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
        _mn, mx, _ml, loc = cv2.minMaxLoc(r)
        if mx >= thr:
            hits.append({"event": p.stem, "score": round(float(mx), 3),
                         "x": int(loc[0] + t.shape[1] / 2), "y": int(loc[1] + t.shape[0] / 2)})
    hits.sort(key=lambda h: h["score"], reverse=True)
    print(json.dumps({"found": len(hits), "hits": hits}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--learn", nargs=3, metavar=("SHOT", "NAME", "X0,Y0,X1,Y1"))
    ap.add_argument("--match")
    ap.add_argument("--thr", type=float, default=0.80)
    a = ap.parse_args()
    if a.learn:
        return learn(*a.learn)
    if a.match:
        return match(a.match, a.thr)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
