#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号 × 关卡 可刷能力台账 —— Hermes 以后不猜"这个号能不能刷这关"，先查台账。

写盘契约（**与其余五个工具的「默认 dry-run」相反，别穿错鞋**）：
  本工具**默认真写盘**——`--build` / `--update` / `--rebuild-local`（以及不带 `--no-write` 的
  `--replay`）会覆盖下面三个产物文件；干跑一律加 `--no-write`（只算不写、不建锁）。数据源全只读。
  写盘一律 tmp+os.replace 原子替换（读者永远看不到半截 JSON）；`_finish` 的读-改-写与
  `--rebuild-local` 的「读 state→写报表」共用一把 `runtime/stage_capability.lock` 单飞锁
  （并发写不互相覆盖；锁被占先等，超时才明确让路 rc=2）。写盘后补一条 `mas_audit` 统一审计
  （谁重建、扫了几次、state 行数）。

产出（本工具是唯一生成者）：
  runtime/reports/stage_capability.json   机读台账（schema maaorch.stage_capability/1）
  runtime/reports/STAGE_CAPABILITY.md     人读表 + 每号「未知」清单
  runtime/state/stage_capability_state.json  逐次尝试事件（增量更新的中间态）
deps: tools/maa_error_daily.py（复用其 _ssh_exe/_child_env：血泪定稿的 ssh 子进程环境自愈）
      tools/mas_audit.py（写盘后补一条统一审计；缺了也不影响主流程）

数据源（全部只读；主机 = 100.79.173.69，经 SSH）：
  E:\\AUTO-MAS\\history\\<date>\\<账号>\\*.json   -> maa_result / drop_statistics（按关分组的掉落）
  E:\\AUTO-MAS\\history\\<date>\\<账号>\\*.log    -> 该次尝试的 gui.log 副本（逐账号归属，实测含
                                                    GetFightStage/开始行动/掉落统计/任务出错/剿灭模式）
为什么不用 E:\\MAA-Multi\\MAA_*\\debug\\gui.log：一个 MAA 目录被多个账号轮流使用，
日志里没有稳定的账号归属，跨账号会串味；history 的 .log 是"逐账号逐次"切片，天然对齐。
（asst.log 有 TaskChainError 细节，但按 64MiB 轮转且同样无归属；细粒度定因交给
 mas_error_triage.py / maa_error_daily.py，本工具只做「账号×关卡」的结论口径。）

判据（保守；见 --selftest 的离线用例）：
  success  该关卡**出现在成功记录里**：`drop_statistics` 键命中（AUTO-MAS 按关落的掉落，主证据），
           或该次尝试副本日志里的 `<关> 掉落统计:` 块（交叉验证/补漏），或剿灭链
           `完成任务: 剿灭作战`（剿灭不掉落，故不进 drop_statistics）。
           * maa_result 成功但该关没有上述任一记录 ⇒ 不是 success（记为 unknown）。
  failed   关卡级失败证据（capability_impact=true）：
             stage_not_open    该关当日不可选（GetFightStage: selected null + 关卡计划内没有当日开放的关卡）
             stage_unavailable 关卡被选中但 Fight 链直接报错、整次没开打（无「开始行动」）
             proxy_mismatch    `代理指挥失误`（代理记录与实战不符）
  unknown  其余：从没刷过 / 选过但无结论 / 只见到「掉线·重连·连接失败」类环境失败（capability_impact=false）
           记录在 failures 里但**不**把 status 拉成 failed（实测 1066/1088 条 Fight 链报错是掉线/环境）。

用法（**默认写盘；干跑加 --no-write**）：
  python tools/stage_capability.py --build [--days 30] [--no-write]   # 全量重建（扫主机最近 N 天）
  python tools/stage_capability.py --update [--update-days 3] [--no-write]   # 增量（只扫最近 N 天并入 state）
  python tools/stage_capability.py --account <号> [--json]            # 查台账（不联网）
  python tools/stage_capability.py --json [--account <号>]            # 打印机读 JSON
  python tools/stage_capability.py --rebuild-local [--no-write]       # 从 state 重算报告（不联网）
  python tools/stage_capability.py --replay <raw_scan.json> [--no-write]  # 用已抓取的原始扫描重建（离线复现）
  python tools/stage_capability.py --selftest                         # 离线分类器自检（不联网）
  python tools/stage_capability.py --check                            # SSH 连通性自检

import 接口：
  from stage_capability import can_farm, can_farm_detail
  can_farm("hbznn119-日", "1-7")  ->  "success" | "failed" | "unknown"
  注意：账号名必须与 AUTO-MAS 里的一致（= history\\<date>\\<账号> 的目录名）；台账里查不到的
  账号/关卡一律返回 "unknown"（交给「先试探后开刷」流程，不猜）。

退出码：0=成功；2=数据源不可达/无台账/写盘锁等待超时（明确让路）；1=未预期错误。
环境变量：MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST（同其他 mas_* 工具）；
         MAAORCH_STAGECAP_LOCK_STALE_S（写盘锁陈旧阈值秒，默认 300，低于 60 按 60 算）/
         MAAORCH_STAGECAP_LOCK_WAIT_S（锁忙等待上限秒，默认 360，0=不等待立即让路）。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
REPORTS = os.path.join(RUNTIME, "reports")
STATE_DIR = os.path.join(RUNTIME, "state")
STATE_PATH = os.path.join(STATE_DIR, "stage_capability_state.json")
JSON_PATH = os.path.join(REPORTS, "stage_capability.json")
MD_PATH = os.path.join(REPORTS, "STAGE_CAPABILITY.md")
LOCK_PATH = os.path.join(RUNTIME, "stage_capability.lock")   # 写盘单飞锁（_finish 与 --rebuild-local 共用）


def _env_int(name, default):
    try:
        raw = str(os.environ.get(name, "")).strip()
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


# 锁参数（协议与 mas_dispatch._dispatch_lock 同款：O_EXCL + 陈旧抢占 + 释放清理；
# 阈值走环境变量，默认 300s 档；等待上限默认比陈旧阈值多 60s——活锁也最多只多等这么久）
LOCK_STALE_S = max(60, _env_int("MAAORCH_STAGECAP_LOCK_STALE_S", 300))
LOCK_WAIT_S = max(0, _env_int("MAAORCH_STAGECAP_LOCK_WAIT_S", LOCK_STALE_S + 60))

SCHEMA_STATE = "maaorch.stage_capability_state/1"
SCHEMA_REPORT = "maaorch.stage_capability/1"
HIST_ROOT = r"E:\AUTO-MAS\history"
REMOTE_PROBE = r"E:\_hermes_stagecap_probe.ps1"   # 两跳传输的远端落点（同 mas_* 家族约定；非配置目录）
DEFAULT_DAYS = 30
DEFAULT_UPDATE_DAYS = 3
EV_CAP = 40          # 每次尝试最多保留的事件行数（防爆量）
LINE_CAP = 200       # 单行截断长度

# 状态与失败类型
ST_SUCCESS, ST_FAILED, ST_UNKNOWN = "success", "failed", "unknown"
F_NOTOPEN = "stage_not_open"
F_UNAVAIL = "stage_unavailable"
F_PROXY = "proxy_mismatch"
F_NET = "fight_error_net"
F_NOREWARD = "no_reward_annihilation"
CAP_FAIL = {F_NOTOPEN, F_UNAVAIL, F_PROXY}
FAIL_ZH = {F_NOTOPEN: "关卡当日不可选（未开放）", F_UNAVAIL: "关卡被选中但无法开打",
           F_PROXY: "代理指挥失误", F_NET: "掉线/重连类环境失败（不影响能力判定）",
           F_NOREWARD: "无奖励关卡（剿灭周上限，信息性）"}
# ---------------------------------------------------------------- SSH（复用定稿管道）

def _med():
    """延迟 import tools/maa_error_daily.py（同目录），拿它定稿的 ssh 环境/可执行文件。"""
    if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import maa_error_daily  # noqa: PLC0415
    return maa_error_daily


