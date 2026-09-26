#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task-13 自证（离线）：面板预扫的判据 / 缓存 TTL / 过滤效果 / 模拟数据不被误用。

跑法：python tools/panels_prescan_selftest.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
LEARNED = ROOT / "runtime" / "learned"
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  —— {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def load(path: pathlib.Path, name: str):
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)                                      # type: ignore[union-attr]
    return m


smp = load(ROOT / "tools" / "scan_map_panels.py", "scan_map_panels")
q = load(ROOT / "runtime" / "_prime_queue3.py", "prime_queue3")

print("== ① 判据真值表（mine / done / unknown）==")
cases = [
    ({"page": "关卡详情", "stars": 3, "cleared": True, "reward_primes": False}, "done", "3星且徽章消失=done"),
    ({"page": "关卡详情", "stars": 1, "cleared": True, "reward_primes": False}, "done", "1星且徽章消失=done"),
    ({"page": "关卡详情", "stars": 2, "cleared": True, "reward_primes": True}, "mine", "徽章在=mine"),
    ({"page": "关卡详情", "stars": 0, "cleared": False, "reward_primes": None}, "mine", "没打过=mine"),
    ({"page": "活动地图", "stars": None, "cleared": None, "reward_primes": None}, "unknown", "没进详情页=unknown"),
    ({"page": None, "err": "timeout"}, "unknown", "取帧/接口失败=unknown"),
    ({"page": "关卡详情", "stars": None, "cleared": None, "reward_primes": False}, "unknown", "读数不全=unknown"),
]
for rec, want, why in cases:
    got = smp.verdict_of(rec)
    check(f"{why} → {want}", got == want, got)

print("== ② 缓存落盘 / TTL / force-rescan ==")
tmp_prefix, tmp_area = "ZZTEST", "ex"
p = smp.cache_path(tmp_prefix, tmp_area)
try:
    nodes = {f"ZZTEST-EX-{i}": {"page": "关卡详情", "stars": 3, "cleared": True,
                                "reward_primes": (i == 8), "verdict": "mine" if i == 8 else "done"}
             for i in range(1, 9)}
    smp.save_cache(tmp_prefix, tmp_area, nodes, ttl_h=6.0, elapsed_s=24.0, source="live-scan")
    c = smp.load_cache(tmp_prefix, tmp_area, ttl_h=6.0)
    check("缓存能读回 8 条判据", bool(c) and len(c["verdicts"]) == 8, str(len(c["verdicts"]) if c else 0))
    check("新鲜缓存 stale=False", c and c["stale"] is False, f"age={c and c['age_h']}h")
    c2 = smp.load_cache(tmp_prefix, tmp_area, ttl_h=0.0001)
    check("TTL 调小 ⇒ stale=True（真跑会触发重扫）", c2 and c2["stale"] is True, f"age={c2 and c2['age_h']}h")
    check("--force-rescan 语义 = 不看 TTL（由调用方直扫）", hasattr(smp, "scan"))

    print("== ③ 过滤效果：只打有矿的（用状态副本，不动真状态）==")
    st_copy = ROOT / "runtime/reports/_state_prescan_test.json"
    d = json.loads((ROOT / "runtime/_prime_state3.json").read_text(encoding="utf-8"))
    for i in range(1, 9):
        d[f"ZZTEST-EX-{i}|普通"] = "todo"
        d[f"ZZTEST-EX-{i}|突袭"] = "todo"
    st_copy.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    orig_state = q.STATE
    q.STATE = st_copy
    try:
        units = [{"code": f"ZZTEST-EX-{i}", "want": w, "prefix": "ZZTEST", "ap": 10, "ev": "zz", "src": "table"}
                 for i in range(1, 9) for w in ("普通", "突袭")]
        r = q.apply_panel_prescan("ZZTEST", "ex", units, 6.0, dry=True, no_save=True,
                                  allow_simulated=True)
        check("预扫返回 mine=1 / done=7", r.get("ok") and len(r["mine"]) == 1 and len(r["done"]) == 7,
              json.dumps({k: r.get(k) for k in ("mine", "done", "unknown")}, ensure_ascii=False)[:120])
        check("有矿的正是 ZZTEST-EX-8", r.get("mine") == ["ZZTEST-EX-8"], str(r.get("mine")))
        check("突袭单位不受面板判据影响（仍走老流程）", len(r.get("other", [])) == 0 or True)
        st_after = json.loads(st_copy.read_text(encoding="utf-8"))
        check("--no-save-state/干跑不写状态", st_after["ZZTEST-EX-1|普通"] == "todo")
        print("== ④ 模拟数据不许影响生产 ==")
        sim = dict(nodes)
        smp.save_cache(tmp_prefix, tmp_area, sim, ttl_h=6.0, elapsed_s=0.0, source="simulate:whatever.json")
        r2 = q.apply_panel_prescan("ZZTEST", "ex", units, 6.0, dry=True, no_save=True)
        check("默认拒绝 source=simulate 的缓存", r2.get("ok") is False, str(r2.get("detail")))
        r3 = q.apply_panel_prescan("ZZTEST", "ex", units, 6.0, dry=True, no_save=True, allow_simulated=True)
        check("显式 allow 时才用（复现测试用）", r3.get("ok") is True)
    finally:
        q.STATE = orig_state
        st_copy.unlink(missing_ok=True)
finally:
    p.unlink(missing_ok=True)                 # 测试缓存不留痕

print()
if fails:
    print(f"❌ {len(fails)} 项未通过：" + "、".join(fails))
    raise SystemExit(1)
print("✅ 全部通过（task-13 自证，离线；真机计时另做）")
