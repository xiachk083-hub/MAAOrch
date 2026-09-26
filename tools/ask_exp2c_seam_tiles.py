#!/usr/bin/env python3
"""实验 2c（外援 §A-2c）：绿色高亮**内部**切单格 -> 一对一 tile correspondence。

流程：green highlight -> 单格质心 -> 与 TileCalc2 预测格心一对一匹配(Hungarian)
      -> **硬 fail-closed 闸**（配对少/未匹配率高/残差大/拓扑乱 => INCONCLUSIVE）
      -> 过闸才拟合 similarity vs homography（70/30 holdout，看 test 与 delta_test）

坑（留在代码里）：A 腐蚀断缝=0格（mask 自带 MORPH_OPEN 7x7 已抹平缝）；
B 用"小块中位数过滤"=0格（中位被噪声拖到 ~100px^2，真格子 ~1e4 被上限拒）；
C 选帧不能信 index 的 hl 字段、也不能手挑 => 实测 mask 面积选帧。
"""
from __future__ import annotations
import json, pathlib, sys
import cv2, numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import battle_loop as BL
import rogue_routes as RR
import tilepos_audit as TA
from scipy.optimize import linear_sum_assignment

PITCH = 130.0
GATE = dict(n_min=8, unmatched_max=0.60, match_res_max=35.0, topo_min=0.70)
GAME_LOG = pathlib.Path(r"D:/Xiach/M-A/MAA/MAA-v6.12.2-win-x64/debug/gui.log")


def green_tiles(im):
    mask = TA._mask(im)
    n0, _, st0, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n0 <= 1: return np.zeros((0, 2))
    best = max(range(1, n0), key=lambda i: st0[i, 4])
    reg = (mask == best)
    g = im[:, :, 1].astype(np.float32); v = g[reg]
    if v.size < 5000: return np.zeros((0, 2))
    med = float(np.median(v))
    for delta in (8.0, 10.0, 12.0, 15.0, 20.0, 6.0, 5.0):
        strong = np.uint8(reg & (g >= med + delta)) * 255
        n, lab, st, ce = cv2.connectedComponentsWithStats(strong, 8)
        cand = [(int(st[i, 4]), i) for i in range(1, n)
                if st[i, 4] > 300 and st[i, 3] <= 2.2 * st[i, 2] and st[i, 2] <= 2.2 * st[i, 3]]
        if len(cand) < 6: continue
        cand.sort(reverse=True); top = cand[:20]
        m = float(np.median([a for a, _ in top]))
        keep = [(a, i) for a, i in top if 0.45 * m <= a <= 2.2 * m]
        if len(keep) >= 6: return np.array([ce[i] for _, i in keep], float)
    return np.zeros((0, 2))


def predict(L, side):
    s, out = L.stage_px, []
    for r in range(L.lv.height):
        for c in range(L.lv.width):
            t = L.lv.at(c, r)
            if not t or not t.buildable: continue
            q = L.lv.project(c, r, side=side)
            if q: out.append(np.array(q) * s)
    return np.array(out, float)


def match(P, Q, gate):
    D = np.linalg.norm(P[:, None, :] - Q[None, :, :], axis=2)
    C = np.where(D <= gate, D, 1e6)
    ri, ci = linear_sum_assignment(C)
    return [(int(i), int(j), float(D[i, j])) for i, j in zip(ri, ci) if C[i, j] < 1e6]


def topo(P, Q, pr):
    m = {i: j for i, j, _ in pr}; good = tot = 0
    for i in m:
        d = np.linalg.norm(P - P[i], axis=1); d[i] = np.inf
        k = int(np.argmin(d))
        if k not in m: continue
        tot += 1
        dP = np.linalg.norm(P[i] - P[k]); dQ = np.linalg.norm(Q[m[i]] - Q[m[k]])
        if dP / 3 <= dQ <= dP * 3: good += 1
    return (good / tot) if tot else float("nan")


