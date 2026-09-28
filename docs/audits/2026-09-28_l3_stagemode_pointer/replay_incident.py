#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""复现 t_a8b4ceb0 现场：把 10:34 的采样窗口重放给「收敛后」的 L3 判据。

只读：读真实 runtime/run_profile_state.json，拷到临时目录后把那次 run 的状态改回 `dispatched`
（模拟采样时刻的台账态），租约用真实 runtime/instance_leases.json。
不改任何真实文件、不发车、不写配置、不碰实例。

用法：
  "C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe" \
      docs/audits/2026-09-28_l3_stagemode_pointer/replay_incident.py
"""
import datetime as dt
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
while not (ROOT / "tools" / "tests" / "test_regression.py").is_file() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import test_regression as reg  # noqa: E402

RID = "rp_20260928_102708_-b-3"
UID = "bd2e68c5-5af7-4883-b316-82c18799439b"
PID = "bc659275-698d-4371-9f1a-ad0f4888a1b0"
DIFFS = ["StageMode:'Fixed'->%s" % PID]      # 10:34 现场 mas_stage_fix --verify 报的就是这条
NOW = dt.datetime(2026, 9, 28, 10, 34, 4)    # 现场采样时刻


def replay(status: str, label: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        st = json.loads((ROOT / "runtime" / "run_profile_state.json").read_text(encoding="utf-8"))
        rec = (st.get("runs") or {}).get(RID)
        if not rec:
            print("[%s] 台账里没有 %s（记录被裁了？）" % (label, RID))
            return
        rec["status"] = status
        p = pathlib.Path(tmp) / "state.json"
        p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        runs = reg._run_profile_open_runs(state=p, leases=ROOT / "runtime" / "instance_leases.json",
                                          lock=pathlib.Path(tmp) / "no-lock", now=NOW)
        got = runs.get(RID) or {}
        acc = reg._inflight_account(UID, runs)
        left = [d for d in DIFFS if not (acc and reg._excusable_diff(d, acc))]
        print("[%s] 台账 status=%s → 判「在飞」=%s（%s；现租约 idx=%s）"
              % (label, status, got.get("live"), got.get("why"), got.get("emu")))
        print("      L3 结论：%s%s"
              % ("FAIL（未核销的差异 ⇒ 真违例）" if left else
                 "PASS（在飞核销，认领 run=%s，指针=%s）" % ((acc or {}).get("run_id"), PID),
                 "；未核销=%r" % left if left else ""))


print("现场 diff（10:34 `mas_stage_fix.py --verify` 输出）：%s" % DIFFS)
print("现场采样时刻：%s\n" % NOW)
replay("dispatched", "① 10:34 采样窗口（run 未还原）")
replay("restored", "② 已还原但指针还在（= 真残留假设）")
