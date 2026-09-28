#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""班次前 GO/NO-GO 自检（发车前 5 分钟）——只读。

把「能不能发」变成一条命令 / 一行结论：

    python tools/mas_preflight.py --queue 晚班定时1
    python tools/mas_preflight.py --script 明日方舟-官-1-1-7 --json
    python tools/mas_preflight.py --queue 晚班定时1 --skip-screen

末行输出 `PREFLIGHT=GO` 或 `PREFLIGHT=NO-GO`（--json 时输出结构化 JSON）。

检查项（任一 FAIL ⇒ NO-GO；WARN 只提示不阻断）：
  1. REST      AUTO-MAS 可达（POST /api/scripts/get code=200）
  2. 槽位空闲  Win32_Process 中无目标 MAA 目录的 MAA.exe（忙 = 与同目录脚本互杀）
  3. 模拟器    VMM 进程 → 监听端口 → adb get-state（在跑实例必须可连；
               未开实例只做「可 launch」可行性判定，绝不启动）
               ※ 不用 `MuMuManager adb --vmindex`：实测它在健康实例上也会误报
                 -201「vm not running」（状态面假死），改走 VMM 进程定位链
  3L 实例租约  目标实例是否已被别的任务租下（`runtime/instance_leases.json`，跨族单飞，
               t_1df99ae5 接入）：**别人占着 ⇒ FAIL**（换实例 / 等 / 走 hold，别发出去互相踩）；
               自己的租约（`MAAORCH_LEASE_OWNER` / `HERMES_KANBAN_TASK` == 持有者）⇒ PASS
               —— 自己的发车链跑预检不会拦自己。纯本地账本读，不发车、不写账本。
  4. 残留模式  在跑实例截图 + OCR（登录被取消=FAIL；更新/公告/网络 等=WARN；
               判据来源：kb_maaerr（残留模态会让 StartUp 必然失败，实例 13 实锤））
  5. 注入闸门  目标 MAA 目录 gui.new.json 官方 masked+true / 非官方 ""+false
               （复用 tools/mas_switch_gate.py 的判定；VIOLATION/UNKNOWN 均 FAIL）
  6. 磁盘水位 主机 C/E 剩余（默认 <10GB FAIL、<30GB WARN）
  7. 客户端版本 目标实例 ARK 包 versionName/lastUpdateTime 与基线比对（t_634e7685，
               复用 tools/interrupt_guard.py）：变化 ⇒ FAIL（建议暂停该实例发车，
               避免半途更新打断）；首次见（无基线）/不可读 ⇒ WARN；目标全未运行 ⇒ SKIP
  8. 宿主重启 计划重启任务临近/落班次窗口 + 待重启标记 + 在飞 MAA（t_634e7685）：
               ≤15 分钟即刻重启 / ≤45 分钟且宿主在飞 MAA>0 ⇒ FAIL；
               待重启标记、重启落班次窗口、WU 完成重启通道启用 ⇒ WARN（窗口建议只出不改）

约束：只读——不启动/不关闭实例、不改任何配置、不发车。
截图只落本地 --shot-dir（默认 runtime/preflight_shots）。

环境变量：
  MAAORCH_MAS_API               REST 地址，默认 http://100.79.173.69:36163
  MAAORCH_SSH_KEY               默认 ~\.ssh\maaorch_target
  MAAORCH_SSH_HOST              默认 Administrator@100.79.173.69
  MAAORCH_MUMUMANAGER           默认 E:\MuMu Player 12\nx_main\MuMuManager.exe
  MAAORCH_ADB                   默认 E:\MuMu Player 12\shell\adb.exe
  MAAORCH_PREFLIGHT_SKIP_SCREEN 1/true/on = 跳过第 4 项（等价 --skip-screen）
  MAAORCH_LEASE_OWNER           第 3L 项判定「自己的租约」的持有者 id（缺省取
                                HERMES_KANBAN_TASK；两者都没有 ⇒ 任何在租都算被占）

退出码：0=GO；1=NO-GO；2=无法判定/参数错误。
OCR 依赖：Python312 + rapidocr_onnxruntime（OCR 不可用时第 4 项 FAIL —— 显式
--skip-screen 才算跳过，fail-closed）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
MUMUMANAGER = os.environ.get("MAAORCH_MUMUMANAGER", r"E:\MuMu Player 12\nx_main\MuMuManager.exe")
ADB = os.environ.get("MAAORCH_ADB", r"E:\MuMu Player 12\shell\adb.exe")
SKIP_SCREEN_ENV = str(os.environ.get("MAAORCH_PREFLIGHT_SKIP_SCREEN", "")).strip().lower() in {
    "1", "true", "yes", "on", "enable", "enabled"}

MAA_DIR_RE = re.compile(r"^[A-Za-z]:[\\/]MAA-Multi[\\/]MAA_\d+$", re.I)
MASK_RE = re.compile(r"^\d{3}\*{4}\d{4}$")
PHONE_RE = re.compile(r"^\d{11}$")
VMM_RE = re.compile(r"MuMuPlayer-12\.0-(\d+)(?:\D|$)")
FOCUS_RE = re.compile(r"mCurrentFocus=Window\{[^}]*\s([A-Za-z0-9_.]+)/")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------- 关键词表（第 4 项判据）
# BLOCK = 已知会让 StartUp 必然失败的残留模态（kb_maaerr 实测：实例 13「登录被取消，请重试」）
# WARN  = 需要人工看一眼但不硬阻断（首页按钮「查看公告」等会被排除）
BLOCK_KW = [
    (r"登(?:录|入)被取消|登(?:录|入)已取消", "登录被取消模态（已知会让 StartUp 必然失败）"),
]
WARN_KW = [
    (r"发现新版本|立即更新|下载更新|正在更新|版本过低", "疑似客户端更新提示"),
    (r"维护|停机", "疑似停机维护"),
    (r"连接失败|网络异常|重新连接|失去连接|连接中断|重新登录", "疑似网络/登录异常"),
    (r"公告", "『公告』字样（标题页按钮也会命中，非弹窗可忽略）"),
]
BENIGN_LINE_RE = re.compile(r"查看公告|开始唤醒|扫码登录|账号管理|TAP TO START|TAP TO CONTINUE", re.I)


