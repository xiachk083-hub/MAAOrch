# -*- coding: utf-8 -*-
r"""巡检 job 52b88b0ffca0 提示词接线提案：KANBAN_HEALTH=（卡 t_4cd871de → 集成 owner t_403cd83b）。

背景：本卡（t_4cd871de）原计划自行 apply，按 lead 11:10 协调改由 t_403cd83b 统一集成
（避免三方同时改 cron prompt）。本脚本 = **rebase 提案**：把 kanban_health 的 8 处替换
重挂到**当前活体 prompt**（已含 t_537fbff2 的 audit_waterline 收尾项）之上。

机制：与仓库既有做法一致（apply_patrol_prompt.py / tmp/hermes_bridge/apply_v3.py 同款）：
    hermes -p mas cron edit 52b88b0ffca0 --prompt <new>

用法（**默认只干跑**，不写）：
    python apply_prompt_rebase.py            # 干跑：断言每处替换恰好命中 1 次 → 产出 rebased.txt/diff + sha
    python apply_prompt_rebase.py --apply    # 真改：先备份 jobs.json → cron edit → 逐字节回读校验
回滚：cp %LOCALAPPDATA%\hermes\profiles\mas\cron\jobs.json.bak-t4cd-kbrb-<ts> jobs.json
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
CRON = os.path.join(HOME, "AppData", "Local", "hermes", "profiles", "mas", "cron")
JOBS = os.path.join(CRON, "jobs.json")
HERE = os.path.dirname(os.path.abspath(__file__))
JOB = "52b88b0ffca0"

KANBAN_ITEM = (
    "9) `mas_kanban_health()`（看板治理体检，t_67915887 产物）⇒ 原样引用返回的 line："
    "`KANBAN_HEALTH=…`（status=OK/WARN/CRIT；行内每类计数=需动作的 P1·P2，P3 只进汇总）。"
    "status 非 OK（WARN/CRIT）⇒ 再调 1 次 `mas_kanban_health(json=True)` 取明细："
    "证据行列出 P1/P2 条目标题（≤4 条，`[P2] <标题>` 形式，顿号分隔）——只列标题，不粘详情/命令/全文。"
    "工具面未提供 ⇒ `KANBAN_HEALTH=UNKNOWN:工具面未提供（mas-observe 未接线 kanban_health）`，不许顶替。"
)
KANBAN_ROW = (
    "KANBAN_HEALTH=…（原样引用 mas_kanban_health.line；status 非 OK 时证据行附 P1/P2 标题 ≤4 条；"
    "工具面未提供 ⇒ UNKNOWN:工具面未提供（mas-observe 未接线 kanban_health），不许顶替）"
)

# ---- 8 处替换（old, new），每处必须恰好命中 1 次 ----
REPL = [
    # ① 引言：现场七检+审计水位 → 追加"看板体检"
    ("③现场七检+审计水位（切号闸门/补丁闸门/错误聚合/客户端状态/身份留痕/磁盘与空转/打断源治理/审计水位）",
     "③现场七检+审计水位+看板体检（切号闸门/补丁闸门/错误聚合/客户端状态/身份留痕/磁盘与空转/打断源治理/审计水位/看板体检）"),
    # ② 第三步标题：与 ① 同步
    ("【第三步 现场七检（每项 1 次）】", "【第三步 现场七检+审计水位+看板体检（每项 1 次）】"),
    # ③ 预算 22 → 23（kanban 行+明细最多 2 次调用）
    ("最多 22 次工具调用", "最多 23 次工具调用"),
    # ④ 重复上限表：追加 kanban_health
    ("audit_waterline ≤1）；不要用 tool_search", "audit_waterline ≤1；kanban_health 行+明细 ≤2）；不要用 tool_search"),
    # ⑤ 设计面：点名 kanban_health（计数 9 不变——audit 已把并发的本工具计入）
    ("mas-observe 9（含 audit_waterline）", "mas-observe 9（含 audit_waterline / kanban_health）"),
    # ⑥ 第三步新增第 9 项（接在 audit_waterline 项之后）
    ("（mas-observe 未接线 audit_waterline）`，不许顶替。\n",
     "（mas-observe 未接线 audit_waterline）`，不许顶替。\n" + KANBAN_ITEM + "\n"),
    # ⑦ 第六步主表 15 → 16 行
    ("先 15 行主表", "先 16 行主表"),
    # ⑧ 主表加行（放 AUDIT_STATUS 之后、结论= 之前）
    ("，不许顶替）\n结论=", "，不许顶替）\n" + KANBAN_ROW + "\n结论="),
]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def read_prompt() -> str:
    with open(JOBS, encoding="utf-8") as f:
        d = json.load(f)
    jobs = d if isinstance(d, list) else d.get("jobs", d)
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    for j in jobs:
        if (j.get("id") or "") == JOB:
            return j["prompt"]
    raise SystemExit("job not found: %s" % JOB)


def main() -> int:
    p = read_prompt()
    nl = "\r\n" if "\r\n" in p else "\n"
    print("live prompt: %d chars sha=%s (newline=%s)" % (len(p), sha(p), repr(nl)))
    if nl != "\n":
        print("FAIL: 活体 prompt 使用 CRLF，本脚本替换串按 LF 预置；先改脚本再跑（防呆）。")
        return 2

    new = p
    bad = []
    for i, (old, rep) in enumerate(REPL, 1):
        n = new.count(old)
        print("  #%d hits=%d %s" % (i, n, "OK" if n == 1 else "FAIL"))
        if n != 1:
            bad.append("#%d hits=%d: %r" % (i, n, old[:50]))
            continue
        new = new.replace(old, rep)
    if bad:
        print("FAIL: 替换命中数不符（prompt 可能已被并发编辑），未落盘：\n  " + "\n  ".join(bad))
        return 2

    txt = os.path.join(HERE, "job_prompt_rebased.txt")
    dif = os.path.join(HERE, "job_prompt_rebased.diff")
    with open(os.path.join(HERE, "job_prompt_live_at_rebase.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(p)
    with open(txt, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    with open(dif, "w", encoding="utf-8", newline="") as f:
        f.writelines(difflib.unified_diff(p.splitlines(True), new.splitlines(True),
                                          "live(now)", "rebased"))
    print("rebased: %d chars sha=%s" % (len(new), sha(new)))
    for probe in ("KANBAN_HEALTH=", "mas_kanban_health", "最多 23 次工具调用", "先 16 行主表",
                  "mas-observe 9（含 audit_waterline / kanban_health）"):
        print("  has %-44r %s" % (probe, probe in new))
    print("产出：%s / %s" % (txt, dif))

    if "--apply" not in sys.argv:
        print("dry-run only（--apply 才真改；应用前请与 t_403cd83b 确认集成权）")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(CRON, "jobs.json.bak-t4cd-kbrb-%s" % ts)
    shutil.copy2(JOBS, bak)
    print("backup: %s" % bak)
    exe = os.path.expandvars(r"%LOCALAPPDATA%\hermes\bin\hermes.exe")
    r = subprocess.run([exe, "-p", "mas", "cron", "edit", JOB, "--prompt", new],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=120)
    print("cron edit rc=%s" % r.returncode)
    print((r.stdout or "").strip()[:300])
    if r.stderr:
        print("stderr: " + (r.stderr or "").strip()[:300])
    if r.returncode != 0:
        return 2
    back = read_prompt()
    if back == new:
        print("readback: byte_equal=True sha=%s APPLIED OK" % sha(back))
        return 0
    if back.strip() == new.strip():
        print("readback: 仅尾空白差异（target=%s back=%s），视为 OK" % (sha(new), sha(back)))
        return 0
    print("MISMATCH: 回读与目标不一致（target sha=%s back sha=%s）；回滚见文件头。" % (sha(new), sha(back)))
    return 2


if __name__ == "__main__":
    sys.exit(main())
