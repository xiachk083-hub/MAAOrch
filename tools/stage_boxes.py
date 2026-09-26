#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**关卡按钮识别** —— 用户 2026-09-24 给的判据：

    地图上那种"白色方框、上面写着关卡代号（XX-1 / XX-EX-3 / XX-S-2，或剧情关）"
    就是**关卡按钮**；**能看到这种白框，就说明已经进到活动里了**。

这一条同时解决三件事（比坐标表/模板都省事）：
  ① 判"进活动成功了吗" → 能看到带代号的白色方框 = 进去了
  ② 找关卡节点位置     → 白框中心就是点击目标（**零手填坐标**）
  ③ 读关卡代号         → 框里的字就是代号（**免维护：不依赖版本快照关卡表**）

做法（毫秒级）：白色掩膜（低饱和 + 高亮度）→ 连通域 → 筛"横长条"→ 每个框里读字
（先 OCR；读不出再问外部视觉模型）。

用法:
    python tools/stage_boxes.py --shot runtime/_map_EPEX.png     # 离线验证
    python tools/stage_boxes.py                                  # 抓当前屏
    python tools/stage_boxes.py --json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
CODE_RE = re.compile(r"\b([A-Z]{1,3})\s*-\s*(EX-|S-|SP-)?(\d{1,2})\b")
# **不受 `\b` 拖累的自由正则**（2026-09-24 审计踩到）：OCR 会把名条上的
# 「OPERATION」和代号连写成 `OPERATIONPV-2` —— 此时 `\b` 在字母之间**不成立**，匹配失败。
# 所以结构化判据统一走：**去掉空格 → 去掉 OPERATION/TUTORIAL 噪声 → 不带 \b 地找代号**。
# ⚠️ 2026-09-24 真机 AD 首验又踩到第二个坑：中间那段只写了 `EX-|S-|SP-`，**漏了 `ST-`/`TR-`**
#   ⇒ `AD-TR-1` 被解析成 **`TR-1`**、`AD-ST-3` 被解析成 `ST-3`（前缀丢了）。
#   冒烟时看不出来（不带前缀时"能认出来"就算过），但**带前缀过滤时会判成"不是本活动的地图"**
#   ⇒ 新活动的教学/剧情关名条会被漏掉。现在把 `ST|TR` 补上。
CODE_RE_FREE = re.compile(r"([A-Z]{1,3})-((?:EX|S|SP|ST|TR)-)?(\d{1,2})")
NOISE = ("OPERATION", "TUTORIAL", "EPISODE")


def extract_code(txt: str) -> str:
    """从 OCR 文本里取关卡代号（**唯一实现**，容忍 `OPERATIONMT-6` / `TUTORIALAD-TR-1` 连写）。"""
    t = str(txt or "").upper().replace(" ", "")
    for n in NOISE:
        t = t.replace(n, "")
    m = CODE_RE_FREE.search(t)
    return f"{m.group(1)}-{m.group(2) or ''}{m.group(3)}" if m else ""


def codes_in_text(txt: str) -> list[str]:
    """一段 OCR 文本里的**全部**代号（去重保序）—— `page_of` 走"文本判据"时用它。"""
    t = str(txt or "").upper().replace(" ", "")
    for n in NOISE:
        t = t.replace(n, "")
    out: list[str] = []
    for m in CODE_RE_FREE.finditer(t):
        c = f"{m.group(1)}-{m.group(2) or ''}{m.group(3)}"
        if c not in out:
            out.append(c)
    return out


def white_boxes(img):
    """找出"白色横长条"，返回 [(x0,y0,x1,y1)]。这是关卡名条的稳定特征。"""
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0].astype(np.int16), hsv[:, :, 1].astype(np.int16), hsv[:, :, 2].astype(np.int16)
    mask = ((s < 85) & (v > 175)).astype(np.uint8) * 255   # 放宽（实测严阈值只检出 2/8 个节点）
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, hh = cv2.boundingRect(c)
        if w < 70 or not (16 <= hh <= 80) or w / max(hh, 1) < 2.0:
            continue
        out.append((x, y, x + w, y + hh))
    return out


