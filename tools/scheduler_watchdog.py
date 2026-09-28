#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调度看门狗（外部守护）—— 检测 Hermes gateway / cron 调度 / 发车审计的「静默停摆」。

## 为什么存在（为什么不能用 Hermes cron 看护 Hermes）
日常调度完全依赖 Hermes gateway 常驻：cron 在 gateway 进程里跑。gateway 一挂：
到点不发车 ⇒ 全队停摆，而且**没有任何东西会报告这件事** —— Hermes 的巡检、日报、cron
全都住在 gateway 里，gateway 死 = 监控一起死（自指闭环，没有「没收到心跳就报警」的第三方）。
所以看门狗必须生活在 Hermes 之外：**Windows 计划任务每 5 分钟跑一次 `--once`**
（`MAAOrch-SchedulerWatchdog` → tools\\run_hidden.vbs → tools\\scheduler_watchdog_launch.ps1）。
详见 docs/SCHEDULER_WATCHDOG.md。

## 检测（全部只读；不依赖 Hermes 进程 / 不依赖 hermes CLI）
 ① gateway 进程/端口存活
    - <hermes>/gateway.pid → PID → 进程是否真活着（OpenProcess + GetExitCodeProcess）
    - <hermes>/gateway_state.json → gateway_state / updated_at / code_sha（陈旧代码比对）
    - 每个受查 profile 的 cron 心跳：profiles/<p>/cron/ticker_heartbeat（>3×60+20s=过期）
      与 ticker_last_success（心跳新但 success 过期 = ticker 空转）、ticker_last_error
    - 端口（辅助）：gateway PID 的本地 LISTEN 口（netstat -ano）+ TCP 连通
 ② cron runs（执行台账）超期
    - profiles/<p>/cron/jobs.json：enabled 任务 next_run_at 过期量 > 容忍窗口
      （容忍窗口 = min(120, max(grace_min(默认15), 发车间隔+5)) 分钟，按 schedule 计算）
    - profiles/<p>/cron/executions.db：每个任务最近一次执行 claimed_at（"最近一次执行时间"）
 ③ 最近发车审计时间
    - runtime/mas_dispatch_log.jsonl 尾部 ts、runtime/mas_dispatch_state.json 今日 "at"
    - 发车窗口（05:00 / 19:30 起）内，超过 45 分钟仍无任何今日发车审计 ⇒ audit_stale 告警
    - 附带：mas_dispatch_alerts.json 未消除告警数 / late 标记数（只报告）

## 处置
- 先记录（runtime/scheduler_watchdog.log + state），再告警：
  dry-run payload 落 runtime/reports/scheduler_watchdog_alert_*.json +
  runtime/scheduler_watchdog_alerts.jsonl；`--alert-send` 才真发（复用 host_watchdog.notify +
  runtime/watchdog.ini，与主机看门狗/班次日报同源通道）。同一条告警 renotify_min 内不重复；
  条件消失发「已消除」。
- `--restart`（默认关，需显式开启）：探测到 gateway 不活/心跳停摆/代码陈旧时执行拉起命令
  （默认 `hermes --profile default gateway restart`），随后复验；restart_min_interval_min 内不重复拉起。
- 开机静默窗：系统开机 < boot_grace_min 分钟内只记录不告警（宿主每日 04:00/19:00 计划重启，
  gateway 尚未拉起属预期，避免假警报）。

## 写边界
只写本机 runtime/（log/state/payload/jsonl）与（`--install-task --apply` 时的）本机计划任务
`MAAOrch-SchedulerWatchdog`；绝不写主机、不改 AUTO-MAS、不改 Hermes 配置。

## 用法
  python tools/scheduler_watchdog.py --once             # 单次探测（计划任务用）
  python tools/scheduler_watchdog.py --once --json      # 机读（单行 JSON）
  python tools/scheduler_watchdog.py --check            # 环境自检，退出码 0/2
  python tools/scheduler_watchdog.py --selftest         # 离线自检（纯函数+夹具），退出码 0/3
  python tools/scheduler_watchdog.py --drill gate-down  # 演练：伪造网关掉线走完整告警链路（绝不真发）
  python tools/scheduler_watchdog.py --alert-test       # 打印模拟告警 payload（落 SAMPLE，绝不发送）
  python tools/scheduler_watchdog.py --test-notify      # 真发一条标注测试的通知（验证通道）
  python tools/scheduler_watchdog.py --once --restart   # 显式开启拉起（默认关闭）
  python tools/scheduler_watchdog.py --install-task     # 打印注册计划任务命令；--apply 才执行
  python tools/scheduler_watchdog.py --uninstall-task --apply   # 注销（回滚）
  python tools/scheduler_watchdog.py --once --hermes-home <dir> --runtime <dir>   # 测试环境（假主目录）

退出码：0=OK/BOOT_GRACE；4=ALERT（有告警，已落文件/按开关发送）；2=--check 环境问题；3=自检失败；1=未预期。
末行 `WATCHDOG=OK|ALERT|BOOT_GRACE|DRILL` 供计划任务/巡检 grep（与 SWITCH_GATE= / SELFTEST= 同风格）。
环境变量：MAAORCH_WATCHDOG_RUNTIME / MAAORCH_DISPATCH_RUNTIME（覆盖 runtime）、
MAAORCH_HERMES_HOME（覆盖 Hermes 主目录）、MAAORCH_SCHEDWD_ALERT_SEND=1（等同 --alert-send）。
"""
from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
DEFAULT_LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")

TICKER_FRESH_S = 60 * 3 + 20          # 与 hermes_cli.cron._ticker_age_is_fresh 同口径（3×tick+20s）
RENOTIFY_MIN = 30                     # 同一告警 30 分钟内不重复通知（与 host_watchdog 同口径）
GRACE_MIN = 15                        # 超期容忍基线（Hermes cron doctor 同口径 15min）
BOOT_GRACE_MIN = 20                   # 开机静默窗（宿主每日 04:00/19:00 计划重启）
TOL_CAP_MIN = 120                     # 容忍窗口上限
RESTART_MIN_INTERVAL_MIN = 20         # 两次自动拉起最小间隔（防循环）
AUDIT_WINDOW_GRACE_MIN = 45           # 发车窗口起 +N 分钟仍无发车审计 ⇒ 告警
AUDIT_WINDOW_END_MIN = 150            # 窗口判定截止（起 +N 分钟后不再用「窗口内无审计」判据）
LOG_MAX_BYTES = 512_000
LOG_KEEP_LINES = 2000

# 发车窗口（班次表 SHIFT_TABLE 的起点；与 tools/mas_dispatch.py 一致）
DISPATCH_WINDOWS = (("daily", 5, 0), ("evening", 19, 30))

TASK_NAME = "MAAOrch-SchedulerWatchdog"
TASK_LAUNCH_PS1 = os.path.join(TOOLS, "scheduler_watchdog_launch.ps1")

SENSITIVE = ("password", "token", "sklandtoken", "secret", "credential", "cookie", "auth_code")


def log(msg: str, log_file: str | None = None) -> None:
    line = "%s  %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    if not log_file:
        return
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(log_file) > LOG_MAX_BYTES:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines[-LOG_KEEP_LINES:]) + "\n")
    except OSError:
        pass


def now_dt() -> dt.datetime:
    return dt.datetime.now()


def now_aware() -> dt.datetime:
    return dt.datetime.now().astimezone()


def now_str() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def save_json_atomic(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def scrub(o):
    """读入即丢弃凭据类键（一切外部数据都过它；看门狗只读，但载荷可能进日志）。"""
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items() if not any(s in str(k).lower() for s in SENSITIVE)}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


def iso_parse(s):
    """ISO 时间串 → aware datetime（naive 按本地时区补齐）；失败回 None。"""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).strip())
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return t


def parse_ts_loose(s):
    """'2026-09-28 05:57:04' / ISO 都吃；失败回 None。"""
    t = iso_parse(s)
    if t is not None:
        return t
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(str(s).strip()[:19], fmt).replace(
                tzinfo=dt.datetime.now().astimezone().tzinfo)
        except ValueError:
            continue
    return None


def ago_s_str(seconds) -> str:
    if seconds is None:
        return "?"
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "%ds" % seconds
    minutes = seconds // 60
    if minutes < 90:
        return "%dm" % minutes
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return "%dh%02dm" % (hours, minutes)
    return "%dd%02dh" % (hours // 24, hours % 24)


# ---------------------------------------------------------------- OS 探针

def pid_alive(pid) -> bool:
    """进程是否活着。Windows: OpenProcess+GetExitCodeProcess；ACCESS_DENIED(5)=存在但不可开。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return k32.GetLastError() == 5
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def system_uptime_min():
    """开机时长（分钟）；取不到回 None。"""
    if os.name == "nt":
        try:
            import ctypes
            return ctypes.windll.kernel32.GetTickCount64() / 60000.0
        except Exception:  # noqa: BLE001
            return None
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            return float(f.read().split()[0]) / 60.0
    except OSError:
        return None


