"""逐秒读战斗 HUD（本地 OCR）：剩余可放置角色 / 击杀数 / 费用 —— 用来审计"部署到底落地了几个"。

为什么（2026-09-21）：怀疑 MAA 的拖拽部署会静默失败（日志记 N 次 Deploy，HUD 只减了更少的人数）。
要量化它，就得把每一秒的 HUD 读出来，而不是靠人肉看几帧。

用法:
    python tools/ce6_battle_audit.py runtime/_ce6/v36a.mp4 --from 60 --to 95 --step 1
输出:
    t=xx 剩余=N 击杀=x/41 费用=yy
"""
from __future__ import annotations

import argparse
import pathlib
import re

import cv2
from rapidocr_onnxruntime import RapidOCR

ROI_REMAIN = (1060, 530, 1280, 580)   # x0,y0,x1,y1 「剩余可放置角色：N」
ROI_KILL = (460, 0, 720, 50)          # 顶部中央 0/41
ROI_COST = (1120, 470, 1260, 520)     # 右下角费用

_engine = None


def ocr(img):
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    res, _ = _engine(img)
    return " ".join(t[1] for t in res) if res else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=1e9)
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--scale", type=float, default=2.0, help="裁剪区放大倍数（提升 OCR 命中）")
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    print("%s 片长 %.1fs" % (args.video.name, total))

    t = args.t0
    prev = None
    while t <= min(args.t1, total):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, fr = cap.read()
        if not ok:
            break
        out = {}
        for name, (x0, y0, x1, y1) in (("剩余", ROI_REMAIN), ("击杀", ROI_KILL), ("费用", ROI_COST)):
            crop = fr[y0:y1, x0:x1]
            crop = cv2.resize(crop, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_CUBIC)
            out[name] = ocr(crop).replace(" ", "")
        m = re.search(r"[：:]?(\d+)\s*$", out["剩余"]) or re.search(r"(\d+)", out["剩余"])
        remain = m.group(1) if m else "?"
        mk = re.search(r"(\d+)\s*/\s*(\d+)", out["击杀"])
        kills = "%s/%s" % mk.groups() if mk else "?"
        mc = re.search(r"(\d+)", out["费用"])
        cost = mc.group(1) if mc else "?"
        line = "t=%5.1fs 剩余=%-3s 击杀=%-8s 费用=%-4s | %s" % (t, remain, kills, cost, out["剩余"][:24])
        if prev != (remain, kills) or t % 5 == 0:
            print(line)
        prev = (remain, kills)
        t += args.step
    cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
