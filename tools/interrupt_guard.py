#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""打断源治理 v1（只读）—— 游戏客户端强更 / 系统重启 的检测与班次保护。

背景（2026-09-28 两次真实打断，卡 t_634e7685）：
  ① 04:00 宿主计划重启（`DailyRestart0400` = `shutdown /r /f /t 30`）吞掉在跑队列；
  ② 游戏客户端更新（版本变化）会让 MAA 在跑时失败。
两者此前**都没有检测** ⇒ 本工具补上，并把结论折成一行 `INTERRUPT_RISK=` 供巡检 / 班前采用。

两块检测（全部只读）：
  ① 客户端版本：对**运行中**的实例逐台 `dumpsys package <ARK包> | versionName/lastUpdateTime`，
     与基线（runtime/interrupt_guard_state.json）比对；版本或包安装时间变化 ⇒ 告警 +
     建议暂停该实例发车（避免半途更新打断）。基线只在 `--record` 时写入（人工动作）。
  ② 系统重启：读宿主 / 本机的「重启类计划任务」（action 含 shutdown /r 或 Restart-Computer）
     的 NextRunTime/State + 待重启标记（Session Manager PendingFileRenameOperations /
     CBS RebootPending / WU RebootRequired）+ 在飞 MAA 数，评估：
       · 未决重启标记存在            ⇒ 系统重启风险（升级可能在任意维护动作时落地）
       · 下一次重启 ≤15 分钟         ⇒ 系统重启风险（发车必被吞）
       · 下一次重启 ≤45 分钟且宿主在飞 MAA>0 ⇒ 系统重启风险（会吞在飞任务；09-28 04:00 事故形态）
       · 下一次重启时刻落在班次窗口  ⇒ 系统重启风险 + **建议调整重启窗口**（只建议，不改计划任务）

只读保证：不启动/不关闭实例、不改计划任务、不动客户端、不发车、不写远端。
唯一的写动作 = `--record`（只写本机 runtime/interrupt_guard_state.json；`--dry-run` 连它也不写）。

用法：
  python tools/interrupt_guard.py                     # 全量检查（版本面 + 宿主/本机重启面）
  python tools/interrupt_guard.py --version-only      # 只查客户端版本面
  python tools/interrupt_guard.py --restart-only      # 只查重启面
  python tools/interrupt_guard.py --idx 13,14         # 版本面只查这些实例（默认=所有运行中实例）
  python tools/interrupt_guard.py --max 6             # 版本面最多查几台运行中实例（默认 6）
  python tools/interrupt_guard.py --record            # 记录/刷新版本基线（唯一写动作）
  python tools/interrupt_guard.py --record --dry-run  # 只打印将要写入什么，不落盘
  python tools/interrupt_guard.py --json              # 机器可读（不打印 INTERRUPT_RISK= 行）
  python tools/interrupt_guard.py --selftest          # 纯本地规则自检（不连网）

输出（文本模式）末行：
  INTERRUPT_RISK=无 | 客户端更新 | 系统重启 | 客户端更新+系统重启 | UNKNOWN:<原因>
退出码：0=无风险；1=有风险；2=无法判定（fail-closed，不当通过）。

环境变量：
  MAAORCH_SSH_KEY    默认 ~\.ssh\maaorch_target
  MAAORCH_SSH_HOST   默认 Administrator@100.79.173.69
  MAAORCH_ADB        默认 E:\MuMu Player 12\shell\adb.exe（宿主机上）
  MAAORCH_IG_STATE   版本基线文件覆盖（默认 runtime/interrupt_guard_state.json；演练/测试用）

阈值（可调，见 argparse）：--soon-min 45 / --block-min 15 / --window-lead-min 10
基线文件：runtime/interrupt_guard_state.json（schema=maaorch.interrupt_guard/1；runtime/ 已 gitignore）
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
RUNTIME = os.path.join(ROOT, "runtime")
STATE_FILE = os.path.join(RUNTIME, "interrupt_guard_state.json")
SCHEMA = "maaorch.interrupt_guard/1"

SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
ADB = os.environ.get("MAAORCH_ADB", r"E:\MuMu Player 12\shell\adb.exe")

# 班次窗口（口径 = KB 执行窗口 ∪ 发车点/守卫窗：
#   执行窗口 05:00–08:00 / 19:30–23:00（KB inc-2026-09-27-evening-orphan 红线）
#   发车点 05:00–05:33 / 19:30–20:03、守卫窗 05:00–05:40 / 19:20–20:20（PLAYBOOKS §0.2）
#   ⇒ 并集 05:00–08:00 / 19:20–23:00；计划重启（04:00 / 19:00）不在其中=按设计的安全窗）
SHIFT_WINDOWS = (("05:00", "08:00"), ("19:20", "23:00"))
SOON_MIN = 45        # 「重启临近」阈值：≤此分钟且宿主在飞 MAA>0 ⇒ 风险
BLOCK_MIN = 15       # 「重启即刻」阈值：≤此分钟 ⇒ 风险（不看在飞与否）
WINDOW_LEAD_MIN = 10  # 班次窗口交叠判定的提前量（分钟）

