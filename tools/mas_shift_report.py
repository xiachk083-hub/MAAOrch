#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""班次日报 + 告警渠道（只读采集 → 本地报告 + 看板只读汇总卡 + 告警 payload/dry-run）。

## 为什么存在
班次跑完没人主动看：报告只落本地文件、要人问才看。本工具把「每班一页日报」自动化：
  ① 采集（全只读）：应跑/实跑/缺口（逐号）、成功率、失败类型 Top、补跑结果、
     资源水位（盘/内存）、空转实例、配置漂移/SWITCH_GATE、发车审计、到点提醒（周期事件 UPCOMING=）；
  ② 产出 `runtime/reports/shift_report_<YYYYMMDD>_<shift>.md`；
  ③ 在看板（Hermes kanban）开一张**只读汇总卡**（status=done、无 assignee ⇒ 永不被派工）；
  ④ 告警渠道：只在关键事件触发（发车失败 / 缺口>N / 侧车异常 / 实例全忙 / 连续失败账号），
     **默认 dry-run**（只落本地 payload 文件 + 汇总卡注释），`--alert-send` 才真发（email 复用
     AUTO-MAS/watchdog 的通知配置）。

## 口径（与既有工具一致，别再发明）
- 班次表 = tools/mas_dispatch.py 的 SHIFT_TABLE（24 条：日常定时1-12 @05:00+3′、晚班定时1-12 @19:30+3′）。
- 班次日 D 的窗口 = [D 05:00, D+1 05:00)；daily 班窗口 [D 05:00, D 12:00)、evening 班 [D 19:30, D+1 05:00)。
- 应跑 = 本班 12 条队列的队列项脚本的**启用账号**，去掉脚本名 `l-*`（肉鸽机队，红线不发）与停用号
  （口径同技能 auto-mas-host-ops「该跑 = 全名单 − 肉鸽机 − 停用号」）。
- 实跑 = 班次日 history 里有 DONE 记录的应跑账号；缺口 = 应跑 − 实跑（未跑 / 仅失败）。
- 成功率：账号级 = 实跑/应跑；尝试级 = maa_error_daily 的 hist ok/(ok+fail)（逐次尝试口径）。
- 失败类型 Top / 错误聚合 = tools/maa_error_daily.py（四源：history/gui/asst/app.log）的 JSON。
- 资源水位 / 空转实例 / 切号闸门 / 班次表漂移 = 主机 SSH 采样 / tools/mas_idle_emulators.py /
  tools/mas_switch_gate.py / tools/mas_dispatch.py --check-schedule（全部只读）。
- 到点提醒 `UPCOMING=` = tools/event_calendar.py --check 的末三行（EVENT_CAL=/UPCOMING=/EVENT_ADVICE=）
  原样进第 9 节（只读、零写盘；`--check` 自带 fail-closed）。调用失败/无输出 ⇒ 写
  `UPCOMING=UNKNOWN:<原因>`（**不许当「无事件」用**），日报照常产出（不阻塞）。

## 告警判据（只在关键事件触发；含成熟度闸防误报）
- dispatch_failed   发车失败（runtime/mas_dispatch_alerts.json active，排除 test 注入）
- gap_over_n        缺口 > N（默认 3；**成熟度闸**：生成时刻 ≥ 班次起 +150min 才评估）
- sidecar_abnormal  REST 不可达 / /api/core/health 非 ready / 侧车进程在**班次活跃窗**（起 +210min）内重启
- all_busy          运行中实例全部在用（in_use ≥ running > 0）且仍有缺口（同样过成熟度闸）
- consecutive_failed 连续 ≥2 天（含班次日）有失败记录的账号（同样过成熟度闸）
告警状态（已通知/已消除）记 runtime/shift_report_state.json；重复运行不重复通知，
条件消失发一条「已消除」。

## 写边界
只写本机 runtime/（报告、告警 payload、状态文件）与看板 DB 的**一张汇总卡**（直写 SQLite，
见 `_board_upsert`：字段按 create_task/complete_task 的既有形状，status=done 不参与派工）；
**绝不写主机 / 不改 AUTO-MAS 配置 / 不发外部通知（除 --alert-send）**。

## 用法
  python tools/mas_shift_report.py                      # 自动判班次（<12:00=daily，否则 evening），出报告+卡，告警 dry-run
  python tools/mas_shift_report.py --shift daily --date 2026-09-28
  python tools/mas_shift_report.py --no-card --no-alert  # 只要报告文件
  python tools/mas_shift_report.py --alert-send         # 有新告警时真发（email，复用 watchdog.ini）
  python tools/mas_shift_report.py --alert-test         # 模拟一条告警 payload（打印+落 SAMPLE，绝不发送）
  python tools/mas_shift_report.py --json               # 机读（单行 JSON）
  python tools/mas_shift_report.py --check              # 连通性自检（REST/SSH/子工具/看板），退出码 0/2
  python tools/mas_shift_report.py --selftest           # 离线自检（纯函数+夹具，不连网），退出码 0/3

退出码：0=报告已出（含局部 UNKNOWN）；2=核心源不可达（报告仍出，UNKNOWN 标注，REST 不可达会触发 sidecar_abnormal）；1=未预期；3=自检失败。
环境变量：MAAORCH_MAS_API / MAAORCH_SSH_KEY / MAAORCH_SSH_HOST / MAAORCH_SHIFT_ALERT_SEND（1=等同 --alert-send）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 常量/路径

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
RUNTIME = os.environ.get("MAAORCH_DISPATCH_RUNTIME") or os.path.join(ROOT, "runtime")
REPORTS = os.path.join(RUNTIME, "reports")
STATE_FILE = os.path.join(RUNTIME, "shift_report_state.json")
ALERT_LOG = os.path.join(RUNTIME, "shift_alerts.jsonl")

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")

# 班次窗口（本地时间；daily 到 12:00、evening 到次日 05:00 只为圈定记录范围，实际报"截至生成时刻"）
SHIFT_WINDOWS = {"daily": ("05:00", 12 * 60), "evening": ("19:30", 29 * 60)}
SHIFT_LABEL = {"daily": "日常班", "evening": "晚班"}
ACTIVE_MIN = 210          # 班次"活跃窗"长度（05:00–08:30 / 19:30–23:00）：侧车重启判据只看这段
MATURE_MIN = 150          # 成熟度闸：缺口/全忙/连续失败告警要等班次起 +150min 才评估
GAP_N_DEFAULT = 3         # 缺口告警阈值
STREAK_DAYS_DEFAULT = 2   # 连续失败天数阈值
HISTORY_LOOKBACK = 5      # 连续失败回看天数

# 只读端点白名单（防被改成会写；本工具只允许这些路径）
READ_ENDPOINTS = (
    "/api/queue/get", "/api/queue/time/get", "/api/queue/item/get",
    "/api/scripts/get", "/api/scripts/user/get",
    "/api/history/search", "/api/core/health",
)

SENSITIVE = ("password", "token", "sklandtoken", "secret", "credential", "cookie", "auth_code")

# 到点提醒：只读子工具（--check 输出 EVENT_CAL=/UPCOMING=/EVENT_ADVICE= 三行，零写盘）
CLI_EVENT_CAL = os.path.join(TOOLS, "event_calendar.py")
EVENT_CAL_KEYS = ("EVENT_CAL", "UPCOMING", "EVENT_ADVICE")


def log(msg: str) -> None:
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def now_dt() -> dt.datetime:
    return dt.datetime.now()


def now_str() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def scrub(o):
    """读入即丢弃凭据类键（本工具所有外部数据都过它）。"""
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items() if not any(s in str(k).lower() for s in SENSITIVE)}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


def load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def save_json_atomic(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- REST（只读）

class RestError(RuntimeError):
    pass


_REST_CACHE: dict = {}


def rest_post(path: str, body: dict | None = None, timeout: int = 40, use_cache: bool = True) -> dict:
    if path not in READ_ENDPOINTS:
        raise RestError("非只读端点被拒绝: %s" % path)
    key = (path, json.dumps(body or {}, sort_keys=True))
    if use_cache and key in _REST_CACHE:
        return _REST_CACHE[key]
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = scrub(json.loads(r.read().decode("utf-8", "replace")))
    except urllib.error.HTTPError as e:
        raise RestError("%s -> HTTP %s" % (path, e.code)) from e
    except Exception as e:  # noqa: BLE001
        raise RestError("%s -> %s" % (path, str(e)[:160])) from e
    if use_cache:
        _REST_CACHE[key] = data
    return data


def rest_reachable() -> tuple[bool, str]:
    try:
        r = rest_post("/api/queue/get", {}, timeout=15, use_cache=False)
        return True, "code=%s" % r.get("code")
    except RestError as e:
        return False, str(e)


# ---------------------------------------------------------------- 子工具（spawn 型，全部只读）

def run_tool(args: list, timeout: int = 300) -> tuple[int, str, str]:
    cmd = [sys.executable] + args
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout or "", r.stderr or ""


def _last_json_line(out: str):
    """从子工具 stdout 里取最后一个 JSON 对象（单行紧凑或 indent 多行都要吃）。"""
    lines = (out or "").splitlines()
    for ln in reversed(lines):
        ln = ln.strip()
        if ln.startswith("{") and ln.endswith("}"):
            try:
                return json.loads(ln)
            except json.JSONDecodeError:
                continue
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i].strip()
        if ln.startswith("{"):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                continue
    return None


def collect_check_schedule() -> dict | None:
    rc, out, _ = run_tool([os.path.join(TOOLS, "mas_dispatch.py"), "--check-schedule", "--json"], timeout=180)
    obj = _last_json_line(out)
    if obj is not None:
        obj["_rc"] = rc
    return obj


def collect_switch_gate() -> dict | None:
    rc, out, _ = run_tool([os.path.join(TOOLS, "mas_switch_gate.py"), "--json"], timeout=240)
    obj = _last_json_line(out)
    if obj is not None:
        obj["_rc"] = rc
    return obj


def collect_idle() -> dict | None:
    rc, out, _ = run_tool([os.path.join(TOOLS, "mas_idle_emulators.py"), "--json"], timeout=300)
    obj = _last_json_line(out)
    if obj is not None:
        obj["_rc"] = rc
    return obj


