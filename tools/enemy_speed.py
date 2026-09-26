"""敌人移速 → 到达时间。

数据源：**本地就有，不需要抓 Wiki**（优先级①）
    data/_enemy_db.json —— 游戏 gamedata 敌人库，2151 条
    移速位置：<Key>.Value[<level>].enemyData.attributes.moveSpeed.m_value
    （同层还有 motion = WALK/FLY，决定路径怎么量）

换算系数 = **1.0**：moveSpeed 本身就是「格/秒」，**已用实测对账**（别改成 0.5）：

    右路 (10,1)->(10,5) 是 9 格 **8 步**；寻路者精锐战士 moveSpeed = 1.1
    → T+3 出怪，3 + 8/1.1 = **T+10.27**，与实测「首 T+3 / 约 T+10 到门」吻合。
    若按流传的「×0.5」→ 8/0.55 = 14.5 → **T+17.5**，与实测差一倍 ⇒ 本数据源不吃 ×0.5。

    ⚠️ 流传的「原始值 × 0.5」在别的数据集上可能对，但在这个文件上被实测否掉了。
    换数据源就必须重做对账（`reconcile()`），不要凭印象改系数。

对账时发现的另一处偏差（留档，别再踩）：
    `docs/GA_EX2_阶段汇总_20260916.md` 记的「左路约 T+16 到门」，等于拿
    7 步 ÷ 1.1（寻路者速度）＋ T+10；但左路 T+10 出的是**战术猎犬（1.9）**，
    实际 7/1.9 = 3.68 → **T+13.68**。也就是那份汇总把右路的速度套到了左路首怪上。
    方向上是"左路更早到"，不改变它"开局顺序错了"的结论，但数字要按数据。

缺失一律标注、绝不猜（`reason` 字段：speed_missing / path_missing / motion_unsupported）。

2026-09-19 修正（根因见 docs/CLEARING_AGENT.md §8d-1）:
  · `reconcile()` 原来用**未翻转**的 raw 行号 `(col,row)` 去查 `viz_plan` 的路径表，而那张表的端点是
    `(col, H-1-row)`（viz_plan.py:124）—— 两套约定不一致 ⇒ 查不到 ⇒ **静默退回直线近似**，
    全量 1068 条出怪里 **0 条**用对路径（322 条张冠李戴 + 746 条直线近似，误差中位 7.5s、42% 超 10s）。
  · 修法：改走 `V.level(stage, all_routes=True)` 并**按 `route_index` 对号**（顺带解决"同起点不同路径"
    的张冠李戴），保留"翻转行号"作退化路线的二级查找；**查不到就记进 `LAST_MISSING` 并跳过，不返回近似值**
    （fail-closed，与"安全闸必须 fail-closed"同源）。
  · 修后验收：32 关 333 个起点的最早到门**全部与真路径一致**（0 缺失、0 不一致）；1068 条出怪 100% 拿到真路径。
    罚站随真路径自动带上（御4 从 T+17.5 修正为 T+52.5）。
"""
import json
import pathlib

PROJ = pathlib.Path(__file__).resolve().parent.parent
DB = PROJ / "data" / "_enemy_db.json"

# 见模块 docstring：1.0 是实测出来的，不是默认值
MULT = 1.0

# `reconcile()` 最近一次跑出来的"路径缺失"清单（fail-closed：缺了就跳过、不给近似值）。
# 调用方想看"这关有没有查不到路径的出怪"读它即可，不必解析 stdout。
LAST_MISSING: list = []

_cache = {}


def load_enemy_speeds(path=None):
    """整库加载 → {key: {name, speed_raw, multiplier, speed_actual, motion, source, levels}}。

    levels 是该敌人的全部 level 档位（部分敌人有 level=1 变体，数值可能为空/0，
    取用时按 get_speed(level=...) 指定，默认 0）。
    """
    p = pathlib.Path(path) if path else DB
    key_cache = str(p)
    if key_cache in _cache:
        return _cache[key_cache]
    if not p.is_file():
        raise FileNotFoundError("找不到敌人数据源：%s" % p)
    data = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for e in data.get("enemies") or []:
        k = e.get("Key")
        if not k:
            continue
        levels = {}
        for ent in (e.get("Value") or []):
            ed = ent.get("enemyData") or {}
            attrs = ed.get("attributes") or {}
            raw = (attrs.get("moveSpeed") or {}).get("m_value")
            if raw is None:
                continue
            levels[ent.get("level") or 0] = {
                "name": (ed.get("name") or {}).get("m_value"),
                "speed_raw": raw,
                "motion": (ed.get("motion") or {}).get("m_value"),
            }
        if not levels:
            continue
        lv0 = levels.get(0) or next(iter(levels.values()))
        out[k] = {
            "name": lv0["name"],
            "speed_raw": lv0["speed_raw"],
            "multiplier": MULT,
            "speed_actual": lv0["speed_raw"] * MULT,
            "motion": lv0["motion"],
            "levels": levels,
            "source": str(p),
        }
    _cache[key_cache] = out
    return out


