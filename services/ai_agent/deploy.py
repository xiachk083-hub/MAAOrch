"""部署手势 — 把"往哪一格放、朝哪个方向"变成真实拖拽。

游戏里没有"格子坐标"，也没有方向按钮。MAA 的做法（BattleHelper::deploy_oper）
是两次滑动：
    1) 从干员卡中心拖到目标格中心（拖拽距离越远，滑动时长按比例加长，有下限）
    2) 从目标格中心再朝该方向滑一段（约 400px），这一步定朝向
本文件按同一套做法实现，坐标来源是 tile_map 的投影（已用真实关卡验证）。

两个实测得来的要点:
  · **部署栏是右对齐的**：真实战斗截图里 6 张卡在 y=655、x=629..1219，间距 118px。
    卡数变化时右锚点固定、向左生长。所以卡片坐标可以本地算，不必每次问视觉；
    但首次仍用视觉标定锚点（自校准，别信我的硬编码）。
  · 拖拽要用"慢滑"（时长 400-600ms）：`adb input swipe` 太快会被当成抹动，
    游戏不会认作拖拽放置。
"""
from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass

from services.ai_agent.percept import PROJ, Hit, Perception, log
from services.ai_agent.tile_map import Level

# 方向 → 屏幕位移向量（用于第二次滑动定朝向）
DIR_VEC = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
           "Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}
DIRECTION_COEFF = 400.0      # MAA: special_params[0]=400 × (屏高/720)


@dataclass
class CardLayout:
    """部署栏布局（自校准）。"""

    y: int
    anchor_x: int          # 最右侧卡片的中心 x
    pitch: int = -118      # 每往左一张的间距（负值 = 向左）
    count: int = 0

    def point(self, index_from_right: int) -> tuple[int, int]:
        return (int(self.anchor_x + self.pitch * index_from_right), int(self.y))

    def points(self, n: int | None = None) -> list[tuple[int, int]]:
        n = n or self.count or 1
        return [self.point(i) for i in range(n)]


_SYS = """你在看明日方舟战斗截图（1280x720）。底部是"干员部署栏"（一排干员卡，卡上有费用数字）。
只输出 JSON: {"cards":[{"x":int,"y":int,"cost":"数字"}], "y":int}
x/y 是每张卡**中心的像素坐标**；y 是这一排卡统一的中心高度。只列真正是干员卡的东西。"""


def calibrate_cards(p: Perception, img=None, llm=None) -> CardLayout | None:
    """用视觉模型标定部署栏（一次），之后本地算所有卡位。"""
    from services.ai_agent.llm import LLM
    client = llm or LLM()
    img = p.shot() if img is None else img
    if not client.vision_ready:
        log("部署栏标定需要视觉模型（ai_vision_model）")
        return None
    r = client.ask_json(_SYS, "列出部署栏每张干员卡的中心坐标。", image=img,
                        vision=True, max_tokens=8000)
    if not r.get("ok"):
        log(f"部署栏标定失败: {r.get('error', '')[:100]}")
        return None
    d = r["json"] or {}
    cards = sorted([c for c in (d.get("cards") or []) if c.get("x") is not None],
                   key=lambda c: -int(c["x"]))
    if len(cards) < 2:
        log(f"部署栏标定结果太少: {cards}")
        return None
    xs = [int(c["x"]) for c in cards]
    ys = [int(c.get("y") or d.get("y") or 655) for c in cards]
    pitch = int(round((xs[0] - xs[1])))
    layout = CardLayout(y=int(sum(ys) / len(ys)), anchor_x=xs[0], pitch=pitch or -118,
                        count=len(cards))
    log(f"部署栏标定: y={layout.y} 右锚点={layout.anchor_x} 间距={layout.pitch} 卡数={layout.count}")
    return layout


# 战斗内实测的稳定布局（12 张卡；卡宽约 85px，x 偏 ±20 仍能点中）。
# 用它替代"每次问视觉标定" —— 视觉标定实测会失败（模型偶发不返回卡片），
# 而且每次要 10-30s，战斗里等不起。选中与否用左侧面板的变化量验证。
DEFAULT_LAYOUT = CardLayout(y=660, anchor_x=1225, pitch=-106, count=12)
# 左侧干员详情面板区域（选中卡后这里会出现立绘+技能说明）
PANEL_ROI = (0, 130, 330, 430)


