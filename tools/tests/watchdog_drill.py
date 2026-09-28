#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调度看门狗端到端演练 —— 假 Hermes 主目录 + 假 runtime，验证「检测 → 告警 → （可选）拉起」全链路。

为什么不直接停真 gateway：真 gateway 停掉会打断线上调度（且属于受控动作）。本演练造一个
**假环境**（假 gateway.pid/心跳/cron 存储/发车审计），让看门狗在真实代码路径上跑完整流程：
  步骤① 探测：假网关死（pid 不存在）+ 心跳过期 + cron 任务超期 + 审计陈旧
         ⇒ 期望 gateway_down / ticker_stalled / job_overdue 找据，判定 ALERT（rc=4）
  步骤② 告警：dry-run payload 落**假 runtime**（生产 state/log 不被触碰）
  步骤③ 拉起：`--restart --restart-cmd <演练脚本>` ⇒ 命令被执行、复验（活 pid + 新心跳）通过
  步骤④ 恢复：把假环境修好（模拟 gateway 回来 + 任务重新排列）⇒ 判定 OK + 「已消除」通知

用法：
  python tools/tests/watchdog_drill.py            # 全部步骤，末行 DRILL=PASS|FAIL，退出码 0/3
  python tools/tests/watchdog_drill.py --json     # 机读
  python tools/tests/watchdog_drill.py --keep     # 保留假环境目录（排障）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
WATCHDOG = ROOT / "tools" / "scheduler_watchdog.py"
PY = sys.executable
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _iso(delta_s: float) -> str:
    return (dt.datetime.now() + dt.timedelta(seconds=delta_s)).astimezone().isoformat()


def _write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def make_fake_env(td: pathlib.Path) -> tuple:
    """假 hermes 主目录 + 假 runtime（全部指向过期/死亡状态）。"""
    hh = td / "hermes"
    rt = td / "runtime"
    cdir = hh / "profiles" / "mas" / "cron"
    cdir.mkdir(parents=True, exist_ok=True)
    (rt / "reports").mkdir(parents=True, exist_ok=True)

    # 假 gateway：pid 不存在（999999 基本不可能命中真实进程），state 过期
    _write_json(hh / "gateway.pid", {"pid": 999999, "kind": "hermes-gateway",
                                     "argv": ["<drill>"], "hermes_home": str(hh)})
    _write_json(hh / "gateway_state.json", {"pid": 999999, "gateway_state": "running",
                                            "updated_at": _iso(-3 * 3600), "served_profiles": ["mas"]})
    # 假心跳：3 小时前
    old = str(dt.datetime.now().timestamp() - 3 * 3600)
    (cdir / "ticker_heartbeat").write_text(old, encoding="utf-8")
    (cdir / "ticker_last_success").write_text(old, encoding="utf-8")
    # 假 cron 任务：dispatch 超期 2 小时；另一个禁用任务不该报
    _write_json(cdir / "jobs.json", {"jobs": [
        {"id": "drill_dispatch", "name": "mas-shift-dispatch（演练）", "enabled": True,
         "state": "scheduled", "schedule": {"kind": "cron", "expr": "*/3 5,19,20 * * *"},
         "schedule_display": "*/3 5,19,20 * * *",
         "next_run_at": _iso(-2 * 3600), "last_run_at": _iso(-2 * 3600),
         "last_status": "ok", "failure_streak": 0},
        {"id": "drill_off", "name": "已禁用任务（演练）", "enabled": False, "state": "paused",
         "schedule": {"kind": "interval", "minutes": 5}, "next_run_at": _iso(-9999)},
    ]})
    # 假执行台账：最近一次执行 = 2 小时前
    con = sqlite3.connect(str(cdir / "executions.db"))
    con.execute("""CREATE TABLE IF NOT EXISTS executions (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source TEXT NOT NULL, process_id TEXT NOT NULL,
        pid INTEGER NOT NULL, process_started_at INTEGER, status TEXT NOT NULL,
        handoff_pending INTEGER NOT NULL DEFAULT 0, handoff_started_at REAL,
        claimed_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT,
        delivery_outcome TEXT, scheduled_instant TEXT)""")
    con.execute("INSERT INTO executions (id, job_id, source, process_id, pid, status, claimed_at, "
                "finished_at) VALUES ('drill_r1', 'drill_dispatch', 'builtin', 'p1', 1, "
                "'completed', ?, ?)", (_iso(-2 * 3600), _iso(-2 * 3600 + 20)))
    con.commit()
    con.close()
    # 假发车审计：昨天写过一条（今天什么都没发）
    yday = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    (rt / "mas_dispatch_log.jsonl").write_text(
        json.dumps({"ts": yday, "kind": "dispatch_ok"}) + "\n", encoding="utf-8")
    _write_json(rt / "mas_dispatch_state.json", {})
    _write_json(rt / "mas_dispatch_alerts.json", {"active": [], "cleared": []})
    return hh, rt


