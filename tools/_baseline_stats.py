#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 l-1 gui.log 抽取本次实测（2026-09-28 09:17–10:22）的局/事件/时长统计。

用法: <Python312>\python.exe tools/_baseline_stats.py <gui.log 路径>
只读；输出人读表格 + 关键推导。
"""
import re
import sys
from datetime import datetime

TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
EVENTS = [
    ("explore", re.compile(r"已开始探索 (\d+) 次")),
    ("settle", re.compile(r"肉鸽结算: (.) \(n(\d+)\)")),
    ("battle_fail", re.compile(r"战斗失败")),
    ("battle_done", re.compile(r"战斗完成")),
    ("battle_start", re.compile(r"开始战斗: (.+)")),
    ("stage", re.compile(r"关卡: (.+)")),
    ("event", re.compile(r"事件: (.+)")),
    ("node", re.compile(r"节点: (.+)")),
    ("foresee", re.compile(r"预见: (.+)")),
]

def main(path: str) -> int:
    rows = []
    for line in open(path, encoding="utf-8", errors="replace"):
        m = TS.match(line)
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
        if t < datetime(2026, 9, 28, 9, 15) or t > datetime(2026, 9, 28, 10, 25):
            continue
        body = line[m.end():]
        for name, rx in EVENTS:
            mm = rx.search(body)
            if mm:
                rows.append((t, name, mm.group(1) if mm.groups() else ""))
                break
    rows.sort()

    print("== 时间线（浓缩）==")
    starts, fails = [], []
    cur_battles = []
    for t, name, val in rows:
        if name == "explore":
            starts.append(t)
            print(f"{t.strftime('%H:%M:%S')}  ▶ 已开始探索 {val} 次")
        elif name == "battle_start":
            cur_battles.append([t, val])
            print(f"{t.strftime('%H:%M:%S')}    battle ▶ {val}")
        elif name == "battle_done":
            dt = (t - cur_battles[-1][0]).total_seconds() if cur_battles else 0
            if cur_battles:
                cur_battles[-1].append(("win", dt))
            print(f"{t.strftime('%H:%M:%S')}    battle ✓ {dt:.0f}s")
        elif name == "battle_fail":
            dt = (t - cur_battles[-1][0]).total_seconds() if cur_battles else 0
            fails.append(t)
            if cur_battles:
                cur_battles[-1].append(("lose", dt))
            print(f"{t.strftime('%H:%M:%S')}    battle ✗ {dt:.0f}s  (战斗失败)")
        elif name in ("stage", "event", "node", "foresee"):
            print(f"{t.strftime('%H:%M:%S')}    {name}: {val}")
        elif name == "settle":
            print(f"{t.strftime('%H:%M:%S')}  ★ 肉鸽结算 {val}（结算块）")

    print("\n== 局统计 ==")
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else None
        fail = next((f for f in fails if f >= s and (end is None or f < end)), None)
        dur = ((fail or end) - s).total_seconds() if (fail or end) else None
        seg = [b for b in cur_battles if b[0] >= s and (end is None or b[0] < end)]
        wins = sum(1 for b in seg for x in b[2:] if x[0] == "win")
        losses = sum(1 for b in seg for x in b[2:] if x[0] == "lose")
        dur_txt = f"{dur / 60:.2f} min" if dur is not None else "（未收局/未截到）"
        print(f"run#{i + 1}: start {s.strftime('%H:%M:%S')} → "
              f"{'战斗失败@' + fail.strftime('%H:%M:%S') if fail else (end.strftime('%H:%M:%S') if end else '...')}  "
              f"时长 {dur_txt}  战斗 {wins}胜/{losses}负")
    for i in range(len(starts) - 1):
        gap = (starts[i + 1] - (fails[i] if i < len(fails) else starts[i])).total_seconds() if i < len(fails) else None
        if fails and i < len(fails):
            print(f"局间间隔 run#{i + 1}结束→run#{i + 2}开始: {(starts[i + 1] - fails[i]).total_seconds():.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
