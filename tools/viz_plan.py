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
STAGE = "act16side_ex02"
# 关卡 -> (装置格[(x,y,标记)], 必须有人站的格, 装置名)。
# 装置几何是逐关的、从 level 数据推不出来，所以按关登记。没登记的关卡既不该画装置、
# 也不该报这条冲突 —— 否则换关后会拿 ex02 的 [5,3] 在别的图上胡说。
DEVICE = {"act16side_ex02": ([(5, 3, "❄")], [(5, 2), (5, 4)], "冰淇淋机 [5,3]")}
WALK = {"tile_road", "tile_floor", "tile_start", "tile_flystart", "tile_end", "tile_fence"}


def device_info(stage=None):
    return DEVICE.get(stage or STAGE) or ([], [], "")


def level(stage: str = None):
    stage = stage or STAGE
    lf = PROJ / "data" / ("_level_%s.json" % stage)
    if not lf.exists():
        raise RuntimeError("缺少关卡文件 %s（先下载该关 level 数据）" % lf)
    d = json.load(open(lf, encoding="utf-8"))
    T, M = d["mapData"]["tiles"], d["mapData"]["map"]
    H, W = len(M), len(M[0])

    def key(x, y):
        return T[M[H - 1 - y][x]]["tileKey"]

    def bt(x, y):
        return T[M[H - 1 - y][x]]["buildableType"]

    def bfs(a, b):
        q = collections.deque([a]); prev = {a: None}
        while q:
            x, y = q.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
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

    routes, fly_starts = [], set()
    for r in d.get("routes") or []:
        sp, ep = r.get("startPosition") or {}, r.get("endPosition") or {}
        a, b = (sp.get("col"), sp.get("row")), (ep.get("col"), ep.get("row"))
        if a == b or any(x[0] == a for x in routes):
            continue
        p = bfs(a, b)
        if not p:
            # BFS 跨不过地形（例：第 3 列整列不可走，左右两半在步行格上不相连）= 飞行单位。
            # 用两点直线近似 —— 画出来比留个 route? 强，也让御4 的接近路线可见。
            fly_starts.add(a)
            if ES:
                p = ES.line_cells(a, b)
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
    paths_by_start = {a: p for a, b, p in routes}
    for w in d.get("waves", []):
        for fr in w.get("fragments", []):
            for act in fr.get("actions", []):
                if act.get("actionType") == "SPAWN":
                    rt = d["routes"][act["routeIndex"]]
                    sp = rt.get("startPosition") or {}
                    ep = rt.get("endPosition") or {}
                    st = (sp.get("col"), sp.get("row"))
                    en = (ep.get("col"), ep.get("row"))
                    path = paths_by_start.get(st)
                    approx = st in fly_starts
                    if not path and ES:
                        path = ES.line_cells(st, en)
                    arr = (ES.calc_arrival(act.get("preDelay") or 0, path, act.get("key"),
                                           speeds=speeds, approx=approx)
                           if ES else {"arrival": None, "reason": "no_engine"})
                    waves.append({"start": [sp.get("col"), sp.get("row")], "t": act.get("preDelay"),
                                  "n": act.get("count", 1), "route": act.get("routeIndex"),
                                  "key": act.get("key"),
                                  "name": ENAME.get(act.get("key") or "", (act.get("key") or "")[:18]),
                                  "arrival": arr.get("arrival"), "why": arr.get("reason"),
                                  "speed": arr.get("speed_used"), "approx": approx})
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


