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
"""
import json
import pathlib

PROJ = pathlib.Path(__file__).resolve().parent.parent
DB = PROJ / "data" / "_enemy_db.json"

# 见模块 docstring：1.0 是实测出来的，不是默认值
MULT = 1.0

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


def calc_arrival(spawn_t, path, enemy_key, level=0, speeds=None, approx=False, mult=None):
    """到达时间。返回 dict：

        {arrival, steps, speed_used, enemy_name, reason, approx}
        arrival 为 None 时 reason 说明缺什么（speed_missing / path_missing），**不猜时间**。
    """
    sp = get_speed(enemy_key, level, speeds=speeds, mult=mult)
    steps = steps_of(path)
    base = {"spawn_t": spawn_t, "enemy_key": enemy_key, "steps": steps,
            "approx": approx, "enemy_name": sp.get("name") if sp else None}
    if sp is None:
        return dict(base, arrival=None, speed_used=None, reason="speed_missing")
    base["speed_used"] = sp["speed"]
    base["motion"] = sp["motion"]
    if steps is None:
        return dict(base, arrival=None, reason="path_missing")
    if not sp["speed"]:
        return dict(base, arrival=None, reason="speed_zero")
    return dict(base, arrival=round(spawn_t + steps / sp["speed"], 2),
                reason=None,
                pos=round(steps / sp["speed"], 2))


def reconcile(stage="act16side_ex02", verbose=True):
    """用本项目已实测的到达时间对账（判断系数对不对）。

    已知实测（docs/GA_EX2_阶段汇总_20260916.md）：
        右路 (10,1)->(10,5) 首怪 T+3，约 T+10 到门
    返回 {lane: (模型算出的最早到达, 该怪的 key/name)}，以及是否与实测吻合。
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import viz_plan as V
    W, H, key_fn, bt, routes, waves = V.level(stage)
    raw = json.loads((PROJ / "data" / ("_level_%s.json" % stage)).read_text(encoding="utf-8"))
    rts = raw["routes"]
    walk = {}
    for a, b, p in routes:
        walk[a] = (p, False)
    speeds = load_enemy_speeds()
    best = {}
    for w in raw.get("waves") or []:
        for fr in w.get("fragments") or []:
            for a in fr.get("actions") or []:
                if a.get("actionType") != "SPAWN":
                    continue
                i = a.get("routeIndex")
                sp, ep = rts[i].get("startPosition") or {}, rts[i].get("endPosition") or {}
                st = (sp.get("col"), sp.get("row"))
                en = (ep.get("col"), ep.get("row"))
                if st == en:
                    continue
                # 地面走 BFS 路径；**BFS 为 None 就是飞行**（跨不过地形），改用直线近似。
                # 注意 level() 会把飞行路线也放进 routes（path=None），所以不能只看 "st in walk"，
                # 否则御4 这类会被当"路径缺失"静默丢掉。
                path, approx = walk.get(st, (None, False))
                if not path:
                    path, approx = line_cells(st, en), True
                r = calc_arrival(a.get("preDelay") or 0, path, a.get("key"), speeds=speeds, approx=approx)
                if r["arrival"] is None:
                    continue
                if st not in best or r["arrival"] < best[st]["arrival"]:
                    best[st] = r
    if verbose:
        for st, r in sorted(best.items(), key=lambda kv: kv[1]["arrival"]):
            print("起点 %-9s 首到 T+%-6s (%s, %s格/秒, %d步%s)"
                  % (str(st), r["arrival"], r["enemy_name"], r["speed_used"], r["steps"],
                     ", 直线近似" if r["approx"] else ""))
    return best


if __name__ == "__main__":
    reconcile()