def parse_event_cal(out: str) -> dict:
    """从 `event_calendar.py --check` 的 stdout 取 EVENT_CAL=/UPCOMING=/EVENT_ADVICE= 三行（原样行）。

    缺 UPCOMING 行 ⇒ fail-closed 补 `UPCOMING=UNKNOWN:…`（**不许当「无事件」用**）。
    纯函数（离线自测用）。
    """
    lines: dict = {}
    for raw in (out or "").splitlines():
        ln = raw.strip()
        for key in EVENT_CAL_KEYS:
            if ln.startswith(key + "=") and key not in lines:
                lines[key] = ln
                break
    if "UPCOMING" not in lines:
        lines["UPCOMING"] = "UPCOMING=UNKNOWN:无 UPCOMING 行（event_calendar 未产出）"
    return lines


def collect_event_calendar(window_hours: int = 48, days: int = 14) -> dict:
    """到点提醒（只读）—— tools/event_calendar.py --check；失败**不阻塞**日报（写 UNKNOWN:<原因>）。"""
    res = {"ok": False, "detail": "", "lines": {}, "rc": None}
    if not os.path.exists(CLI_EVENT_CAL):
        res["detail"] = "缺 tools/event_calendar.py"
        res["lines"] = {"UPCOMING": "UPCOMING=UNKNOWN:缺 tools/event_calendar.py（工具面未提供）"}
        return res
    try:
        rc, out, err = run_tool([CLI_EVENT_CAL, "--check",
                                 "--window-hours", str(window_hours), "--days", str(days)], timeout=60)
    except subprocess.TimeoutExpired:
        res["detail"] = "超时（>60s）"
        res["lines"] = {"UPCOMING": "UPCOMING=UNKNOWN:超时（event_calendar --check >60s）"}
        return res
    except Exception as e:  # noqa: BLE001
        res["detail"] = "%s: %s" % (type(e).__name__, e)
        res["lines"] = {"UPCOMING": "UPCOMING=UNKNOWN:%s" % res["detail"][:120]}
        return res
    res["rc"] = rc
    lines = parse_event_cal(out)
    res["lines"] = lines
    up = lines.get("UPCOMING", "")
    if rc not in (0, 2):     # 2 = 工具自身已 fail-closed（UNKNOWN 三行）；其它非零 = 未预期
        res["detail"] = "rc=%s %s" % (rc, (err or "").strip()[:120] or "")
        if not up.startswith("UPCOMING=UNKNOWN"):
            lines["UPCOMING"] = "UPCOMING=UNKNOWN:rc=%s（event_calendar 未产出）" % rc
            up = lines["UPCOMING"]
    res["ok"] = rc == 0 and up.startswith("UPCOMING=") and not up.startswith("UPCOMING=UNKNOWN")
    if not res["detail"]:
        res["detail"] = "rc=0" if res["ok"] else up[:120]
    return res


def collect_errors(date_s: str, skip: bool = False, max_age_min: int = 180) -> dict | None:
    """读 runtime/reports/maa_errors_<date>.json；缺/过期则跑 maa_error_daily.py（本地写报告，只读主机）。"""
    path = os.path.join(REPORTS, "maa_errors_%s.json" % date_s)
    if not skip:
        obj = load_json(path)
        fresh = False
        if isinstance(obj, dict) and obj.get("date") == date_s:
            try:
                gen = dt.datetime.strptime(str(obj.get("generated_at")), "%Y-%m-%d %H:%M:%S")
                fresh = (now_dt() - gen).total_seconds() <= max_age_min * 60
            except Exception:  # noqa: BLE001
                fresh = False
        if not fresh:
            try:
                run_tool([os.path.join(TOOLS, "maa_error_daily.py"), "--date", date_s], timeout=420)
            except subprocess.TimeoutExpired:
                pass
            obj = load_json(path)
    else:
        obj = load_json(path)
    if isinstance(obj, dict):
        return {"path": path, "data": obj}
    return None


def collect_host_sample() -> dict | None:
    """主机一次采样（内存/磁盘/MAA/VMM/adb/侧车启动时刻）——只读 SSH。"""
    remote = (
        '$os=Get-CimInstance Win32_OperatingSystem;'
        '$freem=[int]($os.FreePhysicalMemory/1024);'
        '$dr=@(); foreach($d in @("C","E")){ $x=Get-PSDrive $d -EA 0; if($x){$dr += ("{0}:{1}/{2}" -f $d,[int]($x.Free/1GB),[int](($x.Free+$x.Used)/1GB))} };'
        '$maa=(Get-Process MAA -EA 0).Count;'
        '$vmm=(Get-Process MuMuVMMHeadless -EA 0).Count;'
        '$adb="E:\\MuMu Player 12\\nx_main\\adb.exe";'
        '$adbn=@(& $adb devices 2>$null | Select-String "device$").Count;'
        '$sc=Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | Where-Object { $_.CommandLine -match "AUTO-MAS" } | Select-Object -First 1;'
        '$scs= if($sc){$sc.CreationDate.ToString("yyyy-MM-dd HH:mm:ss")}else{"none"};'
        '$scp= if($sc){$sc.ProcessId}else{0};'
        '"PERF|{0}|{1}|{2}|{3}|{4}|{5}|{6}" -f $freem,($dr -join ","),$maa,$vmm,$adbn,$scp,$scs'
    )
    b64 = base64.b64encode(remote.encode("utf-16-le")).decode()
    ssh = shutil.which("ssh") or r"C:\Windows\System32\OpenSSH\ssh.exe"
    cmd = [ssh, "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=15", SSH_HOST, "powershell -NoProfile -EncodedCommand " + b64]
    try:
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    out = (r.stdout or b"").decode("utf-8", "replace")
    for ln in reversed(out.splitlines()):
        ln = ln.strip()
        if ln.startswith("PERF|"):
            parts = ln.split("|")
            if len(parts) >= 8:
                disks = {}
                for seg in parts[2].split(","):
                    if ":" in seg and "/" in seg:
                        name, vals = seg.split(":", 1)
                        free, _, total = vals.partition("/")
                        try:
                            disks[name.strip()] = {"free_gb": int(free), "total_gb": int(total)}
                        except ValueError:
                            continue
                return {
                    "free_mem_mb": int(parts[1]) if parts[1].isdigit() else None,
                    "disks": disks,
                    "maa": int(parts[3]) if parts[3].isdigit() else None,
                    "vmm": int(parts[4]) if parts[4].isdigit() else None,
                    "adb_online": int(parts[5]) if parts[5].isdigit() else None,
                    "sidecar_pid": int(parts[6]) if parts[6].isdigit() else None,
                    "sidecar_start": parts[7].strip(),
                }
    return None


def collect_health() -> dict | None:
    try:
        req = urllib.request.Request(API + "/api/core/health", method="GET")
        with urllib.request.urlopen(req, timeout=15) as r:
            return scrub(json.loads(r.read().decode("utf-8", "replace")))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 班次模型

def resolve_shift(explicit: str | None) -> str:
    if explicit in ("daily", "evening"):
        return explicit
    h = now_dt().hour
    return "daily" if 5 <= h < 12 else "evening"


def resolve_date(explicit: str | None, shift: str) -> str:
    if explicit:
        return explicit
    n = now_dt()
    # <05:00 时"今天"还没开始：归到昨天（覆盖晚班尾巴 / 凌晨补跑）
    return (n.date() - dt.timedelta(days=1)).isoformat() if n.hour < 5 else n.date().isoformat()


def window_bounds(shift: str, date_s: str) -> tuple[dt.datetime, dt.datetime]:
    d = dt.date.fromisoformat(date_s)
    start_hm, end_min = SHIFT_WINDOWS[shift]
    h, m = start_hm.split(":")
    start = dt.datetime.combine(d, dt.time(int(h), int(m)))
    end = start + dt.timedelta(minutes=end_min - (int(h) * 60 + int(m)))
    return start, end


