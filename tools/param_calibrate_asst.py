"""参数校准（asst.log 版）— 用 maa_history 全量 asst.log 校准固定时间参数。

events.log 部署即清（今天 20:35 部署后样本 n=6）→ 数据不足；
maa_history 是部署保留路径（963 个 asst.log，46 账号 × 5 天）—
每行毫秒级时间戳，可精确解析：

  Version v6.16.6                    = 一次运行开始
  ConnectionInfo {"what":"UuidGot"}   = ADB 连接成功（启动就绪点）
  AllTasksCompleted                   = 整链完成
  FightMissionFailed / SubTaskError   = 失败/错误

统计项 → 校准参数:
  Version→UuidGot 秒                → launch_ready_timeout / boot_wait
  Version→AllTasksCompleted 分钟    → max_run_minutes
  同账号相邻运行间隔 分钟           → emu_pending_keep_min（真实重试间隔）
  完成率 / battle_failed / err 频率 → 错误窗口阈值
  asst.log 行时间戳间隔             → log_stall_minutes

用法: python tools/param_calibrate_asst.py [--path 日志根目录]
      （默认读本地 logs/collected/logs/maa_history；远程用 --remote 经 manager 跑）
"""
from __future__ import annotations
import argparse
import base64
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
RUN_RE = re.compile(r"Version v")
READY_RE = re.compile(r'"what":"UuidGot"')
DONE_RE = re.compile(r"AllTasksCompleted")
FAIL_RE = re.compile(r"FightMissionFailed")
ERR_RE = re.compile(r'SubTaskError\s+\{"class":"asst::(\w+)')
CHAIN_RE = re.compile(r'"taskchain":"(\w+)"')

MIN_SAMPLES = 10


def parse_asst(fp: Path) -> dict:
    """解析单个 asst.log → 运行片段统计。返回 {n_runs, ready_s[], done_s[], fails, errs, gaps[]}"""
    runs = []  # 每次 Version v 开新段
    cur = None
    last_ts = None
    gaps = []
    fails = 0
    errs = Counter()
    for ln in open(fp, encoding="utf-8", errors="replace"):
        m = TS_RE.search(ln)
        if not m:
            continue
        ts = m.group(1)
        if last_ts:
            gaps.append((float(ts[17:20]) - float(last_ts[17:20])) + 1.0 if ts[:17] == last_ts[:17] else 0.0)
        last_ts = ts
        if RUN_RE.search(ln):
            cur = {"start": ts, "ready": None, "done": None, "last": None}
            runs.append(cur)
            continue
        if cur is None:
            continue
        cur["last"] = ts  # 每次日志行的最新时间戳 = 运行存活末端
        if READY_RE.search(ln) and cur["ready"] is None:
            cur["ready"] = ts
        elif DONE_RE.search(ln) and cur["done"] is None:
            cur["done"] = ts
        if FAIL_RE.search(ln):
            fails += 1
        em = ERR_RE.search(ln)
        if em:
            errs[em.group(1)] += 1
    return {"runs": runs, "fails": fails, "errs": errs}


def _sec(a: str, b: str) -> float:
    from datetime import datetime
    f = "%Y-%m-%d %H:%M:%S.%f"
    try:
        return (datetime.strptime(b, f) - datetime.strptime(a, f)).total_seconds()
    except Exception:
        return -1.0


