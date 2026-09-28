#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号健康度日报（只读）—— 谁在浪费理智、谁总失败、谁在拖时长。

产出（本工具是唯一生成者）：
  runtime/reports/account_health_<date>.md    人读日报（逐号一行 + Top 浪费/Top 失败 + 优先处理建议）
  runtime/reports/account_health_<date>.json  机读版（schema maaorch.account_health/1；供 X6「每日刷取建议」联动）

数据源（全部只读；主机 = 100.79.173.69，经 SSH）：
  1. E:\\AUTO-MAS\\history\\<班次日>\\<账号>\\*.json
       -> `sanity`（该次运行结束时的理智余量；AUTO-MAS 抓的最后一条 `理智: x/y`）
          `sanity_full_at`（**绝对回满时间**，例：`理智将在 2026-09-29 02:03 回满。(20h 14m 后)`）
          `maa_result`（`Success!` / 各类失败文案 —— 账号级权威口径）
  2. 同目录同名 `.log`（= 该次尝试的 gui.log 逐账号切片，AUTO-MAS 按次落盘）
       -> 首行/末行时间戳 = 该轮「任务链起止」⇒ 单轮耗时；只读头 4KB + 尾 16KB，不做全文件扫描
  3. AUTO-MAS REST（只读）      -> 脚本→用户（账号名/启用态/服）、脚本→MAA 目录（实例槽位）
                                  ※ Info.Id / Info.Password 是登录凭据：本工具**只取 Name/Status/Server**，
                                    绝不落盘；报告里不出现手机号与任何凭据
  4. 能力台账（可选，存在即读） -> runtime/reports/stage_capability.json（账号×关卡可刷能力，计数摘要）
  5. 错误日报（可选，存在即读） -> runtime/reports/maa_errors_<date>.json（账号级定因；「等自愈 vs 补跑」）
  6. 资产名册（可选，存在即读） -> runtime/reports/mas_account_master_*.json（取最新一份，补 UID/实例）

指标（口径见报告 §8）：
  * **理智溢出**：`sanity_full_at` 已过（now > 回满时间）⇒ 已满 hours_full 小时；损失点 = 满后每 6 分钟 1 点
    ⇒ `lost_points = hours_full × 10`。「距上次运行超过 `--waste-hours`（默认 26h）且已回满」列入 Top 浪费。
    基线：实测理智上限约 205，0→满约 20.5h ⇒ 每天跑一次固有溢出 ≈3.5h，不告警。
  * **成功率**：近 7 / 30 班次日（history 目录名即班次日）`Success!` 占比；**失败类型分布**按 maa_result 家族归并。
  * **单轮耗时**：逐次 `log 末时间戳 − 首时间戳`，统计成功轮 p50/p95/max（>6h 的样本丢弃）。
  * **健康分**（0–100，可解释；逐项分值在 JSON `breakdown` / `score_why`）：
        45×率7d + 15×新鲜度 + 25×理智 + 5×耗时 + 10×连败
  * **优先处理建议**：补跑 / 定因后补跑 / 查环境 / 连败先定因 / 等自愈（资源类勿补跑）/ 停用 / 无记录。

与 X6「每日刷取建议」联动：X6 读 `account_health_<date>.json` 的 `accounts[].sanity.*`（该不该先刷它）、
`rate7 / fail_families / tail_fails / advice`（能不能刷、要不要先试探）。详见报告 §7。

用法：
  python tools/account_health.py                     # 全量：扫 30 班次日 → 写 md+json → stdout 摘要
  python tools/account_health.py --days 7            # 只扫最近 7 个班次日
  python tools/account_health.py --date 2026-09-27   # 以某班次日出报告（窗口右端）
  python tools/account_health.py --summary           # 只打一行 HEALTH_SUMMARY=…
  python tools/account_health.py --json              # 结构化 JSON（不写盘）
  python tools/account_health.py --account y9Flipped # 只看一个号
  python tools/account_health.py --no-write          # 不写盘
  python tools/account_health.py --raw-out <路径>    # 另存原始采集记录（排障）
  python tools/account_health.py --check             # 连通性自检（REST+SSH），退出码 0/2
  python tools/account_health.py --selftest          # 离线自检（不连网）

退出码：0=成功；2=数据源不可达；1=未预期错误。
环境变量：MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST（同其他 mas_*/maa_* 工具）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
REPORTS = os.path.join(RUNTIME, "reports")

sys.path.insert(0, os.path.join(ROOT, "tools"))

# 复用 maa_error_daily 里血泪定稿的 ssh 子进程环境自愈（大小写双键/ComSpec 缺失 → ssh 静默失败）
from maa_error_daily import (  # noqa: E402
    SSH_HOST, API, KEY, _child_env, _ssh_exe, _l, post,
    CAT_RESOURCE,
)

HIST_ROOT = r"E:\AUTO-MAS\history"
REMOTE_PS = "E:\\_hermes_account_health.ps1"

WINDOW_START_HOUR = 5          # 班次日窗口 [D 05:00, D+1 05:00)（同 maa_error_daily）
SCHEMA = "maaorch.account_health/1"

DEFAULT_DAYS = 30
WASTE_HOURS = 26               # 「距上次运行超过 X 小时且已回满」的 X（口径见模块 docstring）
SLOW_P50_MIN = 45              # p50 超过此值 ⇒ 慢（经验阈值）
MAX_RUN_MIN = 360              # 单轮 > 6h 视为异常样本（不参与耗时统计）

# 健康分权重/阈值（改这里 = 改口径，务必同步模块 docstring 与报告 §8）
W_RATE, W_FRESH, W_SANITY, W_SPEED, W_STREAK = 45, 15, 25, 5, 10
SANITY_OK_H, SANITY_BAD_H = 6, 30
FRESH_OK_H, FRESH_BAD_H = 30, 96
SPEED_OK_MIN, SPEED_BAD_MIN = 22, 60
STREAK_SCORE = {0: 10, 1: 7, 2: 4, 3: 2}   # ≥4 ⇒ 0

OK_RESULT = "Success!"

FAM_SIM = "模拟器启动失败"
FAM_LOGIN = "登录失败"
FAM_QUIT = "运行中退出"
FAM_ABORT = "运行中中止"
FAM_PARTIAL = "部分任务失败"
FAM_TIMEOUT = "进程超时"
FAM_HUMAN = "人为中止"
FAM_NOWATCH = "未开始监看"
FAM_EMPTY = "空/未知"
FAM_OTHER = "其他"

# maa_result 家族归并（顺序敏感：前缀类在前）
FAM_RULES = [
    (FAM_SIM, lambda s: s.startswith("模拟器启动失败")),
    (FAM_LOGIN, lambda s: s == "MAA 未能正确登录 PRTS"),
    (FAM_QUIT, lambda s: s == "MAA 在完成任务前退出"),
    (FAM_ABORT, lambda s: s == "MAA 在完成任务前中止"),
    (FAM_PARTIAL, lambda s: s == "MAA 部分任务执行失败"),
    (FAM_TIMEOUT, lambda s: s == "MAA 进程超时"),
    (FAM_HUMAN, lambda s: s == "任务被用户手动中止"),
    (FAM_NOWATCH, lambda s: s == "未开始监看日志"),
    (FAM_EMPTY, lambda s: s == ""),
]


def fam_of(result) -> str:
    s = str(result or "").strip()
    if s == OK_RESULT:
        return OK_RESULT
    for name, pred in FAM_RULES:
        if pred(s):
            return name
    return FAM_OTHER + ":" + s[:24]


# ---------------------------------------------------------------- 时间

def resolve_date(s):
    """缺省 = 今天；本地 <05:00 时取昨天（覆盖夜班尾巴），同 maa_error_daily。"""
    if s:
        return dt.date.fromisoformat(s)
    now = dt.datetime.now()
    if now.hour < WINDOW_START_HOUR:
        return (now - dt.timedelta(days=1)).date()
    return now.date()


def _pdt(s):
    try:
        return dt.datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return None


def _pdtm(s):
    try:
        return dt.datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return None


FULL_AT_RE = re.compile(r"理智将在\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*回满")


