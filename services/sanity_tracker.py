"""体力实时快照（2026-08-12 用户: 以最后实时的结果为准）。

MAA 只发 SanityBeforeStage（进关卡前体力，1764 样本）— log_watcher
每收到一条就更新本快照 → 所有消费方（sanity 门/界面/调度）读它，
而不是运行结束才落盘的 stats.json 死值。

快照文件: models/accounts/<aid>/sanity_live.json（与 RunStats 同级，
部署只覆盖 DEFAULT_FILES 清单，此目录天然保留）。

字段:
  current     进关卡前体力（最后一次）
  max         体力上限
  report_time MAA 报告时间（游戏内真实时间）
  captured_at 本机捕获时间（report_time 缺失时用，格式 %Y-%m-%d %H:%M:%S）
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

BASE = Path(__file__).parent.parent
LIVE_DIR = BASE / "models" / "accounts"

_JSON_RE = re.compile(r"\{.*\}")


def _safe_aid(aid: str) -> str:
    import re as _re
    return _re.sub(r"[^\w.-]", "_", aid or "") or "_"


def _resolve_aid(watcher_aid: str, inst_dir) -> str:
    """实例归属权威 = .meta 的 aid（MAA 启动前写，表示实例当前属于谁）。

    防串号（2026-08-12 用户: 账号之间不能污染）: watcher 可能泄漏/复用
    （MAA 挂死没走 cleanup），实例被新账号接管后旧 watcher 仍在读同一个
    asst.log — 以 .meta 为准记到新账号名下，而不是 watcher 记住的旧 aid。
    .meta 缺失（实例空闲/清理中）回退 watcher 的 aid。
    """
    try:
        mf = Path(inst_dir) / ".meta"
        if mf.exists():
            meta = mf.read_text(encoding="utf-8", errors="replace").strip()
            if meta and "|" in meta:
                return meta.split("|", 1)[0].strip()
    except Exception:
        pass
    return watcher_aid


def update(aid: str, current, max_sanity, report_time: str = "") -> None:
    """记录一次实时体力（进关卡前，MAA SanityBeforeStage 报告）。"""
    if not aid or current is None:
        return
    try:
        d = LIVE_DIR / _safe_aid(aid)
        d.mkdir(parents=True, exist_ok=True)
        rec = {
            "current": int(current),
            "max": int(max_sanity or 0),
            "report_time": report_time or "",
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(d / "sanity_live.json", "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
    except Exception:
        pass  # 快照失败不影响主流程（消费方回退 stats.json）


def parse_line(line: str) -> dict | None:
    """从 asst.log 原始行解析 SanityBeforeStage（纯函数，供 log_watcher 事件
    payload 与 update_from_line 共用 — 单点收敛 Step 3a 账房收口）。

    返回 {current, max, report_time} 或 None（未命中）。

    双格式兼容（2026-08-13 生产 MAA 换成 6.16.0，回调字段变了）:
      - 6.16.6/8: SubTaskExtraInfo what="SanityBeforeStage"
      - 6.16.0:   SubTaskExtraInfo class="asst::FightTimesTaskPlugin"
    """
    try:
        m = _JSON_RE.search(line)
        if not m:
            return None
        data = json.loads(m.group(0))
        if data.get("what") == "SanityBeforeStage":
            det = data.get("details") or {}
        elif data.get("class") == "asst::FightTimesTaskPlugin":
            det = data.get("details") or {}
        else:
            return None
        cur = det.get("current_sanity")
        if cur is None:
            return None
        return {
            "current": cur,
            "max": det.get("max_sanity", 0),
            "report_time": det.get("report_time", ""),
        }
    except Exception:
        return None


def update_from_line(aid: str, line: str, inst_dir=None) -> bool:
    """从 asst.log 原始行解析 SanityBeforeStage 并更新快照。返回是否命中。
    inst_dir: 实例目录（含 .meta）— 归属校验，防账号串号。
    """
    det = parse_line(line)
    if not det:
        return False
    update(_resolve_aid(aid, inst_dir), det["current"], det["max"],
           det["report_time"])
    return True


# ── EventBus 订阅（单点收敛 Step 3a — 体力写入者收口到本模块） ──

_subscribed = False


def _on_bus_sanity(evt: dict) -> None:
    """消费 fight.sanity_before 事件 → 更新快照（与 log_watcher 直调
    双写同值，幂等；迁移期双写，行为等价后删直调 — STRUCTURE.md 原则）。"""
    try:
        d = evt.get("data") or {}
        s = d.get("sanity")
        if not s or s.get("current") is None:
            return
        update(_resolve_aid(evt.get("aid", ""), d.get("inst_dir")),
               s["current"], s.get("max", 0), s.get("report_time", ""))
    except Exception:
        pass


def subscribe_bus() -> None:
    """注册 bus 订阅（幂等）。runner 启动时调用，保证先于 LogWatcher 生效。"""
    global _subscribed
    if _subscribed:
        return
    try:
        from services.event_bus import subscribe
        subscribe("fight.sanity_before", _on_bus_sanity)
        _subscribed = True
    except Exception:
        pass


def get(aid: str) -> dict | None:
    """读最后实时体力快照（无则 None — 调用方回退旧数据源）。"""
    try:
        fp = LIVE_DIR / _safe_aid(aid) / "sanity_live.json"
        if not fp.exists():
            return None
        return json.loads(fp.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
