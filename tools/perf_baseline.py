#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""性能基线（只读）—— 单号日常耗时 / MAA 启动→首个任务链 / 截图用时 / 任务链耗时的
分位数（P50/P90）基线与「最近一轮 vs 基线」回归检测。

背景（2026-09-28, t_c6f3a410）：当天多次感觉「慢」，但没有基线，无法判断是否异常。
本工具把散在两处的时间证据归一成一个可复算的基线文件 + 一个阈值化的 --check。

## 指标与数据源（全部只读）

  A. 单号日常总耗时（按服 / 按槽位）——`E:\\AUTO-MAS\\history\\<班次日>\\<账号>\\*.log`
     该次尝试 gui.log 切片的 末时间戳 − 首时间戳（含唤醒 + 全部任务链；口径同
     account_health：>6h 的样本丢弃）。按 (账号, 班次日) 汇总成「当日总耗时」，
     再按 服（REST/名册）与槽位（MAA 目录）分组。
  B. MAA 启动 → 首个任务链 —— `E:\\MAA-Multi\\MAA_*\\debug\\gui.log`
     `MaaAssistantArknights GUI started` → 其后第一条 `Start Task Chain`（秒）。
     单会话 > 30 分钟视为「非自动化启动」（人工开 GUI 不打任务），不计入统计、只计数。
  C. 截图用时 —— 同 gui.log：`截图用时 <N>ms。`（单次样本分布）+ `截图用时较长/过长(平均: Nms)`。
  D. 任务链级耗时 —— 同 gui.log：`Start/Completed Task Chain: <链>, Task ID: <N>` 按
     （目录, 链, id）先入先出配对；未闭合（只有 Start）单列计数。

窗口口径：班次日 [D 05:00, D+1 05:00)（同 maa_error_daily / account_health）。基线默认取
最近 14 个班次日；gui.log 有 4MiB 轮转（gui.bak.log 存上一段），覆盖不到的部分以
`coverage` 字段如实标注 —— 分位数只对真采到的样本负责。

产出：
  runtime/reports/perf_baseline.json        基线（schema maaorch.perf_baseline/1；P50/P90/样本数）
  runtime/reports/perf_check_<班次日>.json  --check 机读报告（schema maaorch.perf_check/1）
  runtime/reports/perf_check_<班次日>.md    --check 人读报告（超阈项表 + 摘要）

用法：
  python tools/perf_baseline.py                    # 采集近 14 天 → 写 perf_baseline.json + 摘要
  python tools/perf_baseline.py --days 30          # 换窗口
  python tools/perf_baseline.py --check            # 最近一轮（今天班次日）vs 基线 → 超阈就报
  python tools/perf_baseline.py --check --date 2026-09-28 --mult 1.5
  python tools/perf_baseline.py --json             # 打印基线 JSON（不写盘）
  python tools/perf_baseline.py --summary          # 只打一行 PERF_SUMMARY=…
  python tools/perf_baseline.py --no-write         # 不写盘
  python tools/perf_baseline.py --raw-out <路径>   # 另存原始采集（排障）
  python tools/perf_baseline.py --selftest         # 离线自检（不连网）

退出码：0=成功/无回归；3=--check 发现超阈项；2=数据源不可达；1=未预期错误。
环境变量：MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST（同其他 mas_*/maa_* 工具）；
          MAAORCH_MAA_ROOT 覆盖 MAA 根目录（缺省 E:\\MAA-Multi）。

只读保证：远端只上传一个探针脚本文件（E:\\_hermes_perf_probe.ps1，同 maa_error_daily /
account_health 的惯例），不读凭据、不写配置、不落盘任何业务数据。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import json
import math
import os
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
REPORTS = os.path.join(RUNTIME, "reports")

sys.path.insert(0, os.path.join(ROOT, "tools"))

# 复用两条已经血泪定稿的采集底座：ssh 子进程环境自愈 / history 采集与解析 / 名册
from maa_error_daily import SSH_HOST, _child_env, _ssh_exe  # noqa: E402
from account_health import (  # noqa: E402
    OK_RESULT, MAX_RUN_MIN, WINDOW_START_HOUR, _pdt, collect_remote, fam_of,
    load_master_roster, parse_records, resolve_date, rest_roster,
)

MAA_ROOT = os.environ.get("MAAORCH_MAA_ROOT", r"E:\MAA-Multi")
REMOTE_PS = "E:\\_hermes_perf_probe.ps1"
BASELINE_JSON = os.path.join(REPORTS, "perf_baseline.json")

DEFAULT_DAYS = 14          # 基线窗口：最近 N 个班次日
STARTUP_CAP_S = 1800       # 启动→首链 > 30min = 非自动化启动（人工/空转），只计数不入样
STARTUP_MIN_S = 0          # 允许 0（极端快），负值丢弃
CHAIN_CAP_S = 21600        # 链耗时 > 6h 丢弃（同 MAX_RUN_MIN 口径）
MAX_KIND_PER_FILE = 30000  # 单文件单类事件上限（防畸形日志撑爆回传）

THRESHOLDS = {
    "account_day_mult": 1.5,   # 单号当日总耗时 > 基线 P90 × 1.5
    "run_mult": 1.5,           # 单轮耗时（history 单次尝试）同上
    "startup_mult": 1.5,       # MAA 启动→首链 同上
    "screenshot_mult": 1.5,    # 窗口截图 p50 > 基线 p90 × 1.5（单张噪点大，用中位数）
    "chain_mult": 1.5,         # 链级 p50 同上
    "chain_min_n": 2,          # 窗口某链样本 < 2 不判
    "screenshot_min_n": 3,     # 窗口截图样本 < 3 不判
}

SCHEMA_BASE = "maaorch.perf_baseline/1"
SCHEMA_CHECK = "maaorch.perf_check/1"
KINDS = ("UP", "EXIT", "CS", "CD", "S", "W", "O")


# ================================================================ 基础工具

def pct(vals, q):
    """分位数（线性插值；等价 numpy.percentile 默认口径）。"""
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return v[lo]
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def stats(vals, nd=1):
    """-> {n, p50, p90, min, max, mean}（空 -> 全 None / n=0）。"""
    v = [x for x in vals if x is not None]
    if not v:
        return {"n": 0, "p50": None, "p90": None, "min": None, "max": None, "mean": None}
    r = lambda x: None if x is None else round(x, nd)  # noqa: E731
    return {"n": len(v), "p50": r(pct(v, 0.5)), "p90": r(pct(v, 0.9)),
            "min": r(min(v)), "max": r(max(v)), "mean": r(sum(v) / len(v))}