def collect_schedule_rows(shift: str) -> dict:
    """本班 12 条队列 → 队列项 → 脚本 → 账号（只读 REST）。返回 {rows, errors, queue_times}"""
    prefix = "日常定时" if shift == "daily" else "晚班定时"
    out = {"rows": [], "errors": [], "queue_times": {}}
    try:
        q = rest_post("/api/queue/get", {})
    except RestError as e:
        out["errors"].append("queue/get: %s" % e)
        return out
    qmap = {}
    for uid, v in (q.get("data") or {}).items():
        name = ((v or {}).get("Info") or {}).get("Name")
        if name:
            qmap[name] = uid
    try:
        sdata = rest_post("/api/scripts/get", {}).get("data") or {}
    except RestError as e:
        sdata = {}
        out["errors"].append("scripts/get: %s" % e)
    sname = {uid: ((v or {}).get("Info") or {}).get("Name") for uid, v in sdata.items()}
    for n in range(1, 13):
        qname = "%s%d" % (prefix, n)
        qid = qmap.get(qname)
        if not qid:
            out["errors"].append("缺队列 %s" % qname)
            continue
        try:
            t = rest_post("/api/queue/time/get", {"queueId": qid})
            for _tsid, tv in (t.get("data") or {}).items():
                info = (tv or {}).get("Info") or {}
                if info.get("Time"):
                    out["queue_times"][qname] = {"Time": str(info["Time"]),
                                                 "Enabled": bool(info.get("Enabled")),
                                                 "Days": list(info.get("Days") or [])}
        except RestError as e:
            out["errors"].append("queue/time/get %s: %s" % (qname, e))
        try:
            items = (rest_post("/api/queue/item/get", {"queueId": qid}).get("data") or {})
        except RestError as e:
            out["errors"].append("queue/item/get %s: %s" % (qname, e))
            continue
        for it in items.values():
            sid = ((it or {}).get("Info") or {}).get("ScriptId")
            if not sid:
                continue
            try:
                users = (rest_post("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {})
            except RestError as e:
                out["errors"].append("scripts/user/get %s: %s" % (qname, e))
                continue
            for uname, uv in users.items():
                if uname == "instances":
                    continue
                info = ((uv or {}).get("Info") or {})
                nm = info.get("Name")
                if nm:
                    out["rows"].append({"account": nm, "queue": qname, "script": sname.get(sid, "?"),
                                        "enabled": bool(info.get("Status")), "server": info.get("Server")})
    return out


def collect_history(date_s: str, days: int = HISTORY_LOOKBACK) -> dict:
    """近 N 天 history（班次日口径）：{day: {account: {records: [...], done, err}}}"""
    start = (dt.date.fromisoformat(date_s) - dt.timedelta(days=max(0, days - 1))).isoformat()
    try:
        r = rest_post("/api/history/search", {"mode": "DAILY", "start_date": start, "end_date": date_s})
    except RestError as e:
        return {"ok": False, "error": str(e), "days": {}}
    days_out = {}
    for day, per_acct in (r.get("data") or {}).items():
        if not isinstance(per_acct, dict):
            continue
        day_map = {}
        for acct, rec in per_acct.items():
            idx = ((rec or {}).get("index") or [])
            records = []
            for e in idx:
                records.append({"date": str((e or {}).get("date") or ""),
                                "status": str((e or {}).get("status") or "").upper()})
            errs = (rec or {}).get("error_info")
            if isinstance(errs, dict):
                for ts, msg in errs.items():
                    for rec2 in records:
                        if rec2["date"] == ts:
                            rec2["error"] = str(msg)[:200]
            records.sort(key=lambda x: x["date"])
            day_map[acct] = {
                "records": records,
                "done": sum(1 for x in records if x["status"] == "DONE"),
                "err": sum(1 for x in records if x["status"] == "ERROR"),
                "last": records[-1]["date"] if records else "",
            }
        days_out[day] = day_map
    return {"ok": True, "error": "", "days": days_out}


def classify_accounts(schedule: dict, history: dict, date_s: str) -> dict:
    """应跑/实跑/缺口（逐号）。"""
    rows = schedule.get("rows") or []
    uniq: dict = {}
    for r in rows:
        uniq.setdefault(r["account"], []).append(r)
    l_accounts = sorted({a for a in uniq if any(str(x["script"]).startswith("l-") for x in uniq[a])})
    disabled = sorted({a for a in uniq if not any(x["enabled"] for x in uniq[a])})
    excluded = set(l_accounts) | set(disabled)
    expected = sorted(a for a in uniq if a not in excluded)
    hday = (history.get("days") or {}).get(date_s) or {}
    detail = []
    for a in expected:
        rec = hday.get(a) or {}
        records = rec.get("records") or []
        done = int(rec.get("done") or 0)
        err = int(rec.get("err") or 0)
        if done:
            status = "完成"
        elif err:
            status = "仅失败"
        else:
            status = "未跑"
        detail.append({
            "account": a, "queues": sorted({x["queue"] for x in uniq[a]}),
            "script": uniq[a][0]["script"], "status": status, "done": done, "err": err,
            "last": rec.get("last") or "", "records": len(records),
            "last_error": next((x.get("error") for x in reversed(records) if x.get("status") == "ERROR"), ""),
        })
    done_list = [d for d in detail if d["status"] == "完成"]
    gap = [d for d in detail if d["status"] != "完成"]
    return {
        "expected": len(expected), "done": len(done_list), "gap": len(gap),
        "gap_accounts": [d["account"] for d in gap],
        "norec": [d["account"] for d in gap if d["status"] == "未跑"],
        "failed_only": [d["account"] for d in gap if d["status"] == "仅失败"],
        "l_accounts": l_accounts, "disabled": disabled, "detail": detail,
        "rate_account": (len(done_list) / len(expected)) if expected else None,
    }


def collect_ledger(window: tuple[dt.datetime, dt.datetime]) -> dict:
    """补跑/按需发车台账（本机文件，只读）。"""
    start, end = window
    out = {"entries": [], "ondemand": [], "errors": []}
    # mas_ledger.jsonl（mas_watch / mas_rerun / run_profile / probe_farm 等）
    try:
        with open(os.path.join(RUNTIME, "mas_ledger.jsonl"), "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                ts = str(rec.get("ts") or "")
                try:
                    t = dt.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if start <= t <= end:
                    out["entries"].append(scrub(rec))
    except OSError as e:
        out["errors"].append("mas_ledger.jsonl: %s" % e)
    # mas_ondemand_ledger.json（按需发车账本）
    led = load_json(os.path.join(RUNTIME, "mas_ondemand_ledger.json"))
    if isinstance(led, dict):
        for day, daymap in led.items():
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(day)) or not isinstance(daymap, dict):
                continue
            for key, rec in daymap.items():
                if not isinstance(rec, dict):
                    continue
                ts = str(rec.get("ts") or "")
                try:
                    t = dt.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if start <= t <= end:
                    out["ondemand"].append({"day": day, "key": key, "target_kind": rec.get("target_kind"),
                                            "kind": rec.get("kind"),
                                            "target": rec.get("target"), "mode": rec.get("mode"),
                                            "status": rec.get("status"), "ts": ts,
                                            "released_at": rec.get("released_at"),
                                            "temp_queue": (rec.get("temp_queue") or {}).get("name")})
    return out


def collect_dispatch(date_s: str, window) -> dict:
    """发车审计：状态文件 + 告警台账 + 窗口内日志（只读）。"""
    out = {"sent": {}, "alerts_active": [], "late": {}, "log_lines": [], "errors": []}
    st = load_json(os.path.join(RUNTIME, "mas_dispatch_state.json")) or {}
    day = st.get(date_s) or {}
    out["sent"] = {k: {"at": (v or {}).get("at"), "taskId": (v or {}).get("taskId")}
                   for k, v in day.items() if isinstance(v, dict)}
    late = st.get("late") or {}
    out["late"] = {k: v for k, v in late.items() if isinstance(v, dict)}
    al = load_json(os.path.join(RUNTIME, "mas_dispatch_alerts.json")) or {}
    out["alerts_active"] = [scrub(e) for e in (al.get("active") or []) if isinstance(e, dict) and not e.get("test")]
    start, end = window
    try:
        with open(os.path.join(RUNTIME, "mas_dispatch_log.jsonl"), "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                ts = str(rec.get("ts") or "")
                try:
                    t = dt.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if start <= t <= end:
                    out["log_lines"].append(scrub(rec))
    except OSError as e:
        out["errors"].append("mas_dispatch_log.jsonl: %s" % e)
    return out


def sidecar_view(host: dict | None, window, active_min: int = ACTIVE_MIN) -> dict:
    """侧车（AUTO-MAS python 后端）异常视图。"""
    start, _end = window
    active_end = start + dt.timedelta(minutes=active_min)
    out = {"abnormal": False, "reasons": [], "start": None, "pid": None,
           "active_window": [start.strftime("%Y-%m-%d %H:%M:%S"), active_end.strftime("%Y-%m-%d %H:%M:%S")]}
    if not host:
        out["abnormal"] = True
        out["reasons"].append("主机采样失败（SSH 不可达）⇒ 侧车状态未知")
        return out
    out["start"] = host.get("sidecar_start")
    out["pid"] = host.get("sidecar_pid")
    ss = str(host.get("sidecar_start") or "")
    if ss and ss != "none":
        try:
            t = dt.datetime.strptime(ss[:19], "%Y-%m-%d %H:%M:%S")
            if start <= t <= active_end:
                out["abnormal"] = True
                out["reasons"].append("侧车在班次活跃窗内重启（起于 %s）——重启会吞在飞任务的重试/收尾（孤儿化风险）" % ss)
        except ValueError:
            out["reasons"].append("侧车启动时刻不可解析: %s" % ss)
    return out


def eval_alerts(model: dict, gap_n: int, streak_days: int) -> list:
    """告警判据（只在关键事件触发；缺口/全忙/连续失败过成熟度闸）。"""
    events = []
    now = model["now_dt"]
    start = model["window_start"]
    mature = now >= start + dt.timedelta(minutes=MATURE_MIN)

    # ① 发车失败（终态，任何时刻都报）
    for e in model["dispatch"]["alerts_active"]:
        events.append({"kind": "dispatch_failed", "severity": "critical",
                       "subject": str(e.get("queue") or "?"),
                       "detail": "发车失败：%s（%s）" % (e.get("reason"), str(e.get("detail"))[:160]),
                       "evidence": {"reason": e.get("reason"), "attempts": e.get("attempts"),
                                    "first_seen": e.get("first_seen") or e.get("ts")}})

    # ② 侧车异常（REST 不可达 / health 非 ready / 活跃窗内重启）
    reach, why = model["reach"]
    if not reach:
        events.append({"kind": "sidecar_abnormal", "severity": "critical", "subject": "rest",
                       "detail": "AUTO-MAS REST 不可达：%s" % why, "evidence": {"api": API}})
    else:
        h = model.get("health")
        if isinstance(h, dict) and (h.get("ready") is False or str(h.get("backgroundStatus")) not in ("ready", "")):
            events.append({"kind": "sidecar_abnormal", "severity": "critical", "subject": "health",
                           "detail": "AUTO-MAS 后端未就绪：%s" % json.dumps(h, ensure_ascii=False)[:200],
                           "evidence": h})
        sc = model["sidecar"]
        if sc.get("abnormal"):
            events.append({"kind": "sidecar_abnormal", "severity": "critical", "subject": "sidecar",
                           "detail": "；".join(sc["reasons"]),
                           "evidence": {"sidecar_start": sc.get("start"), "pid": sc.get("pid")}})

    # ③ 缺口 > N（成熟度闸）
    gap = model["accounts"]
    if gap["gap"] > gap_n:
        if mature:
            events.append({"kind": "gap_over_n", "severity": "critical", "subject": "gap",
                           "detail": "应跑 %d / 实跑 %d，缺口 %d > %d：%s"
                                     % (gap["expected"], gap["done"], gap["gap"], gap_n,
                                        "、".join(gap["gap_accounts"][:12])),
                           "evidence": {"norec": gap["norec"], "failed_only": gap["failed_only"],
                                        "threshold": gap_n}})
        else:
            model["notes"].append("缺口 %d 未达成熟度闸（生成于班次起 +%dmin 前），本轮不告警"
                                  % (gap["gap"], MATURE_MIN))

    # ④ 实例全忙（运行中实例全部在用 且 仍有缺口；成熟度闸）
    idle = model.get("idle") or {}
    counts = idle.get("counts") or {}
    running = int(counts.get("running") or 0)
    in_use = int(counts.get("in_use") or 0)
    if running > 0 and in_use >= running and gap["gap"] > 0:
        if mature:
            events.append({"kind": "all_busy", "severity": "warn", "subject": "instances",
                           "detail": "实例全忙：运行 %d / 在用 %d，仍有缺口 %d（无空闲产能消化）"
                                     % (running, in_use, gap["gap"]),
                           "evidence": {"running": running, "in_use": in_use,
                                        "candidates": counts.get("candidates"), "gap": gap["gap"]}})
        else:
            model["notes"].append("实例全忙未达成熟度闸，本轮不告警")

    # ⑤ 连续失败账号（含班次日连续 ≥N 天；成熟度闸）
    streak = model["streaks"]
    hit = [s for s in streak if s.get("streak", 0) >= streak_days]
    if hit:
        if mature:
            for s in hit[:8]:
                events.append({"kind": "consecutive_failed", "severity": "warn",
                               "subject": s.get("account") or "?",
                               "detail": "连续 %d 天有失败记录（末次 %s%s）：%s"
                                         % (s.get("streak", 0), s.get("last_day") or "-",
                                            (" " + s["last_ts"][11:]) if s.get("last_ts") else "",
                                            str(s.get("last_error") or "")[:120]),
                               "evidence": {"days": s.get("days") or [], "streak": s.get("streak", 0)}})
        else:
            model["notes"].append("连续失败账号 %d 个未达成熟度闸，本轮不告警" % len(hit))
    return events


def compute_streaks(history: dict, date_s: str) -> list:
    """连续失败：截至班次日（含），连续 ≥2 天「有失败记录且当天未完成」（err>0 且 done==0）。

    口径要点：**重试自愈不算失败日**——「MAA 未能正确登录 PRTS」这类是重试级噪声
    （实测全队每天 40–140 条，首试失败重试即过，当天最终 DONE）。只有整天没跑成
    才是"连续失败"（与技能 auto-mas-host-ops「噪声 vs 真错」分层一致）。
    """
    days = sorted((history.get("days") or {}).keys())
    if not days:
        return []
    out = []
    for acct in sorted({a for d in days for a in (history["days"][d] or {})}):
        streak = 0
        last_day = last_ts = ""
        last_error = ""
        fail_days = []
        for d in reversed(days):
            rec = (history["days"][d] or {}).get(acct) or {}
            errs = [r for r in (rec.get("records") or []) if r.get("status") == "ERROR"]
            if errs and int(rec.get("done") or 0) == 0:
                streak += 1
                fail_days.append(d)
                if not last_day:
                    last_day = d
                    last_ts = errs[-1]["date"]
                    last_error = errs[-1].get("error") or ""
            else:
                break
        if streak >= 2:
            out.append({"account": acct, "streak": streak, "last_day": last_day,
                        "last_ts": last_ts, "last_error": last_error,
                        "days": sorted(fail_days)})
    out.sort(key=lambda x: (-x["streak"], x["account"]))
    return out


def build_model(shift: str, date_s: str, opts: dict) -> dict:
    model = {"shift": shift, "shift_label": SHIFT_LABEL[shift], "date": date_s,
             "now_dt": now_dt(), "now": now_str(), "notes": [], "sources": []}
    start, end = window_bounds(shift, date_s)
    model["window_start"], model["window_end"] = start, end
    model["mature"] = model["now_dt"] >= start + dt.timedelta(minutes=MATURE_MIN)
    model["mature_after"] = (start + dt.timedelta(minutes=MATURE_MIN)).strftime("%Y-%m-%d %H:%M")

    reach, why = rest_reachable()
    model["reach"] = (reach, why)
    model["sources"].append({"name": "AUTO-MAS REST", "ok": reach, "detail": why})
    if not reach:
        model["accounts"] = {"expected": 0, "done": 0, "gap": 0, "gap_accounts": [], "norec": [],
                             "failed_only": [], "l_accounts": [], "disabled": [], "detail": [],
                             "rate_account": None}
        model["history"] = {"ok": False, "error": why, "days": {}}
        model["schedule"] = {"rows": [], "errors": [why], "queue_times": {}}
        model["streaks"] = []
        model["dispatch"] = collect_dispatch(date_s, (start, end))
        model["ledger"] = collect_ledger((start, end))
        model["errors"] = None
        model["switch_gate"] = None
        model["idle"] = None
        model["check_schedule"] = None
        model["health"] = None
    else:
        model["schedule"] = collect_schedule_rows(shift)
        model["history"] = collect_history(date_s)
        model["accounts"] = classify_accounts(model["schedule"], model["history"], date_s)
        model["streaks"] = compute_streaks(model["history"], date_s) if model["history"].get("ok") else []
        model["dispatch"] = collect_dispatch(date_s, (start, end))
        model["ledger"] = collect_ledger((start, end))
        model["errors"] = collect_errors(date_s, skip=opts.get("skip_errors", False))
        model["switch_gate"] = collect_switch_gate()
        model["idle"] = collect_idle()
        model["check_schedule"] = collect_check_schedule()
        model["health"] = collect_health()
    host = collect_host_sample()
    model["host"] = host
    model["sidecar"] = sidecar_view(host, (start, end))
    model["sources"].append({"name": "SSH 主机采样", "ok": host is not None,
                             "detail": "ok" if host else "不可达/超时"})
    # 到点提醒（周期事件）：独立于 AUTO-MAS REST；本地快照口径，失败只降级成 UNKNOWN 行，不阻塞日报
    model["event_cal"] = collect_event_calendar()
    model["gap_n"] = int(opts.get("gap_n") or GAP_N_DEFAULT)
    model["streak_days"] = int(opts.get("streak_days") or STREAK_DAYS_DEFAULT)
    return model


# ---------------------------------------------------------------- 渲染

def _fmt_ts(s: str) -> str:
    return str(s or "-")


def render_markdown(model: dict, events: list, new_events: list, cleared: list,
                    send_planned: bool = False) -> str:
    a = model["accounts"]
    shift_label = model["shift_label"]
    date_s = model["date"]
    ws, we = model["window_start"], model["window_end"]
    L = []
    L.append("# 班次日报 · %s %s" % (shift_label, date_s))
    L.append("")
    L.append("- 生成: %s (Asia/Shanghai) ｜ 班次日: %s（口径 [D 05:00, D+1 05:00)）" % (model["now"], date_s))
    L.append("- 班次窗口: %s–%s ｜ 活跃窗 %s–%s ｜ 数据截至生成时刻%s"
             % (ws.strftime("%H:%M"), we.strftime("%H:%M"),
                ws.strftime("%H:%M"), (ws + dt.timedelta(minutes=ACTIVE_MIN)).strftime("%H:%M"),
                "" if model.get("mature") else "（班次进行中）"))
    L.append("- 数据源: AUTO-MAS REST（history/queue/scripts/health）· 本机 runtime 台账 · SSH 主机采样 · "
             "子工具（mas_switch_gate / mas_idle_emulators / maa_error_daily / mas_dispatch --check-schedule / "
             "event_calendar）")
    L.append("- 脱敏: 本文件不含凭据；账号用名字（无手机号/密码/token）")
    L.append("")
    L.append("## 0. 结论")
    L.append("")
    rate = ("%.1f%%" % (a.get("rate_account") * 100)) if a.get("rate_account") is not None else "UNKNOWN"
    err_stats = ""
    hist = ((model.get("errors") or {}).get("data") or {}).get("hist") or {}
    if hist:
        tot = (hist.get("ok") or 0) + (hist.get("fail") or 0)
        if tot:
            err_stats = " ｜ 尝试级成功率 **%.1f%%**（%d/%d）" % (100.0 * (hist.get("ok") or 0) / tot,
                                                                hist.get("ok") or 0, tot)
    L.append("应跑 **%d** ｜ 实跑 **%d** ｜ 缺口 **%d**（未跑 %d / 仅失败 %d）｜ 完成率 **%s**%s ｜ 告警 **%d** 条（新 %d）"
             % (a["expected"], a["done"], a["gap"], len(a["norec"]), len(a["failed_only"]), rate, err_stats,
                len(events), len(new_events)))
    if model["notes"]:
        L.append("")
        L.append("> 注: " + "；".join(model["notes"]))
    L.append("")

    # 1. 逐号
    L.append("## 1. 应跑 / 实跑 / 缺口（逐号）")
    L.append("")
    if not a["detail"]:
        L.append("- UNKNOWN: 账号清单不可用（%s）" % ((model["schedule"].get("errors") or ["?"])[0]))
    else:
        L.append("| # | 账号 | 队列 | 状态 | DONE/ERR | 最后记录 | 备注 |")
        L.append("|---|---|---|---|---|---|---|")
        order = {"未跑": 0, "仅失败": 1, "完成": 2}
        for i, d in enumerate(sorted(a["detail"], key=lambda x: (order.get(x["status"], 9), x["account"])), 1):
            note = ""
            if d["status"] != "完成":
                note = "缺口" + ("（末错: %s）" % d["last_error"][:60] if d["last_error"] else "")
            L.append("| %d | %s | %s | %s | %d/%d | %s | %s |"
                     % (i, d["account"], ",".join(d["queues"]), d["status"], d["done"], d["err"],
                        _fmt_ts(d["last"]), note))
        if a["l_accounts"] or a["disabled"]:
            L.append("")
            L.append("- 已排除: 肉鸽机队 `l-*`（%d：%s）｜ 停用号（%d：%s）"
                     % (len(a["l_accounts"]), "、".join(a["l_accounts"]) or "-",
                        len(a["disabled"]), "、".join(a["disabled"]) or "-"))
    L.append("")

    # 2. 失败类型 Top
    L.append("## 2. 失败类型 Top（四源聚合：history/gui/asst/app.log）")
    L.append("")
    ed = (model.get("errors") or {}).get("data") or {}
    counts = ed.get("counts") or {}
    if counts:
        by_cat = counts.get("by_cat") or {}
        top_cat = " ｜ ".join("%s ×%d" % (k, v) for k, v in
                             sorted(by_cat.items(), key=lambda kv: -kv[1])[:5])
        raw = {}
        for it in (ed.get("items") or []):
            r = str(it.get("raw") or "").strip()
            if not r:
                r = "（无原始文案，见 %s）" % (it.get("cat") or "未知")
            raw[r] = raw.get(r, 0) + 1
        top_raw = " ｜ ".join("%s ×%d" % (k, v) for k, v in sorted(raw.items(), key=lambda kv: -kv[1])[:4])
        L.append("- 总量: %d 条（致命 %s / 告警 %s / 可忽略 %s）；登录类 %s 条%s"
                 % (counts.get("n") or 0, counts.get("fatal"), counts.get("warn"), counts.get("benign"),
                    counts.get("login_n"), "（已升级告警）" if counts.get("escalated") else ""))
        L.append("- Top 类型: %s" % top_cat)
        L.append("- Top 原始错误: %s" % top_raw)
        L.append("- 详情: `%s`" % ((model.get("errors") or {}).get("path")))
    else:
        L.append("- UNKNOWN: 错误聚合不可用（maa_error_daily 未产出；--skip-errors 时正常）")
    L.append("")

    # 3. 补跑
    L.append("## 3. 补跑 / 按需发车结果（本窗口台账）")
    L.append("")
    ledger = model.get("ledger") or {}
    entries = ledger.get("entries") or []
    ondemand = ledger.get("ondemand") or []
    if not entries and not ondemand:
        L.append("- 本窗口无补跑/按需发车记录。")
    else:
        L.append("| 时间 | 类型 | 目标 | 结果 | 详情 |")
        L.append("|---|---|---|---|---|")
        for e in entries:
            tgt = "、".join(e.get("accounts") or []) or str(e.get("scope") or e.get("run_id") or "-")
            L.append("| %s | %s | %s | %s | %s |"
                     % (str(e.get("ts"))[11:19], e.get("kind"), tgt,
                        "ok" if e.get("ok") else "fail", str(e.get("detail") or "-")[:90].replace("\n", " ")))
        for o in ondemand:
            L.append("| %s | 按需(%s) | %s | %s | %s |"
                     % (str(o.get("ts"))[11:19], o.get("target_kind") or o.get("kind") or "-",
                        o.get("target"), o.get("status"), o.get("temp_queue") or "-"))
    recovered = [d["account"] for d in (model["accounts"].get("detail") or [])
                 if d["status"] == "完成" and d["err"] > 0]
    if recovered:
        L.append("")
        L.append("- 窗口内先错后成（重试/补跑转正）: %s" % "、".join(recovered[:15]))
    L.append("")

    # 4. 资源水位
    L.append("## 4. 资源水位（主机采样）")
    L.append("")
    host = model.get("host")
    if not host:
        L.append("- UNKNOWN: 主机采样失败（SSH 不可达/超时）")
    else:
        mem = host.get("free_mem_mb")
        disks = host.get("disks") or {}
        dparts = []
        warn = {"C": 60, "E": 150}
        for name in sorted(disks):
            d = disks[name]
            flag = " ⚠低水位" if name in warn and d["free_gb"] < warn[name] else ""
            dparts.append("%s: %sG/%sG%s" % (name, d["free_gb"], d["total_gb"], flag))
        L.append("- 内存可用: %s MB（%.1f GB）｜ 磁盘: %s"
                 % (mem, (mem or 0) / 1024.0, " ｜ ".join(dparts) or "-"))
        L.append("- 在飞: MAA=%s / VMM=%s / adb 在线=%s" % (host.get("maa"), host.get("vmm"), host.get("adb_online")))
        sc = model["sidecar"]
        L.append("- 侧车: pid=%s 起于 %s（班次活跃窗内%s重启）"
                 % (sc.get("pid"), sc.get("start"),
                    "有" if sc.get("abnormal") else "无"))
    L.append("")

    # 5. 空转实例
    L.append("## 5. 空转实例")
    L.append("")
    idle = model.get("idle") or {}
    counts = idle.get("counts") or {}
    if not counts:
        L.append("- UNKNOWN: 空转扫描不可用（mas_idle_emulators 未产出）")
    else:
        running = int(counts.get("running") or 0)
        in_use = int(counts.get("in_use") or 0)
        idle_n = max(0, running - in_use)
        L.append("- 运行中 %d ｜ 在用 %d ｜ **空转 %d** ｜ 可关候选 %d（口径: 空转=运行中−在用；候选=不在下一班/补跑口径）"
                 % (running, in_use, idle_n, int(counts.get("candidates") or 0)))
        rows = [r for r in (idle.get("rows") or []) if not r.get("in_use")]
        if rows:
            L.append("- 空转明细: " + "；".join("idx%s(%s)" % (r.get("idx"), str(r.get("title"))[:18]) for r in rows[:8]))
        cmds = idle.get("suggested_commands") or []
        if cmds:
            L.append("- 建议关闭（本工具不执行）: %s" % "；".join(str(c) for c in cmds[:5]))
    L.append("")

    # 6. 配置漂移 / SWITCH_GATE
    L.append("## 6. 配置漂移 / SWITCH_GATE")
    L.append("")
    cs = model.get("check_schedule")
    if not cs:
        L.append("- 班次表一致性: UNKNOWN（mas_dispatch --check-schedule 不可用）")
    else:
        brief = cs.get("brief") or {}
        drift = cs.get("drift") or []
        L.append("- 班次表一致性: %s（%s/%s 队列在位；漂移 %d%s）"
                 % ("OK" if cs.get("ok") else "FAIL", brief.get("found"), brief.get("scheduled"),
                    len(drift), ("：" + "、".join(str(x) for x in drift[:5])) if drift else ""))
        for w in (cs.get("warnings") or [])[:3]:
            L.append("  - 警告: %s" % w)
        for p in (cs.get("problems") or [])[:5]:
            L.append("  - 问题: %s" % p)
    sg = model.get("switch_gate")
    if not sg:
        L.append("- SWITCH_GATE=UNKNOWN（mas_switch_gate 不可用）")
    else:
        L.append("- SWITCH_GATE=%s" % sg.get("switch_gate"))
        viol = sg.get("violations") or []
        for v in viol[:6]:
            L.append("  - 违规: %s" % json.dumps(v, ensure_ascii=False)[:220])
    L.append("")

    # 7. 发车审计
    L.append("## 7. 发车审计（Hermes 调度）")
    L.append("")
    disp = model.get("dispatch") or {}
    sent = disp.get("sent") or {}
    table = ["日常定时%d" % i for i in range(1, 13)] if model["shift"] == "daily" else \
            ["晚班定时%d" % i for i in range(1, 13)]
    sent_names = [n for n in table if n in sent]
    not_sent = [n for n in table if n not in sent]
    L.append("- 已发 %d/12（%s）｜ 未发: %s" % (len(sent_names), ", ".join("%s@%s" % (n, str(sent[n].get("at"))[11:19]) for n in sent_names[:12]),
                                        "、".join(not_sent) or "无"))
    late = disp.get("late") or {}
    L.append("- 迟到超窗（late）: %s" % ("、".join(late.keys()) or "无"))
    alerts_a = disp.get("alerts_active") or []
    L.append("- 发车告警台账: %s" % ("、".join("%s:%s" % (e.get("queue"), e.get("reason")) for e in alerts_a) if alerts_a else "空"))
    L.append("- 窗口内审计行: %d 条（runtime/mas_dispatch_log.jsonl）" % len(disp.get("log_lines") or []))
    L.append("")

    # 8. 告警评估
    L.append("## 8. 告警评估（本次）")
    L.append("")
    L.append("- 触发: %s" % ("无" if not events else "；".join("[%s]%s" % (e["severity"], e["kind"]) for e in events)))
    for e in events:
        L.append("  - `%s` %s: %s" % (e["kind"], e["subject"], e["detail"]))
    if new_events:
        L.append("- 新告警（待通知）: %d 条" % len(new_events))
    if cleared:
        L.append("- 已消除: %s" % "、".join(cleared))
    if send_planned:
        L.append("- 渠道: email（`--alert-send` 生效；复用 runtime/watchdog.ini，与 AUTO-MAS 通知同源）"
                 "｜ 状态: runtime/shift_report_state.json")
    else:
        L.append("- 渠道: file（dry-run；payload 落 runtime/reports/shift_alert_*.json；`--alert-send` 才真发）"
                 "｜ 状态: runtime/shift_report_state.json")
    L.append("")

    # 9. 到点提醒（周期事件）
    L.append("## 9. 到点提醒（周期事件 · event_calendar）")
    L.append("")
    ec = model.get("event_cal") or {}
    eclines = ec.get("lines") or {}
    if eclines:
        for key in ("EVENT_CAL", "UPCOMING", "EVENT_ADVICE"):
            if eclines.get(key):
                L.append(eclines[key])
    else:
        L.append("EVENT_CAL=UNKNOWN:%s" % (ec.get("detail") or "未采集"))
        L.append("UPCOMING=UNKNOWN:%s" % (ec.get("detail") or "未采集"))
    if not ec.get("ok"):
        L.append("")
        L.append("> 到点提醒降级（%s）；来源 `tools/event_calendar.py --check`（只读、零写盘），"
                 "不影响其余区块。" % (ec.get("detail") or "-"))
    L.append("")

    # 附
    L.append("## 附: 数据源与新鲜度")
    L.append("")
    L.append("| 源 | 结果 | 说明 |")
    L.append("|---|---|---|")
    for s in model["sources"]:
        L.append("| %s | %s | %s |" % (s["name"], "ok" if s["ok"] else "FAIL", str(s["detail"])[:100]))
    L.append("| maa_error_daily | %s | %s |" % ("ok" if (model.get("errors") or {}).get("data") else "FAIL",
                                               (model.get("errors") or {}).get("path") or "-"))
    L.append("| switch_gate | %s | %s |" % ("ok" if model.get("switch_gate") else "FAIL",
                                            (model.get("switch_gate") or {}).get("switch_gate") or "-"))
    L.append("| idle_emulators | %s | candidates=%s |" % ("ok" if (model.get("idle") or {}).get("counts") else "FAIL",
                                                          ((model.get("idle") or {}).get("counts") or {}).get("candidates")))
    L.append("| check-schedule | %s | %s |" % ("ok" if model.get("check_schedule") else "FAIL",
                                               ((model.get("check_schedule") or {}).get("brief"))))
    L.append("| event_calendar | %s | %s |" % ("ok" if (model.get("event_cal") or {}).get("ok") else "FAIL",
                                               ((model.get("event_cal") or {}).get("detail") or "-")))
    L.append("")
    L.append("> 复跑: `python tools/mas_shift_report.py --shift %s --date %s`" % (model["shift"], model["date"]))
    return "\n".join(L) + "\n"


def render_card(model: dict, events: list, report_path: str) -> tuple[str, str, str]:
    a = model["accounts"]
    title = "班次日报 · %s %s" % (model["shift_label"], model["date"])
    rate = ("%.1f%%" % (a.get("rate_account") * 100)) if a.get("rate_account") is not None else "UNKNOWN"
    lines = [
        "**%s**（生成 %s）" % (title, model["now"]),
        "",
        "- 应跑 %d / 实跑 %d / 缺口 %d（未跑 %d / 仅失败 %d）/ 完成率 %s"
        % (a["expected"], a["done"], a["gap"], len(a["norec"]), len(a["failed_only"]), rate),
    ]
    if a.get("gap_accounts") or []:
        lines.append("- 缺口号: %s" % "、".join((a.get("gap_accounts") or [])[:12]))
    ed = (model.get("errors") or {}).get("data") or {}
    counts = ed.get("counts") or {}
    if counts:
        top = counts.get("by_cat") or {}
        lines.append("- 失败 Top: %s（共 %d 条）"
                     % ("、".join("%s×%d" % (k, v) for k, v in sorted(top.items(), key=lambda kv: -kv[1])[:4]),
                        counts.get("n") or 0))
    sg = model.get("switch_gate") or {}
    lines.append("- SWITCH_GATE=%s ｜ 侧车=%s ｜ 空转=%s"
                 % (sg.get("switch_gate") or "UNKNOWN",
                    "异常" if (model.get("sidecar") or {}).get("abnormal") else "正常",
                    ((model.get("idle") or {}).get("counts") or {}).get("candidates")))
    lines.append("- 告警: %s" % ("无" if not events else "；".join(e["kind"] for e in events)))
    lines.append("")
    lines.append("完整报告: `%s`" % report_path)
    lines.append("")
    lines.append("> 只读汇总卡（status=done，无 assignee，不参与派工）。本卡由 tools/mas_shift_report.py 自动维护。")
    body = "\n".join(lines)
    result = "班次日报 %s %s：应跑 %d / 实跑 %d / 缺口 %d；告警 %d；report=%s" % (
        model["shift"], model["date"], a["expected"], a["done"], a["gap"], len(events), report_path)
    return title, body, result


def render_payload(model: dict, events: list, new_events: list, cleared: list, report_path: str,
                   channel: str, send: bool) -> dict:
    a = model["accounts"]
    title = "[MAAOrch] 班次告警 · %s %s · %d 条" % (model["shift_label"], model["date"], len(new_events)) \
        if new_events else "[MAAOrch] 班次日报 · %s %s · 无告警" % (model["shift_label"], model["date"])
    summary_lines = ["班次日报 %s %s（生成 %s）" % (model["shift_label"], model["date"], model["now"]),
                     "应跑 %d / 实跑 %d / 缺口 %d ｜ 完成率 %s" % (
                         a["expected"], a["done"], a["gap"],
                         ("%.1f%%" % (a.get("rate_account") * 100)) if a.get("rate_account") is not None else "UNKNOWN")]
    if a.get("gap_accounts") or []:
        summary_lines.append("缺口号: " + "、".join((a.get("gap_accounts") or [])[:12]))
    for e in new_events:
        summary_lines.append("[%s] %s — %s" % (e["severity"], e["kind"], e["detail"]))
    if cleared:
        summary_lines.append("已消除: " + "、".join(cleared))
    summary_lines.append("报告: " + report_path)
    return {
        "schema": "maaorch.shift_alert/1",
        "title": title,
        "generated_at": model["now"],
        "shift": model["shift"], "date": model["date"],
        "report": report_path,
        "events": events,
        "new_events": new_events,
        "cleared_events": cleared,
        "summary": "\n".join(summary_lines),
        "channel": channel,
        "send": bool(send),
        "test": bool(model.get("alert_test")),
    }


# ---------------------------------------------------------------- 看板卡（直写 SQLite）

def kanban_db_path() -> str | None:
    env = os.environ.get("HERMES_KANBAN_DB", "").strip()
    if env and os.path.exists(env):
        return env
    home = os.path.expanduser("~")
    roots = [os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"),
                          "hermes", "kanban"),
             os.path.join(home, ".hermes", "kanban")]
    for kroot in roots:
        cur = os.path.join(kroot, "current")
        if os.path.exists(cur):
            try:
                slug = open(cur, "r", encoding="utf-8").read().strip()
            except OSError:
                slug = ""
            db = os.path.join(kroot, "boards", slug, "kanban.db")
            if slug and os.path.exists(db):
                return db
        db = os.path.join(kroot, "kanban.db")
        if os.path.exists(db):
            return db
    return None


def board_upsert(title: str, body: str, result: str, key: str) -> dict:
    """在活跃看板开/更新一张只读汇总卡（status=done、无 assignee）。直写 SQLite：
    字段形状照 hermes_cli.kanban_db 的 create_task/complete_task；不参与派工。"""
    db = kanban_db_path()
    if not db:
        return {"ok": False, "error": "看板 DB 不可定位（HERMES_KANBAN_DB/current 均无）"}
    try:
        con = sqlite3.connect(db, timeout=15)
        con.execute("PRAGMA busy_timeout=10000")
        cols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}

        def _run_row(tid_: str, result_: str, ts_: int) -> None:
            tcols = {r[1] for r in con.execute("PRAGMA table_info(task_runs)")}
            if not tcols:
                return
            rvals = {"task_id": tid_, "profile": "shift-report", "status": "done", "started_at": ts_,
                     "ended_at": ts_, "outcome": "completed", "summary": result_,
                     "metadata": json.dumps({"source": "mas_shift_report", "idempotency_key": key})}
            ruse = {k: v for k, v in rvals.items() if k in tcols}
            con.execute("INSERT INTO task_runs (%s) VALUES (%s)" % (", ".join(ruse), ", ".join("?" * len(ruse))),
                        list(ruse.values()))

        now = int(dt.datetime.now().timestamp())
        row = con.execute("SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived' "
                          "ORDER BY created_at DESC LIMIT 1", (key,)).fetchone()
        if row:
            tid = row[0]
            sets, vals = [], []
            for c, v in (("title", title), ("body", body), ("result", result), ("completed_at", now)):
                if c in cols:
                    sets.append("%s = ?" % c)
                    vals.append(v)
            vals.append(tid)
            con.execute("UPDATE tasks SET %s WHERE id = ?" % ", ".join(sets), vals)
            if "task_comments" in {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}:
                con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                            (tid, "shift-report", "重新生成（%s）" % now_str(), now))
            con.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
                        (tid, None, "commented", json.dumps({"author": "shift-report",
                                                             "note": "重新生成"}, ensure_ascii=False), now))
            _run_row(tid, result, now)
            con.commit()
            con.close()
            return {"ok": True, "id": tid, "how": "updated", "db": db}
        tid = "t_" + secrets.token_hex(4)
        values = {
            "id": tid, "title": title, "body": body, "assignee": None, "status": "done",
            "priority": 0, "created_by": "shift-report", "created_at": now, "completed_at": now,
            "workspace_kind": "dir", "workspace_path": ROOT, "result": result,
            "idempotency_key": key, "completion_contract": "local-only",
            "goal_mode": 0, "block_recurrences": 0, "consecutive_failures": 0,
        }
        use = {k: v for k, v in values.items() if k in cols}
        con.execute("INSERT INTO tasks (%s) VALUES (%s)" % (", ".join(use), ", ".join("?" * len(use))),
                    list(use.values()))
        con.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
                    (tid, None, "created", json.dumps({"assignee": None, "status": "done",
                                                       "workspace_kind": "dir", "workspace_path": ROOT,
                                                       "idempotency_key": key}, ensure_ascii=False), now))
        con.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
                    (tid, None, "completed", json.dumps({"result": result, "summary": result,
                                                         "source": "shift-report"}, ensure_ascii=False), now))
        tcols = {r[1] for r in con.execute("PRAGMA table_info(task_runs)")}
        if tcols:
            rvals = {"task_id": tid, "profile": "shift-report", "status": "done", "started_at": now,
                     "ended_at": now, "outcome": "completed", "summary": result,
                     "metadata": json.dumps({"source": "mas_shift_report", "idempotency_key": key})}
            ruse = {k: v for k, v in rvals.items() if k in tcols}
            con.execute("INSERT INTO task_runs (%s) VALUES (%s)" % (", ".join(ruse), ", ".join("?" * len(ruse))),
                        list(ruse.values()))
        con.commit()
        con.close()
        return {"ok": True, "id": tid, "how": "created", "db": db}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


