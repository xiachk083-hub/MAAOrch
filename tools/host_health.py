#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""主机健康巡检（只读）—— CPU / 内存 / 磁盘 / 进程 / 温度 + 趋势 + 预警。

盯的是那台 24×7 跑模拟器的生产主机（AUTO-MAS 主机 100.79.173.69）。
现有工具各有盲区：host_perf 只做内存告警、storage_waterline 只算存储水位、
host_watchdog 只看「活没活」。本工具补上「**一根一行的取数线**」，供巡检直接摘：

    HOST_HEALTH=cpu=20%,mem=31.7%,diskC=41.7G,diskE=118.3G,emu=4,maa=3,ssh=ok,temp=na,memFree=44607M,rateE=na,alerts=none,status=OK

只读约束：
  * 远端只跑**一条只读 PowerShell**（CIM 查询 / Get-Process / adb devices）；
    不留文件、不动配置、**不装任何第三方监控软件**；
  * 本机只追加 runtime/host_health.jsonl（趋势数据源，可 --no-log 关掉）。

阈值（集中在本文件顶部；取数依据与处置建议见 docs/HOST_HEALTH.md）：
  E: < 20G ⇒ CRIT（本项目红线）；< 60G ⇒ WARN
  C: < 15G ⇒ CRIT；< 35G ⇒ WARN
  内存已用 > 90% ⇒ CRIT；> 85% ⇒ WARN；可用 < free_mem_alert_mb（watchdog.ini，默认 8192MB）⇒ WARN
  CPU ≥ 95%（单采样）⇒ WARN
  温度 ≥ 92℃ ⇒ CRIT；≥ 85℃ ⇒ WARN（WMI 可得时；不可得则 temp=na，不告警）
  E: 缩水 ≤ -2GB/h 或预计 24h 内跌破 20G ⇒ WARN
  emu>0 但 adb 在线 = 0 ⇒ WARN（模拟器进程在、设备链路断）

用法：
  python tools/host_health.py                # 详表（含磁盘/内存趋势 + 进程窗口）+ 末行 HOST_HEALTH=
  python tools/host_health.py --check        # 紧凑（巡检 cron 用），末行 HOST_HEALTH=
  python tools/host_health.py --json         # 机器可读（含 line 字段）
  python tools/host_health.py --selftest     # 纯本地逻辑自检（不连网）
选项：--host HOST --key KEY --timeout S --window-h H --no-log --history PATH --notify
退出码：0 全绿；2 有 WARN；3 有 CRIT；1 取数失败（ssh 不通/输出不可解析 ⇒ 行内 ssh=down）
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

# Windows：不带这个标志，子进程（ssh）会**闪一个 cmd 黑窗** ✗
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROJ = pathlib.Path(__file__).resolve().parents[1]
CONF = PROJ / "runtime" / "watchdog.ini"
HISTORY = PROJ / "runtime" / "host_health.jsonl"
STATE = PROJ / "runtime" / "host_health_state.json"

DEFAULT_HOST = "100.79.173.69"
DEFAULT_KEY = str(pathlib.Path.home() / ".ssh" / "maaorch_target")

# ---------------------------------------------------------------- 阈值
THR = {
    "mem_used_warn_pct": 85.0,
    "mem_used_crit_pct": 90.0,
    "mem_free_warn_mb": 8192,          # 与 watchdog.ini [perf] free_mem_alert_mb 保持一致
    "cpu_warn_pct": 95.0,
    "diskC_warn_gb": 35.0,             # 一次实例导出会在 C 落 ~24GB（见 2026-09-27_storage_policy）
    "diskC_crit_gb": 15.0,
    "diskE_warn_gb": 60.0,             # 一个 MuMu 实例 ≈ 29-32GB（建实例前必须留得下）
    "diskE_crit_gb": 20.0,             # 任务红线：E 盘 <20G ⇒ 预警
    "temp_warn_c": 85.0,
    "temp_crit_c": 92.0,
    "rate_warn_gb_h": -2.0,            # E 盘缩水速度（负=在掉）；稳态应 ≈ 0 GB/h
    "eta_warn_h": 24.0,                # 按当前速率预计跌破 diskE_crit 的时间
}
TREND_WINDOW_H = 6.0                   # 趋势窗口（小时）
TREND_MIN_SPAN_MIN = 20.0              # 少于这么长的跨度不报速率（噪声）
REALERT_MIN = 60                       # --notify：持续告警每 60 分钟重提醒一次
HIST_MAX_LINES = 5000                  # 超过就裁剪到后 2000 行

LEVEL_WARN, LEVEL_CRIT = "WARN", "CRIT"

