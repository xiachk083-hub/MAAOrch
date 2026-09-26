# -*- coding: utf-8 -*-
"""BI-EX-7 箱子注入器 v3：模板匹配找箱子卡（OCR 认不出徽标），两种手势并自证。

v2 的教训：`X10` 徽标是小字，rapidocr 在那一刻读不出来（v10/bi7k/bi7l 三局都卡在「找不到徽标」）。
改成用 bi7f_seq/055 那张部署栏里**箱子图标**的模板做 matchTemplate —— 箱子是个白色立体方块，
和任何干员卡面都不同，matchTemplate 分数很高。

用法：
    python tools/ex7_crate_probe.py --tile 3,2            # 假定战斗已在跑，直接找卡+放
    python tools/ex7_crate_probe.py --tile 3,2 --wait      # 先等战斗 HUD 出现再放
"""
from __future__ import annotations

import argparse, pathlib, re, subprocess, sys, time

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

P = pathlib.Path(r"D:\1-2\MAAOrch")
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"   # #1（官服）
A = (78.4567, 1.5641, 276.11)
B = (-9.0487, 64.2118, 150.46)
HUD = (980, 530, 1280, 580)
BAR = (430, 560, 1280, 700)
TPL_PATH = P / "data" / "templates" / "ex7_crate_card.png"
_ocr = RapidOCR()


def shot():
    r = subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], capture_output=True)
    if not r.returncode or r.stdout:
        return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
    return None


def ocr_lines(img, box, scale=2.5):
    x1, y1, x2, y2 = box
    crop = cv2.resize(img[y1:y2, x1:x2], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    res, _ = _ocr(crop)
    return [(t, (min(q[0] for q in b)) / scale + x1, (min(q[1] for q in b)) / scale + y1) for b, t, _s in (res or [])]


def rest_slots(img):
    txt = "".join(t for t, _x, _y in ocr_lines(img, HUD))
    if "剩余" not in txt:
        return None
    m = re.findall(r"(\d+)", txt)
    return int(m[-1]) if m else None


def find_crate(img, tpl):
    x1, y1, x2, y2 = BAR
    reg = img[y1:y2, x1:x2]
    res = cv2.matchTemplate(reg, tpl, cv2.TM_CCOEFF_NORMED)
    _mn, mx, _ml, ml = cv2.minMaxLoc(res)
    th, tw = tpl.shape[:2]
    cx, cy = x1 + ml[0] + tw // 2, y1 + ml[1] + th // 2
    return mx, cx, cy


def tap(x, y):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", str(int(x)), str(int(y))], capture_output=True)


def swipe(x1, y1, x2, y2, ms):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "swipe", str(int(x1)), str(int(y1)),
                    str(int(x2)), str(int(y2)), str(int(ms))], capture_output=True)


def to_px(col, row):
    return (int(A[0] * col + A[1] * row + A[2]), int(B[0] * col + B[1] * row + B[2]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tile", default="3,2")
    ap.add_argument("--out")
    ap.add_argument("--wait", action="store_true", help="先等战斗 HUD 出现")
    ap.add_argument("--wait-timeout", type=float, default=200.0)
    a = ap.parse_args()
    col, row = (int(v) for v in a.tile.split(","))
    tx, ty = to_px(col, row)
    tpl = cv2.imread(str(TPL_PATH))
    if tpl is None:
        print("✗ 缺模板 %s" % TPL_PATH)
        return 2
    print("目标格 (%d,%d) -> (%d,%d)；模板 %s %s" % (col, row, tx, ty, TPL_PATH.name, tpl.shape), flush=True)

    t0 = time.time()
    if a.wait:
        while time.time() - t0 < a.wait_timeout:
            img = shot()
            if img is not None and rest_slots(img) is not None:
                print("  战斗 HUD 出现（t=%.1fs）" % (time.time() - t0), flush=True)
                break
            time.sleep(2.0)

    img = shot()
    if img is None:
        print("✗ 截图失败")
        return 1
    score, cx, cy = find_crate(img, tpl)
    print("  模板匹配：score=%.3f 卡面中心≈(%d,%d)" % (score, cx, cy), flush=True)
    if score < 0.45:
        print("✗ 匹配分数过低，箱子卡可能不在栏里（未上场/已用完/换页）")
        if a.out:
            cv2.imwrite(a.out.replace(".png", "_fail.png"), img)
        return 3
    before = img[max(0, ty - 26):ty + 26, max(0, tx - 26):tx + 26].astype(np.int16)
    if a.out:
        cv2.imwrite(a.out.replace(".png", "_before.png"), img)

    for label, act in (("拖拽 2500ms", lambda: swipe(cx, cy, tx, ty, 2500)),
                       ("点卡->点格", lambda: (tap(cx, cy), time.sleep(1.2), tap(tx, ty)))):
        print("  手势：%s" % label, flush=True)
        act()
        time.sleep(2.5)
        img2 = shot()
        s2, c2x, c2y = find_crate(img2, tpl)
        after = img2[max(0, ty - 26):ty + 26, max(0, tx - 26):tx + 26].astype(np.int16)
        diff = float(np.abs(after - before).mean()) if after.shape == before.shape else -1.0
        print("    匹配 score=%.3f @(%d,%d)（原 %d,%d）；目标格像素差=%.1f" % (s2, c2x, c2y, cx, cy, diff), flush=True)
        if a.out:
            cv2.imwrite(a.out, img2)
        if s2 < 0.30 or diff > 8.0:
            print("  ⇒ 判定：%s 似乎生效了（卡没了/格子画面变了）" % label, flush=True)
            return 0
    print("  ⇒ 两种手势都没看出变化", flush=True)
    return 4


if __name__ == "__main__":
    sys.exit(main())