# ---------------------------------------------------------------- 告警状态/发送

def alert_state_update(events: list, date_s: str, shift: str) -> tuple[list, list]:
    """返回 (new_events, cleared_subjects)；状态记 shift_report_state.json（去重+消除）。"""
    st = load_json(STATE_FILE) or {}
    alerts = st.get("alerts") or {}
    now = now_str()
    new_events, cleared = [], []
    seen = set()
    for e in events:
        k = "%s|%s|%s|%s" % (date_s, shift, e["kind"], e["subject"])
        seen.add(k)
        rec = alerts.get(k)
        if rec is None:
            alerts[k] = {"first": now, "last": now, "notified": False, "kind": e["kind"], "subject": e["subject"]}
            new_events.append(e)
        else:
            rec["last"] = now
            if not rec.get("notified"):
                new_events.append(e)
    # 消除：本班此前 active、本轮消失的
    for k, rec in list(alerts.items()):
        if k.startswith("%s|%s|" % (date_s, shift)) and k not in seen and not rec.get("cleared"):
            rec["cleared"] = True
            rec["cleared_at"] = now
            cleared.append("%s:%s" % (rec.get("kind"), rec.get("subject")))
    st["alerts"] = alerts
    st["updated"] = now
    save_json_atomic(STATE_FILE, st)
    return new_events, cleared