def tcp_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=4):
            return True
    except OSError:
        return False


def listening_ports_for_pid(pid) -> list:
    """netstat -ano 里该 PID 的本地 LISTEN 端口列表（只取本地地址；辅助判据）。"""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, errors="replace",
                           timeout=25, stdin=subprocess.DEVNULL, creationflags=NOWIN)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in (r.stdout or "").splitlines():
        f = ln.split()
        if len(f) >= 5 and f[0].upper() == "TCP" and f[3].upper() == "LISTENING" and f[4] == str(pid):
            addr = f[1]
            host, _, port = addr.rpartition(":")
            if port.isdigit():
                out.append({"addr": addr, "host": host.strip("[]"), "port": int(port)})
    return out


def git_head(dirpath: str):
    try:
        r = subprocess.run(["git", "-C", dirpath, "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=15, stdin=subprocess.DEVNULL, creationflags=NOWIN)
        out = (r.stdout or "").strip()
        return out if r.returncode == 0 and out else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 路径解析

def resolve_hermes_home(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    env = (os.environ.get("MAAORCH_HERMES_HOME") or "").strip()
    if env:
        return os.path.abspath(env)
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        p = os.path.abspath(env)
        if os.path.isdir(os.path.join(p, "profiles")):
            return p
        # 形如 <root>\profiles\<name> 的 profile 级 HERMES_HOME：回退到 root
        if os.path.basename(os.path.dirname(p)) == "profiles":
            return os.path.dirname(os.path.dirname(p))
    return os.path.join(DEFAULT_LOCALAPPDATA, "hermes")


def resolve_runtime(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    for env in ("MAAORCH_WATCHDOG_RUNTIME", "MAAORCH_DISPATCH_RUNTIME"):
        v = (os.environ.get(env) or "").strip()
        if v:
            return os.path.abspath(v)
    return os.path.join(ROOT, "runtime")


def load_cfg(runtime: str) -> dict:
    """runtime/scheduler_watchdog.ini（可选）；缺省全部内置默认。"""
    cfg = {"profiles": ["mas"], "grace_min": GRACE_MIN, "renotify_min": RENOTIFY_MIN,
           "boot_grace_min": BOOT_GRACE_MIN, "restart_cmd": "", "restart_wait_s": 90,
           "restart_min_interval_min": RESTART_MIN_INTERVAL_MIN, "port_check": "auto"}
    path = os.path.join(runtime, "scheduler_watchdog.ini")
    if not os.path.exists(path):
        return cfg
    c = configparser.ConfigParser()
    try:
        c.read(path, encoding="utf-8")
    except Exception:  # noqa: BLE001
        return cfg
    if c.has_section("watchdog"):
        s = c["watchdog"]
        if s.get("profiles"):
            cfg["profiles"] = [x.strip() for x in s["profiles"].split(",") if x.strip()]
        for k, typ in (("grace_min", int), ("renotify_min", int), ("boot_grace_min", int),
                       ("restart_wait_s", int), ("restart_min_interval_min", int)):
            if s.get(k):
                try:
                    cfg[k] = typ(s[k])
                except ValueError:
                    pass
        if s.get("restart_cmd"):
            cfg["restart_cmd"] = s["restart_cmd"].strip()
        if s.get("port_check"):
            cfg["port_check"] = s["port_check"].strip()
    return cfg


# ---------------------------------------------------------------- ① gateway

def collect_gateway(hermes_home: str, profiles: list, cfg: dict) -> dict:
    """gateway 进程/端口/心跳。返回 {ok, details, findings}。"""
    out = {"pid": None, "pid_alive": None, "pid_file": None, "state": None, "ports": [],
           "profiles": {}, "stale_code": None, "notes": [], "findings": []}

    pid_path = os.path.join(hermes_home, "gateway.pid")
    pid_obj = load_json(pid_path)
    out["pid_file"] = pid_path
    if not isinstance(pid_obj, dict) or not pid_obj.get("pid"):
        out["findings"].append({
            "kind": "gateway_down", "severity": "critical", "subject": "gateway.pid",
            "detail": "gateway.pid 缺失或不可解析 —— gateway 没在跑（或从未启动）",
            "evidence": {"pid_file": pid_path}})
    else:
        pid = int(pid_obj["pid"])
        out["pid"] = pid
        alive = pid_alive(pid)
        out["pid_alive"] = alive
        if not alive:
            out["findings"].append({
                "kind": "gateway_down", "severity": "critical", "subject": "pid=%s" % pid,
                "detail": "gateway 进程不在了（gateway.pid 记录 pid=%s，进程无响应）—— cron 不会触发" % pid,
                "evidence": {"pid": pid, "start_time": pid_obj.get("start_time"),
                             "argv": pid_obj.get("argv")}})
        else:
            pc = str(cfg.get("port_check") or "auto")
            ports = []
            if pc not in ("off", "none", "0"):
                if pc.isdigit():
                    ports = [{"addr": "127.0.0.1:%s" % pc, "host": "127.0.0.1", "port": int(pc)}]
                else:
                    ports = listening_ports_for_pid(pid)
                for p in ports:
                    host = p.get("host") or "127.0.0.1"
                    if host in ("0.0.0.0", "::", ""):
                        host = "127.0.0.1"
                    p["reachable"] = tcp_ok(host, int(p["port"]))
                out["ports"] = ports
                dead = [p for p in ports if not p.get("reachable")]
                if dead and str(pc).isdigit():
                    out["findings"].append({
                        "kind": "gateway_port_dead", "severity": "warn", "subject": "port=%s" % pc,
                        "detail": "进程活着但本地端口 %s 连不上（端口检查为辅助判据）" % pc,
                        "evidence": {"ports": dead}})

    state = load_json(os.path.join(hermes_home, "gateway_state.json"))
    if isinstance(state, dict):
        out["state"] = scrub({k: state.get(k) for k in
                              ("gateway_state", "updated_at", "exit_reason", "restart_requested",
                               "served_profiles", "code_version", "active_agents")})
        st = str(state.get("gateway_state") or "")
        if st and st not in ("running",):
            out["findings"].append({
                "kind": "gateway_down", "severity": "critical", "subject": "gateway_state=%s" % st,
                "detail": "gateway_state.json 状态=%s（非 running）" % st,
                "evidence": out["state"]})
        up = iso_parse(state.get("updated_at"))
        if up is not None:
            age = (now_dt().astimezone(dt.timezone.utc) - up.astimezone(dt.timezone.utc)).total_seconds()
            out["state_updated_age_s"] = age
            if age > 30 * 60 and out.get("pid_alive") is False:
                pass  # 进程已死，上面已报，不再叠加
            elif age > 30 * 60:
                out["findings"].append({
                    "kind": "gateway_state_stale", "severity": "warn", "subject": "updated_at",
                    "detail": "gateway_state.json 已 %.0f 分钟未更新（进程在但状态面停更）" % (age / 60),
                    "evidence": out["state"]})
        # 陈旧代码（gateway 起于旧 commit ⇒ ticker 空转、什么都不触发）
        sha = str(state.get("code_sha") or "")
        checkout = os.path.join(hermes_home, "hermes-agent")
        head = git_head(checkout) if os.path.isdir(os.path.join(checkout, ".git")) else None
        if sha and head:
            out["stale_code"] = {"gateway_code_sha": sha, "checkout_head": head, "match": sha == head}
            if sha != head:
                out["findings"].append({
                    "kind": "stale_code", "severity": "warn", "subject": "gateway",
                    "detail": "gateway 跑的是旧 commit（%s），checkout 已是 %s —— ticker 会空转"
                              "什么都不触发，需重启 gateway" % (sha[:10], head[:10]),
                    "evidence": out["stale_code"]})

    for prof in profiles:
        cdir = os.path.join(hermes_home, "profiles", prof, "cron")
        pv = {"cron_dir": cdir, "exists": os.path.isdir(cdir)}
        hb_age = ok_age = None
        for fname, key in (("ticker_heartbeat", "hb_age_s"), ("ticker_last_success", "ok_age_s")):
            p = os.path.join(cdir, fname)
            try:
                with open(p, "r", encoding="utf-8-sig") as f:
                    secs = float(f.read().strip() or 0)
                age = time.time() - secs
                pv[key] = age
                if key == "hb_age_s":
                    hb_age = age
                else:
                    ok_age = age
            except Exception:  # noqa: BLE001
                pv[key] = None
        err_path = os.path.join(cdir, "ticker_last_error")
        if os.path.exists(err_path):
            try:
                with open(err_path, "r", encoding="utf-8-sig", errors="replace") as f:
                    pv["last_error"] = f.read().strip()[:300]
            except OSError:
                pv["last_error"] = None
        out["profiles"][prof] = pv

        tag = "profiles/%s" % prof
        if not pv["exists"]:
            out["notes"].append("%s: cron 存储不存在（跳过心跳判定）" % tag)
            continue
        if hb_age is None:
            out["findings"].append({
                "kind": "ticker_stalled", "severity": "critical", "subject": tag,
                "detail": "cron ticker 心跳文件缺失/不可读 —— 该 profile 的 cron 不会触发",
                "evidence": {"cron_dir": cdir}})
        elif hb_age > TICKER_FRESH_S:
            out["findings"].append({
                "kind": "ticker_stalled", "severity": "critical", "subject": tag,
                "detail": "cron ticker 停摆：心跳已 %s 未更新（阈值 %ds）—— 到点不会触发任何任务"
                          % (ago_s_str(hb_age), TICKER_FRESH_S),
                "evidence": {"hb_age_s": round(hb_age), "cron_dir": cdir}})
        if ok_age is None and pv["exists"]:
            if hb_age is not None and hb_age <= TICKER_FRESH_S:
                out["findings"].append({
                    "kind": "ticks_failing", "severity": "warn", "subject": tag,
                    "detail": "ticker 心跳新但从未成功过（ticker_last_success 缺失/不可读）",
                    "evidence": {"cron_dir": cdir, "last_error": pv.get("last_error")}})
        elif ok_age is not None and ok_age > TICKER_FRESH_S:
            out["findings"].append({
                "kind": "ticks_failing", "severity": "critical", "subject": tag,
                "detail": "ticker 在跑但连着失败：最近一次成功 tick 在 %s 前（阈值 %ds）"
                          % (ago_s_str(ok_age), TICKER_FRESH_S),
                "evidence": {"ok_age_s": round(ok_age), "last_error": pv.get("last_error")}})
        elif pv.get("last_error") and hb_age is not None and hb_age <= TICKER_FRESH_S:
            out["notes"].append("%s: 心跳正常但存在历史 tick 错误记录（%s）"
                                % (tag, str(pv.get("last_error"))[:120]))
    return out


# ---------------------------------------------------------------- cron schedule 解析（容忍窗口）

def _field_values(spec: str, lo: int, hi: int):
    vals = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, _, st = part.partition("/")
            try:
                step = max(1, int(st))
            except ValueError:
                step = 1
        if part in ("*", "?"):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            try:
                a = b = int(part)
            except ValueError:
                return None
        a = max(lo, min(hi, a))
        b = max(lo, min(hi, b))
        vals.update(range(a, b + 1, step))
    return vals


def cron_fire_minutes(expr: str, start: dt.datetime, days: float = 8.0):
    """枚举 [start, start+days) 内该 5 段 cron 的触发分钟（分钟粒度）。

    仅用于估算发车间隔 / 容忍窗口；不实现 cron 的 dom/dow OR 语义等边角。
    """
    f = str(expr or "").split()
    if len(f) != 5:
        return None
    m_f, h_f, dom_f, mon_f, dow_f = f
    mins = _field_values(m_f, 0, 59)
    hrs = _field_values(h_f, 0, 23)
    doms = _field_values(dom_f, 1, 31)
    mons = _field_values(mon_f, 1, 12)
    dows = _field_values(dow_f, 0, 6)
    if None in (mins, hrs, doms, mons, dows):
        return None
    out = []
    t = start.replace(second=0, microsecond=0)
    end = start + dt.timedelta(days=days)
    one = dt.timedelta(minutes=1)
    while t < end:
        if (t.minute in mins and t.hour in hrs and t.day in doms and t.month in mons
                and (t.weekday() + 1) % 7 in dows):
            out.append(t)
        t += one
    return out


def schedule_interval_min(sched: dict):
    """发车间隔（分钟，取序列里的最小间隔）；无法估算回 None。"""
    kind = str((sched or {}).get("kind") or "")
    if kind == "interval":
        try:
            return max(1, int((sched or {}).get("minutes") or 1))
        except (TypeError, ValueError):
            return None
    if kind == "cron":
        fires = cron_fire_minutes(str((sched or {}).get("expr") or ""), now_dt(), days=8)
        if not fires or len(fires) < 2:
            return None
        gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(fires, fires[1:])]
        return max(1, int(min(gaps)))
    return None      # once / 其他：不做超期判定（next_run_at 无周期含义）


def job_tolerance_min(sched: dict, grace_min: int) -> int:
    """容忍窗口 = min(上限, max(基线, 发车间隔+5min))。"""
    iv = schedule_interval_min(sched)
    base = max(1, int(grace_min))
    if iv is None:
        return base
    return min(TOL_CAP_MIN, max(base, iv + 5))


# ---------------------------------------------------------------- ② cron 任务/执行台账

def last_executions(db_path: str, job_id: str, limit: int = 3) -> list:
    """executions.db 只读：最近若干次执行（claimed_at 即「最近一次执行时间」口径）。"""
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("?", "%3f"), uri=True, timeout=8)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT id, status, source, claimed_at, started_at, finished_at, error "
                "FROM executions WHERE job_id = ? ORDER BY claimed_at DESC, id DESC LIMIT ?",
                (job_id, limit)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def collect_jobs(hermes_home: str, profiles: list, cfg: dict) -> dict:
    """cron 任务超期 + 最近执行时间。返回 {jobs:[...], findings:[...], notes:[...]}"""
    grace = int(cfg.get("grace_min") or GRACE_MIN)
    out = {"jobs": [], "findings": [], "notes": []}
    for prof in profiles:
        cdir = os.path.join(hermes_home, "profiles", prof, "cron")
        jobs_obj = load_json(os.path.join(cdir, "jobs.json"))
        if not isinstance(jobs_obj, dict):
            out["notes"].append("profiles/%s: jobs.json 不可读 —— 无法做超期判定" % prof)
            continue
        exdb = os.path.join(cdir, "executions.db")
        for job in (jobs_obj.get("jobs") or []):
            if not isinstance(job, dict):
                continue
            jid = str(job.get("id") or "?")
            name = str(job.get("name") or jid)
            rec = {"profile": prof, "id": jid, "name": name,
                   "enabled": bool(job.get("enabled")), "state": job.get("state"),
                   "schedule": job.get("schedule_display") or "",
                   "next_run_at": job.get("next_run_at"), "last_run_at": job.get("last_run_at"),
                   "last_status": job.get("last_status"), "failure_streak": job.get("failure_streak"),
                   "last_error": job.get("last_error")}
            if not rec["enabled"] or rec["state"] in ("paused", "completed"):
                rec["skip"] = "未启用/已暂停"
                out["jobs"].append(rec)
                continue
            # 最近一次执行（台账优先，jobs.json 兜底）
            execs = last_executions(exdb, jid)
            rec["last_executions"] = [{k: e.get(k) for k in ("id", "status", "source", "claimed_at")}
                                      for e in execs]
            last_claim = execs[0].get("claimed_at") if execs else None
            rec["last_exec_claimed_at"] = last_claim or rec["last_run_at"]
            rec["last_exec_status"] = execs[0].get("status") if execs else job.get("last_status")
            if execs and execs[0].get("error"):
                rec["last_exec_error"] = str(execs[0]["error"])[:200]
            # 超期判定（next_run_at）
            tol = job_tolerance_min(job.get("schedule") or {}, grace)
            rec["tolerance_min"] = tol
            stamp = iso_parse(job.get("next_run_at"))
            if stamp is None:
                rec["overdue_s"] = None
                rec["note"] = "next_run_at 不可解析（跳过超期判定）"
                out["notes"].append("profiles/%s %s: next_run_at 不可解析" % (prof, name))
            else:
                overdue_s = (now_dt().astimezone(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc)).total_seconds()
                rec["overdue_s"] = round(overdue_s)
                if overdue_s > tol * 60:
                    out["findings"].append({
                        "kind": "job_overdue", "severity": "critical",
                        "subject": "%s/%s" % (prof, name),
                        "detail": "任务超期未触发：next_run_at=%s 已过 %s（容忍 %dmin，按 schedule %s）——"
                                  "最近一次执行=%s（%s）"
                                  % (job.get("next_run_at"), ago_s_str(overdue_s), tol,
                                     rec["schedule"], rec["last_exec_claimed_at"] or "无记录",
                                     rec["last_exec_status"] or "?"),
                        "evidence": {"job_id": jid, "tolerance_min": tol, "overdue_s": round(overdue_s),
                                     "schedule": rec["schedule"], "last_exec": rec["last_exec_claimed_at"],
                                     "next_run_at": job.get("next_run_at")}})
            # 连续失败（≥2 次）
            try:
                fs = int(job.get("failure_streak") or 0)
            except (TypeError, ValueError):
                fs = 0
            if fs >= 2:
                out["findings"].append({
                    "kind": "job_failing", "severity": "warn",
                    "subject": "%s/%s" % (prof, name),
                    "detail": "任务连续失败 %d 次：last_status=%s，last_error=%s"
                              % (fs, job.get("last_status"), str(job.get("last_error"))[:200]),
                    "evidence": {"job_id": jid, "failure_streak": fs}})
            out["jobs"].append(rec)
    return out


# ---------------------------------------------------------------- ③ 发车审计

def collect_audit(runtime: str) -> dict:
    """最近发车审计时间 + 未消除告警/late 标记。只读。"""
    out = {"log_ts": None, "state_ts": None, "last_ts": None, "last_age_s": None,
           "today_sent": [], "active_alerts": [], "late": {}, "findings": [], "notes": []}
    log_path = os.path.join(runtime, "mas_dispatch_log.jsonl")
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                t = parse_ts_loose(rec.get("ts"))
                if t is not None:
                    out["log_ts"] = max(out["log_ts"], t) if out["log_ts"] else t
    except OSError:
        out["notes"].append("mas_dispatch_log.jsonl 不可读")
    today = now_dt().date()
    st = load_json(os.path.join(runtime, "mas_dispatch_state.json"))
    if isinstance(st, dict):
        day = st.get(today.isoformat()) or {}
        for q, v in day.items():
            if isinstance(v, dict) and v.get("at"):
                t = parse_ts_loose(v.get("at"))
                if t is not None:
                    out["state_ts"] = max(out["state_ts"], t) if out["state_ts"] else t
                    out["today_sent"].append({"queue": q, "at": v.get("at")})
        out["late"] = {k: v for k, v in (st.get("late") or {}).items() if isinstance(v, dict)}
    al = load_json(os.path.join(runtime, "mas_dispatch_alerts.json"))
    if isinstance(al, dict):
        out["active_alerts"] = [scrub(e) for e in (al.get("active") or [])
                                if isinstance(e, dict) and not e.get("test")]
    cand = [t for t in (out["log_ts"], out["state_ts"]) if t is not None]
    if cand:
        out["last_ts"] = max(cand)
        out["last_age_s"] = round((now_aware() - out["last_ts"]).total_seconds())

    # 发车窗口内无任何审计 ⇒ 告警（窗口起 +45min 起判，+150min 后不再判）
    n = now_dt()
    for label, hh, mm in DISPATCH_WINDOWS:
        w = n.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not (w + dt.timedelta(minutes=AUDIT_WINDOW_GRACE_MIN)
                <= n <= w + dt.timedelta(minutes=AUDIT_WINDOW_END_MIN)):
            continue
        sent_in_window = [e for e in out["today_sent"]
                          if (parse_ts_loose(e.get("at")) or w) >= w]
        if not sent_in_window:
            out["findings"].append({
                "kind": "audit_stale", "severity": "critical", "subject": "dispatch",
                "detail": "%s发车窗口（%02d:%02d 起）已过 %d 分钟，但今天没有任何发车审计"
                          "（mas_dispatch_state/log 均无本窗口记录）—— 到点没发车"
                          % ({"daily": "日常", "evening": "晚班"}[label], hh, mm,
                             int((n - w).total_seconds() // 60)),
                "evidence": {"window_start": w.strftime("%Y-%m-%d %H:%M:%S"),
                             "last_audit_ts": out["last_ts"].strftime("%Y-%m-%d %H:%M:%S")
                             if out["last_ts"] else None,
                             "today_sent_n": len(out["today_sent"])}})
    if out["active_alerts"]:
        out["notes"].append("mas_dispatch_alerts 未消除 %d 条（%s）"
                            % (len(out["active_alerts"]),
                               "、".join("%s:%s" % (e.get("queue"), e.get("reason"))
                                        for e in out["active_alerts"][:6])))
    if out["late"]:
        out["notes"].append("late 标记：%s" % "、".join(list(out["late"].keys())[:8]))
    return out


# ---------------------------------------------------------------- 汇总模型

def build_model(opts: dict) -> dict:
    hermes_home = opts["hermes_home"]
    runtime = opts["runtime"]
    cfg = opts["cfg"]
    profiles = cfg["profiles"]
    model = {"hermes_home": hermes_home, "runtime": runtime, "now": now_str(),
             "now_dt": now_dt(), "cfg": {k: v for k, v in cfg.items() if k != "restart_cmd"},
             "notes": []}
    up = system_uptime_min()
    model["uptime_min"] = up
    model["boot_grace"] = bool(up is not None and up < cfg.get("boot_grace_min", BOOT_GRACE_MIN))
    model["gateway"] = collect_gateway(hermes_home, profiles, cfg)
    model["cron"] = collect_jobs(hermes_home, profiles, cfg)
    model["audit"] = collect_audit(runtime)
    findings = (list(model["gateway"]["findings"]) + list(model["cron"]["findings"])
                + list(model["audit"]["findings"]))
    model["findings"] = findings
    return model


def drill_model(model: dict, kind: str) -> dict:
    """演练：伪造找据（不碰真实健康态），走完整告警链路（drill 模式绝不真发）。"""
    if kind in ("gate-down", "all"):
        model["gateway"]["findings"] = [f for f in model["gateway"]["findings"]
                                        if f["kind"] != "gateway_down"]
        model["gateway"]["findings"].append({
            "kind": "gateway_down", "severity": "critical", "subject": "pid=（演练）",
            "detail": "【演练】伪造 gateway 进程死亡 —— 验证告警链路用，未触碰真实进程",
            "evidence": {"drill": True}})
    if kind in ("overdue", "all"):
        model["cron"]["findings"] = [f for f in model["cron"]["findings"] if f["kind"] != "job_overdue"]
        model["cron"]["findings"].append({
            "kind": "job_overdue", "severity": "critical", "subject": "mas/mas-shift-dispatch（演练）",
            "detail": "【演练】伪造 cron 任务超期未触发（阈值调小语义）—— 验证告警链路用",
            "evidence": {"drill": True, "tolerance_min": 0}})
    if kind in ("audit-stale", "all"):
        model["audit"]["findings"] = [f for f in model["audit"]["findings"] if f["kind"] != "audit_stale"]
        model["audit"]["findings"].append({
            "kind": "audit_stale", "severity": "critical", "subject": "dispatch（演练）",
            "detail": "【演练】伪造发车窗口内无审计 —— 验证告警链路用",
            "evidence": {"drill": True}})
    model["findings"] = (list(model["gateway"]["findings"]) + list(model["cron"]["findings"])
                         + list(model["audit"]["findings"]))
    model["drill"] = kind
    return model


def verdict(model: dict) -> str:
    if model.get("drill"):
        return "DRILL"
    if model.get("boot_grace"):
        return "BOOT_GRACE"
    return "ALERT" if model["findings"] else "OK"


# ---------------------------------------------------------------- 告警状态 / 渲染 / 发送

def alert_key(f: dict) -> str:
    return "%s|%s" % (f.get("kind"), f.get("subject"))


def alert_state_update(state: dict, findings: list, renotify_min: int) -> tuple:
    """返回 (emit_targets, cleared)。state['alerts'][key] = {first,last,last_emit,last_send,...}"""
    alerts = state.setdefault("alerts", {})
    now = now_str()
    emit, cleared = [], []
    seen = set()
    for f in findings:
        k = alert_key(f)
        seen.add(k)
        rec = alerts.get(k)
        if rec is None:
            alerts[k] = {"first": now, "last": now, "last_emit": None, "last_send": None,
                         "kind": f.get("kind"), "subject": f.get("subject"), "severity": f.get("severity")}
            emit.append(f)
        else:
            rec["last"] = now
            rec["severity"] = f.get("severity") or rec.get("severity")
            le = iso_parse(rec.get("last_emit"))
            if le is None or (now_dt().astimezone(dt.timezone.utc)
                              - le.astimezone(dt.timezone.utc)).total_seconds() > renotify_min * 60:
                emit.append(f)
    for k, rec in list(alerts.items()):
        if k not in seen and not rec.get("cleared"):
            rec["cleared"] = True
            rec["cleared_at"] = now
            if rec.get("last_emit"):
                cleared.append("%s:%s" % (rec.get("kind"), rec.get("subject")))
    return emit, cleared


def mark_emitted(state: dict, findings: list) -> None:
    alerts = state.setdefault("alerts", {})
    now = now_str()
    for f in findings:
        rec = alerts.get(alert_key(f))
        if rec is not None:
            rec["last_emit"] = now


def mark_sent(state: dict, findings: list) -> None:
    alerts = state.setdefault("alerts", {})
    now = now_str()
    for f in findings:
        rec = alerts.get(alert_key(f))
        if rec is not None:
            rec["last_send"] = now


def render_payload(model: dict, findings: list, emit_targets: list, cleared: list,
                   runtime: str, send: bool, channel: str) -> dict:
    kinds = "、".join(sorted({str(f.get("kind")) for f in findings})) or "无"
    title = ("[MAAOrch] 调度看门狗 · %s · %d 项" % (model["now"][:16], len(findings))) \
        if findings else "[MAAOrch] 调度看门狗 · 正常"
    lines = ["调度看门狗 %s（主机开机 %s，hermes=%s）"
             % (model["now"], ago_s_str((model.get("uptime_min") or 0) * 60), model["hermes_home"]),
             "判定: %s ｜ 告警项: %s" % (verdict(model), kinds)]
    for f in findings:
        lines.append("[%s] %s — %s" % (f.get("severity"), f.get("kind"), f.get("detail")))
    gw = model.get("gateway") or {}
    lines.append("gateway: pid=%s alive=%s 心跳=%s"
                 % (gw.get("pid"), gw.get("pid_alive"),
                    "、".join("%s:%s" % (p, ago_s_str(v.get("hb_age_s")))
                              for p, v in (gw.get("profiles") or {}).items()) or "?"))
    au = model.get("audit") or {}
    lines.append("最近发车审计: %s（%s前）｜ 今日已发 %d 条"
                 % (au["last_ts"].strftime("%Y-%m-%d %H:%M:%S") if au.get("last_ts") else "无记录",
                    ago_s_str(au.get("last_age_s")), len(au.get("today_sent") or [])))
    if cleared:
        lines.append("已消除: " + "、".join(cleared))
    return {
        "schema": "maaorch.scheduler_watchdog/1",
        "title": title,
        "generated_at": model["now"],
        "host": socket.gethostname(),
        "hermes_home": model["hermes_home"],
        "verdict": verdict(model),
        "drill": model.get("drill"),
        "findings": findings,
        "emit_targets": emit_targets,
        "cleared_events": cleared,
        "gateway": {k: v for k, v in (model.get("gateway") or {}).items() if k != "findings"},
        "cron": {k: v for k, v in (model.get("cron") or {}).items() if k != "findings"},
        "audit": {k: v for k, v in (model.get("audit") or {}).items() if k != "findings"},
        "summary": "\n".join(lines),
        "channel": channel,
        "send": bool(send),
    }


def write_alert_files(payload: dict, runtime: str, tag: str = "") -> list:
    reports = os.path.join(runtime, "reports")
    os.makedirs(reports, exist_ok=True)
    outs = []
    path = os.path.join(reports, "scheduler_watchdog_alert_%s%s.json"
                        % (now_dt().strftime("%Y%m%d_%H%M%S"), tag))
    save_json_atomic(path, payload)
    outs.append(path)
    log_path = os.path.join(runtime, "scheduler_watchdog_alerts.jsonl")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": payload.get("generated_at"), "verdict": payload.get("verdict"),
                                "n": len(payload.get("findings") or []),
                                "new": len(payload.get("emit_targets") or []),
                                "send": payload.get("send"), "channel": payload.get("channel"),
                                "kinds": sorted({str(x.get("kind")) for x in
                                                 (payload.get("findings") or [])})},
                               ensure_ascii=False) + "\n")
        outs.append(log_path)
    except OSError:
        pass
    return outs


def send_notify(subject: str, body: str) -> dict:
    """复用 host_watchdog 的通知配置与实现（runtime/watchdog.ini），与班次日报/主机看门狗同源。"""
    try:
        sys.path.insert(0, TOOLS)
        import host_watchdog  # noqa: PLC0415
        c = host_watchdog.load_conf()
        host_watchdog.notify(c, subject, body, send=True)
        return {"ok": True, "channel": c["notify"].get("channel") or "email"}
    except SystemExit as e:
        return {"ok": False, "error": "watchdog.ini 缺失: %s" % e}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


# ---------------------------------------------------------------- 拉起（可选，默认关）

def default_restart_cmd(hermes_home: str) -> str:
    for cand in (os.path.join(hermes_home, "bin", "hermes.exe"),
                 os.path.join(DEFAULT_LOCALAPPDATA, "hermes", "bin", "hermes.exe")):
        if os.path.exists(cand):
            return '"%s" --profile default gateway restart' % cand
    return "hermes --profile default gateway restart"


def do_restart(model: dict, cfg: dict, cmd: str, wait_s: int) -> dict:
    """执行拉起命令并复验。返回 {ok, cmd, rc, output, verify}"""
    res = {"cmd": cmd, "at": now_str(), "rc": None, "output": "", "ok": False, "verify": None}
    env = dict(os.environ)
    env["MAAORCH_WATCHDOG_PID"] = str(os.getpid())     # 演练脚本可用它伪造「进程回来了」
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="replace",
                           timeout=180, stdin=subprocess.DEVNULL, env=env, cwd=ROOT)
        res["rc"] = r.returncode
        res["output"] = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()[-800:]
    except subprocess.TimeoutExpired:
        res["output"] = "timeout(180s)"
    # 复验：进程活 + 目标 profile 心跳新鲜
    deadline = time.time() + max(0, int(wait_s))
    prof0 = (cfg.get("profiles") or ["mas"])[0]
    hb = os.path.join(model["hermes_home"], "profiles", prof0, "cron", "ticker_heartbeat")
    while True:
        pid_obj = load_json(os.path.join(model["hermes_home"], "gateway.pid")) or {}
        alive = bool(pid_obj.get("pid")) and pid_alive(pid_obj.get("pid"))
        fresh = False
        try:
            with open(hb, "r", encoding="utf-8-sig") as f:
                fresh = (time.time() - float(f.read().strip() or 0)) <= TICKER_FRESH_S
        except Exception:  # noqa: BLE001
            fresh = False
        res["verify"] = {"pid": pid_obj.get("pid"), "alive": alive, "heartbeat_fresh": fresh,
                         "waited_s": int(max(0, wait_s - max(0, deadline - time.time())))}
        if alive and fresh:
            res["ok"] = True
            break
        if time.time() >= deadline:
            break
        time.sleep(10)
    return res


