"""Activity tracker — 每小时识别当前活动关卡（企鹅物流三源，2026-08-13 用户拍板）。

用户需求: 活动会提前几天/半个月发公告, 当天关卡数据晚上 8-9 点就有,
每小时扒一次。MAA 每次活动出 4 个可导航关: 3 蓝材料 + 1 搓玉(固源岩)。

识别法（数据实测验证, 命中 act53side 直到大地变成一颗酸橙）:
    1. 企鹅 zones API → ACTIVITY zone 列表 + stageId→zone 映射
    2. 企鹅 matrix 原始行带 start/end(ms): 近 30 天新开采集期的行
       → 按 zone 汇总样本量 → 最大者即当前活动（阈值 5000）
    3. 该 zone 的关卡 → MAA stages.json 映射 code + 主掉落(NORMAL_DROP)
       → 分类: 材料关(17 蓝材料, 掉率前 3) / 搓玉关(固源岩最高掉率) /
       资源关(龙门币/作战记录)
    4. 写 config/activity_stages.json（人工可改, manual 标记防覆盖）

ActivityScheduler: 每小时 detect() 一次（main_web.pyw 启动）。
"""
from __future__ import annotations
import json
import pathlib
import ssl
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "activity_stages.json"
MAA_RES = ROOT / "services" / "maa" / "source" / "resource"

ZONES_API = "https://penguin-stats.io/PenguinStats/api/v2/zones"
ACTIVE_THRESHOLD = 5000      # 近 30 天样本量下限（低于视为无活动）
RECENT_WINDOW_MS = 30 * 86400 * 1000

# 资源类物品名（龙门币/作战记录）
RESOURCE_ITEMS = {"龙门币", "基础作战记录", "初级作战记录", "中级作战记录", "高级作战记录"}

_SCAN_STATUS: dict = {}
_SCAN_LOCK = threading.Lock()


def _set_status(**kw) -> None:
    with _SCAN_LOCK:
        _SCAN_STATUS.update({"ts": time.time(), **kw})


def _fetch_json(url: str, timeout: float = 90.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "MAAOrch/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout,
                                             context=ssl.create_default_context()).read())


def _load_maa() -> tuple[dict, list]:
    items = json.loads((MAA_RES / "item_index.json").read_text(encoding="utf-8"))
    stages = json.loads((MAA_RES / "stages.json").read_text(encoding="utf-8"))
    return items, stages


def _until_from_events(zone_name: str) -> str:
    """PRTS 活动日历（event_analyzer 产出）里找活动截止日期。"""
    try:
        evs = json.loads((ROOT / "logs" / "announcements" / "events.json")
                         .read_text(encoding="utf-8"))
        for e in (evs if isinstance(evs, list) else []):
            name = e.get("name", "") or ""
            if name and (name in zone_name or zone_name in name):
                return e.get("end", "") or e.get("until", "") or ""
    except Exception:
        pass
    return ""


