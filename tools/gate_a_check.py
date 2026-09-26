#!/usr/bin/env python3
"""Gate A 逐样本验收：干员框 → 头顶锚区 → 徽标 → 局部几何 → 每帧残差（格）。

链条（每一环都有出处 ✓）：
  干员框   = MAA 自带 `operators_det.onnx`（640×640 / RGB / /255 / CHW / {1,5,8400} / thr 0.3 / **NMS** ✓）
  头顶锚区 = 框中心 ±0.3W、垂直取"身体高 = 0.6×框高"的顶部一段（MAA 的框含脚下那格 ⇒ 不能直接用框高 ✗）
  徽标     = 亮黄 + **内部深色占比 ≥0.30**（实测徽标 0.49、旁边的黄色箭头只有 0.17~0.27 ✓）
  局部几何 = 徽标点 ↔ 可部署格心（初值给"格心上方半格"）→ Hungarian 一对一 → similarity 拟合
  验收     = **每帧**（每样本）在 70/30 留出集上的最大残差 < 0.5 格 ✓（不看平均 ✗）

用法： python tools/gate_a_check.py [最多几帧]
"""

from __future__ import annotations

import json
import pathlib
import sys

import cv2
import numpy as np
import onnxruntime as ort
from scipy.optimize import linear_sum_assignment

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import battle_loop as BL                                         # noqa: E402
import tilepos_audit as TA                                       # noqa: E402

ROOT = HERE.parent
DATA = ROOT / "data"
MODEL = ROOT / "services/maa/instances/1/resource/onnx/operators_det.onnx"
GAME_LOG = pathlib.Path(r"D:/Xiach/M-A/MAA/MAA-v6.12.2-win-x64/debug/gui.log")
N_MIN = 3


def detect_operators(im: np.ndarray, thr: float = 0.3, iou: float = 0.5) -> list:
    """按 MAA 源码复刻：resize 640×640 → RGB → /255 → CHW → 阈值 0.3 → **NMS** ✓"""
    h, w = im.shape[:2]
    rgb = cv2.cvtColor(cv2.resize(im, (640, 640), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    o = _SESS.run(None, {_SESS.get_inputs()[0].name:
                         (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]})[0][0]
    sx, sy = w / 640.0, h / 640.0
    bx, sc = [], []
    for i in np.where(o[4] >= thr)[0]:
        cx, cy, bw, bh = o[0, i] * sx, o[1, i] * sy, o[2, i] * sx, o[3, i] * sy
        if bw < 15 or bh < 15:
            continue
        bx.append([cx - bw / 2, cy - bh / 2, bw, bh])
        sc.append(float(o[4, i]))
    k = cv2.dnn.NMSBoxes(bx, sc, thr, iou)
    return [bx[i] for i in (k.flatten() if len(k) else [])]


_SESS = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])


def is_selected_state(im: np.ndarray) -> bool:
    """这一帧是不是**选人/暂停态**（镜头被拉近+旋转 ⇒ 属于另一套变换，不能混进几何标定 ✓）。

    判据复用 `annotate_live.layout_boxes` 的 `ops.*` 状态闸（干员详情面板在屏 ⇒ 有 ops.* 节点 ✓）——
    2026-09-19 用户看图指出："其中几张地图视野是偏离的" ✓，正是这个态 ✓。
    """
    import contextlib
    import io as _io
    import annotate_live as AL
    with contextlib.redirect_stdout(_io.StringIO()):
        ui, _tr, _w, _c = AL.layout_boxes(im)
    return bool(ui) and any("ops" in k for k in ui)


