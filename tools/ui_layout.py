"""UI 静态布局表（Layer 1）+ 锚点（Layer 2）+ 映射（Layer 3）。

为什么要有这一层（2026-09-19，用户"框没框在完整 UI 上" + 外援意见）：
    我们原来每帧都在"手标 ROI 里找亮像素外框" ✗ —— 那是**内容剪影**（数字/图标的墨迹），
    **不是 UI 元素**；而且换关/换状态就整片偏 ✗。外援给的框架更对：
      Layer 1  在 **1280×720 逻辑画布**上维护一份**固定**的元素框表（UI 是固定美术 ⇒ 写死一次）
      Layer 2  每帧只找**少量极可靠锚点**（暂停键 / 敌人数徽章 / 手牌带上下边）
      Layer 3  由锚点解出仿射（缩放+平移）⇒ 把表里的框映射到当前帧；**解不出来就明说，不画** ✗

坐标系约定：本文件里所有数字都是 **1280×720 逻辑坐标**（和 MAA 的 roi 同一套 ✓）。
实测 1920×1080 帧 ⇒ 统一缩放 1.5 ✓（若某帧不是等比缩放，Layer 3 会解出 sx≠sy 并拒绝 ✓）。

表里的框怎么来的：**在人工核对过的基准帧上量出来的**（`data/_live_now4.png`，PA-1 演习，1080p），
再 ÷1.5 落到逻辑坐标 —— 不是拍的。元素"完整"的口径 = **该元素在屏幕上画出来的全部可见墨迹**
（徽章+数字+底板边缘，不只是文字）✓。
"""
from __future__ import annotations

import cv2
import numpy as np

LW, LH = 1280, 720          # 逻辑画布
SCALE_1080 = 1.5            # 1920×1080 帧的标称缩放


# ── Layer 1：静态元素表（逻辑坐标，1080p 实量 ÷1.5） ──────────────────────
LAYOUT: dict[str, tuple[int, int, int, int]] = {
    # 战斗 HUD（屏幕坐标系固定，不跟地图走）
    "gear":        (57, 33, 102, 72),        # 1080p (85,49,153,108)：整个齿轮按钮
    "enemy":       (483, 11, 602, 49),       # (725,16,903,73)：橙色徽章 + 数字
    "lives":       (678, 14, 764, 47),       # (1017,21,1146,70)：棋子图标 + 数字
    "speed":       (1076, 31, 1117, 77),     # (1614,46,1676,115)：1X 文字 + 小 ▶（同一个开关）
    "pause":       (1191, 39, 1224, 67),     # (1786,59,1836,101)：▶/‖ 按钮
    "cost":        (1166, 499, 1256, 532),   # (1749,748,1884,798)：菱形 C + 数字
    "cost_bar":    (1159, 540, 1246, 545),   # (1739,810,1869,817)：费用回复条
    "deploy_text": (1083, 553, 1254, 570),   # (1625,829,1882,855)：残り配置可能数：N
    # 手牌行（**右对齐**：右端固定，张数决定左端）—— 只给结构，不给单卡
    "hand_row":    (23, 590, 1304, 718),
    "hand_right":  1304,                     # 右端（1080p 1956）
    "hand_pitch":  116.4,                    # 步距（1080p 174.6）
    "hand_top":    590,
    "hand_bottom": 718,
    # 战场干员（被选中时）左侧面板 + 地图上的技能钮/撤退
    "ops_panel":   (0, 150, 344, 720),
    "ops_skill":   (563, 266, 663, 329),     # (845,399,994,494)
    "ops_charge":  (594, 307, 686, 339),     # (892,460,1029,509)
    "ops_charge_n": (573, 330, 666, 364),    # (860,495,998,546)
    "ops_retreat": (586, 203, 637, 237),     # (879,304,956,355)
}

