"""t_a190ca4b 接线验收 harness（隔离 runtime，走真代码路径）。

覆盖「在生产 runtime 里不安全触发」的 6 个工具：
  mas_probe_farm / mas_dispatch / mas_stage_fix / mas_patch_guard / mas_watch / activity_switch

做法：
  1) 各工具 `MAAORCH_*_RUNTIME` → 临时目录（工具既有语义：自测/异机用）；
  2) `mas_audit.stream_for_runtime()` 会让统一流跟随到 `<tmp>/audit/mas_audit.jsonl`
     ⇒ **生产统一流零污染**（其余 5 个工具本轮用生产 dry-run 取证）；
  3) 调用的都是各工具**真函数**（audit / ledger_append / _ledger / log_event），不是桩。

产出：本目录下 harness_isolated.txt（全部输出）+ 隔离流的行样本。
用法：python harness_isolated.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = r"D:\1-2\MAAOrch"
PY312 = r"C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
TOOLS = os.path.join(ROOT, "tools")
HERE = os.path.dirname(os.path.abspath(__file__))

TMP = tempfile.mkdtemp(prefix="audit_wire_iso_")
RT = os.path.join(TMP, "runtime")
os.makedirs(RT, exist_ok=True)

ENV = dict(os.environ)
for k in ("MAAORCH_RUN_PROFILE_RUNTIME", "MAAORCH_PROBE_FARM_RUNTIME", "MAAORCH_DISPATCH_RUNTIME",
          "MAAORCH_ONDEMAND_RUNTIME", "MAAORCH_FARM_ORCH_RUNTIME", "MAAORCH_PLAN_BUILDER_RUNTIME",
          "MAAORCH_SNAPSHOT_RUNTIME", "MAAORCH_ACTIVITY_SWITCH_RUNTIME", "MAAORCH_LEASE_RUNTIME"):
    ENV[k] = RT
ENV.pop("MAAORCH_AUDIT_DIR", None)          # 不改统一流目录：隔离由 runtime 重定向驱动
os.environ.update(ENV)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)


def note(s):
    print(s, flush=True)


note("== 隔离 runtime：%s" % RT)
note("== 生产统一流（本 harness 前后行数应不变）")

# ① probe_farm：真 audit() + 共用台账 ledger_append()（kind=probe_farm → farm.probe）
import mas_probe_farm as pf                                                            # noqa: E402
pf.audit({"run_id": "pf_wire_iso", "event": "probe_dispatch", "account": "验收号", "stage": "1-7"})
pf.audit({"run_id": "pf_wire_iso", "event": "done", "rc": 0, "account": "验收号", "stage": "1-7",
          "verdict": "stage_farmed", "farm_rc": 0})
pf.audit({"run_id": "pf_wire_iso", "event": "refused", "reason": "no_go", "env": "MAAORCH_PROBE_FARM_NO_GO"})
pf.ledger_append({"kind": "probe_farm", "ok": True, "run_id": "pf_wire_iso",
                  "scope": "验收号 × 1-7（success）",
                  "detail": json.dumps({"probe_rc": 0, "farm_rc": 0,
                                        "verdict": {"status": "success", "kind": "stage_farmed"}},
                                       ensure_ascii=False)})
note("① probe_farm：3 条事件 + 1 条台账已写（隔离）")

# ② mas_dispatch：真 CLI（--release --apply 需要 state 里的 busy 条目；零 REST 调用）
st = {"2026-09-28": {"验收-队列": {"at": "2026-09-28 10:00:00", "taskId": "wire-iso-task",
                                   "mode": "AutoProxy", "resp": {"code": 200},
                                   "resources": {"dirs": ["e:\\maa-multi\\maa_1"], "scripts": ["sid-x"]}}}}
with open(os.path.join(RT, "mas_dispatch_state.json"), "w", encoding="utf-8") as f:
    json.dump(st, f, ensure_ascii=False)
r = subprocess.run([PY312, os.path.join(TOOLS, "mas_dispatch.py"), "--release", "验收-队列", "--apply", "--json"],
                   capture_output=True, env=ENV, cwd=ROOT)
note("② mas_dispatch --release：rc=%s out=%s" % (r.returncode, r.stdout.decode("utf-8", "replace").strip()[:200]))

# ③ mas_stage_fix：真 ledger_append()（模块常量指到隔离目录）
import mas_stage_fix as sf                                                              # noqa: E402
sf.LEDGER = pathlib.Path(RT) / "mas_ledger.jsonl"
sf.LEDGER_LOCK = pathlib.Path(RT) / "mas_ledger.lock"
sf.ledger_append({"kind": "stage_fix", "scope": "Info.Stage_1 -> '1-7' × 6 SR-* users", "ok": True,
                  "detail": "api_ok=6/6, disk_bad=0, disk_pending_flush=0, locked_left=0（隔离演练）"})
note("③ mas_stage_fix：台账 + 统一流已写（隔离）")

# ④ mas_patch_guard：真 _ledger()（模块常量指到隔离目录）
import mas_patch_guard as pg                                                            # noqa: E402
pg.LEDGER = os.path.join(RT, "mas_patch_guard_log.jsonl")
pg._ledger({"ts": "2026-09-28 11:00:00", "kind": "reapply", "patch": "accswitch_v2",
            "file": r"E:\AUTO-MAS\AutoProxy.py", "backup": r"E:\AUTO-MAS\AutoProxy.py.bak-wire",
            "ok": True, "sha256": "f" * 64, "fails": []})
note("④ mas_patch_guard：补丁台账 + 统一流已写（隔离）")

# ⑤ mas_watch：真 log_event()（RUNTIME/LEDGER 指到隔离目录）
import mas_watch as mw                                                                  # noqa: E402
mw.RUNTIME = RT
mw.LEDGER = os.path.join(RT, "mas_ledger.jsonl")
mw.log_event("fetch_error", error="wire-iso：演练用假错误")
mw.log_event("rerun", accounts=["验收号"], ok=True, detail="（隔离演练，未真发车）")
note("⑤ mas_watch：2 条事件已写（隔离）")

# ⑥ activity_switch：真 ledger_append()（共用写者 → kind=activity_switch）
import activity_switch as asw                                                           # noqa: E402
asw.ledger_append({"kind": "activity_switch", "ok": True,
                   "scope": json.dumps({"server": "官", "accounts": 1}, ensure_ascii=False),
                   "detail": "apply rc=0 {\"applied\": 1} approved_by=wire-iso（隔离演练）"})
note("⑥ activity_switch：台账 + 统一流已写（隔离）")

note("")
note("== 隔离统一流 %s" % os.path.join(RT, "audit", "mas_audit.jsonl"))
uni = os.path.join(RT, "audit", "mas_audit.jsonl")
rows = [l.strip() for l in open(uni, encoding="utf-8")] if os.path.exists(uni) else []
for l in rows:
    note("  " + l[:220])
note("== 隔离统一流行数：%d" % len(rows))

note("== 隔离旧流（双写证据）")
for name in ("mas_run_profile_log.jsonl", "mas_ledger.jsonl", "mas_dispatch_log.jsonl",
             "mas_patch_guard_log.jsonl"):
    p = os.path.join(RT, name)
    n = len([1 for _ in open(p, encoding="utf-8")]) if os.path.exists(p) else 0
    note("  %-28s %d 行" % (name, n))

# 证据归档：隔离产物拷进本目录
dst = os.path.join(HERE, "iso")
os.makedirs(dst, exist_ok=True)
if os.path.exists(uni):
    shutil.copyfile(uni, os.path.join(dst, "mas_audit.iso.jsonl"))
for name in ("mas_run_profile_log.jsonl", "mas_ledger.jsonl", "mas_dispatch_log.jsonl",
             "mas_patch_guard_log.jsonl"):
    p = os.path.join(RT, name)
    if os.path.exists(p):
        shutil.copyfile(p, os.path.join(dst, name))
note("== 隔离产物已拷到 %s" % dst)
