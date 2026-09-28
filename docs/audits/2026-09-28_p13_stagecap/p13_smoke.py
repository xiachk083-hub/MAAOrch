#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""P1-3 冒烟②：并发写冒烟 —— 两个 stage_capability._finish(update) 并发不丢更新 + 锁留痕。

角色（脚本自己托管自己）：
  --role worker   在沙盒里跑一次 stage_capability._finish(mode=update)（路径全部重定向到沙盒）
  默认 orchestrate：
    phase 1  自然竞态：两 worker 同时起（≥1500 条/方，保证读-改-写窗口重叠）
    phase 1b 定序竞态：harness 先占锁 → 两 worker 都见「等待中」→ 3s 后放锁 → 串行落盘
    phase 2  真实路径：两个 `--rebuild-local` 在真仓库同时跑（只重写 runtime/reports，不联网）
    验证：JSON/MD 完好可解析；state 含 A+B 全部 attempt；等待/获锁痕迹；锁清理；审计 2 行
输出末行：`SMOKE=PASS ...` 或 `SMOKE=FAIL ...`（rc：0=PASS，1=FAIL）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TOOLS = ROOT / "tools"
PY = sys.executable


def _attempts(prefix, day, count):
    lines = ['GetFightStage: from ["1-7"], selected 1-7',
             "Start Task Chain: Fight, Task ID: 2", "开始行动 1 次, -6理智",
             "1-7 掉落统计: ", "任务已全部完成！"]
    return [{"day": day, "acct": "冒烟号", "file": "%s_%05d.json" % (prefix, i),
             "mt": day + " 10:00:00", "result": "Success!", "drops": ["1-7"], "lines": lines}
           for i in range(count)]


def worker_main(a):
    sys.path.insert(0, str(TOOLS))
    import stage_capability as sc  # noqa: PLC0415
    sb = a.sandbox
    sc.STATE_DIR = os.path.join(sb, "state")
    sc.STATE_PATH = os.path.join(sc.STATE_DIR, "stage_capability_state.json")
    sc.REPORTS = os.path.join(sb, "reports")
    sc.JSON_PATH = os.path.join(sc.REPORTS, "stage_capability.json")
    sc.MD_PATH = os.path.join(sc.REPORTS, "STAGE_CAPABILITY.md")
    sc.LOCK_PATH = os.path.join(sb, "stage_capability.lock")
    if a.lock_wait is not None:
        sc.LOCK_WAIT_S = a.lock_wait
    payload = {"meta": {"days": [a.day], "secs": 1}, "attempts": _attempts(a.prefix, a.day, a.count)}
    t0 = time.time()
    rc = sc._finish(payload, 30, False, mode="update")
    print("WORKER prefix=%s rc=%d secs=%.1f pid=%d" % (a.prefix, rc, time.time() - t0, os.getpid()))
    return rc


def _spawn(sandbox, prefix, count, day, extra_env=None):
    env = dict(os.environ)
    env["MAAORCH_AUDIT_DIR"] = os.path.join(sandbox, "audit")
    env["HERMES_KANBAN_TASK"] = "t_a69d0027"
    env.update(extra_env or {})
    cmd = [PY, os.path.abspath(__file__), "--role", "worker", "--sandbox", sandbox,
           "--prefix", prefix, "--count", str(count), "--day", day]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace", env=env, cwd=str(ROOT))