def read_code(img, box, ocr_engine=None):
    """读白框里的关卡代号：先 OCR，读不出返回空串（调用方可用视觉模型兜底）。"""
    from rapidocr_onnxruntime import RapidOCR
    x0, y0, x1, y1 = box
    crop = img[max(0, y0 - 2):y1 + 2, max(0, x0 - 2):x1 + 2]
    if crop.size == 0:
        return ""
    if ocr_engine is None:
        ocr_engine = RapidOCR()
    res, _ = ocr_engine(crop)
    txt = "".join(t for _b, t, *_ in (res or []))
    m = CODE_RE.search(txt.upper().replace(" ", ""))
    return f"{m.group(1)}-{m.group(2) or ''}{m.group(3)}" if m else ""


# ── 「这张屏是不是活动地图」的**结构性判据**（2026-09-24，task-14；唯一实现）──────
# 用户口径：地图上必然有**带关卡代号的白色方框**（"能看到白框 + 框里写着代号，就是进到活动里了"）。
# 为什么必须用它替代"整屏模板"：模板只覆盖"自己那一屏"，新活动/新视图就退化成「未知」——
#   实测 `_map_mt`(真地图) 0.794 / `_mt_act` 0.791 / `_pv` 0.772 都被 0.80 阈值压成未知，
#   而同一分数带里还混着 `_stone_dlg` 0.782（用源石恢复理智的弹窗）、`_dlg*` 0.783（剧情对话）
#   ⇒ **单阈值分不开**（0.772 真地图 vs 0.782 弹窗只差 0.010）。
# 代价（实测，466 张语料）：
#   · 纯几何预筛会命中 82% 的屏 ⇒ **不能**拿它当无条件闸门；
#   · 所以服务端 `page_of()` 只在"模板说不准(map_max≥0.70) "时才调本判据 —— 实测只触发
#     9/466 张（2%），而那 9 张恰好是"原本只能拿到未知"的屏。
#   · 需要"绝对确信"的一次性调用（如进活动后的落地核验）可以传 `always=True` 无条件跑。
PLATE_W = (100, 340)          # 关卡名条的宽高（实测真地图 130x43 / 277x42 / 212x42）
PLATE_H = (26, 84)


def plate_boxes(img, max_n: int = 2):
    """几何筛出"像关卡名条"的白框（比 white_boxes 严：尺寸像名条）。"""
    out = []
    for x0, y0, x1, y1 in white_boxes(img):
        w, h = x1 - x0, y1 - y0
        if PLATE_W[0] <= w <= PLATE_W[1] and PLATE_H[0] <= h <= PLATE_H[1]:
            out.append((x0, y0, x1, y1))
        if len(out) >= max_n:
            break
    return out


