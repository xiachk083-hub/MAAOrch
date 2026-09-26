"""算 CE-6 的「出怪顺序 + 到达封口格的时刻」—— 回答"敌人先从哪儿出来"。

数据来源：关卡 waves 里的 fragments[].actions[]（每个 action 有 preDelay/interval/count/routeIndex），
以及 routes[] 的起止行（决定它走哪条车道）。移速按 敌人moveSpeed × 关卡 moveMultiplier(0.5)。

用途：决定**先封哪条道、先下谁**。我此前一直默认"重车道先来"，但那是推断，没算过。

用法:
    python tools/ce6_spawn_order.py
"""
from __future__ import annotations

import collections
import json
import pathlib

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
STAGE = "wk_melee_6"


def main() -> int:
    lvl = json.loads((PROJ / f"data/_level_{STAGE}.json").read_text(encoding="utf-8"))
    mul = float((lvl.get("options") or {}).get("moveMultiplier") or 1.0)
    routes = lvl["routes"]
    waves = lvl["waves"]
    if not waves:
        raise SystemExit("没有 waves 数据")

    # 敌人移速（猎犬pro 1.9 / 大剑手 0.85 / 粉碎攻坚手 0.5 …）
    db = json.loads((PROJ / "data/_enemy_db.json").read_text(encoding="utf-8"))["enemies"]
    speed = {}
    for e in db:
        v = e.get("Value")
        if isinstance(v, dict) and "enemyData" in v:
            a = (v["enemyData"].get("attributes") or {})
            def g(k):
                x = a.get(k)
                return x.get("m_value") if isinstance(x, dict) else x
            speed[e["Key"]] = float(g("moveSpeed") or 1.0)

    rows = []
    # ⚠️ 2026-09-21 更正：我第一版把 fragments 当成**串行**（前一段的怪出完才轮到下一段），
    #    结果算出"轻车道 58 秒才来第一只"—— 与原报告实测的"首达 3.2 秒"明显矛盾 ✗。
    #    关卡是 managedByScheduler + maxTimeWaitingForNextWave=-1，各 fragment 的 preDelay
    #    都是从**开打**起算（并行），所以这里不再累加。宁可取"更早"的估算，也不要低估压力。
    for frag in waves[0]["fragments"]:
        for a in (frag.get("actions") or []):
            if a.get("actionType") != "SPAWN":
                continue
            cnt = int(a.get("count") or 1)
            pre = float(a.get("preDelay") or 0.0)
            itv = float(a.get("interval") or 0.0)
            ri = int(a.get("routeIndex") or 0)
            key = a.get("key")
            rt = routes[ri] if ri < len(routes) else {}
            spos = rt.get("startPosition") or {}
            sy = spos.get("row")
            lane = "重车道(第3/4行)" if sy in (3, 4) else ("轻车道(第1/2行)" if sy in (1, 2) else "?")
            v = speed.get(key, 1.0) * mul
            dist_cut = max(0, int(spos.get("col") or 7) - 2)
            dist_gate = int(spos.get("col") or 7)
            rows.append(dict(t=round(pre, 1), enemy=key, n=cnt, ri=ri, row=sy, lane=lane,
                             v=round(v, 2),
                             t_cut=round(pre + dist_cut / v, 1),
                             t_gate=round(pre + dist_gate / v, 1)))

    rows.sort(key=lambda r: r["t_cut"])
    print("关卡 moveMultiplier=%.2f（敌人实际速度 = 面板移速 × 它）" % mul)
    print("\n== 按「到达封口格(列2)」排序的前 16 条出怪 ==")
    print("%8s %8s %-9s %-6s %-16s %s" % ("出怪@", "到格@", "敌人", "数量", "车道", "路线"))
    for r in rows[:16]:
        print("%8.1f %8.1f %-9s x%-5d %-16s r%d(row%d)" % (
            r["t"], r["t_cut"], r["enemy"].replace("enemy_", ""), r["n"], r["lane"], r["ri"], r["row"]))

    print("\n== 每条车道最早到达封口格的时刻 ==")
    first = {}
    for r in rows:
        first.setdefault(r["lane"], r)
    for lane, r in sorted(first.items(), key=lambda kv: kv[1]["t_cut"]):
        print("   %-16s 第一只 %.1fs 到格（%.1fs 出怪，%s）" % (lane, r["t_cut"], r["t"], r["enemy"]))

    cnt = collections.Counter(r["lane"] for r in rows for _ in range(r["n"]))
    print("\n== 各车道敌人总数 ==")
    for lane, n in cnt.most_common():
        print("   %-16s %d 只" % (lane, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
