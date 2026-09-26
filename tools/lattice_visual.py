#!/usr/bin/env python3
"""**视觉格网**（visual lattice）：不依赖 tile-pos 的 view，直接从画面里把地图格网拟出来。

为什么需要它：官方 TileCalc2 投影在肉鸽图上体检判 `DATA/RENDER MISMATCH`
（`tilepos_audit` 实测：落魄骑士轴夹角 [15.1,54.4] / chamfer 25.8px ✗），
而三态闸给的出路就是 `fallback: "visual_lattice"` ✓ —— 本工具是那条路的第一版。

方法（全程只有"看图"，没有任何官方数据参与）：
  ① 取地图 ROI（去掉顶部 HUD / 底部手牌区，复用 `tilepos_audit` 的 VIEW 边界）
  ② Sobel 梯度幅值 → 去均值 → 汉宁窗 → 2D FFT
  ③ 频谱里挑**最强的两个非共线峰**：它们是格子的两个空间频率 ⇒ 反解出两支基向量
  ④ 两支波的**相位**给一个落在格点上的原点（解 2×2 线性方程）
  ⑤ 按用户认过的样式画：**竖线横线 + 格心点**（不是方框 ✗）

**fail-closed**：基向量长度/夹角/峰值强度任一不合规 ⇒ 打印原因、**不画**（宁可不给 ✓）。

用法：
  python tools/lattice_visual.py --img data/samples/rogue/.../x.jpg --out data/lattice_1.png
  python tools/lattice_visual.py --img ... --show-official 分赃不均   # 顺手叠一份官方投影做对照
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import cv2
import numpy as np

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import tilepos_audit as TA                                        # noqa: E402  (只借 VIEW 边界)

# 格距（1080p 屏幕像素）：PA-1 实测列步 ~126、行步 ~94 ⇒ 肉鸽图若同机位应在同量级。
# ⚠️ 第一版只写 (34,300) 结果锁到**装饰纹理的周期**（59~79px，约真格距的一半）✗ ——
# 纹理是更高频的周期 ⇒ 判据必须**优先最大格距**，并且下限收紧到 95px。
ANG_RANGE = (72.0, 108.0)      # 菱形格在 45° 俯视下的两支基向量夹角，实测 PA-1 ≈ 87°
INLIER_MIN = 0.55              # 内点率下限（fail-closed：不够就不画）


def roi_of(frame: np.ndarray) -> tuple[int, int, int, int]:
    """地图 ROI = 去掉顶部 HUD 与底部手牌带（沿用 tilepos_audit 的 VIEW 边界）。"""
    y0, y1, x0, x1 = TA.VIEW
    h, w = frame.shape[:2]
    y1 = int(h * 0.82)                     # 底部手牌/费用条一律不进 ROI
    return max(0, x0), max(0, y0), min(w, x1 or w), min(h, y1)


def fit_lattice_fft(frame: np.ndarray, verbose: bool = False):
    """**第一版（失败，留作教训）**：FFT 找周期。

    实测：这张图上锁到的"周期"都是 59~79px，只有真格距的一半左右 —— 装饰纹理/谐波比格子更"显眼" ✗。
    收紧阈值后 4 张全部 fail-closed 拒画。结论：**频域法在带画的地图上不可靠** ⇒ 见 fit_lattice()。
    """
    raise NotImplementedError("FFT 版已弃用（见 docstring）")


def fit_lattice(frame: np.ndarray, verbose: bool = False):
    """角点法：地图 ROI 里的**角点**构成格子点集 ⇒ 位移直方图定基向量，再搜相位定原点。

    为什么比 FFT 稳：格子的角点是**可重复的点集**（纹理角点更随机），
    而且能直接算**内点率**当闸（fail-closed：内点率不够就不画 ✓）。
    返回 (s1, s2, origin, info)；失败则 (None, None, None, info)。
    """
    x0, y0, x1, y1 = roi_of(frame)
    roi = frame[y0:y1, x0:x1]
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    info: dict = {"roi": (x0, y0, x1, y1)}
    pts = cv2.goodFeaturesToTrack(g, maxCorners=600, qualityLevel=0.03, minDistance=9,
                                  blockSize=7, useHarrisDetector=False)
    if pts is None or len(pts) < 40:
        info["why"] = "角点太少（%s）" % (0 if pts is None else len(pts))
        return None, None, None, info
    P = pts.reshape(-1, 2).astype(np.float64)

    # 位移直方图：只统计 40~220px 的近邻位移，按 (角度, 长度) 量化找峰
    diff = (P[:, None, :] - P[None, :, :]).reshape(-1, 2)
    L = np.hypot(diff[:, 0], diff[:, 1])
    sel = (L >= 40) & (L <= 220)
    d, l = diff[sel], L[sel]
    if len(d) < 100:
        info["why"] = "近邻位移样本太少（%d）" % len(d)
        return None, None, None, info
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180.0
    bins_a = np.arange(0, 181, 3)
    bins_l = np.arange(40, 221, 6)
    hist, _, _ = np.histogram2d(ang, l, bins=[bins_a, bins_l])
    # 取两个非共线方向的峰（夹角要求 72~108°）
    idx = np.dstack(np.unravel_index(np.argsort(-hist, axis=None), hist.shape))[0]
    vecs = []
    for ia, il in idx[:400]:
        if hist[ia, il] < 20:
            break
        A = bins_a[ia] + 1.5
        Ll = bins_l[il] + 3.0
        m = np.abs(((ang - A + 90) % 180) - 90) < 6
        m &= np.abs(l - Ll) < 8
        if m.sum() < 12:
            continue
        v = d[m].mean(0)                       # 该方向的平均位移 = 候选基向量
        if np.hypot(*v) < 40:
            continue
        vecs.append((float(m.sum()) * float(np.hypot(*v)), v, int(m.sum())))
    if not vecs:
        info["why"] = "位移直方图里没有稳定峰（${len(d)} 个位移）".replace("${len(d)}", str(len(d)))
        return None, None, None, info
    vecs.sort(key=lambda z: -z[0])
    s1 = vecs[0][1]
    s2 = None
    for _sc, v, _n in vecs[1:]:
        cosang = float(np.dot(s1, v) / (np.hypot(*s1) * np.hypot(*v)))
        a = np.degrees(np.arccos(np.clip(abs(cosang), -1, 1)))
        if ANG_RANGE[0] <= a <= ANG_RANGE[1]:
            s2 = v
            break
    if s2 is None:
        info["why"] = "只锁到一个方向（%d 个候选）" % len(vecs)
        return None, None, None, info
    if s1[0] < 0:
        s1 = -s1
    if s2[0] > 0:
        s2 = -s2
    info.update(s1=[round(float(v), 1) for v in s1], s2=[round(float(v), 1) for v in s2],
                L1=round(float(np.hypot(*s1)), 1), L2=round(float(np.hypot(*s2)), 1),
                ang=round(float(np.degrees(np.arccos(np.clip(
                    abs(float(np.dot(s1, s2) / (np.hypot(*s1) * np.hypot(*s2)))), -1, 1)))), 1))

    # 搜相位（原点）—— 在**一个格子内**网格搜索，取内点率最高的偏移 ✓
    Mmat = np.array([[s1[0], s2[0]], [s1[1], s2[1]]])
    Minv = np.linalg.inv(Mmat)
    best = (-1.0, None)
    for u in np.linspace(0, 1, 24, endpoint=False):
        for vv in np.linspace(0, 1, 24, endpoint=False):
            o = P[0] - (u * s1 + vv * s2)
            frac = (P - o) @ Minv                      # 每个角点在格子坐标下的位置
            res = np.abs(frac - np.round(frac)).max(1) # 到最近格点的最大相对偏差
            inl = float((res < 0.18).mean())
            if inl > best[0]:
                best = (inl, o)
    info["inlier"] = round(best[0], 3)
    info["n_corners"] = int(len(P))
    if best[1] is None or best[0] < 0.55:
        info["why"] = ("内点率太低 %.2f（角点 %d 个 / 合格阈值 0.55）⇒ 拟合不可信"
                       % (best[0], len(P)))
        return None, None, None, info
    origin = np.array([float(best[1][0] + x0), float(best[1][1] + y0)])
    info["origin"] = [round(float(origin[0]), 1), round(float(origin[1]), 1)]
    if verbose:
        print("  角点法：", info)
    return s1, s2, origin, info


def draw(frame: np.ndarray, s1, s2, origin, info) -> np.ndarray:
    """按"竖线横线 + 格心点"画（用户认过的样式 ✓）。"""
    out = frame.copy()
    x0, y0, x1, y1 = info["roi"]
    H, W = out.shape[:2]
    # 格心 = 原点 + i·s1 + j·s2；每个格心画两条"格线方向"的短线（沿 s1 / s2）
    n1 = n2 = 14
    for i in range(-n1, n1 + 1):
        for j in range(-n2, n2 + 1):
            c = origin + i * s1 + j * s2
            if not (x0 <= c[0] <= x1 and y0 <= c[1] <= y1):
                continue
            a = c - s1 / 2
            b = c + s1 / 2
            cv2.line(out, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), (60, 220, 60), 1, cv2.LINE_AA)
            a = c - s2 / 2
            b = c + s2 / 2
            cv2.line(out, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), (60, 220, 60), 1, cv2.LINE_AA)
            cv2.circle(out, (int(c[0]), int(c[1])), 2, (0, 255, 255), -1, cv2.LINE_AA)
    cv2.putText(out, "visual lattice  s1=(%.0f,%.0f) s2=(%.0f,%.0f)  ang=%.0fdeg  inlier=%.2f"
                % (s1[0], s1[1], s2[0], s2[1], info["ang"], info.get("inlier", 0)),
                (24, H - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 220, 60), 2, cv2.LINE_AA)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="视觉格网（从画面自拟，不依赖官方投影）")
    ap.add_argument("--img", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--show-official", default="", help="顺手叠一份官方投影（关卡名）做对照")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    frame = cv2.imread(a.img)
    if frame is None:
        print("读不到图：%s" % a.img)
        return 2
    s1, s2, origin, info = fit_lattice(frame, a.verbose)
    if s1 is None:
        print("✗ 拟合失败（fail-closed，不画）：%s" % info.get("why"))
        print("  峰值：", info.get("top_peaks"))
        return 3
    out = draw(frame, s1, s2, origin, info)
    dst = a.out or str(pathlib.Path(a.img).with_name(pathlib.Path(a.img).stem + "_lattice.png"))
    cv2.imwrite(dst, out)
    print("✓ %s" % dst)
    print("  格距 %.1f / %.1f px  夹角 %.1f°  内点率 %.2f（角点 %s 个）"
          % (info["L1"], info["L2"], info["ang"], info.get("inlier", 0), info.get("n_corners")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
