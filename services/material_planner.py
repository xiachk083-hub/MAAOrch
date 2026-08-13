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
REPORT_DIR = ROOT / "data" / "reports"


def load_farm_stages() -> dict:
    """优先读全量备选表 config/farm_stages.json，缺失回退内置单关表。"""
    if FARM_STAGES_PATH.exists():
        return json.loads(FARM_STAGES_PATH.read_text(encoding="utf-8"))
    return FARM_TABLE


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

    # ── 现状全览 ──
    lines.append("")
    lines.append(f"{'材料':<8}{'仓库':>7}{'水位':>7}{'余量':>7}  "
                 f"{'推荐关':<10}{'刷取次数':>8}{'期望收获':>8}{'期望AP':>8}")
    lines.append("-" * 80)
    for r in p["rows"]:
        if r.get("locked"):
            stage = f"锁(最优{r['best']})"
            rounds = "-"
            gain = "-"
            apc = "-"
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
            lines.append(f"  {r['name']}: 缺 {r['gap']} → {r['stage']} × "
                         f"{r['rounds']} 次 (期望 +{r['expected_gain']}, "
                         f"{r['ap_cost']} AP){alt}")
        lines.append(f"  合计 {len(p['gap_fills'])} 个任务 {p['total_ap']} AP")

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
            lines.append(f"  {r['name']}: 缺 {r['gap']} ({r.get('lock_reason', '章节超出')}，"
                         f"最优 {r['best']} 起)")

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