def detect() -> dict:
    """识别当前活动并写 activity_stages.json。返回配置 dict。"""
    from services.penguin_data import BLUE_MATERIALS, fetch_matrix
    _set_status(state="running")
    try:
        items, stages = _load_maa()
        id2name = {k: v["name"] for k, v in items.items()}
        name2id = {v["name"]: k for k, v in items.items()}

        # 1) zones → ACTIVITY zone + stageId→zone
        zones = _fetch_json(ZONES_API)
        act_zones = [z for z in zones if z.get("type") == "ACTIVITY"]
        sid2zone = {}
        for z in act_zones:
            for s in z.get("stages", []):
                sid2zone[s] = z

        # 2) matrix 原始行（含 start/end 时间窗）→ 近 30 天活跃 zone
        rows = fetch_matrix()
        now_ms = time.time() * 1000
        zone_times: dict = {}
        for r in rows:
            z = sid2zone.get(r.get("stageId", ""))
            if not z:
                continue
            if (r.get("start") or 0) <= now_ms - RECENT_WINDOW_MS:
                continue
            zone_times[z["zoneId"]] = zone_times.get(z["zoneId"], 0) + r.get("times", 0)
        if not zone_times or max(zone_times.values()) < ACTIVE_THRESHOLD:
            cfg = {"active": False, "reason": "近 30 天无活跃活动 zone",
                   "ts": time.time(), "detected": True,
                   "material": [], "orundum": None, "resource": []}
            _write_config(cfg)
            _set_status(state="done", active=False)
            return cfg
        active_zone_id = max(zone_times, key=zone_times.get)
        azone = next(z for z in act_zones if z["zoneId"] == active_zone_id)

        # 3) 该 zone 关卡 → code + 主掉落 + 掉率
        stage_map = {}
        for st in stages:
            if st["stageId"] in azone.get("stages", []):
                stage_map[st["stageId"]] = (st["code"], st["apCost"])
        agg: dict = {}  # (stageId, itemId) -> [q, t]
        for r in rows:
            if r.get("stageId") in stage_map:
                a = agg.setdefault((r["stageId"], r["itemId"]), [0, 0])
                a[0] += r.get("quantity", 0)
                a[1] += r.get("times", 0)
        stage_drops: dict = {}  # stageId -> [(item, dropType)]
        for st in stages:
            if st["stageId"] not in stage_map:
                continue
            stage_drops[st["stageId"]] = [
                (d.get("itemId"), d.get("dropType"))
                for d in st.get("dropInfos", [])
                if d.get("dropType") == "NORMAL_DROP"]

        # 4) 分类
        material_cand = []
        orundum_cand = []
        resource_cand = []
        orundum_id = name2id.get("固源岩", "")
        for sid, drops in stage_drops.items():
            code, ap = stage_map[sid]
            for iid, _dt in drops:
                name = id2name.get(iid, "")
                q, t = agg.get((sid, iid), (0, 0))
                if t < 300:  # 活动新关样本少, 门槛放低
                    continue
                rate = q / t
                entry = {"stage": code, "item": name, "rate": round(rate, 3)}
                if iid == orundum_id:
                    orundum_cand.append(entry)
                elif name in RESOURCE_ITEMS:
                    resource_cand.append(entry)
                elif name in BLUE_MATERIALS:
                    material_cand.append(entry)
        material_cand.sort(key=lambda x: -x["rate"])
        orundum_cand.sort(key=lambda x: -x["rate"])
        resource_cand.sort(key=lambda x: -x["rate"])
        # 同一关取最高掉率那条，去重取前 3
        seen, material = set(), []
        for e in material_cand:
            if e["stage"] in seen:
                continue
            seen.add(e["stage"])
            material.append(e)
            if len(material) >= 3:
                break

        cfg = {
            "active": True,
            "zone": active_zone_id,
            "zone_name": azone.get("zoneName", ""),
            "until": _until_from_events(azone.get("zoneName", "")),
            "material": material,
            "orundum": orundum_cand[0] if orundum_cand else None,
            "resource": resource_cand[:2],
            "recent_times": zone_times[active_zone_id],
            "ts": time.time(),
            "detected": True,
        }
        _write_config(cfg)
        _set_status(state="done", active=True, zone_name=cfg["zone_name"],
                    material=[m["stage"] for m in material],
                    orundum=cfg["orundum"]["stage"] if cfg["orundum"] else None)
        return cfg
    except Exception as e:
        _set_status(state="error", error=str(e)[:300])
        return {"active": False, "error": str(e)[:300], "ts": time.time()}


def _write_config(cfg: dict) -> None:
    """写盘；人工修正（manual=true）时保留人工的分类字段。"""
    old = None
    try:
        old = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    if isinstance(old, dict) and old.get("manual"):
        for k in ("material", "orundum", "resource", "until"):
            if k in old and k not in cfg:
                cfg[k] = old[k]
        if old.get("material"):
            cfg["material"] = old["material"]
        if old.get("orundum"):
            cfg["orundum"] = old["orundum"]
        if old.get("resource"):
            cfg["resource"] = old["resource"]
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def status() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return {"ok": True, "config": cfg, "scan": dict(_SCAN_STATUS)}


class ActivityScheduler(threading.Thread):
    """每小时识别当前活动（main_web.pyw 启动一次）。"""

    def __init__(self, mw) -> None:
        super().__init__(daemon=True, name="activity-scheduler")
        self.mw = mw

    def run(self) -> None:
        while True:
            try:
                r = detect()
                fn = getattr(self.mw, "_log", None)
                if fn:
                    if r.get("active"):
                        fn(f"[活动] 当前活动: {r.get('zone_name')} "
                           f"({[m['stage'] for m in r.get('material', [])]})")
                    elif not r.get("error"):
                        fn(f"[活动] 无进行中活动: {r.get('reason', '')}")
            except Exception as e:
                fn = getattr(self.mw, "_log", None)
                if fn:
                    fn(f"[活动] 识别异常: {e}")
            time.sleep(3600)
