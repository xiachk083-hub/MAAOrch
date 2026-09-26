#!/usr/bin/env python3
"""给生成好的作业补 `skill` / `skill_usage`（own_axis_job.py 只写干员名，不写技能）。

为什么需要：`skill` = 用第几个技能。不写时 MAA 默认第 1 个 ⇒ 想用 S3 的干员会被当成白板，
而"一技能白板"正是前几版轴被否掉的原因之一 ✓。

用法:
    python tools/job_add_skill.py --job data/copilot/CE-6_ours_v19.json --default 3 \
        --set 极境=1 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, type=pathlib.Path)
    ap.add_argument("--default", type=int, default=3, help="默认用第几个技能")
    ap.add_argument("--usage", type=int, default=1, help="skill_usage（1=好了就开）")
    ap.add_argument("--set", action="append", default=[], help="按干员覆盖，如 --set 极境=1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    overrides = {}
    for s in args.set:
        name, _, val = s.partition("=")
        overrides[name.strip()] = int(val)

    job = json.loads(args.job.read_text(encoding="utf-8"))
    n = 0
    for g in job.get("groups", []):
        for o in g.get("opers", []):
            # 只给"组里唯一那个人"补技能；候选串里的其他人保持原样（MAA 编队会顺延）
            if "name" not in o:
                continue
            o["skill"] = overrides.get(o["name"], args.default)
            o["skill_usage"] = args.usage
            n += 1
    if args.dry_run:
        print(json.dumps(job, ensure_ascii=False, indent=1))
        return 0
    args.job.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    print("✓ %s：给 %d 个干员写了 skill（默认 S%d，覆盖 %s）" % (
        args.job.name, n, args.default, overrides or "无"))
    for g in job["groups"]:
        for o in g["opers"]:
            print("   %-10s S%d usage%d" % (g["name"], o["skill"], o["skill_usage"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
