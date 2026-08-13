"""Penguin data — 企鹅物流(Penguin Stats)掉落矩阵定期采集 + 本地 SQLite 库。

2026-08-13 用户: 企鹅物流数据权威，定期下载更新本地数据库，数据变了
自动重生成材料备选榜（config/farm_stages.json）并过漏检审计（总漏 0 才生效）。

链路:
    每日 penguin_refresh_time（默认 03:30，早于 04:30 仓库扫描）
    → 拉取 matrix API（penguin-stats.io 优先, penguin-stats.cn 兜底）
    → 聚合 (stage_id, item_id) → SQLite 快照入库（data/penguin.db）
    → 与上一快照对比掉率：变化 → 重生成 farm_stages.json + 审计门禁

SQLite 结构:
    snapshots(id, ts, row_count, changed)
    matrix(stage_id, item_id, quantity, times, snapshot_id)
"""
from __future__ import annotations
import json
import pathlib
import sqlite3
import ssl
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "penguin.db"
FARM_STAGES_PATH = ROOT / "config" / "farm_stages.json"
MAA_RES = ROOT / "services" / "maa" / "source" / "resource"

# 蓝材料（T3）标准清单 — 备选榜覆盖的 17 个核心材料
BLUE_MATERIALS = [
    "固源岩组", "糖组", "聚酸酯组", "异铁组", "酮凝集组", "装置",
    "扭转醇", "轻锰矿", "研磨石", "RMA70-12", "凝胶", "炽合金",
    "晶体元件", "半自然溶剂", "化合切削液", "环烃聚质", "转质盐组",
]

MATRIX_PATH = "PenguinStats/api/v2/result/matrix?show_closed_zones=false&server=CN"
ENDPOINTS = [
    "https://penguin-stats.io/" + MATRIX_PATH,
    "https://penguin-stats.cn/" + MATRIX_PATH,
]

# 掉率相对变化阈值：超过视为数据有变（计数持续增长，用率对比才稳定）
RATE_CHANGE_THRESHOLD = 0.005

_SCAN_STATUS: dict = {}  # API 轮询: {state, ts, error, ...}
_SCAN_LOCK = threading.Lock()


def _set_status(**kw) -> None:
    with _SCAN_LOCK:
        _SCAN_STATUS.update({"ts": time.time(), **kw})


# ── 拉取 ──
def fetch_matrix(timeout: float = 90.0) -> list:
    """拉取掉落矩阵（返回原始行列表；双域名兜底，重试各 2 次）。"""
    last_err = ""
    for url in ENDPOINTS:
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "MAAOrch/1.0"})
                data = json.loads(urllib.request.urlopen(
                    req, timeout=timeout, context=ssl.create_default_context()).read())
                rows = data.get("matrix") or []
                if rows:
                    return rows
                last_err = "API 返回空矩阵"
            except Exception as e:
                last_err = str(e)[:200]
            time.sleep(3)
    raise RuntimeError(f"企鹅物流拉取失败: {last_err}")


