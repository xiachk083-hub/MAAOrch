#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_identity_trace.py —— 「每轮身份留痕」：多号账号每轮抓大厅/标题帧 + 核对 UID（只读）。

把「串号」变成可回归检测。三个子命令（全部只读；不写远端、不发车、不改配置）：

  watch  盯 MAA 目录的 gui.log（+ AUTO-MAS app.log）：
         - 命中 `账号切换 -->> <掩码>` 记一行（时间 + 掩码 + 账号名*）
         - 每轮（StartUp 链）在「完成前 / 完成后」各截帧存证（adb screencap，只读）
  ocr    对已抓帧跑 RapidOCR，抽 `ID:<UID>` / 玩家掩码 → ocr_index.json
  report 离线解析 gui.log + app.log（+ 帧 OCR），输出「每个号每轮」期望 vs 观测对照表；
         任何一轮掩码错配 = 红色

* 账号名 = 掩码→配置用户名的映射（--targets 指定脚本 + API/--user-map 解析；拿不到则 '?'）。

用法（示例）:
  python mas_identity_trace.py watch  --duration-min 240            # 盯今晚的自然班次
  python mas_identity_trace.py ocr    --outdir runtime/identity_trace
  python mas_identity_trace.py report --since 2026-09-27 --until 2026-09-28 \
         --out runtime/identity_trace/report_20260927.md

运行时注意：
- ocr 子命令需要 rapidocr_onnxruntime（本机 = Python312：
  C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe）。
- watch/report 用任意 python3 (>=3.9) 均可；report 默认从远端现拉日志（ssh）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

# ---------------- 常量和基础 ----------------

TZ = dt.timezone(dt.timedelta(hours=8))
SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
MAA_ROOT = r"E:\MAA-Multi"
APP_LOG = r"E:\AUTO-MAS\debug\app.log"

# 默认目标：本任务范围的两个多号脚本
DEFAULT_TARGETS = "官-1-1-7=MAA_6:13;官-2-4-47=MAA_2:14"

RE_STARTUP = re.compile(r"Start\s+Task\s+Chain:\s*StartUp", re.I)
RE_STARTUP_DONE = re.compile(r"Completed\s+Task\s+Chain:\s*StartUp", re.I)
RE_SWITCH = re.compile(r"账号切换\s*-+>>\s*(\S+)")
RE_LINE_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
RE_APP_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
RE_APP_ROUND_START = re.compile(r"开始代理用户:\s*([0-9a-fA-F-]{36})")
RE_APP_ROUND_DONE = re.compile(r"用户 ([0-9a-fA-F-]{36}) 的自动代理任务已完成")
RE_APP_ROUND_FAIL = re.compile(r"用户: ([0-9a-fA-F-]{36}) - 代理任务异常: (.+)")
RE_APP_ATTEMPT = re.compile(r"用户 (\S+) - 模式: \S+ - 尝试次数: (\d+)/(\d+)")
RE_APP_RESULT = re.compile(r"MAA 任务结果: ([^,]+), 日志锁已释放")
RE_APP_TASK_START = re.compile(r"任务开始: ([0-9a-fA-F-]{36})")
RE_APP_TASK_END = re.compile(r"任务结束: ([0-9a-fA-F-]{36})")
RE_APP_LOCK = re.compile(r"已(锁定|解锁)脚本配置 ([0-9a-fA-F-]{36})")
RE_ID = re.compile(r"ID\s*[:：]\s*(\d{6,12})")
RE_MASK = re.compile(r"\b(\d{3})\*{4}(\d{4})\b")

ADB_CANDIDATES = [r"E:\MuMu Player 12\shell\adb.exe", r"E:\MuMu Player 12\nx_main\adb.exe"]


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def ts_str(t: dt.datetime | None = None) -> str:
    return (t or now()).strftime("%Y-%m-%d %H:%M:%S")


def line_ts(line: str) -> dt.datetime | None:
    m = RE_LINE_TS.match(line) or RE_APP_TS.match(line)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except Exception:
        return None


def mask_of(acc_id: str) -> str:
    s = str(acc_id or "")
    if len(s) == 11 and s.isdigit():
        return s[:3] + "****" + s[7:]
    return s or "?"


# ---------------- 远端访问（ssh；模板与其他 MAAOrch 工具一致） ----------------

def _ssh_base() -> list:
    return ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", SSH_HOST]


