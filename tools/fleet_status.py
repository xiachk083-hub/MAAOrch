#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAAOrch 机队一览（只读一屏）—— 设备占用 / 队列 / 计划任务 / 肉鸽机队 / 机器人席位 / 房间最后几条。

用法：
  C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe tools/fleet_status.py
  （加 --json 输出机器可读 JSON）

纪律：只读。不碰设备（不截图/不 OCR/不点击）、不写任何仓库文件。
解释器必须用 3.12（与队列同）；本脚本只依赖标准库。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent            # D:/1-2/MAAOrch
RUNTIME = ROOT / "runtime"
HERMES = Path(r"C:\Users\xiach\AppData\Local\hermes")
PROFILE_SLUGS = ["default", "ark", "mas", "u8fbe-u82ac-u5947", "u91c7-u8d2d-u5458"]
PS = ["powershell", "-NoProfile", "-Command"]


def profile_home(slug: str) -> Path:
    return HERMES if slug == "default" else HERMES / "profiles" / slug


def run_ps(command: str) -> str:
    ps = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + command
    try:
        r = subprocess.run(PS + [ps], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return (r.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"(powershell 失败: {exc})"


def tail(path: Path, n: int = 3) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    lines = [ln for ln in text.replace("\r", "").split("\n") if ln.strip()]
    return lines[-n:]


def mtime_str(path: Path) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(path.stat().st_mtime))
    except Exception:
        return "?"


def age_str(path: Path) -> str:
    try:
        s = time.time() - path.stat().st_mtime
    except Exception:
        return "?"
    if s < 90:
        return f"{s:.0f}s"
    if s < 5400:
        return f"{s / 60:.0f}min"
    return f"{s / 3600:.1f}h"


# ---------------------------------------------------------------- 设备 / 进程
def device_section() -> dict:
    out = run_ps(
        'Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^(python|pythonw|node|pwsh|powershell)(\\.exe)?$" } | '
        'ForEach-Object { $c=$_.CommandLine; if ($c) { "{0}`t{1}`t{2}" -f $_.ProcessId, $_.Name, $c } }'
    )
    procs = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            procs.append({"pid": parts[0], "name": parts[1], "cmd": parts[2]})

    def find(pattern: str) -> list[dict]:
        return [p for p in procs if re.search(pattern, p["cmd"]) and "fleet_status" not in p["cmd"]]

    queue = find(r"prime_queue3\.py")
    sweep = find(r"event_sweep\.mjs")
    attempt = find(r"stage_attempt\.py|maa_job_fallback\.py")
    campaign = find(r"run_campaign\.ps1")
    flag = (RUNTIME / "_preempt.flag").exists()
    halt = (RUNTIME / "_prime_halt.json").exists()
    health = "?"
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=4) as resp:
            health = "ok" if json.loads(resp.read().decode()).get("ok") else "bad"
    except Exception:
        health = "off"
    return {"queue": queue, "sweep": sweep, "attempt": attempt, "campaign": campaign,
            "preempt_flag": flag, "halt": halt, "service_health": health}


# ---------------------------------------------------------------- 队列状态
def queue_section() -> dict:
    log = RUNTIME / "_prime_queue3.log"
    last = tail(log, 4)
    state_counter = {}
    try:
        st = json.loads((RUNTIME / "_prime_state3.json").read_text(encoding="utf-8"))
        for v in st.values():
            state_counter[v] = state_counter.get(v, 0) + 1
        total = len(st)
    except Exception:
        total = 0
    noreach = 0
    try:
        nr = json.loads((RUNTIME / "_prime_noreach.json").read_text(encoding="utf-8"))
        noreach = len(nr) if isinstance(nr, dict) else len(nr)
    except Exception:
        pass
    reports = sorted((ROOT / "runtime" / "reports").glob("campaign_*.log"), key=lambda p: p.stat().st_mtime)
    return {"log_mtime": mtime_str(log), "log_age": age_str(log), "log_tail": last,
            "state_counter": state_counter, "state_total": total, "noreach": noreach,
            "campaign_log": str(reports[-1]) if reports else "", "campaign_age": age_str(reports[-1]) if reports else "?"}


# ---------------------------------------------------------------- 计划任务
def tasks_section() -> list[str]:
    out = run_ps(
        'Get-ScheduledTask | Where-Object { $_.TaskName -like "MAAOrch-*" } | '
        'ForEach-Object { $i=$_ | Get-ScheduledTaskInfo; '
        '"{0}`t{1}`t{2}`t{3}`t{4}" -f $_.TaskName, $_.State, $i.LastRunTime, $i.LastTaskResult, $i.NextRunTime }'
    )
    return [ln for ln in out.splitlines() if "\t" in ln]