def alert_mark_notified(events: list, date_s: str, shift: str) -> None:
    st = load_json(STATE_FILE) or {}
    alerts = st.get("alerts") or {}
    for e in events:
        k = "%s|%s|%s|%s" % (date_s, shift, e["kind"], e["subject"])
        if k in alerts:
            alerts[k]["notified"] = True
            alerts[k]["notified_at"] = now_str()
    st["alerts"] = alerts
    save_json_atomic(STATE_FILE, st)


def write_alert_files(payload: dict, date_s: str, shift: str) -> list:
    outs = []
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, "shift_alert_%s_%s.json" % (date_s.replace("-", ""), shift))
    save_json_atomic(path, payload)
    outs.append(path)
    try:
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": payload.get("generated_at"), "shift": shift, "date": date_s,
                                "n_events": len(payload.get("events") or []),
                                "n_new": len(payload.get("new_events") or []),
                                "send": payload.get("send"), "channel": payload.get("channel"),
                                "title": payload.get("title")}, ensure_ascii=False) + "\n")
        outs.append(ALERT_LOG)
    except OSError:
        pass
    return outs


def send_email(payload: dict) -> dict:
    """真发（--alert-send）：复用 host_watchdog 的通知配置（watchdog.ini，与 AUTO-MAS 通知同源）。"""
    try:
        sys.path.insert(0, TOOLS)
        import host_watchdog  # noqa: PLC0415
        c = host_watchdog.load_conf()
        host_watchdog.notify(c, payload["title"], payload["summary"], send=True)
        return {"ok": True, "channel": c["notify"].get("channel") or "email"}
    except SystemExit as e:
        return {"ok": False, "error": "watchdog.ini 缺失: %s" % e}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