ARK_PKG_RE = re.compile(r"(arknights|hypergryph|yostar|txwy)", re.I)
# WU「装完更新自动重启」通道（按任务名；存在时只作提示级）：
#   \Microsoft\Windows\UpdateOrchestrator\Reboot_AC / Reboot_Battery（MusNotification /RunOnAC|RunOnBattery RebootDialog）
#   \Microsoft\Windows\WindowsUpdate\sih / sihboot（部分系统有）
WU_CHANNEL_RE = re.compile(r"^(Reboot_(AC|Battery)|sih|sihboot)$", re.I)
TS_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------- PS 执行（EncodedCommand 路线）

_PS_PREFIX = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; ")

# 重启面探针：重启类计划任务 + 待重启标记 + 在飞 MAA 数 + 上次开机/当前时刻。
# 注意：本 PS 会被 base64(UTF-16LE) 后塞进 ssh 命令行 ⇒ 保持紧凑（实测 ≤7KB b64 可用）。
_PS_RESTART = _PS_PREFIX + r"""
$out = @()
$maa = @(Get-CimInstance Win32_Process -Filter "Name='MAA.exe'" -ErrorAction SilentlyContinue)
$out += ("MAA_COUNT|" + $maa.Count)
foreach ($t in Get-ScheduledTask) {
  $acts = @($t.Actions | Where-Object { $_.Execute -match 'shutdown|Restart-Computer' })
  if ($acts.Count -eq 0 -and $t.TaskName -notmatch 'Restart|Reboot') { continue }
  $next = ''; $last = ''; $rc = ''
  try { $i = Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath } catch { $i = $null }
  if ($i) {
    if ($i.NextRunTime) { $next = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') }
    if ($i.LastRunTime) { $last = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') }
    $rc = [string]$i.LastTaskResult
  }
  $act = (($acts | ForEach-Object { ([string]$_.Execute) + ' ' + ([string]$_.Arguments) }) -join ' ; ')
  $out += ("TASK|" + $t.TaskName + "|" + [string]$t.State + "|" + $next + "|" + $last + "|" + $rc + "|" + $act)
}
$sm = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -ErrorAction SilentlyContinue
$pfr = 0
if ($sm -and $sm.PendingFileRenameOperations) { $pfr = @($sm.PendingFileRenameOperations).Count }
$out += ("PFR_COUNT|" + $pfr)
$out += ("CBS_PENDING|" + (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'))
$out += ("CBS_INPROGRESS|" + (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootInProgress'))
$out += ("WU_REQUIRED|" + (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'))
try { $b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss') } catch { $b = '' }
$out += ("BOOT|" + $b)
$out += ("NOW|" + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))
$out += "IG-END"
$out
"""

# 版本面探针：VMM 进程 → 端口 → adb → ARK 包 versionName/lastUpdateTime。
_PS_VERSION = _PS_PREFIX + r"""
$adb = '__ADB__'
$idxs = @(__IDX__)
$vmm = @(Get-CimInstance Win32_Process -Filter "Name='MuMuVMMHeadless.exe'" -ErrorAction SilentlyContinue)
$run = @()
foreach ($p in $vmm) {
  $m = [regex]::Match([string]$p.CommandLine, 'MuMuPlayer-12\.0-(\d+)')
  if ($m.Success) {
    $idx = [int]$m.Groups[1].Value
    $ports = @()
    try { $ports = @(Get-NetTCPConnection -State Listen -OwningProcess $p.ProcessId -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort) } catch {}
    $run += [pscustomobject]@{ idx = $idx; pid = $p.ProcessId; ports = ($ports -join ',') }
  }
}
foreach ($r in $run) { Write-Output ("RUN|" + $r.idx + "|" + $r.pid + "|" + $r.ports) }
$n = 0
foreach ($r in $run) {
  if ($idxs.Count -gt 0 -and ($idxs -notcontains $r.idx)) { continue }
  if ($n -ge __MAX__) { break }
  $port = 0
  if ($r.ports) { $port = [int]($r.ports -split ',')[0] }
  if ($port -eq 0) { if ($r.idx -le 31) { $port = 16384 + 32 * $r.idx } else { $port = 16388 + 32 * ($r.idx - 32) } }
  $serial = "127.0.0.1:" + $port
  Write-Output ("TRY|" + $r.idx + "|" + $serial)
  & $adb connect $serial | Out-Null
  $state = (& $adb -s $serial get-state 2>&1 | Out-String).Trim()
  Write-Output ("STATE|" + $r.idx + "|" + $serial + "|" + $state)
  if ($state -ne 'device') { $n++; continue }
  $raw = (& $adb -s $serial shell "pm list packages | grep -iE 'arknights|hypergryph|yostar|txwy'" 2>$null | Out-String)
  if (-not $raw.Trim()) { $raw = (& $adb -s $serial shell "pm list packages" 2>$null | Out-String) }
  foreach ($ln in ($raw -split "`n")) {
    $ln = $ln.Trim()
    if (-not $ln -or $ln -notmatch 'arknights|hypergryph|yostar|txwy') { continue }
    $pkg = $ln -replace '^package:', ''
    $dump = (& $adb -s $serial shell "dumpsys package $pkg" 2>$null | Out-String)
    foreach ($dl in ($dump -split "`n")) {
      $dl = $dl.Trim()
      if ($dl -match '^(versionName|lastUpdateTime|firstInstallTime)=') {
        Write-Output ("VER|" + $r.idx + "|" + $serial + "|" + $pkg + "|" + $dl)
      }
    }
  }
  $n++
}
Write-Output "IG-END"
"""


