#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一行命令看全局：源石账本 / 待打的矿 / 下一步排产 / 团队任务板提示。

用法：python tools/dashboard.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")


def jload(p: pathlib.Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def main() -> int:
    print("=" * 68)
    print(f"MAAOrch 战况看板  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 68)

    # 1) 源石
    sl = jload(ROOT / "runtime" / "stone_ledger.json")
    base = sl.get("基线", 7)
    held = sl.get("当前持有", "?")
    total = sl.get("累计首通", "?")
    print(f"\n【源石】基线 {base} → 持有 {held}（累计首通 {total}，目标 107 / 100 颗）")
    for m in (sl.get("明细") or [])[-5:]:
        print(f"   ✓ {m.get('时间','')} {m.get('关卡','')} {m.get('星','')}★  {m.get('活动','')}  轴={m.get('轴','')}")

    # 2) 单位状态
    st = jload(ROOT / "runtime" / "_prime_state3.json")
    def cnt(prefix):
        return sum(1 for k, v in st.items() if k.startswith(prefix) and v in ("todo", "partial"))
    todo = sum(1 for v in st.values() if v in ("todo", "partial"))
    done = sum(1 for v in st.values() if v == "done")
    noreach = sum(1 for v in st.values() if v == "noreach")
    print(f"\n【单位】总计 {len(st)}｜可打 {todo}｜已三星 {done}｜暂缓 {noreach}")
    evs = {}
    for k, v in st.items():
        if v in ("todo", "partial"):
            evs[k.split("-")[0]] = evs.get(k.split("-")[0], 0) + 1
    top = sorted(evs.items(), key=lambda kv: -kv[1])[:10]
    print("   待打最多的事件前缀：" + "  ".join(f"{k}:{v}" for k, v in top))

    # 3) 排产前几名
    plan = jload(ROOT / "runtime" / "mine_plan.json", [])
    if isinstance(plan, list) and plan:
        print("\n【排产前 8】（章EV → 单颗 EV）")
        for r in plan[:8]:
            print(f"   {r.get('unit',''):16s} 章EV={r.get('chapter_ev','')!s:>6}  EV={r.get('ev','')!s:>5}  ap={r.get('ap','')}")

    # 4) 服务与模拟器
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=5) as r:
            h = json.loads(r.read().decode())
        print(f"\n【服务】ok={h.get('ok')} dev={h.get('dev')}")
    except Exception as e:  # noqa: BLE001
        print(f"\n【服务】不可用：{str(e)[:60]}")

    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MAA.exe", "/FO", "CSV"],
                             capture_output=True, encoding="utf-8", errors="replace", timeout=15).stdout
        n = out.count("MAA.exe")
        print(f"【模拟器】MAA.exe 进程 {n} 个（注意：常驻的那个是用户自己的 emu #0，不要碰）")
    except Exception:
        pass

    # 5) 学习产物
    learned = sorted(p.name for p in (ROOT / "runtime" / "learned").glob("*"))
    print(f"【学习产物】{', '.join(learned) if learned else '（空）'}")
    audits = sorted(p.name for p in (ROOT / "docs" / "audits").glob("*.md"))
    print(f"【审计报告】{len(audits)} 份" + (f"，最新 {audits[-1]}" if audits else ""))
    print("\n提示：详细任务板用 team_task_list 看；打关日志 runtime/_prime_queue3.log；"
          "源石账本 runtime/stone_ledger.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
