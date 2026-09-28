#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""多账号实例「有没有串号」校验 —— 用 MAA 每轮上报的干员名册做指纹。

问题：一个脚本配置 = 一台模拟器带 N 个号（例：明日方舟-官-1-1-7 / MAA_6 / 4 个号）。
MAA 的日志里**没有账号身份**（uid 命中的其实都是 uuid），所以不能直接读"登录了谁"。
但 MAA 每个任务链都会向 GameDataReportService 上报当前登录账号的**干员名册** ——
不同账号名册不同 ⇒ 用名册指纹就能反推"这一轮跑的是哪个号"：
  * 各号名册指纹互不相同 ⇒ 每轮登录的是各自的号（未发现串号）
  * 两个号的名册指纹相同   ⇒ 强证据：这两轮其实登录的是同一个号（串号）

数据源（全部只读）：
  * AUTO-MAS REST：脚本 -> 用户清单 + MAA 目录
  * <MAA目录>\debug\gui.log：GameDataReportService 上报体里的 char_* 干员 id
  * E:\AUTO-MAS\history\<date>\<账号>\*.json：每轮的结束时间

用法：
  python tools/mas_account_identity_check.py --script 明日方舟-官-1-1-7 [--date 2026-09-26] [--window-min 35] [--json]
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")


