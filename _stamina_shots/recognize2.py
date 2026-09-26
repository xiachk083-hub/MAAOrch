# -*- coding: utf-8 -*-
"""批量识别模拟器截图的体力值 v2 — 大区域 + 位置关联。
体力显示: 主页右上角 [当前体力大数字] [+] [理智/上限]
"""
import glob
import json
import os
import re
import sys
from PIL import Image

from rapidocr_onnxruntime import RapidOCR

OCR = RapidOCR()


def crop_sanity(img):
    """扩大裁剪区域: x 620-950, y 115-265（覆盖 + / 大数字 / 理智-上限）。"""
    w, h = img.size
    if w == 1280 and h == 720:
        box = (620, 110, 950, 270)
    else:
        box = (int(w * 0.48), int(h * 0.15), int(w * 0.74), int(h * 0.37))
    crop = img.crop(box)
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    return crop


def extract_sanity(items):
    """items: [(text, score, box)] — 用位置关联提取 (当前, 上限)。"""
    # 1. 找 "理智/N" 或 "/N" 或 "Sanity/N" → 上限
    cap = None
    for text, _s, box in items:
        m = re.search(r"(?:理智|sanity|Sanity|/)\s*[/]?\s*(\d{1,4})", text, re.I)
        if m:
            cap = int(m.group(1))
            break
    # 2. 找 "+" 的 box（当前体力数字在其右侧）
    plus_box = None
    for text, _s, box in items:
        if text.strip() == "+":
            plus_box = box
            break
    cur = None
    if plus_box:
        px = plus_box[0][0]  # 左上 x
        # 找 x 在 plus 右侧最近的大数字
        best = None
        for text, _s, box in items:
            m = re.match(r"^(\d{1,4})$", text.strip())
            if not m:
                continue
            bx = box[0][0]
            if bx > px + 10 and (best is None or bx < best[1]):
                best = (int(m.group(1)), bx)
        if best:
            cur = best[0]
    # 3. 兜底：全文本里的第一个大数字（按 y 从上到下）
    if cur is None:
        nums = []
        for text, _s, box in items:
            m = re.match(r"^(\d{1,4})$", text.strip())
            if m:
                nums.append((int(m.group(1)), box[0][1]))  # (num, y)
        if nums:
            nums.sort(key=lambda x: x[1])  # 最上面（y 最小）
            cur = nums[0][0]
    # 4. 上限兜底：raw 里的独立数字（210/205/209 等，非当前体力）
    if cap is None:
        cands = []
        for text, _s, box in items:
            m = re.match(r"^(\d{1,4})$", text.strip())
            if m:
                n = int(m.group(1))
                if n >= 100:  # 上限通常 >= 100
                    cands.append((n, box[0][1]))
        if cands:
            cands.sort(key=lambda x: x[1])
            cap = cands[0][0]
    return cur, cap


def main():
    shot_dir = os.path.dirname(os.path.abspath(__file__))
    out = {}
    for fp in sorted(glob.glob(os.path.join(shot_dir, "emu*.png"))):
        if "_sanity" in os.path.basename(fp) or "_check" in os.path.basename(fp):
            continue
        emu = os.path.basename(fp).replace("emu", "").replace(".png", "")
        try:
            img = Image.open(fp)
            crop = crop_sanity(img)
            crop_path = os.path.join(shot_dir, f"_v2_{emu}.png")
            crop.save(crop_path)
            result, _ = OCR(crop_path)
            items = [(item[1], item[2], item[0]) for item in (result or [])]
            cur, cap = extract_sanity(items)
            out[emu] = {"cur": cur, "cap": cap, "raw": [i[0] for i in items]}
            print(f"emu{emu}: {cur}/{cap}  raw={[i[0] for i in items]}")
        except Exception as e:
            print(f"emu{emu}: ERR {e}")
    with open(os.path.join(shot_dir, "sanity_results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("---SUMMARY---")
    for emu in sorted(out, key=int):
        d = out[emu]
        print(f"  emu{emu}: {d['cur']}/{d['cap']}")


if __name__ == "__main__":
    main()