def shift_window(d: dt.date):
    """班次日 -> [D 05:00, D+1 05:00)。"""
    start = dt.datetime.combine(d, dt.time(WINDOW_START_HOUR))
    return start, start + dt.timedelta(days=1)


def _now():
    return dt.datetime.now().replace(microsecond=0)


# ================================================================ 远端采集（gui.log）

_PS_GUI = r"""
$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'
$d0s = '@@D0@@'
$root = '@@MAA@@'
$sw = [Diagnostics.Stopwatch]::StartNew()
$ev = New-Object 'System.Collections.Generic.List[string]'
$errs = New-Object 'System.Collections.Generic.List[string]'
$nFiles = 0; $nTrunc = 0
function Read-Shared([string]$p) {
  $fs = New-Object IO.FileStream($p, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
  try { $sr = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8); return $sr.ReadToEnd() } finally { $fs.Dispose() }
}
$kinds = @(
  @('UP',   'MaaAssistantArknights GUI started'),
  @('EXIT', 'MaaAssistantArknights GUI exited'),
  @('CS',   'Start Task Chain: (\w+), Task ID: (\d+)'),
  @('CD',   'Completed Task Chain: (\w+), Task ID: (\d+)'),
  @('S',    '截图用时 (\d+)ms。'),
  @('W',    '截图用时较长\(平均: (\d+)ms\)'),
  @('O',    '截图用时过长\(平均: (\d+)ms\)')
)
foreach ($d in (Get-ChildItem $root -Directory -Filter 'MAA_*')) {
  foreach ($n in @('gui.log','gui.bak.log')) {
    $f = Join-Path $d.FullName ('debug\' + $n)
    if (-not (Test-Path -LiteralPath $f)) { continue }
    $nFiles++
    $t = $null
    try { $t = Read-Shared $f } catch { $errs.Add($d.Name + '/' + $n + ': ' + $_.Exception.Message); continue }
    if ([string]::IsNullOrEmpty($t)) { continue }
    foreach ($k in $kinds) {
      $m = [regex]::Matches($t, '(?m)^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\]]*\][^\r\n]*' + $k[1])
      $cnt = 0
      foreach ($x in $m) {
        $ts = $x.Groups[1].Value
        if ($ts -lt $d0s) { continue }
        $a = ''; $b = ''
        if ($x.Groups.Count -ge 4) { $a = $x.Groups[2].Value; $b = $x.Groups[3].Value }
        elseif ($x.Groups.Count -ge 3) { $a = $x.Groups[2].Value }
        $ev.Add(($d.Name + '|' + $n + '|' + $ts + '|' + $k[0] + '|' + $a + '|' + $b))
        $cnt++
        if ($cnt -ge @@MAXK@@) { $nTrunc++; break }
      }
    }
  }
}
$sw.Stop()
$payload = [ordered]@{
  events = $ev.ToArray()
  meta = [ordered]@{ files = $nFiles; trunc = $nTrunc; elapsed_ms = $sw.ElapsedMilliseconds
                     errs = $errs.ToArray(); d0 = $d0s; root = $root }
}
$json = ConvertTo-Json -Compress -Depth 5 $payload
$ms = New-Object IO.MemoryStream
$gz = New-Object IO.Compression.GZipStream($ms, [IO.Compression.CompressionMode]::Compress)
$bytes = [Text.Encoding]::UTF8.GetBytes($json)
$gz.Write($bytes, 0, $bytes.Length); $gz.Close()
Write-Output ('__GZ__' + [Convert]::ToBase64String($ms.ToArray()))
"""


