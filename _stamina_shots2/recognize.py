# -*- coding: utf-8 -*-
"""第二轮截图识别（stamina_check2）。"""
import glob
import json
import os
import sys

sys.path.insert(0, r"D:\1-2\MAAOrch\_stamina_shots")
from PIL import Image

from recognize2 import OCR, crop_sanity, extract_sanity

shot_dir = r"D:\1-2\MAAOrch\_stamina_shots2"
out = {}
for fp in sorted(glob.glob(os.path.join(shot_dir, "emu*.png"))):
    emu = os.path.basename(fp).replace("emu", "").replace(".png", "")
    try:
        img = Image.open(fp)
        crop = crop_sanity(img)
        cp = os.path.join(shot_dir, f"_v2_{emu}.png")
        crop.save(cp)
        result, _ = OCR(cp)
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
