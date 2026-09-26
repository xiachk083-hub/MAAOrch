"""作业费用体检：跑局前先算这套编队要多少费、按 1 费/秒多久能铺完。

为什么要有它（2026-09-21 血的教训）：BI-EX-3 连跪两局，根因是我一直**凭印象估费**——
泥岩实际 36 费（我按 24 估）、蕾缪安 22、艾雅法拉 21、流明 23，v2 实际总费 137 而不是我以为的 118，
而关卡只有 10 初始费 + 1 费/秒 ⇒ 后半个编队根本来不及落地（录像里 8 个位置只落地 3~7 个）。

用法:
    python tools/job_cost.py data/copilot/BI-EX-3_v3.json [--initial 10] [--rate 1.0] [--dp-skill 0.7]
"""
from __future__ import annotations

import argparse
import json
import pathlib

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
CT = json.loads((PROJ / "data" / "_character_table_full.json").read_text(encoding="utf-8"))
COST = {}
for k, v in CT.items():
    if isinstance(v, dict) and v.get("name"):
        for f in ((v.get("phases") or [{}])[-1].get("attributesKeyFrames") or []):
            d = f.get("data") or {}
            if "cost" in d:
                COST[v["name"]] = d["cost"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", type=pathlib.Path)
    ap.add_argument("--initial", type=float, default=10.0)
    ap.add_argument("--rate", type=float, default=1.0, help="每秒回费")
    ap.add_argument("--dp-skill", type=float, default=0.0, help="回费技能带来的额外费/秒（如琴柳/凛御银灰约 0.5~0.8）")
    args = ap.parse_args()

    job = json.loads(args.job.read_text(encoding="utf-8"))
    names = []
    for g in job.get("groups") or []:
        for o in g.get("opers") or []:
            names.append(o["name"])
    if not names:
        names = [o["name"] for o in (job.get("opers") or [])]
    # ⚠️ 2026-09-21 实测坑：5 星干员只有 2 个技能，作业里写 skill:3 会让 MaaCore 直接报错
    #    （asst::CopilotConfig::parse_oper_usage | Oper 安哲拉 with rarity 5 cannot use skill index 3）
    #    ⇒ 跑局前先自查，免得白跑一局。
    bad = []
    for g in job.get("groups") or []:
        for o in g.get("opers") or []:
            n, sk = o["name"], int(o.get("skill") or 1)
            cnt = None
            for k, v in CT.items():
                if isinstance(v, dict) and v.get("name") == n:
                    cnt = len(v.get("skills") or [])
                    break
            if cnt is not None and sk > cnt:
                bad.append((n, sk, cnt))
    if bad:
        print("⚠️ 技能编号越界（会让 MaaCore 报错，请先改）：")
        for n, sk, cnt in bad:
            print("   %s 写了 S%d，但ta 只有 %d 个技能" % (n, sk, cnt))
    # 按 actions 里的 Deploy 顺序排
    order = []
    byname = {}
    for g in job.get("groups") or []:
        byname[g["name"]] = g["opers"][0]["name"]
    for a in job.get("actions") or []:
        if a.get("type") == "Deploy":
            order.append(byname.get(a.get("name"), a.get("name")))
    if not order:
        order = names

    total = 0
    rate = args.rate + args.dp_skill
    print("作业 %s（%s）" % (args.job.name, job.get("doc", {}).get("title", "")))
    print("关卡初始费 %.0f  回费 %.1f/秒（含技能 %.1f）" % (args.initial, args.rate, args.dp_skill))
    print("%-4s %-12s %5s %8s %10s" % ("#", "干员", "费用", "累计", "预计落地"))
    for i, n in enumerate(order, 1):
        c = COST.get(n)
        if c is None:
            print("  %-2d %-12s   ?（账号数据里没查到费用）" % (i, n))
            continue
        total += c
        need = (total - args.initial) / rate
        print("  %-2d %-12s %5d %8d  t≈%.0fs" % (i, n, c, total, max(0.0, need)))
    print("总费用 %d；按这条曲线，最后一个干员约在 t≈%.0fs 落地（战斗通常 150~250s ⇒ %s）"
          % (total, max(0.0, (total - args.initial) / rate),
             "铺得完 ✓" if (total - args.initial) / rate < 170 else "太挤 ✗ 建议砍费/加回费单位"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
