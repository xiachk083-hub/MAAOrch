#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""空转实例收敛清单（**只读**）：班次后扫出"没有 MAA 在用、且不属于下一班要用的"MuMu 实例。

定位：接在班次收尾后跑（或巡检里按需跑），输出**建议关闭清单 + 建议关闭命令**（本工具不执行任何关闭）。
判据（与 t_aaacd03c 共用；2026-09-27 实测校准）：

  1. 跑着的实例 = 主机 `MuMuVMMHeadless.exe` 进程，命令行 `--comment MuMuPlayer-12.0-<idx>`；
  2. 在用的 = 主机在飞 `MAA.exe`（命令行 = `<MAA目录>\\MAA.exe`）→ MAA 目录 → 实例：
     优先读该目录 `debug\\asst.log` 尾部的实际 adb serial（`-s 127.0.0.1:<port>`）→ 用
     `/api/emulator/status` 的 `adb_address` 反查 idx（端口会漂，别用 16384+32*idx 公式硬算）；
     读不到日志时退化为"该 MAA 目录绑定的全部实例"（保守：宁多留、不错关）；
  3. 下一班要用的 = SHIFT_TABLE 里**下一班**的队列（`日常定时*`/`晚班定时*`）→ 队列项脚本 → 实例，
     并入"巡检补跑可能涉及的脚本"（今天没有 DONE 记录的启用号所在脚本）绑定的实例；
  4. 其余（跑着 ∧ 不在用 ∧ 不在下一班口径）= **可关候选**。

只读保证：仅 SSH 读主机进程/日志 mtime/日志尾部 + AUTO-MAS 只读端点（见 READ_ENDPOINTS 白名单）；
不启动、不关闭、不 kill、不发车、不改配置；默认**不碰 adb**（前台包列需 `--with-fg` 才查，且与其它
adb 任务错开时再用）。读入即丢弃凭据类键。

用法：
    python tools/mas_idle_emulators.py                  # 班次后扫描（严格口径）
    python tools/mas_idle_emulators.py --aggressive     # 扩展口径：空闲即候选（下班冷启成本自担）
    python tools/mas_idle_emulators.py --with-fg        # 附前台包（adb 只读；每台一次 dumpsys）
    python tools/mas_idle_emulators.py --json           # 机器可读输出
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
SSH = ["ssh", "-i", os.path.expanduser(r"~\.ssh\maaorch_target"),
       "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
       "Administrator@100.79.173.69"]
REMOTE_PS = "E:\\_hermes_idle_emu_probe.ps1"

# 只读端点白名单：本文件**只能**出现这些路径（防被改成会写）。
READ_ENDPOINTS = (
    "/api/scripts/get",
    "/api/scripts/user/get",
    "/api/queue/get",
    "/api/queue/item/get",
    "/api/emulator/status",
    "/api/history/search",
)
SENSITIVE_KEYS = ("password", "token", "sklandtoken", "secret", "credential", "cookie")