def panel_open_score(p: Perception, img_before, img_after) -> float:
    """左侧面板区域的变化量 —— 用它判断"卡是否被选中/面板是否打开"。"""
    import cv2
    import numpy as np
    x, y, w, h = PANEL_ROI
    a = img_before[y:y + h, x:x + w]
    b = img_after[y:y + h, x:x + w]
    if a.size == 0 or b.size == 0:
        return 0.0
    ga = cv2.cvtColor(cv2.resize(a, (160, 200)), cv2.COLOR_BGR2GRAY).astype(float)
    gb = cv2.cvtColor(cv2.resize(b, (160, 200)), cv2.COLOR_BGR2GRAY).astype(float)
    return float(np.abs(ga - gb).mean() / 255.0)


# 底部部署栏的扫描范围（12 张卡的实测范围 x≈40..1030、y≈655；卡宽约 80px，
# 所以按 ~82px 步进扫一遍，不必知道精确卡位）。别再靠"标定精确卡位" ——
# 视觉标定偶发失败、CV 剖面法在相邻头像上会粘连（2026-09-14 三种方法都试过）。
CARD_SCAN_Y = 655
CARD_SCAN_X = list(range(70, 1045, 82))

# 部署栏的**索引布局**：实机截图量出 12 张卡横跨 x≈40~1280、间距≈103、y≈655
# （与视觉标定的"右锚点 1225 / 间距 106"吻合）。
# 关键前提：**部署栏顺序 = 编队顺序**，所以只要开战前读一次编队名单，
# 选定某个干员就是"查索引 + 点坐标"，毫秒级，不用逐张问视觉。
# 这是把战斗循环做成实时的关键（视觉单次 10-40s，一场战斗才 3 分钟）。
CARD_Y = 655
CARD_X0 = 90
CARD_PITCH = 103


def card_point_by_index(i: int) -> tuple[int, int]:
    return (int(CARD_X0 + CARD_PITCH * i), CARD_Y)


# ── 卡位识别（头像模板匹配）─────────────────────────────────────────
# 为什么不能靠"编队顺序 = 部署栏顺序"：2026-09-15 实机实测（15-12 演习，12 张卡），
# 部署栏是**按费用升序**排的（费用 3/8/12/13/14/17/20/21/21/21/22/30），而编队页
# 顺序是 MAA 填槽位的顺序 —— 拿着索引去点会点到别人身上（实测索引 0 读到 GALLUS²，
# 而编队第 1 位是艾雅法拉）。MAA 自己也是靠头像模板认卡（cache/avatars/<id>.png）：
# 卡面肖像与头像本就是同一张图，实测 12/12 全中、分数 0.95~1.00。
BAR_STRIP_Y = (598, 700)      # 部署栏竖条范围（实机量取）
CARD_FIRST_CX = 56            # 第一张卡中心
CARD_PITCH_X = 106.5          # 卡间距（12 张横跨 56..1229）


def avatar_dirs() -> list[pathlib.Path]:
    """头像目录候选：本仓库实例 + 本机 MAA 副本的 cache/avatars。

    站点头像接口要鉴权（`/copilot/avatar` 实测 401），所以这里不自己下，
    只"找现成的" —— MAA 每跑一次作业就会把它要用的干员头像下到这里。
    """
    import os
    cands = list((PROJ / "services" / "maa" / "instances").glob("*/cache/avatars"))
    roots = [pathlib.Path("D:/Xiach/M-A/MAA")]
    if os.environ.get("MAA_ROOT"):
        roots.append(pathlib.Path(os.environ["MAA_ROOT"]))
    for root in roots:
        if root.exists():
            cands += list(root.glob("*/cache/avatars"))
    return [d for d in cands if d.is_dir()]


def char_id_by_name(paths=None) -> dict[str, str]:
    """干员名 → char_id（找头像文件用）。来源: resource/battle_data.json。"""
    import json
    from services.ai_agent.percept import ResourcePaths
    p = (paths or ResourcePaths()).resource_dir / "battle_data.json"
    try:
        chars = json.loads(p.read_text(encoding="utf-8")).get("chars", {})
    except Exception as e:
        log(f"battle_data.json 读取失败: {e}")
        return {}
    return {v.get("name"): k for k, v in chars.items() if v.get("name")}


