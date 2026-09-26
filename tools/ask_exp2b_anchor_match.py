#!/usr/bin/env python3
"""实验 2b（外援 §4~§7）：**以格子为单位**的一对一锚点匹配 → similarity vs homography 裁决。

为什么不能用上一版：把 300~410 个预测角点和 120 个视觉角点丢进最近邻 ⇒ **多对一**
（一堆预测点抢同一个锚点）⇒ optimizer 解的是"怎么让很多点靠近少数点" ✗ ⇒ 40~52px 残差作废 ✓。

这一版的顺序（照外援原文）：
  1. 语义单位 = **一个高亮格 = 一个 tile** ⇒ 视觉侧用**距离变换峰值**把连成一片的高亮切成单格质心 ✓
  2. cost = **中心距为主**（+ 门限），**Hungarian 一对一** ✓
  3. 硬验收：assigned cost < gate ✓、无重复占用 ✓
  4. **拓扑一致性 sanity check**（外援 §6）：匹配后近邻关系不能乱 ⇒ 乱了就是匹配错、不是标定问题 ✗
  5. 拟合：similarity vs homography，**70/30 holdout**，报告 train/test 的 median/P90/max 与 Δ_test ✓
  6. synthetic oracle：先证明"匹配+拟合"整条链能恢复已知变换 ✓
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

try:
    from scipy.optimize import linear_sum_assignment
    HAVE_SCIPY = True
except Exception:                                                # noqa: BLE001
    HAVE_SCIPY = False

CASES = [("落魄骑士", "data/samples/rogue/2026-09-19/211837_b01_05.jpg"),
         ("压轴登场", "data/samples/rogue/2026-09-19/210209_b04_09.jpg"),
         ("巡逻队", "data/samples/rogue/2026-09-19/215008_b11_01.jpg")]


def visual_tile_centers(im: np.ndarray, min_val: float = 14.0) -> np.ndarray:
    """高亮掩码 → 距离变换峰值 ⇒ **单格**质心（连成一片也能切开 ✓）。"""
    mask = TA._mask(im)
    B, G, R = (im[:, :, i].astype(int) for i in range(3))
    for m in (((R - G > 50) & (R - B > 50) & (R > 110)), ((B - R > 30) & (B - G > 20) & (B > 90))):
        mask = np.maximum(mask, np.uint8(m) * 255)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    dt = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    k = int(min_val * 2) | 1
    peak = (dt == cv2.dilate(dt, np.ones((k, k), np.float32)))
    ys, xs = np.where(peak & (dt >= min_val * 0.5))
    # 峰太密就按 DT 值贪心去重（保证格间距）
    pts = sorted(zip(dt[ys, xs], xs, ys), reverse=True)
    out = []
    for v, x, y in pts:
        if all((x - a) ** 2 + (y - b) ** 2 > (min_val * 1.2) ** 2 for _, a, b in out):
            out.append((v, int(x), int(y)))
    return np.array([[x, y] for _, x, y in out], float)


def predict_centers(L, side: bool, buildable_only: bool = True) -> np.ndarray:
    s = L.stage_px
    out = []
    for r in range(L.lv.height):
        for c in range(L.lv.width):
            t = L.lv.at(c, r)
            if not t or (buildable_only and not t.buildable):
                continue
            q = L.lv.project(c, r, side=side)
            if q:
                out.append(np.array(q) * s)
    return np.array(out, float)


def match(P: np.ndarray, Q: np.ndarray, gate: float):
    """中心距为主的一对一匹配（Hungarian；无 scipy 时用带互斥的贪心）。"""
    if not len(P) or not len(Q):
        return []
    D = np.linalg.norm(P[:, None, :] - Q[None, :, :], axis=2)
    C = np.where(D <= gate, D, 1e6)
    if HAVE_SCIPY:
        ri, ci = linear_sum_assignment(C)
        pairs = [(int(i), int(j)) for i, j in zip(ri, ci) if C[i, j] < 1e6]
    else:                                                        # 贪心 + 互斥
        pairs, used = [], set()
        for i, j in sorted(((i, j) for i in range(len(P)) for j in range(len(Q))), key=lambda z: C[z[0], z[1]]):
            if C[i, j] >= 1e6:
                break
            if i in {a for a, _ in pairs} or j in used:
                continue
            pairs.append((i, j))
            used.add(j)
    return pairs


def topology_ok(P: np.ndarray, Q: np.ndarray, pairs) -> float:
    """拓扑一致性（外援 §6）：匹配后"谁离谁近"的相对次序不该乱 ⇒ 返回一致比例。"""
    if len(pairs) < 3:
        return float("nan")
    keep = []
    for i, j in pairs:
        d = np.linalg.norm(P - P[i], axis=1)
        d[i] = np.inf
        k = int(np.argmin(d))
        if any(a == k for a, _ in pairs):
            keep.append((i, k))
    good = 0
    for i, k in keep:
        ji = dict(pairs)[i]
        jk = dict(pairs)[k]
        dP = np.linalg.norm(P[i] - P[k])
        dQ = np.linalg.norm(Q[ji] - Q[jk])
        # 匹配后距离不应出现数量级畸变（>3x 视为次序出错）
        if dQ <= dP * 3 and dQ >= dP / 3:
            good += 1
    return good / max(1, len(keep))


def fit_compare(Pm: np.ndarray, Qm: np.ndarray) -> dict:
    rng = np.random.default_rng(5)
    idx = rng.permutation(len(Pm))
    tr, te = idx[: int(len(idx) * 0.7)], idx[int(len(idx) * 0.7):]
    res = {}
    for name in ("similarity", "homography"):
        if name == "similarity":
            r = cv2.estimateAffinePartial2D(Pm[tr], Qm[tr], method=cv2.LMEDS)
            M = r[0] if r else None
            if M is None:
                res[name] = None
                continue
            f = lambda X: (M[:, :2] @ X.T).T + M[:, 2]                       # noqa: E731
        else:
            r = cv2.findHomography(Pm[tr], Qm[tr], cv2.RANSAC, 6.0)
            M = r[0] if r else None
            if M is None:
                res[name] = None
                continue
            f = lambda X: (lambda h: h[:, :2] / h[:, 2:3])(np.hstack([X, np.ones((len(X), 1))]) @ M.T)  # noqa: E731
        etr = np.linalg.norm(f(Pm[tr]) - Qm[tr], axis=1)
        ete = np.linalg.norm(f(Pm[te]) - Qm[te], axis=1) if len(te) else np.array([np.nan])
        res[name] = dict(tr_med=float(np.median(etr)), te_med=float(np.median(ete)),
                         te_p90=float(np.percentile(ete, 90)), te_max=float(ete.max()), M=M)
    return res


def main() -> int:
    print("scipy(Hungarian) %s" % ("可用 ✓" if HAVE_SCIPY else "不可用 ⇒ 用带互斥的贪心 ✗"))
    print("\n=== synthetic oracle（外援 §7：先证明这条链能恢复已知变换）")
    rng = np.random.default_rng(11)
    P0 = np.column_stack([rng.uniform(0, 1200, 30), rng.uniform(0, 700, 30)])
    a = np.radians(2.0)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    Q0 = P0 @ (0.90 * R).T + np.array([17, -31])
    pr = match(P0, Q0 + rng.normal(0, 0.6, Q0.shape), gate=120)
    topo = topology_ok(P0, Q0, pr)
    rr = fit_compare(P0[[i for i, _ in pr]], Q0[[j for _, j in pr]])
    print("  匹配对 %d/%d ｜ 拓扑一致 %.2f ｜ similarity test 中位 %.3f px ｜ homography test 中位 %.3f px ⇒ Δ=%.3f"
          % (len(pr), len(P0), topo, rr["similarity"]["te_med"], rr["homography"]["te_med"],
             rr["similarity"]["te_med"] - rr["homography"]["te_med"]))
    for nm, f in CASES:
        p = pathlib.Path(f)
        if not p.is_file():
            continue
        im = cv2.imread(str(p))
        L = BL.Loop(nm, None)
        Q = visual_tile_centers(im)
        print("\n=== %s（%s）视觉单格质心 %d 个" % (nm, p.name, len(Q)))
        if len(Q) < 6:
            print("  视觉格子太少 ⇒ 跳过")
            continue
        pitch = 133.0
        for side in (False, True):
            P = predict_centers(L, side)
            pr = match(P, Q, gate=pitch * 0.8)
            if len(pr) < 6:
                print("   side=%-5s 匹配对 %d（<6）⇒ 不作裁决" % (side, len(pr)))
                continue
            topo = topology_ok(P, Q, pr)
            Pm, Qm = P[[i for i, _ in pr]], Q[[j for _, j in pr]]
            rr = fit_compare(Pm, Qm)
            s, h = rr.get("similarity"), rr.get("homography")
            if not s or not h:
                print("   side=%-5s 拟合失败" % side)
                continue
            d = s["te_med"] - h["te_med"]
            print("   side=%-5s 配对 %3d ｜ 拓扑一致 %.2f ｜ similarity test 中位 %6.2f P90 %6.2f ｜ "
                  "homography test 中位 %6.2f P90 %6.2f ｜ Δ_test %+.2f %s"
                  % (side, len(pr), topo, s["te_med"], s["te_p90"], h["te_med"], h["te_p90"], d,
                     '⇒ H 明显更好 ⇒ 有投影成分 ✗' if d > s["te_med"] * 0.3 else
                     ('⇒ 两者相当 ⇒ 近似 similarity ✓' if topo > 0.6 else '⇒ 但拓扑一致性低 ⇒ 匹配不可信，不作裁决 ✗')))
    return 0


if __name__ == "__main__":
    sys.exit(main())