def ssh_ps(script, timeout=420):
    """远端执行 PowerShell（只读约定）。形态与 account_health.ssh_ps 同款（上传 + -File）。

    与 account_health 重复这段而不 import 的原因：远端探针文件名不同、两边各自独立演进；
    真正需要共享的 _ssh_exe/_child_env 已 import。
    """
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    payload = b"\xef\xbb\xbf" + (prefix + script).encode("utf-8")
    b64 = base64.b64encode(payload).decode("ascii")
    upload = ("$b64='%s'\n$b=[Convert]::FromBase64String($b64)\n[IO.File]::WriteAllBytes('%s', $b)\n"
              "Write-Output ('written ' + $b.Length)\n" % (b64, REMOTE_PS))
    up = [_ssh_exe(), "-i", os.path.expanduser(os.environ.get(
            "MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target")),
          "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
          SSH_HOST, "chcp 65001 >nul & powershell -NoProfile -Command -"]
    r = subprocess.run(up, input=upload.encode("utf-8"), capture_output=True,
                       timeout=min(timeout, 180), env=_child_env())
    if b"written" not in r.stdout:
        raise RuntimeError("SSH 上传未确认: " +
                           (r.stdout + r.stderr).decode("utf-8", "replace")[:300])
    run = [_ssh_exe(), "-i", os.path.expanduser(os.environ.get(
            "MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target")),
           "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -ExecutionPolicy Bypass -File " + REMOTE_PS]
    r = subprocess.run(run, capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=timeout, env=_child_env())
    return (r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"))


def collect_gui(d0: dt.datetime, timeout=420):
    """采集所有 MAA 目录 gui.log/gui.bak.log 的窗口内事件 -> {events:[…], meta:{…}}。"""
    ps = (_PS_GUI.replace("@@MAA@@", MAA_ROOT)
          .replace("@@D0@@", d0.strftime("%Y-%m-%d %H:%M:%S"))
          .replace("@@MAXK@@", str(MAX_KIND_PER_FILE)))
    out, err = ssh_ps(ps, timeout=timeout)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("__GZ__"):
            return json.loads(gzip.decompress(base64.b64decode(line[len("__GZ__"):])).decode("utf-8"))
    raise RuntimeError("SSH 采集失败（无 __GZ__ 标记）: " + (out or "(空)")[-200:] +
                       " ｜ stderr: " + (err or "(空)")[-400:])


# ================================================================ 解析与派生指标

def parse_events(lines):
    """PS 行 'dir|file|ts|kind|a|b' -> 事件列表（按目录+时间稳定排序）。"""
    ev = []
    for ln in lines or []:
        p = str(ln).split("|")
        if len(p) < 4:
            continue
        d, f, ts, kind = p[0], p[1], p[2], p[3]
        if kind not in KINDS:
            continue
        t = _pdt(ts)
        if not t:
            continue
        a = p[4] if len(p) > 4 else ""
        b = p[5] if len(p) > 5 else ""
        e = {"dir": d, "file": f, "ts": t, "kind": kind, "chain": "", "id": "", "ms": None}
        if kind in ("CS", "CD"):
            e["chain"], e["id"] = a, b
        elif kind in ("S", "W", "O"):
            e["ms"] = int(a) if str(a).isdigit() else None
        ev.append(e)
    ev.sort(key=lambda x: (x["dir"], x["ts"]))
    return ev


def build_sessions(events):
    """按 UP 事件切会话 -> [{dir, t_up, t_first_chain, startup_s, kind}]。

    kind ∈ sample / no_chain / over_cap。
    """
    out = []
    by_dir = defaultdict(list)
    for e in events:
        by_dir[e["dir"]].append(e)
    for d, evs in by_dir.items():
        cur = None
        for e in evs:
            if e["kind"] == "UP":
                if cur:
                    out.append(cur)
                cur = {"dir": d, "t_up": e["ts"], "t_first_chain": None}
            elif cur is not None and e["kind"] == "CS" and cur["t_first_chain"] is None:
                cur["t_first_chain"] = e["ts"]
        if cur:
            out.append(cur)
    for s in out:
        if s["t_first_chain"] is None:
            s["startup_s"] = None
            s["kind"] = "no_chain"
        else:
            s["startup_s"] = (s["t_first_chain"] - s["t_up"]).total_seconds()
            if s["startup_s"] < STARTUP_MIN_S:
                s["kind"] = "no_chain"
            elif s["startup_s"] > STARTUP_CAP_S:
                s["kind"] = "over_cap"
            else:
                s["kind"] = "sample"
    return out


def chain_pairs(events):
    """按（目录, 链, id）**栈式**配对（每条 CD 配「它之前最近的一条未配对 CS」）-> (闭合对, 未闭合计数)。

    为什么是栈而不是队列：MAA 的链在同一会话内**串行**执行，同一 (链, id) 在跨会话时会重复
    （Task ID 每次启动从 1 计）。若队列式（FIFO）配对，上一会话被 kill 遗留的未闭合 CS 会被
    后面的 CD 认领 ⇒ 链耗时被凭空拉长到几小时；栈式配对则会把它留在「未闭合」里如实计数。
    """
    pending = defaultdict(list)
    pairs = []
    for e in events:
        if e["kind"] == "CS":
            pending[(e["dir"], e["chain"], e["id"])].append(e["ts"])
        elif e["kind"] == "CD":
            k = (e["dir"], e["chain"], e["id"])
            q = pending.get(k) or []
            if q:
                t0 = q.pop()
                sec = (e["ts"] - t0).total_seconds()
                if 0 < sec <= CHAIN_CAP_S:
                    pairs.append({"dir": e["dir"], "chain": e["chain"], "id": e["id"],
                                  "start": t0, "done": e["ts"], "sec": sec})
    unclosed = {k[1]: len(v) for k, v in pending.items() if v}
    return pairs, unclosed


def gui_metrics(events, now=None):
    """事件 -> 基线/检查两用的 gui 派生指标（不含历史）。"""
    now = now or _now()
    events = sorted(events, key=lambda e: (e["dir"], e["ts"]))   # 防御：配对依赖时间序
    sessions = build_sessions(events)
    pairs, unclosed = chain_pairs(events)

    su_all, su_by_slot = [], defaultdict(list)
    n_nc = n_oc = 0
    for s in sessions:
        if s["kind"] == "sample":
            su_all.append(s["startup_s"])
            su_by_slot[s["dir"]].append(s["startup_s"])
        elif s["kind"] == "no_chain":
            n_nc += 1
        else:
            n_oc += 1

    shots, shots_slot = [], defaultdict(list)
    warn, over = [], []
    for e in events:
        if e["ms"] is None:
            continue
        if e["kind"] == "S":
            shots.append(e["ms"])
            shots_slot[e["dir"]].append(e["ms"])
        elif e["kind"] == "W":
            warn.append(e["ms"])
        elif e["kind"] == "O":
            over.append(e["ms"])

    ch_all, ch_slot = defaultdict(list), defaultdict(list)
    for p in pairs:
        ch_all[p["chain"]].append(p["sec"])
        ch_slot[p["dir"]].append(p["sec"])

    ts = [e["ts"] for e in events]
    return {
        "startup": {"all": stats(su_all), "by_slot": {d: stats(v) for d, v in sorted(su_by_slot.items())},
                    "launches": len(sessions), "no_chain": n_nc, "over_cap": n_oc,
                    "cap_s": STARTUP_CAP_S},
        "screenshot": {"single_all": stats(shots, 0),
                       "single_by_slot": {d: stats(v, 0) for d, v in sorted(shots_slot.items())},
                       "warn": {"n": len(warn), **stats(warn, 0)},
                       "over_n": len(over)},
        "chains": {"by_chain": {c: stats(v) for c, v in sorted(ch_all.items())},
                   "by_slot": {d: stats(v) for d, v in sorted(ch_slot.items())},
                   "unclosed": unclosed, "pairs": len(pairs),
                   "cap_s": CHAIN_CAP_S},
        "coverage": {"min_ts": min(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else None,
                     "max_ts": max(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else None,
                     "dirs": len({e["dir"] for e in events}),
                     "n_events": len(events)},
    }


def history_metrics(recs, roster=None, master=None, only_day=None):
    """history 记录 -> 单轮/当日耗时指标 + 按 (服, 槽位) 分组。

    only_day: 'YYYY-MM-DD' 时只统计该班次日（--check 用）。
    """
    roster, master = roster or {}, master or {}
    runs, day_totals = [], defaultdict(float)
    for r in recs:
        if only_day and r.get("dirday") != only_day:
            continue
        tf, tl = r.get("t_first"), r.get("t_last")
        if not tf or not tl:
            continue
        mins = (tl - tf).total_seconds() / 60.0
        if not (0 < mins <= MAX_RUN_MIN):
            continue
        ok = str(r.get("result") or "").strip() == OK_RESULT
        runs.append({"acct": r["acct"], "dirday": r["dirday"], "mins": mins, "ok": ok,
                     "result": r.get("result") or ""})
        day_totals[(r["acct"], r["dirday"])] += mins

    def info(acct):
        m = master.get(acct) or {}
        rr = roster.get(acct) or {}
        return {"server": m.get("server") or rr.get("server") or "未知",
                "slot": m.get("maadir") or rr.get("slot") or "未知"}

    def group(vals_by_key):
        g = defaultdict(list)
        for k, v in vals_by_key:
            g[k].append(v)
        return {k: stats(v) for k, v in sorted(g.items())}

    per_acct_runs = defaultdict(list)
    for x in runs:
        per_acct_runs[x["acct"]].append(x["mins"])
    per_acct_days = defaultdict(list)
    for (acct, _dd), tot in day_totals.items():
        per_acct_days[acct].append(tot)

    per_account = {}
    for acct in sorted(set(list(per_acct_runs) + list(per_acct_days))):
        inf = info(acct)
        day = stats(per_acct_days.get(acct, []))
        day["att"] = len(per_acct_runs.get(acct, []))   # 参与统计的尝试数（单日窗口≈当天尝试数）
        per_account[acct] = {**inf, "day": day, "run": stats(per_acct_runs.get(acct, []))}

    return {
        "runs": runs,
        "day_totals": {("%s|%s" % k): v for k, v in day_totals.items()},
        "account_daily_minutes": {
            "all": stats(day_totals.values()),
            "by_server": group((info(a)["server"], v) for (a, _d), v in day_totals.items()),
            "by_slot": group((info(a)["slot"], v) for (a, _d), v in day_totals.items()),
            "per_account": per_account,
        },
        "account_run_minutes": {
            "all": stats(x["mins"] for x in runs),
            "ok": stats(x["mins"] for x in runs if x["ok"]),
            "fail": stats(x["mins"] for x in runs if not x["ok"]),
            "by_server": group((info(x["acct"])["server"], x["mins"]) for x in runs),
            "by_slot": group((info(x["acct"])["slot"], x["mins"]) for x in runs),
        },
    }


# ================================================================ 基线装配

def build_baseline(d, days, h, g, meta=None):
    now = _now()
    d0 = d - dt.timedelta(days=days - 1)
    w0, _ = shift_window(d0)
    out = {
        "schema": SCHEMA_BASE,
        "generator": "tools/perf_baseline.py",
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": {"host": SSH_HOST, "history": r"E:\AUTO-MAS\history",
                   "maa_root": MAA_ROOT, "gui": r"MAA_*\debug\gui.log(+.bak)"},
        "window": {"d0": d0.isoformat(), "d1": d.isoformat(), "days": days,
                   "since": w0.strftime("%Y-%m-%d %H:%M:%S")},
        "thresholds": dict(THRESHOLDS),
        "samples": {"history_runs": len(h["runs"]),
                    "account_days": len(h["day_totals"]),
                    "gui_events": g["coverage"]["n_events"]},
        "account_daily_minutes": h["account_daily_minutes"],
        "account_run_minutes": h["account_run_minutes"],
        "maa_startup_to_first_chain_s": g["startup"],
        "screenshot_ms": g["screenshot"],
        "chain_seconds": g["chains"],
        "coverage": {"gui": g["coverage"], "collect_meta": meta or {}},
    }
    return out


# ================================================================ 阈值比较

def _mult(args):
    m = dict(THRESHOLDS)
    if getattr(args, "mult", None):
        for k in ("account_day_mult", "run_mult", "startup_mult", "screenshot_mult", "chain_mult"):
            m[k] = float(args.mult)
    return m


def _basis(base, section, min_n=3):
    """取比较基准 P90：section.all 的 P90（样本 < min_n 视为无基准）。"""
    allst = ((base.get(section) or {}).get("all")) or {}
    if allst.get("p90") is None or (allst.get("n") or 0) < min_n:
        return None, "无"
    return allst["p90"], "全量"


def compare(base, h, g, mults):
    """窗口指标 vs 基线 -> findings 列表（每项含 kind/subject/value/base/ratio/text）。"""
    out = []

    def add(kind, subject, value, basev, unit, basis, mult):
        if value is None or basev in (None, 0):
            return
        ratio = float(value) / float(basev)
        if ratio > mult:
            out.append({"kind": kind, "subject": subject, "value": round(float(value), 1),
                        "baseline_p90": round(float(basev), 1), "ratio": round(ratio, 2),
                        "unit": unit, "basis": basis, "mult": mult})

    # A. 单号当日总耗时 / 单轮
    basev, basis = _basis(base, "account_daily_minutes")
    adm = base.get("account_daily_minutes") or {}
    for acct, st in sorted((adm.get("per_account") or {}).items()):
        day = st.get("day") or {}
        if not (day.get("n") or 0):
            continue
        # 窗口 = 一个班次日 ⇒ 该号当天 1 个样本（多次尝试已求和）；优先用该号自己的基线 P90
        b, bs = basev, basis
        if day.get("p90") is not None and (day.get("n") or 0) >= 3:
            b, bs = day["p90"], "自身"
        att = day.get("att")
        add("ACCOUNT_DAY_SLOW", "%s（%s/%s%s）" % (
                acct, st.get("server"), st.get("slot"),
                "，%d 次尝试" % att if att else ""),
            day.get("p90"), b, "min", bs, mults["account_day_mult"])
    rv, rbasis = _basis(base, "account_run_minutes")
    for x in h["runs"]:
        # 单轮同样优先用「该号自己的基线 P90」（n≥3），否则全量
        own = ((adm.get("per_account") or {}).get(x["acct"]) or {}).get("run") or {}
        b, bs = rv, rbasis
        if own.get("p90") is not None and (own.get("n") or 0) >= 3:
            b, bs = own["p90"], "自身"
        add("RUN_SLOW", "%s %s 单轮" % (x["acct"], x["dirday"]), x["mins"], b,
            "min", bs, mults["run_mult"])

    # B. 启动→首链
    bv, bs = _basis(base, "maa_startup_to_first_chain_s")
    for s in g["sessions"] if "sessions" in g else []:
        add("STARTUP_SLOW", "%s %s 启动→首链" % (s["dir"], s["t_up"].strftime("%m-%d %H:%M")),
            s["startup_s"], bv, "s", bs, mults["startup_mult"])

    # C. 截图（窗口 p50 vs 基线 p90）
    #    注意：基线里截图段是 single_all 而非 all（本段专用键），不能走 _basis。
    bsh = ((base.get("screenshot_ms") or {}).get("single_all")) or {}
    sv = bsh.get("p90") if (bsh.get("n") or 0) >= 20 else None
    sbs = "全量"
    su = g.get("screenshot", {}).get("single_all") or {}
    if (su.get("n") or 0) >= mults["screenshot_min_n"]:
        add("SCREENSHOT_SLOW", "窗口截图总体", su.get("p50"), sv, "ms", sbs, mults["screenshot_mult"])
    for d, st in sorted((g.get("screenshot", {}).get("single_by_slot") or {}).items()):
        if (st.get("n") or 0) >= mults["screenshot_min_n"]:
            add("SCREENSHOT_SLOW", "槽位 %s 截图" % d, st.get("p50"), sv, "ms", sbs,
                mults["screenshot_mult"])

    # D. 链级（窗口 p50 vs 基线该链 P90）
    for chain, st in sorted((g.get("chains", {}).get("by_chain") or {}).items()):
        if (st.get("n") or 0) < mults["chain_min_n"]:
            continue
        bc = ((base.get("chain_seconds") or {}).get("by_chain") or {}).get(chain)
        if bc and bc.get("p90") is not None and (bc.get("n") or 0) >= 3:
            b, bs = bc["p90"], "该链"
        else:
            b, bs = _basis(base, "chain_seconds")
        add("CHAIN_SLOW", "链 %s" % chain, st.get("p50"), b, "s", bs, mults["chain_mult"])
    return out


# ================================================================ 渲染

def render_check_md(d, findings, base, base_path, h, g, window):
    L = []
    L.append("# 性能回归检测 — 班次日 %s" % d.isoformat())
    L.append("")
    L.append("- 基线：`%s`（built_at=%s，窗口 %s ~ %s）" % (
        os.path.relpath(base_path, ROOT).replace("\\", "/"),
        base.get("generated_at"), (base.get("window") or {}).get("d0"),
        (base.get("window") or {}).get("d1")))
    L.append("- 窗口：[%s, %s)（右端按当前时刻截断）" % (
        window[0].strftime("%Y-%m-%d %H:%M"), window[1].strftime("%Y-%m-%d %H:%M")))
    L.append("- 阈值：超出基线 P90 × 倍数即报（明细见 json `thresholds`）")
    L.append("")
    if findings:
        L.append("## 超阈项（%d）" % len(findings))
        L.append("")
        L.append("| 类型 | 对象 | 实测 | 基线 P90 | 倍率 | 基准 |")
        L.append("|---|---|---|---|---|---|")
        for f in findings:
            L.append("| %s | %s | %s %s | %s %s | ×%.2f | %s |" % (
                f["kind"], f["subject"], f["value"], f["unit"], f["baseline_p90"],
                f["unit"], f["ratio"], f["basis"]))
    else:
        L.append("## 超阈项：无")
    L.append("")
    L.append("## 窗口观测摘要（未超阈也列出，便于人工对照）")
    L.append("")
    dm = h["account_daily_minutes"]
    rm = h["account_run_minutes"]
    su = g["screenshot"]["single_all"]
    st = g["startup"]["all"]
    L.append("- 账号当日总耗时：n=%s p50=%s p90=%s min（基线 %s / %s）" % (
        dm["all"]["n"], dm["all"]["p50"], dm["all"]["p90"],
        (base.get("account_daily_minutes") or {}).get("all", {}).get("p50"),
        (base.get("account_daily_minutes") or {}).get("all", {}).get("p90")))
    L.append("- 单轮耗时：n=%s p50=%s p90=%s min（基线 %s / %s）" % (
        rm["all"]["n"], rm["all"]["p50"], rm["all"]["p90"],
        (base.get("account_run_minutes") or {}).get("all", {}).get("p50"),
        (base.get("account_run_minutes") or {}).get("all", {}).get("p90")))
    L.append("- MAA 启动→首链：n=%s p50=%s p90=%s s（基线 %s / %s；无链 %s，超 30min 未计 %s）" % (
        st["n"], st["p50"], st["p90"],
        ((base.get("maa_startup_to_first_chain_s") or {}).get("all") or {}).get("p50"),
        ((base.get("maa_startup_to_first_chain_s") or {}).get("all") or {}).get("p90"),
        g["startup"]["no_chain"], g["startup"]["over_cap"]))
    L.append("- 截图用时：n=%s p50=%s p90=%s ms（基线 %s / %s ms；较长 %s 条）" % (
        su["n"], su["p50"], su["p90"],
        ((base.get("screenshot_ms") or {}).get("single_all") or {}).get("p50"),
        ((base.get("screenshot_ms") or {}).get("single_all") or {}).get("p90"),
        g["screenshot"]["warn"]["n"]))
    ch = g["chains"]["by_chain"]
    if ch:
        L.append("- 链级 p50（s）：" + "；".join(
            "%s %s(n=%s)" % (c, s["p50"], s["n"]) for c, s in sorted(ch.items())))
    L.append("")
    L.append("## 口径与边界")
    L.append("")
    L.append("- 账号耗时口径 = history 该次 `.log` 末−首（含唤醒+全部链；>6h 丢弃）；无记录账号不参与。")
    L.append("- 启动→首链 > %ds 视为非自动化启动，只计数不入样；截图窗口样本 <%s 张不判（噪点大）。" % (
        STARTUP_CAP_S, THRESHOLDS["screenshot_min_n"]))
    L.append("- gui.log 4MiB 轮转 ⇒ 窗口右端到底但左端可能被轮转截掉；覆盖见 json `coverage`。")
    return "\n".join(L) + "\n"


def render_baseline_summary(base):
    dm = (base.get("account_daily_minutes") or {}).get("all") or {}
    rm = (base.get("account_run_minutes") or {}).get("all") or {}
    su = (base.get("maa_startup_to_first_chain_s") or {}).get("all") or {}
    sh = (base.get("screenshot_ms") or {}).get("single_all") or {}
    ch = ((base.get("chain_seconds") or {}).get("by_chain") or {}).get("StartUp") or {}
    return ("PERF_SUMMARY=baseline %s~%s 账号日耗时P50/P90=%s/%smin(n=%s) 单轮=%s/%smin "
            "启动首链=%s/%ss(n=%s) 截图=%s/%sms(n=%s) StartUp链=%s/%ss" % (
                (base.get("window") or {}).get("d0"), (base.get("window") or {}).get("d1"),
                dm.get("p50"), dm.get("p90"), dm.get("n"),
                rm.get("p50"), rm.get("p90"), su.get("p50"), su.get("p90"), su.get("n"),
                sh.get("p50"), sh.get("p90"), sh.get("n"), ch.get("p50"), ch.get("p90")))


# ================================================================ 命令

def _collect_all(d, days, timeout=420):
    """history + gui 一次采齐；返回 (recs, bad, gui_raw, h, g)。"""
    raw = collect_remote(d, days=days, timeout=timeout)
    recs, bad = parse_records(raw)
    d0 = d - dt.timedelta(days=days - 1)
    w0, _ = shift_window(d0)
    gui_raw = collect_gui(w0, timeout=timeout)
    events = parse_events(gui_raw.get("events"))
    roster, _slots = rest_roster()
    master = load_master_roster()
    h = history_metrics(recs, roster, master)
    g = gui_metrics(events)
    g["sessions"] = build_sessions(events)
    return recs, bad, gui_raw, h, g


def cmd_build(args):
    d = resolve_date(args.date)
    days = int(args.days)
    try:
        recs, bad, gui_raw, h, g = _collect_all(d, days)
    except Exception as e:  # noqa: BLE001
        print("采集失败：%s" % e)
        return 2
    base = build_baseline(d, days, h, g, meta=gui_raw.get("meta"))
    if args.raw_out:
        with open(args.raw_out, "w", encoding="utf-8") as fh:
            json.dump({"recs": recs, "events": gui_raw.get("events"),
                       "meta": gui_raw.get("meta")}, fh, ensure_ascii=False, indent=1)
        print("raw -> %s" % args.raw_out)
    if args.json:
        print(json.dumps(base, ensure_ascii=False, indent=1))
        return 0
    if not args.no_write:
        os.makedirs(REPORTS, exist_ok=True)
        with open(BASELINE_JSON, "w", encoding="utf-8") as fh:
            json.dump(base, fh, ensure_ascii=False, indent=1)
        print("baseline -> %s" % BASELINE_JSON)
    if args.summary:
        print(render_baseline_summary(base))
    else:
        dm = base["account_daily_minutes"]["all"]
        su = base["maa_startup_to_first_chain_s"]["all"]
        sh = base["screenshot_ms"]["single_all"]
        print("窗口 %s ~ %s（%d 天）｜history 单轮 n=%d（坏记录 %d）｜gui 事件 n=%d（文件 %s，截断 %s）"
              % (base["window"]["d0"], base["window"]["d1"], days,
                 len(h["runs"]), bad, g["coverage"]["n_events"],
                 (gui_raw.get("meta") or {}).get("files"), (gui_raw.get("meta") or {}).get("trunc")))
        print("  账号日耗时 min : P50=%s P90=%s (n=%s)" % (dm["p50"], dm["p90"], dm["n"]))
        print("  启动→首链  s   : P50=%s P90=%s (n=%s；无链 %s / 超cap %s)"
              % (su["p50"], su["p90"], su["n"], base["maa_startup_to_first_chain_s"]["no_chain"],
                 base["maa_startup_to_first_chain_s"]["over_cap"]))
        print("  截图      ms   : P50=%s P90=%s (n=%s)" % (sh["p50"], sh["p90"], sh["n"]))
        for c, s in sorted(base["chain_seconds"]["by_chain"].items()):
            print("  链 %-10s s  : P50=%s P90=%s (n=%s)" % (c, s["p50"], s["p90"], s["n"]))
        print(render_baseline_summary(base))
    return 0


def cmd_check(args):
    path = args.baseline or BASELINE_JSON
    if not os.path.exists(path):
        print("基线不存在：%s —— 先跑一次 `python tools/perf_baseline.py`" % path)
        return 2
    base = json.load(open(path, encoding="utf-8"))
    if base.get("schema") != SCHEMA_BASE:
        print("基线 schema 不匹配：%s" % base.get("schema"))
        return 2
    d = resolve_date(args.date)
    w0, w1 = shift_window(d)
    now = _now()
    w1 = min(w1, now)
    mults = _mult(args)
    try:
        raw = collect_remote(d, days=3, timeout=args.timeout)
        recs, _bad = parse_records(raw)
        gui_raw = collect_gui(w0, timeout=args.timeout)
    except Exception as e:  # noqa: BLE001
        print("采集失败：%s" % e)
        return 2
    roster, _slots = rest_roster()
    master = load_master_roster()
    day_recs = [r for r in recs if r.get("dirday") == d.isoformat()]
    h = history_metrics(day_recs, roster, master)
    events = [e for e in parse_events(gui_raw.get("events")) if w0 <= e["ts"] <= w1]
    g = gui_metrics(events, now)
    g["sessions"] = build_sessions(events)

    findings = compare(base, h, g, mults)
    report = {
        "schema": SCHEMA_CHECK,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "shift_day": d.isoformat(),
        "window": [w0.strftime("%Y-%m-%d %H:%M:%S"), w1.strftime("%Y-%m-%d %H:%M:%S")],
        "baseline": {"path": path, "generated_at": base.get("generated_at"),
                     "window": base.get("window")},
        "thresholds": mults,
        "findings": findings,
        "observed": {
            "account_daily_minutes": h["account_daily_minutes"],
            "account_run_minutes": h["account_run_minutes"],
            "maa_startup_to_first_chain_s": dict(g["startup"]),
            "screenshot_ms": g["screenshot"],
            "chain_seconds": g["chains"],
            "coverage": g["coverage"],
        },
    }
    md = render_check_md(d, findings, base, path, h, g, (w0, w1))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 3 if findings else 0
    if not args.no_write:
        os.makedirs(REPORTS, exist_ok=True)
        pj = os.path.join(REPORTS, "perf_check_%s.json" % d.isoformat())
        pm = os.path.join(REPORTS, "perf_check_%s.md" % d.isoformat())
        with open(pj, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        with open(pm, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("check -> %s / %s" % (pj, pm))
    print(md)
    if findings:
        print("PERF_CHECK=%d 项超阈：%s" % (
            len(findings), ", ".join(sorted({f["kind"] for f in findings}))))
    else:
        print("PERF_CHECK=OK")
    return 3 if findings else 0


# ================================================================ 自检（离线）

def _mk_ev(dirname, ts, kind, a="", b=""):
    return "%s|gui.log|%s|%s|%s|%s" % (dirname, ts, kind, a, b)


def _selftest():
    ok = [0, 0]

    def chk(name, got, exp):
        ok[1] += 1
        if got == exp:
            ok[0] += 1
            return True
        print("FAIL %s: got %r exp %r" % (name, got, exp))
        return False

    # --- 1) 分位数
    chk("pct p50 偶数", round(pct([10, 20, 30, 40], 0.5), 4), 25.0)
    chk("pct p90 1..10", round(pct(list(range(1, 11)), 0.9), 4), 9.1)
    chk("pct 单样本", pct([7], 0.9), 7)
    chk("pct 空", pct([], 0.5), None)
    chk("stats n", stats([5, 5, 5])["n"], 3)
    chk("stats p90", stats([1, 2, 3])["p90"], 2.8)

    # --- 2) 事件解析（含 PS 行形状 / 缺字段 / 未知 kind）
    lines = [
        _mk_ev("MAA_6", "2026-09-28 06:03:22", "UP"),
        _mk_ev("MAA_6", "2026-09-28 06:04:09", "S", "363"),
        _mk_ev("MAA_6", "2026-09-28 06:05:00", "CS", "StartUp", "1"),
        _mk_ev("MAA_6", "2026-09-28 06:07:31", "CD", "StartUp", "1"),
        _mk_ev("MAA_6", "2026-09-28 06:40:28", "W", "638"),
        "badline",
        _mk_ev("MAA_6", "2026-09-28 07:00:00", "ZZ", "x"),
    ]
    ev = parse_events(lines)
    chk("parse n", len(ev), 5)
    chk("parse 截图 ms", [e["ms"] for e in ev if e["kind"] == "S"], [363])
    chk("parse 链字段", [(e["chain"], e["id"]) for e in ev if e["kind"] == "CS"],
        [("StartUp", "1")])

    # --- 3) 会话：正常 / 无链 / 超 cap（31min）
    e2 = parse_events([
        _mk_ev("MAA_2", "2026-09-28 06:00:00", "UP"),
        _mk_ev("MAA_2", "2026-09-28 06:00:42", "CS", "StartUp", "1"),
        _mk_ev("MAA_2", "2026-09-28 07:00:00", "UP"),                       # 新会话，无链
        _mk_ev("MAA_2", "2026-09-28 08:00:00", "UP"),                       # 新会话
        _mk_ev("MAA_2", "2026-09-28 08:31:00", "CS", "StartUp", "1"),       # 31min > cap
    ])
    ss = build_sessions(e2)
    chk("sessions n", len(ss), 3)
    chk("session1 kind/s", (ss[0]["kind"], ss[0]["startup_s"]), ("sample", 42.0))
    chk("session2 kind", ss[1]["kind"], "no_chain")
    chk("session3 kind", ss[2]["kind"], "over_cap")

    # --- 4) 链配对：栈式（同 id 跨会话）/ 未闭合
    e3 = parse_events([
        _mk_ev("MAA_1", "2026-09-28 05:10:00", "CS", "Fight", "2"),         # 上一会话被 kill 遗留
        _mk_ev("MAA_1", "2026-09-28 05:40:00", "CS", "Fight", "2"),         # 本会话真身
        _mk_ev("MAA_1", "2026-09-28 06:10:00", "CD", "Fight", "2"),         # 应配 05:40 的那条
        _mk_ev("MAA_1", "2026-09-28 07:00:00", "CS", "Infrast", "3"),       # 永不闭合
    ])
    prs, un = chain_pairs(e3)
    chk("配对 n", len(prs), 1)
    chk("配对秒", prs[0]["sec"], 1800.0)
    chk("配对取最近一条 Start", prs[0]["start"].strftime("%H:%M"), "05:40")
    chk("未闭合", un, {"Fight": 1, "Infrast": 1})

    # --- 5) gui 指标聚合
    gm = gui_metrics(e2 + parse_events([
        _mk_ev("MAA_2", "2026-09-28 06:00:43", "CD", "StartUp", "1"),
        _mk_ev("MAA_2", "2026-09-28 06:30:00", "S", "500"),
        _mk_ev("MAA_2", "2026-09-28 06:31:00", "S", "700"),
        _mk_ev("MAA_2", "2026-09-28 06:32:00", "O", "1500"),
    ]))
    chk("gui 启动样本 n", gm["startup"]["all"]["n"], 1)
    chk("gui 链 pairs", gm["chains"]["pairs"], 1)
    chk("gui 截图 n", gm["screenshot"]["single_all"]["n"], 2)
    chk("gui 过长 n", gm["screenshot"]["over_n"], 1)

    # --- 6) history 指标：按服/槽位分组 + 当日汇总
    recs = [
        {"acct": "α", "dirday": "2026-09-27", "t_first": _pdt("2026-09-27 05:00:00"),
         "t_last": _pdt("2026-09-27 05:30:00"), "result": "Success!"},
        {"acct": "α", "dirday": "2026-09-28", "t_first": _pdt("2026-09-28 05:00:00"),
         "t_last": _pdt("2026-09-28 06:00:00"), "result": "Success!"},
        {"acct": "α", "dirday": "2026-09-28", "t_first": _pdt("2026-09-28 07:00:00"),
         "t_last": _pdt("2026-09-28 07:15:00"), "result": "MAA 部分任务执行失败"},
        {"acct": "β", "dirday": "2026-09-28", "t_first": _pdt("2026-09-28 05:00:00"),
         "t_last": _pdt("2026-09-28 05:20:00"), "result": "Success!"},
        {"acct": "γ", "dirday": "2026-09-28", "t_first": _pdt("2026-09-28 05:00:00"),
         "t_last": _pdt("2026-09-28 05:00:00"), "result": "Success!"},        # 0 分钟丢弃
        {"acct": "δ", "dirday": "2026-09-28", "t_first": _pdt("2026-09-28 05:00:00"),
         "t_last": _pdt("2026-09-28 05:10:00"), "result": "Success!"},        # 名册外 → 服/槽位=未知
    ]
    roster = {"α": {"server": "Official", "slot": "MAA_6"}, "β": {"server": "Bilibili", "slot": "MAA_9"}}
    hm = history_metrics(recs, roster, {})
    chk("history 单轮 n", len(hm["runs"]), 5)
    chk("history α 当日(9/28) 总", round(hm["account_daily_minutes"]["per_account"]["α"]["day"]["max"], 1), 75.0)
    chk("history by_server 键", sorted(hm["account_daily_minutes"]["by_server"].keys()),
        ["Bilibili", "Official", "未知"])
    chk("history fail n", hm["account_run_minutes"]["fail"]["n"], 1)
    chk("history 未知服归组", hm["account_daily_minutes"]["by_server"]["未知"]["n"], 1)
    hm_day = history_metrics(recs, roster, {}, only_day="2026-09-28")
    chk("history only_day n", len(hm_day["runs"]), 4)

    # --- 7) 比较器：阈值触发与不触发
    base = {
        "schema": SCHEMA_BASE,
        "account_daily_minutes": {"all": stats([10, 12, 14]), "per_account": {}},
        "account_run_minutes": {"all": stats([8, 9, 10])},
        "maa_startup_to_first_chain_s": {"all": stats([20, 25, 30])},
        "screenshot_ms": {"single_all": stats([300 + i for i in range(25)], 0)},
        "chain_seconds": {"by_chain": {"StartUp": stats([120, 150, 180])}},
    }
    h2 = {"runs": [{"acct": "α", "dirday": "2026-09-28", "mins": 40.0, "ok": True}],
          "account_daily_minutes": {"per_account": {}}, "day_totals": {}}
    g2 = {"sessions": [{"dir": "MAA_6", "t_up": _pdt("2026-09-28 06:00:00"), "startup_s": 100.0}],
          "screenshot": {"single_all": stats([900, 950, 1000], 0),
                         "single_by_slot": {}, "warn": {"n": 0}, "over_n": 0},
          "chains": {"by_chain": {"StartUp": stats([600, 620])}, "unclosed": {}},
          "coverage": {}}
    fs = compare(base, h2, g2, _mult(argparse.Namespace(mult=None)))
    kinds = sorted(f["kind"] for f in fs)
    chk("触发四类", kinds, ["CHAIN_SLOW", "RUN_SLOW", "SCREENSHOT_SLOW", "STARTUP_SLOW"])
    h3 = {"runs": [{"acct": "α", "dirday": "2026-09-28", "mins": 9.0, "ok": True}],
          "account_daily_minutes": {"per_account": {}}, "day_totals": {}}
    g3 = {"sessions": [{"dir": "MAA_6", "t_up": _pdt("2026-09-28 06:00:00"), "startup_s": 25.0}],
          "screenshot": {"single_all": stats([100, 110, 120], 0),
                         "single_by_slot": {}, "warn": {"n": 0}, "over_n": 0},
          "chains": {"by_chain": {"StartUp": stats([100, 110])}, "unclosed": {}},
          "coverage": {}}
    chk("未触发", compare(base, h3, g3, _mult(argparse.Namespace(mult=None))), [])
    # 该号自己的基线优先：α 自身 run p90=31.8（n=3）⇒ 45min 不超阈（对全量 9.8 则会是 4.6×）
    base_own = dict(base)
    base_own["account_daily_minutes"] = {
        "all": stats([10, 12, 14]),
        "per_account": {"α": {"server": "Official", "slot": "MAA_6",
                              "day": stats([30, 40, 50]),
                              "run": stats([28, 30, 32])}},
    }
    h4 = {"runs": [{"acct": "α", "dirday": "2026-09-28", "mins": 45.0, "ok": True}],
          "account_daily_minutes": {"per_account": {}}, "day_totals": {}}
    chk("自身基线内不报", [f["kind"] for f in compare(base_own, h4, g3,
                                              _mult(argparse.Namespace(mult=None)))], [])
    h5 = {"runs": [{"acct": "α", "dirday": "2026-09-28", "mins": 60.0, "ok": True}],
          "account_daily_minutes": {"per_account": {}}, "day_totals": {}}
    fs5 = compare(base_own, h5, g3, _mult(argparse.Namespace(mult=None)))
    chk("自身基线外报（basis=自身）",
        [(f["kind"], f["basis"]) for f in fs5 if f["kind"] == "RUN_SLOW"], [("RUN_SLOW", "自身")])
    chk("样本不足不判", compare(
        base, {"runs": [], "account_daily_minutes": {"per_account": {}}, "day_totals": {}},
        {"sessions": [], "screenshot": {"single_all": stats([9, 9], 0), "single_by_slot": {},
                                        "warn": {"n": 0}, "over_n": 0},
         "chains": {"by_chain": {"StartUp": stats([999])}, "unclosed": {}}, "coverage": {}},
        _mult(argparse.Namespace(mult=None))), [])

    # --- 8) 基线装配：schema + 关键段
    b2 = build_baseline(dt.date(2026, 9, 28), 14, hm, gm)
    chk("baseline schema", b2["schema"], SCHEMA_BASE)
    for sec in ("account_daily_minutes", "account_run_minutes", "maa_startup_to_first_chain_s",
                "screenshot_ms", "chain_seconds", "thresholds", "coverage"):
        ok[1] += 1
        if sec in b2:
            ok[0] += 1
        else:
            print("FAIL baseline 缺段 %s" % sec)
    chk("summary 行前缀", render_baseline_summary(b2).startswith("PERF_SUMMARY="), True)

    print("selftest: %d/%d" % (ok[0], ok[1]))
    return 0 if ok[0] == ok[1] else 1


# ================================================================ main

def main():
    ap = argparse.ArgumentParser(description="性能基线 / 回归检测（只读）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="基线窗口天数（班次日，默认 14）")
    ap.add_argument("--date", help="窗口右端班次日 YYYY-MM-DD（缺省=今天，<05:00 取昨天）")
    ap.add_argument("--check", action="store_true", help="最近一轮 vs 基线，超阈即报（退出码 3）")
    ap.add_argument("--baseline", help="基线文件路径（缺省 runtime/reports/perf_baseline.json）")
    ap.add_argument("--mult", type=float, help="阈值倍数覆盖（默认 1.5，全部指标）")
    ap.add_argument("--json", action="store_true", help="打印 JSON（不写盘）")
    ap.add_argument("--summary", action="store_true", help="只打一行 PERF_SUMMARY=")
    ap.add_argument("--no-write", action="store_true", help="不写盘")
    ap.add_argument("--raw-out", help="另存原始采集 JSON（排障）")
    ap.add_argument("--timeout", type=int, default=420, help="单条 SSH 采集超时秒（默认 420）")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不连网）")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.check:
        return cmd_check(args)
    return cmd_build(args)


if __name__ == "__main__":
    sys.exit(main())
