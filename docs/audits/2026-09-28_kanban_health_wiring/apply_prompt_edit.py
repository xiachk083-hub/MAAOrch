# -*- coding: utf-8 -*-
r"""巡检 job 52b88b0ffca0 提示词接线：加 `KANBAN_HEALTH=` 行（卡 t_4cd871de）。

用法：
  python apply_prompt_edit.py            # 干跑：只产出 after.txt + diff，打印 sha
  python apply_prompt_edit.py --apply    # 真改：hermes -p mas cron edit … --prompt <new>，回读校验 sha

原则：每处替换必须恰好命中 1 次（多/少 ⇒ 报错退出，不落盘）；应用后逐字节回读比对。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~")
JOBS = os.path.join(HOME, "AppData", "Local", "hermes", "profiles", "mas", "cron", "jobs.json")
HERE = os.path.dirname(os.path.abspath(__file__))
JOB_ID = "52b88b0ffca0"

# ---- 7 处替换（old, new, 期望命中次数）----
REPL = [
    # ① 引言：七检 → 八检
    ("③现场七检（切号闸门/补丁闸门/错误聚合/客户端状态/身份留痕/磁盘与空转/打断源治理）",
     "③现场八检（切号闸门/补丁闸门/错误聚合/客户端状态/身份留痕/磁盘与空转/打断源治理/看板体检）", 1),
    # ② 预算 21 → 22 + 重复上限表加 kanban_health
    ("最多 21 次工具调用", "最多 22 次工具调用", 1),
    ("event_calendar ≤1）；不要用 tool_search", "event_calendar ≤1；kanban_health 行+明细 ≤2）；不要用 tool_search", 1),
    # ③ 第一步设计面：mas-observe 7 → 8
    ("mas-observe 7；工具面刷新可能滞后", "mas-observe 8；工具面刷新可能滞后", 1),
    # ④ 第三步标题
    ("【第三步 现场七检（每项 1 次）】", "【第三步 现场八检（每项 1 次）】", 1),
    # ⑤ 第三步新增第 8 项（插在第 7 项 interrupt_guard 段末尾的换行前）
    ("工具面未提供 ⇒ `INTERRUPT_RISK=UNKNOWN:工具面未提供（mas-observe 未接线）`，不许顶替。\n",
     "工具面未提供 ⇒ `INTERRUPT_RISK=UNKNOWN:工具面未提供（mas-observe 未接线）`，不许顶替。\n"
     "8) `mas_kanban_health()`（看板治理体检，t_67915887 产物）⇒ 原样引用返回的 line："
     "`KANBAN_HEALTH=…`（status=OK/WARN/CRIT；行内每类计数=需动作的 P1·P2，P3 只进汇总）。"
     "status 非 OK（WARN/CRIT）⇒ 再调 1 次 `mas_kanban_health(json=True)` 取明细："
     "证据行列出 P1/P2 条目标题（≤4 条，`[P2] <标题>` 形式，顿号分隔）——只列标题，不粘详情/命令/全文。"
     "工具面未提供 ⇒ `KANBAN_HEALTH=UNKNOWN:工具面未提供（mas-observe 未接线）`，不许顶替。\n", 1),
    # ⑥ 第六步：14 行 → 15 行主表
    ("先 14 行主表", "先 15 行主表", 1),
    # ⑦ 主表加行（放 INTERRUPT_RISK 之后、WIKI_GOV 之前）
    ("INTERRUPT_RISK=…（原样引用 mas_interrupt_guard.risk_line；工具面未提供 ⇒ UNKNOWN:工具面未提供（mas-observe 未接线），不许顶替）\n",
     "INTERRUPT_RISK=…（原样引用 mas_interrupt_guard.risk_line；工具面未提供 ⇒ UNKNOWN:工具面未提供（mas-observe 未接线），不许顶替）\n"
     "KANBAN_HEALTH=…（原样引用 mas_kanban_health.line；status 非 OK 时证据行附 P1/P2 标题 ≤4 条；"
     "工具面未提供 ⇒ UNKNOWN:工具面未提供（mas-observe 未接线），不许顶替）\n", 1),
]


def read_prompt() -> str:
    with open(JOBS, encoding="utf-8") as f:
        d = json.load(f)
    jobs = d if isinstance(d, list) else d.get("jobs", d)
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    for j in jobs:
        if (j.get("id") or "") == JOB_ID:
            return j["prompt"]
    raise SystemExit("job not found: %s" % JOB_ID)


def main() -> int:
    p = read_prompt()
    new = p
    for i, (old, rep, want) in enumerate(REPL, 1):
        n = new.count(old)
        if n != want:
            print("FAIL: 替换 #%d 命中 %d 次（期望 %d）：%r" % (i, n, want, old[:60]))
            return 2
        new = new.replace(old, rep)
    b = os.path.join(HERE, "job_prompt_before.txt")
    a = os.path.join(HERE, "job_prompt_after.txt")
    with open(b, "w", encoding="utf-8", newline="") as f:
        f.write(p)
    with open(a, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    diff = difflib.unified_diff(p.splitlines(True), new.splitlines(True),
                                "job_prompt_before.txt", "job_prompt_after.txt")
    with open(os.path.join(HERE, "job_prompt_diff.txt"), "w", encoding="utf-8", newline="") as f:
        f.writelines(diff)
    print("before: %d chars sha256=%s" % (len(p), hashlib.sha256(p.encode("utf-8")).hexdigest()[:16]))
    print("after : %d chars sha256=%s" % (len(new), hashlib.sha256(new.encode("utf-8")).hexdigest()[:16]))
    for probe in ("KANBAN_HEALTH=", "mas_kanban_health", "最多 22 次工具调用", "先 15 行主表", "mas-observe 8"):
        print("  has %-22r %s" % (probe, probe in new))

    if "--apply" not in sys.argv:
        print("dry-run only（加 --apply 才真改）")
        return 0

    hermes = os.path.join(HOME, "AppData", "Local", "hermes", "bin", "hermes.exe")
    if not os.path.isfile(hermes):
        for cand in ("hermes", "hermes.cmd", "hermes.exe"):
            hermes = cand
            break
    r = subprocess.run([hermes, "-p", "mas", "cron", "edit", JOB_ID, "--prompt", new],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    print("cron edit rc=%s out=%s err=%s" % (r.returncode, (r.stdout or "").strip()[:200], (r.stderr or "").strip()[:300]))
    if r.returncode != 0:
        return 2
    back = read_prompt()
    same = back == new
    print("readback: %d chars sha256=%s | byte_equal=%s" % (
        len(back), hashlib.sha256(back.encode("utf-8")).hexdigest()[:16], same))
    if not same:
        # 常见差分：尾换行。给出提示但仍按 diff 报错
        print("MISMATCH: 回读与目标不一致（尾差=%r）" % (back[-8:] != new[-8:]))
        return 2
    print("APPLIED OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
