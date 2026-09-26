"""给作业里的每个 Deploy 加 pre_delay（等"行动开始"横幅动画结束再拖拽）。

由来（2026-09-21）：MAA 日志显示它在 `BattleOfficiallyBegin` 才认到 0.82 分时就开始发拖拽，
那正是入场横幅的动画期 ⇒ 手势被游戏丢掉，于是"日志记了 5 次 Deploy、场上只站 1~3 个"。

用法:
    python tools/job_add_delay.py data/copilot/CE-6_v49.json data/copilot/CE-6_v50.json --first 3500 --step 2200
"""
from __future__ import annotations

import argparse
import json
import pathlib


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=pathlib.Path)
    ap.add_argument("dst", type=pathlib.Path)
    ap.add_argument("--first", type=int, default=3500, help="第一个部署的延迟（毫秒）")
    ap.add_argument("--step", type=int, default=2200, help="后续每个部署的间隔（毫秒）")
    ap.add_argument("--title", default="")
    args = ap.parse_args()

    j = json.loads(args.src.read_text(encoding="utf-8"))
    d = args.first
    n = 0
    for a in j["actions"]:
        if a.get("type") == "Deploy":
            a["pre_delay"] = d
            d += args.step
            n += 1
    if args.title:
        j.setdefault("doc", {})["title"] = args.title
        j["doc"]["details"] = ("MAA 日志显示它在行动开始横幅的动画期就开始发拖拽 ⇒ 手势被丢掉。"
                               "本作业给每个 Deploy 加 pre_delay（首个 %d ms、之后每 %d ms 一个）。"
                               % (args.first, args.step))
    args.dst.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    print("✓ %s：给 %d 个 Deploy 加了 pre_delay（%d 起、每 %d 递增）" % (args.dst.name, n, args.first, args.step))
    for a in j["actions"]:
        print("   %-10s %-12s %s" % (a["type"], a.get("name", ""), a.get("pre_delay", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
