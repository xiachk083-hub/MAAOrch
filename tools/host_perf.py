# -*- coding: utf-8 -*-
"""主机指标采样（从本机 SSH 拉一次，写本地日志）。

不往主机写任何文件：只跑一条只读的 PowerShell 取计数器。
配置复用 runtime/watchdog.ini 的 [host] 段；[perf] 段管内存告警阈值。

用法:
  python tools/host_perf.py --once     # 采一次（计划任务用这个）
  python tools/host_perf.py --loop     # 常驻，每 interval_min 分钟采一次
  python tools/host_perf.py --once --no-send --mem-threshold-mb 999999   # 测告警逻辑（不发信）

输出: runtime/host_perf.log
  时间 | CPU% | 可用内存MB | MuMuNxDevice数 | VMM数 | MAA数 | 最大WS进程

内存告警: 可用内存跌破阈值（默认 8192MB）→ 发通知；低于阈值期间每 60 分钟重提醒一次；
          回到阈值以上发一条"恢复"。告警里带**趋势**（按最近几次采样的 MB/分钟，
          以及按此速率还有多久跌破）。
"""
from __future__ import annotations

import argparse
import base64
import configparser
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

from host_watchdog import load_conf, notify

# Windows：不带这个标志，子进程（adb/ssh/ping）会**闪一个 cmd 黑窗** ✗
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROJ = pathlib.Path(__file__).resolve().parents[1]
CONF = PROJ / "runtime" / "watchdog.ini"
OUT = PROJ / "runtime" / "host_perf.log"
MEM_STATE = PROJ / "runtime" / "host_perf_state.json"
KEY = os.environ.get("MAA_SSH_KEY", r"C:\Users\xiach\.ssh\maaorch_target")
REALERT_MIN = 60          # 持续低于阈值时，每隔这么久再提醒一次

REMOTE = (
    '$c=(Get-Counter "\\Processor(_Total)\\% Processor Time","\\Memory\\Available MBytes").CounterSamples;'
    '$adb="E:\\MuMu Player 12\\nx_main\\adb.exe";'
    '$a=@(& $adb devices 2>$null | Select-String "device$").Count;'
    '$e=(Get-Process MuMuNxDevice -EA 0).Count;'
    '$v=(Get-Process MuMuVMMHeadless -EA 0).Count;'
    '$m=(Get-Process MAA -EA 0).Count;'
    '$t=(Get-Process | Sort-Object WS -Descending | Select-Object -First 1).ProcessName;'
    '"{0}|{1}|{2}|{3}|{4}|{5}|{6}" -f [int]$c[0].CookedValue,[int]$c[1].CookedValue,$a,$e,$v,$m,$t'
)


def ssh_exe() -> str:
    for c in (shutil.which("ssh"), r"C:\Windows\System32\OpenSSH\ssh.exe"):
        if c and pathlib.Path(c).exists():
            return c
    sys.exit("[perf] 找不到 ssh.exe")


def sample(host: str) -> str:
    b64 = base64.b64encode(REMOTE.encode("utf-16-le")).decode()
    cmd = [ssh_exe(), "-i", KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=15", "Administrator@" + host,
           "powershell -NoProfile -EncodedCommand " + b64]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=90, creationflags=NOWIN)
    out = (r.stdout or b"").decode("utf-8", "replace")
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line[:1].isdigit() and line.count("|") >= 5:     # 采样行（6 或 7 字段）
            return line
    err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return "ERR|%s" % (err[-1][:120] if err else "no output")


def _parse(line: str):
    """采样行 → (CPU%, 可用内存MB, adb在线数, NxDevice, VMM, MAA, 最大WS进程)。

    **两种形态都要吃**：原始行 `75|16840|11|13|13|12|MuMuVMMHeadless`、
    日志行 `2026-09-18 06:03 | 75|16840|11|...`。
    （踩过：只用一套下标解析两种形态 ⇒ 把"实例数 13"当成"内存 13MB"，
      直接发出一封"内存跌破 8192"的假告警。）
    兼容 6 字段的老行（2026-09-18 06:30 之前没有 adb 在线数）。
    """
    parts = [p.strip() for p in line.split("|")]
    if parts and ":" in parts[0]:                      # 日志行：吃掉时间戳
        parts = parts[1:]
    if len(parts) >= 7:                                # 新格式
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]),
                    int(parts[4]), int(parts[5]), parts[6])
        except ValueError:
            return None
    if len(parts) == 6:                                # 老格式（无 adb 在线数）
        try:
            return (int(parts[0]), int(parts[1]), None, int(parts[2]),
                    int(parts[3]), int(parts[4]), parts[5])
        except ValueError:
            return None
    return None