def load_ranges(jobs):
    """射程表：MAA battle_data.json 的 chars[name].rangeId + ranges[rangeId] 偏移列表。

    两条经数据实证的结论（别凭直觉改）：
      1) rangeId 是**三元组 = 精英阶段 [E0, E1, E2]**，不是四个方向；无精二的 3★ 干员
         第 3 项回落成 E0（实测 4 例）。所以画哪一阶段要按 box 的 elite 取 —— 写死 [0]
         会给 E2 干员少画一格（本局 逻各斯/圣聆初雪/白面鸮 全中）。
      2) 全部 73 个射程都关于朝向轴对称（y 对称，实测 73/73），所以「旋转 90°/转置/镜像」
         三种摆法结果**完全等价**，不需要实测校准；唯一还成立的假设是「+x = 前方」，
         由数据自身佐证（长边与 nose 都朝 +x；近战 1-1 = 自身格 + 前方格）。
    """
    try:
        bd = json.load(open(PROJ / "services/maa/instances/2/resource/battle_data.json", encoding="utf-8"))
        all_tri = {}
        for c in (bd.get("chars") or {}).values():
            tri = c.get("rangeId")
            if c.get("name") and isinstance(tri, list) and tri:
                all_tri[c["name"]] = list(tri)
        names = set()
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


def ranges_payload(jobs):
    """(name2tri, ranges, ops) —— 前端画射程所需的全部数据，两个调用点共用。"""
    n2t, rng = load_ranges(jobs)
    names = set()
    for jb in jobs:
        for o in (jb.get("opers") or []):
            if isinstance(o, dict) and o.get("name"): names.add(o["name"])
        for a in (jb.get("actions") or []):
            if a.get("name"): names.add(a["name"])
    return n2t, rng, box_ops(load_box(), names)


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


def normalize_job(j, prof, src=None):
    """一份 MAA 作业 → 前端用的 {title, src, actions, opers, groups}。

    两条生成路径（main / render_for）共用。costs/kills/preDelay 是 MAA 动作自带的
    条件字段，前端右列要显示它们 —— 曾经只有 main() 注入，render_for() 不注入，
    导致同一条"起跑前体检"路径画出来的图少了这几列。
    """
    acts = []
    for a in j.get("actions") or []:
        t, nm = a.get("type"), a.get("name", "")
        if t in ("Deploy", "部署"):
            # 注意：MAA 作业里这个字段是 snake_case 的 pre_delay（实测 272 份作业出现 263 次，
            # camelCase 一次都没有）—— 只读 preDelay 会让前端那个 "+Ns" 角标永远是空的。
            pd = a.get("preDelay")
            if pd is None:
                pd = a.get("pre_delay")
            acts.append({"type": "Deploy", "name": nm, "location": a.get("location"),
                         "direction": a.get("direction"), "profession": prof.get(nm),
                         "costs": a.get("costs"), "kills": a.get("kills"),
                         "preDelay": pd})
        elif t in ("Retreat", "撤退"):
            acts.append({"type": "Retreat", "name": nm})
        elif t in ("Skill", "技能", "SkillDaemon"):
            acts.append({"type": "Skill", "name": nm})
    title = (j.get("doc") or {}).get("title")
    if not title:
        title = pathlib.Path(src).stem if src else "作业"
    return {"title": title, "src": rel_src(src) if src else "", "actions": acts,
            "opers": j.get("opers") or [], "groups": j.get("groups") or []}


def load_jobs(files, prof, stage=None):
    """读一批作业；无 actions 的跳过（口径：valid_job = 有 actions）。返回 (jobs, skipped)。"""
    jobs, skipped = [], []
    for f in files:
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            skipped.append((str(f), "读取失败: %s" % e))
            continue
        jb = normalize_job(j, prof, f)
        if not jb["actions"]:
            skipped.append((str(f), "无 actions"))
            continue
        jb["warns"] = conflicts(jb, stage)
        jobs.append(jb)
    return jobs, skipped