def match_cards(p: Perception, names: list[str], img=None,
                min_score: float = 0.9) -> list[dict]:
    """在部署栏里按头像找卡 → [{'name','point','score'}]（按屏幕 x 排序）。"""
    import cv2
    img = p.shot() if img is None else img
    strip = cv2.cvtColor(img[BAR_STRIP_Y[0]:BAR_STRIP_Y[1]], cv2.COLOR_BGR2GRAY)
    # 同一个 char_id 在不同 MAA 副本里可能是**不同的图**（实测两份 MAA 的
    # char_102_texas.png md5 不同：一份是卡面肖像、一份匹配只有 0.625），
    # 所以每个名字把候选文件全部试一遍，取分数最高的那个。
    files: dict[str, list[pathlib.Path]] = {}
    for d in avatar_dirs():
        for f in d.glob("*.png"):
            files.setdefault(f.stem, []).append(f)
    if not files:
        log("  没有可用的头像目录（MAA 没跑过作业？）—— 退化用索引选卡")
        return []
    name2id = char_id_by_name()
    hits = []
    for n in names:
        best = None
        for f in files.get(name2id.get(n, ""), []):
            tpl = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
            if tpl is None or tpl.shape[0] > strip.shape[0]:
                continue
            r = cv2.matchTemplate(strip, tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, ml = cv2.minMaxLoc(r)
            if best is None or mx > best[0]:
                best = (float(mx), ml, tpl.shape)
        if best and best[0] >= min_score:
            (mx, ml, shape) = best
            hits.append({"name": n, "score": round(mx, 3),
                         "point": (int(ml[0] + shape[1] // 2),
                                   int(BAR_STRIP_Y[0] + ml[1] + shape[0] // 2))})
    return sorted(hits, key=lambda h: h["point"][0])


def select_any_card(p: Perception, prefer_from_right: bool = True) -> tuple[int, int] | None:
    """扫底部栏，点中一张卡并**用"左侧面板是否打开"验证**。返回选中点或 None。

    验证方式与部署无关，纯本地差分，约 0.7s/次。
    """
    xs = list(reversed(CARD_SCAN_X)) if prefer_from_right else CARD_SCAN_X
    for x in xs:
        before = p.shot()
        p.screen.run_shell(["input", "tap", str(x), str(CARD_SCAN_Y)])
        time.sleep(0.45)
        after = p.shot()
        score = panel_open_score(p, before, after)
        if score > 0.012:
            log(f"  选中卡 @({x},{CARD_SCAN_Y})（面板变化 {score:.3f}）")
            return (x, CARD_SCAN_Y)
    log("  扫遍底部栏都没能选中卡（费用不足/卡不可用/不在战斗）")
    return None


def select_card(p: Perception, pt: tuple[int, int], verify: bool = True) -> bool:
    """点一张干员卡把它拿在手上。验证方式：底部栏/地图应当出现变化。"""
    import cv2
    import numpy as np
    before = p.shot()
    p.screen.run_shell(["input", "tap", str(pt[0]), str(pt[1])])
    time.sleep(0.6)
    after = p.shot()
    if not verify:
        return True
    g0 = cv2.cvtColor(cv2.resize(before, (320, 180)), cv2.COLOR_BGR2GRAY).astype(float)
    g1 = cv2.cvtColor(cv2.resize(after, (320, 180)), cv2.COLOR_BGR2GRAY).astype(float)
    diff = float(np.abs(g0 - g1).mean() / 255.0)
    log(f"  选卡 @{pt} 画面变化 {diff:.3f}")
    return diff > 0.002


def place(p: Perception, level: Level, card_pt: tuple[int, int], col: int, row: int,
          direction: str = "left", side: bool = False, drag_ms: int = 500) -> dict:
    """把卡上的干员放到 (col,row) 并设朝向。返回 {ok, tile_px, direction}。"""
    tile_px = level.project(col, row, side=side)
    if tile_px is None:
        return {"ok": False, "why": "投影失败"}
    dx, dy = DIR_VEC.get(direction, (0, 0))
    # 朝向滑动的落点要夹在屏内（MAA 的 fix_swipe_out_of_limit 就是干这个）：
    # 靠边的格子直接加 400px 会滑到屏幕外，实测算出 x=-24 这种落点。
    # 夹回屏内、保留方向即可 —— 游戏读的是拖动方向，不是精确距离。
    ex = int(tile_px[0] + dx * DIRECTION_COEFF)
    ey = int(tile_px[1] + dy * DIRECTION_COEFF)
    ex = min(max(ex, 12), 1268)
    ey = min(max(ey, 12), 708)
    end = (ex, ey)
    # 1) 卡 → 目标格（慢滑，否则不被认作放置）
    p.screen.run_shell(["input", "swipe", str(card_pt[0]), str(card_pt[1]),
                        str(tile_px[0]), str(tile_px[1]), str(drag_ms)])
    time.sleep(0.45)
    # 2) 目标格 → 方向（定朝向）
    p.screen.run_shell(["input", "swipe", str(tile_px[0]), str(tile_px[1]),
                        str(end[0]), str(end[1]), "300"])
    time.sleep(0.5)
    log(f"  放置 格({col},{row})→{tile_px} 朝向 {direction}（拖动 {drag_ms}ms）")
    return {"ok": True, "tile_px": list(tile_px), "direction": direction,
            "card": list(card_pt)}


def verify_placed(p: Perception, tile_px: tuple[int, int], radius: int = 60) -> bool:
    """用干员检测确认目标格附近出现了干员（ONNX，~50ms）。"""
    from services.ai_agent.battle_vision import BattleVision
    img = p.shot()
    ops = BattleVision(p.paths).operator.detect(img)
    for o in ops:
        cx, cy = o.center
        if abs(cx - tile_px[0]) <= radius and abs(cy - tile_px[1]) <= radius:
            log(f"  确认已部署: 检出干员 @{o.center} 分数 {o.score:.2f}")
            return True
    log(f"  未在 {tile_px} 附近检出干员（检出 {len(ops)} 个: "
        f"{[o.center for o in ops[:4]]}）")
    return False


# ── 简单打法：堵门 + 高台 ────────────────────────────────────────────
def plan_simple(level: Level) -> dict:
    """最朴素的打法：在敌人路径靠蓝门处放地面阻挡（朝向来敌），
    再在最近的高台放一个远程位。够用来验证手势，不指望一次打赢。"""
    path = level.enemy_path()
    if len(path) < 3:
        # 路径退化（特殊地图/数据异常）→ 退到"离蓝门最近的地面可部署格"，
        # 并要求它边上确实有路可走，否则宁可不出手（别瞎放）
        ends = level.ends
        best = None
        for c, r in level.ground_spots:
            d = min((abs(c - ec) + abs(r - er)) for ec, er in ends) if ends else 99
            if best is None or d < best[0]:
                best = (d, c, r)
        if best and best[0] <= 3:
            log(f"路径退化，退用离蓝门最近的地面位 格({best[1]},{best[2]})")
            return {"blocker": {"col": best[1], "row": best[2], "direction": "left",
                                "fallback": True}}
        log(f"路径退化且没有合适的地面位（ends={ends}）—— 不出手")
        return {}
    blocker = None
    for i in range(len(path) - 1, -1, -1):
        c, r = path[i]
        t = level.at(c, r)
        if t and t.ground_deployable:
            prev = path[i - 1] if i > 0 else path[i]
            dx, dy = prev[0] - c, prev[1] - r        # 来敌方向
            direction = ("left" if dx > 0 else "right" if dx < 0 else
                         "up" if dy > 0 else "down")
            blocker = {"col": c, "row": r, "direction": direction, "path_index": i}
            break
    if blocker is None:
        return {}
    # 高台：离阻挡位最近的可部署高台
    best = None
    for c, r in level.high_spots:
        d = abs(c - blocker["col"]) + abs(r - blocker["row"])
        if best is None or d < best[0]:
            best = (d, c, r)
    plan = {"blocker": blocker}
    if best:
        plan["ranged"] = {"col": best[1], "row": best[2],
                          "direction": blocker["direction"]}
    log(f"打法: 阻挡 格({blocker['col']},{blocker['row']}) 朝 {blocker['direction']}"
        + (f"；高台 格({plan['ranged']['col']},{plan['ranged']['row']})" if best else ""))
    return plan