def hp_bars(im: np.ndarray) -> np.ndarray:
    """干员脚边的**蓝 HP 条** —— 2026-09-19 用户指出"只有血条显示得最精准" ✓，
    实测：与干员框 1:1、相邻间距≈官方格距（155~163 vs 147~153 px ⇒ 5~10%）✓
    判据复用 `annotate_live` 的"蓝 HP 条"：B−R>40 且 B>120 ✓ + 细长水平（宽≥35、高≤14、宽高比≥4）✓
    """
    B, Gc, R = (im[:, :, i].astype(int) for i in range(3))
    m = np.uint8((B - R > 40) & (B > 120) & (B > Gc + 10)) * 255
    m[:TA.VIEW[0]] = 0
    m[int(im.shape[0] * 0.82):] = 0
    m[:, :300] = 0
    n, lab, st, ce = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        a = int(st[i, 4])
        x, y, w, h = (int(v) for v in st[i, :4])
        if a < 60 or h > 14 or w < 35 or w / max(1, h) < 4:
            continue
        out.append((x + w / 2, y + h / 2))
    return np.array(out) if out else np.zeros((0, 2))


def yellow_badges(im: np.ndarray, dark_min: float = 0.30) -> np.ndarray:
    B, G, R = (im[:, :, i].astype(int) for i in range(3))
    y = np.uint8((R > 170) & (G > 130) & (B < 110) & ((R - B) > 80)) * 255
    y[:TA.VIEW[0]] = 0
    y[int(im.shape[0] * 0.82):] = 0
    n, lab, st, ce = cv2.connectedComponentsWithStats(y, 8)
    out = []
    for i in range(1, n):
        a = int(st[i, 4])
        x, yy, ww, hh = (int(v) for v in st[i, :4])
        if not (200 <= a <= 6000) or not (0.5 <= ww / max(1, hh) <= 2.0):
            continue
        reg = im[yy:yy + hh, x:x + ww].astype(int)
        d = float(((reg[:, :, 0] < 95) & (reg[:, :, 1] < 95) & (reg[:, :, 2] < 95)).mean())
        if d < dark_min:
            continue
        out.append((x + ww / 2, yy + hh / 2))
    return np.array(out) if out else np.zeros((0, 2))


def head_badges(im: np.ndarray) -> np.ndarray:
    """干员框 → 头顶锚区 → 落区内的徽标（外援判据：相对关系、不是"最近的黄" ✓）"""
    ops = detect_operators(im)
    cand = yellow_badges(im)
    got = []
    for (x, y, w, h) in ops:
        # ⚠️ 徽标挂在**框顶上方**（不在框内 ✗ —— 我第一版把锚区算进框里 ⇒ 0 命中 ✓）
        cx = x + w / 2
        for (bx, by) in cand:
            # 锚区按**实测分布**定（不是我拍的数 ✓）：横向 |dx|≤0.45W（实测最大 0.39）、纵向 0~1.15H（实测最大 1.10）
            # ⚠️ 关键安全性质：0.45W < 0.5×格距（实测框宽≈格距）⇒ 锚区**够不到邻格**的徽标 ✓ 不会配错人 ✓
            if abs(bx - cx) <= 0.45 * w and 0.0 <= (y - by) <= 1.15 * h:
                got.append((bx, by))
    return np.array(got) if got else np.zeros((0, 2))


def frames(limit: int):
    starts = [(l[12:19], l.split("开始战斗: ")[-1].strip()[:20])
              for l in GAME_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
              if len(l) > 21 and l[1:11] == "2026-09-19" and l[12:19] >= "20:42:00" and "开始战斗: " in l]

    def name_at(t):
        nm = None
        for st, n in starts:
            if st <= t:
                nm = n
        return nm
    rows = sorted((json.loads(l) for l in (DATA / "samples/rogue/index.jsonl").read_text(encoding="utf-8")
                   .splitlines() if l.strip()), key=lambda r: r["t"])
    # ⚠️ 不能取"每图第一帧" ✗ —— 战斗刚开始时**干员还没上场** ⇒ 干员框 0（我踩过 ✓）
    #    改为：每图在若干候选帧里挑**干员最多**的那帧 ✓
    per: dict = {}
    for r in rows:
        nm = name_at(r["t"][11:])
        p = pathlib.Path(r["file"])
        if not nm or not p.is_file():
            continue
        per.setdefault(nm, []).append(p)
    out = []
    for nm, ps in per.items():
        best = (0, None)
        for p in ps[:12]:                       # 每图最多试 12 帧 ✓
            im = cv2.imread(str(p))
            if im is None:
                continue
            if is_selected_state(im):     # 选人态镜头偏了 ⇒ 不进标定样本 ✓
                continue
            n = len(hp_bars(im))
            if n > best[0]:
                best = (n, p)
            if n >= 4:
                break
        if best[1] is not None:
            out.append((nm, best[1]))
        if len(out) >= limit:
            break
    return out


