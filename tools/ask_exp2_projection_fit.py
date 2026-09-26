#!/usr/bin/env python3
"""实验 2（外援 2026-09-19 方案 A2~A4）：投影是"统一 scale"还是"投影变化"？

判据（照外援原文）：
  · 同时拟合 similarity（scale+rotation+translation，4 自由度）与 homography（8 自由度）
  · `gain = 1 - E_H / E_S`：若 ≈0 ⇒ 真的是统一 scale ✓；若明显 >0 且残差随位置系统性变化 ⇒ 是相机/FOV/透视 ✗
  · **必须 holdout**（70/30 或 leave-one-out），禁止"全点拟合再全点验收" ✗（我们上次 97~100 百分位假通过就是栽在这）

锚点怎么来（我们试过的替代方案）：
  · 单格大小的绿色高亮块**不存在**（高亮总是连成一片）✗ ⇒ 不能用"块质心=格心"
  · 改用**高亮区域的边界角点**：高亮边界是游戏按真实 tile 边界画的 ⇒ 其角点 ≈ tile 角点 ✓
  · 与投影出来的所有 tile 角点做最近邻匹配（限 0.6 个格距以内）✓

顺带做 **synthetic oracle**（外援 A4）：造已知变换 + 噪声，检查拟合器能不能恢复，并跑三种对抗切分
（左右分半 / 上下分半）—— 专治"局部纹理刚好对齐"的假通过 ✓。
"""

from __future__ import annotations

import pathlib
import sys

import cv2
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import battle_loop as BL                                         # noqa: E402
import tilepos_audit as TA                                       # noqa: E402

CASES = [("落魄骑士", "data/samples/rogue/2026-09-19/211837_b01_05.jpg"),
         ("压轴登场", "data/samples/rogue/2026-09-19/210209_b04_09.jpg"),
         ("巡逻队", "data/samples/rogue/2026-09-19/215008_b11_01.jpg")]


def tile_corners(L, side: bool) -> np.ndarray:
    """所有格的四个角（1080p 像素）。"""
    out = []
    s = L.stage_px
    for r in range(L.lv.height):
        for c in range(L.lv.width):
            q = L.lv.project(c, r, side=side)
            if not q:
                continue
            q = np.array(q, float)
            bc = np.array(L.lv.project(min(c + 1, L.lv.width - 1), r, side=side) or q, float) - q
            br = np.array(L.lv.project(c, min(r + 1, L.lv.height - 1), side=side) or q, float) - q
            for a, b in (((-bc - br) / 2, (bc - br) / 2), ((bc - br) / 2, (bc + br) / 2),
                         ((bc + br) / 2, (-bc + br) / 2), ((-bc + br) / 2, (-bc - br) / 2)):
                out.append((q + a) * s)
                out.append((q + b) * s)
    return np.array(out) if out else np.zeros((0, 2))


def visual_anchors(im: np.ndarray) -> np.ndarray:
    """高亮区域的**边界角点**（游戏按真实 tile 边界画的 ✓）。"""
    mask = TA._mask(im)
    B, G, R = (im[:, :, i].astype(int) for i in range(3))
    for m in (((R - G > 50) & (R - B > 50) & (R > 110)),
              ((B - R > 30) & (B - G > 20) & (B > 90))):
        mask = np.maximum(mask, np.uint8(m) * 255)
    edges = cv2.Canny(mask, 50, 150)
    pts = cv2.goodFeaturesToTrack(edges, maxCorners=120, qualityLevel=0.05, minDistance=12)
    return np.zeros((0, 2)) if pts is None else pts.reshape(-1, 2).astype(float)


def fit_pair(P: np.ndarray, Q: np.ndarray, idx_tr, idx_te):
    """用训练子集拟合 similarity / homography，在测试子集上报告残差（像素）。"""
    out = {}
    A, B = P[idx_tr], Q[idx_tr]
    for name, est in (("similarity", lambda: cv2.estimateAffinePartial2D(A, B, method=cv2.LMEDS)),
                      ("homography", lambda: cv2.findHomography(A, B, cv2.RANSAC, 6.0))):
        r = est()
        if r is None or r[0] is None:
            out[name] = None
            continue
        M = r[0]
        if M.shape == (2, 3):
            proj = (M[:, :2] @ P[idx_te].T).T + M[:, 2]
        else:
            h = np.hstack([P[idx_te], np.ones((len(idx_te), 1))]) @ M.T
            proj = h[:, :2] / h[:, 2:3]
        e = np.linalg.norm(proj - Q[idx_te], axis=1)
        out[name] = (float(np.median(e)), float(np.percentile(e, 90)), float(e.max()),
                     M if M.shape == (2, 3) else None)
    return out