# ---------------------------------------------------------------- 自检

def cmd_check() -> int:
    print("mas_shift_report --check")
    problems, warns = [], []
    reach, why = rest_reachable()
    print("  REST %s: %s" % ("ok" if reach else "FAIL", why))
    if not reach:
        problems.append("REST 不可达: %s" % why)
    for f in ("mas_dispatch_state.json", "mas_dispatch_alerts.json", "mas_dispatch_log.jsonl",
              "mas_ledger.jsonl", "mas_ondemand_ledger.json"):
        p = os.path.join(RUNTIME, f)
        ok = os.path.exists(p)
        print("  runtime/%s: %s" % (f, "ok" if ok else "MISSING"))
        if not ok:
            warns.append("缺 %s（对应区块会 UNKNOWN）" % f)
    for t in ("mas_switch_gate.py", "mas_idle_emulators.py", "maa_error_daily.py", "mas_dispatch.py",
              "event_calendar.py"):
        ok = os.path.exists(os.path.join(TOOLS, t))
        print("  tools/%s: %s" % (t, "ok" if ok else "MISSING"))
        if not ok:
            problems.append("缺 tools/%s" % t)
    db = kanban_db_path()
    print("  kanban db: %s" % (db or "MISSING"))
    if not db:
        warns.append("看板 DB 不可定位（--no-card 可绕开）")
    if reach:
        try:
            rest_post("/api/history/search", {"mode": "DAILY",
                                              "start_date": dt.date.today().isoformat(),
                                              "end_date": dt.date.today().isoformat()})
            print("  history/search: ok")
        except RestError as e:
            problems.append("history/search 失败: %s" % e)
            print("  history/search: FAIL %s" % e)
    for w in warns:
        print("  - 警告: %s" % w)
    for p in problems:
        print("  - 问题: %s" % p)
    print("CHECK=%s" % ("OK" if not problems else "FAIL"))
    return 2 if problems else 0