def ssh_ps(script, timeout=1500):
    """远端执行 PowerShell（只读约定），返回 (stdout, stderr)。

    传输形态与 tools/maa_error_daily.py::ssh_ps 同款（两跳：b64 单行上传 + -File 执行），
    原因见那边的血泪史（EncodedCommand 撞 8191 上限 / stdin 脚本死锁 / PS 5.1 stdin 静默空输出）。
    仅远端落点不同（本工具的 __REMOTE_PROBE），避免与巡检 cron 抢同一个文件。
    """
    import base64
    import subprocess
    med = _med()
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    payload = b"\xef\xbb\xbf" + (prefix + script).encode("utf-8")
    b64 = base64.b64encode(payload).decode("ascii")
    upload = ("$b64='%s'\n"
              "$b=[Convert]::FromBase64String($b64)\n"
              "[IO.File]::WriteAllBytes('%s', $b)\n"
              "Write-Output ('written ' + $b.Length)\n" % (b64, REMOTE_PROBE))
    up = [med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
          med.SSH_HOST, "chcp 65001 >nul & powershell -NoProfile -Command -"]
    r = subprocess.run(up, input=upload.encode("utf-8"), capture_output=True,
                       timeout=min(timeout, 180), env=med._child_env())
    if b"written" not in r.stdout:
        raise RuntimeError("SSH 上传未确认: " + (r.stdout + r.stderr).decode("utf-8", "replace")[:300])
    run = [med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           med.SSH_HOST, "powershell -NoProfile -ExecutionPolicy Bypass -File " + REMOTE_PROBE]
    r = subprocess.run(run, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                       env=med._child_env())
    return r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

# ---------------------------------------------------------------- 远端扫描（PS 5.1，一天一次扫描）

PS_SCAN = r"""
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$histRoot = '@@HIST@@'
$nd = @@NDAYS@@
$end = '@@END@@'
$pat = '@@PAT@@'
$t0 = Get-Date
$allDirs = @(Get-ChildItem $histRoot -Directory | Sort-Object Name -Descending)
if ($end -ne '') { $allDirs = @($allDirs | Where-Object { $_.Name -le $end }) }
$days = @($allDirs | Select-Object -First $nd)
$attempts = @()
foreach ($dir in $days) {
  $dd = $dir.Name
  $d = $dir.FullName
  # A) 逐账号 JSON -> 归属到「尝试」（同名的 .log 是这次尝试的 gui.log 副本）
  $jrecs = @()
  foreach ($ad in (Get-ChildItem $d -Directory)) {
    foreach ($jf in (Get-ChildItem $ad.FullName -Filter *.json -File)) {
      $rec = [ordered]@{ day=$dd; acct=[string]$ad.Name; file=[string]$jf.Name
        mt=$jf.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'); result=''; drops=@(); lines=@(); bad=$false }
      try {
        $j = (Get-Content -Raw -Encoding UTF8 $jf.FullName | ConvertFrom-Json -ErrorAction Stop)
        $rec.result = [string]$j.maa_result
        if ($null -ne $j.drop_statistics) { $rec.drops = @($j.drop_statistics.PSObject.Properties | ForEach-Object { [string]$_.Name }) }
      } catch { $rec.bad = $true }
      $jrecs += $rec
    }
  }
  # B) 一天一次日志扫描（快），按文件分组，保序
  $lmap = @{}
  $ms = @(Select-String -Path (Join-Path $d '*\*.log') -Pattern $pat -Encoding UTF8 -ErrorAction SilentlyContinue)
  foreach ($x in $ms) {
    $p = [string]$x.Path
    if (-not $lmap.ContainsKey($p)) { $lmap[$p] = New-Object System.Collections.ArrayList }
    [void]$lmap[$p].Add([string]$x.Line)
  }
  # C) 合并：去掉行首时间戳/级别/模块前缀，只留判据文本
  foreach ($rec in $jrecs) {
    $lf = [System.IO.Path]::ChangeExtension((Join-Path (Join-Path $d $rec.acct) $rec.file), '.log')
    if ($lmap.ContainsKey($lf)) {
      $out = New-Object System.Collections.ArrayList
      foreach ($ln in $lmap[$lf]) {
        $s = $ln -replace '^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]\[\w+\]\[[^\]]*\]\s*', ''
        $s = $s -replace '^<\d+>\s*', ''
        $s = $s.Trim()
        if ($s.Length -gt @@LINECAP@@) { $s = $s.Substring(0, @@LINECAP@@) }
        if ($out.Count -lt @@EVCAP@@) { [void]$out.Add($s) }
      }
      $rec.lines = $out
    }
    $attempts += $rec
  }
}
Write-Output ('__JSON__' + (ConvertTo-Json -Compress -Depth 6 ([ordered]@{
  meta = [ordered]@{ n = $attempts.Count; secs = [int]((Get-Date) - $t0).TotalSeconds
    days = @($days | ForEach-Object { $_.Name }); hist = $histRoot }
  attempts = $attempts })))
"""

# 判据行预筛（一条正则，避免多次全量扫描）：命中任意一项即留行，具体归类在 Python 侧做
PS_PATTERN = ("|".join([
    r"GetFightStage",
    r"Task Chain: Fight",
    r"掉落统计",
    r"任务出错[:：]",
    r"开始行动",
    r"剿灭",
    r"正在运行中",
    r"任务已全部完成",
    r"任务已完成，但出现错误",
    r"理智作战: ",
    r"代理指挥失误",
    r"无奖励关卡",
    r"重连",
    r"游戏掉线",
    r"检查连接设置",
    r"已停止",
]))


def scan(days=DEFAULT_DAYS, end="", timeout=1500):
    """扫主机最近 `days` 个 history 日目录，返回原始扫描 payload（dict）。"""
    ps = (PS_SCAN.replace("@@HIST@@", HIST_ROOT)
                 .replace("@@NDAYS@@", str(int(days)))
                 .replace("@@END@@", end)
                 .replace("@@PAT@@", PS_PATTERN)
                 .replace("@@LINECAP@@", str(LINE_CAP))
                 .replace("@@EVCAP@@", str(EV_CAP)))
    out, err = ssh_ps(ps, timeout=timeout)
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("__JSON__"):
            return json.loads(line[len("__JSON__"):]), ""
    return None, "SSH 采集失败（无 __JSON__ 标记）: " + (out or "(空)")[-300:] + " | stderr: " + (err or "(空)")[-300:]


def load_raw(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ---------------------------------------------------------------- 判据：逐行事件 -> 逐次尝试结论

RE_SEL = re.compile(r"^GetFightStage: from \[(?P<lst>[^\]]*)\], selected (?P<sel>.*)$")
RE_DROPSTAT = re.compile(r"^(?P<st>\S+)\s+掉落统计\s*:?\s*$")
RE_CHAINERR = re.compile(r"^任务出错[:：]\s*(?P<chain>\S+)\s*$")
RE_CHAINSTART = re.compile(r"Task Chain: (?P<chain>\w+)")
RE_SKIP = re.compile(r"^理智作战: (?P<task>.+?)\s*任务跳过$")
RE_ANNIH = re.compile(r"剿灭模式\s*[:：]\s*(?P<a>\d+)\s*/\s*(?P<b>\d+)")
RE_STAGE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z\-+]*$")   # 关卡码白名单形态（1-7 / SR-7 / CE-6 / Annihilation）
ANNIH_STAGE = "Annihilation"
# 剿灭地图名（会以「<地图> 掉落统计:」出现，不是关卡码）
MAP_NAMES = {"炎国", "炎國", "拉特兰", "拉特蘭", "乌萨斯", "烏薩斯", "龙门", "龍門"}
NET_MARKS = ("游戏掉线", "重连失败", "连接断开", "检查连接设置", "模拟器断开", "截图失败")
NET_OK_MARKS = ("重连成功",)


def is_stage_code(s):
    """是否为可信的关卡码（不是 <2> 这类残留、不是剿灭地图名）。"""
    s = (s or "").strip()
    if not s or s in MAP_NAMES or s.startswith("<"):
        return False
    return bool(RE_STAGE.match(s)) and any(c.isdigit() or c.isalpha() for c in s)