def norm_dir(p) -> str:
    """目录归一：折叠 \\\\ 双分隔符、去尾部分隔符。"""
    s = str(p or "").strip()
    s = re.sub(r"[\\/]+", "\\\\", s)
    return s.rstrip("\\")


def post(path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body if body is not None else {}).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------------------------------------------------------------- ssh 辅助

def _ssh_base() -> list:
    return ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20", SSH_HOST]


def ssh_ps(script: str, timeout: int = 240) -> str:
    """stdin 喂 PowerShell（避免多层引号；实测最稳）。"""
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    cmd = _ssh_base() + ["chcp 65001 >nul & powershell -NoProfile -Command -"]
    r = subprocess.run(cmd, input=(prefix + script).encode("utf-8"),
                       capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def ssh_cmd(cmdline: str, timeout: int = 90) -> str:
    r = subprocess.run(_ssh_base() + [cmdline], capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def ssh_bytes(cmdline: str, timeout: int = 90) -> bytes:
    r = subprocess.run(_ssh_base() + [cmdline], capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout


# ---------------------------------------------------------------- 巡检面（远端只读）

_SURVEY_PS = r"""
$maa = @(Get-CimInstance Win32_Process -Filter "Name='MAA.exe'")
foreach ($p in $maa) { Write-Output ("MAAPROC|" + $p.ProcessId + "|" + [string]$p.ExecutablePath + "|" + [string]$p.CommandLine) }
$vmm = @(Get-CimInstance Win32_Process -Filter "Name='MuMuVMMHeadless.exe'")
foreach ($p in $vmm) { Write-Output ("VMMPROC|" + $p.ProcessId + "|" + [string]$p.CommandLine) }
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object { Write-Output ("DISK|" + $_.DeviceID + "|" + [math]::Round($_.FreeSpace/1GB,2) + "|" + [math]::Round($_.Size/1GB,2)) }
Write-Output ("TESTPATH|mm|" + (Test-Path '__MM__'))
Write-Output ("TESTPATH|adb|" + (Test-Path '__ADB__'))
Write-Output ("SVC|" + @(Get-Process -Name MuMuVMMSVC -ErrorAction SilentlyContinue).Count)
Write-Output "SURVEY-END"
"""


def remote_survey(timeout: int = 240) -> dict:
    """一次 ssh 拿：MAA 进程表 + VMM 进程表 + 磁盘 + 关键路径存在性 + VMM 服务。"""
    ps = _SURVEY_PS.replace("__MM__", MUMUMANAGER).replace("__ADB__", ADB)
    out = ssh_ps(ps, timeout=timeout)
    if "SURVEY-END" not in out:
        raise RuntimeError("远端巡检脚本无输出（ssh 不通 / PowerShell 异常）")
    survey = {"maa": [], "vmm": {}, "disks": {}, "paths": {}, "svc_vmm": None}
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("MAAPROC|"):
            _, pid, exe, cmd = (ln.split("|", 3) + ["", "", ""])[:4]
            survey["maa"].append({"pid": pid.strip(), "exe": norm_dir(exe), "cmd": cmd.strip()})
        elif ln.startswith("VMMPROC|"):
            _, pid, cmd = (ln.split("|", 2) + ["", ""])[:3]
            m = VMM_RE.search(cmd)
            if m:
                survey["vmm"][m.group(1)] = pid.strip()
        elif ln.startswith("DISK|"):
            _, dev, free, size = ln.split("|")[:4]
            try:
                survey["disks"][dev.strip().upper()] = (float(free), float(size))
            except ValueError:
                pass
        elif ln.startswith("TESTPATH|"):
            _, k, v = ln.split("|")[:3]
            survey["paths"][k] = (v.strip().lower() == "true")
        elif ln.startswith("SVC|"):
            try:
                survey["svc_vmm"] = int(ln.split("|")[1])
            except (ValueError, IndexError):
                pass
    return survey


def netstat_ports(timeout: int = 90) -> dict:
    """{pid(int): [port(int), ...]}（仅 127.0.0.1 LISTENING）。"""
    out = ssh_cmd("netstat -ano", timeout=timeout)
    res: dict = {}
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING" and parts[1].startswith("127.0.0.1"):
            try:
                pid = int(parts[4])
                port = int(parts[1].rsplit(":", 1)[-1])
            except ValueError:
                continue
            res.setdefault(pid, []).append(port)
    return res


# ---------------------------------------------------------------- 实例检查（第 3 项）

def instance_adb_probe(ports: list, timeout: int = 60) -> dict:
    """给候选端口逐个 adb connect + get-state + getprop，返回第一个可用端点。"""
    cands = sorted([p for p in ports if p != 7555], key=int)  # 7555 为共享伪口，剔除
    for p in cands[:3]:
        seq = ('"%s" connect 127.0.0.1:%d & "%s" -s 127.0.0.1:%d get-state & '
               '"%s" -s 127.0.0.1:%d shell getprop sys.boot_completed') % (ADB, p, ADB, p, ADB, p)
        try:
            out = ssh_cmd(seq, timeout=timeout)
        except Exception:  # noqa: BLE001
            continue
        lines = [x.strip() for x in out.replace("\r", "").splitlines() if x.strip()]
        state_ok = any(x == "device" for x in lines)
        if not state_ok:
            continue
        boot = ""
        for x in reversed(lines):
            if x in ("0", "1"):
                boot = x
                break
        return {"serial": "127.0.0.1:%d" % p, "port": p, "boot": boot}
    return {}


def mumu_info(idxs: list, timeout: int = 120) -> dict:
    """MuMuManager info -v <idx,...> → {idx: info_dict}（只读）。"""
    if not idxs:
        return {}
    out = ssh_cmd('"%s" info -v %s' % (MUMUMANAGER, ",".join(str(i) for i in idxs)), timeout=timeout)
    try:
        data = json.loads(out)
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(data, dict) and "index" in data:
        return {str(data.get("index")): data}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    return {}


# ---------------------------------------------------------------- 画面检查（第 4 项）

_OCR = None


def ocr_texts(png_path: str) -> list:
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415
        _OCR = RapidOCR()
    res, _ = _OCR(png_path)
    out = []
    for item in res or []:
        try:
            text = str(item[1]).strip()
            score = float(item[2])
        except Exception:  # noqa: BLE001
            continue
        if text and score >= 0.5:
            out.append((text, score))
    return out


def classify_residual(lines: list) -> dict:
    """OCR 行 → {block:[(行,原因)], warn:[...]}"""
    block, warn = [], []
    for text, _score in lines:
        benign = bool(BENIGN_LINE_RE.search(text))
        for rx, why in BLOCK_KW:
            if re.search(rx, text):
                block.append((text, why))
                break
        if not benign:
            for rx, why in WARN_KW:
                if re.search(rx, text):
                    warn.append((text, why))
                    break
    return {"block": block, "warn": warn}


def foreground_pkg(serial: str, timeout: int = 60) -> str:
    out = ssh_cmd('"%s" -s %s shell "dumpsys window | grep mCurrentFocus"' % (ADB, serial), timeout=timeout)
    m = FOCUS_RE.search(out)
    return m.group(1) if m else ""


LAUNCHER_HINTS = ("launcher", "lawnchair", "trebuchet", "nexuslauncher", "miui.home", "oneplus.launcher")


def pkg_class(pkg: str) -> str:
    if not pkg:
        return "unknown"
    low = pkg.lower()
    if any(h in low for h in LAUNCHER_HINTS) or "mumu.store" in low:
        return "launcher"
    if "arknights" in low or "hypergryph" in low or "yostar" in low:
        return "game"
    return "other"


def screencap(serial: str, outdir: str, tag: str, timeout: int = 90) -> str:
    os.makedirs(outdir, exist_ok=True)
    for attempt in (0, 1):
        try:
            b = ssh_bytes('cmd /S /c ""%s" -s %s exec-out screencap -p"' % (ADB, serial), timeout=timeout)
        except Exception:  # noqa: BLE001
            b = b""
        if len(b) > 10000 and b.startswith(PNG_MAGIC):
            path = os.path.join(outdir, "%s_%s.png" % (dt.datetime.now().strftime("%Y%m%d_%H%M%S"), tag))
            with open(path, "wb") as f:
                f.write(b)
            return path
        time.sleep(1.0)
    return ""


# ---------------------------------------------------------------- 注入闸门（第 5 项）

def gate_module():
    """导入同目录的 mas_switch_gate（复用其判定；缺失则 None）。"""
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    try:
        import mas_switch_gate  # noqa: PLC0415
        return mas_switch_gate
    except Exception:  # noqa: BLE001
        return None


def gate_meta_for(targets: list) -> dict:
    """{dir_lower: {"servers": set, "masks": set, "odd_official_id_lens": set}}（账号侧只读）。"""
    meta: dict = {}
    for t in targets:
        d = t["dir"].lower()
        m = meta.setdefault(d, {"servers": set(), "masks": set(), "odd_official_id_lens": set(),
                                "scripts": [t["name"]]})
        try:
            users = (post("/api/scripts/user/get", {"scriptId": t["sid"]}).get("data") or {})
        except Exception:  # noqa: BLE001
            users = {}
        for _uid, u in users.items():
            ui = (u or {}).get("Info") or {}
            srv, idv = ui.get("Server"), str(ui.get("Id") or "")
            if srv:
                m["servers"].add(str(srv))
            if srv == "Official":
                if PHONE_RE.fullmatch(idv):
                    m["masks"].add(idv[:3] + "****" + idv[7:])
                elif idv:
                    m["odd_official_id_lens"].add(len(idv))
    return meta


# ---------------------------------------------------------------- 打断源（第 7/8 项，t_634e7685）

def guard_module():
    """导入同目录的 interrupt_guard（复用其检测；缺失则 None）。"""
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    try:
        import interrupt_guard  # noqa: PLC0415
        return interrupt_guard
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 目标解析

class TargetError(Exception):
    """目标名不存在/不唯一（用法问题，退出码 2）。"""


def reachable() -> tuple:
    try:
        r = post("/api/scripts/get", {}, timeout=15)
        return (r.get("code") == 200), "code=%s" % r.get("code")
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


def fetch_scripts() -> dict:
    """{sid: {"name","path","emu","sid"}}；路径不合格的脚本跳过。"""
    data = (post("/api/scripts/get", {}).get("data") or {})
    out = {}
    for sid, s in data.items():
        info = (s or {}).get("Info") or {}
        p = norm_dir(info.get("Path"))
        if not MAA_DIR_RE.match(p):
            continue
        emu = ((s or {}).get("Emulator") or {}).get("Index")
        out[str(sid)] = {"sid": str(sid), "name": str(info.get("Name") or ""), "path": p,
                         "dir": p, "emu": str(emu) if emu is not None else ""}
    return out


def resolve_targets(a) -> dict:
    scripts = fetch_scripts()
    if not scripts:
        raise RuntimeError("REST 可达但脚本列表为空（异常）")
    if a.script:
        hit = [s for s in scripts.values() if s["name"] == a.script]
        if not hit:
            raise TargetError("找不到脚本 %r（共 %d 个）" % (a.script, len(scripts)))
        if len(hit) > 1:
            raise TargetError("脚本名 %r 不唯一（%d 个），请用 --queue" % (a.script, len(hit)))
        return {"kind": "script", "name": a.script, "targets": hit}
    # queue
    queues = (post("/api/queue/get", {"queueId": None}).get("data") or {})
    quid = None
    for uid, v in queues.items():
        if ((v or {}).get("Info") or {}).get("Name") == a.queue:
            quid = uid
            break
    if quid is None:
        names = sorted(((v or {}).get("Info") or {}).get("Name") or "" for v in queues.values())
        raise TargetError("找不到队列 %r；现有：%s" % (a.queue, ", ".join(x for x in names if x)))
    items = (post("/api/queue/item/get", {"queueId": quid}).get("data") or {})
    targets = []
    for _iid, v in items.items():
        sid = ((v or {}).get("Info") or {}).get("ScriptId")
        s = scripts.get(str(sid))
        if s:
            targets.append(s)
    if not targets:
        raise RuntimeError("队列 %r 无有效脚本项" % a.queue)
    return {"kind": "queue", "name": a.queue, "queue_id": quid, "targets": targets}


# ---------------------------------------------------------------- 汇总

def _st(items, key="status"):
    return [i for i in items if i.get(key) == "FAIL"]


def lease_check(emus, owner_id=None) -> dict:
    """第 3L 项：目标实例是否已被别的任务租下（`runtime/instance_leases.json`；跨族单飞闸门）。

    * 别人占着 ⇒ FAIL（fail-closed：发车前就该换实例 / 等，而不是发出去互相踩）；
    * 自己的租约（`MAAORCH_LEASE_OWNER` / `HERMES_KANBAN_TASK` == 持有者）⇒ PASS
      —— 自己的发车链（run_profile / ondemand）在预检里不会拦自己；
    * 目标没实例号 / 租约工具不在位 ⇒ SKIP + 说明（不硌主流程）；
    * 判不了（账本读异常）⇒ FAIL（与其余项同口径：fail-closed）。

    明细逐字沿用 `tools/mas_instance_lease.py` 的输出（含持有者卡号与到期时间）。
    只读：不 acquire / 不 release / 不发车。
    """
    try:
        import mas_lease_glue as LG  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return {"status": "SKIP", "per_idx": {},
                "detail": "mas_lease_glue 不可导入（%s: %s）——跳过" % (type(e).__name__, e)}
    idxs = [str(i) for i in (emus or [])]
    if not idxs:
        return {"status": "SKIP", "per_idx": {}, "detail": "目标无实例（脚本没有 Emulator.Index）——跳过"}
    r = LG.check_all(idxs, owner_id=(owner_id if owner_id is not None else LG.owner_any()))
    per = {i: {"status": v.get("status"), "detail": v.get("detail"), "holder": v.get("holder")}
           for i, v in (r.get("rows") or {}).items()}
    if r.get("status") == "unavailable":
        return {"status": "SKIP", "per_idx": per, "detail": str(r.get("detail") or "租约工具不在位——跳过")}
    if r.get("ok"):
        return {"status": "PASS", "per_idx": per, "host": r.get("host"),
                "detail": LG.detail_of(r.get("rows") or {}, idxs) or "无目标实例"}
    out = {"status": "FAIL", "per_idx": per, "host": r.get("host"),
           "holder": LG.holder_of(r.get("rows") or {}),
           "detail": LG.detail_of(r.get("rows") or {}, idxs)}
    return out


def run_preflight(a) -> dict:
    started = dt.datetime.now()
    res = {"tool": "mas_preflight", "generated_at": started.strftime("%Y-%m-%d %H:%M:%S"),
           "api": API, "target": None, "checks": {}, "reasons": [], "preflight": "NO-GO"}

    # --- 第 1 项：REST 可达
    ok, why = reachable()
    if not ok:
        res["checks"]["rest"] = {"status": "FAIL", "detail": "AUTO-MAS 不可达：%s" % why}
        res["reasons"].append("AUTO-MAS REST 不可达（%s）" % why)
        return res
    res["checks"]["rest"] = {"status": "PASS", "detail": "POST /api/scripts/get code=200 @%s" % API}

    # --- 目标解析
    res["target"] = resolve_targets(a)  # TargetError 上抛给 main（退出码 2）

    tg = res["target"]["targets"]
    dirs = sorted({t["dir"] for t in tg}, key=lambda x: (len(x), x))
    emus = sorted({t["emu"] for t in tg if t["emu"]}, key=lambda x: int(x))
    res["target"]["dirs"], res["target"]["emus"] = dirs, emus
    nm = "队列 %s" % res["target"]["name"] if res["target"]["kind"] == "queue" else "脚本 %s" % res["target"]["name"]
    tg_desc = "%s → %d 脚本 / %d 目录 / %d 实例" % (nm, len(tg), len(dirs), len(emus))

    # --- 远端巡检（第 2/3/6 项的前置）
    try:
        survey = remote_survey(timeout=a.timeout)
        ssh_ok, ssh_err = True, ""
    except Exception as e:  # noqa: BLE001
        survey, ssh_ok, ssh_err = None, False, "%s: %s" % (type(e).__name__, e)

    # --- 第 2 项：槽位空闲
    slot_items = []
    if not ssh_ok:
        res["checks"]["slot_idle"] = {"status": "FAIL", "detail": "远端不可达：%s" % ssh_err, "per_dir": {}}
        res["reasons"].append("槽位检查失败：远端 ssh 不可达（%s）" % ssh_err)
    else:
        maa_by_dir = {}
        for m in survey["maa"]:
            maa_by_dir.setdefault(m["exe"].lower(), []).append(m)
        per = {}
        for d in dirs:
            dl = d.lower()
            busy = []
            for exe, procs in maa_by_dir.items():
                if exe == dl or exe.startswith(dl + "\\"):
                    busy.extend(procs)
            per[d] = {"busy": bool(busy), "procs": [{"pid": p["pid"]} for p in busy]}
            slot_items.append({"dir": d, "busy": bool(busy), "procs": [p["pid"] for p in busy]})
        st = "FAIL" if any(x["busy"] for x in slot_items) else "PASS"
        detail = "; ".join("%s 在跑(pid %s)" % (x["dir"], ",".join(x["procs"])) for x in slot_items if x["busy"]) \
            or "目标目录均无 MAA.exe 在跑"
        res["checks"]["slot_idle"] = {"status": st, "per_dir": per, "detail": detail}
        if st == "FAIL":
            res["reasons"].append("槽位忙：" + detail)
        # 附带信息：其他槽位在跑什么（不阻断）
        other = sorted({m["exe"] for m in survey["maa"]
                        if not any(m["exe"].lower() == d.lower() or m["exe"].lower().startswith(d.lower() + "\\")
                                   for d in dirs)})
        res["checks"]["slot_idle"]["other_busy"] = other

    # --- 第 3/4 项：逐实例
    emu_rows, resid_rows = {}, {}
    if not ssh_ok:
        res["checks"]["emulator"] = {"status": "FAIL", "detail": "远端不可达：%s" % ssh_err, "per_idx": {}}
        res["checks"]["residual"] = {"status": "SKIP", "detail": "远端不可达", "per_idx": {}}
        res["reasons"].append("实例检查失败：远端 ssh 不可达")
    else:
        try:
            ports_map = netstat_ports()
        except Exception:  # noqa: BLE001
            ports_map = {}
        off_idxs = [i for i in emus if i not in survey["vmm"]]
        info = mumu_info(off_idxs) if off_idxs else {}
        skip_screen = a.skip_screen or SKIP_SCREEN_ENV
        ocr_err = ""
        # 第一遍：连通性（第 3 项）
        for i in emus:
            row = {"idx": i, "running": i in survey["vmm"]}
            if row["running"]:
                ports = ports_map.get(int(survey["vmm"][i]), [])
                row["ports"] = sorted(ports)
                probe = instance_adb_probe(ports)
                if probe:
                    row.update({"adb": "ok", **probe})
                else:
                    row.update({"adb": "fail", "detail": "VMM 在跑但 adb 连不上（端口漂移/卡死）"})
            else:
                ii = info.get(str(i)) or {}
                if ii and str(ii.get("index")) == str(i) and int(ii.get("error_code", 1)) == 0:
                    row.update({"adb": "off", "launch_ok": True, "name": ii.get("name"),
                                "detail": "未运行；launch 可行性 OK（MuMuManager 在位、实例有效）——不自启"})
                else:
                    row.update({"adb": "off", "launch_ok": False,
                                "detail": "实例 idx 在 MuMuManager 无有效记录（不保证能 launch）"})
            emu_rows[i] = row
        emu_fail = [r for r in emu_rows.values() if r.get("adb") == "fail" or (r.get("adb") == "off" and not r.get("launch_ok"))]
        res["checks"]["emulator"] = {
            "status": "FAIL" if emu_fail else "PASS", "per_idx": emu_rows,
            "detail": "; ".join("idx%s %s" % (r["idx"], r.get("detail") or ("UP %s boot=%s" % (r.get("serial"), r.get("boot"))))
                                for r in emu_rows.values()) or "无目标实例"}
        if emu_fail:
            res["reasons"].append("模拟器：" + "; ".join("idx%s(%s)" % (r["idx"], r.get("detail")) for r in emu_fail))

        # 第二遍：残留画面（第 4 项）——只查在跑实例；槽位在跑的跳过（已 NO-GO）
        if skip_screen:
            res["checks"]["residual"] = {"status": "SKIP", "detail": "显式 --skip-screen", "per_idx": {}}
        else:
            try:
                import rapidocr_onnxruntime  # noqa: F401,PLC0415
            except Exception as e:  # noqa: BLE001
                ocr_err = "OCR 不可用（%s）——用 Python312+rapidocr 或显式 --skip-screen" % e
            busy_dirs = {x["dir"] for x in slot_items if x["busy"]}
            resid_fail, resid_warn = [], []
            for i in emus:
                row = emu_rows[i]
                if row.get("adb") != "ok":
                    resid_rows[i] = {"status": "SKIP", "detail": "实例未运行/不可连，无残留可查"}
                    continue
                d = next((t["dir"] for t in tg if t["emu"] == i), "")
                if d in busy_dirs:
                    resid_rows[i] = {"status": "SKIP", "detail": "槽位 %s 在跑（已 NO-GO），跳过截图" % os.path.basename(d)}
                    continue
                if ocr_err:
                    resid_rows[i] = {"status": "FAIL", "detail": ocr_err}
                    resid_fail.append(i)
                    continue
                tag = "idx%s_%s" % (i, os.path.basename(d) or "slot")
                shot = screencap(row["serial"], a.shot_dir, tag, timeout=a.timeout)
                fg = foreground_pkg(row["serial"], timeout=a.timeout)
                entry = {"status": "PASS", "shot": shot, "foreground": fg, "pkg_class": pkg_class(fg),
                         "block": [], "warn": [], "detail": ""}
                if not shot:
                    entry.update(status="FAIL", detail="截图取证失败（screencap 无有效 PNG）")
                    resid_fail.append(i)
                else:
                    try:
                        lines = ocr_texts(shot)
                    except Exception as e:  # noqa: BLE001
                        lines = []
                        entry["ocr_error"] = "%s: %s" % (type(e).__name__, e)
                    hits = classify_residual(lines)
                    entry["block"], entry["warn"] = hits["block"], hits["warn"]
                    entry["ocr_sample"] = [t for t, _ in lines[:12]]
                    if hits["block"]:
                        entry.update(status="FAIL", detail="检测到残留模态：%s" % "; ".join(t for t, _ in hits["block"]))
                        resid_fail.append(i)
                    elif hits["warn"]:
                        entry.update(status="WARN", detail="可疑字样：%s" % "; ".join(t for t, _ in hits["warn"]))
                        resid_warn.append(i)
                    elif entry["pkg_class"] == "launcher":
                        entry.update(status="WARN", detail="停在桌面（游戏未在前台）——若依赖已开客户端请先处理")
                        resid_warn.append(i)
                    else:
                        entry["detail"] = "未见残留关键词（前台=%s）" % (fg or "?")
                resid_rows[i] = entry
            st = "FAIL" if resid_fail else ("WARN" if resid_warn else "PASS")
            checked = {i: r for i, r in resid_rows.items() if r.get("status") != "SKIP"}
            if not checked and not resid_fail:
                st = "SKIP"
            res["checks"]["residual"] = {"status": st, "per_idx": resid_rows,
                                         "detail": ("; ".join("idx%s: %s" % (i, r.get("detail"))
                                                              for i, r in checked.items()) or "无在跑实例可查")}
            if resid_fail:
                res["reasons"].append("残留画面：" + "; ".join("idx%s(%s)" % (i, resid_rows[i].get("detail")) for i in resid_fail))

    # --- 第 3L 项：实例租约（跨族单飞；纯本地账本读，无网络）
    res["checks"]["lease"] = lease_check(emus)
    if res["checks"]["lease"].get("status") == "FAIL":
        res["reasons"].append("实例租约：" + str(res["checks"]["lease"].get("detail") or ""))

    # --- 第 5 项：注入闸门
    G = gate_module()
    if G is None:
        res["checks"]["config_gate"] = {"status": "FAIL", "detail": "mas_switch_gate.py 不可导入（需与其同目录）", "per_dir": {}}
        res["reasons"].append("注入闸门：脚本缺失")
    else:
        try:
            meta = gate_meta_for(tg)
            infos = G.read_dirs(dirs)
            per, bad = {}, []
            for d, fi in zip(dirs, infos):
                ev = G.evaluate(meta.get(d.lower(), {}), fi)
                per[d] = {"verdict": ev.get("verdict"), "server": ev.get("server"), "account": ev.get("account"),
                          "switch": ev.get("switch"), "issues": ev.get("issues"), "notes": ev.get("notes")}
                if ev.get("verdict") != "OK":
                    bad.append(d)
            st = "FAIL" if bad else "PASS"
            res["checks"]["config_gate"] = {"status": st, "per_dir": per,
                                            "detail": "; ".join(
                                                "%s %s" % (d, per[d]["verdict"]) for d in dirs) or "无目录"}
            if bad:
                res["reasons"].append("注入闸门：" + "; ".join(
                    "%s=%s(%s)" % (d, per[d]["verdict"], ",".join(per[d]["issues"] or [])) for d in bad))
        except Exception as e:  # noqa: BLE001
            res["checks"]["config_gate"] = {"status": "FAIL", "detail": "%s: %s" % (type(e).__name__, e), "per_dir": {}}
            res["reasons"].append("注入闸门判定失败：%s" % e)

    # --- 第 6 项：磁盘
    if not ssh_ok:
        res["checks"]["disk"] = {"status": "FAIL", "detail": "远端不可达：%s" % ssh_err, "drives": {}}
    else:
        drives, st, det = survey["disks"], "PASS", []
        for dev in ("C:", "E:"):
            if dev not in drives:
                det.append("%s 未知" % dev)
                st = "WARN" if st == "PASS" else st
                continue
            free, size = drives[dev]
            if free < a.min_free_gb:
                st = "FAIL"
                det.append("%s %.1fGB (<%.0f FAIL)" % (dev, free, a.min_free_gb))
            elif free < a.warn_free_gb:
                st = "WARN" if st != "FAIL" else st
                det.append("%s %.1fGB (<%.0f WARN)" % (dev, free, a.warn_free_gb))
            else:
                det.append("%s %.1fGB" % (dev, free))
        res["checks"]["disk"] = {"status": st, "drives": {k: {"free_gb": v[0], "size_gb": v[1]} for k, v in drives.items()},
                                 "detail": "; ".join(det)}
        if st == "FAIL":
            res["reasons"].append("磁盘水位：" + "; ".join(det))

    # --- 第 7 项：客户端版本（打断源①：游戏强更/整包更新；t_634e7685）
    IG = guard_module()
    if IG is None:
        res["checks"]["client_version"] = {"status": "FAIL", "detail": "interrupt_guard.py 不可导入（需与其同目录）", "per_idx": {}}
        res["reasons"].append("客户端版本：脚本缺失")
    else:
        try:
            vres = IG.version_check(idxs=[int(i) for i in emus], maxn=max(6, len(emus)), timeout=a.timeout)
            cmp = vres["compare"]
            off = [i for i in emus if not any(str(r["idx"]) == str(i) for r in cmp["rows"])]
            per = {str(c["idx"]): {"pkg": c["pkg"], "versionName": c.get("versionName"),
                                   "lastUpdateTime": c.get("lastUpdateTime"), "status": c["status"]}
                   for c in cmp["rows"]}
            changed, new, unread = cmp["changed"], cmp["new"], cmp["unreadable"]
            if changed:
                st = "FAIL"
                det = "; ".join("idx%s %s %s→%s（lastUpdate %s）" % (
                    c["idx"], c["pkg"], (c.get("baseline") or {}).get("versionName"), c.get("versionName"),
                    c.get("lastUpdateTime")) for c in changed)
                res["reasons"].append("客户端版本变化 —— 建议暂停该实例发车（人工确认可正常进游戏后再 "
                                      "interrupt_guard.py --record 刷新基线）：" + det)
            elif not cmp["rows"]:
                st = "SKIP"
                det = "目标 %d 台实例均未运行（无版本可读，不阻断）" % len(emus)
            elif new or unread:
                st = "WARN"
                det = "; ".join(["idx%s %s 无基线" % (n["idx"], n["pkg"]) for n in new]
                                + ["idx%s 不可读(%s)" % (u.get("idx"), u.get("reason")) for u in unread]
                                + (["idx%s 未运行" % i for i in off] if off else []))
                if new:
                    res["reasons"].append("客户端版本：%d 项无基线（首次见到；建议 interrupt_guard.py --record 建基线）" % len(new))
            else:
                st = "PASS"
                det = "; ".join("idx%s %s=%s" % (r["idx"], r["pkg"], r["versionName"]) for r in cmp["same"])
                if off:
                    det += "; 未运行 idx%s" % ",".join(off)
            res["checks"]["client_version"] = {"status": st, "detail": det, "per_idx": per}
        except Exception as e:  # noqa: BLE001
            res["checks"]["client_version"] = {"status": "FAIL", "detail": "%s: %s" % (type(e).__name__, e), "per_idx": {}}
            res["reasons"].append("客户端版本检测失败：%s" % e)

    # --- 第 8 项：宿主重启（打断源②：计划重启/待重启标记/在飞；t_634e7685）
    if IG is None:
        res["checks"]["host_restart"] = {"status": "FAIL", "detail": "interrupt_guard.py 不可导入", "host": {}}
        res["reasons"].append("宿主重启：脚本缺失")
    else:
        HARD = ("restart_imminent", "restart_soon_active")
        try:
            rres = IG.restart_check(scope=("host", "local"), timeout=a.timeout)
            sigs = rres.get("signals") or []
            hard = [s for s in sigs if s["kind"] in HARD]
            soft = [s for s in sigs if s["kind"] not in HARD]
            errs = [e for e in (rres.get("errors") or []) if e.startswith("host:")]
            hs = (rres.get("surveys") or {}).get("host") or {}
            nxt = rres.get("next") or []
            if errs:
                st, det = "FAIL", "宿主重启面探测失败：%s" % "; ".join(errs)
                res["reasons"].append("宿主重启：%s" % det)
            elif hard:
                st = "FAIL"
                det = "; ".join(s["detail"] for s in hard)
                res["reasons"].append("宿主重启 —— 暂停发车：" + det)
            elif soft:
                st = "WARN"
                det = "; ".join(s["detail"] for s in soft)
                res["reasons"].append("宿主重启（提示级）：" + det)
            else:
                st = "PASS"
                det = "在飞 MAA=%s；%s" % (
                    hs.get("in_flight"), "; ".join("%s %s@%s（%.0f min）" % (n["machine"], n["task"], n["next"], n["minutes"])
                                                   for n in nxt[:2]) or "无计划重启")
            res["checks"]["host_restart"] = {"status": st, "detail": det,
                                             "host": {"in_flight": hs.get("in_flight"), "boot": hs.get("boot"),
                                                      "pending": hs.get("pending")},
                                             "next": nxt, "signals": sigs}
        except Exception as e:  # noqa: BLE001
            res["checks"]["host_restart"] = {"status": "FAIL", "detail": "%s: %s" % (type(e).__name__, e), "host": {}}
            res["reasons"].append("宿主重启检测失败：%s" % e)

    fails = [k for k, v in res["checks"].items() if v.get("status") == "FAIL"]
    res["preflight"] = "NO-GO" if fails else "GO"
    res["failing_checks"] = fails
    return res


# ---------------------------------------------------------------- 输出

def print_report(res: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    t = res.get("target") or {}
    print("班次前自检（只读）  %s" % res["generated_at"])
    if t:
        nm = "队列 %s" % t["name"] if t["kind"] == "queue" else "脚本 %s" % t["name"]
        print("目标: %s → %d 脚本 / %d 目录 / %d 实例" % (nm, len(t["targets"]), len(t["dirs"]), len(t["emus"])))
    order = [("rest", "1 REST"), ("slot_idle", "2 槽位空闲"), ("emulator", "3 模拟器"),
             ("lease", "3L 实例租约"),
             ("residual", "4 残留画面"), ("config_gate", "5 注入闸门"), ("disk", "6 磁盘水位"),
             ("client_version", "7 客户端版本"), ("host_restart", "8 宿主重启")]
    for key, label in order:
        c = res["checks"].get(key)
        if not c:
            continue
        print("[%s] %-10s %s" % (c.get("status", "?"), label, c.get("detail", "")))
        if key == "slot_idle" and c.get("other_busy"):
            names = {os.path.basename(os.path.dirname(x)) for x in c["other_busy"]}
            nat = sorted(names, key=lambda s: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)])
            print("        （其他在跑槽位：%s，不阻断）" % ", ".join(nat))
        if key == "emulator":
            for i, r in (c.get("per_idx") or {}).items():
                if r.get("running"):
                    print("        idx=%-4s UP serial=%s boot=%s" % (i, r.get("serial", "?"), r.get("boot", "?")))
                else:
                    print("        idx=%-4s OFF %s" % (i, r.get("detail", "")))
        if key == "lease":
            for i, r in (c.get("per_idx") or {}).items():
                print("        idx=%-4s %-5s %s%s" % (i, r.get("status"), r.get("detail", ""),
                                                     ("（owner=%s）" % r.get("holder")) if r.get("holder") else ""))
        if key == "residual":
            for i, r in (c.get("per_idx") or {}).items():
                print("        idx=%-3s %-4s %s" % (i, r.get("status"), r.get("detail", "")))
                if r.get("shot"):
                    print("                shot=%s" % r["shot"])
        if key == "config_gate":
            for d, r in (c.get("per_dir") or {}).items():
                print("        %-8s %-9s server=%s account=%s switch=%s %s" % (
                    os.path.basename(d), r.get("verdict"), r.get("server"), r.get("account"),
                    r.get("switch"), ("issue=%s" % ";".join(r.get("issues") or [])) if r.get("issues") else ""))
        if key == "client_version":
            for i, r in (c.get("per_idx") or {}).items():
                print("        idx=%-4s %-28s %-12s %s" % (i, r.get("pkg"), r.get("versionName"), r.get("status")))
        if key == "host_restart":
            for n in c.get("next") or []:
                print("        %-5s %-18s @ %s（%.0f min）" % (n.get("machine"), n.get("task"), n.get("next"), n.get("minutes")))
    print("PREFLIGHT=%s" % res.get("preflight"))
    for r in res.get("reasons") or []:
        print("  - %s" % r)


# ---------------------------------------------------------------- 自检

def selftest() -> int:
    bad = 0

    def ck(label, got, want):
        nonlocal bad
        ok = got == want
        bad += 0 if ok else 1
        print("%s %-34s got=%r want=%r" % ("PASS" if ok else "FAIL", label, got, want))

    # dir 归一 & 前缀匹配
    ck("norm doubled", norm_dir(r"E:\\MAA-Multi\\MAA_12"), r"E:\MAA-Multi\MAA_12")
    ck("norm plain", norm_dir(r"E:\MAA-Multi\MAA_1"), r"E:\MAA-Multi\MAA_1")
    d1, d12 = norm_dir(r"E:\MAA-Multi\MAA_1"), norm_dir(r"E:\MAA-Multi\MAA_12")

    def busy(exe, target):
        exe_l, t_l = norm_dir(exe).lower(), target.lower()
        return exe_l == t_l or exe_l.startswith(t_l + "\\")

    ck("busy MAA_1 hits MAA_1", busy(r"E:\MAA-Multi\MAA_1\MAA.exe", d1), True)
    ck("busy prefix guard MAA_12", busy(r"E:\MAA-Multi\MAA_12\MAA.exe", d1), False)
    ck("busy case-insens", busy(r"e:\maa-multi\MAA_12\MAA.exe", d12), True)

    # 关键词
    hits = classify_residual([("登录被取消，请重试", 0.99)])
    ck("block login-cancel", bool(hits["block"]), True)
    hits = classify_residual([("开始唤醒", 0.9), ("查看公告", 0.9)])
    ck("benign no warn", (hits["block"], hits["warn"]), ([], []))
    hits = classify_residual([("发现新版本，是否更新", 0.9)])
    ck("warn update", bool(hits["warn"]), True)
    hits = classify_residual([("公告", 0.9)])
    ck("warn bare gonggao", bool(hits["warn"]), True)
    hits = classify_residual([("维护中", 0.9)])
    ck("warn maintenance", bool(hits["warn"]), True)

    # 端口过滤
    ck("port filter 7555", [p for p in [7555, 5581, 16800] if p != 7555], [5581, 16800])

    # 前台归类
    ck("pkg launcher", pkg_class("com.mumu.launcher"), "launcher")
    ck("pkg lawnchair", pkg_class("app.lawnchair"), "launcher")
    ck("pkg game", pkg_class("com.hypergryph.arknights"), "game")
    ck("pkg game jp", pkg_class("com.YoStarJP.Arknights"), "game")
    ck("pkg other", pkg_class("com.android.settings"), "other")

    # VMM 注释解析
    ck("vmm parse", VMM_RE.search("... --comment MuMuPlayer-12.0-13 --startvm xx").group(1), "13")
    ck("vmm parses 130 whole", VMM_RE.search("--comment MuMuPlayer-12.0-130 --startvm").group(1), "130")
    ck("vmm no partial 13-in-135", VMM_RE.search("--comment MuMuPlayer-12.0-135 --startvm").group(1), "135")
    ck("vmm no match", VMM_RE.search("--comment Other-12.0-13"), None)

    # 第 3L 项：实例租约（P1-2 跨族单飞）——隔离 runtime，真取租再判
    import shutil
    import tempfile
    tmp_lease = tempfile.mkdtemp(prefix="mas_preflight_lease_selftest_")
    try:
        import mas_lease_glue as LG  # noqa: PLC0415
        LG.set_runtime(tmp_lease)
        ck("lease empty ⇒ PASS", lease_check(["999"], owner_id="")["status"], "PASS")
        ac = LG.acquire_all(["999"], "t_preflight_probe", "preflight selftest", ttl_s=120, src="selftest")
        ck("lease acquire ok", ac.get("ok"), True)
        chk = lease_check(["999"], owner_id="")
        ck("lease busy(别人) ⇒ FAIL", chk["status"], "FAIL")
        ck("lease detail 带持有者", "t_preflight_probe" in (chk.get("detail") or ""), True)
        ck("lease detail 带到期", "到期" in (chk.get("detail") or ""), True)
        ck("lease FAIL 带 per_idx", chk["per_idx"]["999"]["status"], "busy")
        ck("lease 自己的 ⇒ PASS", lease_check(["999"], owner_id="t_preflight_probe")["status"], "PASS")
        ck("lease 别人 owner ⇒ FAIL", lease_check(["999"], owner_id="t_other")["status"], "FAIL")
        ck("lease 无实例 ⇒ SKIP", lease_check([])["status"], "SKIP")
        ck("lease 释放", LG.release_all(["999"], owner_id="t_preflight_probe").get("ok"), True)
        ck("lease 释放后 PASS", lease_check(["999"], owner_id="")["status"], "PASS")
        ck("lease 账本写在隔离目录", os.path.exists(os.path.join(tmp_lease, "instance_leases.json")), True)
        ck("lease 无 tmp 残留", os.path.exists(os.path.join(tmp_lease, "instance_leases.json.tmp")), False)
    finally:
        shutil.rmtree(tmp_lease, ignore_errors=True)

    print("selftest: %s (%d 失败)" % ("OK" if not bad else "FAIL", bad))
    return 0 if not bad else 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="班次前 GO/NO-GO 自检（只读）")
    ap.add_argument("--queue", help="目标队列名（如 晚班定时1）")
    ap.add_argument("--script", help="目标脚本名（如 明日方舟-官-1-1-7）")
    ap.add_argument("--json", action="store_true", help="JSON 输出（不打印 PREFLIGHT= 行）")
    ap.add_argument("--skip-screen", action="store_true", help="跳过第 4 项截图+OCR")
    ap.add_argument("--min-free-gb", type=float, default=10.0, help="磁盘 FAIL 阈值（默认 10GB）")
    ap.add_argument("--warn-free-gb", type=float, default=30.0, help="磁盘 WARN 阈值（默认 30GB）")
    ap.add_argument("--shot-dir", default=os.path.join(ROOT, "runtime", "preflight_shots"))
    ap.add_argument("--timeout", type=int, default=240, help="单次远端命令超时（秒）")
    ap.add_argument("--selftest", action="store_true", help="本地规则自检（不连网）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.queue and not a.script:
        ap.print_help()
        return 2
    if a.queue and a.script:
        print("--queue 与 --script 二选一", file=sys.stderr)
        return 2
    try:
        res = run_preflight(a)
    except TargetError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print("preflight 执行失败: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 2
    print_report(res, a.json)
    return 0 if res["preflight"] == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
