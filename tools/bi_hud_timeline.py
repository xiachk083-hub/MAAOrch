# -*- coding: utf-8 -*-
"""逐帧 OCR 战场 HUD（击杀数 / 剩余可放置角色 / 费用），用于还原「谁在什么时候死掉」。"""
import pathlib, re, sys
import cv2
from rapidocr_onnxruntime import RapidOCR

P = pathlib.Path(r"D:\1-2\MAAOrch")
ocr = RapidOCR()


def read(img, boxes):
    out = {}
    for name, (x1, y1, x2, y2) in boxes.items():
        crop = img[y1:y2, x1:x2]
        crop = cv2.resize(crop, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        r, _ = ocr(crop)
        txt = " ".join(t[1] for t in (r or []))
        out[name] = txt
    return out


def main(tag):
    d = P / "runtime" / "_ce6" / ("%s_seq" % tag)
    files = sorted(d.glob("*.png"))
    boxes = {
        "kill": (450, 10, 620, 60),
        "rest": (980, 530, 1280, 580),
        "cost": (1050, 490, 1240, 530),
    }
    prev = {}
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        v = read(img, boxes)
        if v["kill"] == prev.get("kill") and v["rest"] == prev.get("rest"):
            continue
        prev = dict(v)
        print("%s | 击杀=%-8s 剩余=%-6s 费=%s" % (f.stem, v["kill"], v["rest"], v["cost"]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bi7e")
