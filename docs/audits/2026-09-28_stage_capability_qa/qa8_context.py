#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤8：取关键尝试的「完整上下文窗口」原始日志（不做判据过滤）。

用途：failed 类要判"关卡级失败 vs 客户端未就绪"；① 类要判 Success! 无掉落的真实原因。
"""
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

# (标签, day, acct, file, start正则, end正则, 前置行数, 后置行数)
JOBS = [
    ("CA5fail_peier_081147", "2026-09-02", "皮埃尔商店", "明日方舟-官-41-08-11-47.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错: 理智作战", 3, 3),
    ("CA5fail_peier_084505", "2026-09-02", "皮埃尔商店", "明日方舟-官-41-08-45-05.json",
     r"GetFightStage", r"任务出错: 理智作战", 2, 2),
    ("LS6fail_tansi_023910", "2026-09-07", "檀丝江喜", "明日方舟-官-14-02-39-10.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错: 理智作战", 3, 3),
    ("SR7fail_tbNick_175800", "2026-09-08", "tbNick_h56zy", "明日方舟-b-12-17-58-00.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错: 理智作战", 3, 3),
    ("SR5fail_gitano_173942", "2026-09-08", "Gitano", "明日方舟-官-3-搓玉-17-39-42.json",
     r"Start Task Chain: Fight, Task ID: 2", r"任务出错: 理智作战", 3, 3),
    ("CA5net_heqing_021958", "2026-09-02", "河清灯_别寄圆通", "明日方舟-日-4-02-19-58.json",
     r"完成任务: 开始唤醒", r"游戏掉线", 4, 4),
    ("n1drop_leung_011626", "2026-08-31", "LEUNG", "明日方舟-官-19-01-16-26.json",
     r"Start Task Chain: Fight, Task ID: 2", r"Completed Task Chain: Fight, Task ID: 2", 2, 2),
    ("notopen_leung_020512", "2026-09-01", "LEUNG", "明日方舟-b-1-02-05-12.json",
     r"selected null", r"剩余理智|任务已全部完成", 8, 12),
    ("SR7_ok_xunzhao_0908", "2026-09-08", "寻找勇者的大魔王", "明日方舟-官-33-??.json", "", "", 0, 0),
]
JOBS = [j for j in JOBS if not j[3].startswith("明日方舟-官-33-??")]


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
}
"""]
for tag, day, acct, file, st, en, pre, post in JOBS:
    parts.append("Ctx %s %s %s %s %s %s %d %d" % (quote(tag), quote(day), quote(acct), quote(file),
                                                   quote(st), quote(en), pre, post))
script = "\n".join(parts)
out, err, rc = ssh_ps(script)
with open(os.path.join(CTX, "windows.txt"), "w", encoding="utf-8") as f:
    f.write(out + "\n---STDERR---\n" + err + "\nrc=%s\n" % rc)
print("rc=%s bytes=%d" % (rc, len(out)))
print(out[:6000])