def cmd_selftest() -> int:
    """离线自检：纯函数 + 夹具（不连网、不写 runtime）。"""
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)
        print("  %s %s" % ("ok " if cond else "FAIL", name))

    # 窗口
    s, e = window_bounds("daily", "2026-09-28")
    check("daily 窗口起=05:00", s.strftime("%H:%M") == "05:00")
    check("daily 窗口终=12:00", e.strftime("%H:%M") == "12:00")
    s, e = window_bounds("evening", "2026-09-28")
    check("evening 窗口起=19:30", s.strftime("%H:%M") == "19:30")
    check("evening 窗口终=次日05:00", e.strftime("%Y-%m-%d %H:%M") == "2026-09-29 05:00")
    check("shift 自动判定(daily)", resolve_shift("daily") == "daily")

    # 脱敏
    sc = scrub({"Password": "x", "SklandToken": "y", "Info": {"Name": "a", "Password": "z"}})
    check("脱敏丢凭据键", "Password" not in sc and "SklandToken" not in sc and sc["Info"]["Name"] == "a")

    # 分类（夹具）
    schedule = {"rows": [
        {"account": "A", "queue": "日常定时1", "script": "明日方舟-官-1", "enabled": True, "server": "Official"},
        {"account": "B", "queue": "日常定时1", "script": "明日方舟-官-1", "enabled": True, "server": "Official"},
        {"account": "L", "queue": "日常定时1", "script": "l-1-酸酸酸酸", "enabled": True, "server": "YoStarJP"},
        {"account": "D", "queue": "日常定时1", "script": "明日方舟-官-2", "enabled": False, "server": "Official"},
    ], "errors": [], "queue_times": {}}
    history = {"ok": True, "days": {"2026-09-28": {
        "A": {"records": [{"date": "2026-09-28 05:05:00", "status": "DONE"}], "done": 1, "err": 0, "last": "2026-09-28 05:05:00"},
        "B": {"records": [{"date": "2026-09-28 05:06:00", "status": "ERROR", "error": "MAA 未能正确登录 PRTS"}],
              "done": 0, "err": 1, "last": "2026-09-28 05:06:00"},
    }}}
    cls = classify_accounts(schedule, history, "2026-09-28")
    check("应跑=2（排 l-*/停用）", cls["expected"] == 2)
    check("实跑=1", cls["done"] == 1)
    check("缺口=1（仅失败 B）", cls["gap"] == 1 and cls["failed_only"] == ["B"])
    check("排除 l-*", cls["l_accounts"] == ["L"] and cls["disabled"] == ["D"])

    # 连续失败（口径：err>0 且 done==0 才算失败日；重试自愈不算）
    history2 = {"ok": True, "days": {
        "2026-09-26": {"X": {"records": [{"date": "2026-09-26 05:05:00", "status": "ERROR"}], "done": 0, "err": 1, "last": "2026-09-26 05:05:00"}},
        "2026-09-27": {"X": {"records": [{"date": "2026-09-27 05:05:00", "status": "ERROR"}], "done": 0, "err": 1, "last": "2026-09-27 05:05:00"}},
        "2026-09-28": {"X": {"records": [{"date": "2026-09-28 05:05:00", "status": "ERROR"}], "done": 0, "err": 1, "last": "2026-09-28 05:05:00"}},
    }}
    st = compute_streaks(history2, "2026-09-28")
    check("连续失败 streak=3", len(st) == 1 and st[0]["streak"] == 3)
    # 重试自愈（当天先错后成）不算失败日 ⇒ 无 streak
    history3 = {"ok": True, "days": {
        "2026-09-27": {"Y": {"records": [{"date": "2026-09-27 05:05:00", "status": "ERROR"},
                                         {"date": "2026-09-27 05:20:00", "status": "DONE"}], "done": 1, "err": 1, "last": "2026-09-27 05:20:00"}},
        "2026-09-28": {"Y": {"records": [{"date": "2026-09-28 05:05:00", "status": "ERROR"},
                                         {"date": "2026-09-28 05:20:00", "status": "DONE"}], "done": 1, "err": 1, "last": "2026-09-28 05:20:00"}},
    }}
    check("重试自愈不算连续失败", compute_streaks(history3, "2026-09-28") == [])

    # 子工具 JSON 提取：单行 + 多行（indent）两种都要吃
    check("_last_json_line 单行", _last_json_line('log line\n{"a": 1}') == {"a": 1})
    check("_last_json_line 多行", _last_json_line('x\n{\n "b": 2,\n "c": [1]\n}\n') == {"b": 2, "c": [1]})
    check("_last_json_line 无 JSON", _last_json_line("no json here") is None)

    # 告警判据（夹具：缺口 3 > 2，成熟）
    model = {"now_dt": dt.datetime(2026, 9, 28, 9, 0), "window_start": dt.datetime(2026, 9, 28, 5, 0),
             "reach": (True, "code=200"), "health": {"ready": True, "backgroundStatus": "ready"},
             "sidecar": {"abnormal": False, "reasons": []},
             "accounts": {"expected": 5, "done": 2, "gap": 3, "gap_accounts": ["a", "b", "c"],
                          "norec": ["a"], "failed_only": ["b", "c"]},
             "idle": {"counts": {"running": 4, "in_use": 4, "candidates": 0}},
             "streaks": [{"account": "X", "streak": 2, "last_day": "2026-09-28", "last_ts": "2026-09-28 05:05:00",
                          "last_error": "e"}],
             "dispatch": {"alerts_active": [{"queue": "日常定时7", "reason": "rest_unreachable", "detail": "x"}]},
             "notes": []}
    ev = eval_alerts(model, gap_n=2, streak_days=2)
    kinds = {e["kind"] for e in ev}
    check("告警: dispatch_failed", "dispatch_failed" in kinds)
    check("告警: gap_over_n", "gap_over_n" in kinds)
    check("告警: all_busy", "all_busy" in kinds)
    check("告警: consecutive_failed", "consecutive_failed" in kinds)
    # 成熟度闸：班次刚起 → 缺口/全忙/连续失败不报
    model2 = dict(model)
    model2["now_dt"] = dt.datetime(2026, 9, 28, 5, 30)
    model2["notes"] = []
    ev2 = eval_alerts(model2, gap_n=2, streak_days=2)
    kinds2 = {e["kind"] for e in ev2}
    check("成熟度闸: 未成熟不报 gap/all_busy/streak",
          "gap_over_n" not in kinds2 and "all_busy" not in kinds2 and "consecutive_failed" not in kinds2)
    check("成熟度闸: dispatch_failed 仍报", "dispatch_failed" in kinds2)

    # payload/卡片/报告渲染
    model3 = dict(model)
    model3.update({"shift": "daily", "shift_label": "日常班", "date": "2026-09-28",
                   "now": "2026-09-28 09:00:00", "notes": [], "mature": True,
                   "window_start": dt.datetime(2026, 9, 28, 5, 0),
                   "window_end": dt.datetime(2026, 9, 28, 12, 0), "sources": [],
                   "schedule": {"errors": []},
                   "accounts": dict(model["accounts"], detail=[], l_accounts=[], disabled=[])})
    payload = render_payload(model3, ev, ev, [], "/tmp/x.md", "file", False)
    check("payload schema", payload["schema"] == "maaorch.shift_alert/1" and len(payload["events"]) == len(ev))
    t, b, r = render_card(model3, ev, "/tmp/x.md")
    check("卡片含结论行", "应跑" in b and "report" in r)
    # 到点提醒（UPCOMING=）接线：解析 + fail-closed + 进报告
    ec_ok = parse_event_cal("EVENT_CAL=OK（14 天 / 事件源 server_events / 快照 0.2 天前）\n"
                            "UPCOMING=国际服 周切：剿灭周额度重置 + 周任务刷新:2026-09-28 19:00（约 9.0 小时后）\n"
                            "EVENT_ADVICE=今日（周一）04:00 已周切：注意剿灭周额度\n")
    check("event_cal: 取 UPCOMING 行", ec_ok["UPCOMING"].startswith("UPCOMING=国际服 周切"))
    check("event_cal: 取 EVENT_CAL/EVENT_ADVICE 行",
          ec_ok["EVENT_CAL"].startswith("EVENT_CAL=OK") and ec_ok["EVENT_ADVICE"].startswith("EVENT_ADVICE="))
    check("event_cal: 无 UPCOMING 行 ⇒ fail-closed UNKNOWN",
          parse_event_cal("noise only\n")["UPCOMING"].startswith("UPCOMING=UNKNOWN:"))
    check("event_cal: 工具自身 UNKNOWN 原样保留",
          parse_event_cal("UPCOMING=UNKNOWN:活动快照损坏\n")["UPCOMING"] == "UPCOMING=UNKNOWN:活动快照损坏")
    model3["event_cal"] = {"ok": True, "detail": "rc=0", "lines": ec_ok}
    md_dry = render_markdown(model3, ev, ev, [], send_planned=False)
    md_send = render_markdown(model3, ev, ev, [], send_planned=True)
    check("报告渠道行: dry-run 与真发区分", "dry-run" in md_dry and "email" in md_send and "dry-run" not in md_send)
    check("报告第 9 节含 UPCOMING= 行", "## 9. 到点提醒" in md_dry and "UPCOMING=国际服 周切" in md_dry)
    model3b = dict(model3, event_cal={"ok": False, "detail": "缺 tools/event_calendar.py", "lines": {}})
    md_unk = render_markdown(model3b, ev, ev, [], send_planned=False)
    check("报告降级仍含 UPCOMING=UNKNOWN: 行", "UPCOMING=UNKNOWN:缺 tools/event_calendar.py" in md_unk)

    # 看板 DB 定位（只读）
    db = kanban_db_path()
    check("看板 DB 可定位或明确返回 None", db is None or os.path.exists(db))

    print("SELFTEST=%s（%d 项失败）" % ("OK" if not fails else "FAIL", len(fails)))
    return 0 if not fails else 3


