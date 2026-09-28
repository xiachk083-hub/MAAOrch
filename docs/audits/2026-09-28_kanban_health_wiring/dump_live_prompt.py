# -*- coding: utf-8 -*-
"""t_4cd871de 复核脚本 2（只读）：dump 活体提示词 + 与各备份/留档对比。"""
import difflib
import hashlib
import json
import os
import glob

HOME = os.path.expanduser("~")
CRON = os.path.join(HOME, "AppData", "Local", "hermes", "profiles", "mas", "cron")
JOBS = os.path.join(CRON, "jobs.json")
HERE = os.path.dirname(os.path.abspath(__file__))
JOB = "52b88b0ffca0"


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    jobs = d if isinstance(d, list) else d.get("jobs", d)
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    return jobs


def prompt_of(path, job_id=JOB):
    for j in load(path):
        if (j.get("id") or "") == job_id:
            return j
    return None


def main():
    j = prompt_of(JOBS)
    live = j["prompt"]
    with open(os.path.join(HERE, "live_prompt_now.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(live)
    print("LIVE job %s: %d chars sha=%s enabled=%s" % (JOB, len(live), sha(live), j.get("enabled")))
    probes = ["现场七检", "现场八检", "KANBAN_HEALTH=", "mas_kanban_health", "mas-observe 8",
              "mas-observe 9", "AUDIT_STATUS=", "audit_waterline", "最多 22 次工具调用",
              "先 15 行主表", "先 16 行主表", "14 行主表", "16 行主表"]
    for p in probes:
        print("  %-26r %s" % (p, p in live))
    print()
    for b in sorted(glob.glob(os.path.join(CRON, "jobs.json.bak-*"))):
        r = prompt_of(b)
        if r is None:
            print("BAK %-52s (no job %s)" % (os.path.basename(b), JOB))
            continue
        p = r["prompt"]
        print("BAK %-52s %5d chars sha=%s kh=%-5s audit=%-5s n22=%-5s n15=%-5s" % (
            os.path.basename(b), len(p), sha(p),
            "KANBAN_HEALTH=" in p, "AUDIT_STATUS=" in p,
            "最多 22 次工具调用" in p, "先 15 行主表" in p))
    print()
    for name in ("job_prompt_before.txt", "job_prompt_after.txt"):
        q = open(os.path.join(HERE, name), encoding="utf-8", newline="").read()
        print("FILE %-24s %5d chars sha=%s" % (name, len(q), sha(q)))
    # 与 after 的差异摘要
    after = open(os.path.join(HERE, "job_prompt_after.txt"), encoding="utf-8", newline="").read()
    d = list(difflib.unified_diff(after.splitlines(True), live.splitlines(True),
                                  "after.txt", "live_now.txt"))
    with open(os.path.join(HERE, "live_vs_after.diff"), "w", encoding="utf-8", newline="") as f:
        f.writelines(d)
    print("\nlive_vs_after.diff: %d diff lines -> %s" % (len(d), os.path.join(HERE, "live_vs_after.diff")))


if __name__ == "__main__":
    main()
