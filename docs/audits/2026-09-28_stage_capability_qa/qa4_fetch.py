#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤4：按 manifest 取原始证据（远端只读；json 全文 + log 过滤行）。

输出：docs/audits/2026-09-28_stage_capability_qa/raw/batch_*.txt
每条：###ENTRY|day|acct|file|pair|why / ###JSON / ###LOG(头+过滤+尾) / 结束标记
"""
import base64
import json
import os
import subprocess
import sys

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
RAW = os.path.join(OUT, "raw")
os.makedirs(RAW, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import maa_error_daily as med  # noqa: E402

with open(os.path.join(OUT, "manifest.json"), encoding="utf-8") as f:
    entries = json.load(f)["entries"]

# 按 day 分组，再切成批次（约 27 条/批）
by_day = {}
for e in entries:
    by_day.setdefault(e["day"], []).append(e)
ordered = []
for d in sorted(by_day):
    ordered.extend(by_day[d])
BATCH = 27
batches = [ordered[i:i + BATCH] for i in range(0, len(ordered), BATCH)]

FILTER = ("GetFightStage|Task Chain|开始行动|掉落统计|任务出错|代理指挥失误|剿灭|无奖励|关卡计划内|"
          "任务跳过|理智作战|重连|掉线|连接|唤醒|客户端|模拟器|截图|CloseAnno|StartUp|SubTaskError|"
          "TaskChainError|错误|失败|异常|理智|完成任务|任务已全部完成|任务已完成|退出|注意|警告|WARN|"
          "停止|中断|超时")

REMOTE_PROBE = r"E:\_hermes_stagecapqa_probe.ps1"   # 两跳落点（错开 stage_capability 与巡检的探针文件）


def ssh_ps(script, timeout=900):
    """两跳：b64 单行上传 + -File 执行（EncodedCommand 撞 8191 上限，2026-09-28 实测 rc=1 空输出）。"""
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    payload = b"\xef\xbb\xbf" + (prefix + script).encode("utf-8")
    b64 = base64.b64encode(payload).decode("ascii")
    up_script = ("$b64='%s'\n"
                 "$b=[Convert]::FromBase64String($b64)\n"
                 "[IO.File]::WriteAllBytes('%s', $b)\n"
                 "Write-Output ('written ' + $b.Length)\n" % (b64, REMOTE_PROBE))
    up = [med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no",
          "-o", "ConnectTimeout=20", med.SSH_HOST,
          "chcp 65001 >nul & powershell -NoProfile -Command -"]
    r = subprocess.run(up, input=up_script.encode("utf-8"), capture_output=True,
                       timeout=min(timeout, 180), env=med._child_env())
    if b"written" not in r.stdout:
        raise RuntimeError("SSH 上传未确认: " + (r.stdout + r.stderr).decode("utf-8", "replace")[:300])
    run = [med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=20", med.SSH_HOST,
           "powershell -NoProfile -ExecutionPolicy Bypass -File " + REMOTE_PROBE]
    r = subprocess.run(run, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                       env=med._child_env())
    return (r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), r.returncode)


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


def ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


for i, batch in enumerate(batches, 1):
    parts = [HEAD.replace("@@PAT@@", FILTER)]
    for e in batch:
        parts.append("Show-Entry %s %s %s %s %s" % (
            ps_quote(e["day"]), ps_quote(e["acct"]), ps_quote(e["file"]),
            ps_quote(e["pair"]), ps_quote(e["why"])))
    script = "\n".join(parts)
    print("[batch %d/%d] %d 条 ..." % (i, len(batches), len(batch)))
    out, err, rc = ssh_ps(script)
    path = os.path.join(RAW, "batch_%02d.txt" % i)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out + "\n---STDERR---\n" + err + "\nrc=%s\n" % rc)
    n_entry = out.count("###ENTRY|")
    print("    -> %s  (%d bytes, entries=%d, rc=%s)" % (path, len(out), n_entry, rc))
    if n_entry != len(batch):
        print("    !! 条目数不符：期望 %d，得到 %d" % (len(batch), n_entry))