# ---------------------------------------------------------------- 肉鸽机队
def rogue_section() -> dict:
    fleet_log = RUNTIME / "rogue_fleet_watch.log"
    machines = {}
    try:
        st = json.loads((RUNTIME / "rogue_fleet_state.json").read_text(encoding="utf-8"))
        for name, m in (st.get("machines") or {}).items():
            machines[name] = {"humanUntil": m.get("humanUntil"), "lastAction": m.get("lastActionAt")}
    except Exception:
        pass
    r0 = {}
    try:
        st0 = json.loads((RUNTIME / "rogue0_watch_state.json").read_text(encoding="utf-8"))
        r0 = {"keys": list(st0)[:6]}
    except Exception:
        pass
    return {"last": tail(fleet_log, 2), "machines": machines, "rogue0": r0}


# ---------------------------------------------------------------- 机器人席位
def bots_section() -> list[dict]:
    rows = []
    for slug in PROFILE_SLUGS:
        db = profile_home(slug) / "state.db"
        if not db.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            cur = con.cursor()
            last_msg_ts = cur.execute("select max(timestamp) from messages").fetchone()[0]
            newest = cur.execute(
                "select id, started_at, message_count from sessions order by started_at desc limit 1"
            ).fetchone()
            live = cur.execute("select count(*) from sessions where started_at > ?", (time.time() - 3600,)).fetchone()[0]
            con.close()
            rows.append({
                "slug": slug,
                "last_msg": datetime.fromtimestamp(last_msg_ts).strftime("%H:%M:%S") if last_msg_ts else "?",
                "last_age_s": (time.time() - last_msg_ts) if last_msg_ts else None,
                "newest_session": newest[0] if newest else "",
                "sessions_1h": live,
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"slug": slug, "error": str(exc)})
    return rows


# ---------------------------------------------------------------- 房间最后几条
def room_section() -> list[str]:
    path = HERMES / "profile.yaml"
    try:
        import yaml  # type: ignore
    except Exception:
        return ["(PyYAML 不可用，跳过房间解析——房间内容以桌面 Bot Chat 为准)"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rooms = ((data.get("ui_meta") or {}).get("hermes-bots-groups") or {}).get("rooms") or {}
        lines = []
        for rid, room in rooms.items():
            log = (room or {}).get("log") or []
            for entry in log[-2:]:
                frm = (entry.get("from") or {})
                text = (entry.get("text") or "").replace("\n", " ")
                lines.append(f"{room.get('name', rid)} | {frm.get('name', '?')}: {text[:150]}")
        return lines
    except Exception as exc:  # noqa: BLE001
        return [f"(房间解析失败: {exc})"]


# ---------------------------------------------------------------- 输出
def main() -> int:
    as_json = "--json" in sys.argv
    data = {
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device": device_section(),
        "queue": queue_section(),
        "tasks": tasks_section(),
        "rogue": rogue_section(),
        "bots": bots_section(),
        "room": room_section(),
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
        return 0

    dev, q = data["device"], data["queue"]
    holder = []
    for key, label in (("campaign", "启动器"), ("queue", "队列"), ("sweep", "sweep"), ("attempt", "关卡尝试")):
        for p in dev[key]:
            holder.append(f"{label}(pid {p['pid']})")
    print(f"=== MAAOrch 机队一览（只读） | {data['now']} ===")
    print(f"[设备 16416] 占用: {' + '.join(holder) if holder else '无'} | 让位flag={'有!' if dev['preempt_flag'] else '无'} "
          f"| halt={'有!' if dev['halt'] else '无'} | 服务 8765={dev['service_health']}")
    print(f"[队列] 日志 mtime {q['log_mtime']}（{q['log_age']} 前）| 状态 {q['state_counter']} 共 {q['state_total']} | noreach {q['noreach']}")
    for ln in q["log_tail"][-2:]:
        print(f"        | {ln[:160]}")
    print(f"[计划任务] " + " || ".join(t.replace("\t", " ") for t in data["tasks"]))
    r = data["rogue"]
    print(f"[肉鸽机队] 最后: {r['last'][-1][:170] if r['last'] else '?'}")
    for name, m in (r["machines"] or {}).items():
        if m.get("humanUntil"):
            print(f"        | {name} 交人工至 {m['humanUntil']}")
    print("[机器人]")
    for b in data["bots"]:
        if b.get("error"):
            print(f"        | {b['slug']}: (读取失败 {b['error'][:60]})")
            continue
        star = "★" if (b.get("last_age_s") or 1e9) < 900 else " "
        print(f"        |{star} {b['slug']:22s} 最后活动 {b['last_msg']}（{int((b.get('last_age_s') or 0)//60)}min 前）"
              f" 会话 {b['newest_session']} 1h 内会话 {b['sessions_1h']}")
    print("[房间]")
    for ln in data["room"]:
        print(f"        | {ln}")
    print("（只读：未碰设备、未写文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
