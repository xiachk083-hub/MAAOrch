#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出 Laya 微调样本 —— 用户方案"Python 侧"的那一半（2026-09-24）

输入：`runtime/axis_outcomes.jsonl`（我们跑过的真实结果）+ `runtime/op_recipes.jsonl`（作业配方）
      + `runtime/account_snapshot.json`（账号 box）
输出：`runtime/laya_train.jsonl`（每行一个训练样本，字段与用户 notebook 对齐）
      + 控制台给出"现在能不能开微调"的判定（用户方案要求 ≈150 条手标 / 每关成功组 ≥2）

样本结构:
    {"state": {"关卡":..., "账号box": {...}, "作业列表":[...]},
     "questions": {"最优作业": {"type":"choice", "instructions":..., "criteria": {...}}},
     "labels": {"最优作业": "<跑出 3 星的那份作业 op_id>"}}

用法:
    python tools/laya_export.py                 # 导出 + 判定
    python tools/laya_export.py --min-success 2 # 改"每关成功组下限"
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
RT = ROOT / "runtime"


def load_jsonl(p: pathlib.Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-success", type=int, default=2,
                    help="每关至少要有几次三星样本才纳入（用户 notebook 的建议值：2）")
    ap.add_argument("--out", default=str(RT / "laya_train.jsonl"))
    a = ap.parse_args()

    outcomes = load_jsonl(RT / "axis_outcomes.jsonl")
    recipes = {r["op_id"]: r for r in load_jsonl(RT / "op_recipes.jsonl")}
    box = json.load(open(RT / "account_snapshot.json", encoding="utf-8")).get("opers", {})

    by_stage: dict[str, list[dict]] = collections.defaultdict(list)
    for o in outcomes:
        if o.get("stage"):
            by_stage[o["stage"]].append(o)

    samples, skipped = [], []
    for stage, runs in by_stage.items():
        success = [r for r in runs if (r.get("stars") or 0) >= 3]
        if len(success) < a.min_success:
            skipped.append((stage, len(runs), len(success)))
            continue
        cands = []
        for r in runs:
            rid = str(r.get("id"))
            rec = recipes.get(f"{stage}_{rid}") or {}
            cands.append({
                "op_id": rid,
                "干员": r.get("opers") or rec.get("自备干员") or [],
                "要求": [f"{q['name']} 专{q.get('mastery')} 模{q.get('module')}" for q in (rec.get("自备需求") or [])],
                "标题": rec.get("标题") or "",
            })
        crit = {c["op_id"]: f"{c['标题']}｜干员:{'/'.join(c['干员'])}｜要求:{'、'.join(c['要求']) or '无'}"
                for c in cands}
        samples.append({
            "state": {"关卡": stage, "账号box": box, "作业列表": cands},
            "questions": {"最优作业": {
                "type": "choice",
                "instructions": f"关卡 {stage}：哪份作业对这个账号通过率最高？",
                "criteria": crit,
            }},
            "labels": {"最优作业": str(success[0].get("id"))},
        })

    pathlib.Path(a.out).write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + ("\n" if samples else ""),
                                   encoding="utf-8")
    print(f"账本 {len(outcomes)} 条 → 可构造训练样本 **{len(samples)}** 个（写到 {a.out}）")
    if skipped:
        print(f"因'每关三星 < {a.min_success} 次'跳过的关卡 {len(skipped)} 个（每关 试过/三星）：")
        for st, n, g in sorted(skipped, key=lambda x: -x[1])[:10]:
            print(f"   {st}: {n}/{g}")
    need = 150
    print(f"\n微调门槛（用户方案）：约 {need} 条手标样本；现在 {len(samples)} ⇒ "
          f"{'可以试跑了' if len(samples) >= need else f'还差 {need - len(samples)} 个，先继续攒账本'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
