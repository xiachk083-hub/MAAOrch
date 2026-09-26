#!/usr/bin/env python3
"""账号体力 + 基建统计（读本地 AUTO-MAS 存档）。

每个账号取最新一次运行：状态/理智/基建设施访问/掉落关卡。
输出控制台表格 + data/auto_mas_stats.csv（utf-8-sig，Excel 双击打开）。

用法:
    python tools/auto_mas_stats.py                # 表格 + CSV
    python tools/auto_mas_stats.py --today        # 只显示今天有运行的账号
    python tools/auto_mas_stats.py --csv out.csv  # 指定 CSV 路径
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

ARCHIVE = pathlib.Path(__file__).resolve().parent.parent / "data" / "auto_mas_history.json"
DEFAULT_CSV = pathlib.Path(__file__).resolve().parent.parent / "data" / "auto_mas_stats.csv"

FACILITIES = ("制造站", "贸易站", "宿舍", "发电站", "会客室", "办公室", "控制中枢", "训练室", "加工站")


def load_latest() -> list[dict]:
    """读存档，返回每账号最新运行（按日期+时间排序取最后）。"""
    arch = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, dict]] = []
    for ds in sorted(arch.get("days", {})):
        for name, runs in arch["days"][ds].items():
            for r in runs:
                rows.append((ds, r.get("time", ""), name, r))
    rows.sort(key=lambda x: (x[0], x[1]))
    latest: dict[str, dict] = {}
    for ds, t, name, r in rows:
        latest[name] = {"time": t, **r}
    out = []
    for name, r in latest.items():
        item = {"name": name}
        item.update(r)
        out.append(item)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="账号体力+基建统计")
    ap.add_argument("--today", action="store_true", help="只看今天有运行的账号")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="CSV 输出路径")
    ap.add_argument("--no-csv", action="store_true", help="不写 CSV")
    args = ap.parse_args()

    rows = load_latest()
    if args.today:
        rows = [r for r in rows if r.get("time", "").startswith("2026-") and r["time"][:10] == r["time"][:10]]
        today = __import__("datetime").date.today().isoformat()
        rows = [r for r in rows if r.get("time", "").startswith(today)]

    # 控制台表格
    hdr = f"{'账号':20s} {'最后运行':17s} {'状态':5s} {'理智':7s} {'基建':4s} {'设施':34s}"
    print(hdr)
    for r in sorted(rows, key=lambda x: x["name"]):
        s = r.get("sanity", "-")
        inf = r.get("infrast") or {}
        fac = " ".join(f"{k}{inf[k]}" for k in FACILITIES if inf.get(k))
        print(f"{r['name'][:20]:20s} {r.get('time','')[:16]:17s} "
              f"{r.get('status','?'):5s} {s:7s} {str(len(inf)):4s} {fac[:40]}")

    if not args.no_csv:
        out = pathlib.Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["账号", "最后运行", "状态", "理智", "理智上限", "基建设施数",
                        "制造站", "贸易站", "宿舍", "发电站", "会客室", "办公室",
                        "控制中枢", "训练室", "加工站", "掉落关卡"])
            for r in sorted(rows, key=lambda x: x["name"]):
                s = r.get("sanity", "-")
                cur = up = ""
                if isinstance(s, str) and "/" in s:
                    cur, up = s.split("/", 1)
                inf = r.get("infrast") or {}
                w.writerow([r["name"], r.get("time", ""), r.get("status", ""),
                            cur, up, len(inf),
                            inf.get("制造站", ""), inf.get("贸易站", ""),
                            inf.get("宿舍", ""), inf.get("发电站", ""),
                            inf.get("会客室", ""), inf.get("办公室", ""),
                            inf.get("控制中枢", ""), inf.get("训练室", ""),
                            inf.get("加工站", ""),
                            len(r.get("drops", []))])
        print(f"\nCSV: {out} ({len(rows)} 个账号)")


if __name__ == "__main__":
    main()