# ---------------------------------------------------------------- 远端探针（只读）
# 输出行：HH-BEGIN / MEM|total_mb|free_mb / SYS|host|boot|uptime_h / CPU|pct /
#         DISK|C:|free|total / PROC|name|n / ADB|n / TEMP|值|来源 / TS|主机钟 / HH-END
PROBE_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Write-Output 'HH-BEGIN'
$os = Get-CimInstance Win32_OperatingSystem
if ($os) {
  $tmb = [int]($os.TotalVisibleMemorySize / 1024)
  $fmb = [int]($os.FreePhysicalMemory / 1024)
  Write-Output ("MEM|{0}|{1}" -f $tmb, $fmb)
  $up = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
  Write-Output ("SYS|{0}|{1}|{2}" -f $os.CSName, $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss'), $up)
}
# CPU：优先 Get-Counter 一次采样；计数器坏了则回退 Win32_Processor 平均负载
$cpu = $null
try { $cpu = [int](Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples[0].CookedValue } catch {}
if ($null -eq $cpu) {
  $p = @(Get-CimInstance Win32_Processor)
  if ($p.Count -gt 0) { $cpu = [int](($p | Measure-Object LoadPercentage -Average).Average) }
}
if ($null -ne $cpu) { Write-Output ("CPU|{0}" -f $cpu) }
foreach ($d in @('C:','E:')) {
  $ld = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$d'"
  if ($ld) { Write-Output ("DISK|{0}|{1}|{2}" -f $d, [math]::Round($ld.FreeSpace/1GB,1), [math]::Round($ld.Size/1GB,1)) }
}
foreach ($n in @('MuMuVMMHeadless','MuMuNxDevice','MuMuNxMain','MAA')) {
  Write-Output ("PROC|{0}|{1}" -f $n, @(Get-Process -Name $n -ErrorAction SilentlyContinue).Count)
}
$adbp = 'E:\MuMu Player 12\nx_main\adb.exe'
if (Test-Path -LiteralPath $adbp) {
  $a = @(& $adbp devices 2>$null | Select-String 'device$')
  Write-Output ("ADB|{0}" -f $a.Count)
}
# 温度：只探「已经存在」的只读来源，不装任何软件。
#  ① ACPI 热区（root/wmi，单位 0.1K）—— 很多桌面机不暴露
#  ② OpenHardwareMonitor 命名空间（若机上本来就有）
$temp = $null; $src = 'na'
$tz = @(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue)
if ($tz.Count -gt 0) {
  $vals = @($tz | ForEach-Object { [math]::Round(($_.CurrentTemperature / 10) - 273.15, 1) } | Where-Object { $_ -gt -40 -and $_ -lt 150 })
  if ($vals.Count -gt 0) { $temp = ($vals | Measure-Object -Maximum).Maximum; $src = 'acpi-tz' }
}
if ($null -eq $temp) {
  $ohm = @(Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor -ErrorAction SilentlyContinue |
           Where-Object { $_.SensorType -eq 'Temperature' })
  if ($ohm.Count -gt 0) {
    $vals = @($ohm | Where-Object { $_.Name -match 'CPU|Package|Core' } | ForEach-Object { [double]$_.Value } |
              Where-Object { $_ -gt -40 -and $_ -lt 150 })
    if ($vals.Count -gt 0) { $temp = [math]::Round(($vals | Measure-Object -Maximum).Maximum, 1); $src = 'ohm' }
  }
}
if ($null -eq $temp) { Write-Output ("TEMP|na|{0}" -f $src) } else { Write-Output ("TEMP|{0}|{1}" -f $temp, $src) }
Write-Output ("TS|{0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Output 'HH-END'
"""


def ssh_exe() -> str:
    for c in (shutil.which("ssh"), r"C:\Windows\System32\OpenSSH\ssh.exe"):
        if c and pathlib.Path(c).exists():
            return c
    sys.exit("[host_health] 找不到 ssh.exe")


def fetch(host: str, key: str, timeout: int = 90) -> tuple[bool, str]:
    """跑一次远端只读探针。返回 (是否取到, 原始输出或错误说明)。"""
    b64 = base64.b64encode(PROBE_PS.encode("utf-16-le")).decode("ascii")
    cmd = [ssh_exe(), "-i", key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=15", "%s@%s" % (os.environ.get("MAAORCH_SSH_USER", "Administrator"), host),
           "powershell -NoProfile -EncodedCommand " + b64]
    try:
        # stdin=DEVNULL 必须：在 MCP stdio server / cron 里跑时，ssh 若继承父进程 stdin 会挂死
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=timeout, creationflags=NOWIN)
    except subprocess.TimeoutExpired:
        return False, "ssh 超时(%ds)" % timeout
    except OSError as e:                                    # noqa: BLE001
        return False, "ssh 起不来: %s" % e
    out = (r.stdout or b"").decode("utf-8", "replace")
    if "HH-BEGIN" in out and "HH-END" in out:
        return True, out
    err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    tail = err[-1][:140] if err else (out.strip().splitlines() or ["(无输出)"])[-1][:140]
    return False, "ssh rc=%s: %s" % (r.returncode, tail)


def parse_probe(text: str) -> dict | None:
    """探针输出 → 指标 dict；没拿到 BEGIN/END 或没有磁盘数据 ⇒ None（判不了，不当通过）。"""
    if "HH-BEGIN" not in text or "HH-END" not in text:
        return None
    m = {"disks": {}, "proc": {}, "mem_total_mb": None, "mem_free_mb": None, "cpu_pct": None,
         "adb": None, "temp_c": None, "temp_src": "na", "sys": {}, "host_ts": None}
    for ln in text.splitlines():
        p = [x.strip() for x in ln.strip().split("|")]
        k = p[0] if p else ""
        try:
            if k == "MEM" and len(p) >= 3:
                m["mem_total_mb"], m["mem_free_mb"] = int(p[1]), int(p[2])
            elif k == "SYS" and len(p) >= 4:
                m["sys"] = {"name": p[1], "boot": p[2], "uptime_h": float(p[3])}
            elif k == "CPU" and len(p) >= 2:
                m["cpu_pct"] = int(p[1])
            elif k == "DISK" and len(p) >= 4:
                m["disks"][p[1]] = {"free_gb": float(p[2]), "total_gb": float(p[3])}
            elif k == "PROC" and len(p) >= 3:
                m["proc"][p[1]] = int(p[2])
            elif k == "ADB" and len(p) >= 2:
                m["adb"] = int(p[1])
            elif k == "TEMP" and len(p) >= 2:
                m["temp_src"] = p[2] if len(p) >= 3 else "?"
                if p[1].lower() != "na":
                    m["temp_c"] = float(p[1])
            elif k == "TS" and len(p) >= 2:
                m["host_ts"] = p[1]
        except (ValueError, IndexError):
            continue
    if not m["disks"]:
        return None
    tot, free = m["mem_total_mb"], m["mem_free_mb"]
    m["mem_used_pct"] = round((tot - free) * 100.0 / tot, 1) if (tot and free is not None) else None
    m["emu"] = m["proc"].get("MuMuVMMHeadless")
    m["maa"] = m["proc"].get("MAA")
    return m


# ---------------------------------------------------------------- 趋势（本机历史）

def load_history(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def append_history(path: pathlib.Path, row: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if path.stat().st_size > 2_000_000:                 # 太大留尾部
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            path.write_text("\n".join(lines[-HIST_MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def slope_per_hour(pts: list[tuple[float, float]]) -> float | None:
    """最小二乘斜率（值/小时）；点数 <2 或全在同一时刻 ⇒ None。"""
    if len(pts) < 2:
        return None
    xs = [t / 3600.0 for t, _v in pts]
    ys = [v for _t, v in pts]
    n = len(xs)
    xm, ym = sum(xs) / n, sum(ys) / n
    den = sum((x - xm) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / den


def trend(rows: list[dict], getter, window_h: float, min_span_min: float,
          now_epoch: float) -> dict | None:
    """窗口内取 (epoch, 值) 做线性回归。返回 {"slope": 值/小时, "n": 样本, "span_h": 跨度} 或 None。"""
    cut = now_epoch - window_h * 3600.0
    pts = []
    for r in rows:
        if not isinstance(r.get("epoch"), (int, float)) or r.get("epoch") < cut:
            continue
        if r.get("ssh") != "ok":
            continue
        v = getter(r)
        if isinstance(v, (int, float)):
            pts.append((float(r["epoch"]), float(v)))
    if len(pts) < 2:
        return None
    span_h = (pts[-1][0] - pts[0][0]) / 3600.0
    if span_h < min_span_min / 60.0:
        return None
    return {"slope": slope_per_hour(pts), "n": len(pts), "span_h": round(span_h, 2)}


def window_stats(rows: list[dict], key: str, window_h: float, now_epoch: float) -> dict | None:
    """窗口内某个计数字段的 min/max/首末（进程数是步进量，看变化，不报斜率）。"""
    cut = now_epoch - window_h * 3600.0
    vals: list[float] = []
    for r in rows:
        if not isinstance(r.get("epoch"), (int, float)) or r["epoch"] < cut:
            continue
        if r.get("ssh") != "ok":
            continue
        v = r.get(key)
        if isinstance(v, (int, float)):
            vals.append(float(v))
    if len(vals) < 2:
        return None
    return {"n": len(vals), "first": vals[0], "last": vals[-1], "min": min(vals), "max": max(vals)}


# ---------------------------------------------------------------- 判定（纯逻辑）

def evaluate(m: dict, tr: dict, thr: dict = THR) -> list[dict]:
    """指标 + 趋势 → 告警列表 [{level, code, msg}]。纯函数（selftest 覆盖）。"""
    a: list[dict] = []

    def add(level, code, msg):
        a.append({"level": level, "code": code, "msg": msg})

    if m.get("ssh") != "ok":
        add(LEVEL_CRIT, "ssh_down", "主机 SSH 不可达：%s" % (m.get("error") or "无输出"))
        return a

    mp = m.get("mem_used_pct")
    if mp is not None:
        if mp >= thr["mem_used_crit_pct"]:
            add(LEVEL_CRIT, "mem_used", "内存已用 %.1f%%（≥ %.0f%%）；可用 %d MB" % (mp, thr["mem_used_crit_pct"], m.get("mem_free_mb") or -1))
        elif mp >= thr["mem_used_warn_pct"]:
            add(LEVEL_WARN, "mem_used", "内存已用 %.1f%%（≥ %.0f%%）" % (mp, thr["mem_used_warn_pct"]))
    mf = m.get("mem_free_mb")
    if mf is not None and mf < thr["mem_free_warn_mb"]:
        add(LEVEL_WARN, "mem_free", "可用内存 %d MB（< %d MB）" % (mf, thr["mem_free_warn_mb"]))

    cpu = m.get("cpu_pct")
    if cpu is not None and cpu >= thr["cpu_warn_pct"]:
        add(LEVEL_WARN, "cpu_high", "CPU %d%%（≥ %.0f%%，单采样）" % (cpu, thr["cpu_warn_pct"]))

    c = (m.get("disks") or {}).get("C:")
    if c:
        if c["free_gb"] < thr["diskC_crit_gb"]:
            add(LEVEL_CRIT, "diskC_low", "C: 剩余 %.1fG（< %.0fG）" % (c["free_gb"], thr["diskC_crit_gb"]))
        elif c["free_gb"] < thr["diskC_warn_gb"]:
            add(LEVEL_WARN, "diskC_low", "C: 剩余 %.1fG（< %.0fG）" % (c["free_gb"], thr["diskC_warn_gb"]))
    e = (m.get("disks") or {}).get("E:")
    if e:
        if e["free_gb"] < thr["diskE_crit_gb"]:
            add(LEVEL_CRIT, "diskE_low", "E: 剩余 %.1fG（< %.0fG）" % (e["free_gb"], thr["diskE_crit_gb"]))
        elif e["free_gb"] < thr["diskE_warn_gb"]:
            add(LEVEL_WARN, "diskE_low", "E: 剩余 %.1fG（< %.0fG）" % (e["free_gb"], thr["diskE_warn_gb"]))
        rate = (tr or {}).get("diskE", {}).get("slope") if tr else None
        if rate is not None and rate <= thr["rate_warn_gb_h"]:
            add(LEVEL_WARN, "diskE_rate", "E: 近 %.1fh 缩水 %.1fGB/h（≤ %.1fGB/h；克隆实例属预期，其余情况查日志/回收站）"
                % ((tr or {}).get("diskE", {}).get("span_h", 0) or 0, -rate, thr["rate_warn_gb_h"]))
        # 预计跌破线独立于「速率超阈」：慢速但持续的缩水同样要在 24h 内报出来
        if rate is not None and rate < 0 and e["free_gb"] > thr["diskE_crit_gb"]:
            eta = (e["free_gb"] - thr["diskE_crit_gb"]) / (-rate)
            if eta < thr["eta_warn_h"]:
                add(LEVEL_WARN, "diskE_eta", "E: 按当前速率（%.1fGB/h）约 %.1fh 跌破 %.0fG"
                    % (rate, eta, thr["diskE_crit_gb"]))

    t = m.get("temp_c")
    if t is not None:
        if t >= thr["temp_crit_c"]:
            add(LEVEL_CRIT, "temp_high", "温度 %.1f℃（≥ %.0f℃）" % (t, thr["temp_crit_c"]))
        elif t >= thr["temp_warn_c"]:
            add(LEVEL_WARN, "temp_high", "温度 %.1f℃（≥ %.0f℃）" % (t, thr["temp_warn_c"]))

    emu, adb = m.get("emu"), m.get("adb")
    if emu is not None and emu > 0 and adb == 0:
        add(LEVEL_WARN, "emu_adb", "%d 个模拟器进程在，但 adb 在线设备=0（设备链路断）" % emu)
    if m.get("maa") is not None and m.get("maa", 0) > 0 and emu == 0:
        add(LEVEL_WARN, "maa_no_emu", "有 %d 个 MAA 进程但无模拟器进程（疑似起不来/已停）" % m["maa"])
    return a


def status_of(alerts: list[dict], ssh_ok: bool) -> str:
    if not ssh_ok:
        return "DOWN"
    lv = {x["level"] for x in alerts}
    if LEVEL_CRIT in lv:
        return "CRIT"
    if LEVEL_WARN in lv:
        return "WARN"
    return "OK"


def build_line(m: dict, tr: dict | None, alerts: list[dict], st: str) -> str:
    """巡检固定格式：HOST_HEALTH=cpu=..,mem=..,diskC=..,diskE=..,emu=..,maa=..,+扩展字段。"""
    def num(v, unit="", fmt="%s"):
        return "-" if v is None else (fmt % v) + unit
    rate = (tr or {}).get("diskE", {}).get("slope") if tr else None
    fields = [
        "cpu=%s" % num(m.get("cpu_pct"), "%"),
        "mem=%s" % num(m.get("mem_used_pct"), "%", "%.1f"),
        "diskC=%s" % num(((m.get("disks") or {}).get("C:") or {}).get("free_gb"), "G", "%.1f"),
        "diskE=%s" % num(((m.get("disks") or {}).get("E:") or {}).get("free_gb"), "G", "%.1f"),
        "emu=%s" % num(m.get("emu")),
        "maa=%s" % num(m.get("maa")),
        "ssh=%s" % ("ok" if m.get("ssh") == "ok" else "down"),
        "temp=%s" % ("na" if m.get("temp_c") is None else ("%.1fC" % m["temp_c"])),
        "memFree=%s" % num(m.get("mem_free_mb"), "M"),
        "rateE=%s" % ("na" if rate is None else ("%+.1fG/h" % rate)),
        "alerts=%s" % (";".join(x["code"] for x in alerts) or "none"),
        "status=%s" % st,
    ]
    return "HOST_HEALTH=" + ",".join(fields)


# ---------------------------------------------------------------- 通知（可选）

def maybe_notify(status: str, alerts: list[dict], line: str) -> None:
    """--notify：CRIT 发一封、恢复发一封；冷却 REALERT_MIN 分钟。默认不启用。"""
    try:
        from host_watchdog import load_conf, notify  # noqa: PLC0415  同目录兄弟模块
    except Exception as e:                                  # noqa: BLE001
        print("[host_health] --notify 不可用（%s），跳过通知" % e)
        return
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        st = {"level": "OK", "at": 0.0}
    now = time.time()
    if status == "CRIT":
        if st.get("level") != "CRIT" or now - st.get("at", 0) > REALERT_MIN * 60:
            body = "主机健康告警（%s）：\n%s\n\n%s" % (
                dt.datetime.now().strftime("%Y-%m-%d %H:%M"), line,
                "\n".join("· [%s] %s" % (x["level"], x["msg"]) for x in alerts))
            notify(load_conf(), "[告警] 主机健康 %s" % ",".join(x["code"] for x in alerts), body)
            st = {"level": "CRIT", "at": now}
    elif st.get("level") == "CRIT":
        notify(load_conf(), "[恢复] 主机健康回到 %s" % status, "当前：%s" % line)
        st = {"level": status, "at": 0.0}
    else:
        st = {"level": status, "at": st.get("at", 0.0)}
    try:
        STATE.write_text(json.dumps(st), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- 自检（纯本地）

_SELFTEST_PROBE_OK = """\
HH-BEGIN
MEM|65400|44607
SYS|DESKTOP-ABC|2026-09-26 20:41:00|36.5
CPU|20
DISK|C:|41.7|300.0
DISK|E:|118.3|930.0
PROC|MuMuVMMHeadless|4
PROC|MuMuNxDevice|4
PROC|MuMuNxMain|1
PROC|MAA|3
ADB|6
TEMP|na|na
TS|2026-09-28 09:40:00
HH-END
"""


def _mk_hist(slope_gb_h: float, hours: float = 2.0, n: int = 5, base: float = 200.0,
             now: float = 2_000_000.0) -> list[dict]:
    rows = []
    for i in range(n):
        t = now - hours * 3600 + i * (hours * 3600 / (n - 1))
        rows.append({"epoch": t, "ssh": "ok",
                     "disk_gb": {"C:": 100.0, "E:": base + slope_gb_h * ((t - now) / 3600.0)}})
    return rows


def selftest() -> int:
    bad = 0
    cases = []

    def check(label, got, want):
        nonlocal bad
        ok = got == want
        if not ok:
            bad += 1
        print("%s %-34s got=%r want=%r" % ("PASS" if ok else "FAIL", label, got, want))

    m = parse_probe(_SELFTEST_PROBE_OK)
    assert m is not None
    cases.append(("parse cpu/mem", (m["cpu_pct"], m["mem_total_mb"], m["mem_free_mb"]), (20, 65400, 44607)))
    cases.append(("parse mem_used_pct", m["mem_used_pct"], round((65400 - 44607) * 100.0 / 65400, 1)))
    cases.append(("parse disks", (m["disks"]["E:"]["free_gb"], m["disks"]["C:"]["total_gb"]), (118.3, 300.0)))
    cases.append(("parse proc/emu/maa", (m["emu"], m["maa"], m["adb"]), (4, 3, 6)))
    cases.append(("parse temp na", (m["temp_c"], m["temp_src"]), (None, "na")))
    cases.append(("parse sys uptime", m["sys"]["uptime_h"], 36.5))
    cases.append(("parse garbage -> None", parse_probe("no markers here"), None))
    cases.append(("parse missing disk -> None", parse_probe("HH-BEGIN\nCPU|10\nHH-END\n"), None))

    ok_m = parse_probe(_SELFTEST_PROBE_OK)
    ok_m["ssh"] = "ok"
    cases.append(("eval healthy -> []", evaluate(ok_m, None), []))

    def _m(**kw):
        base = parse_probe(_SELFTEST_PROBE_OK)
        assert base is not None
        base["ssh"] = "ok"
        base.update(kw)
        return base

    mm = _m(); mm["mem_used_pct"] = 93.0
    cases.append(("eval mem>90 -> CRIT", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("CRIT", "mem_used")]))

    mm = _m(); mm["mem_used_pct"] = 86.0
    cases.append(("eval mem>85 -> WARN", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("WARN", "mem_used")]))

    mm = _m(); mm["mem_free_mb"] = 7000; mm["mem_used_pct"] = 80.0
    cases.append(("eval mem_free<8G -> WARN", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("WARN", "mem_free")]))

    mm = _m(); mm["cpu_pct"] = 96
    cases.append(("eval cpu>=95 -> WARN", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("WARN", "cpu_high")]))

    mm = _m(); mm["disks"]["E:"] = {"free_gb": 18.0, "total_gb": 930.0}
    cases.append(("eval E<20 -> CRIT", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("CRIT", "diskE_low")]))

    mm = _m(); mm["disks"]["E:"] = {"free_gb": 45.0, "total_gb": 930.0}
    cases.append(("eval E<60 -> WARN", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("WARN", "diskE_low")]))

    mm = _m(); mm["disks"]["C:"] = {"free_gb": 10.0, "total_gb": 300.0}
    cases.append(("eval C<15 -> CRIT", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("CRIT", "diskC_low")]))

    mm = _m(); mm["temp_c"] = 88.0; mm["temp_src"] = "acpi-tz"
    cases.append(("eval temp>=85 -> WARN", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("WARN", "temp_high")]))

    mm = _m(); mm["temp_c"] = 95.0
    cases.append(("eval temp>=92 -> CRIT", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("CRIT", "temp_high")]))

    mm = _m(); mm["adb"] = 0
    cases.append(("eval emu>0 adb=0 -> WARN", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("WARN", "emu_adb")]))

    mm = _m(); mm["emu"] = 0; mm["proc"]["MuMuVMMHeadless"] = 0
    cases.append(("eval maa>0 emu=0 -> WARN", [(x["level"], x["code"]) for x in evaluate(mm, None)], [("WARN", "maa_no_emu")]))

    tr = trend(_mk_hist(-2.0), lambda r: r["disk_gb"]["E:"], 6.0, 20.0, 2_000_000.0)
    cases.append(("trend slope ≈ -2.0/h", tr and abs(tr["slope"] + 2.0) < 1e-6, True))
    cases.append(("trend points/span", (tr["n"], tr["span_h"] if tr else None), (5, 2.0)))

    tr2 = trend(_mk_hist(-1.0, hours=0.1, n=2), lambda r: r["disk_gb"]["E:"], 6.0, 20.0, 2_000_000.0)
    cases.append(("trend span<20min -> None", tr2, None))

    rows = _mk_hist(-3.0, hours=2.0)
    tr3 = trend(rows, lambda r: r["disk_gb"]["E:"], 6.0, 20.0, 2_000_000.0)
    mm = _m(); mm["disks"]["E:"] = {"free_gb": 80.0, "total_gb": 930.0}
    got = [(x["level"], x["code"]) for x in evaluate(mm, {"diskE": tr3})]
    cases.append(("eval rate -3G/h -> WARN+eta", got, [("WARN", "diskE_rate"), ("WARN", "diskE_eta")]))

    # 慢速缩水（未到 -2GB/h 阈值）但 24h 内会跌破 20G ⇒ 仍要报 eta
    mm = _m(); mm["disks"]["E:"] = {"free_gb": 25.0, "total_gb": 930.0}
    got = [(x["level"], x["code"]) for x in evaluate(mm, {"diskE": {"slope": -0.5, "n": 6, "span_h": 4.0}})]
    cases.append(("eval slow drain eta<24h", got, [("WARN", "diskE_low"), ("WARN", "diskE_eta")]))

    mm = _m(); mm["disks"]["E:"] = {"free_gb": 500.0, "total_gb": 930.0}
    got = [(x["level"], x["code"]) for x in evaluate(mm, {"diskE": tr3})]
    cases.append(("eval rate alert at high free", got, [("WARN", "diskE_rate")]))

    # 进程窗口统计（窗口内 min/max/首末；ssh 失败的样本剔除）
    rows_p = [
        {"epoch": 2_000_000.0 - 3600, "ssh": "ok", "emu": 3.0, "maa": 2.0},
        {"epoch": 2_000_000.0 - 1800, "ssh": "ok", "emu": 5.0, "maa": 2.0},
        {"epoch": 2_000_000.0, "ssh": "ok", "emu": 4.0, "maa": 1.0},
    ]
    cases.append(("window emu stats", window_stats(rows_p, "emu", 6.0, 2_000_000.0),
                  {"n": 3, "first": 3.0, "last": 4.0, "min": 3.0, "max": 5.0}))
    cases.append(("window missing key -> None", window_stats(rows_p, "adb", 6.0, 2_000_000.0), None))
    cases.append(("window ssh-down filtered -> None",
                  window_stats([dict(rows_p[0], ssh="down"), rows_p[2]], "emu", 6.0, 2_000_000.0), None))

    ln = build_line(ok_m, None, [], "OK")
    cases.append(("line prefix/order", ln.split("=", 1)[1].split(",")[:6],
                  ["cpu=20%", "mem=31.8%", "diskC=41.7G", "diskE=118.3G", "emu=4", "maa=3"]))
    cases.append(("line tail status", ln.endswith("status=OK"), True))
    cases.append(("line alerts none", "alerts=none" in ln, True))

    dn = {"disks": {}, "proc": {}, "mem_total_mb": None, "mem_free_mb": None, "cpu_pct": None,
          "adb": None, "temp_c": None, "temp_src": "na", "sys": {}, "host_ts": None,
          "ssh": "down", "error": "ssh 超时(90s)"}
    al = evaluate(dn, None)
    cases.append(("eval ssh down -> CRIT", [(x["level"], x["code"]) for x in al], [("CRIT", "ssh_down")]))
    cases.append(("line ssh down", build_line(dn, None, al, status_of(al, False)),
                  "HOST_HEALTH=cpu=-,mem=-,diskC=-,diskE=-,emu=-,maa=-,ssh=down,temp=na,memFree=-,rateE=na,"
                  "alerts=ssh_down,status=DOWN"))
    cases.append(("status rollup warn", status_of([{"level": "WARN"}], True), "WARN"))
    cases.append(("status rollup crit", status_of([{"level": "WARN"}, {"level": "CRIT"}], True), "CRIT"))

    for label, got, want in cases:
        check(label, got, want)
    print("selftest: %s (%d/%d)" % ("OK" if not bad else "FAIL", len(cases) - bad, len(cases)))
    return 0 if not bad else 1


# ---------------------------------------------------------------- 主流程

def load_cfg(args) -> tuple[str, str, float]:
    """主机 / 密钥 / 内存阈值：env > watchdog.ini > 默认。"""
    host = args.host or os.environ.get("MAAORCH_MAS_HOST")
    key = args.key or os.environ.get("MAA_SSH_KEY")
    thr_mem = None
    if CONF.exists():
        try:
            import configparser  # noqa: PLC0415
            c = configparser.ConfigParser()
            c.read(CONF, encoding="utf-8")
            if not host:
                host = c["host"]["host"]
            if "perf" in c and c["perf"].get("free_mem_alert_mb"):
                thr_mem = c["perf"].getint("free_mem_alert_mb")
        except Exception:                                   # noqa: BLE001
            pass
    if thr_mem:
        THR["mem_free_warn_mb"] = thr_mem
    return host or DEFAULT_HOST, key or DEFAULT_KEY, args.timeout


def print_report(res: dict) -> None:
    m, tr = res["metrics"], res["trend"]
    print("主机健康巡检（%s）  只读  host=%s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M"), res["host"]))
    if m.get("ssh") != "ok":
        print("取数失败：%s（SSH 不通 ⇒ 一切下游工具都会失能）" % m.get("error"))
        for x in res["alerts"]:
            print("%-4s %s" % (x["level"], x["msg"]))
        print("")
        print(res["line"])
        return
    sysinfo = m.get("sys") or {}
    print("采样 %s（主机钟）｜ %s ｜ 运行 %.1fh（boot %s）"
          % (m.get("host_ts") or "-", sysinfo.get("name") or "?", sysinfo.get("uptime_h") or 0, sysinfo.get("boot") or "-"))
    print("%-8s %s" % ("CPU", "%d%%" % m["cpu_pct"] if m["cpu_pct"] is not None else "-"))
    print("%-8s 已用 %.1f%%  可用 %s MB / 共 %s MB"
          % ("内存", m["mem_used_pct"] or 0, m["mem_free_mb"], m["mem_total_mb"]))
    for d in ("C:", "E:"):
        v = (m.get("disks") or {}).get(d)
        if v:
            print("%-8s 剩余 %.1fG / 共 %.1fG" % ("磁盘 " + d, v["free_gb"], v["total_gb"]))
    emu, maa = m.get("emu"), m.get("maa")
    print("%-8s %d 个 MuMuVMMHeadless（NxDevice %s）｜ adb 在线 %s"
          % ("模拟器", emu if emu is not None else -1,
             (m.get("proc") or {}).get("MuMuNxDevice", "-"), m.get("adb")))
    print("%-8s %d 个进程" % ("MAA", maa if maa is not None else -1))
    print("%-8s %s" % ("温度", "%.1f℃（%s）" % (m["temp_c"], m["temp_src"]) if m["temp_c"] is not None
                       else "na（来源：%s；WMI 不暴露时不装第三方软件，见 docs/HOST_HEALTH.md）" % m["temp_src"]))
    win = res.get("window_h", TREND_WINDOW_H)
    if tr:
        seg = []
        for k, label in (("diskC", "C:"), ("diskE", "E:"), ("memFree", "内存可用")):
            t = tr.get(k)
            if t and t.get("slope") is not None:
                unit = "G/h" if k.startswith("disk") else "M/h"
                seg.append("%s %+.1f%s" % (label, t["slope"], unit))
        if seg:
            print("%-8s 近 %.0fh：%s（%d 点）" % ("趋势", win, " ｜ ".join(seg),
                                                  (tr.get("diskE") or {}).get("n", 0)))
    pw = res.get("proc_win") or {}
    pseg = []
    for k in ("emu", "maa", "adb"):
        w = pw.get(k)
        if w:
            pseg.append("%s %d→%d（min %d / max %d，%d 点）" % (k, w["first"], w["last"], w["min"], w["max"], w["n"]))
    if pseg:
        print("%-8s 近 %.0fh：%s" % ("进程趋势", win, " ｜ ".join(pseg)))
    if not res["alerts"]:
        print("预警      无")
    for x in res["alerts"]:
        print("%-9s [%s] %s" % ("预警" if x["level"] == "WARN" else "预警✱", x["code"], x["msg"]))
    print("")
    print(res["line"])


def print_check(res: dict) -> None:
    for x in res["alerts"]:
        print("%-4s %-12s %s" % (x["level"], x["code"], x["msg"]))
    print("主机健康 %s  host=%s  ssh=%s  预警 %d（WARN %d / CRIT %d）"
          % (dt.datetime.now().strftime("%Y-%m-%d %H:%M"), res["host"],
             "ok" if res["metrics"].get("ssh") == "ok" else "down", len(res["alerts"]),
             sum(1 for x in res["alerts"] if x["level"] == "WARN"),
             sum(1 for x in res["alerts"] if x["level"] == "CRIT")))
    print(res["line"])


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                       # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="主机健康巡检（只读）：CPU/内存/磁盘/进程/温度 + 趋势 + 预警")
    ap.add_argument("--check", action="store_true", help="紧凑模式（巡检用），末行 HOST_HEALTH=")
    ap.add_argument("--json", action="store_true", help="JSON 输出（含 line 字段）")
    ap.add_argument("--selftest", action="store_true", help="本地逻辑自检（不连网）")
    ap.add_argument("--host", help="覆盖主机（默认取 runtime/watchdog.ini 或 %s）" % DEFAULT_HOST)
    ap.add_argument("--key", help="覆盖 SSH 私钥")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--window-h", type=float, default=TREND_WINDOW_H, help="趋势窗口（小时，默认 6）")
    ap.add_argument("--history", default=str(HISTORY), help="历史日志路径（测试用）")
    ap.add_argument("--no-log", action="store_true", help="不写历史（测试用）")
    ap.add_argument("--notify", action="store_true", help="CRIT 时发通知（默认关；巡检走 HOST_HEALTH= 行）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    host, key, timeout = load_cfg(a)
    now = time.time()
    hist_path = pathlib.Path(a.history)
    ok, raw = fetch(host, key, timeout)
    m = parse_probe(raw) if ok else None
    if m is None:
        m = {"disks": {}, "proc": {}, "mem_total_mb": None, "mem_free_mb": None, "cpu_pct": None,
             "adb": None, "temp_c": None, "temp_src": "na", "sys": {}, "host_ts": None,
             "ssh": "down", "error": raw}
    else:
        m["ssh"] = "ok"

    hist = load_history(hist_path)
    tr = {
        "diskC": trend(hist, lambda r: (r.get("disk_gb") or {}).get("C:"), a.window_h, TREND_MIN_SPAN_MIN, now),
        "diskE": trend(hist, lambda r: (r.get("disk_gb") or {}).get("E:"), a.window_h, TREND_MIN_SPAN_MIN, now),
        "memFree": trend(hist, lambda r: r.get("mem_free_mb"), a.window_h, TREND_MIN_SPAN_MIN, now),
    }
    tr = {k: v for k, v in tr.items() if v} or None
    proc_win = {k: window_stats(hist, k, a.window_h, now) for k in ("emu", "maa", "adb")}

    alerts = evaluate(m, tr)
    st = status_of(alerts, m.get("ssh") == "ok")
    line = build_line(m, tr, alerts, st)

    res = {
        "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "host": host,
        "window_h": a.window_h,
        "metrics": m,
        "trend": {k: v for k, v in (tr or {}).items()} or None,
        "proc_win": proc_win,
        "alerts": alerts,
        "status": st,
        "line": line,
    }
    if not a.no_log:
        append_history(hist_path, {
            "epoch": now, "ts": res["ts"], "host": host, "ssh": m.get("ssh"),
            "cpu_pct": m.get("cpu_pct"), "mem_total_mb": m.get("mem_total_mb"),
            "mem_free_mb": m.get("mem_free_mb"), "mem_used_pct": m.get("mem_used_pct"),
            "disk_gb": {d: v["free_gb"] for d, v in (m.get("disks") or {}).items()},
            "emu": m.get("emu"), "maa": m.get("maa"), "adb": m.get("adb"),
            "temp_c": m.get("temp_c"), "temp_src": m.get("temp_src"),
            "status": st, "alerts": [x["code"] for x in alerts],
        })

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif a.check:
        print_check(res)
    else:
        print_report(res)

    if a.notify:
        maybe_notify(st, alerts, line)

    if st == "DOWN":
        return 1
    if st == "CRIT":
        return 3
    if st == "WARN":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
