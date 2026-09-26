"""数结算页的星星（1~3）—— 终于能把"三星"这件事自动化判定了。

怎么找到的（2026-09-21）：在通关页与失败页做**差分**，发现 `y≈280..360, x≈30..340` 这一带
通关页有 3691 个亮像素、失败页只有 925 ⇒ 那就是星级行；把该带渲染成 ASCII 后能看出是**两个星形图标**
（每个约 55x55，形如带有两个下脚的星）。失败页在这一带没有星形。

用法:
    python tools/ce6_star_count.py runtime/_ce6/v40b_result.png [--ascii]
返回:
    打印判定到的星数（1~3）；0 表示没认出（多半是失败页或还没结算完）。
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

BAND = (30, 340, 280, 360)   # x0,x1,y0,y1


def page_kind(img) -> str:
    """先判这一页是"通关页"还是"失败页" —— 否则失败页的大字标题会被误当成星星 ✗。

    依据（2026-09-21 实测）：失败页标题「任务失败 / MISSION FAILED」正好落在 y≈280..360 这个带里，
    我的形状过滤会把其中 3 个字当成 3 颗星 ⇒ 报出**假的三星**（v41a 就这么被骗过一次）。
    通关页标题「行动结束 / MISSION RESULTS」在更高的位置（y≈179..261），带里只剩星星 ✓。
    判据：OCR 左半屏找关键词；找不到关键词就返回 unknown（宁可说"没认出"）。
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return "unknown"
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCR()
    crop = img[150:720, 20:520]
    res, _ = _ENGINE(crop)
    txt = "".join(t[1] for t in res) if res else ""
    up = txt.upper()
    # 失败：有「理智返还」或「任务失败」；失败页的标题也是「行动结束」，只能靠这两条区分 ✓
    if ("理智返还" in txt) or ("任务失败" in txt) or ("FAILED" in up):
        return "fail"
    if ("声望" in txt) or ("信赖" in txt):
        return "clear"
    if ("行动结束" in txt) or ("RESULTS" in up):
        return "maybe-clear"      # 像通关页但没读到奖励字样
    return "unknown"


_ENGINE = None


def star_blobs(img):
    x0, x1, y0, y1 = BAND
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    m = (g[y0:y1, x0:x1] > 150).astype(np.uint8)
    # ⚠️ 别用大核 CLOSE：9x9 会把相邻两颗星粘成一个 120px 宽的块，反而被尺寸过滤掉 ✗
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        # 星形：约 35~80 见方、填充率 15%~80%
        fill = a / float(max(1, w * h))
        if a >= 250 and 20 <= w <= 85 and 20 <= h <= 85 and 0.12 <= fill <= 0.8:
            out.append((int(cent[i][0]) + x0, int(cent[i][1]) + y0, a, w, h, fill))
    out.sort(key=lambda b: b[0])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=pathlib.Path)
    ap.add_argument("--ascii", action="store_true")
    args = ap.parse_args()

    img = cv2.imread(str(args.image))
    if img is None:
        raise SystemExit("read fail: %s" % args.image)
    kind = page_kind(img)
    print("%s -> 页面类型：%s" % (args.image.name, kind))
    if kind == "fail":
        print("=> 星级 = 0（失败页，没有星星）")
        return 0
    bl = star_blobs(img)
    if kind == "unknown":
        print("=> 页面类型未认出 ⇒ 不判星（这一栏的形状统计经常把标题/图标当成星，别信）")
        return 2
    print("%s -> 判定的星形图标 %d 个" % (args.image.name, len(bl)))
    for x, y, a, w, h, fill in bl:
        print("   (%4d,%4d) 面积%-5d %dx%d 填充率%.2f" % (x, y, a, w, h, fill))
    print("=> 星级 = %s" % (len(bl) if bl else "未认出（可能是失败页）"))

    if args.ascii:
        x0, x1, y0, y1 = BAND
        sub = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[y0:y1, x0:x1] > 150).astype(np.uint8)
        s = cv2.resize(sub, (103, 20), interpolation=cv2.INTER_AREA)
        for r in s:
            print("   " + "".join("#" if c > 0.4 else ("+" if c > 0.15 else ".") for c in r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
