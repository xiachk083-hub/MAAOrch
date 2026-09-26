# -*- coding: utf-8 -*-
"""动态状态拼装（第 2 步）—— 每号一份"今天的状态"。

输入：AUTO-MAS history（今天）+ 每次运行的 MAA 日志（复用 `auto_mas_archiver`
      的 `parse_log_content` / `fetch_record`，不再写第二遍解析）。
输出：`data/daily_state/<账号>.json` + 控制台总表。

不依赖模拟器/截图：进度类字段先留空（主线进度要读界面，属后续）。

用法:
    python tools/daily_state.py                 # 今天
    python tools/daily_state.py --date 2026-09-17
    python tools/daily_state.py --limit 10      # 只跑前 N 个账号（快）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ / "tools"))
import auto_mas_archiver as arch          # noqa: E402

OUT_DIR = PROJ / "data" / "daily_state"
CLEARED_MAX = 40          # 剩余理智 ≤ 这个数算"清过"（单次作战最贵 36 = CE-6）


def build(day: dict) -> dict:
    """history 的一天 → {账号: 状态}"""
    out = {}
    for name, rec in (day or {}).items():
        idx = rec.get("index") or []
        if not idx:
            continue
        last = idx[-1]
        state = {
            "account": name,
            "uid": None,                       # 账号名↔画面 UID 的映射要走 box/截图，后续补
            "run_today": True,
            "runs": len(idx),
            "last_run": (last.get("date") or "")[-8:],
            "status_last": last.get("status"),
            "jsonFile": last.get("jsonFile"),
            "sanity_left": None,
            "sanity_cap": None,
            "stamina_cleared": None,
            "infrast_done": None,
            "annihilation_done": None,
            "event_progress": None,
            "error_task": None,
            "duration_sec": None,
        }
        det = arch.fetch_record(last.get("jsonFile")) if last.get("jsonFile") else None
        if det:
            san = det.get("sanity") or ""
            if "/" in san:
                a, b = san.split("/", 1)
                state["sanity_left"] = int(a) if a.isdigit() else None
                state["sanity_cap"] = int(b) if b.isdigit() else None
            chains = (det.get("chains") or {}).get("completed") or []
            low = [c.lower() for c in chains]
            state["infrast_done"] = any("infrast" in c for c in low)
            state["annihilation_done"] = any("annihilation" in c for c in low)
            stages = [d.get("stage") for d in (det.get("drops") or []) if d.get("stage")]
            if stages:
                state["event_progress"] = stages[-1]
            state["error_task"] = det.get("error_task")
            state["duration_sec"] = det.get("duration_sec")
        if state["sanity_left"] is not None:
            state["stamina_cleared"] = state["sanity_left"] <= CLEARED_MAX
        out[name] = state
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个账号")
    a = ap.parse_args()

    day = arch.fetch_day(a.date) or {}          # 直接就是 {账号: record}
    names = sorted(day)
    if a.limit:
        names = names[:a.limit]
    print("日期 %s：有记录的账号 %d 个%s" % (a.date, len(day), "（只处理前 %d）" % a.limit if a.limit else ""))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    states = []
    for i, n in enumerate(names, 1):
        st = build({n: day[n]})[n]
        st["date"] = a.date
        (OUT_DIR / ("%s.json" % n.replace("/", "_"))).write_text(
            json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        states.append(st)
        print("  [%2d/%d] %-16s 跑%2d次 末次%-8s %-5s 理智=%-7s 基建=%-4s 剿灭=%-4s 进度=%s%s"
              % (i, len(names), n[:16], st["runs"], st["last_run"], st["status_last"],
                 ("%s/%s" % (st["sanity_left"], st["sanity_cap"])) if st["sanity_left"] is not None else "-",
                 st["infrast_done"], st["annihilation_done"], st["event_progress"] or "-",
                 ("  出错:%s" % st["error_task"]) if st["error_task"] else ""))

    print()
    ok = [s for s in states if s["status_last"] == "DONE"]
    bad = [s for s in states if s["status_last"] == "ERROR"]
    cleared = [s for s in states if s["stamina_cleared"]]
    infra = [s for s in states if s["infrast_done"]]
    anni = [s for s in states if s["annihilation_done"]]
    print("汇总：共 %d 号 | 末次成功 %d / 失败 %d | 清过体力 %d | 基建完成 %d | 剿灭完成 %d"
          % (len(states), len(ok), len(bad), len(cleared), len(infra), len(anni)))
    print("产物：%s（每号一份 json）" % OUT_DIR)


if __name__ == "__main__":
    main()