# ── SQLite 存取 ──
def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        row_count INTEGER NOT NULL,
        changed INTEGER NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS matrix (
        stage_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        times INTEGER NOT NULL,
        snapshot_id INTEGER NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_matrix_snap ON matrix(snapshot_id)")
    return c


def aggregate(rows: list) -> dict:
    """(stage_id, item_id) -> [quantity, times] 聚合。"""
    agg: dict = {}
    for r in rows:
        sid = r.get("stageId", "")
        iid = r.get("itemId", "")
        if not sid or not iid:
            continue
        a = agg.setdefault((sid, iid), [0, 0])
        a[0] += int(r.get("quantity", 0))
        a[1] += int(r.get("times", 0))
    return agg


def latest_agg(c) -> dict:
    row = c.execute("SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {}
    out = {}
    for sid, iid, q, t in c.execute("SELECT stage_id, item_id, quantity, times "
                                    "FROM matrix WHERE snapshot_id=?", (row[0],)):
        out[(sid, iid)] = [q, t]
    return out


def status() -> dict:
    """采集状态（API/UI 用）。"""
    c = _conn()
    try:
        last = c.execute("SELECT id, ts, row_count, changed FROM snapshots "
                         "ORDER BY id DESC LIMIT 1").fetchone()
        n_snap = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        farm_mtime = (FARM_STAGES_PATH.stat().st_mtime if FARM_STAGES_PATH.exists()
                      else 0)
        return {"ok": True,
                "last_refresh": last[1] if last else 0,
                "snapshots": n_snap,
                "last_row_count": last[2] if last else 0,
                "last_changed": bool(last[3]) if last else None,
                "farm_ts": int(farm_mtime),
                "scan": dict(_SCAN_STATUS)}
    finally:
        c.close()


# ── 刷新主流程 ──
def refresh() -> dict:
    """拉取 → 入库 → 对比 → 变了则重生成备选榜+审计门禁。"""
    _set_status(state="running")
    try:
        rows = fetch_matrix()
        agg = aggregate(rows)
        c = _conn()
        try:
            old = latest_agg(c)
            # 掉率对比（计数持续增长，用率对比稳定）
            rate_shifts = 0
            new_pairs = 0
            for k, (q, t) in agg.items():
                if k not in old:
                    new_pairs += 1
                    continue
                oq, ot = old[k]
                if ot > 0 and t > 0 and abs(q / t - oq / ot) > RATE_CHANGE_THRESHOLD:
                    rate_shifts += 1
            removed = sum(1 for k in old if k not in agg)
            changed = new_pairs > 0 or rate_shifts > 0 or removed > 0
            cur = c.execute("INSERT INTO snapshots (ts, row_count, changed) "
                            "VALUES (?,?,?)", (time.time(), len(agg),
                                               int(changed)))
            sid = cur.lastrowid
            c.executemany("INSERT INTO matrix VALUES (?,?,?,?,?)",
                          [(k[0], k[1], v[0], v[1], sid) for k, v in agg.items()])
            c.commit()
        finally:
            c.close()

        farm_regen = {"attempted": changed, "ok": False, "audit_missing": []}
        if changed:
            farm_regen = regen_farm_table()
        _set_status(state="done", rows=len(agg), changed=changed,
                    new_pairs=new_pairs, rate_shifts=rate_shifts,
                    removed=removed, farm_regen=farm_regen)
        return {"ok": True, "rows": len(agg), "changed": changed,
                "new_pairs": new_pairs, "rate_shifts": rate_shifts,
                "removed": removed, "farm_regen": farm_regen}
    except Exception as e:
        _set_status(state="error", error=str(e)[:300])
        return {"ok": False, "error": str(e)[:300]}


# ── 备选榜重生成 + 审计（逻辑自 tools/material_stages.py 迁入）──
def _load_maa() -> tuple[dict, list]:
    items = json.loads((MAA_RES / "item_index.json").read_text(encoding="utf-8"))
    stages = json.loads((MAA_RES / "stages.json").read_text(encoding="utf-8"))
    return items, stages


def is_resident(stage_id: str) -> bool:
    return stage_id.startswith(("main_", "tough_", "sub_", "wk_"))


def build_farm_table(agg: dict) -> dict:
    """聚合矩阵 → 备选表 {材料: [{stage, ap, rate} 期望AP/个升序]}。

    规则（2026-08-13 逐条定稿）:
    - 只有 stages.json 主掉落(NORMAL_DROP)的关进榜（EXTRA 噪声剔除）
    - 磨难(tough_)独立成 MAA 原生 -HARD 条目，一条不漏
    - 双掉落关合法（rate 可 >1，如装置 14-11 每把 2 个）
    - 严格按期望AP/个升序，不做平手特权
    """
    items, stages = _load_maa()
    name2id = {v["name"]: k for k, v in items.items()}
    stage_map = {}  # stageId -> (code, ap, hard)
    normal_drops: set = set()
    for st in stages:
        sid = st["stageId"]
        if not is_resident(sid):
            continue
        hard = sid.startswith("tough_")
        code = f"{st['code']}-HARD" if hard else st["code"]
        stage_map[sid] = (code, st["apCost"], hard)
        for d in st.get("dropInfos", []):
            if d.get("dropType") == "NORMAL_DROP" and d.get("itemId"):
                normal_drops.add((sid, d["itemId"]))
    out: dict = {}
    for name in BLUE_MATERIALS:
        iid = name2id.get(name)
        if not iid:
            continue
        per_stage: dict = {}
        for (sid, item), (q, t) in agg.items():
            if item != iid or (sid, iid) not in normal_drops:
                continue
            per_stage[sid] = [q, t]
        rows = []
        for sid, (q, t) in per_stage.items():
            if t < 500:
                continue
            code, ap, hard = stage_map[sid]
            rate = q / t
            if not (0 < rate <= 5):  # >1 合法=双掉落关; 5 只挡异常
                continue
            rows.append({"stage": code, "ap": ap, "rate": round(rate, 3),
                         "eap": ap / rate})
        rows.sort(key=lambda r: r["eap"])
        if rows:
            out[name] = [{"stage": r["stage"], "ap": r["ap"],
                          "rate": r["rate"]} for r in rows]
    return out


def audit_farm_table(farm: dict) -> list:
    """漏检审计: stages.json 全部主掉落关（含磨难）都必须入榜。

    返回漏掉的关卡名列表；空 = 总漏 0。
    """
    items, stages = _load_maa()
    name2id = {v["name"]: k for k, v in items.items()}
    all_normal: dict = {}
    for st in stages:
        sid = st["stageId"]
        if not sid.startswith(("main_", "sub_", "tough_")):
            continue
        code = f"{st['code']}-HARD" if sid.startswith("tough_") else st["code"]
        for d in st.get("dropInfos", []):
            if d.get("dropType") == "NORMAL_DROP" and d.get("itemId"):
                all_normal.setdefault(d["itemId"], []).append(code)
    missing_all = []
    for name, rows in farm.items():
        iid = name2id.get(name)
        have = all_normal.get(iid, [])
        codes_in = [r["stage"] for r in rows]
        missing = [c for c in have if c not in codes_in]
        if missing:
            missing_all.append(f"{name}: {missing}")
    return missing_all


def regen_farm_table() -> dict:
    """用最新快照重生成备选榜，审计总漏 0 才替换生效。"""
    c = _conn()
    try:
        agg = latest_agg(c)
    finally:
        c.close()
    if not agg:
        return {"attempted": True, "ok": False, "error": "本地库无数据"}
    farm = build_farm_table(agg)
    missing = audit_farm_table(farm)
    if missing:
        return {"attempted": True, "ok": False,
                "audit_missing": missing[:5]}
    FARM_STAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FARM_STAGES_PATH.write_text(json.dumps(farm, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    return {"attempted": True, "ok": True, "materials": len(farm)}


# ── 定时线程 ──
class PenguinScheduler(threading.Thread):
    """每日 penguin_refresh_time 自动采集（main_web.pyw 启动一次）。

    配置: config/material_water_levels.json 的 penguin_enabled /
    penguin_refresh_time（默认 03:30，早于 04:30 仓库扫描）。
    """

    def __init__(self, mw) -> None:
        super().__init__(daemon=True, name="penguin-scheduler")
        self.mw = mw
        self._last_run_date = ""

    def _load_cfg(self) -> dict:
        try:
            p = ROOT / "config" / "material_water_levels.json"
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _tick(self) -> None:
        cfg = self._load_cfg()
        if not cfg.get("penguin_enabled", True):
            return
        hhmm = cfg.get("penguin_refresh_time", "03:30")
        today = time.strftime("%Y-%m-%d")
        if time.strftime("%H:%M") == hhmm and self._last_run_date != today:
            self._last_run_date = today
            fn = getattr(self.mw, "_log", None)
            try:
                r = refresh()
                msg = (f"企鹅物流采集: {r.get('rows', 0)} 对, "
                       f"变化={r.get('changed')}, 榜重生成={r.get('farm_regen', {}).get('ok')}")
                if fn:
                    fn(f"[材料规划] {msg}")
            except Exception as e:
                if fn:
                    fn(f"[材料规划] 企鹅物流采集异常: {e}")

    def run(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:
                pass
            time.sleep(60)