def build_payload(stage, lv, jobs):
    """前端数据包。两条生成路径共用 —— 字段集合必须一致，否则产物行为会分叉。"""
    W, H, key, bt, routes, waves = lv
    tiles = [{"x": x, "y": y, "k": key(x, y), "b": bt(x, y)} for y in range(H) for x in range(W)]
    paths = [p for _, _, p in routes]
    _n2t, _rng, _ops = ranges_payload(jobs)
    d_at, d_cover, d_why = device_info(stage)
    # —— 费用/判漏：对每份作业算"每个部署最早落地时刻"，再判每条出怪来不来得及 ——
    opts = CM.load_level_options(stage) if CM else None
    if opts:
        _costs, _elites = CM.load_char_costs(), CM.load_elites()
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
    return {"stage": stage, "W": W, "H": H, "tiles": tiles, "paths": paths,
            "spawns": [{"x": t["x"], "y": t["y"], "fly": t["k"] == "tile_flystart"}
                       for t in tiles if t["k"] in ("tile_start", "tile_flystart")],
            "doors": [{"x": t["x"], "y": t["y"]} for t in tiles if t["k"] == "tile_end"],
            "waves": waves, "jobs": jobs, "opts": opts,
            "devices": [list(d) for d in d_at], "deviceCover": [list(c) for c in d_cover],
            "deviceWhy": d_why,
            "ranges": _rng, "name2tri": _n2t, "ops": _ops,
            "pathTiles": sorted({"%d,%d" % (pt[0], pt[1]) for pa in paths if pa for pt in pa})}


def out_path(stage, out=None):
    return pathlib.Path(out) if out else PROJ / "data/visualization" / ("%s.html" % stage)


def write_html(data, out):
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
                   .replace("__LW__", str(LW)).replace("__LH__", str(LH))
                   .replace("__CLS__", json.dumps(CLS)).replace("__FACE__", json.dumps(FACE)),
                   encoding="utf-8")
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
    a = ap.parse_args(argv)

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
const cls=k=>k==='tile_wall'?'wallrow':(k==='tile_fence'?'fence':(k==='tile_forbidden'?'forb':'floor'));
D.tiles.forEach(t=>{const d=document.createElement('div');d.className='cell '+cls(t.k);
 d.style.left=(t.x*LW)+'px';d.style.top=((D.H-1-t.y)*LH)+'px';
 d.title='['+t.x+','+t.y+'] '+t.k+' build='+t.b;map.appendChild(d);});
const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');
svg.setAttribute('width',D.W*LW);svg.setAttribute('height',D.H*LH);
D.paths.forEach((p,i)=>{if(!p)return;const pl=document.createElementNS(ns,'polyline');
 pl.setAttribute('points',p.map(q=>(q[0]*LW+LW/2)+','+((D.H-1-q[1])*LH+LH/2)).join(' '));
 pl.setAttribute('fill','none');pl.setAttribute('stroke',['#ff5252','#ffb142','#4fa3ff','#c9a0ff'][i%4]);
 pl.setAttribute('stroke-width','3');pl.setAttribute('stroke-dasharray','6 4');svg.appendChild(pl);});
map.appendChild(svg);
const mk=(x,y,txt,c)=>{const e=document.createElement('div');e.className='mk';e.textContent=txt;
 e.style.left=(x*LW+LW/2)+'px';e.style.top=((D.H-1-y)*LH+LH/2)+'px';e.style.color=c;map.appendChild(e);};
