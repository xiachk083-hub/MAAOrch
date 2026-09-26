# -*- coding: utf-8 -*-
"""调度器（叫号模型）—— 步骤 1-6，默认 dry-run。

设计（用户 2026-09-18 拍板）：
  任务模型 → 六状态机 → 编号 YYYYMMDDHH-NNN → 叫号队列 → 主循环（scan→叫号→执行）
  有效优先级 = 基础优先级 - 等待小时数 × wait_weight（等得越久越靠前，防饿死）
  next_run = **完成时间** + 周期（不是上次时间），避免漂移；初始加一次抖动避免同步

**默认 dry-run**：只打印"它会叫谁"，不触发生产。真派发要显式 `--exec`
（写操作：POST /api/dispatch/start，且只在窗口外执行）。

用法:
    python tools/scheduler.py --selftest          # 纯逻辑自检（不联网）
    python tools/scheduler.py                     # dry-run 一轮（读 daily_state）
    python tools/scheduler.py --exec              # 真派发（危险，需本人确认）

产物: data/schedule/{tasks_<uid>.json, id_counter.json, config.json, script_map.json}
"""
from __future__ import annotations

import argparse
import base64
import configparser
import datetime as dt
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request

PROJ = pathlib.Path(__file__).resolve().parents[1]
SCHED = PROJ / "data" / "schedule"
DAILY = PROJ / "data" / "daily_state"
API = "http://100.79.173.69:36163"
CONF_INI = PROJ / "runtime" / "watchdog.ini"
SSH_KEY = os.environ.get("MAA_SSH_KEY", r"C:\Users\xiach\.ssh\maaorch_target")
UPTIME_CACHE = PROJ / "runtime" / "sched_uptime.json"
UPTIME_MIN_GATE = 30          # 开机 < 30 分钟 ⇒ 不派任务（机器刚崩完，AUTO-MAS 自己在恢复）
ROGUE_PS1 = r"E:\maa-alas\fz-maa\_autostart_l.ps1"

STATES = ("disabled", "waiting", "pending", "running", "done", "failed")
TRANSITIONS = {                      # 允许的状态迁移（非法迁移直接拒绝）
    "disabled": {"waiting"},
    "waiting": {"pending", "disabled"},
    # running→pending 只走**崩溃对账**那条路（任务被打断，history 里没有完成记录）
    "pending": {"running", "waiting", "failed"},
    "running": {"done", "failed", "pending"},
    "done": {"waiting"},
    "failed": {"pending", "disabled"},
}

DEFAULT_CONFIG = {
    "concurrency_limit": 11,          # 实测 adb 在线 10~13；等今晚峰值数据再定（不是拍脑袋的 9）
    "call_interval_sec": 5,
    "priority_weights": {"event": 1, "infrast": 3, "stamina": 5, "annihilation": 7},
    "wait_weight": 0.5,
    "retry": {"max": 3, "delay_min": 5},
    "screenshot": {"limit_gb": 40, "buffer_gb": 5},   # 主机 E: 实测只剩 ~48GB ⇒ 不是 100
    "periods": {"infrast_h": 12, "stamina_regen_h": 10},   # stamina 周期按"回满时长"估
}

DEFAULT_TASKS = [                    # done_when 从 data/daily_state/<uid>.json 读
    # ⚠️ event 默认 **disabled**：它的完成判据是"关卡推完"，没有目标配置前永远不满足
    #    ⇒ 若默认启用，它会把队列淹掉（dry-run 实测：16 个号里 event 全 pending）
    {"command": "event", "priority": 1, "window": [0, 24], "done_when": None, "enable": False},
    {"command": "infrast", "priority": 3, "window": [0, 24], "done_when": "infrast_done"},
    {"command": "stamina", "priority": 5, "window": [0, 24], "done_when": "stamina_cleared"},
    {"command": "annihilation", "priority": 7, "window": [0, 24], "done_when": "annihilation_done"},
]

RECENT_RUN_MIN = 60        # 该号最后一次落账在 N 分钟内 ⇒ 认为可能还在跑，不重复派发
SAMPLE_LOG = PROJ / "runtime" / "host_perf.log"     # 采样器日志（含 adb 在线数）


# ---------------------------------------------------------------- 基础件

