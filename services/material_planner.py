"""Material planner — 仓库水位对比 + 当日刷取规划（多日排程）。

模式（用户 2026-08-13 拍板）:
  1. 库存水位补货: 材料低于水位 → 缺口 = 水位 - 仓库（纯刷取，不合成）
  2. 刷取目标只出报告, 不入队（执行仍人工）
  3. 当日有效: 报告基于当次仓库识别, 隔天重新扫仓库重新规划
  4. 仓库无上限: 全达标时刷余量最小(最接近跌破水位)的材料,
     量 = 当日体力预算(ap_budget_per_day)
  5. 每个材料不只一个关可掉: 全量常驻备选表按掉率降序,
     账号主线进度(mainline_chapter)过滤, 锁定顺位选次优
  6. 多日排程: 任务按 AP 消耗升序, 每日预算填满顺延, 末日剩余补刀

用法（CLI）:
    python services/material_planner.py --adb 127.0.0.1:16384 --account 官-0 --chapter 10
    python services/material_planner.py --snapshot data/depot_snapshots/官-0_xxx.json
"""
from __future__ import annotations
import json
import math
import sys
import pathlib

# ── 内置兜底表（config/farm_stages.json 缺失时用）──
# 每个材料可掉落关卡不只一个 — farm_stages.json 是全量常驻备选表
# (tools/material_stages.py --dump-farm 生成, 企鹅物流掉率, 按掉率降序)。

# ── 合成兜底配方（官方 ArknightsGameData building_data.json，2026-08-13）──
# 刷不了高级材料时刷低级去合（用户拍板）。17 材料中只有这 6 个可合成，
# 其余（扭转醇/轻锰矿/研磨石/RMA/凝胶/炽合金/晶体元件/半自然溶剂/
# 化合切削液/环烃聚质/转质盐组）无加工站配方，仅掉落/兑换。
# t1 刷取点 = 固定可刷资源关/1-7（无锁风险）。
CRAFT_RECIPES = {
    "固源岩组": {"t1": "固源岩", "ratio": 5, "stage": "1-7", "gold": 200},
    "糖组":    {"t1": "糖",     "ratio": 4, "stage": "S3-1", "gold": 200},
    "聚酸酯组": {"t1": "聚酸酯",  "ratio": 4, "stage": "S3-3", "gold": 200},
    "异铁组":   {"t1": "异铁",   "ratio": 4, "stage": "S3-2", "gold": 200},
    "酮凝集组": {"t1": "酮凝集",  "ratio": 4, "stage": "S3-5", "gold": 200},
    "装置":    {"t1": "破损装置", "ratio": 3, "stage": "S3-4", "gold": 100},
}

FARM_TABLE = {
    "固源岩组":  [{"stage": "10-6", "ap": 21, "rate": 0.76}],
    "糖组":     [{"stage": "2-5", "ap": 12, "rate": 0.37}],
    "聚酸酯组":  [{"stage": "7-4", "ap": 18, "rate": 0.56}],
    "异铁组":    [{"stage": "14-12", "ap": 21, "rate": 0.52}],
    "酮凝集组":  [{"stage": "14-14", "ap": 21, "rate": 0.78}],
    "装置":     [{"stage": "S3-4", "ap": 15, "rate": 0.92}],
    "扭转醇":    [{"stage": "14-18", "ap": 21, "rate": 0.71}],
    "轻锰矿":    [{"stage": "10-16", "ap": 21, "rate": 0.55}],
    "研磨石":    [{"stage": "9-16", "ap": 18, "rate": 0.40}],
    "RMA70-12": [{"stage": "9-19", "ap": 18, "rate": 0.40}],
    "凝胶":     [{"stage": "14-8", "ap": 21, "rate": 0.49}],
    "炽合金":    [{"stage": "16-14", "ap": 21, "rate": 0.56}],
    "晶体元件":  [{"stage": "16-17", "ap": 21, "rate": 0.71}],
    "半自然溶剂": [{"stage": "17-10", "ap": 21, "rate": 0.47}],
    "化合切削液": [{"stage": "14-11", "ap": 21, "rate": 0.70}],
    "环烃聚质":  [{"stage": "15-20", "ap": 21, "rate": 0.33}],
    "转质盐组":  [{"stage": "14-2", "ap": 21, "rate": 0.42}],
}

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # 直接 python services/xx.py 时根不在 sys.path
CONFIG_PATH = ROOT / "config" / "material_water_levels.json"
FARM_STAGES_PATH = ROOT / "config" / "farm_stages.json"
DROP_MATRIX_PATH = ROOT / "config" / "drop_matrix.json"
REPORT_DIR = ROOT / "data" / "reports"

# LP 求解器（2026-08-16 完整升级: 全局最小理智 + 合成/副产品决策）
# scipy/numpy 缺失时自动回退贪心模式（plan_mode="greedy" 等价旧行为）。
try:
    import numpy as _np
    from scipy.optimize import linprog as _linprog
    _HAS_LP = True
