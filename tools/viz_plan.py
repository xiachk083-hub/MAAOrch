"""排轴可视化：单文件 HTML（底图 + 敌人路径(BFS) + 干员位置/朝向 + 动作列表 + 冲突标注）。

用法:
  python tools/viz_plan.py                        # 扫这一关池子全烘进下拉（原池 + <stage>_edited）
  python tools/viz_plan.py <job.json> [...]       # 只画指定的几份
  python tools/viz_plan.py --stage act16side_08   # 换关
  python tools/viz_plan.py --ingest [FILE]        # 收编导出的 edited JSON 后重画（不给路径=取下载目录最新）
输出: data/visualization/<stage>.html   （浏览器直接打开）
"""
import argparse, collections, json, pathlib, shutil

try:
    import enemy_speed as ES        # 同目录（tools/）；脚本运行时 sys.path[0] 就是 tools/
except Exception:                   # 敌人库缺失/被杀毒拦等 → 只是没有到达时间，不该让整页生成失败
    ES = None

try:
    import cost_model as CM         # 简化费用模型（悲观下界）；同目录
except Exception:
    CM = None

try:
    import duel_model as DM         # 1v1 逐秒决斗（技能时间轴 + 天赋自愈）；同目录
except Exception:
    DM = None

PROJ = pathlib.Path(__file__).resolve().parent.parent
LW, LH = 58, 56
CLS = {"PIONEER": "#e6b422", "WARRIOR": "#e05252", "TANK": "#4a90d9", "SNIPER": "#8e44ad",
       "CASTER": "#2f9e78", "MEDIC": "#4fd1c5", "SUPPORT": "#d98cb3", "SPECIALIST": "#999"}
FACE = {"Up": "↑", "Down": "↓", "Left": "←", "Right": "→", "上": "↑", "下": "↓", "左": "←", "右": "→"}
DIR_EN = {"上": "Up", "下": "Down", "左": "Left", "右": "Right",
          "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right"}


def norm_dir(d):
    """朝向统一成 MAA 的英文枚举。**池子里真有中文朝向**（实测 ex02 的池：右×8、下×4），
    原样写回作业会让 MAA 认不出来；编辑器里也会被当成 Right 旋转（'上' 会画错射程）。"""
    if not isinstance(d, str):
        return d
    d = d.strip()
    return DIR_EN.get(d, DIR_EN.get(d.upper(), d))
STAGE = "act16side_ex02"
# 关卡 -> (装置格[(x,y,标记)], 必须有人站的格, 装置名)。
# 装置几何是逐关的、从 level 数据推不出来，所以按关登记。没登记的关卡既不该画装置、
# 也不该报这条冲突 —— 否则换关后会拿 ex02 的 [5,3] 在别的图上胡说。
DEVICE = {"act16side_ex02": ([(5, 3, "❄")], [(5, 2), (5, 4)], "冰淇淋机 [5,3]")}
WALK = {"tile_road", "tile_floor", "tile_start", "tile_flystart", "tile_end", "tile_fence"}
# 2026-09-16 补两类（用户批准）。判漏实际看得到的 247 条路线里，原来 71 条"地面被迫退回直线"：
#   · `tile_replace_road` = **可替换道路**：没被替换时就是路，敌人照走（已含 `tile_road`，同类）。
#   · `tile_telin` = **敌人入场点**：MOVE 途经点确实会落在这格上（独立测：单补它无效，必须和上面一起）。
# 补完 71 → 30 条；剩的是"真几何不通"（26）与退化段（4，见 bfs 里的处理），不再是缺类型。
WALK |= {"tile_replace_road", "tile_telin"}


def device_info(stage=None):
    return DEVICE.get(stage or STAGE) or ([], [], "")


def level(stage: str = None, all_routes: bool = False):
    stage = stage or STAGE
    lf = PROJ / "data" / ("_level_%s.json" % stage)
    if not lf.exists():
        raise RuntimeError("缺少关卡文件 %s（先下载该关 level 数据）" % lf)
    d = json.load(open(lf, encoding="utf-8"))
    T, M = d["mapData"]["tiles"], d["mapData"]["map"]
    H, W = len(M), len(M[0])

    # ⚠️ **坐标系（2026-09-16 用 MAA Tile-Pos 定案，别再翻）**：
    # `location:[x,y]` 与关卡 `mapData.map` 的行序**相同** —— y 直接索引 `M[y][x]`。
    # 判据：MAA `Arknights-Tile-Pos` 的 tiles 与 `M` 逐格同序 **100%**（4/5 关，第 5 关对称无法区分）。
    # 历史 bug：这里原来用 `M[H-1-y]`，而页面画格子时又用了 `H-1-y` 的屏幕行 ⇒ 地图(两次翻转)对了、
    # 干员/路径/出怪口(只翻一次)**相对地图上下镜像** ⇒ "干员飘在虚空里"。
    def key(x, y):
        return T[M[y][x]]["tileKey"]

    def bt(x, y):
        return T[M[y][x]]["buildableType"]

    # **8 邻域**：关卡数据里 `allowDiagonalMove` 在全部 852 条路线上都是 true ⇒ 敌人真的斜着走。
    # 原来只走 4 邻域会高估路径长度（斜穿等于 1 步而不是 2 步），到门时刻随之偏晚。
    _NB = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))

    def bfs(a, b):
        # 退化段（途经点与上一段终点同格）：`n == b` 只在**邻居**上判，a == b 时永远返回 None，
        # 整条路线就被误判成"走不通"→ 退回直线近似。实测 4 条踩到这个（如 act16side_06 (6,4)）。
        if a == b:
            return [a]
        q = collections.deque([a]); prev = {a: None}
        while q:
            x, y = q.popleft()
            for dx, dy in _NB:
                n = (x + dx, y + dy)
                if n in prev or not (0 <= n[0] < W and 0 <= n[1] < H) or key(*n) not in WALK:
                    continue
                prev[n] = (x, y)
                if n == b:
                    p, c = [], n
                    while c:
                        p.append(c); c = prev[c]
                    return list(reversed(p))
                q.append(n)
        return None


    class RoutePath(list):
        """路径（格列表）+ 这条路的元数据。**故意做成 list 子类**：
        `for a,b,p in routes` / `[p for _,_,p in routes]` / `json.dumps` 这些既有用法全都不用改，
        要停留时间的地方 `getattr(p,'waits',0)` 取。改元组元数会打断 job_judge/stage_logic/_axis_check。"""
        waits = 0.0        # 累计停留秒数（WAIT_FOR_SECONDS）
        mode = "WALK"      # motionMode：WALK / FLY
        approx = False     # 是否直线近似
        waits_at = ()      # 停留点格（画在地图上）
        waits_seq = ()     # [(格, 秒), …] **逐次**停留：只有总秒数 + 停留点**定不了**"这一格之前等了多久"

    routes, fly_starts = [], set()

    def rp(p):
        """路线端点 → **网格坐标**：`row` 与 `map` 行序**相反**，换算一次。

        判据：按 `H-1-row` 换算后，端点落在 MAA 的 `isStart`/`isEnd` 上是 **99%（334/338）**；
        不换算只有 13%。所以"route 的行"和"map 的行"在同一份文件里是相反的两种约定。
        """
        return (p.get("col"), H - 1 - p.get("row"))

    ri2path: dict = {}   # raw routeIndex → (start, end, path)：出怪表的 routeIndex 索引的是 raw 列表
    for ri, r in enumerate(d.get("routes") or []):
        sp, ep = r.get("startPosition") or {}, r.get("endPosition") or {}
        a, b = rp(sp), rp(ep)
        if a == b:
            continue
        mode = r.get("motionMode") or "WALK"
        # 途经点：MOVE 走过去；WAIT_FOR_SECONDS 原地等（**这就是"罚站"**）；APPEAR_AT_POS 换位出现；
        # DISAPPEAR 离场。实测全库：MOVE 2648 / WAIT_FOR_SECONDS 637 / APPEAR_AT_POS 106 / DISAPPEAR 106。
        way, waits, waits_at, appear = [], 0.0, [], False
        wseq = []
        last_pt = a
        for c in (r.get("checkpoints") or []):
            ty = c.get("type")
            pos = c.get("position") or {}
            if ty == "WAIT_FOR_SECONDS":
                t = float(c.get("time") or 0)
                waits += t
                # ⚠️ WAIT 的 `position` **不可信**：实测 626 处里 624 处是 (0,0)。
                # 停留发生在**链里上一个途经点**（看序列：MOVE(5,9) → WAIT 12s → MOVE(5,6) ⇒ 在 (5,9) 等）。
                if t:
                    waits_at.append(last_pt)
                    wseq.append((last_pt, t))
            elif ty == "MOVE" and pos:
                last_pt = rp(pos)
                way.append(last_pt)
            elif ty == "APPEAR_AT_POS" and pos:
                appear = True
                last_pt = rp(pos)
                way.append(last_pt)
        if mode == "FLY":
            # **用 motionMode 判飞行**（原来是"BFS 失败才当飞行"，那是间接推断，会漏也会误判）
            fly_starts.add(a)
            p = RoutePath(ES.line_cells(a, b)) if ES else RoutePath([a, b])
            p.approx = True
            p.mode = "FLY"
        else:
            pts = [a] + way + [b]
            seg_all, ok = [], True
            for i in range(len(pts) - 1):
                seg = bfs(pts[i], pts[i + 1])
                if not seg:
                    ok = False
                    break
                seg_all += (seg if i == 0 else seg[1:])
            p = RoutePath(seg_all) if ok and len(seg_all) > 1 else None
            if not p:
                # 真跨不过去（例：第 3 列整列不可走）→ 退回直线近似并标注，不静默丢路线
                fly_starts.add(a)
                p = RoutePath(ES.line_cells(a, b)) if ES else RoutePath([a, b])
                p.approx = True
            p.mode = mode
        p.waits = waits
        p.waits_at = tuple(waits_at)
        p.waits_seq = tuple(wseq)
        p.appear = appear
        p.route_index = ri          # 本条路线在 raw `routes[]` 里的下标 = 出怪表 SPAWN.routeIndex
        ri2path[ri] = (a, b, p)
        # ⚠️ 默认按**起点格**去重：同一出发点的多条路线只留第一条（画图/payload 够用）。
        # **去重只影响"返回的清单"，不影响 `ri2path` 与 wave 的到达时刻** —— 同一出发点下的
        # 不同路线走向可能完全不同（实测 PA-1：起点 (1,0) 有 4 条，11 步 vs 21 步；罚站分布也不同），
        # 按起点去重会把别的路线的路径/罚站张冠李戴（2026-09-19 对账时踩到）。
        if not all_routes and any(x[0] == a for x in routes):
            continue
        routes.append((a, b, p))
    try:
        _en = json.load(open(PROJ / "data/_enemy_db.json", encoding="utf-8"))["enemies"]
        ENAME = {}
        for e in _en:
            v = e.get("Value") or []
            if v and isinstance(v, list):
                nm = ((v[0].get("enemyData") or {}).get("name") or {}).get("m_value")
                if nm:
                    ENAME[e.get("Key")] = nm
    except Exception:
        ENAME = {}
    waves = []
    # 到达时间：移速来自 data/_enemy_db.json（本地就有，见 tools/enemy_speed.py 的换算依据）
    speeds = ES.load_enemy_speeds() if ES else None
    for w in d.get("waves", []):
        for fr in w.get("fragments", []):
            for act in fr.get("actions", []):
                if act.get("actionType") == "SPAWN":
                    rt = (d["routes"][act["routeIndex"]]
                          if isinstance(act.get("routeIndex"), int)
                          and 0 <= act["routeIndex"] < len(d["routes"]) else {})
                    st = rp(rt.get("startPosition") or {})
                    en = rp(rt.get("endPosition") or {})
                    # **按 raw routeIndex 取该路线自己的路径**（不能用 path_by_start：同一出发点的
                    # 多条路线走向可能完全不同，按起点取会张冠李戴 —— 2026-09-19 对账时踩到）
                    hit = ri2path.get(act.get("routeIndex"))
                    path = hit[2] if hit else None
                    approx = bool(getattr(path, "approx", False)) if path else (st in fly_starts)
                    if not path and ES:
                        path = ES.line_cells(st, en)
                    arr = (ES.calc_arrival(act.get("preDelay") or 0, path, act.get("key"),
                                           speeds=speeds, approx=approx)
                           if ES else {"arrival": None, "reason": "no_engine"})
                    # **停留时间已由 `calc_arrival` 加进到门时刻**（2026-09-16 口径统一到一处）。
                    # 这里**不再自己加一次** —— 否则 `RoutePath.waits` 会被算两遍。
                    # 背景：`WAIT_FOR_SECONDS` 实测 637 处、最长 240s，涉及 23 关；
                    # 原来一律忽略 ⇒ 到门时刻平均低估 25.5s、最大 150s（判漏随之偏悲观）。
                    # ⚠️ 这里必须用**换算后的** st：写成 `sp.get("col")` 会拿到上面 routes 循环泄漏下来的
                    # 旧变量（Python 函数作用域），于是每条 wave 的"起点"都变成最后一条 route 的起点，
                    # 后果是 `_arrival_at` 全部返回 None（时间窗集体消失）。实测踩到过。
                    waves.append({"start": [st[0], st[1]], "t": act.get("preDelay"),
                                  "wait": float(getattr(path, "waits", 0) or 0),
                                  "waits_at": [list(c) for c in (getattr(path, "waits_at", ()) or ())],
                                  "waits_seq": [[list(c), s]
                                                for c, s in (getattr(path, "waits_seq", ()) or ())],
                                  "mode": getattr(path, "mode", None),
                                  "n": act.get("count", 1), "route": act.get("routeIndex"),
                                  "key": act.get("key"),
                                  "name": ENAME.get(act.get("key") or "", (act.get("key") or "")[:18]),
                                  "arrival": arr.get("arrival"), "why": arr.get("reason"),
                                  "speed": arr.get("speed_used"), "approx": approx,
                                  # 该路线**按行进顺序**的格列表：下游（job_judge 判漏/simulate）直接用它，
                                  # 免得各自再按"起点"去查表 —— 那正是 2026-09-19 那批 bug 的共同成因
                                  "cells": [list(c) for c in (path or [])]})
    return W, H, key, bt, routes, waves


