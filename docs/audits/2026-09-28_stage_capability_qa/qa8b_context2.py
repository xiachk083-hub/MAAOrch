#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤8b：第二轮上下文窗口（LS-6 unknown 真因 / not_open 最近案例 / SR-7 家族对照 / 截图失败时序）。"""
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
    ("v_LS6_165450", "2026-09-07", "v", "明日方舟-官-5-16-54-50.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错|掉落统计", 2, 10),
    ("v_LS6_014157", "2026-09-07", "v", "明日方舟-官-5-01-41-57.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错|掉落统计", 2, 10),
    ("u1739_LS6_025241", "2026-09-07", "用户_173920298", "明日方舟-b-3-02-52-41.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错|掉落统计", 2, 10),
    ("tansi_LS6_startup", "2026-09-07", "檀丝江喜", "明日方舟-官-14-02-39-10.json",
     r"开始任务: 开始唤醒", r"任务出错: 理智作战", 2, 2),
    ("leung_LS5_null", "2026-09-28", "LEUNG", "明日方舟-官-19-04-46-53.json",
     r"selected null", r"已停止|中止|停止|任务已全部完成|出错", 6, 12),
    ("dice_SR7_recent", "2026-09-26", "骰子", "明日方舟-官-9-15-42-00.json",
     r"GetFightStage", r"已停止|中止|停止|任务已全部完成|出错", 2, 10),
    ("emperor_SR8", "2026-09-27", "帝国猛虎", "明日方舟-官-38-01-15-44.json",
     r"GetFightStage", r"已停止|中止|停止|任务已全部完成|出错", 2, 10),
    ("raiz_17", "2026-09-21", "Raiz", "明日方舟-台-1-03-47-11.json",
     r"GetFightStage", r"已停止|中止|停止|任务已全部完成|出错|退出", 2, 10),
    ("peier_CA5_0941", "2026-09-04", "皮埃尔商店", "明日方舟-官-41-19-41-59.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错", 2, 8),
    ("yiqi_SR7", "2026-09-08", "以七", "明日方舟-官-13-16-54-12.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错|掉落统计", 2, 8),
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
function Ctx([string]$tag, [string]$day, [string]$acct, [string]$file, [string]$st, [string]$en, [int]$pre, [int]$post) {
  $fl = Join-Path (Join-Path (Join-Path $hist $day) $acct) ([System.IO.Path]::ChangeExtension($file, '.log'))
  Write-Output ('@@@' + $tag + '|' + $day + '|' + $acct + '|' + $file)
  if (-not (Test-Path $fl)) { Write-Output '(LOG 缺)'; return }
  $all = @(Get-Content -Encoding UTF8 $fl)
  $i = -1; $j = -1
  for ($k = 0; $k -lt $all.Count; $k++) {
    if ($i -lt 0 -and $all[$k] -match $st) { $i = $k }
    elseif ($i -ge 0 -and $j -lt 0 -and $all[$k] -match $en) { $j = $k; break }
  }
  if ($i -lt 0) { Write-Output ('(start 未命中: ' + $st + ')'); return }
  if ($j -lt 0) { $j = [Math]::Min($i + 20, $all.Count - 1) }
  $a = [Math]::Max(0, $i - $pre); $b = [Math]::Min($all.Count - 1, $j + $post)
  Write-Output ('(窗口 ' + $a + '..' + $b + ' / 共 ' + $all.Count + ' 行)')
  for ($k = $a; $k -le $b; $k++) { Write-Output (('{0,4}| ' -f $k) + $all[$k]) }
  if (($b - $a) -gt 140) { Write-Output '...(窗口过长已截断展示)' }
}
"""]
for tag, day, acct, file, st, en, pre, post in JOBS:
    parts.append("Ctx %s %s %s %s %s %s %d %d" % (quote(tag), quote(day), quote(acct), quote(file),
                                                   quote(st), quote(en), pre, post))
out, err, rc = ssh_ps("\n".join(parts))
with open(os.path.join(CTX, "windows2.txt"), "w", encoding="utf-8") as f:
    f.write(out + "\n---STDERR---\n" + err + "\nrc=%s\n" % rc)
print("rc=%s bytes=%d" % (rc, len(out)))