def parse_full_at(s):
    """'理智将在 2026-09-29 02:03 回满。(20h 14m 后)' -> datetime（分钟精度）；失败 None。"""
    m = FULL_AT_RE.search(str(s or ""))
    if not m:
        return None
    return _pdtm("%s %s" % (m.group(1), m.group(2)))


# ---------------------------------------------------------------- 远端采集

_PS_TMPL = r"""
$ErrorActionPreference = 'Continue'; $ProgressPreference = 'SilentlyContinue'
$hist = '@@HIST@@'
$d0 = '@@D0@@'; $d1 = '@@D1@@'
$recs = New-Object 'System.Collections.Generic.List[string]'
$nDays = 0; $nBad = 0
foreach ($d in (Get-ChildItem $hist -Directory)) {
  $dn = $d.Name
  if ($dn -lt $d0 -or $dn -gt $d1) { continue }
  $nDays++
  foreach ($a in (Get-ChildItem $d.FullName -Directory)) {
    foreach ($f in (Get-ChildItem $a.FullName -File -Filter *.json)) {
      $sanity = ''; $full = ''; $result = ''; $bad = ''
      try {
        $j = Get-Content -Raw -Encoding UTF8 -ErrorAction Stop $f.FullName | ConvertFrom-Json -ErrorAction Stop
        $sanity = [string]$j.sanity
        $full = [string]$j.sanity_full_at
        $result = [string]$j.maa_result
      } catch { $bad = 'BADJSON'; $nBad++ }
      $first = ''; $last = ''
      $lf = [System.IO.Path]::ChangeExtension($f.FullName, '.log')
      if (Test-Path $lf) {
        try {
          $len = (Get-Item $lf).Length
          if ($len -gt 0) {
            $fs = [IO.File]::Open($lf, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            try {
              $n1 = [int][Math]::Min(4096, $len)
              $buf = New-Object byte[] $n1
              $r1 = $fs.Read($buf, 0, $n1)
              $head = [Text.Encoding]::UTF8.GetString($buf, 0, $r1)
              $n2 = [int][Math]::Min(16384, $len)
              [void]$fs.Seek([Math]::Max(0, $len - $n2), [IO.SeekOrigin]::Begin)
              $buf2 = New-Object byte[] $n2
              $r2 = $fs.Read($buf2, 0, $n2)
              $tail = [Text.Encoding]::UTF8.GetString($buf2, 0, $r2)
            } finally { $fs.Close() }
            $m1 = [regex]::Match($head, '(?m)^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
            if ($m1.Success) { $first = $m1.Value.Substring(1) }
            $ms = [regex]::Matches($tail, '(?m)^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
            if ($ms.Count -gt 0) { $last = $ms[$ms.Count - 1].Groups[1].Value }
          }
        } catch { }
      }
      $recs.Add(($a.Name + '|' + $dn + '|' + $f.Name + '|' + $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') +
                 '|' + $result + '|' + $sanity + '|' + $full + '|' + $first + '|' + $last + '|' + $bad))
    }
  }
}
$json = ConvertTo-Json -Compress -Depth 4 ([ordered]@{ d0 = $d0; d1 = $d1; days = $nDays; bad = $nBad; n = $recs.Count; recs = $recs })
$ms2 = New-Object IO.MemoryStream
$gz = New-Object IO.Compression.GZipStream($ms2, [IO.Compression.CompressionMode]::Compress)
$bytes = [Text.Encoding]::UTF8.GetBytes($json)
$gz.Write($bytes, 0, $bytes.Length); $gz.Close()
Write-Output ('__GZ__' + [Convert]::ToBase64String($ms2.ToArray()))
"""


def ssh_ps(script, timeout=600):
    """远端执行 PowerShell（只读约定）。传输形态与 maa_error_daily.ssh_ps 同款（upload + -File）。

    与 maa_error_daily 重复这段而不 import 的原因：远端探针文件名不同、两边各自独立演进；
    真正需要共享的是 _ssh_exe/_child_env 这类「环境自愈」定稿，那里已 import。
    返回值大（几千条记录）⇒ 远端 gzip+base64 单行回传，避免长输出被截断（stage_capability 的前车之鉴）。
    """
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    payload = b"\xef\xbb\xbf" + (prefix + script).encode("utf-8")
    b64 = base64.b64encode(payload).decode("ascii")
    upload = ("$b64='%s'\n$b=[Convert]::FromBase64String($b64)\n[IO.File]::WriteAllBytes('%s', $b)\n"
              "Write-Output ('written ' + $b.Length)\n" % (b64, REMOTE_PS))
    up = [_ssh_exe(), "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
          SSH_HOST, "chcp 65001 >nul & powershell -NoProfile -Command -"]
    r = subprocess.run(up, input=upload.encode("utf-8"), capture_output=True,
                       timeout=min(timeout, 180), env=_child_env())
    if b"written" not in r.stdout:
        raise RuntimeError("SSH 上传未确认: " + (r.stdout + r.stderr).decode("utf-8", "replace")[:300])
    run = [_ssh_exe(), "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -ExecutionPolicy Bypass -File " + REMOTE_PS]
    r = subprocess.run(run, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, env=_child_env())
    return r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def collect_remote(d, days=DEFAULT_DAYS, timeout=600):
    d0 = d - dt.timedelta(days=days - 1)
    ps = (_PS_TMPL.replace("@@HIST@@", HIST_ROOT)
          .replace("@@D0@@", d0.isoformat()).replace("@@D1@@", d.isoformat()))
    out, err = ssh_ps(ps, timeout=timeout)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("__GZ__"):
            return json.loads(gzip.decompress(base64.b64decode(line[len("__GZ__"):])).decode("utf-8"))
    raise RuntimeError("SSH 采集失败（无 __GZ__ 标记）: " + (out or "(空)")[-200:] +
                       " ｜ stderr: " + (err or "(空)")[-400:])


def parse_records(raw):
    """远端行 'acct|dirday|file|mt|result|sanity|full_at|t_first|t_last|bad' -> (记录列表, 坏行数)。"""
    recs, bad = [], 0
    for ln in _l(raw.get("recs")):
        parts = str(ln).split("|")
        if len(parts) != 10:
            bad += 1
            continue
        acct, dirday, fname, mt, result, sanity, full, t_first, t_last, jbad = parts
        recs.append({
            "acct": acct, "dirday": dirday, "file": fname, "mt": mt or None,
            "result": result, "bad": jbad == "BADJSON",
            "sanity": int(sanity) if sanity.strip().lstrip("-").isdigit() else None,
            "full_at": parse_full_at(full), "full_raw": full,
            "t_first": _pdt(t_first), "t_last": _pdt(t_last),
        })
    return recs, bad


# ---------------------------------------------------------------- REST 名册（只取名/启用态/服）

def rest_roster():
    """-> ({账号: {script, slot, enabled, server}}, {脚本名: 槽位})；REST 不可达则空。

    只读 `Info.Name / Info.Status / Info.Server` 与脚本 `Info.Path` 的目录名（实例槽位）。
    **绝不读取或落盘 Info.Id（手机号）与 Info.Password（凭据）。**
    """
    roster, script_slots = {}, {}
    try:
        scripts = (post("/api/scripts/get", {"scriptId": None}).get("data") or {})
    except Exception:  # noqa: BLE001
        return roster, script_slots
    for sid, s in scripts.items():
        info = (s or {}).get("Info") or {}
        sname = str(info.get("Name") or "")
        path = str(info.get("Path") or "").replace("\\\\", "\\").rstrip("\\/")
        slot = os.path.basename(path) if path else "-"
        if sname:
            script_slots[sname] = slot or "-"
        try:
            users = (post("/api/scripts/user/get", {"scriptId": sid}).get("data") or {})
        except Exception:  # noqa: BLE001
            users = {}
        for _uuid, u in users.items():
            ui = (u or {}).get("Info") or {}
            name = ui.get("Name")
            if not name:
                continue
            entry = {"script": sname, "slot": slot or "-",
                     "enabled": bool(ui.get("Status", True)), "server": str(ui.get("Server") or "")}
            prev = roster.get(str(name))
            if prev is None or str(prev.get("script") or "").startswith("l-"):
                roster[str(name)] = entry      # 同名多脚本时优先保留非肉鸽线脚本
    return roster, script_slots


