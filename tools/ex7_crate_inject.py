# -*- coding: utf-8 -*-
"""BI-EX-7 箱子注入器 v2：自动找箱子卡、两种手势、并用「X10→X9」徽标 + 目标格像素差自证。

为什么需要（2026-09-21）：v10 用固定卡位 (619,637) 拖拽，事后逐帧比对发现**箱子根本没放上去**
（注入前后 (3,2) 那一格画面完全一样，HUD 徽标始终 X10）。所以改成：
  ① OCR 扫描部署栏找 `X10`/`×10` 徽标 ⇒ 卡面中心 = 徽标中心 +(-10,+46)（bi7f 实测）；
  ② 先试「拖拽（卡片→格子）」，再查徽标是否掉到 X9；没掉就试「点卡片→点格子」；
  ③ 用目标格周边像素差再确认一次，并把前后截图存证。

用法（与 stage_attempt.py 并行）：
    python tools/ex7_crate_inject.py --tile 3,2 --inject-after 2 --out runtime/_ce6/bi7k_crate.png
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
HUD = (980, 530, 1280, 580)          # 剩余可放置角色
BAR = (430, 555, 1280, 700)          # 部署栏
_ocr = RapidOCR()


def shot():
    r = subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


def ocr_texts(img, box, scale=2.0):
    x1, y1, x2, y2 = box
    crop = cv2.resize(img[y1:y2, x1:x2], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    res, _ = _ocr(crop)
    out = []
    for b, t, s in (res or []):
        xs = [q[0] / scale + x1 for q in b]
        ys = [q[1] / scale + y1 for q in b]
        out.append((t, (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
    return out


def rest_slots(img):
    parts = ocr_texts(img, HUD, 2.5)
    txt = "".join(t for t, _cx, _cy in parts)
    if "剩余" not in txt:
        return None
    m = re.findall(r"(\d+)", txt)
    return int(m[-1]) if m else None


def crate_badge(img):
    """找部署栏里的 X10 徽标，返回 (count, cx, cy)。"""
    best = None
    for t, cx, cy in ocr_texts(img, BAR, 2.0):
        tt = t.replace(" ", "").upper().replace("×", "X")
        m = re.search(r"X(\d+)", tt)
        if m and 1 <= int(m.group(1)) <= 30:
            n = int(m.group(1))
            if best is None or n > best[0]:
                best = (n, cx, cy)
    return best


def tap(x, y):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", str(int(x)), str(int(y))], capture_output=True)


def swipe(x1, y1, x2, y2, ms=2500):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "swipe", str(int(x1)), str(int(y1)),
                    str(int(x2)), str(int(y2)), str(ms)], capture_output=True)


def to_px(col, row):
    return (int(A[0] * col + A[1] * row + A[2]), int(B[0] * col + B[1] * row + B[2]))


def tile_sig(img, cx, cy, r=26):
    patch = img[cy - r:cy + r, cx - r:cx + r].astype(np.int16)
    return patch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tile", default="3,2")
    ap.add_argument("--inject-after", type=int, default=2)
    ap.add_argument("--limit", type=int, default=9)
    ap.add_argument("--timeout", type=float, default=220.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    col, row = (int(v) for v in a.tile.split(","))
    tx, ty = to_px(col, row)
    print("目标格 (%d,%d) -> 像素 (%d,%d)" % (col, row, tx, ty), flush=True)

    t0 = time.time()
    last = None
    while time.time() - t0 < a.timeout:
        img = shot()
        if img is None:
            time.sleep(1.5)
            continue
        n = rest_slots(img)
        if n != last:
            print("  t=%5.1fs 剩余可放置角色=%s" % (time.time() - t0, n), flush=True)
            last = n
        if n is not None and n <= a.limit - a.inject_after:
            break
        time.sleep(1.5)
    else:
        print("✗ 超时，没等到注入时机", flush=True)
        return 1

    img = shot()
    badge = crate_badge(img)
    if badge is None:
        print("✗ 部署栏里找不到箱子徽标（Xnn）", flush=True)
        return 2
    cnt, bx, by = badge
    card = (bx - 10, by + 46)
    print("  箱子徽标 X%d @(%d,%d) ⇒ 卡面中心 (%d,%d)" % (cnt, bx, by, card[0], card[1]), flush=True)
    before = tile_sig(img, tx, ty)
    if a.out:
        cv2.imwrite(a.out.replace(".png", "_before.png"), img)

    # 手势 1：拖拽
    print("  手势1：拖拽 卡片->格子", flush=True)
    swipe(card[0], card[1], tx, ty, 2500)
    time.sleep(2.5)
    img2 = shot()
    b2 = crate_badge(img2) if img2 is not None else None
    ok = b2 is not None and b2[0] < cnt
    print("  手势1 后徽标 = %s ⇒ %s" % (b2, "成功" if ok else "未生效"), flush=True)
    if not ok:
        # 手势 2：点卡片 -> 点格子
        print("  手势2：点卡片 -> 点格子", flush=True)
        tap(card[0], card[1])
        time.sleep(1.2)
        tap(tx, ty)
        time.sleep(2.5)
        img2 = shot()
        b2 = crate_badge(img2) if img2 is not None else None
        ok = b2 is not None and b2[0] < cnt
        print("  手势2 后徽标 = %s ⇒ %s" % (b2, "成功" if ok else "仍未生效"), flush=True)

    if img2 is not None:
        after = tile_sig(img2, tx, ty)
        diff = float(np.abs(after - before).mean()) if after.shape == before.shape else -1.0
        print("  目标格像素平均差 = %.1f（>8 说明那一格画面变了）" % diff, flush=True)
        if a.out:
            cv2.imwrite(a.out, img2)
            print("  ⇒ 存证 %s（及 _before.png）" % a.out, flush=True)
    print("  ⇒ 完成（t=%.1fs）" % (time.time() - t0), flush=True)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
