# -*- coding: utf-8 -*-
"""真实场景模拟: 账号大量关卡刷不了, LP 规划的鲁棒性测试。

模拟维度:
1. 候选关随机受限: 每个常驻关有 (1-avail_rate) 概率"刷不了"
   (未解锁/代理不可用/剿灭导航失败等), 每种可用率跑 N 次随机。
2. probe 锁定迭代: 模拟 LP 选中的关实测发现不可刷 → 剔除重解
   (plan_lp 内置迭代, 这里传随机 probe_fn 演示)。

统计: 求解成功率 / 平均AP(含最差) / locked 兜底数 / 合成占比。
"""
import sys
import json
import random
import statistics
sys.path.insert(0, r"D:\1-2\MAAOrch")
from services import material_planner as mp

random.seed(20260816)

SNAP = json.loads(open(r"D:\1-2\MAAOrch\data\depot_snapshots\本机0号_20260813_044448.json",
                       encoding="utf-8").read())
CFG = json.loads(open(r"D:\1-2\MAAOrch\config\material_water_levels.json",
                      encoding="utf-8").read())
BUDGET = 280


def run(avail_rate: float, chapter=None, probe_deny_rate: float = 0.0,
        budget=BUDGET) -> dict:
    """一次模拟: 随机生成可用关集合, 跑 plan_lp。"""
    farm = mp.load_farm_stages()
    # 随机禁关: 每个候选关独立 (1-avail_rate) 概率不可用
    banned = set()
    for m, stages in farm.items():
        for s in stages:
            if random.random() > avail_rate:
                banned.add(s["stage"])
    # 通过 probe_fn 把禁关判为"实测不可刷"（plan_lp 的 probe 迭代会剔除重解）
    def probe_fn(stage):
        if stage in banned:
            return {"ok": False, "verdict": "随机模拟不可刷", "detail": "模拟锁定"}
        if probe_deny_rate > 0 and random.random() < probe_deny_rate:
            return {"ok": False, "verdict": "随机实测失败", "detail": "模拟锁定"}
        return {"ok": True, "verdict": "可刷", "detail": ""}

    cfg = dict(CFG)
    cfg["plan_mode"] = "lp"
    p = mp.plan(SNAP, cfg, budget=budget, chapter=chapter, probe_fn=probe_fn)
    n_cand = sum(len(v) for v in farm.values())
    fills = p.get("gap_fills", [])
    locked = p.get("locked", [])
    craft_total = sum((f.get("craft") or {}).get("num", 0) for f in fills)
    return {
        "ok": p.get("total_ap") is not None and not p.get("error"),
        "total_ap": p.get("total_ap", 0),
        "fills": len(fills),
        "locked": len(locked),
        "craft": craft_total,
        "banned_ratio": len(banned) / n_cand,
        "days": len(p.get("days", [])),
        "plan": p,
    }


def summary(avail_rate: float, n=30, chapter=None, probe_deny=0.0):
    res = [run(avail_rate, chapter=chapter, probe_deny_rate=probe_deny)
           for _ in range(n)]
    ok = [r for r in res if r["ok"]]
    aps = [r["total_ap"] for r in ok]
    locks = [r["locked"] for r in res]
    crafts = [r["craft"] for r in ok]
    tag = f"chapter={chapter}, " if chapter else ""
    print(f"[可用率 {avail_rate:.0%} {tag}N={n}] "
          f"成功率 {len(ok)}/{n} | "
          f"AP: 中位 {statistics.median(aps):.0f} "
          f"(min {min(aps):.0f} / max {max(aps):.0f})" if ok else
          f"[可用率 {avail_rate:.0%}] 全失败!")
    if ok:
        print(f"        locked 均值 {statistics.mean(locks):.1f} | "
              f"合成材料均值 {statistics.mean(crafts):.1f} | "
              f"天数中位 {statistics.median([r['days'] for r in ok])}")
    return res


print("=" * 70)
print("场景 1: 候选关随机受限（未解锁/代理不可用等）— 各可用率 30 次随机")
print("=" * 70)
for rate in (1.0, 0.8, 0.6, 0.4, 0.2):
    summary(rate)

print()
print("=" * 70)
print("场景 2: 主线进度 5 章（10+ 章关全禁）+ 随机受限")
print("=" * 70)
for rate in (1.0, 0.6, 0.3):
    summary(rate, chapter=5)

print()
print("=" * 70)
print("场景 3: probe 实测随机失败 20%（选中关实测不可刷 → 迭代重解）")
print("=" * 70)
summary(1.0, probe_deny=0.2)
summary(0.7, probe_deny=0.2)

# ── 展示一次具体场景: 可用率 0.5, 详细计划 ──
print()
print("=" * 70)
print("示例: 可用率 50% 的一次运行（随机种子固定, 可复现）")
print("=" * 70)
r = run(0.5)
p = r["plan"]
print(f"禁用比例: {r['banned_ratio']:.0%} | 总AP: {p.get('total_ap')} | "
      f"locked: {len(p.get('locked', []))} | 天数: {len(p.get('days', []))}")
for f in p.get("gap_fills", []):
    src = " + ".join(f"{x['stage']}x{x['rounds']}" for x in f.get("farm", [])) or "合成"
    cr = f" + 合{f['craft']['num']}" if f.get("craft") else ""
    print(f"  {f['name']}: {src}{cr} ({f['ap_cost']} AP)")
for lk in p.get("locked", []):
    print(f"  LOCKED {lk['name']}: {lk.get('lock_reason', '')}")