def get_speed(enemy_key, level=0, path=None, speeds=None, mult=None):
    """查一只怪的移速。查不到返回 None —— **不猜**。

    mult 只用来做**敏感性对照**（1.0 vs 关卡 options.moveMultiplier=0.5），
    默认 None = 用模块常数 MULT。见 docstring 的对账说明。
    """
    m = MULT if mult is None else mult
    table = speeds if speeds is not None else load_enemy_speeds(path)
    rec = table.get(enemy_key)
    if not rec:
        return None
    lv = rec["levels"].get(level)
    if lv and lv.get("speed_raw"):
        return {"key": enemy_key, "name": lv.get("name") or rec["name"], "level": level,
                "speed_raw": lv["speed_raw"], "multiplier": m,
                "speed": lv["speed_raw"] * m,
                "motion": lv.get("motion") or rec["motion"], "source": rec["source"]}
    # 该 level 档位缺失或为 0（如御4 的 level=1 是 0.0）→ 回落 level 0，并标明回落
    lv0 = rec["levels"].get(0)
    if not lv0 or not lv0.get("speed_raw"):
        return None
    return {"key": enemy_key, "name": lv0["name"], "level": 0, "fell_back": True,
            "speed_raw": lv0["speed_raw"], "multiplier": m,
            "speed": lv0["speed_raw"] * m, "motion": lv0["motion"],
            "source": rec["source"]}


def line_cells(a, b):
    """Bresenham 直线经过的格 —— 飞行单位用（它们的路径 BFS 算不出来，见下）。

    地面路径用可走格 BFS（viz_plan.level() 里 already 有，`routes[].path`）。
    飞行单位无视地形，走的是起终点之间的直线；关卡数据只给起终点，
    所以这里按直线近似 —— **这是近似，不是精确**，标注在返回里。
    """
    x0, y0 = a
    x1, y1 = b
    cells, dx, dy = [], abs(x1 - x0), abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx - dy
    while True:
        cells.append((x0, y0))
        if (x0, y0) == (x1, y1):
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy
    return cells


def steps_of(path):
    """路径步数 = 格数 - 1（走到终点格中心即到门）。路径无效返回 None。"""
    if not path or len(path) < 2:
        return None
    return len(path) - 1


def calc_arrival(spawn_t, path, enemy_key, level=0, speeds=None, approx=False, mult=None,
                 waits=None):
    """到达时间。返回 dict：

        {arrival, steps, speed_used, enemy_name, reason, approx, wait, travel}
        arrival 为 None 时 reason 说明缺什么（speed_missing / path_missing），**不猜时间**。

    **停留时间在这个出口就算进去**（2026-09-16 口径统一）：`WAIT_FOR_SECONDS` 是敌人**原地罚站**
    （实测 637 处、最长 240s、涉及 23 关），到门时刻 = 步数 / 移速 **+ 停留**。
    原来这个加法散在调用方 —— `viz_plan` 自己加了一次，于是**把路线拍平成格列表的调用方
    （`job_judge`）静默少算**，同一条路两个口径。现在只在这里加一次。

    `waits=None` → 从 `path` 上取（`viz_plan` 的 `RoutePath.waits`）；
    调用方已经把路线拍平成普通 list（拿不到属性）时，显式给 `waits=<秒>`。
    """
    sp = get_speed(enemy_key, level, speeds=speeds, mult=mult)
    steps = steps_of(path)
    wait_s = float((getattr(path, "waits", 0) or 0) if waits is None else (waits or 0))
    base = {"spawn_t": spawn_t, "enemy_key": enemy_key, "steps": steps,
            "approx": approx, "enemy_name": sp.get("name") if sp else None,
            "wait": wait_s}
    if sp is None:
        return dict(base, arrival=None, speed_used=None, reason="speed_missing")
    base["speed_used"] = sp["speed"]
    base["motion"] = sp["motion"]
    if steps is None:
        return dict(base, arrival=None, reason="path_missing")
    if not sp["speed"]:
        return dict(base, arrival=None, reason="speed_zero")
    travel = steps / sp["speed"]
    return dict(base, arrival=round(spawn_t + travel + wait_s, 2),
                reason=None, travel=round(travel, 2),
                pos=round(travel, 2))       # pos/travel 语义不变（纯走路时长），只加 wait


