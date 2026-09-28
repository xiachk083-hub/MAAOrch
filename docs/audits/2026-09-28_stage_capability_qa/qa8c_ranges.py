#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤8c：按绝对行号区间取原始日志（解 v 的 LS-6/LS-5 之谜）。"""
import base64
import os
import subprocess
import sys

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
CTX = os.path.join(OUT, "context")
os.makedirs(CTX, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import maa_error_daily as med  # noqa: E402

REMOTE_PROBE = r"E:\_hermes_stagecapqa_ctx.ps1"

JOBS = [
    ("v_165450", "2026-09-07", "v", "明日方舟-官-5-16-54-50.json", "1-25,88-190"),
    ("v_014157", "2026-09-07", "v", "明日方舟-官-5-01-41-57.json", "88-175"),
    ("u1739_025241", "2026-09-07", "用户_173920298", "明日方舟-b-3-02-52-41.json", "88-175"),
]


def ssh_ps(script, timeout=600):
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    payload = b"\xef\xbb\xbf" + (prefix + script).encode("utf-8")
    b64 = base64.b64encode(payload).decode("ascii")
    up_script = ("$b64='%s'\n$b=[Convert]::FromBase64String($b64)\n"
                 "[IO.File]::WriteAllBytes('%s', $b)\nWrite-Output ('written ' + $b.Length)\n"
                 % (b64, REMOTE_PROBE))
    r = subprocess.run([med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=20", med.SSH_HOST,
                        "chcp 65001 >nul & powershell -NoProfile -Command -"],
                       input=up_script.encode("utf-8"), capture_output=True, timeout=180,
                       env=med._child_env())
    if b"written" not in r.stdout:
        raise RuntimeError("上传未确认: " + (r.stdout + r.stderr).decode("utf-8", "replace")[:200])
    r = subprocess.run([med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=20", med.SSH_HOST,
                        "powershell -NoProfile -ExecutionPolicy Bypass -File " + REMOTE_PROBE],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                       env=med._child_env())
    return r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), r.returncode


def quote(s):
    return "'" + s.replace("'", "''") + "'"


parts = [r"""
$ErrorActionPreference = 'Continue'
$hist = 'E:\AUTO-MAS\history'
function Rng([string]$tag, [string]$day, [string]$acct, [string]$file, [string]$ranges) {
  $fl = Join-Path (Join-Path (Join-Path $hist $day) $acct) ([System.IO.Path]::ChangeExtension($file, '.log'))
  Write-Output ('@@@' + $tag + '|' + $day + '|' + $acct + '|' + $file)
  if (-not (Test-Path $fl)) { Write-Output '(LOG 缺)'; return }
  $all = @(Get-Content -Encoding UTF8 $fl)
  Write-Output ('(共 ' + $all.Count + ' 行)')
  foreach ($rg in $ranges.Split(',')) {
    $p = $rg.Split('-')
    $a = [int]$p[0] - 1; $b = [int]$p[1] - 1
    Write-Output ('--- 行 ' + $p[0] + '..' + $p[1] + ' ---')
    for ($k = $a; $k -le $b -and $k -lt $all.Count; $k++) { Write-Output (('{0,4}| ' -f ($k+1)) + $all[$k]) }
  }
}
"""]
for tag, day, acct, file, ranges in JOBS:
    parts.append("Rng %s %s %s %s %s" % (quote(tag), quote(day), quote(acct), quote(file), quote(ranges)))
out, err, rc = ssh_ps("\n".join(parts))
with open(os.path.join(CTX, "ranges.txt"), "w", encoding="utf-8") as f:
    f.write(out + "\n---STDERR---\n" + err + "\nrc=%s\n" % rc)
print("rc=%s bytes=%d" % (rc, len(out)))
