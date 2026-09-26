# -*- coding: utf-8 -*-
"""批量识别模拟器截图的体力值（理智）。
体力显示格式: 主页右上角 "大数字 + 理智/210"。
"""
import glob
import os
import re
import sys
from PIL import Image

from rapidocr_onnxruntime import RapidOCR

OCR = RapidOCR()

# 模拟器号 → 账号映射（由外部提供，这里留空由调用方填）
EMU_ACCOUNT = {}


def crop_sanity(img):
    """裁剪体力区域并放大（原图 1280x720，体力在右上角）。"""
    # 基于实测: 大数字约 x 700-820, y 160-220; "理智/210" 在其下
    w, h = img.size
    if w == 1280 and h == 720:
        box = (690, 150, 880, 235)
    else:
        # 其他分辨率按比例
        box = (int(w * 0.55), int(h * 0.21), int(w * 0.70), int(h * 0.33))
    crop = img.crop(box)
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
    return crop


def extract_sanity(texts):
    """从 OCR 文本中提取 当前体力/上限。
    期望格式: 数字 + 理智/上限（可能分开成多个词）。
    """
    joined = " ".join(texts)
    # 优先匹配 N/上限
    m = re.search(r"(\d{1,4})\s*/\s*(\d{1,4})", joined)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 匹配 理智/N 或 理智 N
    m = re.search(r"理智\s*[\/]?\s*(\d{1,4})", joined)
    if m:
        # 再找前面的数字（当前体力）
        prefix = joined[: m.start()]
        nums = re.findall(r"\d{1,4}", prefix)
        if nums:
            return int(nums[-1]), int(m.group(1))
        return None, int(m.group(1))
    # 只找到孤立数字：第一个大数字可能是当前体力
    nums = re.findall(r"\d{1,4}", joined)
    if nums:
        return int(nums[0]), None
    return None, None


def main():
    shot_dir = os.path.dirname(os.path.abspath(__file__))
    results = []
    for fp in sorted(glob.glob(os.path.join(shot_dir, "emu*.png"))):
        if "_sanity" in os.path.basename(fp):
            continue
        emu = os.path.basename(fp).replace("emu", "").replace(".png", "")
        try:
            img = Image.open(fp)
            crop = crop_sanity(img)
            crop_path = os.path.join(shot_dir, f"_sanity_{emu}.png")
            crop.save(crop_path)
            result, _ = OCR(crop_path)
            texts = [item[1] for item in (result or [])]
            cur, mx = extract_sanity(texts)
            results.append((emu, cur, mx, texts))
            print(f"emu{emu}: {cur}/{mx}  raw={texts}")
        except Exception as e:
            print(f"emu{emu}: ERR {e}")
    print("---")
    print("SUMMARY:")
    for emu, cur, mx, _t in results:
        print(f"  emu{emu}: {cur}/{mx}")


if __name__ == "__main__":
    main()
