#!/usr/bin/env python3
"""账号任务审计 — 每个账号的任务完成情况 + 失败任务汇总。

数据源：本地 AUTO-MAS 存档（data/auto_mas_history.json）。
- 任务链：每条运行 start/completed 的任务列表（StartUp/Fight/Infrast/Recruit/Mall/Award）
- 失败任务：ERROR 运行的 error_task（如"理智作战"/"领取奖励"）
- 输出控制台表格 + data/auto_mas_audit.csv（Excel 可用）

用法:
    python tools/auto_mas_audit.py                # 全部历史
    python tools/auto_mas_audit.py --days 3       # 只看最近 N 天
    python tools/auto_mas_audit.py --csv out.csv  # 指定 CSV
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
from collections import Counter, defaultdict

ARCHIVE = pathlib.Path(__file__).resolve().parent.parent / "data" / "auto_mas_history.json"
DEFAULT_CSV = pathlib.Path(__file__).resolve().parent.parent / "data" / "auto_mas_audit.csv"

TASKS = ("StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award", "Copilot", "Roguelike", "Reclamation")


def main() -> None:
    ap = argparse.ArgumentParser(description="账号任务审计")
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 天（0=全部）")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="CSV 输出路径")
    ap.add_argument("--no-csv", action="store_true", help="不写 CSV")
    args = ap.parse_args()

    arch = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    days = sorted(arch.get("days", {}))
    if args.days:
        days = days[-args.days:]

    # 每账号聚合：运行数 / 成功失败 / 各任务执行与完成 / 失败任务
    agg: dict[str, dict] = defaultdict(lambda: {
        "runs": 0, "done": 0, "err": 0, "err_map": Counter(),
        "task_start": Counter(), "task_done": Counter(), "last": "", "last_status": "",
    })
    for ds in days:
        for name, runs in arch["days"][ds].items():
            a = agg[name]
            for r in runs:
                a["runs"] += 1
                if r.get("status") == "DONE":
                    a["done"] += 1
                elif r.get("status") == "ERROR":
                    a["err"] += 1
                    a["err_map"][r.get("error_task") or r.get("error") or "未知"] += 1
                ch = r.get("chains")
                if ch:
                    a["task_start"].update(ch.get("start", []))
                    a["task_done"].update(ch.get("completed", []))
                if r.get("time", "") > a["last"]:
                    a["last"] = r.get("time", "")
                    a["last_status"] = r.get("status", "?")

    # 控制台表格
    print(f"窗口: {days[0]} ~ {days[-1]} | 账号 {len(agg)}")
    hdr = f"{'账号':22s} {'运行':4s} {'成功':4s} {'失败':4s}  各任务(执行/完成)            失败任务"
    print(hdr)
    for name in sorted(agg):
        a = agg[name]
        tasks = " ".join(f"{t}{a['task_start'][t]}/{a['task_done'][t]}" for t in TASKS if a["task_start"][t])
        fails = " ".join(f"{t}×{n}" for t, n in a["err_map"].most_common(3)) or "-"
        print(f"{name[:22]:22s} {a['runs']:4d} {a['done']:4d} {a['err']:4d}  {tasks:38s} {fails[:42]}")

    # 失败任务排行
    print("\n=== 失败任务排行（全部账号）===")
    total = Counter()
    for a in agg.values():
        total.update(a["err_map"])
    for t, n in total.most_common():
        print(f"  {t}: {n} 次")

    if not args.no_csv:
        out = pathlib.Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["账号", "运行", "成功", "失败", "最后运行", "最后状态"] +
                       [f"执行_{t}" for t in TASKS] + [f"完成_{t}" for t in TASKS] +
                       ["失败任务_1", "失败任务_2", "失败任务_3"])
            for name in sorted(agg):
                a = agg[name]
                fails = [f"{t}×{n}" for t, n in a["err_map"].most_common(3)]
                w.writerow([name, a["runs"], a["done"], a["err"], a["last"], a["last_status"]]
                           + [a["task_start"][t] for t in TASKS]
                           + [a["task_done"][t] for t in TASKS]
                           + fails + [""] * (3 - len(fails)))
        print(f"\nCSV: {out}")


if __name__ == "__main__":
    main()