def load_master_roster():
    """最新 mas_account_master_*.json -> {账号: {uid, idx, maadir, server}}；缺则空。"""
    out = {}
    try:
        fs = sorted(f for f in os.listdir(REPORTS)
                    if f.startswith("mas_account_master_") and f.endswith(".json"))
        if not fs:
            return out
        j = json.load(open(os.path.join(REPORTS, fs[-1]), encoding="utf-8"))
        for a in _l(j.get("accounts")):
            nm = str((a or {}).get("game_name_cfg") or "")
            if nm:
                out[nm] = {"uid": str(a.get("uid") or ""), "idx": a.get("idx"),
                           "maadir": str(a.get("maadir") or ""), "server": str(a.get("server") or "")}
    except Exception:  # noqa: BLE001
        return {}
    return out


def load_capability():
    """能力台账 stage_capability.json -> ({账号: 状态计数}, meta)；缺则空。"""
    out, meta = {}, {}
    p = os.path.join(REPORTS, "stage_capability.json")
    if not os.path.exists(p):
        return out, meta
    try:
        j = json.load(open(p, encoding="utf-8"))
        meta = {"built_at": j.get("built_at"), "window": j.get("window")}
        for row in _l(j.get("rows")):
            nm = str((row or {}).get("account") or "")
            if not nm:
                continue
            e = out.setdefault(nm, {"success": 0, "failed": 0, "unknown": 0})
            st = str(row.get("status") or "")
            if st in e:
                e[st] += 1
    except Exception:  # noqa: BLE001
        return {}, {}
    return out, meta


def load_err_daily(d):
    """错误日报 maa_errors_<date>.json -> ({账号: Counter(归类)}, 说明)；缺则空。

    注意：**所有 sev 都收**（不只 fatal）——「资源类/公招缺券」在日报里是 benign，
    但正是「等自愈、勿补跑」的关键判据（v 级误判会让补跑白跑一轮）。
    """
    agg, note = {}, ""
    p = os.path.join(REPORTS, "maa_errors_%s.json" % d.isoformat())
    if not os.path.exists(p):
        return agg, "未接入（可先跑 tools/maa_error_daily.py --date %s 补定因）" % d.isoformat()
    try:
        j = json.load(open(p, encoding="utf-8"))
        for it in _l(j.get("items")):
            acct = str((it or {}).get("account") or "")
            if acct:
                agg.setdefault(acct, Counter())[str(it.get("cat"))] += 1
    except Exception:  # noqa: BLE001
        return {}, "读取失败（忽略）"
    return agg, "已接入（%s 的条目按账号/归类聚合，含 benign）" % d.isoformat()


# ---------------------------------------------------------------- 聚合与评分

def _rec_key(rec):
    """记录排序键：班次日 + 结束时间（t_last 缺省回退 mtime）——统一成 str 避免混合类型比较。"""
    t = rec.get("t_last")
    return (rec.get("dirday") or "", t.strftime("%Y-%m-%d %H:%M:%S") if t else (rec.get("mt") or ""))


def _norm_script(fname):
    """'明日方舟-官-3-搓玉-20-43-12.json' -> '明日方舟-官-3-搓玉'。"""
    return re.sub(r"-\d{2}-\d{2}-\d{2}$", "", str(fname).rsplit(".", 1)[0])


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def _median(vals):
    return round(statistics.median(vals), 1) if vals else None


def _p95(vals):
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))], 1)


def _lin(x, x_ok, x_bad, w):
    """x≤x_ok ⇒ w；x≥x_bad ⇒ 0；中间线性。"""
    if x <= x_ok:
        return float(w)
    if x >= x_bad:
        return 0.0
    return w * (x_bad - x) / (x_bad - x_ok)


def score_account(r, now):
    """账号行 -> (健康分 0..100, breakdown dict, why 列表)。规则见模块 docstring。"""
    b, why = {}, []

    rate7 = (r["ok7"] / r["att7"]) if r["att7"] else 0.0
    s_rate = W_RATE * rate7
    b["rate"] = round(s_rate, 1)
    why.append(("率7d %.0f%%" % (rate7 * 100) if r["att7"] else "7d 无运行") + "→%.1f/%d" % (s_rate, W_RATE))

    h = r.get("hours_since_last_run")
    if h is None:
        s_fresh = 0.0
        why.append("无运行记录→0/%d" % W_FRESH)
    else:
        s_fresh = _lin(h, FRESH_OK_H, FRESH_BAD_H, W_FRESH)
        why.append("新鲜 %.0fh→%.1f/%d" % (h, s_fresh, W_FRESH))
    b["fresh"] = round(s_fresh, 1)

    hf = r.get("hours_full")
    if hf is None:
        s_san = W_SANITY * 0.5
        why.append("无理智证据→%.1f/%d" % (s_san, W_SANITY))
    else:
        s_san = _lin(hf, SANITY_OK_H, SANITY_BAD_H, W_SANITY)
        why.append("满 %.1fh→%.1f/%d" % (hf, s_san, W_SANITY))
    b["sanity"] = round(s_san, 1)

    p50 = (r.get("duration") or {}).get("p50_ok")
    if p50 is None:
        s_sp = W_SPEED * 0.6
        why.append("无耗时数据→%.1f/%d" % (s_sp, W_SPEED))
    else:
        s_sp = _lin(p50, SPEED_OK_MIN, SPEED_BAD_MIN, W_SPEED)
        why.append("p50 %.0fm→%.1f/%d" % (p50, s_sp, W_SPEED))
    b["speed"] = round(s_sp, 1)

    k = int(r.get("tail_fails") or 0)
    s_st = STREAK_SCORE.get(k, 0) if r["att7"] else 0        # 7d 一场没跑 ⇒ 不给「没连败」分
    b["streak"] = float(s_st)
    why.append("连败 %d→%d/%d" % (k, s_st, W_STREAK))

    total = s_rate + s_fresh + s_san + s_sp + s_st
    return int(round(max(0.0, min(100.0, total)))), b, why


def advice_of(r, err_cats, waste_h, line="日常"):
    """账号行 -> 建议标签列表（报告与 X6 直接消费）。line=日常/肉鸽/停用/离册 决定建议域。"""
    out = []
    if line == "停用":
        return ["停用：不产车（存档，无需动作）"]
    if line == "离册":
        return ["不在名册（历史残留）：脚本已删/已移出 ⇒ 不产车、无需动作；若确应产车请补进脚本"]
    if line == "肉鸽":
        return ["肉鸽线：另线口径（按需发车，理智不消耗，不计溢出）"]

    fam = r.get("fail_families") or {}
    top_fam = max(fam.items(), key=lambda kv: kv[1])[0] if fam else "-"
    rate7 = (r["ok7"] / r["att7"]) if r["att7"] else None

    if r["att30"] == 0:
        out.append("无运行记录：确认漏排车 / 已停用（近 30 班次日零尝试）")
        return out

    if err_cats.get(CAT_RESOURCE):
        out.append("等自愈：今日错误日报归类为资源类（公招缺券等），勿补跑")
        return out

    hf = r.get("hours_full")
    if hf is not None and hf >= waste_h:
        if rate7 is not None and rate7 >= 0.5:
            out.append("补跑：理智已满 %.0fh（约 %s 点待用；率 %.0f%%，主错 %s）"
                       % (hf, r.get("lost_points"), rate7 * 100, top_fam))
        else:
            out.append("定因后补跑：理智已满 %.0fh 但率仅 %.0f%%（主错 %s）"
                       % (hf, (rate7 or 0) * 100, top_fam))
    if fam and top_fam in (FAM_SIM, FAM_LOGIN, FAM_NOWATCH) and rate7 is not None and rate7 < 0.7:
        share = 100.0 * fam.get(top_fam, 0) / max(1, sum(fam.values()))
        if share >= 40 and (r["att7"] - r["ok7"]) >= 3:
            out.append("查环境：近 30d 失败中「%s」占 %.0f%%（实例/模拟器/登录链路）" % (top_fam, share))
    if r.get("tail_fails", 0) >= 3:
        out.append("连败 %d 次：先定因（看 maa_errors 日报）再发车" % r["tail_fails"])
    if rate7 is not None and rate7 < 0.6 and r["att7"] >= 5 and not out:
        out.append("观察：近 7 班成功率仅 %.0f%%（主错 %s）" % (rate7 * 100, top_fam))
    if not out:
        out.append("正常")
    return out


