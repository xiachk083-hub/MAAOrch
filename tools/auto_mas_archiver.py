#!/usr/bin/env python3
"""AUTO-MAS 历史数据本地存档（防目标机数据丢失；2026-09-26 前端退役后不再有前端读取方）。

把 AUTO-MAS 的每次运行记录拉到本地 data/auto_mas_history.json：
- 索引（日期/账号/状态/用时/错误信息）增量更新（已有存档只拉新日期）
- DONE 运行额外拉取 log_content（AUTO-MAS 存档的 MAA 运行日志原文），
  解析出掉落统计（关卡/物品/数量/次数）、理智值、任务链、MAA 版本

目标机数据丢失后，本地存档仍完整可分析。掉落统计供刷图优化/运营统计使用。

用法:
    python tools/auto_mas_archiver.py                # 增量拉取存档（含掉落统计）
    python tools/auto_mas_archiver.py --full         # 全量重拉索引（不含统计）
    python tools/auto_mas_archiver.py --enrich       # 给历史存档补齐掉落统计
    python tools/auto_mas_archiver.py --show         # 打印存档统计
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.request

HOST = "http://100.79.173.69:36163"
ARCHIVE = pathlib.Path(__file__).resolve().parent.parent / "data" / "auto_mas_history.json"
MAX_DAYS = 420          # 往前拉取上限（约 14 个月）
EMPTY_STOP = 14         # 连续 N 天空数据即停
DONE_LOG_LIMIT = 3      # 每账号每天最多拉取的 DONE 次数（--full 索引重拉时控制请求量）


def api(path: str, body: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(body if body is not None else {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fmt(d) -> str:
    return d.strftime("%Y-%m-%d")


def load_archive() -> dict:
    if ARCHIVE.exists():
        try:
            return json.loads(ARCHIVE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"meta": {"updated_at": "", "earliest": "", "latest": ""}, "days": {}}


def save_archive(arch: dict) -> None:
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(json.dumps(arch, ensure_ascii=False, indent=1), encoding="utf-8")
    # 归档副本到 ui/retired_web_data/。
    # 2026-09-26（前端整体退役）：旧目标 ui/web/data/ 会把刚清掉的目录重新长出来，故改到本目录。
    # grep 核实（auto_mas_history / retired_web_data）结果：**本副本当前无任何读取方** ——
    # 前端 ui/web/auto_mas.js 已随前端删除；规范档仍是上面的 data/auto_mas_history.json。
    # 因此这里只作「目标机数据丢失后的历史备份」，不参与任何读取链路。
    web_arch = pathlib.Path(__file__).resolve().parent.parent / "ui" / "retired_web_data" / "auto_mas_history.json"
    try:
        web_arch.parent.mkdir(parents=True, exist_ok=True)
        web_arch.write_text(json.dumps(arch, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[警告] 复制到 ui/retired_web_data 失败: {e}")


def fetch_day(ds: str) -> dict:
    r = api("/api/history/search", {"mode": "DAILY", "start_date": ds, "end_date": ds})
    return (r.get("data") or {}).get(ds) or {}


def fetch_duration(json_file: str) -> int | None:
    """从 history/data 的日志解析用时（秒）。"""
    rec = fetch_record(json_file)
    return rec.get("duration_sec") if rec else None


def parse_log_content(log: str) -> dict:
    """从 MAA 运行日志原文解析 用时/版本/MAA路径/理智/掉落统计/任务链/基建设施。"""
    out = {"duration_sec": None, "version": "", "maa_path": "", "sanity": "",
           "drops": [], "chains": {"start": [], "completed": []}, "infrast": {}}
    if not log:
        return out

    m = re.search(r"用时\s*(\d+)h\s*(\d+)m\s*(\d+)s", log)
    if m:
        out["duration_sec"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m = re.search(r"Version\s+(v[\d.]+)", log)
    if m:
        out["version"] = m.group(1)
    m = re.search(r"User Dir\s+(\S+)", log)
    if m:
        out["maa_path"] = m.group(1).rstrip("\\")
    m = re.findall(r"理智[:：]?\s*(\d+)/(\d+)", log)
    if m:
        out["sanity"] = f"{m[-1][0]}/{m[-1][1]}"
    for t in re.finditer(r"Start Task Chain:\s*(\w+)", log):
        out["chains"]["start"].append(t.group(1))
    for t in re.finditer(r"Completed Task Chain:\s*(\w+)", log):
        out["chains"]["completed"].append(t.group(1))

    # 基建设施访问（MAA 基建换班时逐设施打卡："当前设施: 制造站 01"）
    infra: dict[str, int] = {}
    for m in re.finditer(r"当前设施[:：]?\s*([\u4e00-\u9fff]{2,6})\s*(\d+)", log):
        fac = m.group(1).strip()
        if fac:
            infra[fac] = infra.get(fac, 0) + 1
    if infra:
        out["infrast"] = infra

    # 掉落统计块：`<2> TO-5 掉落统计:` 后跟物品行（无时间戳），`当前次数 : N` 收尾
    lines = log.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if "掉落统计" in ln:
            m = re.search(r"<2>\s*([\w\-/]+)\s*掉落统计:", ln)
            stage = m.group(1) if m else "?"
            items: list[dict] = []
            runs: int | None = None
            i += 1
            while i < len(lines):
                s = lines[i].strip().rstrip("\r")
                if not s or "掉落统计" in s:
                    break
                if "当前次数" in s:
                    mm = re.search(r"当前次数\s*[:：]?\s*(\d+)", s)
                    runs = int(mm.group(1)) if mm else None
                    i += 1
                    break
                mm = re.match(r"^(.+?)\s*:\s*(\d+)(?:\s*\((\+?\d+)\))?$", s)
                if mm and mm.group(1).strip() not in ("理智", "当前次数"):
                    items.append({"name": mm.group(1).strip(),
                                  "count": int(mm.group(2)),
                                  "delta": int(mm.group(3)) if mm.group(3) else None})
                i += 1
            out["drops"].append({"stage": stage, "runs": runs, "items": items})
            continue
        i += 1

    # 失败任务（MAA 出错行："任务出错: 理智作战"）
    m = re.search(r"任务出错[:：]?\s*([\u4e00-\u9fffA-Za-z]+)", log)
    if m:
        out["error_task"] = m.group(1).strip()
    return out


def fetch_record(json_file: str) -> dict | None:
    """拉一条运行的 log_content 并解析结构化字段；失败/无日志返回 None。"""
    try:
        d = api("/api/history/data", {"jsonPath": json_file.replace("\\", "/")}, timeout=60)
        log = (d.get("data") or {}).get("log_content") or ""
        if not log:
            return None
        return parse_log_content(log)
    except Exception:
        return None


def collect_range(start_ds: str, end_ds: str, with_data: bool = True) -> dict[str, dict]:
    """拉 [start_ds, end_ds] 每天的运行记录，返回 {日期: {账号: [运行, ...]}}。

    with_data=True（增量默认）：DONE 运行全量拉 log_content 解析掉落统计；
    with_data=False（--full 索引重拉）：每账号每天限 DONE_LOG_LIMIT 条只解析用时。
    """
    from datetime import date, timedelta
    s = date.fromisoformat(start_ds)
    e = date.fromisoformat(end_ds)
    dates = []
    d = s
    while d <= e:
        dates.append(fmt(d))
        d += timedelta(days=1)

    # 并发拉每天的 search
    CONC = 7
    day_data: dict[str, dict] = {}
    for i in range(0, len(dates), CONC):
        chunk = dates[i:i + CONC]
        for ds, accts in zip(chunk, [fetch_day(x) for x in chunk]):
            day_data[ds] = accts
        print(f"  已拉日期 {dates[i]} ~ {chunk[-1]}（共 {len(dates)} 天）", flush=True)

    # 收集 DONE/ERROR 的 jsonFile 拉记录（ERROR 也拉 — 日志里有失败任务名）
    jobs = []
    for ds, accts in day_data.items():
        for full_name, info in accts.items():
            done_n = 0
            for run in info.get("index", []):
                if run.get("status") == "DONE":
                    if not with_data and done_n >= DONE_LOG_LIMIT:
                        continue
                    done_n += 1
                elif run.get("status") != "ERROR":
                    continue
                jobs.append((ds, full_name, run))
    action = "日志解析掉落/失败任务" if with_data else "日志解析用时"
    print(f"  拉取 {len(jobs)} 条运行 {action}…", flush=True)
    records: dict[tuple, dict | int | None] = {}
    for j, (ds, name, run) in enumerate(jobs):
        jf = run.get("jsonFile", "")
        records[(ds, name, run.get("date", ""))] = fetch_record(jf) if with_data else fetch_duration(jf)
        if (j + 1) % 20 == 0:
            print(f"    {j + 1}/{len(jobs)}", flush=True)
        time.sleep(0.05)

    # 组装
    out: dict[str, dict] = {}
    for ds, accts in day_data.items():
        out[ds] = {}
        for full_name, info in accts.items():
            runs = []
            err_map = info.get("error_info") or {}
            for run in info.get("index", []):
                key = (ds, full_name, run.get("date", ""))
                rec = records.get(key)
                if with_data:
                    dur = rec.get("duration_sec") if isinstance(rec, dict) else None
                else:
                    dur = rec if isinstance(rec, int) else None
                item: dict = {
                    "time": run.get("date", ""),
                    "status": run.get("status", "?"),
                    "duration_sec": dur,
                    "json_file": run.get("jsonFile", "").split("\\")[-1],
                    "path": run.get("jsonFile", ""),
                }
                if run.get("status") == "ERROR":
                    item["error"] = err_map.get(run.get("date", "")) or "运行失败"
                    if isinstance(rec, dict) and rec.get("error_task"):
                        item["error_task"] = rec["error_task"]
                if isinstance(rec, dict) and (rec.get("drops") or rec.get("infrast") or rec.get("sanity")):
                    item.update({k: rec[k] for k in ("version", "maa_path", "sanity", "drops", "chains", "infrast") if rec.get(k)})
                runs.append(item)
            out[ds][full_name.replace("明日方舟-", "")] = runs
    return out


def enrich_archive(arch: dict) -> int:
    """给存档补拉 log_content：DONE 缺掉落统计 / ERROR 缺失败任务，返回补拉条数。"""
    jobs = []
    for ds, accts in arch.get("days", {}).items():
        for name, runs in accts.items():
            for run in runs:
                if run.get("status") == "DONE" and "drops" not in run:
                    jobs.append((ds, name, run, "drops"))
                elif run.get("status") == "ERROR" and "error_task" not in run:
                    jobs.append((ds, name, run, "error_task"))
    if not jobs:
        return 0
    n_done = sum(1 for _, _, _, kind in jobs if kind == "drops")
    n_err = sum(1 for _, _, _, kind in jobs if kind == "error_task")
    print(f"回填 {len(jobs)} 条日志（DONE缺统计 {n_done} / ERROR缺失败任务 {n_err}）…", flush=True)
    for j, (ds, name, run, kind) in enumerate(jobs):
        # 存档可能只有文件名；先试存档名目录，再试带"明日方舟-"前缀的目录
        jf = run.get("path", "")
        rec = None
        if kind == "drops":
            if jf:
                rec = fetch_record(jf)
            else:
                base = f"E:/AUTO-MAS/history/{ds}"
                for c in (f"{base}/{name}/{run.get('json_file', '')}",
                          f"{base}/明日方舟-{name}/{run.get('json_file', '')}"):
                    rec = fetch_record(c)
                    if rec and rec.get("drops"):
                        jf = c
                        break
        else:
            rec = fetch_record(jf or f"E:/AUTO-MAS/history/{ds}/明日方舟-{name}/{run.get('json_file', '')}")
        if isinstance(rec, dict):
            if kind == "error_task" and rec.get("error_task"):
                run["error_task"] = rec["error_task"]
                run["path"] = jf or run.get("path", "")
            elif kind == "drops" and (rec.get("drops") or rec.get("infrast") or rec.get("sanity")):
                run["path"] = jf
                run["duration_sec"] = rec.get("duration_sec") or run.get("duration_sec")
                run.update({k: rec[k] for k in ("version", "maa_path", "sanity", "drops", "chains", "infrast") if rec.get(k)})
        if (j + 1) % 20 == 0:
            print(f"    {j + 1}/{len(jobs)}", flush=True)
        time.sleep(0.05)
    return len(jobs)


def main() -> None:
    ap = argparse.ArgumentParser(description="AUTO-MAS 历史数据本地存档")
    ap.add_argument("--full", action="store_true", help="全量重拉索引（不含掉落统计，量大慎用）")
    ap.add_argument("--enrich", action="store_true", help="给历史存档补齐掉落统计")
    ap.add_argument("--today", action="store_true", help="强制重拉今天（补当天晚间运行）")
    ap.add_argument("--show", action="store_true", help="只打印存档统计")
    args = ap.parse_args()

    if args.show:
        arch = load_archive()
        days = arch.get("days", {})
        n_runs = sum(len(runs) for accts in days.values() for runs in accts.values())
        n_drops = sum(1 for accts in days.values() for runs in accts.values()
                      for r in runs if r.get("drops"))
        print(f"存档: {ARCHIVE}")
        print(f"日期范围: {arch['meta'].get('earliest')} ~ {arch['meta'].get('latest')}")
        print(f"有数据天数: {len(days)} | 运行记录: {n_runs} 条 | 含掉落统计: {n_drops} 条")
        return

    from datetime import date, timedelta
    arch = load_archive()
    today = fmt(date.today())
    latest = arch["meta"].get("latest", "")

    if args.enrich:
        n = enrich_archive(arch)
        print(f"回填完成: {n} 条")
        arch["meta"]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_archive(arch)
        return

    if args.today:
        # 强制重拉今天（当天增量不自动刷新 — 早上的存档拿不到晚上的运行）
        print(f"重拉今天 {today}")
        part = collect_range(today, today, with_data=True)
        if today in part:
            arch["days"][today] = part[today]
        arch["meta"]["latest"] = today
        arch["meta"]["earliest"] = min(arch["days"].keys()) if arch["days"] else ""
        arch["meta"]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_archive(arch)
        n_runs = sum(len(runs) for accts in arch["days"].values() for runs in accts.values())
        print(f"存档完成: {ARCHIVE} | 今天 {len(part.get(today, {}))} 个账号 | 总运行 {n_runs} 条")
        return

    if args.full or not latest:
        start_ds = ""
        # 全量：从今天往前拉，直到连续 EMPTY_STOP 天空
        d = date.today()
        collected = 0
        while collected < MAX_DAYS:
            s = d - timedelta(days=MAX_DAYS - collected - 1) if False else None
            # 拉一块 42 天
            batch_s = d - timedelta(days=41)
            if batch_s < date(2026, 1, 1):
                batch_s = date(2026, 1, 1)
            part = collect_range(fmt(batch_s), fmt(d), with_data=False)
            for ds, accts in part.items():
                arch["days"][ds] = accts
            collected += 42
            # 检查最旧 14 天是否全空
            oldest = sorted(arch["days"].keys())[:EMPTY_STOP]
            if len(oldest) >= EMPTY_STOP and all(not arch["days"][o] for o in oldest):
                break
            d = batch_s - timedelta(days=1)
        arch["meta"]["earliest"] = min(arch["days"].keys()) if arch["days"] else ""
        arch["meta"]["latest"] = max(arch["days"].keys()) if arch["days"] else ""
    else:
        # 增量：从 latest+1 到 today（含掉落统计）
        next_ds = fmt(date.fromisoformat(latest) + timedelta(days=1))
        if next_ds <= today:
            print(f"增量拉取 {next_ds} ~ {today}")
            part = collect_range(next_ds, today, with_data=True)
            arch["days"].update(part)
            arch["meta"]["latest"] = today
        else:
            print("已是最新，无需增量")

    arch["meta"]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_archive(arch)
    n_runs = sum(len(runs) for accts in arch["days"].values() for runs in accts.values())
    print(f"存档完成: {ARCHIVE}")
    print(f"日期范围: {arch['meta'].get('earliest')} ~ {arch['meta'].get('latest')} | 运行记录 {n_runs} 条")


if __name__ == "__main__":
    main()