BOX_PATH = PROJ / "data/operbox_batch/emu2.json"


_BOX_CACHE = {}


def load_box(path=None):
    """box（练度）：name -> {elite, level, rarity}。缺则空。

    ⚠️ **带缓存**：这个函数在内层循环里被反复调用（ranges_payload→box_ops、
    判漏/决斗每个配对都要取精英和等级），不缓存会把每次调用变成一次 252 条 JSON 读取 ——
    实测这是 792 次逐秒决斗的主要耗时来源（单次 0.97s → 缓存后毫秒级）。
    """
    key = str(pathlib.Path(path) if path else BOX_PATH)
    if key in _BOX_CACHE:
        return _BOX_CACHE[key]
    try:
        d = json.load(open(key, encoding="utf-8"))
        own = d.get("own") or d.get("own_opers") or []
        out = {o["name"]: o for o in own if isinstance(o, dict) and o.get("name")}
    except Exception:
        out = {}
    _BOX_CACHE[key] = out
    return out


def box_ops(box, names):
    """只保留作业用到的干员，压成 {name: {e, lv, r}} 注入前端。"""
    out = {}
    for n in names:
        o = box.get(n)
        if o:
            out[n] = {"e": o.get("elite"), "lv": o.get("level"), "r": o.get("rarity")}
    return out


def load_ranges(jobs, extra_names=()):
    """射程表：MAA battle_data.json 的 chars[name].rangeId + ranges[rangeId] 偏移列表。

    两条经数据实证的结论（别凭直觉改）：
      1) rangeId 是**三元组 = 精英阶段 [E0, E1, E2]**，不是四个方向；无精二的 3★ 干员
         第 3 项回落成 E0（实测 4 例）。所以画哪一阶段要按 box 的 elite 取 —— 写死 [0]
         会给 E2 干员少画一格（本局 逻各斯/圣聆初雪/白面鸮 全中）。
      2) 全部 73 个射程都关于朝向轴对称（y 对称，实测 73/73），所以「旋转 90°/转置/镜像」
         三种摆法结果**完全等价**，不需要实测校准；唯一还成立的假设是「+x = 前方」，
         由数据自身佐证（长边与 nose 都朝 +x；近战 1-1 = 自身格 + 前方格）。

    `extra_names`：工作区页面要把**整个 box** 的射程也带上 —— 编辑器里换人（替代干员）
    和加载历史版本都可能用到候选作业里没出现过的干员，只有作业用到的那几个会让
    "换个人就没射程可看"（这是编辑器功能性缺口，不是画多了）。
    """
    try:
        bd = json.load(open(PROJ / "services/maa/instances/2/resource/battle_data.json", encoding="utf-8"))
        all_tri = {}
        for c in (bd.get("chars") or {}).values():
            tri = c.get("rangeId")
            if c.get("name") and isinstance(tri, list) and tri:
                all_tri[c["name"]] = list(tri)
        names = set(extra_names)
        for jb in jobs:
            for o in (jb.get("opers") or []):
                if isinstance(o, dict) and o.get("name"): names.add(o["name"])
            for a in (jb.get("actions") or []):
                if a.get("name"): names.add(a["name"])
        tri_used = {n: all_tri[n] for n in names if n in all_tri}
        used = {r for t in tri_used.values() for r in t}
        return tri_used, {r: (bd["ranges"].get(r) or []) for r in used}
    except Exception:
        return {}, {}


def ranges_payload(jobs, box_path=None, extra_names=()):
    """(name2tri, ranges, ops) —— 前端画射程所需的全部数据，两个调用点共用。"""
    n2t, rng = load_ranges(jobs, extra_names)
    names = set(extra_names)
    for jb in jobs:
        for o in (jb.get("opers") or []):
            if isinstance(o, dict) and o.get("name"): names.add(o["name"])
        for a in (jb.get("actions") or []):
            if a.get("name"): names.add(a["name"])
    return n2t, rng, box_ops(load_box(box_path), names)


def conflicts(job, stage=None):
    warns, seen = [], {}
    for a in job["actions"]:
        if a["type"] != "Deploy" or not a.get("location"):
            continue
        loc = tuple(a["location"])
        if loc in seen:
            warns.append(f"位置重叠：{a['name']} 与 {seen[loc]} 同在 [{loc[0]},{loc[1]}]")
        seen[loc] = a["name"]
    cover, why = device_info(stage)[1:3]
    if cover and not any(tuple(x["location"]) in tuple(cover) for x in job["actions"]
                         if x["type"] == "Deploy" and x.get("location")):
        warns.append("无人站 %s：打不到%s" % ("/".join("[%d,%d]" % tuple(c) for c in cover), why))
    return warns


def rel_src(p):
    """作业身份：仓库内用相对 posix 路径（前端按它给编辑分槽），仓库外用绝对路径。"""
    try:
        return pathlib.Path(p).resolve().relative_to(PROJ).as_posix()
    except Exception:
        return pathlib.Path(p).as_posix()


def pool_dirs(stage):
    """这一关的池子目录：原池在前、编辑产物池在后（导出默认落后者）。"""
    return (PROJ / "data" / "copilot" / stage, PROJ / "data" / "copilot" / (stage + "_edited"))


def scan_jobs(stage):
    """扫这一关的全部作业文件 —— 生成一次全烘进下拉，不必为了换一份作业重跑命令。

    三个来源（缺一就会漏掉最该看的那批）：
      ① data/copilot/<stage>/            —— 下载/筛选来的原池
      ② data/copilot/<stage>_edited/     —— 页面上改完收编回来的（--ingest 落这里）
      ③ data/copilot/*/<stage>_*.json    —— 别的目录里以本关名开头的作业。
         这一条是关键：推导/编排出来的版本叫 <stage>_derived_v*.json，住在 ga_derived/ 里，
         只扫前两个目录会让"自己算过的那些"一份都进不了下拉。
    """
    seen, out = set(), []

    def add(paths):
        for p in sorted(paths):
            if p.is_file() and p.resolve() not in seen:
                seen.add(p.resolve())
                out.append(p)

    for d in pool_dirs(stage):
        if d.is_dir():
            add(d.glob("*.json"))
    root = PROJ / "data" / "copilot"
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        if d.name in (stage, stage + "_edited"):
            continue                     # 前两步已收
        add(d.glob("%s_*.json" % stage))
    return out


def professions():
    CT = json.load(open(PROJ / "data/_char_table.json", encoding="utf-8"))
    return {v["name"]: v.get("profession") for v in CT.values()}


def range_cells(act, n2t, rng, ops, W, H):
    """该部署动作覆盖的格集合（含**自身格**——站在路径上就是挡住）。

    Python 侧**唯一实现**：`_axis_check.py`、`cost_model.py` 都从这里取，
    页面里的 `rangeOf()`/`drawRange()` 是它的 JS 对应版（浏览器里没法复用 Python）。
    ops 是 `box_ops()` 的输出（键是 `e`）；**不要**直接传 `load_box()` 的原始条目
    （那里键是 `elite`，取 `e` 会恒为 None ⇒ 所有人都按 E0 射程算，曾踩过）。
    """
    loc = act.get("location")
    if not loc:
        return set()
    x0, y0 = loc
    out = {(x0, y0)} if (0 <= x0 < W and 0 <= y0 < H) else set()
    tri = n2t.get(act.get("name"))
    if not tri:
        return out                       # 占位符/组名：只有站位，没有射程
    op = ops.get(act.get("name")) or {}
    e = op.get("e")
    ph = max(0, min(len(tri) - 1, 0 if e is None else e))
    rid = tri[ph] or tri[0]
    d = act.get("direction")
    for dx, dy in (rng.get(rid) or []):
        if d == "Left":
            rx, ry = -dx, dy
        elif d == "Up":
            rx, ry = dy, dx
        elif d == "Down":
            rx, ry = -dy, -dx
        else:
            rx, ry = dx, dy
        x, y = x0 + rx, y0 + ry
        if 0 <= x < W and 0 <= y < H:
            out.add((x, y))
    return out


# MAA copilot 的动作类型（权威清单见 data/_copilot_schema.md L65）。
# **不在这个集合里也一律不丢** —— 未知类型原样带走并标 `unknown`，宁可页面上不认识它，
# 也不能在人保存一版的时候把它销毁（丢数据是不可逆的，页面显示不认识是可修的）。
KNOWN_ACTIONS = ("Deploy", "部署", "Skill", "技能", "Retreat", "撤退", "SpeedUp",
                 "BulletTime", "SkillUsage", "Output", "SkillDaemon", "MoveCamera",
                 "ResetStopwatch")


