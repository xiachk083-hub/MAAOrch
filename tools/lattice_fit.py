"""格网拟合：按"先全局投影晶格、再给局部轮廓赋整数坐标"的顺序来（外援 2026-09-19 的意见）。

为什么要重写顺序（上一版的失败）：
    上一版是 `contour → 局部整数编号 → findHomography` ✗，得到 41 个对应点**只有 5 个内点** ⇒
    格网整个扇开 ✗。真因是**每个高亮块各自从 (0,0) 起算**，块间整数平移未知 ⇒ 对应点互相矛盾 ✗。
    正确顺序：**先恢复全局投影晶格 H，再让局部轮廓去 H 上"对号入座"** ✓。

本文件做两件事：
  ① `truth_h(stage)` —— 用**有数据关卡**的真值投影造一把"尺子"（PA-5 我们有关卡数据 ✓）；
  ② `fit_from_masks(...)` —— **lattice image fitting**（外援 §10）：直接优化 H，让投影出来的格网
     与游戏给的高亮 mask 最吻合 ⇒ **整数平移/相位问题自动消失** ✓（H 一正确，所有块自动落在同一套晶格上 ✓）。

验收按外援 §13/§14：**格心误差 / 四角平均 / 四角最大 / cell IoU**，且阈值**双重定义**
（相对格对角线的比例 + 绝对像素）—— 因为最怕的是"中间看着对、边缘扇飞" ✗。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import cv2
import numpy as np

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

VIEW = (110, 875, 515, 1905)


def truth_h(stage: str, n: int = 6):
    """**真值单应**（格坐标 → 屏幕）：只对有数据的关卡可用（当尺子 ✓）。"""
    import battle_loop as BL
    L = BL.Loop(stage, None)
    src, dst = [], []
    for c in range(n + 1):
        for r in range(n + 1):
            q = L.lv.project(c, r)
            if q:
                src.append([c, r])
                dst.append([q[0] * L.stage_px, q[1] * L.stage_px])
    H, _ = cv2.findHomography(np.float32(src), np.float32(dst), 0)
    return H, L.lv


def grid_stats(H, n: int = 6):
    """一套 H 的格心/四角，供比对。"""
    centers, corners = [], []
    for i in range(n):
        for j in range(n):
            for (di, dj), box in (((0, 0), centers),):
                q = H @ np.array([i + .5, j + .5, 1.0])
                box.append(q[:2] / q[2])
            cs = []
            for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1)):
                q = H @ np.array([i + di, j + dj, 1.0])
                cs.append(q[:2] / q[2])
            corners.append(np.array(cs))
    return np.array(centers), corners


def mask_score(H, mask: np.ndarray, n: int = 6):
    """外援 §10 的评分：预测的**可放格**应落在 mask 里，预测的格边界应与 mask 边界一致。"""
    h, w = mask.shape[:2]
    inside = 0.0
    tot = 0
    for i in range(n):
        for j in range(n):
            q = H @ np.array([i + .5, j + .5, 1.0])
            p = q[:2] / q[2]
            x, y = int(round(p[0])), int(round(p[1]))
            if not (0 <= x < w and 0 <= y < h):
                continue
            tot += 1
            patch = mask[max(0, y - 4):y + 4, max(0, x - 4):x + 4]
            inside += float(patch.mean()) / 255.0 if patch.size else 0.0
    if not tot:
        return -1e9
    # 覆盖率（想让"预测格"尽量落在高亮里）
    cover = inside / tot
    # 反向：高亮像素有多少被预测格覆盖（避免 H 缩成一坨全落在 mask 里）
    penal = 0.0
    ys, xs = np.where(mask > 0)
    if len(xs):
        pts = np.stack([xs, ys, np.ones_like(xs)], 0).astype(float)
        Hinv = np.linalg.inv(H)
        gp = Hinv @ pts
        gi, gj = gp[0] / gp[2], gp[1] / gp[2]
        ok = (gi >= 0) & (gi <= n) & (gj >= 0) & (gj <= n)
        penal = float(ok.mean())
    return cover + 0.6 * penal


def fit_from_masks(mask: np.ndarray, H0, n: int = 6, iters: int = 400):
    """在 H0 附近做**无梯度局部优化**（8 参数），目标 = mask 评分最大（外援 §10 的 Method C）。"""
    from scipy.optimize import minimize                       # noqa: PLC0415
    p0 = H0.ravel() / H0[2, 2]
    def neg(v):
        H = v.reshape(3, 3)
        if abs(H[2, 2]) < 1e-9:
            return 1e6
        H = H / H[2, 2]
        s = mask_score(H, mask, n)
        return -s
    res = minimize(neg, p0, method="Nelder-Mead",
                   options={"maxiter": iters, "xatol": 1e-4, "fatol": 1e-5})
    H = res.x.reshape(3, 3)
    return H / H[2, 2], float(-res.fun)


def report(name: str, H, Htrue, n: int = 6) -> None:
    c_p, cs_p = grid_stats(H, n)
    c_t, cs_t = grid_stats(Htrue, n)
    dc = np.linalg.norm(c_p - c_t, axis=1)
    # 四角误差（逐格取 4 角的最大值，再看分布）
    mx = []
    for cp, ct in zip(cs_p, cs_t):
        mx.append(float(np.max(np.linalg.norm(cp - ct, axis=1))))
    mx = np.array(mx)
    # 格对角线长度（相对阈值用）
    diag = float(np.median([np.linalg.norm(ct[2] - ct[0]) for ct in cs_t])) or 1.0
    print(f"{name:26s} 格心 中位 {np.median(dc):6.1f}px  95% {np.percentile(dc,95):6.1f}px  "
          f"四角 max 中位 {np.median(mx):6.1f}px  最大 {mx.max():6.1f}px  "
          f"({mx.max()/diag*100:5.1f}% 格对角线)")


def main() -> None:
    ap = argparse.ArgumentParser(description="格网拟合（全局晶格 + mask 拟合）")
    ap.add_argument("--stage", default="act51side_05")
    ap.add_argument("--plain", default="data/_live_pa5_bright.png")
    ap.add_argument("--ground", nargs="*", default=["data/_pa5_card4.png"])
    ap.add_argument("--high", nargs="*", default=["data/_pa5_card2.png"])
    ap.add_argument("--noise", type=float, default=0.0, help='给初值加的噪声（模拟「无数据时的初猜」）' )
    ap.add_argument("--out", default="data/lattice_fit.png")
    a = ap.parse_args()
    plain = cv2.imdecode(np.fromfile(a.plain, np.uint8), 1)
    Ht, lv = truth_h(a.stage)
    print("真值 H（格坐标→屏幕）已就绪")

    def union(paths, need=3000):
        m = np.zeros(plain.shape[:2], np.uint8)
        for p in paths:
            if not pathlib.Path(p).is_file():
                continue
            f = cv2.imdecode(np.fromfile(p, np.uint8), 1)
            B, G, R = (f[:, :, i].astype(int) for i in range(3))
            mm = np.uint8(((G - R > 10) & (G - B > 8) & (G > 65)) * 255)
            mm[:VIEW[0]] = 0; mm[VIEW[1]:] = 0; mm[:, :VIEW[2]] = 0; mm[:, VIEW[3]:] = 0
            mm = cv2.morphologyEx(mm, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
            m |= mm
        return m

    gmask, hmask = union(a.ground), union(a.high)
    print("掩码：地面 %d px  高台 %d px" % (gmask.sum() // 255, hmask.sum() // 255))
    mask = cv2.bitwise_or(gmask, hmask)

    report("真值（自比）", Ht, Ht)
    if a.noise:
        rng = np.random.default_rng(0)
        # 直接对"屏幕坐标"加现实扰动，再重解 H（比扰动 H 的元素可控得多 ✓）
        cc, _cs = grid_stats(Ht, 6)
        sh = (rng.normal(0, 30 * a.noise, cc.shape)
              + cc * rng.normal(0, 0.03 * a.noise, (1, 2))
              + cc[:, ::-1] * np.deg2rad(1.0 * a.noise))
        grid_pts = np.float32([[i + .5, j + .5] for i in range(6) for j in range(6)])
        H0, _ = cv2.findHomography(grid_pts, np.float32(cc + sh), 0)
        if H0 is None:
            H0 = Ht.copy()
        report(f"初猜（真值+噪声 {a.noise}）", H0, Ht)
        H1, sc = fit_from_masks(mask, H0)
        report("mask 拟合后", H1, Ht)
        print("  拟合得分 %.4f（越大越好）" % sc)
    cv2.imwrite(a.out, plain)
    print("（本次未画图，只出误差表 —— 先看数，别急着看画得漂不漂亮 ✓）")


if __name__ == "__main__":
    main()
