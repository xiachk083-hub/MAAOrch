#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""权威映射表：MAA 槽位 ↔ 脚本 ↔ 实例 idx ↔ 实例标题 ↔ adb 串口（实测）↔ 账号数 ↔ 队列。

背景（为什么要这份表）：这套映射散落在 MAS_EMULATORS.md / MAS_SCHEDULE_MAP.md /
MAA_ROGUELIKE_PLAYBOOK.md 等多份报告里，而且 **adb 端口会漂**——文档写死 17248，
实例重启后 vm_config 变 17249（2026-09-28 实测 idx27）。每次实机任务都要重新摸索。
本工具把它变成一条命令：**建表 + 自动校验（实测 vs 公式/槽位记忆/REST/基线）+ 机器可读产物**。

数据源（全部只读；不启动/不关闭实例、不改任何配置）：
  * ssh <host>  E:\MuMu Player 12\vms\MuMuPlayer-12.0-<idx>\configs\vm_config.json
        → 每实例 adb 主口 host_port（**实测**，持久化在实例上）
  * ssh         configs\extra_config.json → 实例标题 playerName + 创建时间（判是否重建）
  * ssh         Win32_Process MuMuVMMHeadless（--comment MuMuPlayer-12.0-<idx>）+ 其监听口
        → 在跑实例（MuMuManager 的 is_process_started 在本机队假死，只信 VMM 进程）
  * ssh         "E:\MuMu Player 12\shell\adb.exe" devices → 当前 adb 端点（主口+副口）
  * ssh         E:\MAA-Multi\MAA_N\config\gui.new.json → 槽位**记忆地址**（最近一次注入）
  * ssh         E:\MAA-Multi\MAA_N\debug\asst.log 末段 → MAA **最近一次实际连接**的地址
  * ssh         E:\AUTO-MAS\config\ScriptConfig.json → 脚本→实例 idx→账号（白名单字段）
  * REST        http://100.79.173.69:36163 只读端点：
        /api/scripts/get          → 脚本名/MAA 目录/实例 idx（配置侧）
        /api/scripts/user/get     → 账号（ScriptConfig 取不到时兜底）
        /api/queue/get + /api/queue/item/get → 队列 → 脚本（队列列）
        /api/emulator/status      → AUTO-MAS 眼里的实例 title/status/adb_address（**副口家族**）

凭据卫生：只取白名单字段；任何 password/token/secret/Id 在读入后即被丢弃（导出里不可能出现）。

用法：
  python tools/mas_mapping.py --build                建表 → runtime/reports/mapping.{json,md}
  python tools/mas_mapping.py --verify               校验（不写文件）：实测 vs 公式/槽位/REST/基线
  python tools/mas_mapping.py --verify --json        机器可读校验结果
  python tools/mas_mapping.py --port 13              取某实例当前 adb 串口（实测优先，基线兜底）
  python tools/mas_mapping.py --lookup 明日方舟-官-1-1-7   某脚本的整行（槽位/idx/口/队列）
  python tools/mas_mapping.py --offline fixture.json 用本地快照跑（自测/回归）
  python tools/mas_mapping.py --selftest             离线自测（不联网）

退出码：0=无 RED；1=有 RED（就是「标红」件）；2=取数失败/判不了。

作为库使用（供其它工具 import）：
  import mas_mapping as mm
  snap = mm.fetch_live()                 # 全量实测快照
  doc  = mm.load_mapping()               # 上一次 --build 的产物（无网络）
  got  = mm.serial_for(doc, idx=13)      # → {"serial": "127.0.0.1:16800", "port": 16800, ...}
  port = mm.expected_port(27)            # 公式期望值（注意：只作基准，实测为准）

⚠️ 两个历史教训（写进代码防止重犯）：
  1) 经 ssh 喂 `powershell -NoProfile -Command -` 的脚本是**行式解析**：多行 `{` 块会被
     静默吞掉（rc=0、无输出）。**所有块必须写在一行**（foreach (...) { ... }）。
  2) MuMuManager `info -v` 的 `is_process_started` / `adb --cmd connect` 在本机队不可信
     （恒 false / 恒 -201）。死活判据 = VMM 进程 + 监听口 + adb devices。
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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "runtime" / "reports"
OUT_JSON = REPORTS / "mapping.json"
OUT_MD = REPORTS / "mapping.md"

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
MUMU_VMS = os.environ.get("MAAORCH_MUMU_VMS", r"E:\MuMu Player 12\vms")
MAA_ROOT = os.environ.get("MAAORCH_MAA_ROOT", r"E:\MAA-Multi")
ADB_EXE = os.environ.get("MAAORCH_ADB", r"E:\MuMu Player 12\shell\adb.exe")
MUMU_MANAGER = os.environ.get("MAAORCH_MUMUMANAGER", r"E:\MuMu Player 12\nx_main\MuMuManager.exe")
SCRIPT_CONFIG = os.environ.get("MAAORCH_SCRIPT_CONFIG", r"E:\AUTO-MAS\config\ScriptConfig.json")

ADB_BASE, ADB_STEP = 16384, 32        # 家族 A（idx<=31）
FAMILY_B_BASE = 16388                 # 家族 B（idx>=32）= 16388 + 32*(idx-32)
SEC_BASE, SEC_STEP = 5555, 2          # 副口（运行时；AUTO-MAS REST 用的就是这一族）
MASK_SUB = ("password", "token", "secret", "cookie", "credential", "skland", "phone")
SENSITIVE_EXACT = ("id",)             # 账号 Info.Id = 手机号
WHITELIST_SCRIPT = ("Name", "Path")
WHITELIST_USER = ("Name", "Server", "Status")

BAD_LINE = re.compile(r"^[^|]*\|")
LC_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[^\]]*)\].*\"address\":\"(127\.0\.0\.1:(\d+))\"")
SERIAL_RE = re.compile(r"^127\.0\.0\.1:(\d+)$")


def log(msg: str) -> None:
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def expected_port(idx: int) -> tuple:
    """公式期望口（仅供比对基准，**实测为准**）。→ (port, family)"""
    idx = int(idx)
    if idx <= 31:
        return ADB_BASE + ADB_STEP * idx, "A"
    return FAMILY_B_BASE + ADB_STEP * (idx - 32), "B"


def secondary_port(idx: int) -> int:
    """副口（运行时存在；AUTO-MAS REST adb_address 就是这一族）。"""
    return SEC_BASE + SEC_STEP * int(idx)