except ImportError:
    _np = None
    _linprog = None
    _HAS_LP = False


def load_farm_stages() -> dict:
    """优先读全量备选表 config/farm_stages.json，缺失回退内置单关表。"""
    if FARM_STAGES_PATH.exists():
        return json.loads(FARM_STAGES_PATH.read_text(encoding="utf-8"))
    return FARM_TABLE


def load_drop_matrix() -> dict:
    """副产品全量表 config/drop_matrix.json（企鹅矩阵, 每关对 17 蓝材料期望）。

    缺失时从 farm_stages 退化：仅主掉落单材料向量（无副产品抵扣）。
    """
    if DROP_MATRIX_PATH.exists():
        return json.loads(DROP_MATRIX_PATH.read_text(encoding="utf-8"))
    dm = {}
    for m, stages in load_farm_stages().items():
        for s in stages:
            dm.setdefault(s["stage"], {"ap": s.get("ap", 18),
                                       "drops": {}, "sample": 0})
    return dm


def load_item_index() -> dict:
    """MAA item_index.json（材料名 <-> itemId 权威映射，与企鹅/工具箱同体系）。"""
    p = ROOT / "services" / "maa" / "source" / "resource" / "item_index.json"
    return json.loads(p.read_text(encoding="utf-8"))


def stage_chapter(code: str) -> int | None:
    """关卡所属章节（用于账号主线进度过滤）。

    "10-6" → 10; "S3-4" → 0 (资源本永远可刷); "R8-11" → 8 (突袭跟章节)。
    无法解析返回 None（规划时保守跳过）。
    """
    import re
    m = re.match(r"^(\d+)-", code)
    if m:
        return int(m.group(1))
    if code.startswith("S"):
        return 0
    m = re.match(r"^[A-Za-z]+(\d+)-", code)
    if m:
        return int(m.group(1))
    return None


def pick_stage(stages: list[dict], chapter: int | None,
               probe_fn=None) -> dict | None:
    """在备选列表里选该账号能刷的最优关。

    chapter=None 不过滤（取掉率第一）；否则取第一个章节 ≤ chapter 的关。
    probe_fn: 可选实测回调 probe_fn(stage) -> {ok, verdict, ...} —
    候选关逐个实测「全权委托」是否可用，锁定的顺位换下一备选关。
    返回 {stage, ap, rate, alt(是否次优替代), best(最优关名), probe}。
    """
    if not stages:
        return None
    if chapter is None:
        cands = list(stages)
    else:
        cands = [s for s in stages
                 if (stage_chapter(s["stage"]) is not None
                     and stage_chapter(s["stage"]) <= chapter)]
    if not cands:
        return None  # 全部超出章节
    if probe_fn is None:
        s = cands[0]
        return {"stage": s["stage"], "ap": s["ap"], "rate": s["rate"],
                "alt": cands[0] is not stages[0] and stages[0] not in cands,
                "best": stages[0]["stage"], "probe": None}
    for s in cands:
        pr = probe_fn(s["stage"])
        if pr.get("ok") in (True, None):  # None=没测到, 不因不确定锁关
            return {"stage": s["stage"], "ap": s["ap"], "rate": s["rate"],
                    "alt": s is not stages[0], "best": stages[0]["stage"],
                    "probe": pr}
    return None  # 全部实测锁定


