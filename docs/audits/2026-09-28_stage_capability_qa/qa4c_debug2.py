#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 调试2：把真实 batch1 脚本走两跳，逐步定位（上传完整性与 -File 结果）。"""
import base64
import json
import os
import subprocess
import sys

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import maa_error_daily as med  # noqa: E402

PROBE = r"E:\_hermes_stagecapqa_probe.ps1"

with open(os.path.join(OUT, "manifest.json"), encoding="utf-8") as f:
    entries = json.load(f)["entries"]
by_day = {}
for e in entries:
    by_day.setdefault(e["day"], []).append(e)
ordered = []
for d in sorted(by_day):
    ordered.extend(by_day[d])
batch = ordered[:27]

FILTER = ("GetFightStage|Task Chain|开始行动|掉落统计|任务出错|代理指挥失误|剿灭|无奖励|关卡计划内|"
          "任务跳过|理智作战|重连|掉线|连接|唤醒|客户端|模拟器|截图|CloseAnno|StartUp|SubTaskError|"
          "TaskChainError|错误|失败|异常|理智|完成任务|任务已全部完成|任务已完成|退出|注意|警告|WARN|"
          "停止|中断|超时")


def ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


HEAD = r"""
$ErrorActionPreference = 'Continue'
$hist = 'E:\AUTO-MAS\history'
function Show-Entry([string]$day, [string]$acct, [string]$file, [string]$pair, [string]$why) {
  $b = Join-Path (Join-Path $hist $day) $acct
  $fj = Join-Path $b $file
  $fl = Join-Path $b ([System.IO.Path]::ChangeExtension($file, '.log'))
  Write-Output ('###ENTRY|' + $day + '|' + $acct + '|' + $file + '|' + $pair + '|' + $why)
  Write-Output '###JSON'
  if (Test-Path $fj) { Get-Content -Raw -Encoding UTF8 $fj } else { Write-Output '(JSON 缺)' }
  Write-Output '###JSON_END'
  if (Test-Path $fl) {
    $all = @(Get-Content -Encoding UTF8 $fl)
    Write-Output ('###LOG|lines=' + $all.Count + '|bytes=' + (Get-Item $fl).Length)
    Write-Output '##HEAD'
    $all | Select-Object -First 6 | ForEach-Object { Write-Output $_ }
    Write-Output '##FILTER'
    $pat = '@@PAT@@'
    $all | Where-Object { $_ -match $pat } | ForEach-Object { Write-Output $_ }
    Write-Output '##TAIL'
    $all | Select-Object -Last 4 | ForEach-Object { Write-Output $_ }
  } else {
    Write-Output '###LOG|MISSING'
  }
  Write-Output '###END'
}
"""

parts = [HEAD.replace("@@PAT@@", ps_quote(FILTER))]
for e in batch:
    parts.append("Show-Entry %s %s %s %s %s" % (
        ps_quote(e["day"]), ps_quote(e["acct"]), ps_quote(e["file"]),
        ps_quote(e["pair"]), ps_quote(e["why"])))
script = "\n".join(parts)
print("script chars=%d bytes=%d" % (len(script), len(script.encode("utf-8"))))

prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
          "$OutputEncoding=[System.Text.Encoding]::UTF8; "
          "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
payload = b"\xef\xbb\xbf" + (prefix + script).encode("utf-8")
print("payload bytes=%d b64 chars=%d" % (len(payload), len(base64.b64encode(payload))))

# 方案A：单行 b64 上传
b64 = base64.b64encode(payload).decode("ascii")
up_script = ("$b64='%s'\n"
             "$b=[Convert]::FromBase64String($b64)\n"
             "[IO.File]::WriteAllBytes('%s', $b)\n"
             "Write-Output ('written ' + $b.Length)\n" % (b64, PROBE))
r = subprocess.run([med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=20", med.SSH_HOST,
                    "chcp 65001 >nul & powershell -NoProfile -Command -"],
                   input=up_script.encode("utf-8"), capture_output=True, timeout=180,
                   env=med._child_env())
print("A-UPLOAD rc=%s out=%r" % (r.returncode, r.stdout.decode("utf-8", "replace")[:200]))

chk = ("$p='%s'\n(Get-Item $p).Length\n(Get-Content -Encoding UTF8 $p -TotalCount 2) -join ' | '\n" % PROBE)
r = subprocess.run([med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=20", med.SSH_HOST,
                    "powershell -NoProfile -EncodedCommand " + base64.b64encode(chk.encode("utf-16-le")).decode("ascii")],
                   capture_output=True, stdin=subprocess.DEVNULL, timeout=180, env=med._child_env())
print("A-CHECK rc=%s out=%r" % (r.returncode, r.stdout.decode("utf-8", "replace")[:300]))

r = subprocess.run([med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=20", med.SSH_HOST,
                    "powershell -NoProfile -ExecutionPolicy Bypass -File " + PROBE],
                   capture_output=True, stdin=subprocess.DEVNULL, timeout=600, env=med._child_env())
print("A-RUN rc=%s stdout=%d bytes stderr=%r" % (r.returncode, len(r.stdout),
                                                 r.stderr.decode("utf-8", "replace")[:400]))
open(os.path.join(OUT, "raw", "dbg_a_run.txt"), "wb").write(r.stdout)