def label_codes(img, ocr=None, max_n: int = 4, prefix: str = "", full_ocr=None) -> dict:
    """名条 + 框内代号 OCR；**名条找不到时退一步做一次全屏 OCR**（返回 {codes, bars, ...}）。

    为什么要退这一步（2026-09-24 实测）：`_map_mt.png` 那种**浅色背景**地图上，
    `white_boxes()`（白色掩膜 + RETR_EXTERNAL）返回 **0 个**候选 —— 整屏偏亮，白掩膜连成大块，
    名条根本分离不出来。⇒ 只靠"白框"会漏掉一整类地图。全屏 OCR 只在这条**已经被闸门筛过**的
    路径上跑（服务端闸门 map≤0.70 时只触发 9/466 张），代价可控。

    `ocr` / `full_ocr` 可传常驻引擎（服务里传 `engine()`）；不给就自己建（CLI 冷启动 ~4s）。
    """
    import time
    t0 = time.perf_counter()
    bars = plate_boxes(img, max_n=max_n)
    codes: list[str] = []
    if ocr is None and bars:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
    for x0, y0, x1, y1 in bars:
        crop = img[max(0, y0 - 2):y1 + 2, max(0, x0 - 2):x1 + 2]
        if crop.size == 0:
            continue
        res, _ = ocr(crop)
        c = extract_code("".join(t for _b, t, *_ in (res or [])))
        if c:
            codes.append(c)
            break
    full_used = False
    if not codes:                                  # 浅色背景地图：白框分离不出来 ⇒ 全屏 OCR 兜底
        try:
            if full_ocr is None:
                from rapidocr_onnxruntime import RapidOCR
                full_ocr = ocr or RapidOCR()
            res, _ = full_ocr(img)
            # ⚠️ 2026-09-24 实测的取舍：曾想加"必须同时看到 OPERATION、或 ≥2 个代号"来收紧，
            #   结果 **PV 地图全灭**（`_pv/_pv2/_now6` 的 `OPERATION` 是小字，OCR 读不出来，
            #   屏上只有 1 个代号 `PV-S-1`）⇒ 收紧条件把 3 张真地图又打回未知。
            #   所以这里保持"**≥1 个代号**"，把安全性交给**调用方的上下文**：
            #     · 默认路径（`page_of` 门控）本来就已经要求"地图模板最高分 ≥0.70"，安全；
            #     · 想无条件调用（`struct=True`）请自己带 prefix/页面上下文，
            #       否则"关卡列表里写着 EA-1 的页面"也会被判成地图（已知边界，别踩）。
            codes = sorted({c for c in (extract_code(t) for _b, t, *rest in (res or [])) if c})
            full_used = True
        except Exception:                          # noqa: BLE001
            pass
    want = f"{prefix.upper()}-" if prefix else ""
    return {"codes": codes, "bars": len(bars), "checked": len(bars), "full_ocr": full_used,
            "prefix_hit": bool(want) and any(c.startswith(want) for c in codes),
            "ocr_ms": round((time.perf_counter() - t0) * 1000)}


def is_activity_map(img, prefix: str = "", ocr=None, max_n: int = 4, full_ocr=None) -> dict:
    """**结构性判据**：屏上存在"白名条 + 条内读得出关卡代号" ⇒ 是活动地图。

    给了 `prefix` 就要求代号属于该活动；没给则任意代号都算（用于"新活动零手填"）。
    """
    r = label_codes(img, ocr=ocr, max_n=max_n, prefix=prefix, full_ocr=full_ocr)
    r["is_map"] = bool(r["prefix_hit"] if prefix else r["codes"])
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--vision", action="store_true", help="OCR 读不出时问外部视觉模型")
    a = ap.parse_args()

    import cv2
    if a.shot:
        img = cv2.imread(str(ROOT / a.shot if not pathlib.Path(a.shot).is_absolute() else a.shot))
    else:
        p = ROOT / "runtime" / "_boxes.png"
        urllib.request.urlopen(f"http://127.0.0.1:8765/shot?path={p}", timeout=25).read()
        img = cv2.imread(str(p))
    if img is None:
        print(json.dumps({"ok": False, "detail": "拿不到画面"}, ensure_ascii=False))
        return 1

    boxes = white_boxes(img)
    eng = None
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR()
    items = []
    for b in boxes:
        code = read_code(img, b, eng)
        items.append({"code": code, "x": (b[0] + b[2]) // 2, "y": (b[1] + b[3]) // 2,
                      "box": list(b)})
    typed = [it for it in items if it["code"]]
    out = {"ok": True, "boxes": len(boxes), "codes": sorted({it["code"] for it in typed}), "items": items}
    if a.vision and not typed and boxes:
        try:
            sys.path.insert(0, str(ROOT / "tools"))
            import vision as _v
            p = ROOT / "runtime" / "_boxes.png"
            ans = _v.ask(p, '列出图上所有关卡代号（形如 EA-1 / PV-EX-3），只输出 JSON {"codes":[...]}')
            s = ans[ans.find("{"): ans.rfind("}") + 1] if "{" in ans else ""
            out["vision_codes"] = (json.loads(s).get("codes") if s else []) or []
        except Exception as e:                    # noqa: BLE001
            out["vision_err"] = str(e)[:80]
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"白框 {out['boxes']} 个，读出代号 {len(typed)} 个：")
        for it in typed[:20]:
            print(f"  {it['code']:10s} @ ({it['x']},{it['y']})")
        if not typed:
            print("  （没读出代号；加 --vision 让外部视觉模型兜底）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