def _slope_mb_per_min(n: int = 8) -> float:
    """从已有采样里估内存速率（MB/分钟；负数=在掉）。"""
    try:
        pts = []
        for ln in OUT.read_text(encoding="utf-8").splitlines()[-n:]:
            stamp, _, rest = ln.partition("|")
            v = _parse(stamp + "|" + rest)
            if v:
                pts.append((stamp.strip(), v[1]))
        if len(pts) >= 2:
            (t0, m0), (t1, m1) = pts[0], pts[-1]
            mins = (dt.datetime.strptime(t1, "%Y-%m-%d %H:%M:%S")
                    - dt.datetime.strptime(t0, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            if mins > 0:
                return (m1 - m0) / mins
    except Exception:                                       # noqa: BLE001
        pass
    return 0.0


def _load_mem_state() -> dict:
    try:
        return json.loads(MEM_STATE.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return {"low": False, "alert_at": 0.0}


def _save_mem_state(s: dict) -> None:
    try:
        MEM_STATE.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def check_mem(c: configparser.ConfigParser, mem: int, line: str,
              warn: int, send: bool = True) -> None:
    """可用内存跌破阈值 → 告警（带趋势与预计跌破时间）；回升 → 恢复。"""
    st = _load_mem_state()
    now = time.time()
    if mem < warn:
        if (not st.get("low")) or now - st.get("alert_at", 0) > REALERT_MIN * 60:
            slope = _slope_mb_per_min()
            eta = ""
            if slope < 0:
                eta = "（按当前速率 %.0f MB/分钟，约 %.0f 分钟后耗尽）" % (
                    -slope, mem / (-slope))
            notify(c, "[告警] 主机可用内存跌破 %d MB" % warn,
                   "主机可用内存只剩 %d MB（阈值 %d MB）%s。\n采样：%s\n\n"
                   "内存最大进程通常是 MuMuVMMHeadless —— 实例数越多掉得越快。"
                   % (mem, warn, eta, line), send=send)
            st["alert_at"] = now
        st["low"] = True
    else:
        if st.get("low"):
            notify(c, "[恢复] 主机可用内存回到 %d MB 以上" % warn,
                   "当前可用内存 %d MB（阈值 %d MB）。\n采样：%s" % (mem, warn, line), send=send)
        st["low"] = False
    _save_mem_state(st)


def heartbeat(c: configparser.ConfigParser, send: bool = True, force: bool = False) -> None:
    """每天发一封"我还活着"的心跳邮件（覆盖"告警系统自己挂了没人知道"这个盲区）。

    判据是**收不到**：哪天没收到 ⇒ 采样器没跑 / 邮件通道断了 / 开发机关机。
    所以邮件里带近 24h 的采样统计 —— 既证明活着，也顺手日报。
    发出时机：到点后**第一次运行**发（不要求准点跑），日期记在状态文件里防重复。
    """
    st = _load_mem_state()
    today = dt.date.today().isoformat()
    if st.get("hb_date") == today and not force:
        return
    try:
        hour = c["perf"].getint("heartbeat_hour", fallback=8)
    except KeyError:
        hour = 8
    if not force and dt.datetime.now().hour < hour:
        return

    lines = OUT.read_text(encoding="utf-8").splitlines() if OUT.exists() else []
    cut = dt.datetime.now() - dt.timedelta(hours=24)
    pts = []
    for ln in lines:
        stamp, _, rest = ln.partition("|")
        v = _parse(stamp + "|" + rest)
        if v:
            try:
                if dt.datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M:%S") >= cut:
                    pts.append(v)
            except ValueError:
                pass
    mem_lo, mem_hi = (min(p[1] for p in pts), max(p[1] for p in pts)) if pts else (0, 0)
    emu_lo, emu_hi = (min(p[2] for p in pts), max(p[2] for p in pts)) if pts else (0, 0)
    maa_hi = max((p[4] for p in pts), default=0)
    body = (
        "MAAOrch 看门狗心跳：本机采样器活着。\n\n"
        "· 近 24h 采样：%d 条（正常 ≈ 1440，每分钟一条）\n"
        "· 可用内存区间：%d ~ %d MB\n"
        "· 模拟器实例数：%d ~ %d 台 ｜ MAA 进程最多 %d 个\n"
        "· 最近一条：%s\n"
        "· 内存告警状态：%s\n\n"
        "**收不到这封邮件 = 这套告警已经死了**（开发机关机 / 计划任务被禁 / 邮件通道断）。\n"
        "检查：计划任务 MAAOrch-HostPerf、MAAOrch-HostWatchdog；以及 runtime/watchdog.log。"
        % (len(pts), mem_lo, mem_hi, emu_lo, emu_hi, maa_hi,
           lines[-1] if lines else "(无)",
           "低内存告警中" if st.get("low") else "正常")
    )
    notify(c, "[心跳] MAAOrch 看门狗 %s" % today, body, send=send)
    st["hb_date"] = today
    _save_mem_state(st)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--no-send", action="store_true", help="测试用：只记日志不发通知")
    ap.add_argument("--mem-threshold-mb", type=int, default=None, help="覆盖内存告警阈值")
    ap.add_argument("--heartbeat-now", action="store_true",
                    help="立刻发一封心跳（测试用：故意造信号，看它真会发）")
    a = ap.parse_args()
    send = not a.no_send

    c = load_conf()
    host = c["host"]["host"]
    interval = c["host"].getint("interval_min", fallback=5)
    warn = a.mem_threshold_mb
    if warn is None:
        try:
            warn = c["perf"].getint("free_mem_alert_mb", fallback=8192)
        except KeyError:
            warn = 8192

    def tick() -> None:
        line = sample(host)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with OUT.open("a", encoding="utf-8") as f:
                f.write("%s | %s\n" % (stamp, line))
        except OSError as e:                                # noqa: BLE001
            print("写日志失败:", e)
        print("%s | %s" % (stamp, line))
        v = _parse(line)
        if v:
            check_mem(c, v[1], line, warn, send=send)
        heartbeat(c, send=send)

    if a.heartbeat_now:
        heartbeat(c, send=send, force=True)
        return

    if a.loop:
        while True:
            tick()
            time.sleep(interval * 60)
    tick()


if __name__ == "__main__":
    main()
