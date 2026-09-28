# -*- coding: utf-8 -*-
r"""把 patrol_prompt.after.txt 应用为 cron job 52b88b0ffca0 的 prompt（卡 t_537fbff2）。

为什么不是手敲 prompt：本 job 的 prompt 是 11KB 中文生产文本，手抄一个字符的错都会被下一轮巡检放大。
本脚本按仓库既有做法（tmp/hermes_bridge/apply_v3.py 同款）走 CLI：

    hermes -p mas cron edit 52b88b0ffca0 --prompt <文件内容>

回滚（任一即生效，jobs.json 是唯一真源）：
    copy jobs.json.bak-auditpatrol-<ts> → jobs.json
    （备份由本卡动手前创建于 %LOCALAPPDATA%\hermes\profiles\mas\cron\）
或：
    hermes -p mas cron edit 52b88b0ffca0 --prompt "$(cat docs/audits/2026-09-28_audit_patrol/patrol_prompt.before.txt)"

用法：python docs/audits/2026-09-28_audit_patrol/apply_patrol_prompt.py [--dry-run]
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AFTER = os.path.join(HERE, "patrol_prompt.after.txt")
JOB = "52b88b0ffca0"


def main() -> int:
    prompt = open(AFTER, encoding="utf-8").read().strip()
    exe = os.path.expandvars(r"%LOCALAPPDATA%\hermes\bin\hermes.exe")
    print("job=%s prompt=%d chars (from %s)" % (JOB, len(prompt), os.path.basename(AFTER)))
    if "--dry-run" in sys.argv:
        print("dry-run：不写。")
        return 0
    r = subprocess.run([exe, "-p", "mas", "cron", "edit", JOB, "--prompt", prompt],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("cron edit rc=%s" % r.returncode)
    print((r.stdout or "").strip()[:400])
    if r.stderr:
        print("stderr: " + (r.stderr or "").strip()[:400])
    return 0 if r.returncode == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
