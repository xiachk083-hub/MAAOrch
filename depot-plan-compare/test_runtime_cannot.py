# -*- coding: utf-8 -*-
"""RUNTIME_CANNOT 回写闭环测试:
1. runner 回写方法写 farm_probe.json (RUNTIME_CANNOT)
2. depot_scheduler.probe_fn 读缓存 → ok=False
3. plan_lp 剔除该关自动重推换关
"""
import sys
import json
import time
import pathlib
sys.path.insert(0, r"D:\1-2\MAAOrch")

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
TEST_AID = "test_rt_cannot"
BASE = ROOT / "models" / "accounts" / TEST_AID
BASE.mkdir(parents=True, exist_ok=True)
probe_p = BASE / "farm_probe.json"
if probe_p.exists():
    probe_p.unlink()

# ── 1. 模拟 runner 回写（直接调用实例方法等价逻辑: 用真实方法）──
import types
from services import runner as runner_mod

# 造一个最小 runner 实例（只测 _record_runtime_cannot）
fake_runner = object.__new__(runner_mod.AccountRunner)
fake_runner._log = types.SimpleNamespace(info=lambda *a, **k: print("[runner.log]", a[0] if a else ""))
fake_runner._record_runtime_cannot(TEST_AID, "10-6-HARD", "导航/进入作战失败")
print("[1] 回写完成, farm_probe.json 内容:")
print(json.loads(probe_p.read_text(encoding="utf-8")))

# ── 2. depot_scheduler.probe_fn 读取 ──
from services import depot_scheduler as ds_mod
fake_ds = object.__new__(ds_mod.DepotScanner)
fake_ds._log = lambda *a, **k: print("[ds.log]", a[1] if len(a) > 1 else "")
fake_ds._pick_free_instance = lambda: None  # 不应走到实例探测
fake_ds._protect_emu = lambda *a, **k: None
fake_ds._probe_cache_path = lambda aid: BASE / "farm_probe.json"
fake_ds._load_probe_cache = lambda aid: json.loads(probe_p.read_text(encoding="utf-8"))

ac = {"id": TEST_AID, "name": "测试号"}
probe_fn = fake_ds._make_probe_fn(ac, "0", "Official")
r = probe_fn("10-6-HARD")
print(f"[2] probe_fn(10-6-HARD) -> {r}")
assert r.get("ok") is False, "RUNTIME_CANNOT 应返回 ok=False"
assert r.get("verdict") == "RUNTIME_CANNOT"
print("[2] PASS: probe_fn 识别 RUNTIME_CANNOT → ok=False")

# 其他关不受影响（无缓存 → 无实例 → ok=None 不锁）
r2 = probe_fn("2-4")
print(f"[2b] probe_fn(2-4) -> {r2}")
assert r2.get("ok") is None, "无缓存关应 ok=None 不锁"

# ── 3. plan_lp 集成: RUNTIME_CANNOT 关被剔除, 自动重推 ──
from services import material_planner as mp
snap = json.loads(open(ROOT / "data" / "depot_snapshots" / "本机0号_20260813_044448.json",
                       encoding="utf-8").read())
cfg = json.loads(open(ROOT / "config" / "material_water_levels.json",
                      encoding="utf-8").read())
cfg["plan_mode"] = "lp"

# 基线: 无锁定
p0 = mp.plan(snap, cfg, budget=280)
print(f"[3] 基线 LP: total_ap={p0.get('total_ap')} "
      f"固源岩组 stage={[f['stage'] for f in p0['gap_fills'] if f['name']=='固源岩组']}")

# 加锁: 10-6-HARD / 10-6 都 RUNTIME_CANNOT（probe_fn 直接喂）
def locked_probe(stage):
    if stage in ("10-6-HARD", "10-6"):
        return {"ok": False, "verdict": "RUNTIME_CANNOT",
                "detail": "测试: 运行时实测不可刷", "ts": time.time()}
    return {"ok": True, "verdict": "CAN_FARM", "detail": "", "ts": time.time()}

p1 = mp.plan(snap, cfg, budget=280, probe_fn=locked_probe)
fills = {f["name"]: f for f in p1["gap_fills"]}
print(f"[3b] 锁 10-6 后 LP: total_ap={p1.get('total_ap')}")
print(f"     固源岩组 farm={fills['固源岩组'].get('farm')} "
      f"craft={fills['固源岩组'].get('craft')}")
assert "10-6-HARD" not in [x["stage"] for x in fills["固源岩组"].get("farm", [])]
assert "10-6" not in [x["stage"] for x in fills["固源岩组"].get("farm", [])]
print("[3] PASS: 被锁关剔除, 自动换关/合成兜底")

# ── 4. 7 天窗口过期 → 重测（不直接锁）──
old_ts = time.time() - 8 * 86400
probe_p.write_text(json.dumps({"10-6-HARD": {"verdict": "RUNTIME_CANNOT",
                                             "ok": False, "ts": old_ts}},
                              ensure_ascii=False), encoding="utf-8")
fake_ds._load_probe_cache = lambda aid: json.loads(probe_p.read_text(encoding="utf-8"))
probe_fn2 = fake_ds._make_probe_fn(ac, "0", "Official")
# 无实例 → 走到 NO_INSTANCE（过期后不再直接锁, 尝试实测）
r3 = probe_fn2("10-6-HARD")
print(f"[4] 过期后 probe_fn(10-6-HARD) -> {r3}")
assert r3.get("verdict") in ("NO_INSTANCE", "PROBE_ERROR", "SKIP_SUPPLY"), r3
print("[4] PASS: 过期后不再直接锁")

# 清理
probe_p.unlink(missing_ok=True)
print("\n全部闭环测试通过 ✅")