def make_restart_sim(td: pathlib.Path) -> pathlib.Path:
    """演练用的「拉起」命令：按 MAAORCH_WATCHDOG_PID 把假 gateway 写活 + 刷新假心跳。"""
    script = td / "restart_sim.py"
    script.write_text(
        "import datetime as dt, json, os, pathlib, sys\n"
        "hh = pathlib.Path(sys.argv[1]); cdir = hh / 'profiles' / 'mas' / 'cron'\n"
        "pid = int(os.environ.get('MAAORCH_WATCHDOG_PID') or 0)\n"
        "if pid <= 0: sys.exit(3)\n"
        "hh.joinpath('gateway.pid').write_text(json.dumps({'pid': pid, 'kind': 'hermes-gateway'}), encoding='utf-8')\n"
        "for f in ('ticker_heartbeat', 'ticker_last_success'):\n"
        "    (cdir / f).write_text(str(dt.datetime.now().timestamp()), encoding='utf-8')\n"
        "print('drill restart simulated pid=%d' % pid)\n",
        encoding="ascii")
    return script


def run_watchdog(args: list) -> tuple:
    r = subprocess.run([PY, str(WATCHDOG)] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300,
                       stdin=subprocess.DEVNULL, creationflags=NOWIN, cwd=str(ROOT))
    obj = None
    for ln in reversed((r.stdout or "").splitlines()):
        ln = ln.strip()
        if ln.startswith("{") and ln.endswith("}"):
            try:
                obj = json.loads(ln)
                break
            except json.JSONDecodeError:
                continue
    return r.returncode, (r.stdout or ""), (r.stderr or ""), obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--keep", action="store_true", help="保留假环境目录")
    args = ap.parse_args()

    fails, steps = [], []
    td = pathlib.Path(tempfile.mkdtemp(prefix="schedwd_drill_"))

    PROD_FILES = ("scheduler_watchdog_state.json", "scheduler_watchdog.log",
                  "scheduler_watchdog_alerts.jsonl", "scheduler_watchdog_drill_state.json")

    def _prod_snapshot():
        out = {}
        for name in PROD_FILES:
            p = ROOT / "runtime" / name
            try:
                s = p.stat()
                out[name] = (s.st_mtime_ns, s.st_size)
            except FileNotFoundError:
                out[name] = None
        return out

    def ck(name, cond, detail=None):
        steps.append({"step": name, "ok": bool(cond), "detail": str(detail)[:400]})
        if not cond:
            fails.append(name)
        print("  %s %s%s" % ("ok " if cond else "FAIL", name, (" — %s" % detail) if detail and not cond else ""))

    try:
        hh, rt = make_fake_env(td)
        sim = make_restart_sim(td)
        common = ["--once", "--json", "--hermes-home", str(hh), "--runtime", str(rt)]
        prod_before = _prod_snapshot()

        # ① 探测：假网关死 + 超期
        rc, out, err, obj = run_watchdog(common)
        ck("① rc=4（ALERT）", rc == 4, "rc=%s stderr=%s" % (rc, err[:200]))
        kinds = {f["kind"] for f in (obj or {}).get("findings", [])}
        ck("① gateway_down", "gateway_down" in kinds, sorted(kinds))
        ck("① ticker_stalled", "ticker_stalled" in kinds, sorted(kinds))
        ck("① job_overdue", "job_overdue" in kinds, sorted(kinds))
        jobs = (obj or {}).get("cron", {}).get("jobs", [])
        sched = [j for j in jobs if j.get("id") == "drill_dispatch"]
        ck("① 最近执行时间取自台账", bool(sched) and "claimed_at" not in str(sched[0].get("last_exec_claimed_at", ""))
           and bool(sched[0].get("last_exec_claimed_at")), sched[:1])
        ck("① 禁用任务被跳过", any(j.get("id") == "drill_off" and j.get("skip") for j in jobs))

        # ② 告警落盘（dry-run，仅假 runtime）
        reports = sorted((rt / "reports").glob("scheduler_watchdog_alert_*.json"))
        ck("② 告警 payload 落盘", len(reports) >= 1, str(reports))
        ck("② drill 状态文件生成", (rt / "scheduler_watchdog_state.json").exists())
        ck("② 生产 runtime 未被写入（前后快照一致）", _prod_snapshot() == prod_before,
           {"before": prod_before, "after": _prod_snapshot()})

        # ③ 拉起（--restart + 演练命令）
        cmd = '"%s" "%s" "%s"' % (PY, sim, hh)
        rc2, out2, err2, obj2 = run_watchdog(common + ["--restart", "--restart-cmd", cmd, "--restart-wait", "15"])
        rst = (obj2 or {}).get("restart") or {}
        ck("③ 拉起命令被执行（rc=0）", rst.get("rc") == 0, json.dumps(rst, ensure_ascii=False)[:300])
        ck("③ 复验通过（活 pid + 心跳新）", bool(rst.get("ok")) and bool((rst.get("verify") or {}).get("alive"))
           and bool((rst.get("verify") or {}).get("heartbeat_fresh")), json.dumps(rst.get("verify"), ensure_ascii=False))
        st = json.loads((rt / "scheduler_watchdog_state.json").read_text(encoding="utf-8"))
        ck("③ 拉起记录进状态文件", bool(st.get("restarts")) and st["restarts"][-1].get("ok"))
        # 防循环：立刻再来一次，应 skip
        rc3, out3, err3, obj3 = run_watchdog(common + ["--restart", "--restart-cmd", cmd, "--restart-wait", "5"])
        ck("③ 防循环（20min 内不重复拉起）",
           ((obj3 or {}).get("restart") or {}).get("skipped") == "too_soon",
           json.dumps((obj3 or {}).get("restart"), ensure_ascii=False))

        # ④ 恢复：修好假环境（模拟 gateway 回来 + ticker 重新排任务）
        _write_json(hh / "gateway.pid", {"pid": os.getpid(), "kind": "hermes-gateway"})  # 活 pid=本演练进程
        _write_json(hh / "gateway_state.json", {"pid": os.getpid(), "gateway_state": "running",
                                                "updated_at": _iso(0), "served_profiles": ["mas"]})
        now_epoch = str(dt.datetime.now().timestamp())
        (hh / "profiles" / "mas" / "cron" / "ticker_heartbeat").write_text(now_epoch, encoding="utf-8")
        (hh / "profiles" / "mas" / "cron" / "ticker_last_success").write_text(now_epoch, encoding="utf-8")
        jobs_obj = json.loads((hh / "profiles" / "mas" / "cron" / "jobs.json").read_text(encoding="utf-8"))
        for j in jobs_obj["jobs"]:
            if j.get("id") == "drill_dispatch":
                j["next_run_at"] = _iso(+27 * 60)
                j["last_run_at"] = _iso(-60)
        _write_json(hh / "profiles" / "mas" / "cron" / "jobs.json", jobs_obj)
        rc4, out4, err4, obj4 = run_watchdog(common)
        ck("④ 恢复后判定 OK（rc=0）", rc4 == 0, "rc=%s findings=%s" % (rc4, (obj4 or {}).get("findings")))
        ck("④ 「已消除」通知", bool((obj4 or {}).get("cleared")), (obj4 or {}).get("cleared"))
    finally:
        if args.keep:
            print("  假环境保留: %s" % td)
        else:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    ok = not fails
    if args.json:
        print(json.dumps({"ok": ok, "steps": steps, "fails": fails}, ensure_ascii=False))
    print("DRILL=%s（%d 步，失败 %d）" % ("PASS" if ok else "FAIL", len(steps), len(fails)))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