def synthetic_oracle() -> str:
    """外援 A4：造已知变换，检查拟合器能不能恢复（含对抗切分）。"""
    rng = np.random.default_rng(7)
    P = np.column_stack([rng.uniform(0, 1200, 40), rng.uniform(0, 700, 40)])
    lines = []

    def make(sc, rot_deg, tx, ty):
        a = np.radians(rot_deg)
        R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        return P @ (sc * R).T + np.array([tx, ty])

    for sc, rot, tx, ty in ((0.90, 2.0, 17, -31), (0.95, 0.0, 0, 0), (0.90, 5.0, 0, 0), (0.90, 0.0, 20, -20)):
        Q = make(sc, rot, tx, ty) + rng.normal(0, 0.5, P.shape)
        idx = np.arange(len(P))
        r = fit_pair(P, Q, idx[: int(len(P) * 0.7)], idx[int(len(P) * 0.7):])
        s = r.get("similarity")
        if s and s[3] is not None:
            M = s[3]
            est_sc = float(np.hypot(M[0, 0], M[0, 1]))
            est_rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
            lines.append("  真值 scale %.2f rot %+.1f° t(%+d,%+d) ⇒ 恢复 scale %.3f rot %+.2f° t(%+.1f,%+.1f)"
                         % (sc, rot, tx, ty, est_sc, est_rot, M[0, 2], M[1, 2]))
    # 对抗切分：左右分半 / 上下分半（train/test 不重叠区域）
    Q = make(0.90, 2.0, 17, -31)
    med = np.median(P[:, 0])
    for tag, tr, te in (("左半训练/右半测试", P[:, 0] <= med, P[:, 0] > med),
                        ("上半训练/下半测试", P[:, 1] <= np.median(P[:, 1]), P[:, 1] > np.median(P[:, 1]))):
        r = fit_pair(P, Q, np.where(tr)[0], np.where(te)[0])
        s, h = r.get("similarity"), r.get("homography")
        lines.append("  %s：similarity 中位 %s px ｜ homography 中位 %s px"
                     % (tag, ("%.2f" % s[0]) if s else "-", ("%.2f" % h[0]) if h else "-"))
    return "\n".join(lines)


def main() -> int:
    print("=== synthetic oracle（外援 A4：先验证拟合器本身可信）")
    print(synthetic_oracle())
    for nm, f in CASES:
        p = pathlib.Path(f)
        if not p.is_file():
            continue
        im = cv2.imread(str(p))
        L = BL.Loop(nm, None)
        Q = visual_anchors(im)
        print("\n=== %s（%s）视觉锚点 %d 个" % (nm, p.name, len(Q)))
        if len(Q) < 8:
            print("   锚点太少 ⇒ 跳过")
            continue
        for side in (False, True):
            P = tile_corners(L, side)
            if not len(P):
                continue
            d = np.linalg.norm(P[:, None, :] - Q[None, :, :], axis=2)
            j = d.argmin(1)
            ok = d[np.arange(len(P)), j] < 90          # 限 0.6 格距内
            if ok.sum() < 8:
                print("   side=%s 匹配对太少（%d）⇒ 跳过" % (side, int(ok.sum())))
                continue
            Pp, Qm = P[ok], Q[j[ok]]
            rng = np.random.default_rng(3)
            idx = rng.permutation(len(Pp))
            tr, te = idx[: int(len(idx) * 0.7)], idx[int(len(idx) * 0.7):]
            r = fit_pair(Pp, Qm, tr, te)
            s, h = r.get("similarity"), r.get("homography")
            if not s or not h:
                print("   side=%s 拟合失败" % side)
                continue
            gain = 1 - h[0] / s[0] if s[0] else 0
            print("   side=%-5s 配对 %3d ｜ similarity 中位 %5.2f / P90 %5.2f ｜ homography 中位 %5.2f / P90 %5.2f ｜ gain %.2f %s"
                  % (side, len(Pp), s[0], s[1], h[0], h[1], gain,
                     '⇒ 像统一 scale ✓' if abs(gain) < 0.15 else '⇒ 有投影成分（非纯 scale）✗'))
    return 0


if __name__ == "__main__":
    sys.exit(main())