def sample_set(path: str = "data/gateA_samples.json", limit: int = 12) -> list:
    """**固定样本清单**：首次生成后只读 ✓。

    为什么必须固定：两个脚本各按"HP 条最多"挑选、还都早停 ⇒ 同一张图会落到不同帧上，
    于是同一张图出现两种结论（似曾相识 PASS 0.47 / FAIL 2.25 ✗）—— **逐样本验收必须配固定样本集** ✓。
    """
    f = DATA / "gateA_samples.json"
    if f.is_file():
        d = json.loads(f.read_text(encoding="utf-8"))
        return [(k, DATA.parent / v) if not pathlib.Path(v).is_absolute() else (k, pathlib.Path(v))
                for k, v in d.items()]
    picked = {}
    for nm, first in frames(limit * 3):
        if nm in picked:
            continue
        best = (-1, None)
        for p in [first]:
            im = cv2.imread(str(p))
            if im is None or is_selected_state(im):
                continue
            n = len(hp_bars(im))
            if n > best[0]:
                best = (n, p)
        if best[1] is not None and best[0] >= 3:
            picked[nm] = str(pathlib.Path(best[1]).as_posix())
    f.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已生成固定样本清单 %s（%d 张图）✓" % (f, len(picked)))
    return [(k, pathlib.Path(v)) for k, v in picked.items()]


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    print("逐样本验收（每帧一个样本；判据：**留出集最大残差 < 0.5 格**，不看平均 ✗）")
    res = []
    for nm, p in sample_set():
        im = cv2.imread(str(p))
        if im is None:
            continue
        try:
            L = BL.Loop(nm, None)
        except Exception:                                        # noqa: BLE001
            continue
        s = L.stage_px
        if is_selected_state(im):
            print("  %-8s 跳过：选人/暂停态（镜头偏移，属另一套变换 ✓）" % nm)
            continue
        if is_selected_state(im):
            print("  %-8s 跳过：选人/暂停态（镜头偏移，属另一套变换 ✓）" % nm)
            continue
        B = hp_bars(im)                    # 锚点 = HP 条（用户实测最准 ✓）
        if len(B) < N_MIN:
            print("  %-8s HP 条 %d（<%d ⇒ 该样本不可评）" % (nm, len(B), N_MIN))
            continue
        ops = detect_operators(im)         # **干员框播种** ✓
        tiles = [(c, rr) for rr in range(L.lv.height) for c in range(L.lv.width)
                 if (t2 := L.lv.at(c, rr)) and t2.buildable and L.lv.project(c, rr)]
        if not tiles:
            continue
        pts = [np.array(L.lv.project(c, rr)) * s for c, rr in tiles]
        q0 = np.array(L.lv.project(0, 0)) * s
        q1 = np.array(L.lv.project(1, 0)) * s if L.lv.project(1, 0) else q0
        pitch = float(np.hypot(*(q1 - q0))) or 130.0
        P = np.array([[q[0], q[1]] for q in pts])
        # 播种：每条 HP 条先认"自己的干员框"（1:1，门限 0.7×框宽 ✓）
        if len(ops) >= len(B):
            boxc = np.array([[x + w / 2, y + h / 2] for x, y, w, h in ops])
            Db = np.linalg.norm(boxc[:, None, :] - B[None, :, :], axis=2)
            Cb = np.where(Db <= 0.7 * float(np.median([w for _x, _y, w, _h in ops])), Db, 1e6)
            rb, cb = linear_sum_assignment(Cb)
            owner = {int(j): int(i) for i, j in zip(rb, cb) if Cb[i, j] < 1e6}
        else:
            owner = {}
        # 一对一：格 ↔ HP 条（门限 1.2 格；有框的优先靠近自己框的格 ✓）
        D = np.linalg.norm(P[:, None, :] - B[None, :, :], axis=2)
        C = np.where(D <= 1.2 * pitch, D, 1e6)
        if owner:
            for i, (x, y, w, h) in enumerate(ops):
                cen = np.array([x + w / 2, y + h / 2])
                for j in owner:
                    if owner[j] == i:
                        far = np.linalg.norm(P - cen, axis=1) > 1.4 * pitch
                        C[far, j] = 1e6            # 离"自己那条 HP 条所属框"太远的格直接排除 ✓
        ri, ci = linear_sum_assignment(C)
        pr = [(int(i), int(j), float(C[i, j])) for i, j in zip(ri, ci) if C[i, j] < 1e6]
        if len(pr) < N_MIN:
            print("  %-8s HP 条 %d 配 %d（太少）" % (nm, len(B), len(pr)))
            continue
        # 拓扑一致性（外援 §6）：近的 HP 条应配到近的格 ⇒ 比值不该出现数量级畸变 ✓
        topo_ok = tot2 = 0
        for a in range(len(pr)):
            for b in range(a + 1, len(pr)):
                ia, ja, _ = pr[a]; ib, jb, _ = pr[b]
                dP = float(np.linalg.norm(P[ia] - P[ib])); dQ = float(np.linalg.norm(B[ja] - B[jb]))
                if dP < 1.6 * pitch and dQ < 1.6 * pitch:
                    tot2 += 1
                    if dP / 3 <= dQ <= dP * 3:
                        topo_ok += 1
        topo = (topo_ok / tot2) if tot2 else float("nan")
        if not (topo >= 0.7):
            print("  %-6s 拓扑一致性 %.2f <0.7 ⇒ 本样本**不可评**（配错格风险）✗" % (nm, topo))
            continue
        rng = np.random.default_rng(7)
        idx = rng.permutation(len(pr))
        tr = idx[: max(1, int(len(idx) * 0.7))]
        te = idx[max(1, int(len(idx) * 0.7)):]
        A = np.array([P[i] for i, _j, _d in pr])
        Q = np.array([B[j] for _i, j, _d in pr])
        M, _ = cv2.estimateAffinePartial2D(A[tr], Q[tr], method=cv2.LMEDS)
        if M is None or not len(te):
            continue
        e = np.linalg.norm((M[:, :2] @ A[te].T).T + M[:, 2] - Q[te], axis=1)
        em, ex = float(np.median(e)) / pitch, float(e.max()) / pitch
        res.append((nm, len(B), len(pr), em, ex))
        print("  %-8s 徽标 %d 配 %2d ｜ 留出残差 中位 %.2f / 最大 %.2f 格 ⇒ %s"
              % (nm, len(B), len(pr), em, ex, "PASS" if ex < 0.5 else "FAIL"))
    print("")
    if res:
        bad = [z for z in res if z[4] >= 0.5]
        print("⇒ 逐样本：%d 个可评样本，%d 个达标，%d 个不达标 ⇒ **%s**"
              % (len(res), len(res) - len(bad), len(bad),
                 "Gate A PASS ✓" if not bad else "Gate A UNDECIDED ✗（有一个样本 ≥0.5 格就不算过 ✓）"))
    else:
        print("⇒ 没有可评样本 ⇒ Gate A 保持 UNDECIDED ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