def scrub_keys(obj):
    """递归丢凭据类键（读入即丢弃）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kk = str(k)
            low = kk.lower()
            if low in SENSITIVE_EXACT or any(s in low for s in MASK_SUB):
                continue
            out[kk] = scrub_keys(v)
        return out
    if isinstance(obj, list):
        return [scrub_keys(x) for x in obj]
    return obj


# ---------------------------------------------------------------- ssh / REST

def _ssh_env() -> dict:
    """子进程环境：确保裸名 ssh 可解析（MCP 极简 env 的教训：PATH 里必须有 System32\\OpenSSH）。"""
    env = dict(os.environ)
    sysroot = env.get("SystemRoot") or env.get("windir") or r"C:\Windows"
    oss = os.path.join(sysroot, "System32", "OpenSSH")
    must = [os.path.join(sysroot, "System32"), oss, os.path.join(sysroot, "System32", "Wbem")]
    parts = [p for p in (env.get("PATH") or "").split(os.pathsep) if p]
    low = [p.rstrip("\\").lower() for p in parts]
    for d in must:
        if os.path.isdir(d) and d.rstrip("\\").lower() not in low:
            parts.insert(0, d)
    env["PATH"] = os.pathsep.join(parts)
    return env


def _ssh_base() -> list:
    return ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", SSH_HOST]


def ssh_cmd(cmdline: str, timeout: int = 90) -> str:
    """一次远端 cmd 命令（stdin=DEVNULL 是硬要求：不能继承调用方的管道）。"""
    p = subprocess.run(_ssh_base() + [cmdline], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=timeout, env=_ssh_env())
    return p.stdout.decode("utf-8", "replace")


def ssh_ps(script: str, timeout: int = 240) -> str:
    """stdin 喂 PowerShell。

    2026-09-28 实测教训：`powershell -Command -` 是**行式解析**——以 `{` 结尾的行开出的
    多行块会被静默吞掉（rc=0、零输出）。两条出路：① 每个 `{ }` 块写在一行（本文件早期写法）；
    ② 改走 `-EncodedCommand`（UTF-16LE base64，仓库里 mas_switch_gate / test_regression 的写法）——
    下面用 ②，块可以随便折行。stdin=DEVNULL 是硬要求（不能继承调用方/ MCP 的管道）。
    """
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    b64 = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    cmd = _ssh_base() + ["powershell -NoProfile -EncodedCommand " + b64]
    p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=timeout, env=_ssh_env())
    return p.stdout.decode("utf-8", "replace")


def post(path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body if body is not None else {}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------------------------------------------------------------- 采集面

INSTANCES_PS = (
    "$vms = '" + MUMU_VMS + "'; Get-ChildItem $vms -Directory | ForEach-Object { "
    "$m = [regex]::Match($_.Name, 'MuMuPlayer-12\\.0-(\\d+)$'); "
    "if ($m.Success) { $idx = $m.Groups[1].Value; $vd = Join-Path $_.FullName 'configs\\vm_config.json'; $ed = Join-Path $_.FullName 'configs\\extra_config.json'; "
    "$p = '-'; $mt = '-'; $nm = '-'; $ct = '-'; "
    "if (Test-Path $vd) { try { $j = Get-Content $vd -Raw -Encoding UTF8 | ConvertFrom-Json; $p = $j.vm.nat.port_forward.adb.host_port; $mt = (Get-Item $vd).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') } catch { $p = 'BADJSON' } }; "
    "if (Test-Path $ed) { try { $e2 = Get-Content $ed -Raw -Encoding UTF8 | ConvertFrom-Json; $nm = $e2.playerName; $ct = $e2.createTime } catch {} }; "
    "Write-Output ('INST|' + $idx + '|' + $nm + '|' + $p + '|' + $mt + '|' + $ct) } }; Write-Output 'INST-END'"
)

VMM_PS = (
    "$vmm = @(Get-CimInstance Win32_Process -Filter \"Name='MuMuVMMHeadless.exe'\"); "
    "foreach ($p in $vmm) { if ($p.CommandLine -match 'MuMuPlayer-12\\.0-(\\d+)') { $idx = $matches[1]; "
    "$ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $p.ProcessId } | ForEach-Object { $_.LocalPort } | Sort-Object -Unique); "
    "Write-Output ('VMM|' + $idx + '|' + $p.ProcessId + '|' + ($ports -join ',')) } }; Write-Output 'VMM-END'"
)

SLOTS_PS = (
    "Get-ChildItem '" + MAA_ROOT + "' -Directory | ForEach-Object { $f = Join-Path $_.FullName 'config\\gui.new.json'; "
    "if (Test-Path $f) { try { $j = Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json; "
    "$c = $j.Configurations.Default.Gui.ConnectSettings; $s = $j.Configurations.Default.Gui.StartUpSettings; "
    "Write-Output ('SLOT|' + $_.Name + '|' + $c.Address + '|' + $c.Extras.MuMuEmulator12.InstanceIndex + '|' + $c.AutoDetect + '|' + $s.RunDirectly + '|' + (Get-Item $f).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')) "
    "} catch { Write-Output ('SLOT|' + $_.Name + '|BADJSON') } } else { Write-Output ('SLOT|' + $_.Name + '|NOFILE') } }; Write-Output 'SLOT-END'"
)

LASTCONNECT_PS = (
    "Get-ChildItem '" + MAA_ROOT + "' -Directory | ForEach-Object { $f = Join-Path $_.FullName 'debug\\asst.log'; "
    "if (Test-Path $f) { $lines = Get-Content -Path $f -Tail 4000 -Encoding UTF8 -ErrorAction SilentlyContinue; "
    "$hit = $lines | Select-String -Pattern '\"what\":\"Connected\"' | Select-Object -Last 1; "
    "if ($hit) { Write-Output ('LC|' + $_.Name + '|' + $hit.Line) } } }; Write-Output 'LC-END'"
)


def fetch_instances(timeout: int = 180) -> dict:
    """{idx(str): {title, port(int|None), vm_config_mtime, created}}（实测）。"""
    out = {}
    for ln in ssh_ps(INSTANCES_PS, timeout=timeout).splitlines():
        ln = ln.strip()
        if not ln.startswith("INST|"):
            continue
        parts = ln.split("|")
        if len(parts) < 6:
            continue
        _tag, idx, name, port, mt, ct = parts[:6]
        out[idx.strip()] = {
            "idx": idx.strip(),
            "title": name.strip() or None,
            "port": int(port) if port.strip().isdigit() else None,
            "port_raw": port.strip(),
            "vm_config_mtime": mt.strip() or None,
            "created": ct.strip() or None,
        }
    return out


def fetch_vmm(timeout: int = 180) -> dict:
    """{idx(str): {pid: int, ports: [int, ...]}}（在跑实例，实测）。"""
    out = {}
    for ln in ssh_ps(VMM_PS, timeout=timeout).splitlines():
        ln = ln.strip()
        if not ln.startswith("VMM|"):
            continue
        parts = ln.split("|")
        if len(parts) < 4:
            continue
        _tag, idx, pid, ports = parts[:4]
        pl = []
        for p in ports.split(","):
            p = p.strip()
            if p.isdigit():
                pl.append(int(p))
        out[idx.strip()] = {"pid": int(pid) if pid.strip().isdigit() else None, "ports": sorted(pl)}
    return out


def fetch_slots(timeout: int = 180) -> dict:
    """{slot(str): {address, address_port, instance_index, auto_detect, run_directly, file_mtime|None, bad: bool}}。"""
    out = {}
    for ln in ssh_ps(SLOTS_PS, timeout=timeout).splitlines():
        ln = ln.strip()
        if not ln.startswith("SLOT|"):
            continue
        parts = ln.split("|")
        slot = parts[1].strip()
        if len(parts) < 3 or parts[2] in ("NOFILE", "BADJSON"):
            out[slot] = {"address": None, "address_port": None, "bad": True,
                         "reason": parts[2] if len(parts) > 2 else "?"}
            continue
        _tag, slot, addr = parts[0], parts[1].strip(), parts[2].strip()
        iidx = parts[3].strip() if len(parts) > 3 else ""
        auto = parts[4].strip() if len(parts) > 4 else ""
        run = parts[5].strip() if len(parts) > 5 else ""
        fmt = parts[6].strip() if len(parts) > 6 else ""
        m = SERIAL_RE.match(addr)
        out[slot] = {"address": addr or None, "address_port": int(m.group(1)) if m else None,
                     "instance_index": iidx, "auto_detect": auto, "run_directly": run,
                     "file_mtime": fmt or None, "bad": False}
    return out


def fetch_last_connect(timeout: int = 240) -> dict:
    """{slot(str): {address, port, ts}}（MAA asst.log 里最近一次 Connected 的地址）。"""
    out = {}
    for ln in ssh_ps(LASTCONNECT_PS, timeout=timeout).splitlines():
        ln = ln.strip()
        if not ln.startswith("LC|"):
            continue
        parts = ln.split("|", 2)
        if len(parts) < 3:
            continue
        slot = parts[1].strip()
        m = LC_RE.search(parts[2])
        if not m:
            continue
        out[slot] = {"ts": m.group(1).strip(), "address": m.group(2), "port": int(m.group(3))}
    return out


def fetch_adb(timeout: int = 90) -> list:
    """[{"serial": "127.0.0.1:16800", "port": 16800, "state": "device"}, ...]（实测）。"""
    out = []
    txt = ssh_cmd('"%s" devices' % ADB_EXE, timeout=timeout)
    for ln in txt.replace("\r", "").splitlines():
        ln = ln.strip()
        if not ln or ln.lower().startswith("list of devices"):
            continue
        parts = ln.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        m = SERIAL_RE.match(serial)
        out.append({"serial": serial, "port": int(m.group(1)) if m else None, "state": state})
    return out


def fetch_mumu_info(timeout: int = 120) -> dict:
    """MuMuManager info -v all → {idx: {name, is_process_started, is_android_started, disk_size_bytes}}。
    状态字段本身不可信（假死），只作旁证入库。"""
    try:
        txt = ssh_cmd('"%s" info -v all' % MUMU_MANAGER, timeout=timeout)
        data = json.loads(txt)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            out[str(k)] = {"name": v.get("name"), "is_process_started": v.get("is_process_started"),
                           "is_android_started": v.get("is_android_started"), "disk_size_bytes": v.get("disk_size_bytes")}
    return out


def fetch_script_config(timeout: int = 120) -> dict:
    """E:\\AUTO-MAS\\config\\ScriptConfig.json → {sid: {name, path, idx, users:[...]}}（白名单）。"""
    try:
        txt = ssh_cmd('type "%s"' % SCRIPT_CONFIG, timeout=timeout)
        raw = json.loads(txt)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for sid, sv in (raw or {}).items():
        if sid == "instances" or not isinstance(sv, dict):
            continue
        info = sv.get("Info") or {}
        emu = sv.get("Emulator") or {}
        users = []
        ud = ((sv.get("SubConfigsInfo") or {}).get("UserData") or {})
        for it in (ud.get("instances") or []):
            u = (ud.get(it.get("uid")) or {}).get("Info") or {}
            if not any(u.get(k) for k in ("Name", "Server")):
                continue
            users.append({"login": str(u.get("Name") or "?"), "server": str(u.get("Server") or "?"),
                          "enabled": bool(u.get("Status"))})
        out[sid] = {"name": str(info.get("Name") or ""), "path": str(info.get("Path") or ""),
                    "idx": (str(emu.get("Index")).strip() if emu.get("Index") is not None else ""),
                    "users": users}
    return out


def fetch_rest(timeout: int = 30) -> dict:
    """REST 只读面：scripts / users(兜底) / queues / emulator。任一失败返回空 dict 的分节。"""
    out = {"scripts": {}, "users": {}, "queues": {}, "emulator": {}, "ok": True}
    try:
        sc = (post("/api/scripts/get", {"scriptId": None}, timeout=timeout).get("data") or {})
    except Exception:  # noqa: BLE001
        out["ok"] = False
        return out
    for sid, s in sc.items():
        info = (s or {}).get("Info") or {}
        emu = (s or {}).get("Emulator") or {}
        idx = emu.get("Index")
        out["scripts"][sid] = {"name": str(info.get("Name") or ""), "path": str(info.get("Path") or ""),
                               "idx": str(idx).strip() if idx is not None else ""}
    try:
        qs = (post("/api/queue/get", {"queueId": None}, timeout=timeout).get("data") or {})
    except Exception:  # noqa: BLE001
        qs = {}
    for qid, qv in qs.items():
        info = (qv or {}).get("Info") or {}
        name = str(info.get("Name") or "")
        try:
            items = (post("/api/queue/item/get", {"queueId": qid, "queueItemId": None}, timeout=timeout).get("data") or {})
        except Exception:  # noqa: BLE001
            items = {}
        sids = []
        for _iid, iv in items.items():
            sid = ((iv or {}).get("Info") or {}).get("ScriptId")
            if sid:
                sids.append(str(sid))
        out["queues"][qid] = {"name": name, "sids": sids,
                              "time_enabled": info.get("TimeEnabled"), "startup_enabled": info.get("StartUpEnabled")}
    try:
        es = (post("/api/emulator/status", {}, timeout=timeout).get("data") or {})
        for _eid, m in es.items():
            if not isinstance(m, dict):
                continue
            for idx, v in m.items():
                if isinstance(v, dict):
                    out["emulator"][str(idx)] = {"title": v.get("title"), "status": v.get("status"),
                                                 "adb_address": v.get("adb_address")}
    except Exception:  # noqa: BLE001
        pass
    return out


def fetch_users_rest(rest: dict, timeout: int = 20) -> dict:
    """REST 兜底取账号（ScriptConfig 不可用时）：{sid: [users]}。"""
    out = {}
    for sid in (rest.get("scripts") or {}):
        try:
            us = (post("/api/scripts/user/get", {"scriptId": sid}, timeout=timeout).get("data") or {})
        except Exception:  # noqa: BLE001
            continue
        lst = []
        for _uid, uv in us.items():
            ui = (uv or {}).get("Info") or {}
            if not any(ui.get(k) for k in WHITELIST_USER):
                continue
            lst.append({"login": str(ui.get("Name") or "?"), "server": str(ui.get("Server") or "?"),
                        "enabled": bool(ui.get("Status"))})
        out[sid] = lst
    return out


def fetch_live(no_rest: bool = False, timeouts: dict | None = None) -> dict:
    """全量**实测**快照（只读）。返回 dict（可直接喂 build_doc / 落盘为 fixture）。"""
    t = {"instances": 180, "vmm": 180, "slots": 180, "lc": 240, "adb": 90, "info": 120, "cfg": 120, "rest": 30}
    t.update(timeouts or {})
    snap = {"fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "host": SSH_HOST, "api": API}
    snap["instances"] = fetch_instances(timeout=t["instances"])
    snap["vmm"] = fetch_vmm(timeout=t["vmm"])
    snap["slots"] = fetch_slots(timeout=t["slots"])
    snap["last_connect"] = fetch_last_connect(timeout=t["lc"])
    snap["adb"] = fetch_adb(timeout=t["adb"])
    snap["mumu_info"] = fetch_mumu_info(timeout=t["info"])
    snap["script_config"] = fetch_script_config(timeout=t["cfg"])
    snap["rest"] = fetch_rest(timeout=t["rest"]) if not no_rest else {"scripts": {}, "users": {}, "queues": {}, "emulator": {}, "ok": False}
    return snap


# ---------------------------------------------------------------- 构图

def _slot_of(path: str) -> str:
    p = str(path or "").replace("\\\\", "\\").rstrip("\\/")
    return os.path.basename(p) if p else "-"


def build_doc(snap: dict, baseline: dict | None = None) -> dict:
    """把快照（+可选基线）加工成映射文档（含 findings / changes）。纯函数，便于离线自测。"""
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    instances = {str(k): v for k, v in (snap.get("instances") or {}).items()}
    vmm = {str(k): v for k, v in (snap.get("vmm") or {}).items()}
    slots = {str(k): v for k, v in (snap.get("slots") or {}).items()}
    lc = {str(k): v for k, v in (snap.get("last_connect") or {}).items()}
    adb = list(snap.get("adb") or [])
    rest = snap.get("rest") or {}
    cfg = {str(k): v for k, v in (snap.get("script_config") or {}).items()}
    rscripts = {str(k): v for k, v in (rest.get("scripts") or {}).items()}
    rqueues = rest.get("queues") or {}
    remu = {str(k): v for k, v in (rest.get("emulator") or {}).items()}
    minfo = {str(k): v for k, v in (snap.get("mumu_info") or {}).items()}

    findings: list = []

    def find(level: str, code: str, subject: str, detail: str) -> None:
        findings.append({"level": level, "code": code, "subject": subject, "detail": detail})

    # ---- 实例表（实测）
    inst_rows = []
    port_owner: dict = {}
    for idx in sorted(instances, key=lambda s: int(s)):
        i = instances[idx]
        port = i.get("port")
        exp, fam = expected_port(int(idx))
        running = idx in vmm
        listen = (vmm.get(idx) or {}).get("ports") or []
        adb_states = [a["state"] for a in adb if a.get("port") == port]
        formula_ok = (port == exp) if port else None
        inst_rows.append({
            "idx": idx, "title": i.get("title"), "port": port, "port_raw": i.get("port_raw"),
            "family": fam, "formula_port": exp, "formula_ok": formula_ok,
            "secondary_port": secondary_port(int(idx)),
            "vm_config_mtime": i.get("vm_config_mtime"), "created": i.get("created"),
            "running": running, "vmm_pid": (vmm.get(idx) or {}).get("pid"), "listen_ports": listen,
            "adb_state": adb_states[0] if adb_states else None,
            "mumu_info": {"name": (minfo.get(idx) or {}).get("name"),
                          "is_process_started": (minfo.get(idx) or {}).get("is_process_started")},
        })
        if port:
            port_owner.setdefault(int(port), []).append(idx)
        if port and formula_ok is False:
            find("RED", "PORT_FORMULA_MISMATCH", "idx%s %s" % (idx, i.get("title") or "?"),
                 "实测 adb 口 %s ≠ 公式家族 %s 期望 %s（文档/脚本里按公式写死的口会连不上）" % (port, fam, exp))
        if port and port == 7555:
            find("INFO", "PORT_7555", "idx%s" % idx, "实例挂在 7555（MuMu 默认口遗留）；主口=%s，勿依赖 7555" % port)

    for port, owners in port_owner.items():
        if len(owners) > 1:
            find("RED", "PORT_COLLISION", "port %d" % port, "多个实例同口：idx %s" % ", ".join(owners))

    adb_ports = set(a["port"] for a in adb if a.get("port"))
    for idx in sorted(vmm, key=lambda s: int(s)):
        i = instances.get(idx)
        if not i:
            find("WARN", "VMM_UNKNOWN_INSTANCE", "idx%s" % idx, "VMM 进程在跑，但 vms 目录没有对应实例")
            continue
        if i.get("port") and i["port"] not in adb_ports:
            find("WARN", "RUNNING_NOT_IN_ADB", "idx%s %s" % (idx, i.get("title") or "?"),
                 "VMM 在跑（端口 %s）但 adb devices 里没有它（adb server 可能没连；执行前先 adb connect 复核）" % i["port"])

    known_ports = set()
    for idx, i in instances.items():
        if i.get("port"):
            known_ports.add(int(i["port"]))
        known_ports.add(secondary_port(int(idx)))
    known_ports.add(7555)
    for a in adb:
        if a.get("port") and a["port"] not in known_ports:
            find("WARN", "ADB_STALE_ENTRY", a["serial"], "adb devices 里的条目不对应任何实例端口（陈旧登记）")
        elif a.get("state") not in ("device", None):
            find("INFO", "ADB_OFFLINE", a["serial"], "adb 状态=%s（未就绪/陈旧）" % a.get("state"))

    # ---- 脚本表（ScriptConfig 为主、REST 为对照）
    all_sids = set(cfg) | set(rscripts)
    sid_to_queues: dict = {}
    queue_rows = []
    for qid, qv in rqueues.items():
        name = str(qv.get("name") or "")
        for sid in qv.get("sids") or []:
            sid_to_queues.setdefault(sid, []).append(name)
        queue_rows.append({"qid": qid[:8], "name": name, "kind": queue_kind(name),
                           "n_items": len(qv.get("sids") or []),
                           "sids": [s[:8] for s in (qv.get("sids") or [])]})

    for sid in all_sids:
        c, r = cfg.get(sid) or {}, rscripts.get(sid) or {}
        if c and r:
            for fld in ("name", "idx"):
                if str(c.get(fld) or "") and str(r.get(fld) or "") and str(c.get(fld)) != str(r.get(fld)):
                    find("RED", "CONFIG_REST_DIVERGENCE", "%s (sid %s)" % (c.get("name") or r.get("name") or "?", sid[:8]),
                         "ScriptConfig.%s=%r ≠ REST.%s=%r" % (fld, c.get(fld), fld, r.get(fld)))
            if _slot_of(c.get("path")) != _slot_of(r.get("path")):
                find("RED", "CONFIG_REST_DIVERGENCE", (c.get("name") or "?"),
                     "MAA 目录不一致：ScriptConfig=%s ≠ REST=%s" % (_slot_of(c.get("path")), _slot_of(r.get("path"))))

    rows = []
    idx_owner: dict = {}
    for sid in all_sids:
        c, r = cfg.get(sid) or {}, rscripts.get(sid) or {}
        name = str(c.get("name") or r.get("name") or "")
        path = str(c.get("path") or r.get("path") or "")
        slot = _slot_of(path)
        idx = str(c.get("idx") or r.get("idx") or "")
        users = c.get("users")
        if users is None:
            users = (rest.get("users") or {}).get(sid) or []
        inst = instances.get(idx)
        port = inst.get("port") if inst else None
        flags = []
        if not idx.isdigit():
            flags.append("NO_IDX")
        if idx and not inst:
            flags.append("INDEX_MISSING")
            find("RED", "SCRIPT_IDX_MISSING", "%s (sid %s)" % (name or "?", sid[:8]),
                 "配置指向实例 idx%s，但 vms 目录没有该实例（幽灵绑定：排到必全程失败）" % idx)
        if idx and inst:
            idx_owner.setdefault(idx, []).append(name or sid[:8])
            exp, fam = expected_port(int(idx))
            if port and port != exp:
                flags.append("PORT_FORMULA_MISMATCH")
            title = inst.get("title") or ""
            if title and name and title != name:
                flags.append("标题≠脚本名")
        n_enabled = sum(1 for u in users if u.get("enabled"))
        if users and n_enabled == 0:
            flags.append("无启用账号")
            find("WARN", "NO_ENABLED_USER", name or sid[:8], "该脚本 %d 个账号全部停用（排到即空转）" % len(users))
        qnames = sid_to_queues.get(sid) or []
        if len(qnames) > 1 and any("补跑" in q for q in qnames):
            pass
        if (n_enabled > 0) and not qnames:
            find("WARN", "SCRIPT_NO_QUEUE", name or sid[:8], "有启用账号但不在任何队列里（不会自动发车）")
        rows.append({
            "slot": slot, "script": name, "sid": sid, "idx": idx if idx.isdigit() else (idx or None),
            "title": (inst or {}).get("title"), "port": port,
            "serial": ("127.0.0.1:%d" % port) if port else None,
            "port_source": "measured:vm_config" if port else None,
            "family": expected_port(int(idx))[1] if idx.isdigit() else None,
            "accounts_total": len(users), "accounts_enabled": n_enabled,
            "users": [{"login": u.get("login"), "server": u.get("server"), "enabled": bool(u.get("enabled"))} for u in users],
            "queues": sorted(set(qnames)),
            "flags": flags,
        })

    for idx, owners in idx_owner.items():
        if len(owners) > 1:
            find("RED", "IDX_DUPLICATE", "idx%s" % idx, "多个脚本指同一实例：%s" % ", ".join(owners))

    rows.sort(key=lambda r: (0 if r["slot"].startswith("MAA_") else 1,
                             int(r["slot"].split("_")[1]) if r["slot"].startswith("MAA_") and r["slot"].split("_")[1].isdigit() else 99,
                             int(r["idx"]) if (r["idx"] or "").isdigit() else 999))

    # ---- 队列结构校验
    for qid, qv in rqueues.items():
        sids = qv.get("sids") or []
        slots_in = set()
        for sid in sids:
            c, r = cfg.get(sid) or {}, rscripts.get(sid) or {}
            slots_in.add(_slot_of(c.get("path") or r.get("path")))
        if len([s for s in slots_in if s and s != "-"]) > 1:
            find("RED", "QUEUE_SLOT_SPLIT", str(qv.get("name")),
                 "队列项横跨多个 MAA 目录：%s（同槽同刻一个任务，横跨=互杀面）" % ", ".join(sorted(slots_in)))
    pair_names = {}
    for qv in rqueues.values():
        n = str(qv.get("name") or "")
        m = re.match(r"^([日常晚班])定时(\d+)$", n)
        if m:
            pair_names.setdefault(m.group(2), {})[m.group(1)] = set(
                _slot_of((cfg.get(sid) or rscripts.get(sid) or {}).get("path") or "") for sid in (qv.get("sids") or []))
    for num, sides in sorted(pair_names.items(), key=lambda kv: int(kv[0])):
        if len(sides) == 2:
            a, b = sides.get("日常", set()), sides.get("晚班", set())
            if a != b:
                find("WARN", "QUEUE_PAIR_MISMATCH", "定时%s" % num,
                     "日常/晚班脚本集不一致：日常=%s 晚班=%s" % (sorted(a), sorted(b)))

    # ---- 槽位表（记忆地址 vs 实测 vs 最近连接）——只认 MAA_N 目录；MAA-Multi 下的
    #      externals/Python/Res/resource 等不是槽位，直接跳过（不是问题件）。
    slot_rows = []
    maa_slots = [s for s in slots if re.match(r"^MAA_\d+$", s)]
    for slot in sorted(maa_slots, key=lambda s: (0, int(s.split("_")[1])) if s.startswith("MAA_") and s.split("_")[1].isdigit() else (1, 0)):
        s = slots[slot]
        mine = [r for r in rows if r["slot"] == slot]
        mine_ports = set(r["port"] for r in mine if r["port"])
        mine_ports |= set(secondary_port(int(r["idx"])) for r in mine if (r["idx"] or "").isdigit())
        row = {"slot": slot, "address": s.get("address"), "address_port": s.get("address_port"),
               "instance_index": s.get("instance_index"), "auto_detect": s.get("auto_detect"),
               "run_directly": s.get("run_directly"), "file_mtime": s.get("file_mtime"),
               "n_scripts": len(mine), "scripts": [r["script"] for r in mine]}
        if s.get("bad"):
            find("WARN", "SLOT_CONFIG_UNREADABLE", slot, "gui.new.json 读不到/坏（%s）" % s.get("reason"))
            row["memory_ok"] = None
        elif s.get("address_port") and mine_ports:
            ok = s["address_port"] in mine_ports
            row["memory_ok"] = ok
            if not ok:
                find("WARN", "SLOT_MEMORY_OFF_SLOT", slot,
                     "槽位记忆地址 %s 不对应本槽任何脚本实例的口（本槽口集 %s）——跨轮残留或端口已漂"
                     % (s.get("address"), sorted(mine_ports)))
        l = lc.get(slot)
        if l:
            row["last_connect"] = l
            if mine_ports:
                ok = l["port"] in mine_ports
                row["last_connect_ok"] = ok
                if not ok:
                    others = [r for r in rows if r["port"] == l["port"]]
                    find("RED", "SLOT_LASTCONNECT_OFF_SLOT", slot,
                         "MAA 最近实际连接 %s（%s）不属于本槽脚本实例口集 %s%s"
                         % (l["address"], l.get("ts"), sorted(mine_ports),
                            "；该口属于 %s" % others[0]["script"] if others else ""))
        slot_rows.append(row)

    # ---- REST emulator 对照：adb_address 只会在 {主口, 副口} 两族里（实测语义：
    #      在跑实例报主口、停着的报副口 5555+2·idx），落在这两族外才值得标红。
    for idx, e in sorted(remu.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 999):
        if not str(idx).isdigit():
            continue
        i = instances.get(str(idx))
        addr = str(e.get("adb_address") or "")
        m = SERIAL_RE.match(addr)
        sec = secondary_port(int(idx))
        if i is None:
            find("INFO", "REST_EMU_UNKNOWN", "idx%s" % idx, "REST 里有该实例但 vms 目录没有（REST=%s）" % addr)
            continue
        i["rest_adb_address"] = addr or None
        for _ir in inst_rows:
            if _ir["idx"] == str(idx):
                _ir["rest_adb_address"] = addr or None
                break
        if m:
            p = int(m.group(1))
            fam = "主口(实测)" if p == i.get("port") else ("副口" if p == sec else "其它")
            i["rest_addr_family"] = fam
            for _ir in inst_rows:
                if _ir["idx"] == str(idx):
                    _ir["rest_addr_family"] = fam
                    break
            if fam == "其它":
                find("WARN", "REST_ADDR_OFF_FAMILY", "idx%s %s" % (idx, e.get("title") or "?"),
                     "REST adb_address=%s 既不是实测主口 %s 也不是副口 %d（两族之外的值，值得注意）"
                     % (addr, i.get("port"), sec))
        if e.get("title") and i.get("title") and str(e["title"]) != str(i["title"]):
            find("WARN", "REST_TITLE_MISMATCH", "idx%s" % idx,
                 "REST title=%s ≠ 实测 title=%s" % (e.get("title"), i.get("title")))

    # ---- MuMuManager 状态面（只作旁证）
    lie = 0
    for idx, mi in minfo.items():
        if idx in vmm and mi.get("is_process_started") is False:
            lie += 1
    if lie:
        find("INFO", "MUMU_STATE_LIE", "MuMuManager info -v",
             "%d 个实例 VMM 明明在跑，info.is_process_started 仍报 false（状态面假死，别用它判死活）" % lie)

    # ---- 与基线比对（端口漂移史）
    changes = []
    if baseline:
        b = baseline.get("instances") or []
        b_ports = {str(x.get("idx")): x.get("port") for x in b}
        for ir in inst_rows:
            old = b_ports.get(ir["idx"])
            if old is not None and ir.get("port") is not None and old != ir["port"]:
                changes.append({"kind": "port_changed", "idx": ir["idx"], "title": ir.get("title"),
                                "from": old, "to": ir["port"], "baseline_built_at": baseline.get("built_at")})
                find("RED", "PORT_DRIFT", "idx%s %s" % (ir["idx"], ir.get("title") or "?"),
                     "端口漂移：%s → %s（基线 %s）——文档/脚本里写死的口已失效，执行前必须现查"
                     % (old, ir["port"], baseline.get("built_at")))
        b_set = set(b_ports)
        n_set = set(x["idx"] for x in inst_rows)
        for gone in sorted(b_set - n_set, key=lambda s: int(s)):
            changes.append({"kind": "instance_gone", "idx": gone})
        for new in sorted(n_set - b_set, key=lambda s: int(s)):
            changes.append({"kind": "instance_new", "idx": new})

    doc = {
        "schema": "maaorch.mapping/1",
        "built_at": now,
        "sources": {
            "ssh": SSH_HOST, "rest": API,
            "instances": r"%s\MuMuPlayer-12.0-<idx>\configs\vm_config.json (adb.host_port) + extra_config.json (title)" % MUMU_VMS,
            "vmm": "Win32_Process MuMuVMMHeadless + Get-NetTCPConnection",
            "adb": "%s devices" % ADB_EXE,
            "slots": r"%s\MAA_N\config\gui.new.json (Gui.ConnectSettings.Address)" % MAA_ROOT,
            "last_connect": r"%s\MAA_N\debug\asst.log 末段 \"what\":\"Connected\"" % MAA_ROOT,
            "config": SCRIPT_CONFIG, "mumu_info": MUMU_MANAGER,
        },
        "port_rules": {
            "family_a": "idx<=31: 16384+32*idx",
            "family_b": "idx>=32: 16388+32*(idx-32)",
            "secondary_runtime": "5555+2*idx（运行时；AUTO-MAS REST adb_address 停着的实例用这一族）",
            "rest_semantics": "AUTO-MAS /api/emulator/status.adb_address：在跑实例报主口、停着的报副口（2026-09-28 实测 2 主口 + 53 副口）",
            "trust_order": ["adb devices（在跑）", "vm_config host_port（实测持久化）", "VMM 监听口", "公式（仅基准，已被 idx27 打破）", "文档（可能过期）"],
        },
        "counts": {
            "instances": len(inst_rows), "scripts": len(rows),
            "slots": len([s for s in slot_rows if s["n_scripts"] or s.get("address")]),
            "queues": len(queue_rows),
            "running": sum(1 for i in inst_rows if i["running"]),
            "red": sum(1 for f in findings if f["level"] == "RED"),
            "warn": sum(1 for f in findings if f["level"] == "WARN"),
            "info": sum(1 for f in findings if f["level"] == "INFO"),
            "changes": len(changes),
        },
        "instances": inst_rows,
        "slots": slot_rows,
        "rows": rows,
        "queues": sorted(queue_rows, key=lambda q: (queue_sort_key(q["name"]), q["name"])),
        "adb_devices": adb,
        "unbound_instances": [{"idx": i["idx"], "title": i["title"], "port": i["port"]}
                              for i in inst_rows if i["idx"] not in set(r["idx"] for r in rows if r["idx"])],
        "ghosts": [{"script": r["script"], "idx": r["idx"], "slot": r["slot"]}
                   for r in rows if "INDEX_MISSING" in r["flags"]],
        "findings": findings,
        "changes": changes,
    }
    return doc


def queue_kind(name: str) -> str:
    if re.match(r"^([日常晚班])定时\d+$", name or ""):
        return "定时"
    if "补跑" in (name or ""):
        return "补跑残留"
    return "其它"


def queue_sort_key(name: str) -> tuple:
    m = re.match(r"^([日常晚班])定时(\d+)$", name or "")
    if m:
        return (0 if m.group(1) == "日常" else 1, int(m.group(2)))
    return (2, 0)


# ---------------------------------------------------------------- 渲染

def render_md(doc: dict) -> str:
    L = []
    c = doc["counts"]
    L.append("# 权威映射表：MAA 槽位 ↔ 脚本 ↔ 实例 idx ↔ adb 串口（实测）")
    L.append("")
    L.append("> 生成：`tools/mas_mapping.py --build`（只读；不启动/不关闭实例、不改配置）｜时间：%s" % doc["built_at"])
    L.append("> 机器可读同源产物：`runtime/reports/mapping.json`（schema `%s`）；校验：`tools/mas_mapping.py --verify`" % doc["schema"])
    L.append("")
    L.append("实例 %d（在跑 %d）｜脚本 %d｜槽位 %d｜队列 %d｜**RED %d / WARN %d / INFO %d**｜较基线变化 %d"
             % (c["instances"], c["running"], c["scripts"], c["slots"], c["queues"], c["red"], c["warn"], c["info"], c["changes"]))
    L.append("")
    L.append("端口口径：家族 A `16384+32·idx`（idx≤31）｜家族 B `16388+32·(idx−32)`（idx≥32）｜副口 `5555+2·idx`（运行时）。")
    L.append("**端口会漂，实测为准**——表格里的「adb 串口」列是本次实测值（vm_config，在跑实例与 VMM 监听口互证）。")
    L.append("")
    if doc["findings"]:
        L.append("## 0. 标红件（RED 必须先看）")
        L.append("")
        for f in doc["findings"]:
            L.append("- **[%s] %s** ｜ %s —— %s" % (f["level"], f["code"], f["subject"], f["detail"]))
        L.append("")
    if doc["changes"]:
        L.append("## 0.1 较上次建表的变化")
        L.append("")
        for ch in doc["changes"]:
            if ch["kind"] == "port_changed":
                L.append("- 端口漂移：idx%s %s：%s → **%s**（基线 %s）" % (ch["idx"], ch.get("title") or "", ch["from"], ch["to"], ch.get("baseline_built_at")))
            else:
                L.append("- %s：idx%s" % (ch["kind"], ch["idx"]))
        L.append("")

    L.append("## 1. 主表（脚本维度；按槽位排序）")
    L.append("")
    L.append("| MAA 槽位 | 脚本 | 实例 idx | 实例标题 | 当前 adb 串口（实测） | 公式 | 账号数(启用/总) | 队列 | 备注 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in doc["rows"]:
        formula = "✓" if (r["idx"] or "").isdigit() and r["port"] == expected_port(int(r["idx"]))[0] else "✗"
        if not (r["idx"] or "").isdigit():
            formula = "?"
        note = "、".join(r["flags"]) or "—"
        L.append("| %s | %s | %s | %s | `%s` | %s | %d/%d | %s | %s |"
                 % (r["slot"], r["script"], r["idx"] or "-", r["title"] or "-", r["serial"] or "-",
                    formula, r["accounts_enabled"], r["accounts_total"],
                    "、".join(r["queues"]) or "—", note))
    L.append("")

    L.append("## 2. 槽位表（记忆地址 vs 实测 vs 最近实际连接）")
    L.append("")
    L.append("| 槽位 | 脚本数 | gui.new.json 记忆地址 | 记忆∈本槽口集 | asst.log 最近连接 | 最近连接时间 | 最近连接∈本槽口集 | 注入 RunDirectly/AutoDetect |")
    L.append("|---|---|---|---|---|---|---|---|")
    for s in doc["slots"]:
        l = s.get("last_connect") or {}
        mem_ok = {True: "✓", False: "✗", None: "?"}.get(s.get("memory_ok"), "?")
        lc_ok = {True: "✓", False: "✗", None: "-"}.get(s.get("last_connect_ok"), "-")
        L.append("| %s | %d | %s | %s | %s | %s | %s | %s / %s |"
                 % (s["slot"], s["n_scripts"], s.get("address") or "-", mem_ok,
                    l.get("address") or "-", l.get("ts") or "-", lc_ok,
                    s.get("run_directly") or "-", s.get("auto_detect") or "-"))
    L.append("")

    L.append("## 3. 实例表（全部 %d 台，实测口 + 公式吻合 + 运行态）" % len(doc["instances"]))
    L.append("")
    L.append("| idx | 标题 | adb 主口（实测） | 公式 | 家族 | 副口 | REST 地址 | 在跑 | VMM 监听口 | adb 状态 | vm_config mtime | 有无脚本 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    bound = set(r["idx"] for r in doc["rows"] if r["idx"])
    for i in doc["instances"]:
        ok = {True: "✓", False: "**✗**", None: "?"}[i["formula_ok"]]
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                 % (i["idx"], i["title"] or "-", i["port"] or "-", ok, i["family"], i["secondary_port"],
                    i.get("rest_adb_address") or "-",
                    "在跑" if i["running"] else "—", ",".join(str(p) for p in i["listen_ports"]) or "-",
                    i["adb_state"] or "-", i["vm_config_mtime"] or "-", "有" if i["idx"] in bound else "**无（在册外）**"))
    L.append("")

    L.append("## 4. 队列（%d 条：定时 %d / 补跑残留 %d / 其它 %d）"
             % (len(doc["queues"]),
                sum(1 for q in doc["queues"] if q["kind"] == "定时"),
                sum(1 for q in doc["queues"] if q["kind"] == "补跑残留"),
                sum(1 for q in doc["queues"] if q["kind"] == "其它")))
    L.append("")
    L.append("| 队列 | 类型 | 项数 | 脚本（sid 前8） |")
    L.append("|---|---|---|---|")
    for q in doc["queues"]:
        L.append("| %s | %s | %d | %s |" % (q["name"], q["kind"], q["n_items"], ", ".join(q["sids"]) or "—"))
    L.append("")
    if doc["ghosts"]:
        L.append("## 5. 幽灵绑定（指向不存在实例的脚本）")
        L.append("")
        for g in doc["ghosts"]:
            L.append("- %s → idx%s（槽位 %s）" % (g["script"], g["idx"], g["slot"]))
        L.append("")
    if doc["unbound_instances"]:
        L.append("## 6. 在册外实例（有实例、无脚本）")
        L.append("")
        for u in doc["unbound_instances"]:
            L.append("- idx%s %s（口 %s）" % (u["idx"], u["title"] or "-", u["port"] or "-"))
        L.append("")

    L.append("## 7. 复现与口径")
    L.append("")
    L.append("```")
    L.append("python tools/mas_mapping.py --build     # 重建本表（只读）")
    L.append("python tools/mas_mapping.py --verify    # 校验（末行 MAPPING_VERIFY=OK|DRIFT:<n>|UNKNOWN）")
    L.append("python tools/mas_mapping.py --port 27   # 单实例现查口")
    L.append("```")
    L.append("")
    L.append("- 实测口 = `vm_config.json` 的 `vm.nat.port_forward.adb.host_port`；在跑实例与 VMM 监听口互证；adb 状态来自 `adb devices`。")
    L.append("- 槽位记忆 = `E:\\MAA-Multi\\MAA_N\\config\\gui.new.json` 的 `Gui.ConnectSettings.Address`（最近一次注入值，**不代表当前**）。")
    L.append("- 最近连接 = `asst.log` 末段最后一条 `\"what\":\"Connected\"`（MAA 真的连上的口，**这是最强证据**）。")
    L.append("- 本文件不含凭据：password/token/secret/Id 字段在读入即丢弃。")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 库接口

def load_mapping(path: str | Path | None = None) -> dict:
    """读上一次 --build 的产物；没有则 {}。"""
    p = Path(path) if path else OUT_JSON
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def serial_for(doc: dict | None = None, idx=None, script: str | None = None, slot: str | None = None) -> dict:
    """从映射 doc 里取 adb 串口（离线）。→ {serial, port, idx, script, slot, source, found}"""
    doc = doc or load_mapping()
    got = {"found": False, "serial": None, "port": None, "idx": None, "script": None, "slot": None,
           "source": doc.get("built_at") and "mapping@%s" % doc["built_at"]}
    if not doc:
        return got
    idx = str(idx) if idx is not None else None
    for r in doc.get("rows") or []:
        if (idx and str(r.get("idx")) == idx) or (script and r.get("script") == script) or (slot and r.get("slot") == slot):
            got.update({"found": True, "serial": r.get("serial"), "port": r.get("port"),
                        "idx": r.get("idx"), "script": r.get("script"), "slot": r.get("slot")})
            return got
    for i in doc.get("instances") or []:
        if idx and str(i.get("idx")) == idx:
            port = i.get("port")
            got.update({"found": bool(port), "serial": ("127.0.0.1:%d" % port) if port else None,
                        "port": port, "idx": i.get("idx"), "slot": None, "script": None})
            return got
    return got


def live_instance(idx, timeouts: dict | None = None) -> dict:
    """单实例**现查**（ssh 一次拿 vm_config 口 + VMM 监听口 + adb 状态）。→ 给需要现查的工具用。"""
    idx = str(idx)
    t = {"instances": 120, "vmm": 120, "adb": 90}
    t.update(timeouts or {})
    ip = INSTANCES_PS
    vp = VMM_PS
    out = {"idx": idx, "port": None, "title": None, "listen_ports": [], "running": False, "adb_state": None}
    for ln in ssh_ps(ip, timeout=t["instances"]).splitlines():
        parts = ln.strip().split("|")
        if len(parts) >= 6 and parts[0] == "INST" and parts[1].strip() == idx:
            out["title"] = parts[2].strip() or None
            out["port"] = int(parts[3]) if parts[3].strip().isdigit() else None
    for ln in ssh_ps(vp, timeout=t["vmm"]).splitlines():
        parts = ln.strip().split("|")
        if len(parts) >= 4 and parts[0] == "VMM" and parts[1].strip() == idx:
            out["running"] = True
            out["listen_ports"] = [int(p) for p in parts[3].split(",") if p.strip().isdigit()]
            out["vmm_pid"] = parts[2].strip()
    for a in fetch_adb(timeout=t["adb"]):
        if a.get("port") and out["port"] and a["port"] == out["port"]:
            out["adb_state"] = a["state"]
    best = None
    if out["running"] and out["listen_ports"]:
        cand = [p for p in out["listen_ports"] if p not in (7555,)]
        if out["port"] in cand:
            best = out["port"]
        elif cand:
            best = sorted(cand)[0]
    elif out["port"]:
        best = out["port"]
    out["serial"] = ("127.0.0.1:%d" % best) if best else None
    return out


# ---------------------------------------------------------------- 打磨：CLI

def cmd_build(a) -> int:
    snap = _snap_from_args(a)
    if snap is None:
        return 2
    baseline = load_mapping(a.baseline or OUT_JSON) if not a.no_baseline else {}
    doc = build_doc(snap, baseline or None)
    out_json = Path(a.out_json) if a.out_json else OUT_JSON
    out_md = Path(a.out_md) if a.out_md else OUT_MD
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(render_md(doc), encoding="utf-8")
    if a.json:
        print(json.dumps({k: doc[k] for k in ("schema", "built_at", "counts", "findings", "changes")}, ensure_ascii=False, indent=1))
    else:
        c = doc["counts"]
        log("已写 %s" % out_json)
        log("已写 %s" % out_md)
        print("MAPPING_BUILD=OK scripts=%d instances=%d slots=%d queues=%d running=%d RED=%d WARN=%d INFO=%d changes=%d"
              % (c["scripts"], c["instances"], c["slots"], c["queues"], c["running"], c["red"], c["warn"], c["info"], c["changes"]))
        for f in doc["findings"]:
            if f["level"] == "RED":
                print("  [RED] %-24s %s —— %s" % (f["code"], f["subject"], f["detail"]))
    return 0


def cmd_verify(a) -> int:
    snap = _snap_from_args(a)
    if snap is None:
        print("MAPPING_VERIFY=UNKNOWN")
        return 2
    baseline = load_mapping(a.baseline or OUT_JSON) if not a.no_baseline else {}
    doc = build_doc(snap, baseline or None)
    if a.json:
        print(json.dumps({"built_at": doc["built_at"], "counts": doc["counts"],
                          "findings": doc["findings"], "changes": doc["changes"]}, ensure_ascii=False, indent=1))
    else:
        c = doc["counts"]
        print("实测快照 %s（host %s）｜实例 %d（在跑 %d）｜脚本 %d｜RED %d / WARN %d / INFO %d｜较基线变化 %d"
              % (doc["built_at"], snap.get("host"), c["instances"], c["running"], c["scripts"], c["red"], c["warn"], c["info"], c["changes"]))
        for lv in ("RED", "WARN", "INFO"):
            for f in doc["findings"]:
                if f["level"] == lv:
                    print("  [%-4s] %-26s %s\n         %s" % (lv, f["code"], f["subject"], f["detail"]))
        if doc["changes"]:
            print("  较基线变化：")
            for ch in doc["changes"]:
                if ch["kind"] == "port_changed":
                    print("    idx%s %s: %s → %s" % (ch["idx"], ch.get("title") or "", ch["from"], ch["to"]))
                else:
                    print("    %s: idx%s" % (ch["kind"], ch["idx"]))
    red = doc["counts"]["red"]
    print("MAPPING_VERIFY=%s" % ("OK" if red == 0 else "DRIFT:%d" % red))
    return 0 if red == 0 else 1


def cmd_port(a) -> int:
    """--port IDX：现查（实测优先，基线兜底）。"""
    try:
        live = live_instance(a.port, timeouts={"instances": 120, "vmm": 120, "adb": 90})
    except Exception as e:  # noqa: BLE001
        live = {"idx": str(a.port), "serial": None, "err": str(e)[:200]}
    if live.get("serial"):
        if a.json:
            print(json.dumps(live, ensure_ascii=False, indent=1))
        else:
            print("%s" % live["serial"])
        return 0
    got = serial_for(idx=a.port)
    if got.get("found"):
        if a.json:
            print(json.dumps(got, ensure_ascii=False, indent=1))
        else:
            print(got["serial"])
        return 0
    log("取不到 idx%s 的口（VMM/adb/vm_config 都没有，映射基线里也没有）" % a.port)
    print("MAPPING_PORT=UNKNOWN")
    return 2


def cmd_lookup(a) -> int:
    doc = load_mapping(a.baseline or OUT_JSON)
    if not doc:
        log("没有映射基线（先跑 --build）")
        return 2
    key = a.lookup
    hit = [r for r in doc["rows"] if r["script"] == key or r["slot"] == key or str(r["idx"]) == key]
    if a.json:
        print(json.dumps(hit, ensure_ascii=False, indent=1))
    else:
        for r in hit:
            print("%-8s %-22s idx%-3s %-14s %-18s 账号 %d/%d  队列 %s  %s"
                  % (r["slot"], r["script"], r["idx"], r["title"] or "-", r["serial"] or "-",
                     r["accounts_enabled"], r["accounts_total"], "、".join(r["queues"]) or "—",
                     "、".join(r["flags"])))
    return 0 if hit else 2


def _snap_from_args(a) -> dict | None:
    if a.offline:
        try:
            return json.loads(Path(a.offline).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log("读 fixture 失败：%s" % e)
            return None
    try:
        log("现取：ssh %s（实例/VMM/槽位/asst.log/adb/MuMuManager/ScriptConfig）+ REST %s …" % (SSH_HOST, API))
        return fetch_live(no_rest=a.no_rest)
    except Exception as e:  # noqa: BLE001
        log("取数失败：%s" % e)
        return None


# ---------------------------------------------------------------- 自测

def _fixture() -> dict:
    """离线 fixture：刻意埋 4 类问题（端口漂移 / 幽灵绑定 / 槽位记忆不一致 / 队列横跨）各一。"""
    inst = {}
    for idx, (name, port, mt) in {
        13: ("明日方舟-官-1", 16800, "2026-09-28 09:10:02"),
        14: ("明日方舟-官-2", 16832, "2026-09-28 09:10:18"),
        27: ("明日方舟-官-12", 17249, "2026-09-28 07:57:24"),   # 公式 17248（漂了 +1）
        43: ("明日方舟-官-24", 16740, "2026-09-28 05:39:45"),
        52: ("l-7-不要加班了", 17028, "2026-09-28 02:46:02"),
    }.items():
        inst[str(idx)] = {"idx": str(idx), "title": name, "port": port, "port_raw": str(port),
                          "vm_config_mtime": mt, "created": "1768973112070090"}
    return {
        "fetched_at": "2026-09-28 10:00:00", "host": "Administrator@100.79.173.69",
        "instances": inst,
        "vmm": {"13": {"pid": 1234, "ports": [16800, 5581]},
                "27": {"pid": 2345, "ports": [17249, 5609]}},
        "slots": {
            "MAA_6": {"address": "127.0.0.1:16800", "address_port": 16800, "instance_index": "1",
                      "auto_detect": "False", "run_directly": "True", "file_mtime": "2026-09-28 09:11:00", "bad": False},
            "MAA_12": {"address": "127.0.0.1:17248", "address_port": 17248, "instance_index": "1",
                       "auto_detect": "False", "run_directly": "True", "file_mtime": "2026-09-28 07:00:00", "bad": False},
        },
        "last_connect": {
            "MAA_6": {"ts": "2026-09-28 09:13:42.631", "address": "127.0.0.1:16800", "port": 16800},
            "MAA_12": {"ts": "2026-09-28 08:05:14.973", "address": "127.0.0.1:17249", "port": 17249},
        },
        "adb": [{"serial": "127.0.0.1:16800", "port": 16800, "state": "device"},
                {"serial": "127.0.0.1:17249", "port": 17249, "state": "device"},
                {"serial": "127.0.0.1:9999", "port": 9999, "state": "offline"}],
        "mumu_info": {"13": {"name": "明日方舟-官-1", "is_process_started": False},
                      "27": {"name": "明日方舟-官-12", "is_process_started": False}},
        "script_config": {
            "sid-6": {"name": "明日方舟-官-1-1-7", "path": r"E:\MAA-Multi\MAA_6", "idx": "13",
                      "users": [{"login": "汐影月k", "server": "Official", "enabled": True},
                                {"login": "潮响", "server": "Official", "enabled": True},
                                {"login": "him", "server": "Official", "enabled": True},
                                {"login": "tbNick", "server": "Official", "enabled": True}]},
            "sid-12": {"name": "明日方舟-官-12", "path": r"E:\MAA-Multi\MAA_12", "idx": "27",
                       "users": [{"login": "烤箱", "server": "Official", "enabled": True}]},
            "sid-7": {"name": "l-7-不要加班了", "path": r"E:\\MAA-Multi\\MAA_12", "idx": "52",
                      "users": [{"login": "不要加班了", "server": "YoStarJP", "enabled": True}]},
            "sid-28": {"name": "明日方舟-官-28", "path": r"E:\MAA-Multi\MAA_8", "idx": "51",
                       "users": [{"login": "hbznn119-官", "server": "Official", "enabled": True}]},
            "sid-5": {"name": "明日方舟-官-5", "path": r"E:\MAA-Multi\MAA_4", "idx": "19",
                      "users": [{"login": "v", "server": "Official", "enabled": False}]},
        },
        "rest": {
            "ok": True,
            "scripts": {"sid-6": {"name": "明日方舟-官-1-1-7", "path": r"E:\MAA-Multi\MAA_6", "idx": "13"},
                        "sid-12": {"name": "明日方舟-官-12", "path": r"E:\MAA-Multi\MAA_12", "idx": "27"},
                        "sid-7": {"name": "l-7-不要加班了", "path": r"E:\\MAA-Multi\\MAA_12", "idx": "52"},
                        "sid-28": {"name": "明日方舟-官-28", "path": r"E:\MAA-Multi\MAA_8", "idx": "51"},
                        "sid-5": {"name": "明日方舟-官-5", "path": r"E:\MAA-Multi\MAA_4", "idx": "19"}},
            "queues": {
                "q6": {"name": "日常定时6", "sids": ["sid-6"], "time_enabled": True, "startup_enabled": False},
                "q12": {"name": "晚班定时12", "sids": ["sid-12", "sid-7"], "time_enabled": True, "startup_enabled": False},
                "qx": {"name": "临时-补跑-0928_0100-1", "sids": ["sid-6"], "time_enabled": False, "startup_enabled": False},
            },
            "emulator": {"13": {"title": "明日方舟-官-1", "status": 1, "adb_address": "127.0.0.1:5581"},
                         "27": {"title": "明日方舟-官-12", "status": 1, "adb_address": "127.0.0.1:5609"}},
        },
    }


def selftest() -> int:
    fails = 0

    def ck(cond, what):
        nonlocal fails
        print(("  ok   " if cond else "  FAIL ") + what)
        fails += 0 if cond else 1

    print("自测 1：公式与端口族")
    ck(expected_port(0) == (16384, "A"), "idx0 → 16384 家族 A")
    ck(expected_port(31) == (17376, "A"), "idx31 → 17376 家族 A")
    ck(expected_port(32) == (16388, "B"), "idx32 → 16388 家族 B")
    ck(expected_port(60) == (17284, "B"), "idx60 → 17284 家族 B")
    ck(secondary_port(13) == 5581 and secondary_port(27) == 5609, "副口 5555+2*idx")

    print("自测 2：构图 + 标红（端口漂移）")
    doc = build_doc(_fixture())
    codes = [f["code"] for f in doc["findings"]]
    ck("PORT_FORMULA_MISMATCH" in codes, "idx27 17249 ≠ 公式 17248 ⇒ RED")
    ck("SCRIPT_IDX_MISSING" in codes, "官-28 → idx51 幽灵绑定 ⇒ RED")
    ck("SLOT_MEMORY_OFF_SLOT" in codes, "MAA_12 记忆地址 17248 不在本槽口集 ⇒ WARN")
    ck("SLOT_LASTCONNECT_OFF_SLOT" not in codes, "MAA_12 最近连接 17249 属于本槽 ⇒ 不报")
    ck("ADB_STALE_ENTRY" in codes, "adb 9999 不对应任何实例端口 ⇒ WARN")
    ck("QUEUE_SLOT_SPLIT" not in codes, "晚班定时12 两项同槽（双反斜杠路径也能归一）⇒ 不报")
    ck("NO_ENABLED_USER" in codes, "官-5 账号全停用 ⇒ WARN")
    ck("MUMU_STATE_LIE" in codes, "MuMuManager 状态面假死 ⇒ INFO")
    row6 = [r for r in doc["rows"] if r["script"] == "明日方舟-官-1-1-7"][0]
    ck(row6["serial"] == "127.0.0.1:16800" and row6["accounts_total"] == 4, "官-1-1-7 行：串口+账号数")
    ck(serial_for(doc, idx=27)["serial"] == "127.0.0.1:17249", "serial_for(idx=27) 用实测口")
    ck(serial_for(doc, script="l-7-不要加班了")["slot"] == "MAA_12", "双反斜杠路径归一 → 槽位 MAA_12")

    print("自测 3：基线比对（端口漂移记史）")
    base = {"built_at": "2026-09-27 21:26:00",
            "instances": [{"idx": "27", "port": 17248}, {"idx": "13", "port": 16800}]}
    doc2 = build_doc(_fixture(), base)
    ch = [c for c in doc2["changes"] if c["kind"] == "port_changed"]
    ck(any(c["idx"] == "27" and c["from"] == 17248 and c["to"] == 17249 for c in ch), "27: 17248→17249 记入 changes")
    ck("PORT_DRIFT" in [f["code"] for f in doc2["findings"]], "漂移 ⇒ RED")

    print("自测 4：md 渲染 + 凭据剔除")
    md = render_md(doc)
    ck("| MAA_6 |" in md and "当前 adb 串口（实测）" in md, "md 主表存在")
    ck(render_md(doc2).count("端口漂移") >= 1, "md 变化节存在")
    s = json.dumps(scrub_keys({"Info": {"Name": "x", "Password": "BLOB", "Id": "13800000000", "SklandToken": "t",
                                        "Server": "Official"}}), ensure_ascii=False)
    ck("BLOB" not in s and "13800000000" not in s and "Skland" not in s and "Official" in s, "凭据/手机号键被剔除")

    print("自测：%s（失败 %d）" % ("全绿" if fails == 0 else "有失败", fails))
    return 0 if fails == 0 else 3


def main() -> int:
    ap = argparse.ArgumentParser(description="权威映射表：MAA 槽位↔脚本↔实例↔adb 口（实测）↔账号↔队列；只读")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--build", action="store_true", help="建表 → runtime/reports/mapping.{json,md}")
    g.add_argument("--verify", action="store_true", help="校验（不写文件），rc 0/1/2")
    g.add_argument("--port", metavar="IDX", help="现查某实例的 adb 串口（实测优先，基线兜底）")
    g.add_argument("--lookup", metavar="KEY", help="按脚本名/槽位/idx 查一行")
    g.add_argument("--selftest", action="store_true", help="离线自测（不联网）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--offline", metavar="FIXTURE", help="用本地快照 JSON 跑（不联网）")
    ap.add_argument("--no-rest", action="store_true", help="跳过 REST（只用 ssh 面）")
    ap.add_argument("--no-baseline", action="store_true", help="不与基线比对")
    ap.add_argument("--baseline", help="基线 mapping.json 路径（默认 runtime/reports/mapping.json）")
    ap.add_argument("--out-json", help="--build 的 JSON 输出路径")
    ap.add_argument("--out-md", help="--build 的 md 输出路径")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.build:
        return cmd_build(a)
    if a.verify:
        return cmd_verify(a)
    if a.port:
        return cmd_port(a)
    if a.lookup:
        return cmd_lookup(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