def run_ps(script: str, host: str | None = None, timeout: int = 180) -> str:
    """跑一段 PowerShell：host=None → 本机；host 非空 → ssh 到该主机（EncodedCommand，UTF-16LE）。"""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    if host:
        cmd = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
               host, "chcp 65001 >nul & powershell -NoProfile -EncodedCommand " + b64]
    else:
        cmd = ["powershell", "-NoProfile", "-EncodedCommand", b64]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def _b64_len(script: str) -> int:
    return len(base64.b64encode(script.encode("utf-16-le")))


# ---------------------------------------------------------------- 解析（纯函数，供自检）

def parse_restart_survey(text: str) -> dict:
    """PS 输出 → {tasks[], pending{}, in_flight, boot, now, end}；无 IG-END ⇒ RuntimeError。"""
    out = {"tasks": [], "pending": {"pfr": 0, "cbs_pending": False, "cbs_inprogress": False,
                                    "wu_required": False}, "in_flight": 0, "boot": None, "now": None}
    end = False
    for ln in text.replace("\r", "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln == "IG-END":
            end = True
            break
        parts = ln.split("|")
        k = parts[0]
        if k == "MAA_COUNT":
            try:
                out["in_flight"] = int(parts[1])
            except (ValueError, IndexError):
                pass
        elif k == "TASK" and len(parts) >= 7:
            out["tasks"].append({"name": parts[1], "state": parts[2],
                                 "next": parts[3] or None, "last": parts[4] or None,
                                 "rc": parts[5], "action": "|".join(parts[6:])})
        elif k == "PFR_COUNT":
            try:
                out["pending"]["pfr"] = int(parts[1])
            except (ValueError, IndexError):
                pass
        elif k in ("CBS_PENDING", "CBS_INPROGRESS", "WU_REQUIRED"):
            key = {"CBS_PENDING": "cbs_pending", "CBS_INPROGRESS": "cbs_inprogress",
                   "WU_REQUIRED": "wu_required"}[k]
            out["pending"][key] = (len(parts) > 1 and parts[1].strip().lower() == "true")
        elif k in ("BOOT", "NOW"):
            out[k.lower()] = parts[1] if len(parts) > 1 and parts[1] else None
    if not end:
        raise RuntimeError("探针无 IG-END（ssh 不通 / PowerShell 异常）")
    out["wu_channels"] = [{"name": t["name"], "state": t["state"], "next": t.get("next")}
                          for t in out["tasks"] if WU_CHANNEL_RE.match(t["name"] or "")]
    return out


def parse_version_survey(text: str) -> dict:
    """PS 输出 → {running[], instances{idx:{serial,state,pkgs{pkg:{versionName,lastUpdateTime,firstInstallTime}}}}}。"""
    res = {"running": [], "instances": {}, "end": False}
    for ln in text.replace("\r", "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln == "IG-END":
            res["end"] = True
            break
        parts = ln.split("|")
        k = parts[0]
        if k == "RUN" and len(parts) >= 4:
            try:
                res["running"].append({"idx": int(parts[1]), "pid": parts[2], "ports": parts[3]})
            except ValueError:
                continue
        elif k == "STATE" and len(parts) >= 4:
            try:
                idx = int(parts[1])
            except ValueError:
                continue
            res["instances"].setdefault(idx, {"idx": idx, "serial": parts[2], "state": parts[3],
                                              "pkgs": {}})
        elif k == "VER" and len(parts) >= 5:
            try:
                idx = int(parts[1])
            except ValueError:
                continue
            inst = res["instances"].setdefault(idx, {"idx": idx, "serial": parts[2],
                                                     "state": "unknown", "pkgs": {}})
            pkg, field = parts[3], parts[4]
            if "=" in field:
                fk, fv = field.split("=", 1)
                inst["pkgs"].setdefault(pkg, {})[fk.strip()] = fv.strip()
    if not res["end"]:
        raise RuntimeError("版本探针无 IG-END（ssh 不通 / PowerShell 异常）")
    return res


def parse_ts(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(s, TS_FMT)
    except (TypeError, ValueError):
        return None


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_shift_window(t: dt.datetime, windows=SHIFT_WINDOWS, lead_min: int = WINDOW_LEAD_MIN) -> str | None:
    """时刻落在班次窗口（含提前量）⇒ 返回窗口名；否则 None。"""
    tm = t.hour * 60 + t.minute
    for a, b in windows:
        if _hhmm(a) - lead_min <= tm <= _hhmm(b):
            return "%s–%s" % (a, b)
    return None


def is_restart_task(task: dict) -> bool:
    """重启类任务：action 是真正重启机器的命令（shutdown /r* 、Restart-Computer、WU 完成重启对话框）。"""
    act = (task.get("action") or "").lower()
    if "restart-computer" in act:
        return True
    if "musnotification.exe" in act and re.search(r"/runon(ac|battery)", act):
        return True  # WU UpdateOrchestrator Reboot_AC / Reboot_Battery：更新装完触发重启对话框
    if "shutdown" in act and re.search(r"(^|\s)/r(\s|$|/)", act):
        return True
    return False


def restart_evaluate(surveys: dict, now: dt.datetime | None = None, soon_min: int = SOON_MIN,
                     block_min: int = BLOCK_MIN, lead_min: int = WINDOW_LEAD_MIN) -> dict:
    """surveys = {"host": survey|None, "local": survey|None} → {signals[], risk, next[], errors[]}。

    signal = {kind, level(risk/advisory), machine, detail, task?, minutes?, window?}
    """
    now = now or dt.datetime.now()
    signals: list = []
    nexts: list = []
    for machine, s in surveys.items():
        if s is None:
            continue
        pend = s.get("pending") or {}
        marks = []
        if pend.get("pfr", 0):
            marks.append("PendingFileRename×%s" % pend["pfr"])
        if pend.get("cbs_pending"):
            marks.append("CBS RebootPending")
        if pend.get("cbs_inprogress"):
            marks.append("CBS RebootInProgress")
        if pend.get("wu_required"):
            marks.append("WU RebootRequired")
        if marks:
            signals.append({"kind": "pending_reboot", "level": "risk", "machine": machine,
                            "detail": "未决重启标记存在（%s）——重启可能随时落地" % "、".join(marks)})
        for ch in s.get("wu_channels") or []:
            if ch.get("state") != "Disabled":
                signals.append({"kind": "wu_reboot_channel", "level": "advisory", "machine": machine,
                                "detail": "WU 完成重启通道已启用：%s（state=%s，next=%s）——更新装完会自动重启，视情况暂停发车"
                                          % (ch.get("name"), ch.get("state"), ch.get("next") or "-")})
        for t in s.get("tasks") or []:
            if t.get("state") == "Disabled" or not is_restart_task(t):
                continue
            nt = parse_ts(t.get("next") or "")
            if not nt:
                continue
            mins = (nt - now).total_seconds() / 60.0
            if mins < -1:
                continue
            nexts.append({"machine": machine, "task": t.get("name"), "next": t["next"],
                          "minutes": round(mins, 1), "state": t.get("state")})
            win = in_shift_window(nt, lead_min=lead_min)
            if win:
                signals.append({"kind": "window_overlap", "level": "risk", "machine": machine,
                                "task": t.get("name"), "minutes": round(mins, 1), "window": win,
                                "detail": "计划重启 %s@%s 落在班次窗口 %s（%.0f 分钟后）——"
                                          "建议把该计划任务挪出班次窗口（只建议，不改计划任务）"
                                          % (t.get("name"), t["next"], win, mins)})
            if mins <= block_min:
                signals.append({"kind": "restart_imminent", "level": "risk", "machine": machine,
                                "task": t.get("name"), "minutes": round(mins, 1),
                                "detail": "%s 计划重启 %s 在 %.0f 分钟内（%s）——现在发车必被吞"
                                          % (machine, t.get("name"), mins, t["next"])})
            elif machine == "host" and mins <= soon_min and (s.get("in_flight") or 0) > 0:
                signals.append({"kind": "restart_soon_active", "level": "risk", "machine": machine,
                                "task": t.get("name"), "minutes": round(mins, 1),
                                "detail": "宿主 %.0f 分钟后计划重启（%s），且有 %d 个 MAA 在飞——"
                                          "在飞任务会被吞（09-28 04:00 事故形态）"
                                          % (mins, t["next"], s.get("in_flight"))})
    risk = [x for x in signals if x.get("level") == "risk"]
    nexts.sort(key=lambda x: x["minutes"])
    return {"signals": signals, "risk_signals": risk, "next": nexts, "has_risk": bool(risk)}


# ---------------------------------------------------------------- 版本面

def version_state_load(path: str | None = None) -> dict:
    p = path or STATE_FILE
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("versions"), dict):
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"schema": SCHEMA, "updated": None, "versions": {}}


def vkey(idx: int, pkg: str) -> str:
    return "%d|%s" % (idx, pkg)


def version_compare(instances: dict, state: dict) -> dict:
    """instances = parse_version_survey()['instances'] → {rows[], changed[], new[], same[], unreadable[]}。"""
    rows, changed, new, same, unreadable = [], [], [], [], []
    for idx, inst in sorted(instances.items()):
        if inst.get("state") != "device":
            unreadable.append({"idx": idx, "serial": inst.get("serial"), "reason": inst.get("state")})
            continue
        if not inst.get("pkgs"):
            unreadable.append({"idx": idx, "serial": inst.get("serial"), "reason": "未发现 ARK 包"})
            continue
        for pkg, fields in sorted(inst["pkgs"].items()):
            cur = {"idx": idx, "pkg": pkg, "serial": inst.get("serial"),
                   "versionName": fields.get("versionName"),
                   "lastUpdateTime": fields.get("lastUpdateTime"),
                   "firstInstallTime": fields.get("firstInstallTime")}
            base = (state.get("versions") or {}).get(vkey(idx, pkg))
            if not cur["versionName"]:
                cur["status"] = "unreadable"
                unreadable.append(cur)
            elif not base:
                cur["status"] = "new"
                new.append(cur)
            elif (cur["versionName"] != base.get("versionName")
                  or cur["lastUpdateTime"] != base.get("lastUpdateTime")):
                cur["status"] = "changed"
                cur["baseline"] = {"versionName": base.get("versionName"),
                                   "lastUpdateTime": base.get("lastUpdateTime")}
                changed.append(cur)
            else:
                cur["status"] = "same"
                same.append(cur)
            rows.append(cur)
    return {"rows": rows, "changed": changed, "new": new, "same": same, "unreadable": unreadable}


def state_apply(state: dict, rows: list, now: dt.datetime | None = None) -> dict:
    """把可读行并入 state（返回新 state；不落盘）。"""
    now = now or dt.datetime.now()
    out = {"schema": SCHEMA, "updated": now.strftime(TS_FMT),
           "versions": dict((state or {}).get("versions") or {})}
    for r in rows:
        if r.get("status") == "unreadable" or not r.get("versionName"):
            continue
        k = vkey(r["idx"], r["pkg"])
        prev = out["versions"].get(k) or {}
        out["versions"][k] = {"idx": r["idx"], "pkg": r["pkg"],
                              "versionName": r["versionName"],
                              "lastUpdateTime": r.get("lastUpdateTime"),
                              "firstInstallTime": r.get("firstInstallTime"),
                              "first_seen": prev.get("first_seen") or now.strftime(TS_FMT),
                              "recorded_at": now.strftime(TS_FMT)}
    return out


def state_write(state: dict, path: str | None = None) -> str:
    """原子写（.tmp → os.replace）。"""
    p = path or STATE_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def version_check(idxs: list | None = None, maxn: int = 6, timeout: int = 240,
                  record: bool = False, dry_run: bool = False, state_path: str | None = None,
                  host: str = SSH_HOST) -> dict:
    """版本面全流程（只读；record=True 时写基线）。返回结构化结果。"""
    state_path = state_path or os.environ.get("MAAORCH_IG_STATE") or STATE_FILE
    idx_lit = ",".join(str(int(i)) for i in (idxs or []))
    ps = _PS_VERSION.replace("__ADB__", ADB.replace("\\", "\\\\")).replace("__IDX__", idx_lit).replace("__MAX__", str(int(maxn)))
    if _b64_len(ps) > 7000:
        raise RuntimeError("版本探针脚本过长（%d b64）" % _b64_len(ps))
    text = run_ps(ps, host=host, timeout=timeout)
    survey = parse_version_survey(text)
    state = version_state_load(state_path)
    cmp = version_compare(survey["instances"], state)
    res = {"survey": survey, "compare": cmp, "state_file": state_path or STATE_FILE,
           "baseline_updated": False, "record_dry_run": bool(record and dry_run)}
    if record and not dry_run:
        new_state = state_apply(state, cmp["rows"])
        res["record_path"] = state_write(new_state, state_path)
        res["baseline_updated"] = True
    elif record and dry_run:
        res["record_preview"] = state_apply(state, cmp["rows"])["versions"]
    return res


# ---------------------------------------------------------------- 重启面

def restart_check(scope=("host", "local"), timeout: int = 240, host: str = SSH_HOST,
                  soon_min: int = SOON_MIN, block_min: int = BLOCK_MIN,
                  lead_min: int = WINDOW_LEAD_MIN) -> dict:
    """重启面全流程（只读）。scope: host / local。"""
    surveys, errors = {}, []
    for m in scope:
        try:
            if m == "host":
                text = run_ps(_PS_RESTART, host=host, timeout=timeout)
            else:
                text = run_ps(_PS_RESTART, host=None, timeout=timeout)
            surveys[m] = parse_restart_survey(text)
        except Exception as e:  # noqa: BLE001
            surveys[m] = None
            errors.append("%s: %s: %s" % (m, type(e).__name__, str(e)[:200]))
    ev = restart_evaluate(surveys, soon_min=soon_min, block_min=block_min, lead_min=lead_min)
    ev["surveys"] = surveys
    ev["errors"] = errors
    return ev


# ---------------------------------------------------------------- 汇总 & 输出

def build_risk(version_res: dict | None, restart_res: dict | None, record: bool = False) -> dict:
    """→ {interrupt_risk, reasons[], hard_error}。record=True（真写基线）时不再提示"无基线"。"""
    parts, reasons = [], []
    if version_res is not None:
        cmp = version_res.get("compare") or {}
        for c in cmp.get("changed") or []:
            b = c.get("baseline") or {}
            reasons.append("客户端更新：idx%s %s %s→%s（lastUpdate %s→%s）——"
                           "建议暂停该实例发车，确认可正常进游戏后 --record 刷新基线"
                           % (c["idx"], c["pkg"], b.get("versionName"), c.get("versionName"),
                              b.get("lastUpdateTime"), c.get("lastUpdateTime")))
        if cmp.get("changed"):
            parts.append("客户端更新")
        if not record:
            for n in cmp.get("new") or []:
                reasons.append("注意：idx%s %s 无基线（首次见到，建议 --record 建基线）"
                               % (n["idx"], n["pkg"]))
    if restart_res is not None:
        for s in restart_res.get("risk_signals") or []:
            reasons.append("系统重启：%s" % s.get("detail"))
        for s in restart_res.get("signals") or []:
            if s.get("level") != "risk":
                reasons.append("提示：%s" % s.get("detail"))
        if restart_res.get("risk_signals"):
            parts.append("系统重启")
    hard_error = None
    if version_res is not None and version_res.get("error"):
        hard_error = version_res["error"]
    if restart_res is not None:
        errs = restart_res.get("errors") or []
        host_err = [e for e in errs if e.startswith("host:")]
        if host_err:
            hard_error = "；".join(host_err)
    if hard_error:
        risk = "UNKNOWN:%s" % hard_error
    elif parts:
        risk = "+".join(parts)
    else:
        risk = "无"
    return {"interrupt_risk": risk, "reasons": reasons, "hard_error": hard_error}


def print_report(res: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    v, r = res.get("version"), res.get("restart")
    print("打断源治理（只读）  %s" % res["generated_at"])
    if v:
        cmp = v["compare"]
        rows = cmp["rows"]
        print("[版本] 运行中实例 %d 台（读得 %d 项）：一致 %d / 变化 %d / 新 %d / 不可读 %d"
              % (len(v["survey"]["running"]), len(rows), len(cmp["same"]), len(cmp["changed"]),
                 len(cmp["new"]), len(cmp["unreadable"])))
        for c in cmp["rows"]:
            b = c.get("baseline") or {}
            extra = ""
            if c["status"] == "changed":
                extra = "  ← 变化（基线 %s / %s）" % (b.get("versionName"), b.get("lastUpdateTime"))
            elif c["status"] == "new":
                extra = "  （无基线）"
            print("        idx=%-3s %-28s %-12s lastUpdate=%s %s"
                  % (c["idx"], c["pkg"], c.get("versionName") or "?", c.get("lastUpdateTime") or "?", extra))
        for u in cmp["unreadable"]:
            print("        idx=%-3s 不可读（%s）" % (u.get("idx"), u.get("reason")))
        if v.get("baseline_updated"):
            print("        基线已更新 → %s" % v.get("record_path"))
        elif v.get("record_dry_run"):
            print("        [dry-run] 基线未落盘（将写入 %d 项）" % len(v.get("record_preview") or {}))
    if r:
        for m, s in (r.get("surveys") or {}).items():
            if not s:
                continue
            pend = s.get("pending") or {}
            marks = [k for k, on in (("PFR", pend.get("pfr")), ("CBS", pend.get("cbs_pending")),
                                     ("CBS_IP", pend.get("cbs_inprogress")), ("WU", pend.get("wu_required"))) if on]
            tasks = [t for t in (s.get("tasks") or []) if t.get("state") != "Disabled" and is_restart_task(t)]
            print("[重启] %-5s 在飞 MAA=%s boot=%s；待重启标记=%s；重启类任务 %d 条"
                  % (m, s.get("in_flight"), s.get("boot") or "?", "、".join(marks) or "无", len(tasks)))
            for t in tasks:
                print("        %-24s %-8s next=%s last=%s rc=%s [%s]"
                      % (t["name"], t["state"], t.get("next") or "-", t.get("last") or "-",
                         t.get("rc") or "-", t.get("action")))
            wu = [t for t in (s.get("tasks") or []) if WU_CHANNEL_RE.match(t.get("name") or "")]
            if wu:
                print("        WU 完成重启通道：%s" % "、".join("%s=%s" % (t["name"], t["state"]) for t in wu))
        for nr in r.get("next") or []:
            print("        最近：%-5s %s @ %s（%.0f 分钟后）" % (nr["machine"], nr["task"], nr["next"], nr["minutes"]))
        for e in r.get("errors") or []:
            print("        [错误] %s" % e)
    for why in res.get("reasons") or []:
        print("  - %s" % why)
    print("INTERRUPT_RISK=%s" % res["interrupt_risk"])


# ---------------------------------------------------------------- 自检

def selftest() -> int:
    bad = 0

    def ck(label, got, want):
        nonlocal bad
        ok = got == want
        bad += 0 if ok else 1
        print("%s %-38s got=%r want=%r" % ("PASS" if ok else "FAIL", label, got, want))

    # 班次窗口
    t = lambda h, m: dt.datetime(2026, 9, 28, h, m)  # noqa: E731
    ck("window 05:20 in", bool(in_shift_window(t(5, 20))), True)
    ck("window 04:00 out", in_shift_window(t(4, 0)), None)
    ck("window 19:25 in(lead10)", bool(in_shift_window(t(19, 25))), True)
    ck("window 19:00 out(lead10)", in_shift_window(t(19, 0)), None)
    ck("window 22:00 in(执行窗)", bool(in_shift_window(t(22, 0))), True)
    ck("window 23:30 out", in_shift_window(t(23, 30)), None)

    # 重启任务识别
    ck("restart /r", is_restart_task({"action": r"C:\Windows\System32\shutdown.exe /r /f /t 30"}), True)
    ck("restart /s out", is_restart_task({"action": r"shutdown /s /t 0"}), False)
    ck("restart-computer", is_restart_task({"action": "Restart-Computer -Force"}), True)
    ck("restart musnotification", is_restart_task(
        {"action": r"%systemroot%\system32\MusNotification.exe /RunOnAC RebootDialog"}), True)
    ck("reboot-only name out", is_restart_task({"action": "cmd /c echo hi"}), False)

    # PS 解析
    sample = ("MAA_COUNT|2\n"
              "TASK|DailyRestart0400|Ready|2026-09-29 04:00:00|2026-09-28 04:00:00|0|shutdown /r /f /t 30\n"
              "TASK|Reboot_AC|Disabled||11/30/1999 00:00:00|267011|\n"
              "PFR_COUNT|3\nCBS_PENDING|False\nCBS_INPROGRESS|False\nWU_REQUIRED|False\n"
              "BOOT|2026-09-28 04:01:18\nNOW|2026-09-28 09:50:33\nIG-END\n")
    s = parse_restart_survey(sample)
    ck("parse in_flight", s["in_flight"], 2)
    ck("parse tasks n", len(s["tasks"]), 2)
    ck("parse pfr", s["pending"]["pfr"], 3)
    ck("parse boot", s["boot"], "2026-09-28 04:01:18")
    ck("parse wu channels", [c["name"] for c in s["wu_channels"]], ["Reboot_AC"])

    vsample = ("RUN|13|35388|16800,7555\n"
               "STATE|13|127.0.0.1:16800|device\n"
               "VER|13|127.0.0.1:16800|com.hypergryph.arknights|versionName=2.7.71\n"
               "VER|13|127.0.0.1:16800|com.hypergryph.arknights|lastUpdateTime=2026-09-22 23:46:46\n"
               "IG-END\n")
    v = parse_version_survey(vsample)
    ck("parse ver name", v["instances"][13]["pkgs"]["com.hypergryph.arknights"]["versionName"], "2.7.71")

    # 版本比对
    st = {"schema": SCHEMA, "versions": {
        vkey(13, "com.hypergryph.arknights"): {"versionName": "2.7.70", "lastUpdateTime": "2026-09-01 00:00:00"},
        vkey(14, "com.hypergryph.arknights"): {"versionName": "2.7.71", "lastUpdateTime": "2026-09-22 23:46:46"}}}
    inst = {13: {"idx": 13, "serial": "s", "state": "device",
                 "pkgs": {"com.hypergryph.arknights": {"versionName": "2.7.71",
                                                       "lastUpdateTime": "2026-09-22 23:46:46"}}},
            14: {"idx": 14, "serial": "s2", "state": "device",
                 "pkgs": {"com.hypergryph.arknights": {"versionName": "2.7.71",
                                                       "lastUpdateTime": "2026-09-22 23:46:46"}}},
            15: {"idx": 15, "serial": "s3", "state": "device",
                 "pkgs": {"com.hypergryph.arknights": {"versionName": "2.7.71",
                                                       "lastUpdateTime": "2026-09-22 23:46:46"}}},
            16: {"idx": 16, "serial": "s4", "state": "offline", "pkgs": {}}}
    c = version_compare(inst, st)
    ck("cmp changed n", len(c["changed"]), 1)
    ck("cmp same n", len(c["same"]), 1)
    ck("cmp new n", len(c["new"]), 1)
    ck("cmp unreadable n", len(c["unreadable"]), 1)

    # 重启评估：临近+在飞 / 仅临近无在飞 / 即刻 / 待重启标记 / 窗口交叠
    now = dt.datetime(2026, 9, 28, 10, 0)
    s_soon = {"pending": {}, "in_flight": 1,
              "tasks": [{"name": "DailyRestart0400", "state": "Ready",
                         "next": "2026-09-28 10:30:00", "action": "shutdown /r /f /t 30"}]}
    ev = restart_evaluate({"host": s_soon}, now=now)
    ck("eval soon+active risk", any(x["kind"] == "restart_soon_active" for x in ev["risk_signals"]), True)
    s_idle = dict(s_soon, in_flight=0)
    ev = restart_evaluate({"host": s_idle}, now=now)
    ck("eval soon idle no risk", ev["has_risk"], False)
    s_imm = {"pending": {}, "in_flight": 0,
             "tasks": [{"name": "DailyRestart0400", "state": "Ready",
                        "next": "2026-09-28 10:05:00", "action": "shutdown /r /f /t 30"}]}
    ck("eval imminent risk", restart_evaluate({"host": s_imm}, now=now)["has_risk"], True)
    s_pend = {"pending": {"pfr": 2}, "in_flight": 0, "tasks": []}
    ck("eval pending risk", restart_evaluate({"host": s_pend}, now=now)["has_risk"], True)
    s_win = {"pending": {}, "in_flight": 0,
             "tasks": [{"name": "DailyRestart0400", "state": "Ready",
                        "next": "2026-09-28 19:30:00", "action": "shutdown /r /f /t 30"}]}
    ev = restart_evaluate({"host": s_win}, now=now)
    ck("eval window overlap risk", any(x["kind"] == "window_overlap" for x in ev["risk_signals"]), True)
    s_dis = {"pending": {}, "in_flight": 5,
             "tasks": [{"name": "Reboot_AC", "state": "Disabled",
                        "next": "2026-09-28 10:10:00", "action": "shutdown /r"}]}
    ck("eval disabled skip", restart_evaluate({"host": s_dis}, now=now)["has_risk"], False)
    s_wu = {"pending": {}, "in_flight": 0, "tasks": [],
            "wu_channels": [{"name": "Reboot_AC", "state": "Ready", "next": "2026-09-28 21:00:00"}]}
    ev_wu = restart_evaluate({"host": s_wu}, now=now)
    ck("eval wu channel kind", [x["kind"] for x in ev_wu["signals"]], ["wu_reboot_channel"])
    ck("eval wu channel not risk", ev_wu["has_risk"], False)
    ck("risk wu advisory none", build_risk(None, ev_wu)["interrupt_risk"], "无")
    ck("risk wu advisory hint", any(r.startswith("提示：") for r in build_risk(None, ev_wu)["reasons"]), True)

    # 汇总
    vres = {"compare": {"changed": [{"idx": 13, "pkg": "p", "versionName": "9",
                                     "lastUpdateTime": "t", "baseline": {"versionName": "8",
                                                                         "lastUpdateTime": "t0"}}],
                        "new": []}}
    rres = {"risk_signals": [{"detail": "x"}], "errors": []}
    ck("risk both", build_risk(vres, rres)["interrupt_risk"], "客户端更新+系统重启")
    ck("risk none", build_risk({"compare": {"changed": [], "new": []}}, {"risk_signals": [], "errors": []})["interrupt_risk"], "无")
    ck("risk unknown", build_risk(None, {"risk_signals": [], "errors": ["host: RuntimeError: x"]})["interrupt_risk"].startswith("UNKNOWN:"), True)

    # state 落盘（临时目录）
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "st.json")
        st2 = state_apply({"versions": {}}, c["rows"], now=now)
        state_write(st2, p)
        back = version_state_load(p)
        ck("state roundtrip", back["versions"][vkey(15, "com.hypergryph.arknights")]["versionName"], "2.7.71")
        ck("state keeps first_seen", "first_seen" in back["versions"][vkey(15, "com.hypergryph.arknights")], True)

    print("selftest: %s (%d 失败)" % ("OK" if not bad else "FAIL", bad))
    return 0 if not bad else 1


# ---------------------------------------------------------------- main

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="打断源治理（只读）：客户端强更 / 系统重启检测")
    ap.add_argument("--version-only", action="store_true", help="只查客户端版本面")
    ap.add_argument("--restart-only", action="store_true", help="只查重启面")
    ap.add_argument("--idx", help="版本面实例下标（逗号分隔；默认=所有运行中实例）")
    ap.add_argument("--max", type=int, default=6, help="版本面最多查几台运行中实例（默认 6）")
    ap.add_argument("--record", action="store_true", help="记录/刷新版本基线（唯一写动作）")
    ap.add_argument("--state-file", default=None, help="版本基线文件（默认 runtime/interrupt_guard_state.json）")
    ap.add_argument("--dry-run", action="store_true", help="连 --record 也不落盘（只打印）")
    ap.add_argument("--scope", default="host,local", help="重启面范围：host / local / host,local（默认 both）")
    ap.add_argument("--soon-min", type=int, default=SOON_MIN)
    ap.add_argument("--block-min", type=int, default=BLOCK_MIN)
    ap.add_argument("--window-lead-min", type=int, default=WINDOW_LEAD_MIN)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    started = dt.datetime.now()
    res = {"tool": "interrupt_guard", "schema": SCHEMA,
           "generated_at": started.strftime(TS_FMT), "mode": "check"}
    version_res, restart_res = None, None
    if not a.restart_only:
        idxs = [x for x in (a.idx or "").split(",") if x.strip()] if a.idx else None
        try:
            version_res = version_check(idxs=idxs, maxn=a.max, timeout=a.timeout,
                                        record=a.record, dry_run=a.dry_run,
                                        state_path=a.state_file)
        except Exception as e:  # noqa: BLE001
            version_res = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        res["version"] = version_res
    if not a.version_only:
        scope = tuple(x for x in (a.scope or "").split(",") if x.strip()) or ("host",)
        try:
            restart_res = restart_check(scope=scope, timeout=a.timeout, soon_min=a.soon_min,
                                        block_min=a.block_min, lead_min=a.window_lead_min)
        except Exception as e:  # noqa: BLE001
            restart_res = {"errors": ["host: %s: %s" % (type(e).__name__, str(e)[:200])]}
        res["restart"] = restart_res
    built = build_risk(version_res, restart_res, record=bool(a.record and not a.dry_run))
    res.update(built)
    if a.record and not a.dry_run:
        res["mode"] = "record"
    print_report(res, a.json)
    if built["hard_error"]:
        return 2
    return 1 if built["interrupt_risk"] != "无" else 0


if __name__ == "__main__":
    sys.exit(main())
