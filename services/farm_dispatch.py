"""Farm dispatch — 账号标签 × 活动/非活动 的每日刷关分派（2026-08-13 用户拍板）。

标签 farm_tag: material(补材料, 默认) / orundum(搓玉) / resource(补资源)

| 标签      | 活动期间                                        | 非活动 |
|-----------|------------------------------------------------|--------|
| material  | 活动蓝材料关中库存数量最少的刷（预检锁换下一材料）  | plan.json D1 第一关 |
| orundum   | 活动搓玉关（固源岩最高掉率；预检锁→1-7）          | 1-7    |
| resource  | 活动资源关（龙门币/作战记录；预检锁→周分流）       | CE/LS 周分流 |

返回 None = 无分派结果，走现有 fight_mode 逻辑（存量账号不受影响）。
纯函数 + 缓存文件读取（60s），供 config_injector.inject_smart 每次派发调用。
"""
from __future__ import annotations
import json
import pathlib
import threading
import time

ROOT = pathlib.Path(__file__).parent.parent

# 资源本周分流（与 smart_scheduler.MATERIAL_STAGES 一致 — 2026-08-13）
RES_DAYS = {
    "lmd": {"stage": "CE-6", "fallback": "CE-5",
            "days": {"wed", "sat", "sun"}},
    "exp": {"stage": "LS-6", "fallback": "LS-5",
            "days": {"mon", "thu", "fri", "sun"}},
}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_io_cache: dict = {}
_io_lock = threading.Lock()


def _read_json(p: pathlib.Path, ttl: float = 60.0):
    key = str(p)
    with _io_lock:
        hit = _io_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = None
    with _io_lock:
        _io_cache[key] = (time.time(), data)
    return data


def _activity() -> dict | None:
    d = _read_json(ROOT / "config" / "activity_stages.json")
    return d if isinstance(d, dict) else None


def _probe_ok(probe: dict, stage: str) -> bool:
    """预检确认可刷（CAN_FARM）。没预检过 = 不确定 = 不可用（安全优先）。"""
    return probe.get(stage, {}).get("verdict") == "CAN_FARM"


def _pick_activity_material(ac: dict, activity: dict, depot: dict,
                            probe: dict) -> str | None:
    """3 个活动蓝材料关 → 库存数量最少的先试，预检锁换下一个。"""
    mats = activity.get("material") or []
    if not mats:
        return None
    named = depot.get("named", {}) if isinstance(depot, dict) else {}
    ordered = sorted(mats, key=lambda m: named.get(m.get("item", ""), 0))
    for m in ordered:
        st = m.get("stage", "")
        if st and _probe_ok(probe, st):
            return st
    return None


def _pick_plan_d1(plan: dict, probe: dict) -> str | None:
    """非活动: plan.json 多日排程 D1 第一个可刷关。"""
    if not isinstance(plan, dict):
        return None
    days = plan.get("days") or []
    if not days:
        return None
    for item in days[0].get("items", []):
        st = item.get("stage", "")
        if not st:
            continue
        pr = (plan.get("probe") or {}).get(st, {})
        if pr.get("ok") is False:
            continue
        return st
    return None


def pick_farm_stage(ac: dict, activity: dict, depot: dict,
                    plan: dict, probe: dict, today_key: str) -> str | None:
    """核心分派。返回关卡名或 None（走现有 fight_mode）。"""
    tag = ac.get("farm_tag", "material")
    act_active = isinstance(activity, dict) and activity.get("active")

    if tag == "orundum":
        if act_active:
            o = activity.get("orundum") or {}
            st = o.get("stage", "")
            if st and _probe_ok(probe, st):
                return st
        return "1-7"

    if tag == "resource":
        if act_active:
            for m in activity.get("resource") or []:
                st = m.get("stage", "")
                if st and _probe_ok(probe, st):
                    return st
        # 周分流: 龙门币日 CE-6；作战记录日 LS-6；同开(周日)取龙门币；都不开 CE-5
        if today_key in RES_DAYS["lmd"]["days"]:
            return RES_DAYS["lmd"]["stage"]
        if today_key in RES_DAYS["exp"]["days"]:
            return RES_DAYS["exp"]["stage"]
        return RES_DAYS["lmd"]["fallback"]

    # material（默认）
    if act_active:
        st = _pick_activity_material(ac, activity, depot, probe)
        if st:
            return st
    st = _pick_plan_d1(plan, probe)
    if st:
        return st
    return None


def pick_farm_stage_for(ac: dict) -> str | None:
    """配置注入用入口: 自读 depot/plan/activity/预检缓存（60s 缓存）。"""
    aid = ac.get("id", "")
    base = ROOT / "models" / "accounts" / aid
    depot = _read_json(base / "depot.json")
    plan = _read_json(base / "plan.json")
    probe = _read_json(base / "farm_probe.json") or {}
    activity = _activity()
    today_key = WEEKDAYS[time.localtime().tm_wday]
    try:
        return pick_farm_stage(ac, activity, depot, plan, probe, today_key)
    except Exception:
        return None


def build_farm_task(ac: dict) -> dict | None:
    """调度台打包入口（2026-08-13 任务参数化）: 标签分派 → 带参任务。

    返回 {"tasks": ["StartUp","Fight"], "fight": {"stage", "times"}}
    times = 计划 D1 该关当日预估次数（plan.json 有则用）;
            无计划回退账号 fight_times_per_stage（0=不限刷到体力完）。
    分派无结果（无计划/无标签数据）→ None（走原 fight_mode 逻辑）。
    """
    aid = ac.get("id", "")
    base = ROOT / "models" / "accounts" / aid
    depot = _read_json(base / "depot.json")
    plan = _read_json(base / "plan.json")
    probe = _read_json(base / "farm_probe.json") or {}
    activity = _activity()
    today_key = WEEKDAYS[time.localtime().tm_wday]
    try:
        stage = pick_farm_stage(ac, activity, depot, plan, probe, today_key)
    except Exception:
        return None
    if not stage:
        return None
    times = None
    if isinstance(plan, dict):
        for day in plan.get("days") or []:
            for item in day.get("items", []):
                if item.get("stage") == stage and item.get("rounds"):
                    times = int(item["rounds"])
                    break
            if times:
                break
    if times is None:
        ft = ac.get("fight_times_per_stage")
        times = int(ft) if ft not in (None, "", 0) else 0
    return {"tasks": ["StartUp", "Fight"],
            "fight": {"stage": stage, "times": times}}