def remote_ps(script: str, timeout: int = 120) -> str:
    prefix = ("$ErrorActionPreference='Continue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    b64 = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    r = subprocess.run(_ssh_base() + ["powershell -NoProfile -EncodedCommand " + b64],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def remote_bytes(inner_cmd: str, timeout: int = 120):
    r = subprocess.run(_ssh_base() + ['cmd /S /c "' + inner_cmd + '"'],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.returncode, r.stdout


def tail_remote(path: str, n: int = 500, timeout: int = 60) -> list:
    return remote_ps("Get-Content -Encoding UTF8 -Tail %d '%s'" % (n, path), timeout=timeout).splitlines()


# 远端预过滤锚点（只保留解析器真正消费的行；无标点、超集安全）
# 2026-09-28 t_6cfe7865：远机负载下 20MB 全量 pull 曾超 420s 预算 → 先过滤再拉。
GUI_PULL_FILTERS = ["Task Chain", "->>"]      # RE_STARTUP / RE_STARTUP_DONE / RE_SWITCH
APP_PULL_FILTERS = ["开始代理用户", "自动代理任务已完成", "代理任务异常", "尝试次数",
                    "MAA 任务结果", "任务开始", "任务结束", "脚本配置"]  # RE_APP_* 锚点


def pull_remote_text(path: str, timeout: int = 300, filters: "list | None" = None) -> str:
    """整份文本拉回（type 字节直通）。

    filters 非空时改走远端 PS 预过滤（UTF8 ReadLines + 字面量并集正则），只回所需行
    （~1-3% 体量）；解析器本就只认这些行，语义不变。
    """
    if filters:
        toks = ", ".join("'" + str(f).replace("'", "''") + "'" for f in filters)
        # 日志正被 MAA/AUTO-MAS 写入 → 必须 FileShare::ReadWrite 共享读；
        # [IO.File]::ReadLines 的默认共享与写入端冲突（异常会被 $EAP='Continue' 吞成空输出）。
        script = ("$rx=(@(" + toks + ") | ForEach-Object {[regex]::Escape($_)}) -join '|'; "
                  "$p='" + str(path).replace("'", "''") + "'; "
                  "if (Test-Path $p) { "
                  "$fs=[IO.File]::Open($p,[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,"
                  "[System.IO.FileShare]::ReadWrite); "
                  "$sr=New-Object System.IO.StreamReader($fs,[System.Text.Encoding]::UTF8); "
                  "try { while(($ln=$sr.ReadLine()) -ne $null) { if ($ln -match $rx) { Write-Output $ln } } } "
                  "finally { $sr.Close(); $fs.Close() } }")
        return remote_ps(script, timeout=timeout)
    r = subprocess.run(_ssh_base() + ['cmd /S /c "type %s"' % path],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def discover_emulator(vmindex: int, timeout: int = 150) -> dict:
    ps = r'''
$r=[ordered]@{}
$vmm=@(Get-CimInstance Win32_Process -Filter "Name='MuMuVMMHeadless.exe'" | Where-Object { $_.CommandLine -match 'MuMuPlayer-12\.0-{IDX}(\D|$)' })
$r.pids=@($vmm | ForEach-Object { $_.ProcessId })
$ports=@()
foreach ($p in $vmm) { $ports += @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $p.ProcessId } | ForEach-Object { $_.LocalPort }) }
$r.ports=@($ports | Sort-Object -Unique)
$cand=@('E:\MuMu Player 12\shell\adb.exe','E:\MuMu Player 12\nx_main\adb.exe')
$adb=$cand | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $adb) { $adb=(Get-ChildItem 'E:\MuMu Player 12' -Recurse -Filter adb.exe -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }
$r.adbpath=$adb
if ($adb) { $r.devices0=(& $adb devices 2>&1 | Out-String).Trim() }
$mm='E:\MuMu Player 12\shell\MuMuManager.exe'
if (Test-Path $mm) { $r.mmout=(& $mm adb --vmindex {IDX} connect 2>&1 | Out-String).Trim() }
if ($adb) {
  $rr=[ordered]@{}
  foreach ($port in $r.ports) { $rr["$port"]=(& $adb connect "127.0.0.1:$port" 2>&1 | Out-String).Trim() }
  $r.conn=$rr
  $r.devices=(& $adb devices 2>&1 | Out-String).Trim()
}
$r | ConvertTo-Json -Depth 4
'''.replace("{IDX}", str(vmindex))
    txt = remote_ps(ps, timeout=timeout)
    try:
        return json.loads(txt)
    except Exception:
        return {"_parse_error": True, "_raw": txt[:2000]}


def _dev_lines(dev_text: str) -> dict:
    out = {}
    for ln in (dev_text or "").splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[0] != "List":
            out[parts[0]] = parts[1]
    return out


def pick_serial(d: dict):
    ports = [str(p) for p in (d.get("ports") or [])]
    dev = _dev_lines(d.get("devices") or "")
    dev0 = _dev_lines(d.get("devices0") or "")
    for p in ports:
        s = "127.0.0.1:" + p
        if dev.get(s) == "device":
            return s
    for m in re.findall(r"127\.0\.0\.1:(\d{4,5})", str(d.get("mmout") or "")):
        s = "127.0.0.1:" + m
        if dev.get(s) == "device":
            return s
    new = [s for s in dev if s not in dev0]
    if len(new) == 1 and dev.get(new[0]) == "device":
        return new[0]
    for p in ports:
        if ("127.0.0.1:" + p) in dev:
            return "127.0.0.1:" + p
    if ports:
        return "127.0.0.1:" + sorted(ports, key=lambda x: (len(x), x), reverse=True)[0]
    return None


class EmuCtl:
    """一个实例的 adb 句柄（懒发现）。"""

    def __init__(self, vmindex: int):
        self.vmindex = vmindex
        self.adb = None
        self.serial = None

    def ensure(self, log=print) -> bool:
        try:
            d = discover_emulator(self.vmindex)
            if d.get("adbpath"):
                self.adb = d["adbpath"]
            s = pick_serial(d)
            if s:
                self.serial = s
            if self.adb and self.serial:
                dev = remote_ps("& '%s' devices" % self.adb, timeout=60)
                if not re.search(re.escape(self.serial) + r"\s+device", dev):
                    remote_ps("& '%s' connect %s" % (self.adb, self.serial), timeout=60)
                    dev = remote_ps("& '%s' devices" % self.adb, timeout=60)
                return bool(re.search(re.escape(self.serial) + r"\s+device", dev))
        except Exception as e:  # noqa: BLE001
            log("  [emu%d] discover err %r" % (self.vmindex, e))
        return False

    def shot(self, timeout: int = 90) -> bytes:
        inner = '"%s" -s %s exec-out screencap -p' % (self.adb, self.serial)
        _rc, data = remote_bytes(inner, timeout=timeout)
        return data


# ---------------- 目标解析 / 用户表 ----------------

def parse_targets(spec: str) -> list:
    """'官-1-1-7=MAA_6:13;官-2-4-47=MAA_2:14' -> [{script, dir, idx}]"""
    out = []
    for part in re.split(r"[;；]", spec.strip()):
        if not part.strip():
            continue
        m = re.match(r"(.+?)=(\S+?):(\d+)$", part.strip())
        if not m:
            raise SystemExit("--targets 片段无法解析: %r（应如 官-1-1-7=MAA_6:13）" % part)
        out.append({"script": m.group(1), "dir": m.group(2), "idx": int(m.group(3))})
    return out


def api_post(path: str, body: dict | None = None, timeout: int = 30, retries: int = 3) -> dict:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"),
                                         method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001  502/超时 有偶发，重试
            last = e
            time.sleep(2 + 3 * i)
    raise last


def fetch_user_tables(targets: list, log=print) -> dict:
    """{script_key: {"users":[{name,mask,uid}], "by_mask":{...}, "by_uuid":{...}}}；API 失败返回 {}。"""
    try:
        data = api_post("/api/scripts/get", {"scriptId": None}).get("data") or {}
        id2name = {}
        for sid, s in data.items():
            name = ((s or {}).get("Info") or {}).get("Name") or ""
            id2name[sid] = name
        out = {}
        for t in targets:
            sid = None
            for k, name in id2name.items():
                if t["script"] in name:
                    sid = k
                    break
            if not sid:
                log("  [api] 找不到脚本 %r" % t["script"])
                continue
            users = api_post("/api/scripts/user/get", {"scriptId": sid}).get("data") or {}
            rows = []
            for uid_key, u in users.items():
                info = (u or {}).get("Info") or {}
                rows.append({"name": info.get("Name") or "?",
                             "mask": mask_of(info.get("Id")),
                             "uuid": str(uid_key).lower()})
            out[t["script"]] = {
                "script_id": sid,
                "users": rows,
                "by_mask": {r["mask"]: r["name"] for r in rows if r["mask"] != "?"},
                "by_uuid": {r["uuid"].lower(): r for r in rows if r["uuid"]},
            }
        return out
    except Exception as e:  # noqa: BLE001
        log("  [api] 用户表获取失败: %r（继续，账号名可能为 '?'）" % e)
        return {}


def load_user_map(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------- watch ----------------

class Tracer:
    def __init__(self, targets, outdir, shot_delays, post_delay, max_rounds, no_shots, poll, quiet_min):
        self.targets = targets
        self.outdir = outdir
        os.makedirs(os.path.join(outdir, "shots"), exist_ok=True)
        self.shot_delays = shot_delays
        self.post_delay = post_delay
        self.max_rounds = max_rounds
        self.no_shots = no_shots
        self.poll = poll
        self.quiet_min = quiet_min
        self.seen = {}        # dir -> set(md5)
        self.seen_app = set()
        self.round_no = {}    # dir -> int
        self.last_event = None
        self.pending = []     # (due_ts_epoch, dir, round, phase, delay)
        self.started = now()
        self.stop = False
        self.emu = {t["dir"]: EmuCtl(t["idx"]) for t in targets}
        self.user_tables = {}

    # --- 文件 ---
    def p(self, *a):
        return os.path.join(self.outdir, *a)

    def log(self, msg):
        line = "[%s][trace] %s" % (now().strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(self.p("watch.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def emit(self, rec: dict):
        rec = {"at": ts_str(), **rec}
        with open(self.p("events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.last_event = now()

    def save_status(self, poll_i):
        with open(self.p("status.json"), "w", encoding="utf-8") as f:
            json.dump({"last_poll": ts_str(), "polls": poll_i,
                       "rounds": self.round_no, "pending_shots": len(self.pending)}, f)

    # --- 远端 ---
    def gui_log_of(self, d: dict) -> str:
        return r"%s\%s\debug\gui.log" % (MAA_ROOT, d["dir"])

    def seed(self):
        for t in self.targets:
            d = t["dir"]
            self.seen[d] = set()
            try:
                lines = tail_remote(self.gui_log_of(t), 600)
                for l in lines:
                    self.seen[d].add(hashlib.md5(l.encode("utf-8", "replace")).hexdigest())
                # bootstrap：最近 180 秒内的 StartUp 才补事件（避免把历史当新事件）
                for l in lines:
                    if RE_STARTUP.search(l):
                        lt = line_ts(l)
                        if lt and -180 <= (now() - lt).total_seconds() <= 120:
                            self.on_startup(t, l, lt, bootstrap=True)
                self.log("seeded %s: %d lines (bootstrap rounds=%s)" % (d, len(lines), self.round_no.get(d, 0)))
            except Exception as e:  # noqa: BLE001
                self.log("seed %s failed: %r" % (d, e))
        try:
            for l in tail_remote(APP_LOG, 300):
                self.seen_app.add(hashlib.md5(l.encode("utf-8", "replace")).hexdigest())
        except Exception as e:  # noqa: BLE001
            self.log("app seed failed: %r" % e)

    def mask_to_name(self, mask: str) -> str:
        for t in self.targets:
            tbl = self.user_tables.get(t["script"]) or {}
            n = (tbl.get("by_mask") or {}).get(mask)
            if n:
                return n
        for tbl in self.user_tables.values():
            n = (tbl.get("by_mask") or {}).get(mask)
            if n:
                return n
        return "?"

    # --- 事件 ---
    def on_startup(self, t, line, lt, bootstrap=False):
        d = t["dir"]
        n = self.round_no.get(d, 0) + 1
        self.round_no[d] = n
        self.emit({"kind": "startup_start", "dir": d, "vmindex": t["idx"], "round": n,
                   "line_ts": ts_str(lt), "line": line.strip()[:200],
                   "bootstrap": bool(bootstrap)})
        self.log("ROUND %s#%d StartUp%s %s" % (d, n, " [bootstrap]" if bootstrap else "", ts_str(lt)))
        if not self.no_shots:
            base = time.time()
            for k, delay in enumerate(self.shot_delays):
                self.pending.append((base + delay, d, n, "pre%d" % (k + 1), delay))

    def on_done(self, t, line, lt):
        d = t["dir"]
        self.emit({"kind": "startup_done", "dir": d, "round": self.round_no.get(d, 0),
                   "line_ts": ts_str(lt), "line": line.strip()[:200]})
        self.log("ROUND %s#%d StartUp DONE %s" % (d, self.round_no.get(d, 0), ts_str(lt)))
        if not self.no_shots and self.round_no.get(d, 0):
            self.pending.append((time.time() + self.post_delay, d, self.round_no[d], "post", self.post_delay))

    def on_switch(self, t, line, lt, mask):
        d = t["dir"]
        name = self.mask_to_name(mask)
        self.emit({"kind": "switch", "dir": d, "vmindex": t["idx"], "round": self.round_no.get(d, 0),
                   "mask": mask, "account": name, "line_ts": ts_str(lt), "line": line.strip()[:200]})
        self.log("SWITCH %s#%d  %s  account=%s  %s" % (d, self.round_no.get(d, 0), mask, name, ts_str(lt)))

    def run_shots(self):
        alive = []
        for due, d, rnd, phase, delay in self.pending:
            if time.time() < due:
                alive.append((due, d, rnd, phase, delay))
                continue
            name = "%s_r%d_%s_%s.png" % (d, rnd, phase, now().strftime("%H%M%S"))
            path = self.p("shots", name)
            t = next(x for x in self.targets if x["dir"] == d)
            ctl = self.emu[d]
            ok = False
            last_head = ""
            for attempt in (1, 2):
                if not ctl.serial or not ctl.ensure(self.log):
                    time.sleep(2)
                try:
                    data = ctl.shot()
                except Exception as e:  # noqa: BLE001
                    last_head = repr(e)
                    data = b""
                if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 15000:
                    with open(path, "wb") as f:
                        f.write(data)
                    self.emit({"kind": "shot", "dir": d, "vmindex": ctl.vmindex, "round": rnd,
                               "phase": phase, "delay": delay, "path": os.path.relpath(path, self.outdir),
                               "bytes": len(data), "serial": ctl.serial, "attempt": attempt})
                    self.log("SHOT %s (%d bytes, +%ds, attempt%d)" % (name, len(data), delay, attempt))
                    ok = True
                    break
                last_head = (data[:120].decode("utf-8", "replace") if data else last_head)
                ctl.serial = None  # 触发重发现
                time.sleep(2)
            if not ok:
                self.emit({"kind": "shot_fail", "dir": d, "round": rnd, "phase": phase,
                           "delay": delay, "head": last_head[:120]})
                self.log("SHOT FAIL %s (+%ds): %s" % (name, delay, last_head[:80]))
        self.pending = alive

    # --- 主循环 ---
    def run(self, duration_min=None, deadline=None):
        self.user_tables = fetch_user_tables(self.targets, self.log)
        if self.user_tables:
            with open(self.p("account_map.json"), "w", encoding="utf-8") as f:
                json.dump({k: {"users": v["users"]} for k, v in self.user_tables.items()},
                          f, ensure_ascii=False, indent=1)
        self.log("START pid=%d targets=%s outdir=%s" % (
            os.getpid(), ";".join("%s=%s:%d" % (t["script"], t["dir"], t["idx"]) for t in self.targets), self.outdir))
        self.seed()
        poll_i = 0
        while not self.stop:
            nw = now()
            if deadline and nw >= deadline:
                self.log("deadline reached")
                break
            if duration_min and (nw - self.started).total_seconds() >= duration_min * 60:
                self.log("duration reached (%d min)" % duration_min)
                break
            if self.max_rounds and sum(self.round_no.values()) >= self.max_rounds:
                self.log("max rounds reached")
                break
            if (self.quiet_min and self.last_event
                    and (nw - self.last_event).total_seconds() >= self.quiet_min * 60):
                self.log("quiet %d min, stop" % self.quiet_min)
                break
            poll_i += 1
            for t in self.targets:
                d = t["dir"]
                try:
                    for l in tail_remote(self.gui_log_of(t), 400):
                        h = hashlib.md5(l.encode("utf-8", "replace")).hexdigest()
                        if h in self.seen[d]:
                            continue
                        self.seen[d].add(h)
                        if RE_STARTUP.search(l):
                            self.on_startup(t, l, line_ts(l))
                        elif RE_STARTUP_DONE.search(l):
                            self.on_done(t, l, line_ts(l))
                        else:
                            m = RE_SWITCH.search(l)
                            if m:
                                self.on_switch(t, l, line_ts(l), m.group(1))
                except Exception as e:  # noqa: BLE001
                    self.log("poll %s err: %r" % (d, e))
            try:
                for l in tail_remote(APP_LOG, 200):
                    h = hashlib.md5(l.encode("utf-8", "replace")).hexdigest()
                    if h in self.seen_app:
                        continue
                    self.seen_app.add(h)
                    for rgx, kind in ((RE_APP_ROUND_START, "round_start"),
                                      (RE_APP_ROUND_DONE, "round_done"),
                                      (RE_APP_ROUND_FAIL, "round_fail"),
                                      (RE_APP_TASK_START, "task_start"),
                                      (RE_APP_TASK_END, "task_end")):
                        m = rgx.search(l)
                        if m:
                            self.emit({"kind": "app." + kind, "uuid": m.group(1),
                                       "ts_line": ts_str(line_ts(l)), "line": l.strip()[:220]})
                            break
            except Exception as e:  # noqa: BLE001
                self.log("app poll err: %r" % e)
            self.run_shots()
            self.save_status(poll_i)
            if poll_i % 15 == 0:
                self.log("poll#%d alive rounds=%s pending_shots=%d" % (poll_i, self.round_no, len(self.pending)))
            time.sleep(self.poll)
        # 收尾：给 pending 一个机会（最多 3 分钟）
        t_end = time.time() + 180
        while self.pending and time.time() < t_end:
            self.run_shots()
            time.sleep(5)
        self.log("DONE rounds=%s" % self.round_no)


# ---------------- ocr ----------------

def run_ocr(frames_dir, outdir, force=False, engine=None):
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        print("rapidocr_onnxruntime 不可用：请用 Python312 运行 ocr（"
              r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe）")
        return 2
    engine = engine or RapidOCR()
    ocr_dir = os.path.join(outdir, "ocr")
    os.makedirs(ocr_dir, exist_ok=True)
    index = {}
    idx_path = os.path.join(outdir, "ocr_index.json")
    if os.path.exists(idx_path) and not force:
        try:
            with open(idx_path, "r", encoding="utf-8") as f:
                index = json.load(f)
        except Exception:
            index = {}
    names = sorted([n for n in os.listdir(frames_dir) if n.lower().endswith(".png")])
    for n in names:
        path = os.path.join(frames_dir, n)
        txt_path = os.path.join(ocr_dir, n + ".txt")
        if os.path.exists(txt_path) and not force and n in index:
            continue
        try:
            result, _ = engine(path)
        except Exception as e:  # noqa: BLE001
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("OCR ERROR: %r\n" % e)
            index[n] = {"error": repr(e)}
            continue
        lines = []
        ids, masks, player = [], [], ""
        for box, text, score in (result or []):
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append("%.3f\tx=%d-%d y=%d-%d\t%s" % (score, min(xs), max(xs), min(ys), max(ys), text))
            t = text.replace(" ", "")
            for m in RE_ID.finditer(t):
                if m.group(1) not in ids:
                    ids.append(m.group(1))
            for m in RE_MASK.finditer(t):
                mm = m.group(0)
                if mm not in masks:
                    masks.append(mm)
            if "玩家" in t and not player:
                player = t[:60]
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        index[n] = {"ids": ids, "masks": masks, "player": player, "lines": len(lines)}
        print("ocr", n, "ids=%s masks=%s" % (ids, masks), flush=True)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print("ocr index ->", idx_path, "frames=%d" % len(index))
    return 0


# ---------------- report ----------------

def load_gui_events(path: str) -> list:
    """gui.log -> [{ts, kind, mask?, line}]（StartUp/Completed/switch）。"""
    ev = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            lt = None
            m = RE_STARTUP.search(line)
            if m:
                lt = line_ts(line)
                ev.append({"ts": lt, "kind": "startup_start", "line": line.strip()[:200]})
                continue
            if RE_STARTUP_DONE.search(line):
                lt = line_ts(line)
                ev.append({"ts": lt, "kind": "startup_done", "line": line.strip()[:200]})
                continue
            m = RE_SWITCH.search(line)
            if m:
                lt = line_ts(line)
                ev.append({"ts": lt, "kind": "switch", "mask": m.group(1), "line": line.strip()[:200]})
    return ev


def build_gui_rounds(events: list, since=None, until=None) -> list:
    rounds = []
    cur = None
    for e in events:
        if e["ts"] is None:
            continue
        if since and e["ts"] < since:
            continue
        if until and e["ts"] > until:
            continue
        if e["kind"] == "startup_start":
            cur = {"start": e["ts"], "done": None, "switches": [], "raw_start": e["line"]}
            rounds.append(cur)
        elif e["kind"] == "startup_done":
            if cur is not None and cur["done"] is None:
                cur["done"] = e["ts"]
        elif e["kind"] == "switch":
            if cur is not None:
                cur["switches"].append({"ts": e["ts"], "mask": e["mask"]})
    return rounds


def build_app_rounds(applog_path: str, uuid2script: dict, uuid2name: dict = None,
                     since=None, until=None) -> list:
    """app.log -> 每用户轮 [{uuid, script, user, start, end, done, fails, attempts}]

    注意 app.log 是**多脚本交错**的流，因此全部按 uuid 键跟踪，不用「当前轮」模型。
    """
    uuid2name = uuid2name or {}
    rounds_by_uuid = {}
    rounds = []
    with open(applog_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ts = line_ts(line)
            if ts is None:
                continue
            m = RE_APP_ROUND_START.search(line)
            if m:
                uuid = m.group(1).lower()
                cur = {"uuid": uuid, "script": uuid2script.get(uuid, "?"),
                       "name": uuid2name.get(uuid, "?"),
                       "start": ts, "end": ts, "done": False, "fails": [], "attempts": 0}
                rounds.append(cur)
                rounds_by_uuid.setdefault(uuid, []).append(cur)
                continue
            m = RE_APP_ROUND_DONE.search(line)
            if m:
                uuid = m.group(1).lower()
                cand = [r for r in rounds_by_uuid.get(uuid, []) if not r["done"]]
                if cand:
                    cand[-1]["done"] = True
                    cand[-1]["end"] = ts
                continue
            m = RE_APP_ROUND_FAIL.search(line)
            if m:
                uuid = m.group(1).lower()
                cand = [r for r in rounds_by_uuid.get(uuid, []) if not r["done"]]
                if cand:
                    cand[-1]["fails"].append({"ts": ts, "reason": m.group(2)})
                    cand[-1]["end"] = ts
                continue
            m = RE_APP_ATTEMPT.search(line)
            if m:
                nm = m.group(1)
                for r in reversed(rounds):
                    if r["name"] == nm and not r["done"]:
                        r["attempts"] = max(r["attempts"], int(m.group(2)))
                        break
    if since or until:
        kept = []
        for r in rounds:
            if since and r["start"] < since:
                continue
            if until and r["start"] > until:
                continue
            kept.append(r)
        rounds = kept
    return rounds


def load_frames_manifest(path: str) -> list:
    """json(list) 或 jsonl：[{dir, ts, path}]"""
    frames = []
    if not path:
        return frames
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    rows = []
    if txt.startswith("["):
        rows = json.loads(txt)
    else:
        for ln in txt.splitlines():
            if ln.strip():
                rows.append(json.loads(ln))
    for r in rows:
        t = r.get("ts")
        if isinstance(t, str):
            t = dt.datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        frames.append({"dir": r["dir"], "ts": t, "path": r["path"], "phase": r.get("phase", ""),
                       "ids": r.get("ids"), "masks": r.get("masks"), "player": r.get("player", "")})
    return frames


def ocr_lookup(index: dict, frame_path: str):
    """ocr_index.json 按 basename 匹配。"""
    base = os.path.basename(frame_path)
    if base in index:
        return index[base]
    for k, v in index.items():
        if k.endswith(base):
            return v
    return None


def uid_map_for_script(uid_map_path: str, script: str) -> dict:
    if not uid_map_path or not os.path.exists(uid_map_path):
        return {}
    try:
        with open(uid_map_path, "r", encoding="utf-8") as f:
            m = json.load(f)
    except Exception:
        return {}
    # 允许 {'script': {mask: uid}} 或 {mask: uid} 单表
    for k in m:
        if k == script or script in k:
            return {mm: str(uu) for mm, uu in (m[k] or {}).items() if uu}
    return {mm: str(uu) for mm, uu in m.items() if isinstance(uu, (str, int))}


def make_report(args) -> int:
    targets = parse_targets(args.targets or DEFAULT_TARGETS)
    since = dt.datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=TZ) if args.since else None
    until = (dt.datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=TZ)
             if args.until else (since + dt.timedelta(days=1) if since else None))

    user_tables = {}
    if args.user_map:
        user_tables = load_user_map(args.user_map)
    else:
        user_tables = fetch_user_tables(targets, log=lambda *a: print(*a, file=sys.stderr))

    uuid2script = {}
    uuid2name = {}
    for t in targets:
        tbl = user_tables.get(t["script"]) or {}
        for r in tbl.get("users", []):
            if r.get("uuid"):
                uuid2script[r["uuid"].lower()] = t["script"]
                uuid2name[r["uuid"].lower()] = r.get("name", "?")

    # 日志来源：本地 or 远端现拉
    gui_texts = {}
    for t in targets:
        if args.logs and t["dir"] in args.logs:
            with open(args.logs[t["dir"]], "r", encoding="utf-8", errors="replace") as f:
                gui_texts[t["dir"]] = f.read()
        else:
            print("pull gui.log %s ..." % t["dir"], file=sys.stderr)
            gui_texts[t["dir"]] = pull_remote_text(r"%s\%s\debug\gui.log" % (MAA_ROOT, t["dir"]),
                                                   filters=GUI_PULL_FILTERS)
    if args.applog and os.path.exists(args.applog):
        with open(args.applog, "r", encoding="utf-8", errors="replace") as f:
            app_text = f.read()
    else:
        print("pull app.log ...", file=sys.stderr)
        app_text = pull_remote_text(APP_LOG, filters=APP_PULL_FILTERS)
    tmp_app = os.path.join(args.outdir, "_app_slice.log") if args.outdir else "_app_slice.log"
    with open(tmp_app, "w", encoding="utf-8") as f:
        f.write(app_text)

    app_rounds = build_app_rounds(tmp_app, uuid2script, uuid2name, since=since, until=until)

    frames = load_frames_manifest(args.frames_manifest) if args.frames_manifest else []
    ocr_index = {}
    ocr_path = args.ocr_index or (os.path.join(args.outdir, "ocr_index.json") if args.outdir else None)
    if ocr_path and os.path.exists(ocr_path):
        with open(ocr_path, "r", encoding="utf-8") as f:
            ocr_index = json.load(f)
    # 收集 watch outdir 的 shots（如果有）
    if args.outdir and os.path.isdir(os.path.join(args.outdir, "shots")):
        for n in sorted(os.listdir(os.path.join(args.outdir, "shots"))):
            if n.lower().endswith(".png"):
                frames.append({"dir": n.split("_")[0], "ts": None, "path": os.path.join(args.outdir, "shots", n),
                               "phase": "", "ids": None, "masks": None})

    lines_out = []
    json_out = {"generated": ts_str(), "since": args.since, "until": args.until, "scripts": {}}

    for t in targets:
        script, d = t["script"], t["dir"]
        tbl = user_tables.get(script) or {}
        users = tbl.get("users") or []
        expected_by_uuid = {r["uuid"].lower(): r for r in users if r.get("uuid")}
        gui_ev = load_gui_events_text(gui_texts.get(d, ""))
        gui_rounds = build_gui_rounds(gui_ev, since=since, until=until)

        # 本脚本在 app.log 的轮
        mine = [r for r in app_rounds if r["script"] == script]
        # join：给每个 app 轮找一个 gui 轮（同 dir，start 在 [app.start-3min, app.start+20min]，按序不重复）
        used = set()
        rows = []
        for r in mine:
            gr = None
            for i, g in enumerate(gui_rounds):
                if i in used:
                    continue
                # MAA 轮总是晚于 app 轮开始（本机实测 +24~80s）；取「开始之后最近的一个」
                if g["start"] >= r["start"] - dt.timedelta(seconds=10) \
                        and g["start"] <= r["start"] + dt.timedelta(minutes=15):
                    gr = g
                    used.add(i)
                    break
            r["gui"] = gr
            rows.append(r)
        # 也列出该 dir 里没归属的 gui 轮（共享目录的别的脚本 / 手动轮）
        orphans = [g for i, g in enumerate(gui_rounds) if i not in used]

        uidmap = uid_map_for_script(args.uid_map, script)
        uid2mask = {v: k for k, v in uidmap.items()}

        lined = []
        for r in rows:
            user = expected_by_uuid.get(r["uuid"], {})
            uname, umask = user.get("name", "?"), user.get("mask", "?")
            g = r.get("gui")
            sw = g["switches"][0]["mask"] if (g and g["switches"]) else None
            # 帧证
            frame = None
            if g:
                hits = [fr for fr in frames if fr["dir"] == d and fr["ts"] and
                        g["start"] - dt.timedelta(minutes=1) <= fr["ts"] <= (g["done"] or g["start"]) + dt.timedelta(minutes=8)]
                hits.sort(key=lambda fr: fr["ts"])
                for fr in hits:
                    info = ocr_index.get(os.path.basename(fr["path"])) if ocr_index else None
                    fid = (info or {}).get("ids") or []
                    fmask = (info or {}).get("masks") or []
                    if fid or fmask:
                        frame = {"path": fr["path"], "ts": fr["ts"], "ids": fid, "masks": fmask,
                                 "player": (info or {}).get("player", "")}
                        break
            observed_mask = None
            observed_uid = None
            src = ""
            if frame:
                if frame["masks"]:
                    observed_mask = frame["masks"][0]
                if frame["ids"]:
                    observed_uid = frame["ids"][0]
                    if observed_uid in uid2mask:
                        observed_mask = observed_mask or uid2mask[observed_uid]
                    elif observed_mask and observed_mask in uidmap:
                        pass
                src = "帧"
            else:
                observed_mask = sw
                src = "日志" if sw else ""

            # verdict（身份优先：先看帧，再看切换行；再谈轮自身成败）
            if frame and observed_mask == umask:
                verdict, color = "PASS（帧级 ID 核对）", "green"
            elif frame and observed_mask and observed_mask != umask:
                verdict, color = "🔴 错配：实测 %s ≠ 期望 %s" % (observed_mask, umask), "red"
            elif frame and not observed_mask:
                verdict, color = "⚠️ 帧无掩码可判（UID=%s 未入表）" % observed_uid, "yellow"
            elif sw == umask and sw:
                verdict, color = "PASS（日志级 切换行）", "yellow-green"
            elif sw and sw != umask:
                verdict, color = "🔴 切换行错配：%s ≠ 期望 %s" % (sw, umask), "red"
            elif g and (g.get("done") or r["done"]):
                verdict, color = "⚠️ 无切换行且无帧证（可能同号重登，需帧证）", "yellow"
            elif r["fails"]:
                verdict, color = "轮失败（无身份证据）", "gray"
            else:
                verdict, color = "轮未完成（无身份结论）", "gray"

            lined.append({"round": r, "user": umask, "user_name": uname, "gui": g,
                          "switch": sw, "frame": frame, "observed_mask": observed_mask,
                          "observed_uid": observed_uid, "src": src, "verdict": verdict, "color": color})

        # 依据 app 轮序切分「运行」（相邻轮间隔 > 20min 视为新运行）
        runs = []
        for row in lined:
            r = row["round"]
            if not runs or (r["start"] - runs[-1][-1]["round"]["start"]) > dt.timedelta(minutes=20):
                runs.append([row])
            else:
                runs[-1].append(row)

        # 输出
        lines_out.append("### %s（%s / 实例%d）" % (script, d, t["idx"]))
        lines_out.append("")
        if not mine:
            lines_out.append("*窗口内无该脚本的运行记录*")
            lines_out.append("")
        for ri, run in enumerate(runs):
            seq = []
            for row in run:
                m = row["observed_mask"] or "—"
                seq.append("%s: %s%s" % (row["user_name"], m, ("🔴" if row["color"] == "red" else "")))
            lines_out.append("**运行 #%d**（%s 起，%d 轮）序列: %s" % (
                ri + 1, run[0]["round"]["start"].strftime("%m-%d %H:%M"), len(run), " → ".join(seq)))
            lines_out.append("")
            lines_out.append("| 轮 | 开始 | 用户@期望掩码 | 切换行 | 帧证(UID) | 实测 | 判定 |")
            lines_out.append("|---|---|---|---|---|---|---|")
            for row in run:
                r = row["round"]
                sw = row["switch"] or "—"
                fr = row["frame"]
                fcell = "—"
                if fr and fr["ids"]:
                    fcell = "ID:%s" % fr["ids"][0]
                    if len(fr["ids"]) > 1:
                        fcell += " (+%d)" % (len(fr["ids"]) - 1)
                elif fr and fr["masks"]:
                    fcell = "掩码 %s" % fr["masks"][0]
                st = r["start"].strftime("%H:%M:%S")
                verdict = row["verdict"]
                if row["color"] == "red":
                    verdict = "**" + verdict + "**"
                att = ""
                if (r["attempts"] or 0) > 1 or r["fails"]:
                    att = "（尝试%d次，失败%d）" % (max(1, r["attempts"] or 1), len(r["fails"]))
                lines_out.append("| %s | %s | %s@%s | %s | %s | %s | %s |" % (
                    r["start"].strftime("%m-%d %H:%M"), st, row["user_name"], row["user"],
                    sw, fcell, row["observed_mask"] or "—", verdict + att))
            lines_out.append("")
        if orphans:
            lines_out.append("<details><summary>%d 个未归属的 gui 轮（共享目录/其它脚本/手动轮）</summary>" % len(orphans))
            lines_out.append("")
            for g in orphans:
                sws = ",".join(s["mask"] for s in g["switches"]) or "—"
                lines_out.append("- %s → %s | 切换行: %s" % (
                    g["start"].strftime("%m-%d %H:%M:%S"),
                    g["done"].strftime("%H:%M:%S") if g["done"] else "未完成", sws))
            lines_out.append("")
            lines_out.append("</details>")
            lines_out.append("")
        json_out["scripts"][script] = {
            "maa_dir": d, "users": users,
            "rounds": [{"user": row["user"], "user_name": row["user_name"],
                        "start": ts_str(row["round"]["start"]),
                        "done": ts_str(row["gui"]["done"]) if (row["gui"] and row["gui"]["done"]) else None,
                        "app_completed": bool(row["round"]["done"]),
                        "switch": row["switch"], "observed_mask": row["observed_mask"],
                        "observed_uid": row["observed_uid"], "evidence": row["src"],
                        "verdict": row["verdict"], "color": row["color"]} for row in lined],
        }
    md = "\n".join(lines_out)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        print("report ->", args.out)
    else:
        print(md)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(json_out, f, ensure_ascii=False, indent=1)
        print("json   ->", args.json)
    reds = sum(1 for s in json_out["scripts"].values() for r in s["rounds"] if r["color"] == "red")
    print("RED rounds:", reds)
    return 1 if reds else 0


def load_gui_events_text(text: str) -> list:
    """gui.log 文本 -> 事件（不落盘）。"""
    ev = []
    for line in text.splitlines():
        if RE_STARTUP.search(line):
            ev.append({"ts": line_ts(line), "kind": "startup_start", "line": line.strip()[:200]})
            continue
        if RE_STARTUP_DONE.search(line):
            ev.append({"ts": line_ts(line), "kind": "startup_done", "line": line.strip()[:200]})
            continue
        m = RE_SWITCH.search(line)
        if m:
            ev.append({"ts": line_ts(line), "kind": "switch", "mask": m.group(1), "line": line.strip()[:200]})
    return ev


# ---------------- selftest ----------------

SELFTEST_SAMPLES = [
    ("[2026-09-27 19:46:07.531][INF][AsstProxy]              <2> Start Task Chain: StartUp, Task ID: 1",
     "startup_start"),
    ("[2026-09-27 19:46:50.635][INF][AsstProxy]              <2> Completed Task Chain: StartUp, Task ID: 1",
     "startup_done"),
    ("[2026-09-27 19:46:18.964][INF][TaskQueueViewModel]     <2> 账号切换 -->> 134****5276", "switch"),
]


def selftest() -> int:
    fails = 0
    for line, kind in SELFTEST_SAMPLES:
        ev = load_gui_events_text(line)
        ok = ev and ev[0]["kind"] == kind
        if kind == "switch":
            ok = ok and ev[0]["mask"] == "134****5276"
            ok = ok and ev[0]["ts"].strftime("%H:%M:%S") == "19:46:18"
        print(("PASS" if ok else "FAIL"), kind, "|", line[:80])
        fails += 0 if ok else 1
    # app 行
    for line, pat, want in [
        ("2026-09-27 19:45:41.632 | INFO     | MAA 自动代理 | 开始代理用户: 78986923-94b7-446a-a8da-f4b12fd09c71",
         RE_APP_ROUND_START, "78986923-94b7-446a-a8da-f4b12fd09c71"),
        ("2026-09-27 11:15:07.125 | SUCCESS  | MAA 自动代理 | 用户 78986923-94b7-446a-a8da-f4b12fd09c71 的自动代理任务已完成",
         RE_APP_ROUND_DONE, "78986923-94b7-446a-a8da-f4b12fd09c71"),
    ]:
        m = pat.search(line)
        ok = bool(m) and m.group(1) == want
        print(("PASS" if ok else "FAIL"), pat.pattern[:40], "|", line[:70])
        fails += 0 if ok else 1
    print("selftest:", "OK" if not fails else "FAILED(%d)" % fails)
    return 0 if not fails else 1


# ---------------- main ----------------

def main() -> int:
    ap = argparse.ArgumentParser(description="每轮身份留痕（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="盯 gui.log + 抓帧")
    w.add_argument("--targets", default=DEFAULT_TARGETS)
    w.add_argument("--outdir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "..", "runtime", "identity_trace"))
    w.add_argument("--duration-min", type=int)
    w.add_argument("--deadline", help="HH:MM")
    w.add_argument("--poll", type=int, default=20)
    w.add_argument("--shot-delays", default="12,45")
    w.add_argument("--post-delay", type=int, default=8)
    w.add_argument("--max-rounds", type=int, default=24)
    w.add_argument("--quiet-min", type=int, default=0)
    w.add_argument("--no-shots", action="store_true")

    o = sub.add_parser("ocr", help="帧 OCR")
    o.add_argument("--outdir", required=True)
    o.add_argument("--frames-dir")
    o.add_argument("--force", action="store_true")

    r = sub.add_parser("report", help="离线对照报告")
    r.add_argument("--targets", default=DEFAULT_TARGETS)
    r.add_argument("--since")
    r.add_argument("--until")
    r.add_argument("--logs", help="MAA_6=path,MAA_2=path（本地已有日志时用）")
    r.add_argument("--applog")
    r.add_argument("--outdir", default=None, help="含 shots/ocr_index 的运行目录")
    r.add_argument("--ocr-index")
    r.add_argument("--frames-manifest")
    r.add_argument("--uid-map")
    r.add_argument("--user-map")
    r.add_argument("--out")
    r.add_argument("--json")

    sub.add_parser("selftest", help="解析器自检")

    a = ap.parse_args()

    if a.cmd == "selftest":
        return selftest()

    if a.cmd == "watch":
        targets = parse_targets(a.targets)
        deadline = None
        if a.deadline:
            hh, mm = a.deadline.split(":")
            deadline = now().replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if deadline <= now():
                deadline += dt.timedelta(days=1)
        tr = Tracer(targets, a.outdir,
                    shot_delays=[int(x) for x in a.shot_delays.split(",") if x.strip()],
                    post_delay=a.post_delay, max_rounds=a.max_rounds,
                    no_shots=a.no_shots, poll=a.poll, quiet_min=a.quiet_min)
        try:
            tr.run(duration_min=a.duration_min, deadline=deadline)
        except KeyboardInterrupt:
            tr.log("KeyboardInterrupt -> stop")
            tr.stop = True
        return 0

    if a.cmd == "ocr":
        frames_dir = a.frames_dir or os.path.join(a.outdir, "shots")
        return run_ocr(frames_dir, a.outdir, force=a.force)

    if a.cmd == "report":
        if a.logs:
            kv = {}
            for part in a.logs.split(","):
                k, v = part.split("=", 1)
                kv[k.strip()] = v.strip()
            a.logs = kv
        return make_report(a)


if __name__ == "__main__":
    sys.exit(main())