def flags_of(r, waste_h):
    """标记列表。非「日常」线的号只给线标记（溢出/缺跑/慢 对它们没有口径意义）。"""
    f = []
    line = r.get("line", "日常")
    if line == "停用":
        return ["停用"]
    if line == "离册":
        return ["不在名册"]
    if line == "肉鸽":
        return ["肉鸽线"]
    if r["att30"] == 0:
        f.append("无记录")
    if (r.get("hours_since_last_run") or 0) >= 48:
        f.append("缺跑≥48h")
    if (r.get("hours_full") or 0) >= waste_h:
        f.append("理智溢出")
    if r.get("state") == "full_weak":
        f.append("疑似溢出(弱证据)")
    if (r.get("tail_fails") or 0) >= 3:
        f.append("连败%d" % r["tail_fails"])
    if r.get("ev_lag_ok"):
        f.append("证据滞后")
    if (r["duration"]["p50_ok"] or 0) >= SLOW_P50_MIN:
        f.append("慢")
    if r.get("multi_account"):
        f.append("多号共用")
    if not f:
        f.append("正常")
    return f


def build(d, recs, roster, script_slots, master, cap, err_agg, days, waste_h, now=None):
    now = (now or dt.datetime.now()).replace(microsecond=0)
    w7 = d - dt.timedelta(days=6)
    w30 = d - dt.timedelta(days=days - 1)

    per = {}

    def row(acct):
        r = per.get(acct)
        if r is None:
            r = per[acct] = {
                "account": acct, "att7": 0, "ok7": 0, "att30": 0, "ok30": 0,
                "fail_families": Counter(), "dur_ok": [], "dur_fail": [],
                "_last_end": None, "_last_ok_end": None,
                "_ev": None, "_san": None, "_ends": [], "_tail": 0,
                "script": None, "slot": None, "enabled": None, "server": None, "script_infer": None,
            }
        return r

    for rec in recs:
        r = row(rec["acct"])
        end = rec["t_last"] or _pdt(rec["mt"])
        ok = rec["result"].strip() == OK_RESULT
        dd = rec["dirday"]
        if w30.isoformat() <= dd <= d.isoformat():
            r["att30"] += 1
            if ok:
                r["ok30"] += 1
            else:
                r["fail_families"][fam_of(rec["result"])] += 1
        if w7.isoformat() <= dd <= d.isoformat():
            r["att7"] += 1
            if ok:
                r["ok7"] += 1
        if rec["t_first"] and rec["t_last"]:
            mins = (rec["t_last"] - rec["t_first"]).total_seconds() / 60.0
            if 0 < mins <= MAX_RUN_MIN:
                (r["dur_ok"] if ok else r["dur_fail"]).append(mins)
        if end:
            r["_ends"].append((end, ok))
            if r["_last_end"] is None or end > r["_last_end"]:
                r["_last_end"] = end
            if ok and (r["_last_ok_end"] is None or end > r["_last_ok_end"]):
                r["_last_ok_end"] = end
            if rec["full_at"] is not None and (r["_ev"] is None or end >= r["_ev"][0]):
                r["_ev"] = (end, rec["full_at"], rec["file"], rec["full_raw"])
            if rec["sanity"] is not None and (r["_san"] is None or end >= r["_san"][0]):
                r["_san"] = (end, rec["sanity"], rec["file"])
        if not r.get("script_infer"):
            r["script_infer"] = _norm_script(rec["file"])

    # 名册并入（零运行账号也要出现）
    for acct, info in roster.items():
        r = row(acct)
        r["script"] = info.get("script")
        r["slot"] = info.get("slot")
        r["enabled"] = info.get("enabled")
        r["server"] = info.get("server")
        r["in_roster"] = True
    roster_ok = len(roster) > 5      # REST 降级（空名册）时不做「停用/离册」判定

    # tail_fails：近 7 班次日、时间序尾部的连续失败数
    tail = {}
    for rec in recs:
        if w7.isoformat() <= rec["dirday"] <= d.isoformat():
            tail.setdefault(rec["acct"], []).append(rec)
    for acct, lst in tail.items():
        lst.sort(key=_rec_key)
        k = 0
        for rec in reversed(lst):
            if rec["result"].strip() == OK_RESULT:
                break
            k += 1
        row(acct)["_tail"] = k

    script_users = Counter(str(r["script"]) for r in per.values() if r.get("script"))

    rows = []
    for acct, r in sorted(per.items()):
        ev, san, ends = r.pop("_ev"), r.pop("_san"), r.pop("_ends")
        r["tail_fails"] = r.pop("_tail")
        r["last_end"] = r.pop("_last_end")
        r["last_ok_end"] = r.pop("_last_ok_end")
        if not r.get("script"):
            r["script"] = r.get("script_infer") or "-"
            r["slot"] = script_slots.get(r["script"], "-")
        if not r.get("slot"):
            r["slot"] = "-"
        m = master.get(acct) or {}
        r["uid"] = m.get("uid") or ""
        r["idx"] = m.get("idx")
        if not r.get("server") and m.get("server"):
            r["server"] = m["server"]

        # 线别：停用 > 离册 > 肉鸽 > 日常（决定建议域与榜单口径）
        if not roster_ok:
            r["line"] = "日常"
        elif r.get("enabled") is False:
            r["line"] = "停用"
        elif not r.get("in_roster"):
            r["line"] = "离册"
        elif str(r.get("script") or "").startswith("l-"):
            r["line"] = "肉鸽"
        else:
            r["line"] = "日常"
        r.pop("in_roster", None)

        r["sanity_full_at"] = ev[1].strftime("%Y-%m-%d %H:%M") if ev else None
        r["full_src"] = ev[2] if ev else None
        r["sanity_last"] = san[1] if san else None
        r["ev_lag"] = r["ev_lag_ok"] = None
        if ev:
            later = [ok for (e, ok) in ends if e > ev[0]]
            r["ev_lag"] = len(later)
            r["ev_lag_ok"] = sum(1 for ok in later if ok)
        if ev is None:
            r["state"] = "full_weak" if r["sanity_last"] is not None else "unknown"
            hours_full, lost = None, None
        elif ev[1] > now:
            r["state"] = "ok"                 # 有回满时间但还没到 ⇒ 未满（hours_full=0 是「真值」，不是缺数据）
            hours_full, lost = 0.0, 0
        else:
            r["state"] = "full"
            hours_full = round((now - ev[1]).total_seconds() / 3600.0, 1)
            lost = int(round(hours_full * 10))       # 每 6 分钟 1 点 ⇒ 10 点/小时
        r["hours_full"] = hours_full
        r["lost_points"] = lost
        r["hours_since_last_run"] = (round((now - r["last_end"]).total_seconds() / 3600.0, 1)
                                     if r["last_end"] else None)
        r["rate7"] = _pct(r["ok7"], r["att7"])
        r["rate30"] = _pct(r["ok30"], r["att30"])
        r["duration"] = {"p50_ok": _median(r["dur_ok"]), "p95_ok": _p95(r["dur_ok"]),
                         "max_ok": round(max(r["dur_ok"]), 1) if r["dur_ok"] else None,
                         "n_ok": len(r["dur_ok"]), "n_all": len(r["dur_ok"]) + len(r["dur_fail"])}
        r.pop("dur_ok", None)
        r.pop("dur_fail", None)
        r["multi_account"] = bool(r.get("script")) and script_users[str(r["script"])] > 1
        r["capability"] = dict(cap.get(acct) or {}) or None
        erc = err_agg.get(acct) or Counter()
        r["err_cats_today"] = dict(erc)
        sc, br, why = score_account(r, now)
        r["score"] = sc
        r["breakdown"] = br
        r["score_why"] = why
        r["advice"] = advice_of(r, erc, waste_h, r["line"])
        r["fail_families"] = dict(r["fail_families"].most_common())
        r["flags"] = flags_of(r, waste_h)
        r["last_end"] = r["last_end"].strftime("%Y-%m-%d %H:%M") if r["last_end"] else None
        r["last_ok_end"] = r["last_ok_end"].strftime("%Y-%m-%d %H:%M") if r["last_ok_end"] else None
        rows.append(r)

    # 排序：日常线按健康分升序（最该看的在最上），其后依次 肉鸽 / 停用 / 离册
    LINE_ORD = {"日常": 0, "肉鸽": 1, "停用": 2, "离册": 3}
    rows.sort(key=lambda r: (LINE_ORD.get(r["line"], 9), r["score"] if r["line"] == "日常" else 0,
                             -(r["hours_full"] or 0), r["account"]))

    daily = [r for r in rows if r["line"] == "日常"]
    strict_waste = [r for r in daily if (r.get("hours_full") or 0) >= waste_h and not (r.get("ev_lag_ok") or 0)]
    weak_waste = [r for r in daily if (r.get("hours_full") or 0) >= waste_h and (r.get("ev_lag_ok") or 0)]
    strict_waste.sort(key=lambda r: -(r["lost_points"] or 0))
    weak_waste.sort(key=lambda r: -(r["lost_points"] or 0))
    fails7 = sorted([r for r in daily if r["att7"] > r["ok7"]],
                    key=lambda r: (-(r["att7"] - r["ok7"]), r["score"]))
    slow = sorted([r for r in daily if (r["duration"]["p50_ok"] or 0) >= SLOW_P50_MIN],
                  key=lambda r: -(r["duration"]["p50_ok"] or 0))
    silent_daily = [r for r in daily if r["att30"] == 0]
    silent_other = [r for r in rows if r["att30"] == 0 and r["line"] != "日常"]
    stale = sorted([r for r in daily if r["att30"] > 0 and (r["hours_since_last_run"] or 0) >= 48],
                   key=lambda r: -(r["hours_since_last_run"] or 0))

    tot_att = sum(r["att30"] for r in daily)
    tot_ok = sum(r["ok30"] for r in daily)
    cat_counter = Counter()
    for r in daily:
        for k, v in (r["fail_families"] or {}).items():
            cat_counter[k] += v

    data = {
        "schema": SCHEMA,
        "date": d.isoformat(),
        "window": {"day": d.isoformat(), "days": days, "d0": w30.isoformat(), "d1": d.isoformat()},
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": {"waste_hours": waste_h, "slow_p50_min": SLOW_P50_MIN,
                       "weights": {"rate": W_RATE, "fresh": W_FRESH, "sanity": W_SANITY,
                                   "speed": W_SPEED, "streak": W_STREAK},
                       "fresh_h": [FRESH_OK_H, FRESH_BAD_H], "sanity_h": [SANITY_OK_H, SANITY_BAD_H],
                       "speed_min": [SPEED_OK_MIN, SPEED_BAD_MIN]},
        "source": {"host": SSH_HOST, "history": HIST_ROOT + r"\<班次日>\<账号>\*.json",
                   "rest": API, "capability": bool(cap), "err_daily": bool(err_agg),
                   "master_roster": bool(master), "roster_ok": roster_ok},
        "totals": {"accounts": len(rows), "daily_accounts": len(daily),
                   "by_line": dict(Counter(r["line"] for r in rows)),
                   "attempts30": tot_att, "ok30": tot_ok, "rate30": _pct(tot_ok, tot_att),
                   "waste_accounts": len(strict_waste), "waste_accounts_weak": len(weak_waste),
                   "waste_points_total": sum(r["lost_points"] or 0 for r in strict_waste),
                   "fails30_by_family": dict(cat_counter.most_common())},
        "accounts": rows,
        "top_waste": [r["account"] for r in strict_waste[:15]],
        "top_waste_weak": [r["account"] for r in weak_waste[:10]],
        "top_fail": [r["account"] for r in fails7[:15]],
        "top_slow": [r["account"] for r in slow[:10]],
        "silent": [r["account"] for r in silent_daily],
        "silent_other": [r["account"] for r in silent_other],
        "stale": [r["account"] for r in stale],
        "interfaces": {
            "daily_advice_X6": {
                "file": "runtime/reports/account_health_<date>.json",
                "fields": {
                    "sanity": "accounts[].state / .hours_full / .lost_points / .sanity_full_at / .sanity_last",
                    "fitness": "accounts[].rate7 / .fail_families / .tail_fails / .flags / .advice",
                    "roster": "accounts[].account / .script / .slot / .server / .enabled",
                    "advice": "含「等自愈」⇒ 不要排车；含「定因后补跑」⇒ 先 mas_probe_farm 试探",
                },
                "order": "按 lost_points 降序 = 今天优先刷（继续拖着就是继续浪费理智）",
            }
        },
    }
    return data