def reconcile(stage="act16side_ex02", verbose=True):
    """用本项目已实测的到达时间对账（判断系数对不对）。

    已知实测（docs/GA_EX2_阶段汇总_20260916.md）：
        右路 (10,1)->(10,5) 首怪 T+3，约 T+10 到门
    返回 {lane: (模型算出的最早到达, 该怪的 key/name)}，以及是否与实测吻合。
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import viz_plan as V
    # ⚠️ **必须 all_routes=True**（2026-09-19 修）：默认会按"起点格"去重，
    # 而出怪表的 `routeIndex` 索引的是 raw `routes[]` —— 同一出发点下的多条路线走向可能完全不同
    # （PA-1 实测：起点 (1,0) 有 4 条，11 步 vs 21 步）。去重表会把别的路线的路径/罚站套上来。
    W, H, key_fn, bt, routes, waves = V.level(stage, all_routes=True)
    raw = json.loads((PROJ / "data" / ("_level_%s.json" % stage)).read_text(encoding="utf-8"))
    rts = raw["routes"]
    # 两条查到路径的路：① 按 route_index 对号（正解，绕开下面那套行号约定）；
    # ② 退化路线（起点==终点被 viz_plan 跳过）时退回"**翻转后**的起点"查表。
    by_ri, by_start = {}, {}
    for a, b, p in routes:
        if not p:
            continue
        if getattr(p, "route_index", None) is not None:
            by_ri[p.route_index] = (a, b, p)
        by_start.setdefault(a, (b, p))
    speeds = load_enemy_speeds()
    best = {}
    global LAST_MISSING
    LAST_MISSING = []

    def _resolve(i):
        """routeIndex → (起点格, 路径)。查不到返回 (None, None)（**不猜**）。"""
        rt = by_ri.get(i)
        if rt:
            return rt[0], rt[2]
        # ② 翻转行号重试。**旧代码这里用的是 `(col, row)` 未翻转**，而 viz_plan 的路径端点是
        # `(col, H-1-row)`（viz_plan.py:124）—— 两套约定不一致 ⇒ 几乎永远查不到 ⇒ 静默退回
        # 直线近似（实测 1068 条里 0 条用对路径）。这条 flip 就是 P0-1 的修法。
        r = rts[i] if isinstance(i, int) and 0 <= i < len(rts) else None
        sp = (r or {}).get("startPosition") or {}
        if not sp:
            return None, None
        hit = by_start.get((sp.get("col"), H - 1 - sp.get("row")))
        return (hit[0], hit[1]) if hit else (None, None)

    for w in raw.get("waves") or []:
        for fr in w.get("fragments") or []:
            for a in fr.get("actions") or []:
                if a.get("actionType") != "SPAWN":
                    continue
                i = a.get("routeIndex")
                start, path = _resolve(i)
                if not path:
                    # **P0-2（fail-closed）**：查不到就是查不到 —— 记进 LAST_MISSING + 标明原因，
                    # **绝不**退回直线近似给一个"看起来正常"的数（与"安全闸必须 fail-closed"同源）。
                    # 调用方看到 LAST_MISSING / reason="path_missing" 自行决定跳过还是显式标注近似。
                    LAST_MISSING.append({"route_index": i, "key": a.get("key"),
                                         "t": a.get("preDelay"), "reason": "path_missing"})
                    continue
                # 罚站（WAIT_FOR_SECONDS）在路径对象上（`RoutePath.waits`），calc_arrival 会自动带上
                r = calc_arrival(a.get("preDelay") or 0, path, a.get("key"), speeds=speeds,
                                 approx=bool(getattr(path, "approx", False)))
                if r["arrival"] is None:
                    LAST_MISSING.append({"route_index": i, "key": a.get("key"),
                                         "t": a.get("preDelay"), "reason": r.get("reason")})
                    continue
                st = start
                if st not in best or r["arrival"] < best[st]["arrival"]:
                    best[st] = r
    if verbose:
        for st, r in sorted(best.items(), key=lambda kv: kv[1]["arrival"]):
            print("起点 %-9s 首到 T+%-6s (%s, %s格/秒, %d步%s)"
                  % (str(st), r["arrival"], r["enemy_name"], r["speed_used"], r["steps"],
                     ", 直线近似" if r["approx"] else ""))
        if LAST_MISSING:
            print("⚠️ 路径缺失 %d 条（**未给近似值**，按 fail-closed 跳过）：" % len(LAST_MISSING))
            for m in LAST_MISSING[:8]:
                print("   ", m)
    return best


if __name__ == "__main__":
    reconcile()
