#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 调试：两跳上传/执行链路自检。"""
import base64
import os
import subprocess
import sys

ROOT = r"D:\1-2\MAAOrch"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import maa_error_daily as med  # noqa: E402

PROBE = r"E:\_hermes_stagecapqa_probe.ps1"

print("ssh exe:", med._ssh_exe())
print("key:", med.KEY, "exists:", os.path.exists(med.KEY))
print("host:", med.SSH_HOST)

script = "Write-Output 'HELLO from probe'\nWrite-Output ('cwd=' + (Get-Location).Path)\n"
payload = b"\xef\xbb\xbf" + script.encode("utf-8")
b64 = base64.b64encode(payload).decode("ascii")
up_script = ("$b64='%s'\n"
             "$b=[Convert]::FromBase64String($b64)\n"
             "$p='%s'\n"
             "[IO.File]::WriteAllBytes($p, $b)\n"
             "Write-Output ('written ' + $b.Length + ' to ' + $p)\n"
             "Write-Output ('exists=' + (Test-Path $p))\n" % (b64, PROBE))

up = [med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
      med.SSH_HOST, "chcp 65001 >nul & powershell -NoProfile -Command -"]
r = subprocess.run(up, input=up_script.encode("utf-8"), capture_output=True, timeout=180,
                   env=med._child_env())
print("== UPLOAD rc=%s" % r.returncode)
print("stdout:", r.stdout.decode("utf-8", "replace")[:800])
print("stderr:", r.stderr.decode("utf-8", "replace")[:800])

run = [med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
       med.SSH_HOST, "powershell -NoProfile -ExecutionPolicy Bypass -File " + PROBE]
r2 = subprocess.run(run, capture_output=True, stdin=subprocess.DEVNULL, timeout=180,
                    env=med._child_env())
print("== RUN rc=%s" % r2.returncode)
print("stdout:", r2.stdout.decode("utf-8", "replace")[:800])
print("stderr:", r2.stderr.decode("utf-8", "replace")[:800])

# 变体：EncodedCommand 小脚本（对照，qa2 已证可用）
enc = script.encode("utf-16-le")
b = base64.b64encode(enc).decode("ascii")
run2 = [med._ssh_exe(), "-i", med.KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
        med.SSH_HOST, "powershell -NoProfile -EncodedCommand " + b]
r3 = subprocess.run(run2, capture_output=True, stdin=subprocess.DEVNULL, timeout=180,
                    env=med._child_env())
print("== ENC rc=%s" % r3.returncode)
print("stdout:", r3.stdout.decode("utf-8", "replace")[:800])
print("stderr:", r3.stderr.decode("utf-8", "replace")[:800])