def normalize_job(j, prof, src=None):
    """一份 MAA 作业 → 前端用的 {title, src, actions, opers, groups}。

    两条生成路径（main / render_for）共用。costs/kills/preDelay 是 MAA 动作自带的
    条件字段，前端右列要显示它们 —— 曾经只有 main() 注入，render_for() 不注入，
    导致同一条"起跑前体检"路径画出来的图少了这几列。

    **动作按原顺序、逐字段保留**（每个动作带 `raw` = 原始字典，页面导出时写回）：
    只留 Deploy/Retreat/Skill 会把 `BulletTime`（1/5 速）、`ResetStopwatch`（帧级计时）、
    `SkillUsage`、`Output`、`MoveCamera` 这些整条丢掉 —— 其中 BulletTime/ResetStopwatch
    正是**帧级技能轴**的机制（见记忆「MAA 作业帧级能力」），丢了等于把最精确的那部分打法扔掉。
    """
    acts = []
    for a in j.get("actions") or []:
        t, nm = a.get("type"), a.get("name", "")
        if t not in KNOWN_ACTIONS:
            d = dict(a)
            d["raw"] = dict(a)
            d["unknown"] = True          # 页面显示但不认识 → 只搬运、不改写
            acts.append(d)
            continue
        if t in ("Deploy", "部署"):
            # 注意：MAA 作业里这个字段是 snake_case 的 pre_delay（实测 272 份作业出现 263 次，
            # camelCase 一次都没有）—— 只读 preDelay 会让前端那个 "+Ns" 角标永远是空的。
            pd = a.get("preDelay")
            if pd is None:
                pd = a.get("pre_delay")
            acts.append({"type": "Deploy", "name": nm, "location": a.get("location"),
                         "direction": norm_dir(a.get("direction")), "profession": prof.get(nm),
                         "costs": a.get("costs"), "kills": a.get("kills"),
                         "preDelay": pd, "raw": dict(a)})
        elif t in ("Retreat", "撤退", "Skill", "技能", "SkillDaemon", "SpeedUp",
                   "BulletTime", "SkillUsage", "Output", "MoveCamera", "ResetStopwatch"):
            # **逐字段保留**（2026-09-16 用户质疑"框架动作边界"时查出来的真 bug）：
            # 池子里 56 份作业带定时 `Skill`、60 份带 `Retreat`，而且带战术条件 ——
            # `cost_changes`/`kills`/`pre_delay`/`post_delay`/`rear_delay`，`doc` 里甚至写着
            # "本次开技能的时间为红线走到火陈右侧2格左右…"。原来这里只留 (type,name)，
            # 页面导出又只 emit Deploy ⇒ **人保存一版就把整条技能/撤退轴丢掉了**。
            # 现在：保留原字典（页面只改它能改的字段），`raw` 供导出原样写回。
            d = dict(a)
            d["type"] = "Skill" if t == "技能" else ("Retreat" if t == "撤退" else t)
            d["name"] = nm
            d["raw"] = dict(a)           # 导出时写回：条件字段/kills/cost_changes/doc 一个不丢
            acts.append(d)
    title = (j.get("doc") or {}).get("title")
    if not title:
        title = pathlib.Path(src).stem if src else "作业"
    return {"title": title, "src": rel_src(src) if src else "", "actions": acts,
            "opers": j.get("opers") or [], "groups": j.get("groups") or [],
            # **作业级元数据：页面导出必须原样写回**（2026-09-16 用户现场抓到）。
            # 页面里 `jobJson()` 曾硬编码 `minimum_required:'v4.0.0'` 和一段固定 doc ——
            # 实测每存一次就把 v6.0.0 降成 v4.0.0、把 doc.details 换成固定文案。
            "meta": {"version": j.get("version"), "stage_name": j.get("stage_name"),
                     "minimum_required": j.get("minimum_required"),
                     "doc": j.get("doc") or {}}}


def load_jobs(files, prof, stage=None):
    """读一批作业；无 actions 的跳过（口径：valid_job = 有 actions）。返回 (jobs, skipped)。"""
    jobs, skipped = [], []
    for f in files:
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            skipped.append((str(f), "读取失败: %s" % e))
            continue
        if isinstance(j, list):
            # 顶层是数组 = 作业集/导出 dump（实测 data/copilot/sr_ex05_jobs_dump.json 是 20 份作业的数组）。
            # **不能取首元素**：那会把"第 20 份"当成"这一份"来画（下游还会 AttributeError 崩掉）。
            # `batch_index.index_jobs()` 取首元素是它的口径（统计需求用），这里要画图，故明确跳过。
            skipped.append((str(f), "顶层是数组（作业集/dump），不是单份作业"))
            continue
        jb = normalize_job(j, prof, f)
        if not jb["actions"]:
            skipped.append((str(f), "无 actions"))
            continue
        jb["warns"] = conflicts(jb, stage)
        jobs.append(jb)
    return jobs, skipped


def build_payload(stage, lv, jobs, box_path=None):
    """前端数据包。两条生成路径共用 —— 字段集合必须一致，否则产物行为会分叉。

    `box_path` 指定"按哪个账号的练度算"（工作区里每关生成的页面必须用那个账号的 box，
    否则射程按 E0、费用按错阶段 —— 结论会静默偏差）。给了就写进模块全局 `BOX_PATH`：
    `duel_model.duel()` 内部走的是 `load_box()`，它读的就是这个全局，不设会拿默认 box 算决斗。
    """
    global BOX_PATH
    if box_path:
        BOX_PATH = pathlib.Path(box_path)
    W, H, key, bt, routes, waves = lv
    tiles = [{"x": x, "y": y, "k": key(x, y), "b": bt(x, y)} for y in range(H) for x in range(W)]
    paths = [p for _, _, p in routes]
    # 工作区页面：把整个 box 的射程/练度/职业也带上（换人 + 读历史版本都要用）
    box_names = sorted(load_box(box_path)) if box_path else []
    _n2t, _rng, _ops = ranges_payload(jobs, box_path, box_names)
    _prof = professions()
    _prof_used = {n: _prof.get(n) for n in (set(_ops) | set(_n2t)) if _prof.get(n)}
    d_at, d_cover, d_why = device_info(stage)
    # —— 费用/判漏：对每份作业算"每个部署最早落地时刻"，再判每条出怪来不来得及 ——
    opts = CM.load_level_options(stage) if CM else None
    if opts:
        _costs, _elites = CM.load_char_costs(), CM.load_elites(box_path)
        path_by_start = {tuple(a): p for a, b, p in routes}
        lane_cells = {}
        for w in waves:
            k2 = tuple(w["start"])
            if k2 not in lane_cells:
                lane_cells[k2] = {"cells": set(tuple(c) for c in (path_by_start.get(k2) or [])),
                                  "spawns": []}
            lane_cells[k2]["spawns"].append({"key": w.get("key"), "arrival": w.get("arrival")})

        for jb in jobs:
            jb["_skills"] = {o["name"]: o.get("skill") for o in (jb.get("opers") or [])}
            dts, _ = CM.simulate_deploy_times(jb["actions"], opts, _costs, _elites)
            dep = [a for a in jb["actions"] if a.get("type") == "Deploy" and a.get("name")]
            for a, dd in zip(dep, dts):
                dd["loc"] = a.get("location")
            jb["dts"] = dts
            verd = []
            for w in waves:
                arr = w.get("arrival")
                cells = path_by_start.get(tuple(w["start"])) or []
                if arr is None or not cells:
                    verd.append(None)
                    continue
                cells = set(tuple(c) for c in cells)
                cand = [d for d in dts if d.get("time") is not None and d["time"] <= arr
                        and d.get("loc")]
                stand = [d for d in cand if tuple(d["loc"]) in cells]
                # 射程覆盖（把 dts 与 dep 对齐后算）
                cov = []
                for d in cand:
                    act = next((a for a, x in zip(dep, dts) if x is d), None)
                    if act and range_cells(act, _n2t, _rng, _ops, W, H) & cells:
                        cov.append(d)
                pick = (stand or cov or [None])[0]
                verd.append({"v": "存疑" if not cov else ("已挡" if stand else "可打"),
                             "by": pick["name"] if pick else None,
                             "t": pick["time"] if pick else None})
            jb["verd"] = verd
            # 每个**挡位** vs 它那条路上最早到门的敌人：逐秒决斗。
            # ⚠️ 这个结论**不依赖系数**（系数只管到门时刻，不管能不能守住）⇒ 是硬一档的。
            if DM:
                for a, dd in zip(dep, dts):
                    loc = a.get("location")
                    if not loc or dd.get("time") is None:
                        continue
                    for st2, ln2 in lane_cells.items():
                        if tuple(loc) not in ln2["cells"]:
                            continue
                        if a.get("profession") in ("CASTER", "MEDIC", "SNIPER", "SUPPORT"):
                            continue                      # 高台不阻挡
                        first = min((x for x in ln2["spawns"] if x["arrival"] is not None),
                                    key=lambda x: x["arrival"], default=None)
                        if not first:
                            continue
                        du = DM.duel(first["key"], a["name"], t_deploy=dd["time"],
                                     t_enter=max(dd["time"], first["arrival"]),
                                     skill_idx=(jb.get("_skills") or {}).get(a["name"]),
                                     mastery=9)
                        if du.get("result"):
                            dd["duel"] = {"enemy": du.get("enemy"), "result": du["result"],
                                          "t": du.get("t_death"),
                                          "hp_left": du.get("our_hp_left"),
                                          "hp_max": du.get("our_hp_max"),
                                          "enemy_left": du.get("enemy_hp_left")}
                        break
    # 出怪口/门：**两个来源取并集**。
    # ① 关卡文件的 `tile_start`/`tile_flystart`/`tile_end`；
    # ② **routes 的起终点**（换算后就是真实出怪口/门）。
    # 为什么必须并上 ②：实测 `act54side_ex03` 的关卡文件里 **tile_start=0**（MAA 却标了多处 isStart），
    # 只靠 ① 那关的出怪口一个都画不出来；而 routes 的起点不仅有，还带 `motionMode` 的飞行标记。
    _sp, _dr = {}, set()
    for t in tiles:
        if t["k"] in ("tile_start", "tile_flystart"):
            _sp[(t["x"], t["y"])] = (t["k"] == "tile_flystart")
        elif t["k"] == "tile_end":
            _dr.add((t["x"], t["y"]))
    for a, b, p_ in routes:
        _sp.setdefault(a, getattr(p_, "mode", None) == "FLY")
        _dr.add(b)
    return {"stage": stage, "W": W, "H": H, "tiles": tiles, "paths": paths,
            "spawns": [{"x": x, "y": y, "fly": f} for (x, y), f in sorted(_sp.items())],
            "doors": [{"x": x, "y": y} for x, y in sorted(_dr)],
            "waves": waves, "jobs": jobs, "opts": opts,
            "box": (rel_src(BOX_PATH) if box_path else ""),
            "devices": [list(d) for d in d_at], "deviceCover": [list(c) for c in d_cover],
            "deviceWhy": d_why,
            "ranges": _rng, "name2tri": _n2t, "ops": _ops, "prof": _prof_used,
            "pathTiles": sorted({"%d,%d" % (pt[0], pt[1]) for pa in paths if pa for pt in pa})}


def out_path(stage, out=None):
    return pathlib.Path(out) if out else PROJ / "data/visualization" / ("%s.html" % stage)


def check_page_js(html_text: str, name: str = "page") -> str | None:
    """页面里的 JS 做一次语法检查（有 node 才做）。返回错误描述或 None。

    页面 JS 是 Python 字符串拼出来的：少一个括号 ⇒ 整个页面白屏（实测踩过一次，
    "Unexpected token ':'"，而症状只是"下拉框不存在"）。所以生成时就卡一道 ——
    没有 node 的环境自动跳过（不阻塞生成，只是少一道网）。
    """
    import shutil as _sh, subprocess as _sp, tempfile as _tf
    node = _sh.which("node")
    if not node:
        return None
    try:
        i, j = html_text.index("<script>"), html_text.rindex("</script>")
        js = html_text[i + len("<script>"):j]
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(js)
            tmp = f.name
        r = _sp.run([node, "--check", tmp], capture_output=True, text=True, timeout=30)
        if r.returncode:
            return (r.stderr or "").strip().splitlines()[0:6]
    except Exception:
        return None
    finally:
        try:
            pathlib.Path(tmp).unlink()
        except Exception:
            pass
    return None


def write_html(data, out):
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = (HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__LW__", str(LW)).replace("__LH__", str(LH))
            .replace("__CLS__", json.dumps(CLS)).replace("__FACE__", json.dumps(FACE)))
    err = check_page_js(text, out.name)
    if err:
        print("   ! 页面 JS 语法检查未通过（浏览器打开会是白屏）：%s" % err)
    out.write_text(text, encoding="utf-8")
    return out


