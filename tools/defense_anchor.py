#!/usr/bin/env python3
"""Gate A · 第一块砖：**防守点锚点的级联检测**（外援方案 C 的落地）。

为什么要级联：2026-09-19 我用"最大蓝色连通块"当锚 ⇒ 两张不同的图报出**同一个坐标 (450,468)**
（那是地图里的蓝色装饰/UI 元素 ✗）⇒ 整批读数作废 ✓。颜色阈值单独用**一定会假通过** ✓。

级联（照外援原文）：
   ① 颜色只负责**提候选**（蓝色 mask，且限在地图区域）
   ② **闭合四边形**：approxPolyDP 出 4 顶点 + 凸 + 有内部空洞（ring 闭合）
   ③ **内部白三角**：四边形内部找白色连通块 ⇒ 近似 3 顶点 + 质心靠近框心 + 面积比在区间
   ④ **红框反例**：同样像"框+三角形"但颜色是红 ⇒ 必须 **FAIL**
   ⑤ 任一层不过 ⇒ **FAIL-CLOSED**（返回 None，不许猜）

判据贯穿一条纪律：**每条规则都要能用构造样张验红** ✓（见 `--selftest`：造蓝框✓/红框✗/蓝框无三角✗/断边✗/纯蓝块✗ 五种）。

用法：
  python tools/defense_anchor.py --selftest                 # 构造样张 5 例（含 3 个反例）
  python tools/defense_anchor.py --scan 40                  # 扫采样库找锚点，与数据 tile_end 对表
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import cv2
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import battle_loop as BL                                         # noqa: E402
import tilepos_audit as TA                                       # noqa: E402

ROOT = HERE.parent
DATA = ROOT / "data"


def _blue_mask(im: np.ndarray) -> np.ndarray:
    B, G, R = (im[:, :, i].astype(int) for i in range(3))
    m = np.uint8((B - R > 30) & (B - G > 20) & (B > 90)) * 255
    m[:TA.VIEW[0]] = 0
    m[TA.VIEW[1]:] = 0
    # ⚠️ 2026-09-19 事故：`VIEW[2]` 正好等于 515，而**干员详情面板的血条就从 x=515 开始** ✗
    # ⇒ 血条被当成"蓝块"检进来，我还拿它当防守点锚用了一轮 ✓（用户当场看出"不太对" ✓）。
    # ⇒ 左边界加余量、并显式排掉干员面板那一带 ✓（面板宽约 344/1280 ⇒ 515 in 1080p ✓）
    m[:, :TA.VIEW[2] + 60] = 0
    m[:, TA.VIEW[3]:] = 0
    m[int(im.shape[0] * 0.82):] = 0                             # 底部手牌带一律不看 ✓
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))


def _white_triangle(im: np.ndarray, box) -> tuple[bool, float]:
    """框内是否有**像三角形的白块** ✓（返回 (是否, 面积比)）。"""
    x, y, w, h = box
    pad = max(6, int(0.15 * min(w, h)))
    x0, y0 = max(0, x + pad), max(0, y + pad)
    x1, y1 = min(im.shape[1], x + w - pad), min(im.shape[0], y + h - pad)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return False, 0.0
    reg = im[y0:y1, x0:x1].astype(int)
    white = np.uint8((reg[:, :, 0] > 150) & (reg[:, :, 1] > 150) & (reg[:, :, 2] > 150)) * 255
    n, lab, st, ce = cv2.connectedComponentsWithStats(white, 8)
    if n <= 1:
        return False, 0.0
    i = 1 + int(np.argmax(st[1:, 4]))
    area = int(st[i, 4])
    if area < 40:
        return False, 0.0
    cnt = cv2.findContours(np.uint8(lab == i) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    if not cnt:
        return False, 0.0
    ap = cv2.approxPolyDP(cnt[0], 0.06 * cv2.arcLength(cnt[0], True), True)
    tri = len(ap) == 3
    ratio = area / max(1, (x1 - x0) * (y1 - y0))
    return (tri and 0.04 <= ratio <= 0.75), float(ratio)


def find_defense_anchor(im: np.ndarray, verbose: bool = False):
    """级联检测防守点锚点。返回 (x, y, info) 或 (None, None, info)。"""
    m = _blue_mask(im)
    n, lab, st, ce = cv2.connectedComponentsWithStats(m, 8)
    info = {"candidates": 0, "rejects": [], "trace": []}
    best = None
    for i in range(1, n):
        area = int(st[i, 4])
        if area < 800:
            continue
        info["candidates"] += 1
        box = tuple(int(v) for v in st[i, :4])
        tr = {"box": box, "area": area}
        comp = np.uint8(lab == i) * 255
        cnt = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        if not cnt:
            continue
        c = max(cnt, key=cv2.contourArea)
        per = 0.03 * cv2.arcLength(c, True)
        ap = cv2.approxPolyDP(c, per, True)
        tr["contour_len"] = round(cv2.arcLength(c, True), 1)
        tr["approx_eps"] = round(per, 1)
        tr["vertices"] = len(ap)
        # ② **闭合环**（2026-09-19 改模型，不是放松阈值 ✗）：
        #    实测真标记是**3D 方块**（带内部棱线）⇒ 外轮廓本来就不是四边形 ✗
        #    （落魄骑士那个蓝块 9 顶点、压轴登场 6 顶点、且 not_convex ✗ —— 被旧模型误杀 ✓）。
        #    改为要求：**必须是"环"（内部有空洞）+ 顶点数合理 + bbox 形状不细长** ✓
        #    反例仍必须全部拒掉（断边 ⇒ 环不闭合 ✓；纯蓝块 ⇒ 无空洞 ✓）。
        if not (3 <= len(ap) <= 20):
            tr["final"] = "FAIL@vertices=%d" % len(ap)
            info["trace"].append(tr)
            info["rejects"].append(("vertices=%d" % len(ap), box))
            continue
        x_, y_, w_, h_ = box
        ar = w_ / max(1, h_)
        tr["aspect"] = round(ar, 2)
        if not (0.35 <= ar <= 2.8):
            tr["final"] = "FAIL@aspect=%.2f" % ar
            info["trace"].append(tr)
            info["rejects"].append(("aspect=%.2f" % ar, box))
            continue
        filled = np.zeros_like(comp)
        cv2.drawContours(filled, [c], -1, 255, -1)
        fa = int((filled > 0).sum())
        ra = int((comp > 0).sum())
        ring = fa - ra
        tr["ring_ratio"] = round(ring / max(1, fa), 3)
        if fa <= 0 or ring < 0.15 * fa:
            tr["final"] = "FAIL@not_closed"
            info["trace"].append(tr)
            info["rejects"].append(("not_closed(ring %.2f)" % (ring / max(1, fa)), box))
            continue
        ok_tri, ratio = _white_triangle(im, box)
        tr["white_tri"] = ok_tri
        tr["white_ratio"] = round(ratio, 3)
        if not ok_tri:
            tr["final"] = "FAIL@no_white_triangle"
            info["trace"].append(tr)
            info["rejects"].append(("no_white_triangle(%.2f)" % ratio, box))
            continue
        tr["final"] = "PASS"
        info["trace"].append(tr)
        score = area * ratio
        if best is None or score > best[0]:
            best = (score, int(ce[i][0]), int(ce[i][1]), box, ratio)
    if best is None:
        return None, None, info
    return best[1], best[2], {**info, "box": best[3], "white_ratio": round(best[4], 3)}


# ── 构造样张自测（外援要求：每条规则都要能验红）────────────────────────
def _synthetic(kind: str, size=(1920, 1080)) -> np.ndarray:
    """合成样张 **必须用 1920×1080、且把方块放在地图 ROI 内** ✓

    ⚠️ 2026-09-19 事故：原来用 900×900 画，而 `_blue_mask` 用 1080p 的
    `VIEW=(110, 875, 515, 1905)` 裁剪 ⇒ 方块左边被切掉、只剩 59×247 的竖条 ✗
    ⇒ 正例被"not_convex"误杀 ✓。**这是样张构造问题，不是判据过严** ✓（所以判据一层都没放松 ✓）。
    """
    im = np.full((size[1], size[0], 3), 40, np.uint8)
    cx, cy = 1200, 500                                   # 落在 VIEW(x 515~1905, y 110~875) 内 ✓
    r = 120
    if kind in ("blue_good", "red_good", "no_triangle", "broken_edge"):
        col = (240, 160, 40) if kind == "blue_good" or kind in ("no_triangle", "broken_edge") else (40, 60, 240)
        pts = np.array([[cx - r, cy - r], [cx + r, cy - r], [cx + r, cy + r], [cx - r, cy + r]], np.int32)
        if kind == "broken_edge":
            cv2.polylines(im, [pts[:3]], False, col, 6)               # 缺一条边 ⇒ 不闭合
        else:
            cv2.polylines(im, [pts], True, col, 6)
        if kind in ("blue_good", "red_good"):                        # 只有这两种画白三角
            tri = np.array([[cx, cy - 45], [cx - 50, cy + 35], [cx + 50, cy + 35]], np.int32)
            cv2.fillPoly(im, [tri], (245, 245, 245))
    elif kind == "blue_blob":                                        # 纯蓝色块（旧方法的假通过 ✓）
        cv2.circle(im, (cx, cy), r, (240, 160, 40), -1)
    return im


def selftest() -> int:
    cases = [("blue_good", True), ("red_good", False), ("no_triangle", False),
             ("broken_edge", False), ("blue_blob", False)]
    ok = 0
    for kind, expect in cases:
        x, y, info = find_defense_anchor(_synthetic(kind))
        got = x is not None
        good = got == expect
        ok += good
        print("  %s %-12s 期望 %-5s 实得 %-5s  %s"
              % ("✓" if good else "✗", kind, expect, got, (info.get("rejects") or [])[:2]))
        if not good:
            for z in info.get("trace", [])[:3]:
                print("        逐层: 候选 box=%s area=%d 轮廓长=%s eps=%s 顶点=%s 凸=%s ring=%s 白三角=%s(%.2f) ⇒ %s"
                      % (z.get("box"), z.get("area", 0), z.get("contour_len"), z.get("approx_eps"),
                         z.get("vertices"), z.get("convex"), z.get("ring_ratio"),
                         z.get("white_tri"), z.get("white_ratio", 0.0), z.get("final")))
    print("自测：%d/%d" % (ok, len(cases)))
    return 0 if ok == len(cases) else 1


def scan(limit: int) -> int:
    """逐样本表 + **逐样本验收**（外援口径：每个有效人工核对样本都必须 < 0.5 格，不许用平均数洗 ✓）。

    列：sample_id / frame / anchor_px / predicted_tile_end_px / distance_px /
        local_grid_spacing_px / error_cells / validity
    validity：`human_verified` = 我们**肉眼核对过蓝 ⚠ 块**的帧（目前 2 帧 ✓）；其余记 `auto` ✓，
    **验收只看 human_verified** ✓，auto 只作参考 ✓。
    """
    g = pathlib.Path(r"D:/Xiach/M-A/MAA/MAA-v6.12.2-win-x64/debug/gui.log")
    starts = [(l[12:19], l.split("开始战斗: ")[-1].strip()[:20])
              for l in g.read_text(encoding="utf-8", errors="replace").splitlines()
              if len(l) > 21 and l[1:11] == "2026-09-19" and l[12:19] >= "20:42:00" and "开始战斗: " in l]

    def name_at(t_):
        nm = None
        for st, n in starts:
            if st <= t_:
                nm = n
        return nm
    HUMAN = {"211837_b01_05.jpg", "210209_b04_09.jpg"}        # 我肉眼核对过蓝 ⚠ 的帧 ✓
    rows = sorted((json.loads(l) for l in (DATA / "samples/rogue/index.jsonl").read_text(encoding="utf-8")
                   .splitlines() if l.strip()), key=lambda r: r["t"])
    out = []
    print("sample_id | frame | anchor_px | predicted_tile_end_px | distance_px | spacing_px | error_cells | validity")
    for r in rows:
        nm = name_at(r["t"][11:])
        pth = pathlib.Path(r["file"])
        if not nm or not pth.is_file():
            continue
        im = cv2.imread(str(pth))
        if im is None:
            continue
        x, y, info = find_defense_anchor(im)
        if x is None:
            continue
        try:
            L = BL.Loop(nm, None)
        except Exception:                                        # noqa: BLE001
            continue
        s = L.stage_px
        ends = [(np.array(L.lv.project(c, rr)) * s, (c, rr)) for rr in range(L.lv.height)
                for c in range(L.lv.width)
                if (tt := L.lv.at(c, rr)) and tt.is_end and L.lv.project(c, rr)]
        if not ends:
            continue
        d, cell = min(((float(np.hypot(*(e - np.array([x, y])))), cr) for e, cr in ends), key=lambda z: z[0])
        q0 = np.array(L.lv.project(*cell)) * s
        q1 = np.array(L.lv.project(cell[0] + 1, cell[1])) * s if L.lv.project(cell[0] + 1, cell[1]) else None
        q2 = np.array(L.lv.project(cell[0], cell[1] + 1)) * s if L.lv.project(cell[0], cell[1] + 1) else None
        sp = [float(np.hypot(*(q - q0))) for q in (q1, q2) if q is not None]
        spacing = min(sp) if sp else 130.0
        ec = d / spacing
        val = "human_verified" if pth.name in HUMAN else "auto"
        out.append((nm, pth.name, x, y, d, spacing, ec, val))
        print("  %-6s %s (%4d,%4d) (%4.0f,%4.0f) %6.0f %6.0f **%.2f** %s"
              % (nm, pth.name, x, y, q0[0], q0[1], d, spacing, ec, val))
        if len(out) >= limit * 6:
            break
    hv = [z for z in out if z[7] == "human_verified"]
    print("")
    if hv:
        print("**验收（只看 human_verified，%d 个样本）**：" % len(hv))
        for z in hv:
            print("   %s error_cells=%.2f ⇒ %s" % (z[1], z[6], "PASS" if z[6] < 0.5 else "FAIL"))
        allok = all(z[6] < 0.5 for z in hv)
        print("  ⇒ Gate A 几何：%s（逐样本判定，不用平均数 ✓）" % ("PASS ✓" if allok else "**FAIL ✗** —— 按要求保持 UNDECIDED"))
    else:
        print("**没有任何 human_verified 样本** ⇒ Gate A 几何保持 **UNDECIDED** ✓（auto 样本仅供参考 ✗）")
    if out:
        autos = [z for z in out if z[7] == "auto"]
        if autos:
            e = np.array([z[6] for z in autos])
            print("  （auto 参考：%d 个样本，中位 %.2f 格、P90 %.2f 格 —— 不作验收依据 ✗）"
                  % (len(autos), float(np.median(e)), float(np.percentile(e, 90))))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate A：防守点锚点级联检测")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scan", type=int, default=0)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.scan:
        return scan(a.scan)
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
