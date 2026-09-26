"""战斗画面标注器（贴合版）—— 所有框都是**量出来的紧贴外框**，不是估的。

为什么单开一个工具：`data/annotated_*.png` 是给用户看"我现在认出了什么"的**验收物**，
之前靠临时 python -c 拼，框的位置全凭印象，被用户连点两次（"UI这边并不精细"/"要贴合"）。
所以把量测固化下来：换一帧重跑一次，框必然跟着量出来的值走，不再有"画在哪儿"的自由。

四层标注
  ① UI 元素（橙 #1..#8）：齿轮 / 敌人数 / 我方生命 / 倍速 / 暂停 / 费用 / 费用条 / 部署数
  ② 手牌（蓝 #9..）：每张卡的真实边界 = 职业图标 x−29 … +146（间距 174.6，2x 目视校准过）
  ③ 地图格（绿=地面可放 / 黄=高台 / 灰=不可放）：投影来自关卡文件，**不是从像素猜的**
  ④ 右侧图例栏：编号 → 名字 → 实测框 → 读数

用法
  python tools/annotate_live.py                          # 默认 data/_live_now.png
  python tools/annotate_live.py --img data/x.png --out data/y.png
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import battle_loop as BL  # noqa: E402

# ── 颜色（BGR）──
C_UI = (0, 165, 255)      # 橙：UI 元素
C_CARD = (255, 130, 0)    # 蓝：手牌
C_GND = (0, 220, 0)       # 绿：地面可放
C_HIGH = (0, 230, 255)    # 黄：高台可放
C_BLK = (120, 120, 120)   # 灰：不可放
C_FO = (255, 220, 0)      # 青：战场中干员 UI
PANEL = 640               # 右侧图例栏宽度


def _bbox(img: np.ndarray, roi, pred):
    """在 roi 内按 pred 取紧贴外框；取不到返回 None（**不估**）。"""
    x, y, w, h = roi
    sub = img[y:y + h, x:x + w]
    m = pred(sub)
    ys, xs = np.where(m)
    if len(xs) == 0:
        return None
    return (x + int(xs.min()), y + int(ys.min()), x + int(xs.max()), y + int(ys.max()))


WHITE = lambda s, t=150: s.max(2) > t                                  # noqa: E731
ORANGE = lambda s: (s[:, :, 2] > 170) & (s[:, :, 1] > 90) & (s[:, :, 2] - s[:, :, 0] > 50)   # noqa: E731
IVORY = lambda s: (s.max(2) > 170) | ((s[:, :, 2] - s[:, :, 0] > 35) & (s[:, :, 2] > 170) & (s[:, :, 1] > 70))  # noqa: E731


def measure_ui(a: np.ndarray) -> dict:
    """逐元素量测。ROI 的边界是按放大截图目视定的（写死在这里＝量测的可复现部分）。

    ⚠️ 每个 ROI 都**故意留了余量**：ROI 太紧会像上次那样把框切掉一半（1X 只量到 5px 宽）。
    红色「演習中」条会污染「敌人数/生命值」两个框（它是红的，落进橙/粉判据里），
    所以这两个 ROI 的 y 上限卡在 74 —— 条子的上沿在 ~75。
    """
    return {
        "1 齿轮(设置入口)": _bbox(a, (70, 38, 110, 90), lambda s: s.max(2) > 110),
        "2 敌人数 x/y": _bbox(a, (718, 10, 225, 64), ORANGE),
        "3 我方生命值": _bbox(a, (985, 10, 210, 64), IVORY),   # x0 必须 <1017：卡在 1017 会把图标左沿切掉
        "4 倍速开关(1X)": _bbox(a, (1595, 30, 100, 90), WHITE),
        "5 暂停/继续": _bbox(a, (1770, 44, 84, 66), WHITE),
        "6 费用(菱形C+数字)": _bbox(a, (1735, 740, 160, 62), WHITE),
        "7 费用回复条": _bbox(a, (1730, 803, 150, 22), WHITE),
        "8 还可部署数": _bbox(a, (1608, 818, 285, 46), WHITE),
    }


CARD_PITCH = 174.6        # 相邻职业图标间距（实测 174/176 交替）
CARD_L, CARD_R = -29, 146  # 卡边界相对职业图标的偏移（2x 放大目视校准）
CARD_TOP, CARD_BOT = 886, 1077


def measure_cards(a: np.ndarray, L: BL.Loop, tr=None) -> list[dict]:
    """手牌几何来自**行结构**（表里的右锚 + 步距），图标只用来**数张数/给职业** ✓。

    外援意见 + 我们的实测都指向同一点：**图标是内容、不是卡片几何** ✗ ——
    所以图标只当锚点/计数用；卡的边界由"右对齐 + 固定步距"推（这条规律我们验过 11/11 ✓）。
    纯几何分卡（竖缝投影）在本 UI 上不可行（卡紧挨着、缝对比度极低 ✗，试过两版都失败）。
    """
    deck = L.read_deck(a)
    if tr is not None and deck:
        import ui_layout as UL                                   # noqa: PLC0415
        sc = a.shape[1] / UL.LW
        # **动态手牌**：右锚 + 按时数取间距（实测反推的表 ✓）；卡宽≠间距时按间距近似 ✓
        _b, _meta = UL.hand_boxes_dynamic(len(deck), tr)
        print(f"  手牌：N={_meta['n']}  间距={_meta['pitch_1080']:.1f}(1080p)  "
              f"{'压缩档' if _meta['compressed'] else '常规档'}")
        boxes = [(int(round(b[0] * sc)), int(round(b[1] * sc)),
                  int(round(b[2] * sc)), int(round(b[3] * sc))) for b in _b]
        return [{"n": 9 + i, "idx": i, "role": deck[i - 1]["role"],
                 "icon_x": deck[i - 1]["x1080"], "box": boxes[i - 1]}
                for i in range(1, len(deck) + 1)]
    out = []
    for i, c in enumerate(deck, 1):
        x = c["x1080"]
        out.append({"n": 9 + i, "idx": i, "role": c["role"], "icon_x": x,
                    "box": (x + CARD_L, CARD_TOP, x + CARD_R, CARD_BOT)})
    return out


def measure_grid(a: np.ndarray, L: BL.Loop) -> list[dict]:
    """地图格：把投影当**仿射基**用，画出每格真实的平行四边形四角。

    PA-1 实测：相邻列投影差 (+84,+22)、相邻行 (−13,+61)（1280 系）
    ⇒ 格子是斜的（45° 俯视），**不能用轴对齐矩形** —— 上一版拿 "列x差 × 行y差"
    拼出 126×91 的方框，框住 2×2 格还错位。四角 = 中心 ±(基向量1 ± 基向量2)/2。
    """
    cells = []
    if L.lv.width < 2 or L.lv.height < 2:
        return cells
    q0 = L.lv.project(0, 0)
    bcol = np.array(L.lv.project(1, 0)) - np.array(q0)
    brow = np.array(L.lv.project(0, 1)) - np.array(q0)
    if np.allclose(bcol, 0) or np.allclose(brow, 0):
        return cells
    s = L.stage_px
    for r in range(L.lv.height):
        for c in range(L.lv.width):
            t = L.lv.at(c, r)
            if not t:
                continue
            q = L.lv.project(c, r)
            if not q:
                continue
            cen = (int(q[0] * s), int(q[1] * s))
            if t.buildable == 1 or getattr(t, "ground_deployable", False):
                kind = "gnd"
            elif t.buildable == 2 or getattr(t, "high_deployable", False):
                kind = "high"
            else:
                kind = "blk"
            cells.append({"cell": (int(c), int(r)), "kind": kind, "center": cen})
    return cells


def _tag(canvas, box, n, color):
    """编号牌贴在框**外面**（框内保持干净）。

    放法按框形挑：顶部放得下就放上面；框太矮（费用条那种 8px 高）放上面会压住上面的元素
    ⇒ 挪到右边（右边也没地方才放左边）；顶部贴边的挪到框下。
    """
    x0, y0, x1, y1 = box
    tw, th = 46, 30
    W = canvas.shape[1] - PANEL
    if y1 - y0 < 24:
        if x1 + tw + 4 < W:
            p0, p1 = (x1 + 4, y0 - 4), (x1 + 4 + tw, y0 - 4 + th)
        else:
            p0, p1 = (max(0, x0 - tw - 4), y0 - 4), (x0 - 4, y0 - 4 + th)
    elif y0 >= th + 4:
        p0, p1 = (x0, y0 - th - 3), (x0 + tw, y0 - 3)
    else:
        p0, p1 = (x0, y1 + 3), (x0 + tw, y1 + 3 + th)
    cv2.rectangle(canvas, p0, p1, color, -1)
    cv2.putText(canvas, str(n), (p0[0] + 8, p1[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 0, 0), 2)


LAYERS = ("ui", "cards", "grid", "ops", "dyn")   # dyn = 动态目标（我方干员 + 算出来的敌人）


def render(a: np.ndarray, ui: dict, cards: list[dict], grid: list[dict],
           fo: dict | None = None, layers=LAYERS, panel: bool = True,
           debug_containers: bool = False, dyn: dict | None = None) -> np.ndarray:
    """layers: 选画哪几层（ui=战斗 HUD / cards=手牌 / grid=地图格 / ops=战场干员 UI）。
    panel=False 时不画右侧图例栏（网页版把数据放 HTML 表里，图上留干净）。"""
    H, W = a.shape[:2]
    if not panel:
        canvas = a.copy()
    else:
        canvas = np.zeros((H, W + PANEL, 3), np.uint8)
        canvas[:, :W] = a
        canvas[:, W:] = (28, 28, 32)

    # 地图：**竖线横线 + 每格中心点**（用户认可的那版标法，2026-09-19 恢复；
    # 我先改成平行四边形，被点名"地图的标法怎么变了 之前那个就可以了" ⇒ 别动它）
    if "grid" in layers:
        CEN = {(g["cell"][0], g["cell"][1]): g["center"] for g in grid}
        for g in grid:
            c, r = g["cell"]
            for nb in ((c + 1, r), (c, r + 1)):
                if nb in CEN:
                    cv2.line(canvas, g["center"], CEN[nb], (165, 165, 165), 1, cv2.LINE_AA)
        for g in grid:
            col = {"gnd": C_GND, "high": C_HIGH, "blk": (235, 235, 235)}[g["kind"]]
            cv2.circle(canvas, g["center"], 4 if g["kind"] != "blk" else 2, col, -1, cv2.LINE_AA)

    fo = fo or {}
    if "dyn" in layers and dyn:                       # ④ 动态目标：我方=看出来的 / 敌人=算出来的（标 calc ✓）
        _lv = dyn.get("_level")
        for cell in dyn.get("ours", []):
            _q = _lv.project(*cell) if _lv else None
            if _q:
                _x, _y = int(_q[0] * 1.5), int(_q[1] * 1.5)
                cv2.circle(canvas, (_x, _y), 18, (0, 220, 0), 2)
                cv2.putText(canvas, "ours", (_x - 18, _y - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (0, 220, 0), 1, cv2.LINE_AA)
        for _e in dyn.get("enemies", []):
            _q = _lv.project(*_e["cell"]) if _lv else None
            if _q:
                _x, _y = int(_q[0] * 1.5), int(_q[1] * 1.5)
                cv2.circle(canvas, (_x, _y), 20, (0, 0, 255), 2)
                cv2.putText(canvas, f"{_e['name'][:4]}(calc)", (_x - 30, _y - 26),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 255), 1, cv2.LINE_AA)
    for it in (cards if "cards" in layers else []):   # ② 手牌
        cv2.rectangle(canvas, it["box"][:2], it["box"][2:], C_CARD, 3)
        _tag(canvas, it["box"], it["n"], C_CARD)

    for i, (name, box) in enumerate((ui.items() if "ui" in layers else []), 1):   # ① UI
        if not box:
            continue
        cv2.rectangle(canvas, box[:2], box[2:], C_UI, 3)
        _tag(canvas, box, i, C_UI)
    for j, (name, box) in enumerate((fo.items() if "ops" in layers else []), 1):  # ④ 战场干员 UI（青）
        if not box:
            continue
        cv2.rectangle(canvas, box[:2], box[2:], C_FO, 3)
        _tag(canvas, box, f"F{j}", C_FO)

    if not panel:
        return canvas
    ly = 40
    def line(txt, color=(235, 235, 235), sc=0.58, dx=16):
        nonlocal ly
        cv2.putText(canvas, txt, (W + dx, ly), cv2.FONT_HERSHEY_SIMPLEX, sc, color, 1, cv2.LINE_AA)
        ly += int(34 * sc) + 8

    line("ANNOTATION v3 — UI element boxes are tight visual-element bboxes;", (255, 255, 255), 0.6)
    line("              layout containers (hand_row) are non-visual (debug only)", (200, 200, 200), 0.52)
    ly += 8
    line("UI ELEMENTS", C_UI, 0.62)
    for i, (name, box) in enumerate(ui.items(), 1):
        line(f"{i}. {name}", (200, 220, 255))
        line(f"     box = {box}", (140, 160, 200), 0.52)
    ly += 6
    line("HAND CARDS  (box = role-icon x-29 … x+146, pitch 174.6)", C_CARD, 0.6)
    for it in cards:
        line(f"card[{it['idx'] - 1}] bbox={it['box']}  icon={{x:{it['icon_x']}}}",
             (255, 205, 180), 0.52)
    line("costs (eyeballed off the badges, OCR reads only 1/11):", (170, 190, 210), 0.5)
    line("     7  9  11  13  15  16  17  18  20  21  25", (255, 205, 180), 0.52)
    if fo:
        ly += 6
        line("ON-FIELD OPERATOR UI  (operator selected on the battlefield)", C_FO, 0.6)
        for j, (name, box) in enumerate(fo.items(), 1):
            line(f"F{j}. {name}", (255, 225, 190))
            line(f"     box = {box}", (200, 170, 140), 0.52)
        line("(range diamond left unmeasured: thin line, same brightness as PAUSE text)", (170, 150, 130), 0.48)
    ly += 6
    line("MAP  (from level file, NOT from pixels)", (180, 255, 180), 0.6)
    n_g = sum(1 for g in grid if g["kind"] == "gnd")
    n_h = sum(1 for g in grid if g["kind"] == "high")
    n_b = sum(1 for g in grid if g["kind"] == "blk")
    line("lattice lines + one dot per cell centre  (you approved this style)", (200, 200, 200), 0.5)
    line(f"green dot  = ground-deployable (buildable=1)  x{n_g}", (180, 255, 180), 0.54)
    line(f"cyan dot   = high-ground-deployable (buildable=2; enemies do NOT walk here)  x{n_h}",
         (140, 230, 255), 0.54)
    line(f"white dot  = cannot deploy  x{n_b}", (200, 200, 200), 0.54)
    if grid:
        CEN2 = {(g["cell"][0], g["cell"][1]): g["center"] for g in grid}
        q0, q1, q2 = CEN2.get((0, 0)), CEN2.get((1, 0)), CEN2.get((0, 1))
        if q0 and q1 and q2:
            dcol = float(np.hypot(q1[0] - q0[0], q1[1] - q0[1]))
            drow = float(np.hypot(q2[0] - q0[0], q2[1] - q0[1]))
            line(f"cell pitch: col {dcol:.0f} px, row {drow:.0f} px"
                 f" (projection is sheared ~45-deg view)", (150, 200, 150), 0.5)
    return canvas


def estimate_h(a: np.ndarray, b: np.ndarray, roi=(130, 820, 300, 1780)) -> tuple[np.ndarray | None, str]:
    """**唯一一份**视角配准核心：求 (a → b) 的单应 + 说明。a/b 传灰度图。

    为什么单独抽出来：我先在 `annotate_view` 里另写了一份"实时版"，结果同一对图
    两份实现给出**不同**的 H（静态那份是对的、实时那份偏 45px ✗）—— 教训是**别让两条路径各写一遍**。
    """
    y0, y1, x0, x1 = roi
    orb = cv2.ORB_create(8000, scaleFactor=1.15, nlevels=10, fastThreshold=6)
    k1, d1 = orb.detectAndCompute(a[y0:y1, x0:x1], None)
    k2, d2 = orb.detectAndCompute(b[y0:y1, x0:x1], None)
    if d1 is None or d2 is None:
        return None, "配准：有一帧找不到特征点"
    mt = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [m for m, n in mt if m.distance < 0.72 * n.distance]
    if len(good) < 24:
        return None, f"配准：好匹配只有 {len(good)} 对"
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    H, mask = cv2.findHomography(p1, p2, cv2.RANSAC, 2.5)
    inl = int(mask.sum()) if mask is not None else 0
    if H is None or inl < 20:
        return None, f"配准：内点只有 {inl}"
    J = H[:2, :2] - np.outer(H[2, :2], H[:2, 2]) / H[2, 2]
    sc = float(np.sqrt(abs(np.linalg.det(J))))
    rot = float(np.degrees(np.arctan2(J[1, 0], J[0, 0])))
    return H, (f"(a→b) 缩放 ×{sc:.3f} 旋转 {rot:+.2f}° 平移 ({H[0, 2]:+.0f},{H[1, 2]:+.0f})px "
               f"内点 {inl}/{len(good)}")


def estimate_view(img_path: str, ref_path: str, roi=(130, 820, 300, 1780)) -> np.ndarray | None:
    """从文件求 (本帧 → 参照帧) 的单应（同一局的普通视角帧当参照才准）。"""
    a = cv2.imdecode(np.fromfile(img_path, np.uint8), cv2.IMREAD_GRAYSCALE)
    b = cv2.imdecode(np.fromfile(ref_path, np.uint8), cv2.IMREAD_GRAYSCALE)
    if a is None or b is None:
        return None
    H, info = estimate_h(a, b, roi)
    print("  视角配准:", info)
    return H


def transform_cells(grid: list[dict], Hinv: np.ndarray) -> list[dict]:
    """把格心按 Hinv（参照视角 → 本帧视角）搬过来，点/线随之重画。"""
    out = []
    for g in grid:
        p = Hinv @ np.array([g["center"][0], g["center"][1], 1.0])
        out.append({**g, "center": (int(p[0] / p[2]), int(p[1] / p[2]))})
    return out


def _plate(img: np.ndarray, roi, lo: int = 26, hi: int = 120):
    """找 roi 里"比周围亮一截"的浅色方牌（撤退 ✕ 那种：深色符号 + 浅色底）。
    自适应阈值 + 取最大连通块，尺寸不在 [lo,hi] 就返回 None（宁可不标）。"""
    x, y, w, h = roi
    sub = img[y:y + h, x:x + w]
    g = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    th = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 41, -8)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _lab, st, _ce = cv2.connectedComponentsWithStats(th, 8)
    best = None
    for bx, by, bw, bh, ar in st[1:]:
        if lo <= bw <= hi and lo <= bh <= hi and ar > 400:
            if best is None or ar > best[4]:
                best = (bx, by, bw, bh, ar)
    if best is None:
        return None
    return (x + int(best[0]), y + int(best[1]), x + int(best[0] + best[2]), y + int(best[1] + best[3]))


def digit_roi(img: np.ndarray, box, thr: int = 150) -> tuple | None:
    """在一个已知框里**自动找出数字区**（把图标和文字标签排除掉）。

    为什么要它：数字必须喂**字符模型**（PaddleCharOCR），而字符模型只认识 ASCII
    ⇒ 混进汉字/图标就读出 `StJICGpaE*X:7` 这种垃圾（2026-09-19 实测 ✗）。
    MAA 是靠任务里写死的纯数字 ROI 解决的；我们没有那份表，就按**字形特征**挑：
    数字是"高 ~28~60px、宽 ~14~60px 的亮块"，把满足条件的相邻块并起来即成。
    """
    x0, y0, x1, y1 = box
    sub = img[y0:y1, x0:x1]
    if sub.size == 0:
        return None
    m = np.uint8(sub.max(2) > thr) * 255
    n, _lab, st, _ce = cv2.connectedComponentsWithStats(m, 8)
    keep = []
    for bx, by, bw, bh, ar in st[1:]:
        if 5 <= bw <= 70 and 18 <= bh <= 70 and ar > 60:       # 数字形状（宽度放到 5：不然窄的"1"被吃掉 ✗）
            keep.append((int(bx), int(by), int(bw), int(bh)))
    if not keep:
        return None
    mx0 = min(k[0] for k in keep); my0 = min(k[1] for k in keep)
    mx1 = max(k[0] + k[2] for k in keep); my1 = max(k[1] + k[3] for k in keep)
    pad = 3
    return (x0 + max(0, mx0 - pad), y0 + max(0, my0 - pad),
            x0 + min(sub.shape[1], mx1 + pad), y0 + min(sub.shape[0], my1 + pad))


_NUM_RE = re.compile(r"\d+")
_RATIO_RE = re.compile(r"\d+\s*/\s*\d+")


def read_number(img: np.ndarray, box, kind: str = "int", loop=None, upscale: float = 3.0):
    """读一个数字字段：**字符模型 + 抽令牌**（不靠把 ROI 裁得多干净）。

    为什么要"抽令牌"：字符模型只认 ASCII，框里混着图标/汉字时输出像 `c29`、`tUICGPAE*X7`、
    `H3` 这种（2026-09-19 实测）—— 但**数字本身往往是准的** ✓ ⇒ 从输出里挑出
    `数字/数字`（kind='ratio'，如敌人数、HP、充能）或**最后一段数字**（kind='int'，如费用、可部署数）即可。
    """
    if loop is None:
        return None
    x0, y0, x1, y1 = box
    h = loop.p.ocr.read_region(img, (x0, y0, x1 - x0, y1 - y0),
                               upscale=upscale, min_score=0.05, char=True)
    lab = (h.label or "") if h else ""
    if kind == "ratio":
        m = _RATIO_RE.search(lab.replace(" ", ""))
        if not m:
            return None
        a, b = m.group(0).split("/")
        return (int(a), int(b))
    hits = _NUM_RE.findall(lab.replace(" ", ""))
    if not hits:
        return None
    try:
        return int(hits[-1])
    except ValueError:
        return None


# 表里的短键 → 界面/读数沿用的老键（下游不用改）
ALIAS = {"hud.settings": "1 齿轮(设置入口)", "hud.enemy_count": "2 敌人数 x/y",
         "hud.life": "3 我方生命值", "hud.speed": "4 倍速开关(1X)",
         "hud.pause": "5 暂停/继续", "hud.cost": "6 费用(菱形C+数字)",
         "hud.cost_bar": "7 费用回复条", "hud.deploy_count": "8 还可部署数",
         "hand.row": "9 hand_row(手牌行·父坐标系)", "ops.panel": "10 ops_panel",
         "ops.skill": "11 ops_skill", "ops.charge": "12 ops_charge",
         "ops.charge_text": "13 ops_charge_text", "ops.retreat": "14 ops_retreat",
         # 兼容短键
         "gear": "1 齿轮(设置入口)", "enemy": "2 敌人数 x/y", "lives": "3 我方生命值",
         "speed": "4 倍速开关(1X)", "pause": "5 暂停/继续", "cost": "6 费用(菱形C+数字)",
         "cost_bar": "7 费用回复条", "deploy_text": "8 还可部署数"}


def layout_boxes(a: np.ndarray):
    """**Layer 1+2+3**：静态表 + 锚点 + 仿射 ⇒ 当前帧的 UI 框（1080p 坐标）。

    返回 (boxes, tr, why)。**解不出仿射就返回 (None, None, why)** —— fail-closed：
    UI 元素宁可一个都不画，也不画一堆错位的框（用户 2026-09-19："框都没框在完整 UI 上" ✗）。
    """
    import ui_layout as UL                                   # noqa: PLC0415
    F = cv2.resize(a, (UL.LW, UL.LH), interpolation=cv2.INTER_AREA)
    anc = UL.find_anchors(F)
    tr = UL.fit_affine(anc)
    if tr is None:
        return None, None, f"锚点不足/解不出仿射（找到 {sorted(anc)}）⇒ 不画 UI 框", {}
    # **两层**（外援 §2/§3）：容器框给人看、内容框喂 OCR；tight bbox ≠ UI element bbox
    nodes = UL.nodes_in_frame(tr)
    sc = a.shape[1] / UL.LW
    def _m(b):
        return (int(round(b[0] * sc)), int(round(b[1] * sc)), int(round(b[2] * sc)), int(round(b[3] * sc)))
    out = {ALIAS.get(k, k): _m(v["container"]) for k, v in nodes.items()}
    # ②' 状态闸：`ops.*` 那一组**只在干员详情面板真的在屏幕上时**才画（fail-closed ✓）
    #     实测踩到（2026-09-19）：普通战斗里没有面板，那 13 个框会照画到地图上 ✗
    if out:
        # 状态闸：`ops.*` 只在**干员详情面板真的在屏上**时画。判据用**专属性强的信号** ——
        # 面板里必有一条**蓝色 HP 条**（B−R>40 & B>120）。早先用"亮像素占比"被亮地图蒙混过关 ✗
        # （2026-09-19 实测：普通战斗的亮灰地图让那 13 个框照画到地图上）。
        _hp = out.get("18 ops.hp") or out.get("10 ops_panel")
        _hit = 0.0
        if _hp:
            _hx0, _hy0, _hx1, _hy1 = _hp
            _reg = a[max(0, _hy0):_hy1, max(0, _hx0):_hx1].astype(int)
            if _reg.size:
                _blue = (_reg[:, :, 0] - _reg[:, :, 2] > 40) & (_reg[:, :, 0] > 120)
                _hit = float(_blue.mean())
        if _hit < 0.02:                            # 找不到蓝 HP 条 ⇒ 面板不在
            _drop = [k for k in list(out) if "ops" in k.split(" ")[-1].replace(".", "_")]
            for _k in _drop:
                out.pop(_k, None)
            print(f"  状态闸：干员面板不在屏上（蓝 HP 条占比 {_hit:.1%}）⇒ 撤掉 {len(_drop)} 个 ops.* 框")

    out_content = {ALIAS.get(k, k): _m(v["content"]) for k, v in nodes.items()}
    return out, tr, "", out_content


def measure_fieldops(a: np.ndarray) -> dict:
    """**战场中干员被选中时**的 UI（用户 2026-09-19 点名要看的这组）。

    分两处：① 左侧详情面板（名字/LV/数值/HP/技能页签/技能块）；
    ② 地图上（攻击范围菱形里的技能按钮 + 充能计数 + 绿条，菱形角上的撤退 ✕）。
    ⚠️ 菱形那圈**范围线**没量（它是细线、和 "PAUSE" 大字同样亮，阈值分不开 ✗）——
    要它得从干员数据里的 range 算，别拿像素猜（宁可留空）。
    """
    BLUE = lambda s: (s[:, :, 0] - s[:, :, 2] > 40) & (s[:, :, 0] > 120)      # noqa: E731
    GREEN = lambda s: (s[:, :, 1] - s[:, :, 2] > 30) & (s[:, :, 1] - s[:, :, 0] > 30)  # noqa: E731
    return {
        "A 干员名": _bbox(a, (0, 225, 300, 62), WHITE),
        "B LV/ATK/DEF/BLK": _bbox(a, (0, 250, 260, 150), WHITE),
        "C 攻撃範囲 标签": _bbox(a, (130, 335, 180, 44), WHITE),
        "D HP 数字+血条": _bbox(a, (80, 350, 460, 66), lambda s: WHITE(s) | BLUE(s)),
        "E 技能/特性/素質 页签": _bbox(a, (0, 420, 340, 46), WHITE),
        "F 技能图标": _bbox(a, (10, 455, 105, 110), WHITE),
        "G 技能名+标签行": _bbox(a, (100, 450, 430, 62), WHITE),
        "H 技能说明": _bbox(a, (100, 505, 430, 120), WHITE),
        "I 战场:技能按钮": _bbox(a, (845, 380, 150, 115), WHITE),
        "J 战场:充能绿条": _bbox(a, (880, 460, 150, 50), GREEN),
        "K 战场:充能计数 x/y": _bbox(a, (860, 495, 160, 52), WHITE),
        # 撤退 ✕ 是"深色符号画在浅色方牌上" ⇒ 固定阈值抓不到（踩过两次 ROI/阈值坑）
        # ⇒ 用**自适应阈值找浅色方牌**（比周围亮一截的色块），尺寸不合理就如实留 None
        "L 战场:撤退 ✕": _plate(a, (830, 215, 160, 140)),
    }


def read_values(a: np.ndarray, L, ui: dict, cards: list[dict], upscale: float = 2.0) -> dict:
    """把"框"变成"值"—— 识别结果（标注要看的正是这个）。"""
    g = lambda k: read_number(a, ui[k], "int", loop=L, upscale=upscale) if ui.get(k) else None  # noqa: E731
    out = {
        "敌人数": read_number(a, ui["2 敌人数 x/y"], "ratio", loop=L, upscale=upscale) if ui.get("2 敌人数 x/y") else None,
        "我方生命": g("3 我方生命值"),
        "费用": g("6 费用(菱形C+数字)"),
        "可部署": g("8 还可部署数"),
        "手牌费用": [read_number(a, (c["box"][0] + 4, 884, c["box"][0] + 150, 946), "int",
                              loop=L, upscale=upscale) for c in cards],
    }
    return out



# ── 关卡自动识别（UI 布局跨关通用 ✓，但**格网/敌人是按某一关算的** ✗ ⇒ 必须先认出是哪关）──
def stage_registry(pattern: str = "data/grid_ref_*.npy") -> list[str]:
    """已注册指纹的关卡名（注册 = 在**该关的确认帧**上存一份指纹，见 --register-stage）。"""
    return sorted(pathlib.Path(f).name[len("grid_ref_"):-4]
                  for f in pathlib.Path(".").glob(pattern))


def detect_stage(a: np.ndarray, stages: list[str] | None = None):
    """拿 a 帧去比所有已注册关卡的指纹，返回 (关卡名, 误差, 全部候选)。

    **认不出就不认**（fail-closed ✓）：误差都超过 tol ⇒ 返回 (None, best_err, 候选)，
    调用方应当**不画**格网/敌人（画别关的格网=整片错 ✗，正是用户 2026-09-19 点出的问题）。
    """
    stages = stages or stage_registry()
    best, all_d = None, {}
    for st in stages:
        ref = pathlib.Path(f"data/grid_ref_{st}.npy")
        try:
            L = BL.Loop(st, None)
            d = float(np.abs(L.map_fingerprint(a) - np.load(ref)).mean())
        except Exception:                                        # noqa: BLE001
            continue
        all_d[st] = d
        if best is None or d < best[1]:
            best = (st, d)
    if best is None:
        return None, None, {}
    tol = BL.Loop(best[0], None).fp_tol
    others = sorted(v for k, v in all_d.items() if k != best[0])
    margin_ok = (not others) or (best[1] * 1.8 < others[0])   # 必须**明显**好于第二名，否则不认 ✓
    return (best[0] if (best[1] <= tol and margin_ok) else None), best[1], all_d


def stage_trusted(stage: str, registry: str = "data/tilepos_audit.json"):
    """三态闸（委托给 `tilepos_audit.trusted` —— **单一真相点** ✓，battle_loop 也用它 ✓）。"""
    import tilepos_audit as _TA                                  # noqa: PLC0415
    return _TA.trusted(stage, registry)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="data/_live_now.png")
    ap.add_argument("--out", default="data/annotated_tight.png")
    ap.add_argument("--debug-containers", action="store_true",
                    help="把 layout_container（hand_row 等**非视觉**的逻辑父）也画出来（默认不画 ✓）")
    ap.add_argument("--stage", default="act51side_01")
    ap.add_argument("--json", default="data/annotation_live.json")
    ap.add_argument("--save-grid-ref", default="",
                    help="把当前帧的格网指纹存为本关参照（在确认过的画面上存一次）")
    ap.add_argument("--grid-ref", default="data/grid_ref_%s.npy", help="格网参照文件（%%s=关卡名）")
    ap.add_argument("--layers", default=",".join(LAYERS), help="要画哪几层（ui,cards,grid,ops,dyn）")
    ap.add_argument("--panel", action="store_true", default=True, help="图上带右侧图例栏")
    ap.add_argument("--register-stage", default="",
                    help="把当前帧注册为**该关**的参照（在其确认帧上跑一次）：--register-stage act51side_05")
    ap.add_argument("--spent", type=float, default=0.0,
                    help="本局已经花掉的费用（手动/别处放过的干员）—— 费用钟 T = 费用 − initialCost + spent")
    ap.add_argument("--ref", default="",
                    help="参照帧（同一局的**普通视角**截图）。给了就把格心按实测单应搬到本帧视角 —— "
                         "选牌后镜头会拉近(≈1.30×)，不搬就是偏的")
    args = ap.parse_args()
    args.layers = tuple(x for x in args.layers.split(",") if x in LAYERS) or LAYERS

    a = cv2.imdecode(np.fromfile(args.img, np.uint8), cv2.IMREAD_COLOR)
    L = BL.Loop(args.stage, None, spent=getattr(args, "spent", 0.0))
    # 关卡身份闸（fail-closed）：格网是按**某一关**算的，画到别的关上整片都是错的 ✗
    fp = L.map_fingerprint(a)
    if args.save_grid_ref:
        np.save(args.save_grid_ref, fp)
        print(f"已存格网参照 {args.save_grid_ref}（{len(fp)} 格）")
    ref = pathlib.Path(args.grid_ref % args.stage)
    grid_ok, grid_why = True, ""
    if ref.is_file():
        d = float(np.abs(fp - np.load(ref)).mean())
        if d > L.fp_tol:
            grid_ok, grid_why = False, f"画面指纹与本关参照差 {d:.0f}（> {L.fp_tol}）⇒ 这帧不是 {args.stage} ⇒ 不画格网"
    else:
        grid_why = f"没有 {ref} 参照 ⇒ 格网未校验"
    # UI 框：**走静态表 + 锚点**（而不是每帧在手标 ROI 里找内容剪影 ✗）
    ui, _tr, ui_why, ui_content = layout_boxes(a)
    # **关卡自动识别**：UI 布局跨关通用 ✓，但格网/敌人是按某一关算的 ⇒ 先认关，认不出就不画 ✓
    if args.register_stage:
        np.save(f"data/grid_ref_{args.register_stage}.npy", L.map_fingerprint(a))
        print(f"✓ 已注册 {args.register_stage} 的格网参照（{len(L.map_fingerprint(a))} 格）")
        return
    _st, _err, _cands = detect_stage(a)
    if _st and _st != args.stage:
        print(f"  关卡识别：这帧是 **{_st}**（误差 {_err:.0f}，注册表 {list(_cands)}）⇒ 改用该关数据")
        args.stage = _st
        L = BL.Loop(_st, None, spent=getattr(args, "spent", 0.0))
    _grid_ok, _grid_why = (stage_trusted(args.stage) if _st else (False, "关卡未识别 ⇒ 不画格网"))
    _grid_why = (f"关卡识别不了（最接近 {min(_cands, key=_cands.get) if _cands else '-'} 误差 "
                 f"{_err:.0f}，注册表 {list(_cands) or '空'}）⇒ 不画格网/敌人 ✓" if not _st else "")
    if ui is None:
        print("  ⚠️", ui_why)
        ui = {}
    else:
        print(f"  Layer1+2+3 定位成功（仿射 缩放 {_tr[0]:.3f}/{_tr[1]:.3f} 平移 {_tr[2]:+.1f}）")
    cards = measure_cards(a, L, _tr)
    # ③ 边缘精修：把 layout 推出来的理论框**各自贴到最近的强边缘**（局部微调，不重检测 ✓）
    if ui:
        import ui_layout as _UL                                       # noqa: PLC0415
        _gray = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        _d = {}
        for _k, _b in list(ui.items()):
            _nb, _dd = _UL.refine_box(_gray, _b)
            ui[_k], _d[_k] = _nb, _dd
        _moved = sum(1 for v in _d.values() if any(abs(x) > 0 for x in v))
        print(f"  边缘精修：{_moved}/{len(_d)} 个框有边被贴动（每边 ±2px、需明显更强的边缘才动）")
    # ② 几何 → 图标校验：每张推出来的卡里**应当**有职业图标（校验，不是几何来源 ✓）
    if cards and ui:
        import ui_layout as _UL2                                      # noqa: PLC0415
        _sc = a.shape[1] / _UL2.LW
        _g1280 = cv2.resize(a, (_UL2.LW, _UL2.LH), interpolation=cv2.INTER_AREA)
        _b1280 = [(round(c["box"][0] / _sc), round(c["box"][1] / _sc),
                   round(c["box"][2] / _sc), round(c["box"][3] / _sc)) for c in cards]
        _rate, _info = _UL2.validate_cards(L, _g1280, _b1280)
        print(f"  几何→图标校验：{_rate * 100:.0f}% 的卡里找到职业图标"
              f"（命中 {sum(1 for x in _info if x['role'])}/{len(_info)}）" +
              ("" if _rate >= 0.8 else "  ⚠️ 低于 80% ⇒ 手牌假设可疑"))
    grid = measure_grid(a, L) if _grid_ok else []          # 关卡身份闸（不符就不画格网 ✓）
    if not _grid_ok:
        print(f"  ⚠️ {_grid_why}")
    fo = measure_fieldops(a)
    print(f"战场干员UI {sum(1 for v in fo.values() if v)}/{len(fo)}")
    for k, v in fo.items():
        print(f"  {k:22s} {v}")
    if args.ref:
        H = estimate_view(args.img, args.ref)
        if H is not None:
            grid = transform_cells(grid, np.linalg.inv(H))
        else:
            print("  ⚠️ 参照帧配准失败 ⇒ 格心仍是普通视角坐标（这张图的格子可能偏）")
    print(f"UI {sum(1 for v in ui.values() if v)}/{len(ui)}  卡 {len(cards)}  格 {len(grid)}"
          + (f"   ⚠️ {grid_why}" if grid_why else ""))
    for k, v in ui.items():
        print(f"  {k:16s} {v}")
    _cost = (read_number(a, ui["6 费用(菱形C+数字)"], "int", loop=L, upscale=2)
             if ui.get("6 费用(菱形C+数字)") else None)
    _T = L.clock(_cost)[0] if _cost is not None else None
    dyn = measure_dynamic(a, L, args.stage, _T)
    dyn["_level"] = L.lv if _grid_ok else None             # 只在关卡闸通过时画（坐标才有意义 ✓）
    print(f"  动态层：T={_T}  我方 {len(dyn['ours'])}  敌人（算出来的）{len(dyn['enemies'])}"
          + ("" if _grid_ok else "   （格网未过闸 ⇒ 不画 ✓）"))
    cv2.imwrite(args.out, render(a, ui, cards, grid, fo, layers=args.layers,
                                 panel=args.panel, debug_containers=args.debug_containers,
                                 dyn=dyn))
    pathlib.Path(args.json).write_text(json.dumps(
        {"img": args.img, "ui": ui, "fieldops": fo, "cards": cards, "grid": grid}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"→ {args.out}  /  {args.json}")


# ── ② 动态目标层（战场上的"真正动态内容"，外援 §"needs_visual_detection" 第三项）──
def measure_dynamic(a: np.ndarray, L, stage: str, t_sec: float | None):
    """返回 {"ours": [(cell)], "enemies": [{"cell":..,"name":..}]}。

    · **我方**：暂停压暗帧用蓝绿血条色检测 ✓（跑动帧会闪，见 §8c 的已知问题）。
    · **敌人**：MAA 零敌人检测 ⇒ 位置**算**出来（`enemy_timeline`），这里只是把算出来的位置画到帧上 ✓；
      所以必须先过**关卡身份闸**（不是本关就不画 ✓），并且需要 T（费用钟 ✓）。
    """
    out = {"ours": list(L.our_operators(a)), "enemies": []}
    if t_sec is None or t_sec < 0:
        return out
    try:
        import enemy_timeline as ET                                  # noqa: PLC0415
        for e in ET.enemies_at(stage, t_sec):
            g = e.get("grid")
            if g:
                out["enemies"].append({"cell": tuple(g), "name": e.get("enemy_name", "?")})
    except Exception:                                                # noqa: BLE001
        pass
    return out


if __name__ == "__main__":
    main()
