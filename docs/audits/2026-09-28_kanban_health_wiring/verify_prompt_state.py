# -*- coding: utf-8 -*-
"""t_4cd871de 复核脚本（只读）：比对 job 52b88b0ffca0 提示词 与 审计留档 before/after。"""
import hashlib
import json
import os

JOBS = os.path.expanduser("~/AppData/Local/hermes/profiles/mas/cron/jobs.json")
HERE = os.path.dirname(os.path.abspath(__file__))


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def read_job_prompt(job_id="52b88b0ffca0"):
    d = json.load(open(JOBS, encoding="utf-8"))
    jobs = d if isinstance(d, list) else d.get("jobs", d)
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    for j in jobs:
        if (j.get("id") or "") == job_id:
            return j
    raise SystemExit("job not found: %s" % job_id)


def main():
    j = read_job_prompt()
    p = j["prompt"]
    after = open(os.path.join(HERE, "job_prompt_after.txt"), encoding="utf-8", newline="").read()
    before = open(os.path.join(HERE, "job_prompt_before.txt"), encoding="utf-8", newline="").read()
    print("job enabled=%s schedule=%s" % (j.get("enabled"), j.get("schedule")))
    print("live  : %d chars sha=%s" % (len(p), sha(p)))
    print("after : %d chars sha=%s  equal=%s" % (len(after), sha(after), p == after))
    print("before: %d chars sha=%s  equal=%s" % (len(before), sha(before), p == before))
    for probe in ("KANBAN_HEALTH=", "mas_kanban_health", "最多 22 次工具调用", "先 15 行主表",
                  "mas-observe 8", "mas-observe 9", "现场八检"):
        print("  has %-24r %s" % (probe, probe in p))


if __name__ == "__main__":
    main()
