#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤2-侦察：确认远端 history 结构 / 文件大小 / json 与 log 的实际形态（只读）。"""
import base64
import json
import os
import subprocess
import sys

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import maa_error_daily as med  # noqa: E402


def ssh_ps(script, timeout=240):
    prefix = ("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; "
              "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ")
    enc = (prefix + script).encode("utf-16-le")
    b64 = base64.b64encode(enc).decode("ascii")
    cmd = [med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no",
           "-o", "ConnectTimeout=20", med.SSH_HOST,
           "powershell -NoProfile -EncodedCommand " + b64]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                       env=med._child_env())
    return (r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), r.returncode)


SCRIPT = r"""
$hist = 'E:\AUTO-MAS\history'
Write-Output '=== 可用日目录（最近 32 天）==='
Get-ChildItem $hist -Directory | Sort-Object Name -Descending | Select-Object -First 32 |
  ForEach-Object { $n=(Get-ChildItem $_.FullName -Directory).Count; Write-Output ($_.Name + '  accounts=' + $n) }
Write-Output ''
$d = Join-Path $hist '2026-09-08'
Write-Output ('=== 2026-09-08 目录：目录数=' + (Get-ChildItem $d -Directory).Count + ' 文件数=' + (Get-ChildItem $d -File).Count + ' ===')
Get-ChildItem $d -Directory | Select-Object -First 5 | ForEach-Object { Write-Output (' 账号目录: ' + $_.Name) }
Write-Output ''
$acct = '骰子'
$a = Join-Path $d $acct
Write-Output ('=== 账号目录 ' + $acct + ' 文件清单（前 40）===')
Get-ChildItem $a -File | Select-Object -First 40 | ForEach-Object {
  Write-Output ('{0}  {1}  {2}' -f $_.Name, $_.Length, $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
}
Write-Output ''
$fj = Join-Path $a '明日方舟-官-9-15-42-00.json'
Write-Output ('=== JSON 全文 ' + $fj + ' ===')
if (Test-Path $fj) { Get-Content -Raw -Encoding UTF8 $fj } else { Write-Output '(缺)' }
Write-Output ''
$fl = Join-Path $a '明日方舟-官-9-15-42-00.log'
Write-Output ('=== LOG 行数/大小 ' + $fl + ' ===')
if (Test-Path $fl) {
  $ln = (Get-Content -Encoding UTF8 $fl).Count
  Write-Output ('lines=' + $ln + ' bytes=' + (Get-Item $fl).Length)
  Write-Output '--- LOG 前 15 行 ---'
  Get-Content -Encoding UTF8 $fl -TotalCount 15 | ForEach-Object { Write-Output $_ }
  Write-Output '--- LOG 末 15 行 ---'
  Get-Content -Encoding UTF8 $fl -Tail 15 | ForEach-Object { Write-Output $_ }
} else { Write-Output '(缺)' }
"""

out, err, rc = ssh_ps(SCRIPT)
with open(os.path.join(OUT, "recon.txt"), "w", encoding="utf-8") as f:
    f.write(out + "\n---STDERR---\n" + err + "\nrc=%s\n" % rc)
print(out[:9000])
print("---STDERR---", err[:2000], "rc=", rc)
