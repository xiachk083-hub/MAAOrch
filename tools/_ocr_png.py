#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对本地 PNG 做 OCR（rapidocr）并打印带坐标的文本行。

用法: <Python312>\python.exe tools/_ocr_png.py <png> [--json]
口径: 输出按 (y, x) 排序；--json 额外打印一行 JSON。
"""
import sys
import json

from rapidocr_onnxruntime import RapidOCR


def main() -> int:
    path = sys.argv[1]
    if "--crop" in sys.argv:
        import cv2
        x1, y1, x2, y2 = (int(v) for v in sys.argv[sys.argv.index("--crop") + 1].split(","))
        img = cv2.imread(path)
        img = img[y1:y2, x1:x2]
        if "--scale" in sys.argv:
            s = float(sys.argv[sys.argv.index("--scale") + 1])
            img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        path = img
    eng = RapidOCR()
    res, _ = eng(path)
    out = []
    for box, text, score in (res or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cx = int(sum(xs) / len(xs))
        cy = int(sum(ys) / len(ys))
        out.append({"x": cx, "y": cy, "text": text, "score": round(float(score), 3)})
    out.sort(key=lambda r: (r["y"], r["x"]))
    for r in out:
        print(f"({r['x']:4d},{r['y']:4d}) {r['text']}")
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