# ---------------------------------------------------------------- 报告渲染

def health_summary_line(data):
    t = data["totals"]
    tw = data["top_waste"][0] if data["top_waste"] else "-"
    wa = next((r for r in data["accounts"] if r["account"] == tw), None) if tw != "-" else None
    tf = data["top_fail"][0] if data["top_fail"] else "-"
    return ("HEALTH_SUMMARY=号 %d（日常 %d），率30d %.1f%%，浪费 %d 个号/%d 点；Top浪费=%s%s；Top失败=%s"
            % (t["accounts"], t.get("daily_accounts", t["accounts"]), t["rate30"],
               t["waste_accounts"], t["waste_points_total"], tw,
               ("(%sh)" % wa["hours_full"]) if wa else "", tf))


def _fmt_h(v, dash="-"):
    return dash if v is None else ("%.1f" % v)


def render_md(data):
    L = []
    t, th = data["totals"], data["thresholds"]
    L.append("# 账号健康度日报 — %s" % data["date"])
    L.append("")
    L.append("窗口 %s ~ %s（%d 班次日）｜生成 %s（快照）｜主机 %s"
             % (data["window"]["d0"], data["window"]["d1"], data["window"]["days"],
                data["generated_at"], data["source"]["host"]))
    L.append("")
    L.append("**%s**" % health_summary_line(data))
    L.append("")
    bl = t.get("by_line", {})
    L.append("- 扫描 **%d 号**（日常 %d ｜ 肉鸽 %d ｜ 停用 %d ｜ 离册 %d）；**日常线** 30 班尝试 **%d**"
             "（成功 %d，率 %.1f%%）"
             % (t["accounts"], t.get("daily_accounts", 0), bl.get("肉鸽", 0), bl.get("停用", 0),
                bl.get("离册", 0), t["attempts30"], t["ok30"], t["rate30"]))
    L.append("- 理智溢出（日常线，≥%sh 且已回满）**%d 个号**，合计约 **%d 点**（每 6 分钟 1 点）"
             % (th["waste_hours"], t["waste_accounts"], t["waste_points_total"]))
    if t["waste_accounts_weak"]:
        L.append("- 弱证据（后续有成功轮、回满时间可能偏早）另 %d 个号：%s"
                 % (t["waste_accounts_weak"], "、".join(data["top_waste_weak"][:8])))
    if t["fails30_by_family"]:
        L.append("- 失败家族（30 班）：" + " ｜ ".join("%s×%d" % kv for kv in
                                                      list(t["fails30_by_family"].items())[:8]))
    for k, ok in (("capability", "能力台账"), ("err_daily", "错误日报"), ("master_roster", "资产名册")):
        if not data["source"].get(k):
            L.append("- 注：%s 未接入（可选数据源，缺省降级）" % ok)
    L.append("")

    L.append("## 1. 主表（逐号一行；日常线按健康分升序，其后 肉鸽 / 停用 / 离册）")
    L.append("")
    L.append("> 率7d/率30d = 近 7/30 班次日 `Success!` 占比；已满 = 自理智回满起的小时数（>=%sh 即溢出）；"
             "损失点 = 已满小时×10；p50 = 成功轮耗时中位数（分钟）；分 = 健康分（0–100，规则见 §8）。" % th["waste_hours"])
    L.append("")
    L.append("| 分 | 账号 | 线 | 脚本 | 实例 | 服 | 7d 成/败 | 率7d | 率30d | 末次运行(止) | 已满 | 损失点 | p50 | 标记 |")
    L.append("|---:|---|---|---|---|---|---:|---:|---:|---|---:|---:|---:|---|")
    for r in data["accounts"]:
        hf = r["hours_full"]
        daily = r["line"] == "日常"
        full_cell = ("未满" if r["state"] == "ok" else
                     (_fmt_h(hf, "—") if hf is not None else
                      ("疑似" if r["state"] == "full_weak" else "—")))
        L.append("| %d | %s | %s | %s | %s | %s | %d/%d | %.0f%% | %.0f%% | %s | %s | %s | %s | %s |"
                 % (r["score"], r["account"], r["line"], r["script"], r["slot"], r["server"] or "-",
                    r["ok7"], r["att7"] - r["ok7"], r["rate7"], r["rate30"],
                    (r["last_end"] or "—")[5:],
                    full_cell if daily else "—",
                    (r["lost_points"] if r["lost_points"] is not None else "—") if daily else "—",
                    _fmt_h(r["duration"]["p50_ok"], "—"),
                    "、".join(r["flags"])))
    L.append("")
    L.append("> 非日常线（肉鸽/停用/离册）的「已满/损失点」列显示 `—`：对它们没有「浪费」口径（见 §2 注）。")
    L.append("")

    L.append("## 2. Top 浪费理智（日常线，已回满 ≥%sh 且还在拖）" % th["waste_hours"])
    L.append("")
    L.append("| 账号 | 脚本 | 已满 | 损失点 | 回满时间 | 末次运行 | 率7d | 建议 |")
    L.append("|---|---|---:|---:|---|---|---:|---|")
    if not data["top_waste"]:
        L.append("| - | - | （无：没有号在拖理智） | | | | | |")
    for nm in data["top_waste"]:
        r = next(x for x in data["accounts"] if x["account"] == nm)
        L.append("| %s | %s | %.1fh | %d | %s | %s | %.0f%% | %s |"
                 % (r["account"], r["script"], r["hours_full"], r["lost_points"], r["sanity_full_at"],
                    (r["last_end"] or "—")[5:], r["rate7"], "；".join(r["advice"])))
    if data["top_waste_weak"]:
        L.append("")
        L.append("弱证据补充（回满时间之后还有成功轮 ⇒ 实际溢出可能更少）：%s" % "、".join(data["top_waste_weak"]))
    L.append("")
    L.append("> 口径：只统计**日常线**且**未停用**的号；肉鸽线不消耗理智、停用号不产车、离册号已不在脚本里，"
             "三者的「满仓」不是浪费，故不计入（见 §1 主表各自标记）。")
    L.append("")

    L.append("## 3. Top 失败（日常线，近 7 班次日按失败次数）")
    L.append("")
    L.append("| 账号 | 脚本 | 7d 成/败 | 率7d | 率30d | 主错 | 失败分布(30d) | 末次成功 | 建议 |")
    L.append("|---|---|---:|---:|---:|---|---|---|---|")
    if not data["top_fail"]:
        L.append("| - | - | （无失败） | | | | | | |")
    for nm in data["top_fail"]:
        r = next(x for x in data["accounts"] if x["account"] == nm)
        fam = r["fail_families"] or {}
        top = next(iter(fam), "-")
        dist = "、".join("%s×%d" % kv for kv in list(fam.items())[:4]) or "-"
        L.append("| %s | %s | %d/%d | %.0f%% | %.0f%% | %s | %s | %s | %s |"
                 % (r["account"], r["script"], r["ok7"], r["att7"] - r["ok7"], r["rate7"], r["rate30"],
                    top, dist, (r["last_ok_end"] or "—")[5:], "；".join(r["advice"])))
    L.append("")

    L.append("## 4. 单轮耗时异常（日常线，成功轮 p50 ≥ %d 分钟）" % th["slow_p50_min"])
    L.append("")
    if not data["top_slow"]:
        L.append("- （无）")
    else:
        L.append("| 账号 | 脚本 | p50 | p95 | max | 样本 | 末次运行 |")
        L.append("|---|---|---:|---:|---:|---:|---|")
        for nm in data["top_slow"]:
            r = next(x for x in data["accounts"] if x["account"] == nm)
            d = r["duration"]
            L.append("| %s | %s | %.0fm | %s | %s | %d | %s |"
                     % (r["account"], r["script"], d["p50_ok"], _fmt_h(d["p95_ok"], "—"),
                        _fmt_h(d["max_ok"], "—"), d["n_ok"], (r["last_end"] or "—")[5:]))
    L.append("")

    L.append("## 5. 静默 / 缺跑 / 非日常线")
    L.append("")
    sil = [r for r in data["accounts"] if r["att30"] == 0 and r["line"] == "日常"]
    stale = [r for r in data["accounts"] if r["line"] == "日常" and r["att30"] > 0
             and (r["hours_since_last_run"] or 0) >= 48]
    other = [r for r in data["accounts"] if r["line"] != "日常"]
    L.append("- **日常线 30 班次日零尝试（%d）**：%s" % (len(sil), "、".join(r["account"] for r in sil) or "无"))
    L.append("- **日常线有记录但 ≥48h 未跑（%d）**：%s" % (len(stale), "、".join(
        "%s(%.0fh)" % (r["account"], r["hours_since_last_run"]) for r in stale) or "无"))
    L.append("- 非日常线（%d，不产车/另线口径，无需动作）：%s" % (len(other), "、".join(
        "%s[%s]" % (r["account"], r["line"]) for r in other) or "无"))
    nocap = [r for r in data["accounts"] if r["line"] == "日常" and not r.get("capability")]
    cap_meta = (data.get("source") or {}).get("capability_meta") or {}
    L.append("- **无能力台账（%d，刷取计划生成器排不了关 ⇒ 需先学关）**：%s"
             % (len(nocap), "、".join(r["account"] for r in nocap) or "无"))
    if cap_meta.get("built_at"):
        w = cap_meta.get("window") or {}
        wtxt = ("%s~%s" % (w.get("start"), w.get("end"))) if w.get("start") else "-"
        L.append("- 能力台账 `stage_capability.json` 构建于 %s（窗口 %s）" % (cap_meta.get("built_at"), wtxt))
    L.append("")

    L.append("## 6. 优先处理建议")
    L.append("")
    groups = {
        "① 立即补跑（理智白满着，能跑）": [],
        "② 定因后补跑（满着但一直在失败）": [],
        "③ 查环境（模拟器/登录类失败占比高）": [],
        "④ 连败先定因": [],
        "⑤ 等自愈（资源类，勿补跑）": [],
        "⑥ 人工核实（日常线零记录 / 缺跑）": [],
    }
    for r in data["accounts"]:
        if r["line"] != "日常":
            continue
        adv = "；".join(r["advice"])
        if r["att30"] == 0:
            groups["⑥ 人工核实（日常线零记录 / 缺跑）"].append(r["account"])
        elif "等自愈" in adv:
            groups["⑤ 等自愈（资源类，勿补跑）"].append(r["account"])
        elif "补跑" in adv and "定因后" not in adv:
            groups["① 立即补跑（理智白满着，能跑）"].append(r["account"])
        elif "定因后补跑" in adv:
            groups["② 定因后补跑（满着但一直在失败）"].append(r["account"])
        elif "查环境" in adv:
            groups["③ 查环境（模拟器/登录类失败占比高）"].append(r["account"])
        elif "连败" in adv:
            groups["④ 连败先定因"].append(r["account"])
        elif (r["hours_since_last_run"] or 0) >= 48:
            groups["⑥ 人工核实（日常线零记录 / 缺跑）"].append("%s(%.0fh未跑)" % (r["account"],
                                                                        r["hours_since_last_run"]))
    for k, v in groups.items():
        L.append("- **%s**（%d）：%s" % (k, len(v), "、".join(v) if v else "无"))
    L.append("")

    L.append("## 7. 与「每日刷取建议（X6）」的机读接口")
    L.append("")
    L.append("- 文件：`runtime/reports/account_health_%s.json`（schema `%s`）" % (data["date"], SCHEMA))
    L.append("- X6 取用：`accounts[].sanity.{state,hours_full,lost_points}`（该不该先刷它）、")
    L.append("  `accounts[].{rate7,fail_families,tail_fails,flags,advice}`（能不能刷、要不要先试探）、")
    L.append("  `accounts[].{account,script,slot,server,enabled,line}`（名册与实例；**只看 `line==\"日常\"`**）。")
    L.append("- 排序口径：`lost_points` 降序 = 今天优先刷；`advice` 含「等自愈」⇒ 不要排车；")
    L.append("  含「定因后补跑」⇒ 先 `tools/mas_probe_farm.py` 试探一轮再决定；`state==\"ok\"` ⇒ 未满（不用抢时间）。")
    L.append("- 现成名单：JSON 顶层 `top_waste`（今天该优先刷）、`top_fail`（能不能刷）、`silent`/`stale`（没在跑的日常线）。")
    L.append("")

    L.append("## 8. 口径与复现")
    L.append("")
    L.append("- 数据源：`E:\\AUTO-MAS\\history\\<班次日>\\<账号>\\*.json|.log`（只读 SSH）+ REST 名册（只取名/启用态/服）。"
             "班次日 = 目录名；窗口 = 最近 %d 个班次日。" % data["window"]["days"])
    L.append("- 线别（`line`）：**日常**=在册且启用的日常刷取号（健康分/榜单/建议都只对它们生效）；"
             "**肉鸽**=脚本名 `l-*`（理智不消耗、按需发车）；**停用**=REST `Status=false`（不产车）；"
             "**离册**=历史里有记录但已不在任何脚本（脚本已删/移出）。")
    L.append("- `sanity_full_at` 是 AUTO-MAS 落盘的**绝对回满时间**（分钟精度）；`state=ok` ⇒ 还没满（`hours_full=0` 是真值）；"
             "`lost_points = 已满小时 × 10`（1 点/6 分钟）。")
    L.append("- 上限按号不同（实测约 205 ⇒ 0→满 20.5h）⇒ 每天跑一次固有溢出约 3.5h，阈值 X=%sh 即为此留出余量；"
             "证据之后还有成功轮的号降级为弱证据（不进 Top，只备注）。" % th["waste_hours"])
    L.append("- 单轮耗时 = 该次 `.log` 末行时间戳 − 首行时间戳（含唤醒/全部任务链）；>6h 的样本丢弃。")
    L.append("- 健康分 = %d×率7d + %d×新鲜度 + %d×理智 + %d×耗时 + %d×连败；分项分值见 JSON `breakdown`、"
             "白话见 `score_why`。" % (th["weights"]["rate"], th["weights"]["fresh"], th["weights"]["sanity"],
                                       th["weights"]["speed"], th["weights"]["streak"]))
    L.append("- 失败家族按 `maa_result` 文案归并；**深定因**（哪条链、资源类/环境类）看 `runtime/reports/maa_errors_%s.*`。"
             % data["date"])
    L.append("- 复跑：`python tools/account_health.py --date %s`；机读：`runtime/reports/account_health_%s.json`。"
             % (data["date"], data["date"]))
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 子命令