def classify_attempt(lines, drops, result=""):
    """把一次尝试的判据行 + drop_statistics 键 -> 结构化结论（纯函数，可离线自测）。

    lines 顺序 = 文件顺序。链↔关卡映射：第 k 条 Start Task Chain: Fight 对应第 k 个
    非空 GetFightStage 选区（MAA 在开跑前按队列 Index 顺序逐条 GetFightStage）。
    """
    ev = {"selected": [], "not_open": [], "planned": [], "drop_log": [], "fights": [],
          "actions": 0, "other_chain_errs": OrderedDict(), "annih_done": False,
          "annih_progress": "", "no_reward": False, "round_ok": False, "round_partial": False,
          "skipped": [], "net_marks": 0, "dropped": [s for s in drops if is_stage_code(s)],
          "chains_seen": 0, "evidence": {}}
    fight_starts = 0
    net_recent = False
    cur_stage = None          # 当前链的关卡（由链序号映射）
    for raw in lines:
        ln = (raw or "").strip()
        if not ln:
            continue
        m = RE_SEL.match(ln)
        if m:
            lst = [x.strip().strip('"') for x in (m.group("lst") or "").split(",") if x.strip()]
            sel = (m.group("sel") or "").strip()
            for st in lst:
                if is_stage_code(st) and st not in ev["planned"]:
                    ev["planned"].append(st)
            if sel in ("null", ""):
                for st in lst:
                    if is_stage_code(st) and st not in ev["not_open"]:
                        ev["not_open"].append(st)
                ev["evidence"].setdefault(F_NOTOPEN, ln)
            else:
                ev["selected"].append(sel)
            continue
        m = RE_DROPSTAT.match(ln)
        if m:
            st = m.group("st")
            if is_stage_code(st) and st not in ev["drop_log"]:
                ev["drop_log"].append(st)
            continue
        m = RE_CHAINERR.match(ln)
        if m:
            chain = m.group("chain")
            if chain == "理智作战":
                stage = cur_stage if cur_stage else (ev["selected"][0] if len(ev["selected"]) == 1 else None)
                if net_recent:
                    kind = F_NET
                elif stage and (stage in ev["dropped"] or stage in ev["drop_log"]):
                    kind = "fight_err_after_success"      # 该关本次确实刷成了，报错另有其因
                else:
                    kind = F_UNAVAIL
                ev["fights"].append({"stage": stage, "kind": kind, "line": ln})
                et = F_NET if kind == F_NET else (F_UNAVAIL if kind == F_UNAVAIL else "fight_err_after_success")
                ev["evidence"].setdefault(et, ln)
            else:
                ev["other_chain_errs"][chain] = ev["other_chain_errs"].get(chain, 0) + 1
            continue
        m = RE_CHAINSTART.search(ln)
        if m and m.group("chain") == "Fight":
            if "Start Task Chain" in ln:
                fight_starts += 1
                idx = fight_starts - 1
                sel = [s for s in ev["selected"] if s]
                cur_stage = sel[idx] if idx < len(sel) else (sel[-1] if sel else None)
                net_recent = False
                ev["chains_seen"] = fight_starts
            continue
        if "开始行动" in ln:
            ev["actions"] += 1
            net_recent = False
            continue
        m = RE_ANNIH.search(ln)
        if m:
            ev["annih_progress"] = m.group("a") + "/" + m.group("b")
            continue
        if "完成任务: 剿灭作战" in ln:
            ev["annih_done"] = True
            continue
        if "无奖励关卡" in ln:
            ev["no_reward"] = True
            ev["evidence"].setdefault(F_NOREWARD, ln)
            continue
        if "代理指挥失误" in ln:
            ev["evidence"].setdefault(F_PROXY, ln)
            ev["fights"].append({"stage": cur_stage, "kind": F_PROXY, "line": ln})
            continue
        if "任务已全部完成" in ln:
            ev["round_ok"] = True
            continue
        if "任务已完成，但出现错误" in ln:
            ev["round_partial"] = True
            continue
        m = RE_SKIP.match(ln)
        if m:
            t = m.group("task").strip()
            if t not in ev["skipped"]:
                ev["skipped"].append(t)
            continue
        if any(k in ln for k in NET_MARKS):
            net_recent = True
            ev["net_marks"] += 1
            ev["evidence"].setdefault(F_NET, ln)
            continue
        if any(k in ln for k in NET_OK_MARKS):
            ev["net_marks"] += 1
            continue
    return ev

# ---------------------------------------------------------------- 状态（增量中间态）

def _attempt_key(a):
    return "%s|%s|%s" % (a.get("day", ""), a.get("acct", ""), a.get("file", ""))


def load_state(path=None):
    path = path or STATE_PATH
    try:
        st = _load_json(path)
    except FileNotFoundError:      # 首次运行（或替换瞬间被抢走）⇒ 空 state
        return {"schema": SCHEMA_STATE, "built_at": "", "attempts": {}}
    st.setdefault("attempts", {})
    return st


def _norm_attempt(a):
    """把远端/缓存记录规整成 state 记录（只留聚合需要的字段，控制体积）。"""
    return {"day": a.get("day", ""), "acct": a.get("acct", ""), "file": a.get("file", ""),
            "mt": a.get("mt", ""), "result": a.get("result", ""),
            "drops": [s for s in (a.get("drops") or []) if is_stage_code(s)],
            "lines": [str(x)[:LINE_CAP] for x in (a.get("lines") or [])]}


def merge_attempts(state, attempts, window_days=DEFAULT_DAYS, today=None):
    """把新扫描的尝试并入 state；按窗口裁剪旧日期。返回 (新增, 更新, 删除)。"""
    added = updated = 0
    for a in attempts:
        na = _norm_attempt(a)
        k = _attempt_key(na)
        old = state["attempts"].get(k)
        if old is None:
            added += 1
        elif old != na:
            updated += 1
        state["attempts"][k] = na
    today = today or dt.date.today()
    cut = (today - dt.timedelta(days=window_days - 1)).isoformat()
    dropped = [k for k, v in state["attempts"].items() if v.get("day", "") < cut]
    for k in dropped:
        del state["attempts"][k]
    return added, updated, len(dropped)


def save_state(state, path=None):
    path = path or STATE_PATH
    _write_text_atomic(path, json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))


# ---------------------------------------------------------------- 写盘：原子替换 + 单飞锁 + 审计

