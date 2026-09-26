"""只靠"看"画出 3D 格网：从**游戏自己画的可放置高亮**反推整张格子网。

为什么这么做（用户 2026-09-19 的要求）：新活动没有关卡数据时，格网/分类也得能标。
游戏其实已经把答案画出来了 —— **选中一张牌，可放置的格子会被高亮** ✓。而高亮区域的
**轮廓是阶梯状**的（每一步正好跨一格）⇒ 轮廓的拐点就是**"屏幕点 ↔ 整数格坐标"的对应点** ✓
⇒ 有足够多这样的点就能**拟合单应**（透视投影）⇒ 反推出整张格网 ✓✓。

分类（用户要的三类）：
    地面可放 = 选中**地面**干员时被高亮的格
    高台可放 = 选中**高台（远程）**干员时被高亮的格
    不可部署 = 两次都没被高亮的格
⇒ 所以至少要两张选中帧（一地面、一高台）；同一张牌重复选不影响结果 ✓。

用法：
    python tools/lattice_from_highlight.py --frames data/_pa5_card4.png data/_pa5_card2.png \
        --plain data/_live_pa5_bright.png --out data/_pa5_lattice.png
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

# 战场视口（去掉 HUD/右侧/手牌/左侧面板）—— 格网只在这里面画
VIEW = (110, 875, 515, 1905)


def _masks(f: np.ndarray) -> np.ndarray:
    """高亮掩码：淡绿/淡青的可放置tag。用在"选中帧 − 未选中帧"的差分上更稳。"""
    B, G, R = (f[:, :, i].astype(int) for i in range(3))
    return np.uint8(((G - R > 10) & (G - B > 8) & (G > 65)) * 255)


def highlight_masks(plain: np.ndarray, sel: np.ndarray, need: int = 3000) -> list:
    """返回该选中帧里的高亮连通块（已在战场视口内、去噪）。"""
    y0, y1, x0, x1 = VIEW
    m = _masks(sel)
    m[:y0] = 0
    m[y1:] = 0
    m[:, :x0] = 0
    m[:, x1:] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    n, lab, st, _ce = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        if st[i, 4] < need:
            continue
        comp = np.uint8(lab == i) * 255
        c = max(cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0],
                key=cv2.contourArea)
        ap = cv2.approxPolyDP(c, 0.01 * cv2.arcLength(c, True), True).reshape(-1, 2)
        out.append({"mask": comp, "poly": ap, "area": int(st[i, 4])})
    return out


def _dirs_of(poly: np.ndarray) -> list[tuple[float, float]]:
    """轮廓各边的**单位方向**（把方向归一化成"指向右/下"，避免 ±180 重复）。"""
    ds = []
    for i in range(len(poly)):
        v = poly[(i + 1) % len(poly)].astype(float) - poly[i].astype(float)
        n = float(np.hypot(*v))
        if n < 12:                     # 太短的边丢掉（噪点）
            continue
        if v[0] < 0 or (v[0] == 0 and v[1] < 0):
            v = -v
        ds.append((v[0] / n, v[1] / n))
    return ds


def step_axis(d: tuple[float, float], axes: list[tuple[float, float]]) -> int:
    """这条边属于哪根格轴（0/1）：与哪根轴夹角小就是它。"""
    best, bi = -2.0, 0
    for i, a in enumerate(axes):
        dot = d[0] * a[0] + d[1] * a[1]
        if dot > best:
            best, bi = dot, i
    return bi


def fit_lattice(clus: list[dict], verbose: bool = True):
    """从高亮块拟合 (单应, 屏幕点→整数格)。返回 (H, 采样统计)。"""
    # ① 两根格轴：把所有轮廓边方向聚成两类
    ds = [d for cl in clus for d in _dirs_of(cl["poly"])]
    if len(ds) < 4:
        return None, {}
    first = ds[0]
    axes = [first, (-first[0], -first[1])]
    for _ in range(12):                       # 简单 k-means（两类）
        groups = [[], []]
        for d in ds:
            groups[step_axis(d, axes)].append(d)
        new = []
        for g in groups:
            if not g:
                new = axes
                break
            v = np.mean(np.array(g), axis=0)
            v = v / max(1e-6, np.hypot(*v))
            new.append((float(v[0]), float(v[1])))
        if np.allclose(new, axes):
            break
        axes = new
    if verbose:
        print('  两根格轴（单位方向）: %s' % [(round(a[0], 3), round(a[1], 3)) for a in axes])

    # ② 拐点 → (屏幕点, 整数格坐标)：走轮廓，每段按所属轴 ±1
    pts, idx = [], []
    steps_by_axis: dict[int, list[float]] = {}
    for cl in clus:
        poly, i0, j0 = cl["poly"], 0, 0
        for k in range(len(poly)):
            p = poly[k]
            if k > 0:
                v = poly[k].astype(float) - poly[k - 1].astype(float)
                if np.hypot(*v) < 12:
                    continue
                if v[0] < 0 or (v[0] == 0 and v[1] < 0):
                    v = -v
                a = step_axis((v[0] / np.hypot(*v), v[1] / np.hypot(*v)), axes)
                # 这一段跨了几格？**不能用固定像素步长**（透视下格距随位置变 ✗，
                # 第一版写 round(len/42) ⇒ 41 个对应点只有 5 个内点、格网整个扇开 ✗）。
                # 正解：用**该轴自己的步长统计** —— 单步段是最短的那批 ⇒ 取低分位作一步的长度 ✓
                loc = float(np.linalg.norm(v))
                steps_by_axis.setdefault(a, []).append(loc)
                pool = sorted(steps_by_axis[a])
                unit = pool[len(pool) // 4] if len(pool) >= 4 else (pool[0] if pool else loc)
                step = max(1, int(round(loc / max(8.0, unit))))
                if a == 0:
                    i0 += step
                else:
                    j0 += step
            pts.append(p)
            idx.append((i0, j0))
        cl["idx"] = idx
    src = np.float32(pts).reshape(-1, 1, 2)
    dst = np.float32([(float(i), float(j)) for i, j in idx]).reshape(-1, 1, 2)
    if len(src) < 8:
        return None, {}
    H, inl = cv2.findHomography(dst, src, cv2.RANSAC, 3.0)   # 格坐标 → 屏幕
    if verbose:
        print('  对应点 %d 个，内点 %d' % (len(src), int(inl.sum()) if inl is not None else -1))
    return H, {"n_pts": len(src), "n_inl": int(inl.sum()) if inl is not None else 0,
               "axes": axes}


def build_annotated(plain: np.ndarray, gnd_frames: list, high_frames: list):
    """产出带 3D 格网的图（绿=地面可放 / 黄=高台可放 / 灰=都不可放）。"""
    g_all = np.zeros(plain.shape[:2], np.uint8)
    h_all = np.zeros(plain.shape[:2], np.uint8)
    for f in gnd_frames:
        for cl in highlight_masks(plain, f):
            g_all |= cl["mask"]
    for f in high_frames:
        for cl in highlight_masks(plain, f):
            h_all |= cl["mask"]
    return g_all, h_all


def main() -> None:
    ap = argparse.ArgumentParser(description="从可放置高亮反推 3D 格网（不依赖关卡数据）")
    ap.add_argument("--plain", default="data/_live_pa5_bright.png", help="未选中（干净）的帧")
    ap.add_argument("--ground", nargs="*", default=[], help="选中**地面**干员的帧")
    ap.add_argument("--high", nargs="*", default=[], help="选中**高台**干员的帧")
    ap.add_argument("--out", default="data/lattice_seen.png")
    a = ap.parse_args()
    plain = cv2.imdecode(np.fromfile(a.plain, np.uint8), 1)
    gf = [cv2.imdecode(np.fromfile(p, np.uint8), 1) for p in a.ground if pathlib.Path(p).is_file()]
    hf = [cv2.imdecode(np.fromfile(p, np.uint8), 1) for p in a.high if pathlib.Path(p).is_file()]
    if not gf and not hf:
        raise SystemExit('至少给一类选中帧（--ground / --high）')
    g_all, h_all = build_annotated(plain, gf, hf)
    print('地面高亮 %d px   高台高亮 %d px' % (g_all.sum() // 255, h_all.sum() // 255))
    clus = highlight_masks(plain, (gf or hf)[0])
    print('用于拟合的块数:', len(clus), ' 面积:', [c["area"] for c in clus])
    H, info = fit_lattice(clus)
    if H is None:
        raise SystemExit('拟合失败（轮廓太碎）')
    # 画格网：在格坐标里扫一个范围，投影回屏幕
    y0, y1, x0, x1 = VIEW
    vis = plain.copy()
    drawn = gnd = high = 0
    for i in range(-3, 26):
        for j in range(-3, 26):
            q = H @ np.array([i, j, 1.0])
            q2 = H @ np.array([i + 1, j + 1, 1.0])
            if abs(q[2]) < 1e-6 or abs(q2[2]) < 1e-6:
                continue
            p = (q[:2] / q[2]).astype(float)
            p2 = (q2[:2] / q2[2]).astype(float)
            if not (x0 - 60 < p[0] < x1 + 60 and y0 - 60 < p[1] < y1 + 60):
                continue
            # 该格的四角（用相邻格点推）——按单应取四个角
            corners = []
            for (di, dj) in ((0, 0), (1, 0), (1, 1), (0, 1)):
                qq = H @ np.array([i + di, j + dj, 1.0])
                corners.append((qq[:2] / qq[2]).astype(int))
            quad = np.array(corners, np.int32)
            cx = int(np.mean([c[0] for c in corners])); cy = int(np.mean([c[1] for c in corners]))
            if not (0 <= cx < plain.shape[1] and 0 <= cy < plain.shape[0]):
                continue
            ing = g_all[max(0, cy - 3):cy + 3, max(0, cx - 3):cx + 3].mean() > 40
            inh = h_all[max(0, cy - 3):cy + 3, max(0, cx - 3):cx + 3].mean() > 40
            col = (0, 220, 0) if ing else (0, 200, 255) if inh else (120, 120, 120)
            cv2.polylines(vis, [quad], True, col, 2 if (ing or inh) else 1)
            drawn += 1; gnd += 1 if ing else 0; high += 1 if inh else 0
    txt = f"seen lattice: {drawn} cells  ground {gnd}  high {high}  (no level data)"
    cv2.putText(vis, txt, (18, plain.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                (0, 220, 255), 2, cv2.LINE_AA)
    cv2.imwrite(a.out, vis)
    print('→ %s   %s' % (a.out, txt))


if __name__ == "__main__":
    main()