def cmd_check():
    problems = []
    n_scripts = 0
    try:
        r = post("/api/scripts/get", {"scriptId": None})
        if r.get("code") != 200:
            problems.append("REST code=%s" % r.get("code"))
        n_scripts = len(r.get("data") or {})
    except Exception as e:  # noqa: BLE001
        problems.append("REST 不可达: %s" % e)
    hist_ok = False
    try:
        out, _err = ssh_ps("$h='%s'; Write-Output ('H=' + (hostname)); "
                           "Write-Output ('HA=' + (Test-Path $h)); "
                           "Write-Output ('D=' + @(Get-ChildItem $h -Directory).Count)" % HIST_ROOT,
                           timeout=90)
        hist_ok = "HA=True" in out
        if not hist_ok:
            problems.append("SSH 无输出 / history 不可见: " + out.strip()[:120])
    except Exception as e:  # noqa: BLE001
        problems.append("SSH 失败: %s" % e)
    print("account_health --check: %s" % ("FAIL" if problems else "OK"))
    print("  REST scripts=%d ｜ SSH history=%s" % (n_scripts, "ok" if hist_ok else "fail"))
    for p in problems:
        print("  - " + p)
    return 2 if problems else 0


def _mk_rec(acct, dirday, result, t_end, full_raw="", sanity=None, dur_min=None, file="x-00-00-00.json"):
    t_last = _pdt(t_end)
    t_first = (t_last - dt.timedelta(minutes=dur_min)) if (t_last and dur_min) else None
    return {"acct": acct, "dirday": dirday, "file": file, "mt": t_end,
            "result": result, "bad": False, "sanity": sanity,
            "full_at": parse_full_at(full_raw), "full_raw": full_raw,
            "t_first": t_first, "t_last": t_last}