# ── Layer 2：锚点 —— 每帧只找这几个，用来解仿射 ──────────────────────────
# 每个锚点：在**逻辑坐标**下的搜索区 + 判定方式（都是"极可靠"的东西）
ANCHOR_ROI = {
    "pause": (1160, 20, 1280, 90),      # 右上 ▶/‖（亮、形状稳定）
    "enemy": (450, 5, 640, 60),         # 橙色徽章（颜色极独特）
    "hand_band": (0, 560, 1280, 720),   # 手牌带（上下沿是长水平边）
}


def find_anchors(frame720: np.ndarray) -> dict:
    """返回找到的锚点（逻辑坐标）。

    三个锚点各自极可靠：暂停键=右上最亮的团块；敌人数=橙色徽章的质心；
    手牌带=底部那条长水平边缘。找不到的锚点**不返回**（调用方按缺失处理 ✓）。
    """
    out: dict = {}
    h, w = frame720.shape[:2]
    # ① 暂停键：右上 ROI 里最亮的连通块
    x0, y0, x1, y1 = ANCHOR_ROI["pause"]
    roi = frame720[y0:y1, x0:x1]
    m = np.uint8(roi.max(2) > 170) * 255
    n, _l, st, ce = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for (bx, by, bw, bh, ar), (cx, cy) in zip(st[1:], ce[1:]):
        if ar > 60 and (best is None or ar > best[0]):
            best = (ar, x0 + cx, y0 + cy, bw, bh)
    if best:
        out["pause"] = best[1:]
    # ② 敌人数徽章：橙色（R 高、B 低）
    x0, y0, x1, y1 = ANCHOR_ROI["enemy"]
    roi = frame720[y0:y1, x0:x1].astype(np.int16)
    orange = (roi[:, :, 2] > 150) & (roi[:, :, 2] - roi[:, :, 0] > 60) & (roi[:, :, 1] > 70)
    if orange.sum() > 30:
        ys, xs = np.where(orange)
        out["enemy"] = (x0 + float(xs.mean()), y0 + float(ys.mean()),
                        int(np.ptp(xs)), int(np.ptp(ys)))   # numpy 2.x 移除了 ndarray.ptp
    # ③ 手牌带：底部一条长水平边（取最强的那条）
    x0, y0, x1, y1 = ANCHOR_ROI["hand_band"]
    roi = cv2.cvtColor(frame720[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    gy = np.abs(cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=3)).sum(1)
    gy = cv2.GaussianBlur(gy.reshape(-1, 1), (0, 0), 2).ravel()
    out["hand_top"] = (0.0, float(y0 + int(np.argmax(gy[: (600 - y0)]))), 0, 0)
    out["hand_bottom"] = (0.0, float(y0 + int(np.argmax(gy[(640 - y0):]) + (640 - y0))), 0, 0)
    return out


# 标称锚点（在基准帧上量出来的逻辑坐标）—— Layer 3 拿它当"0 偏"参照
NOMINAL = {
    "pause": (1207.5, 52.5),
    "enemy": (508.0, 28.0),
    "hand_top": 596.7,
    "hand_bottom": 714.0,
}


def fit_affine(anchors: dict, nominal: dict | None = None) -> tuple[float, float, float] | None:
    """由锚点解 (sx, sy, dy) —— UI 只在**竖直方向**上可能有整体平移/缩放差异，
    水平方向用标称 1.0 逻辑（手牌右端与右上角都是固定锚）。

    返回 (sx, sy, dy)；锚点不足 2 个 ⇒ None（fail-closed：宁可不画 ✗）。
    """
    nom = nominal or NOMINAL
    if not {"pause", "enemy", "hand_bottom"} <= set(anchors):
        return None
    (px, py, *_a) = anchors["pause"]
    (ex, *_b) = anchors["enemy"]
    by = anchors["hand_bottom"][1]
    # ⚠️ 必须用**相距最远**的锚解缩放：暂停键与敌人数只差 ~24px，一点噪声就解出 5% 的假缩放 ✗
    #    （实测：用那两个解出 sy=0.948 ⇒ 右下三个元素集体偏 25px ✗；改用 暂停键↔手牌带下沿 差 ~660px ✓）
    sx = (ex - px) / (nom["enemy"][0] - nom["pause"][0])
    sy = (by - py) / (nom["hand_bottom"] - nom["pause"][1])
    dy = py - nom["pause"][1] * sy
    if not (0.6 <= sx <= 1.6 and 0.6 <= sy <= 1.6):    # 离谱就拒绝（fail-closed）
        return None
    if abs(sx - sy) > 0.08:                            # 非等比 ⇒ 交给专门校准，别硬拉
        return None
    return (sx, sy, dy)


def map_box(box, tr) -> tuple[int, int, int, int]:
    """把逻辑框映射到当前 1280×720 帧。"""
    sx, sy, dy = tr
    x0, y0, x1, y1 = box
    return (int(round(x0 * sx)), int(round(y0 * sy + dy)),
            int(round(x1 * sx)), int(round(y1 * sy + dy)))


def hand_boxes(n_cards: int, tr, right: int | None = None,
               pitch: float | None = None) -> list[tuple[int, int, int, int]]:
    """手牌：**右对齐** + 固定步距 ⇒ 由张数推出每张卡的完整框（不再按图标偏移猜 ✓）。"""
    sx, sy, dy = tr
    r = (right if right is not None else LAYOUT["hand_right"]) * sx
    p = (pitch if pitch is not None else LAYOUT["hand_pitch"]) * sx
    top = int(round(LAYOUT["hand_top"] * sy + dy))
    bot = int(round(LAYOUT["hand_bottom"] * sy + dy))
    return [(int(round(r - (n_cards - i) * p)), top, int(round(r - (n_cards - i - 1) * p)), bot)
            for i in range(n_cards)]


def to_frame_boxes(tr) -> dict[str, tuple[int, int, int, int]]:
    """整张表映射到当前帧。"""
    return {k: map_box(v, tr) for k, v in LAYOUT.items() if isinstance(v, tuple) and len(v) == 4}


def check_on_baseline(path: str = "data/_live_now4.png") -> None:
    """自检：在基准帧上，映射出来的框应与"当时实量"的一致（差几像素可接受）。"""
    import pathlib
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    F = cv2.resize(img, (LW, LH), interpolation=cv2.INTER_AREA)
    a = find_anchors(F)
    print("锚点:", {k: tuple(round(x, 1) for x in v[:2]) for k, v in a.items()})
    tr = fit_affine(a)
    print("仿射:", None if tr is None else tuple(round(v, 4) for v in tr))
    if tr:
        got = to_frame_boxes(tr)
        # 对照：1080p 实量 ÷1.5
        real = {"gear": (85, 49, 153, 108), "enemy": (725, 16, 903, 73), "lives": (1017, 21, 1146, 70),
                "speed": (1614, 46, 1676, 115), "pause": (1786, 59, 1836, 101),
                "cost": (1749, 748, 1884, 798), "cost_bar": (1739, 810, 1869, 817),
                "deploy_text": (1625, 829, 1882, 855)}
        print("%-12s %-26s %-26s %s" % ("元素", "映射后(逻辑)", "实量÷1.5", "差(px)"))
        for k, v in real.items():
            r = tuple(round(x / 1.5) for x in v)
            g = got[k]
            d = max(abs(g[i] - r[i]) for i in range(4))
            print("%-12s %-26s %-26s %s" % (k, str(g), str(r), f"{d} {'✓' if d <= 6 else '✗'}"))


# ── 坐标系声明（外援 §7：不许让标注工具自己猜缩放）─────────────────────────
COORDINATE_SPACE = {
    # 外援 §6：**不许让 [x1,y1,x2,y2] 与 [x,y,w,h] 同时存在** ⇒ 显式声明（本表与导出一律 xyxy）
    "bbox_format": "xyxy",
    "bbox_note": "所有 bbox 一律 [x1, y1, x2, y2]（左上、右下）；要 [x,y,w,h] 请自行换算一次",
    "internal": {"width": LW, "height": LH, "type": "arknights-logical-720"},
    "frame":    {"width": 1920, "height": 1080, "type": "arknights-frame-1080"},
    "scale":    SCALE_1080,
    "note": "表内全为 internal(1280×720)；映射到 frame(1920×1080) 时 ×1.5。任何写出去的框都要带 space。",
}

# ── UI 本体树（外援 §4）：每个节点 = 容器框 + 内容框 + 锚点 + 方法与置信 ──────
#   container = 玩家眼里的那个"控件"（含底板/边缘）→ 画给人看
#   content   = 识别器真正看到的那点墨迹（数字/图标）→ 喂 OCR
#   两层的区别是外援 §2/§3 点破的：tight bbox ≠ UI element bbox
NODES: dict[str, dict] = {
    "hud.settings":    {"container": (52, 28, 108, 78),  "content": (57, 33, 102, 72),
                        "anchor": None,            "method": "static", "conf": 0.9},
    "hud.enemy_count": {"container": (478, 8, 608, 54),  "content": (483, 11, 602, 49),
                        "anchor": "enemy_badge",   "method": "anchor+affine", "conf": 0.95},
    "hud.life":        {"container": (672, 10, 770, 52), "content": (678, 14, 764, 47),
                        "anchor": "enemy_badge",   "method": "anchor+affine", "conf": 0.85},
    "hud.speed":       {"container": (1070, 27, 1124, 82), "content": (1076, 31, 1117, 77),
                        "anchor": "pause",         "method": "anchor+affine", "conf": 0.9},
    "hud.pause":       {"container": (1185, 34, 1230, 72), "content": (1191, 39, 1224, 67),
                        "anchor": "pause",         "method": "anchor", "conf": 0.95},
    "hud.cost":        {"container": (1160, 494, 1262, 537), "content": (1166, 499, 1256, 532),
                        "anchor": "hand_bottom",   "method": "anchor+affine", "conf": 0.9},
    "hud.cost_bar":    {"container": (1153, 536, 1252, 548), "content": (1159, 540, 1246, 545),
                        "anchor": "hand_bottom",   "method": "anchor+affine", "conf": 0.8},
    "hud.deploy_count": {"container": (1077, 549, 1260, 575), "content": (1083, 553, 1254, 570),
                        "anchor": "hand_bottom",   "method": "anchor+affine", "conf": 0.85},
    "hand.row":        {"container": (23, 590, 1304, 718), "content": (23, 590, 1304, 718),
                        "anchor": "hand_band",     "method": "static+band", "conf": 0.9},
    "ops.panel":       {"container": (0, 150, 344, 720),  "content": (0, 150, 344, 720),
                        "anchor": None,            "method": "static", "conf": 0.6},
    "ops.skill":       {"container": (558, 261, 668, 334), "content": (563, 266, 663, 329),
                        "anchor": None, "method": "static", "conf": 0.6},
    "ops.charge":      {"container": (589, 302, 691, 344), "content": (594, 307, 686, 339),
                        "anchor": None, "method": "static", "conf": 0.6},
    "ops.charge_text": {"container": (568, 325, 671, 369), "content": (573, 330, 666, 364),
                        "anchor": None, "method": "static", "conf": 0.6},
    "ops.retreat":     {"container": (581, 198, 642, 242), "content": (586, 203, 637, 237),
                        "anchor": None, "method": "static", "conf": 0.6},
}

# 手牌：**父坐标系**（外援 §5：screen → hand.row → N-card layout → card[i]）
HAND_PARENT = "hand.row"
HAND_GEOM = {"right": 1304, "top": 590, "bottom": 718, "pitch": 116.4,
             "n_range": (7, 12), "icon_role": "validation_only"}


def nodes_in_frame(tr) -> dict[str, dict]:
    """整棵树按仿射映射 —— **返回逻辑坐标（1280×720）**，缩放交给调用方做一次。

    ⚠️ 这里乘过一次、调用方再乘一次 ⇒ 框会以 2.25× 飘出去（2026-09-19 踩过并靠看图抓到 ✗）。
    """
    out = {}
    for k, v in NODES.items():
        if not isinstance(v.get("container"), tuple):
            continue
        c = map_box(v["container"], tr)
        n = map_box(v["content"], tr) if isinstance(v.get("content"), tuple) else c
        out[k] = {"container": c, "content": n,
                  "anchor": v.get("anchor"), "method": v.get("method"), "conf": v.get("conf")}
    return out


def ground_truth(frame_path: str = "data/_live_now4.png") -> dict:
    """把**人工核对过的基准帧**导出成 ground truth（外援 §9）：
    存的是**几何结构参数**（不是模板 ✗）—— 以后换关/换分辨率，验的是"同一套 logical layout
    还能不能经 anchor + edge refinement 得到这些框"，而不是重新训检测器。"""
    import pathlib as _pl
    img = cv2.imdecode(np.fromfile(frame_path, np.uint8), cv2.IMREAD_COLOR)
    F = cv2.resize(img, (LW, LH), interpolation=cv2.INTER_AREA)
    anc = find_anchors(F)
    tr = fit_affine(anc)
    return {
        "scene": "battle",
        "source_frame": _pl.Path(frame_path).name,
        "coordinate_space": COORDINATE_SPACE,
        "anchor_solved": None if tr is None else {"sx": round(tr[0], 5), "sy": round(tr[1], 5),
                                                  "dy": round(tr[2], 4)},
        "nodes": {k: {"container": v["container"], "content": v["content"],
                      "method": v["method"], "conf": v["conf"]}
                  for k, v in NODES.items()},
        "hand": {"parent": HAND_PARENT, "bbox": NODES["hand.row"]["container"],
                 "count": 11, "card": {"pitch": HAND_GEOM["pitch"],
                                       "width": HAND_GEOM["pitch"],
                                       "top": HAND_GEOM["top"], "bottom": HAND_GEOM["bottom"]},
                 "right": HAND_GEOM["right"]},
        "acceptance": {"hand": {"iou": 0.95, "edge_px": 5},
                       "hud": {"box": "container", "iou": 0.90}},
    }

# ── ① 干员详情面板的**内部小块**（进树 ✓；原来是每帧量的 F1–F8 ✗）──────────
# 逻辑坐标 = 基准帧实量 ÷1.5；container 比 content 外扩 ~4px 表示"控件边缘" ✓
for _k, _c, _m in (
    ("ops.name",       (26, 150, 142, 191), 4),
    ("ops.stats",      (17, 167, 145, 266), 4),
    ("ops.range_label",(85, 220, 113, 245), 4),
    ("ops.hp",         (53, 233, 291, 277), 4),
    ("ops.tabs",       (17, 280, 205, 310), 4),
    ("ops.skill_icon", (25, 305, 76, 334), 4),
    ("ops.skill_name", (67, 300, 289, 341), 4),
    ("ops.skill_desc", (67, 337, 353, 399), 4),
):
    NODES[_k] = {"container": (_c[0] - _m, _c[1] - _m, _c[2] + _m, _c[3] + _m), "content": _c,
                 "anchor": None, "method": "static", "conf": 0.6}

# ── 节点类型（外援 §"hand_row 不要理解成视觉框"）────────────────────────────
#   ui_element        = 游戏真的画出来的一块 UI（overlay 该画它）
#   layout_container  = 只是"这些元素属于同一个布局系统"的逻辑父（**非视觉** ⇒ 默认不画 ✓）
NODE_TYPE = {
    # ui_element      = 游戏画出来的一块具体控件（overlay 画它）
    # ui_container    = 游戏画出来的一块**容器/背板**，里面还装子元素（overlay 也画，但要知道它是容器）
    # layout_container= **非视觉**的逻辑父坐标系（overlay 默认不画 ✓）
    "hand.row": {"type": "layout_container", "visible": False},
    "ops.panel": {"type": "ui_container", "visible": True},
}
for _k in NODES:
    NODE_TYPE.setdefault(_k, {"type": "ui_element", "visible": True})

# HandRow 的**参数化模型**（外援 §"每帧只需重新估计 N / x_right / y_top / y_bottom"）
HAND_PARAMS = {
    "x_right": HAND_GEOM["right"], "y_top": HAND_GEOM["top"], "y_bottom": HAND_GEOM["bottom"],
    "card_width": HAND_GEOM["pitch"], "card_height": HAND_GEOM["bottom"] - HAND_GEOM["top"],
    "pitch": HAND_GEOM["pitch"], "count": 11,
    "x_of": "x_right - card_width - (N - 1 - i) * pitch",
    "per_frame_estimate": ["count", "x_right", "y_top", "y_bottom"],
}


def emit_layout_json(path: str = "data/battle_ui_layout.json",
                     frame_path: str = "data/_live_now4.png") -> dict:
    """把 v3 的标注结构**正式导出成 BattleUI layout**（外援给的下一阶段入口）。

    形态：battle_ui → { hud / ops_panel / hand_row }；每个节点带 type（ui_element vs layout_container）、
    container/content 两层框、anchor/method/conf；hand_row 下挂 card[i]（**bbox 是一级数据，
    icon 是子数据** —— 命名上不许再像"icon 驱动几何" ✗）。"""
    import json as _json
    import pathlib as _pl
    cards = []
    for i in range(HAND_PARAMS["count"]):
        left = HAND_PARAMS["x_right"] - HAND_PARAMS["card_width"] - (HAND_PARAMS["count"] - 1 - i) * HAND_PARAMS["pitch"]
        cards.append({"id": f"card[{i}]", "type": "ui_element",
                      "bbox": [int(round(left)), HAND_PARAMS["y_top"],
                               int(round(left + HAND_PARAMS["card_width"])), HAND_PARAMS["y_bottom"]],
                      "icon": {"x": 64 + int(round(i * HAND_GEOM["pitch"])), "role": None,
                               "note": "icon 只做校验；几何来自 hand_row 参数"}})
    def node(k):
        v = NODES[k]
        n = {"id": k, "type": NODE_TYPE[k]["type"], "visible": NODE_TYPE[k]["visible"],
             "container": list(v["container"]), "content": list(v["content"]),
             "anchor": v.get("anchor"), "method": v.get("method"), "conf": v.get("conf")}
        return n
    tree = {
        "schema": "battle_ui/v1",
        "coordinate_space": COORDINATE_SPACE,
        "id": "battle_ui", "type": "root",
        "definition": {
            "ui_element": "tight visual-element bbox（游戏真的画出来的那块）",
            "layout_container": "逻辑父坐标系，**非视觉** ⇒ overlay 默认不画",
        },
        "children": [
            {"id": "hud", "type": "group",
             "children": [node(k) for k in NODES if k.startswith("hud.")]},
            {"id": "ops_panel", "type": "ui_element", "children":
                [node("ops.panel")] + [node(k) for k in NODES if k.startswith("ops.") and k != "ops.panel"]},
            {"id": "hand_row", "type": "layout_container", "visible": False,
             "params": HAND_PARAMS, "children": cards},
        ],
        "acceptance": {"hand": {"iou": 0.95, "edge_px": 5},
                       "hud": {"box": "container", "iou": 0.90}},
        "needs_visual_detection": ["anchor", "geometry_refinement", "dynamic_objects"],
    }
    _json.dump(tree, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return tree


# ── 手牌 layout 规则（**实测反推**，2026-09-19）──────────────────────────────
# 在 9 张不同 N 的战斗帧上量"图标间距 vs 张数"（1080p）：
#     N=10 → 178.0   （行宽 1781，右端 1898 ⇒ 右对齐）
#     N=11 → 174~176 （行宽 1920，左端 −23 ⇒ **溢出屏左**）
#     N=12 → 160.0   （行宽 1921 ⇒ **压缩档**）
# ⇒ **间距随张数压缩、右端固定**；12 张起进"压缩档"。这张表就是外援要的 hand_layout_model ✓。
HAND_LAYOUT_TABLE = {          # 1080p 下的 (间距, 是否为压缩档)
    4: (176.0, False), 5: (176.0, False), 6: (176.0, False), 7: (176.0, False),
    8: (176.0, False), 9: (177.0, False), 10: (178.0, False), 11: (174.6, False),
    12: (160.0, True), 13: (160.0, True),
}
HAND_RIGHT_1080 = 1897.0       # 右端固定（右对齐锚）
HAND_RIGHT_LOGICAL = HAND_RIGHT_1080 / SCALE_1080      # ≈1264.7

# `hand_row` = **布局允许出现的区域**（不是当前卡的 union ✗ —— 否则坐标系每帧自己变 ✓）
NODES["hand.row"] = {"container": (0, 586, LW, 720), "content": (0, 586, LW, 720),
                     "anchor": "hand_band", "method": "static+band", "conf": 0.9}
NODE_TYPE["hand.row"] = {"type": "layout_container", "visible": False}


def hand_pitch(n: int) -> tuple[float, bool]:
    """按时数取 (间距, 压缩档?)；表外插值/回落到最近邻（1080p → 逻辑坐标由调用方乘）。"""
    if n in HAND_LAYOUT_TABLE:
        return HAND_LAYOUT_TABLE[n]
    keys = sorted(HAND_LAYOUT_TABLE)
    if n < keys[0]:
        return HAND_LAYOUT_TABLE[keys[0]]
    if n > keys[-1]:
        return HAND_LAYOUT_TABLE[keys[-1]]
    lo = max(k for k in keys if k < n); hi = min(k for k in keys if k > n)
    p_lo, c_lo = HAND_LAYOUT_TABLE[lo]; p_hi, _ = HAND_LAYOUT_TABLE[hi]
    return (p_lo + (p_hi - p_lo) * (n - lo) / (hi - lo), c_lo)


def hand_boxes_dynamic(n: int, tr):
    """动态手牌：**右锚 + 按时数取间距** ⇒ 生成 n 张卡的框（逻辑坐标）。

    返回 (boxes, meta)。meta 里带 pitch / 压缩档 / 右端 —— 便于上层做"几何→图标校验" ✓。
    """
    pitch1080, compressed = hand_pitch(n)
    pitch = pitch1080 / SCALE_1080                      # → 逻辑
    right = HAND_RIGHT_LOGICAL
    top = NODES["hand.row"]["container"][1] + 4
    bottom = NODES["hand.row"]["container"][3]
    boxes = []
    for i in range(n):
        x1 = right - (n - i) * pitch
        boxes.append((int(round(x1)), int(top), int(round(x1 + pitch)), int(bottom)))
    return boxes, {"n": n, "pitch_1080": pitch1080, "compressed": compressed,
                   "right_1080": HAND_RIGHT_1080, "mode": "compressed" if compressed else "normal"}


# ── ② 几何 → 图标校验（外援 §6：方向必须反过来）────────────────────────────
def validate_cards(loop_obj, frame1280: np.ndarray, card_boxes: list, min_score: float = 0.62):
    """每张推出来的卡里**应当**能找到职业图标 —— 这是**校验**，不是几何来源 ✓。

    返回 (通过率, 每张卡的命中信息)。命中率过低 ⇒ 调用方应否掉整个手牌假设（fail-closed ✓）。
    """
    roles = ("Pioneer", "Warrior", "Sniper", "Caster", "Tank", "Medic", "Support", "Special")
    ok, info = 0, []
    for i, b in enumerate(card_boxes):
        x0, y0, x1, y1 = (int(v) for v in b)
        # ⚠️ N≥11 时最左那张会**溢出屏左**（实测 x≈−23），直接拿负坐标去裁会得到空图 ⇒ OpenCV 崩 ✗
        H, W = frame1280.shape[:2]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W, x1), min(H, y1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            info.append({"i": i, "role": None, "score": 0.0, "note": "越界(卡在屏外)"})
            continue
        hit_role, hit_score = None, 0.0
        for r in roles:
            for hh in loop_obj.p.templates.find_all(frame1280, "BattleOperRole" + r,
                                                    roi=(x0, y0, max(1, x1 - x0), max(1, y1 - y0)),
                                                    threshold=min_score):
                if float(hh.score) > hit_score:
                    hit_role, hit_score = r, float(hh.score)
        info.append({"i": i, "role": hit_role, "score": round(hit_score, 3)})
        ok += 1 if hit_role else 0
    return (ok / max(1, len(card_boxes))), info


# ── ③ 边缘精修（外援 §"把理论框贴到真实边缘"）──────────────────────────────
def refine_box(gray: np.ndarray, box, span: int = 2, min_grad: float = 6.0,
               gain: float = 2.0):
    """把理论框的四条边贴到**明显更该在**的位置（局部微调，不重检测 ✓）。

    ⚠️ 判据必须严格：**只在"当前这条边不像是边缘、而附近有一条明显强得多的边缘"时才动** ✗。
    第一版写成"±6px 内取最大梯度" ⇒ 22/22 个框都被顶格推 6px ✗（UI 到处是纹理 ⇒ 那就是加噪声，
    不是精修）。现在要求：候选峰值须是局部极大，且**比当前所在位置的梯度强 ≥ gain 倍** ✓。
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    H, W = gray.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W - 1, x1), min(H - 1, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return (x0, y0, x1, y1), (0, 0, 0, 0)
    col = np.abs(cv2.Sobel(gray[y0:y1, :], cv2.CV_32F, 1, 0, ksize=3)).mean(0)
    row = np.abs(cv2.Sobel(gray[:, x0:x1], cv2.CV_32F, 0, 1, ksize=3)).mean(1)
    def snap(profile, at, lo, hi):
        a, b = max(lo, at - span), min(hi, at + span + 1)
        seg = profile[a:b]
        if seg.size == 0:
            return at
        j = int(np.argmax(seg))
        cand = a + j
        here = profile[at]
        is_local_max = (j == 0 or seg[j] >= seg[j - 1]) and (j == seg.size - 1 or seg[j] >= seg[j + 1])
        if cand != at and is_local_max and seg[j] >= min_grad and seg[j] >= gain * max(here, 1e-6):
            return cand
        return at
    nx0 = snap(col, x0, 0, x1 - 2)
    nx1 = snap(col, x1, nx0 + 2, W - 1)
    ny0 = snap(row, y0, 0, y1 - 2)
    ny1 = snap(row, y1, ny0 + 2, H - 1)
    return (nx0, ny0, nx1, ny1), (nx0 - x0, ny0 - y0, nx1 - x1, ny1 - y1)


if __name__ == "__main__":
    import json as _json
    check_on_baseline()
    gt = ground_truth()
    _json.dump(gt, open("data/ground_truth_battle_1080.json", "w", encoding="utf-8"),
               ensure_ascii=False, indent=1)
    print("已导出 data/ground_truth_battle_1080.json（%d 节点 + 手牌结构）" % len(gt["nodes"]))
    tr_ = emit_layout_json()
    print("已导出 data/battle_ui_layout.json（树：%s）" % [c["id"] for c in tr_["children"]])