def analyze(root: Path) -> dict:
    launch_chain = []   # Version→UuidGot 秒
    task_dur = []       # Version→AllTasksCompleted 分钟
    run_alive = []      # 所有运行的存活时长（最后日志−开始）分钟 — 保险丝误杀验证
    retry_gap = []      # 同账号相邻运行间隔 分钟（前一次未完成）
    retry_gap_short = []  # 其中 <60m 的短重试（队列长等 ≠ 重试，另看）
    done_count = 0
    total_runs = 0
    fails_total = 0
    errs_total = Counter()
    per_account = defaultdict(list)

    for fp in sorted(root.glob("*/*_asst.log")):
        st = parse_asst(fp)
        if not st["runs"]:
            continue
        total_runs += len(st["runs"])
        fails_total += st["fails"]
        errs_total.update(st["errs"])
        for r in st["runs"]:
            if r["ready"]:
                s = _sec(r["start"], r["ready"])
                if s > 0:
                    launch_chain.append(s)
            if r["done"]:
                s = _sec(r["start"], r["done"])
                if s > 0:
                    task_dur.append(s / 60)
                done_count += 1
            alive = _sec(r["start"], r["last"] or r["start"])
            if alive > 0:
                run_alive.append(alive / 60)
            per_account[fp.parent.name].append((r["start"], r["done"] is not None))
    # 重试间隔：同账号目录内相邻运行；**前一次未完成（无 AllTasksCompleted）
    # 且间隔 < 6h 才算重试** — 正常定时调度（隔天/隔数小时）不是重试。
    for aid, runs in per_account.items():
        runs.sort(key=lambda x: x[0])
        for i in range(1, len(runs)):
            s = _sec(runs[i - 1][0], runs[i][0])
            if 0 < s < 6 * 3600 and not runs[i - 1][1]:
                retry_gap.append(s / 60)
                if s < 3600:
                    retry_gap_short.append(s / 60)

    def stats(v):
        v = sorted(v)
        n = len(v)
        if not n:
            return None
        return {"n": n, "med": v[n // 2], "p90": v[int(n * 0.9)], "max": v[-1]}

    return {
        "files": sum(1 for _ in root.glob("*/*_asst.log")),
        "runs": total_runs, "done": done_count,
        "launch_chain_sec": stats(launch_chain),
        "task_dur_min": stats(task_dur),
        "run_alive_min": stats(run_alive),
        "alive_gt_60": sum(1 for m in run_alive if m > 60),
        "alive_gt_90": sum(1 for m in run_alive if m > 90),
        "alive_gt_180": sum(1 for m in run_alive if m > 180),
        "retry_gap_min": stats(retry_gap),
        "retry_gap_short_min": stats(retry_gap_short),
        "fail_rate_per_run": round(fails_total / max(1, total_runs), 2),
        "err_types": dict(errs_total.most_common(10)),
    }


def render(data: dict) -> str:
    L = []
    L.append("=" * 70)
    L.append("参数校准表（数据源: maa_history asst.log 全量解析）")
    L.append("=" * 70)
    rows = []
    lc = data.get("launch_chain_sec")
    if lc and lc["n"] >= MIN_SAMPLES:
        rows.append(("launch_ready_timeout", "150s",
                     f"启动→ADB就绪 med={lc['med']:.0f}s p90={lc['p90']:.0f}s max={lc['max']:.0f}s (n={lc['n']})",
                     f"建议 {max(60, int(lc['p90'] * 2.5))}s", "高"))
    elif lc:
        rows.append(("launch_ready_timeout", "150s", f"启动链路 n={lc['n']}（样本不足）", "保持", "低"))
    td = data.get("task_dur_min")
    if td and td["n"] >= MIN_SAMPLES:
        rows.append(("max_run_minutes", "180m",
                     f"任务 med={td['med']:.0f}m p90={td['p90']:.0f}m max={td['max']:.0f}m (n={td['n']})",
                     "保持 180（保险丝，肉鸽长任务需余量）" if td["max"] > 100 else f"可收紧到 {max(60, int(td['p90'] * 1.5))}m",
                     "高"))
    elif td:
        rows.append(("max_run_minutes", "180m", f"任务 n={td['n']}（样本不足）", "保持", "低"))
    rg = data.get("retry_gap_min")
    rgs = data.get("retry_gap_short_min")
    if rg and rg["n"] >= MIN_SAMPLES:
        rgs_txt = (f" 短重试(<60m) med={rgs['med']:.1f}m p90={rgs['p90']:.1f}m n={rgs['n']}"
                   if rgs and rgs["n"] >= MIN_SAMPLES else "")
        rows.append(("emu_pending_keep_min", "10m",
                     f"重试间隔 med={rg['med']:.1f}m p90={rg['p90']:.1f}m (n={rg['n']}){rgs_txt}",
                     f"建议 {max(10, int(rgs['p90']) + 5) if rgs and rgs['n'] >= MIN_SAMPLES else '保持 10'}m（覆盖短重试 p90；长队列等待该回收）",
                     "高"))
    elif rg:
        rows.append(("emu_pending_keep_min", "10m", f"重试间隔 n={rg['n']}（样本不足）", "保持", "低"))
    ra = data.get("run_alive_min")
    if ra and ra["n"] >= MIN_SAMPLES:
        rows.append(("max_run_minutes 误杀检查", "180m",
                     f"运行存活 med={ra['med']:.0f}m p90={ra['p90']:.0f}m max={ra['max']:.0f}m (n={ra['n']})",
                     f"存活>60m: {data.get('alive_gt_60', 0)} | >90m: {data.get('alive_gt_90', 0)} | >180m: {data.get('alive_gt_180', 0)}",
                     "高"))
    for name, cur, data_s, sugg, conf in rows:
        L.append(f"{name:<26} 当前={cur:<8} {data_s}")
        L.append(f"{'':<26} → {sugg}  置信: {conf}")
    L.append(f"\n运行总览: 文件={data['files']} 运行={data['runs']} 完整完成={data['done']} "
             f"完成率={data['done'] / max(1, data['runs']) * 100:.0f}%")
    L.append(f"失败率(次/运行): {data['fail_rate_per_run']}")
    L.append(f"SubTaskError 类型: {data['err_types']}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="logs/collected/logs/maa_history")
    ap.add_argument("--remote", action="store_true", help="经 manager 在目标机跑")
    args = ap.parse_args()
    if args.remote:
        import urllib.request
        # 自包含：把本文件除 __main__ 外的全部源码注入远程 python 执行
        # （目标机无此工具文件 — 部署保留路径只有 maa_history/queue.json）
        src = open(__file__, encoding="utf-8").read().rsplit('if __name__ == "__main__":', 1)[0]
        src = src.replace("from __future__ import annotations\n", "")  # exec 里必须文件头
        remote = (
            "import sys\n" + src +
            "\ndata = analyze(Path(r'E:\\MAAOrch\\logs\\maa_history'))\n"
            "print(render(data))\n"
        )
        b64 = base64.b64encode(remote.encode("utf-8")).decode()
        cmd = "python -X utf8 -c \"import base64;exec(base64.b64decode('%s'))\"" % b64
        req = urllib.request.Request(
            "http://100.79.173.69:19998/api/exec",
            data=json.dumps({"command": cmd, "timeout": 300}).encode(),
            headers={"Content-Type": "application/json",
                     "x-manager-token": "e210ad4863f14c4e"},
            method="POST")
        r = json.loads(urllib.request.urlopen(req, timeout=320).read().decode())
        print(r.get("stdout", "") or r.get("error", ""))
        if r.get("stderr"):
            print("[stderr]", r["stderr"][-800:])
        return
    root = Path(args.path)
    if not root.exists():
        print(f"目录不存在: {root}")
        sys.exit(1)
    print(render(analyze(root)))


if __name__ == "__main__":
    main()