# ── 主机侧采集（一次上传 + 一次执行，全只读）────────────────────────────
HOST_PS = r"""
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
"NOW|" + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='MuMuVMMHeadless.exe'")) {
  $cl = [string]$p.CommandLine
  $m = [regex]::Match($cl, '--comment\s+MuMuPlayer-12\.0-(\d+)')
  if ($m.Success) { "VMM|idx=" + $m.Groups[1].Value + "|pid=" + $p.ProcessId + "|start=" + $p.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') }
}
foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='MAA.exe'")) {
  "MAA|pid=" + $p.ProcessId + "|start=" + $p.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') + "|exe=" + ([string]$p.CommandLine)
}
foreach ($d in (Get-ChildItem 'E:\MAA-Multi' -Directory -ErrorAction SilentlyContinue)) {
  $asst = Join-Path $d.FullName 'debug\asst.log'
  $gui  = Join-Path $d.FullName 'debug\gui.log'
  $a = '-'
  $g = '-'
  if (Test-Path $asst) { $a = (Get-Item $asst).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') }
  if (Test-Path $gui)  { $g = (Get-Item $gui).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') }
  "DIR|" + $d.Name + "|asst=" + $a + "|gui=" + $g
}
foreach ($d in (Get-ChildItem 'E:\MAA-Multi' -Directory -ErrorAction SilentlyContinue)) {
  $asst = Join-Path $d.FullName 'debug\asst.log'
  if (Test-Path $asst) {
    $hit = Get-Content $asst -Tail 400 -Encoding UTF8 -ErrorAction SilentlyContinue | Select-String -Pattern '-s 127\.0\.0\.1:(\d+)' | Select-Object -Last 1
    if ($hit) { "SER|" + $d.Name + "|" + $hit.Matches[0].Groups[1].Value }
  }
}
$lines = Get-Content 'E:\AUTO-MAS\debug\app.log' -Tail 15000 -Encoding UTF8 -ErrorAction SilentlyContinue | Select-String -Pattern '开始启动模拟器' | Select-Object -Last 150
foreach ($l in $lines) { "APP|" + $l.Line }
"END"
"""

TS_FMT = "%Y-%m-%d %H:%M:%S"


class UpstreamError(RuntimeError):
    pass


def _scrub(obj):
    """读入即丢弃敏感键（沿用 tools/mas_accounts.py 的约定），避免凭据进入输出。"""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()
                if not any(s in str(k).lower() for s in SENSITIVE_KEYS)}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def api(path: str, body: dict | None = None, timeout: int = 60, retries: int = 2) -> dict:
    if path not in READ_ENDPOINTS:
        raise UpstreamError("非只读端点被拒绝: %s" % path)
    last = None
    for _ in range(max(1, retries)):
        req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
            return _scrub(json.loads(raw))
        except Exception as e:  # noqa: BLE001
            last = e
    raise UpstreamError("%s -> %s: %s" % (path, type(last).__name__, str(last)[:120]))


def pad(s: str, width: int) -> str:
    """按显示宽度补齐（CJK 记 2 列），保证中文表格对齐。"""
    w = sum(2 if ord(c) > 0x2E7F else 1 for c in s)
    return s + " " * max(0, width - w)


def parse_ts(s: str):
    try:
        return dt.datetime.strptime(s.strip(), TS_FMT)
    except Exception:  # noqa: BLE001
        return None


# ── 主机侧采集 ─────────────────────────────────────────────────────────