def ingest(src, stage=None):
    """把导出的 edited JSON 收进 data/copilot/<stage>_edited/ —— 导出→导回的回路后半段。

    浏览器只能"下载"，写不进仓库目录（页面由静态 http.server 托管，没有写接口），
    所以落地这一步必须由命令行做：收编之后下次生成自动把它扫进下拉。
    """
    p = find_latest(stage) if src == "" else pathlib.Path(src)
    if not p.is_file():
        raise SystemExit("找不到要收编的文件：%s" % p)
    j = json.load(open(p, encoding="utf-8"))
    st = stage or j.get("stage_name") or STAGE
    d = PROJ / "data" / "copilot" / (st + "_edited")
    d.mkdir(parents=True, exist_ok=True)
    dest = d / p.name
    shutil.copy2(p, dest)
    return st, dest


def find_latest(stage=None):
    """下载目录里最新的 <stage>__*.json（即排轴可视化的导出命名）。"""
    dl = pathlib.Path.home() / "Downloads"
    pat = ("%s__*" % stage) if stage else "*__*"
    cands = sorted(dl.glob(pat + ".json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise SystemExit("下载目录 %s 里没有 %s.json —— 先在页面上点「导出当前作业 JSON」" % (dl, pat))
    return cands[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description="排轴可视化：生成单文件 HTML")
    ap.add_argument("jobs", nargs="*", help="作业 JSON（不给则扫 --stage 的池子）")
    ap.add_argument("--stage", default=None, help="关卡名（默认 %s）" % STAGE)
    ap.add_argument("--out", default=None, help="输出路径（默认 data/visualization/<stage>.html）")
    ap.add_argument("--ingest", nargs="?", const="", default=None,
                    help="把导出的 edited JSON 收进 data/copilot/<stage>_edited/ 后重画；"
                         "不给路径 = 取下载目录里最新的 <stage>__*.json")
    ap.add_argument("--workspace", default=None, metavar="ACCOUNT",
                    help="工作区模式：一次生成一整个活动（配 --activity），账号 id 对应 "
                         "data/operbox_batch/<账号>.json")
    ap.add_argument("--activity", default=None, help="工作区模式的活动 id，如 act54side")
    ap.add_argument("--limit", type=int, default=6, help="工作区模式：每关挑几份候选（默认 6）")
    ap.add_argument("--include-derived", action="store_true",
                    help="工作区模式：把自产件（我们自己算的轴）也算进候选")
    ap.add_argument("--no-fetch", action="store_true", help="工作区模式：缺关卡数据也不联网拉")
    ap.add_argument("--list-workspaces", action="store_true", help="列工作区账号/活动")
    a = ap.parse_args(argv)

    if a.workspace or a.list_workspaces or (a.activity and not a.jobs and not a.stage):
        import workspace as WSP                     # 工作区逻辑独立成模块，这里只做入口
        if a.list_workspaces or not a.workspace:
            return WSP.main(["--list"] if a.list_workspaces else [])
        wargv = ["--account", a.workspace, "--activity", a.activity or "", "--limit", str(a.limit)]
        if a.include_derived:
            wargv.append("--include-derived")
        if a.no_fetch:
            wargv.append("--no-fetch")
        if a.stage:
            wargv += ["--stage", a.stage]
        return WSP.main(wargv)

    stage = a.stage or STAGE
    if a.ingest is not None:
        stage, dest = ingest(a.ingest, a.stage)
        print("[ingest] ->", rel_src(dest))

    files = [pathlib.Path(f) for f in a.jobs] or scan_jobs(stage)
    jobs, skipped = load_jobs(files, professions(), stage)
    for f, why in skipped:
        print("   跳过 %s（%s）" % (f, why))
    if not jobs:
        raise SystemExit("没有可用作业：%s 池子里没有带 actions 的 JSON（%s）"
                         % (stage, pool_dirs(stage)[0]))
    p = write_html(build_payload(stage, level(stage), jobs), out_path(stage, a.out))
    print("->", rel_src(p), "(%d KB)  关卡 %s ｜ %d 份作业" % (p.stat().st_size // 1024, stage, len(jobs)))
    for jb in jobs:
        print("   %s: %d 动作 | 冲突: %s" % (jb["title"], len(jb["actions"]),
                                            "；".join(jb["warns"]) or "无"))




HTML = r"""<!doctype html><meta charset="utf-8"><title>排轴可视化</title>
<style>
*{box-sizing:border-box}
body{background:#161a1e;color:#d8dee6;font:13px/1.55 system-ui,"Microsoft YaHei";margin:0;padding:0}
header{position:sticky;top:0;z-index:50;background:#161a1e;border-bottom:1px solid #242c33;
       padding:12px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header .t{font-size:14px;font-weight:600;color:#8ac;white-space:nowrap}
main{padding:14px 16px;display:flex;gap:12px;align-items:flex-start;flex-wrap:nowrap;overflow-x:auto}
.col{display:flex;flex-direction:column;gap:12px}
h3{margin:0 0 8px;font-size:13px;font-weight:600;color:#8ac;letter-spacing:.3px}
h3{margin:12px 0 5px;font-size:14px;color:#8ac}
#wrap{display:none}
.cell{width:__LW__px;height:__LH__px;border:1px solid #2c3138;position:absolute;box-sizing:border-box;font-size:10px;color:#0006;padding:1px}
.floor{background:#d4c5a9}.wallrow{background:#8b7355}.forb{background:#3a3f45}.fence{background:#b98f5a}
.mapbox{position:relative;border:1px solid #444}
svg{position:absolute;left:0;top:0;pointer-events:none;z-index:2}
.mk{position:absolute;transform:translate(-50%,-50%);font-size:15px;z-index:3}
.op{position:absolute;transform:translate(-50%,-50%);text-align:center;font-size:11px;z-index:5;line-height:1.05;white-space:nowrap}
.op b{font-size:17px;display:block}
.op span{background:#000b;padding:0 3px;border-radius:3px;white-space:nowrap;display:inline-block}
ul{margin:4px 0;padding-left:18px}li{margin:3px 0;padding:0;border-top:2px solid transparent;display:flex;gap:6px;align-items:flex-start}
li .no{flex:0 0 22px;text-align:right;color:#e6b422;font-weight:700}
li .txt{flex:1 1 auto;min-width:0}
#tlCol li{font-size:11px}   /* 到门时刻+判漏接上去后行会变长，小一号字避免整列被撑成两三倍高 */
li .mv{flex:0 0 auto}
.warn{color:#ff6b6b}.ok{color:#6bd47a}
.arr{color:#6bd47a;font-weight:600}.arr.miss{color:#e6b422}
.vd{margin-left:4px;font-weight:700}.vd.good{color:#6bd47a}.vd.warn2{color:#e6b422}
.apx{color:#93a2b0;font-style:normal;font-size:11px}
.p{margin:2px 0;display:flex;align-items:center;gap:6px}
.cond{color:#e6b422;font-size:11px;margin-left:4px}
.op.sel span{background:#e6b422;color:#111}
.op.sel b{text-shadow:0 0 6px #e6b422}
.mv{background:#2b333c;color:#cfd6dd;border:0;border-radius:4px;padding:0 4px;cursor:pointer;font-size:11px}
.mv:hover{background:#3a444f}
.tip{color:#93a2b0;font-size:12px;margin:4px 0 0}
.ord{position:absolute;left:-14px;top:-6px;background:#e6b422;color:#111;border-radius:50%;
     width:14px;height:14px;line-height:14px;font-size:9px;font-style:normal;text-align:center}
select{background:#242c34;color:#d8dee6;border:1px solid #39434d;border-radius:6px;padding:6px 10px;
       font:13px system-ui;min-width:360px;cursor:pointer}
button{background:#2f9e78;color:#fff;border:0;border-radius:6px;padding:6px 12px;font:13px system-ui;cursor:pointer}
button:hover{background:#37b489}
.card{padding:0}
.card h3{margin:0 0 6px}
#jobSel{margin-bottom:2px}
.dot{width:10px;height:10px;border-radius:50%}
/* 工作区横条：只有工作区生成的页面才有（D.ws 注入），单关页面上它一直 display:none */
#wsbar{background:#1b2026;border-bottom:1px solid #242c33;padding:8px 16px;display:none;
       align-items:center;gap:12px;flex-wrap:wrap;font-size:13px}
#wsbar.on{display:flex}
#wsbar a{color:#8ac;text-decoration:none}
#wsbar a:hover{text-decoration:underline}
#wsbar select{min-width:0;padding:3px 6px;font:12px system-ui;background:#242c34;color:#d8dee6;
              border:1px solid #39434d;border-radius:5px;max-width:220px}
#wsbar button{padding:4px 10px;font-size:12px}
#wsbar .sp{flex:0 0 100%}
#wsbar textarea{flex:1 1 100%;min-height:48px;background:#12161a;color:#d8dee6;
                border:1px solid #39434d;border-radius:6px;font:12px/1.5 system-ui;padding:6px 8px}
#wsMsg{font-size:12px}
#wsVNote{flex:1 1 220px;min-width:150px;background:#12161a;color:#d8dee6;border:1px solid #39434d;
         border-radius:5px;padding:4px 8px;font:12px system-ui}
/* 修改树（版本链）：节点 = 一次改动，缩进表示深度 */
#wsVersions{max-width:380px}
#wsVersions ul{padding-left:0;list-style:none}
#wsVersions li{border-top:1px solid #242c33;padding:4px 0 5px;display:block}
#wsVersions li:first-child{border-top:0}
#wsVersions .vh{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
#wsVersions .vchanges{font-size:11px;color:#cfd6dd;margin:4px 0 0 10px}
#wsVersions .vchanges div{margin:1px 0}
#wsVersions .vnote{color:#93a2b0;font-size:11px;margin:3px 0 0 10px}
@media (max-width: 1100px){
  main{flex-wrap:wrap}
  .col{flex:1 1 100% !important;max-width:none}
  #map{transform-origin:top left}
}
@media (max-width: 720px){
  header{padding:8px 10px;gap:8px}
  header .tip{display:none}
  main{padding:8px 10px}
  select{min-width:0;width:100%}
  #map{transform:scale(.62)}          /* 窄屏把地图整体缩到 62%，仍可点（缩放后坐标由浏览器换算） */
  #mapWrapInner{height:calc(406px * .62)}
}
</style>
<header id="top"><span class="t">排轴可视化</span><span class="tip">点干员=选中（高亮）→ 再点地图格子=移动过去 ｜ 点干员本体=切朝向 ｜ 列表 ↑↓ = 调部署顺序</span></header>
<div id="wsbar"></div>
<main>
  <div class="col" style="flex:0 0 380px" id="tlCol"></div>
  <div class="col" style="flex:1 1 auto;min-width:0"><div id="mapWrapInner" style="overflow:auto">
      <h3>地图（浅=地面 深=高台 灰=虚空；点干员可切换朝向）</h3>
      <div id="map" class="mapbox"></div><div id="legend" style="margin-top:8px"></div></div></div>
  <div class="col" style="flex:0 0 330px" id="side"></div>
</main>
<script>
const D=__DATA__, LW=__LW__, LH=__LH__, CLS=__CLS__, FACE=__FACE__;
const map=document.getElementById('map');
map.style.width=(D.W*LW)+'px'; map.style.height=(D.H*LH)+'px';
// 配色按**能不能部署/是不是高台**，不按 tileKey：
// 实测 `tile_replace_wall` 是 RANGED(高台)但 tileKey 里带 wall 以外的名字 → 按 tileKey 判会画成地面
// （法师站上去看着像站在地上）。虚空=forb；RANGED=高台；其余=地面（围栏单独一色）。
const cls=t=>(t.k==='tile_forbidden'?'forb':(t.b==='RANGED'?'wallrow':(String(t.k).indexOf('tile_fence')>=0?'fence':'floor')));
D.tiles.forEach(t=>{const d=document.createElement('div');d.className='cell '+cls(t);
 d.style.left=(t.x*LW)+'px';d.style.top=(t.y*LH)+'px';   // 屏幕行 = 网格行（y 直接下行）
 d.title='['+t.x+','+t.y+'] '+t.k+' build='+t.b;map.appendChild(d);});
const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');
svg.setAttribute('width',D.W*LW);svg.setAttribute('height',D.H*LH);
D.paths.forEach((p,i)=>{if(!p)return;const pl=document.createElementNS(ns,'polyline');
 pl.setAttribute('points',p.map(q=>(q[0]*LW+LW/2)+','+(q[1]*LH+LH/2)).join(' '));
 pl.setAttribute('fill','none');pl.setAttribute('stroke',['#ff5252','#ffb142','#4fa3ff','#c9a0ff'][i%4]);
 pl.setAttribute('stroke-width','3');pl.setAttribute('stroke-dasharray','6 4');svg.appendChild(pl);});
map.appendChild(svg);
const mk=(x,y,txt,c)=>{const e=document.createElement('div');e.className='mk';e.textContent=txt;
 e.style.left=(x*LW+LW/2)+'px';e.style.top=(y*LH+LH/2)+'px';e.style.color=c;map.appendChild(e);};
D.spawns.forEach(s=>mk(s.x,s.y,s.fly?'✈':'S','#ff5252'));
D.doors.forEach(s=>mk(s.x,s.y,'G','#4fa3ff'));
// 停留点（WAIT_FOR_SECONDS）：敌人在这站住不动，到门时刻里已含这段时间
const seenWait={};
(D.waves||[]).forEach(w=>(w.waits_at||[]).forEach(([x,y])=>{
  const k=x+','+y; const t=Math.max(seenWait[k]||0, w.wait||0); seenWait[k]=t;
  mk(x,y,'⏸','#e6b422');}));
Object.keys(seenWait).forEach(k=>{const [x,y]=k.split(',').map(Number);
  const tag=document.createElement('div'); tag.className='tip';
  tag.style.cssText='position:absolute;left:'+(x*LW)+'px;top:'+(y*LH)+'px;z-index:7;color:#e6b422;font-size:10px';
  tag.textContent='⏸'+seenWait[k]+'s'; map.appendChild(tag);});
const DEVLIST=(D.devices===undefined)?[[5,3,'❄']]:D.devices;   // 旧页面无此字段才兜底；空数组=这关没登记装置，就不画
DEVLIST.forEach(dv=>mk(dv[0],dv[1],dv[2]||'❄','#c9a0ff'));
document.getElementById('side').innerHTML='';
document.getElementById('legend').innerHTML=
 '<div class="p"><span class="dot" style="background:#ff5252"></span>出怪口 S / 飞行出怪 ✈</div>'
 +'<div class="p"><span class="dot" style="background:#4fa3ff"></span>蓝门 G</div>'
 +(DEVLIST.length?('<div class="p"><span class="dot" style="background:#c9a0ff"></span>'+(D.deviceWhy||'装置')+' '+DEVLIST.map(d=>d[2]||'❄').join(' ')+'</div>'):'')
 +'<div class="p">虚线 = BFS 算出的真实路径（颜色对应时间轴上的 route 编号）</div>'
 +'<div class="p">到门时刻 = 出怪时刻 + 路径步数 ÷ 移速（移速读 <code>data/_enemy_db.json</code>；飞行单位按两点直线近似）</div>'
 +'<div class="p"><span class="dot" style="background:#3ddc84"></span>选中干员的射程中<b>盖住敌人路径</b>的格</div>'
 +'<div class="p"><span class="dot" style="background:#5a6570"></span>射程覆盖但<b>不在路径上</b>的格</div>'
 +'<div class="p">射程形状按 box 里的<b>精英阶段</b>取（E0/E1/E2 三套，同一干员不同练度射程不同）</div>'
 +'<div class="p" style="color:#93a2b0">选中一个干员即可看到它的真实射程（按朝向旋转；对称射程改朝向不变化）</div>';
const COL=['#ff5252','#ffb142','#4fa3ff','#c9a0ff'];
// 出怪时间轴（按 T+ 排序；颜色 = 该 route 在地图上的虚线颜色）
const tl=document.createElement('div');
tl.className='card'; tl.innerHTML='<h3>出怪 → 到门（T+ 出怪 / 只数 / route / 敌人 → T+ 到门 ｜ 判漏）</h3>';
const ul2=document.createElement('ul'); tl.appendChild(ul2);
document.getElementById('tlCol').appendChild(tl);
const WORDER=(D.waves||[]).map((w,i)=>i).sort((a,b)=>((D.waves[a].t||0)-(D.waves[b].t||0)));
function drawTimeline(ji){                  // 判漏依赖选中作业（费用/部署时刻），所以跟着切作业重绘
 const job=D.jobs[ji]||{}, vd=job.verd||[];
 ul2.innerHTML='';
 WORDER.forEach(i=>{
  const w=D.waves[i], v=vd[i];
  const li=document.createElement('li');
  const ri=(D.paths||[]).findIndex(p=>p&&p[0]&&w.start&&p[0][0]===w.start[0]&&p[0][1]===w.start[1]);
  const dot='<span class="dot" style="display:inline-block;background:'+COL[Math.max(ri,0)%4]+'"></span>';
  // 到门时刻：缺失一律标注，不猜（与后端 reason 字段同一套口径）
  const waitBadge=(w.wait?('<b class="cond" title="这条路上有 WAIT_FOR_SECONDS 停留，已计入到门时刻">⏸'+w.wait+'s</b>'):'');
  const arr=(w.arrival!=null)
    ?('<b class="arr"> →T+'+w.arrival+'</b>'+(w.approx?'<i class="apx">≈直线</i>':'')+waitBadge)
    :('<b class="arr miss"> →'+(w.why==='speed_missing'?'移速缺失':(w.why==='path_missing'?'路径缺失':(w.why==='no_engine'?'未接移速库':'时间未知')))+'</b>');
  // 判漏：悲观下界（不计先锋产费）⇒「存疑」不等于一定漏，「已挡/可打」才是硬结论。
  // 谁挡的/什么时候落地放 tooltip，行内只留标签，否则每行被撑成两三行。
  const vb=v?('<b class="vd '+(v.v==='存疑'?'warn2':'good')+'">'+v.v+'</b>'):'';
  li.title=v?('判漏：'+v.v+(v.by?('（'+v.by+' T+'+v.t+' 落地）'):'')
              +'　— 悲观下界（不计先锋产费）：已挡/可打=确定不漏，存疑=需算产费才算得出'):'';
  li.innerHTML=dot+' T+'+Math.round(w.t||0)+'s  ×'+ (w.n||1) +'  route'+ (ri<0?'?':ri) +'  '+(w.name||'')+'  '+arr+' '+vb;
  ul2.appendChild(li);
 });
}
const side=document.getElementById('side');
const sel=document.createElement('select'); sel.id='jobSel';
D.jobs.forEach((jb,i)=>{const o=document.createElement('option');o.value=i;
 const base=((jb.src||'').split('/').pop()||'').replace(/\.json$/,'');
 o.textContent=jb.title+(base?('｜'+base):'');
 o.title=(jb.src||'')+((jb.warns&&jb.warns.length)?('\n! '+jb.warns.join('\n! ')):'');
 sel.appendChild(o);});
const selBox=document.createElement('span'); selBox.style.display='flex'; selBox.style.alignItems='center'; selBox.style.gap='8px'; selBox.innerHTML='<span class="t">作业</span>（每次只显示一份，避免同名干员叠在一起）</h3>';
selBox.appendChild(sel); document.getElementById('top').appendChild(selBox);
const rangeLayer=document.createElement('div'); rangeLayer.id='rangeLayer';
rangeLayer.style.cssText='position:absolute;left:0;top:0;z-index:3;overflow:visible;width:'+(D.W*LW)+'px;height:'+(D.H*LH)+'px';
map.appendChild(rangeLayer);
const PATH=new Set(D.pathTiles||[]);
function rangeOf(a){                        // 该干员实际射程：按 box 精英阶段取形状
 const tri=(D.name2tri||{})[a.name]; if(!tri||!tri.length) return null;
 const o=(D.ops||{})[a.name]||{};
 const ph=Math.max(0, Math.min(tri.length-1, o.e==null?0:o.e));
 const rid=tri[ph]||tri[0]; const offs=(D.ranges||{})[rid];
 return (offs&&offs.length)?{rid:rid, offs:offs, ph:ph}:null;
}
function drawRange(a){                     // 画某干员的射程（形状按精英阶段，摆放按朝向）
 rangeLayer.innerHTML=''; if(!a||!a.location) return;
 const rr=rangeOf(a); if(!rr) return;
 const offs=rr.offs;
 const [x0,y0]=a.location; const hit=[]; const miss=[];
 // 73 个射程全部关于朝向轴对称 -> 旋转/转置/镜像等价，此处任选一种（已实测 73/73）
 offs.forEach(o=>{ const dx=o[0], dy=o[1];
   let rx,ry;
   if(a.direction==='Left'){rx,ry=-dx,dy;} else if(a.direction==='Up'){rx,ry=dy,dx;}
   else if(a.direction==='Down'){rx,ry=-dy,-dx;} else {rx,ry=dx,dy;}
   const x=x0+rx, y=y0+ry;
   if(x<0||x>=D.W||y<0||y>=D.H) return;
   (PATH.has(x+','+y)?hit:miss).push([x,y]); });
 [[miss,'#5a6570',0.45],[hit,'#3ddc84',0.35]].forEach(([cells,col,op])=>{
   cells.forEach(([x,y])=>{ const d=document.createElement('div');
     d.style.cssText='position:absolute;width:'+LW+'px;height:'+LH+'px;left:'+(x*LW)+'px;top:'+(y*LH)+'px;'
       +'background:'+col+';opacity:'+op+';z-index:3';
     rangeLayer.appendChild(d); }); });
 const o=(D.ops||{})[a.name]||{};
 const tag=document.createElement('div');
 tag.style.cssText='position:absolute;left:'+(x0*LW)+'px;top:'+(y0*LH-18)+'px;z-index:6;'
   +'background:#0f1418;border:1px solid #35424d;color:#9fb0be;padding:1px 6px;border-radius:4px;'
   +'white-space:nowrap;font-size:11px';
 tag.textContent='射程 '+rr.rid+'（E'+rr.ph+'）'+rr.offs.length+'格'
   +(o.lv!=null?(' · 精'+rr.ph+' Lv'+o.lv):'')+' · 有效 '+hit.length+'/'+rr.offs.length;
 rangeLayer.appendChild(tag);
}
const opsLayer=document.createElement('div'); opsLayer.id='opsLayer';
opsLayer.style.cssText='position:absolute;left:0;top:0;z-index:4;overflow:visible;width:'+(D.W*LW)+'px;height:'+(D.H*LH)+'px'; map.appendChild(opsLayer);
let selOp=null;                            // 当前选中的部署动作（引用 job.actions 里的对象）
const BUILD={};                            // 格子类型索引，用于移动校验
const KIND={};                             // tileKey 索引（判"虚空"还是"可走地面"）
D.tiles.forEach(t=>{BUILD[t.x+','+t.y]=t.b; KIND[t.x+','+t.y]=t.k;});
const WALKABLE=new Set(['tile_road','tile_floor','tile_start','tile_flystart','tile_end','tile_fence']);
function typeOK(prof,build){
 if(!prof) return true;
 const rng=(prof==='CASTER'||prof==='MEDIC'||prof==='SNIPER'||prof==='SUPPORT');
 return rng? build==='RANGED' : build==='MELEE';
}
// 能不能往这一格放人。**以实战为准，不以字段为准**：
// 关卡数据里 `buildableType=NONE` 但 tileKey 是可走地面（tile_floor/tile_road）的格子，
// 实战作业确实往上面站人（实测 SR-3：池子里已通关的 101900 把 煌 放在 (4,4)、101955 把
// 能天使放在 (2,5)，两格都是 floor/NONE）⇒ 数据字段与实战冲突时放行并**标注存疑**，
// 只有真正的虚空格（tile_forbidden）与地形（墙/其他）才硬拦。
function cellDeploy(x,y,prof){
 const k=KIND[x+','+y], b=BUILD[x+','+y];
 if(k===undefined) return {ok:false, why:'不在图内'};
 if(k==='tile_forbidden') return {ok:false, why:'虚空格（tile_forbidden）'};
 const rngOk = (b==='MELEE'||b==='RANGED');
 if(rngOk && !typeOK(prof,b)) return {ok:false, why:'格子类型与职业不匹配：'+(prof||'?')+' 需 '+(b==='RANGED'?'高台':'地面')};
 if(rngOk||b==='ALL') return {ok:true};
 if(WALKABLE.has(k)) return {ok:true, doubt:'关卡数据标 NONE，但实战作业在这类格上站过人'};
 return {ok:false, why:'地形不可部署（'+k+'/'+(b||'?')+'）'};
}
function drawJob(i){
 opsLayer.innerHTML='';
 drawTimeline(i);                            // 判漏/部署时刻与选中作业绑定
 const job=D.jobs[i]; if(!job) return;
 const deploys=job.actions.filter(z=>z.type==='Deploy'&&z.location);
 deploys.forEach((a,ai)=>{
  const x=a.location[0], y=a.location[1];
  const d=document.createElement('div'); d.className='op';
  d.style.left=(x*LW+LW/2)+'px'; d.style.top=(y*LH+LH/2)+'px';
  const order=deploys.indexOf(a)+1;
  if(selOp===a) drawRange(a);
  const CIR=['①','②','③','④','⑤','⑥','⑦','⑧','⑨','⑩'];
  d.innerHTML='<i class="ord">'+order+'</i><b style="color:'+(CLS[a.profession]||'#fff')+'">'+(FACE[a.direction]||'?')+'</b><span>'+(CIR[order-1]||order)+a.name+'</span>';
  d.title=a.name+' ['+x+','+y+'] 朝 '+(a.direction||'?')+'（点击切换朝向）';
  d.style.cursor='pointer';
  if(selOp===a) d.classList.add('sel');
  d.onclick=(ev)=>{ if(selOp===a){ const cyc=['Up','Right','Down','Left'];   // 再点本体=切朝向
                      a.direction=cyc[(cyc.indexOf(a.direction)+1)%4]; }
                    else selOp=a;                                          // 首次点=选中
                    drawJob(i); };
  opsLayer.appendChild(d); });
 // 点地图格子 = 把选中的干员移过去（校验用 cellDeploy：虚空硬拦，数据存疑放行但提示）
 map.onclick=(ev)=>{ if(!selOp) return;
   const r=map.getBoundingClientRect();
   const cx=Math.floor((ev.clientX-r.left)/LW), cy=Math.floor((ev.clientY-r.top)/LH);
   if(cx<0||cx>=D.W||cy<0||cy>=D.H) return;
   const cd=cellDeploy(cx,cy,selOp.profession);
   if(!cd.ok){ alert(cd.why); return; }
   selOp.location=[cx,cy]; recompute(i);
   if(cd.doubt && window.wsMsg) wsMsg('['+cx+','+cy+'] 已放置（'+cd.doubt+'）','warn'); };

 const info=document.getElementById('jobInfo');
 if(info) info.remove();
 const box=document.createElement('div'); box.id='jobInfo'; box.className='card';
 box.innerHTML='<h3>'+job.title+'</h3>'+(job.warns.length?('<div class="warn">! '+job.warns.join('<br>! ')+'</div>'):'<div class="ok">无冲突项</div>');
 const ul=document.createElement('ul');
 const dn=[]; job.actions.forEach((a,ai)=>{const li=document.createElement('li');
  let cond=''; if(a.costs!=null) cond+=' costs>='+a.costs; if(a.kills!=null) cond+=' kills>='+a.kills;
  if(a.preDelay!=null) cond+=' +'+a.preDelay+'s';
  const no=document.createElement('span'); no.className='no';
  const txt=document.createElement('span'); txt.className='txt';
  if(a.type==='Deploy'){ dn.push(a); no.textContent=dn.length+'.';
    const op=(D.ops||{})[a.name]||{}; const rr=rangeOf(a);
    // 最早落地时刻（悲观下界：不计先锋产费）；费用缺失就标明，不算数
    const dt=(job.dts||[])[dn.length-1]||{};
    const tTxt=(dt.time!=null)?('<span class="cond arr">T+'+dt.time+'</span>')
      :(dt.missing?('<span class="cond warn">费用缺失</span>'):'');
    // 逐秒决斗结论（不依赖系数的硬一档信息）：挡位 vs 它那条路的敌人
    const du=dt.duel?('<span class="cond '+(dt.duel.result==='enemy_died'?'arr':'warn')+'" title="逐秒决斗：'
        +dt.duel.result+'（我剩 '+dt.duel.hp_left+'/'+dt.duel.hp_max+' 血，敌剩 '+dt.duel.enemy_left+'）">⚔ '
        +dt.duel.enemy+' '+(dt.duel.result==='enemy_died'?'敌人死':'挡位死')+' 用时'+dt.duel.t+'s</span>'):'';
    const lvTxt=(op.e!=null?('<span class="cond">精'+op.e+(op.lv!=null?(' '+op.lv+'级'):'')+'</span>'):'')
      +(rr?('<span class="cond">射程 '+rr.rid+' '+rr.offs.length+'格</span>'):'');
    txt.innerHTML='Deploy '+a.name+'（'+(a.profession||'?')+'）['+a.location+'] '+(FACE[a.direction]||a.direction||'?')
      +lvTxt+tTxt+du+(cond?'<span class="cond">'+cond+'</span>':'');
  } else {
    // 非部署动作（Retreat/Skill/SpeedUp/BulletTime/ResetStopwatch…）与**不认识的类型**：
    // 只显示类型与名字（可拖动排序），条件字段放 tooltip —— 它们必须**原样留住**，
    // 页面不认识它们不代表可以销毁（帧级计时就靠 BulletTime/ResetStopwatch）。
    no.textContent=(dn.length+1)+'.';
    const extra=(a.raw?Object.keys(a.raw).filter(k=>!['type','name','raw'].includes(k)):[]);
    txt.innerHTML=(a.unknown?'<span class="cond">? </span>':'')+a.type+' '+(a.name||'')
      +(cond?'<span class="cond">'+cond+'</span>':'')
      +(extra.length?('<span class="cond">'+extra.slice(0,3).join(',')+(extra.length>3?'…':'')+'</span>'):'');
    li.title=(a.raw?JSON.stringify(a.raw):'')+(a.unknown?' ／ 不认识的类型：原样保留、不改写':''); }
  li.appendChild(no); li.appendChild(txt);
  const up=document.createElement('button'); up.className='mv'; up.textContent='↑';
  const dn2=document.createElement('button'); dn2.className='mv'; dn2.textContent='↓';
  up.onclick=()=>{ if(ai>0){ [job.actions[ai-1],job.actions[ai]]=[job.actions[ai],job.actions[ai-1]]; recompute(i);} };
  dn2.onclick=()=>{ if(ai<job.actions.length-1){ [job.actions[ai+1],job.actions[ai]]=[job.actions[ai],job.actions[ai+1]]; recompute(i);} };
  li.draggable=true; li.dataset.idx=ai;
  li.addEventListener('dragstart',e=>{e.dataTransfer.setData('text/plain',String(ai)); li.style.opacity=.4;});
  li.addEventListener('dragend',e=>{li.style.opacity=1;});
  li.addEventListener('dragover',e=>{e.preventDefault(); li.style.borderTop='2px solid #e6b422';});
  li.addEventListener('dragleave',e=>{li.style.borderTop='';});
  li.addEventListener('drop',e=>{e.preventDefault(); li.style.borderTop='';
    const from=parseInt(e.dataTransfer.getData('text/plain'),10); const to=ai;
    if(isNaN(from)||from===to) return;
    const moved=job.actions.splice(from,1)[0]; job.actions.splice(to,0,moved); recompute(i);});
  li.style.cursor='grab';
  li.appendChild(up); li.appendChild(dn2);
  if(a.type==='Deploy'){                       // 替代干员：从**这个号的 box** 里换（格子类型决定近战/高台）
   const rep=document.createElement('button'); rep.className='mv'; rep.textContent='换人';
   rep.title='从这个账号的 box 里换一个能站这类格子的人';
   rep.onclick=()=>wsReplaceOp(i,a,rep);
   li.appendChild(rep); }
  ul.appendChild(li);});
 box.appendChild(ul); side.appendChild(box);
}
// —— 编辑暂存：按「作业身份（源文件路径）」分槽 ——
// 旧版键是 vizEdits_<stage> 且按下标回填，池子里多一份/少一份作业就会把编辑
// 串到另一份作业身上。现在按 src 认领，且按 (type,name) 匹配动作，顺序与位置都能还原。
const EKEY='vizEdits2_'+(D.stage||'stage');
const JKEY=jb=>jb.src||jb.title||'';
function saveEdits(){ try{
  const all={};
  D.jobs.forEach(jb=>{ all[JKEY(jb)]={n:(jb.actions||[]).length,
    acts:(jb.actions||[]).map(a=>[a.type,a.name,a.location,a.direction])}; });
  localStorage.setItem(EKEY, JSON.stringify(all)); }catch(e){} }
function loadEdits(){ try{
  const raw=localStorage.getItem(EKEY); if(!raw) return;
  const all=JSON.parse(raw); if(!all||typeof all!=='object') return;
  D.jobs.forEach(jb=>{
    const rec=all[JKEY(jb)]; if(!rec||!rec.acts) return;
    if(rec.n!==(jb.actions||[]).length) return;        // 动作条数变了 -> 整份不套用，宁可丢编辑也不串位
    const pool=(jb.actions||[]).slice(); const out=[];
    rec.acts.forEach(v=>{                             // 按 (type,name) 认领再还原，故重排过的顺序也吃得住
      const k=pool.findIndex(a=>a.type===v[0]&&(a.name||'')===(v[1]||''));
      if(k<0) return;
      const a=pool.splice(k,1)[0];
      if(v[2]) a.location=v[2]; if(v[3]) a.direction=v[3];
      out.push(a); });
    out.push.apply(out,pool);                         // 没被认领的按原序补回，保持总数不变
    if(out.length===(jb.actions||[]).length) jb.actions=out; }); }catch(e){} }
function wsReplaceOp(i,a,btn){
 // 换人：候选取自 D.ops（= 这个账号 box 里的干员，含练度），按目标格类型过滤近战/高台
 const cd=cellDeploy(a.location[0], a.location[1], null);
 if(!cd.ok){ alert(cd.why+'：先把这个位置挪到能站人的格上'); return; }
 const b=BUILD[a.location.join(',')];
 const want=(b==='RANGED')?'RANGED':'MELEE';
 if(b!=='RANGED'&&b!=='MELEE'){ wsMsg('这一格的类型数据缺失（'+b+'）——换人只能列出"不一定贴格子"的全部，自己核一下','warn'); }
 const cands=Object.keys(D.ops||{}).filter(n=>{
   const p=(D.prof||{})[n]; if(!p) return false;
   if(b!=='RANGED'&&b!=='MELEE') return true;      // 类型未知 → 不按类型过滤
   const rng=(p==='CASTER'||p==='MEDIC'||p==='SNIPER'||p==='SUPPORT');
   return (rng?'RANGED':'MELEE')===want;
 }).sort();
 const s=document.createElement('select');
 s.style.cssText='max-width:150px;padding:0 4px;font-size:11px';
 s.title='只列能站「'+(want==='RANGED'?'高台':'地面')+'」的干员（按 box 过滤）'+(cd.doubt?('；'+cd.doubt):'');
 s.innerHTML='<option value="">换人…（'+cands.length+'个）</option>'
  +cands.map(n=>{const o=(D.ops||{})[n]||{};
    return '<option value="'+n+'"'+(n===a.name?' selected':'')+'>'+n
      +(o.e!=null?(' 精'+o.e+(o.lv!=null?(' '+o.lv):'')):'')+'</option>';}).join('');
 s.onchange=e=>{ const n=e.target.value; if(!n) return;
   a.name=n; a.profession=(D.prof||{})[n]; recompute(i); };
 btn.replaceWith(s);
}
function recalcWarns(i){                    // 只重算冲突（与后端同一规则），不画不存
 const job=D.jobs[i]; if(!job) return; const w=[]; const seen={};
 (job.actions||[]).forEach(a=>{ if(a.type!=='Deploy'||!a.location) return;
  const k=a.location.join(','); if(seen[k]) w.push('位置重叠：'+a.name+' 与 '+seen[k]+' 同在 ['+k+']'); seen[k]=a.name; });
 // 装置覆盖规则按关数据出（D.deviceCover 为空=这关没登记装置，就不该乱报）
 const cover=D.deviceCover||[];
 if(cover.length && !(job.actions||[]).some(a=>a.type==='Deploy'&&a.location
    &&cover.some(c=>c[0]===a.location[0]&&c[1]===a.location[1])))
  w.push('无人站 '+cover.map(c=>'['+c.join(',')+']').join('/')+'：打不到'+(D.deviceWhy||'装置'));
 job.warns=w;
}
function recompute(i){ recalcWarns(i); drawJob(i); saveEdits(); }
function jobJson(i){              // 当前编辑态 → MAA 作业 JSON（导出与「保存为新版本/cleared」共用）
 const job=D.jobs[i]; const stage=D.stage||'act16side_ex02';
 // **逐字段写回**：以动作原始字典为底（`raw`），只覆盖页面真正改过的字段。
 // 只 emit Deploy 会把 Skill/Retreat 整条战术轴丢掉（池子里 56 份带定时 Skill、60 份带 Retreat，
 // 条件字段 kill/cost_changes/post_delay 与 doc 注释都是打法本体）——那是真丢数据，不是格式化。
 // **逐条按原顺序写回**（不再合成 SpeedUp/SkillDaemon：它们现在也在 actions 里，
 // 合成会导致重复、并把原作业中途的 SpeedUp 位置挪到最前 —— 那会改动节奏）。
 const acts=[];
 job.actions.forEach(a=>{
   const base=Object.assign({}, a.raw||{});
   if(a.type==='Deploy') Object.assign(base, {type:'Deploy', name:a.name,
     location:a.location, direction:a.direction});
   else Object.assign(base, {type:a.type, name:a.name||undefined});
   acts.push(base);
 });
 // **元数据以原作业为底、原样写回**（2026-09-16 用户现场抓到）。这里原来硬编码
 // `minimum_required:'v4.0.0'` + 固定 doc ⇒ ① 每保存一次就把原值降级/覆盖（实测
 // v6.0.0 → v4.0.0、原出处链接丢失）；② 版本下拉标题带 `｜版本` 标签，被当标题再拼一次
 // "（页面编辑）"⇒ 标题逐次累积（实测 v1「SR-1 - 月出」→ v2「…（页面编辑）」→
 // v3「…（页面编辑）｜版本（页面编辑）」）。现在只有**人真的改了标题**才追加。
 const meta=job.meta||{}, odoc=Object.assign({}, meta.doc||{});
 const label=String(job.title||'').replace(/｜版本$/,'');
 const origTitle=odoc.title||label;
 if(!odoc.title) odoc.title=origTitle;
 if(label && label!==origTitle) odoc.title=label+'（页面编辑）';
 if(!odoc.details) odoc.details='由排轴可视化导出（保留原 opers 的技能与练度字段）';
 return {version:(meta.version||3),stage_name:(meta.stage_name||stage),
  minimum_required:(meta.minimum_required||'v4.0.0'),
  doc:odoc,
  // opers 只补**真干员**：占位/口语名（实测 SR-EX-8 的「车」「快活」）不是干员，
  // 给它们造一条 skill:1 等于让 MAA 去配一个不存在的人（配队阶段就会报错/异常）。
  // 判据用页面已有的职业表 D.prof（名字来自 char_table，池子里的占位名不在其中）。
  opers:(function(){const orig=(job.opers||[]).map(o=>Object.assign({},o));const named=new Set(orig.map(o=>o.name));job.actions.filter(a=>a.type==='Deploy').forEach(a=>{ if(a.name && !named.has(a.name) && (D.prof||{})[a.name]){ orig.push({name:a.name,skill:1,skill_usage:1}); named.add(a.name);} });return orig;})(),
  groups:(job.groups||[]),
  actions:acts};
}
function exportJob(i){
 const out=jobJson(i);
 const blob=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});
 // 命名带上关卡+作业名+日期：旧版 edited_<下拉下标>.json 换一次生成就对不上号
 const dt=new Date(); const ds=''+dt.getFullYear()+String(dt.getMonth()+1).padStart(2,'0')+String(dt.getDate()).padStart(2,'0');
 const safe=((D.jobs[i]||{}).title||'job').replace(/[\\/:*?"<>|\s]+/g,'_').slice(0,60);
 const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
 a.download=(D.stage||'stage')+'__'+safe+'__'+ds+'.json'; a.click();
}
const expBtn=document.createElement('button'); expBtn.textContent='导出当前作业 JSON';
expBtn.onclick=()=>exportJob(+sel.value||0); document.getElementById('top').appendChild(expBtn);

// —— 工作区（D.ws 由 tools/workspace.py 注入；单关 --stage 生成的页面里没有它，整块不生效）——
// 账号/活动下拉 = 直接跳转到那个主页（静态托管没有后端可查，所以链接在生成时就写死）。
// 「保存为 cleared」优先 POST 给 tools/ws_server.py；静态 http.server 下没有写接口（501），
// 于是退回下载 + 命令行收编 —— 与既有的 --ingest 同一条回路，不是"没做完"。
const WS=D.ws||null;
function wsMsg(t,kind){
 const m=document.getElementById('wsMsg'); if(!m) return;
 m.textContent=t||''; m.className=(kind==='ok'?'arr':(kind==='warn'?'warn':'tip'));
}
function wsNotes(){const t=document.getElementById('wsNotes');return t?t.value:'';}
function wsVNote(){const t=document.getElementById('wsVNote');return t?t.value.trim():'';}
function wsRenderState(){
 const c=document.getElementById('wsState');
 if(c) c.innerHTML=WS.cleared?'<b class="arr">已通关 ✓</b>':'<span class="tip">未通关</span>';
}
function wsPost(url,body){
 return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(r=>r.json().then(j=>({ok:r.ok,status:r.status,j:j})).catch(()=>({ok:r.ok,status:r.status,j:{}})));
}
function wsSaveNotes(){
 if(!WS) return;
 wsPost(WS.notes_url,{account:WS.account,activity:WS.activity,stage:WS.stage,code:WS.code,notes:wsNotes()})
  .then(({ok,j})=>wsMsg(ok&&j.ok?'备注已保存到 '+j.notes:'备注保存失败：'+(j.error||('HTTP '+(j.status||''))),ok&&j.ok?'ok':'warn'))
  .catch(e=>wsMsg('连不上保存服务（'+e.message+'）—— 起服务：python tools/ws_server.py','warn'));
}
function wsSaveCleared(){
 if(!WS) return;
 const i=+sel.value||0;
 const body={account:WS.account,activity:WS.activity,stage:WS.stage,code:WS.code,
             from_job:(D.jobs[i]||{}).src||'',job:jobJson(i),notes:wsNotes(),
             note:wsVNote()||wsNotes().trim(),parent:wsParentOf((D.jobs[i]||{}).src)};
 wsPost(WS.save_url,body).then(({ok,status,j})=>{
  if(ok&&j.ok){ WS.cleared=true; wsRenderState(); wsAfterSave(j);
    wsMsg('已保存为 cleared：'+j.cleared+'（版本 '+j.version+'，改 '+j.n_changes+' 处）','ok'); }
  else if(ok||j.error) wsMsg('保存失败：'+(j.error||('HTTP '+status))+' → 导出后用 python tools/workspace.py --save-cleared '+WS.account+' '+WS.activity+' '+WS.code,'warn');
  else throw new Error('HTTP '+status);
 }).catch(e=>{
  wsMsg('没有写接口（'+e.message+'）→ 已改为下载文件；落盘用：python tools/workspace.py --save-cleared '+WS.account+' '+WS.activity+' '+WS.code,'warn');
  exportJob(i);
 });
}

// —— 修改树：每次保存都是一个版本节点（v1_ai → v2_human → …）——
// 迭代闭环靠这棵树：节点里带着"相对父版本改了哪几处"，规则入库直接读它，不用事后重算。
function wsParentOf(src){
 const m=/(?:^|\/)versions\/(v\d+[a-z]*_\w+)\.json$/.exec(String(src||''));
 return m? m[1] : null;          // cleared/候选 → 交给后端按 cleared_meta / base_src 判
}
const KNOWN_ACT=['Deploy','Skill','Retreat','SpeedUp','BulletTime','SkillUsage','Output',
                 'SkillDaemon','MoveCamera','ResetStopwatch'];
function wsNormalizeJob(job,src){
 // 版本文件没经过 Python 的 normalize_job，这里补一遍（动作别名 + 职业从 D.prof 取）。
 // **同样逐条逐字段保留**：这里原来只认 Deploy/Retreat/Skill，把 BulletTime/ResetStopwatch/
 // SkillUsage/Output 和全部条件字段丢掉了 —— 读一个版本再存下去就是静默销毁战术动作。
 const acts=[];
 (job.actions||[]).forEach(a=>{ const raw=String(a.type||'');
  const t=raw==='部署'?'Deploy':raw==='撤退'?'Retreat':(raw==='技能'?'Skill':raw);
  const d=Object.assign({}, a, {type:t, raw:Object.assign({}, a)});
  if(t==='Deploy'){ Object.assign(d,{profession:(D.prof||{})[a.name],costs:a.costs,kills:a.kills,
    preDelay:(a.pre_delay!=null?a.pre_delay:a.preDelay)}); }
  if(KNOWN_ACT.indexOf(t)<0) d.unknown=true;
  acts.push(d); });
 const nm=String(src||'').split('/').pop().replace(/\.json$/,'');
 return {title:(((job.doc||{}).title)||nm)+'｜版本', src:src, actions:acts,
         opers:job.opers||[], groups:job.groups||[], warns:[],
         // 元数据留给 jobJson 原样写回。注意 `｜版本` 是**下拉标签**，不属于 doc.title ——
         // 不剥掉它就会被当成"标题被改过"，再拼一次"（页面编辑）"（实测 v3 就累积成这样）。
         meta:{version:job.version, stage_name:job.stage_name,
               minimum_required:job.minimum_required, doc:(job.doc||{})}};
}
function rebuildJobSel(want){
 sel.innerHTML='';
 D.jobs.forEach((jb,i)=>{const o=document.createElement('option');o.value=i;
  const base=((jb.src||'').split('/').pop()||'').replace(/\.json$/,'');
  o.textContent=jb.title+(base?('｜'+base):''); o.title=jb.src||''; sel.appendChild(o);});
 const idx=(want==null)?(+sel.value||0):want;
 sel.value=String(idx);
 recalcWarns(idx); drawJob(idx);       // 载入版本走只读口径：不写 localStorage（与启动一致）
}
async function wsLoadVersion(v){
 if(!WS) return;
 const url=(WS.versions_dir||'versions/')+v.file;
 try{
  const r=await fetch(url,{cache:'no-store'}); if(!r.ok) throw new Error('HTTP '+r.status);
  const job=await r.json();
  const nj=wsNormalizeJob(job,url);
  const k=D.jobs.findIndex(x=>x.src===url);
  if(k>=0) D.jobs[k]=nj; else D.jobs.push(nj);
  rebuildJobSel(D.jobs.findIndex(x=>x.src===url));
  wsMsg('已载入 '+v.id+'（在它之上继续编辑 → 保存为新版本）','ok');
 }catch(e){ wsMsg('载入版本失败：'+e.message,'warn'); }
}
function wsRegisterVersion(j){
 // 保存后把新版本文件读回下拉，这样"继续改下一版"是接着它，不是接着旧的那份
 const url=(WS.versions_dir||'versions/')+j.version+'.json';
 return fetch(url,{cache:'no-store'}).then(r=>r.json()).then(job=>{
  const nj=wsNormalizeJob(job,url);
  const k=D.jobs.findIndex(x=>x.src===url);
  if(k>=0) D.jobs[k]=nj; else D.jobs.push(nj);
  rebuildJobSel(D.jobs.findIndex(x=>x.src===url));
 }).catch(()=>{});
}
function wsAfterSave(j){ if(j&&j.versions){ WS.versions=j.versions; } wsRenderVersions(); return wsRegisterVersion(j); }
function wsSaveVersion(){
 if(!WS) return;
 const i=+sel.value||0;
 const body={account:WS.account,activity:WS.activity,stage:WS.stage,code:WS.code,
             from_job:(D.jobs[i]||{}).src||'',job:jobJson(i),note:wsVNote(),notes:wsNotes(),
             parent:wsParentOf((D.jobs[i]||{}).src)};
 wsPost(WS.version_url,body).then(({ok,status,j})=>{
  if(ok&&j.ok){ wsAfterSave(j);
    wsMsg('已存为 '+j.version+'（父 '+(j.parent||'候选')+'，改 '+j.n_changes+' 处）','ok'); }
  else if(ok||j.error) wsMsg('保存失败：'+(j.error||('HTTP '+status))+' → 导出后用 python tools/workspace.py --save-version '+WS.account+' '+WS.activity+' '+WS.code,'warn');
  else throw new Error('HTTP '+status);
 }).catch(e=>{
  wsMsg('没有写接口（'+e.message+'）→ 已改为下载文件；落盘用：python tools/workspace.py --save-version '+WS.account+' '+WS.activity+' '+WS.code,'warn');
  exportJob(i);
 });
}
function wsRenderVersions(){
 const box=document.getElementById('wsVersions'); if(!box) return;
 const vs=((WS&&WS.versions)||{}).nodes||[];
 box.innerHTML='<h3>修改树 <span style="font-weight:400;color:#93a2b0">'+(vs.length||0)+' 个版本</span></h3>';
 if(!vs.length){ box.innerHTML+='<div class="tip">还没有版本节点（生成时会自动建 v1_ai）</div>'; return; }
 const ul=document.createElement('ul');
 vs.forEach(v=>{
  const li=document.createElement('li');
  const depth=Math.max(0,((v.id.match(/^v(\d+)/)||[,'1'])[1]-1));
  li.style.marginLeft=(depth*14)+'px';
  const h=document.createElement('div'); h.className='vh';
  h.innerHTML=(v.author==='ai'?'<span class="vd good">AI</span>':'<span class="vd warn2">人</span>')
   +'<b>'+v.id+'</b><span class="tip">'+String(v.at||'').slice(5,16)+'</span>'
   +(v.n_changes?('<span class="vd '+(v.author==='ai'?'good':'warn2')+'">改 '+v.n_changes+' 处</span>')
                :'<span class="tip">'+(v.parent?'无改动':'根')+'</span>')
   +(v.mark_cleared?' <span class="vd good">✓通关</span>':'');
  const bLoad=document.createElement('button'); bLoad.className='mv'; bLoad.textContent='载入';
  const bDiff=document.createElement('button'); bDiff.className='mv'; bDiff.textContent='看改动';
  h.appendChild(bLoad); h.appendChild(bDiff); li.appendChild(h);
  if(v.note){ const n=document.createElement('div'); n.className='vnote'; n.textContent=v.note; li.appendChild(n); }
  const ch=document.createElement('div'); ch.className='vchanges'; ch.style.display='none';
  (v.changes||[]).forEach(c=>{ const d=document.createElement('div');
   d.textContent='· '+(c.step?('第'+c.step+'步 '):'')+'['+c.type+'] '+(c.note||''); ch.appendChild(d); });
  li.appendChild(ch);
  bLoad.onclick=()=>wsLoadVersion(v);
  bDiff.onclick=()=>{ ch.style.display=(ch.style.display==='none')?'':'none'; };
  ul.appendChild(li);
 });
 box.appendChild(ul);
}
function initWsBar(){
 if(!WS) return;
 const bar=document.getElementById('wsbar'); bar.classList.add('on');
 bar.innerHTML='<a href="'+WS.activity_url+'">← 活动主页</a><a href="'+WS.root_url+'">⌂ 工作区</a>'
  +'<span class="t">'+WS.code+' '+(WS.name||'')+'</span><span id="wsState"></span>'
  +'<span class="tip" id="wsBox"></span>';
 const asel=document.createElement('select');
 asel.title='切账号（跳到那个账号的主页）';
 asel.innerHTML='<option value="">账号 '+WS.account+'</option>'
  +WS.accounts.filter(a=>a.id!==WS.account).map(a=>'<option value="'+a.href+'">账号 '+a.id+'</option>').join('');
 asel.onchange=e=>{ if(e.target.value) location.href=e.target.value; };
 const actsel=document.createElement('select');
 actsel.title='切活动（同账号）';
 actsel.innerHTML='<option value="">活动 '+WS.activity_name+'</option>'
  +WS.activities.filter(x=>x.id!==WS.activity).map(x=>'<option value="'+x.href+'">'+x.name+'</option>').join('');
 actsel.onchange=e=>{ if(e.target.value) location.href=e.target.value; };
 bar.appendChild(asel); bar.appendChild(actsel);
 const bSave=document.createElement('button'); bSave.textContent='保存为 cleared'; bSave.onclick=wsSaveCleared;
 const bVer=document.createElement('button'); bVer.textContent='保存为新版本'; bVer.onclick=wsSaveVersion;
 const bNote=document.createElement('button'); bNote.textContent='保存备注'; bNote.onclick=wsSaveNotes;
 bar.appendChild(bSave); bar.appendChild(bVer); bar.appendChild(bNote);
 bar.appendChild(Object.assign(document.createElement('span'),{id:'wsMsg',className:'tip'}));
 const vn=document.createElement('input'); vn.id='wsVNote';
 vn.placeholder='这条版本改了什么（写进修改树：v2_human 的改动说明 + 为什么）';
 vn.title='写进版本节点 meta 的 note；规则入库时它是"为什么"的唯一来源';
 bar.appendChild(vn);
 const ta=document.createElement('textarea'); ta.id='wsNotes'; ta.value=WS.notes||'';
 ta.placeholder='备注（写进本关 notes.md）：为什么改、改了哪里 —— 同一关不同账号的 cleared.json 放一起就是差异数据';
 ta.title='备注跟着关卡走，不跟着作业下拉走';
 bar.appendChild(ta);
 const b=(document.getElementById('wsBox'));
 if(b) b.textContent=(D.box?('按 '+D.box+' 的练度算'):'')+' ｜ 下拉 '+WS.n_jobs+' 份'
   +(WS.cleared?'（首项=你存过的 cleared）':'（全是候选）');
 // 修改树面板放左列（时间轴下面）：它跟作业下拉是两件事——下拉换"看哪份"，面板记录"改过哪些版本"
 const vbox=document.createElement('div'); vbox.id='wsVersions'; vbox.className='card';
 const tlCol=document.getElementById('tlCol');
 if(tlCol) tlCol.appendChild(vbox);
 wsRenderState(); wsRenderVersions();
}
initWsBar();

// —— 时间轴动画：把每只怪按 (出怪 t → 到门 arrival) 线性插值到路径上 ——
// 用来一眼看出"某个时刻这只怪在哪、有没有人到门"（到门计数就是漏怪数）
const waveLayer=document.createElement('div'); waveLayer.id='waveLayer';
waveLayer.style.cssText='position:absolute;left:0;top:0;z-index:3;overflow:visible;width:'+(D.W*LW)+'px;height:'+(D.H*LH)+'px';
map.appendChild(waveLayer);
const TMAX=Math.max(60,Math.ceil(Math.max.apply(null,[0].concat((D.waves||[]).map(w=>w.arrival||0)))));
let tNow=0,timer=null;
const tbar=document.createElement('div');
tbar.className='card'; tbar.style.cssText='display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap';
const tRange=document.createElement('input'); tRange.type='range'; tRange.min=0; tRange.max=TMAX; tRange.value=0; tRange.step=1;
tRange.style.cssText='flex:1 1 160px;min-width:120px';
const tBtn=document.createElement('button'); tBtn.textContent='▶ 播放';
const tLabel=document.createElement('b'); tLabel.className='arr'; tLabel.textContent='T+0s';
const leakLabel=document.createElement('span'); leakLabel.className='tip';
tbar.appendChild(tBtn); tbar.appendChild(tRange); tbar.appendChild(tLabel); tbar.appendChild(leakLabel);
document.getElementById('legend').after(tbar);
function pathOf(w){
 const ri=(D.paths||[]).findIndex(p=>p&&p[0]&&w.start&&p[0][0]===w.start[0]&&p[0][1]===w.start[1]);
 return {ri:ri,path:(D.paths||[])[ri]};
}
function drawWaves(){
 waveLayer.innerHTML='';
 let leaked=0;
 (D.waves||[]).forEach(w=>{
  if(w.arrival==null) return;                       // 缺数据的怪不画（时间未知，不猜）
  const pp=pathOf(w); if(!pp.path) return;
  if(tNow>=w.arrival) leaked+=(w.n||1);
  if(tNow<(w.t||0)) return;                         // 还没出怪
  const span=Math.max(.001,w.arrival-(w.t||0));
  const seg=Math.min(1,(tNow-(w.t||0))/span)*(pp.path.length-1);
  const i0=Math.max(0,Math.min(pp.path.length-2,Math.floor(seg)));
  const f=seg-i0,p0=pp.path[i0],p1=pp.path[i0+1]||p0;
  const x=p0[0]+(p1[0]-p0[0])*f,y=p0[1]+(p1[1]-p0[1])*f;
  const e=document.createElement('div');
  e.textContent='●';
  e.style.cssText='position:absolute;transform:translate(-50%,-50%);font-size:14px;z-index:3;text-shadow:0 0 4px #000;color:'+COL[Math.max(pp.ri,0)%4];
  e.style.left=(x*LW+LW/2)+'px'; e.style.top=(y*LH+LH/2)+'px';
  e.title=w.name+'：T+'+w.t+' 出怪 → T+'+w.arrival+' 到门';
  waveLayer.appendChild(e);
 });
 tLabel.textContent='T+'+Math.round(tNow)+'s';
 leakLabel.innerHTML=leaked?('<span class="warn">已到门 '+leaked+' 只</span>'):'已到门 0 只';
}
tRange.oninput=()=>{ tNow=+tRange.value; drawWaves(); };
tBtn.onclick=()=>{
 if(timer){ clearInterval(timer); timer=null; tBtn.textContent='▶ 播放'; return; }
 tBtn.textContent='⏸ 暂停';
 timer=setInterval(()=>{
  tNow=Math.min(TMAX,tNow+1); tRange.value=tNow; drawWaves();
  if(tNow>=TMAX){ clearInterval(timer); timer=null; tBtn.textContent='▶ 播放'; }
 },120);
};

sel.addEventListener('change',e=>drawJob(+e.target.value));
loadEdits();
D.jobs.forEach((_,i)=>recalcWarns(i));   // 回填过位置的作业，冲突要跟着重算，否则显示的是原始位置的结论
drawJob(+sel.value||0);                  // 启动只读：这里不写 localStorage，否则一次 load 闪失就会把用户编辑永久覆盖
drawWaves();
</script>
"""


def render_for(stage: str, job_files: list, out=None):
    """函数接口：给一批作业出图，返回 (html路径, 每份作业的冲突列表)。

    供自动化调用（抓取后自动重画 / 起跑前自动体检），不必走 argv。
    """
    global STAGE
    STAGE = stage
    jobs, skipped = load_jobs(job_files, professions(), stage)
    for f, why in skipped:
        print("   跳过 %s（%s）" % (f, why))
    p = write_html(build_payload(stage, level(stage), jobs), out_path(stage, out))
    return str(p), [(jb["title"], jb["warns"]) for jb in jobs]

if __name__ == "__main__":
    main()