def _write_text_atomic(path, text):
    """原子写文本（对照 mas_run_profile.save_json / mas_plan_builder.save_json：tmp + os.replace）。

    - 读者永远只见旧文或新文，不可能看到半截 JSON（tmp 带 pid：并发写互不截断对方的 tmp）；
    - Windows 上读者恰好持句柄时 os.replace 会撞共享锁（PermissionError）⇒ ~1s 窗口内重试；
    - 进程被杀可能留下孤立的 `<name>.tmp.<pid>`（无害，不影响产物，也不会被读取）。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        for attempt in range(10):          # ~1s 窗口：Windows 上读者恰好持句柄时 replace 会撞共享锁
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.1)
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.remove(tmp)


def _load_json(path):
    """读 JSON（含原子替换瞬时重试：Windows 上 replace 期间读者可能撞 ERROR_ACCESS_DENIED/ENOENT）。

    配对 `_write_text_atomic`：写侧保证「旧文 or 新文」，读侧用 ~150ms 重试骑过替换瞬间的
    文件名/句柄临空。重试耗尽后最后一次照常抛错（不吞）。
    """
    for attempt in range(3):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            time.sleep(0.05)
    with open(path, "r", encoding="utf-8") as f:   # 最后一次：让真实错误原样抛出
        return json.load(f)


class _LockBusy(RuntimeError):
    """写盘锁等待超时（调用方应当明确让路，别猜）。"""


@contextlib.contextmanager
def _stage_lock(wait_s=None, path=None):
    """`_finish` 读-改-写的跨进程单飞锁（协议与 mas_dispatch._dispatch_lock 同款）。

    O_EXCL 创建（写 pid/ts）+ 陈旧抢占（`MAAORCH_STAGECAP_LOCK_STALE_S`）+ 释放清理；
    锁被占时**有界等待**（`MAAORCH_STAGECAP_LOCK_WAIT_S`）——等与不等都留痕：
    等待打印一行，获锁打印一行，超时抛 `_LockBusy`（调用方让路 rc=2）。
    """
    path = path or LOCK_PATH
    if wait_s is None:
        wait_s = LOCK_WAIT_S
    os.makedirs(os.path.dirname(path), exist_ok=True)
    t0 = time.time()
    notified = False
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("pid=%d ts=%s\n"
                         % (os.getpid(), dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            break
        except FileExistsError:
            holder = ""
            with contextlib.suppress(OSError):
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    holder = fh.read().strip()[:120]
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                age = 0.0
            if age > LOCK_STALE_S:
                # 陈旧锁（持有进程被 kill/超时未清）：抢过来继续
                with contextlib.suppress(OSError):
                    os.remove(path)
                continue
            if time.time() - t0 >= wait_s:
                raise _LockBusy("锁被占（%s，%.0fs 前）；已等 %.0fs 仍拿不到，本次让路"
                                % (holder or "未知持有者", age, time.time() - t0))
            if not notified:
                notified = True
                print("stage_capability: 写盘锁被占（%s，%.0fs 前），等待中…（上限 %.0fs）"
                      % (holder or "未知持有者", age, wait_s))
            time.sleep(0.5)
    waited = time.time() - t0
    if waited >= 1.0:
        print("stage_capability: 已获写盘锁（等待 %.1fs）" % waited)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def _audit_write(mode, state, rows, scanned=None, added=None, updated=None, dropped=None):
    """写盘后补一条统一审计（mas_audit v1：谁重建、扫了几次、state 行数——审计报告 P2-8 方向）。

    审计是旁路：任何失败都不打断写盘主流程（`safe_append` 兜底 + 本函数再包一层）。
    """
    params = {"mode": mode, "state_attempts": len(state.get("attempts") or {}), "rows": len(rows)}
    if scanned is not None:
        params["scanned"] = scanned
    if added is not None:
        params.update({"added": added, "updated": updated, "dropped": dropped})
    try:
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import mas_audit  # noqa: PLC0415
        mas_audit.safe_append(actor="tool:stage_capability", action="stage.rebuild",
                              target="file:runtime/reports/stage_capability.json", result="ok",
                              params=params, evidence=[STATE_PATH, JSON_PATH, MD_PATH],
                              card=os.environ.get("HERMES_KANBAN_TASK", ""))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 聚合：账号×关卡

def aggregate(state):
    """逐次尝试 -> 账号×关卡的台账（含 unknown 池与统计）。"""
    per = {}          # (acct, stage) -> row
    acct_planned = {}  # acct -> set(stage) 曾经出现在计划/选中里
    acct_days = {}
    for a in state["attempts"].values():
        acct, day, fname = a.get("acct", ""), a.get("day", ""), a.get("file", "")
        if not acct:
            continue
        ev = classify_attempt(a.get("lines") or [], a.get("drops") or [], a.get("result", ""))
        acct_days.setdefault(acct, set()).add(day)
        pl = acct_planned.setdefault(acct, set())
        for s in list(ev["planned"]) + list(ev["selected"]) + list(ev["not_open"]):
            pl.add(s)
        stages = set(ev["selected"]) | set(ev["not_open"]) | set(ev["dropped"]) | set(ev["drop_log"])
        if ev["annih_done"] or (ev["annih_progress"] and ANNIH_STAGE in ev["selected"]):
            stages.add(ANNIH_STAGE)
        for stage in sorted(stages):
            row = per.setdefault((acct, stage), {
                "account": acct, "stage": stage, "status": ST_UNKNOWN,
                "last_success": "", "last_failure": "", "last_attempt": "",
                "success_n": 0, "selected_n": 0, "not_open_n": 0, "attempt_n": 0,
                "actions": 0, "sources": [], "failures": [], "net_fail_n": 0})
            row["attempt_n"] += 1
            row["last_attempt"] = max(row["last_attempt"], day)
            # 成功证据（保守：必须"该关卡出现在成功记录里"）
            ok, srcs = False, []
            if stage in ev["dropped"]:
                srcs.append("drop_statistics")          # AUTO-MAS 落的按关掉落（主证据）
            if stage in ev["drop_log"]:
                srcs.append("fight_log_drop")           # 副本日志的 `<关> 掉落统计:` 块（交叉验证/补漏）
            if stage == ANNIH_STAGE and ev["annih_done"]:
                srcs.append("annihilation_chain_done")  # 剿灭不掉落，只能认链完成
            if srcs:
                ok = True
                row["success_n"] += 1
                for src in srcs:
                    if src not in row["sources"]:
                        row["sources"].append(src)
                if day > row["last_success"]:
                    row["last_success"] = day
            # 选中
            if stage in ev["selected"]:
                row["selected_n"] += 1
            if stage in ev["not_open"]:
                row["not_open_n"] += 1
                row["failures"].append({"type": F_NOTOPEN, "day": day, "file": fname,
                                        "line": ev["evidence"].get(F_NOTOPEN, ""), "capability_impact": True})
                if day > row["last_failure"]:
                    row["last_failure"] = day
            # 链级失败
            for fr in ev["fights"]:
                if fr.get("stage") != stage:
                    continue
                k = fr.get("kind")
                if k == F_NET or k == "fight_err_after_success":
                    row["net_fail_n"] += 1
                    row["failures"].append({"type": k, "day": day, "file": fname,
                                            "line": fr.get("line", ""), "capability_impact": False})
                elif k in CAP_FAIL:
                    row["failures"].append({"type": k, "day": day, "file": fname,
                                            "line": fr.get("line", "") or ev["evidence"].get(k, ""),
                                            "capability_impact": True})
                    if day > row["last_failure"]:
                        row["last_failure"] = day
            if stage in ev["drop_log"] and "fight_log_drop" not in row["sources"]:
                row["sources"].append("fight_log_drop")
            if ev["actions"] and stage in ev["selected"]:
                row["actions"] += 1
    pool = sorted({s for (_, s) in per})
    rows = []
    for (acct, stage) in sorted(per):
        row = per[(acct, stage)]
        cap_fail = [f for f in row["failures"] if f.get("capability_impact")]
        if row["success_n"]:
            row["status"] = ST_SUCCESS
        elif cap_fail:
            row["status"] = ST_FAILED
        else:
            row["status"] = ST_UNKNOWN
        row["failure_types"] = OrderedDict(sorted(Counter(f["type"] for f in row["failures"]).items(),
                                                 key=lambda kv: (-kv[1], kv[0])))
        row["fail_n"] = len(cap_fail)
        row["fail_after_success"] = bool(row["success_n"] and row["last_failure"] > row["last_success"])
        row["failures"] = row["failures"][:6]
        row["impact"] = "capability" if cap_fail else ("env" if row["net_fail_n"] else "")
        rows.append(row)
    # 未知池：全队出现过的关 - 本号 success 过的关
    unknown = OrderedDict()
    for acct in sorted(acct_days):
        mine = {r["stage"] for r in rows if r["account"] == acct and r["status"] == ST_SUCCESS}
        tried = {r["stage"] for r in rows if r["account"] == acct and (r["selected_n"] or r["not_open_n"])}
        miss = [{"stage": s, "tried": s in tried} for s in pool if s not in mine]
        unknown[acct] = miss
    st = {
        "accounts": sorted(acct_days),
        "account_days": {k: sorted(v) for k, v in sorted(acct_days.items())},
        "pool": pool,
    }
    return rows, unknown, st


def stats_of(rows, unknown, st):
    n_rows = len(rows)
    by_status = Counter(r["status"] for r in rows)
    pairs = len(unknown) * len(st["pool"])
    unk_pairs = sum(1 for v in unknown.values() for x in v)
    tried_unk = sum(1 for v in unknown.values() for x in v if x["tried"])
    return {
        "accounts": len(st["accounts"]), "stages": len(st["pool"]), "rows": n_rows,
        "success_rows": by_status.get(ST_SUCCESS, 0), "failed_rows": by_status.get(ST_FAILED, 0),
        "unknown_rows": by_status.get(ST_UNKNOWN, 0),
        "rows_unknown_ratio": round(by_status.get(ST_UNKNOWN, 0) / n_rows, 4) if n_rows else 0,
        "pool_pairs": pairs, "pool_unknown_pairs": unk_pairs,
        "pool_unknown_ratio": round(unk_pairs / pairs, 4) if pairs else 0,
        "pool_unknown_tried": tried_unk,
        "accounts_with_success": len({r["account"] for r in rows if r["status"] == ST_SUCCESS}),
    }

# ---------------------------------------------------------------- 报告渲染

def build_report(state, rows, unknown, st, window_days, source_note=""):
    meta = state.get("built_at") or ""
    return {
        "schema": SCHEMA_REPORT,
        "built_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "state_built_at": meta,
        "window": {"days": window_days, "start": min((v["day"] for v in state["attempts"].values()), default=""),
                   "end": max((v["day"] for v in state["attempts"].values()), default=""),
                   "attempts": len(state["attempts"])},
        "source": {
            "host": "100.79.173.69",
            "history": HIST_ROOT + r"\<date>\<account>\*.json",
            "attempt_log": HIST_ROOT + r"\<date>\<account>\*.log",
            "note": source_note or ("只读扫描：drop_statistics(JSON) + 逐次尝试 gui.log 副本；不读 MAA-Multi 目录"
                                    "（一目录多账号无法归属，改用 history 的逐账号切片）"),
        },
        "judgement": {
            "success": "该关卡出现在成功记录里：drop_statistics 键命中 / 副本日志 <关> 掉落统计 块 / 剿灭链 完成任务: 剿灭作战",
            "failed": "关卡级失败证据：" + " / ".join(sorted(CAP_FAIL)),
            "unknown": "从没刷过 / 选过但无结论 / 只见到掉线类环境失败（不做能力判定）",
        },
        "stats": stats_of(rows, unknown, st),
        "accounts": st["accounts"],
        "account_days": st["account_days"],
        "stages": st["pool"],
        "rows": rows,
        "unknown": unknown,
    }


def render_json(report):
    return json.dumps(report, ensure_ascii=False, indent=1, sort_keys=False)


def _zh(status):
    return {ST_SUCCESS: "✅成功", ST_FAILED: "❌失败", ST_UNKNOWN: "❓未知"}.get(status, status)


def render_md(report):
    s = report["stats"]
    L = []
    L.append("# 账号 × 关卡 可刷能力台账")
    L.append("")
    L.append("> 生成时间 %s ｜ 窗口 %s ~ %s（%d 天，%d 次尝试）｜ 只读产出（history 的 drop_statistics + 逐次尝试 gui.log 副本）"
             % (report["built_at"], report["window"]["start"], report["window"]["end"],
                report["window"]["days"], report["window"]["attempts"]))
    L.append(">")
    L.append("> **判据**：`success` = 该关卡出现在成功记录里（`drop_statistics` 键命中 / 副本日志 `<关> 掉落统计` 块 / 剿灭 `完成任务: 剿灭作战`）；"
             "`failed` = 关卡级失败证据（%s）；其余为 `unknown`。"
             "掉线/重连类环境失败**不算** failed（实测 1066/1088 条 Fight 链报错属此类）。" % " / ".join(sorted(CAP_FAIL)))
    L.append(">")
    L.append("> 机器可读版：`runtime/reports/stage_capability.json`（schema `%s`）；代码接口 `tools/stage_capability.py::can_farm(account, stage)`。" % SCHEMA_REPORT)
    L.append("")
    L.append("## 0. 总览")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append("| 账号数（窗口内有记录） | %d |" % s["accounts"])
    L.append("| 关卡池（全队出现过的关） | %d：%s |" % (s["stages"], "、".join(report["stages"])))
    L.append("| 台账行（账号×关卡，本号有过证据） | %d |" % s["rows"])
    L.append("| └ 成功 / 失败 / 未知 | %d / %d / %d（unknown 占 %.1f%%） |"
             % (s["success_rows"], s["failed_rows"], s["unknown_rows"], 100 * s["rows_unknown_ratio"]))
    L.append("| 全池对照（每号 × 每关） | %d 对，其中未知 %d（%.1f%%），其中本号试过没成的 %d |"
             % (s["pool_pairs"], s["pool_unknown_pairs"], 100 * s["pool_unknown_ratio"], s["pool_unknown_tried"]))
    L.append("| 至少刷成过一关的账号 | %d / %d |" % (s["accounts_with_success"], s["accounts"]))
    L.append("")
    L.append("## 1. 总表（每号一行）")
    L.append("")
    L.append("| 账号 | 成功关 | 失败关 | 未知(试过没成/池内) | 最近成功 | 最近失败 | 备注 |")
    L.append("|---|---|---|---|---|---|---|")
    by_acct = OrderedDict()
    for r in report["rows"]:
        by_acct.setdefault(r["account"], []).append(r)
    for acct in report["accounts"]:
        rs = by_acct.get(acct, [])
        ok = [r["stage"] for r in rs if r["status"] == ST_SUCCESS]
        bad = [r for r in rs if r["status"] == ST_FAILED]
        badset = {r["stage"] for r in bad}
        unk = [x for x in report["unknown"].get(acct, []) if x["tried"] and x["stage"] not in badset]
        last_ok = max([r["last_success"] for r in rs] or [""])
        last_bad = max([r["last_failure"] for r in bad] or [""])
        note = []
        if bad:
            note.append("失败类型：" + ",".join(sorted({t for r in bad for t in r["failure_types"]})))
        if any(r.get("fail_after_success") for r in rs):
            note.append("该关最近一次是失败（此前成功过）")
        if unk:
            note.append("试过没成：" + ",".join(x["stage"] for x in unk))
        L.append("| %s | %s | %s | %d/%d | %s | %s | %s |"
                 % (acct, "、".join(ok) or "—", "、".join(r["stage"] for r in bad) or "—",
                    len(unk), len(report["unknown"].get(acct, [])), last_ok or "—", last_bad or "—",
                    "；".join(note) or "—"))
    L.append("")
    L.append("## 2. 明细（每号：成功/失败/未知）")
    L.append("")
    for acct in report["accounts"]:
        rs = by_acct.get(acct, [])
        L.append("### %s" % acct)
        L.append("")
        ds = report.get("account_days", {}).get(acct, [])
        if ds:
            rng = ds[0] if len(ds) == 1 else "%s ~ %s" % (ds[0], ds[-1])
            L.append("- 尝试 %d 天（%s）" % (len(ds), rng))
        okr = [r for r in rs if r["status"] == ST_SUCCESS]
        if okr:
            L.append("- **成功刷过**（%d 关）：%s"
                     % (len(okr), "；".join("%s（最近 %s，%d 次，%s）"
                                          % (r["stage"], r["last_success"], r["success_n"], "+".join(r["sources"]))
                                          for r in okr)))
            warn = [r for r in okr if r["fail_n"]]
            if warn:
                L.append("  - ⚠ 其中带能力类失败记录（成功过，但某些次被选中却没开打/当日不可选）：%s"
                         % "；".join("%s ×%d（最近 %s：%s）"
                                    % (r["stage"], r["fail_n"], r["last_failure"],
                                       ",".join(t for t in r["failure_types"] if t in CAP_FAIL)) for r in warn))
        else:
            L.append("- **成功刷过**：无")
        badr = [r for r in rs if r["status"] == ST_FAILED]
        if badr:
            L.append("- **失败**（%d 关）：" % len(badr))
            for r in badr:
                f = next((x for x in r["failures"] if x.get("capability_impact")), r["failures"][0] if r["failures"] else {})
                L.append("  - %s ｜ 类型 %s ｜ 最近失败 %s ｜ 证据：`%s`"
                         % (r["stage"], ",".join(r["failure_types"]), r["last_failure"], (f.get("line") or "")[:120]))
        else:
            L.append("- **失败**：无")
        unk_all = report["unknown"].get(acct, [])
        badset = {r["stage"] for r in rs if r["status"] == ST_FAILED}
        untried = [x["stage"] for x in unk_all if not x["tried"]]
        tried = [x["stage"] for x in unk_all if x["tried"] and x["stage"] not in badset]
        L.append("- **未知（该号从没刷过，全队池对照 %d 关）**：试过没成 %s；从未尝试 %s"
                 % (len(unk_all), "、".join(tried) or "—", "、".join(untried) or "—"))
        nf = [r for r in rs if r["net_fail_n"]]
        if nf:
            L.append("- 环境类（不影响能力判定）：%s" % "；".join("%s ×%d" % (r["stage"], r["net_fail_n"]) for r in nf))
        L.append("")
    L.append("## 3. 口径与已知局限")
    L.append("")
    L.append("1. `success` 只认**该关卡出现在成功记录里**；`maa_result` 成功但掉落里没这关 ⇒ 记 `unknown`（不猜）。")
    L.append("2. 关卡归属：MAA 在开跑前按队列顺序逐条 `GetFightStage`，第 k 条 `Start Task Chain: Fight` 对应第 k 个非空选区；据此把 Fight 链报错落到具体关卡。")
    L.append("3. 剿灭（`Annihilation`）不掉落、不进 `drop_statistics`，成功判据是 `完成任务: 剿灭作战`（＋`剿灭模式 N/M` 进度）。")
    L.append("4. `stage_not_open`（当日未开放）与 `stage_unavailable`（选中但没开打）都是**日粒度**证据：活动关/轮换关当天不可打，隔天可能就能打；消费方按 `last_failure` 判断新鲜度。")
    L.append("5. 窗口默认 %d 天；只用 history 的逐账号切片，不读 `E:\\MAA-Multi\\MAA_*\\debug`（一目录多账号，无稳定归属）。" % report["window"]["days"])
    L.append("6. 台账外（既不在 rows 也不在池里）的关卡一律返回 `unknown` ⇒ 按「先试探后开刷」流程处理。")
    L.append("")
    return "\n".join(L)

# ---------------------------------------------------------------- 对外接口：can_farm

_CACHE = {"path": None, "mtime": None, "index": None}


def _index(path=JSON_PATH):
    """读已生成的报告 JSON，建 (account, stage) -> row 索引（带 mtime 缓存）。"""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    if _CACHE["path"] == path and _CACHE["mtime"] == mt:
        return _CACHE["index"]
    try:
        rep = _load_json(path)          # 原子替换瞬间可能临空 ⇒ 内部重试
    except FileNotFoundError:
        return None
    idx = {(r["account"], r["stage"]): r for r in rep.get("rows", [])}
    _CACHE.update({"path": path, "mtime": mt, "index": idx})
    return idx


def can_farm_detail(account, stage, path=JSON_PATH):
    """-> dict(状态/证据/最近成败) 或 None（台账里没有这条：既没刷过也没试过）。"""
    idx = _index(path)
    if not idx:
        return None
    return idx.get((account, stage))


def can_farm(account, stage, path=JSON_PATH):
    """账号×关卡 -> "success" | "failed" | "unknown"（台账查不到 = unknown，交给试探流程）。"""
    row = can_farm_detail(account, stage, path)
    return row["status"] if row else ST_UNKNOWN


# ---------------------------------------------------------------- CLI

def cmd_build(days, no_write, timeout):
    payload, err = scan(days=days, timeout=timeout)
    if payload is None:
        print("stage_capability: 扫描失败 — " + err)
        return 2
    return _finish(payload, days, no_write, mode="build")


def cmd_update(window_days, update_days, no_write, timeout):
    payload, err = scan(days=update_days, timeout=timeout)
    if payload is None:
        print("stage_capability: 扫描失败 — " + err)
        return 2
    return _finish(payload, window_days, no_write, mode="update")


def _assemble(payload, window_days, mode):
    """装配 state（build=全新；update=读-改-写）+ 聚合 + 渲染报告。

    update 模式的读-改-写**必须在 `_stage_lock()` 里调用**（`_finish` 已如此）；干跑路径只读不写。
    """
    if mode == "build":
        state = {"schema": SCHEMA_STATE, "built_at": "", "attempts": {}}
    else:
        state = load_state()
    added, updated, dropped = merge_attempts(state, payload.get("attempts") or [],
                                            window_days=window_days, today=dt.date.today())
    state["built_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["window_days"] = window_days
    rows, unknown, st = aggregate(state)
    rep = build_report(state, rows, unknown, st, window_days)
    rep["source"]["scanned"] = (payload.get("meta") or {}).get("days") or []
    rep["source"]["scan_secs"] = (payload.get("meta") or {}).get("secs")
    return state, rows, rep, added, updated, dropped


def _print_scan_summary(mode, payload, added, updated, dropped, rows, rep):
    print("stage_capability[%s]: 扫描 %d 次尝试/%d 天（%.0fs）；state 新增 %d 更新 %d 裁掉 %d；台账 %d 行；unknown %.1f%%"
          % (mode, len(payload.get("attempts") or []), payload.get("meta", {}).get("days") and len(payload["meta"]["days"]) or 0,
             float(payload.get("meta", {}).get("secs") or 0), added, updated, dropped, len(rows),
             100 * rep["stats"]["rows_unknown_ratio"]))


def _finish(payload, window_days, no_write, mode="build"):
    if no_write:   # 干跑：不建锁、不碰盘（update 只多一次只读的 load_state）
        state, rows, rep, added, updated, dropped = _assemble(payload, window_days, mode)
        _print_scan_summary(mode, payload, added, updated, dropped, rows, rep)
        print("(--no-write：未写盘)")
        return 0
    try:
        with _stage_lock():
            # 读-改-写整段持锁：两个并发 --update 串行，后者基于前者的 state 继续合并（不丢更新）
            state, rows, rep, added, updated, dropped = _assemble(payload, window_days, mode)
            _print_scan_summary(mode, payload, added, updated, dropped, rows, rep)
            os.makedirs(STATE_DIR, exist_ok=True)
            os.makedirs(REPORTS, exist_ok=True)
            save_state(state)
            _write_text_atomic(JSON_PATH, render_json(rep))
            _write_text_atomic(MD_PATH, render_md(rep))
            _audit_write(mode, state, rows, scanned=len(payload.get("attempts") or []),
                         added=added, updated=updated, dropped=dropped)
    except _LockBusy as e:
        print("stage_capability[%s]: %s" % (mode, e))
        return 2
    print("写出：%s\n      %s\n      %s" % (STATE_PATH, JSON_PATH, MD_PATH))
    return 0


def cmd_local(window_days, no_write):
    """--rebuild-local：只从本地 state 重渲染报表（不扫远端）。

    写盘与 `--update` 共用同一把单飞锁：否则「读 state → 写报表」之间若有 update 落地，
    盘上报表就会**落后于 state**（消费者读到过期台账）。干跑只读，不建锁不碰盘。
    """
    def _render_from_state():
        state = load_state()
        if not state["attempts"]:
            print("stage_capability: state 为空（先跑 --build）")
            return None
        rows, unknown, st = aggregate(state)
        return state, rows, build_report(state, rows, unknown, st, state.get("window_days") or window_days)

    if no_write:
        got = _render_from_state()
        if got is None:
            return 2
        _state, rows, rep = got
        print("(local) 台账 %d 行；unknown %.1f%%" % (len(rows), 100 * rep["stats"]["rows_unknown_ratio"]))
        print("(--no-write：未写盘)")
        return 0
    try:
        with _stage_lock():
            got = _render_from_state()
            if got is None:
                return 2
            state, rows, rep = got
            os.makedirs(REPORTS, exist_ok=True)
            _write_text_atomic(JSON_PATH, render_json(rep))
            _write_text_atomic(MD_PATH, render_md(rep))
            _audit_write("rebuild-local", state, rows)
    except _LockBusy as e:
        print("stage_capability[rebuild-local]: %s" % e)
        return 2
    print("(local) 重建完成：%d 行；unknown %.1f%%" % (len(rows), 100 * rep["stats"]["rows_unknown_ratio"]))
    return 0


def cmd_replay(path, days, no_write):
    payload = load_raw(path)
    return _finish(payload, days, no_write, mode="build")


def cmd_account(acct, as_json):
    idx = _index()
    if idx is None:
        print("stage_capability: 台账不存在（先跑 --build）")
        return 2
    rows = [r for (a, s), r in sorted(idx.items()) if a == acct]
    if not rows:
        print("stage_capability: 台账里没有账号 %s（窗口内无记录）" % acct)
        return 2
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    rep = _load_json(JSON_PATH)         # 原子替换瞬间可能临空 ⇒ 内部重试
    unk = rep.get("unknown", {}).get(acct, [])
    print("账号 %s（窗口 %s ~ %s）" % (acct, rep["window"]["start"], rep["window"]["end"]))
    for stt in (ST_SUCCESS, ST_FAILED, ST_UNKNOWN):
        sub = [r for r in rows if r["status"] == stt]
        if not sub:
            continue
        print("%s %d 关：" % (_zh(stt), len(sub)))
        for r in sub:
            tail = []
            if r["last_success"]:
                tail.append("最近成功 " + r["last_success"])
            cap = [t for t in r["failure_types"] if t in CAP_FAIL]
            env = [t for t in r["failure_types"] if t not in CAP_FAIL]
            if cap:
                tail.append("能力类失败 %s（%d 次，最近 %s）" % (",".join(cap), r["fail_n"], r["last_failure"]))
            if env:
                tail.append("环境/信息类 %s（不判能力）" % ",".join(env))
            if r["selected_n"]:
                tail.append("被选中 %d 次" % r["selected_n"])
            if r["not_open_n"]:
                tail.append("当日不可选 %d 次" % r["not_open_n"])
            print("   %-14s %s" % (r["stage"], "；".join(tail) or "—"))
    badset = {r["stage"] for r in rows if r["status"] == ST_FAILED}
    tried = [x["stage"] for x in unk if x["tried"] and x["stage"] not in badset]
    untried = [x["stage"] for x in unk if not x["tried"]]
    print("未知（全队池对照）：试过没成 %s｜从未尝试 %s" % ("、".join(tried) or "—", "、".join(untried) or "—"))
    return 0


def cmd_check():
    try:
        payload, err = scan(days=1, timeout=300)
    except Exception as e:  # noqa: BLE001
        print("stage_capability --check: FAIL (%s)" % e)
        return 2
    if payload is None:
        print("stage_capability --check: FAIL (%s)" % err)
        return 2
    m = payload.get("meta") or {}
    print("stage_capability --check: OK（SSH+PS 通；当日 %s 个目录，%d 次尝试，%.0fs）"
          % (len(m.get("days") or []), m.get("n") or 0, float(m.get("secs") or 0)))
    return 0


def cmd_json_report(acct=None):
    if not os.path.exists(JSON_PATH):
        print("stage_capability: 台账不存在（先跑 --build）")
        return 2
    rep = _load_json(JSON_PATH)         # 原子替换瞬间可能临空 ⇒ 内部重试
    if acct:
        rep = dict(rep)
        rep["rows"] = [r for r in rep["rows"] if r["account"] == acct]
        rep["unknown"] = {acct: rep.get("unknown", {}).get(acct, [])}
    print(render_json(rep))
    return 0


# ---------------------------------------------------------------- 离线自检

def _SEL(s):
    return 'GetFightStage: from ["%s"], selected %s' % (s, s)


def _SELN(s):
    return 'GetFightStage: from ["%s"], selected null' % s


_CASES = [
    ("success via drop_statistics", {"drops": ["1-7"], "result": "Success!",
     "lines": ['Index 1, Type "Fight", Name 理智作战, IsEnable true', _SEL("1-7"),
               "正在运行中……", "Start Task Chain: Fight, Task ID: 2", "开始行动 21~30 次, -60理智",
               "1-7 掉落统计: ", "Completed Task Chain: Fight, Task ID: 2", "任务已全部完成！"]},
     [("1-7", ST_SUCCESS, None)]),
    ("maa_result success but no drop for that stage -> unknown", {"drops": [], "result": "Success!",
     "lines": [_SEL("1-7"), "Start Task Chain: Fight, Task ID: 2", "开始行动 1~5 次, -30理智", "任务已全部完成！"]},
     [("1-7", ST_UNKNOWN, None)]),
    ("stage_not_open", {"drops": [], "result": "MAA 部分任务执行失败",
     "lines": [_SELN("SR-7"), "关卡计划内没有当日开放的关卡", _SEL("1-7"), "开始行动 1~10 次, -60理智",
               "1-7 掉落统计: ", "任务已完成，但出现错误！"]},
     [("SR-7", ST_FAILED, F_NOTOPEN), ("1-7", ST_SUCCESS, None)]),
    ("stage_unavailable (CA-5 selected, chain error before battle)", {"drops": ["1-7"], "result": "MAA 部分任务执行失败",
     "lines": ['Index 1, Type "Fight", Name 理智作战, IsEnable true', _SEL("CA-5"),
               'Index 2, Type "Fight", Name 剩余理智, IsEnable true', _SEL("1-7"), "正在运行中……",
               "Start Task Chain: Fight, Task ID: 2", "任务出错: 理智作战",
               "Start Task Chain: Fight, Task ID: 3", "开始行动 1~10 次, -60理智", "1-7 掉落统计: ",
               "Completed Task Chain: Fight, Task ID: 3", "任务已全部完成！"]},
     [("CA-5", ST_FAILED, F_UNAVAIL), ("1-7", ST_SUCCESS, None)]),
    ("fight error after real drops -> success (env note kept)", {"drops": [], "result": "MAA 部分任务执行失败",
     "lines": [_SEL("AT-4"), "Start Task Chain: Fight, Task ID: 2", "开始行动 1~10 次, -120理智",
               "AT-4 掉落统计: ", "游戏掉线，重新连接", "任务出错: 理智作战"]},
     [("AT-4", ST_SUCCESS, None)]),
    ("net failure without drops -> unknown (not failed)", {"drops": [], "result": "MAA 在完成任务前退出",
     "lines": [_SEL("AT-4"), "Start Task Chain: Fight, Task ID: 2", "游戏掉线，重新连接", "任务出错: 理智作战"]},
     [("AT-4", ST_UNKNOWN, None)]),
    ("annihilation chain done -> success", {"drops": [], "result": "Success!",
     "lines": ['Index 1, Type "Fight", Name 剿灭作战, IsEnable true', _SEL("Annihilation"),
               "Start Task Chain: Fight, Task ID: 2", "开始行动 1~5 次, -125理智", "剿灭模式 : 1320 / 1800",
               "完成任务: 剿灭作战", "任务已全部完成！"]},
     [("Annihilation", ST_SUCCESS, None)]),
    ("annihilation selected but chain error -> failed", {"drops": [], "result": "MAA 部分任务执行失败",
     "lines": [_SEL("Annihilation"), "Start Task Chain: Fight, Task ID: 2", "任务出错: 理智作战"]},
     [("Annihilation", ST_FAILED, F_UNAVAIL)]),
    ("proxy mismatch -> failed", {"drops": [], "result": "MAA 部分任务执行失败",
     "lines": [_SEL("1-7"), "Start Task Chain: Fight, Task ID: 2", "开始行动 1 次, -6理智", "代理指挥失误",
               "任务出错: 理智作战"]},
     [("1-7", ST_FAILED, F_PROXY)]),
]


def _selftest():
    ok = True
    for i, (name, att, expects) in enumerate(_CASES, 1):
        st = {"schema": SCHEMA_STATE, "attempts": {}}
        a = dict(att)
        a.update({"day": "2026-09-27", "acct": "测试号", "file": "case%d.json" % i, "mt": "2026-09-27 10:00:00"})
        st["attempts"]["k%d" % i] = _norm_attempt(a)
        rows, unknown, stt = aggregate(st)
        got = {r["stage"]: r for r in rows}
        for stage, want_status, want_type in expects:
            r = got.get(stage)
            actual = r["status"] if r else "(missing)"
            detail = ""
            if want_type:
                detail = ",".join(r["failure_types"].keys()) if r else ""
                if want_type not in detail:
                    actual += " [types=%s]" % detail
            flag = "OK " if actual == want_status and (not want_type or want_type in detail) else "FAIL"
            if flag == "FAIL":
                ok = False
            print("%s  case%d %-46s %-14s -> %s（期望 %s%s）"
                  % (flag, i, name, stage, actual, want_status, "/" + want_type if want_type else ""))
    # 掉线类不计失败：AT-4 应无 capability 失败
    st = {"schema": SCHEMA_STATE, "attempts": {}}
    for i, (_, att, _e) in enumerate(_CASES):
        a = dict(att)
        a.update({"day": "2026-09-27", "acct": "测试号", "file": "case%d.json" % i, "mt": ""})
        st["attempts"]["k%d" % i] = _norm_attempt(a)
    rows, unknown, stt = aggregate(st)
    at4 = next((r for r in rows if r["stage"] == "AT-4"), None)
    if at4 and at4["net_fail_n"] >= 1 and not at4["fail_n"]:
        print("OK  掉线类计入环境失败（net_fail_n=%d）且不拉低能力判定" % at4["net_fail_n"])
    else:
        ok = False
        print("FAIL 掉线类判定异常: %s" % at4)
    if not _selftest_write_layer():
        ok = False
    print("--selftest:", "全部通过" if ok else "有失败用例")
    return 0 if ok else 1


def _selftest_write_layer():
    """写盘层自检：原子写 / 锁（忙等→让路、陈旧抢占、释放）/ `_finish` 读-改-写回环。

    全程在临时目录跑（临时重定向模块路径）；审计打桩，不碰真实 runtime/。
    """
    import io as _io
    import shutil as _shutil
    import tempfile as _tempfile

    ok = True
    d = _tempfile.mkdtemp(prefix="stagecap_selftest_")
    saved = {k: globals()[k] for k in ("STATE_PATH", "JSON_PATH", "MD_PATH", "LOCK_PATH",
                                       "STATE_DIR", "REPORTS")}
    saved_audit = globals()["_audit_write"]
    seen = []

    def line(flag, name, detail=""):
        nonlocal ok
        if flag == "FAIL":
            ok = False
        print("%s  %s%s" % (flag, name, (" ← " + detail) if detail else ""))

    def _att(day, fname):
        return {"day": day, "acct": "写层测试号", "file": fname, "mt": day + " 10:00:00",
                "result": "Success!", "drops": ["1-7"],
                "lines": [_SEL("1-7"), "Start Task Chain: Fight, Task ID: 2", "开始行动 1 次, -6理智",
                          "1-7 掉落统计: ", "任务已全部完成！"]}

    try:
        globals()["STATE_DIR"] = os.path.join(d, "state")
        globals()["STATE_PATH"] = os.path.join(d, "state", "s.json")
        globals()["REPORTS"] = os.path.join(d, "reports")
        globals()["JSON_PATH"] = os.path.join(d, "reports", "r.json")
        globals()["MD_PATH"] = os.path.join(d, "reports", "r.md")
        globals()["LOCK_PATH"] = os.path.join(d, "stage_capability.lock")
        globals()["_audit_write"] = lambda *a, **kw: (seen.append(a), True)[1]
        with contextlib.redirect_stdout(_io.StringIO()):
            # 1) 原子写：内容正确、覆盖生效、不留 tmp
            p = os.path.join(d, "a.json")
            _write_text_atomic(p, '{"v": 1}')
            b1 = open(p, encoding="utf-8").read()
            _write_text_atomic(p, '{"v": 2}')
            b2 = open(p, encoding="utf-8").read()
            leftovers = [x for x in os.listdir(d) if x.startswith("a.json") and x != "a.json"]
            # 2) 锁被占：有界等待后明确让路（且不删别人的锁）
            lockp = LOCK_PATH
            with open(lockp, "w", encoding="utf-8") as fh:
                fh.write("pid=999 ts=桩\n")
            t0 = time.time()
            try:
                with _stage_lock(wait_s=0.4):
                    busy = "acquired"
            except _LockBusy:
                busy = "busy"
            waited = time.time() - t0
            kept = os.path.exists(lockp)
            # 3) 陈旧抢占 + 释放清理
            old = time.time() - (LOCK_STALE_S + 10)
            os.utime(lockp, (old, old))
            with _stage_lock(wait_s=0.5):
                holder = open(lockp, encoding="utf-8").read()
            released = not os.path.exists(lockp)
            # 4) _finish 读-改-写回环：两次 update 合并叠加 + 审计打桩
            rc1 = _finish({"meta": {"days": ["2026-09-27"], "secs": 1},
                           "attempts": [_att("2026-09-27", "a%d.json" % i) for i in range(3)]},
                          30, False, mode="update")
            rc2 = _finish({"meta": {"days": ["2026-09-26"], "secs": 1},
                           "attempts": [_att("2026-09-26", "b%d.json" % i) for i in range(2)]},
                          30, False, mode="update")
            st = json.load(open(STATE_PATH, encoding="utf-8"))
            rep = json.load(open(JSON_PATH, encoding="utf-8"))
            md_ok = os.path.exists(MD_PATH) and os.path.getsize(MD_PATH) > 0
            row = next((r for r in rep.get("rows", []) if r["stage"] == "1-7"), None)
            # 5) 干跑不碰盘
            rc3 = _finish({"meta": {}, "attempts": [_att("2026-09-26", "c9.json")]}, 30, True, mode="update")
            st2 = json.load(open(STATE_PATH, encoding="utf-8"))
            lock_gone = not os.path.exists(LOCK_PATH)
            naudit5 = len(seen)                             # 5) 的审计条数：在 6) 再写盘前先定格
            # 6) rebuild-local 与 update 同锁：忙时让路（不改产物）、闲时写盘、干跑不碰盘
            j_before = open(JSON_PATH, "rb").read()
            with open(LOCK_PATH, "w", encoding="utf-8") as fh:
                fh.write("pid=888 ts=桩\n")
            saved_wait = globals()["LOCK_WAIT_S"]
            globals()["LOCK_WAIT_S"] = 0.4
            rc4 = cmd_local(30, False)                      # 锁被占 ⇒ 让路
            globals()["LOCK_WAIT_S"] = saved_wait
            j_busy = open(JSON_PATH, "rb").read()
            os.remove(LOCK_PATH)
            rc5 = cmd_local(30, False)                      # 空闲 ⇒ 真写盘
            j_free = open(JSON_PATH, "rb").read()
            rc6 = cmd_local(30, True)                       # 干跑 ⇒ 不碰盘
            j_dry = open(JSON_PATH, "rb").read()
            rep6 = json.loads(open(JSON_PATH, encoding="utf-8").read())
            lock_gone2 = not os.path.exists(LOCK_PATH)
        if b1 == '{"v": 1}' and b2 == '{"v": 2}' and not leftovers:
            line("OK ", "写盘层 1/6 原子写（覆盖生效、不留 tmp）")
        else:
            line("FAIL", "写盘层 1/6 原子写", "b1=%r b2=%r leftovers=%r" % (b1, b2, leftovers))
        if busy == "busy" and waited >= 0.35 and kept:
            line("OK ", "写盘层 2/6 锁被占：等待 %.1fs 后让路（不删别人的锁）" % waited)
        else:
            line("FAIL", "写盘层 2/6 锁被占让路", "busy=%s waited=%.2f kept=%s" % (busy, waited, kept))
        if ("pid=%d" % os.getpid()) in holder and released:
            line("OK ", "写盘层 3/6 陈旧锁抢占 + 释放清理")
        else:
            line("FAIL", "写盘层 3/6 陈旧锁", "holder=%r released=%s" % (holder, released))
        if rc1 == 0 and rc2 == 0 and len(st["attempts"]) == 5 and md_ok \
                and row and row["status"] == ST_SUCCESS:
            line("OK ", "写盘层 4/6 _finish 读-改-写回环（两次 update 合并 %d 条）" % len(st["attempts"]))
        else:
            line("FAIL", "写盘层 4/6 _finish 回环",
                 "rc1=%s rc2=%s attempts=%s md=%s row=%s" % (rc1, rc2, len(st["attempts"]), md_ok, row))
        if rc3 == 0 and len(st2["attempts"]) == 5 and naudit5 == 2 and lock_gone:
            line("OK ", "写盘层 5/6 干跑不碰盘 + 写盘留审计 %d 条" % naudit5)
        else:
            line("FAIL", "写盘层 5/6 干跑/审计",
                 "rc3=%s attempts=%s audited=%s lock_gone=%s" % (rc3, len(st2["attempts"]), naudit5, lock_gone))
        if rc4 == 2 and j_busy == j_before and rc5 == 0 and rc6 == 0 and j_dry == j_free \
                and bool(rep6.get("rows")) and lock_gone2 and len(seen) == 3:
            line("OK ", "写盘层 6/6 rebuild-local 同锁（忙让路 rc=2 不改产物、闲写盘 %d 行、干跑不碰盘）"
                 % len(rep6.get("rows") or []))
        else:
            line("FAIL", "写盘层 6/6 rebuild-local 同锁",
                 "rc4=%s 产物未被忙写=%s rc5=%s rc6=%s 干跑不碰盘=%s rows=%s lock_gone=%s audited=%s"
                 % (rc4, j_busy == j_before, rc5, rc6, j_dry == j_free, len(rep6.get("rows") or []),
                    lock_gone2, len(seen)))
    except Exception as e:  # noqa: BLE001  自检里任何异常都算失败，但要把环境收拾回去
        ok = False
        line("FAIL", "写盘层自检异常", "%s: %s" % (type(e).__name__, e))
    finally:
        for k, v in saved.items():
            globals()[k] = v
        globals()["_audit_write"] = saved_audit
        _shutil.rmtree(d, ignore_errors=True)
    return ok


def main():
    ap = argparse.ArgumentParser(description="账号×关卡 可刷能力台账（数据源只读；**默认写盘**，干跑加 --no-write）")
    ap.add_argument("--build", action="store_true", help="全量重建（扫主机最近 N 天；默认写盘，干跑加 --no-write）")
    ap.add_argument("--update", action="store_true", help="增量（只扫最近 N 天并入 state；默认写盘，干跑加 --no-write）")
    ap.add_argument("--rebuild-local", action="store_true", help="从 state 重算报告（不联网；默认写盘，干跑加 --no-write）")
    ap.add_argument("--replay", metavar="RAWJSON", help="用已抓取的原始扫描 JSON 重建（离线；默认写盘）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="窗口天数（默认 %d）" % DEFAULT_DAYS)
    ap.add_argument("--update-days", type=int, default=DEFAULT_UPDATE_DAYS, help="增量扫描天数（默认 %d）" % DEFAULT_UPDATE_DAYS)
    ap.add_argument("--account", help="只看某个账号（读台账，不联网）")
    ap.add_argument("--json", action="store_true", help="打印机读 JSON")
    ap.add_argument("--no-write", action="store_true", help="不写盘（干跑；本工具默认真写盘，与其余工具默认 dry-run 相反）")
    ap.add_argument("--timeout", type=int, default=1500, help="SSH 超时秒（默认 1500）")
    ap.add_argument("--selftest", action="store_true", help="离线分类器自检")
    ap.add_argument("--check", action="store_true", help="连通性自检")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if a.check:
        return cmd_check()
    if a.replay:
        return cmd_replay(a.replay, a.days, a.no_write)
    if a.build:
        return cmd_build(a.days, a.no_write, a.timeout)
    if a.update:
        return cmd_update(a.days, a.update_days, a.no_write, a.timeout)
    if a.rebuild_local:
        return cmd_local(a.days, a.no_write)
    if a.account:
        return cmd_account(a.account, a.json)
    if a.json:
        return cmd_json_report()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