def collect_host(timeout: int = 240) -> dict:
    """上传采集脚本到主机（base64+BOM，规避 PS 5.1 无 BOM 中文乱码）并执行，解析成结构化数据。"""
    payload = b"\xef\xbb\xbf" + HOST_PS.encode("utf-8")
    b64 = base64.b64encode(payload).decode("ascii")
    upload = ("$b64='%s'\n"
              "$b=[Convert]::FromBase64String($b64)\n"
              "[IO.File]::WriteAllBytes('%s', $b)\n"
              "Write-Output ('written ' + $b.Length)\n" % (b64, REMOTE_PS))
    try:
        r = subprocess.run(SSH + ["chcp 65001 >nul & powershell -NoProfile -Command -"],
                           input=upload.encode("utf-8"), capture_output=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise UpstreamError("SSH 上传失败: %s: %s" % (type(e).__name__, str(e)[:120])) from e
    if b"written" not in r.stdout:
        raise UpstreamError("SSH 上传未确认: %s" % (r.stdout + r.stderr).decode("utf-8", "replace")[:200])
    try:
        r = subprocess.run(SSH + ["chcp 65001 >nul & powershell -NoProfile -ExecutionPolicy Bypass -File " + REMOTE_PS],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise UpstreamError("SSH 执行失败: %s: %s" % (type(e).__name__, str(e)[:120])) from e
    out = (r.stdout + r.stderr).decode("utf-8", "replace")

    res = {"now": None, "vmm": {}, "maa": [], "dirs": {}, "ser": {}, "app_start": {}}
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("NOW|"):
            res["now"] = parse_ts(ln[4:])
        elif ln.startswith("VMM|"):
            m = re.search(r"idx=(\d+)\|pid=(\d+)\|start=(.+)", ln)
            if m:
                res["vmm"][int(m.group(1))] = {"pid": int(m.group(2)), "start": parse_ts(m.group(3))}
        elif ln.startswith("MAA|"):
            m = re.search(r"pid=(\d+)\|start=(.+)", ln)
            exe = ln.split("|exe=", 1)[1] if "|exe=" in ln else ""
            d = re.search(r"MAA_(\d+)", exe)
            if m:
                res["maa"].append({"pid": int(m.group(1)), "start": parse_ts(m.group(2)),
                                   "exe": exe.strip(), "dir": ("MAA_%s" % d.group(1)) if d else None})
        elif ln.startswith("DIR|"):
            m = re.search(r"DIR\|(MAA_\d+)\|asst=([^|]*)\|gui=(.*)", ln)
            if m:
                res["dirs"][m.group(1)] = {"asst": parse_ts(m.group(2)), "gui": parse_ts(m.group(3))}
        elif ln.startswith("SER|"):
            m = re.search(r"SER\|(MAA_\d+)\|(\d+)", ln)
            if m:
                res["ser"][m.group(1)] = m.group(2)
        elif ln.startswith("APP|"):
            m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*开始启动模拟器\s+(\d+)", ln)
            if m:
                t = parse_ts(m.group(1))
                idx = int(m.group(2))
                if t and (idx not in res["app_start"] or t > res["app_start"][idx]):
                    res["app_start"][idx] = t
    if res["now"] is None or not res["vmm"]:
        raise UpstreamError("主机采集不完整（now=%s, vmm=%d 台）：%s"
                            % (res["now"], len(res["vmm"]), out[-300:]))
    return res


# ── REST 侧采集（全部只读）────────────────────────────────────────────

def collect_rest() -> dict:
    scr = api("/api/scripts/get", {"scriptId": None}).get("data") or {}
    scripts = {}
    for sid, sv in scr.items():
        info = (sv or {}).get("Info") or {}
        emu = (sv or {}).get("Emulator") or {}
        try:
            idx = int(emu.get("Index"))
        except (TypeError, ValueError):
            idx = None
        scripts[sid] = {"name": info.get("Name") or "?", "path": (info.get("Path") or "").rstrip("\\/"),
                        "emu_id": emu.get("Id"), "emu_idx": idx}

    queues = {}
    qd = api("/api/queue/get", {"queueId": None}).get("data") or {}
    for qid, qv in qd.items():
        name = ((qv or {}).get("Info") or {}).get("Name") or "?"
        items = api("/api/queue/item/get", {"queueId": qid}).get("data") or {}
        sids = [((iv or {}).get("Info") or {}).get("ScriptId") for iv in items.values()]
        queues[qid] = {"name": name, "script_ids": [s for s in sids if s]}

    emus = {}
    es = api("/api/emulator/status", {})
    for dev, inner in (es.get("data") or {}).items():
        for idx, ev in (inner or {}).items():
            try:
                i = int(idx)
            except (TypeError, ValueError):
                continue
            emus[i] = {"title": (ev or {}).get("title") or "?", "adb": (ev or {}).get("adb_address") or "",
                       "dev": dev}

    # 启用号 + 今日无 DONE 的号（供"补跑可能涉及"）
    users, pending_accounts = {}, set()
    for sid, s in scripts.items():
        ud = api("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {}
        names = []
        for k, uv in ud.items():
            if k == "instances" or not isinstance(uv, dict):
                continue
            ui = (uv or {}).get("Info") or {}
            if ui.get("Status") and ui.get("Name"):
                names.append(ui["Name"])
        users[sid] = names

    today = dt.date.today().isoformat()
    try:
        h = api("/api/history/search", {"mode": "DAILY", "start_date": today, "end_date": today}).get("data") or {}
        for _day, per in h.items():
            if not isinstance(per, dict):
                continue
            for acct, rec in per.items():
                idxs = [str((e or {}).get("status") or "").upper() for e in ((rec or {}).get("index") or [])]
                if not idxs or idxs[-1] != "DONE":
                    pending_accounts.add(acct)
        # 完全没记录的启用号也算"还没跑"（无记录 ⇒ 可能待跑）
        recorded = set()
        for _day, per in h.items():
            if isinstance(per, dict):
                recorded |= set(per.keys())
        for sid, names in users.items():
            for n in names:
                if n not in recorded:
                    pending_accounts.add(n)
    except UpstreamError:
        pass  # 拿不到历史 ⇒ 补跑集为空（下一班集仍生效，见 main 的说明）

    return {"scripts": scripts, "queues": queues, "emus": emus, "users": users,
            "pending_accounts": pending_accounts}


def shift_table() -> dict:
    """从 tools/mas_dispatch.py 取 SHIFT_TABLE（单一来源）；失败则用内置快照兜底。"""
    try:
        import importlib.util
        path = os.path.join(ROOT, "tools", "mas_dispatch.py")
        spec = importlib.util.spec_from_file_location("_mas_dispatch_tbl", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tbl = dict(mod.SHIFT_TABLE)
        if tbl:
            return tbl
    except Exception:  # noqa: BLE001
        pass
    return {
        "日常定时1": "05:00", "日常定时2": "05:03", "日常定时3": "05:06", "日常定时4": "05:09",
        "日常定时5": "05:12", "日常定时6": "05:15", "日常定时7": "05:18", "日常定时8": "05:21",
        "日常定时9": "05:24", "日常定时10": "05:27", "日常定时11": "05:30", "日常定时12": "05:33",
        "晚班定时1": "19:30", "晚班定时2": "19:33", "晚班定时3": "19:36", "晚班定时4": "19:39",
        "晚班定时5": "19:42", "晚班定时6": "19:45", "晚班定时7": "19:48", "晚班定时8": "19:51",
        "晚班定时9": "19:54", "晚班定时10": "19:57", "晚班定时11": "20:00", "晚班定时12": "20:03",
    }


def resolve_next_shift(tbl: dict, now: dt.datetime) -> dict:
    """下一班 = 最早一个"最后一条定时 >= now"的班次（在跑的班次仍算下一班；过点则翻到次日早班）。"""
    def span(prefix: str) -> tuple[dt.datetime, dt.datetime]:
        times = sorted(t for n, t in tbl.items() if n.startswith(prefix))
        first, last = times[0], times[-1]
        def mk(t):
            h, m = map(int, t.split(":"))
            return now.replace(hour=h, minute=m, second=0, microsecond=0)
        return mk(first), mk(last)

    d_first, d_last = span("日常")
    e_first, e_last = span("晚班")
    cands = [("daily", d_first, d_last, "早班"), ("evening", e_first, e_last, "晚班")]
    for key, first, last, label in cands:
        if last >= now:
            return {"key": key, "label": label, "first": first, "last": last}
    nxt = d_first + dt.timedelta(days=1)
    return {"key": "daily", "label": "早班(次日)", "first": nxt, "last": d_last + dt.timedelta(days=1)}


# ── 判定 ───────────────────────────────────────────────────────────────

def port_to_idx(emus: dict) -> dict:
    """adb 端口 → 实例 idx：以 /api/emulator/status 的 adb_address 为准（端口会漂）。"""
    m = {}
    for idx, e in emus.items():
        adb = e.get("adb") or ""
        mm = re.search(r":(\d+)$", adb)
        if mm:
            m[mm.group(1)] = idx
    return m


def compute(host: dict, rest: dict, tbl: dict) -> dict:
    now = host["now"]
    running = host["vmm"]
    scripts, queues, emus, users = rest["scripts"], rest["queues"], rest["emus"], rest["users"]

    # 实例 → 脚本
    idx_scripts = {}
    for sid, s in scripts.items():
        if s["emu_idx"] is not None:
            idx_scripts.setdefault(s["emu_idx"], []).append(s)
    dir_idxs = {}
    for s in scripts.values():
        if s["emu_idx"] is not None and s["path"]:
            dir_idxs.setdefault(os.path.basename(s["path"]), set()).add(s["emu_idx"])

    # 在用（MAA 目录 → 实例；优先 asst.log 实际 serial）
    p2i = port_to_idx(emus)
    in_use, in_use_note = set(), {}
    for m in host["maa"]:
        d = m["dir"]
        if not d:
            continue
        port = host["ser"].get(d)
        idx = p2i.get(port) if port else None
        if idx is None and port and (int(port) - 16384) % 32 == 0:
            cand = (int(port) - 16384) // 32
            if cand in idx_scripts:
                idx = cand
                in_use_note[d] = "serial=%s→idx=%d（公式推断）" % (port, cand)
        if idx is not None:
            in_use.add(idx)
            in_use_note.setdefault(d, "serial=%s→idx=%d" % (port, idx))
        else:
            fb = dir_idxs.get(d, set())
            in_use |= fb
            in_use_note[d] = "无 serial，退化取目录绑定 %s" % (sorted(fb) or "∅")

    # 下一班要用的
    ns = resolve_next_shift(tbl, now)
    prefix = "日常" if ns["key"] == "daily" else "晚班"
    next_sids, next_idxs = [], set()
    for q in queues.values():
        if not q["name"].startswith(prefix):
            continue
        for sid in q["script_ids"]:
            s = scripts.get(sid)
            if not s or sid in next_sids:
                continue
            if users.get(sid, []) == [] and users:  # 无启用号 ⇒ 不会真跑
                continue
            next_sids.append(sid)
            if s["emu_idx"] is not None:
                next_idxs.add(s["emu_idx"])

    # 巡检补跑可能涉及（今天没有 DONE 的启用号所在脚本；补跑只看队列项）
    pending = rest["pending_accounts"]
    catchup_idxs, catchup_scripts = set(), []
    if pending:
        for q in queues.values():
            for sid in q["script_ids"]:
                s = scripts.get(sid)
                if not s or sid in catchup_scripts:
                    continue
                if any(n in pending for n in users.get(sid, [])):
                    catchup_scripts.append(sid)
                    if s["emu_idx"] is not None:
                        catchup_idxs.add(s["emu_idx"])

    needed = next_idxs | catchup_idxs

    # 空闲时长：max(实例启动, 目录日志 mtime, app.log 启动事件)
    rows = []
    for idx in sorted(running):
        v = running[idx]
        ss = sorted(idx_scripts.get(idx, []), key=lambda x: x["name"])
        dirs = sorted({os.path.basename(s["path"]) for s in ss if s["path"]})
        last = v["start"]
        src = "VMM启动"
        for d in dirs:
            for k in ("asst", "gui"):
                t = (host["dirs"].get(d) or {}).get(k)
                if t and t > last:
                    last, src = t, "%s/%s.log" % (d, k)
        if idx in host["app_start"] and host["app_start"][idx] > last:
            last, src = host["app_start"][idx], "app.log启动"
        idle = now - last if last else None
        rows.append({
            "idx": idx, "pid": v["pid"], "title": (emus.get(idx) or {}).get("title") or "?",
            "adb": (emus.get(idx) or {}).get("adb") or "",
            "scripts": [s["name"] for s in ss], "dirs": dirs,
            "in_use": idx in in_use, "needed": idx in needed,
            "needed_by": ("下一班" if idx in next_idxs else "") + ("+补跑" if idx in catchup_idxs and idx in next_idxs else ("补跑" if idx in catchup_idxs else "")),
            "idle_s": int(idle.total_seconds()) if idle else None,
            "idle_src": src, "last_activity": last.strftime(TS_FMT) if last else None,
        })

    candidates = [r for r in rows if not r["in_use"] and not r["needed"]]
    aggressive = [r for r in rows if not r["in_use"]]
    return {"now": now, "next_shift": ns, "rows": rows,
            "candidates": candidates, "aggressive": aggressive,
            "counts": {"running": len(rows), "in_use": sum(1 for r in rows if r["in_use"]),
                       "needed": sum(1 for r in rows if r["needed"] and not r["in_use"]),
                       "candidates": len(candidates)},
            "next_sids": next_sids, "catchup_scripts": catchup_scripts,
            "in_use_note": in_use_note, "emu_dev": (next(iter(emus.values())) or {}).get("dev") if emus else None}


def fmt_idle(s) -> str:
    if s is None:
        return "-"
    h, m = divmod(s // 60, 60)
    return "%dh%02dm" % (h, m) if h else "%dm" % m


def suggest_close(dev: str, idx: int, title: str) -> list:
    """建议关闭命令（不执行）。AUTO-MAS 等价界面操作优先；MuMuManager 直控备选。"""
    return [
        "  # idx=%d %s" % (idx, title),
        "  curl -s -X POST %s/api/emulator/operate -H \"Content-Type: application/json\" "
        "-d '{\"emulatorId\":\"%s\",\"operate\":\"close\",\"index\":\"%d\"}'" % (API, dev, idx),
        "  \"E:\\MuMu Player 12\\nx_main\\MuMuManager.exe\" control -v %d shutdown" % idx,
    ]


def fg_probe(serials: list) -> dict:
    """可选：adb 只读查前台包（每台一次 dumpsys）。与其它 adb 任务错开时再用。"""
    out = {}
    adb = r"E:\MuMu Player 12\nx_main\adb.exe"
    for idx, serial in serials:
        try:
            r = subprocess.run(SSH + ["\"%s\" -s %s shell dumpsys window | findstr mCurrentFocus" % (adb, serial)],
                               capture_output=True, stdin=subprocess.DEVNULL, timeout=90)
            txt = (r.stdout + r.stderr).decode("utf-8", "replace")
            m = re.search(r"mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^}/]+)", txt)
            out[idx] = m.group(1) if m else (txt.strip().splitlines()[-1][:80] if txt.strip() else "无输出")
        except Exception as e:  # noqa: BLE001
            out[idx] = "查询失败:%s" % type(e).__name__
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="空转实例收敛清单（只读；不关闭任何实例）")
    ap.add_argument("--aggressive", action="store_true",
                    help="扩展口径：空闲即候选（含下一班要复用的；代价=下班冷启）")
    ap.add_argument("--with-fg", action="store_true", help="附前台包（adb 只读，与其它 adb 任务错开时再用）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args(argv)

    try:
        host = collect_host(timeout=args.timeout)
        rest = collect_rest()
    except UpstreamError as e:
        print("[错误] 上游不可用: %s" % e)
        return 2

    tbl = shift_table()
    d = compute(host, rest, tbl)
    rows, cands = d["rows"], (d["aggressive"] if args.aggressive else d["candidates"])

    fg = {}
    if args.with_fg and rows:
        fg = fg_probe([(r["idx"], r["adb"]) for r in rows if r["adb"]])

    if args.json:
        # suggested_commands：与文本模式同源（suggest_close 产物进 JSON，供 MCP/巡检引用；不执行）
        sugg = [ln for r in cands for ln in suggest_close(d["emu_dev"] or "<emulatorId>", r["idx"], r["title"])]
        print(json.dumps({"ok": True, "generated_at": d["now"].strftime(TS_FMT),
                          "next_shift": {"label": d["next_shift"]["label"],
                                         "last_dispatch": d["next_shift"]["last"].strftime(TS_FMT)},
                          "counts": d["counts"],
                          "candidates": cands, "rows": rows, "foreground": fg,
                          "suggested_commands": sugg,
                          "next_shift_scripts": len(d["next_sids"]),
                          "catchup_scripts": len(d["catchup_scripts"]),
                          "in_use_note": d["in_use_note"]}, ensure_ascii=False, indent=1))
        return 0

    ns = d["next_shift"]
    print("空转实例收敛扫描（只读）· %s" % d["now"].strftime(TS_FMT))
    print("跑着 %d 台 ｜ 在用 %d 台 ｜ 空闲且下班复用 %d 台 ｜ %s可关候选 %d 台"
          % (d["counts"]["running"], d["counts"]["in_use"], d["counts"]["needed"],
             "扩展口径" if args.aggressive else "严格口径", len(cands)))
    print("下一班：%s（末条定时 %s）｜ 其队列涉及脚本 %d 个 ｜ 补跑可能涉及脚本 %d 个"
          % (ns["label"], ns["last"].strftime("%m-%d %H:%M"), len(d["next_sids"]), len(d["catchup_scripts"])))
    print()
    hdr = pad("idx", 5) + pad("标题", 20) + pad("脚本", 28) + pad("前台包", 24) + pad("空闲", 8) + "建议"
    print(hdr)
    print("-" * sum(2 if ord(c) > 0x2E7F else 1 for c in hdr))
    strict_ids = {c["idx"] for c in d["candidates"]}
    for r in rows:
        if r["in_use"]:
            rec = "在用（勿动）"
        elif r["idx"] in strict_ids:
            rec = "可关候选"
        elif args.aggressive:
            rec = "可关（下班需冷启）"
        else:
            rec = "保留（%s复用）" % ("下一班" if r["needed_by"].startswith("下一班") else "补跑")
        fgv = fg.get(r["idx"], "-") if args.with_fg else "-（未查）"
        print(pad(str(r["idx"]), 5) + pad(r["title"], 20)
              + pad("、".join(r["scripts"]) or "（无脚本绑定）", 28)
              + pad(fgv, 24)
              + pad("在用" if r["in_use"] else fmt_idle(r["idle_s"]), 8) + rec)
    print()
    if cands:
        print("建议关闭命令（本工具不执行；AUTO-MAS close 等价界面操作，MuMuManager 为直控备选）：")
        for r in cands:
            for ln in suggest_close(d["emu_dev"] or "<emulatorId>", r["idx"], r["title"]):
                print(ln)
        if args.aggressive:
            print("  ⚠️ 扩展口径：上述实例下班（%s）会需要冷启，启动失败风险见 t_aaacd03c 报告。" % ns["label"])
    else:
        print("严格口径下无候选：跑着的实例不是在用、就是下一班/补跑要复用的。")
        if not args.aggressive:
            idle = [r for r in d["aggressive"]]
            if idle:
                print("如需立刻回收资源，可用 --aggressive 把下列 %d 台空闲实例也列为可关（代价=下班冷启）：%s"
                      % (len(idle), "、".join("idx%d(%s)" % (r["idx"], r["title"]) for r in idle)))
    print()
    print("口径说明：在用的判定 = 在飞 MAA 目录 → asst.log 实际 serial → emulator/status 反查（端口会漂）；")
    print("          空闲时长 = now − max(实例启动, 所属 MAA 目录日志 mtime, app.log 启动事件)（目录级粒度，属上界估计）；")
    print("          下一班 = SHIFT_TABLE 中'末条定时 ≥ now'的班次；补跑集 = 今日无 DONE 的启用号所在队列脚本。")
    if d["in_use_note"]:
        print("          在用映射明细：%s" % "；".join("%s %s" % (k, v) for k, v in sorted(d["in_use_note"].items())))
    if not args.with_fg:
        print("          前台包未查（默认不碰 adb；需要时 --with-fg，且与其它 adb 任务错开）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