def fit(Pm, Qm):
    rng = np.random.default_rng(5); idx = rng.permutation(len(Pm))
    tr, te = idx[: int(len(idx) * 0.7)], idx[int(len(idx) * 0.7):]
    out = {}
    for name in ("similarity", "homography"):
        if name == "similarity":
            r = cv2.estimateAffinePartial2D(Pm[tr], Qm[tr], method=cv2.LMEDS); M = r[0] if r else None
        else:
            r = cv2.findHomography(Pm[tr], Qm[tr], cv2.RANSAC, 6.0); M = r[0] if r else None
        if M is None: out[name] = None; continue
        if M.shape == (2, 3):
            f = lambda X, M=M: (M[:, :2] @ X.T).T + M[:, 2]
        else:
            f = lambda X, M=M: (lambda h: h[:, :2] / h[:, 2:3])(np.hstack([X, np.ones((len(X), 1))]) @ M.T)
        etr = np.linalg.norm(f(Pm[tr]) - Qm[tr], axis=1)
        ete = np.linalg.norm(f(Pm[te]) - Qm[te], axis=1) if len(te) else np.array([np.nan])
        out[name] = dict(tr=float(np.median(etr)), te=float(np.median(ete)),
                         p90=float(np.percentile(ete, 90)), M=M)
    return out


def pick_frames(limit=6):
    rows = sorted((json.loads(l) for l in (HERE.parent / "data/samples/rogue/index.jsonl")
                   .read_text(encoding="utf-8").splitlines() if l.strip()), key=lambda r: r["t"])
    starts = [(l[12:19], l.split("开始战斗: ")[-1].strip()[:20])
              for l in GAME_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
              if len(l) > 21 and l[1:11] == "2026-09-19" and l[12:19] >= "20:42:00" and "开始战斗: " in l]
    def name_at(t_):
        nm = None
        for st, n in starts:
            if st <= t_: nm = n
        return nm
    best = {}
    for r in rows:
        nm = name_at(r["t"][11:]); pth = pathlib.Path(r["file"])
        if not nm or not pth.is_file() or (nm in best and best[nm][0] > 60000): continue
        im = cv2.imread(str(pth))
        if im is None: continue
        area = int((TA._mask(im) > 0).sum())
        if nm not in best or area > best[nm][0]: best[nm] = (area, r["file"])
    out = sorted(((a, nm, f) for nm, (a, f) in best.items()), reverse=True)
    return [(nm, f) for a, nm, f in out[:limit] if a >= 8000]


def main():
    print("闸（fail-closed）：配对>=%d | 未匹配率<=%.2f | 匹配残差中位<=%.0fpx | 拓扑>=%.2f"
          % (GATE["n_min"], GATE["unmatched_max"], GATE["match_res_max"], GATE["topo_min"]))
    frames = pick_frames()
    print("按实测 mask 面积选帧：%s" % ", ".join("%s(%s)" % (nm, pathlib.Path(f).name) for nm, f in frames))
    for nm, f in frames:
        im = cv2.imread(f)
        RR.ensure_level(nm)                       # 缺 level 文件就先补（新图会冒出来）
        try:
            L = BL.Loop(nm, None)
        except Exception as exc:                  # noqa: BLE001
            print("%s=== %s 跳过（%s）" % (chr(10), nm, str(exc)[:48]))
            continue
        Q = green_tiles(im)
        print("%s=== %s（%s）切出单格 %d 个" % (chr(10), nm, pathlib.Path(f).name, len(Q)))
        if len(Q) < GATE["n_min"]:
            print("   -> INCONCLUSIVE（切出的单格太少 ✗）"); continue
        for side in (False, True):
            P = predict(L, side); pr = match(P, Q, PITCH * 0.8)
            res = float(np.median([r for _, _, r in pr])) if pr else 999.0
            un = 1 - len(pr) / max(1, len(Q)); tp = topo(P, Q, pr)
            fail = (len(pr) < GATE["n_min"] or un > GATE["unmatched_max"]
                    or res > GATE["match_res_max"] or not (tp >= GATE["topo_min"]))
            print("   side=%-5s 配对 %2d | 未匹配率 %.2f | 匹配残差中位 %5.1f | 拓扑 %.2f => %s"
                  % (side, len(pr), un, res, tp, "INCONCLUSIVE ✗（闸挡下）" if fail else "过闸 ✓"))
            if fail: continue
            rr = fit(P[[i for i, _, _ in pr]], Q[[j for _, j, _ in pr]])
            s, h = rr.get("similarity"), rr.get("homography")
            if not s or not h:
                print("        拟合失败 ✗"); continue
            d = s["te"] - h["te"]
            print("        similarity train %.2f / test %.2f (P90 %.2f) | homography train %.2f / test %.2f (P90 %.2f) | delta_test %+.2f %s"
                  % (s["tr"], s["te"], s["p90"], h["tr"], h["te"], h["p90"], d,
                     "=> H 明显更好 => 有投影成分 ✗" if d > s["te"] * 0.3 else "=> 两者相当 => 近似 similarity ✓"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
