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
    for fp in sorted(glob.glob(str(SAMPLES / "*.jsonl*"))):
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


# ── 自动分拣（2026-08-11 用户: 东西进来要识别分裂，不能堆着）──
import threading as _th


class LogSorterThread(_th.Thread):
    """常驻分拣线程：每 60s 增量扫描 log_samples/*.jsonl（跟踪行号），
    事件进来即时分裂（launch 切分运行、事件分类、新事件发现），
    维护内存索引（API 实时可查）+ 每 10 分钟落盘。"""

    def __init__(self):
        super().__init__(daemon=True, name="log_sorter")
        self._pos: dict = {}          # aid -> 已处理行号
        self.runs: list = []          # 最近运行（内存，最多 200）
        self.event_counter = Counter()
        self.new_events: set = set()
        self._lock = _th.Lock()
        self._last_save = 0.0

    def run(self) -> None:
        while True:
            try:
                self._sweep()
            except Exception:
                pass
            time.sleep(60)

    def _sweep(self) -> None:
        for fp in sorted(glob.glob(str(SAMPLES / "*.jsonl*"))):
            aid = os.path.basename(fp)[:8]
            try:
                lines = open(fp, encoding="utf-8", errors="replace").readlines()
            except Exception:
                continue
            pos = self._pos.get(aid, 0)
            if pos >= len(lines):
                continue
            for ln in lines[pos:]:
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                evt = d.get("event", "")
                with self._lock:
                    self.event_counter[evt] += 1
                    if evt and evt not in KNOWN_EVENTS:
                        self.new_events.add(evt)
                # 运行聚合（launch 切分）
                with self._lock:
                    if evt == "launch":
                        self.runs.append({"aid": aid, "start": d.get("ts", ""), "events": [], "exit": None})
                        if len(self.runs) > 200:
                            self.runs = self.runs[-200:]
                    if self.runs:
                        r = self.runs[-1]
                        r["events"].append({"t": d.get("ts", ""), "e": evt})
                        if evt == "exit":
                            r["exit"] = (d.get("line") or "")[:80]
            self._pos[aid] = len(lines)
        # 每 10 分钟落盘索引
        if time.time() - self._last_save > 600:
            self._last_save = time.time()
            try:
                INDEX.mkdir(parents=True, exist_ok=True)
                with self._lock:
                    snap = {
                        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "runs": len(self.runs),
                        "event_stats": dict(self.event_counter.most_common(40)),
                        "new_events": sorted(self.new_events),
                    }
                (INDEX / "live.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass

    def snapshot(self) -> dict:
        """实时索引快照（API 用）。"""
        with self._lock:
            return {
                "runs": len(self.runs),
                "event_stats": dict(self.event_counter.most_common(40)),
                "new_events": sorted(self.new_events),
                "recent_runs": [
                    {"aid": r["aid"], "start": r.get("start", ""), "n_events": len(r["events"]), "exit": r.get("exit")}
                    for r in self.runs[-10:]
                ],
            }

_SORTER = None  # 全局实例（api_fastapi 读）


def start_sorter() -> LogSorterThread:
    """启动自动分拣线程（main_web 调用一次）。"""
    global _SORTER
    if _SORTER is None:
        _SORTER = LogSorterThread()
        _SORTER.start()
    return _SORTER