def load_config(path: pathlib.Path | None = None) -> dict:
    p = path or CONFIG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def load_snapshot(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def observed_daily_ap(aid: str) -> float | None:
    """实测每日体力消耗（2026-08-13 用户: 号是自己刷的，用实测预测）。

    - 统计每次运行的首末体力差求和（runner 记录 sanity_before/after）
    - 排除吃了理智药的运行：我们策略普通药关、只有 48h 过期药自动吃，
      过期药会挤一天刷完导致当天消耗虚高（用户点出）
    - 窗口 7 天（24h 太短会撞上突击日），按有数据的自然日取平均
    无有效数据 → None（调用方回退配置默认预算 ap_budget_per_day）。
    """
    import time as _t
    try:
        from models.stats import RunStats
        now_ts = _t.time()
        runs = RunStats(aid).runs
        daily: dict = {}
        for r in runs:
            ts = str(r.get("ts", ""))
            try:
                t = _t.mktime(_t.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                continue
            if t <= now_ts - 7 * 86400:
                continue
            if r.get("medicine_used"):
                continue  # 过期药突击日不计入常态
            b = r.get("sanity_before")
            a = r.get("sanity_after")
            if (isinstance(b, dict) and isinstance(a, dict)
                    and b.get("current") is not None and a.get("current") is not None):
                d = int(b["current"]) - int(a["current"])
                if d >= 0:  # 负数=吃药补体力（已按 medicine_used 排除，兜底）
                    day = ts[:10]
                    if ts[11:16] < "04:00":
                        # 游戏日 04:00 界（2026-08-14 对齐）：凌晨跑归前一游戏日
                        import datetime as _dt
                        day = (_dt.datetime.strptime(ts[:10], "%Y-%m-%d")
                               - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
                    daily[day] = daily.get(day, 0.0) + d
        if not daily:
            return None
        avg = sum(daily.values()) / len(daily)
        if avg < 60:  # 平均日消耗异常低（基本没刷）→ 不算数
            return None
        return round(avg, 1)
    except Exception:
        return None


def plan(snapshot: dict, config: dict, budget: int | None = None,
         chapter: int | None = None, probe_fn=None) -> dict:
    """规划入口: 按配置 plan_mode 分发。

    - "lp"（默认, 需 scipy）: 全局最小理智线性规划, 合成+副产品抵扣
    - "greedy": 旧贪心（每材料 top1 关, 纯刷）; scipy 缺失时自动回退
    """
    mode = config.get("plan_mode", "lp")
    if mode == "lp" and _HAS_LP:
        try:
            return plan_lp(snapshot, config, budget=budget, chapter=chapter,
                           probe_fn=probe_fn)
        except Exception as e:  # LP 异常兜底, 不阻塞扫描
            import traceback as _tb
            _tb.print_exc()
            cfg2 = dict(config)
            cfg2["plan_mode"] = "greedy"
            cfg2["_lp_error"] = str(e)[:300]
            return plan_greedy(snapshot, cfg2, budget=budget, chapter=chapter,
                               probe_fn=probe_fn)
    return plan_greedy(snapshot, config, budget=budget, chapter=chapter,
                       probe_fn=probe_fn)


def plan_greedy(snapshot: dict, config: dict, budget: int | None = None,
                chapter: int | None = None, probe_fn=None) -> dict:
    """Water-level planning. Returns full plan dict for report rendering.

    chapter: 账号主线进度（None=不过滤，如 10=最多能刷 10 章）。
    probe_fn: 可选实测回调 probe_fn(stage) -> {ok, verdict, detail} —
    选中关逐个实测代理可用性，锁定顺位换备选；结果记入 probe 字典。
    """
    farm = load_farm_stages()
    levels: dict = config.get("materials", {})
    budget = budget if budget is not None else int(config.get("ap_budget_per_day", 1000))
    counts = snapshot.get("named", {})
    if snapshot.get("error"):
        return {"error": snapshot["error"]}

    rows = []
    for name, level in levels.items():
        cur = counts.get(name, 0)
        gap = max(0, level - cur)
        rows.append({"name": name, "cur": cur, "level": level,
                     "margin": cur - level, "gap": gap})

    probe_log: dict = {}
    gap_fills: list[dict] = []
    skipped: list[dict] = []      # 不在表内
    locked: list[dict] = []       # 有备选关但全部超出账号章节 / 全部实测锁定
    for r in rows:
        if r["gap"] <= 0:
            continue
        stages = farm.get(r["name"])
        if not stages:
            skipped.append(r)
            continue
        st = pick_stage(stages, chapter, probe_fn)
        if not st:
            reason = "章节超出进度"
            if probe_fn is not None:
                legal = [s for s in stages
                         if (stage_chapter(s["stage"]) is not None
                             and (chapter is None
                                  or stage_chapter(s["stage"]) <= chapter))]
                if legal:
                    reason = "代理实测锁定"
            locked.append(r)
            r.update({"locked": True, "best": stages[0]["stage"],
                      "lock_reason": reason})
            # 合成兜底（2026-08-13 用户: 都刷不了 → 刷低级去合）
            if r["name"] in CRAFT_RECIPES:
                rc = CRAFT_RECIPES[r["name"]]
                r["craft"] = {"t1": rc["t1"], "ratio": rc["ratio"],
                              "stage": rc["stage"], "gold": rc["gold"],
                              "need": r["gap"] * rc["ratio"]}
            else:
                r["craft"] = None  # 无加工站配方，仅掉落/兑换
            continue
        if st.get("probe"):
            probe_log[st["stage"]] = {k: st["probe"].get(k)
                                      for k in ("ok", "verdict", "score", "detail")}
        rounds = math.ceil(r["gap"] / st["rate"])
        r.update({"stage": st["stage"], "rounds": rounds,
                  "expected_gain": round(rounds * st["rate"], 1),
                  "ap_cost": rounds * st["ap"]})
        if st.get("alt"):
            r["alt_of"] = st.get("best")
        gap_fills.append(r)

    # ── 多日排程: 按 AP 消耗升序（便宜先刷），每日预算填满顺延 ──
    gap_fills.sort(key=lambda x: x["ap_cost"])
    days: list[dict] = []
    for f in gap_fills:
        f["rounds_remaining"] = f["rounds"]
    last_leftover = budget if gap_fills else 0
    while any(f["rounds_remaining"] > 0 for f in gap_fills):
        day_items: list[dict] = []
        ap_used = 0
        budget_left = budget
        for f in gap_fills:
            if budget_left <= 0 or f["rounds_remaining"] <= 0:
                continue
            f_ap = f["ap_cost"] / f["rounds"]  # 单次 AP（rounds>0 恒真）
            n = min(f["rounds_remaining"], int(budget_left // f_ap))
            if n <= 0:
                continue
            f["rounds_remaining"] -= n
            day_items.append({"name": f["name"], "stage": f["stage"], "rounds": n})
            ap_used += int(n * f_ap)
            budget_left -= int(n * f_ap)
        if not day_items:
            break  # 剩余预算连一次都刷不起
        days.append({"items": day_items, "ap_used": ap_used,
                     "leftover": int(budget_left)})
        last_leftover = int(budget_left)
    for f in gap_fills:
        f.pop("rounds_remaining", None)

    # 补刀: 缺口排完后（或全达标），余量最小且有可刷关的材料吃剩余/当日预算
    topup = None
    topup_stage = None
    for r in sorted(rows, key=lambda x: x["margin"]):
        stages = farm.get(r["name"])
        if not stages:
            continue
        st = pick_stage(stages, chapter, probe_fn)
        if st and all(f["name"] != r["name"] for f in gap_fills):
            topup = r
            topup_stage = st
            break
    topup_rounds = 0
    if topup:
        budget_for_topup = budget if not gap_fills else last_leftover
        topup_rounds = max(0, budget_for_topup // topup_stage["ap"])
        if topup_rounds:
            topup.update({"stage": topup_stage["stage"], "rounds": topup_rounds,
                          "expected_gain": round(topup_rounds * topup_stage["rate"], 1),
                          "ap_cost": topup_rounds * topup_stage["ap"]})
            if topup_stage.get("alt"):
                topup["alt_of"] = topup_stage.get("best")

    met = [r for r in rows if r["gap"] == 0 and r["name"] in farm]
    return {
        "account": snapshot.get("account", ""),
        "account_id": snapshot.get("account_id", ""),
        "ts": snapshot.get("ts", ""),
        "snapshot_file": snapshot.get("_snapshot_file", ""),
        "budget": budget,
        "chapter": chapter,
        "rows": rows,
        "gap_fills": gap_fills,
        "skipped": skipped,
        "locked": locked,
        "days": days,
        "total_ap": sum(f["ap_cost"] for f in gap_fills),
        "topup": topup if topup_rounds else None,
        "probe": probe_log,
        "met_count": len(met),
        "total": len(levels),
        "all_met": not gap_fills,
    }


# ═══════════════════════════════════════════════════════════════════
# LP 全局规划（2026-08-16 完整升级: 最小理智 + 合成决策 + 副产品抵扣）
# ═══════════════════════════════════════════════════════════════════

def _name2id() -> dict:
    try:
        return {v["name"]: k for k, v in load_item_index().items()}
    except Exception:
        return {}


def _solve_lp(cands: dict, mat_drops: dict, gaps: dict, name2id: dict,
              allow_craft: bool, counts: dict) -> dict | None:
    """核心 LP: min Σ ap_s·x_s, s.t. ∀m Σ_s drop_{s,m}·x_s + y_m ≥ gap_m。

    cands:     {code: {"ap": int}}
    mat_drops: {材料名: {code: rate}} — 每材料合格关的掉率（已按效率门槛过滤）
    gaps:      {材料名: 缺口数}
    返回 {"x": {code: 次数(实数)}, "y": {材料: 合成数(实数)}} 或 None(无解)。
    """
    mat_names = list(gaps.keys())
    codes = sorted(cands.keys())
    craft_names = ([m for m in mat_names if m in CRAFT_RECIPES]
                   if allow_craft else [])
    n_stage, n_craft = len(codes), len(craft_names)

    c = _np.array([cands[cd]["ap"] for cd in codes] + [0.0] * n_craft)
    A_ub, b_ub = [], []
    for i, m in enumerate(mat_names):
        drops = mat_drops.get(m, {})
        row = [drops.get(cd, 0.0) for cd in codes]
        if m in craft_names:
            ci = craft_names.index(m)
            # 合成变量系数=1（1 个成品补 1 个缺口）；ratio 只用于库存上限
            row += [1.0 if j == ci else 0.0 for j in range(n_craft)]
        else:
            row += [0.0] * n_craft
        A_ub.append([-v for v in row])
        b_ub.append(-gaps[m])
    bounds = [(0, None)] * n_stage
    for m in craft_names:
        t1 = CRAFT_RECIPES[m]["t1"]
        have = int(counts.get(t1, 0))
        bounds.append((0, max(0, have // CRAFT_RECIPES[m]["ratio"])))
    res = _linprog(c, A_ub=_np.array(A_ub), b_ub=_np.array(b_ub),
                   bounds=bounds, method="highs")
    if not res.success:
        return None
    x = res.x
    return {
        "x": {cd: float(x[j]) for j, cd in enumerate(codes) if x[j] > 1e-9},
        "y": {m: float(x[n_stage + k]) for k, m in enumerate(craft_names)
              if x[n_stage + k] > 1e-9},
    }


def _build_days_from_stage_times(stage_times: dict, gaps: dict,
                                 budget: int) -> list:
    """关级任务多日排程: 单次 AP 升序, 每日预算填满顺延。

    stage_times: {code: {times, ap, name(主材料名)}}。返回 days 列表。
    """
    tasks = sorted(stage_times.values(), key=lambda t: t["ap"])
    remain = {t["stage"]: t["times"] for t in tasks}
    days: list[dict] = []
    while any(v > 0 for v in remain.values()):
        day_items = []
        ap_used = 0
        budget_left = budget
        for t in tasks:
            st, ap = t["stage"], t["ap"]
            if budget_left <= 0 or remain[st] <= 0:
                continue
            n = min(remain[st], budget_left // ap)
            if n <= 0:
                continue
            remain[st] -= n
            day_items.append({"name": t["name"], "stage": st, "rounds": n})
            ap_used += int(n * ap)
            budget_left -= int(n * ap)
        if not day_items:
            break
        days.append({"items": day_items, "ap_used": ap_used,
                     "leftover": int(budget_left)})
    return days


def plan_lp(snapshot: dict, config: dict, budget: int | None = None,
            chapter: int | None = None, probe_fn=None) -> dict:
    """LP 全局最小理智规划（合成+副产品抵扣, 多关混合）。

    与 plan_greedy 同签名/同输出结构（days/gap_fills/locked/topup 兼容），
    gap_fills 每材料一条: farm=[{stage,rounds}]（可多关）+ craft 合成字段。
    """
    farm = load_farm_stages()
    drop_mx = load_drop_matrix()
    levels: dict = config.get("materials", {})
    budget = budget if budget is not None else int(config.get("ap_budget_per_day", 1000))
    allow_craft = bool(config.get("allow_craft", True))
    counts = snapshot.get("named", {})
    if snapshot.get("error"):
        return {"error": snapshot["error"]}
    name2id = _name2id()

    rows = []
    for name, level in levels.items():
        cur = counts.get(name, 0)
        gap = max(0, level - cur)
        rows.append({"name": name, "cur": cur, "level": level,
                     "margin": cur - level, "gap": gap})
    gaps = {r["name"]: r["gap"] for r in rows if r["gap"] > 0}

    # ── 候选关: farm 备选并集 ∩ drop_matrix（副产品向量全量）──
    cand: dict = {}
    for m, stages in farm.items():
        for s in stages:
            code = s["stage"]
            dm = drop_mx.get(code)
            if not dm:
                continue
            cand.setdefault(code, {"ap": dm["ap"]})
    if chapter is not None:
        cand = {c: v for c, v in cand.items()
                if (stage_chapter(c) is not None and stage_chapter(c) <= chapter)}

    # ── 效率门槛: 每材料只留 eap ≤ 最优 eap × factor 的关（2026-08-16 模拟暴露:
    #    候选被压缩到只剩超低掉率关时, LP 会硬刷上千次凑缺口 → 必须锁给用户）──
    eap_factor = float(config.get("lp_eap_factor", 2.5))
    mat_drops: dict = {}  # {材料名: {code: rate}} 合格关掉率
    for m, stages in farm.items():
        if not stages:
            continue
        best_eap = stages[0].get("ap", 18) / max(stages[0]["rate"], 1e-6)
        cap_eap = best_eap * eap_factor
        ok: dict = {}
        for s in stages:
            code = s["stage"]
            if code not in cand:
                continue
            rate = drop_mx.get(code, {}).get("drops", {}).get(name2id.get(m, ""), 0)
            if rate <= 0:
                continue
            eap = cand[code]["ap"] / rate
            if eap <= cap_eap or rate >= 0.25:  # 绝对掉率下限兜底(好关不许被门槛误杀)
                ok[code] = rate
        if ok:
            mat_drops[m] = ok

    probe_log: dict = {}
    banned: set = set()

    def _cand_now():
        return {c: v for c, v in cand.items() if c not in banned}

    # 无合格关/不可合成的材料 → locked（craft 兜底）
    locked: list[dict] = []
    gap_pool = dict(gaps)
    solved = {m: g for m, g in gap_pool.items()
              if mat_drops.get(m) or (m in CRAFT_RECIPES and allow_craft)}
    unsolved = {m: g for m, g in gap_pool.items() if m not in solved}
    for m, g in unsolved.items():
        r = next((x for x in rows if x["name"] == m), {"name": m})
        if m in CRAFT_RECIPES and allow_craft:
            rc = CRAFT_RECIPES[m]
            locked.append({**r, "locked": True, "best": "",
                           "lock_reason": "无合格掉落关(效率门槛), 用合成兜底",
                           "craft": {"t1": rc["t1"], "ratio": rc["ratio"],
                                     "stage": rc["stage"], "gold": rc["gold"],
                                     "need": g * rc["ratio"]}})
        else:
            locked.append({**r, "locked": True, "best": "",
                           "lock_reason": "无合格掉落关且无合成配方"})
    gaps = solved

    sol = None
    if gaps:
        for _round in range(4):  # probe 锁定迭代重解
            sol = _solve_lp(_cand_now(), mat_drops, gaps, name2id,
                            allow_craft, counts)
            if sol is None:
                break
            if probe_fn is None:
                break
            # 解出的关逐个实测, 锁定剔除重解
            new_banned = []
            for cd in sol["x"]:
                pr = probe_fn(cd)
                probe_log[cd] = {k: pr.get(k) for k in ("ok", "verdict", "score", "detail")}
                if pr.get("ok") is False:
                    new_banned.append(cd)
            if not new_banned:
                break
            banned.update(new_banned)

    # LP 无解(如合格关全被 probe 锁死/合成上限不足): 剩余缺口转 locked 兜底,
    # 绝不允许静默丢失缺口（2026-08-16 模拟暴露）。
    if gaps and sol is None:
        for m, g in list(gaps.items()):
            gaps.pop(m)
            r = next((x for x in rows if x["name"] == m), {"name": m})
            if m in CRAFT_RECIPES and allow_craft:
                rc = CRAFT_RECIPES[m]
                locked.append({**r, "locked": True, "best": "",
                               "lock_reason": "可用关不足以覆盖缺口, 用合成兜底",
                               "craft": {"t1": rc["t1"], "ratio": rc["ratio"],
                                         "stage": rc["stage"], "gold": rc["gold"],
                                         "need": g * rc["ratio"]}})
            else:
                locked.append({**r, "locked": True, "best": "",
                               "lock_reason": "可用关不足以覆盖缺口且无合成配方"})

    # ── 结果组装 ──
    gap_fills: list[dict] = []
    stage_times: dict = {}
    if sol:
        times = {cd: math.ceil(v) for cd, v in sol["x"].items()}
        craft_y = {m: math.ceil(v) for m, v in sol["y"].items()}
        for m, g in gaps.items():
            drops_m = mat_drops.get(m, {})
            farm_rows = []
            for cd, t in times.items():
                rate = drops_m.get(cd, 0)
                if rate > 0:
                    farm_rows.append({"stage": cd, "rounds": t, "rate": rate,
                                      "gain": round(t * rate, 1)})
            farm_rows.sort(key=lambda x: -x["gain"])
            craft = None
            if m in craft_y and craft_y[m] > 0:
                rc = CRAFT_RECIPES[m]
                craft = {"t1": rc["t1"], "ratio": rc["ratio"],
                         "stage": rc["stage"], "gold": rc["gold"],
                         "num": craft_y[m], "need": craft_y[m] * rc["ratio"]}
            gain_total = round(sum(f["gain"] for f in farm_rows), 1)
            if not farm_rows and not craft:
                continue
            if farm_rows:
                main = farm_rows[0]
            else:
                main = {"stage": "🔧合成", "rounds": craft["num"]}
            for fr in farm_rows:
                stage_times.setdefault(fr["stage"],
                                       {"stage": fr["stage"], "ap": cand[fr["stage"]]["ap"],
                                        "times": 0, "name": m})
                stage_times[fr["stage"]]["times"] = fr["rounds"]
            ap_cost = sum(fr["rounds"] * cand[fr["stage"]]["ap"]
                          for fr in farm_rows)
            gap_fills.append({
                "name": m, "cur": next(r["cur"] for r in rows if r["name"] == m),
                "level": next(r["level"] for r in rows if r["name"] == m),
                "gap": g,
                "stage": main["stage"], "rounds": main["rounds"],
                "expected_gain": gain_total,
                "ap_cost": ap_cost,
                "farm": farm_rows,
                "craft": craft,
            })
        # 同关多材料: times 取最大（同关只刷一次, 次数即 LP 解）
        for cd, t in times.items():
            if cd in stage_times:
                stage_times[cd]["times"] = t

    days = _build_days_from_stage_times(stage_times, gaps, budget)

    # ── 补刀: 排程末剩余预算 → 余量最小材料（与 greedy 同）──
    last_leftover = budget
    if days:
        last_leftover = days[-1]["leftover"]
    topup = None
    if not locked and gaps:
        for r in sorted(rows, key=lambda x: x["margin"]):
            stages = farm.get(r["name"])
            if not stages or r["name"] in gaps:
                continue
            st = pick_stage(stages, chapter, probe_fn)
            if st:
                topup_r = last_leftover // st["ap"]
                if topup_r > 0:
                    topup = {**r, "stage": st["stage"], "rounds": topup_r,
                             "expected_gain": round(topup_r * st["rate"], 1),
                             "ap_cost": topup_r * st["ap"]}
                break

    skipped = [r for r in rows if r["gap"] > 0 and r["name"] not in farm
               and r["name"] not in gaps and r["name"] not in
               {l["name"] for l in locked}]
    met = [r for r in rows if r["gap"] == 0 and r["name"] in farm]
    total_ap = sum(t["ap"] * t["times"] for t in stage_times.values())
    return {
        "account": snapshot.get("account", ""),
        "account_id": snapshot.get("account_id", ""),
        "ts": snapshot.get("ts", ""),
        "snapshot_file": snapshot.get("_snapshot_file", ""),
        "budget": budget,
        "chapter": chapter,
        "plan_mode": "lp",
        "allow_craft": allow_craft,
        "lp_error": config.get("_lp_error"),
        "rows": rows,
        "gap_fills": gap_fills,
        "skipped": skipped,
        "locked": locked,
        "days": days,
        "total_ap": total_ap,
        "topup": topup,
        "probe": probe_log,
        "met_count": len(met),
        "total": len(levels),
        "all_met": not gap_fills,
    }


def render_report(p: dict) -> str:
    if p.get("error"):
        return f"ERROR: {p['error']}"

    lines: list[str] = []
    title = f"{p['account'] or '账号'} 仓库规划  {p['ts']}  (当日有效)"
    lines.append("=" * len(title))
    lines.append(title)
    lines.append("=" * len(title))

    if p["all_met"]:
        lines.append(f"✅ 全部达标: {p['met_count']}/{p['total']} 材料均在水位之上")
    else:
        fills = p["gap_fills"]
        skipped = p["skipped"]
        locked = p["locked"]
        extra = []
        if skipped:
            extra.append(f"{len(skipped)} 个无推荐关已跳过")
        if locked:
            extra.append(f"{len(locked)} 个全部关卡超出进度锁定")
        lines.append(f"⚠️  {p['met_count']}/{p['total']} 达标, "
                     f"{len(fills)} 个材料低于水位" +
                     (f", {'、'.join(extra)}" if extra else ""))
    if p.get("chapter") is not None:
        lines.append(f"主线进度过滤: ≤ {p['chapter']} 章（备选关顺位选取）")
    if p.get("plan_mode") == "lp":
        craft_note = "合成+副产品抵扣" if p.get("allow_craft") else "纯刷(副产品抵扣)"
        lines.append(f"规划模式: LP 全局最小理智（{craft_note}）")

    # ── 现状全览 ──
    lines.append("")
    lines.append(f"{'材料':<8}{'仓库':>7}{'水位':>7}{'余量':>7}  "
                 f"{'推荐关':<10}{'刷取次数':>8}{'期望收获':>8}{'期望AP':>8}")
    lines.append("-" * 80)
    fill_by_name = {f["name"]: f for f in p["gap_fills"]}
    for r in p["rows"]:
        if r.get("locked"):
            stage = f"锁(最优{r['best']})"
            rounds = "-"
            gain = "-"
            apc = "-"
        else:
            f = fill_by_name.get(r["name"])
            if f:
                stage = f.get("stage", "")
                rounds = str(f.get("rounds", "-"))
                gain = f"{f.get('expected_gain', 0)}" if f.get("rounds") else "-"
                apc = str(f.get("ap_cost", "-"))
            else:
                stage = r.get("stage", "")
                if r.get("alt_of"):
                    stage += " *"
                rounds = str(r.get("rounds", "-"))
                gain = f"{r.get('expected_gain', 0)}" if r.get("rounds") else "-"
                apc = str(r.get("ap_cost", "-"))
        lines.append(f"{r['name']:<8}{r['cur']:>7}{r['level']:>7}{r['margin']:>+7}  "
                     f"{stage:<10}{rounds:>8}{gain:>8}{apc:>8}")

    # ── 刷取任务（按 AP 消耗升序）──
    if p["gap_fills"]:
        lines.append("")
        lines.append("── 刷取任务（按 AP 消耗升序）──")
        for r in p["gap_fills"]:
            alt = f"（次优关，最优 {r['alt_of']} 超出进度）" if r.get("alt_of") else ""
            farm = r.get("farm") or [{"stage": r.get("stage"),
                                      "rounds": r.get("rounds", 0)}]
            src = " + ".join(f"{x['stage']}×{x['rounds']}" for x in farm if x.get("stage"))
            lines.append(f"  {r['name']}: 缺 {r['gap']} → {src} "
                         f"(期望 +{r['expected_gain']}, {r['ap_cost']} AP){alt}")
            if r.get("craft"):
                c = r["craft"]
                lines.append(f"    🔧 合成: {c['t1']}×{c['need']} "
                             f"→ 合 {c['num']} 个 ({c['gold']} 龙门币/个)")
        lines.append(f"  合计 {len(p['gap_fills'])} 个任务 {p['total_ap']} AP")
        craft_cost = sum((r.get("craft") or {}).get("num", 0)
                         * (r.get("craft") or {}).get("gold", 0)
                         for r in p["gap_fills"])
        if craft_cost:
            lines.append(f"  🔧 合成龙门币: 约 {craft_cost:,}")

        # ── 多日排程 ──
        lines.append("")
        lines.append(f"── 多日排程（AP 升序，每日预算 {p['budget']} AP，排满顺延）──")
        for i, d in enumerate(p["days"], 1):
            items = " + ".join(f"{x['name']}×{x['rounds']}" for x in d["items"])
            lines.append(f"  D{i:<3} {items}  ({d['ap_used']} AP)")
        lines.append(f"  → 共 {len(p['days'])} 天完成")
    if p["skipped"]:
        lines.append("")
        lines.append("── 无推荐关，跳过 ──")
        for r in p["skipped"]:
            lines.append(f"  {r['name']}: 缺 {r['gap']} (不在 17 表内)")
    if p["locked"]:
        lines.append("")
        lines.append("── 无法刷取 ──")
        for r in p["locked"]:
            best_txt = f"，最优 {r['best']} 起" if r.get("best") else ""
            if r.get("craft"):
                c = r["craft"]
                lines.append(f"  {r['name']}: 缺 {r['gap']} ({r.get('lock_reason', '锁定')}{best_txt})"
                             f" → 🔧 合成兜底: 刷 {c['t1']}({c['stage']}) "
                             f"{c['need']} 个 → 合成 {r['gap']} 个 ({c['gold']} 龙门币/个)")
            else:
                lines.append(f"  {r['name']}: 缺 {r['gap']} ({r.get('lock_reason', '锁定')}{best_txt})"
                             f" → 无合成配方（仅掉落/兑换）")

    # ── 代理预检结果 ──
    if p.get("probe"):
        lines.append("")
        lines.append("── 代理预检（全权委托实测）──")
        for stage, pr in p["probe"].items():
            v = pr.get("verdict", "")
            tag = "🟢可刷" if pr.get("ok") is True else ("⚪未测" if pr.get("ok") is None else "🔴锁定")
            lines.append(f"  {stage}: {tag} ({v} {pr.get('detail', '')})")

    # ── 补刀 ──
    if p["topup"]:
        t = p["topup"]
        lines.append("")
        tag = "当日预算" if p["all_met"] else "排程末剩余预算"
        lines.append(f"── 补刀: 刷余量最小材料（{tag}）──")
        lines.append(f"  {t['name']} (余量 {t['margin']:+}) → {t['stage']} × "
                     f"{t['rounds']} 次 (期望 +{t['expected_gain']}, "
                     f"{t['ap_cost']} AP)")

    # ── 预警块: 距水位最近前 5（全达标时提醒下一步）──
    margin_rows = [r for r in p["rows"] if r["name"] in FARM_TABLE]
    near = sorted(margin_rows, key=lambda x: x["margin"])[:5]
    lines.append("")
    lines.append("── 距水位最近前 5 ──")
    for r in near:
        warn = " ⚠️" if r["margin"] <= 10 else ""
        lines.append(f"  {r['name']}: 仓库 {r['cur']} / 水位 {r['level']} "
                     f"(余量 {r['margin']:+}){warn}")

    lines.append("")
    lines.append(f"体力预算: {p['budget']} AP/日  |  "
                 f"快照: {p['snapshot_file']}")
    return "\n".join(lines)


def run_cli() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="仓库水位规划报告")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--adb", help="adb 地址（在线识别）")
    g.add_argument("--snapshot", help="使用已有快照 JSON（离线规划）")
    ap.add_argument("--account", default="", help="账号标签")
    ap.add_argument("--client-type", default="Official")
    ap.add_argument("--budget", type=int, default=None, help="覆盖当日 AP 预算")
    ap.add_argument("--chapter", type=int, default=None,
                    help="账号主线进度（如 10=只规划 ≤10 章的关）")
    ap.add_argument("--config", default=None, help="水位配置 JSON 路径")
    args = ap.parse_args()

    if args.snapshot:
        snap = load_snapshot(pathlib.Path(args.snapshot))
        snap.setdefault("_snapshot_file", str(pathlib.Path(args.snapshot).resolve()))
    else:
        from services.depot_reader import DepotReader
        reader = DepotReader(args.adb, account=args.account,
                             client_type=args.client_type)
        snap = reader.read()

    cfg = load_config(pathlib.Path(args.config) if args.config else None)
    result = plan(snap, cfg, budget=args.budget, chapter=args.chapter)
    report = render_report(result)
    print()
    print(report)

    if result.get("error"):
        sys.exit(1)
    # 报告落盘
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = result["account"] or "emu"
    out = REPORT_DIR / f"{tag}_{result['ts']}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n报告已存: {out}")


if __name__ == "__main__":
    run_cli()
