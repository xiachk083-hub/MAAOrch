#!/usr/bin/env python3
"""AUTO-MAS 完成钩子 — 账号运行一完成就抓取数据入本地存档。

轮询 AUTO-MAS history（默认每 30s），发现新运行立即拉取并解析
（掉落/理智/基建/版本），增量合并进 data/auto_mas_history.json。
相当于"完成一个账号就抓取一下"（30s 内落库）。

用法:
    python tools/auto_mas_hook.py                # 常驻守护
    python tools/auto_mas_hook.py --once         # 跑一轮退出（手动/测试）
    python tools/auto_mas_hook.py --interval 60  # 自定义轮询间隔（秒）
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auto_mas_archiver as arch  # noqa: E402


def fetch_recent_days(days: int = 2) -> dict[str, dict]:
    """拉最近 N 天（含今天）的 history 索引 {日期: {账号: info}}。"""
    out: dict[str, dict] = {}
    today = date.today()
    for i in range(days):
        ds = (today - timedelta(days=i)).isoformat()
        try:
            out[ds] = arch.fetch_day(ds)
        except Exception as e:
            print(f"[钩子] 拉取 {ds} 失败: {str(e)[:120]}", flush=True)
    return out


def merge_new_runs(archive: dict, days_data: dict[str, dict]) -> int:
    """把新运行合并进存档，返回新增条数。"""
    added = 0
    for ds, accts in days_data.items():
        day = archive.setdefault("days", {}).setdefault(ds, {})
        for full_name, info in accts.items():
            name = full_name.replace("明日方舟-", "")
            runs = day.setdefault(name, [])
            known = {r.get("time") for r in runs}
            err_map = info.get("error_info") or {}
            for run in info.get("index", []):
                t = run.get("date", "")
                if t in known:
                    continue
                item = {
                    "time": t,
                    "status": run.get("status", "?"),
                    "duration_sec": None,
                    "json_file": run.get("jsonFile", "").split("\\")[-1],
                    "path": run.get("jsonFile", ""),
                }
                if run.get("status") == "DONE":
                    rec = arch.fetch_record(run.get("jsonFile", ""))
                    if rec:
                        item["duration_sec"] = rec.get("duration_sec")
                        if rec.get("drops") or rec.get("infrast") or rec.get("sanity"):
                            item.update({k: rec[k] for k in
                                         ("version", "maa_path", "sanity", "drops", "chains", "infrast")
                                         if rec.get(k)})
                elif run.get("status") == "ERROR":
                    item["error"] = err_map.get(t) or "运行失败"
                    rec = arch.fetch_record(run.get("jsonFile", ""))
                    if rec and rec.get("error_task"):
                        item["error_task"] = rec["error_task"]
                runs.append(item)
                known.add(t)
                added += 1
                print(f"[钩子] 抓取 {name} {t} {item['status']} "
                      f"理智={item.get('sanity','-')} 掉落={len(item.get('drops', []))}块 "
                      f"基建={len(item.get('infrast', {}))}设施", flush=True)
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description="AUTO-MAS 完成钩子（新运行即抓取）")
    ap.add_argument("--once", action="store_true", help="跑一轮退出")
    ap.add_argument("--interval", type=float, default=30, help="轮询间隔秒（默认 30）")
    ap.add_argument("--days", type=int, default=2, help="每次检查最近 N 天（默认 2，覆盖跨天）")
    args = ap.parse_args()

    archive = arch.load_archive()
    first = True
    while True:
        try:
            days_data = fetch_recent_days(args.days)
            added = merge_new_runs(archive, days_data)
            if added:
                archive.setdefault("meta", {})["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                arch.save_archive(archive)
                print(f"[钩子] 新增 {added} 条，已存档", flush=True)
            elif first:
                print(f"[钩子] 无新运行（存档已最新 {archive.get('meta', {}).get('latest', '?')}）", flush=True)
        except Exception as e:
            print(f"[钩子] 轮询异常: {str(e)[:150]}", flush=True)
        first = False
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