# ---------------------------------------------------------------- 自检 / 检查

def cmd_selftest() -> int:
    """离线自检：纯函数 + 夹具（不连网、不写真实 runtime）。退出码 0/3。"""
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)
        print("  %s %s" % ("ok " if cond else "FAIL", name))

    # cron 解析 / 间隔
    check("cron */3 5,19,20 最小间隔=3", schedule_interval_min({"kind": "cron", "expr": "*/3 5,19,20 * * *"}) == 3)
    check("cron 55 23 最小间隔=1440", schedule_interval_min({"kind": "cron", "expr": "55 23 * * *"}) == 1440)
    check("interval 30m 间隔=30", schedule_interval_min({"kind": "interval", "minutes": 30}) == 30)
    check("坏 cron 回 None", schedule_interval_min({"kind": "cron", "expr": "bad expr"}) is None)
    # 容忍窗口
    check("容忍 */3 → 15min（基线）", job_tolerance_min({"kind": "cron", "expr": "*/3 5,19,20 * * *"}, 15) == 15)
    check("容忍 30m → 35min", job_tolerance_min({"kind": "interval", "minutes": 30}, 15) == 35)
    check("容忍日级 → 上限 120", job_tolerance_min({"kind": "cron", "expr": "55 23 * * *"}, 15) == 120)

    # 超期判定夹具
    jobs = {"jobs": [{"id": "j1", "name": "dispatch", "enabled": True, "state": "scheduled",
                      "schedule": {"kind": "cron", "expr": "*/3 5,19,20 * * *"},
                      "next_run_at": (now_dt() - dt.timedelta(minutes=20)).isoformat(),
                      "last_run_at": (now_dt() - dt.timedelta(minutes=20)).isoformat()}]}
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "profiles", "mas", "cron"), exist_ok=True)
        with open(os.path.join(td, "profiles", "mas", "cron", "jobs.json"), "w", encoding="utf-8") as f:
            json.dump(jobs, f)
        col = collect_jobs(td, ["mas"], {"grace_min": 15})
        check("超期夹具 → job_overdue", any(f["kind"] == "job_overdue" for f in col["findings"]))
        # 不超期（5 分钟前）
        jobs["jobs"][0]["next_run_at"] = (now_dt() - dt.timedelta(minutes=5)).isoformat()
        with open(os.path.join(td, "profiles", "mas", "cron", "jobs.json"), "w", encoding="utf-8") as f:
            json.dump(jobs, f)
        col = collect_jobs(td, ["mas"], {"grace_min": 15})
        check("未超期夹具 → 无 job_overdue", not any(f["kind"] == "job_overdue" for f in col["findings"]))
        # 禁用任务跳过
        jobs["jobs"][0]["enabled"] = False
        with open(os.path.join(td, "profiles", "mas", "cron", "jobs.json"), "w", encoding="utf-8") as f:
            json.dump(jobs, f)
        col = collect_jobs(td, ["mas"], {"grace_min": 15})
        check("禁用任务跳过", not col["findings"] and col["jobs"][0].get("skip"))

    # gateway 心跳夹具
    with tempfile.TemporaryDirectory() as td:
        cdir = os.path.join(td, "profiles", "mas", "cron")
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "ticker_heartbeat"), "w", encoding="utf-8") as f:
            f.write(str(time.time() - 9999))
        with open(os.path.join(cdir, "ticker_last_success"), "w", encoding="utf-8") as f:
            f.write(str(time.time() - 9999))
        with open(os.path.join(td, "gateway.pid"), "w", encoding="utf-8") as f:
            json.dump({"pid": 999999, "kind": "hermes-gateway"}, f)
        gw = collect_gateway(td, ["mas"], {"port_check": "off"})
        kinds = {f["kind"] for f in gw["findings"]}
        check("夹具：pid 死 → gateway_down", "gateway_down" in kinds)
        check("夹具：心跳旧 → ticker_stalled", "ticker_stalled" in kinds)
        # 活 pid + 新心跳 → 无 finding
        with open(os.path.join(cdir, "ticker_heartbeat"), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        with open(os.path.join(cdir, "ticker_last_success"), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        with open(os.path.join(td, "gateway.pid"), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "kind": "hermes-gateway"}, f)
        gw = collect_gateway(td, ["mas"], {"port_check": "off"})
        check("夹具：活 pid + 新心跳 → 无 finding", not gw["findings"])

    # pid_alive
    check("pid_alive(本进程)=True", pid_alive(os.getpid()) is True)
    check("pid_alive(0)=False", pid_alive(0) is False)

    # 发车审计夹具
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "mas_dispatch_log.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-09-27 05:00:16", "kind": "dispatch_ok"}) + "\n")
        au = collect_audit(td)
        check("审计夹具：last_ts 解析", au["last_ts"] is not None and au["last_ts"].year == 2026)
        # 窗口判据（纯逻辑复算：把 now 换成窗口内）
        w = now_dt().replace(hour=5, minute=0, second=0, microsecond=0)
        in_win = (w + dt.timedelta(minutes=AUDIT_WINDOW_GRACE_MIN)
                  <= w + dt.timedelta(minutes=60) <= w + dt.timedelta(minutes=AUDIT_WINDOW_END_MIN))
        check("窗口判据样例", in_win)

    # 告警状态机
    state = {}
    f1 = {"kind": "gateway_down", "subject": "pid=1", "severity": "critical"}
    emit, cleared = alert_state_update(state, [f1], 30)
    check("状态机：首见 → emit 1", len(emit) == 1 and not cleared)
    mark_emitted(state, [f1])
    emit, cleared = alert_state_update(state, [f1], 30)
    check("状态机：重复 → 不 emit", not emit and not cleared)
    emit, cleared = alert_state_update(state, [], 30)
    check("状态机：消失 → cleared 1", not emit and len(cleared) == 1)

    # 演练模型
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "profiles", "mas", "cron"), exist_ok=True)
        model = {"hermes_home": td, "runtime": td, "now": now_str(), "now_dt": now_dt(),
                 "uptime_min": 500, "boot_grace": False, "cfg": {},
                 "gateway": {"findings": [], "profiles": {}, "pid": os.getpid(), "pid_alive": True},
                 "cron": {"findings": [], "jobs": []}, "audit": {"findings": [], "today_sent": []},
                 "findings": []}
        dm = drill_model(dict(model), "all")
        check("演练 all → 3 项找据 + DRILL 判定", len(dm["findings"]) == 3 and verdict(dm) == "DRILL")
        payload = render_payload(dm, dm["findings"], dm["findings"], [], td, False, "file")
        check("payload schema", payload["schema"] == "maaorch.scheduler_watchdog/1")
        check("payload 标题含计数", "项" in payload["title"])

    # 开机静默窗
    model = {"boot_grace": True, "findings": [{"kind": "gateway_down"}]}
    check("开机静默窗 → BOOT_GRACE", verdict(model) == "BOOT_GRACE")

    # scrub
    sc = scrub({"Password": "x", "SklandToken": "y", "Info": {"Name": "a", "auth_code": "z"}})
    check("脱敏丢凭据键", "Password" not in sc and sc["Info"]["Name"] == "a")

    # 计划任务 XML（注册命令的载荷）
    try:
        import xml.etree.ElementTree as ET
        xml = build_task_xml()
        root = ET.fromstring(xml)
        ns = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
        check("任务 XML 可解析", root.tag == ns + "Task")
        check("任务 XML 含 wscript+run_hidden", "wscript.exe" in xml and "run_hidden.vbs" in xml)
        check("任务 XML 每 5 分钟触发", "<Interval>PT5M</Interval>" in xml)
        check("任务 XML 指向 launch ps1", "scheduler_watchdog_launch.ps1" in xml)
    except Exception as e:  # noqa: BLE001
        check("任务 XML 构建（%s）" % e, False)

    print("SELFTEST=%s（%d 项失败）" % ("OK" if not fails else "FAIL", len(fails)))
    return 0 if not fails else 3


