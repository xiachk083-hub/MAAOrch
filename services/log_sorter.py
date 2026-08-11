"""日志样本分拣（2026-08-11 用户: 垃圾场模式 — 全量收进来，分拣整理）。

收集层（log_watcher）把全量事件写 log_samples/*.jsonl（乱）——
本程序分拣：
1. 运行切分：launch 事件为界 → 每次运行为一个单元（事件序列+结果）
2. 事件聚合：按 run 组织时间有序事件
3. 统计：事件类型分布 / 新事件发现（对照已知清单）/ 异常标记
4. 输出：logs/samples_index/（runs/ 每运行聚合 + stats.json + new_events.json）

用法：python -m services.log_sorter [--days 7]
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

SAMPLES = Path(__file__).parent.parent / "logs" / "log_samples"
INDEX = Path(__file__).parent.parent / "logs" / "samples_index"

# 已知事件清单（分拣后对照 — 新出现的标出来）
KNOWN_EVENTS = {
    "completed", "battle_failed", "exceeded", "task_start", "fight_sanity",
    "stage_drops", "recruit", "connection_error", "ocr_error", "load_error",
    "downgrade_signal", "launch", "exit",
}


def sort_account(aid: str, max_ts: str = "") -> dict:
    """分拣一个账号的样本 → 运行切分 + 事件序列 + 统计。"""
    fp = SAMPLES / f"{aid}.jsonl"
    if not fp.exists():
        return {"aid": aid, "runs": 0, "error": "no samples"}
    events = []
    for ln in open(fp, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(ln)
            if max_ts and d.get("ts", "") < max_ts:
                continue
            events.append(d)
        except Exception:
            continue
    events.sort(key=lambda e: e.get("ts", ""))
    # 运行切分：launch 为界
    runs = []
    cur = None
    for e in events:
        evt = e.get("event", "")
        if evt == "launch":
            if cur:
                runs.append(cur)
            cur = {"aid": aid, "start": e.get("ts", ""), "events": [], "exit": None, "elapsed": None}
        if cur is None:
            cur = {"aid": aid, "start": e.get("ts", ""), "events": [], "exit": None, "elapsed": None}
        cur["events"].append({"t": e.get("ts", ""), "e": evt, "l": (e.get("line") or "")[:120]})
        if evt == "exit":
            cur["exit"] = (e.get("line") or "")[:80]
    if cur:
        runs.append(cur)
    # 统计
    evt_counter = Counter()
    for r in runs:
        for ev in r["events"]:
            evt_counter[ev["e"]] += 1
    return {
        "aid": aid,
        "runs": len(runs),
        "events_total": len(events),
        "event_types": dict(evt_counter.most_common()),
        "exits": [r["exit"] for r in runs if r.get("exit")][-10:],
    }


def build_index(days: int = 7) -> dict:
    """全账号分拣 → 汇总索引 + 新事件发现。"""
    INDEX.mkdir(parents=True, exist_ok=True)
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    all_stats = {}
    new_events = Counter()
    for fp in sorted(glob.glob(str(SAMPLES / "*.jsonl"))):
        aid = os.path.basename(fp)[:8]
        st = sort_account(aid, cutoff)
        all_stats[aid] = st
        for evt in st.get("event_types", {}):
            if evt not in KNOWN_EVENTS:
                new_events[evt] += 1
    # 汇总
    total_runs = sum(s.get("runs", 0) for s in all_stats.values())
    total_events = sum(s.get("events_total", 0) for s in all_stats.values())
    summary = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "accounts": len(all_stats),
        "runs": total_runs,
        "events": total_events,
        "new_events": dict(new_events.most_common()),
    }
    (INDEX / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (INDEX / "accounts.json").write_text(json.dumps(all_stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    s = build_index(args.days)
    print(json.dumps(s, ensure_ascii=False, indent=1))
    print(f"\n索引: {INDEX}")


if __name__ == "__main__":
    main()