def _load(p: pathlib.Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return default


def _save(p: pathlib.Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def config() -> dict:
    c = DEFAULT_CONFIG
    cur = _load(SCHED / "config.json", {})
    for k, v in cur.items():                                # 磁盘上的覆盖默认
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k] = {**c[k], **v}
        else:
            c[k] = v
    _save(SCHED / "config.json", c)
    return c


def gen_id(now: dt.datetime | None = None) -> str:
    """任务编号 YYYYMMDDHH-NNN；跨小时重置。"""
    now = now or dt.datetime.now()
    key = now.strftime("%Y%m%d%H")
    ctr = _load(SCHED / "id_counter.json", {})
    ctr[key] = int(ctr.get(key, 0)) + 1
    for old in sorted(ctr):                                 # 只留最近 24 个小时桶
        if old < (now - dt.timedelta(hours=24)).strftime("%Y%m%d%H"):
            ctr.pop(old, None)
    _save(SCHED / "id_counter.json", ctr)
    return "%s-%03d" % (key, ctr[key])


def transit(task: dict, to: str) -> None:
    """状态迁移（非法迁移抛错——宁可炸，也不要静默错状态）。"""
    cur = task.get("state", "waiting")
    if to not in TRANSITIONS.get(cur, set()):
        raise ValueError("非法状态迁移 %s → %s（%s）" % (cur, to, task.get("command")))
    task["state"] = to


def eff_priority(task: dict, now: dt.datetime | None = None) -> float:
    """有效优先级 = 基础 - 等待小时 × wait_weight。"""
    now = now or dt.datetime.now()
    since = task.get("queued_at") or task.get("last_run")
    wait_h = 0.0
    if since:
        try:
            wait_h = max(0.0, (now - dt.datetime.fromisoformat(since)).total_seconds() / 3600)
        except ValueError:
            wait_h = 0.0
    return float(task["priority"]) - wait_h * float(config()["wait_weight"])


# ---------------------------------------------------------------- 任务集

def tasks_path(uid: str) -> pathlib.Path:
    return SCHED / ("tasks_%s.json" % uid.replace("/", "_"))


def load_tasks(uid: str) -> dict:
    doc = _load(tasks_path(uid), None)
    if doc is None:
        doc = {"uid": uid, "tasks": [dict(t, state="waiting", enable=True, retry_count=0,
                                          next_run=None, last_run=None, last_result=None)
                                     for t in DEFAULT_TASKS]}
        _save(tasks_path(uid), doc)
    return doc


def in_window(task: dict, now: dt.datetime) -> bool:
    a, b = task.get("window") or [0, 24]
    return a <= now.hour < b


def load_now() -> dict | None:
    """最近一条采样 → {cpu, mem, adb, nx, vmm, maa, top}。

    复用 `host_perf._parse`（它被坑⑧的断言守着），不重复实现解析。
    """
    try:
        sys.path.insert(0, str(PROJ / "tools"))
        import host_perf                              # noqa: PLC0415
    except Exception:                                 # noqa: BLE001
        return None
    try:
        lines = SAMPLE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for ln in reversed(lines[-20:]):
        stamp, _, rest = ln.partition("|")
        v = host_perf._parse(stamp + "|" + rest)
        if v and v[2] is not None:                    # 需要带 adb 列的新格式
            keys = ("cpu", "mem", "adb", "nx", "vmm", "maa", "top")
            return dict(zip(keys, v))
    return None


def free_slots(limit: int, uptime_min: float | None = None,
               s: dict | None = None) -> tuple[int, str]:
    """空位 = 并发上限 − 真在跑数。**三道闸，任一不过 ⇒ 不派**。

    闸① `MAA = 0`：机器刚起 / AUTO-MAS 未在跑 ⇒ 让路（它自己会把启动队列跑起来）
    闸② 无采样：保守不派
    闸③ `uptime < 30min`：**刚崩完重启**，AUTO-MAS 正在爬坡，别叠加
        （闸①不够：重启后 MAA 会从 0 爬到满，中间几次采样 MAA=4、NxDevice=5
         会让公式算出"6 个空位"，正好在线恢复爬坡时又压一批上去）
    """
    s = s if s is not None else load_now()
    if not s:
        return 0, "无采样 ⇒ 保守不派发"
    if uptime_min is None:
        uptime_min = host_uptime_min()
    if uptime_min is not None and uptime_min < UPTIME_MIN_GATE:
        return 0, "开机 %.0f 分钟（< %d）⇒ 让路，不派发" % (uptime_min, UPTIME_MIN_GATE)
    nx, adb, maa = s["nx"], s["adb"], s["maa"]
    if maa == 0:
        return 0, "MAA=0（机器刚起 / AUTO-MAS 未在跑）⇒ 让路，不派发"
    return (max(0, limit - max(nx, adb)),
            "NxDevice=%d adb=%d MAA=%d CPU=%d%% uptime=%s"
            % (nx, adb, maa, s["cpu"],
               ("%.0fmin" % uptime_min) if uptime_min is not None else "?"))


# ---------------------------------------------------------------- 主机只读探针

def _host() -> str:
    try:
        c = configparser.ConfigParser()
        c.read(CONF_INI, encoding="utf-8")
        return c["host"]["host"]
    except Exception:                                       # noqa: BLE001
        return "100.79.173.69"


def ssh_ps(script: str, timeout: int = 90) -> str:
    """在主机上跑一段 PowerShell（只读探针用），返回 stdout。"""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    exe = shutil.which("ssh") or r"C:\Windows\System32\OpenSSH\ssh.exe"
    r = subprocess.run([exe, "-i", SSH_KEY, "-o", "BatchMode=yes",
                        "-o", "ConnectTimeout=15", "Administrator@" + _host(),
                        "powershell -NoProfile -EncodedCommand " + b64],
                       capture_output=True, timeout=timeout)
    out = (r.stdout or b"").decode("utf-8", "replace")
    return "\n".join(ln for ln in out.splitlines() if not ln.startswith("<Objs"))


def host_uptime_min(ttl_sec: int = 300) -> float | None:
    """主机开机时长（分钟），带 5 分钟缓存（别每次调度都 ssh）。"""
    try:
        c = json.loads(UPTIME_CACHE.read_text(encoding="utf-8"))
        if dt.datetime.now().timestamp() - c.get("t", 0) < ttl_sec:
            return c.get("v")
    except Exception:                                       # noqa: BLE001
        pass
    try:
        txt = ssh_ps("(New-TimeSpan -Start (Get-CimInstance Win32_OperatingSystem)"
                     ".LastBootUpTime).TotalMinutes")
        v = float(re.findall(r"[\d.]+", txt)[0])
    except Exception:                                       # noqa: BLE001
        return None
    try:
        UPTIME_CACHE.parent.mkdir(parents=True, exist_ok=True)
        UPTIME_CACHE.write_text(json.dumps({"t": dt.datetime.now().timestamp(), "v": v}),
                                encoding="utf-8")
    except OSError:
        pass
    return v


# ---------------------------------------------------------------- 肉鸽打断（dry-run）

def rogue_map() -> dict:
    """`_autostart_l.ps1` 里的 `$names` / `$emus` → {l-N: 实例号}。**读脚本，不写死**。"""
    txt = ssh_ps("Get-Content -Encoding UTF8 '%s'" % ROGUE_PS1)
    n = re.search(r"\$names\s*=\s*@\(([^)]*)\)", txt)
    e = re.search(r"\$emus\s*=\s*@\(([^)]*)\)", txt)
    if not (n and e):
        return {}
    ns = [x.strip().strip("'\"") for x in n.group(1).split(",") if x.strip()]
    es = [x.strip() for x in e.group(1).split(",") if x.strip()]
    return dict(zip(ns, es))


def rogue_exec(name: str, pid: str, emu: str, live: bool = False,
               uptime_min: float | None = -1) -> list[str]:
    """执行肉鸽打断（三条命令，用户 2026-09-18 确认）。**--live 才真动。**

    ⚠️ **闸③ 必须 fail-closed**：`uptime` 探测失败（None）也拒绝，不能放行。
    踩过（2026-09-18 实测事故）：写成 `if up is not None and up < 30` ⇒ 探测失败时
    **静默放行**，于是 `--live` 在开机 19 分钟（爬坡期）真的杀了 l-1 的 MAA、关了实例 25。
    **安全闸在"不知道"的时候必须拒绝，不是放行。**
    """
    if not live:
        return ["[DRY-RUN] 未执行（加 --live 才真关）"]
    up = host_uptime_min(ttl_sec=0) if uptime_min == -1 else uptime_min
    if up is None:
        return ["[拒绝] 取不到开机时长 ⇒ 不知道是否在爬坡 ⇒ fail-closed 不执行"]
    if up < UPTIME_MIN_GATE:
        return ["[拒绝] 开机 %.0f 分钟（< %d）⇒ 刚崩完/正在爬坡，不打断" % (up, UPTIME_MIN_GATE)]
    steps = []
    # ① 按 PID 杀（fz-maa 的 MAA）
    steps.append("① " + ssh_ps("Stop-Process -Id %s -Force; 'killed'" % pid).strip())
    # ② 关它那台模拟器
    steps.append("② " + ssh_ps("& 'E:\\MuMu Player 12\\nx_main\\MuMuManager.exe' control -v %s shutdown"
                               " | Out-String" % emu).strip()[:80])
    # ③ 自查（关完必须确认）
    out = ssh_ps("Start-Sleep -Seconds 20; $j = (& 'E:\\MuMu Player 12\\nx_main\\MuMuManager.exe' "
                 "info -v %s | Out-String); if ($j -match '\"is_process_started\": true') "
                 "{ 'STILL-RUNNING' } else { 'CONFIRMED-STOPPED' }" % emu).strip()
    steps.append("③ 自查 %s: %s" % (name, out.splitlines()[-1] if out else "?"))
    return steps


def rogue_resume(live: bool = False) -> list[str]:
    """队列空了才恢复肉鸽。**必须经登录会话**（触发计划任务），不能直接 SSH 调 ps1。

    为什么（两条都是实测）：
      ① `-File` 不带 `-ExecutionPolicy Bypass` 会被策略拦掉，而**错误只进 stderr**
         ⇒ 只看 stdout 会以为"跑过了"（实际脚本一行没执行、它自己的日志也没新增）。
      ② 即便脚本真跑起来，**SSH 是 session 0** —— MuMu 是 GUI 程序，在 session 0
         启模拟器是无效的（`autostart done` + `exit=0`，但一台都没起来）。
     ⇒ 正解 = `schtasks /run /tn fz-maa-l-autostart`（它就是登录时那条任务，
       在交互会话里执行；07:05 崩溃重启后那次复跑就是这么成功的）。
    """
    if not live:
        return ["[DRY-RUN] 未执行（加 --live 才真恢复）"]
    out = ssh_ps("schtasks /run /tn 'fz-maa-l-autostart' | Out-String", timeout=120).strip()
    return ["触发登录任务: " + out[:160],
            "（真起来要 ~5 分钟：起 5 台模拟器 → 等安卓启动 → 起 MAA；之后用 --rogue 自查）"]


def rogue_plan() -> list[str]:
    """列出 fz-maa 的 MAA（路径含 maa-alas），按运行时长排序 ⇒ 给出"关最久那台"的命令。"""
    ps = ("$p = Get-CimInstance Win32_Process -Filter \"Name='MAA.exe'\" | "
          "Where-Object { $_.ExecutablePath -like '*maa-alas*' }; "
          "$p | Sort-Object CreationDate | ForEach-Object { "
          "$_.ProcessId.ToString() + '|' + $_.ExecutablePath + '|' + "
          "$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') }")
    txt = ssh_ps(ps)
    rows = [ln.split("|") for ln in txt.splitlines() if ln.count("|") == 2]
    out = []
    m = rogue_map()
    if not rows:
        return ["（没有在跑的 fz-maa MAA ⇒ 无需打断）"]
    now = dt.datetime.now()
    for pid, path, started in rows[:3]:
        name = path.replace("/", "\\").split("\\")[-2]           # ...\l\  <名>  \MAA.exe
        emu = m.get(name, "?")
        try:
            dur = now - dt.datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
            dur_s = "已运行 %dh%02dm" % (dur.seconds // 3600 + dur.days * 24, dur.seconds % 3600 // 60)
        except ValueError:
            dur_s = "?"
        out.append("  %-8s PID %-6s %s  实例号=%s" % (name, pid, dur_s, emu))
    first_name = rows[0][1].replace("/", "\\").split("\\")[-2]
    first_emu = m.get(first_name, "?")
    out += [
        "",
        "[DRY-RUN] 准备关肉鸽（最久那台 = %s）：" % first_name,
        "  命令1（按 PID 杀，路径已限定）: Stop-Process -Id %s -Force" % rows[0][0],
        "  命令2（关它那台模拟器）      : MuMuManager control -v %s shutdown" % first_emu,
        "  自查（必须）                : MuMuManager info -v %s 应报 is_process_started=false" % first_emu,
        "",
        "[DRY-RUN] 恢复（队列空了才做）:",
        "  powershell -File %s" % ROGUE_PS1,
        "",
        "映射来源: _autostart_l.ps1 的 $names/$emus（不是写死的）",
    ]
    return out


def reconcile(now: dt.datetime | None = None, dry: bool = True) -> list[str]:
    """崩溃对账：state==running 的任务 → 查 history 定 done / pending。

    MAA 任务没有断点续跑（基建换一半 = 没换）⇒ 对账只有两个结果。
    这是"调度器比 MAS 内置调度强"的核心：**MAS 崩了就忘，这里记得**。
    """
    now = now or dt.datetime.now()
    try:
        day = fetch_day_records(now.date().isoformat())
    except Exception as e:                                  # noqa: BLE001
        return ["[跳过] 取 history 失败：%s" % str(e)[:60]]
    acted = []
    for f in sorted(SCHED.glob("tasks_*.json")):
        doc = _load(f, None)
        if not doc:
            continue
        uid = doc.get("uid")
        idx = ((day.get(uid) or {}).get("index")) or []
        for t in doc["tasks"]:
            if t.get("state") != "running":
                continue
            started = t.get("started_at") or t.get("last_run") or ""
            sdt = _as_dt(started)
            done = False
            for i in idx:
                rdt = _as_dt(i.get("date"))
                if rdt and (sdt is None or rdt >= sdt):
                    done = True
                    break
            to = "done" if done else "pending"
            if not dry:
                transit(t, to)
                t["last_result"] = "reconciled_done" if done else "reconciled_interrupted"
                if not done:
                    t["queued_at"] = now.isoformat(timespec="seconds")
            acted.append("%s/%s → %s（history %s）" % (uid[:12], t["command"], to,
                                                      "有完成记录" if done else "无记录"))
        if acted and not dry:
            _save(f, doc)
    return acted


def _as_dt(s) -> dt.datetime | None:
    """时间串 → datetime。**两种分隔符都要认**（`2026-09-18 05:00:00` 与 `...T05:00:00`）。

    踩过：直接拿字符串比大小 ⇒ `' '`(0x20) < `'T'`(0x54) ⇒ 恒 False ⇒
    崩溃对账把"其实跑完了"的任务全判成 interrupted，**重复执行**。
    """
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace(" ", "T")[:19])
    except ValueError:
        return None


def fetch_day_records(ds: str) -> dict:
    """今天的 history（{账号: record}）—— 对账要用最新，不走缓存。"""
    req = urllib.request.Request(
        API + "/api/history/search",
        data=json.dumps({"mode": "DAILY", "start_date": ds, "end_date": ds}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return (json.loads(r.read().decode("utf-8", "replace")).get("data") or {}).get(ds) or {}


def possibly_running(uid: str, now: dt.datetime) -> bool:
    """该号最后一次落账在 RECENT_RUN_MIN 分钟内 ⇒ 很可能还在跑，不重复派发。

    （AUTO-MAS 没有"运行时状态"接口，且记录是**跑完才落账** ⇒ 用"最近落账时间"
      当保守判据：宁可漏掉一轮，也不重复烧理智。）
    """
    st = _load(DAILY / ("%s.json" % uid.replace("/", "_")), {})
    last = st.get("last_run")
    if not last:
        return False
    try:
        t = dt.datetime.combine(now.date(), dt.time.fromisoformat(last))
    except ValueError:
        return False
    return (now - t).total_seconds() < RECENT_RUN_MIN * 60


def due(task: dict, now: dt.datetime) -> bool:
    nr = task.get("next_run")
    if not nr:
        return True
    try:
        return dt.datetime.fromisoformat(nr) <= now
    except ValueError:
        # ⚠️ fail-closed：next_run 写坏（解析失败）时**不放行** —— 放行 = 提前派发。
        # （全局纪律：安全闸在"不知道"时必须拒绝，见 docs/SCHEDULER.md §7）
        return False


def scan(uid: str, now: dt.datetime | None = None) -> dict:
    """按 daily_state 推进一个账号的任务状态。"""
    now = now or dt.datetime.now()
    st = _load(DAILY / ("%s.json" % uid.replace("/", "_")), {})
    doc = load_tasks(uid)
    for t in doc["tasks"]:
        # 没有完成判据的任务不许排队（否则永远 pending，把队列淹掉）
        if not t.get("done_when"):
            t["state"] = "disabled"
            continue
        if not t.get("enable"):
            if t["state"] != "disabled":
                t["state"] = "disabled"
            continue
        if t["state"] == "disabled":
            transit(t, "waiting")
        if t["state"] == "done":                            # 完成 → 重算下次
            transit(t, "waiting")
        if t["state"] == "waiting":
            if not due(t, now):
                continue
            if not in_window(t, now):
                continue
            transit(t, "pending")
            t["queued_at"] = t.get("queued_at") or now.isoformat(timespec="seconds")
        if t["state"] == "pending":
            key = t["done_when"]
            if st.get(key) is True:                          # 状态库说已经做完了
                done_at = now
                if st.get("last_run"):                       # 更忠实：用**完成时刻**算周期
                    try:
                        done_at = dt.datetime.combine(
                            now.date(), dt.time.fromisoformat(st["last_run"]))
                    except ValueError:
                        pass
                transit(t, "running")                        # pending→running→done 快照路径
                transit(t, "done")
                t["last_result"] = "already"
                t["last_run"] = done_at.isoformat(timespec="seconds")
                t["next_run"] = next_run_for(t, done_at)
    _save(tasks_path(uid), doc)
    return doc


def next_run_for(task: dict, done_at: dt.datetime) -> str:
    """next_run = **完成时间** + 周期（+ 首次抖动，避免所有号同一分钟涌上来）。"""
    cfg = config()
    cmd = task["command"]
    if cmd == "infrast":
        delta = dt.timedelta(hours=cfg["periods"]["infrast_h"])
    elif cmd == "stamina":
        delta = dt.timedelta(hours=cfg["periods"]["stamina_regen_h"])
    elif cmd == "annihilation":
        days = (7 - done_at.weekday()) % 7 or 7                # 下周一
        delta = dt.timedelta(days=days)
        done_at = done_at.replace(hour=4, minute=0, second=0, microsecond=0)
    else:
        delta = dt.timedelta(hours=24)
    base = done_at + delta
    if not task.get("jittered"):
        base += dt.timedelta(minutes=random.randint(-30, 30))
        task["jittered"] = True
    return base.isoformat(timespec="seconds")


def call_queue(uids: list[str], now: dt.datetime | None = None) -> list[dict]:
    """把所有 pending 任务按有效优先级排成一队（越靠前越先叫号）。"""
    now = now or dt.datetime.now()
    q = []
    for uid in uids:
        doc = load_tasks(uid)
        for t in doc["tasks"]:
            if t["state"] == "pending":
                q.append({"uid": uid, "command": t["command"],
                          "eff": eff_priority(t, now), "base": t["priority"],
                          "wait_since": t.get("queued_at")})
    q.sort(key=lambda x: (x["eff"], x["base"]))
    return q


# ---------------------------------------------------------------- 执行

def script_map(use_cache: bool = True) -> dict:
    """账号名 → 脚本ID（派发要用）。缓存到 data/schedule/script_map.json。"""
    p = SCHED / "script_map.json"
    if use_cache and p.exists():
        m = _load(p, {})
        if m:
            return m
    def api(path, body=None):
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    API + path, data=json.dumps(body or {}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
            except Exception as e:                          # noqa: BLE001
                last = e
                import time as _t
                _t.sleep(2 + attempt * 3)
        raise RuntimeError("API 连续 3 次失败: %s" % last)
    m = {}
    sc = api("/api/scripts/get", {"scriptId": None})
    for it in (sc.get("index") or []):
        sid = it["uid"]
        u = api("/api/scripts/user/get", {"scriptId": sid, "userId": None})
        for uu, uv in (u.get("data") or {}).items():
            if uu == "instances":
                continue
            nm = (uv.get("Info") or {}).get("Name")
            if nm:
                m[nm] = sid
    _save(p, m)
    return m


def dispatch(uid: str, command: str = "", dry: bool = True) -> str:
    """派发一个任务。**派发前先把 running 写盘**（纪律 #6）——
    否则崩在半路时磁盘上还写着 pending，对账无从下手。"""
    sm = script_map()
    sid = sm.get(uid)
    if not sid:
        return "无脚本映射（%s）" % uid
    now = dt.datetime.now()
    tid = gen_id(now)
    doc = load_tasks(uid)
    for t in doc["tasks"]:
        if t["command"] == command and t["state"] == "pending":
            if not dry:                                     # 先写盘，再发请求
                transit(t, "running")
                t["started_at"] = now.isoformat(timespec="seconds")
                t["task_id"] = tid
                _save(tasks_path(uid), doc)
            break
    if dry:
        return "DRY 取号 %s → POST /api/dispatch/start {taskId:%s, mode:AutoProxy}" % (tid, sid[:8])
    req = urllib.request.Request(
        API + "/api/dispatch/start",
        data=json.dumps({"taskId": sid, "mode": "AutoProxy"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace")).get("taskId", "?")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--rogue", action="store_true", help="肉鸽打断计划（默认 dry-run）")
    ap.add_argument("--rogue-resume", action="store_true", help="恢复肉鸽（跑它的自启脚本）")
    ap.add_argument("--live", action="store_true", help="配合 --rogue：真执行（未实现，等确认）")
    ap.add_argument("--uptime", action="store_true", help="只打印主机开机时长")
    ap.add_argument("--exec", action="store_true", help="真派发（危险；默认 dry-run）")
    ap.add_argument("--interval", type=int, default=0, help="常驻轮询秒数（0=跑一轮就退出）")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())

    if a.uptime:
        print("主机开机时长: %.1f 分钟" % (host_uptime_min(ttl_sec=0) or -1))
        return

    if a.rogue_resume:
        print("== 恢复肉鸽 ==")
        up = host_uptime_min(ttl_sec=0)
        print("开机时长: %s 分钟（闸③ 阈值 %d）" % ("?" if up is None else "%.0f" % up, UPTIME_MIN_GATE))
        for s in rogue_resume(live=True):
            print("  " + s)
        print("（等 60 秒后自查）")
        time.sleep(60)
        for line in rogue_plan()[:6]:
            print(line)
        return

    if a.rogue:
        print("== 肉鸽打断计划（%s）==" % ("LIVE" if a.live else "DRY-RUN"))
        lines = rogue_plan()
        for line in lines:
            print(line)
        if a.live:
            rows = [ln for ln in lines if ln.strip().startswith("l-")]
            if not rows:
                print("\n（没有可打断的目标）")
                return
            first = rows[0]
            pid = first.split("PID")[1].split()[0]
            name = first.strip().split()[0]
            emu = first.split("实例号=")[1].split()[0]
            print("\n== 执行 ==")
            for s in rogue_exec(name, pid, emu, live=True):
                print("  " + s)
        else:
            print("\n（加 --live 才真执行；--live 也受闸③ 约束：刚崩完不打断）")
        return

    cfg = config()
    now = dt.datetime.now()
    uids = sorted(p.stem for p in DAILY.glob("*.json"))

    # ④ 崩溃对账（先做）—— 上次崩在半路时，running 的任务在这里被纠正
    rec = reconcile(now, dry=not a.exec)
    if rec:
        print("== 崩溃对账 ==")
        for line in rec:
            print("  " + line)

    for uid in uids:
        scan(uid, now)
    q = call_queue(uids, now)

    # 防重复：最近落账过的不叫（可能还在跑）
    skipped = [x for x in q if possibly_running(x["uid"], now)]
    q = [x for x in q if not possibly_running(x["uid"], now)]

    limit = cfg["concurrency_limit"]
    slots, why = free_slots(limit)                    # NxDevice 主口径 + 活性检查
    print("== 调度一轮 %s ==" % now.strftime("%Y-%m-%d %H:%M:%S"))
    print("状态库账号 %d 个 | 待叫号 %d 个（跳过'最近在跑' %d）| 并发上限 %d | %s | 空位 %d"
          % (len(uids), len(q), len(skipped), limit, why, slots))
    for i, item in enumerate(q[:slots], 1):
        out = dispatch(item["uid"], item["command"], dry=not a.exec)
        print("  叫号 %2d  %-14s 有效优先级=%.2f（基础%s，等到 %s）→ %s"
              % (i, item["uid"][:14], item["eff"], item["base"],
                 item["wait_since"] or "-", out))
    if len(q) > slots:
        print("  排队中 %d 个（等释放槽位）" % (len(q) - slots))
    if skipped:
        print("  跳过示例：%s" % "、".join(x["uid"][:10] for x in skipped[:5]))
    if a.exec:
        print("⚠️ 已真派发（--exec）")


def selftest() -> int:
    """纯逻辑自检：状态机 / 优先级 / 编号 / 漂移。

    ⚠️ 自检**不能写真实状态**：编号计数器要指向临时目录，否则第二次跑就
      必然失败（踩过：第一次 PASS、第二次 FAIL，因为计数器被自己写脏了）。
    """
    global SCHED
    import tempfile
    SCHED = pathlib.Path(tempfile.mkdtemp(prefix="sched_selftest_"))
    bad = 0

    def chk(name, cond, ev=""):
        nonlocal bad
        print("%s  %s%s" % ("PASS" if cond else "FAIL", name, ("   ← " + ev) if not cond else ""))
        if not cond:
            bad += 1

    t = {"command": "stamina", "priority": 5, "state": "waiting"}
    transit(t, "pending")
    transit(t, "running")
    transit(t, "done")
    okpath = t["state"] == "done"
    try:
        transit(t, "running")            # done→running 非法
        okpath = False
    except ValueError:
        pass
    chk("六状态机：合法路径通 + 非法迁移抛错", okpath, "state=%s" % t["state"])

    now = dt.datetime(2026, 9, 18, 12, 0)
    t1 = {"command": "stamina", "priority": 5, "queued_at": (now - dt.timedelta(hours=4)).isoformat()}
    t2 = {"command": "stamina", "priority": 5, "queued_at": now.isoformat()}
    chk("等待越久越靠前（防饿死）", eff_priority(t1, now) < eff_priority(t2, now),
        "%.2f vs %.2f" % (eff_priority(t1, now), eff_priority(t2, now)))

    i1 = gen_id(dt.datetime(2026, 9, 18, 5, 0))
    i2 = gen_id(dt.datetime(2026, 9, 18, 5, 0))
    i3 = gen_id(dt.datetime(2026, 9, 18, 6, 0))
    chk("编号 YYYYMMDDHH-NNN + 跨小时重置",
        i1.startswith("2026091805-") and int(i2.split("-")[1]) == int(i1.split("-")[1]) + 1
        and i3.startswith("2026091806-001"), "%s %s %s" % (i1, i2, i3))

    task = {"command": "stamina", "priority": 5, "jittered": True}
    a1 = next_run_for(dict(task), dt.datetime(2026, 9, 18, 5, 30))
    chk("next_run = 完成时间 + 周期（不漂移）", a1.startswith("2026-09-18T15:30"), a1)

    # 空位口径 + 两道闸（用临时采样日志；日志是追加的 ⇒ 最新一行在最后）
    global SAMPLE_LOG
    keep = SAMPLE_LOG
    SAMPLE_LOG = SCHED / "_sample.log"

    def _sample(lines: list[str]) -> None:
        SAMPLE_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _sample(["2026-09-18 07:06:03 | 4|49319|2|5|5|0|MuMuNxMain",       # 旧：刚重启
             "2026-09-18 07:03:04 | 93|15213|11|13|13|11|MuMuVMMHeadless"])  # 新：满载
    s1, _ = free_slots(13)
    chk("空位：满载（13 台在跑 / 上限 13）⇒ 0", s1 == 0, "slots=%d" % s1)

    _sample(["2026-09-18 07:06:03 | 4|49319|2|5|5|0|MuMuNxMain"])       # 机器刚起：MAA=0
    s2, w2 = free_slots(11)
    chk("空位：MAA=0（刚重启）⇒ 让路、不派发", s2 == 0 and "让路" in w2, "%d %s" % (s2, w2))

    _sample(["2026-09-18 07:10:00 | 30|40000|2|2|2|1|MuMuNxMain"])      # 2 台在跑
    s3, _ = free_slots(11, uptime_min=120)                             # 注入正常开机时长
    chk("空位：2 台在跑 / 上限 11 / 开机 120min ⇒ 9", s3 == 9, "slots=%d" % s3)
    SAMPLE_LOG = keep

    # 崩溃对账的时间比较：两种分隔符必须等价（踩过：字符串比大小 ⇒ 恒 False ⇒ 重复执行）
    a = _as_dt("2026-09-18 05:00:00")
    b = _as_dt("2026-09-18T05:00:00")
    c = _as_dt("2026-09-18T23:59:00")
    chk("对账时间比较：空格/T 两种写法等价且能比大小",
        a == b and a is not None and c is not None and b >= a and not (c <= a),
        "a=%s b=%s c=%s" % (a, b, c))

    # 闸③ uptime 门槛（注入值，不依赖网络）
    ok_s = {"cpu": 30, "mem": 40000, "adb": 2, "nx": 2, "vmm": 2, "maa": 1, "top": "MuMuNxMain"}
    s4, w4 = free_slots(11, uptime_min=5, s=dict(ok_s))
    chk("闸③ 开机 5 分钟（刚崩完）⇒ 让路", s4 == 0 and "让路" in w4, "%d %s" % (s4, w4))
    s5, _ = free_slots(11, uptime_min=120, s=dict(ok_s))
    chk("闸③ 开机 120 分钟 ⇒ 正常按负载算（11-2=9）", s5 == 9, "slots=%d" % s5)

    # 安全闸 fail-closed：探测不到开机时长 ⇒ 必须拒绝（踩过：静默放行 ⇒ 真杀了生产进程）
    r1 = rogue_exec("l-1", "0", "25", live=True, uptime_min=None)
    r2 = rogue_exec("l-1", "0", "25", live=True, uptime_min=5)
    r3 = rogue_exec("l-1", "0", "25", live=False, uptime_min=None)
    chk("安全闸 fail-closed：取不到 uptime ⇒ 拒绝",
        len(r1) == 1 and "拒绝" in r1[0] and "fail-closed" in r1[0], str(r1))
    chk("安全闸：开机 5 分钟 ⇒ 拒绝", len(r2) == 1 and "拒绝" in r2[0], str(r2))
    chk("安全闸：dry-run 不发请求", len(r3) == 1 and "DRY-RUN" in r3[0], str(r3))

    print()
    print("✅ 调度器逻辑自检通过" if not bad else "❌ %d 项失败" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    main()