def cmd_check(args, cfg) -> int:
    print("scheduler_watchdog --check")
    problems, warns = [], []
    hh = resolve_hermes_home(args.hermes_home)
    print("  hermes home: %s" % hh)
    if not os.path.isdir(hh):
        problems.append("hermes home 不存在: %s" % hh)
    if not os.path.isdir(os.path.join(hh, "profiles")):
        warns.append("无 profiles/ 子目录（profile 列表可能不对）")
    for prof in cfg["profiles"]:
        cdir = os.path.join(hh, "profiles", prof, "cron")
        for f in ("jobs.json", "executions.db", "ticker_heartbeat"):
            ok = os.path.exists(os.path.join(cdir, f))
            print("  profiles/%s/cron/%s: %s" % (prof, f, "ok" if ok else "MISSING"))
            if not ok:
                warns.append("缺 profiles/%s/cron/%s" % (prof, f))
    rt = resolve_runtime(args.runtime)
    print("  runtime: %s" % rt)
    try:
        os.makedirs(rt, exist_ok=True)
        probe = os.path.join(rt, ".scheduler_watchdog_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(probe)
        print("  runtime 可写: ok")
    except OSError as e:
        problems.append("runtime 不可写: %s" % e)
    ini = os.path.join(rt, "watchdog.ini")
    print("  watchdog.ini（通知通道）: %s" % ("ok" if os.path.exists(ini) else "MISSING"))
    if not os.path.exists(ini):
        warns.append("缺 runtime/watchdog.ini（告警只能落文件，不能真发）")
    sub = os.path.join(rt, "scheduler_watchdog.ini")
    print("  scheduler_watchdog.ini（可选覆盖）: %s" % ("ok" if os.path.exists(sub) else "absent"))
    for tool, why in (("netstat", "端口检查"), ("git", "陈旧代码比对"), ("schtasks", "计划任务")):
        found = bool(shutil.which(tool))
        print("  %s: %s" % (tool, "ok" if found else "MISSING"))
        if not found:
            warns.append("缺 %s（%s 降级）" % (tool, why))
    print("  python: %s" % sys.version.split()[0])
    py312 = os.path.join(DEFAULT_LOCALAPPDATA, "Programs", "Python", "Python312", "python.exe")
    print("  Python312（计划任务用）: %s" % ("ok" if os.path.exists(py312) else "MISSING"))
    if not os.path.exists(py312):
        warns.append("缺 Python312（计划任务拉起脚本会退到 PATH 里的 python）")
    print("  launch ps1: %s" % ("ok" if os.path.exists(TASK_LAUNCH_PS1) else "MISSING"))
    if not os.path.exists(TASK_LAUNCH_PS1):
        problems.append("缺 %s（计划任务拉不起来）" % TASK_LAUNCH_PS1)
    gw_pid = load_json(os.path.join(hh, "gateway.pid"))
    print("  gateway.pid: %s" % ("ok pid=%s" % gw_pid.get("pid") if isinstance(gw_pid, dict) else "MISSING"))
    for w in warns:
        print("  - 警告: %s" % w)
    for p in problems:
        print("  - 问题: %s" % p)
    print("CHECK=%s" % ("OK" if not problems else "FAIL"))
    return 2 if problems else 0


# ---------------------------------------------------------------- 计划任务（受控变更；--apply 才执行）

def whoami_id() -> str:
    try:
        r = subprocess.run(["whoami"], capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL, creationflags=NOWIN)
        who = (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        who = ""
    sid = ""
    try:
        r2 = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                            text=True, timeout=10, stdin=subprocess.DEVNULL, creationflags=NOWIN)
        parts = [p.strip().strip('"') for p in (r2.stdout or "").strip().split(",")]
        for p in parts:
            if p.upper().startswith("S-1-"):
                sid = p
                break
    except Exception:  # noqa: BLE001
        pass
    return who, sid


def build_task_xml() -> str:
    who, sid = whoami_id()
    user_id = sid or who or "%USERNAME%"
    start = (now_dt() + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    root_arg = ROOT.replace("/", "\\")
    args = '//nologo "%s" "%s"' % (os.path.join(ROOT, "tools", "run_hidden.vbs").replace("/", "\\"),
                                   TASK_LAUNCH_PS1.replace("/", "\\"))
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>{date}</Date>
    <Author>{who}</Author>
    <URI>\\{name}</URI>
    <Description>MAAOrch 调度看门狗：每 5 分钟探测 Hermes gateway/cron 调度/发车审计，异常落文件并按配置告警（默认不拉起 gateway）。由 tools/scheduler_watchdog.py 生成。</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>{uid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>wscript.exe</Command>
      <Arguments>{args}</Arguments>
      <WorkingDirectory>{root}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""".format(date=start.strftime("%Y-%m-%dT%H:%M:%S"), who=esc(who or "MAAOrch"), name=TASK_NAME,
           uid=esc(user_id), start=start.strftime("%Y-%m-%dT%H:%M:%S"), args=esc(args),
           root=esc(root_arg))


def task_install_plan() -> tuple:
    xml = build_task_xml()
    xml_path = os.path.join(resolve_runtime(None), "scheduler_watchdog_task.xml")
    create = 'schtasks /Create /TN "%s" /XML "%s"' % (TASK_NAME, xml_path)
    run_now = 'schtasks /Run /TN "%s"' % TASK_NAME
    query = 'schtasks /Query /TN "%s" /V /FO LIST' % TASK_NAME
    return xml, xml_path, create, run_now, query


def cmd_install_task(apply_: bool) -> int:
    xml, xml_path, create, run_now, query = task_install_plan()
    print("== 计划任务注册（受控变更）==")
    print("任务名   : %s（每 5 分钟；tools/run_hidden.vbs → tools/scheduler_watchdog_launch.ps1）" % TASK_NAME)
    print("XML      : %s" % xml_path)
    print("注册     : %s" % create)
    print("立即试跑 : %s" % run_now)
    print("查看     : %s" % query)
    print("回滚     : python tools/scheduler_watchdog.py --uninstall-task --apply")
    if not apply_:
        print("\n[DRY-RUN] 未执行任何变更。加 --apply 才真正注册（需 lead 批准后执行）。")
        print("\n---- 任务 XML ----")
        print(xml)
        return 0
    os.makedirs(os.path.dirname(xml_path), exist_ok=True)
    with open(xml_path, "w", encoding="utf-16") as f:   # schtasks /XML 要求 UTF-16
        f.write(xml)
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", xml_path],
                       capture_output=True, text=True, errors="replace", timeout=60,
                       stdin=subprocess.DEVNULL, creationflags=NOWIN)
    ok = r.returncode == 0
    print("schtasks /Create rc=%s\n%s\n%s" % (r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()))
    print("TASK_INSTALL=%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


def cmd_uninstall_task(apply_: bool) -> int:
    cmd = 'schtasks /Delete /TN "%s" /F' % TASK_NAME
    print("注销: %s" % cmd)
    if not apply_:
        print("[DRY-RUN] 未执行。加 --apply 才真正注销。")
        return 0
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="replace",
                       timeout=60, stdin=subprocess.DEVNULL, creationflags=NOWIN)
    ok = r.returncode == 0
    print("rc=%s\n%s\n%s" % (r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()))
    print("TASK_UNINSTALL=%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


# ---------------------------------------------------------------- 主流程

def alert_test_payload() -> dict:
    f = [{"kind": "gateway_down", "severity": "critical", "subject": "pid=12345（样例）",
          "detail": "采样：gateway 进程不在了 —— cron 不会触发", "evidence": {"pid": 12345}},
         {"kind": "job_overdue", "severity": "critical", "subject": "mas/mas-shift-dispatch（样例）",
          "detail": "采样：任务超期未触发：next_run_at 已过 20m（容忍 15min）",
          "evidence": {"tolerance_min": 15, "overdue_s": 1200}},
         {"kind": "audit_stale", "severity": "critical", "subject": "dispatch（样例）",
          "detail": "采样：发车窗口内无任何发车审计", "evidence": {}}]
    model = {"now": now_str(), "hermes_home": "（样例）", "uptime_min": 600, "boot_grace": False,
             "gateway": {"pid": 12345, "pid_alive": False, "profiles": {"mas": {"hb_age_s": 9999}}},
             "cron": {"jobs": []}, "audit": {"last_ts": None, "last_age_s": None, "today_sent": []},
             "findings": f, "drill": None}
    return render_payload(model, f, f, [], resolve_runtime(None), False, "file")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="调度看门狗：gateway/cron/发车审计的外部守护（只读探测；告警默认 dry-run）")
    ap.add_argument("--once", action="store_true", help="单次探测（计划任务用）")
    ap.add_argument("--json", action="store_true", help="机读（单行 JSON）")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不连网），退出码 0/3")
    ap.add_argument("--check", action="store_true", help="环境自检，退出码 0/2")
    ap.add_argument("--drill", choices=["gate-down", "overdue", "audit-stale", "all"],
                    help="演练：伪造找据走告警链路（drill 状态/载荷与生产分离；绝不真发）")
    ap.add_argument("--alert-test", action="store_true", help="打印模拟告警 payload（落 SAMPLE；绝不发送）")
    ap.add_argument("--test-notify", action="store_true", help="真发一条标注测试的通知（验证通道）")
    ap.add_argument("--alert-send", action="store_true", help="有新告警时真发（缺省 dry-run；写文件）")
    ap.add_argument("--no-alert", action="store_true", help="不评估/不落告警（只看探测日志）")
    ap.add_argument("--restart", action="store_true", help="检测到 gateway 不活/心跳停摆/代码陈旧时执行拉起（默认关）")
    ap.add_argument("--restart-cmd", help="覆盖拉起命令（缺省 hermes --profile default gateway restart）")
    ap.add_argument("--restart-wait", type=int, default=None, help="拉起后复验等待秒（默认 90）")
    ap.add_argument("--grace-min", type=int, default=None, help="超期容忍基线分钟（默认 15）")
    ap.add_argument("--hermes-home", help="覆盖 Hermes 主目录（测试用）")
    ap.add_argument("--runtime", help="覆盖 runtime 目录（测试用）")
    ap.add_argument("--profile", help="覆盖受查 profile 列表（逗号分隔；默认 mas）")
    ap.add_argument("--install-task", action="store_true", help="打印注册计划任务命令（受控变更；--apply 才执行）")
    ap.add_argument("--uninstall-task", action="store_true", help="打印/执行注销计划任务（回滚）")
    ap.add_argument("--apply", action="store_true", help="与 --install-task/--uninstall-task 搭配才真正执行")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if args.selftest:
        return cmd_selftest()

    runtime = resolve_runtime(args.runtime)
    cfg = load_cfg(runtime)
    if args.profile:
        cfg["profiles"] = [x.strip() for x in args.profile.split(",") if x.strip()]
    if args.grace_min is not None:
        cfg["grace_min"] = args.grace_min
    if args.restart_wait is not None:
        cfg["restart_wait_s"] = args.restart_wait
    if args.check:
        return cmd_check(args, cfg)
    if args.install_task:
        return cmd_install_task(args.apply)
    if args.uninstall_task:
        return cmd_uninstall_task(args.apply)

    log_file = os.path.join(runtime, "scheduler_watchdog.log")
    os.makedirs(runtime, exist_ok=True)
    send_env = (os.environ.get("MAAORCH_SCHEDWD_ALERT_SEND", "").strip().lower() in ("1", "true", "yes", "on"))
    alert_send = (args.alert_send or send_env) and not args.drill

    if args.alert_test:
        payload = alert_test_payload()
        path = os.path.join(runtime, "reports", "scheduler_watchdog_alert_SAMPLE.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_json_atomic(path, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        log("模拟告警 payload 已落 %s（未发送）" % path, log_file)
        return 0

    if args.test_notify:
        res = send_notify("[测试] MAAOrch 调度看门狗",
                          "这是一条测试通知；收到即表示调度看门狗的告警通道可用。\n"
                          "（本机 %s，%s）" % (socket.gethostname(), now_str()))
        log("测试通知: %s" % res, log_file)
        print("TEST_NOTIFY=%s" % ("OK" if res.get("ok") else "FAIL"))
        return 0 if res.get("ok") else 1

    opts = {"hermes_home": resolve_hermes_home(args.hermes_home), "runtime": runtime, "cfg": cfg}
    model = build_model(opts)
    if args.drill:
        model = drill_model(model, args.drill)

    # 拉起（可选；默认关）
    restart_res = None
    if args.restart:
        gw_kinds = {f["kind"] for f in model["gateway"]["findings"]}
        trigger = bool(gw_kinds & {"gateway_down", "ticker_stalled", "stale_code"})
        restart_cmd = args.restart_cmd or cfg.get("restart_cmd") or default_restart_cmd(opts["hermes_home"])
        state_path_r = os.path.join(runtime, "scheduler_watchdog_state.json")
        if args.drill:
            state_path_r = os.path.join(runtime, "scheduler_watchdog_drill_state.json")
        st = load_json(state_path_r) or {}
        last = parse_ts_loose(st.get("last_restart_at"))
        too_soon = last is not None and (now_aware() - last).total_seconds() < cfg["restart_min_interval_min"] * 60
        if trigger and not too_soon:
            log("[拉起] gateway 异常（%s），执行: %s" % (",".join(sorted(gw_kinds)), restart_cmd), log_file)
            restart_res = do_restart(model, cfg, restart_cmd, int(cfg.get("restart_wait_s") or 90))
            st["last_restart_at"] = now_str()
            st.setdefault("restarts", []).append({k: restart_res.get(k) for k in
                                                  ("at", "cmd", "rc", "ok")})
            st["restarts"] = st["restarts"][-20:]
            save_json_atomic(state_path_r, st)
            log("[拉起] 结果 ok=%s rc=%s verify=%s" % (restart_res.get("ok"), restart_res.get("rc"),
                                                       json.dumps(restart_res.get("verify"), ensure_ascii=False)),
                log_file)
            if not restart_res.get("ok"):
                model["findings"].append({
                    "kind": "restart_failed", "severity": "critical", "subject": "gateway",
                    "detail": "自动拉起失败（rc=%s）—— 需人工介入：%s" % (restart_res.get("rc"), restart_cmd),
                    "evidence": {"output": str(restart_res.get("output"))[:300],
                                 "verify": restart_res.get("verify")}})
        elif trigger and too_soon:
            log("[拉起] 触发但 %d 分钟内已拉起过，跳过（防循环）" % cfg["restart_min_interval_min"], log_file)
            restart_res = {"skipped": "too_soon"}

    # 状态 / 告警
    v = verdict(model)
    state_file = os.path.join(runtime, "scheduler_watchdog_state.json")
    if args.drill:
        state_file = os.path.join(runtime, "scheduler_watchdog_drill_state.json")
    emit_targets, cleared, payload = [], [], None
    if not args.no_alert:
        st = load_json(state_file) or {}
        emit_targets, cleared = alert_state_update(st, model["findings"],
                                                   int(cfg.get("renotify_min") or RENOTIFY_MIN))
        suppress = (v == "BOOT_GRACE")
        if suppress and model["findings"]:
            model["notes"].append("开机静默窗（开机 %s < %dmin）：%d 项找据只记录不告警"
                                  % (ago_s_str((model.get("uptime_min") or 0) * 60),
                                     cfg.get("boot_grace_min", BOOT_GRACE_MIN), len(model["findings"])))
            emit_targets = []
        do_send = bool(alert_send and emit_targets and not suppress)
        channel = "email" if do_send else "file"
        payload = render_payload(model, model["findings"], emit_targets, cleared,
                                 runtime, do_send, channel)
        if payload["findings"] or cleared or args.drill:
            tag = "_drill" if args.drill else ""
            outs = write_alert_files(payload, runtime, tag)
            log("%s告警载荷: %s（findings=%d 新=%d 已消除=%d）"
                % ("[演练] " if args.drill else "", ", ".join(outs),
                   len(model["findings"]), len(emit_targets), len(cleared)), log_file)
            mark_emitted(st, emit_targets)
        if do_send:
            res = send_notify(payload["title"], payload["summary"])
            log("发送结果: %s" % res, log_file)
            if res.get("ok"):
                mark_sent(st, emit_targets)
            else:
                model["notes"].append("发送失败（已落文件兜底）: %s" % res.get("error"))
        st["updated"] = now_str()
        st["last_run"] = now_str()
        st["last_verdict"] = v
        if payload is not None:
            st["last_findings_n"] = len(model["findings"])
        save_json_atomic(state_file, st)

    # 输出
    summary_line = "WATCHDOG=%s（findings=%d%s）" % (
        v, len(model["findings"]),
        ("；" + "、".join("%s:%s" % (f.get("kind"), f.get("subject")) for f in model["findings"][:5]))
        if model["findings"] else "")
    if args.json:
        print(json.dumps({"ok": v in ("OK", "BOOT_GRACE", "DRILL"), "verdict": v,
                          "findings": model["findings"], "cleared": cleared,
                          "emit_targets": len(emit_targets), "restart": restart_res,
                          "gateway": {k: vv for k, vv in model["gateway"].items() if k != "findings"},
                          "cron": {k: vv for k, vv in model["cron"].items() if k != "findings"},
                          "audit": {k: vv for k, vv in model["audit"].items() if k != "findings"},
                          "notes": model["notes"], "payload": payload,
                          "summary_line": summary_line}, ensure_ascii=False, default=str))
    else:
        log("[探测] hermes=%s runtime=%s 开机=%s 判定=%s"
            % (opts["hermes_home"], runtime, ago_s_str((model.get("uptime_min") or 0) * 60), v), log_file)
        gw = model["gateway"]
        log("  gateway: pid=%s 存活=%s 端口=%s 心跳=%s" % (
            gw.get("pid"), gw.get("pid_alive"),
            "、".join("%s:%s" % (p["addr"], "通" if p.get("reachable") else "不通") for p in (gw.get("ports") or [])) or "-",
            "、".join("%s:%s" % (p, ago_s_str(x.get("hb_age_s"))) for p, x in (gw.get("profiles") or {}).items()) or "?"), log_file)
        for j in (model["cron"].get("jobs") or []):
            log("  cron[%s]: %s | next=%s overdue=%s | 最近执行=%s(%s) | tol=%smin"
                % (j.get("profile"), j.get("name"), j.get("next_run_at"), j.get("overdue_s"),
                   j.get("last_exec_claimed_at"), j.get("last_exec_status"), j.get("tolerance_min")), log_file)
        au = model["audit"]
        log("  audit: 最近发车审计=%s（%s前）| 今日已发 %d | 未消除告警 %d | late %d"
            % (au["last_ts"].strftime("%Y-%m-%d %H:%M:%S") if au.get("last_ts") else "无记录",
               ago_s_str(au.get("last_age_s")), len(au.get("today_sent") or []),
               len(au.get("active_alerts") or []), len(au.get("late") or {})), log_file)
        for f in model["findings"]:
            log("  [%s] %s — %s" % (f.get("severity"), f.get("kind"), f.get("detail")), log_file)
        for nt in model["notes"]:
            log("  (注) %s" % nt, log_file)
        print(summary_line)
    if v == "ALERT":
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