D.spawns.forEach(s=>mk(s.x,s.y,s.fly?'✈':'S','#ff5252'));
D.doors.forEach(s=>mk(s.x,s.y,'G','#4fa3ff'));
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
  const arr=(w.arrival!=null)
    ?('<b class="arr"> →T+'+w.arrival+'</b>'+(w.approx?'<i class="apx">≈直线</i>':''))
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
     d.style.cssText='position:absolute;width:'+LW+'px;height:'+LH+'px;left:'+(x*LW)+'px;top:'+((D.H-1-y)*LH)+'px;'
       +'background:'+col+';opacity:'+op+';z-index:3';
     rangeLayer.appendChild(d); }); });
 const o=(D.ops||{})[a.name]||{};
 const tag=document.createElement('div');
 tag.style.cssText='position:absolute;left:'+(x0*LW)+'px;top:'+((D.H-1-y0)*LH-18)+'px;z-index:6;'
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
D.tiles.forEach(t=>{BUILD[t.x+','+t.y]=t.b;});
function typeOK(prof,build){
 if(!prof) return true;
 const rng=(prof==='CASTER'||prof==='MEDIC'||prof==='SNIPER'||prof==='SUPPORT');
 return rng? build==='RANGED' : build==='MELEE';
}
function drawJob(i){
 opsLayer.innerHTML='';
 drawTimeline(i);                            // 判漏/部署时刻与选中作业绑定
 const job=D.jobs[i]; if(!job) return;
 const deploys=job.actions.filter(z=>z.type==='Deploy'&&z.location);
 deploys.forEach((a,ai)=>{
  const x=a.location[0], y=a.location[1];
  const d=document.createElement('div'); d.className='op';
  d.style.left=(x*LW+LW/2)+'px'; d.style.top=((D.H-1-y)*LH+LH/2)+'px';
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
 // 点地图格子 = 把选中的干员移过去（校验格子类型与职业是否匹配）
 map.onclick=(ev)=>{ if(!selOp) return;
   const r=map.getBoundingClientRect();
   const cx=Math.floor((ev.clientX-r.left)/LW), cy=D.H-1-Math.floor((ev.clientY-r.top)/LH);
   if(cx<0||cx>=D.W||cy<0||cy>=D.H) return;
   const b=BUILD[cx+','+cy];
   if(!b||b==='NONE'){ alert('该格不可部署'); return; }
   if(!typeOK(selOp.profession,b)){ alert('格子类型与职业不匹配：'+selOp.profession+' 需 '+(b==='RANGED'?'高台':'地面')); return; }
   selOp.location=[cx,cy]; recompute(i); };

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
  } else { no.textContent=(dn.length+1)+'.';
    txt.innerHTML=a.type+' '+(a.name||'')+(cond?'<span class="cond">'+cond+'</span>':''); }
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
  li.appendChild(up); li.appendChild(dn2); ul.appendChild(li);});
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
function exportJob(i){
 const job=D.jobs[i]; const stage=D.stage||'act16side_ex02';
 const out={version:3,stage_name:stage,minimum_required:'v4.0.0',
  doc:{title:(job.title||'edited')+'（页面编辑）',details:'由排轴可视化导出（保留原 opers 的技能与练度字段）'},
  opers:(function(){const orig=(job.opers||[]).map(o=>Object.assign({},o));const named=new Set(orig.map(o=>o.name));job.actions.filter(a=>a.type==='Deploy').forEach(a=>{ if(a.name && !named.has(a.name)){ orig.push({name:a.name,skill:1,skill_usage:1}); named.add(a.name);} });return orig;})(),
  groups:(job.groups||[]),
  actions:[{type:'SpeedUp'}].concat(job.actions.filter(a=>a.type==='Deploy').map(a=>({type:'Deploy',name:a.name,location:a.location,direction:a.direction}))).concat([{type:'SkillDaemon'}])};
 const blob=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});
 // 命名带上关卡+作业名+日期：旧版 edited_<下拉下标>.json 换一次生成就对不上号
 const dt=new Date(); const ds=''+dt.getFullYear()+String(dt.getMonth()+1).padStart(2,'0')+String(dt.getDate()).padStart(2,'0');
 const safe=(job.title||'job').replace(/[\\/:*?"<>|\s]+/g,'_').slice(0,60);
 const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
 a.download=stage+'__'+safe+'__'+ds+'.json'; a.click();
}
const expBtn=document.createElement('button'); expBtn.textContent='导出当前作业 JSON';
expBtn.onclick=()=>exportJob(+sel.value||0); document.getElementById('top').appendChild(expBtn);

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
  e.style.left=(x*LW+LW/2)+'px'; e.style.top=((D.H-1-y)*LH+LH/2)+'px';
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
