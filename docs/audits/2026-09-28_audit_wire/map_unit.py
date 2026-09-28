"""t_a190ca4b：台账 kind → 统一流 actor/action 映射单元校验（隔离 runtime，零副作用）。

覆盖三类：① 精确表 ② 家族前缀兜底 ③ 未知 kind 兜底；
并与 tools/mas_audit.py::_ad_ledger（桥接口径）逐 kind 对照，防止两侧漂移。
"""
import contextlib
import io
import json
import os
import sys
import tempfile

ROOT = r"D:\1-2\MAAOrch"
TOOLS = os.path.join(ROOT, "tools")
RT = tempfile.mkdtemp(prefix="audit_wire_map_")
os.environ["MAAORCH_RUN_PROFILE_RUNTIME"] = RT
os.environ.pop("MAAORCH_AUDIT_DIR", None)
sys.path.insert(0, TOOLS)

import mas_run_profile as rp  # noqa: E402

# 桥接口径（mas_audit._ad_ledger 的 amap）→ 本地表必须一致的那些 kind
BRIDGE_AMAP = {"run_profile": "profile.apply", "probe_farm": "farm.probe",
               "farm_orchestrator": "farm.run", "activity_switch": "account.activity_switch",
               "stage_fix": "stage.fix"}


def resolve(kind):
    """复刻 _unified_from_ledger 的分派顺序：精确表 → 家族前缀 → tool:ledger。"""
    actor, action = rp._LEDGER_ACTOR_ACTION.get(kind, (None, None))
    if actor is None:
        actor, action = rp._ledger_family_of(kind)
    if actor is None:
        actor, action = "tool:ledger", "ledger." + kind.lower()
    return actor, action


CASES = [
    ({"kind": "run_profile", "ok": True, "run_id": "r1"}, "profile.apply"),
    ({"kind": "run_profile_restore", "ok": True, "run_id": "r1"}, "profile.restore"),
    ({"kind": "run_profile_refused", "ok": False, "run_id": "r1", "reason": "lease_busy"},
     "profile.refuse"),
    ({"kind": "probe_farm", "ok": True, "run_id": "r1"}, "farm.probe"),
    ({"kind": "probe_farm_refused", "ok": False, "run_id": "r1"}, "farm.refuse"),
    ({"kind": "farm_orchestrator", "ok": True, "run_id": "r1"}, "farm.run"),
    ({"kind": "stage_fix", "ok": True}, "stage.fix"),
    ({"kind": "watch", "ok": True}, "run.watch"),
    ({"kind": "activity_switch_restore", "ok": True}, "account.activity_switch"),
    ({"kind": "run_profile_lease_done", "ok": True, "run_id": "r1"}, "profile.lease_done"),
    ({"kind": "probe_stage", "ok": True}, "stage.probe"),
    ({"kind": "unknown_junk", "ok": True}, "ledger.unknown_junk"),
]

bad = 0
print("-- ① 分派（本地表 / 家族兜底 / 未知兜底）")
for rec, want_action in CASES:
    actor, action = resolve(rec["kind"])
    ok = action == want_action
    bad += 0 if ok else 1
    drift = ""
    if rec["kind"] in BRIDGE_AMAP:
        same = BRIDGE_AMAP[rec["kind"]] == action
        drift = " bridge=%s %s" % (BRIDGE_AMAP[rec["kind"]], "同口径✓" if same else "漂移✗")
        bad += 0 if same else 1
    print("%s %-26s -> %s%s" % ("OK " if ok else "BAD", rec["kind"], actor + "/" + action, drift))

print("-- ② 真写一遍（隔离流 %s）" % os.path.join(RT, "audit", "mas_audit.jsonl"))
with contextlib.redirect_stdout(io.StringIO()):
    for rec, _ in CASES:
        rp._unified_from_ledger(rec)

uni = os.path.join(RT, "audit", "mas_audit.jsonl")
rows = [json.loads(l) for l in open(uni, encoding="utf-8")] if os.path.exists(uni) else []
print("   隔离流行数=%d" % len(rows))
for r in rows:
    print("   %-30s %-26s %s" % (r["actor"], r["action"], r["result"]))
print("MAP_UNIT=%s bad=%d" % ("OK" if bad == 0 else "FAIL", bad))
sys.exit(1 if bad else 0)