def post(path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def remote_ps(script: str, timeout: int = 300) -> str:
    """把 PowerShell 脚本 base64(UTF-16LE) 走 -EncodedCommand 发到远端。

    这样同时避开两个坑：① 引号/换行的多层转义；② 远端控制台默认代码页导致中文输出乱码
    （脚本第一句自己把 OutputEncoding 设成 UTF-8，Python 侧按 UTF-8 解码）。
    """
    prefix = ("$ErrorActionPreference='Continue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    enc = (prefix + script).encode("utf-16-le")
    b64 = base64.b64encode(enc).decode("ascii")
    cmd = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
           SSH_HOST, "powershell -NoProfile -EncodedCommand " + b64]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def script_users(script_name: str) -> tuple[str, list[str]]:
    data = post("/api/scripts/get", {"scriptId": None}).get("data") or {}
    for sid, s in data.items():
        info = (s or {}).get("Info") or {}
        if info.get("Name") == script_name:
            users = post("/api/scripts/user/get", {"scriptId": sid}).get("data") or {}
            names = [((u or {}).get("Info") or {}).get("Name") for u in users.values()]
            return str(info.get("Path", "")), [n for n in names if n]
    raise SystemExit("找不到脚本 %s" % script_name)


def history_records(date: str) -> list:
    ps = ("$o=@(); Get-ChildItem 'E:\\AUTO-MAS\\history\\" + date + "' -Directory -ErrorAction SilentlyContinue | "
          "ForEach-Object { $a=$_.Name; Get-ChildItem $_.FullName -File -Filter *.json | ForEach-Object { "
          "$o += [pscustomobject]@{ acct=$a; file=$_.Name; t=$_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') } } }; "
          "$o | ConvertTo-Json -Compress")
    out = remote_ps(ps).strip()
    if not out:
        return []
    try:
        v = json.loads(out)
    except Exception:
        return []
    return v if isinstance(v, list) else [v]


def fingerprints(maa_dir: str, windows: list) -> dict:
    """windows: [(账号, 起始 'YYYY-MM-DD HH:MM:SS', 结束 ...)] -> {账号: (干员数, 指纹)}

    窗口切分与指纹**在远端算完**，只回 "<账号>|<干员数>|<指纹>" 4 行 ——
    早期版本把逐行上报整包 JSON 拉回来，几十万字符会被截断导致解析失败。
    """
    if not windows:
        return {}
    rows = "; ".join("@{%s}" % ("a='%s'; s='%s'; e='%s'" % w) for w in windows)
    ps = ("$log='" + maa_dir + "\\debug\\gui.log'; $win=@(" + rows + "); "
          "$sets=@{}; foreach ($w in $win) { $sets[$w.a] = New-Object 'System.Collections.Generic.HashSet[string]' } "
          "foreach ($line in (Get-Content -Tail 60000 -Encoding UTF8 $log)) { "
          "  if ($line -notmatch 'Request body') { continue } "
          "  if ($line -notmatch 'char_') { continue } "
          "  $ts=''; if ($line -match '^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') { $ts=$Matches[1] } "
          "  if (-not $ts) { continue } "
          "  foreach ($w in $win) { if ($ts -ge $w.s -and $ts -le $w.e) { "
          "    foreach ($m in [regex]::Matches($line, 'char_[0-9a-zA-Z_]+')) { [void]$sets[$w.a].Add($m.Value) } } } } "
          "$sha=[System.Security.Cryptography.SHA1]::Create(); "
          "foreach ($w in $win) { $arr=@($sets[$w.a]) | Sort-Object; $j=($arr -join ','); "
          "  $h=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($j))).Replace('-','').Substring(0,10).ToLower(); "
          "  Write-Output ($w.a + '|' + $arr.Count + '|' + $h) }")
    res = {}
    for ln in remote_ps(ps).splitlines():
        parts = ln.strip().split("|")
        if len(parts) == 3 and parts[2]:
            try:
                res[parts[0]] = (int(parts[1]), parts[2])
            except ValueError:
                continue
    return res


def fp(ids: str) -> str:
    return hashlib.sha1(ids.encode("utf-8")).hexdigest()[:10] if ids else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="多账号实例串号校验（只读）")
    ap.add_argument("--script", required=True)
    ap.add_argument("--date")
    ap.add_argument("--window-min", type=int, default=35)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    maa_dir, users = script_users(a.script)
    dates = [a.date] if a.date else [(dt.date.today() - dt.timedelta(days=i)).isoformat() for i in range(0, 4)]
    recs = []
    for d in dates:
        recs.extend(history_records(d))
    # 按时间轴切分：把该脚本 4 个号的所有历史记录按结束时间排序，每条记录一个**不重叠**窗口
    #（起点 = 上一条记录的结束时间，避免窗口重叠把同一批上报算进两个号 ⇒ 假"串号"）
    mine_all = sorted([x for x in recs if x.get("acct") in users], key=lambda x: x.get("t", ""))
    windows, latest, prev_end = [], {}, None
    for x in mine_all:
        end = dt.datetime.strptime(x["t"], "%Y-%m-%d %H:%M:%S")
        start = end - dt.timedelta(minutes=a.window_min)
        if prev_end and prev_end > start:
            start = prev_end
        key = "%s@%s" % (x["acct"], x["t"])
        windows.append((key, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")))
        n0, _t0, _k0 = latest.get(x["acct"], (0, "", ""))
        latest[x["acct"]] = (n0 + 1, x["t"], key)
        prev_end = end
    fpsmap = fingerprints(maa_dir, windows)

    rows = []
    for u in users:
        n, t, key = latest.get(u, (0, "", ""))
        cnt, f = fpsmap.get(key, (0, ""))
        rows.append({"account": u, "records": n, "last_run_end": t, "roster_ids": cnt, "fingerprint": f})

    fps = [r["fingerprint"] for r in rows if r["fingerprint"]]
    noev = [r["account"] for r in rows if not r["fingerprint"]]
    dup = sorted({f for f in fps if fps.count(f) > 1})
    tail = ("；但 %s 本轮没有名册上报、无法判定" % "、".join(noev)) if noev else ""
    if not fps:
        verdict = "证据不足（这些号的窗口内都没有名册上报；可加大 --window-min 或指定 --date）"
    elif dup:
        groups = [" / ".join(r["account"] for r in rows if r["fingerprint"] == d) for d in dup]
        verdict = "发现重复指纹：" + "；".join(groups) + " ⇒ 这些轮次疑似登录了同一个号（串号）" + tail
    else:
        verdict = "各账号名册指纹互不相同 ⇒ 已有证据的号登录的都是各自账号（未发现串号）" + tail
    result = {"script": a.script, "maa_dir": maa_dir, "dates": dates, "window_min": a.window_min,
              "accounts": rows, "duplicate_fingerprints": dup, "verdict": verdict}
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print("%s（%s）｜扫描日期 %s｜窗口 %d 分钟" % (a.script, maa_dir, ",".join(dates), a.window_min))
        for r in rows:
            print("  %-14s 轮次=%-2d 末轮结束=%-19s 名册干员=%-4s 指纹=%s"
                  % (r["account"], r["records"], r["last_run_end"] or "-",
                     (r["roster_ids"] if r["fingerprint"] else "无证据"), r["fingerprint"] or "-"))
        print("判定: " + verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