def _selftest():
    ok = True
    fails = []

    def chk(name, got, exp):
        nonlocal ok
        if got != exp:
            ok = False
            fails.append("%s: got=%r exp=%r" % (name, got, exp))

    # 1) full_at 解析
    chk("full_at ok", parse_full_at("理智将在 2026-09-29 02:03 回满。(20h 14m 后)"),
        dt.datetime(2026, 9, 29, 2, 3))
    chk("full_at empty", parse_full_at(""), None)
    chk("full_at junk", parse_full_at("理智: 5/205"), None)

    # 2) 家族归并
    chk("fam ok", fam_of("Success!"), "Success!")
    chk("fam sim", fam_of("模拟器启动失败, MAA 未实际运行"), FAM_SIM)
    chk("fam login", fam_of("MAA 未能正确登录 PRTS"), FAM_LOGIN)
    chk("fam quit", fam_of("MAA 在完成任务前退出"), FAM_QUIT)
    chk("fam empty", fam_of(""), FAM_EMPTY)
    chk("fam other", fam_of("奇怪的结果"), FAM_OTHER + ":奇怪的结果")

    # 3) 线性与评分（用固定 now）
    now = dt.datetime(2026, 9, 28, 12, 0)
    row = {"att7": 10, "ok7": 5, "att30": 30, "ok30": 20, "hours_since_last_run": 10.0,
           "hours_full": 2.0, "tail_fails": 0, "duration": {"p50_ok": 20.0}, "fail_families": {}}
    sc, br, _why = score_account(row, now)
    chk("score normal", sc, int(round(45 * 0.5 + 15 + 25 + 5 + 10)))

    row2 = dict(row, att7=0, ok7=0, hours_since_last_run=None, hours_full=None,
                duration={"p50_ok": None})
    sc2, _b2, _w2 = score_account(row2, now)
    chk("score silent", sc2, int(round(0 + 0 + 12.5 + 3 + 0)))
    row3 = dict(row, hours_full=40.0, tail_fails=5, hours_since_last_run=80.0,
                duration={"p50_ok": 90.0})
    sc3, _b3, _w3 = score_account(row3, now)
    chk("score bad", sc3, int(round(22.5 + 15 * (96 - 80) / 66 + 0 + 0 + 0)))

    # 4) build() 端到端（合成记录，不连网；now 注入 ⇒ 结果可复现）
    d = dt.date(2026, 9, 28)
    now = dt.datetime(2026, 9, 28, 12, 0)
    past30 = (now - dt.timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")   # 30h 前跑过
    past20 = (now - dt.timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S")
    recs = [
        # A：正常号（未满：回满时间在将来）
        _mk_rec("A", "2026-09-28", "Success!", past30, "理智将在 2026-09-29 06:00 回满。(x)", 12, 40),
        # B：溢出号（回满时间已过 30h）
        _mk_rec("B", "2026-09-28", "Success!", past30, "理智将在 2026-09-27 06:00 回满。(x)", 0, 40),
        # C：连败 + 模拟器失败为主
        _mk_rec("C", "2026-09-28", "模拟器启动失败, MAA 未实际运行", past30, "", None, None),
        _mk_rec("C", "2026-09-27", "模拟器启动失败, MAA 未实际运行", past20, "", None, None),
        _mk_rec("C", "2026-09-26", "模拟器启动失败, MAA 未实际运行", past20, "", None, None),
        # D：弱证据溢出（回满时间那条证据之后，还有一次成功轮）
        _mk_rec("D", "2026-09-28", "Success!", past30, "理智将在 2026-09-26 06:00 回满。(x)", 0, 40),
        _mk_rec("D", "2026-09-27", "Success!", past20, "", 5, 40),
        # G：离册号（有历史记录、满仓很久，但已不在名册）
        _mk_rec("G", "2026-09-10", "Success!", "2026-09-10 06:00:00",
                "理智将在 2026-09-10 20:00 回满。(x)", 0, 40),
    ]
    roster = {"A": {"script": "s1", "slot": "MAA_1", "enabled": True, "server": "Official"},
              "B": {"script": "s1", "slot": "MAA_1", "enabled": True, "server": "Official"},
              "C": {"script": "s2", "slot": "MAA_2", "enabled": True, "server": "Official"},
              "D": {"script": "s3", "slot": "MAA_3", "enabled": True, "server": "Official"},
              "E": {"script": "s4", "slot": "MAA_4", "enabled": False, "server": "Official"},
              "F": {"script": "s5", "slot": "MAA_5", "enabled": True, "server": "Official"}}
    data = build(d, recs, roster, {"s1": "MAA_1", "s2": "MAA_2", "s3": "MAA_3", "s4": "MAA_4"},
                 {}, {}, {}, 30, 26.0, now=now)
    by = {r["account"]: r for r in data["accounts"]}
    chk("build n", len(data["accounts"]), 7)
    chk("A state", by["A"]["state"], "ok")
    chk("A no waste", by["A"]["lost_points"], 0)
    chk("A sanity score", by["A"]["breakdown"]["sanity"], 25.0)
    chk("B state", by["B"]["state"], "full")
    chk("B hours≈30", abs(by["B"]["hours_full"] - 30.0) < 0.2, True)
    chk("B lost≈300", abs(by["B"]["lost_points"] - 300) < 3, True)
    chk("B in top_waste", "B" in data["top_waste"], True)
    chk("C fail fam", next(iter(by["C"]["fail_families"])), FAM_SIM)
    chk("C tail", by["C"]["tail_fails"], 3)
    chk("C in top_fail", "C" in data["top_fail"], True)
    chk("D weak", by["D"]["ev_lag_ok"], 1)
    chk("D not strict", "D" not in data["top_waste"], True)
    chk("D in weak", "D" in data["top_waste_weak"], True)
    chk("E att30", by["E"]["att30"], 0)
    chk("E line", by["E"]["line"], "停用")
    chk("E advice", by["E"]["advice"][0].startswith("停用"), True)
    chk("E not in silent", "E" not in data["silent"], True)
    chk("F line", by["F"]["line"], "日常")
    chk("F in silent", "F" in data["silent"], True)
    chk("G line 离册", by["G"]["line"], "离册")
    chk("G not in waste", "G" not in data["top_waste"], True)
    chk("by_line", data["totals"]["by_line"].get("离册"), 1)
    chk("dur B", by["B"]["duration"]["p50_ok"], 40.0)
    md = render_md(data)
    chk("md has table", "## 1. 主表" in md, True)
    chk("md has x6", "每日刷取建议（X6）" in md, True)
    chk("summary line", health_summary_line(data).startswith("HEALTH_SUMMARY="), True)
    chk("multi_account A", by["A"]["multi_account"], True)

    print("account_health --selftest: %s%s" % ("OK" if ok else "FAIL",
                                               "" if ok else "（%d 例）" % len(fails)))
    for f in fails:
        print("  - " + f)
    return 0 if ok else 1


# ---------------------------------------------------------------- 入口

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="账号健康度日报（只读）")
    ap.add_argument("--date")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--waste-hours", type=float, default=WASTE_HOURS)
    ap.add_argument("--account")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--raw-out")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    if a.check:
        return cmd_check()
    if a.selftest:
        return _selftest()

    d = resolve_date(a.date)
    try:
        raw = collect_remote(d, days=a.days, timeout=a.timeout)
    except Exception as e:  # noqa: BLE001
        print("HEALTH_SUMMARY=UNKNOWN:%s" % str(e).replace("\n", " ")[:160])
        return 2
    recs, nbad = parse_records(raw)
    roster, script_slots = rest_roster()
    master = load_master_roster()
    cap, cap_meta = load_capability()
    err_agg, err_note = load_err_daily(d)
    data = build(d, recs, roster, script_slots, master, cap, err_agg, a.days, a.waste_hours)
    data["source"]["scanned"] = {"days": raw.get("days"), "records": raw.get("n"),
                                 "bad_json": raw.get("bad"), "bad_lines": nbad}
    data["source"]["capability_meta"] = cap_meta
    data["source"]["err_daily_note"] = err_note

    if a.raw_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.raw_out)), exist_ok=True)
        with open(a.raw_out, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)

    if a.account:
        keep = [r for r in data["accounts"] if r["account"] == a.account]
        if not keep:      # 退化为唯一子串匹配（"hbznn119" -> hbznn119-官/日）
            keep = [r for r in data["accounts"] if a.account in r["account"]]
        if not keep:
            print("HEALTH_SUMMARY=ACCOUNT_NOT_FOUND:%s" % a.account)
            return 1
        data["accounts"] = keep
        names = {r["account"] for r in keep}
        for k in ("top_waste", "top_waste_weak", "top_fail", "top_slow", "silent", "stale"):
            data[k] = [nm for nm in (data.get(k) or []) if nm in names]
        t = data["totals"]      # 单号视图：总量重算（否则数字是全场口径，会误导）
        t["accounts"] = len(keep)
        t["daily_accounts"] = sum(1 for r in keep if r["line"] == "日常")
        t["attempts30"] = sum(r["att30"] for r in keep)
        t["ok30"] = sum(r["ok30"] for r in keep)
        t["rate30"] = round(100.0 * t["ok30"] / t["attempts30"], 1) if t["attempts30"] else 0.0
        t["waste_accounts"] = sum(1 for r in keep if r["line"] == "日常"
                                  and (r["hours_full"] or 0) >= a.waste_hours)
        t["waste_points_total"] = sum((r["lost_points"] or 0) for r in keep if r["line"] == "日常")
        t["waste_accounts_weak"] = sum(1 for r in keep if r["line"] == "日常" and r["ev_lag_ok"]
                                       and (r["hours_full"] or 0) >= a.waste_hours)
        t["by_line"] = {}

    line = health_summary_line(data)
    md = render_md(data)
    outs = []
    if not a.no_write and (not a.json or a.out):
        os.makedirs(REPORTS, exist_ok=True)
        mp = a.out or os.path.join(REPORTS, "account_health_%s.md" % d.isoformat())
        jp = os.path.splitext(mp)[0] + ".json"
        with open(mp, "w", encoding="utf-8") as f:
            f.write(md)
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        outs = [mp, jp]

    if a.summary:
        print(line)
        return 0
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    t = data["totals"]
    bl = t.get("by_line", {})
    print("[account_health] %s 窗口 %s ~ %s（%d 班次日）" % (d.isoformat(), data["window"]["d0"],
                                                         data["window"]["d1"], a.days))
    print("  采集：%s 班次日 / %d 条记录（坏 json %s / 坏行 %d）｜REST 名册 %d 号（%s）"
          % (raw.get("days"), raw.get("n"), raw.get("bad"), nbad, len(roster),
             "ok" if roster else "降级"))
    print("  账号 %d（日常 %d ｜ 肉鸽 %d ｜ 停用 %d ｜ 离册 %d）：日常线 30 班尝试 %d（成功 %d，率 %.1f%%）"
          "｜理智溢出 %d 号 / %d 点（弱证据 %d 号）"
          % (t["accounts"], t.get("daily_accounts", 0), bl.get("肉鸽", 0), bl.get("停用", 0),
             bl.get("离册", 0), t["attempts30"], t["ok30"], t["rate30"],
             t["waste_accounts"], t["waste_points_total"], t["waste_accounts_weak"]))
    if data["top_waste"]:
        print("  Top 浪费：" + "、".join(
            "%s %.1fh/%d点" % (nm, next(x for x in data["accounts"] if x["account"] == nm)["hours_full"],
                               next(x for x in data["accounts"] if x["account"] == nm)["lost_points"])
            for nm in data["top_waste"][:6]))
    if data["top_fail"]:
        print("  Top 失败：" + "、".join(
            "%s %d/%d" % (nm, next(x for x in data["accounts"] if x["account"] == nm)["att7"] -
                          next(x for x in data["accounts"] if x["account"] == nm)["ok7"],
                          next(x for x in data["accounts"] if x["account"] == nm)["att7"])
            for nm in data["top_fail"][:6]))
    for p in outs:
        print("写入: %s" % p)
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
