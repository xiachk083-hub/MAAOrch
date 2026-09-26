# -*- coding: utf-8 -*-
"""作业费用可行性检查：按关卡的费用回复速度模拟一条时间轴，判断作业里每个 Deploy
在预定时刻是否真的付得起（费用不够时游戏会拒绝部署，而 MAA 日志照样记"已部署"）。

背景（2026-09-21）：BI-EX-6 突袭 rune `global_cost_recovery_mul scale=2.0` ⇒ 回复间隔×2
（游戏内文本「部署费用回复速度降低」）⇒ 初始 10 费之后只有 0.5 费/秒。当时作业按
普通局节奏写，结果只有第一个干员落得下去，其余全被游戏拒绝，白丢一局。

用法:
    python tools/job_dp_check.py data/copilot/BI-EX-6_cm_v14.json --stage act14side_ex06 --raid
    python tools/job_dp_check.py <job> --stage act14side_ex07            # 普通
"""
from __future__ import annotations

import argparse, io, json, pathlib, re

P = pathlib.Path(r"D:\1-2\MAAOrch")
CT = json.loads((P / "data/_character_table_full.json").read_text(encoding="utf-8"))
SK = json.loads((P / "data/_skill_table.json").read_text(encoding="utf-8"))
BOX = {e["name"]: e for e in json.loads((P / "data/operbox_batch/emu2_9036_flat.json").read_text(encoding="utf-8"))}
TOKEN_COST = {"障碍物": 5, "闸门": 5}


def op_cost(name: str):
    if name in TOKEN_COST:
        return TOKEN_COST[name]
    for k, v in CT.items():
        if isinstance(v, dict) and v.get("name") == name:
            ph = v.get("phases") or []
            d = (ph[-1].get("attributesKeyFrames") or [{}])[-1].get("data") or {}
            c = d.get("cost")
            # 精英化会降费：取最后一相的 cost（已是最终值）
            if c is not None:
                return int(c)
            for ph2 in reversed(ph):
                dd = ((ph2.get("attributesKeyFrames") or [{}])[-1].get("data") or {})
                if dd.get("cost") is not None:
                    return int(dd["cost"])
    return None


def stage_cost(stage: str, raid: bool):
    d = json.loads((P / "data" / f"_level_{stage}.json").read_text(encoding="utf-8"))
    o = d["options"]
    init, rate, limit = o["initialCost"], 1.0 / max(o["costIncreaseTime"], 1e-6), o["characterLimit"]
    life = o["maxLifePoint"]
    for r in d.get("runes", []):
        bb = {b["key"]: b.get("value") for b in (r.get("blackboard") or [])}
        if r.get("key") == "global_cost_recovery_mul" and "scale" in bb:
            rate = rate / bb["scale"]
        if r.get("key") == "global_lifepoint" and "value" in bb:
            life = bb["value"]
    return init, rate, limit, life


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--stage", required=True)
    ap.add_argument("--raid", action="store_true")
    ap.add_argument("--kill-dp", type=float, default=0.0, help="估算的击杀回费（费/秒）")
    a = ap.parse_args()

    init, rate, limit, life = stage_cost(a.stage, a.raid)
    job = json.loads(pathlib.Path(a.job).read_text(encoding="utf-8"))
    name_of = {}
    for g in job.get("groups", []):
        for o in g.get("opers", []):
            name_of[g["name"]] = o.get("name")
    for o in job.get("opers", []):
        if o.get("name"):
            name_of[o["name"]] = o["name"]

    print("关卡 %s%s：初始 %s 费，回复 %.3f 费/秒，场上上限 %s，生命 %s" %
          (a.stage, "（突袭）" if a.raid else "", init, rate, limit, life))
    t, dp, on_field, bad = 0.0, float(init), 0, []
    for i, act in enumerate(job.get("actions", [])):
        if act.get("type") != "Deploy":
            continue
        real = name_of.get(act.get("name"), act.get("name"))
        cost = op_cost(real)
        # 找到"付得起"的时刻（MAA 不会等，所以这里只做可行性判断）
        if cost is None:
            print("  %-14s 费用未知，跳过" % real)
            continue
        need_at = t
        while dp < cost and t < 600:
            t += 0.5
            dp += rate * 0.5
        ok = dp >= cost
        if on_field >= limit:
            print("  %5.1fs  %-14s 费%-3s  ⚠ 场上已满 %s（要等前面的人撤退/阵亡）" % (t, real, cost, limit))
        elif not ok:
            bad.append(real)
            print("  %5.1fs  %-14s 费%-3s  ✗ 付不起（只有 %.1f 费）" % (t, real, cost, dp))
        else:
            wait = t - need_at
            flag = ("（比预定晚 %.0fs）" % wait) if wait > 1 else ""
            print("  %5.1fs  %-14s 费%-3s  ✓ 剩余 %.1f 费 %s" % (t, real, cost, dp - cost, flag))
        if ok:
            dp -= cost
            on_field += 1
        t += 2.5   # 一次部署大约占 2~3 秒
    print("结论：%s" % ("全部付得起 ✓" if not bad else "有 %d 个干员在费用上根本来不及：%s ✗" % (len(bad), "、".join(bad))))


if __name__ == "__main__":
    main()