# ---------------------------------------------------------------- 主流程

def alert_test_payload(model: dict) -> list:
    """模拟一条告警（每种关键事件各一），只打印/落 SAMPLE，绝不发送。"""
    mk = lambda kind, sev, subj, detail, ev: {"kind": kind, "severity": sev, "subject": subj,
                                              "detail": detail, "evidence": ev}
    return [
        mk("dispatch_failed", "critical", "日常定时7",
           "发车失败：rest_unreachable（HTTPError: connection refused）", {"reason": "rest_unreachable"}),
        mk("gap_over_n", "critical", "gap",
           "应跑 52 / 实跑 48，缺口 4 > 3：A、B、C、D", {"threshold": 3, "norec": ["A", "B"], "failed_only": ["C", "D"]}),
        mk("sidecar_abnormal", "critical", "sidecar",
           "侧车在班次活跃窗内重启（起于 2026-09-28 06:12:33）——重启会吞在飞任务的重试/收尾（孤儿化风险）",
           {"sidecar_start": "2026-09-28 06:12:33"}),
        mk("all_busy", "warn", "instances",
           "实例全忙：运行 4 / 在用 4，仍有缺口 4（无空闲产能消化）", {"running": 4, "in_use": 4}),
        mk("consecutive_failed", "warn", "样例号",
           "连续 2 天有失败记录（末次 2026-09-28 05:05）：MAA 未能正确登录 PRTS", {"streak": 2}),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="班次日报 + 告警渠道（只读采集；本地写；告警默认 dry-run）")
    ap.add_argument("--shift", choices=["daily", "evening"], help="班次；缺省按时间自动（<12:00=daily）")
    ap.add_argument("--date", help="班次日 YYYY-MM-DD；缺省今天（<05:00 归昨天）")
    ap.add_argument("--json", action="store_true", help="机读（单行 JSON）")
    ap.add_argument("--no-card", action="store_true", help="不建/不更新看板汇总卡")
    ap.add_argument("--no-alert", action="store_true", help="不评估告警")
    ap.add_argument("--alert-send", action="store_true", help="有新告警时真发（email；缺省 dry-run）")
    ap.add_argument("--alert-test", action="store_true", help="模拟告警 payload（打印+SAMPLE；绝不发送）")
    ap.add_argument("--gap-n", type=int, default=GAP_N_DEFAULT, help="缺口告警阈值（默认 %d）" % GAP_N_DEFAULT)
    ap.add_argument("--streak-days", type=int, default=STREAK_DAYS_DEFAULT, help="连续失败天数阈值（默认 %d）" % STREAK_DAYS_DEFAULT)
    ap.add_argument("--skip-errors", action="store_true", help="不跑 maa_error_daily（只用既有 JSON）")
    ap.add_argument("--check", action="store_true", help="连通性自检，退出码 0/2")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不连网），退出码 0/3")
    ap.add_argument("--no-report", action="store_true", help="不写报告文件（演练用）")
    args = ap.parse_args(argv)
    if os.environ.get("MAAORCH_SHIFT_ALERT_SEND", "0") == "1":
        args.alert_send = True

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if args.selftest:
        return cmd_selftest()
    if args.check:
        return cmd_check()

    shift = resolve_shift(args.shift)
    date_s = resolve_date(args.date, shift)
    opts = {"gap_n": args.gap_n, "streak_days": args.streak_days, "skip_errors": args.skip_errors}

    log("班次日报：%s %s（窗口 %s–%s）" % (SHIFT_LABEL[shift], date_s,
        window_bounds(shift, date_s)[0].strftime("%m-%d %H:%M"),
        window_bounds(shift, date_s)[1].strftime("%m-%d %H:%M")))
    model = build_model(shift, date_s, opts)
    model["alert_test"] = bool(args.alert_test)

    # 告警评估
    if args.alert_test:
        events = alert_test_payload(model)
        new_events, cleared = events, []
    elif args.no_alert:
        events, new_events, cleared = [], [], []
    else:
        events = eval_alerts(model, args.gap_n, args.streak_days)
        new_events, cleared = alert_state_update(events, date_s, shift)

    send_planned = bool(args.alert_send) and not args.alert_test and bool(new_events)
    report_path = os.path.join(REPORTS, "shift_report_%s_%s.md" % (date_s.replace("-", ""), shift))
    md = render_markdown(model, events, new_events, cleared, send_planned=send_planned)
    if not args.no_report and not args.alert_test:
        os.makedirs(REPORTS, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(md)
        log("报告: %s" % report_path)

    # 看板卡
    card = None
    if not args.no_card and not args.alert_test:
        title, body, result = render_card(model, events, report_path)
        key = "shift-report:%s:%s" % (date_s, shift)
        card = board_upsert(title, body, result, key)
        if card.get("ok"):
            log("看板卡: %s（%s）" % (card["id"], card["how"]))
        else:
            log("看板卡失败: %s" % card.get("error"))
        st = load_json(STATE_FILE) or {}
        st.setdefault("cards", {})["%s|%s" % (date_s, shift)] = {
            "task_id": card.get("id"), "updated": now_str(), "ok": bool(card.get("ok"))}
        st.setdefault("runs", {})["%s|%s" % (date_s, shift)] = {"generated_at": model["now"],
                                                               "report": report_path,
                                                               "gap": model["accounts"]["gap"]}
        save_json_atomic(STATE_FILE, st)

    # 告警 payload / 发送
    payload = None
    if args.alert_test or not args.no_alert:
        send = send_planned
        payload = render_payload(model, events, new_events, cleared, report_path,
                                 "email" if send else "file", send)
        if args.alert_test:
            path = os.path.join(REPORTS, "shift_alert_SAMPLE.json")
            os.makedirs(REPORTS, exist_ok=True)
            save_json_atomic(path, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=1))
            log("模拟告警 payload 已落 %s（未发送）" % path)
        else:
            write_alert_files(payload, date_s, shift)
            if new_events:
                log("告警 %d 条（新 %d）→ %s" % (len(events), len(new_events),
                                                "发送" if send else "dry-run（落文件，未发送）"))
            if send:
                res = send_email(payload)
                log("发送结果: %s" % res)
                if res.get("ok"):
                    alert_mark_notified(new_events, date_s, shift)

    if args.json:
        print(json.dumps({"ok": True, "shift": shift, "date": date_s,
                          "report": report_path, "card": card,
                          "accounts": {k: v for k, v in model["accounts"].items() if k != "detail"},
                          "events": events, "new_events": len(new_events), "cleared": cleared,
                          "payload": payload}, ensure_ascii=False))
    elif not args.alert_test:
        a = model["accounts"]
        print("REPORT=%s" % report_path)
        print("SHIFT=%s/%s | 应跑=%d 实跑=%d 缺口=%d 完成率=%s | 告警=%d(新%d) | 看板卡=%s"
              % (shift, date_s, a["expected"], a["done"], a["gap"],
                 ("%.1f%%" % (a.get("rate_account") * 100)) if a.get("rate_account") is not None else "UNKNOWN",
                 len(events), len(new_events), (card or {}).get("id") or "-"))
    if not model["reach"][0]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