def _dump(evidence, name, text):
    """把 worker 完整 stdout 落进证据目录（等待/获锁痕迹都在这里）。"""
    p = os.path.join(evidence, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _state_of(sandbox):
    with open(os.path.join(sandbox, "state", "stage_capability_state.json"), encoding="utf-8") as f:
        return json.load(f)


def _report_of(sandbox):
    with open(os.path.join(sandbox, "reports", "stage_capability.json"), encoding="utf-8") as f:
        return json.load(f)


def _audit_lines(sandbox):
    p = os.path.join(sandbox, "audit", "mas_audit.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _check_phase(tag, sandbox, count, outA, outB, want_wait):
    """一个 phase 的验证：返回 (ok, notes[])。"""
    notes, ok = [], True

    def chk(cond, msg):
        nonlocal ok
        notes.append(("OK  " if cond else "FAIL") + " " + msg)
        if not cond:
            ok = False

    chk("rc=0" in outA and "rc=0" in outB, "两 worker rc=0（A: %s | B: %s）"
        % (outA.strip().splitlines()[-1] if outA.strip() else "?", outB.strip().splitlines()[-1] if outB.strip() else "?"))
    st = _state_of(sandbox)
    keys = set(st["attempts"])
    a_keys = {k for k in keys if "|A_" in k}
    b_keys = {k for k in keys if "|B_" in k}
    chk(len(a_keys) == count and len(b_keys) == count,
        "state 不丢更新：A=%d/%d B=%d/%d（共 %d）" % (len(a_keys), count, len(b_keys), count, len(keys)))
    rep = _report_of(sandbox)
    row = next((r for r in rep.get("rows", []) if r["account"] == "冒烟号" and r["stage"] == "1-7"), None)
    chk(bool(row) and row["attempt_n"] == 2 * count,
        "报告 JSON 一致：row.attempt_n=%s（期望 %d）" % (row and row["attempt_n"], 2 * count))
    md = os.path.join(sandbox, "reports", "STAGE_CAPABILITY.md")
    chk(os.path.exists(md) and os.path.getsize(md) > 0, "报告 MD 存在且非空")
    chk(not os.path.exists(os.path.join(sandbox, "stage_capability.lock")), "锁已清理（无残留）")
    if want_wait:
        chk("等待中…" in outA and "等待中…" in outB, "两方都留等待痕（写盘锁被占…等待中）")
        chk(("已获写盘锁（等待" in outA) or ("已获写盘锁（等待" in outB), "至少一方留获锁痕（等了 N 秒）")
    else:
        chk(("等待中…" in outA or "已获写盘锁（等待" in outA or "等待中…" in outB
             or "已获写盘锁（等待" in outB) or True, "(自然竞态) 是否重叠等锁：%s"
            % ("是" if ("等待中…" in outA + outB) else "否（量不够大没撞上，见 phase 1b 定序版）"))
    al = _audit_lines(sandbox)
    rb = [x for x in al if x.get("action") == "stage.rebuild"]
    chk(len(rb) == 2, "审计 2 行（mas_audit）：%s"
        % [(x["params"].get("mode"), x["params"].get("state_attempts")) for x in rb])
    if len(rb) == 2:
        sa = sorted(x["params"].get("state_attempts") or 0 for x in rb)
        chk(sa == [count, 2 * count], "审计记到串行叠加（state_attempts=%s）" % sa)
    print("[%s]\n  %s" % (tag, "\n  ".join(notes)))
    return ok


def orchestrate(a):
    ok = True
    ev = a.evidence or str(HERE)
    os.makedirs(ev, exist_ok=True)
    # ---------- phase 1：自然竞态 ----------
    sb1 = os.path.join(a.sandbox, "phase1")
    os.makedirs(sb1, exist_ok=True)
    t0 = time.time()
    pa = _spawn(sb1, "A", a.count, a.day)
    pb = _spawn(sb1, "B", a.count, a.day)
    outA = pa.communicate()[0] or ""
    outB = pb.communicate()[0] or ""
    _dump(ev, "out_phase1_workerA.txt", outA)
    _dump(ev, "out_phase1_workerB.txt", outB)
    print("phase1 自然竞态：两 worker 同起（各 %d 条），总耗时 %.1fs" % (a.count, time.time() - t0))
    print("  A stdout: %s" % outA.strip().replace("\n", " | ")[-400:])
    print("  B stdout: %s" % outB.strip().replace("\n", " | ")[-400:])
    ok &= _check_phase("phase1", sb1, a.count, outA, outB, want_wait=False)

    # ---------- phase 1b：定序竞态（harness 先占锁，两方必然都进等待） ----------
    sb2 = os.path.join(a.sandbox, "phase1b")
    os.makedirs(sb2, exist_ok=True)
    lock = os.path.join(sb2, "stage_capability.lock")
    with open(lock, "w", encoding="utf-8") as f:
        f.write("pid=424242 ts=harness\n")
    pa = _spawn(sb2, "A", a.count, a.day)
    pb = _spawn(sb2, "B", a.count, a.day)
    time.sleep(a.hold_s)
    os.remove(lock)          # 放锁：两 worker 抢 → 一个拿锁干活，另一个继续等
    outA = pa.communicate()[0] or ""
    outB = pb.communicate()[0] or ""
    _dump(ev, "out_phase1b_workerA.txt", outA)
    _dump(ev, "out_phase1b_workerB.txt", outB)
    print("phase1b 定序竞态：harness 占锁 %.1fs 后放锁" % a.hold_s)
    print("  A stdout: %s" % outA.strip().replace("\n", " | ")[-400:])
    print("  B stdout: %s" % outB.strip().replace("\n", " | ")[-400:])
    ok &= _check_phase("phase1b", sb2, a.count, outA, outB, want_wait=True)

    # ---------- phase 2：真实路径 —— 两个 --rebuild-local 同跑 ----------
    if not a.skip_real:
        t0 = time.time()
        pa = subprocess.Popen([PY, str(TOOLS / "stage_capability.py"), "--rebuild-local"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(ROOT),
                              encoding="utf-8", errors="replace")
        pb = subprocess.Popen([PY, str(TOOLS / "stage_capability.py"), "--rebuild-local"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(ROOT),
                              encoding="utf-8", errors="replace")
        oa = pa.communicate()[0] or ""
        ob = pb.communicate()[0] or ""
        _dump(ev, "out_phase2_rebuild_a.txt", oa)
        _dump(ev, "out_phase2_rebuild_b.txt", ob)
        notes, p2ok = [], True

        def chk2(cond, msg):
            nonlocal p2ok
            notes.append(("OK  " if cond else "FAIL") + " " + msg)
            if not cond:
                p2ok = False

        chk2(pa.returncode == 0 and pb.returncode == 0, "两个 --rebuild-local rc=0（%s / %s）"
             % (pa.returncode, pb.returncode))
        with open(ROOT / "runtime" / "reports" / "stage_capability.json", encoding="utf-8") as f:
            rep = json.load(f)
        chk2(bool(rep.get("rows")) and bool(rep.get("built_at")), "真实 JSON 可解析（rows=%d built_at=%s）"
             % (len(rep.get("rows") or []), rep.get("built_at")))
        md = ROOT / "runtime" / "reports" / "STAGE_CAPABILITY.md"
        chk2(md.exists() and md.stat().st_size > 0, "真实 MD 存在且非空（%d B）" % (md.stat().st_size if md.exists() else 0))
        chk2(not (ROOT / "runtime" / "stage_capability.lock").exists(), "真实锁无残留")
        print("phase2 真实 rebuild-local ×2：%.1fs\n  %s" % (time.time() - t0, "\n  ".join(notes)))
        ok &= p2ok

    print("SMOKE=%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["worker"], default=None)
    ap.add_argument("--sandbox", default="")
    ap.add_argument("--prefix", default="A")
    ap.add_argument("--count", type=int, default=1500)
    ap.add_argument("--day", default="2026-09-27")
    ap.add_argument("--lock-wait", type=int, default=None, help="worker 用（覆盖 LOCK_WAIT_S）")
    ap.add_argument("--hold-s", type=float, default=3.0, help="phase1b harness 占锁时长")
    ap.add_argument("--evidence", default="", help="证据目录（worker 完整 stdout 落这里；默认脚本目录）")
    ap.add_argument("--skip-real", action="store_true")
    a = ap.parse_args()
    if a.role == "worker":
        return worker_main(a)
    if not a.sandbox:
        ap.error("orchestrate 需要 --sandbox")
    return orchestrate(a)


if __name__ == "__main__":
    sys.exit(main())
